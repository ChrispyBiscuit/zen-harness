"""Hardware scaling (plans/hardware-scaling.md): any vendor's GPU picks the runtime, model file, context, cache and
vision placement; settings override any of it."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hardware
import runtimes
from agent_config import model_profile

PROF = model_profile({}, "ternary-bonsai-2-27b")


def gpu(vendor, gib, name=None):
    return {"vendor": vendor, "name": name or f"{vendor} card", "vram_mib": int(gib * 1024), "integrated": False}


def have_all(_p):
    return True


def have_small_only(p):
    return "PTQ1_0" in str(p)


@pytest.mark.parametrize("vendor,backends", [("nvidia", ["cuda", "vulkan", "cpu"]), ("amd", ["hip", "vulkan", "cpu"]),
                                             ("intel", ["vulkan", "sycl", "cpu"])])
def test_each_vendor_gets_its_runtime_order(vendor, backends):
    assert hardware.backend_order(gpu(vendor, 8), {}) == backends
    assert hardware.backend_order(gpu(vendor, 8), {"backend": "vulkan"})[0] == "vulkan"   # set by hand: first
    assert hardware.backend_order(None, {}) == ["cpu"]


@pytest.mark.parametrize("gib,file,ctx,kv_v,vision", [
    (8, "PTQ1_0", 65536, "q4_0", False),
    (10, "PTQ1_0", 65536, "q4_0", False),
    (12, "PQ2_0", 98304, "q8_0", True),
    (16, "PQ2_0", 131072, "q8_0", True)])
def test_tiers_from_8_to_16_gb(gib, file, ctx, kv_v, vision):
    for vendor in ("nvidia", "amd", "intel"):
        g = gpu(vendor, gib)
        p = hardware.plan(PROF, gpu=g, backend=hardware.backend_order(g, {})[0], ram_mib=16384, have=have_all)
        assert file in p["model_file"], (vendor, gib, p)
        assert (p["num_ctx"], p["kv_cache"], p["kv_cache_v"], p["vision_gpu"]) == (ctx, "q8_0", kv_v, vision), (vendor, p)
        assert p["cache_ram_mib"] == 3072                     # 16 GB of system RAM


def test_the_bigger_file_is_used_only_once_downloaded():
    p = hardware.plan(PROF, gpu=gpu("amd", 16), backend="hip", ram_mib=16384, have=have_small_only)
    assert "PTQ1_0" in p["model_file"] and "PQ2_0" in p["better_file"]      # runs now, says what would be faster


def test_sycl_never_uses_the_file_it_would_expand():
    # SYCL's XMX path unpacks PTQ1_0 to PQ2_0's size: on a 16 GB Arc use PQ2_0 itself
    p = hardware.plan(PROF, gpu=gpu("intel", 16), backend="sycl", ram_mib=16384, have=have_all)
    assert "PQ2_0" in p["model_file"]


def test_under_8_gb_or_no_gpu_runs_on_the_cpu_and_says_so():
    p = hardware.plan(PROF, gpu=gpu("nvidia", 6), backend="cuda", ram_mib=16384, have=have_all)
    assert p["backend"] == "cpu" and "8 GB" in p["note"] and not p["vision_gpu"]
    assert hardware.plan(PROF, gpu=False, ram_mib=16384, have=have_all)["backend"] == "cpu"


def test_the_dedicated_gpu_wins_over_a_laptops_integrated_one(monkeypatch):
    found = [{"vendor": "amd", "name": "AMD Radeon(TM) Graphics", "vram_mib": 512, "integrated": True},
             {"vendor": "nvidia", "name": "NVIDIA GeForce RTX 3070 Laptop GPU", "vram_mib": 8192, "integrated": False}]
    monkeypatch.setattr(hardware, "gpus", lambda refresh=False: sorted(found, key=lambda g: (g["integrated"], -g["vram_mib"])))
    assert hardware.primary({})["vendor"] == "nvidia"
    assert hardware.primary({"gpu": "radeon"})["vendor"] == "amd"            # chosen by hand: by name
    # Vulkan lists the integrated GPU too: the launch passes the dedicated one
    devs = runtimes.parse_devices("Available devices:\n  Vulkan0: AMD Radeon(TM) Graphics (8000 MiB, 7000 MiB free)\n"
                                  "  Vulkan1: NVIDIA GeForce RTX 3070 Laptop GPU (8192 MiB, 7600 MiB free)\n")
    assert runtimes.pick_device(devs, hardware.primary({}))["id"] == "Vulkan1"


def test_settings_set_by_hand_win_and_auto_off_leaves_the_profile(monkeypatch):
    monkeypatch.setattr(hardware, "primary", lambda hw=None: gpu("nvidia", 16))
    cfg = dict(PROF, hardware={"auto": True, "num_ctx": 32768, "kv_cache_v": "q4_0", "vision_gpu": "off"})
    p = hardware.apply(cfg, backend="cuda", runtime_dir=r"C:\rt\cuda", gpu=gpu("nvidia", 16))
    assert cfg["num_ctx"] == 32768 and cfg["kv_cache_v"] == "q4_0" and cfg["vision_gpu"] is False
    assert cfg["llama_dir"] == r"C:\rt\cuda" and cfg["_auto_ctx"] and p["backend"] == "cuda"
    off = dict(PROF, hardware={"auto": False})
    before = dict(off)
    assert hardware.apply(off) is None and off == before
    plain = {"model_file": "other.gguf"}                                     # a profile with no variants/runtime
    assert hardware.apply(plain) is None


def test_registry_sizes_and_vendors(monkeypatch):
    monkeypatch.setattr(hardware, "_registry_gpus", lambda: [
        {"vendor": "intel", "name": "Intel(R) Arc(TM) A770 Graphics", "vram_mib": 16384, "driver": "", "device_id": "",
         "integrated": False},
        {"vendor": "intel", "name": "Intel(R) UHD Graphics", "vram_mib": 128, "driver": "", "device_id": "",
         "integrated": True}])
    monkeypatch.setattr(hardware, "_nvidia_smi", lambda: [])
    found = hardware.gpus(refresh=True)
    assert found[0]["name"].startswith("Intel(R) Arc") and found[-1]["integrated"]
    hardware._cache.update(t=0.0, gpus=None)


def test_runtime_assets_are_pinned_with_checksums():
    for backend, assets in runtimes.ASSETS.items():
        assert assets and all(len(sha) == 64 and size > 1_000_000 for _f, size, sha in assets), backend
    assert runtimes.ASSETS["sycl"][0][0].endswith("cpu-x64.zip")             # SYCL overlays the CPU package
    for v in PROF["variants"]:
        assert len(v["sha256"]) == 64 and v["bytes"] > 5_000_000_000


def test_an_integrated_gpu_with_more_shared_memory_is_never_picked_by_size():
    # measured on the RTX 3070 laptop with PrismML's Vulkan build: the integrated Radeon lists more memory
    devs = runtimes.parse_devices("  Vulkan0: AMD Radeon(TM) Graphics (8143 MiB, 7735 MiB free)\n"
                                  "  Vulkan1: NVIDIA GeForce RTX 3070 Laptop GPU (8017 MiB, 7249 MiB free)\n")
    renamed = {"vendor": "nvidia", "name": "RTX 3070 (driver name differs)", "vram_mib": 8192}
    assert runtimes.pick_device(devs, renamed)["id"] == "Vulkan1"
    assert runtimes.pick_device(devs[:1], {"vendor": "nvidia", "name": "x"})["id"] == "Vulkan0"   # the only one
    assert runtimes.pick_device(devs[:1] * 2, {"vendor": "nvidia", "name": "x"}) is None          # ambiguous: none


def test_the_engine_applies_the_plan_and_keeps_an_existing_cuda_build(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from offline_suite import engine
    own = tmp_path / "llama.cpp-prism"
    own.mkdir()
    (own / "llama-server.exe").write_text("")
    g = gpu("nvidia", 8, "NVIDIA GeForce RTX 3070 Laptop GPU")
    monkeypatch.setattr(hardware, "primary", lambda hw=None: g)
    monkeypatch.setattr(runtimes, "installed", lambda b: False)
    monkeypatch.setattr(runtimes, "devices", lambda folder, refresh=False: [
        {"id": "CUDA0", "name": "NVIDIA GeForce RTX 3070 Laptop GPU", "total_mib": 8191, "free_mib": 7000}])
    e = engine()
    c = dict(PROF, llama_dir=str(own), model_file="Ternary-Bonsai-2-27B-PTQ1_0.gguf")
    e._hardware_setup(c)
    assert c["llama_dir"] == str(own) and c["_device"] == "CUDA0"
    assert (c["num_ctx"], c["kv_cache"], c["kv_cache_v"], c["vision_gpu"]) == (65536, "q8_0", "q4_0", False)
    # an AMD card with no runtime downloaded yet: the profile is left as written and the chat says to run setup
    monkeypatch.setattr(hardware, "primary", lambda hw=None: gpu("amd", 16))
    c2 = dict(PROF)
    e._hardware_setup(c2)
    assert "_device" not in c2 and c2["num_ctx"] == PROF["num_ctx"]


def test_a_profile_with_only_a_runtime_keeps_its_own_tuning():
    # the Bonsai 9B is tuned for 128K on 8 GB: the 27B's tiers must not cut it to 64K
    nine = model_profile({}, "ternary-bonsai-2-9b")
    cfg = dict(nine)
    hardware.apply(cfg, backend="vulkan", runtime_dir=r"C:\rt\vulkan", gpu=gpu("amd", 8))
    assert cfg["num_ctx"] == nine["num_ctx"] == 131072 and cfg["kv_cache_v"] == nine["kv_cache_v"]
    assert cfg["llama_dir"] == r"C:\rt\vulkan" and cfg["_overhead_mib"] == hardware.OVERHEAD_MIB["vulkan"]
    assert not cfg.get("_auto_ctx")
