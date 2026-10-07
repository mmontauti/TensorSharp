#!/usr/bin/env python3
"""Live performance dashboard for a running TensorSharp.Server.

Tails the server's log for per-turn telemetry lines (chat.complete,
chat.cancelled, generate.complete), polls /api/queue/status, and samples the
server process's memory/CPU. Serves a single-page dashboard on --port.

Standard library only:

    python3 eng/perf-dashboard/perf_dashboard.py \
        --log server.log --server http://localhost:5080 --port 5090

The server log carries no timestamps, so requests already in the log at
startup are shown in order without a time; new ones are stamped when seen.
"""

import argparse
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

EVENT_RE = re.compile(r"\b(chat\.complete|chat\.cancelled|generate\.complete)\s+(.*)$")
FIELD_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
NUMERIC = {"tokens", "promptTokens", "kvReused", "kvReusePercent", "ttftMs", "elapsedMs", "tokensPerSec"}
MAX_REQUESTS = 2000
MAX_IMAGES = 500
MAX_SAMPLES = 1800  # 1 h at the default 2 s poll


def parse_event(line):
    m = EVENT_RE.search(line)
    if not m:
        return None
    ev = {"event": m.group(1)}
    for key, raw in FIELD_RE.findall(m.group(2)):
        if raw.startswith('"'):
            ev[key] = raw[1:-1]
        elif key in NUMERIC:
            try:
                ev[key] = float(raw)
            except ValueError:
                pass
        else:
            ev[key] = raw
    # tokensPerSec in the log is tokens / elapsed (TTFT included); derive the
    # steady decode rate separately so prefill cost doesn't hide it.
    toks, el, ttft = ev.get("tokens"), ev.get("elapsedMs"), ev.get("ttftMs")
    if toks and toks > 1 and el is not None and ttft is not None and el > ttft:
        ev["decodeTps"] = (toks - 1) / ((el - ttft) / 1000.0)
    if ev.get("promptTokens") and ttft:
        prefilled = ev["promptTokens"] - ev.get("kvReused", 0)
        ev["prefillTps"] = prefilled / (ttft / 1000.0) if prefilled > 0 else None
    out = ev.get("assistantOutput") or ev.get("completion")
    if out is not None:
        ev["output"] = out[:160]
        ev.pop("assistantOutput", None)
        ev.pop("completion", None)
    return ev


# Qwen-Image runs. The plain route logs "Image request: ...", the streaming
# route "Image edit (stream): ..."; both then log the same [qwen21-*] timing
# lines, and "total:" closes the run. Durations may use a decimal comma.
IMG_START_RE = re.compile(r"Image (?:request|edit \(stream\)): prompt='(.*)' steps=(\d+) cfg=\S+ images=(\d+)")
IMG_ENCODE_RE = re.compile(r"\[qwen21-timing\] text and vision encode: (\d+)ms")
IMG_STEP_RE = re.compile(r"\[qwen21-step\] (\d+)/(\d+): ([\d.,]+)s")
IMG_DENOISE_RE = re.compile(r"\[qwen21-timing\] denoise: (\d+)ms")
IMG_VAE_RE = re.compile(r"\[qwen21-timing\] VAE decode: (\d+)ms")
IMG_TOTAL_RE = re.compile(r"\[qwen21-timing\] total: ([\d.,]+)s")
IMG_DONE_RE = re.compile(r"Image (?:request|edit \(stream\)) done: (\d+)x(\d+)")
IMG_PLAN_RE = re.compile(r"Qwen-Image-2\.1: (\d+)x(\d+), (\d+) steps")
IMG_FAIL_RE = re.compile(r"Image (?:edit|generation)(?: \(stream\))? (failed|rejected)")


def _num(raw):
    return float(raw.replace(",", "."))


