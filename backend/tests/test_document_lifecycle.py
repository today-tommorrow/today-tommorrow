"""Document lifecycle using synthetic content and real PostgreSQL."""

import io
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_file_documents import FileDocumentsTests

from app.documents.models import Document, DocumentChunk
from app.intake.models import IntakeProfile
from app.jobs.models import Job
from app.recommendations.models import RecommendationRun


class DocumentLifecycleTests(FileDocumentsTests):
    def test_failed_retry_delete_and_replay(self):
        profile = self.create()
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("synthetic")
        output = io.BytesIO()
        writer.write(output)
        original = output.getvalue()
        uploaded = self.upload(
            profile, original, name="test.pdf", mime="application/pdf", key="original"
        ).json()
        doc = uploaded["document"]
        self.assertEqual(doc["status"], "failed")
        url = "/api/documents/" + doc["id"]
        retry = self.client.post(
            url + "/retry",
            headers={"X-CSRF-Token": self.csrf},
            json={"expected_revision": 1},
        )
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["document"]["status"], "failed")
        deleted = self.client.delete(
            url, headers={"X-CSRF-Token": self.csrf}, params={"expected_revision": 2}
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get(url).status_code, 404)
        replay = self.upload(
            profile, original, name="test.pdf", mime="application/pdf", key="original"
        )
        self.assertEqual(replay.status_code, 409)
        self.assertNotIn(doc["id"], replay.text)

    def test_review_only_allows_editable_fields(self):
        profile = self.create()
        with Session(self.engine) as db, db.begin():
            row = db.get(IntakeProfile, UUID(profile["id"]))
            row.reviewed_items_json = [
                {
                    "id": "item",
                    "label": "old",
                    "state": "have",
                    "evidence_quote": "synthetic",
                    "document_id": "source",
                }
            ]
        url = "/api/profiles/" + profile["id"] + "/review-items"
        headers = {"X-CSRF-Token": self.csrf}
        forged = {
            "expected_revision": 0,
            "items": [
                {
                    "id": "item",
                    "label": "new",
                    "state": "unknown",
                    "document_id": "forged",
                }
            ],
        }
        self.assertEqual(
            self.client.patch(url, headers=headers, json=forged).status_code, 422
        )
        forged["items"][0].pop("document_id")
        response = self.client.patch(url, headers=headers, json=forged)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["reviewed_items"][0]["document_id"], "source")
        self.assertEqual(
            self.client.patch(url, headers=headers, json=forged).status_code, 409
        )

    def test_delete_rollback(self):
        profile = self.create()
        uploaded = self.upload(profile, b"synthetic").json()
        url = "/api/documents/" + uploaded["document"]["id"]
        with patch(
            "app.documents.lifecycle.invalidate",
            side_effect=SQLAlchemyError("injected"),
        ):
            response = self.client.delete(
                url,
                headers={"X-CSRF-Token": self.csrf},
                params={"expected_revision": 1},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json()["revision"], 1
        )

    def test_delete_scrubs_linked_snapshots_and_preserves_other_document(self):
        from test_recommendation_pipeline import RecommendationPipelineTests, snapshot

        from app.recommendations.contracts import RunnerResult

        pipeline, _, _ = RecommendationPipelineTests().make_pipeline()
        public = pipeline.run(snapshot(pipeline), lambda _: None)
        public["items"][0]["reasons"][0]["text"] = (
            "prefix synthetic-secret-quote suffix"
        )
        catalog_evidence = public["items"][0]["evidence"]
        public.pop("_snapshot")
        public["needs"] = [
            {
                "id": "need",
                "label": "private label",
                "basis": "user_requested",
                "learner_state": "unknown",
                "source_item_ids": ["synthetic-item"],
                "ncs_reference_ids": [],
                "mapping_state": "unmapped",
            }
        ]
        profile = self.create()
        first = self.upload(profile, b"synthetic-secret-quote").json()
        profile = self.client.get("/api/profiles/" + profile["id"]).json()
        second = self.upload(profile, b"unrelated").json()
        doc_id = first["document"]["id"]
        with Session(self.engine) as db, db.begin():
            row = db.get(IntakeProfile, UUID(profile["id"]))
            chunk = db.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == UUID(doc_id))
            )
            item = {
                "id": "synthetic-item",
                "document_id": doc_id,
                "chunk_id": str(chunk.id),
                "label": "private label",
                "state": "have",
                "evidence_quote": "synthetic-secret-quote",
            }
            row.reviewed_items_json = [
                item,
                {
                    "id": "other",
                    "document_id": second["document"]["id"],
                    "label": "keep",
                    "state": "have",
                },
            ]
            job = Job(
                id=uuid4(),
                profile_id=row.id,
                user_id=row.user_id,
                kind="recommendation",
                status="succeeded",
                phase="succeeded",
                profile_revision=row.revision,
                idempotency_key="synthetic",
                request_hash="hash",
                input_json={
                    "reviewed_items": [item],
                    "answers": {"goal": "keep"},
                    "sources": [{"id": "synthetic-item", "text": "historic excerpt"}],
                    "query": "contains historic excerpt here",
                },
                result_json={"quote": "synthetic-secret-quote"},
                created_at=datetime.now(UTC),
            )
            db.add(job)
            quote_job = Job(
                id=uuid4(),
                profile_id=row.id,
                user_id=row.user_id,
                kind="intake_question",
                status="succeeded",
                phase="succeeded",
                profile_revision=row.revision,
                idempotency_key="quote-only",
                request_hash="hash",
                input_json={"context": {"confirmed_answers": {"goal": "keep"}}},
                result_json={"explanation": "prefix historic excerpt suffix"},
                created_at=datetime.now(UTC),
            )
            db.add(quote_job)
            db.flush()
            run = RecommendationRun(
                id=uuid4(),
                job_id=job.id,
                profile_id=row.id,
                user_id=row.user_id,
                profile_revision=row.revision,
                input_json={"reviewed_items": [item], "answers": {"goal": "keep"}},
                public_json=public,
                provenance_json={"source": item},
                created_at=datetime.now(UTC),
            )
            db.add(run)
            run_id, job_id, quote_job_id = run.id, job.id, quote_job.id
        url = "/api/documents/" + doc_id
        response = self.client.delete(
            url, headers={"X-CSRF-Token": self.csrf}, params={"expected_revision": 2}
        )
        self.assertEqual(response.status_code, 204, response.text)
        with Session(self.engine) as db:
            row = db.get(IntakeProfile, UUID(profile["id"]))
            job, run = db.get(Job, job_id), db.get(RecommendationRun, run_id)
            self.assertIsNone(db.get(Job, quote_job_id).result_json)
            for data in (
                row.reviewed_items_json,
                job.input_json,
                job.result_json,
                run.input_json,
                run.public_json,
                run.provenance_json,
            ):
                self.assertNotIn("synthetic-secret-quote", str(data))
                self.assertNotIn(doc_id, str(data))
                self.assertNotIn("historic excerpt", str(data))
            self.assertEqual(row.reviewed_items_json[0]["label"], "keep")
            self.assertEqual(run.input_json["answers"]["goal"], "keep")
            RunnerResult.model_validate(run.public_json)
            self.assertEqual(run.public_json["items"][0]["evidence"], catalog_evidence)
            self.assertNotIn(
                "synthetic-secret-quote",
                run.public_json["items"][0]["reasons"][0]["text"],
            )
        self.assertEqual(
            self.client.get("/api/documents/" + second["document"]["id"]).status_code,
            200,
        )

    def test_worker_completion_is_fenced_by_delete(self):
        from test_question_flow import QuestionProvider

        from app.intake.question_models import IntakeQuestion
        from app.intake.worker import run_once
        from app.jobs import store as jobs

        profile = self.create()
        uploaded = self.upload(profile, b"synthetic worker source").json()
        doc = uploaded["document"]["id"]
        context = {
            "eligible_slots": ["entry_intent"],
            "allowed_companion_slots": {"entry_intent": []},
            "confirmed_answers": {},
            "resolved_unknown_slots": [],
        }
        with Session(self.engine) as db, db.begin():
            job = jobs.enqueue(
                db,
                profile_id=UUID(profile["id"]),
                user_id=self.user_id,
                profile_revision=1,
                idempotency_key="race",
                request_hash="race",
                input_json={
                    "context": context,
                    "context_hash": "race",
                    "model_id": QuestionProvider.model,
                    "prompt_version": "1",
                    "policy_version": "1",
                },
                now=datetime.now(UTC),
            )
            job_id = job.id
        owner = self

        class DeleteDuringInference(QuestionProvider):
            def generate(self, context, *, cancelled=None):
                response = owner.client.delete(
                    "/api/documents/" + doc,
                    headers={"X-CSRF-Token": owner.csrf},
                    params={"expected_revision": 1},
                )
                owner.assertEqual(response.status_code, 204, response.text)
                return super().generate(context, cancelled=cancelled)

        self.assertTrue(run_once(lambda: Session(self.engine), DeleteDuringInference()))
        with Session(self.engine) as db:
            job = db.get(Job, job_id)
            self.assertEqual(job.status, "superseded")
            self.assertIsNone(job.result_json)
            self.assertIsNone(
                db.scalar(select(IntakeQuestion).where(IntakeQuestion.job_id == job_id))
            )

    def test_retry_uses_retained_bytes_and_replays(self):
        profile = self.create()
        uploaded = self.upload(profile, b"synthetic retry text").json()
        doc_id = uploaded["document"]["id"]
        with Session(self.engine) as db, db.begin():
            doc = db.get(Document, UUID(doc_id))
            doc.status, doc.error_code, doc.content = "failed", "extraction_failed", ""
        url = "/api/documents/" + doc_id + "/retry"
        headers = {"X-CSRF-Token": self.csrf, "Idempotency-Key": "retry-synthetic"}
        response = self.client.post(url, headers=headers, json={"expected_revision": 1})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["document"]["status"], "queued")
        self.assertEqual(
            self.client.post(
                url, headers=headers, json={"expected_revision": 1}
            ).json(),
            response.json(),
        )
        with Session(self.engine) as db:
            doc = db.get(Document, UUID(doc_id))
            chunk = db.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            )
            self.assertEqual(doc.content, "synthetic retry text")
            self.assertEqual(chunk.text_content, "synthetic retry text")

    def test_delete_last_then_explicit_none_and_submit(self):
        from test_recommendation_flow import ANSWERS, SyntheticRunner

        profile = self.create()
        uploaded = self.upload(profile, b"\xff").json()
        current = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertIsNone(current["review_token"])
        self.assertEqual(self.save(current, ANSWERS).status_code, 422)
        response = self.client.delete(
            "/api/documents/" + uploaded["document"]["id"],
            headers={"X-CSRF-Token": self.csrf},
            params={"expected_revision": current["revision"]},
        )
        self.assertEqual(response.status_code, 204)
        current = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertIsNone(current["review_token"])
        saved = self.save(current, ANSWERS)
        self.assertEqual(saved.status_code, 200, saved.text)
        current = saved.json()
        self.assertIsNotNone(current["review_token"])
        with patch(
            "app.recommendations.routes.build_runner", return_value=SyntheticRunner()
        ):
            submitted = self.client.post(
                "/api/profiles/" + profile["id"] + "/submit",
                headers={"X-CSRF-Token": self.csrf, "Idempotency-Key": "recovered"},
                json={
                    "expected_revision": current["revision"],
                    "review_token": current["review_token"],
                },
            )
        self.assertEqual(submitted.status_code, 202, submitted.text)

    def test_owner_csrf_duplicate_and_foreign_items(self):
        profile = self.create()
        uploaded = self.upload(profile, b"private").json()
        doc_id = uploaded["document"]["id"]
        other, csrf, _, _ = self.login()
        url = "/api/documents/" + doc_id
        self.assertEqual(
            other.delete(
                url, headers={"X-CSRF-Token": csrf}, params={"expected_revision": 1}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(url, params={"expected_revision": 1}).status_code, 403
        )
        review = "/api/profiles/" + profile["id"] + "/review-items"
        item = {"id": "foreign", "label": "x", "state": "have"}
        self.assertEqual(
            self.client.patch(
                review,
                headers={"X-CSRF-Token": self.csrf},
                json={"expected_revision": 1, "items": [item]},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                review,
                headers={"X-CSRF-Token": self.csrf},
                json={"expected_revision": 1, "items": [item, item]},
            ).status_code,
            422,
        )
        self.assertEqual(
            other.patch(
                review,
                headers={"X-CSRF-Token": csrf},
                json={"expected_revision": 1, "items": []},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.patch(
                review, json={"expected_revision": 1, "items": []}
            ).status_code,
            403,
        )

    def test_duplicate_content_keeps_other_document_item(self):
        profile = self.create()
        first = self.upload(profile, b"a").json()
        profile = self.client.get("/api/profiles/" + profile["id"]).json()
        second = self.upload(profile, b"a").json()
        with Session(self.engine) as db, db.begin():
            row = db.get(IntakeProfile, UUID(profile["id"]))
            row.reviewed_items_json = [
                {
                    "id": str(i),
                    "document_id": document["id"],
                    "content_sha256": document["content_sha256"],
                    "label": "label",
                    "state": "have",
                    "evidence_quote": "a",
                }
                for i, document in enumerate((first["document"], second["document"]))
            ]
        response = self.client.delete(
            "/api/documents/" + first["document"]["id"],
            headers={"X-CSRF-Token": self.csrf},
            params={"expected_revision": 2},
        )
        self.assertEqual(response.status_code, 204)
        current = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertEqual([item["id"] for item in current["reviewed_items"]], ["1"])
        self.assertEqual(
            self.client.get("/api/documents/" + second["document"]["id"]).status_code,
            200,
        )
