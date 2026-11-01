import copy
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from app.ai.embeddings import BgeM3Embeddings, EmbeddingError

REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
METADATA = {
    "repository": "BAAI/bge-m3", "revision": REVISION, "dimension": 1024,
    "profile": "bge-m3-flagembedding-cuda-v1", "model": "bge-m3",
    "device": "cuda:0", "dtype": "torch.float16", "attention": "sdpa",
}
VECTOR = [1.0] + [0.0] * 1023
RESPONSE = {
    "object": "list", "model": "bge-m3",
    "data": [{"object": "embedding", "index": 0, "embedding": VECTOR}],
    "usage": {"prompt_tokens": 2, "total_tokens": 2},
}


class EmbeddingHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calls: list[tuple[str, str, bytes]] = []
        cls.replies: dict[str, tuple[int, bytes]] = {}

        class Handler(BaseHTTPRequestHandler):
            def respond(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                cls.calls.append((self.command, self.path, body))
                status, response = cls.replies[self.path]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def do_GET(self) -> None:
                self.respond()

            def do_POST(self) -> None:
                self.respond()

            def log_message(self, format: str, *args: object) -> None:
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self) -> None:
        type(self).calls.clear()
        type(self).replies = {
            "/metadata": (200, json.dumps(METADATA).encode()),
            "/v1/embeddings": (200, json.dumps(RESPONSE).encode()),
        }
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.client = BgeM3Embeddings(self.base, timeout=1)

    def test_verbatim_query_with_explicit_corpus_length_and_float_format(self) -> None:
        original = "  한국어 e\u0301\n\t"
        vector = self.client.embed(original, revision=REVISION)
        self.assertEqual(vector, tuple(VECTOR))
        self.assertIsInstance(vector, tuple)
        self.assertEqual([(method, path) for method, path, _ in self.calls], [
            ("GET", "/metadata"), ("POST", "/v1/embeddings"),
        ])
        self.assertEqual(json.loads(self.calls[1][2]), {
            "model": "bge-m3", "input": original, "max_length": 512, "encoding_format": "float",
        })

    def test_metadata_is_rechecked_for_every_query(self) -> None:
        self.client.embed("첫 문장", revision=REVISION)
        metadata = {**METADATA, "revision": "changed"}
        self.replies["/metadata"] = (200, json.dumps(metadata).encode())
        with self.assertRaisesRegex(EmbeddingError, "^embedding_profile_mismatch$"):
            self.client.embed("둘째 문장", revision=REVISION)
        self.assertEqual([method for method, _, _ in self.calls], ["GET", "POST", "GET"])

    def test_each_runtime_metadata_contract_is_enforced(self) -> None:
        mismatches = {
            "repository": "unverified/model", "revision": "other-revision", "dimension": 1024.0,
            "profile": "unverified-profile", "model": "other-model", "device": "cpu",
            "dtype": "torch.float32", "attention": "eager",
        }
        for key, value in mismatches.items():
            self.calls.clear()
            self.replies["/metadata"] = (200, json.dumps({**METADATA, key: value}).encode())
            with self.subTest(key=key), self.assertRaisesRegex(EmbeddingError, "^embedding_profile_mismatch$"):
                self.client.embed("private input", revision=REVISION)
            self.assertEqual([path for _, path, _ in self.calls], ["/metadata"])

    def test_missing_metadata_field_is_not_assumed(self) -> None:
        metadata = {key: value for key, value in METADATA.items() if key != "attention"}
        self.replies["/metadata"] = (200, json.dumps(metadata).encode())
        with self.assertRaisesRegex(EmbeddingError, "^embedding_profile_mismatch$"):
            self.client.embed("query", revision=REVISION)

    def test_http_errors_and_redirects_never_return_fallback_or_server_details(self) -> None:
        for path in ["/metadata", "/v1/embeddings"]:
            for status in [302, 429, 503]:
                self.setUp()
                self.replies[path] = (status, b"private endpoint and server error")
                with self.subTest(path=path, status=status), self.assertRaisesRegex(EmbeddingError, "^embedding_http_error$"):
                    self.client.embed("private query", revision=REVISION)
                self.assertEqual(sum(method == "POST" for method, _, _ in self.calls), int(path != "/metadata"))

    def test_size_limit_is_applied_to_metadata_and_embedding_response(self) -> None:
        self.client = BgeM3Embeddings(self.base, timeout=1, max_response_bytes=400)
        for path in ["/metadata", "/v1/embeddings"]:
            self.replies[path] = (200, b" " * 401)
            with self.subTest(path=path), self.assertRaisesRegex(EmbeddingError, "^embedding_response_too_large$"):
                self.client.embed("query", revision=REVISION)
            self.replies[path] = (200, json.dumps(METADATA if path == "/metadata" else RESPONSE).encode())

    def test_invalid_json_is_rejected_without_returning_response(self) -> None:
        invalid = [
            b"secret invalid JSON", b"[]", b"\xff", b'{"data":[],"data":[]}',
            b'{"value":NaN}', b'{"value":Infinity}', b'{"value":1e999}',
        ]
        for path in ["/metadata", "/v1/embeddings"]:
            for body in invalid:
                self.setUp()
                self.replies[path] = (200, body)
                with self.subTest(path=path, body=body), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_response$"):
                    self.client.embed("private query", revision=REVISION)

    def test_single_embedding_envelope_model_and_index_are_required(self) -> None:
        cases = [
            {}, {**RESPONSE, "object": "other"}, {**RESPONSE, "model": "other"},
            {**RESPONSE, "data": []}, {**RESPONSE, "data": RESPONSE["data"] * 2},
            {**RESPONSE, "data": [None]},
        ]
        for field, value in [("index", 1), ("index", False), ("index", 0.0), ("object", "other")]:
            case = copy.deepcopy(RESPONSE)
            case["data"][0][field] = value
            cases.append(case)
        for case in cases:
            self.replies["/v1/embeddings"] = (200, json.dumps(case).encode())
            with self.subTest(case=case.get("object")), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_response$"):
                self.client.embed("query", revision=REVISION)

    def test_vector_dimension_numeric_types_and_unit_norm_are_required(self) -> None:
        vectors = [VECTOR[:-1], VECTOR + [0.0], [0.0] * 1024, [0.5] + VECTOR[1:],
                   [True] + VECTOR[1:], ["1"] + VECTOR[1:], [None] + VECTOR[1:],
                   [[1]] + VECTOR[1:], [10 ** 400] + VECTOR[1:]]
        for vector in vectors:
            response = copy.deepcopy(RESPONSE)
            response["data"][0]["embedding"] = vector
            self.replies["/v1/embeddings"] = (200, json.dumps(response).encode())
            with self.subTest(size=len(vector)), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_vector$"):
                self.client.embed("query", revision=REVISION)

    def test_finite_vector_with_roundoff_is_not_renormalized(self) -> None:
        vector = [1.000001] + VECTOR[1:]
        response = copy.deepcopy(RESPONSE)
        response["data"][0]["embedding"] = vector
        self.replies["/v1/embeddings"] = (200, json.dumps(response).encode())
        self.assertEqual(self.client.embed("query", revision=REVISION), tuple(vector))

    def test_transport_failures_are_sanitized(self) -> None:
        for error, code in [
            (TimeoutError("private socket"), "embedding_timeout"),
            (OSError("private host"), "embedding_unavailable"),
            (http.client.HTTPException("private response"), "embedding_unavailable"),
        ]:
            with self.subTest(code=code), patch.object(http.client.HTTPConnection, "request", side_effect=error):
                with self.assertRaisesRegex(EmbeddingError, f"^{code}$"):
                    self.client.embed("private query", revision=REVISION)

    def test_invalid_input_is_rejected_before_any_network_call(self) -> None:
        for text, revision in [(None, REVISION), ("", REVISION), ("\ud800", REVISION),
                               ("query", None), ("query", ""), ("query", "\ud800")]:
            with self.subTest(text=repr(text), revision=revision), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_input$"):
                self.client.embed(text, revision=revision)
        self.assertEqual(self.calls, [])

    def test_invalid_endpoint_and_budgets_fail_before_network(self) -> None:
        for url in ["file:///tmp/model", "http:///missing", "http://user:password@host",
                    "http://@host", "http://host/path", "http://host?secret=1", "http://host#part",
                    "http://host:0", "http://host:65536", "http://host:bad", "http://host\n", None]:
            with self.subTest(url=url), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_base_url$"):
                BgeM3Embeddings(url, timeout=1)
        for timeout in [0, -1, float("nan"), float("inf"), 10 ** 400, True, "1", None]:
            with self.subTest(timeout=timeout), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_budget$"):
                BgeM3Embeddings(self.base, timeout=timeout)
        for maximum in [0, -1, True, 100.0, "100", None]:
            with self.subTest(maximum=maximum), self.assertRaisesRegex(EmbeddingError, "^invalid_embedding_budget$"):
                BgeM3Embeddings(self.base, timeout=1, max_response_bytes=maximum)
        self.assertEqual(self.calls, [])