class ImageTracker:
    """Folds the server's per-image log lines into one record per image."""

    def __init__(self, state):
        self.state = state
        self.run = None  # the image being generated right now

    def feed(self, line, now):
        st = self.state
        m = IMG_START_RE.search(line)
        if m:
            self.run = {"prompt": m.group(1)[:160], "kind": "edit" if int(m.group(3)) else "generate",
                        "t": now, "started": now, "stepTimes": [], "steps": None, "status": "running"}
            st.image_live = self.run
            return
        r = self.run
        if r is None:
            # "done" arrives after "total:", once the run is already closed
            m = IMG_DONE_RE.search(line)
            if m and st.images and st.images[-1].get("width") is None:
                st.images[-1].update(width=int(m.group(1)), height=int(m.group(2)))
            return
        if (m := IMG_PLAN_RE.search(line)):
            r["width"], r["height"], r["steps"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
        elif (m := IMG_ENCODE_RE.search(line)):
            r["encodeMs"] = float(m.group(1))
        elif (m := IMG_STEP_RE.search(line)):
            r["stepTimes"].append(_num(m.group(3)))
            r["step"], r["steps"] = int(m.group(1)), int(m.group(2))
        elif (m := IMG_DENOISE_RE.search(line)):
            r["denoiseMs"] = float(m.group(1))
        elif (m := IMG_VAE_RE.search(line)):
            r["vaeMs"] = float(m.group(1))
        elif (m := IMG_TOTAL_RE.search(line)):
            r["totalMs"] = _num(m.group(1)) * 1000.0
            self._close("done")
        elif (m := IMG_FAIL_RE.search(line)):
            self._close(m.group(1))

    def _close(self, status):
        r, st = self.run, self.state
        times = r.pop("stepTimes")
        if times:
            r["secPerStep"] = sum(times) / len(times)
        r["status"] = status
        st.seq_img += 1
        r["seq"] = st.seq_img
        st.images.append(r)
        del st.images[:-MAX_IMAGES]
        st.image_live = None
        self.run = None

    def live(self):
        r = self.run
        if not r:
            return None
        times = r["stepTimes"]
        out = {k: r.get(k) for k in ("prompt", "kind", "started", "step", "steps", "width", "height", "encodeMs")}
        if times:
            out["secPerStep"] = sum(times) / len(times)
            if r.get("steps"):
                out["etaS"] = out["secPerStep"] * (r["steps"] - r["step"])
        return out


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.images = []
        self.image_live = None
        self.seq_img = 0
        self.requests = []
        self.samples = []
        self.seq = 0
        self.server_up = False
        self.server_pid = None


def tail_log(path, state, interval):
    pos, buf, first = 0, "", True
    images = ImageTracker(state)
    state.image_tracker = images
    while True:
        try:
            size = os.path.getsize(path)
            if size < pos:  # log truncated / server restarted
                pos, buf = 0, ""
            if size > pos:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                buf += chunk
                lines = buf.split("\n")
                buf = lines.pop()
                now = time.time()
                with state.lock:
                    for line in lines:
                        images.feed(line, None if first else now)
                        ev = parse_event(line)
                        if ev:
                            state.seq += 1
                            ev["seq"] = state.seq
                            ev["t"] = None if first else now
                            state.requests.append(ev)
                    del state.requests[:-MAX_REQUESTS]
            first = False
        except FileNotFoundError:
            pass
        time.sleep(interval)


def find_pid(port):
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        return int(out[0]) if out else None
    except Exception:
        return None


UNITS = {"B": 1 / 1048576, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1048576}


def footprint_mb(pid):
    # RSS misses Metal/IOKit allocations on Apple silicon; top's MEM column is
    # the physical footprint, which counts them.
    try:
        out = subprocess.run(["top", "-l", "1", "-pid", str(pid), "-stats", "mem"],
                             capture_output=True, text=True, timeout=10).stdout.split()
        m = re.match(r"([\d.]+)([BKMGT])", out[-1])
        return float(m.group(1)) * UNITS[m.group(2)] if m else None
    except Exception:
        return None


def proc_stats(pid):
    try:
        out = subprocess.run(["ps", "-o", "rss=,%cpu=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.split()
        rss, cpu = float(out[0]) / 1024.0, float(out[1])
    except Exception:
        return None, None
    fp = footprint_mb(pid) if os.uname().sysname == "Darwin" else None
    return (fp if fp is not None else rss), cpu


def poll_server(server, state, interval):
    port = urllib.parse.urlparse(server).port or 80
    while True:
        sample = {"t": time.time()}
        try:
            with urllib.request.urlopen(server.rstrip("/") + "/api/queue/status", timeout=3) as r:
                q = json.load(r)
            sample.update(processing=q.get("processing"), pending=q.get("pending_requests"),
                          total=q.get("total_processed"), busy=q.get("busy"))
            up = True
        except Exception:
            up = False
        pid = state.server_pid if up and state.server_pid else (find_pid(port) if up else None)
        if pid:
            rss, cpu = proc_stats(pid)
            if rss is None:
                pid = find_pid(port)
                rss, cpu = proc_stats(pid) if pid else (None, None)
            sample.update(rssMb=rss, cpu=cpu)
        with state.lock:
            state.server_up, state.server_pid = up, pid
            state.samples.append(sample)
            del state.samples[:-MAX_SAMPLES]
        time.sleep(interval)


def make_handler(state, args):
    page_path = os.path.join(HERE, "dashboard.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                with open(page_path, "rb") as f:
                    self._send(f.read(), "text/html; charset=utf-8")
            elif path == "/api/data":
                with state.lock:
                    data = {"server": args.server, "log": os.path.abspath(args.log),
                            "up": state.server_up, "pid": state.server_pid,
                            "requests": list(state.requests), "samples": list(state.samples),
                            "images": list(state.images),
                            "imageLive": state.image_tracker.live() if getattr(state, "image_tracker", None) else None}
                self._send(json.dumps(data).encode(), "application/json")
            else:
                self.send_error(404)

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", required=True, help="TensorSharp.Server log file (stdout/stderr capture)")
    p.add_argument("--server", default="http://localhost:5080", help="TensorSharp.Server base URL")
    p.add_argument("--port", type=int, default=5090, help="dashboard port")
    p.add_argument("--host", default="127.0.0.1", help="dashboard bind address")
    p.add_argument("--interval", type=float, default=2.0, help="poll interval, seconds")
    args = p.parse_args()

    state = State()
    threading.Thread(target=tail_log, args=(args.log, state, 1.0), daemon=True).start()
    threading.Thread(target=poll_server, args=(args.server, state, args.interval), daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(state, args))
    print(f"TensorSharp perf dashboard: http://{args.host}:{args.port}  (log={args.log}, server={args.server})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
