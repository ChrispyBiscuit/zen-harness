"""Run the official llama.cpp server for the swarm (faster than Ollama on the same GGUF).

The swarm names such a model "llama:<ollama model name>", e.g.
"llama:qwen3:4b-instruct-2507-q4_K_M". The GGUF file is read straight from Ollama's model store
(nothing is duplicated), the server is started on first use and stopped when the game ends.
Install: unzip a llama.cpp *win-cuda* release into %USERPROFILE%\\llama.cpp (or set LLAMA_CPP_DIR),
next to llama-server.exe keep the CUDA runtime DLLs (cudart64_12 / cublas64_12 / cublasLt64_12).
"""
import atexit
import json
import re
import os
import socket
import subprocess
import threading
import time
import urllib.request

PORT = 8099                      # preferred port; a free one is used if it is taken
_lock = threading.Lock()
# one server per slot: "main" (the agent's model) and "exec" (the small executor of the hybrid mode)
_slots = {"main": {"proc": None, "key": None, "log": None, "port": PORT},
          "exec": {"proc": None, "key": None, "log": None, "port": PORT + 1},
          "speech": {"proc": None, "key": None, "log": None, "port": PORT + 3},
          "gopher": {"proc": None, "key": None, "log": None, "port": PORT + 5},     # fan-out workers (CPU)
          "test": {"proc": None, "key": None, "log": None, "port": PORT + 7}}       # Settings → Models → Test load
_state = _slots["main"]


def _free_port(preferred):
    """The preferred port if nothing is listening on it, otherwise any free port. (Never talk
    to somebody else's server on a shared port: it may be serving a different model.)"""
    for port in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


_job = None


def _bind_to_app(proc):
    """Put the server in a Windows job object that kills it when this app's process ends — however it ends
    (crash, taskkill /F, closed window). atexit/stop() only cover a clean exit."""
    global _job
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job is None:
            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                            "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class EXT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            info = EXT()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info),
                                                          ctypes.sizeof(info)):
                return
            _job = job                     # never closed: the OS closes it (and kills the servers) when we exit
        k32.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(int(proc._handle)))
    except Exception:
        pass


def reap_orphans():
    """Kill llama-server processes whose parent app is gone (left by a crash before job objects, or by another
    run). A server whose parent is still running belongs to that program and is left alone. -> killed pids"""
    killed = []
    try:
        import psutil
        for p in psutil.process_iter(["name", "ppid", "create_time"]):
            if (p.info["name"] or "").lower() != "llama-server.exe":
                continue
            try:
                parent = psutil.Process(p.info["ppid"])
                alive = parent.create_time() <= p.info["create_time"] + 1     # not a reused pid
            except psutil.Error:
                alive = False
            if not alive:
                p.kill()
                killed.append(p.pid)
        if killed:
            time.sleep(1.0)                # let the GPU memory and the port be released
    except Exception:
        pass
    return killed


def _state_dir():
    """Logs and pid files: the data folder (the llama.cpp folder is read-only in an installed app)."""
    import paths
    d = paths.data("llama_state")
    os.makedirs(d, exist_ok=True)
    return d


def _pid_file(slot="main"):
    return os.path.join(_state_dir(), "swarm_server.pid" if slot == "main" else f"swarm_server_{slot}.pid")


def _kill_stale(slot="main"):
    """A server this app started in an earlier run that was never stopped (crash, kill)."""
    try:
        with open(_pid_file(slot)) as f:
            pid = int(f.read().strip())
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
        if "llama-server" in out:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10,
                           creationflags=0x08000000)
        os.remove(_pid_file(slot))
    except Exception:
        pass


def llama_dir():
    import paths
    return paths.llama_dir()


def exe_path():
    return os.path.join(llama_dir(), "llama-server.exe")


def available():
    return os.path.exists(exe_path())


import paths
MODELS_DIR = paths.models_dir()


def local_gguf_models():
    """GGUF files in ~/models (downloaded outside Ollama), as full paths. Vision projectors and
    mtp-* speculative drafters are helpers loaded alongside a model, not models to chat with."""
    try:
        return sorted(os.path.join(MODELS_DIR, f) for f in os.listdir(MODELS_DIR)
                      if f.lower().endswith(".gguf") and "mmproj" not in f.lower()
                      and not f.lower().startswith("mtp-"))
    except OSError:
        return []


