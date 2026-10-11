import psutil, subprocess
print("=== processes matching ollama/llama ===")
for p in psutil.process_iter(["name"]):
    n = (p.info["name"] or "")
    if "llama" in n.lower() or "ollama" in n.lower():
        try:
            print(f"  pid={p.pid}  {n}  rss={p.memory_info().rss/1e9:.2f}GB")
        except Exception as e:
            print(f"  pid={p.pid}  {n}  err={e}")
print("=== nvidia-smi compute-apps ===")
print(subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                      "--format=csv,noheader"], capture_output=True, text=True).stdout or "(none)")
print("=== nvidia-smi total used ===")
print(subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                      "--format=csv,noheader"], capture_output=True, text=True).stdout)
