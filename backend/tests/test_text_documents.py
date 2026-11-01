"""Text registration contract against the dedicated PostgreSQL test database."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import patch
from uuid import UUID

import test_profile_drafts
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentChunk
from app.intake.models import IntakeProfile
from app.intake.question_models import IntakeRequest
from app.intake.question_service import invalidate
from app.jobs.models import Job


class TextDocumentsTests(test_profile_drafts.ProfileDraftTests):
    def register(
        self, profile, text, *, kind="other", key=None, client=None, csrf=None
    ):
        headers = {"X-CSRF-Token": csrf or self.csrf}
        if key:
            headers["Idempotency-Key"] = key
        return (client or self.client).post(
            "/api/profiles/" + profile["id"] + "/documents/texts",
            headers=headers,
            json={"expected_revision": profile["revision"], "kind": kind, "text": text},
        )

    def test_exact_storage_replay_owner_and_decision_guard(self):
        profile = self.create()
        original = "  synthetic\r\n한글 e\u0301 😀  "
        response = self.register(profile, original, key="synthetic-key")
        self.assertEqual(response.status_code, 202, response.text)
        value = response.json()
        doc = value["document"]
        self.assertEqual(
            doc["content_sha256"], hashlib.sha256(original.encode()).hexdigest()
        )
        self.assertEqual(doc["size_bytes"], len(original.encode()))
        self.assertEqual(doc["status"], "queued")
        self.assertIsNotNone(doc["job_id"])
        self.assertNotIn(original, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            self.register(profile, original, key="synthetic-key").json(), value
        )
        self.assertEqual(
            self.register(profile, "different", key="synthetic-key").status_code, 409
        )
        current = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertEqual(current["documents"], [doc])
        self.assertEqual(current["answers"]["document_decision"], "provided")
        self.assertIsNone(current["review_token"])
        self.assertEqual(
            self.save(current, {"document_decision": "none"}).status_code, 422
        )
        other, csrf, _, _ = self.login()
        self.assertEqual(other.get("/api/documents/" + doc["id"]).status_code, 404)
        self.assertEqual(
            self.register(profile, original, client=other, csrf=csrf).status_code, 404
        )
        with Session(self.engine) as db:
            row = db.get(Document, UUID(doc["id"]))
            chunk = db.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == row.id)
            )
            self.assertEqual(row.content, original)
            self.assertEqual(chunk.text_content, original)
            self.assertEqual(
                chunk.locator, f"text:chars:0:{len(original)}:slice:0:{len(original)}"
            )

    def test_limits_validation_and_capacity_replay(self):
        profile = self.create()
        path = "/api/profiles/" + profile["id"] + "/documents/texts"
        headers = {"X-CSRF-Token": self.csrf}
        for text in ("", "  \r\n ", "bad\x00text", "\ud800"):
            response = self.client.post(
                path,
                headers=headers,
                content=json.dumps(
                    {"expected_revision": 0, "kind": "other", "text": text}
                ),
            )
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("bad", response.text)
        self.assertEqual(self.register(profile, "a" * 100001).status_code, 413)
        first = self.register(profile, "가" * 33333 + "a")
        self.assertEqual(first.status_code, 202, first.text)
        for i in range(4):
            profile = self.client.get("/api/profiles/" + profile["id"]).json()
            self.assertEqual(self.register(profile, str(i)).status_code, 202)
        current = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertEqual(self.register(current, "overflow").status_code, 422)
        self.assertEqual(
            self.register({**profile, "revision": 0}, "가" * 33333 + "a").json(),
            first.json(),
        )

    def test_concurrent_revision_only_one_applies(self):
        profile = self.create()
        barrier = Barrier(2)

        def submit(text):
            with TestClient(
                test_profile_drafts.app, base_url=test_profile_drafts.ORIGIN
            ) as client:
                client.cookies.set("today_tommorrow_session", self.token)
                barrier.wait(timeout=5)
                return self.register(profile, text, client=client).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, ["one", "two"]))
        self.assertEqual(sorted(results), [202, 409])

    def test_failure_after_invalidation_rolls_back_everything(self):
        profile = self.create()
        profile_id = UUID(profile["id"])
        with Session(self.engine) as db, db.begin():
            job = Job(
                profile_id=profile_id,
                user_id=self.user_id,
                kind="intake_question",
                status="queued",
                phase="queued",
                profile_revision=0,
                idempotency_key="rollback-job",
                request_hash="synthetic",
                input_json={},
                created_at=datetime.now(UTC),
            )
            db.add(job)
            db.flush()
            job_id = job.id

        def fail_after_invalidation(db, identifier):
            invalidate(db, identifier)
            raise SQLAlchemyError("synthetic failure")

        with patch(
            "app.documents.service.invalidate", side_effect=fail_after_invalidation
        ):
            response = self.register(
                profile, "synthetic private content", key="rollback-request"
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("synthetic private content", response.text)
        with Session(self.engine) as db:
            saved = db.get(IntakeProfile, profile_id)
            self.assertEqual(saved.revision, 0)
            self.assertEqual(saved.answers_json, {})
            self.assertEqual(saved.status, "draft")
            self.assertIsNone(
                db.scalar(select(Document).where(Document.profile_id == profile_id))
            )
            self.assertIsNone(
                db.scalar(
                    select(DocumentChunk).where(DocumentChunk.profile_id == profile_id)
                )
            )
            self.assertIsNone(
                db.scalar(
                    select(IntakeRequest).where(
                        IntakeRequest.profile_id == profile_id,
                        IntakeRequest.operation == "register_text",
                    )
                )
            )
            self.assertEqual(db.get(Job, job_id).status, "queued")
