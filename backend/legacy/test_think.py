"""Does think=True cleanly separate reasoning from the answer for this model?"""
import ollama

c = ollama.Client()
MODEL = "qwen3:30b-a3b"
msgs = [{"role": "user", "content": "In one short sentence, what is a firefly?"}]

think_txt, content_txt = [], []
for chunk in c.chat(model=MODEL, messages=msgs, options={"num_ctx": 8192},
                    think=True, stream=True):
    m = chunk.message
    if getattr(m, "thinking", None):
        think_txt.append(m.thinking)
    if m.content:
        content_txt.append(m.content)

content = "".join(content_txt)
print("=== thinking captured (chars):", len("".join(think_txt)))
print("=== content has <think> tag?:", "<think>" in content or "</think>" in content)
print("=== CONTENT (the answer) ===")
print(content.strip())
