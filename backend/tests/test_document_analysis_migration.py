"""Verify the legacy queued upload migration against PostgreSQL."""

from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from test_profile_drafts import ProfileDraftTests


class DocumentMigrationTests(ProfileDraftTests):
    def test_legacy_queued_upload_becomes_retryable_without_data_loss(self):
        profile = self.create()
        document_id, chunk_id = uuid4(), uuid4()
        config = Config("alembic.ini")
        command.downgrade(config, "0010_file_documents")
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text("""INSERT INTO today_app.documents
                    (id, profile_id, user_id, kind, source_type, display_name, mime_type,
                     status, content, content_sha256, size_bytes, created_at)
                    VALUES (:id, :profile, :user, 'resume', 'text', 'legacy', 'text/plain',
                            'queued', 'synthetic source', :hash, 16, now())"""),
                    {
                        "id": document_id,
                        "profile": UUID(profile["id"]),
                        "user": self.user_id,
                        "hash": "a" * 64,
                    },
                )
                connection.execute(
                    text("""INSERT INTO today_app.document_chunks
                    (id, document_id, profile_id, user_id, ordinal, text_content, content_sha256, locator)
                    VALUES (:id, :document, :profile, :user, 0, 'synthetic source', :hash, 'text:chars:0:16')"""),
                    {
                        "id": chunk_id,
                        "document": document_id,
                        "profile": UUID(profile["id"]),
                        "user": self.user_id,
                        "hash": "b" * 64,
                    },
                )
        finally:
            command.upgrade(config, "head")
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, error_code, content, job_id FROM today_app.documents WHERE id=:id"
                ),
                {"id": document_id},
            ).one()
            chunk = connection.execute(
                text("SELECT text_content FROM today_app.document_chunks WHERE id=:id"),
                {"id": chunk_id},
            ).scalar_one()
        self.assertEqual(
            tuple(row), ("failed", "analysis_interrupted", "synthetic source", None)
        )
        self.assertEqual(chunk, "synthetic source")
        retry = self.client.post(
            "/api/documents/" + str(document_id) + "/retry",
            headers={"X-CSRF-Token": self.csrf},
            json={"expected_revision": profile["revision"]},
        )
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["document"]["status"], "queued")
        self.assertTrue(retry.json()["document"]["job_id"])
