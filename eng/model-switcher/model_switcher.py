#!/usr/bin/env python3
"""Model switcher for TensorSharp.Server.Host.

TensorSharp hosts one model per process, chosen at startup. This tool keeps a
list of presets (eng/model-switcher/presets.json) and serves a small page that
stops the running server and starts the chosen preset on the same port, so the
regular TensorSharp UI and API stay at one address.

Standard library only:

    python3 eng/model-switcher/model_switcher.py            # page on http://127.0.0.1:5099
    python3 eng/model-switcher/model_switcher.py --start qwen38-27b-skills

The server's output goes to artifacts/model-switcher/server.log (truncated on
each switch), which eng/perf-dashboard can follow with --log.
"""

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HOST_BIN = REPO / "TensorSharp.Server.Host" / "bin" / "TensorSharp.Server.Host"
LOG_DIR = REPO / "artifacts" / "model-switcher"
LOG_PATH = LOG_DIR / "server.log"
READY_TIMEOUT_S = 900
STOP_TIMEOUT_S = 30


def load_jsonc(path):
    """JSON with // line comments (outside strings)."""
    out, text = [], Path(path).read_text(encoding="utf-8")
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return json.loads("".join(out))


def pids_on_port(port):
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5).stdout
        return sorted({int(p) for p in out.split()})
    except Exception:
        return []


def command_of(pid):
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tail(path, lines=40):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-lines:]
    except OSError:
        return []


