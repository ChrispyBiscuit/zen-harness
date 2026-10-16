# Zen Harness — to-do

Items the user has queued for later. Newest first.

## macOS on Apple Silicon (queued 2026-10-16; not started)

A Mac version of the app. Estimate: 3 to 5 weeks to a tester-quality alpha, with a Mac to work and test on and an
Apple Developer account ($99/year) for signing.

- **Already portable:** the Python backend, the React UI, the Qt window (PySide6), and the model runtime: PrismML
  ships macOS arm64 builds of its llama.cpp fork, and every Bonsai format is fully supported on Metal.
- **The agent's terminal** (the largest item, about a week): it is PowerShell today, in 68 lines across 15 files plus
  the tool descriptions and guides. A Mac needs a zsh/bash shell tool with its own prompts and guides, and new test
  runs of the 27B against it (the harness was tuned on PowerShell's behaviour).
- **Processes:** Windows process flags (38 lines in 23 files), `taskkill`, `.exe` names (93 lines in 18 files). One
  platform layer; 2 to 4 days.
- **Hardware:** unified memory. Size for the share of RAM the GPU may use (about 65 to 75%); the 27B needs a 16 GB
  Mac. hardware.py and runtimes.py get a Metal path; 1 to 2 days.
- **Windows-only features:** pywin32, Office automation in doc_create.py, desktop screen control, Startup-folder
  autostart, tray. Replace (login item, non-Office export) or switch off; 2 to 4 days.
- **Paths and browser:** `~/Library/Application Support`, Chrome or bundled Chromium instead of Edge; 1 day.
- **Packaging:** PyInstaller `.app` in a DMG, signed and notarized (without it Gatekeeper blocks the app); 3 to 5
  days. The installed-build updater question applies here too.

Cheaper first step whenever convenient, on Windows: put the shell, process and path code behind one platform layer
(2 to 3 days). It tidies the code and removes most of the risk from the port.
