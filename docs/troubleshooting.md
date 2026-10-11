# Troubleshooting

First step for any problem: **Help → Copy diagnostics** — it copies versions, your GPU, paths and the last log lines
(tokens and keys removed). Paste that into a bug report.

## The app doesn't open / the window is blank
- Wait 10–20 seconds on the first start (it unpacks and starts its server).
- Another copy may be stuck: open Task Manager, end `ZenHarness.exe` and `llama-server.exe`, start again.
- Logs: `%LOCALAPPDATA%\ZenHarness\logs\app_window.log` and `app_server.log`.

## "127.0.0.1 didn't send any data" / the app can't reach its server
Something else is using port 8765. Close other copies of Zen Harness, or change the port in Settings → Connect apps
(then restart). Programs the agent built should use other ports; the app stops any of them that grab its own ports.

## A model won't load
- **Out of memory / "bad allocation"** — the model plus its context is too big. In Settings → Models choose a smaller
  context (e.g. 32K), a lighter KV cache (`q8_0`), or a smaller model/quantization. Mixture-of-experts models: raise
  **CPU expert layers**.
- **Very slow (1–5 words/s) on an NVIDIA GPU** — the model spilled out of video memory. Use a smaller context or
  model. Close other GPU-heavy programs (games, browsers with many video tabs).
- **No NVIDIA GPU** — models run on the CPU; use a small (≤ 4B) model.
- **Old NVIDIA driver** — update it from nvidia.com (CUDA 12 support is needed).
- **Your PC freezes with a large model** — system RAM ran out. Large mixture-of-experts models need 32 GB RAM to be
  comfortable; on 16 GB, use a smaller model or context.

## Answers are wrong or it goes in circles
Small local models make mistakes on long tasks. Give it a concrete hint, ask for one step at a time, switch that chat
to a larger model, or enable the cloud advisor (Settings → Agent).

## Web search doesn't work
It needs internet access. Search engines occasionally rate-limit; wait a minute and retry.

## Other apps can't connect (API/MCP)
- Settings → Connect apps → **Test connection**.
- Check the token (regenerating it breaks old configs) and the port.
- If the app is closed, turn on **Run in the background at sign-in**, or use MCP over stdio (it starts the app).

## Starting over
Quit the app, rename `%LOCALAPPDATA%\ZenHarness\settings.json` (your chats and models stay), start again — you get
default settings and the Welcome screen.
