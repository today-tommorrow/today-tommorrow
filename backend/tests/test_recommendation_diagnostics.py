"""Safe diagnostics retain failure context without exposing provider/private text."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.recommendations import worker
from app.recommendations.contracts import JsonObject, PhaseReporter, RecommendationError

JOB_ID = UUID("41d52e0c-c57e-4437-980f-c34006f32abe")
SECRET = "private-prompt-password-http://user:secret@example.invalid/model"


class RecommendationDiagnosticsTests(unittest.TestCase):
    def test_failure_categories_do_not_expose_exception_contents(self) -> None:
        cases = (
            (
                RecommendationError("recommendation_model_timeout"),
                "recommendation_model_timeout",
            ),
            (RecommendationError("invalid_model_grounding"), "invalid_model_grounding"),
            (RecommendationError(SECRET), "unexpected_error"),
            (RecommendationError("invalid_model_response", SECRET), "unexpected_error"),
            (SQLAlchemyError(SECRET), "database_error"),
            (TimeoutError(SECRET), "provider_timeout"),
            (ValueError(SECRET), "unexpected_error"),
        )
        for error, expected in cases:
            with self.subTest(category=expected):
                self.assertEqual(worker.failure_category(error), expected)

    def test_runner_failure_logs_safe_phase_and_still_fails_job(self) -> None:
        for error in (
            RecommendationError("recommendation_model_timeout"),
            RecommendationError(SECRET),
            SQLAlchemyError(SECRET),
            TimeoutError(SECRET),
            ValueError(SECRET),
        ):
            with self.subTest(category=worker.failure_category(error)):
                factory = MagicMock()
                runner = MagicMock()
                claim = SimpleNamespace(id=JOB_ID, input_json={"answers": {}})

                def fail_with_phase(
                    snapshot: JsonObject, report_phase: PhaseReporter
                ) -> None:
                    report_phase("assessing")
                    raise error

                runner.run.side_effect = fail_with_phase
                with (
                    patch.object(worker.jobs, "claim", return_value=claim),
                    patch.object(worker.jobs, "owns", return_value=SimpleNamespace()),
                    patch.object(worker.jobs, "fail") as fail,
                    patch.object(worker.jobs, "complete") as complete,
                    self.assertLogs(worker.LOGGER, level="WARNING") as logs,
                ):
                    self.assertTrue(worker._run_once(factory, runner))
                fail.assert_called_once()
                self.assertEqual(fail.call_args.kwargs["code"], "recommendation_failed")
                complete.assert_not_called()
                self.assertEqual(len(logs.records), 1)
                record = logs.records[0]
                self.assertEqual(
                    record.getMessage(),
                    f"recommendation_failed job_id={JOB_ID} phase=assessing "
                    f"category={worker.failure_category(error)}",
                )
                self.assertIsNone(record.exc_info)
                self.assertIsNone(record.stack_info)
                self.assertNotIn(SECRET, str(record.__dict__))

    def test_invalid_phase_is_not_logged_as_diagnostic_context(self) -> None:
        factory = MagicMock()
        runner = MagicMock()
        claim = SimpleNamespace(id=JOB_ID, input_json={"answers": {}})

        def invalid_phase(snapshot: JsonObject, report_phase: PhaseReporter) -> None:
            report_phase(SECRET)

        runner.run.side_effect = invalid_phase
        with (
            patch.object(worker.jobs, "claim", return_value=claim),
            patch.object(worker.jobs, "fail") as fail,
            self.assertLogs(worker.LOGGER, level="WARNING") as logs,
        ):
            self.assertTrue(worker._run_once(factory, runner))
        self.assertEqual(fail.call_args.kwargs["code"], "recommendation_failed")
        self.assertIn("phase=freezing category=unexpected_error", logs.output[0])
        self.assertNotIn(SECRET, logs.output[0])

    def test_invalid_result_still_fails_without_persisting_private_output(self) -> None:
        factory = MagicMock()
        runner = MagicMock()
        runner.run.return_value = {"private_output": SECRET}
        claim = SimpleNamespace(id=JOB_ID, input_json={"answers": {}})
        with (
            patch.object(worker.jobs, "claim", return_value=claim),
            patch.object(worker.jobs, "fail") as fail,
            patch.object(worker.jobs, "complete") as complete,
            self.assertLogs(worker.LOGGER, level="WARNING") as logs,
        ):
            self.assertTrue(worker._run_once(factory, runner))
        self.assertEqual(fail.call_args.kwargs["code"], "recommendation_failed")
        complete.assert_not_called()
        self.assertIn("category=invalid_recommendation_result", logs.output[0])
        self.assertNotIn(SECRET, logs.output[0])


if __name__ == "__main__":
    unittest.main()
