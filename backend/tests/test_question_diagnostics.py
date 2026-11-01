"""Question worker failure diagnostics never log user or model content."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.ai.questions import QuestionDraft, QuestionError
from app.intake import worker
from app.jobs.store import public_job

JOB_ID = UUID("41d52e0c-c57e-4437-980f-c34006f32abe")
SECRET = "private-prompt-password-http://user:secret@example.invalid/model"


class QuestionDiagnosticsTests(unittest.TestCase):
    def test_categories_are_allowlisted_and_do_not_format_exceptions(self):
        for error, expected in (
            (QuestionError("model_timeout"), "model_timeout"),
            (QuestionError("invalid_question_json"), "invalid_question_json"),
            (QuestionError(SECRET), "unexpected_error"),
            (QuestionError("model_timeout", SECRET), "unexpected_error"),
            (SQLAlchemyError(SECRET), "database_error"),
            (ValueError("provider_model_changed"), "provider_model_changed"),
            (ValueError(SECRET), "unexpected_error"),
        ):
            with self.subTest(category=expected):
                self.assertEqual(worker.failure_category(error), expected)

    def test_provider_errors_are_private_and_public_failure_remains_generic(self):
        for error in (
            QuestionError("model_timeout"),
            QuestionError(SECRET),
            ValueError(SECRET),
            SQLAlchemyError(SECRET),
        ):
            with self.subTest(category=worker.failure_category(error)):
                factory, provider = MagicMock(), MagicMock()
                provider.model = "model"
                provider.generate.side_effect = error
                claim = SimpleNamespace(
                    id=JOB_ID,
                    input_json={"model_id": "model", "context": {"private": SECRET}},
                )
                with (
                    patch.object(worker.jobs, "claim", return_value=claim),
                    patch.object(worker.jobs, "fail") as fail,
                    patch.object(worker.jobs, "complete") as complete,
                    self.assertLogs(worker.LOGGER, level="WARNING") as logs,
                ):
                    self.assertTrue(worker._run_once(factory, provider))
                self.assertEqual(
                    fail.call_args.kwargs["code"], "question_generation_failed"
                )
                complete.assert_not_called()
                self.assertEqual(
                    logs.records[0].getMessage(),
                    f"question_generation_failed job_id={JOB_ID} stage=provider_generation category={worker.failure_category(error)}",
                )
                self.assertIsNone(logs.records[0].exc_info)
                self.assertIsNone(logs.records[0].stack_info)
                self.assertNotIn(SECRET, str(logs.records[0].__dict__))
        job = SimpleNamespace(
            id=JOB_ID,
            kind="intake_question",
            status="failed",
            phase="failed",
            profile_id=JOB_ID,
            profile_revision=0,
            result_json=None,
            error_code="question_generation_failed",
            created_at=datetime.now(UTC),
            finished_at=None,
        )
        self.assertEqual(public_job(job)["error"]["code"], "question_generation_failed")
        self.assertNotIn(SECRET, str(public_job(job)))

    def test_persistence_validation_failure_is_private_and_does_not_complete(self):
        try:
            QuestionDraft.model_validate({"question": SECRET})
        except ValidationError as error:
            validation_error = error
        factory, provider = MagicMock(), MagicMock()
        provider.model = "model"
        claim = SimpleNamespace(
            id=JOB_ID,
            profile_id=JOB_ID,
            user_id=JOB_ID,
            profile_revision=1,
            input_json={"model_id": "model", "context": {}},
        )
        with (
            patch.object(worker.jobs, "claim", return_value=claim),
            patch.object(worker.jobs, "owns", return_value=SimpleNamespace()),
            patch.object(
                worker.store, "get_profile", return_value=SimpleNamespace(revision=1)
            ),
            patch.object(worker, "save_question", side_effect=validation_error),
            patch.object(worker.jobs, "fail") as fail,
            patch.object(worker.jobs, "complete") as complete,
            self.assertLogs(worker.LOGGER, level="WARNING") as logs,
        ):
            self.assertTrue(worker._run_once(factory, provider))
        self.assertEqual(fail.call_args.kwargs["code"], "question_generation_failed")
        complete.assert_not_called()
        self.assertIn(
            "stage=question_persistence category=validation_error", logs.output[0]
        )
        self.assertNotIn(SECRET, str(logs.records[0].__dict__))
        self.assertIsNone(logs.records[0].exc_info)
        self.assertIsNone(logs.records[0].stack_info)


if __name__ == "__main__":
    unittest.main()
