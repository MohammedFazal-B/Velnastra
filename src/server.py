import json
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from src.config import (
    FIREWALL_HOST, FIREWALL_PORT, MAX_HISTORY_TURNS,
    MAX_REQUEST_BYTES, MAX_PENDING_EVALUATIONS, COLLECTION_NAME,
)
from src.firewall_core import check_user_input, valid_history
import time
from src.analytics import log_request, get_analytics_stats, get_recent_requests

WEB_DIR = Path(__file__).resolve().parent / "web"
WEB_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def make_handler(model, client, llm):
    if MAX_PENDING_EVALUATIONS < 1:
        raise ValueError("MAX_PENDING_EVALUATIONS must be positive")
    inference_lock = threading.Lock()
    pending = threading.BoundedSemaphore(MAX_PENDING_EVALUATIONS)

    class FirewallHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def _send_json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if code == 429:
                self.send_header("Retry-After", "1")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def _allowed_request(self):
            try:
                host = urlparse("//" + self.headers.get("Host", "")).hostname
                if host not in {"localhost", "127.0.0.1", "::1", "firewall", "firewall-app"}:
                    return False
                origin = self.headers.get("Origin")
                if origin is None:
                    return True
                parsed = urlparse(origin)
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                return (parsed.scheme in {"http", "https"} and parsed.hostname == host
                        and origin_port == self.server.server_port)
            except ValueError:
                return False

        def do_GET(self):
            if not self._allowed_request():
                self._send_json(403, {"status":"ERROR","reason":"Host or origin is not allowed"})
                return
            path = urlparse(self.path).path
            if path in WEB_ASSETS:
                filename, content_type = WEB_ASSETS[path]
                try:
                    body = (WEB_DIR / filename).read_bytes()
                except OSError:
                    self._send_json(503, {"status":"ERROR","reason":"Interface assets unavailable"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                return
            if path == "/health":
                try:
                    vectors_ready = bool(client.collection_exists(COLLECTION_NAME))
                except Exception:
                    vectors_ready = False
                ready = llm is not None and vectors_ready
                self._send_json(200 if ready else 503, {
                    "status":"ok" if ready else "degraded",
                    "cognitive_layer":llm is not None,
                    "vector_store":"ready" if vectors_ready else "unavailable",
                    "limits":{"history_turns":MAX_HISTORY_TURNS,"request_bytes":MAX_REQUEST_BYTES},
                })
                return
            if path == "/api/activity":
                try:
                    self._send_json(200, {"requests":get_recent_requests(), "limit":30})
                except Exception as exc:
                    print(f"Activity read failed: {exc}", file=sys.stderr)
                    self._send_json(503, {"status":"ERROR","reason":"Activity unavailable"})
                return
            if path == "/api/analytics":
                try:
                    self._send_json(200, get_analytics_stats())
                except Exception as exc:
                    print(f"Analytics read failed: {exc}", file=sys.stderr)
                    self._send_json(503, {"status":"ERROR","reason":"Analytics unavailable"})
                return
            self._send_json(404, {"status":"ERROR","reason":"Not found"})

        def do_POST(self):
            if not self._allowed_request():
                self._send_json(403, {"status":"ERROR","reason":"Host or origin is not allowed"})
                return
            if urlparse(self.path).path != "/check":
                self._send_json(404, {"status":"ERROR","reason":"Not found"})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                self._send_json(400, {"status":"ERROR","reason":"A single Content-Length is required"})
                return
            try:
                length = int(lengths[0])
                if length < 0:
                    raise ValueError()
            except ValueError:
                self._send_json(400, {"status":"ERROR","reason":"Invalid request size"})
                return
            if length > MAX_REQUEST_BYTES:
                self._send_json(413, {"status":"ERROR","reason":"Request exceeds 64 KiB"})
                return
            if self.headers.get_content_type() != "application/json":
                self._send_json(415, {"status":"ERROR","reason":"Content-Type must be application/json"})
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                payload = json.loads(raw.decode("utf-8"))
            except (TimeoutError, OSError):
                self._send_json(408, {"status":"ERROR","reason":"Request body timed out"})
                return
            except (ValueError, UnicodeDecodeError, RecursionError):
                self._send_json(400, {"status":"ERROR","reason":"Invalid JSON"})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"status":"ERROR","reason":"Expected JSON object"})
                return
            prompt, history = payload.get("prompt"), payload.get("history", [])
            if not isinstance(prompt, str) or not prompt.strip() or not valid_history(history):
                self._send_json(400, {"status":"ERROR","reason":
                    f"Provide a nonempty prompt and at most {MAX_HISTORY_TURNS} user/assistant history turns"})
                return
            if not pending.acquire(blocking=False):
                self._send_json(429, {"status":"ERROR","reason":"Classifier busy; retry shortly"})
                return
            started = time.perf_counter()
            try:
                try:
                    with inference_lock:
                        queued_ms = (time.perf_counter() - started) * 1000
                        result = check_user_input(prompt, model, client, llm, history=history)
                        result.setdefault("timings_ms", {})["queue"] = round(queued_ms, 2)
                except Exception as exc:
                    print(f"Evaluation failed: {exc}", file=sys.stderr)
                    self._send_json(503, {"status":"ERROR","reason":"Evaluation unavailable"})
                    return
                latency = (time.perf_counter() - started) * 1000
                if result.get("status") in {"ALLOW", "BLOCK"}:
                    try:
                        request_id = log_request(
                            prompt, result["status"], result.get("reason"),
                            result.get("score",0), latency, result.get("category"))
                        if isinstance(request_id, int):
                            result["request_id"] = request_id
                    except Exception as exc:
                        print(f"Analytics write failed: {exc}", file=sys.stderr)
                        result["telemetry"] = {"status":"error"}
                code = 200
                if result.get("status") == "ERROR":
                    code = {"context_limit":413, "invalid_request":400}.get(result.get("error_code"),503)
                self._send_json(code, result)
            finally:
                pending.release()

        def log_message(self, format, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    return FirewallHandler


class FirewallHTTPServer(ThreadingHTTPServer):
    # Drain in-flight model calls before main() releases the GPU model.
    daemon_threads = False
    block_on_close = True


def run_server(model, client, llm):
    server = FirewallHTTPServer((FIREWALL_HOST, FIREWALL_PORT), make_handler(model, client, llm))
    print("\n" + "=" * 50)
    print("RAG prompt firewall (HTTP)")
    print("=" * 50)
    print("Qdrant retrieval context -> Qwen intent classification")
    print(f"Open http://127.0.0.1:{FIREWALL_PORT}/ to type a prompt.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
