"""Synthetic GPU-free evaluation tests: these never establish BGE relevance."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from app.catalog.contracts import (
    CatalogError,
    CatalogSnapshot,
    CourseCandidate,
    SearchResult,
)
from app.catalog.evaluate import main
from app.catalog.evaluation import (
    EvaluationError,
    diagnose,
    evaluate_dataset,
    load_dataset,
    render_report,
)
from app.catalog.policy import course_id, offering_id
from app.catalog.service import SearchService

AS_OF = date(2026, 11, 1)
SNAPSHOT = CatalogSnapshot("a" * 64, "b" * 64, "synthetic-revision", "c" * 64, AS_OF)
VECTOR = (1.0,) + (0.0,) * 1023


def case(identifier: str = "known", *, status: str = "judged", relevant: list[str] | None = None) -> dict[str, object]:
    return {"id": identifier, "query": "  synthetic public goal\n ", "as_of": str(AS_OF),
            "filters": {"start_from": "2026-11-01", "budget_krw": 0},
            "judgment": {"status": status, "relevant_course_ids": ["A", "B"] if relevant is None else relevant,
                         "irrelevant_course_ids": ["X"] if status == "judged" else [],
                         "reason": "Precommitted synthetic judgment; not actual BGE evidence."}}


def dataset_value(cases: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"schema_version": 1, "id": "synthetic", "version": "1", "description": "GPU-free test only",
            "catalog": {"profile_id": SNAPSHOT.profile_id, "dataset_id": SNAPSHOT.dataset_id,
                        "revision": SNAPSHOT.revision},
            "provenance": {"synthetic": True}, "cases": [case()] if cases is None else cases}


def result(*identifiers: str, empty_reason: str | None = None) -> SearchResult:
    candidates = tuple(CourseCandidate(
        course_id(key), offering_id(key, "01"), "work24", key, "01", .5, {}, (), (),
    ) for key in identifiers)
    return SearchResult(SNAPSHOT, AS_OF, candidates, 4, 4, {}, (), empty_reason)


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "dataset.json"

    def load(self, value: dict[str, object] | None = None):
        self.path.write_text(json.dumps(dataset_value() if value is None else value), encoding="utf-8")
        return load_dataset(self.path)

    def run_evaluation(self, dataset, results: list[SearchResult | Exception]):
        repository, provider = Mock(), Mock()
        repository.prepare.return_value = SNAPSHOT
        repository.search.side_effect = results
        provider.embed.return_value = VECTOR
        report = evaluate_dataset(
            dataset, SearchService(repository, provider), SNAPSHOT, cutoffs=(1, 3),
            limit=3, evidence_limit=2, execution_mode="synthetic",
            environment={"validation": "synthetic; no GPU or database"},
        )
        return report, repository, provider

    def test_hash_exact_bytes_and_source_ids_preserved(self) -> None:
        value = dataset_value([case(relevant=["A", "01", "1"])])
        dataset = self.load(value)
        self.assertEqual(dataset.sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(dataset.cases[0].relevant, ("A", "01", "1"))
        self.assertEqual(dataset.cases[0].filters.start_from, AS_OF)

    def test_curated_work24_fixture_loads_without_invented_negative_or_empty_labels(self) -> None:
        dataset = load_dataset(Path(__file__).parent / "fixtures/work24-search-evaluation-v1.json")
        self.assertEqual(dataset.id, "work24-search-evaluation")
        self.assertTrue(all(case.as_of == AS_OF for case in dataset.cases))
        preconfirmed = {course["id"] for course in dataset.provenance["expected_courses"]}
        self.assertTrue(all(set(case.relevant) <= preconfirmed for case in dataset.cases))
        self.assertTrue(all(not case.irrelevant and case.expected_empty is None for case in dataset.cases))
        for case in dataset.cases:
            if case.id.startswith(("broad-", "similar-", "empty-result-", "unknown-")):
                self.assertEqual(case.judgment_status, "unjudged")
                self.assertFalse(case.metric_eligible)
            if case.id.startswith("exact-title-"):
                self.assertTrue(case.metric_eligible)
        self.assertTrue(any(case.id.startswith("korean-") for case in dataset.cases))
        self.assertTrue(any(case.id.startswith("mixed-") for case in dataset.cases))

    def test_missing_false_positives_unjudged_duplicates_and_rank_are_separate(self) -> None:
        dataset = self.load()
        row = diagnose(dataset.cases[0], result("X", "A", "A", "U"), (1, 4))
        self.assertEqual(row["false_positive_course_ids"], ["X"])
        self.assertEqual(row["unjudged_course_ids"], ["U"])
        self.assertEqual(row["duplicate_course_ids"], {"A": 2})
        self.assertEqual(row["metrics_at_k"]["4"], {
            "expected_set_recall": .5, "reciprocal_rank": .5,
            "missing_expected_course_ids": ["B"],
        })
        self.assertEqual(row["metrics_at_k"]["1"]["reciprocal_rank"], 0)

    def test_real_service_boundary_is_called_verbatim_with_pinned_snapshot(self) -> None:
        dataset = self.load()
        report, repository, provider = self.run_evaluation(dataset, [result("A")])
        repository.prepare.assert_called_once_with(SNAPSHOT)
        provider.embed.assert_called_once_with(dataset.cases[0].query, revision=SNAPSHOT.revision)
        self.assertEqual(repository.search.call_args.args[:2], (VECTOR, SNAPSHOT))
        self.assertEqual(repository.search.call_args.kwargs["evidence_limit"], 2)
        self.assertFalse(report["actual_bge_results"])
        self.assertEqual(report["snapshot"]["manifest_sha256"], SNAPSHOT.manifest_sha256)
        self.assertEqual(report["summary"]["successful_metric_eligible_cases"], 1)
        self.assertEqual(report["summary"]["metrics_at_k"]["3"]["mrr"], 1)
        self.assertNotIn("embed_text", json.dumps(report))
        self.assertNotIn("embedding", json.dumps(report))

    def test_all_unjudged_and_no_positive_judgments_produce_null_not_perfect_metrics(self) -> None:
        dataset = self.load(dataset_value([case("u", status="unjudged", relevant=[]), case("negative", relevant=[])]))
        report, _, _ = self.run_evaluation(dataset, [result("U"), result("X")])
        self.assertEqual(report["summary"]["successful_metric_eligible_cases"], 0)
        self.assertEqual(report["summary"]["metrics_at_k"]["3"], {
            "macro_expected_set_recall": None, "mrr": None, "cases_with_missing_expected": 0,
        })
        self.assertEqual(report["cases"][0]["false_positive_course_ids"], [])
        self.assertEqual(report["cases"][1]["false_positive_course_ids"], ["X"])

    def test_failed_search_is_not_empty_and_does_not_hide_requested_denominator(self) -> None:
        dataset = self.load(dataset_value([case("failed"), case("success")]))
        report, _, _ = self.run_evaluation(dataset, [CatalogError("driver text must not leak"), result()])
        self.assertEqual(report["summary"]["requested_metric_eligible_cases"], 2)
        self.assertEqual(report["summary"]["successful_metric_eligible_cases"], 1)
        self.assertEqual(report["summary"]["failed_cases"], 1)
        self.assertEqual(report["summary"]["empty_cases"], 1)
        self.assertEqual(report["summary"]["unexpected_empty_cases"], 1)
        self.assertNotIn("empty", report["cases"][0])
        self.assertNotIn("driver text", json.dumps(report))
        self.assertEqual(report["summary"]["metrics_at_k"]["3"]["mrr"], 0)

    def test_expected_empty_mismatch_and_repository_empty_reason_are_independent(self) -> None:
        value = case(relevant=[])
        value["expected_empty"] = True
        dataset = self.load(dataset_value([value]))
        row = diagnose(dataset.cases[0], result(empty_reason="all_filtered"), (1,))
        self.assertTrue(row["empty_expectation_matches"])
        self.assertEqual(row["empty_reason"], "all_filtered")
        self.assertFalse(diagnose(dataset.cases[0], result("U"), (1,))["empty_expectation_matches"])

    def test_snapshot_mismatch_and_invalid_cutoffs_fail_before_search(self) -> None:
        dataset, service = self.load(), Mock()
        for snapshot, cutoffs in [(replace(SNAPSHOT, revision="changed"), (1,)),
                                   (SNAPSHOT, (0,)), (SNAPSHOT, (4,)), (SNAPSHOT, (1, 1)),
                                   (SNAPSHOT, (True,)), (SNAPSHOT, ())]:
            with self.subTest(cutoffs=cutoffs), self.assertRaises(EvaluationError):
                evaluate_dataset(dataset, service, snapshot, cutoffs=cutoffs, limit=3,
                                 evidence_limit=2, execution_mode="synthetic", environment={})
        service.search.assert_not_called()

    def test_changed_snapshot_or_retrieval_mode_is_rejected(self) -> None:
        for changed in [replace(result("A"), snapshot=replace(SNAPSHOT, manifest_sha256="d" * 64)),
                        replace(result("A"), approximate=True)]:
            with self.subTest(changed=changed), self.assertRaises(EvaluationError):
                self.run_evaluation(self.load(), [changed])

    def test_contradictory_duplicate_and_unjudged_labels_are_rejected(self) -> None:
        for relevant, status in [(["X"], "judged"), (["A", "A"], "judged"), (["A"], "unjudged")]:
            with self.subTest(relevant=relevant, status=status), self.assertRaises(EvaluationError):
                self.load(dataset_value([case(relevant=relevant, status=status)]))
        with self.assertRaises(EvaluationError):
            self.load(dataset_value([case(), case()]))
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(EvaluationError, "duplicate_evaluation_json_key"):
            load_dataset(self.path)

    def test_human_report_covers_mode_hash_denominators_and_case_diagnostics(self) -> None:
        report, _, _ = self.run_evaluation(self.load(), [result("X", "A", "A")])
        rendered = render_report(report)
        for text in ["synthetic", "Actual BGE results: False", "SHA256", "successful_metric_eligible_cases", "False positives", "Duplicates", "not exhaustive"]:
            self.assertIn(text, rendered)

    def test_help_never_connects_to_model_or_database(self) -> None:
        with redirect_stdout(io.StringIO()), patch("sys.argv", ["evaluate", "--help"]), patch("app.catalog.evaluate.create_engine") as engine:
            with self.assertRaises(SystemExit) as stopped:
                main()
        self.assertEqual(stopped.exception.code, 0)
        engine.assert_not_called()

    def test_cli_rejects_bad_cutoffs_or_output_reuse_before_connecting(self) -> None:
        self.load()
        for cutoff, output in [("0", self.path.parent / "new.json"), ("1", self.path)]:
            arguments = ["evaluate", "--dataset", str(self.path), "--profile-id", SNAPSHOT.profile_id,
                         "--dataset-id", SNAPSHOT.dataset_id, "--base-url", "http://unused.invalid",
                         "--timeout", "5", "--k", cutoff, "--output-json", str(output),
                         "--output-report", str(self.path.parent / "report.md")]
            with self.subTest(cutoff=cutoff), redirect_stdout(io.StringIO()), patch("sys.argv", arguments), patch("app.catalog.evaluate.create_engine") as engine:
                self.assertEqual(main(), 1)
            engine.assert_not_called()

    def test_no_successful_live_calls_does_not_claim_actual_bge_results(self) -> None:
        repository, provider = Mock(), Mock()
        repository.prepare.return_value = SNAPSHOT
        provider.embed.side_effect = CatalogError("failed")
        report = evaluate_dataset(
            self.load(), SearchService(repository, provider), SNAPSHOT, cutoffs=(1,),
            limit=3, evidence_limit=2, execution_mode="live_bge_cuda", environment={},
        )
        self.assertFalse(report["actual_bge_results"])
        self.assertEqual(report["cases"][0]["error_kind"], "catalog")


if __name__ == "__main__":
    unittest.main()
