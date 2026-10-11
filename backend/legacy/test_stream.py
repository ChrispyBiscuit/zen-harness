"""Verify tokens arrive incrementally (streaming), not in one lump."""
import time
import agent

deltas = []
t0 = time.time()

def emit(kind, data=None):
    if kind == "ans_delta":
        deltas.append((round(time.time() - t0, 2), data))
    elif kind == "answer_end":
        print(f"\n[answer_end after {len(deltas)} deltas]")
    elif kind == "error":
        print("!! ERROR:", data)
    elif kind in ("status", "tool_turn", "think_end"):
        print(f"[{kind}] {data or ''}")

agent.run_agent_events(
    "In one short sentence, what is a firefly?",
    model="qwen3:30b-a3b", emit=emit, think=False,
    temperature=0.7, num_ctx=8192, max_steps=4)

print("delta count:", len(deltas))
if deltas:
    print("first 5 arrival times:", [d[0] for d in deltas[:5]])
    print("last arrival time:", deltas[-1][0])
    print("reconstructed:", "".join(d[1] for d in deltas).strip())
print("STREAMING OK" if len(deltas) > 3 else "NOT STREAMING (single lump)")
