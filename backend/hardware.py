"""What this PC can run, for any GPU vendor (plans/hardware-scaling.md).

  gpus()          every display adapter Windows knows: vendor, name, memory, driver (no vendor tools needed)
  primary()       the GPU the model runs on: the largest dedicated one, or the one chosen in settings
  plan(...)       for a model profile: the runtime (CUDA / HIP / Vulkan / SYCL / CPU), the model file, context, cache
                  types and where vision runs, sized for that GPU and this PC's RAM
  apply(cfg)      the plan merged into a model profile, under the user's own hardware settings

Memory sizes come from the registry's 64-bit HardwareInformation.qwMemorySize (Win32_VideoController's AdapterRAM is
32-bit and stops at 4 GB). NVIDIA's exact total comes from nvidia-smi when it is there.
"""
import os
import re
import subprocess
import time

VENDORS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel"}
MIN_VRAM_MIB = 7680                 # the 8 GB floor (8 GB cards report 8176-8192 MiB)
INTEGRATED_MAX_MIB = 2048           # an integrated GPU reports a small carve-out of system RAM
# llama.cpp's own GPU buffers beside weights and cache (CUDA context, scratch, compute graphs), by runtime. CUDA is
# measured on the RTX 3070; the others are estimates until measured on their cards.
OVERHEAD_MIB = {"cuda": 450, "hip": 550, "vulkan": 650, "sycl": 750, "cpu": 0}
# preferred runtime first; the next one is tried when a runtime can't see the GPU
BACKENDS = {"nvidia": ["cuda", "vulkan"], "amd": ["hip", "vulkan"], "intel": ["vulkan", "sycl"]}
DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

_cache = {"t": 0.0, "gpus": None}


def _registry_gpus():
    import winreg
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS)
    except OSError:
        return out
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    def val(name):
                        try:
                            return winreg.QueryValueEx(k, name)[0]
                        except OSError:
                            return None
                    dev = str(val("MatchingDeviceId") or "")
                    name = str(val("DriverDesc") or "")
                    mem = val("HardwareInformation.qwMemorySize")
                    if mem is None:
                        mem = val("HardwareInformation.MemorySize")
                    driver = str(val("DriverVersion") or "")
            except OSError:
                continue
            m = re.search(r"ven_([0-9a-f]{4})", dev, re.I)
            vendor = VENDORS.get(m.group(1).lower()) if m else None
            if not vendor or not name:
                continue                              # the basic display driver, remote-desktop adapters
            if isinstance(mem, bytes):
                mem = int.from_bytes(mem[:8], "little")
            mib = int(mem or 0) // 1048576
            out.append({"vendor": vendor, "name": name, "vram_mib": mib, "driver": driver,
                        "device_id": dev.lower(), "integrated": mib < INTEGRATED_MAX_MIB})
    return out


