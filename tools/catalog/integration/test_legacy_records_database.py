"""Reviewed legacy metadata supplementation in explicitly owned disposable databases."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

CATALOG = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CATALOG), str(CATALOG / "tests")]

from artifacts import extract_verified, file_hash, load_artifacts  # noqa: E402
from batch_fixture import refresh  # noqa: E402
from batches import activate, stage  # noqa: E402
from database import UNIT_FIELDS, vector_array  # noqa: E402
from records_fixture import legacy_precomputed, mapping_for, normalized  # noqa: E402
from work24_records import write_package  # noqa: E402


class LegacyRecordsDatabaseTests(unittest.TestCase):
    def exercise(self, supplied: Path, dsn: str, root: Path) -> None:
        artifacts = load_artifacts(supplied, pinned=False)
        manifest, courses, units, vectors, _query = artifacts
        parent, profile = manifest["dataset_id"], manifest["embedding_profile_id"]
        with psycopg.connect(dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute((CATALOG / "schema.sql").read_text(), prepare=False)
            conn.execute((CATALOG / "snapshot_schema.sql").read_text(), prepare=False)
            self.assertEqual(conn.execute("SELECT count(*) FROM work24_bge.dataset_snapshot").fetchone()[0], 0,
                             "requires a fresh owned disposable database")
            conn.execute("INSERT INTO work24_bge.embedding_profile(profile_id,config) VALUES(%s,%s)",
                         (profile, Jsonb(manifest["embedding_config"])))
            conn.execute("INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES(%s,%s,%s)",
                         (parent, profile, Jsonb(manifest)))
            with conn.cursor().copy("COPY work24_bge.snapshot_course(dataset_id,course_id,tme,metadata,source_dataset_id) FROM STDIN") as copy:
                for course in courses:
                    copy.write_row((parent, course["course_id"], course["tme"], Jsonb(course), parent))
            fields = ",".join(UNIT_FIELDS)
            with conn.cursor().copy(f"COPY work24_bge.snapshot_unit({fields},source_dataset_id) FROM STDIN") as copy:
                for unit, vector in zip(units, vectors, strict=True):
                    values = {**unit, "embedding": vector, "profile_id": profile, "active": True,
                              "provenance": Jsonb(unit["provenance"])}
                    copy.write_row(tuple(values[field] for field in UNIT_FIELDS) + (parent,))
            conn.execute("INSERT INTO work24_bge.active_catalog(dataset_id) VALUES(%s)", (parent,))
            conn.commit()
            rows = [{"course_id": c["course_id"], "tme": c["tme"], "course_name": c["course_name"],
                     "eligibility": "Synthetic supplementary fact" if c.get("metadata", {}).get("훈련대상요건") is None else None}
                    for c in courses]
            rows[-1]["course_name"] = "Conflicting synthetic observation"
            previous = {"dataset_id": parent, "courses": courses}
            result = normalized(rows, parent=parent, previous=previous, legacy_mapping=mapping_for(supplied))
            self.assertEqual(len(result["courses"]), len(courses) - 1)
            self.assertEqual(result["report"]["quarantined"][0]["reason"], "conflicting_offering")
            package = root / "update"
            output_manifest = write_package(result, supplied, package, created_on="2026-11-01")
            digest = file_hash(package / "manifest.json")
            output_vectors = np.load(package / "vectors.npy", allow_pickle=False)
            zeros = np.argwhere(output_vectors == 0)
            if len(zeros):
                tampered = output_vectors.copy()
                row, column = zeros[0]
                tampered[row, column] = np.copysign(np.float32(0), -1 if not np.signbit(tampered[row, column]) else 1)
                self.assertTrue(np.array_equal(tampered, output_vectors))
                self.assertNotEqual(tampered.tobytes(), output_vectors.tobytes())
                np.save(package / "vectors.npy", tampered)
                _manifest, changed_digest = refresh(package, output_manifest)
                self.assertEqual(stage(conn, package, changed_digest)["error"], "records_parent_units_mismatch")
                conn.rollback()
                np.save(package / "vectors.npy", output_vectors)
                _manifest, digest = refresh(package, output_manifest)
            conn.execute("UPDATE work24_bge.snapshot_course SET metadata=metadata || '{\"changed\":true}'::jsonb WHERE dataset_id=%s AND course_id=%s AND tme=%s",
                         (parent, courses[0]["course_id"], courses[0]["tme"]))
            self.assertEqual(stage(conn, package, digest)["error"], "records_parent_metadata_mismatch")
            conn.rollback()
            conn.execute("UPDATE work24_bge.snapshot_unit SET unit_name='Changed parent unit' WHERE dataset_id=%s AND course_id=%s AND tme=%s",
                         (parent, courses[0]["course_id"], courses[0]["tme"]))
            self.assertEqual(stage(conn, package, digest)["error"], "records_parent_units_mismatch")
            conn.rollback()
            ready = stage(conn, package, digest)
            self.assertEqual(ready["state"], "ready", ready)
            conn.commit()
            activate(conn, ready["batch_id"])
            conn.commit()
            dataset = ready["dataset_id"]
            stored = conn.execute("SELECT course_id,tme,metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s", (dataset,)).fetchall()
            by_key = {(row[0], row[1]): row[2:] for row in stored}
            blocked = courses[-1]
            self.assertEqual(by_key[(blocked["course_id"], blocked["tme"])], (blocked, parent))
            original_units = {unit["unit_key"]: (unit, vector) for unit, vector in zip(units, vectors, strict=True)}
            actual_units = conn.execute(f"SELECT {fields} FROM work24_bge.snapshot_unit WHERE dataset_id=%s", (dataset,)).fetchall()
            self.assertEqual(len(actual_units), len(units))
            for row in actual_units:
                actual = dict(zip(UNIT_FIELDS, row, strict=True))
                unit, vector = original_units[actual["unit_key"]]
                self.assertEqual(vector_array(actual.pop("embedding")).tobytes(), vector.tobytes())
                expected = {**unit, "dataset_id": dataset, "profile_id": profile, "active": True}
                self.assertEqual({key: value for key, value in actual.items() if key != "vector_row"},
                                 {key: expected[key] for key in actual if key != "vector_row"})
            accepted = result["courses"][0]
            course = by_key[(accepted["course_id"], accepted["tme"])][0]
            self.assertEqual(course["work24"]["legacy_parent"]["metadata"], courses[0])
            self.assertIsNone(course["collected_at"])
            repeated = normalized([rows[0]], parent=dataset,
                                  previous={"dataset_id": dataset, "courses": [row[2] for row in stored]})
            write_package(repeated, supplied, root / "repeat", created_on="2026-11-01")
            again = stage(conn, root / "repeat", file_hash(root / "repeat" / "manifest.json"))
            self.assertEqual(again["state"], "ready", again)
            conn.commit()
            activate(conn, again["batch_id"])
            conn.commit()
            self.assertEqual(stage(conn, package, digest)["state"], "activated")

    @unittest.skipUnless(os.environ.get("WORK24_LEGACY_TEST_DATABASE_URL"), "requires owned disposable database")
    def test_synthetic_legacy_supplement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.exercise(legacy_precomputed(root / "source"), os.environ["WORK24_LEGACY_TEST_DATABASE_URL"], root)

    @unittest.skipUnless(os.environ.get("WORK24_PINNED_TEST_DATABASE_URL") and os.environ.get("WORK24_PINNED_ARCHIVE"),
                         "requires owned disposable database and existing pinned archive")
    def test_existing_pinned_artifact_supplement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extract_verified(Path(os.environ["WORK24_PINNED_ARCHIVE"]), root / "source")
            self.exercise(root / "source", os.environ["WORK24_PINNED_TEST_DATABASE_URL"], root)


if __name__ == "__main__":
    unittest.main()
