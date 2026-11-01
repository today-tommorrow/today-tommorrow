"""Synthetic contract checks for exact identities, supplementation and vector admission."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

CATALOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CATALOG))

from artifacts import load_artifacts  # noqa: E402
from records_fixture import normalized, precomputed, record  # noqa: E402
from work24_records import normalize, write_package  # noqa: E402


class Work24RecordsTests(unittest.TestCase):
    def test_order_independent_exact_ids_and_provenance(self) -> None:
        rows = [record(" 00A ", cohort="001", extra_optional={"a": 1}), record("a", cohort="01")]
        first = normalized(rows)
        self.assertEqual(first, normalized(list(reversed(rows))))
        self.assertEqual([(c["course_id"], c["tme"]) for c in first["courses"]], [(" 00A ", "001"), ("a", "01")])
        self.assertEqual(first["report"]["unknown_fields"], ["extra_optional"])
        self.assertEqual(first["courses"][0]["work24"]["raw_json"][0]["record"], rows[0])
        changed_rows = [{**row, "source_excel_row": 999} for row in rows]
        self.assertEqual([(c["course_id"], c["tme"]) for c in normalized(changed_rows)["courses"]], [(" 00A ", "001"), ("a", "01")])

    def test_missing_ids_report_internal_candidates_without_publication(self) -> None:
        rows = [record(course_id=None), record(course_id=None, offering_id="source-offering", source_excel_row=42)]
        result = normalized(rows)
        self.assertEqual(result["courses"], [])
        self.assertEqual(len(result["report"]["duplicate_candidates"]), 1)
        self.assertTrue(all(row["raw_json"]["course_id"] is None for row in result["report"]["quarantined"]))
        self.assertEqual(result, normalized(list(reversed(rows))))
        same_observation = normalized([record(course_id=None, source_excel_row=99)])
        self.assertIn(same_observation["report"]["quarantined"][0]["internal_id"],
                      {row["internal_id"] for row in result["report"]["quarantined"]})
        enriched = normalized([record(course_id=None, eligibility="New fact")])
        self.assertNotEqual(same_observation["report"]["quarantined"][0]["internal_id"], enriched["report"]["quarantined"][0]["internal_id"])

    def test_null_only_supplement_preserves_zero_na_raw_and_field_sources(self) -> None:
        first = normalized([record()])
        previous = {"dataset_id": "1" * 64, "courses": first["courses"]}
        result = normalized([record(region="Seoul", eligibility="Observed requirement", training_hours=None,
                                    tuition_krw=None, listed_self_pay_krw=None)], parent="1" * 64,
                            previous=previous, revision="second")
        course = result["courses"][0]
        self.assertEqual((course["training_fee"], course["training_hours"], course["displayed_self_pay"]), (0, 0, "N/A"))
        self.assertEqual(course["metadata"]["훈련대상요건"], "Observed requirement")
        self.assertEqual(course["source_hash"], first["courses"][0]["source_hash"])
        self.assertEqual(course["work24"]["field_provenance"]["tuition_krw"], first["courses"][0]["work24"]["field_provenance"]["tuition_krw"])
        self.assertNotEqual(course["work24"]["field_provenance"]["region"]["source_hash"], course["source_hash"])
        self.assertEqual(len(course["work24"]["raw_json"]), 2)
        self.assertEqual(result["report"]["personal_price"], "PERSONAL_PRICE_UNKNOWN")
        self.assertEqual(result["report"]["supplemented"][0]["filled_fields"], ["eligibility", "region"])
        self.assertEqual(previous["courses"], first["courses"])

    def test_known_conflicts_quarantine_whole_offering(self) -> None:
        previous = {"dataset_id": "1" * 64, "courses": normalized([record()])["courses"]}
        for changes in ({"listed_self_pay_krw": 0}, {"tuition_krw": 10}, {"curriculum": ["Changed content"]}):
            with self.subTest(changes=changes):
                result = normalized([record(**changes), record("synthetic-B")], parent="1" * 64, previous=previous)
                self.assertEqual([c["course_id"] for c in result["courses"]], ["synthetic-B"])
                self.assertEqual(result["report"]["quarantined"][0]["reason"], "conflicting_offering")
        result = normalized([record(), record(region="Seoul"), record("synthetic-B")])
        self.assertEqual([c["course_id"] for c in result["courses"]], ["synthetic-B"])
        result = normalized([record(), record(tme="01", cohort="02")])
        self.assertEqual(result["courses"], [])

    def test_empty_malformed_arrays_and_malformed_values(self) -> None:
        for payload in ({}, {"records": []}, {"records": {}}, {"records": [None]}):
            with self.assertRaisesRegex(ValueError, "invalid_records_array"):
                normalize(payload, source_hash=None, collected_at=None, parent_dataset_id="0" * 64)
        for changes in ({"tuition_krw": True}, {"start_date": "2026-02-30"}, {"curriculum": [{}]}, {"training_hours": -1}, {"cohort": 1}):
            with self.subTest(changes=changes):
                self.assertTrue(normalized([record(**changes)])["report"]["quarantined"])
        with self.assertRaisesRegex(ValueError, "previous_parent_mismatch"):
            normalized([record()], previous={"dataset_id": "1" * 64, "courses": []})

    def test_oversized_json_numbers_quarantine_only_their_offering(self) -> None:
        for field in ("training_hours", "tuition_krw", "listed_self_pay_krw"):
            for digits in ("1" + "0" * 400, "-1" + "0" * 400):
                with self.subTest(field=field, negative=digits.startswith("-")):
                    rows = json.loads(json.dumps([record(**{field: int(digits)}), record("valid")]))
                    result = normalized(rows)
                    self.assertEqual([course["course_id"] for course in result["courses"]], ["valid"])
                    quarantined = result["report"]["quarantined"]
                    self.assertTrue(any(row["reason"] == "invalid_record_number" for row in quarantined))
                    self.assertTrue(all(row["offering_key"] == ["synthetic-A", "01"] for row in quarantined))
                    self.assertEqual(result["parent_records"], [{"offering_key": ["valid", "01"], "metadata_sha256": None}])

    def test_legacy_parent_is_preserved_as_guard_but_cannot_be_supplemented(self) -> None:
        legacy = {"course_id": "legacy", "tme": "01", "course_name": "Legacy", "training_fee": 123}
        result = normalized([record("legacy"), record("new")], parent="1" * 64,
                            previous={"dataset_id": "1" * 64, "courses": [legacy]})
        self.assertEqual([course["course_id"] for course in result["courses"]], ["new"])
        self.assertEqual(result["report"]["quarantined"][0]["reason"], "legacy_parent_requires_mapping")
        self.assertEqual(result["parent_records"], [{"offering_key": ["new", "01"], "metadata_sha256": None}])

    def test_unrecognized_parent_history_quarantines_only_its_offering(self) -> None:
        adapter = normalized([record("legacy")])["courses"][0]["work24"]
        histories = [("unrelated", {"optional_source_metadata": True}), ("empty", {})]
        for field, malformed in (("values", []), ("raw_json", {}), ("raw_json", [None]), ("sources", {}),
                                 ("sources", []), ("sources", [None]), ("field_provenance", []),
                                 ("field_provenance", {"course_name": None})):
            history = copy.deepcopy(adapter)
            history[field] = malformed
            histories.append((f"malformed_{field}_{type(malformed).__name__}", history))
        for field in ("values", "raw_json", "sources", "field_provenance"):
            history = copy.deepcopy(adapter)
            del history[field]
            histories.append((f"missing_{field}", history))
        for label, history in histories:
            with self.subTest(history=label):
                legacy = {"course_id": "legacy", "tme": "01", "work24": history}
                result = normalized([record("legacy"), record("new")], parent="1" * 64,
                                    previous={"dataset_id": "1" * 64, "courses": [legacy]})
                self.assertEqual([course["course_id"] for course in result["courses"]], ["new"])
                self.assertEqual(result["report"]["quarantined"][0]["reason"], "legacy_parent_requires_mapping")
                self.assertEqual(result["parent_records"], [{"offering_key": ["new", "01"], "metadata_sha256": None}])

    def test_package_reorders_vectors_by_key_and_excludes_quarantined_coverage(self) -> None:
        source = normalized([record("synthetic-B"), record("synthetic-A", curriculum=["First", "Second"])])
        result = normalized([record("synthetic-B"), record(), record(region="conflict")])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            supplied = precomputed(root / "supplied", source)
            before = load_artifacts(supplied, pinned=False)
            manifest = write_package(result, supplied, root / "output", created_on="2026-11-01")
            after = load_artifacts(root / "output", pinned=False)
            self.assertEqual(manifest["batch"]["offering_keys"], [["synthetic-B", "01"]])
            self.assertEqual(manifest["batch"]["coverage_mode"], "partial")
            pairs = {unit["unit_key"]: (unit, vector) for unit, vector in zip(before[2], before[3], strict=True)}
            for unit, vector in zip(after[2], after[3], strict=True):
                original, original_vector = pairs[unit["unit_key"]]
                self.assertTrue(np.array_equal(vector, original_vector))
                self.assertEqual(unit["provenance"], original["provenance"])
            changed = copy.deepcopy(result)
            changed["courses"][0]["curriculum"] = ["No matching precomputed vector"]
            with self.assertRaisesRegex(ValueError, "missing_precomputed_vectors"):
                write_package(changed, supplied, root / "rejected", created_on="2026-11-01")
            self.assertFalse((root / "rejected").exists())

    def test_cli_normalization_keeps_explicit_unknown_collection_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "records.json").write_text(json.dumps({"records": [record()]}))
            completed = subprocess.run([sys.executable, str(CATALOG / "work24_records.py"), str(root / "records.json"),
                                        "--parent-dataset-id", "0" * 64, "--normalized-output", str(root / "normalized.json")],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads((root / "normalized.json").read_text())
            self.assertIsNone(result["courses"][0]["collected_at"])
            self.assertIsNone(result["courses"][0]["source_hash"])


if __name__ == "__main__":
    unittest.main()