class Switcher:
    def __init__(self, presets_path):
        cfg = load_jsonc(presets_path)
        self.port = int(cfg.get("port", 5080))
        self.presets = cfg["presets"]
        self.by_id = {p["id"]: p for p in self.presets}
        self.lock = threading.Lock()
        self.busy = False
        self.proc = None
        self.state = {"status": "stopped", "presetId": None, "pid": None,
                      "startedAt": None, "readyAt": None, "error": None}
        self._adopt_existing()

    # A server left running by an earlier switcher (or started by hand) is
    # adopted rather than duplicated: match its --model against the presets.
    def _adopt_existing(self):
        for pid in pids_on_port(self.port):
            cmd = command_of(pid)
            if "TensorSharp.Server.Host" not in cmd:
                continue
            preset = next((p for p in self.presets
                           if self._model_of(p) and self._model_of(p) in cmd), None)
            self.state.update(status="ready" if self._healthy() else "starting",
                              presetId=preset["id"] if preset else None, pid=pid,
                              startedAt=time.time(), external=True)
            if self.state["status"] == "starting":
                threading.Thread(target=self._wait_ready, args=(pid, None), daemon=True).start()
            return

    @staticmethod
    def _model_of(preset):
        a = preset.get("args", [])
        return a[a.index("--model") + 1] if "--model" in a else None

    def _healthy(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
        s.update(port=self.port, busy=self.busy, log=str(LOG_PATH),
                 logTail=tail(LOG_PATH) if s["status"] in ("stopping", "starting", "failed") else [],
                 presets=[{k: p.get(k) for k in ("id", "name", "description")} for p in self.presets])
        return s

    def _stop_pid(self, pid):
        if not alive(pid):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.time() + STOP_TIMEOUT_S
        while time.time() < deadline and alive(pid):
            if self.proc and self.proc.pid == pid:
                self.proc.poll()
            time.sleep(0.25)
        if alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if self.proc and self.proc.pid == pid:
            self.proc.wait(timeout=5)

    def stop(self):
        """Stop our server and any other TensorSharp server on the port. Returns an error or None."""
        pids = set(pids_on_port(self.port))
        if self.state.get("pid"):
            pids.add(self.state["pid"])
        for pid in pids:
            cmd = command_of(pid)
            if cmd and "TensorSharp.Server.Host" not in cmd:
                return f"port {self.port} is held by another program (pid {pid}: {cmd[:120]})"
        for pid in pids:
            self._stop_pid(pid)
        with self.lock:
            self.proc = None
            if not self.busy:
                self.state.update(status="stopped", presetId=None)
            self.state.update(pid=None, startedAt=None, readyAt=None, error=None, external=False)
        return None

    def claim(self, preset_id):
        """Reserve a switch synchronously, so a client polling right after sees it. Returns an error or None."""
        if preset_id not in self.by_id:
            return f"unknown preset '{preset_id}'"
        with self.lock:
            if self.busy:
                return "a switch is already in progress"
            self.busy = True
            self.state.update(status="stopping", presetId=preset_id, error=None)
        return None

    def switch(self, preset_id):
        err = self.claim(preset_id)
        if err:
            return err
        self.run_switch(preset_id)
        return None

    def run_switch(self, preset_id):
        preset = self.by_id[preset_id]
        try:
            err = self.stop()
            if err:
                with self.lock:
                    self.state.update(status="failed", error=err)
                return err
            if not HOST_BIN.exists():
                err = f"{HOST_BIN} not found; build it with: dotnet build TensorSharp.Server.Host -c Release"
                with self.lock:
                    self.state.update(status="failed", presetId=preset_id, error=err)
                return err
            missing = [a for a in preset["args"] if a.startswith(("models/", "TensorAgent/"))
                       and not (REPO / a).exists()]
            if missing:
                err = "missing file(s): " + ", ".join(missing)
                with self.lock:
                    self.state.update(status="failed", presetId=preset_id, error=err)
                return err
            env = dict(os.environ)
            dotnet_home = Path.home() / ".dotnet"
            if "DOTNET_ROOT" not in env and (dotnet_home / "dotnet").exists():
                env["DOTNET_ROOT"] = str(dotnet_home)
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log = open(LOG_PATH, "w", encoding="utf-8")
            cmd = [str(HOST_BIN), *preset["args"], "--port", str(self.port)]
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            # Own session: the model server outlives a restart of this switcher.
            proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            log.close()
            with self.lock:
                self.proc = proc
                self.state.update(status="starting", presetId=preset_id, pid=proc.pid,
                                  startedAt=time.time(), readyAt=None, error=None, external=False)
            threading.Thread(target=self._wait_ready, args=(proc.pid, proc), daemon=True).start()
            return None
        finally:
            with self.lock:
                self.busy = False

    def _wait_ready(self, pid, proc):
        deadline = time.time() + READY_TIMEOUT_S
        while time.time() < deadline:
            with self.lock:
                if self.state.get("pid") != pid:
                    return  # superseded by another switch
            exited = proc.poll() is not None if proc else not alive(pid)
            if exited:
                with self.lock:
                    if self.state.get("pid") == pid:
                        code = proc.returncode if proc else None
                        cause = next((l.split(":", 1)[1].strip() for l in reversed(tail(LOG_PATH, 200))
                                      if l.startswith("error:")), None)
                        self.state.update(status="failed",
                                          error=cause or f"server exited during startup (code {code}); see the log")
                return
            if self._healthy():
                with self.lock:
                    if self.state.get("pid") == pid:
                        self.state.update(status="ready", readyAt=time.time())
                return
            time.sleep(1)
        with self.lock:
            if self.state.get("pid") == pid:
                self.state.update(status="failed", error=f"not ready after {READY_TIMEOUT_S} s")


def make_handler(sw):
    page_path = HERE / "switcher.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, page_path.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(200, sw.snapshot())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            if path == "/api/switch":
                # Run in the background: stopping a loaded model can take seconds.
                pid = body.get("id")
                err = sw.claim(pid)
                if err:
                    return self._send(409 if pid in sw.by_id else 400, {"error": err})
                threading.Thread(target=sw.run_switch, args=(pid,), daemon=True).start()
                self._send(202, {"ok": True})
            elif path == "/api/stop":
                if sw.busy:
                    return self._send(409, {"error": "a switch is in progress"})
                err = sw.stop()
                self._send(409 if err else 200, {"error": err} if err else {"ok": True})
            else:
                self._send(404, {"error": "not found"})

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--presets", default=str(HERE / "presets.json"), help="presets file (JSON with // comments)")
    p.add_argument("--host", default="127.0.0.1", help="switcher bind address")
    p.add_argument("--port", type=int, default=5099, help="switcher page port")
    p.add_argument("--start", metavar="PRESET", help="switch to this preset on startup")
    args = p.parse_args()

    sw = Switcher(args.presets)
    if args.start:
        if args.start not in sw.by_id:
            raise SystemExit(f"unknown preset '{args.start}'; known: {', '.join(sw.by_id)}")
        threading.Thread(target=sw.switch, args=(args.start,), daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(sw))
    print(f"TensorSharp model switcher: http://{args.host}:{args.port}  "
          f"(models on port {sw.port}, log {LOG_PATH})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