def ollama_blob(name):
    """Path of the GGUF weights Ollama stores for `name` (e.g. 'qwen3:4b-instruct-2507-q4_K_M').
    A path to a .gguf file is used as is."""
    if str(name).lower().endswith(".gguf") and os.path.isfile(name):
        return name
    root = os.path.join(os.environ.get("OLLAMA_MODELS") or
                        os.path.join(os.path.expanduser("~"), ".ollama", "models"))
    repo, _, tag = name.partition(":")
    tag = tag or "latest"
    parts = repo.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library", repo]
    elif "." in parts[0]:                 # a registry host, e.g. hf.co/user/model:quant
        pass
    else:
        parts = ["registry.ollama.ai"] + parts
    manifest = os.path.join(root, "manifests", *parts, tag)
    with open(manifest, encoding="utf-8") as f:
        m = json.load(f)
    for layer in m.get("layers", []):
        if str(layer.get("mediaType", "")).endswith("image.model"):
            return os.path.join(root, "blobs", layer["digest"].replace(":", "-"))
    raise RuntimeError(f"No model weights found in the Ollama manifest for {name}")


def ollama_projector(name):
    """Path of the vision projector (mmproj) Ollama stores with `name`, or None if it has none."""
    if str(name).lower().endswith(".gguf"):
        d = os.path.dirname(str(name))
        cands = [f for f in os.listdir(d) if "mmproj" in f.lower() and f.lower().endswith(".gguf")] if os.path.isdir(d) else []
        return os.path.join(d, cands[0]) if cands else None
    try:
        weights = ollama_blob(name)
    except Exception:
        return None
    root = os.path.dirname(os.path.dirname(weights))
    repo, _, tag = name.partition(":")
    parts = repo.split("/")
    if len(parts) == 1:
        parts = ["registry.ollama.ai", "library", repo]
    elif "." not in parts[0]:
        parts = ["registry.ollama.ai"] + parts
    try:
        with open(os.path.join(root, "manifests", *parts, tag or "latest"), encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        return None
    for layer in m.get("layers", []):
        if str(layer.get("mediaType", "")).endswith("image.projector"):
            return os.path.join(root, "blobs", layer["digest"].replace(":", "-"))
    return None


def gguf_meta(path, keys=("block_count", "head_count_kv", "key_length", "value_length", "full_attention_interval",
                          "embedding_length", "architecture", "head_count", "sliding_window", "expert_count",
                          "expert_used_count", "nextn_predict_layers", "sliding_window_pattern", "key_length_swa",
                          "value_length_swa", "context_length")):
    """The scalar metadata of a GGUF file whose key ends with one of `keys` (reads only the header)."""
    import struct
    sz = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    fm = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    out = {}
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            return out
        f.read(4)
        _nt, nkv = struct.unpack("<QQ", f.read(16))

        def rs():
            n, = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", "replace")

        def rv(t):
            if t == 8:
                return rs()
            if t == 9:
                it, = struct.unpack("<I", f.read(4))
                n, = struct.unpack("<Q", f.read(8))
                if it == 8:
                    for _ in range(n):
                        rs()
                elif it in sz and n <= 1024:          # small per-layer arrays (head counts, window pattern)
                    return [struct.unpack(fm[it], f.read(sz[it]))[0] for _ in range(n)]
                elif it in sz:
                    f.seek(sz[it] * n, 1)
                else:
                    for _ in range(n):
                        rv(it)
                return None
            return struct.unpack(fm[t], f.read(sz[t]))[0]
        for _ in range(nkv):
            k = rs()
            t, = struct.unpack("<I", f.read(4))
            v = rv(t)
            short = k.split(".")[-1]
            if v is not None and (short in keys or k.endswith(tuple("." + x for x in keys))):
                out[k.split(".", 1)[-1] if "." in k else k] = v
    return out


def gguf_tensor_sizes(path):
    """{tensor name: bytes} read from a GGUF file's header (sizes from the data offsets)."""
    import struct
    sz = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            return {}
        f.read(4)
        nt, nkv = struct.unpack("<QQ", f.read(16))

        def rs():
            n, = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", "replace")

        def skip(t):
            if t == 8:
                rs()
            elif t == 9:
                it, = struct.unpack("<I", f.read(4))
                n, = struct.unpack("<Q", f.read(8))
                if it == 8:
                    for _ in range(n):
                        rs()
                elif it in sz:
                    f.seek(sz[it] * n, 1)
                else:
                    for _ in range(n):
                        skip(it)
            else:
                f.seek(sz[t], 1)
        align = 32
        for _ in range(nkv):
            k = rs()
            t, = struct.unpack("<I", f.read(4))
            if k == "general.alignment" and t in (4, 5):
                align, = struct.unpack("<I", f.read(4))
            else:
                skip(t)
        infos = []
        for _ in range(nt):
            name = rs()
            nd, = struct.unpack("<I", f.read(4))
            f.seek(8 * nd, 1)
            _typ, off = struct.unpack("<IQ", f.read(12))
            infos.append((off, name))
        data_start = (f.tell() + align - 1) // align * align
    total = os.path.getsize(path) - data_start
    infos.sort()
    out = {}
    for i, (off, name) in enumerate(infos):
        end = infos[i + 1][0] if i + 1 < len(infos) else total
        out[name] = end - off
    return out


def system_ram_mib():
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys // 1048576, m.ullAvailPhys // 1048576
    except Exception:
        return 0, 0


KV_BYTES = {"f16": 2.0, "bf16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}     # bytes per cached value
# bf16: same size as f16; the Qwen3.6-35B-A3B REAP card says f16 KV accumulates error on that architecture and corrupts
# tool calls over long agent contexts, and that -ctk bf16 -ctv bf16 is "not optional"
COMPUTE_PER_TOKEN = 4096           # compute-buffer growth per context token (measured on Qwythos-9B)
FIXED_OVERHEAD_MB = 450            # CUDA context, scratch buffers
MARGIN_MB = 200                    # left free for the desktop


def _swa_layers(meta):
    """-> (heads per layer, is-sliding-window per layer) when the model mixes local and global attention."""
    pat = meta.get("attention.sliding_window_pattern")
    if not isinstance(pat, (list, tuple)) or not meta.get("attention.sliding_window"):
        return None
    heads = meta.get("attention.head_count_kv") or 8
    heads = list(heads) if isinstance(heads, (list, tuple)) else [heads] * len(pat)
    return heads, [bool(x) for x in pat]


def kv_fixed_bytes(meta, kv_type):
    """KV memory that does NOT grow with the context: sliding-window layers hold only their window."""
    sw = _swa_layers(meta)
    if not sw:
        return 0
    heads, pat = sw
    kl = meta.get("attention.key_length_swa") or meta.get("attention.key_length") or 128
    vl = meta.get("attention.value_length_swa") or kl
    win = int(meta.get("attention.sliding_window") or 0)
    return sum(h for h, local in zip(heads, pat) if local) * (kl + vl) * win * KV_BYTES.get(kv_type, 2.0)


def kv_bytes_per_token(meta, kv_type):
    sw = _swa_layers(meta)
    if sw:                          # only the global-attention layers grow with the context
        heads, pat = sw
        kl = meta.get("attention.key_length") or 128
        vl = meta.get("attention.value_length") or kl
        return sum(h for h, local in zip(heads, pat) if not local) * (kl + vl) * KV_BYTES.get(kv_type, 2.0)
    attn = meta.get("attention.head_count_kv") or meta.get("head_count_kv") or 8
    if isinstance(attn, (list, tuple)):
        attn = max(attn)
    kl = meta.get("attention.key_length") or 128
    vl = meta.get("attention.value_length") or kl
    layers = meta.get("block_count") or 32
    interval = meta.get("full_attention_interval")
    if interval:                    # hybrid models: only every n-th layer keeps a KV cache
        layers = max(1, layers // int(interval))
    return layers * attn * (kl + vl) * KV_BYTES.get(kv_type, 2.0)


def gpu_total_mib():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return 0


def fit_context(model_name, want_ctx, kv_choice="auto", reserve_mib=0, kv_v=None):
    """-> dict(ctx, kv, max={kv: ctx}, note) — the largest context (up to want_ctx) whose KV cache and
    compute buffers fit in GPU memory next to the model weights, so nothing spills to system RAM.
    reserve_mib: GPU memory kept free for another model (the hybrid mode's executor)."""
    want_ctx = int(want_ctx)
    total = gpu_total_mib()
    if total and reserve_mib:
        total = max(1024, total - int(reserve_mib))
    try:
        blob = ollama_blob(model_name)
        meta = gguf_meta(blob)
        weights_mib = os.path.getsize(blob) / 1048576
    except Exception:
        return {"ctx": want_ctx, "kv": "q8_0" if kv_choice == "auto" else kv_choice, "max": {}, "note": ""}
    if not total:
        return {"ctx": want_ctx, "kv": "q8_0" if kv_choice == "auto" else kv_choice, "max": {}, "note": ""}
    if meta.get("expert_count"):
        return _fit_moe(blob, meta, want_ctx, kv_choice, total, kv_v=kv_v)
    mtp = bool(meta.get("nextn_predict_layers"))
    if weights_mib + FIXED_OVERHEAD_MB + MARGIN_MB > total:
        # a dense model bigger than the card: llama.cpp's --fit splits its layers between GPU and CPU (slow), so
        # shrinking the context would not keep it on the GPU anyway — keep the context that was asked for
        kv = "q8_0" if kv_choice == "auto" else kv_choice
        return {"ctx": want_ctx, "kv": kv, "max": {}, "partial": True, "mtp": mtp,
                "note": f"The model ({weights_mib / 1024:.1f} GB) is bigger than the GPU: part of it runs on the CPU (slow).",
                "weights_mib": round(weights_mib), "total_mib": total}
    budget = (total - MARGIN_MB - FIXED_OVERHEAD_MB - weights_mib) * 1048576
    mx = {}
    for kv in ("f16", "q8_0", "q4_0"):
        per = kv_bytes_per_token(meta, kv) + COMPUTE_PER_TOKEN
        mx[kv] = max(0, int((budget - kv_fixed_bytes(meta, kv)) / per) // 4096 * 4096)
    if kv_choice == "auto":
        kv = "f16" if mx["f16"] >= want_ctx else "q8_0"      # 4-bit only when asked for: it hurts small models
    else:
        kv = kv_choice
    if kv_v and kv_v != kv and kv in KV_BYTES and kv_v in KV_BYTES:     # a coarser V cache than K: the average
        per = (kv_bytes_per_token(meta, kv) + kv_bytes_per_token(meta, kv_v)) / 2 + COMPUTE_PER_TOKEN
        fixed = (kv_fixed_bytes(meta, kv) + kv_fixed_bytes(meta, kv_v)) / 2
        mx[kv] = max(0, int((budget - fixed) / per) // 4096 * 4096)
    ctx = min(want_ctx, mx.get(kv, want_ctx))       # 0 means "barely fits", not "no limit"
    ctx = max(ctx, 8192)
    note = ""
    if ctx < want_ctx:
        note = (f"Context reduced from {want_ctx // 1024}k to {ctx // 1024}k tokens so the model stays entirely in GPU "
                f"memory ({kv} cache; {total / 1024:.0f} GB card).")
    return {"ctx": ctx, "kv": kv, "max": mx, "note": note, "weights_mib": round(weights_mib), "total_mib": total}


_EXP = re.compile(r"^blk\.(\d+)\.ffn_(gate|up|down|gate_up)_exps")


def _fit_moe(blob, meta, want_ctx, kv_choice, total_mib, kv_v=None):
    """Mixture-of-experts: attention/shared weights and the KV cache stay on the GPU; as many layers'
    experts as fit go on the GPU too, the rest stay in system RAM (llama.cpp --n-cpu-moe N puts the
    experts of the first N layers on the CPU)."""
    sizes = gguf_tensor_sizes(blob)
    per_layer = {}
    for name, b in sizes.items():
        m = _EXP.match(name)
        if m:
            per_layer[int(m.group(1))] = per_layer.get(int(m.group(1)), 0) + b
    expert_total = sum(per_layer.values())
    non_expert = sum(sizes.values()) - expert_total
    n_layers = (max(per_layer) + 1) if per_layer else int(meta.get("block_count") or 0)
    kv = "q8_0" if kv_choice == "auto" else kv_choice
    budget = (total_mib - MARGIN_MB - FIXED_OVERHEAD_MB) * 1048576 - non_expert
    per_tok = kv_bytes_per_token(meta, kv) + COMPUTE_PER_TOKEN
    if kv_v and kv_v != kv and kv_v in KV_BYTES:       # a coarser V cache: K and V each hold half the bytes
        per_tok = (kv_bytes_per_token(meta, kv) + kv_bytes_per_token(meta, kv_v)) / 2 + COMPUTE_PER_TOKEN
    max_ctx = max(0, int(budget / per_tok) // 4096 * 4096)
    ctx = max(8192, min(int(want_ctx), max_ctx))
    left = budget - ctx * per_tok
    # experts of the LAST layers go on the GPU while they fit
    on_gpu = 0
    for layer in sorted(per_layer, reverse=True):
        if per_layer[layer] <= left:
            left -= per_layer[layer]
            on_gpu += 1
        else:
            break
    n_cpu = max(0, len(per_layer) - on_gpu)
    cpu_bytes = sum(b for l, b in per_layer.items() if l < n_cpu)
    ram_total, ram_free = system_ram_mib()
    notes = []
    if ctx < int(want_ctx):
        notes.append(f"Context reduced from {int(want_ctx) // 1024}k to {ctx // 1024}k tokens to fit the GPU.")
    if ram_total and cpu_bytes / 1048576 > ram_total - 4500:
        notes.append(f"{cpu_bytes / 2**30:.1f} GB of experts in RAM is close to your {ram_total / 1024:.0f} GB: "
                     f"close other programs, or expect pauses while Windows pages to disk.")
    mx = {k: max(0, int(budget / (kv_bytes_per_token(meta, k) + COMPUTE_PER_TOKEN)) // 4096 * 4096)
          for k in ("f16", "q8_0", "q4_0")}
    return {"ctx": ctx, "kv": kv, "max": mx, "note": " ".join(notes), "moe": True, "n_cpu_moe": n_cpu,
            "layers": len(per_layer), "experts_gpu_layers": on_gpu, "experts_cpu_gib": round(cpu_bytes / 2**30, 2),
            "non_expert_gib": round(non_expert / 2**30, 2), "mtp": bool(meta.get("nextn_predict_layers")),
            "weights_mib": round(os.path.getsize(blob) / 1048576), "total_mib": total_mib}


AUTO_CTX_MAX = 131072              # auto sizing never goes past this (or the model's trained context)
DRAFTER_MIB = 900                  # an MTP drafter's weights + cache, kept free when sizing automatically


def auto_size(model_name, tuned_ctx, kv="q8_0", kv_v=None, tuned_mib=8192, cap=None, reserve_mib=0):
    """On a GPU with clearly more memory than the profile was tuned for, spend the extra instead of leaving it idle.
    Dense model: first a full-precision (f16) cache if it still holds the tuned context (better quality), else the
    tuned cache type with more context — up to `cap` and the model's trained context. Mixture-of-experts: the tuned
    context, with the CPU-expert count left to fit_context (more experts on the GPU is faster than more context).
    -> dict(ctx, kv, kv_v, moe, note) or None (no bigger card / unknown model: keep the profile as it is)."""
    total = gpu_total_mib()
    if not total or total <= int(tuned_mib) + 512:
        return None
    try:
        meta = gguf_meta(ollama_blob(model_name))
    except Exception:
        return None
    trained = int(meta.get("context_length") or 0)
    cap = int(cap or AUTO_CTX_MAX)
    if trained:
        cap = min(cap, trained)
    tuned_ctx = min(int(tuned_ctx), cap)
    gb = f"{total / 1024:.0f} GB card (profile tuned for {int(tuned_mib) / 1024:.0f} GB)"
    if meta.get("expert_count"):
        return {"ctx": tuned_ctx, "kv": kv, "kv_v": kv_v, "moe": True,
                "note": f"{gb}: more of the model's experts go on the GPU"}
    f16 = fit_context(model_name, cap, "f16", reserve_mib=reserve_mib)
    if f16.get("partial"):
        return None
    if int((f16.get("max") or {}).get("f16") or 0) >= tuned_ctx:
        ctx = max(tuned_ctx, min(cap, int(f16["max"]["f16"])))
        return {"ctx": ctx, "kv": "f16", "kv_v": None, "moe": False,
                "note": f"{gb}: {ctx // 1024}k context with a full-precision cache"}
    q = fit_context(model_name, cap, kv, reserve_mib=reserve_mib, kv_v=kv_v)
    ctx = max(tuned_ctx, min(cap, int(q.get("ctx") or tuned_ctx)))
    return {"ctx": ctx, "kv": kv, "kv_v": kv_v, "moe": False,
            "note": f"{gb}: {ctx // 1024}k context ({kv}{'/' + kv_v if kv_v and kv_v != kv else ''} cache)"}


def vram_need_mib(model_name, ctx, kv="q8_0"):
    """GPU memory a dense model needs entirely on the GPU at this context (0 if unknown)."""
    try:
        blob = ollama_blob(model_name)
        meta = gguf_meta(blob)
    except Exception:
        return 0
    per = kv_bytes_per_token(meta, kv) + COMPUTE_PER_TOKEN
    return int(os.path.getsize(blob) / 1048576 + FIXED_OVERHEAD_MB + int(ctx) * per / 1048576)


def base_url(slot="main"):
    return f"http://127.0.0.1:{_slots[slot]['port']}"


_WS = [" ", "  ", "   ", "    ", "     ", "      ", "        ", "\n", "\n\n", "\n ", "\n  ",
       "\n   ", "\n    ", "\n     ", "\n      ", "\n        ", "\n          ", "\t", "\n\t",
       "\n\t\t", "\r\n"]


def _find_whitespace_ids(slot="main"):
    """Token ids that are PURE whitespace (indent / newline runs). Banning them keeps the
    model's JSON compact: indentation was ~20% of every reply's tokens."""
    ids = set()
    for s in _WS:
        try:
            body = json.dumps({"content": s, "add_special": False}).encode()
            req = urllib.request.Request(base_url(slot) + "/tokenize", data=body,
                                         headers={"Content-Type": "application/json"})
            toks = json.loads(urllib.request.urlopen(req, timeout=10).read()).get("tokens", [])
            if len(toks) == 1:
                ids.add(int(toks[0]))
        except Exception:
            continue
    return sorted(ids)


def whitespace_ids():
    return _state.get("ws_ids") or []


def _healthy(slot="main"):
    try:
        with urllib.request.urlopen(base_url(slot) + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _unload_ollama():
    """Free the GPU: Ollama keeps models resident for minutes, and 8 GB is not enough for both."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as r:
            models = json.loads(r.read()).get("models", [])
        for m in models:
            body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def _free_vram_mib():
    """Free GPU memory in MiB (0 if it cannot be read)."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return 0


def ensure(model_name, parallel=2, num_ctx=10000, status=lambda s: None, extra_args=(), slot="main", gpu=True,
           high_priority=False, unified=False, exe_dir=None):
    """Start (or reuse) the server for `model_name` in `slot`; returns its base URL.
    gpu=False: all layers on the CPU (llama.cpp still uses the GPU briefly to read prompts, ~0.25 GB).
    unified=True: the parallel slots share ONE num_ctx context pool (--kv-unified) instead of num_ctx each."""
    parallel = max(1, min(4, int(parallel)))
    extra_args = [str(a) for a in extra_args]
    if unified and parallel > 1:
        extra_args += ["--kv-unified"]
    exe_dir = os.path.expanduser(exe_dir) if exe_dir else llama_dir()   # a profile may need its own llama.cpp build
    key = (model_name, parallel, int(num_ctx), tuple(extra_args), exe_dir)
    st = _slots[slot]
    with _lock:
        proc = st["proc"]
        if proc is not None and proc.poll() is None and st["key"] == key and _healthy(slot):
            return base_url(slot)
        _stop_locked(slot)
        _kill_stale(slot)
        reap_orphans()
        if not os.path.isfile(os.path.join(exe_dir, "llama-server.exe")):
            raise RuntimeError(f"llama.cpp not found: put llama-server.exe in {exe_dir}")
        blob = ollama_blob(model_name)
        status(f"Starting llama.cpp server for {model_name}…")
        _unload_ollama()
        time.sleep(1.0)
        size_mib = os.path.getsize(blob) / 1048576
        free_mib = _free_vram_mib()
        big = gpu and bool(free_mib) and size_mib + FIXED_OVERHEAD_MB + MARGIN_MB > free_mib   # weights alone don't fit
        if big:
            parallel = 1                  # every extra slot is more context memory we do not have
            key = (model_name, parallel, int(num_ctx), tuple(extra_args), exe_dir)
            status(f"{model_name} is {size_mib / 1024:.0f} GB and only {free_mib / 1024:.1f} GB "
                   f"of GPU memory is free: llama.cpp will split it between GPU and CPU "
                   f"(one request at a time, slower to load).")
        log_path = os.path.join(_state_dir(), "swarm_server.log" if slot == "main" else f"swarm_server_{slot}.log")
        log = open(log_path, "w")
        ctx = int(num_ctx) * (1 if (unified and parallel > 1) else parallel)
        # fits entirely: all layers on the GPU. Too big: let llama.cpp's --fit place the layers
        # (and MoE experts) between GPU and CPU, keeping a 1 GB margin for the desktop and
        # never shrinking the context below what the prompts need.
        place = (["-ngl", "0"] if not gpu else
                 ["--fit", "on", "--fit-target", "1024", "--fit-ctx", str(ctx)] if big else ["-ngl", "99"])
        port = _free_port({"main": PORT, "exec": PORT + 1, "speech": PORT + 3, "gopher": PORT + 5, "test": PORT + 7}.get(slot, PORT + 4))
        st["port"] = port
        cmd = [os.path.join(exe_dir, "llama-server.exe"), "-m", blob, "-c", str(ctx)] + place + [
               "-fa", "on", "--port", str(port), "--host", "127.0.0.1",
               "--parallel", str(parallel)] + extra_args
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=exe_dir,
                                # no console window; high priority for CPU servers: their threads work in lockstep,
                                # so at normal priority any background program stalls every token (measured: 0.6 vs
                                # 6.7 tok/s per worker under the same load)
                                creationflags=0x08000000 | (0x00000080 if high_priority else 0))
        _bind_to_app(proc)
        st.update(proc=proc, key=key, log=log)
        try:
            with open(_pid_file(slot), "w") as f:
                f.write(str(proc.pid))
        except Exception:
            pass
        t0 = time.time()
        while time.time() - t0 < (600 if big else 240):          # a 19 GB model takes a while
            if _healthy(slot):
                st["ws_ids"] = _find_whitespace_ids(slot)
                return base_url(slot)
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        tail = ""
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                errs = [ln.strip()[:160] for ln in f if " E " in ln]
            tail = " | ".join(errs[-3:])
        except Exception:
            pass
        _stop_locked(slot)
        raise RuntimeError("llama.cpp server did not start: " + (tail or "see swarm_server.log")
                           + (" — the model does not fit; try the Ollama option for this model"
                              if "out of memory" in tail else ""))


def _stop_locked(slot="main"):
    st = _slots[slot]
    proc = st["proc"]
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
    if st.get("log"):
        try:
            st["log"].close()
        except Exception:
            pass
    st.update(proc=None, key=None, log=None)
    try:
        os.remove(_pid_file(slot))
    except Exception:
        pass


def running(slot):
    proc = _slots[slot]["proc"]
    return proc is not None and proc.poll() is None


def stop(slot=None):
    """Stop one slot's server, or all of them."""
    with _lock:
        for name in ([slot] if slot else list(_slots)):
            _stop_locked(name)


atexit.register(stop)
