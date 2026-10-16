# Getting started

Zen is an AI assistant that runs on your own PC. It uses open-weight language models through
[llama.cpp](https://github.com/ggml-org/llama.cpp), so your chats and files stay on your computer unless you choose a
cloud model or a tool that uses the internet (web search, web pages).

## What you need

| | Minimum | Recommended |
|---|---|---|
| Windows | 10 (22H2) or 11, 64-bit | 11 |
| Memory (RAM) | 16 GB | 32 GB |
| Graphics card | none (runs on the CPU, slowly) | NVIDIA with 8 GB+ VRAM (RTX 3060 / 3070 / 4060 or better) |
| Disk | 10 GB free | 30 GB+ (models are 5–20 GB each) |

NVIDIA cards need a reasonably recent driver (2024 or newer). AMD and Intel GPUs are not used in this alpha; the app
runs on the CPU instead.

## Install

1. Run `ZenHarness-Setup-<version>.exe`. Windows may show "Windows protected your PC" because the alpha is not
   code-signed yet: click **More info → Run anyway**.
2. The installer needs no administrator rights. It installs to `%LOCALAPPDATA%\Programs\Zen Harness` and adds a
   Start-menu entry (and a desktop shortcut if you ticked it).
3. Launch **Zen**.

## First run

The first time, the app has no model yet and shows the **Welcome** screen:

1. It checks your hardware (GPU, video memory, RAM, free disk) and recommends models that fit.
2. Pick one and click **Download**. The recommended small model is about 5 GB; the download can be paused and resumed.
3. When it finishes, the app works out the best settings for your card (context length, memory layout), loads the
   model once to test it, and you're ready.

Other choices on the Welcome screen:
- **I have a model file** — pick a `.gguf` file you already downloaded (see [Models](models.md)).
- **Use a cloud model** — paste an OpenAI or Anthropic API key and use their models instead of a local one.

## Your first tasks

Type in the box at the bottom and press Enter. Try:

- *"What's the weather in Chicago today?"* — searches the web and answers with sources.
- *"Make a spreadsheet of my monthly budget: rent 1200, food 400, transport 150"* — creates an Excel file.
- *"Write a Python script that renames the photos in my Pictures folder by date, then run it on a copy"* — writes
  code, runs it, checks the result.
- *"Summarise this PDF"* — drag a file onto the window first.

The agent asks before doing anything risky (deleting, running unknown programs, touching files outside your
allowed folders). See [Using the agent](agent.md).

## Where things are

- **Settings** — the gear icon. Models, permissions, memory, voice, apps that connect to Zen.
- **Help** — the `?` icon: these documents, the About box, and "Copy diagnostics" for bug reports.
- **Your files** — chats, settings and memory are in `%LOCALAPPDATA%\ZenHarness` (see
  [Your data and privacy](data-and-privacy.md)). Uninstalling keeps them unless you tick "remove my data".
