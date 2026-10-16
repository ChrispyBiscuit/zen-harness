"""llama.cpp runtimes for each GPU vendor, downloaded on first run (plans/hardware-scaling.md).

PrismML's fork is needed for the Ternary Bonsai files; it tracks mainline llama.cpp, so the same runtime serves every
other model too. One pinned release, one folder per backend under <data>/runtimes, each zip checked against the
SHA-256 GitHub publishes for it.

  installed(backend)        its llama-server.exe is in place
  install(backend, progress)  download, verify, unpack (SYCL overlays the CPU package, as PrismML documents)
  devices(backend)          what that runtime can use: [{id, name, total_mib, free_mib}] from --list-devices
  pick_device(devs, gpu)    the device to pass with --device (a laptop's integrated GPU is listed too)
  choose(gpu, order)        the first runtime that sees the GPU, installing as needed
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile

import paths

BUILD = "b10777-e3de715"
TAG = "prism-" + BUILD
BASE = f"https://github.com/PrismML-Eng/llama.cpp/releases/download/{TAG}/"
# (file, bytes, sha256) per backend, in unpack order
ASSETS = {
    "cuda": [(f"llama-prism-{BUILD}-bin-win-cuda-12.4-x64.zip", 313951324,
              "fa5874a1412e251a6efb13f289e97bacbbb4e7fc6714c1836b4fc6c1dc863462"),
             ("cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627,
              "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6")],
    "hip": [(f"llama-prism-{BUILD}-bin-win-hip-radeon-x64.zip", 342522145,
             "371921702ccc41ff235836ea78e575b0a054fbfaf4711d1976a79577e982d846")],
    "vulkan": [(f"llama-prism-{BUILD}-bin-win-vulkan-x64.zip", 31298865,
                "6bf30d60dd35ae1129ae221593d79bed053c775c6ce8554f75cfe171f3028f10")],
    "sycl": [(f"llama-prism-{BUILD}-bin-win-cpu-x64.zip", 19530958,
              "cff7eeff19163e6263720618cf98023715b8dac14d0873f5be0918765de323c4"),
             (f"llama-prism-{BUILD}-bin-win-sycl-x64.zip", 103156751,
              "96b99f18a9ad70c5d50feb4d07b7d4cabf901ad6364e22710b837e848c1183bd")],
    "cpu": [(f"llama-prism-{BUILD}-bin-win-cpu-x64.zip", 19530958,
             "cff7eeff19163e6263720618cf98023715b8dac14d0873f5be0918765de323c4")],
}
DEVICE_PREFIX = {"cuda": "CUDA", "hip": "ROCm", "vulkan": "Vulkan", "sycl": "SYCL", "cpu": ""}
_lock = threading.Lock()


def runtime_dir(backend):
    return paths.data("runtimes", f"{backend}-{TAG}")


def server_exe(folder):
    """llama-server.exe in a runtime folder (the zips unpack flat or into one subfolder)."""
    for base, _dirs, files in os.walk(folder):
        if "llama-server.exe" in files:
            return os.path.join(base, "llama-server.exe")
    return ""


def installed(backend):
    return bool(os.path.isfile(os.path.join(runtime_dir(backend), ".ok")) and server_exe(runtime_dir(backend)))


def exe_dir(backend):
    return os.path.dirname(server_exe(runtime_dir(backend)))


def download_mib(backend):
    return sum(size for _f, size, _h in ASSETS.get(backend, [])) // 1048576


def _fetch(url, dest, size, sha, progress, done_before, total):
    h = hashlib.sha256()
    got = 0
    req = urllib.request.Request(url, headers={"User-Agent": "Zazen"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            progress(done_before + got, total)
    if got != size or h.hexdigest() != sha:
        raise RuntimeError(f"{os.path.basename(dest)} did not match its published checksum (got {got:,} bytes): the "
                           f"download was damaged or changed. Try again.")


def install(backend, progress=lambda done, total: None):
    """Download and unpack a runtime. Safe to call again: an installed runtime returns at once."""
    if backend not in ASSETS:
        raise ValueError(f"unknown runtime '{backend}' (one of {', '.join(ASSETS)})")
    with _lock:
        if installed(backend):
            return exe_dir(backend)
        dest = runtime_dir(backend)
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        total = sum(s for _f, s, _h in ASSETS[backend])
        done = 0
        tmp = tempfile.mkdtemp(prefix="zazen-rt-")
        try:
            for name, size, sha in ASSETS[backend]:
                z = os.path.join(tmp, name)
                _fetch(BASE + name, z, size, sha, progress, done, total)
                done += size
                with zipfile.ZipFile(z) as zf:
                    for m in zf.infolist():           # never outside the runtime folder
                        target = os.path.realpath(os.path.join(dest, m.filename))
                        if not target.startswith(os.path.realpath(dest) + os.sep) and target != os.path.realpath(dest):
                            raise RuntimeError(f"{name} has an entry outside its folder: {m.filename}")
                    zf.extractall(dest)
            exe = server_exe(dest)
            if not exe:
                raise RuntimeError(f"the {backend} runtime unpacked without llama-server.exe")
            if backend == "cuda":                       # the CUDA DLLs unpack beside the zip root: put them by the exe
                for f in os.listdir(dest):
                    if f.lower().endswith(".dll") and not os.path.exists(os.path.join(os.path.dirname(exe), f)):
                        shutil.copy2(os.path.join(dest, f), os.path.dirname(exe))
            with open(os.path.join(dest, ".ok"), "w") as f:
                f.write(TAG)
            return os.path.dirname(exe)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


_DEV_RX = re.compile(r"^\s*([A-Za-z]+)(\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", re.M)


def parse_devices(text):
    return [{"id": f"{m.group(1)}{m.group(2)}", "name": m.group(3), "total_mib": int(m.group(4)),
             "free_mib": int(m.group(5))} for m in _DEV_RX.finditer(text or "")]


_dev_cache = {}


def devices(folder, refresh=False):
    """GPUs a runtime folder's llama-server can use ([] = none, or it failed to load its backend). Cached per folder
    for the app's lifetime (starting the backend to list devices takes a second or two)."""
    exe = server_exe(folder) if folder and os.path.isdir(folder) else ""
    if not exe:
        return []
    if not refresh and exe in _dev_cache:
        return [dict(d) for d in _dev_cache[exe]]
    try:
        out = subprocess.run([exe, "--list-devices"], capture_output=True, text=True, timeout=60,
                             cwd=os.path.dirname(exe), creationflags=0x08000000)
    except Exception:
        return []
    found = parse_devices((out.stdout or "") + "\n" + (out.stderr or ""))
    _dev_cache[exe] = found
    return [dict(d) for d in found]


