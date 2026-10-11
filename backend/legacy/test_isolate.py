"""Pin down what triggers the CUDA fault: streaming vs tools vs both."""
import ollama
import agent

c = ollama.Client()
MODEL = "qwen3:30b-a3b"
msgs = [{"role": "user", "content": "Say hello in five words."}]
opts = {"num_ctx": 8192}


def trial(name, **kw):
    try:
        if kw.get("stream"):
            n = 0
            for _ in c.chat(model=MODEL, messages=msgs, options=opts, **kw):
                n += 1
            print(f"{name}: OK ({n} chunks)")
        else:
            r = c.chat(model=MODEL, messages=msgs, options=opts, **kw)
            print(f"{name}: OK ({r.message.content[:40]!r})")
    except Exception as e:
        print(f"{name}: ERROR -> {e}")


trial("A non-stream, no tools")
trial("B non-stream, WITH tools", tools=agent.TOOL_SCHEMAS)
trial("C stream, no tools", stream=True)
trial("D stream, WITH tools", tools=agent.TOOL_SCHEMAS, stream=True)
