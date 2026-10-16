# Hardware scaling: NVIDIA, AMD and Intel, 8 to 16 GB

Status: built 2026-10-16. Settings: `agent.hardware` (auto by default; every field can be set by hand).

## Target

The app ships one model, Ternary Bonsai 2 27B. Minimum: a dedicated GPU with 8 GB. Supported range: 8 to 16 GB of
GPU memory, 16 GB of system RAM. NVIDIA, AMD and Intel. Nothing heavy ships in the installer: the llama.cpp runtime,
the model and the offline Wikipedia download on first run, for the hardware found.

## Facts this is built on

- PrismML's fork is required for the 27B's files (PTQ1_0 and PQ2_0). Its Windows releases have a CUDA, a HIP
  (AMD), a SYCL (Intel), a Vulkan and a CPU build. Its backend table: CUDA and HIP fully implemented; Vulkan and
  SYCL implemented, "needs validation" (BACKEND-SUPPORT.md, release `prism-b10777-e3de715`).
- PTQ1_0 (5.95 GB) and PQ2_0 (7.21 GB) hold the same weights: same answers. PQ2_0 reads prompts about twice as fast
  everywhere and generates faster on Ampere. On SYCL's XMX path PTQ1_0 expands to PQ2_0's size in GPU memory.
- Windows lists every display adapter with vendor id and a 64-bit memory size in the registry
  (`HardwareInformation.qwMemorySize`), with no vendor tool. `llama-server --list-devices` lists what a given
  runtime can use, with total and free memory. A laptop's integrated GPU appears there too: with Vulkan, llama.cpp
  would split the model across both, so the dedicated GPU is always passed with `--device`.

## Decisions

| | NVIDIA | AMD | Intel Arc |
|---|---|---|---|
| Backend, then fallback | CUDA 12.4, Vulkan | HIP, Vulkan | Vulkan, SYCL |
| Download | 314 + 391 MB (runtime + CUDA DLLs) | 343 MB | 31 MB |

Vulkan comes first for Intel because SYCL makes the smaller file as large as the bigger one (an 8 GB Arc would not
fit), and PrismML's own scripts don't select SYCL yet. Each backend is checked after download: the GPU must appear
in its device list, or the next backend is tried.

| GPU memory | File | Context | Cache (K / V) | Vision |
|---|---|---|---|---|
| 8 to 10 GB | PTQ1_0 | up to 64K | q8 / q4 | CPU |
| 11 to 13 GB | PQ2_0 | up to 96K | q8 / q8 | GPU when 0.9 GB is left |
| 14 GB and up | PQ2_0 | up to 128K | q8 / q8 | GPU |

The context is then fitted to the real memory by `llama_local.fit_context`, with a per-backend allowance for its
own buffers. System RAM sets the prompt cache kept in RAM: 3 GB at 16 GB, more on bigger machines.

## Manual settings

`agent.hardware`: `auto` (true), and `backend`, `gpu`, `model_file`, `num_ctx`, `kv_cache`, `kv_cache_v`,
`vision_gpu`, each "auto" or a value. A value always wins over the detected plan. With `auto: false` the model's
profile is used exactly as written, which is the behaviour before this change.

## Not covered

- Tested on hardware: NVIDIA (RTX 3070, 8 GB) only. AMD and Intel paths are built from PrismML's documentation and
  the Windows interfaces; they need a run on real cards before release.
- Per-backend memory allowances other than CUDA are estimates until measured.
- Cards under 8 GB and integrated GPUs are not supported targets; the app says so and offers the CPU build.
