"""Real PostgreSQL lifecycle tests; providers are explicitly injected synthetic runners."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from unittest.mock import patch
from uuid import UUID, uuid4

import test_profile_drafts as profiles
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.jobs import store as jobs
from app.jobs.models import Job
from app.main import app
from app.recommendations.models import RecommendationRun
from app.recommendations.worker import run_once

ANSWERS = {
    "entry_intent": "explore",
    "field_code": "software_cloud",
    "experience": "beginner",
    "learning_goal": "foundation",
    "availability": "flexible",
    "delivery_mode": "online",
    "budget_status": "unknown",
    "start_timing": "browse",
    "document_decision": "none",
    "goal_text": "리눅스 서버 운영 기초",
}


class SyntheticRunner:
    def __init__(self, action=None):
        self.action = action
        self.snapshots = []
        self.context_calls = 0

    def capture_context(self):
        self.context_calls += 1
        return {
            "dataset_id": "synthetic-pinned-dataset",
            "pipeline_version": "synthetic-v1",
        }

    def run(self, snapshot, report_phase):
        self.snapshots.append(deepcopy(snapshot))
        report_phase("retrieving")
        if self.action:
            self.action(snapshot, report_phase)
        return {
            "catalog_version_id": "63478e61-e4c7-43e5-b5e2-6482e591c80a",
            "result_state": "empty",
            "explanation_mode": "none",
            "items": [],
            "notices": [],
            "excluded_counts": {},
            "model_id": "synthetic-model",
            "runtime_mode": "sample",
            "model_outcome": "not_needed",
            "pipeline_version": "synthetic-v1",
            "ranking_config_version": "synthetic-v1",
            "retrieval_config_version": "synthetic-v1",
            "as_of": snapshot["as_of"],
            "needs": [],
            "stages": [
                {
                    "stage": "retrieving",
                    "status": "succeeded",
                    "input_count": 0,
                    "output_count": 0,
                    "reason_counts": {},
                    "duration_ms": None,
                }
            ],
            "_snapshot": {"evidence": "synthetic-internal-only"},
        }


class AnonymousRecommendationTests(unittest.TestCase):
    def test_submission_and_result_require_authentication(self):
        with TestClient(app) as client:
            self.assertEqual(
                client.post(f"/api/profiles/{uuid4()}/submit").status_code, 401
            )
            self.assertEqual(
                client.get(f"/api/recommendations/{uuid4()}").status_code, 401
            )
            self.assertEqual(
                client.get(f"/api/profiles/{uuid4()}/recommendation-job").status_code,
                401,
            )

    def test_invalid_job_kinds_are_rejected_before_sql(self):
        with Session() as db:
            with self.assertRaises(ValueError):
                jobs.claim(
                    db,
                    now=datetime.now(UTC),
                    lease_seconds=5,
                    kind="document_processing",
                )


@unittest.skipUnless(
    os.environ.get("L0_DATABASE_TESTS") == "1", "requires disposable local PostgreSQL"
)
class RecommendationFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        self.fixture = profiles.ProfileDraftTests()
        self.fixture.engine = self.engine
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.csrf = self.fixture.client, self.fixture.csrf
        self.profile = self.fixture.create()
        saved = self.fixture.save(self.profile, ANSWERS)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.profile = saved.json()
        self.runner = SyntheticRunner()
        configured = patch(
            "app.recommendations.routes.build_runner", return_value=self.runner
        )
        configured.start()
        self.addCleanup(configured.stop)

    def submit(self, key="synthetic-submit", profile=None, client=None, csrf=None):
        current = profile or self.profile
        return (client or self.client).post(
            f"/api/profiles/{current['id']}/submit",
            headers={"X-CSRF-Token": csrf or self.csrf, "Idempotency-Key": key},
            json={
                "expected_revision": current["revision"],
                "review_token": current["review_token"],
            },
        )

    def work(self, runner=None):
        return run_once(lambda: Session(self.engine), runner or self.runner)

    def job(self, response):
        return self.client.get("/api/jobs/" + response.json()["job_id"]).json()

    def count_results(self):
        with Session(self.engine) as db:
            return db.scalar(
                select(func.count())
                .select_from(RecommendationRun)
                .where(RecommendationRun.user_id == self.fixture.user_id)
            )

    def test_submit_worker_persist_reload_and_private_snapshot(self):
        self.assertTrue(self.profile["review_token"].startswith("v1."))
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(self.work())
        job = self.job(response)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["kind"], "recommendation")
        self.assertEqual(job["completed_stages"], ["retrieving"])
        url = "/api/recommendations/" + job["result"]["recommendation_run_id"]
        saved = self.client.get(url)
        self.assertEqual(saved.status_code, 200, saved.text)
        public = saved.json()
        self.assertEqual(public["profile_snapshot"], ANSWERS)
        self.assertEqual(public["profile_revision"], self.profile["revision"])
        self.assertEqual(public["runtime_mode"], "sample")
        self.assertNotIn("_snapshot", public)
        self.assertNotIn("user_id", public)
        self.assertEqual(saved.headers["cache-control"], "no-store")
        with Session(self.engine) as db:
            row = db.get(RecommendationRun, UUID(public["id"]))
            self.assertEqual(
                row.provenance_json, {"evidence": "synthetic-internal-only"}
            )
            self.assertEqual(row.input_json["answers"], ANSWERS)
            self.assertEqual(
                row.input_json["context"]["dataset_id"], "synthetic-pinned-dataset"
            )
        changed = self.fixture.save(
            self.profile, {**ANSWERS, "goal_text": "새로운 목표"}
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get(url).json(), public)

    def test_current_recommendation_job_owner_revision_and_newest(self):
        path = f"/api/profiles/{self.profile['id']}/recommendation-job"
        self.assertEqual(
            self.client.get(path).json(),
            {
                "profile_id": self.profile["id"],
                "revision": self.profile["revision"],
                "job": None,
            },
        )
        other, _, _, _ = self.fixture.login()
        self.assertEqual(other.get(path).status_code, 404)
        first = self.submit().json()["job_id"]
        with Session(self.engine) as db, db.begin():
            row = db.get(Job, UUID(first))
            row.status = "failed"
            row.phase = "failed"
            row.error_code = "recommendation_failed"
        second = self.submit("second-recovery-job").json()["job_id"]
        with Session(self.engine) as db, db.begin():
            one, two = db.get(Job, UUID(first)), db.get(Job, UUID(second))
            two.created_at = one.created_at
        response = self.client.get(path)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["job"]["id"], max(first, second))
        self.assertEqual(
            response.json()["job"]["profile_revision"], self.profile["revision"]
        )
        self.assertEqual(
            self.client.get(f"/api/jobs/{second}").json()["profile_revision"],
            self.profile["revision"],
        )
        changed = self.fixture.save(
            self.profile, {**ANSWERS, "goal_text": "changed"}
        ).json()
        latest = self.client.get(path).json()
        self.assertEqual(latest["revision"], changed["revision"])
        self.assertIsNone(latest["job"])

    def test_original_accepted_replay_and_new_key_deduplicate(self):
        first = self.submit()
        self.assertEqual(self.submit().json(), first.json())
        second = self.submit("new-submit-key")
        self.assertEqual(second.json()["job_id"], first.json()["job_id"])
        self.assertEqual(self.runner.context_calls, 1)
        self.assertTrue(self.work())
        self.assertEqual(
            self.submit("finished-key").json()["job_id"], first.json()["job_id"]
        )
        changed = self.fixture.save(self.profile, {**ANSWERS, "goal_text": "새 목표"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.submit().json(), first.json())
        self.assertEqual(self.submit("stale-key").status_code, 409)
        self.assertEqual(self.count_results(), 1)

    def test_same_key_different_request_is_conflict(self):
        self.submit()
        changed = self.fixture.save(
            self.profile, {**ANSWERS, "goal_text": "다른 목표"}
        ).json()
        conflict = self.submit(profile=changed)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "idempotency_conflict")

    def test_parallel_keys_enqueue_one_snapshot(self):
        barrier = Barrier(2)

        def request(key):
            barrier.wait(timeout=5)
            return self.submit(key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(request, ("parallel-one", "parallel-two")))
        self.assertEqual((first.status_code, second.status_code), (202, 202))
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(self.runner.context_calls, 1)

    def test_invalid_token_incomplete_and_material_path_cannot_submit(self):
        invalid = {**self.profile, "review_token": "v1." + "0" * 64}
        self.assertEqual(
            self.submit(profile=invalid).json()["code"], "review_token_invalid"
        )
        for answers in (
            {"field_code": "undecided"},
            {**ANSWERS, "document_decision": "provided"},
        ):
            current = self.client.get("/api/profiles/" + self.profile["id"]).json()
            saved = self.fixture.save(current, answers).json()
            self.assertIsNone(saved["review_token"])
            saved["review_token"] = invalid["review_token"]
            response = self.submit("incomplete-" + str(saved["revision"]), saved)
            expected_status = (
                409 if answers.get("document_decision") == "provided" else 422
            )
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response.json()["code"], "profile_not_ready")
        self.assertEqual(self.runner.context_calls, 0)

    def test_current_revision_does_not_accept_prior_snapshot_token(self):
        prior = self.profile["review_token"]
        changed = self.fixture.save(
            self.profile, {**ANSWERS, "goal_text": "바뀐 목표"}
        ).json()
        self.assertNotEqual(changed["review_token"], prior)
        response = self.submit(profile={**changed, "review_token": prior})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "review_token_invalid")

    def test_owner_csrf_payload_and_unavailable_configuration(self):
        other, csrf, _, _ = self.fixture.login()
        self.assertEqual(self.submit(client=other, csrf=csrf).status_code, 404)
        self.assertEqual(self.submit(csrf="invalid").status_code, 403)
        malformed = self.client.post(
            f"/api/profiles/{self.profile['id']}/submit",
            headers={"X-CSRF-Token": self.csrf, "Idempotency-Key": "invalid"},
            json={"expected_revision": True, "review_token": "x" * 9000},
        )
        self.assertEqual(malformed.status_code, 422)
        with patch(
            "app.recommendations.routes.build_runner",
            side_effect=ValueError("secret-details"),
        ):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-details", response.text)
        accepted = self.submit()
        self.assertEqual(accepted.status_code, 202)
        self.work()
        run = self.job(accepted)["result"]["recommendation_run_id"]
        self.assertEqual(other.get("/api/recommendations/" + run).status_code, 404)
        self.assertEqual(
            self.client.get("/api/recommendations/not-a-uuid").status_code, 404
        )

    def test_cancel_before_claim_and_new_key_retry(self):
        accepted = self.submit()
        cancelled = self.client.post(
            "/api/jobs/" + accepted.json()["job_id"] + "/cancel",
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertFalse(self.work())
        current = self.client.get("/api/profiles/" + self.profile["id"]).json()
        self.assertEqual(current["status"], "submitted")
        self.assertIsNotNone(current["review_token"])
        retry = self.submit("retry-after-cancel", current)
        self.assertNotEqual(accepted.json()["job_id"], retry.json()["job_id"])
        self.assertTrue(self.work())
        self.assertEqual(self.job(retry)["status"], "succeeded")

    def test_cancel_during_provider_discards_result(self):
        accepted = self.submit()
        self.assertEqual(accepted.status_code, 202, accepted.text)
        callback_called = Event()

        def cancel(snapshot, phase):
            callback_called.set()
            response = self.client.post(
                "/api/jobs/" + accepted.json()["job_id"] + "/cancel",
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 200)

        self.assertTrue(
            self.work(SyntheticRunner(cancel)),
            "worker must claim the test job; execution-slot or queue contention is not cancellation",
        )
        self.assertTrue(
            callback_called.is_set(), "provider must reach cancellation callback"
        )
        self.assertEqual(self.job(accepted)["status"], "cancelled")
        self.assertEqual(self.count_results(), 0)

    def test_edit_during_provider_supersedes_result(self):
        accepted = self.submit()

        def edit(snapshot, phase):
            self.assertEqual(
                self.fixture.save(
                    self.profile, {**ANSWERS, "goal_text": "다른 내용"}
                ).status_code,
                200,
            )

        self.work(SyntheticRunner(edit))
        self.assertEqual(self.job(accepted)["status"], "superseded")
        self.assertEqual(self.count_results(), 0)

    def test_provider_failure_is_safe_and_retry_creates_new_job(self):
        accepted = self.submit()

        def fail(snapshot, phase):
            raise ValueError("synthetic private input must not escape")

        self.work(SyntheticRunner(fail))
        failed = self.job(accepted)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["code"], "recommendation_failed")
        self.assertNotIn("private input", str(failed))
        self.assertEqual(self.count_results(), 0)
        retry = self.submit("retry-failure")
        self.assertNotEqual(retry.json()["job_id"], accepted.json()["job_id"])
        self.assertTrue(self.work())
        self.assertEqual(self.count_results(), 1)

    def test_invalid_provider_output_cannot_be_persisted(self):
        accepted = self.submit()
        with patch.object(self.runner, "run", return_value={"result_state": "matches"}):
            self.work()
        self.assertEqual(self.job(accepted)["status"], "failed")
        self.assertEqual(self.count_results(), 0)

    def test_failed_completion_rolls_back_result_insert(self):
        accepted = self.submit()
        with patch("app.recommendations.worker.jobs.complete", return_value=False):
            self.work()
        self.assertEqual(self.count_results(), 0)
        self.assertEqual(self.job(accepted)["status"], "running")

    def test_old_worker_cannot_commit_after_reclaim(self):
        accepted = self.submit()

        def reclaim(snapshot, phase):
            with Session(self.engine) as db, db.begin():
                row = db.get(Job, UUID(accepted.json()["job_id"]))
                row.lease_token = uuid4()
                row.attempt += 1

        self.work(SyntheticRunner(reclaim))
        self.assertEqual(self.count_results(), 0)
        self.assertEqual(self.job(accepted)["status"], "running")

    def test_worker_kind_isolation_preserves_one_global_active_lease(self):
        recommendation = self.submit()
        with Session(self.engine) as db, db.begin():
            question = jobs.enqueue(
                db,
                profile_id=UUID(self.profile["id"]),
                user_id=self.fixture.user_id,
                profile_revision=self.profile["revision"],
                idempotency_key="question-kind-key",
                request_hash="synthetic",
                input_json={},
                now=datetime.now(UTC),
            )
            question_id = question.id
        with Session(self.engine) as db, db.begin():
            claim = jobs.claim(db, now=datetime.now(UTC), lease_seconds=60)
            self.assertEqual(claim.id, question_id)
        self.assertFalse(self.work())
        with Session(self.engine) as db, db.begin():
            self.assertTrue(
                jobs.complete(
                    db,
                    claim,
                    now=datetime.now(UTC),
                    result_json={"question_id": str(uuid4())},
                )
            )
        self.assertTrue(self.work())
        self.assertEqual(self.job(recommendation)["status"], "succeeded")
        with Session(self.engine) as db, db.begin():
            self.assertIsNone(jobs.claim(db, now=datetime.now(UTC), lease_seconds=60))

    def test_heartbeat_and_execution_slot_survive_blocked_call_and_cancel(self):
        from test_question_flow import QuestionProvider

        from app.intake.worker import run_once as question_worker

        accepted = self.submit()
        entered, release, renewed = Event(), Event(), Event()
        original_heartbeat = jobs.heartbeat

        def heartbeat(*args, **kwargs):
            result = original_heartbeat(*args, **kwargs)
            if result:
                renewed.set()
            return result

        def blocked(snapshot, phase):
            entered.set()
            if not release.wait(timeout=8):
                raise TimeoutError("synthetic blocked provider")

        with patch("app.recommendations.worker.jobs.heartbeat", side_effect=heartbeat):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    run_once,
                    lambda: Session(self.engine),
                    SyntheticRunner(blocked),
                    lease_seconds=1,
                )
                try:
                    self.assertTrue(entered.wait(timeout=3))
                    self.assertTrue(renewed.wait(timeout=3))
                    Event().wait(1.2)
                    with Session(self.engine) as db:
                        row = db.get(Job, UUID(accepted.json()["job_id"]))
                        self.assertGreater(row.lease_expires_at, datetime.now(UTC))
                        guards = db.scalar(
                            text(
                                "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND l.classid=741201 AND l.objid=2 AND l.granted AND a.xact_start IS NULL"
                            )
                        )
                        self.assertEqual(guards, 1)
                    cancelled = self.client.post(
                        "/api/jobs/" + accepted.json()["job_id"] + "/cancel",
                        headers={"X-CSRF-Token": self.csrf},
                    )
                    self.assertEqual(cancelled.json()["status"], "cancelled")
                    retry = self.submit("blocked-retry")
                    self.assertFalse(self.work())
                    self.assertFalse(
                        question_worker(
                            lambda: Session(self.engine), QuestionProvider()
                        )
                    )
                finally:
                    release.set()
                self.assertTrue(future.result(timeout=5))
        self.assertEqual(self.count_results(), 0)
        self.assertTrue(self.work())
        self.assertEqual(self.job(retry)["status"], "succeeded")

    def test_expired_recommendation_claim_is_recovered(self):
        accepted = self.submit()
        with Session(self.engine) as db, db.begin():
            claim = jobs.claim(
                db, now=datetime.now(UTC), lease_seconds=10, kind="recommendation"
            )
            db.get(Job, claim.id).lease_expires_at = datetime.now(UTC) - timedelta(
                seconds=1
            )
        self.assertTrue(self.work())
        self.assertEqual(self.job(accepted)["status"], "succeeded")
        self.assertEqual(self.count_results(), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.get(Job, claim.id).attempt, 2)


if __name__ == "__main__":
    unittest.main()
