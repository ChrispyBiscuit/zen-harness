"""Collect third-party licences for everything Zen Harness ships or downloads -> backend/licenses/.

  python packaging/gen_licenses.py [--venv backend/venv] [--ui ui] [--llama <llama.cpp dir>]

Writes backend/licenses/third_party.json (one entry per component: name, version, kind, licence, url, text file)
and the full licence texts under backend/licenses/texts/. The build runs it before freezing; the app shows the result
in Help → About & licences. Components:
  python  every distribution installed in the build venv (what the frozen app can contain), with its licence files
  npm     the UI's production dependencies (bundled into web_dist), with their licence files
  binary  llama.cpp / ggml, NVIDIA CUDA runtime DLLs shipped next to llama-server, the Python runtime
  model   the catalog models and the optional speech / embedding models the app can download
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backend", "licenses")
TEXTS = os.path.join(OUT, "texts")
LIC_FILE_RX = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)(\.[a-z]+)?$|(^|/)licenses?/", re.I)

MIT_GGML = """MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

CUDA_NOTE = """NVIDIA CUDA runtime libraries (cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll)

These files are redistributed unmodified, as permitted for the files listed in Attachment A ("Redistributable
Software") of the NVIDIA CUDA Toolkit End User License Agreement:
https://docs.nvidia.com/cuda/eula/index.html

Copyright (c) NVIDIA Corporation. All rights reserved. Use of these files is subject to the NVIDIA CUDA Toolkit EULA.
"""

QT_NOTE = """Qt for Python (PySide6 / shiboken6) and the Qt libraries, including Qt WebEngine (Chromium)

Used under the GNU Lesser General Public License v3 (LGPL-3.0). Zen Harness uses the Qt libraries as separate,
dynamically loaded files (the DLLs in the program folder); you may replace them with your own compatible build of the
same Qt version. The corresponding Qt source code is available from https://download.qt.io/ (Qt 6, the version
listed above) and https://code.qt.io/. Qt WebEngine contains Chromium and its third-party components, whose licences
are listed at chrome://credits in any Chromium-based browser and in the Qt WebEngine source.

The full LGPL-3.0 and GPL-3.0 texts are included with the PySide6 entries of this list.
"""

EXTRA_MODELS = [
    {"name": "Kokoro TTS v1.0 (voice output, optional download)", "license": "Apache-2.0",
     "url": "https://huggingface.co/hexgrad/Kokoro-82M"},
    {"name": "faster-whisper base.en / small.en (voice input, optional download)", "license": "MIT",
     "url": "https://huggingface.co/Systran/faster-whisper-small.en"},
    {"name": "BAAI bge-small-en-v1.5 (memory search embeddings)", "license": "MIT",
     "url": "https://huggingface.co/BAAI/bge-small-en-v1.5"},
]


# packages whose metadata declares no licence (checked on their GitHub repositories, 2026-10-11)
OVERRIDES = {
    "kokoro-onnx": ("MIT", "https://github.com/thewh1teagle/kokoro-onnx"),
    "py_rust_stemmers": ("MIT", "https://github.com/qdrant/py-rust-stemmers"),
    "espeakng-loader": ("MIT (loader); bundles espeak-ng, GPL-3.0 — not included in the installer, voice output is an "
                        "optional download", "https://github.com/thewh1teagle/espeakng-loader"),
}
# packages that are installed in the dev environment but NOT shipped in the installer (licence reasons, size)
NOT_SHIPPED = {
    "av": "PyAV bundles FFmpeg built with GPL codecs (x264/x265): not included in the installer",
    "faster-whisper": "voice input: not included in the installer (needs PyAV)",
    "ctranslate2": "voice input engine: not included in the installer",
    "kokoro-onnx": "voice output: not included in the installer",
    "espeakng-loader": "bundles espeak-ng (GPL-3.0): not included in the installer",
    "phonemizer": "voice output: not included in the installer",
    "libzim": "offline Wikipedia reader (GPL-3.0): not included in the installer",
}


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]


