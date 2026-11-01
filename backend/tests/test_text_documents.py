"""Text registration contract against the dedicated PostgreSQL test database."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import test_profile_drafts
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentChunk


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
        self.assertIsNone(doc["job_id"])
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
            self.assertEqual(chunk.locator, f"text:chars:0:{len(original)}")

    def test_limits_validation_and_capacity_replay(self):
        profile = self.create()
        path = "/api/profiles/" + profile["id"] + "/documents/texts"
        headers = {"X-CSRF-Token": self.csrf}
        for text in ("", "bad\x00text", "\ud800"):
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
