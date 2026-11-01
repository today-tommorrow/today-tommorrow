"""Document ownership races and explicit submit on a disposable database."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_document_worker import SyntheticProvider
from test_profile_drafts import ProfileDraftTests
from test_recommendation_flow import ANSWERS, SyntheticRunner

from app.ai.document_analysis import DocumentAnalysis
from app.documents.models import Document, DocumentChunk
from app.documents.worker import run_once
from app.jobs.models import Job


class CallbackProvider(SyntheticProvider):
    def __init__(self, callback):
        self.callback = callback

    def analyze(self, document_id, kind, chunks, *, cancelled):
        self.callback()
        return super().analyze(document_id, kind, chunks, cancelled=cancelled)


class DocumentRaceTests(ProfileDraftTests):
    def create_document(self, *, complete=False):
        profile = self.create()
        if complete:
            profile = self.save(profile, ANSWERS).json()
        response = self.client.post(
            f"/api/profiles/{profile['id']}/documents/texts",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "expected_revision": profile["revision"],
                "kind": "resume",
                "text": "Python experience",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        return profile["id"], response.json()["document"]

    def current(self, profile_id):
        return self.client.get(f"/api/profiles/{profile_id}").json()

    def work(self, provider):
        self.assertTrue(run_once(sessionmaker(self.engine), provider, lease_seconds=5))

    def test_delete_during_analysis_cannot_resurrect_document(self):
        profile_id, doc = self.create_document()

        def delete():
            current = self.current(profile_id)
            response = self.client.delete(
                f"/api/documents/{doc['id']}?expected_revision={current['revision']}",
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 204, response.text)

        self.work(CallbackProvider(delete))
        current = self.current(profile_id)
        self.assertEqual(current["documents"], [])
        self.assertEqual(current["reviewed_items"], [])
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Document, UUID(doc["id"])))
            self.assertNotEqual(db.get(Job, UUID(doc["job_id"])).status, "succeeded")

    def test_cancel_then_retry_during_analysis_keeps_new_generation(self):
        profile_id, doc = self.create_document()
        new_job = []

        def cancel_retry():
            cancelled = self.client.post(
                f"/api/jobs/{doc['job_id']}/cancel", headers={"X-CSRF-Token": self.csrf}
            )
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            current = self.current(profile_id)
            retry = self.client.post(
                f"/api/documents/{doc['id']}/retry",
                headers={"X-CSRF-Token": self.csrf},
                json={"expected_revision": current["revision"]},
            )
            self.assertEqual(retry.status_code, 202, retry.text)
            new_job.append(retry.json()["document"]["job_id"])

        self.work(CallbackProvider(cancel_retry))
        current = self.current(profile_id)
        self.assertEqual(current["documents"][0]["job_id"], new_job[0])
        self.assertEqual(current["documents"][0]["status"], "queued")
        self.assertEqual(current["reviewed_items"], [])
        self.work(SyntheticProvider())
        current = self.current(profile_id)
        self.assertEqual(current["documents"][0]["status"], "ready")
        self.assertEqual(len(current["reviewed_items"]), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.get(Job, UUID(doc["job_id"])).status, "cancelled")
            self.assertEqual(db.get(Job, UUID(new_job[0])).status, "succeeded")

    def test_expired_lease_cannot_publish_and_is_retryable(self):
        profile_id, doc = self.create_document()

        def expire():
            with Session(self.engine) as db, db.begin():
                job = db.get(Job, UUID(doc["job_id"]))
                job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

        self.work(CallbackProvider(expire))
        current = self.current(profile_id)
        self.assertEqual(current["reviewed_items"], [])
        self.assertNotEqual(current["documents"][0]["status"], "ready")
        self.assertFalse(
            run_once(
                sessionmaker(self.engine),
                SyntheticProvider(),
                lease_seconds=5,
                max_attempts=1,
            )
        )
        current = self.current(profile_id)
        self.assertEqual(current["documents"][0]["status"], "failed")
        response = self.client.post(
            f"/api/documents/{doc['id']}/retry",
            headers={"X-CSRF-Token": self.csrf},
            json={"expected_revision": current["revision"]},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.work(SyntheticProvider())
        self.assertEqual(self.current(profile_id)["documents"][0]["status"], "ready")

    def test_source_change_during_analysis_never_publishes(self):
        profile_id, doc = self.create_document()

        def change_source():
            with Session(self.engine) as db, db.begin():
                chunk = db.scalar(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == UUID(doc["id"])
                    )
                )
                chunk.text_content = "different source"

        self.work(CallbackProvider(change_source))
        current = self.current(profile_id)
        self.assertEqual(current["documents"][0]["status"], "failed")
        self.assertEqual(current["reviewed_items"], [])

    def test_unrelated_profile_edit_during_analysis_is_preserved(self):
        profile_id, doc = self.create_document()

        def edit():
            current = self.current(profile_id)
            response = self.save(
                current, {**current["answers"], "entry_intent": "explore"}
            )
            self.assertEqual(response.status_code, 200, response.text)

        self.work(CallbackProvider(edit))
        current = self.current(profile_id)
        self.assertEqual(current["answers"]["entry_intent"], "explore")
        self.assertEqual(current["documents"][0]["id"], doc["id"])
        self.assertEqual(current["documents"][0]["status"], "ready")
        self.assertEqual(len(current["reviewed_items"]), 1)

    def test_confirm_edit_submit_and_empty_confirmation(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                profile_id, doc = self.create_document(complete=True)

                class Empty:
                    def analyze(self, document_id, kind, chunks, *, cancelled):
                        return DocumentAnalysis((), tuple(c.chunk_id for c in chunks))

                self.work(Empty() if empty else SyntheticProvider())
                current = self.current(profile_id)
                self.assertIsNone(current["review_token"])
                with patch(
                    "app.recommendations.routes.build_runner",
                    return_value=SyntheticRunner(),
                ):
                    blocked = self.client.post(
                        f"/api/profiles/{profile_id}/submit",
                        headers={
                            "X-CSRF-Token": self.csrf,
                            "Idempotency-Key": "blocked-" + doc["id"],
                        },
                        json={
                            "expected_revision": current["revision"],
                            "review_token": "v1." + "0" * 64,
                        },
                    )
                self.assertEqual(blocked.status_code, 409, blocked.text)
                items = current["reviewed_items"]
                if items:
                    item = items[0]
                    forged = self.client.patch(
                        f"/api/profiles/{profile_id}/review-items",
                        headers={"X-CSRF-Token": self.csrf},
                        json={
                            "expected_revision": current["revision"],
                            "items": [
                                {
                                    "id": item["id"],
                                    "label": "SQL",
                                    "state": "want_to_learn",
                                    "evidence_quote": "forged",
                                }
                            ],
                            "confirmed_document_ids": [doc["id"]],
                        },
                    )
                    self.assertEqual(forged.status_code, 422, forged.text)
                    edits = [
                        {"id": item["id"], "label": "SQL", "state": "want_to_learn"}
                    ]
                else:
                    edits = []
                result = self.client.patch(
                    f"/api/profiles/{profile_id}/review-items",
                    headers={"X-CSRF-Token": self.csrf},
                    json={
                        "expected_revision": current["revision"],
                        "items": edits,
                        "confirmed_document_ids": [doc["id"]],
                    },
                )
                self.assertEqual(result.status_code, 200, result.text)
                current = self.current(profile_id)
                self.assertTrue(current["review_token"])
                if items:
                    self.assertEqual(
                        current["reviewed_items"][0]["evidence_quote"],
                        "Python experience",
                    )
                with patch(
                    "app.recommendations.routes.build_runner",
                    return_value=SyntheticRunner(),
                ):
                    submitted = self.client.post(
                        f"/api/profiles/{profile_id}/submit",
                        headers={
                            "X-CSRF-Token": self.csrf,
                            "Idempotency-Key": "submit-" + doc["id"],
                        },
                        json={
                            "expected_revision": current["revision"],
                            "review_token": current["review_token"],
                        },
                    )
                self.assertEqual(submitted.status_code, 202, submitted.text)
                with Session(self.engine) as db:
                    job = db.get(Job, UUID(submitted.json()["job_id"]))
                    self.assertEqual(job.input_json["documents"][0]["id"], doc["id"])
                    self.assertTrue(job.input_json["documents"][0]["review_confirmed"])
                    if items:
                        self.assertEqual(
                            job.input_json["reviewed_items"][0]["evidence_quote"],
                            "Python experience",
                        )
