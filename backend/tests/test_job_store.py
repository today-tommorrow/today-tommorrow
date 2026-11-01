"""Job queue fencing and ownership contracts against real local PostgreSQL."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Barrier, Event
from time import monotonic
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.auth.models import User
from app.auth.routes import AuthError, auth_error_handler
from app.auth.store import AuthStore
from app.db import get_session
from app.intake.store import create_profile
from app.jobs import store
from app.jobs.models import Job
from app.jobs.routes import JobHTTPError, job_error_handler, router

NOW = datetime(2026, 11, 1, 6, tzinfo=UTC)
ORIGIN = "http://127.0.0.1:5173"
KEY = "synthetic-job-key-at-least-32-bytes"


def jobs_app():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)
    app.add_exception_handler(JobHTTPError, job_error_handler)
    return app


class AnonymousJobTests(unittest.TestCase):
    def test_read_and_cancel_require_authentication(self):
        with TestClient(jobs_app()) as client:
            for method, path in (
                ("GET", "/api/jobs/" + str(uuid4())),
                ("POST", "/api/jobs/" + str(uuid4()) + "/cancel"),
            ):
                self.assertEqual(client.request(method, path).status_code, 401)

    def test_store_rejects_invalid_clock_and_execution_budgets_before_sql(self):
        with Session() as db:
            invalid_values: tuple[Any, ...] = (
                0,
                -1,
                True,
                1.5,
                float("inf"),
                float("nan"),
            )
            for invalid in invalid_values:
                with self.subTest(budget=invalid), self.assertRaises(ValueError):
                    store.claim(db, now=NOW, lease_seconds=invalid)
                with self.subTest(attempts=invalid), self.assertRaises(ValueError):
                    store.claim(db, now=NOW, lease_seconds=10, max_attempts=invalid)
            with self.assertRaises(ValueError):
                store.claim(db, now=NOW.replace(tzinfo=None), lease_seconds=10)

    def test_enqueue_rejects_invalid_key_revision_and_clock_before_sql(self):
        values: dict[str, Any] = {
            "profile_id": uuid4(),
            "user_id": uuid4(),
            "profile_revision": 0,
            "idempotency_key": "synthetic-key",
            "request_hash": "synthetic-hash",
            "input_json": {},
            "now": NOW,
        }
        with Session() as db:
            for key in ("", "x" * 129, "한글", None, 1):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    store.enqueue(db, **{**values, "idempotency_key": key})
            for revision in (-1, True, 1.5):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    store.enqueue(db, **{**values, "profile_revision": revision})
            with self.assertRaises(ValueError):
                store.enqueue(db, **{**values, "now": NOW.replace(tzinfo=None)})


@unittest.skipUnless(
    os.environ.get("L0_DATABASE_TESTS") == "1", "local PostgreSQL required"
)
class JobStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        self.user_ids = []
        self.addCleanup(self.clean)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)
        self.assertEqual(
            self.db.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.status.in_(("queued", "running")))
            ),
            0,
            "worker queue must be idle for integration tests",
        )
        self.db.rollback()
        self.user_id, self.profile_id = self.owner()

    def owner(self):
        with Session(self.engine) as db, db.begin():
            user = AuthStore(
                db, signing_key=b"synthetic-job-key-at-least-32-bytes"
            ).save_google_identity(
                subject="synthetic-job-" + uuid4().hex,
                email="synthetic@example.invalid",
                now=NOW,
            )
            self.user_ids.append(user.id)
            profile = create_profile(db, user.id)
            return user.id, profile.id

    def clean(self):
        with Session(self.engine) as db, db.begin():
            db.execute(delete(User).where(User.id.in_(self.user_ids)))

    def enqueue(self, *, key=None, request_hash="synthetic-hash", now=NOW):
        return store.enqueue(
            self.db,
            profile_id=self.profile_id,
            user_id=self.user_id,
            profile_revision=0,
            idempotency_key=key or uuid4().hex,
            request_hash=request_hash,
            input_json={"private": {"value": "synthetic-secret"}},
            now=now,
        )

    def claim(self, now=NOW, lease_seconds=10):
        return store.claim(self.db, now=now, lease_seconds=lease_seconds)

    def test_enqueue_replay_collision_ownership_and_public_nonleak(self):
        row = self.enqueue(key="synthetic-key")
        self.assertEqual(self.enqueue(key="synthetic-key").id, row.id)
        with self.assertRaises(store.JobConflict):
            self.enqueue(key="synthetic-key", request_hash="different")
        with self.assertRaises(store.JobNotFound):
            store.get_job(self.db, row.id, uuid4())
        self.assertEqual(
            store.accepted(row),
            {"job_id": str(row.id), "status": "queued", "poll_after_ms": 1000},
        )
        public = store.public_job(row)
        self.assertEqual(public["id"], str(row.id))
        self.assertEqual(public["result"], None)
        for secret in (
            "synthetic-secret",
            "synthetic-key",
            "synthetic-hash",
            "input_json",
            "lease_token",
            "user_id",
        ):
            self.assertNotIn(secret, json.dumps(public))

    def test_enqueue_race_replays_one_persisted_job(self):
        barrier = Barrier(2)

        def enqueue():
            with Session(self.engine) as db:
                barrier.wait(timeout=5)
                row = store.enqueue(
                    db,
                    profile_id=self.profile_id,
                    user_id=self.user_id,
                    profile_revision=0,
                    idempotency_key="race-key",
                    request_hash="same",
                    input_json={},
                    now=NOW,
                )
                value = row.id
                db.commit()
                return value

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: enqueue(), range(2)))
        self.assertEqual(ids[0], ids[1])

    def test_two_workers_share_one_global_active_lease(self):
        first = self.enqueue()
        self.enqueue(now=NOW + timedelta(seconds=1))
        first_id = first.id
        self.db.commit()
        barrier = Barrier(2)

        def claim():
            with Session(self.engine) as db:
                db.connection()
                barrier.wait(timeout=5)
                result = store.claim(db, now=NOW, lease_seconds=10)
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claim(), range(2)))
        winners = [value for value in claims if value is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].id, first_id)
        self.assertEqual(winners[0].attempt, 1)
        self.assertEqual(winners[0].input_json["private"]["value"], "synthetic-secret")
        winners[0].input_json["private"]["value"] = "changed"
        self.assertEqual(
            store.get_job(self.db, first_id, self.user_id).input_json["private"][
                "value"
            ],
            "synthetic-secret",
        )

    def test_expired_reclaim_fences_prior_attempt_and_only_exposes_result_id(self):
        row = self.enqueue()
        old = self.claim()
        assert old is not None
        self.assertIsNone(self.claim(now=NOW + timedelta(seconds=1)))
        self.assertFalse(
            store.complete(
                self.db, old, now=NOW + timedelta(seconds=10), result_json={}
            )
        )
        current = self.claim(now=NOW + timedelta(seconds=10))
        assert current is not None
        self.assertNotEqual(old.lease_token, current.lease_token)
        self.assertEqual(current.attempt, 2)
        self.assertFalse(
            store.heartbeat(
                self.db, old, now=NOW + timedelta(seconds=11), lease_seconds=10
            )
        )
        self.assertFalse(
            store.fail(
                self.db, old, now=NOW + timedelta(seconds=11), code="model_failed"
            )
        )
        self.assertFalse(
            store.complete(
                self.db, old, now=NOW + timedelta(seconds=11), result_json={}
            )
        )
        question_id = str(uuid4())
        self.assertTrue(
            store.complete(
                self.db,
                current,
                now=NOW + timedelta(seconds=11),
                result_json={"question_id": question_id, "private": "synthetic-secret"},
            )
        )
        self.assertEqual(store.public_job(row)["result"], {"question_id": question_id})
        self.assertEqual(row.phase, "completed")
        self.assertIsNone(row.lease_token)
        self.assertIsNone(row.lease_expires_at)

    def test_heartbeat_extends_lease_and_cancel_is_terminal(self):
        row = self.enqueue()
        current = self.claim()
        assert current is not None
        self.assertTrue(
            store.heartbeat(
                self.db, current, now=NOW + timedelta(seconds=9), lease_seconds=10
            )
        )
        self.assertIsNone(self.claim(now=NOW + timedelta(seconds=10)))
        with self.assertRaises(store.JobNotFound):
            store.cancel(self.db, row.id, uuid4(), now=NOW)
        self.assertEqual(
            store.cancel(self.db, row.id, self.user_id, now=NOW).status, "cancelled"
        )
        self.assertEqual(
            store.cancel(
                self.db, row.id, self.user_id, now=NOW + timedelta(seconds=1)
            ).finished_at,
            NOW,
        )
        self.assertFalse(store.complete(self.db, current, now=NOW, result_json={}))
        self.assertFalse(store.heartbeat(self.db, current, now=NOW, lease_seconds=10))

    def test_attempt_limit_fails_expired_job_and_advances_fifo(self):
        row = self.enqueue()
        next_row = self.enqueue(now=NOW + timedelta(seconds=1))
        for offset in (0, 10, 20):
            claimed = self.claim(now=NOW + timedelta(seconds=offset))
            assert claimed is not None
            self.assertEqual(claimed.id, row.id)
        next_claim = self.claim(now=NOW + timedelta(seconds=30))
        assert next_claim is not None
        self.assertEqual(next_claim.id, next_row.id)
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.error_code, "lease_exhausted")
        self.assertIsNone(row.lease_token)

    def test_claim_skips_locked_queue_rows_and_active_lease_spans_owners(self):
        first = self.enqueue()
        first_id = first.id
        other_id, profile_id = self.owner()
        second = store.enqueue(
            self.db,
            profile_id=profile_id,
            user_id=other_id,
            profile_revision=0,
            idempotency_key="other-owner",
            request_hash="other",
            input_json={},
            now=NOW + timedelta(seconds=1),
        )
        second_id = second.id
        self.db.commit()
        with Session(self.engine) as locked:
            locked.scalar(select(Job).where(Job.id == first_id).with_for_update())
            current = self.claim()
            assert current is not None
            self.assertEqual(current.id, second_id)
            self.assertEqual(current.user_id, other_id)
            self.db.commit()
        self.assertIsNone(self.claim())

    def test_uncommitted_heartbeat_cannot_open_a_second_active_lease(self):
        self.enqueue()
        self.enqueue(now=NOW + timedelta(seconds=1))
        current = self.claim()
        assert current is not None
        self.db.commit()
        renewed, release = Event(), Event()
        claim_pid: Queue[int] = Queue()

        def renew():
            with Session(self.engine) as db:
                result = store.heartbeat(
                    db, current, now=NOW + timedelta(seconds=9), lease_seconds=10
                )
                renewed.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("heartbeat transaction was not released")
                db.commit()
                return result

        def claim_after_original_expiry():
            with Session(self.engine) as db:
                pid = db.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                claim_pid.put(pid)
                result = store.claim(
                    db, now=NOW + timedelta(seconds=11), lease_seconds=10
                )
                db.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            heartbeat_future = pool.submit(renew)
            try:
                self.assertTrue(renewed.wait(timeout=5))
                claim_future = pool.submit(claim_after_original_expiry)
                pid = claim_pid.get(timeout=5)
                deadline = monotonic() + 5
                with self.engine.connect() as observer:
                    while not claim_future.done():
                        waiting = observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                            ),
                            {"pid": pid},
                        )
                        if waiting:
                            break
                        if monotonic() >= deadline:
                            self.fail(
                                "claimer neither completed nor waited for the queue lock"
                            )
                        release.wait(timeout=0.01)
            finally:
                release.set()
            self.assertTrue(heartbeat_future.result(timeout=5))
            next_claim = claim_future.result(timeout=5)
        active = self.db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status == "running",
                Job.lease_expires_at > NOW + timedelta(seconds=11),
            )
        )
        self.assertEqual(active, 1)
        self.assertIsNone(next_claim)

    def test_injected_clock_rechecks_ownership_after_locks_and_sets_claim_expiry(self):
        row = self.enqueue()
        current = store.claim(
            self.db,
            now=NOW,
            lease_seconds=10,
            clock=lambda: NOW + timedelta(seconds=5),
        )
        assert current is not None
        self.assertEqual(row.lease_expires_at, NOW + timedelta(seconds=15))

        def expired():
            return NOW + timedelta(seconds=15)

        self.assertIsNone(store.owns(self.db, current, now=NOW, clock=expired))
        self.assertFalse(
            store.heartbeat(self.db, current, now=NOW, lease_seconds=10, clock=expired)
        )
        self.assertFalse(
            store.complete(self.db, current, now=NOW, result_json={}, clock=expired)
        )
        self.assertFalse(
            store.fail(self.db, current, now=NOW, code="model_failed", clock=expired)
        )
        self.assertEqual(row.status, "running")
        self.assertEqual(row.lease_expires_at, NOW + timedelta(seconds=15))

    def test_injected_clock_rechecks_after_owns_before_renewal_or_terminal_write(self):
        row = self.enqueue()
        current = self.claim()
        assert current is not None
        for operation in ("heartbeat", "complete", "fail"):
            readings = iter((NOW + timedelta(seconds=9), NOW + timedelta(seconds=10)))

            def clock():
                return next(readings)

            with self.subTest(operation=operation):
                if operation == "heartbeat":
                    result = store.heartbeat(
                        self.db, current, now=NOW, lease_seconds=10, clock=clock
                    )
                elif operation == "complete":
                    result = store.complete(
                        self.db, current, now=NOW, result_json={}, clock=clock
                    )
                else:
                    result = store.fail(
                        self.db, current, now=NOW, code="model_failed", clock=clock
                    )
                self.assertFalse(result)
                self.assertEqual(row.status, "running")

    def test_injected_clock_records_fresh_heartbeat_expiry_and_terminal_timestamp(self):
        row = self.enqueue()
        current = self.claim()
        assert current is not None

        def fresh():
            return NOW + timedelta(seconds=5)

        self.assertTrue(
            store.heartbeat(self.db, current, now=NOW, lease_seconds=10, clock=fresh)
        )
        self.assertEqual(row.lease_expires_at, NOW + timedelta(seconds=15))
        self.assertTrue(
            store.complete(self.db, current, now=NOW, result_json={}, clock=fresh)
        )
        self.assertEqual(row.finished_at, NOW + timedelta(seconds=5))
        other = self.enqueue()
        other_claim = self.claim()
        assert other_claim is not None
        self.assertTrue(
            store.fail(self.db, other_claim, now=NOW, code="model_failed", clock=fresh)
        )
        self.assertEqual(other.finished_at, NOW + timedelta(seconds=5))

    def test_fresh_connection_reclaim_invalidates_closed_prior_worker_snapshot(self):
        row = self.enqueue()
        row_id = row.id
        self.db.commit()
        with Session(self.engine) as db:
            old = store.claim(db, now=NOW, lease_seconds=10)
            assert old is not None
            db.commit()
        with Session(self.engine) as db:
            current = store.claim(db, now=NOW + timedelta(seconds=10), lease_seconds=10)
            assert current is not None
            self.assertEqual(current.id, row_id)
            self.assertNotEqual(current.lease_token, old.lease_token)
            db.commit()
        with Session(self.engine) as db:
            self.assertIsNone(store.owns(db, old, now=NOW + timedelta(seconds=11)))
            self.assertFalse(
                store.complete(
                    db,
                    old,
                    now=NOW + timedelta(seconds=11),
                    result_json={"question_id": str(uuid4())},
                )
            )
            self.assertFalse(
                store.fail(
                    db, old, now=NOW + timedelta(seconds=11), code="model_failed"
                )
            )
            self.assertFalse(
                store.heartbeat(
                    db, old, now=NOW + timedelta(seconds=11), lease_seconds=10
                )
            )
            self.assertTrue(
                store.complete(
                    db,
                    current,
                    now=NOW + timedelta(seconds=11),
                    result_json={"question_id": str(uuid4())},
                )
            )
            db.commit()
        self.assertEqual(
            store.get_job(self.db, row_id, self.user_id).status, "succeeded"
        )

    def test_failure_has_generic_error_and_terminal_cancel_preserves_status(self):
        row = self.enqueue()
        current = self.claim()
        assert current is not None
        self.assertTrue(store.fail(self.db, current, now=NOW, code="model_unavailable"))
        public = store.public_job(row)
        self.assertEqual(public["error"]["code"], "model_unavailable")
        self.assertEqual(set(public["error"]), {"code", "message", "request_id"})
        self.assertEqual(
            store.cancel(self.db, row.id, self.user_id, now=NOW).status, "failed"
        )

    def test_caller_owns_enqueue_claim_heartbeat_complete_and_cancel_transactions(self):
        row = self.enqueue()
        missing_id = row.id
        self.db.rollback()
        self.assertIsNone(self.db.get(Job, missing_id))
        row = self.enqueue()
        row_id = row.id
        self.db.commit()
        current = self.claim()
        assert current is not None
        self.db.rollback()
        self.assertEqual(store.get_job(self.db, row_id, self.user_id).status, "queued")
        current = self.claim()
        assert current is not None
        self.db.commit()
        store.heartbeat(
            self.db, current, now=NOW + timedelta(seconds=9), lease_seconds=10
        )
        self.db.rollback()
        self.assertEqual(
            store.get_job(self.db, row_id, self.user_id).lease_expires_at,
            NOW + timedelta(seconds=10),
        )
        store.complete(self.db, current, now=NOW, result_json={})
        self.db.rollback()
        self.assertEqual(store.get_job(self.db, row_id, self.user_id).status, "running")
        store.cancel(self.db, row_id, self.user_id, now=NOW)
        self.db.rollback()
        self.assertEqual(store.get_job(self.db, row_id, self.user_id).status, "running")

    def test_database_rejects_mismatched_owner_negative_attempt_revision_and_unsupported_kind(
        self,
    ):
        row = self.enqueue()
        row_id = row.id
        self.db.commit()
        other_id, _ = self.owner()
        for field, value in (
            ("user_id", other_id),
            ("attempt", -1),
            ("profile_revision", -1),
            ("kind", "document_processing"),
            ("document_id", uuid4()),
            ("status", "unknown"),
        ):
            with self.subTest(field=field), self.assertRaises(IntegrityError):
                row = self.db.get(Job, row_id)
                assert row is not None
                setattr(row, field, value)
                self.db.flush()
            self.db.rollback()
        self.assertEqual(
            self.db.scalars(select(Job).where(Job.id == row_id)).one().user_id,
            self.user_id,
        )

    def test_job_http_read_cancel_owner_csrf_and_sanitized_errors(self):
        app = jobs_app()

        def db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = db
        now = datetime.now(UTC)
        user = self.db.get(User, self.user_id)
        assert user is not None
        issued = AuthStore(self.db, signing_key=KEY.encode()).issue_session(
            user, now=now, expires_at=now + timedelta(hours=1)
        )
        row = self.enqueue()
        row_id = row.id
        self.db.commit()
        with (
            patch.dict(
                os.environ,
                {
                    "SESSION_SECRET": KEY,
                    "PUBLIC_ORIGIN": ORIGIN,
                    "GOOGLE_REDIRECT_URI": ORIGIN + "/api/auth/google/callback",
                },
            ),
            TestClient(app, base_url=ORIGIN) as client,
        ):
            client.cookies.set("today_tommorrow_session", issued.token)
            path = "/api/jobs/" + str(row_id)
            response = client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("synthetic-secret", response.text)
            for headers in (
                {},
                {"X-CSRF-Token": "wrong"},
                {"X-CSRF-Token": issued.csrf_token, "Origin": "https://other.invalid"},
            ):
                self.assertEqual(
                    client.post(path + "/cancel", headers=headers).status_code, 403
                )
            for identifier in ("not-a-uuid", str(uuid4())):
                missing = client.get("/api/jobs/" + identifier)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(set(missing.json()), {"code", "message", "request_id"})
                self.assertEqual(
                    client.post(
                        "/api/jobs/" + identifier + "/cancel",
                        headers={"X-CSRF-Token": issued.csrf_token},
                    ).status_code,
                    404,
                )
            other_id, other_profile_id = self.owner()
            other = store.enqueue(
                self.db,
                profile_id=other_profile_id,
                user_id=other_id,
                profile_revision=0,
                idempotency_key="other",
                request_hash="other",
                input_json={},
                now=NOW,
            )
            other_path = "/api/jobs/" + str(other.id)
            self.db.commit()
            self.assertEqual(client.get(other_path).status_code, 404)
            self.assertEqual(
                client.post(
                    other_path + "/cancel", headers={"X-CSRF-Token": issued.csrf_token}
                ).status_code,
                404,
            )
            # The existing session contract permits no Origin header, with valid CSRF.
            response = client.post(
                path + "/cancel", headers={"X-CSRF-Token": issued.csrf_token}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "cancelled")
            self.assertEqual(client.get(path).json()["status"], "cancelled")
            for target, method, suffix in (
                ("get_job", "GET", ""),
                ("cancel", "POST", "/cancel"),
            ):
                with patch(
                    "app.jobs.routes.store." + target,
                    side_effect=SQLAlchemyError("synthetic-private-sql"),
                ):
                    response = client.request(
                        method,
                        path + suffix,
                        headers={"X-CSRF-Token": issued.csrf_token},
                    )
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(
                        set(response.json()), {"code", "message", "request_id"}
                    )
                    self.assertNotIn("synthetic-private", response.text)
