"""Synthetic post-hoc review contracts, without models, network or database."""

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.catalog.review import MAX_BYTES, main


def result_value() -> dict[str, object]:
    snapshot = {"profile_id": "a" * 64, "dataset_id": "b" * 64, "revision": "test",
                "manifest_sha256": "c" * 64, "snapshot_created_on": "2026-11-01"}
    return {
        "report_schema_version": 1, "execution_mode": "synthetic",
        "evaluation_dataset": {"id": "test", "version": "1", "sha256": "d" * 64,
                               "catalog": {key: snapshot[key] for key in ("profile_id", "dataset_id", "revision")}},
        "snapshot": snapshot, "search_settings": {"cutoffs": [1, 3]},
        "cases": [{"id": "case", "query": "public query", "as_of": "2026-11-01", "filters": {},
                   "status": "ok", "returned_count": 3, "candidates": [
                       {"source_course_id": "B", "rank": 1, "similarity": .9, "judgment": "unjudged"},
                       {"source_course_id": "01", "rank": 2, "similarity": .8, "judgment": "unjudged"},
                       {"source_course_id": "B", "rank": 3, "similarity": .7, "judgment": "unjudged"}]},
                  {"id": "empty", "query": "empty public query", "as_of": "2026-11-01", "filters": {},
                   "status": "ok", "returned_count": 0, "candidates": []},
                  {"id": "failed", "query": "failed public query", "as_of": "2026-11-01", "filters": {},
                   "status": "error"}],
    }


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.result = self.root / "result.json"
        self.result.write_text(json.dumps(result_value()), encoding="utf-8")
        self.original = self.result.read_bytes()
        self.packet = self.root / "packet.json"
        self.review = self.root / "review.json"
        self.adjudication = self.root / "adjudication.json"

    def call(self, *arguments: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["review", *arguments]), redirect_stdout(stdout):
            status = main()
        self.assertEqual(self.result.read_bytes(), self.original)
        return status, stdout.getvalue()

    def export(self) -> None:
        status, message = self.call("export", "--result", str(self.result), "--output-json", str(self.packet),
                                    "--output-review", str(self.review), "--output-report", str(self.root / "packet.md"))
        self.assertEqual(status, 0, message)

    def submission(self) -> dict[str, object]:
        self.export()
        submission = json.loads(self.review.read_bytes())
        for row in submission["judgments"]:
            row.update(reviewer="reviewer-a", reason="Insufficient evidence; remains unjudged.")
        return submission

    def write_submission(self, submission: dict[str, object]) -> None:
        self.review.write_text(json.dumps(submission), encoding="utf-8")

    def import_review(self) -> tuple[int, str]:
        return self.call("import", "--result", str(self.result), "--packet", str(self.packet),
                         "--review", str(self.review), "--output-json", str(self.adjudication),
                         "--output-report", str(self.root / "import.md"))

    def report(self, suffix: str = "") -> tuple[int, str]:
        return self.call("report", "--result", str(self.result), "--packet", str(self.packet),
                         "--review", str(self.review),
                         "--adjudication", str(self.adjudication), "--output-json", str(self.root / f"report{suffix}.json"),
                         "--output-report", str(self.root / f"report{suffix}.md"))

    def test_export_is_deterministic_rank_hidden_exactly_bound_and_deduplicated(self) -> None:
        self.export()
        packet = json.loads(self.packet.read_bytes())
        self.assertEqual([row["source_course_id"] for row in packet["items"]], ["01", "B"])
        self.assertEqual(packet["binding"]["result_sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(packet["binding"]["evaluation_dataset"]["sha256"], "d" * 64)
        for field in ("rank", "similarity", "judgment", "expected_course_ids", "environment"):
            self.assertNotIn(f'"{field}"', self.packet.read_text())
        other = self.root / "other.json"
        status, message = self.call("export", "--result", str(self.result), "--output-json", str(other),
                                    "--output-review", str(self.root / "other-review.json"),
                                    "--output-report", str(self.root / "other.md"))
        self.assertEqual(status, 0, message)
        self.assertEqual(other.read_bytes(), self.packet.read_bytes())

    def test_import_requires_explicit_review_even_for_unjudged(self) -> None:
        self.export()
        status, _ = self.import_review()
        self.assertEqual(status, 1)
        self.assertFalse(self.adjudication.exists())

    def test_round_trip_counts_negative_hits_and_unjudged_separately(self) -> None:
        submission = self.submission()
        submission["judgments"][1].update(label="irrelevant", reason="Reviewed source does not address query.", evidence={
            "actual_source_course_id": "B", "source_sha256": "e" * 64,
            "pointer": "public-units.jsonl record B field curriculum", "excerpt": None})
        self.write_submission(submission)
        self.assertEqual(self.import_review()[0], 0)
        self.assertEqual(self.report()[0], 0)
        report = json.loads((self.root / "report.json").read_bytes())
        self.assertEqual(report["summary"], {"returned_pairs": 2, "judged_pairs": 1, "relevant_pairs": 0,
            "explicit_negative_hits": 1, "unjudged_pairs": 1, "judgment_coverage": .5, "successful_cases": 2, "failed_cases": 1})
        self.assertEqual(report["at_k"]["1"]["explicit_negative_hits"], 1)
        self.assertEqual(report["cases"][0]["duplicate_rows"], 1)
        self.assertIsNone(report["cases"][1]["judgment_coverage"])
        self.assertEqual(report["cases"][2], {"case_id": "failed", "status": "error"})
        self.assertNotIn("precision", report["summary"])
        self.assertNotIn("expected_empty", json.dumps(report))
        self.assertIn("not verified", (self.root / "report.md").read_text())

    def test_missing_unknown_duplicate_conflicting_and_malformed_records_fail_closed(self) -> None:
        original = self.submission()
        mutations = [
            lambda row: row["judgments"].pop(),
            lambda row: row["judgments"][0].update(source_course_id="unknown"),
            lambda row: row["judgments"].append(copy.deepcopy(row["judgments"][0])),
            lambda row: row["judgments"].append({**row["judgments"][0], "label": "relevant"}),
            lambda row: row["judgments"][0].update(label="negative"),
            lambda row: row["judgments"][0].update(label="relevant"),
            lambda row: row["judgments"][0].update(reviewer=""),
            lambda row: row["judgments"][0].update(reason=""),
            lambda row: row["judgments"][0].update(extra="raw source"),
            lambda row: row.update(schema_version=True),
            lambda row: row.update(packet_sha256="e" * 64),
            lambda row: row["binding"]["snapshot"].update(manifest_sha256="e" * 64),
            lambda row: row["binding"]["evaluation_dataset"].update(sha256="e" * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                submission = copy.deepcopy(original)
                mutate(submission)
                self.write_submission(submission)
                self.assertEqual(self.import_review()[0], 1)
                self.assertFalse(self.adjudication.exists())

    def test_evidence_requires_matching_course_hash_and_bounded_location(self) -> None:
        original = self.submission()
        for evidence in (
            {"actual_source_course_id": "wrong", "source_sha256": "e" * 64, "pointer": "record", "excerpt": None},
            {"actual_source_course_id": "01", "source_sha256": "bad", "pointer": "record", "excerpt": None},
            {"actual_source_course_id": "01", "source_sha256": "e" * 64, "pointer": None, "excerpt": None},
            {"actual_source_course_id": "01", "source_sha256": "e" * 64, "pointer": None, "excerpt": "x" * 501},
        ):
            with self.subTest(evidence=evidence):
                submission = copy.deepcopy(original)
                submission["judgments"][0].update(label="relevant", evidence=evidence)
                self.write_submission(submission)
                self.assertEqual(self.import_review()[0], 1)

    def test_same_semantics_different_packet_bytes_rejected(self) -> None:
        submission = self.submission()
        self.write_submission(submission)
        self.packet.write_bytes(self.packet.read_bytes() + b" ")
        self.assertEqual(self.import_review()[0], 1)

    def test_result_bytes_and_changed_packet_are_rejected(self) -> None:
        submission = self.submission()
        self.write_submission(submission)
        self.original += b" "
        self.result.write_bytes(self.original)
        self.assertEqual(self.import_review()[0], 1)

    def test_report_revalidates_adjudication(self) -> None:
        self.write_submission(self.submission())
        self.assertEqual(self.import_review()[0], 0)
        adjudication = json.loads(self.adjudication.read_bytes())
        adjudication["judgments"].pop()
        self.adjudication.write_text(json.dumps(adjudication))
        self.assertEqual(self.report()[0], 1)

    def test_report_requires_exact_original_submission_and_normalized_adjudication(self) -> None:
        submission = self.submission()
        submission["judgments"][0].update(label="relevant", reason="Relevant source evidence.", evidence={
            "actual_source_course_id": "01", "source_sha256": "e" * 64,
            "pointer": "record 01", "excerpt": "Minimal public evidence"})
        self.write_submission(submission)
        self.assertEqual(self.import_review()[0], 0)
        original = json.loads(self.adjudication.read_bytes())
        mutations = [
            lambda row: row["judgments"][0].update(label="irrelevant"),
            lambda row: row["judgments"][0].update(reviewer="another-reviewer"),
            lambda row: row["judgments"][0].update(reason="Changed rationale."),
            lambda row: row["judgments"][0]["evidence"].update(source_sha256="f" * 64),
            lambda row: row["judgments"][0]["evidence"].update(excerpt="Changed evidence."),
            lambda row: row["judgments"][0]["evidence"].update(pointer="another record"),
            lambda row: row.update(submission_sha256="f" * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(original)
                mutate(changed)
                self.adjudication.write_text(json.dumps(changed), encoding="utf-8")
                self.assertEqual(self.report()[0], 1)
                self.assertFalse((self.root / "report.json").exists())
        self.adjudication.write_text(json.dumps(original), encoding="utf-8")
        self.review.write_bytes(self.review.read_bytes() + b" ")
        self.assertEqual(self.report()[0], 1)

    def test_all_unjudged_is_zero_coverage_without_negative_inference(self) -> None:
        self.write_submission(self.submission())
        self.assertEqual(self.import_review()[0], 0)
        self.assertEqual(self.report()[0], 0)
        summary = json.loads((self.root / "report.json").read_bytes())["summary"]
        self.assertEqual((summary["judgment_coverage"], summary["explicit_negative_hits"], summary["unjudged_pairs"]), (0, 0, 2))

    def test_paths_existing_files_collisions_and_symlinks_are_protected(self) -> None:
        for destination in (self.result, self.root / "missing" / "out.json"):
            status, _ = self.call("export", "--result", str(self.result), "--output-json", str(destination),
                                 "--output-review", str(self.review), "--output-report", str(self.root / "out.md"))
            self.assertEqual(status, 1)
        status, _ = self.call("export", "--result", str(self.result), "--output-json", str(self.packet),
                             "--output-review", str(self.packet), "--output-report", str(self.root / "out.md"))
        self.assertEqual(status, 1)
        self.packet.symlink_to(self.root / "absent.json")
        status, _ = self.call("export", "--result", str(self.result), "--output-json", str(self.packet),
                             "--output-review", str(self.review), "--output-report", str(self.root / "out.md"))
        self.assertEqual(status, 1)
        self.assertFalse(self.review.exists())

    def test_duplicate_keys_nonfinite_and_oversized_json_rejected_without_leaks(self) -> None:
        for raw in (b'{"secret": "PRIVATE", "secret": 1}', b'{"secret": NaN}', b" " * (MAX_BYTES + 1)):
            self.original = raw
            self.result.write_bytes(raw)
            status, message = self.call("export", "--result", str(self.result), "--output-json", str(self.packet),
                                        "--output-review", str(self.review), "--output-report", str(self.root / "out.md"))
            self.assertEqual(status, 1)
            self.assertNotIn("PRIVATE", message)
            self.assertNotIn(str(self.root), message)

    def test_output_expansion_rejected_before_any_artifact_is_created(self) -> None:
        result = result_value()
        result["cases"][0]["query"] = "q" * 4000
        self.original = json.dumps(result).encode()
        self.result.write_bytes(self.original)
        with patch("app.catalog.review.MAX_BYTES", len(self.original)):
            status, message = self.call("export", "--result", str(self.result), "--output-json", str(self.packet),
                                        "--output-review", str(self.review), "--output-report", str(self.root / "out.md"))
        self.assertEqual(status, 1)
        self.assertIn("review_output_too_large", message)
        self.assertFalse(self.packet.exists())
        self.assertFalse(self.review.exists())
        self.assertFalse((self.root / "out.md").exists())

    def test_import_and_help_need_only_standard_library(self) -> None:
        code = "import sys; import app.catalog.review; assert 'sqlalchemy' not in sys.modules; assert 'app.ai.embeddings' not in sys.modules"
        process = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        process = subprocess.run([sys.executable, "-S", "-m", "app.catalog.review", "--help"],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
