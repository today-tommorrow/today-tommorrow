import copy
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.ai.gemma import PROMPT_VERSION, GemmaQuestions
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


def draft_with_patches(slot, options):
    draft = copy.deepcopy(EXAMPLE)
    draft["target_slot"] = slot
    draft["options"] = [
        {"key": f"option_{index}", "label": label, "description": "",
         "answer_patch": answer_patch}
        for index, (label, answer_patch) in enumerate(options)
    ]
    return draft


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
            example["options"] = example["options"][:2]
            example["options"][0]["answer_patch"] = {slot: value}
            example["options"][1]["answer_patch"] = {
                slot: 1000 if slot == "budget_krw" else "다른 입력",
            }
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

    def test_observed_budget_labels_cannot_hide_identical_patches(self):
        draft = draft_with_patches("budget_status", [
            (label, {"budget_status": "no_limit"})
            for label in ["무료", "10만 원 이하", "10만~50만 원", "금액 제한 없음"]
        ])
        with self.assertRaisesRegex(QuestionError, "^duplicate_question_patch$"):
            validate(draft, eligible_slots={"budget_status"},
                     companion_slots={"budget_status": {"budget_krw"}})

    def test_observed_online_variants_cannot_hide_identical_patches(self):
        draft = draft_with_patches("delivery_mode", [
            ("온라인 실시간", {"delivery_mode": "online"}),
            ("방문 수업", {"delivery_mode": "offline"}),
            ("온라인 자율 학습", {"delivery_mode": "online"}),
        ])
        with self.assertRaisesRegex(QuestionError, "^duplicate_question_patch$"):
            validate(draft, eligible_slots={"delivery_mode"})

    def test_duplicate_patch_ignores_object_key_order(self):
        draft = draft_with_patches("budget_status", [
            ("무료", {"budget_status": "amount", "budget_krw": 0}),
            ("유료", {"budget_krw": 0, "budget_status": "amount"}),
        ])
        with self.assertRaisesRegex(QuestionError, "^duplicate_question_patch$"):
            validate(draft, eligible_slots={"budget_status"},
                     companion_slots={"budget_status": {"budget_krw"}})

    def test_budget_options_preserve_zero_and_distinct_caps(self):
        draft = draft_with_patches("budget_status", [
            ("무료만", {"budget_status": "amount", "budget_krw": 0}),
            ("10만 원까지", {"budget_status": "amount", "budget_krw": 100000}),
            ("상한 없음", {"budget_status": "no_limit"}),
        ])
        validated = validate(draft, eligible_slots={"budget_status"},
                             companion_slots={"budget_status": {"budget_krw"}})
        self.assertEqual(validated.model_dump()["options"], draft["options"])

    def test_null_budget_cap_does_not_distinguish_no_amount_options(self):
        for status in ["no_limit", "unknown"]:
            draft = draft_with_patches("budget_status", [
                ("첫 번째", {"budget_status": status}),
                ("두 번째", {"budget_status": status, "budget_krw": None}),
            ])
            with self.subTest(status=status), self.assertRaisesRegex(QuestionError, "^duplicate_question_patch$"):
                validate(draft, eligible_slots={"budget_status"},
                         companion_slots={"budget_status": {"budget_krw"}})

    def test_comparison_does_not_remove_explicit_nulls_from_returned_patches(self):
        draft = draft_with_patches("budget_status", [
            ("상한 없음", {"budget_status": "no_limit", "budget_krw": None}),
            ("무료만", {"budget_status": "amount", "budget_krw": 0}),
        ])
        result = validate(draft, eligible_slots={"budget_status"},
                          companion_slots={"budget_status": {"budget_krw"}})
        self.assertEqual(result.model_dump()["options"], draft["options"])
        draft = draft_with_patches("learning_goal", [
            ("기초 목표 유지", {"learning_goal": "foundation"}),
            ("기초 목표 초기화", {"learning_goal": "foundation", "goal_text": None}),
        ])
        self.assertEqual(validate(draft).model_dump()["options"], draft["options"])

    def test_budget_amount_required_only_when_companion_can_be_collected(self):
        draft = draft_with_patches("budget_status", [
            ("상한 금액 있음", {"budget_status": "amount"}),
            ("상한 없음", {"budget_status": "no_limit"}),
        ])
        validate(draft, eligible_slots={"budget_status"}, companion_slots={})
        validate(draft, eligible_slots={"budget_status"},
                 companion_slots={"budget_status": {"budget_krw"}},
                 confirmed_slots={"budget_krw"})
        for value in [{"budget_status": "amount"},
                      {"budget_status": "amount", "budget_krw": None}]:
            draft["options"][0]["answer_patch"] = value
            with self.subTest(value=value), self.assertRaisesRegex(QuestionError, "^missing_budget_amount$"):
                validate(draft, eligible_slots={"budget_status"},
                         companion_slots={"budget_status": {"budget_krw"}})

    def test_budget_cap_cannot_mean_no_limit_or_unknown(self):
        for status in ["no_limit", "unknown"]:
            draft = draft_with_patches("budget_status", [
                ("금액 있음", {"budget_status": status, "budget_krw": 100000}),
                ("무료만", {"budget_status": "amount", "budget_krw": 0}),
            ])
            with self.subTest(status=status), self.assertRaisesRegex(QuestionError, "^inconsistent_budget_patch$"):
                validate(draft, eligible_slots={"budget_status"},
                         companion_slots={"budget_status": {"budget_krw"}})

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

    def test_request_explains_confirmed_domain_and_budget_codes(self):
        context = {
            **CONTEXT, "eligible_slots": ["budget_status"],
            "allowed_companion_slots": {"budget_status": ["budget_krw"]},
        }
        draft = draft_with_patches("budget_status", [
            ("무료만", {"budget_status": "amount", "budget_krw": 0}),
            ("상한 없음", {"budget_status": "no_limit"}),
        ])
        type(self).body = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(draft),
        }}]}).encode()
        self.client.generate(context)
        body = self.calls[0][1]
        system = body["messages"][0]["content"]
        self.assertEqual(PROMPT_VERSION, "question-0.8.0-2")
        self.assertIn("출력 언어는 한국어", system)
        self.assertIn("한국어 학습을 교육 분야로 가정하지 마세요", system)
        contracts = json.loads(system.split("\n슬롯 계약:\n", 1)[1])
        self.assertEqual(set(contracts), {"field_code", "experience", "budget_status", "budget_krw"})
        self.assertIn("ai_data=AI·데이터", contracts["field_code"]["description"])
        self.assertIn("no_limit=지출 상한 없음", contracts["budget_status"]["description"])
        self.assertIn("무료만 원하면 0", contracts["budget_krw"]["description"])
        schema = body["response_format"]["json_schema"]["schema"]
        patches = schema["properties"]["options"]["items"]["properties"]["answer_patch"]["properties"]
        self.assertEqual(contracts["budget_status"], patches["budget_status"])
        self.assertEqual(json.loads(body["messages"][1]["content"]), context)

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
