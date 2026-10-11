"""Alpha-release pieces: paths, the UI origin check, Help/About/diagnostics, model profile helpers."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Req:
    def __init__(self, origin=None):
        self.headers = {"Origin": origin} if origin else {}


def test_ui_origin_check():
    import harness_api as api
    port = int(getattr(api.wb, "PORT", 8765))
    assert api._ui_origin_ok(Req())                                       # no Origin: same-origin/non-browser
    assert api._ui_origin_ok(Req(f"http://127.0.0.1:{port}"))             # the app's own window
    assert api._ui_origin_ok(Req("http://localhost:5173"))                # vite dev server
    assert not api._ui_origin_ok(Req("https://evil.example"))             # another website
    assert not api._ui_origin_ok(Req("http://localhost:8790"))            # an agent-built test server
    assert not api._ui_origin_ok(Req("null"))                             # sandboxed iframe / file://


def test_paths_resolve(monkeypatch, tmp_path):
    import paths
    monkeypatch.setenv("LWS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LWS_MODELS_DIR", str(tmp_path / "models"))
    assert paths.data_dir() == str(tmp_path / "data") and os.path.isdir(paths.data_dir())
    assert paths.data("x.json") == str(tmp_path / "data" / "x.json")
    assert paths.resolve_model("a.gguf") == str(tmp_path / "models" / "a.gguf")
    assert paths.resolve_model(r"C:\m\b.gguf") == r"C:\m\b.gguf"
    assert paths.resolve_model("") == ""


def test_help_docs_and_redaction():
    import help_api
    pages = help_api.doc_pages()
    slugs = [p["slug"] for p in pages]
    assert slugs[:2] == ["getting-started", "models"], slugs
    assert all(p["title"] for p in pages)
    assert help_api.version() != "dev"
    text = "key sk-ant-abc123def456ghi789 and csk-0123456789abcdefghij and TOKENVALUE123"
    red = help_api.redact(text, extra=["TOKENVALUE123"])
    assert "sk-ant" not in red and "csk-" not in red and "TOKENVALUE123" not in red


def test_model_profile_args_and_download_validation():
    import models_api
    args = models_api.server_args({"kv_cache": "q8_0", "n_cpu_moe": 19, "cache_ram_mib": 512})
    assert args[args.index("-ctk") + 1] == "q8_0" and args[args.index("--n-cpu-moe") + 1] == "19"
    assert "-ctk" not in models_api.server_args({"kv_cache": "f16"})
    for bad in ("http://x/y.gguf", "https://x/y.txt", "https://x/a%20b%3B.gguf", ""):
        with pytest.raises(ValueError):
            models_api.start_download(bad)


def test_model_describe_reads_gguf_header():
    import models_api
    import struct
    d = tempfile.mkdtemp()
    p = os.path.join(d, "Tiny-Q4_K_M.gguf")
    key = b"general.architecture"
    val = b"llama"
    with open(p, "wb") as f:                     # minimal GGUF v3 header: 0 tensors, 1 string key
        f.write(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, 1))
        f.write(struct.pack("<Q", len(key)) + key + struct.pack("<I", 8) + struct.pack("<Q", len(val)) + val)
    info = models_api.describe(p)
    assert info["architecture"] == "llama" and info["quant"] == "Q4_K_M" and info["kind"] == "model"
