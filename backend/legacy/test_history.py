"""Verify the model now receives conversation history (multi-turn memory)."""
import agent

hist = [
    {"role": "user", "content": "My name is Chris and my favorite color is teal."},
    {"role": "assistant", "content": "Nice to meet you, Chris!"},
]
ans = []
def emit(kind, data=None):
    if kind == "ans_delta":
        ans.append(data)

agent.run_agent_events("What is my name and my favorite color?",
                       model="qwen3:30b-a3b", emit=emit, think=False,
                       temperature=0.3, num_ctx=8192, max_steps=3, history=hist)

a = "".join(ans).strip()
print("ANSWER:", a)
ok = "chris" in a.lower() and "teal" in a.lower()
print("HISTORY OK" if ok else "HISTORY FAIL")
