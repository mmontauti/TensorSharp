# TensorSharp — local tools fork

This is [mmontauti/TensorSharp](https://github.com/mmontauti/TensorSharp), a fork of
[zhongkaifu/TensorSharp](https://github.com/zhongkaifu/TensorSharp). It adds tooling for running
TensorSharp as a personal, single-machine assistant: switching between models from the chat UI,
watching performance live, and keeping chat history in the browser. Everything else, including the
inference engine, is upstream TensorSharp; see the main [README](README.md).

The work lives on the `local-tools` branch. Changes are listed in the [changelog](CHANGELOG.md).

## What the fork adds

### Web UI (`TensorSharp.Server.Host/wwwroot/index.html`)

- **Chat history.** Chats are saved in the browser's localStorage and listed in a left pane, grouped
  by date (Today, Yesterday, Previous 7 days, …). Open a chat to continue it; its full history is sent
  to the model again on the next message. The page reopens your last chat after a reload or a model
  switch. ☰ in the header shows or hides the pane; on narrow screens it slides over the chat.
- **Remembered skills.** Your last Agent Skills selection becomes the default for new chats, and each
  chat keeps its own. Skills that are no longer installed are dropped before sending, since the server
  rejects requests that name an unknown skill.
- **Model picker.** A header dropdown lists the model presets, with **Run** and **Stop**. The page
  reloads by itself once the new model is ready. It appears only when the model switcher is running
  on the same machine.
- **Performance link.** Opens the live performance dashboard.
- **Image size picker.** Shown for image-generation models; sets the output width and height.

### Server

- `--assistant-avatar <path>` (or `TS_ASSISTANT_AVATAR`) replaces the assistant's avatar image in the
  web UI without editing `wwwroot`.

### Tools in `eng/`

- **Model switcher** (`eng/model-switcher/`). Keeps a list of model presets and restarts
  `TensorSharp.Server.Host` with the chosen one on a single port, so the chat UI and the API stay at
  one address. Serves its own page on port 5099 and the API the chat UI uses.
- **Performance dashboard** (`eng/perf-dashboard/`). Follows the server log, `/api/queue/status`,
  and the server process's memory and CPU. Shows per-request chat metrics and per-image timings on
  port 5090.

Both tools are Python 3 standard library only.

## Running it

Build the server once:

```sh
dotnet build TensorSharp.Server.Host -c Release
```

Put the model files where `eng/model-switcher/presets.json` expects them (under `models/`), then
start the switcher with a preset and the dashboard:

```sh
python3 eng/model-switcher/model_switcher.py --start qwen38-27b-skills
python3 eng/perf-dashboard/perf_dashboard.py --log artifacts/model-switcher/server.log
```

| Address | What |
|---|---|
| http://localhost:5080 | Chat UI and API (the running model) |
| http://127.0.0.1:5099 | Model switcher page |
| http://127.0.0.1:5090 | Performance dashboard |

The server log is written to `artifacts/model-switcher/server.log` and is cleared on each switch.

## Presets

`eng/model-switcher/presets.json` (JSON with `//` comments) holds one entry per model:

| Preset | Model | Notes |
|---|---|---|
| `qwen38-27b-skills` | Qwen 3.8 27B, IQ3_XXS (10.9 GB) | Agent Skills with scripts, `--code-exec` shell for large files, context capped at 65,536 tokens |
| `qwen-image-21` | Qwen-Image 2.1, Q4_K_M + Qwen3-VL-8B encoder (10.3 GB) | Text-to-image and image editing |
| `gemma4-e4b` | Gemma 4 E4B, Q8_0 (7.2 GB) | Small, fast text chat |

Each preset has:

- `args`: options for `TensorSharp.Server.Host`, with paths relative to the repository root. Leave
  out `--port`; the switcher adds it.
- `env` (optional): environment variables for that preset's server only, for settings that have no
  option. For example, `"env": { "MAX_CONTEXT": "65536" }` caps the context window.

Switcher options: `--presets <file>` uses another presets file, `--dashboard-url` sets the address
the chat UI links to, and `--allow-origin <origin>` lets another web page address use the switcher
API.

## Security notes

- The model server listens on all network interfaces by default, so other devices on your network
  can reach the chat UI and API. Use `--host 127.0.0.1` in a preset's `args` to keep it on this
  machine.
- The presets allow skill scripts (`--skills-allow-exec`) and, for Qwen 3.8, a model-driven shell
  (`--code-exec`). On macOS the sandbox does not fully confine child processes. Don't expose a server
  with these options to people you don't trust.
- The switcher listens only on this machine. It answers the chat UI's own address
  (`localhost`/`127.0.0.1` on the model port) and refuses stop and start requests from any other web
  page.

## Limits

- Chats are stored only in the browser that created them. They don't sync between browsers or
  devices.
- Uploaded and generated files stay on the server; a saved chat keeps only their links.
- Browser storage is usually about 5 MB. When it fills up, the oldest chats are removed first, and
  at most 200 chats are kept.

## Keeping up with upstream

`origin` points at upstream and `fork` at this repository:

```sh
git fetch origin
git merge origin/main        # on local-tools
git push                     # local-tools tracks fork/local-tools
```
