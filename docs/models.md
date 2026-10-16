# Models

Zazen runs models in the **GGUF** format (the llama.cpp format; most open models are published in it on
Hugging Face). Models live in your models folder: `%LOCALAPPDATA%\ZenHarness\models` (Settings → Models →
**Open models folder**).

## The model Zazen is built around

Zazen ships set up for one model, **Ternary Bonsai 2 27B** by [PrismML](https://huggingface.co/prism-ml): a 27B model
stored at under 2 bits per weight, which keeps about 98% of the full model's benchmark score in 5.9 GB. The agent's
prompts, checks and memory settings were tuned on it. First-run setup downloads it (see
[Getting started](getting-started.md#first-run)).

It comes in two files holding the same weights, so they give the same answers:

| File | Size | Used on | Why |
|---|---|---|---|
| `PTQ1_0` | 5.9 GB | cards with 8–10 GB | the smallest packing: fits an 8 GB card with room for context |
| `PQ2_0` | 7.2 GB | cards with 11 GB or more | reads prompts about twice as fast (PrismML's measurements) |

Both need PrismML's fork of llama.cpp; the app downloads the right build for your card.

## Hardware: automatic settings

**Settings → Hardware** shows what the app found and the plan it made. On each start it picks:

- **The runtime** for your graphics card: NVIDIA → CUDA, then Vulkan; AMD → HIP, then Vulkan; Intel Arc → Vulkan,
  then SYCL. If a runtime can't see the card, the next one is used.
- **The card**: the largest dedicated one. A laptop's integrated graphics is never used for the model.
- **The model file, context and cache**, by video memory:

| Video memory | File | Context | Cache (keys / values) | Image reading |
|---|---|---|---|---|
| 8–10 GB | PTQ1_0 | up to 64K | 8-bit / 4-bit | CPU |
| 11–13 GB | PQ2_0 | up to 96K | 8-bit / 8-bit | GPU when it fits |
| 14–16 GB | PQ2_0 | up to 128K | 8-bit / 8-bit | GPU |

The context is then fitted to the memory actually free, so the model always stays entirely on the graphics card.
With 16 GB of system RAM, the prompt cache kept in RAM is limited to 3 GB.

**Changing it by hand:** every field in Settings → Hardware (runtime, card, model file, context, both cache halves,
image reading) can be set yourself, and a value you set always wins. Turn off **Choose settings for this hardware
automatically** to use the model's profile exactly as written.

More context means fewer summaries of older steps on long tasks, but each step gets slower as the context fills (on
an 8 GB RTX 3070: about 33 tokens a second near empty, about 14 at 58K).

## Other models

Any GGUF model can still be added and used (below). The built-in harness settings are tuned for Bonsai 2 27B, so
other models may need their own adjustments.

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

### Bigger graphics cards
For models other than Bonsai, the built-in profiles are tuned for an 8 GB card. On a card with more memory the app
uses the extra when it loads one: a full-precision cache, more context (up to 128K or the model's own limit), or
more of a mixture-of-experts model's experts on the GPU. It says so in the status line. Set `ctx_auto` to `false`
(agent settings) to turn it off.

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