def _nvidia_smi():
    """[(name, total_mib)] from nvidia-smi, or []."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
    except Exception:
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.rsplit(",", 1)]
        if len(parts) == 2 and parts[1].isdigit():
            rows.append((parts[0], int(parts[1])))
    return rows


def gpus(refresh=False):
    """Display adapters, largest dedicated first. Cached for a minute (the registry read is cheap, nvidia-smi isn't)."""
    if not refresh and _cache["gpus"] is not None and time.time() - _cache["t"] < 60:
        return [dict(g) for g in _cache["gpus"]]
    found = _registry_gpus()
    smi = _nvidia_smi() if any(g["vendor"] == "nvidia" for g in found) else []
    for g in found:
        if g["vendor"] == "nvidia":
            hit = next((mib for name, mib in smi if name.lower() in g["name"].lower()
                        or g["name"].lower() in name.lower()), None)
            if hit:
                g["vram_mib"] = hit
    found.sort(key=lambda g: (g["integrated"], -g["vram_mib"]))
    _cache.update(t=time.time(), gpus=found)
    return [dict(g) for g in found]


def _settings():
    try:
        import web_backend as wb
        return dict((wb.STORE.settings.get("agent") or {}).get("hardware") or {})
    except Exception:
        return {}


def primary(hw=None):
    """The GPU the model runs on: the one chosen in settings (hardware.gpu = an index or part of its name), else the
    largest dedicated GPU. None when there is no dedicated GPU."""
    hw = _settings() if hw is None else hw
    found = gpus()
    want = str(hw.get("gpu") or "auto").strip()
    if want and want.lower() != "auto":
        if want.isdigit() and int(want) < len(found):
            return found[int(want)]
        hit = next((g for g in found if want.lower() in g["name"].lower()), None)
        if hit:
            return hit
    return next((g for g in found if not g["integrated"]), None)


def system_ram_mib():
    import llama_local
    return (llama_local.system_ram_mib() or (0, 0))[0]


def backend_order(gpu, hw=None):
    """Runtimes to try for this GPU, best first. A runtime set in settings comes first."""
    hw = _settings() if hw is None else hw
    order = list(BACKENDS.get((gpu or {}).get("vendor"), [])) + ["cpu"]
    want = str(hw.get("backend") or "auto").lower()
    if want in OVERHEAD_MIB:
        order = [want] + [b for b in order if b != want]
    return order


# ---------------------------------------------------------------------------------------------------------- planning
def tier(vram_mib):
    """The settings band for this much GPU memory: (context cap, K cache, V cache, prefer the bigger file)."""
    if vram_mib >= 14 * 1024:
        return 131072, "q8_0", "q8_0", True
    if vram_mib >= 11 * 1024:
        return 98304, "q8_0", "q8_0", True
    return 65536, "q8_0", "q4_0", False


def choose_file(variants, vram_mib, backend, have=os.path.isfile):
    """The model file for this GPU from a profile's variants ([{file, min_vram_mib, ...}], best first): the best one
    whose memory need fits, preferring a file already on disk when the better one isn't downloaded yet.
    -> (file, better_file_not_downloaded or None)."""
    import paths
    fits = [v for v in variants if vram_mib >= int(v.get("min_vram_mib") or 0)
            and not (backend == "sycl" and v.get("sycl_expands"))]
    if not fits:
        fits = variants[-1:]
    best = fits[0]
    if have(paths.resolve_model(best["file"])):
        return best["file"], None
    on_disk = next((v for v in fits if have(paths.resolve_model(v["file"]))), None)
    return (on_disk or best)["file"], (best["file"] if on_disk else None)


def plan(profile, gpu=None, backend=None, ram_mib=None, have=os.path.isfile):
    """Settings for `profile` on this PC. -> dict with backend, device name, model_file (+ better_file when a faster
    file of the same weights isn't downloaded), num_ctx cap, kv_cache, kv_cache_v, vision_gpu, cache_ram_mib,
    overhead_mib and a one-line note. Context is a cap: llama_local.fit_context still fits it to the memory.
    gpu: None = detect it; False = no GPU."""
    gpu = primary() if gpu is None else (gpu or None)
    vram = int((gpu or {}).get("vram_mib") or 0)
    backend = backend or (backend_order(gpu)[0] if gpu else "cpu")
    ram = system_ram_mib() if ram_mib is None else int(ram_mib)
    ctx, kv, kv_v, big = tier(vram)
    out = {"backend": backend, "gpu": (gpu or {}).get("name", ""), "vram_mib": vram, "num_ctx": ctx,
           "kv_cache": kv, "kv_cache_v": kv_v, "overhead_mib": OVERHEAD_MIB.get(backend, 600),
           # llama.cpp keeps old prompt states in RAM: 3 GB fits beside Windows, the browser and the app at 16 GB
           "cache_ram_mib": 3072 if ram < 24 * 1024 else 6144 if ram < 48 * 1024 else 8192}
    variants = profile.get("variants") or []
    if variants:
        f, better = choose_file(variants, vram, backend, have=have)
        out["model_file"] = f
        if better:
            out["better_file"] = better
    weights = next((int(v.get("weights_mib") or 0) for v in variants if v.get("file") == out.get("model_file")), 0)
    # vision on the GPU when the projector fits beside the model at a full tier context; otherwise the CPU (slower)
    proj = int(profile.get("mmproj_mib") or 640)
    spare = vram - weights - out["overhead_mib"] - 200 - _kv_mib(profile, ctx, kv, kv_v)
    out["vision_gpu"] = bool(vram and weights and big and spare >= proj + 256)
    if not gpu or vram < MIN_VRAM_MIB:
        out["note"] = ("No dedicated GPU with 8 GB or more was found" + (f" ({gpu['name']}, {vram // 1024} GB)" if gpu
                       else "") + ": the model runs on the CPU, which is very slow.")
        out["backend"], out["num_ctx"], out["vision_gpu"] = "cpu", 16384, False
    else:
        out["note"] = (f"{gpu['name']} ({vram / 1024:.0f} GB), {backend.upper() if backend != 'vulkan' else 'Vulkan'}"
                       f": {os.path.basename(out.get('model_file') or profile.get('model_file') or '')}, up to "
                       f"{ctx // 1024}K context, vision on the {'GPU' if out['vision_gpu'] else 'CPU'}")
    return out


def _kv_mib(profile, ctx, kv, kv_v):
    """KV cache plus compute buffers at ctx: from the model file's own attention layout when it is on disk, else the
    profile's kv_mib_per_1k (q8_0 K and V; the 27B: 16 attention layers x 4 heads x 512 values = 34 MiB per 1K)."""
    import llama_local as L
    import paths
    per_tok = 0.0
    for f in [profile.get("model_file")] + [v.get("file") for v in profile.get("variants") or []]:
        p = paths.resolve_model(f) if f else ""
        if p and os.path.isfile(p):
            try:
                meta = L.gguf_meta(p)
                per_tok = (L.kv_bytes_per_token(meta, kv) + L.kv_bytes_per_token(meta, kv_v or kv)) / 2
                break
            except Exception:
                pass
    if not per_tok:
        scale = (L.KV_BYTES.get(kv, 1.0625) + L.KV_BYTES.get(kv_v or kv, 1.0625)) / (2 * 1.0625)
        per_tok = float(profile.get("kv_mib_per_1k") or 0) * 1048576 / 1024 * scale
    return int((per_tok + L.compute_per_token(None)) * ctx / 1048576)


HW_KEYS = ("backend", "gpu", "model_file", "num_ctx", "kv_cache", "kv_cache_v", "vision_gpu")


def wants_plan(cfg):
    hw = dict(cfg.get("hardware") or {})
    return hw.get("auto", True) is not False and bool(cfg.get("variants") or cfg.get("runtime"))


def apply(cfg, backend=None, runtime_dir=None, gpu=None):
    """The plan over the profile already merged into cfg, then the user's own hardware settings over the plan.
    Only for profiles that declare a runtime ("prism") or variants; with hardware.auto false nothing changes.
    -> the plan (with note), or None."""
    hw = dict(cfg.get("hardware") or {})
    if not wants_plan(cfg):
        return None
    p = plan(cfg, gpu=gpu, backend=backend or (None if str(hw.get("backend") or "auto") == "auto"
                                                else str(hw["backend"])))
    for k in HW_KEYS:                                 # a value set by hand always wins
        v = hw.get(k)
        if v not in (None, "", "auto"):
            p[k] = v
    cfg["_overhead_mib"] = int(p["overhead_mib"])
    if runtime_dir:
        cfg["llama_dir"] = runtime_dir
    if not cfg.get("variants"):
        # a profile that only names its runtime (the Bonsai 9B): the runtime and GPU for this PC, its own tuned
        # context and cache (the tiers are the 27B's)
        return p
    if p.get("model_file"):
        cfg["model_file"] = p["model_file"]
    cfg["num_ctx"] = int(p["num_ctx"])
    cfg["kv_cache"], cfg["kv_cache_v"] = p["kv_cache"], p["kv_cache_v"]
    # the profile's own prompt-cache size, but never more than this PC's RAM allows
    cfg["cache_ram_mib"] = min(int(cfg.get("cache_ram_mib") or p["cache_ram_mib"]), int(p["cache_ram_mib"]))
    cfg["vision_gpu"] = bool(p["vision_gpu"]) if not isinstance(p["vision_gpu"], str) else p["vision_gpu"] == "on"
    cfg["_auto_ctx"] = True                           # the planner sized it: no separate bigger-card pass
    return p
