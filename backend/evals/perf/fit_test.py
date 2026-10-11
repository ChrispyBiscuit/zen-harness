"""Fit + speed test for a model file under the production 128k profile (same llama-server flags as the app).

python fit_test.py <model.gguf> [label]
Prints: loaded?, layers on GPU, VRAM used, generation tok/s, drafter acceptance, prompt tok/s.
Run with nothing else on the GPU.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

LLAMA = r"C:\Users\user\llama.cpp\llama-server.exe"
DRAFT = os.environ.get("DRAFT") or r"C:\Users\user\models\originals\mtp-qwen3.5-9b-q4head.gguf"
PORT = 8131
model = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(model)
log_path = os.path.join(os.environ.get("TEMP", "."), f"fit_{label}.log")

args = [LLAMA, "-m", model, "-c", "131072", "-ngl", "99", "-fa", "on", "--port", str(PORT), "--host", "127.0.0.1",
        "--parallel", "1", "--jinja", "-cram", "1536", "-ctxcp", "6", "-ctk", "q8_0", "-ctv", "q4_0",
        "-md", DRAFT, "--spec-type", "draft-mtp,ngram-simple", "--spec-draft-n-max", os.environ.get("SPEC_N", "3"), "-ngld", "99",
        "-ub", "128", "-ctkd", "q4_0", "-ctvd", "q4_0"]
if os.environ.get("NO_DRAFT"):                 # raw model speed: no drafter, no n-gram speculation
    i = args.index("-md")
    args = args[:i] + ["-ub", "128"]


def vram():
    o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True).stdout
    return int(o.strip().splitlines()[0])


def post(path, body, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


base = vram()
with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
    p = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)
ok = False
t0 = time.time()
while time.time() - t0 < 240:
    if p.poll() is not None:
        break
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
            if json.load(r).get("status") == "ok":
                ok = True
                break
    except Exception:
        pass
    time.sleep(2)
log = open(log_path, encoding="utf-8", errors="replace").read()
res = {"label": label, "loaded": ok, "vram_before_mib": base}
m = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", log)
res["layers_on_gpu"] = [f"{a}/{b}" for a, b in m]
res["cpu_buffers_mib"] = re.findall(r"CPU(?:_Mapped)? model buffer size\s*=\s*([\d.]+) MiB", log)
if not ok:
    res["error_tail"] = log[-1200:]
    print(json.dumps(res, indent=1))
    try:
        p.kill()
    except Exception:
        pass
    sys.exit(1)
res["vram_loaded_mib"] = vram()
prompt = ("Write a detailed, well-structured explanation of how a hash map works internally: hashing, buckets, "
          "collision handling with chaining and open addressing, load factor and resizing, and the time complexity "
          "of each operation. Include a short Python implementation with comments.")
runs = []
for _ in range(2):
    d = post("/v1/chat/completions", {"messages": [{"role": "user", "content": prompt}], "max_tokens": 700,
                                      "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}})
    t = d.get("timings") or {}
    runs.append({"gen_tps": round(t.get("predicted_per_second", 0), 1), "pp_tps": round(t.get("prompt_per_second", 0), 1),
                 "tokens": t.get("predicted_n"), "draft_n": t.get("draft_n"), "draft_accepted": t.get("draft_n_accepted")})
res["runs"] = runs
dn = sum(r["draft_n"] or 0 for r in runs)
da = sum(r["draft_accepted"] or 0 for r in runs)
res["draft_accept_pct"] = round(100 * da / dn, 1) if dn else None
res["vram_peak_mib"] = vram()
res["gpu_shared_hint"] = "check Task Manager 'Shared GPU memory' if speed is low"
print(json.dumps(res, indent=1))
p.terminate()
try:
    p.wait(20)
except Exception:
    p.kill()
