"""A stand-in OpenAI-compatible server for dry runs: proves the pipeline (dataset, prompts, scoring, results table)
without a model or the GPU. Every chat completion answers with the same short text.

  python mock_server.py [port]        (default 8150; then run.py --model mock --base-url http://127.0.0.1:8150/v1)
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "ANSWER: A\n\nThis is a mock reply from the dry-run server."


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self._send({"status": "ok"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        prompt_chars = sum(len(str(m.get("content") or "")) for m in req.get("messages") or [])
        self._send({
            "id": f"mock-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
            "model": req.get("model", "mock"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": REPLY}}],
            "usage": {"prompt_tokens": prompt_chars // 4, "completion_tokens": 16,
                      "total_tokens": prompt_chars // 4 + 16}})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8150
    print(f"mock OpenAI server on http://127.0.0.1:{port}/v1", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
