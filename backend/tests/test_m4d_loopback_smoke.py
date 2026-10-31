"""Loopback HTTP contract smoke test without external model dependencies."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.m4d_local_client import LocalChatClient
from app.m4d_vlm import review_jpeg
from app.m4d_attributes import parse_attribute_candidates


def test_local_http_transport_to_strict_attributes():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            import json
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            requests.append((self.path, body))
            response = json.dumps({
                "id": "mock-request",
                "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps({"attributes": [
                        {"name": "clothing_upper", "state": "visible", "value": "blue"}
                    ]})
                }}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = LocalChatClient(f"http://127.0.0.1:{server.server_port}/v1", 5)
        jpeg = b"\xff\xd8\xfflocal-mock\xff\xd9"
        result = review_jpeg(jpeg, prompt="visible attributes only", model="mock-vision", client=client)
        parsed = parse_attribute_candidates(result)
        assert result.status == "completed"
        assert parsed.status == "parsed"
        assert parsed.candidates[0].value == "blue"
        assert requests[0][0] == "/v1/chat/completions"
        assert requests[0][1]["model"] == "mock-vision"
        assert requests[0][1]["messages"][0]["content"][0]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
