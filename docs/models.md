# Models

Zen Harness runs models in the **GGUF** format (the llama.cpp format; most open models are published in it on
Hugging Face). Models live in your models folder: `%LOCALAPPDATA%\ZenHarness\models` (Settings → Models →
**Open models folder**).

## Which model should I use?

Pick the largest model that **fits your graphics memory (VRAM)** — a model that spills into system RAM gets much
slower.

| Your GPU | Good choice | File size | Notes |
|---|---|---|---|
| No NVIDIA GPU | a 4B model at 4-bit | ~2.5 GB | works on CPU; expect a few words per second |
| 6–8 GB VRAM | a 9B model at 4-bit (IQ4_XS / Q4_K_M) | ~5 GB | fast (50+ words/s on an RTX 3070); the default recommendation |
| 8 GB VRAM + 32 GB RAM | a ~30B "A3B" mixture-of-experts model at 3–4 bit | 9–17 GB | smarter; part of it runs from RAM, so slower |
| 16 GB VRAM | 14B–27B at 4-bit, or 30B-A3B fully on the GPU | 9–17 GB | |
| 24 GB+ VRAM | 27B–32B dense at 4-bit | 16–20 GB | best local quality |

"4-bit", "IQ4_XS", "Q4_K_M" are compression levels (quantization): smaller files, slightly lower quality. 4-bit is the
usual sweet spot; below 3-bit quality drops noticeably, especially for tool use.

## Adding a model

Settings → **Models** → **Add model**, then one of:

- **Pick a file** — choose a `.gguf` you downloaded.
- **Drop it in the folder** — click **Open models folder**, copy the `.gguf` in, then **Refresh**.
- **Download from Hugging Face** — paste a link to a `.gguf` file (for example
  `https://huggingface.co/<org>/<repo>/resolve/main/<file>.gguf`). Downloads resume if interrupted.

When a model is added the app reads its details (architecture, size, layers, whether it is a mixture-of-experts
model) and **proposes settings** for your hardware:

- **Context length** — how much text the model can keep in mind (chat history + files + tool results). Longer needs
  more memory. 32K–64K is plenty for most tasks; long coding sessions benefit from more.
- **KV cache precision** — `f16`/`bf16` (most accurate), `q8_0` (half the memory, nearly as good), `q4_0` (quarter;
  can hurt long agent sessions). Some models need a specific setting; the proposal follows the model's own advice
  when it is known.
- **CPU expert layers** (mixture-of-experts models only) — how many layers' experts stay in system RAM so the rest
  fits on the GPU.

Click **Test load**: the app loads the model, generates a few words, and reports load time, memory use and speed.
If it works, **Save** the profile and optionally **Use as default**.

### Helper files the app picks up automatically
- **Vision**: an `mmproj-*.gguf` file next to the model enables image input.
- **Speed-up draft model**: a matching `mtp-<model>.gguf` file enables speculative decoding (faster generation).

## Cloud models

Settings → General → Model also lists OpenAI and Anthropic models once you add an API key. Cloud models are much
faster and stronger at long tasks, but your messages and the files the agent reads are sent to that provider.

## If a model won't load

See [Troubleshooting → model won't load](troubleshooting.md#a-model-wont-load).
