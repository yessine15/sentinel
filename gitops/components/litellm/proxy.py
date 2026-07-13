"""
Tiny LLM proxy: OpenAI-compatible /v1/chat/completions → Ollama.

Uses only stdlib (http.server, urllib, json) — no pip install needed.
Runs on python:3.12-alpine (~50MB) in <1s cold start.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.error

PROXY_PORT = int(os.environ.get("PROXY_PORT", "4000"))
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class Proxy(BaseHTTPRequestHandler):
    def _send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _ollama(self, path, body=None):
        url = f"{OLLAMA_URL}{path}"
        req_body = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=req_body, method="POST" if body else "GET")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=180)
            return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code
        except Exception as e:
            return {"error": str(e)}, 502

    def do_GET(self):
        if self.path in ("/health/liveliness", "/health/readiness", "/health"):
            self._send_json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            try:
                data, _ = self._ollama("/api/tags")
                models = [{"id": m["name"], "object": "model"} for m in data.get("models", [])]
                self._send_json(200, {"object": "list", "data": models})
            except Exception as e:
                self._send_json(502, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            body = self._read_body()
            model = body.get("model", "gemma4").replace("ollama/", "")
            messages = body.get("messages", [])
            # Build Ollama prompt from messages
            prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages if m.get("content"))
            data, status = self._ollama("/api/generate", {"model": model, "prompt": prompt, "stream": False})
            if status == 200:
                result = {
                    "id": "chatcmpl-local",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": data.get("response", "")},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
                self._send_json(200, result)
            else:
                self._send_json(status, data)
        elif self.path == "/v1/embeddings":
            body = self._read_body()
            model = body.get("model", "bge-m3")
            inp = body.get("input", "")
            if isinstance(inp, list):
                inp = inp[0]
            data, status = self._ollama("/api/embed", {"model": model, "input": inp})
            if status == 200:
                result = {
                    "object": "list",
                    "data": [{"object": "embedding", "embedding": data.get("embeddings", [[]])[0], "index": 0}],
                    "model": model,
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                }
                self._send_json(200, result)
            else:
                self._send_json(status, data)
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        pass  # suppress access logs


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PROXY_PORT), Proxy)
    print(f"LLM Gateway listening on 0.0.0.0:{PROXY_PORT} (Ollama at {OLLAMA_URL})", flush=True)
    server.serve_forever()
