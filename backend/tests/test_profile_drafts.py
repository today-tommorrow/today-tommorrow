"""Profile HTTP contracts against real PostgreSQL and synthetic service sessions."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.auth.models import AuthSession, User
from app.auth.store import AuthStore
from app.db import get_session
from app.main import app

ORIGIN = "http://127.0.0.1:5173"
KEY = "synthetic-profile-key-at-least-32-bytes"


class AnonymousProfileTests(unittest.TestCase):
    def test_openapi_describes_revision_and_full_answer_request(self):
        schema = TestClient(app).get("/openapi.json").json()
        body = schema["paths"]["/api/profiles/{id}"]["patch"]["requestBody"]
        self.assertTrue(body["required"])
        payload = body["content"]["application/json"]["schema"]
        self.assertEqual(set(payload["required"]), {"expected_revision", "answers"})
        self.assertEqual(payload["properties"]["expected_revision"]["minimum"], 0)
        self.assertFalse(payload["properties"]["answers"]["additionalProperties"])

    def test_personal_profile_endpoints_require_session(self):
        client = TestClient(app)
        for method, path in (
            ("POST", "/api/profiles"),
            ("GET", "/api/profiles/" + str(uuid4())),
            ("PATCH", "/api/profiles/" + str(uuid4())),
            ("GET", "/api/onboarding/options"),
        ):
            with self.subTest(path=path, method=method):
                response = client.request(method, path)
                self.assertEqual(response.status_code, 401)


@unittest.skipUnless(
    os.environ.get("L0_DATABASE_TESTS") == "1", "run after local DB setup"
)
class ProfileDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SESSION_SECRET": KEY,
                "PUBLIC_ORIGIN": ORIGIN,
                "GOOGLE_REDIRECT_URI": ORIGIN + "/api/auth/google/callback",
                "QUESTION_MODEL_BASE_URL": "",
                "QUESTION_MODEL_ID": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.user_ids = []
        self.addCleanup(self.clean)

        def db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = db
        self.addCleanup(app.dependency_overrides.clear)
        self.client, self.csrf, self.user_id, self.token = self.login()

    def clean(self):
        with Session(self.engine) as db, db.begin():
            db.execute(delete(User).where(User.id.in_(self.user_ids)))

    def login(self):
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            store = AuthStore(db, signing_key=KEY.encode())
            user = store.save_google_identity(
                subject="synthetic-profile-" + uuid4().hex,
                email="synthetic@example.invalid",
                now=now,
            )
            self.user_ids.append(user.id)
            issued = store.issue_session(
                user, now=now, expires_at=now + timedelta(hours=1)
            )
        client = TestClient(app, base_url=ORIGIN)
        client.cookies.set("today_tommorrow_session", issued.token)
        self.addCleanup(client.close)
        return client, issued.csrf_token, issued.user_id, issued.token

    def create(self):
        response = self.client.post(
            "/api/profiles", headers={"X-CSRF-Token": self.csrf}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def save(self, profile, answers, revision=None, client=None, csrf=None):
        return (client or self.client).patch(
            "/api/profiles/" + profile["id"],
            headers={"X-CSRF-Token": csrf or self.csrf},
            json={
                "expected_revision": profile["revision"]
                if revision is None
                else revision,
                "answers": answers,
            },
        )

    def test_create_persist_replace_reload_and_distinct_drafts(self):
        profile = self.create()
        self.assertEqual(profile["revision"], 0)
        self.assertEqual(profile["status"], "draft")
        self.assertEqual(profile["answers"], {})
        self.assertIsNone(profile["review_token"])
        self.assertEqual(profile["documents"], [])
        self.assertNotIn("user_id", profile)
        saved = self.save(
            profile, {"entry_intent": "explore", "field_code": "undecided"}
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        value = saved.json()
        self.assertEqual(value["revision"], 1)
        self.assertEqual(value["slot_origins"]["field_code"], "explicit_unknown")
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), value
        )
        # PATCH is the entire answer set, not a merge patch.
        replaced = self.save(value, {"experience": "unknown"})
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(replaced.json()["answers"], {"experience": "unknown"})
        self.assertEqual(replaced.json()["revision"], 2)
        self.assertNotEqual(self.create()["id"], profile["id"])

    def test_stale_write_preserves_latest_answers_and_timestamp(self):
        profile = self.create()
        latest = self.save(profile, {"entry_intent": "explore"}).json()
        stale = self.save(profile, {"entry_intent": "advance"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "revision_conflict")
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), latest
        )

    def test_same_user_can_restore_draft_with_a_new_service_session(self):
        profile = self.create()
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            user = db.get(User, self.user_id)
            assert user is not None
            session = AuthStore(db, signing_key=KEY.encode()).issue_session(
                user, now=now, expires_at=now + timedelta(hours=1)
            )
        with TestClient(app, base_url=ORIGIN) as client:
            client.cookies.set("today_tommorrow_session", session.token)
            self.assertEqual(
                client.get("/api/profiles/" + profile["id"]).json(), profile
            )

    def test_other_owner_and_missing_profile_have_same_404(self):
        profile = self.create()
        other, csrf, _, _ = self.login()
        self.assertEqual(other.get("/api/profiles/" + profile["id"]).status_code, 404)
        self.assertEqual(
            self.save(profile, {}, client=other, csrf=csrf).status_code, 404
        )
        self.assertEqual(other.get("/api/profiles/" + str(uuid4())).status_code, 404)

    def test_mutations_require_csrf_and_matching_origin(self):
        profile = self.create()
        for headers in (
            {},
            {"X-CSRF-Token": "wrong"},
            {"X-CSRF-Token": self.csrf, "Origin": "https://other.invalid"},
        ):
            self.assertEqual(
                self.client.post("/api/profiles", headers=headers).status_code, 403
            )
            self.assertEqual(
                self.client.patch(
                    "/api/profiles/" + profile["id"],
                    headers=headers,
                    json={"expected_revision": 0, "answers": {}},
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), profile
        )

    def test_validation_is_strict_and_does_not_reflect_private_input(self):
        profile = self.create()
        cases = [
            {"expected_revision": True, "answers": {}},
            {"expected_revision": "0", "answers": {}},
            {"expected_revision": -1, "answers": {}},
            {"expected_revision": 0, "answers": {"budget_krw": True}},
            {"expected_revision": 0, "answers": {"experience": None}},
            {
                "expected_revision": 0,
                "answers": {"experience": "synthetic-private-text"},
            },
            {"expected_revision": 0, "answers": {"eligibility_context": {"age": 121}}},
            {"expected_revision": 0, "answers": {"goal_text": "s" * 2001}},
            {"expected_revision": 0, "answers": {}, "user_id": str(uuid4())},
            {
                "expected_revision": 0,
                "answers": {"unknown_key": "synthetic-private-text"},
            },
        ]
        for body in cases:
            with self.subTest(body=body):
                response = self.client.patch(
                    "/api/profiles/" + profile["id"],
                    headers={"X-CSRF-Token": self.csrf},
                    json=body,
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(
                    set(response.json()), {"code", "message", "request_id"}
                )
                self.assertNotIn("synthetic-private-text", response.text)
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), profile
        )

    def test_concurrent_writes_apply_once(self):
        profile = self.create()
        barrier = Barrier(2)

        def save(intent):
            with TestClient(app, base_url=ORIGIN) as client:
                client.cookies.set("today_tommorrow_session", self.token)
                barrier.wait(timeout=5)
                return self.save(profile, {"entry_intent": intent}, client=client)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(save, ["explore", "advance"]))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        winner = next(r.json() for r in responses if r.status_code == 200)
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), winner
        )
        self.assertEqual(winner["revision"], 1)

    def test_field_change_preserves_answers_and_requires_reconfirmation(self):
        profile = self.create()
        answers = {
            "field_code": "ai_data",
            "experience": "basic",
            "learning_goal": "portfolio",
        }
        current = self.save(profile, answers).json()
        changed = self.save(current, {**answers, "field_code": "software_cloud"}).json()
        self.assertEqual(changed["answers"]["experience"], "basic")
        self.assertEqual(
            set(changed["unresolved_conflicts"]), {"experience", "learning_goal"}
        )
        unchanged = self.save(changed, changed["answers"]).json()
        self.assertEqual(
            unchanged["unresolved_conflicts"], changed["unresolved_conflicts"]
        )
        reopened = self.save(
            unchanged, {"field_code": "software_cloud", "learning_goal": "foundation"}
        ).json()
        self.assertEqual(reopened["unresolved_conflicts"], [])
        self.assertIn("experience", reopened["intake_state"]["missing_slots"])

    def test_inapplicable_conflicts_are_hidden_and_reactivate_with_the_parent(self):
        from app.intake.models import IntakeProfile

        profile = self.create()
        answers = {
            "entry_intent": "goal_known",
            "field_code": "ai_data",
            "experience": "basic",
            "previous_gap": "depth",
            "learning_goal": "portfolio",
            "goal_text": "Synthetic goal",
            "availability": "unknown",
            "delivery_mode": "online",
            "budget_status": "no_limit",
            "start_timing": "unknown",
            "document_decision": "none",
        }
        current = self.save(profile, answers).json()
        changed = self.save(current, {**answers, "field_code": "software_cloud"}).json()
        self.assertEqual(
            set(changed["unresolved_conflicts"]),
            {"experience", "previous_gap", "learning_goal", "goal_text"},
        )
        inactive_answers = {
            **changed["answers"],
            "entry_intent": "explore",
            "experience": "beginner",
            "learning_goal": "foundation",
        }
        inactive = self.save(changed, inactive_answers).json()
        self.assertEqual(inactive["answers"], inactive_answers)
        self.assertEqual(inactive["unresolved_conflicts"], [])
        self.assertEqual(inactive["intake_state"]["missing_slots"], [])
        self.assertEqual(inactive["intake_state"]["next_action"], "review")
        self.assertEqual(
            inactive["completed_sections"],
            ["direction", "experience", "goal", "conditions"],
        )
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), inactive
        )
        # Resaving must preserve the latent recheck instead of acknowledging it.
        repeated = self.save(inactive, inactive_answers).json()
        self.assertEqual(repeated["unresolved_conflicts"], [])
        self.assertEqual(repeated["intake_state"]["next_action"], "review")
        with Session(self.engine) as db:
            row = db.scalar(
                select(IntakeProfile).where(IntakeProfile.user_id == self.user_id)
            )
            assert row is not None
            self.assertEqual(
                set(row.unresolved_conflicts_json), {"previous_gap", "goal_text"}
            )
            self.assertEqual(row.answers_json, inactive_answers)
        reactivated = self.save(
            repeated,
            {**inactive_answers, "entry_intent": "goal_known", "experience": "basic"},
        ).json()
        self.assertEqual(
            set(reactivated["unresolved_conflicts"]), {"previous_gap", "goal_text"}
        )
        self.assertEqual(reactivated["answers"]["previous_gap"], "depth")
        self.assertEqual(reactivated["answers"]["goal_text"], "Synthetic goal")
        self.assertEqual(reactivated["intake_state"]["section"], "experience")
        self.assertEqual(reactivated["intake_state"]["next_action"], "resolve_conflict")
        self.assertEqual(
            set(reactivated["intake_state"]["missing_slots"]),
            {"previous_gap", "goal_text"},
        )
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json(), reactivated
        )

    def test_options_report_only_supported_capabilities(self):
        response = self.client.get("/api/onboarding/options")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["allowed_catalog_sources"], ["work24"])
        self.assertFalse(body["question_generation_enabled"])
        self.assertFalse(body["interpretation_enabled"])
        self.assertEqual(body["allowed_file_extensions"], [])
        self.assertEqual(body["max_profile_documents"], 5)

    def test_expired_session_and_database_failure_are_sanitized(self):
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == self.user_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        self.assertEqual(
            self.client.post(
                "/api/profiles", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            401,
        )
        self.client, self.csrf, _, _ = self.login()
        with patch(
            "app.auth.dependencies.AuthStore.get_session",
            side_effect=SQLAlchemyError("synthetic-private-database"),
        ):
            response = self.client.post(
                "/api/profiles", headers={"X-CSRF-Token": self.csrf}
            )
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("synthetic-private", response.text)

    def test_malformed_or_large_body_does_not_change_draft(self):
        profile = self.create()
        path = "/api/profiles/" + profile["id"]
        headers = {"X-CSRF-Token": self.csrf, "Content-Type": "application/json"}
        self.assertEqual(
            self.client.patch(path, headers=headers, content="{").status_code, 422
        )
        self.assertEqual(
            self.client.patch(path, headers=headers, content="s" * 65537).status_code,
            413,
        )
        self.assertEqual(self.client.get(path).json(), profile)
        self.assertEqual(self.client.get("/api/profiles/not-a-uuid").status_code, 404)

    def test_migration_model_defaults_and_cascade(self):
        from app.intake.models import IntakeProfile

        profile = self.create()
        self.assertIn(
            "intake_profiles", inspect(self.engine).get_table_names(schema="today_app")
        )
        with Session(self.engine) as db:
            row = db.scalar(
                select(IntakeProfile).where(IntakeProfile.user_id == self.user_id)
            )
            assert row is not None
            self.assertEqual(str(row.id), profile["id"])
            self.assertEqual(row.answers_json, {})
            self.assertEqual(row.revision, 0)
            self.assertEqual(
                db.execute(
                    text("SELECT count(*) FROM work24_bge.course_unit")
                ).scalar(),
                6099,
            )
            db.execute(delete(User).where(User.id == self.user_id))
            db.flush()
            self.assertIsNone(
                db.scalar(
                    select(IntakeProfile).where(IntakeProfile.user_id == self.user_id)
                )
            )
