"""Verify /no_think path: no reasoning leak, clean content, streaming works."""
import agent

ans, think = [], []
def emit(kind, data=None):
    if kind == "ans_delta":
        ans.append(data)
    elif kind == "think_delta":
        think.append(data)

agent.run_agent_events("In one short sentence, what is a firefly?",
                       model="qwen3:30b-a3b", emit=emit, think=False,
                       temperature=0.7, num_ctx=8192, max_steps=4)

answer = "".join(ans)
print("ans deltas:", len(ans), "| think deltas:", len(think))
print("has <think> tag in answer?:", "<think>" in answer or "</think>" in answer)
print("=== ANSWER ===")
print(answer.strip())
