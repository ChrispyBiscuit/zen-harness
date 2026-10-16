"""Read a picture of an equation into LaTeX (pix2tex, on the CPU only).

Runs in its own process so torch never loads into the app and the GPU is never touched:
  python formula_ocr.py <image path>    -> prints {"latex": "..."} or {"error": "..."}
read(path) is the helper the app calls (subprocess, 120 s limit).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read(path, timeout=180):
    if getattr(sys, "frozen", False):           # pix2tex + PyTorch (4+ GB) are not part of the installed app
        return {"error": "formula recognition is not included in this version of Zazen"}
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([sys.executable, os.path.join(HERE, "formula_ocr.py"), path], capture_output=True,
                           text=True, encoding="utf-8", timeout=timeout, env=env, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return {"error": "reading the formula took too long"}
    for line in reversed((p.stdout or "").splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"error": (p.stderr or "no output").strip().splitlines()[-1][:300] if p.stderr else "no output"}


def _main(path):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        from PIL import Image
        from pix2tex.cli import LatexOCR
        model = LatexOCR()                    # CUDA_VISIBLE_DEVICES="" above: it runs on the CPU
        img = Image.open(path).convert("RGB")
        latex = model(img)
        print(json.dumps({"latex": latex}))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    _main(sys.argv[1])
