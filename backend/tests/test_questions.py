import copy
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.ai.gemma import GemmaQuestions
from app.ai.questions import QuestionError, validate_question

EXAMPLE = json.loads(Path(__file__).with_name("question_example.json").read_text())
CONTEXT = {
    "profile_revision": 4,
    "confirmed_answers": {"field_code": "ai_data", "experience": "basic"},
    "confirmed_evidence": [],
    "eligible_slots": ["learning_goal"],
    "allowed_companion_slots": {"learning_goal": ["goal_text"]},
    "resolved_unknown_slots": [],
}


def validate(value, **kwargs):
    policy = dict(
        eligible_slots={"learning_goal"},
        companion_slots={"learning_goal": {"goal_text"}},
        confirmed_slots={"field_code", "experience"}, resolved_unknown_slots=set(),
    )
    policy.update(kwargs)
    return validate_question(json.dumps(value), **policy)


class QuestionValidationTests(unittest.TestCase):
    def test_contract_example(self):
        result = validate(EXAMPLE)
        self.assertEqual(result.target_slot, "learning_goal")
        # Returning the draft must not insert absent fields as null patches.
        self.assertEqual(result.model_dump()["options"], EXAMPLE["options"])

    def test_schema_and_content_rejections(self):
        for field, value in [("schema_version", "2"), ("selection_mode", "multi"),
                             ("options", []), ("question", " "),
                             ("question", "x" * 181), ("course_candidates", [])]:
            example = copy.deepcopy(EXAMPLE)
            example[field] = value
            with self.subTest(field=field), self.assertRaises(QuestionError):
                validate(example)
        for field, value in [("key", "INVALID"), ("label", " "),
                             ("answer_patch", {}), ("answer_patch", {"learning_goal": None}),
                             ("answer_patch", {"learning_goal": "invented"}),
                             ("label", "<script>alert(1)</script>"),
                             ("description", "https://example.com/course")]:
            example = copy.deepcopy(EXAMPLE)
            example["options"][0][field] = value
            with self.subTest(field=field), self.assertRaises(QuestionError):
                validate(example)

    def test_patch_schema_bounds(self):
        cases = [
            ("budget_krw", -1, False),
            ("budget_krw", 0, True),
            ("goal_text", "가" * 2001, False),
            ("goal_text", "가" * 2000, True),
            ("constraints_text", "가" * 2001, False),
            ("constraints_text", "가" * 2000, True),
        ]
        for slot, value, accepted in cases:
            example = copy.deepcopy(EXAMPLE)
            example["target_slot"] = slot
            for option in example["options"]:
                option["answer_patch"] = {slot: value}
            with self.subTest(slot=slot, accepted=accepted):
                if accepted:
                    self.assertEqual(validate(example, eligible_slots={slot}).target_slot, slot)
                else:
                    with self.assertRaisesRegex(QuestionError, "^invalid_question_json$"):
                        validate(example, eligible_slots={slot})

    def test_duplicates(self):
        for field in ["key", "label"]:
            example = copy.deepcopy(EXAMPLE)
            example["options"][1][field] = example["options"][0][field]
            with self.assertRaises(QuestionError):
                validate(example)
        with self.assertRaises(QuestionError):
            validate_question('{"question":"a","question":"b"}', eligible_slots=set(),
                              companion_slots={}, confirmed_slots=set(), resolved_unknown_slots=set())

    def test_policy_and_confirmed_values(self):
        for policy in [dict(eligible_slots=set()), dict(confirmed_slots={"learning_goal"}),
                       dict(resolved_unknown_slots={"learning_goal"}),
                       dict(companion_slots={}), dict(confirmed_slots={"goal_text"})]:
            with self.subTest(policy=policy), self.assertRaises(QuestionError):
                validate(EXAMPLE, **policy)


class ModelHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                cls.calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(cls.status)
                self.end_headers()
                self.wfile.write(cls.body)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        type(self).calls.clear()
        type(self).status = 200
        type(self).body = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(EXAMPLE),
        }}]}).encode()
        self.client = GemmaQuestions(f"http://127.0.0.1:{self.server.server_port}", "synthetic-model",
                                     timeout=1, max_tokens=512, max_response_bytes=65536)

    def test_local_http_contract(self):
        self.assertEqual(self.client.generate(CONTEXT).target_slot, "learning_goal")
        path, body = self.calls[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "synthetic-model")
        self.assertEqual(body["max_tokens"], 512)
        self.assertFalse(body["stream"])
        self.assertEqual(json.loads(body["messages"][1]["content"]), CONTEXT)

    def test_cancelled_before_call(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(QuestionError, "generation_cancelled"):
            self.client.generate(CONTEXT, cancelled=cancelled)
        self.assertEqual(self.calls, [])

    def test_http_error_sanitization(self):
        type(self).status = 503
        type(self).body = b"secret internal server message"
        with self.assertRaisesRegex(QuestionError, "^model_http_error$"):
            self.client.generate(CONTEXT)

    def test_response_budget(self):
        self.client.max_response_bytes = 10
        with self.assertRaisesRegex(QuestionError, "model_response_too_large"):
            self.client.generate(CONTEXT)

    def test_invalid_response(self):
        for response in [b"not JSON", b"{}", b'{"choices": []}',
                         b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}']:
            type(self).body = response
            with self.subTest(response=response), self.assertRaisesRegex(QuestionError, "invalid_model_response"):
                self.client.generate(CONTEXT)

    def test_transport_timeout(self):
        with patch.object(http.client.HTTPConnection, "request", side_effect=TimeoutError):
            with self.assertRaisesRegex(QuestionError, "^model_timeout$"):
                self.client.generate(CONTEXT)

    def test_missing_policy(self):
        with self.assertRaisesRegex(QuestionError, "invalid_generation_context"):
            self.client.generate({})
        self.assertEqual(self.calls, [])