def pick_device(devs, gpu):
    """The listed device that is `gpu` (by name, else the most memory): with Vulkan or HIP a laptop's integrated GPU
    is listed too, and llama.cpp would split the model across both."""
    if not devs:
        return None
    name = str((gpu or {}).get("name") or "").lower()
    by_name = [d for d in devs if name and (name in d["name"].lower() or d["name"].lower() in name)]
    if by_name:
        return max(by_name, key=lambda d: d["total_mib"])
    # no name match: the same vendor, never by size alone. Vulkan lists a laptop's integrated Radeon with the shared
    # system RAM as its memory (8,143 MiB on the RTX 3070 laptop, more than the 3070's own 8,017)
    words = {"nvidia": ("nvidia", "geforce", "rtx", "quadro"), "amd": ("radeon rx", "radeon pro", "amd radeon rx"),
             "intel": ("arc",)}.get(str((gpu or {}).get("vendor") or ""), ())
    by_vendor = [d for d in devs if any(w in d["name"].lower() for w in words)]
    return max(by_vendor, key=lambda d: d["total_mib"]) if by_vendor else (devs[0] if len(devs) == 1 else None)


def choose(gpu, order, progress=lambda backend, done, total: None, install_missing=True, existing=None):
    """The first runtime in `order` that sees this GPU. existing: {backend: folder} of builds already set up outside
    the app's runtimes folder (a profile's own llama_dir), used instead of downloading that backend.
    -> (backend, exe_dir, device or None, notes)."""
    notes = []
    for backend, folder in (existing or {}).items():
        if backend in order[:1] and folder and os.path.isfile(os.path.join(folder, "llama-server.exe")):
            dev = pick_device(devices(folder), gpu)
            if dev:
                return backend, folder, dev, notes
    for backend in order:
        if backend == "cpu":
            if install_missing or installed("cpu"):
                return "cpu", install("cpu", lambda d, t: progress("cpu", d, t)), None, notes
            continue
        if not installed(backend):
            if not install_missing:
                continue
            try:
                install(backend, lambda d, t, b=backend: progress(b, d, t))
            except Exception as e:
                notes.append(f"{backend}: could not install ({str(e)[:160]})")
                continue
        devs = devices(runtime_dir(backend))
        dev = pick_device(devs, gpu)
        if dev and dev["total_mib"] >= 2048:
            return backend, exe_dir(backend), dev, notes
        notes.append(f"{backend}: the runtime does not see {(gpu or {}).get('name', 'the GPU')}"
                     + (f" (it lists {', '.join(d['name'] for d in devs)})" if devs else
                        " (no GPU listed: driver missing or too old for it)"))
    return None, "", None, notes
