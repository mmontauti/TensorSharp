# Changelog

Changes in this fork ([mmontauti/TensorSharp](https://github.com/mmontauti/TensorSharp), branch
`local-tools`), followed by recent upstream changes from
[zhongkaifu/TensorSharp](https://github.com/zhongkaifu/TensorSharp) for context. See
[FORK.md](FORK.md) for how to use the fork's additions.

## Fork

### Unreleased

Nothing yet.

### 2026-10-14 — chat history, model controls, remembered skills (`3a239763`)

**Added**

- Web UI: chat history saved in localStorage, with a left pane grouped by date to open and delete
  chats. Generated images, reasoning and stats are kept. Reopening a chat sends its full history to
  the model again. The last chat comes back after a reload. Oldest chats are removed when storage
  fills up (at most 200).
- Web UI: the Agent Skills selection is remembered as the default for new chats and stored with each
  chat. Skills that are no longer installed are dropped before sending.
- Web UI: header dropdown to run and stop model presets through the model switcher, and a link to
  the performance dashboard. Both are hidden when no switcher answers.
- Model switcher: per-preset `env` block for server settings that have no option.
- Model switcher: `--dashboard-url` and `--allow-origin` options.

**Changed**

- Model switcher: answers the chat UI's own address, and refuses stop and start requests from other
  web pages.
- Qwen 3.8 27B preset: adds `--code-exec` (a shell for working with large files) and caps the
  context window at 65,536 tokens with `MAX_CONTEXT`.

### 2026-10-07 — local model tools (`13fd1c8f`)

**Added**

- `eng/model-switcher`: page that stops and starts `TensorSharp.Server.Host` with presets
  (Qwen 3.8 27B with skills, Qwen-Image 2.1, Gemma 4 E4B) on one port.
- `eng/perf-dashboard`: live dashboard over the server log, `/api/queue/status`, and process memory
  and CPU, with per-request chat metrics and per-image timings.
- Web UI: image size dropdown, shown only for image-generation models; sends width and height with
  image generation and editing requests.
- Server: `--assistant-avatar` option (`TS_ASSISTANT_AVATAR`) to replace the assistant's avatar
  image, with tests.

## Upstream (recent)

Merged pull requests on `zhongkaifu/TensorSharp` `main`, newest first, up to the fork point
(`3b403e9f`).

| Date | PR | Change |
|---|---|---|
| 2026-09-22 | [#225](https://github.com/zhongkaifu/TensorSharp/pull/225) | DiffusionGemma as a Jev model |
| 2026-09-22 | [#224](https://github.com/zhongkaifu/TensorSharp/pull/224) | Support Unsloth Qwen-Image 2.1 GGUF models |
| 2026-09-22 | [#223](https://github.com/zhongkaifu/TensorSharp/pull/223) | Fix a CUDA bug in Qwen-Image 2.1 |
| 2026-09-21 | [#221](https://github.com/zhongkaifu/TensorSharp/pull/221) | Replace `edit_file` with `apply_patch` |
| 2026-09-18 | [#220](https://github.com/zhongkaifu/TensorSharp/pull/220) | Experimental CUDA 13 GB10 / DGX Spark support |
| 2026-09-17 | [#219](https://github.com/zhongkaifu/TensorSharp/pull/219) | Radix cache for speculative decoding |
| 2026-09-17 | [#218](https://github.com/zhongkaifu/TensorSharp/pull/218) | Fix TensorAgent launch crash on iOS 27 |
| 2026-09-17 | [#216](https://github.com/zhongkaifu/TensorSharp/pull/216) | Replace deprecated ggml precision APIs and scope Metal SDK warnings |
| 2026-09-17 | [#215](https://github.com/zhongkaifu/TensorSharp/pull/215) | Fix build break on macOS 27 |
| 2026-09-17 | [#214](https://github.com/zhongkaifu/TensorSharp/pull/214) | Fix build break and optimizations |
| 2026-09-15 | [#213](https://github.com/zhongkaifu/TensorSharp/pull/213) | Support embedding models |
| 2026-09-15 | [#208](https://github.com/zhongkaifu/TensorSharp/pull/208) | Fix 6 tests failing on Windows due to CR/LF differences |
| 2026-09-14 | [#212](https://github.com/zhongkaifu/TensorSharp/pull/212) | Add new book |
| 2026-09-14 | [#210](https://github.com/zhongkaifu/TensorSharp/pull/210) | Resolve TensorSharp.Server warnings |
| 2026-09-12 | [#206](https://github.com/zhongkaifu/TensorSharp/pull/206) | Update README |
| 2026-09-12 | [#205](https://github.com/zhongkaifu/TensorSharp/pull/205) | Update documents |
| 2026-09-12 | [#204](https://github.com/zhongkaifu/TensorSharp/pull/204) | Optimize DeepSeek V4.1 Flash |
| 2026-09-12 | [#203](https://github.com/zhongkaifu/TensorSharp/pull/203) | DeepSeek V4.1 Flash: faster model load and inference, KV cache reuse fix |
| 2026-09-10 | [#202](https://github.com/zhongkaifu/TensorSharp/pull/202) | Fix iOS build break |
| 2026-09-10 | [#201](https://github.com/zhongkaifu/TensorSharp/pull/201) | Add Hunyuan Dense so Hy-MT2 GGUFs can run |
| 2026-09-10 | [#200](https://github.com/zhongkaifu/TensorSharp/pull/200) | TensorAgent for iOS |
| 2026-09-05 | [#198](https://github.com/zhongkaifu/TensorSharp/pull/198) | Make TensorSharp.Core nullable-enabled and warning free |

For the full upstream history, see the
[upstream commits](https://github.com/zhongkaifu/TensorSharp/commits/main).
