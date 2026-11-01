"""Real DB/API/worker flow with an explicitly injected test question provider."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Event, Thread
from unittest.mock import patch
from uuid import uuid4

import test_profile_drafts as profiles
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.ai.questions import QuestionError, validate_question
from app.jobs.models import Job
from app.main import app


class QuestionProvider:
    model = "synthetic-question-model"

    def generate(self, context, *, cancelled=None):
        value = {
            "schema_version": "1.0",
            "target_slot": "entry_intent",
            "acknowledgement": "배우고 싶은 방향부터 골라주세요.",
            "question": "어떤 방향으로 시작하고 싶나요?",
            "helper_text": "가까운 것을 골라주세요.",
            "selection_mode": "single",
            "options": [
                {
                    "key": "explore",
                    "label": "관심 분야 찾기",
                    "description": "탐색부터 시작",
                    "answer_patch": {"entry_intent": "explore"},
                },
                {
                    "key": "advance",
                    "label": "경험 발전시키기",
                    "description": "경험을 바탕으로",
                    "answer_patch": {"entry_intent": "advance"},
                },
            ],
        }
        return validate_question(
            json.dumps(value),
            eligible_slots=set(context["eligible_slots"]),
            companion_slots={
                k: set(v) for k, v in context["allowed_companion_slots"].items()
            },
            confirmed_slots=set(context["confirmed_answers"]),
            resolved_unknown_slots=set(context["resolved_unknown_slots"]),
        )


@unittest.skipUnless(
    os.environ.get("L0_DATABASE_TESTS") == "1", "requires local PostgreSQL setup"
)
class QuestionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.status.in_(["queued", "running"]))
                ),
                0,
                "worker queue must be idle for integration tests",
            )
        self.fixture = profiles.ProfileDraftTests()
        self.fixture.engine = self.engine
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client, self.csrf = self.fixture.client, self.fixture.csrf
        self.profile = self.fixture.create()
        env = patch.dict(
            os.environ,
            {
                "QUESTION_MODEL_BASE_URL": "http://127.0.0.1:18080",
                "QUESTION_MODEL_ID": QuestionProvider.model,
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def request(
        self, key="synthetic-generation-key", expected=0, client=None, csrf=None
    ):
        return (client or self.client).post(
            "/api/profiles/" + self.profile["id"] + "/questions",
            headers={"X-CSRF-Token": csrf or self.csrf, "Idempotency-Key": key},
            json={"expected_revision": expected},
        )

    def run_worker(self, provider=None, factory=None):
        from app.intake.worker import run_once

        return run_once(
            factory or (lambda: Session(self.engine)),
            provider or QuestionProvider(),
            lease_seconds=120,
            max_attempts=3,
        )

    def ready(self):
        accepted = self.request()
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertTrue(self.run_worker())
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "succeeded", job)
        question = self.client.get(
            "/api/questions/" + job["result"]["question_id"]
        ).json()
        return accepted.json(), question

    def answer(
        self,
        question,
        option_id,
        key="synthetic-answer-key",
        expected=0,
        client=None,
        csrf=None,
    ):
        return (client or self.client).post(
            "/api/profiles/"
            + self.profile["id"]
            + "/questions/"
            + question["id"]
            + "/answer",
            headers={"X-CSRF-Token": csrf or self.csrf, "Idempotency-Key": key},
            json={"expected_revision": expected, "selected_option_id": option_id},
        )

    def test_queue_worker_saved_question_answer_and_original_response_replay(self):
        accepted, question = self.ready()
        self.assertEqual(question["status"], "ready")
        self.assertEqual(len(question["options"]), 3)
        self.assertNotIn("answer_patch", json.dumps(question))
        self.assertNotIn("option_bindings", json.dumps(question))
        selected = question["options"][0]
        answered = self.answer(question, selected["id"])
        self.assertEqual(answered.status_code, 200, answered.text)
        saved = answered.json()
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["answers"], {"entry_intent": "explore"})
        self.assertEqual(saved["slot_origins"]["entry_intent"], "selected_option")
        self.assertEqual(saved["selection_summaries"][0]["label"], selected["label"])
        self.assertIsNone(saved["intake_state"]["active_question_id"])
        self.assertEqual(self.answer(question, selected["id"]).json(), saved)
        self.assertEqual(
            self.answer(question, question["options"][1]["id"]).status_code, 409
        )
        self.assertEqual(
            self.answer(question, selected["id"], key="another-answer-key").status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/api/questions/" + question["id"]).json()["status"],
            "answered",
        )

    def test_generated_context_and_active_question_are_reused_across_keys(self):
        first = self.request()
        second = self.request(key="another-key")
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertTrue(self.run_worker())
        again = self.request(key="ready-key")
        self.assertEqual(first.json()["job_id"], again.json()["job_id"])
        self.assertFalse(self.run_worker())
        profile = self.client.get("/api/profiles/" + self.profile["id"]).json()
        self.assertIsNotNone(profile["intake_state"]["active_question_id"])

    def test_original_answer_replay_after_later_edit_does_not_overwrite_it(self):
        _, question = self.ready()
        option = question["options"][0]["id"]
        first = self.answer(question, option).json()
        changed = self.fixture.save(first, {"entry_intent": "advance"}).json()
        self.assertEqual(self.answer(question, option).json(), first)
        self.assertEqual(
            self.client.get("/api/profiles/" + self.profile["id"]).json(), changed
        )

    def test_switching_back_to_same_context_reuses_saved_question(self):
        accepted, question = self.ready()
        with patch.dict(os.environ, {"QUESTION_MODEL_ID": "alternate-model"}):
            different = self.request(key="alternate-config")
            self.assertEqual(different.status_code, 202)
        restored = self.request(key="restored-config")
        self.assertEqual(restored.json()["job_id"], accepted["job_id"])
        self.assertEqual(
            self.client.get("/api/questions/" + question["id"]).json()["status"],
            "ready",
        )
        self.assertFalse(self.run_worker())

    def test_direct_edit_does_not_resurrect_an_older_selection_label(self):
        _, question = self.ready()
        first = self.answer(question, question["options"][0]["id"]).json()
        reopened = self.fixture.save(first, {}).json()
        accepted = self.request(
            key="regenerate-after-edit", expected=reopened["revision"]
        )
        self.assertEqual(accepted.status_code, 202)
        self.run_worker()
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        new_question = self.client.get(
            "/api/questions/" + job["result"]["question_id"]
        ).json()
        latest = self.answer(
            new_question,
            new_question["options"][1]["id"],
            key="new-selection",
            expected=reopened["revision"],
        ).json()
        edited = self.fixture.save(latest, {"entry_intent": "explore"}).json()
        self.assertEqual(edited["selection_summaries"], [])

    def test_unknown_budget_clears_amount_requirement_without_model_changing_parent(
        self,
    ):
        answers = {
            "entry_intent": "explore",
            "field_code": "ai_data",
            "experience": "unknown",
            "learning_goal": "unknown",
            "availability": "unknown",
            "delivery_mode": "online",
            "budget_status": "amount",
            "start_timing": "browse",
            "document_decision": "none",
        }
        self.fixture.save(self.profile, answers)
        accepted = self.request(expected=1)
        self.assertEqual(accepted.status_code, 202, accepted.text)

        class BudgetProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                payload = {
                    "schema_version": "1.0",
                    "target_slot": "budget_krw",
                    "acknowledgement": "금액 범위를 골라주세요.",
                    "question": "생각한 금액은 어느 정도인가요?",
                    "helper_text": "가까운 값을 골라주세요.",
                    "selection_mode": "single",
                    "options": [
                        {
                            "key": "first",
                            "label": "1만원",
                            "description": "최대 1만원",
                            "answer_patch": {"budget_krw": 10000},
                        },
                        {
                            "key": "second",
                            "label": "2만원",
                            "description": "최대 2만원",
                            "answer_patch": {"budget_krw": 20000},
                        },
                    ],
                }
                return validate_question(
                    json.dumps(payload),
                    eligible_slots=set(context["eligible_slots"]),
                    companion_slots={},
                    confirmed_slots=set(context["confirmed_answers"]),
                    resolved_unknown_slots=set(context["resolved_unknown_slots"]),
                )

        self.run_worker(BudgetProvider())
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "succeeded", job)
        question = self.client.get(
            "/api/questions/" + job["result"]["question_id"]
        ).json()
        selected = next(
            option for option in question["options"] if option["kind"] == "unknown"
        )
        saved = self.answer(question, selected["id"], expected=1)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["answers"]["budget_status"], "unknown")
        self.assertIsNone(saved.json()["answers"]["budget_krw"])
        self.assertEqual(saved.json()["intake_state"]["missing_slots"], [])

    def test_concurrent_generation_has_one_job(self):
        barrier = Barrier(2)

        def generate(key):
            with TestClient(app, base_url=profiles.ORIGIN) as client:
                client.cookies.set("today_tommorrow_session", self.fixture.token)
                barrier.wait(timeout=5)
                return self.request(key=key, client=client)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(generate, ["parallel-one", "parallel-two"]))
        self.assertEqual([r.status_code for r in responses], [202, 202])
        self.assertEqual(responses[0].json()["job_id"], responses[1].json()["job_id"])

    def test_wrong_owner_csrf_invalid_option_and_revision_are_rejected(self):
        _, question = self.ready()
        other, csrf, _, _ = self.fixture.login()
        self.assertEqual(other.get("/api/questions/" + question["id"]).status_code, 404)
        self.assertEqual(
            self.answer(
                question, question["options"][0]["id"], client=other, csrf=csrf
            ).status_code,
            404,
        )
        self.assertEqual(self.request(key="bad-csrf", csrf="wrong").status_code, 403)
        self.assertEqual(self.answer(question, str(uuid4())).status_code, 422)
        self.assertEqual(
            self.answer(
                question, question["options"][0]["id"], key="stale-answer", expected=1
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/api/profiles/" + self.profile["id"]).json()["revision"], 0
        )

    def test_profile_edit_supersedes_pending_job_and_stales_ready_question(self):
        accepted = self.request()
        self.assertEqual(accepted.status_code, 202, accepted.text)
        saved = self.fixture.save(self.profile, {"entry_intent": "advance"})
        self.assertEqual(saved.status_code, 200)
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "superseded")
        self.assertFalse(self.run_worker())

    def test_edit_during_model_io_drops_old_result_and_keeps_no_transaction(self):
        accepted = self.request()
        self.assertEqual(accepted.status_code, 202, accepted.text)
        sessions = []

        def factory():
            session = Session(self.engine)
            sessions.append(session)
            return session

        outer = self

        class EditingProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                outer.assertTrue(all(not s.in_transaction() for s in sessions))
                outer.assertEqual(
                    outer.fixture.save(
                        outer.profile, {"entry_intent": "advance"}
                    ).status_code,
                    200,
                )
                return super().generate(context, cancelled=cancelled)

        self.assertTrue(self.run_worker(EditingProvider(), factory))
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "superseded")
        self.assertIsNone(job["result"])

    def test_cancel_and_provider_failure_do_not_become_successful_questions(self):
        accepted = self.request()
        self.assertEqual(accepted.status_code, 202)
        cancelled = self.client.post(
            "/api/jobs/" + accepted.json()["job_id"] + "/cancel",
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(self.run_worker())
        retry = self.request(key="retry-failed-key")
        self.assertEqual(retry.status_code, 202)

        class FailingProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                raise QuestionError("synthetic-private-message")

        self.run_worker(FailingProvider())
        job = self.client.get("/api/jobs/" + retry.json()["job_id"])
        self.assertEqual(job.json()["status"], "failed")
        self.assertNotIn("synthetic-private", job.text)
        self.assertIsNone(job.json()["result"])

    def test_whitespace_only_required_text_can_be_generated_and_answered(self):
        class TextProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                self.context = context
                target = context["eligible_slots"][0]
                return validate_question(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "target_slot": target,
                            "acknowledgement": "필요한 내용을 골라주세요.",
                            "question": "어떤 내용으로 답하시겠어요?",
                            "helper_text": "가까운 것을 선택해주세요.",
                            "selection_mode": "single",
                            "options": [
                                {
                                    "key": "first",
                                    "label": "첫 번째 응답",
                                    "description": "첫 번째 합성 응답",
                                    "answer_patch": {target: "첫 번째 합성 응답"},
                                },
                                {
                                    "key": "second",
                                    "label": "두 번째 응답",
                                    "description": "두 번째 합성 응답",
                                    "answer_patch": {target: "두 번째 합성 응답"},
                                },
                            ],
                        }
                    ),
                    eligible_slots=set(context["eligible_slots"]),
                    companion_slots={},
                    confirmed_slots=set(context["confirmed_answers"]),
                    resolved_unknown_slots=set(context["resolved_unknown_slots"]),
                )

        base = {
            "entry_intent": "explore",
            "field_code": "ai_data",
            "experience": "beginner",
            "learning_goal": "foundation",
            "availability": "unknown",
            "delivery_mode": "online",
            "budget_status": "no_limit",
            "start_timing": "browse",
            "document_decision": "none",
        }
        for target, changes in (
            ("custom_field", {"field_code": "other", "custom_field": "\t "}),
            ("goal_text", {"entry_intent": "goal_known", "goal_text": " \n "}),
            (
                "commute_region",
                {"delivery_mode": "offline", "commute_region": "\u3000"},
            ),
        ):
            with self.subTest(target=target):
                self.profile = self.fixture.create()
                response = self.fixture.save(self.profile, {**base, **changes})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["answers"][target], changes[target])
                self.assertEqual(
                    response.json()["intake_state"]["eligible_slots"], [target]
                )
                accepted = self.request(key="whitespace-" + target, expected=1)
                self.assertEqual(accepted.status_code, 202, accepted.text)
                provider = TextProvider()
                self.assertTrue(self.run_worker(provider))
                self.assertNotIn(target, provider.context["confirmed_answers"])
                job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
                self.assertEqual(job["status"], "succeeded", job)
                question = self.client.get(
                    "/api/questions/" + job["result"]["question_id"]
                ).json()
                answered = self.answer(
                    question,
                    question["options"][0]["id"],
                    key="whitespace-answer-" + target,
                    expected=1,
                )
                self.assertEqual(answered.status_code, 200, answered.text)
                saved = answered.json()
                self.assertEqual(saved["revision"], 2)
                self.assertEqual(saved["answers"][target], "첫 번째 합성 응답")
                self.assertEqual(saved["intake_state"]["next_action"], "review")
                self.assertEqual(
                    self.client.get("/api/profiles/" + self.profile["id"]).json(), saved
                )

    def test_provider_configuration_is_required_without_test_fallback(self):
        with patch.dict(
            os.environ, {"QUESTION_MODEL_BASE_URL": "", "QUESTION_MODEL_ID": ""}
        ):
            self.assertEqual(self.request().status_code, 503)
            self.assertFalse(
                self.client.get("/api/onboarding/options").json()[
                    "question_generation_enabled"
                ]
            )

    def test_production_gemma_adapter_with_a_local_http_test_server(self):
        from app.intake.question_config import QuestionSettings

        calls = []
        context = {
            "eligible_slots": ["entry_intent"],
            "allowed_companion_slots": {},
            "confirmed_answers": {},
            "resolved_unknown_slots": [],
        }
        draft = QuestionProvider().generate(context).model_dump_json()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                body = json.dumps(
                    {
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": draft}}
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(
                os.environ,
                {"QUESTION_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}"},
            ):
                accepted = self.request()
                self.assertEqual(accepted.status_code, 202)
                self.assertTrue(self.run_worker(QuestionSettings.load().provider()))
                job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
                self.assertEqual(job["status"], "succeeded", job)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["response_format"]["type"], "json_schema")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_worker_heartbeats_while_waiting_without_holding_result_transaction(self):
        from app.jobs import store as jobs

        heartbeat_seen = Event()
        original = jobs.heartbeat

        def heartbeat(*args, **kwargs):
            result = original(*args, **kwargs)
            if result:
                heartbeat_seen.set()
            return result

        outer = self

        class WaitingProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                outer.assertTrue(heartbeat_seen.wait(timeout=10))
                return super().generate(context, cancelled=cancelled)

        accepted = self.request()
        self.assertEqual(accepted.status_code, 202)
        with patch("app.intake.worker.jobs.heartbeat", side_effect=heartbeat):
            from app.intake.worker import run_once

            self.assertTrue(
                run_once(
                    lambda: Session(self.engine), WaitingProvider(), lease_seconds=6
                )
            )
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "succeeded", job)

    def test_running_cancellation_discards_the_model_result(self):
        accepted = self.request()
        self.assertEqual(accepted.status_code, 202)
        outer = self

        class CancellingProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                response = outer.client.post(
                    "/api/jobs/" + accepted.json()["job_id"] + "/cancel",
                    headers={"X-CSRF-Token": outer.csrf},
                )
                outer.assertEqual(response.status_code, 200)
                return super().generate(context, cancelled=cancelled)

        self.run_worker(CancellingProvider())
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNone(job["result"])
        self.assertIsNone(
            self.client.get("/api/profiles/" + self.profile["id"]).json()[
                "intake_state"
            ]["active_question_id"]
        )

    def test_worker_rejects_an_option_that_full_answer_schema_cannot_apply(self):
        self.fixture.save(self.profile, {"entry_intent": "explore"})
        accepted = self.request(expected=1)
        self.assertEqual(accepted.status_code, 202)

        class InvalidCompanionProvider(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                payload = {
                    "schema_version": "1.0",
                    "target_slot": "field_code",
                    "acknowledgement": "관심 분야를 알려주세요.",
                    "question": "어떤 분야인가요?",
                    "helper_text": "가까운 분야를 골라주세요.",
                    "selection_mode": "single",
                    "options": [
                        {
                            "key": "other",
                            "label": "다른 분야",
                            "description": "별도 분야",
                            "answer_patch": {
                                "field_code": "other",
                                "custom_field": "s" * 2001,
                            },
                        },
                        {
                            "key": "data",
                            "label": "데이터",
                            "description": "데이터 분야",
                            "answer_patch": {"field_code": "ai_data"},
                        },
                    ],
                }
                return validate_question(
                    json.dumps(payload),
                    eligible_slots=set(context["eligible_slots"]),
                    companion_slots={
                        k: set(v) for k, v in context["allowed_companion_slots"].items()
                    },
                    confirmed_slots=set(context["confirmed_answers"]),
                    resolved_unknown_slots=set(context["resolved_unknown_slots"]),
                )

        self.run_worker(InvalidCompanionProvider())
        job = self.client.get("/api/jobs/" + accepted.json()["job_id"]).json()
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["result"])

    def test_stale_ready_question_is_not_answered_and_unknown_is_explicit(self):
        _, question = self.ready()
        option = next(item for item in question["options"] if item["kind"] == "unknown")
        answered = self.answer(question, option["id"])
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(
            answered.json()["slot_origins"]["entry_intent"], "explicit_unknown"
        )
        edited = self.fixture.save(answered.json(), {})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["selection_summaries"], [])
