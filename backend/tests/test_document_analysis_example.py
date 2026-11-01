"""Tests for the opt-in synthetic demonstration; no model service required."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.ai.document_analysis import (
    DocumentAnalysis,
    DocumentAnalysisError,
    DocumentChunkInput,
    DocumentProposal,
)
from app.documents.routes import extract_file

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_document_analysis.py"
spec = importlib.util.spec_from_file_location("document_analysis_demo", SCRIPT)
assert spec is not None and spec.loader is not None
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DocumentAnalysisExampleTests(unittest.TestCase):
    def test_pdf_fixture_extracts_both_expected_pages(self):
        extracted = extract_file(demo.synthetic_pdf(), "pdf")
        self.assertEqual(extracted["status"], "queued")
        self.assertEqual(tuple(text for text, _ in extracted["chunks"]), demo.PAGE_TEXT)
        self.assertTrue(
            all(
                locator.startswith(f"pdf:page:{index}:")
                for index, (_, locator) in enumerate(extracted["chunks"], 1)
            )
        )

    def test_checks_require_both_pages_and_expected_states_without_printing_content(
        self,
    ):
        chunks = tuple(
            DocumentChunkInput(id, text)
            for id, text in zip(demo.CHUNK_IDS, demo.PAGE_TEXT, strict=True)
        )
        proposals = tuple(
            DocumentProposal(demo.DOCUMENT_ID, "resume", chunk, term, state, term)
            for chunk, term, state in (
                (demo.CHUNK_IDS[0], "Python", "have"),
                (demo.CHUNK_IDS[0], "Linux", "have"),
                (demo.CHUNK_IDS[1], "SIEM", "want_to_learn"),
                (demo.CHUNK_IDS[1], "incident response", "want_to_learn"),
            )
        )
        locators = ("pdf:page:1:chars:0:41", "pdf:page:2:chars:0:43")
        passed = demo.evaluate(
            DocumentAnalysis(proposals, demo.CHUNK_IDS), chunks, locators
        )
        self.assertEqual(passed["status"], "passed")
        self.assertNotIn("Python", json.dumps(passed))
        self.assertNotIn("incident response", json.dumps(passed))
        self.assertEqual(
            demo.evaluate(
                DocumentAnalysis(proposals[:-1], demo.CHUNK_IDS), chunks, locators
            )["status"],
            "failed",
        )
        self.assertEqual(
            demo.evaluate(
                DocumentAnalysis(proposals, (demo.CHUNK_IDS[0],)), chunks, locators
            )["status"],
            "failed",
        )
        wrong = proposals[:-1] + (
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[0],
                "incident response",
                "want_to_learn",
                "incident response",
            ),
        )
        self.assertEqual(
            demo.evaluate(DocumentAnalysis(wrong, demo.CHUNK_IDS), chunks, locators)[
                "status"
            ],
            "failed",
        )

    def test_generic_labels_and_wrong_state_fail_even_with_full_source_quotes(self):
        chunks = tuple(
            DocumentChunkInput(id, text)
            for id, text in zip(demo.CHUNK_IDS, demo.PAGE_TEXT, strict=True)
        )
        locators = ("pdf:page:1:chars:0:41", "pdf:page:2:chars:0:43")
        generic = (
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[0],
                "I",
                "have",
                demo.PAGE_TEXT[0],
            ),
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[1],
                "I",
                "want_to_learn",
                demo.PAGE_TEXT[1],
            ),
        )
        self.assertEqual(
            demo.evaluate(DocumentAnalysis(generic, demo.CHUNK_IDS), chunks, locators)[
                "status"
            ],
            "failed",
        )
        wrong = (
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[0],
                "Python",
                "want_to_learn",
                demo.PAGE_TEXT[0],
            ),
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[0],
                "Linux",
                "have",
                demo.PAGE_TEXT[0],
            ),
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[1],
                "SIEM",
                "want_to_learn",
                demo.PAGE_TEXT[1],
            ),
            DocumentProposal(
                demo.DOCUMENT_ID,
                "resume",
                demo.CHUNK_IDS[1],
                "incident response",
                "want_to_learn",
                demo.PAGE_TEXT[1],
            ),
        )
        self.assertEqual(
            demo.evaluate(DocumentAnalysis(wrong, demo.CHUNK_IDS), chunks, locators)[
                "status"
            ],
            "failed",
        )

    def test_bad_database_configuration_is_never_exposed(self):
        env = os.environ.copy()
        env["DATABASE_URL"] = "not-a-url-sensitive-secret"
        for args, expected in (([], 2), (["--live"], 1)):
            response = subprocess.run(
                [sys.executable, str(SCRIPT), *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(response.returncode, expected)
            self.assertNotIn("sensitive-secret", response.stdout + response.stderr)
            self.assertEqual(response.stderr, "")
            self.assertIn("failure_code", json.loads(response.stdout))

    def test_example_reports_chunk_limit_as_safe_failure(self):
        captured = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), "--live"]),
            patch.object(
                demo, "run", side_effect=DocumentAnalysisError("document_chunk_limit")
            ),
            redirect_stdout(captured),
        ):
            self.assertEqual(demo.main(), 1)
        result = json.loads(captured.getvalue())
        self.assertEqual(result["failure_code"], "document_chunk_limit")
        self.assertEqual(result["processed_chunk_count"], 0)

    def test_no_live_flag_cannot_call_model(self):
        env = os.environ.copy()
        env["QUESTION_MODEL_BASE_URL"] = "http://127.0.0.1:1"
        env["QUESTION_MODEL_ID"] = "synthetic"
        response = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        self.assertEqual(response.returncode, 2)
        self.assertEqual(
            json.loads(response.stdout)["failure_code"], "explicit_live_opt_in_required"
        )
        self.assertEqual(response.stderr, "")


if __name__ == "__main__":
    unittest.main()
