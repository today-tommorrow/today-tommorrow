import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from app.recommendations.contracts import RecommendationError
from app.recommendations.model import GemmaRecommendations, strict_json
from app.recommendations.settings import RecommendationSettings


class RecommendationModelTests(unittest.TestCase):
    def setUp(self):
        self.status = 200
        self.body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '{"needs":[]}'}}]}).encode()
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(owner.status)
                self.end_headers()
                self.wfile.write(owner.body)
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.model = GemmaRecommendations(self.base, "synthetic-http", timeout=1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_live_http_adapter_sends_strict_schema_and_budgets(self):
        result = self.model.generate("profiling", {"sources": []}, {"type": "object"})
        self.assertEqual(result, {"needs": []})
        path, body = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "synthetic-http")
        self.assertEqual(body["max_tokens"], 2048)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertEqual(self.model.runtime_mode, "real_model")

    def test_truncated_output_and_http_failure_are_explicit(self):
        self.body = b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}'
        with self.assertRaisesRegex(RecommendationError, "invalid_model_response"):
            self.model.generate("profiling", {}, {})
        self.status = 500
        with self.assertRaisesRegex(RecommendationError, "recommendation_model_http_error"):
            self.model.generate("profiling", {}, {})

    def test_oversized_output_and_input_fail_safely(self):
        model = GemmaRecommendations(self.base, "synthetic-http", timeout=1, max_response_bytes=1024)
        self.body = b"x" * 1025
        with self.assertRaisesRegex(RecommendationError, "model_response_too_large"):
            model.generate("profiling", {}, {})
        self.requests.clear()
        with self.assertRaisesRegex(RecommendationError, "model_context_too_large"):
            model.generate("profiling", {"source": "가" * 10000}, {})
        self.assertFalse(self.requests)

    def test_duplicate_keys_nan_and_nonobject_json_rejected(self):
        for raw in ('{"a":1,"a":2}', '{"n":NaN}', '[]', '{"n":Infinity}', '{"n":1e999}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(RecommendationError, "invalid_model_response"):
                strict_json(raw)

    def test_bad_url_or_unconfigured_runtime_has_no_sample_fallback(self):
        for url in ("file:///tmp/private", "https://name:password@example.test", "http://host/path", "http://host/#x"):
            with self.subTest(url=url), self.assertRaises(RecommendationError):
                GemmaRecommendations(url, "model", timeout=1)
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(RecommendationError, "provider_unavailable"):
            RecommendationSettings.load()


if __name__ == "__main__":
    unittest.main()