def write_text(rel, text):
    path = os.path.join(TEXTS, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return "texts/" + rel.replace(os.sep, "/")


def python_entries(venv):
    py = os.path.join(venv, "Scripts", "python.exe")
    code = r'''
import json, importlib.metadata as md
out = []
for d in md.distributions():
    m = d.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        raw = (m.get("License") or "").strip()
        lic = raw if raw and len(raw) < 80 and "\n" not in raw else ""
    if not lic:
        cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = "; ".join(cls)
    if not lic and (m.get("License") or "").strip():
        lic = "see licence text"
    texts = []
    for f in (d.files or []):
        s = str(f).replace("\\", "/")
        low = s.lower()
        if (".dist-info/" in low and any(k in low.rsplit("/", 1)[-1] for k in ("licen", "copying", "notice", "authors"))) or "/licenses/" in low:
            try:
                texts.append((s, d.locate_file(f).read_text(encoding="utf-8", errors="replace")))
            except Exception:
                pass
    if not texts and (m.get("License") or "").strip() and len(m.get("License")) >= 80:
        texts.append(("License", m.get("License")))
    url = m.get("Home-page") or ""
    for u in (m.get_all("Project-URL") or []):
        if not url or "source" in u.lower() or "home" in u.lower():
            url = u.split(",", 1)[-1].strip()
    out.append({"name": m.get("Name"), "version": d.version, "license": lic or "unknown", "url": url, "texts": texts[:6]})
print(json.dumps(out))
'''
    res = subprocess.run([py, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True)
    entries = []
    for e in sorted(json.loads(res.stdout), key=lambda x: (x["name"] or "").lower()):
        if not e["name"] or e["name"].lower() in ("pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
                                                   "pytest", "pluggy", "iniconfig", "altgraph", "pefile", "pywin32-ctypes"):
            continue
        if e["name"] in NOT_SHIPPED or e["name"].lower() in NOT_SHIPPED:
            continue                                   # listed only if it is actually distributed
        if e["name"] in OVERRIDES and e["license"] in ("unknown", ""):
            e["license"], e["url"] = OVERRIDES[e["name"]][0], e["url"] or OVERRIDES[e["name"]][1]
        body = "\n\n".join(f"===== {name} =====\n{text}" for name, text in e["texts"])
        tf = write_text(os.path.join("python", safe(e["name"]) + ".txt"), body) if body else ""
        entries.append({"id": "py:" + e["name"].lower(), "name": e["name"], "version": e["version"], "kind": "python",
                        "license": e["license"], "url": e["url"], "text": tf})
    return entries


def npm_entries(ui):
    res = subprocess.run("npm ls --omit=dev --all --json", cwd=ui, capture_output=True, text=True, encoding="utf-8",
                         shell=True)
    tree = json.loads(res.stdout or "{}")
    seen = {}

    def walk(deps):
        for name, d in (deps or {}).items():
            ver = d.get("version")
            if ver and (name, ver) not in seen:
                seen[(name, ver)] = d
            walk(d.get("dependencies"))
    walk(tree.get("dependencies"))
    entries = []
    for (name, ver), _d in sorted(seen.items()):
        pdir = os.path.join(ui, "node_modules", *name.split("/"))
        lic, url, text = "unknown", "", ""
        try:
            pj = json.load(open(os.path.join(pdir, "package.json"), encoding="utf-8"))
            lic = pj.get("license") or (pj.get("licenses") or [{}])[0].get("type") or "unknown"
            if isinstance(lic, dict):
                lic = lic.get("type", "unknown")
            repo = pj.get("repository")
            url = pj.get("homepage") or (repo.get("url") if isinstance(repo, dict) else repo) or ""
        except Exception:
            pass
        try:
            for fn in os.listdir(pdir):
                if LIC_FILE_RX.search(fn) and os.path.isfile(os.path.join(pdir, fn)):
                    text += f"===== {fn} =====\n" + open(os.path.join(pdir, fn), encoding="utf-8", errors="replace").read() + "\n"
        except OSError:
            pass
        tf = write_text(os.path.join("npm", safe(name + "@" + ver) + ".txt"), text) if text else ""
        entries.append({"id": f"npm:{name}", "name": name, "version": ver, "kind": "npm", "license": str(lic),
                        "url": str(url).replace("git+", ""), "text": tf})
    return entries


def binary_entries(llama, venv):
    out = []
    lic = ""
    for fn in ("LICENSE", "LICENSE.txt", "license.txt"):
        p = os.path.join(llama or "", fn)
        if llama and os.path.isfile(p):
            lic = open(p, encoding="utf-8", errors="replace").read()
            break
    out.append({"id": "bin:llama.cpp", "name": "llama.cpp / ggml (llama-server and its libraries)", "version": "",
                "kind": "binary", "license": "MIT", "url": "https://github.com/ggml-org/llama.cpp",
                "text": write_text("binary/llama.cpp.txt", lic or MIT_GGML)})
    omp = os.path.join(llama or "", "LICENSE-LLVM-OpenMP")
    if llama and os.path.isfile(omp):
        out.append({"id": "bin:libomp", "name": "LLVM OpenMP runtime (libomp.dll, used by llama.cpp)", "version": "",
                    "kind": "binary", "license": "Apache-2.0 WITH LLVM-exception", "url": "https://openmp.llvm.org/",
                    "text": write_text("binary/libomp.txt", open(omp, encoding="utf-8", errors="replace").read())})
    out.append({"id": "bin:cuda", "name": "NVIDIA CUDA runtime (cudart, cuBLAS)", "version": "12", "kind": "binary",
                "license": "NVIDIA CUDA Toolkit EULA (redistributable files)",
                "url": "https://docs.nvidia.com/cuda/eula/index.html", "text": write_text("binary/cuda.txt", CUDA_NOTE)})
    out.append({"id": "bin:qt", "name": "Qt 6 / Qt WebEngine (via PySide6)", "version": "", "kind": "binary",
                "license": "LGPL-3.0", "url": "https://www.qt.io/licensing/open-source-lgpl-obligations",
                "text": write_text("binary/qt.txt", QT_NOTE)})
    base = sys.base_prefix
    pylic = os.path.join(base, "LICENSE.txt")
    out.append({"id": "bin:python", "name": "Python runtime", "version": sys.version.split()[0], "kind": "binary",
                "license": "PSF-2.0", "url": "https://docs.python.org/3/license.html",
                "text": write_text("binary/python.txt", open(pylic, encoding="utf-8", errors="replace").read())
                if os.path.isfile(pylic) else ""})
    return out


def model_entries():
    out = []
    try:
        cat = json.load(open(os.path.join(ROOT, "backend", "model_catalog.json"), encoding="utf-8"))["models"]
    except Exception:
        cat = []
    for m in cat:
        out.append({"id": "model:" + m["id"], "name": m["name"] + " (download offered on first run)", "version": m["file"],
                    "kind": "model", "license": m["license"], "url": m.get("license_url") or "", "text": ""})
    for m in EXTRA_MODELS:
        out.append({"id": "model:" + safe(m["name"]).lower(), "name": m["name"], "version": "", "kind": "model",
                    "license": m["license"], "url": m["url"], "text": ""})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=os.path.join(ROOT, "backend", "venv"))
    ap.add_argument("--ui", default=os.path.join(ROOT, "ui"))
    ap.add_argument("--llama", default=os.environ.get("LLAMA_CPP_DIR") or os.path.join(os.path.expanduser("~"), "llama.cpp"))
    a = ap.parse_args()
    os.makedirs(TEXTS, exist_ok=True)
    entries = binary_entries(a.llama, a.venv) + model_entries() + python_entries(a.venv) + npm_entries(a.ui)
    with open(os.path.join(OUT, "third_party.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_by": "packaging/gen_licenses.py", "components": entries}, f, indent=1)
    kinds = {}
    for e in entries:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    unknown = [e["name"] for e in entries if e["license"] in ("unknown", "")]
    print(f"{len(entries)} components: {kinds}")
    if unknown:
        print(f"licence not declared in metadata ({len(unknown)}): {', '.join(unknown[:30])}")


if __name__ == "__main__":
    main()
