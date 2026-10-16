# Models

Zazen runs models in the **GGUF** format (the llama.cpp format; most open models are published in it on
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

### Bigger graphics cards: automatic sizing
The built-in profiles are tuned for an 8 GB card. On a card with more memory (12, 16, 24 GB…) the app uses the extra
automatically when it loads a model, and says so in the status line:
- **Most models**: a full-precision cache first (better quality) if it still holds the tuned context, otherwise more
  context with the tuned cache — up to 128K tokens or the model's own limit.
- **Mixture-of-experts models** (e.g. Qwen3.6 35B-A3B): more of the model's experts go on the GPU instead of system
  RAM, which is faster.

Calculated from the model files (not yet measured on such cards): a 12 GB card gets Bonsai 2 27B at 84K with a full-precision cache and the 35B
with every expert on the GPU; 16 GB gets Bonsai at 128K. Your own profiles are left as you set them. To turn it off
set `ctx_auto` to `false` (agent settings); `ctx_auto_max` changes the 128K cap.

### Models that need their own llama.cpp build
Some models only run on a fork of llama.cpp (for example Ternary Bonsai 2 needs PrismML's build for its 1-bit
format). Their entry in `settings.json` → `agent.model_overrides.<model>` can carry:

- `llama_dir` — folder holding that build's `llama-server.exe` (and its CUDA DLLs). Used for this model only.
- `kv_cache_v` — a separate precision for the V half of the cache (for example `kv_cache` `q8_0` + `kv_cache_v`
  `q4_0`). Bonsai 2 on PrismML's build produces garbage on long prompts with a same-type quantized cache
  (q8/q8, q4/q4); mixed types or f16 work.
- `strict_template: true` — sends exactly one leading system message and always-valid JSON tool arguments, for chat
  templates that reject anything else with an HTTP 500.
- `reasoning_off_field: true` — turns thinking off with `reasoning_effort: "none"` as well, for templates that
  ignore `enable_thinking`.

## Cloud models

Settings → General → Model also lists OpenAI and Anthropic models once you add an API key. Cloud models are much
faster and stronger at long tasks, but your messages and the files the agent reads are sent to that provider.

## If a model won't load

See [Troubleshooting → model won't load](troubleshooting.md#a-model-wont-load).
