"""Synthetic-provider integration coverage for document analysis."""

from sqlalchemy.orm import sessionmaker
from test_profile_drafts import ProfileDraftTests

from app.ai.document_analysis import DocumentAnalysis, DocumentProposal
from app.documents.worker import run_once


class SyntheticProvider:
    def analyze(self, document_id, kind, chunks, *, cancelled):
        chunk = chunks[0]
        return DocumentAnalysis(
            (
                DocumentProposal(
                    document_id,
                    kind,
                    chunk.chunk_id,
                    "Python",
                    "have",
                    "Python experience",
                ),
            ),
            tuple(c.chunk_id for c in chunks),
        )


class DocumentWorkerTests(ProfileDraftTests):
    def test_document_flow(self):
        profile = self.create()
        response = self.client.post(
            "/api/profiles/" + profile["id"] + "/documents/texts",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "expected_revision": 0,
                "kind": "resume",
                "text": "Python experience",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        document = response.json()["document"]
        self.assertTrue(document["job_id"])
        self.assertTrue(run_once(sessionmaker(self.engine), SyntheticProvider()))
        profile = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertEqual(profile["documents"][0]["status"], "ready")
        self.assertFalse(profile["documents"][0]["review_confirmed"])
        item = profile["reviewed_items"][0]
        self.assertEqual(item["evidence_quote"], "Python experience")
        self.assertEqual(item["state"], "unknown")
        patched = self.client.patch(
            "/api/profiles/" + profile["id"] + "/review-items",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "expected_revision": profile["revision"],
                "items": [{"id": item["id"], "label": "Python", "state": "have"}],
                "confirmed_document_ids": [document["id"]],
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertTrue(patched.json()["documents"][0]["review_confirmed"])
        self.assertEqual(
            patched.json()["reviewed_items"][0]["evidence_quote"], "Python experience"
        )

    def test_failure_retry_and_empty_review(self):
        class Failed:
            def analyze(self, *args, **kwargs):
                raise TimeoutError("private provider output")

        class Empty:
            def analyze(self, document_id, kind, chunks, *, cancelled):
                return DocumentAnalysis((), tuple(c.chunk_id for c in chunks))

        profile = self.create()
        response = self.client.post(
            "/api/profiles/" + profile["id"] + "/documents/texts",
            headers={"X-CSRF-Token": self.csrf},
            json={"expected_revision": 0, "kind": "resume", "text": "Synthetic source"},
        )
        document = response.json()["document"]
        self.assertTrue(run_once(sessionmaker(self.engine), Failed()))
        profile = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertEqual(profile["documents"][0]["status"], "failed")
        self.assertIsNone(profile["review_token"])
        retry = self.client.post(
            "/api/documents/" + document["id"] + "/retry",
            headers={"X-CSRF-Token": self.csrf},
            json={"expected_revision": profile["revision"]},
        )
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["document"]["id"], document["id"])
        self.assertTrue(run_once(sessionmaker(self.engine), Empty()))
        profile = self.client.get("/api/profiles/" + profile["id"]).json()
        self.assertEqual(profile["documents"][0]["status"], "ready")
        self.assertFalse(profile["documents"][0]["review_confirmed"])
        self.assertEqual(profile["reviewed_items"], [])
        patch = self.client.patch(
            "/api/profiles/" + profile["id"] + "/review-items",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "expected_revision": profile["revision"],
                "items": [],
                "confirmed_document_ids": [document["id"]],
            },
        )
        self.assertEqual(patch.status_code, 200, patch.text)
        self.assertTrue(patch.json()["documents"][0]["review_confirmed"])

    def test_confirmed_documents_allow_question_flow(self):
        from uuid import UUID

        from app.intake.question_service import generation_context
        from app.intake.store import get_profile

        profile = self.create()
        profile = self.save(
            profile,
            {
                "entry_intent": "explore",
                "field_code": "software_cloud",
                "experience": "beginner",
            },
        ).json()
        response = self.client.post(
            "/api/profiles/" + profile["id"] + "/documents/texts",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "expected_revision": profile["revision"],
                "kind": "resume",
                "text": "Python experience",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        document = response.json()["document"]
        self.assertTrue(run_once(sessionmaker(self.engine), SyntheticProvider()))
        profile = self.client.get("/api/profiles/" + profile["id"]).json()
        result = self.client.patch(
            "/api/profiles/" + profile["id"] + "/review-items",
            headers={"X-CSRF-Token": self.csrf},
            json={
                "expected_revision": profile["revision"],
                "items": [],
                "confirmed_document_ids": [document["id"]],
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            result.json()["intake_state"]["next_action"], "generate_question"
        )
        with sessionmaker(self.engine)() as db:
            context = generation_context(
                db, get_profile(db, UUID(profile["id"]), self.user_id)
            )
            self.assertEqual(context["eligible_slots"], ["learning_goal"])
