import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch_fixture import package, refresh, rewrite_row
from batches import coverage, read_manifest, validate_provenance


class BatchContractTests(unittest.TestCase):
    def test_coverage_is_explicit_exact_and_work24_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, digest = package(Path(tmp) / "valid", {}, "a" * 64, ["A"], covered=["A", "B"], mode="full")
            self.assertEqual(coverage(read_manifest(Path(tmp) / "valid", digest)), ("a" * 64, "full", [("A", "01"), ("B", "01")]))
            for changes in [{"source_system": "kmooc"}, {"coverage_mode": "all"}, {"offering_keys": []},
                            {"offering_keys": [["A", "01"], ["A", "01"]]}, {"offering_keys": [["A"]]}]:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    coverage({**manifest, "batch": {**manifest["batch"], **changes}})

    def test_manifest_requires_the_operator_supplied_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "valid"
            _manifest, digest = package(root, {}, "a" * 64, ["A"])
            self.assertEqual(read_manifest(root, digest)["counts"]["courses"], 1)
            (root / "manifest.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
                read_manifest(root, digest)

    def test_source_provenance_is_explicit_and_never_inferred(self):
        validate_provenance([{"source_hash": None, "collected_at": None}])
        validate_provenance([{"source_hash": "a" * 64, "collected_at": "2026-10-30T10:00:00+09:00"}])
        for course in [{}, {"source_hash": None}, {"source_hash": "invalid", "collected_at": None},
                       {"source_hash": None, "collected_at": "2026-11-01"}]:
            with self.subTest(course=course), self.assertRaises(ValueError):
                validate_provenance([course])

    def test_dynamic_counts_do_not_weaken_pinned_bootstrap(self):
        from artifacts import load_artifacts
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "valid"
            manifest, _digest = package(root, {}, "a" * 64, ["A"])
            self.assertEqual(len(load_artifacts(root, pinned=False)[1]), 1)
            with self.assertRaisesRegex(ValueError, "row_count_mismatch"):
                load_artifacts(root)
            (root / "courses.jsonl").write_text("")
            refresh(root, manifest)
            with self.assertRaisesRegex(ValueError, "empty_catalog_batch"):
                load_artifacts(root, pinned=False)

    def test_checksum_valid_packages_reject_required_empty_metadata(self):
        from artifacts import load_artifacts
        cases = [("units.jsonl", field, "") for field in ("unit_name", "unit_type", "embed_text")]
        cases += [("courses.jsonl", "course_name", value) for value in ("", " \t\n", None, 0)]
        for artifact, field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "invalid"
                manifest, _digest = package(root, {}, "a" * 64, ["A"])
                rewrite_row(root, manifest, artifact, {field: value})
                with self.assertRaisesRegex(ValueError, "invalid_catalog_metadata"):
                    load_artifacts(root, pinned=False)

    def test_checksum_valid_packages_reject_other_unreadable_unit_metadata(self):
        from artifacts import load_artifacts
        cases = [("hours", value) for value in (float("inf"), float("nan"), -1, True)]
        cases += [("source", 1), ("ncs_unit_code", 1), ("unit_name", None), ("unit_type", None)]
        for field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "invalid"
                manifest, _digest = package(root, {}, "a" * 64, ["A"])
                rewrite_row(root, manifest, "units.jsonl", {field: value})
                with self.assertRaisesRegex(ValueError, "invalid_catalog_metadata"):
                    load_artifacts(root, pinned=False)

    def test_validation_preserves_exact_strings_and_optional_unknowns(self):
        from artifacts import load_artifacts
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "valid"
            manifest, _digest = package(root, {}, "a" * 64, [" A "])
            updates = {"unit_name": " ", "unit_type": " ", "embed_text": " \t\n",
                       "source": "", "ncs_unit_code": "", "hours": 0}
            rewrite_row(root, manifest, "units.jsonl", updates)
            rewrite_row(root, manifest, "courses.jsonl", {"course_name": " Title ", "institution": None, "url": None})
            _manifest, courses, units, _vectors, _query = load_artifacts(root, pinned=False)
            self.assertEqual(courses[0]["course_id"], " A ")
            self.assertEqual(courses[0]["tme"], "01")
            self.assertEqual((units[0]["course_id"], units[0]["tme"]), (" A ", "01"))
            self.assertEqual(courses[0]["course_name"], " Title ")
            self.assertEqual({field: units[0][field] for field in updates}, updates)
