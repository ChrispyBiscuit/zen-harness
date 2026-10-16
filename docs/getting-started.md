# Getting started

Zazen is an AI assistant that runs on your own PC. It uses open-weight language models through
[llama.cpp](https://github.com/ggml-org/llama.cpp), so your chats and files stay on your computer unless you choose a
cloud model or a tool that uses the internet (web search, web pages).

## What you need

| | Requirement |
|---|---|
| Windows | 10 (22H2) or 11, 64-bit |
| Graphics card | A dedicated NVIDIA, AMD or Intel Arc card with 8 GB or more of video memory (up to 16 GB supported) |
| Memory (RAM) | 16 GB |
| Disk | About 10 GB free, plus 14 GB if you want offline Wikipedia |

NVIDIA is tested (RTX 3070 Laptop, 8 GB). AMD and Intel Arc are supported through PrismML's HIP, Vulkan and SYCL
builds of llama.cpp but have not yet been tested on real cards. With less than 8 GB, integrated graphics only, or no
graphics card, the model runs on the CPU: it works, very slowly.

## Install

1. Run `ZenHarness-Setup-<version>.exe`. Windows may show "Windows protected your PC" because the alpha is not
   code-signed yet: click **More info → Run anyway**.
2. The installer needs no administrator rights and downloads nothing heavy. It installs to
   `%LOCALAPPDATA%\Programs\Zen Harness` and adds a Start-menu entry (and a desktop shortcut if you ticked it).
3. Launch **Zazen**.

## First run

The first time, the **Welcome** screen checks this PC and sets it up:

1. It finds your graphics card (any vendor), its video memory and your RAM, and shows the plan: which runtime, which
   model file, how much context, and where image reading runs.
2. Click **Set up this PC**. It downloads, once:
   - the llama.cpp runtime for your card (from 30 MB for Vulkan to about 700 MB for CUDA),
   - Ternary Bonsai 2 27B in the size that fits (5.9 GB, or 7.2 GB on cards with 11 GB or more),
   - its image reader (0.6 GB),
   - offline Wikipedia (14 GB), if you leave it ticked. It downloads last, after the model is ready.
3. Each file is checked against its published checksum. Downloads resume if interrupted, and you can keep using the
   app while Wikipedia finishes.

If the first runtime can't see your card, setup tries the next one (for example Vulkan after HIP on an AMD card).
Everything chosen here can be changed later in **Settings → Hardware**.

Other choices on the Welcome screen:
- **Other models instead** — the catalog of other models, or a `.gguf` file you already have (see [Models](models.md)).
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

- **Settings** — the gear icon. Models, hardware, permissions, memory, voice, apps that connect to Zazen.
- **Help** — the `?` icon: these documents, the About box, and "Copy diagnostics" for bug reports.
- **Your files** — chats, settings and memory are in `%LOCALAPPDATA%\ZenHarness` (see
  [Your data and privacy](data-and-privacy.md)). Uninstalling keeps them unless you tick "remove my data".
