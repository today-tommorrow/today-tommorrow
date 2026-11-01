"""Synthetic records through an owned real PostgreSQL snapshot and activation."""

import copy
import json
import os
import subprocess
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

from artifacts import file_hash, load_artifacts  # noqa: E402
from batch_fixture import package, refresh, rewrite_row  # noqa: E402
from batches import activate, stage  # noqa: E402
from database import vector_array  # noqa: E402
from records_fixture import normalized, precomputed, record  # noqa: E402
from work24_records import write_package  # noqa: E402


class RecordsDatabaseTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("WORK24_RECORDS_TEST_DATABASE_URL"), "requires owned disposable database")
    def test_supplement_and_mixed_quarantine_preserve_parent_bytes(self) -> None:
        # Explicit opt-in target: never use a developer's existing catalog for this test.
        dsn = os.environ.get("WORK24_RECORDS_TEST_DATABASE_URL")
        self.assertTrue(dsn, "set WORK24_RECORDS_TEST_DATABASE_URL to the owned disposable database")
        with psycopg.connect(dsn) as conn, tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            baseline = normalized([record(tuition_krw=123), record("synthetic-B", listed_self_pay_krw=None),
                                   record("synthetic-zero", listed_self_pay_krw=0)])
            supplied = precomputed(root / "baseline", baseline)
            artifacts = load_artifacts(supplied, pinned=False)
            parent = artifacts[0]["dataset_id"]
            conn.execute((CATALOG / "schema.sql").read_text(), prepare=False)
            conn.execute((CATALOG / "snapshot_schema.sql").read_text(), prepare=False)
            self.assertEqual(conn.execute("SELECT count(*) FROM work24_bge.dataset_snapshot").fetchone()[0], 0,
                             "the test requires a fresh disposable database")
            conn.execute("CREATE ROLE today_app; CREATE ROLE today_migrator", prepare=False)
            conn.execute("INSERT INTO work24_bge.embedding_profile(profile_id,config) VALUES(%s,%s)",
                         (artifacts[0]["embedding_profile_id"], Jsonb(artifacts[0]["embedding_config"])))
            conn.execute("INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES(%s,%s,%s)",
                         (parent, artifacts[0]["embedding_profile_id"], Jsonb(artifacts[0])))
            for course in artifacts[1]:
                conn.execute("INSERT INTO work24_bge.snapshot_course VALUES(%s,%s,%s,%s,%s)",
                             (parent, course["course_id"], course["tme"], Jsonb(course), parent))
            for unit, vector in zip(artifacts[2], artifacts[3], strict=True):
                conn.execute("""INSERT INTO work24_bge.snapshot_unit
                    (dataset_id,unit_key,course_id,tme,vector_row,unit_type,ncs_unit_code,unit_name,
                     embed_text,text_sha256,hours,source,is_noise,is_inferred,provenance,embedding,profile_id,source_dataset_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                             (parent, unit["unit_key"], unit["course_id"], unit["tme"], unit["vector_row"], unit["unit_type"],
                              unit["ncs_unit_code"], unit["unit_name"], unit["embed_text"], unit["text_sha256"], unit["hours"],
                              unit["source"], False, False, Jsonb(unit["provenance"]), vector,
                              artifacts[0]["embedding_profile_id"], parent))
            conn.execute("INSERT INTO work24_bge.active_catalog(dataset_id) VALUES(%s)", (parent,))
            conn.commit()
            previous = {"dataset_id": parent, "courses": baseline["courses"]}
            ordinary, _digest = package(root / "ordinary", artifacts[0]["embedding_config"], parent, ["ordinary"])
            _ordinary, digest = rewrite_row(root / "ordinary", ordinary, "courses.jsonl", {"work24": {"optional_source_metadata": True}})
            self.assertEqual(stage(conn, root / "ordinary", digest)["state"], "ready")
            conn.commit()
            for label, prior in (("absent", None), ("incomplete", {"dataset_id": parent, "courses": []})):
                unsafe = normalized([record(tuition_krw=None)], parent=parent, previous=prior)
                write_package(unsafe, supplied, root / label, created_on="2026-11-01")
                rejected = stage(conn, root / label, file_hash(root / label / "manifest.json"))
                self.assertEqual(rejected["error"], "records_parent_metadata_mismatch")
                conn.commit()
                self.assertEqual(conn.execute("SELECT metadata->'training_fee' FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-A'", (parent,)).fetchone()[0], 123)
            update = normalized([record(tuition_krw=None, region="Seoul", eligibility="Observed requirement"),
                                 record("synthetic-B", listed_self_pay_krw=None)], parent=parent,
                                previous=previous, revision="supplement")
            original = copy.deepcopy(baseline["courses"][0])
            manifest = write_package(update, supplied, root / "supplement", created_on="2026-11-01")
            for label, replacement in (("missing-guard", None), ("missing-key", {"version": 1, "parent_records": []})):
                bad = copy.deepcopy(manifest)
                if replacement is None:
                    bad.pop("records_adapter")
                else:
                    bad["records_adapter"] = replacement
                _bad, digest = refresh(root / "supplement", bad)
                rejected = stage(conn, root / "supplement", digest)
                self.assertIn(rejected["error"], ("records_parent_guard_required", "invalid_records_parent_guard"))
                conn.commit()
            refresh(root / "supplement", manifest)
            # Even a correct metadata export cannot authorize losing/changing parent units.
            conn.execute("UPDATE work24_bge.snapshot_unit SET unit_name='Extra parent evidence' WHERE dataset_id=%s AND course_id='synthetic-A'", (parent,))
            rejected = stage(conn, root / "supplement", file_hash(root / "supplement" / "manifest.json"))
            self.assertEqual(rejected["error"], "records_parent_units_mismatch")
            conn.rollback()
            first = stage(conn, root / "supplement", file_hash(root / "supplement" / "manifest.json"))
            self.assertEqual(first["state"], "ready", first)
            conn.commit()
            activate(conn, first["batch_id"])
            conn.commit()
            course = conn.execute("SELECT metadata FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-A'", (first["dataset_id"],)).fetchone()[0]
            self.assertEqual((course["region"], course["institution"], course["training_fee"], course["displayed_self_pay"]), ("Seoul", "Synthetic provider", 123, "N/A"))
            self.assertEqual(course["metadata"]["훈련대상요건"], "Observed requirement")
            self.assertEqual(course["source_hash"], original["source_hash"])
            self.assertEqual(course["work24"]["field_provenance"]["tuition_krw"], original["work24"]["field_provenance"]["tuition_krw"])
            price_values = dict(conn.execute("SELECT course_id,metadata->'displayed_self_pay' FROM work24_bge.snapshot_course WHERE dataset_id=%s", (first["dataset_id"],)).fetchall())
            self.assertEqual(price_values, {"synthetic-A": "N/A", "synthetic-B": None, "synthetic-zero": 0})
            parent_unit_sources = conn.execute("SELECT unit_key,provenance FROM work24_bge.snapshot_unit WHERE dataset_id=%s ORDER BY unit_key", (parent,)).fetchall()
            first_unit_sources = conn.execute("SELECT unit_key,provenance FROM work24_bge.snapshot_unit WHERE dataset_id=%s ORDER BY unit_key", (first["dataset_id"],)).fetchall()
            self.assertEqual(first_unit_sources, parent_unit_sources)
            # #24 source_dataset_id is artifact lineage, distinct from collection evidence.
            self.assertEqual(conn.execute("SELECT DISTINCT source_dataset_id FROM work24_bge.snapshot_unit WHERE dataset_id=%s AND course_id='synthetic-A'", (first["dataset_id"],)).fetchall(), [(manifest["dataset_id"],)])
            consumer_python = CATALOG.parents[1] / "backend" / ".venv" / "bin" / "python"
            if consumer_python.is_file():
                rows = conn.execute("SELECT course_id,tme,metadata FROM work24_bge.snapshot_course WHERE dataset_id=%s ORDER BY course_id", (first["dataset_id"],)).fetchall()
                script = """import json,sys
from app.courses.presentation import present_course
rows=json.load(sys.stdin)
for source_id,tme,metadata in rows:
 detail=present_course(source_id,[{'tme':tme,'metadata':metadata}],[], 'synthetic-dataset')
 assert detail.provider_name == 'Synthetic provider'
 offering=detail.offerings[0]
 assert offering.listed_self_pay_krw == (0 if source_id=='synthetic-zero' else None)
 if source_id=='synthetic-A':
  assert offering.tuition_krw == 123
  assert offering.eligibility_text == 'Observed requirement'
  assert any(e.field_path=='displayed_self_pay' and e.quote=='N/A' for e in detail.evidence)
"""
                completed = subprocess.run([str(consumer_python), "-c", script], cwd=CATALOG.parents[1] / "backend",
                                           input=json.dumps(rows), capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
            before_course = conn.execute("SELECT metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-A'", (first["dataset_id"],)).fetchone()
            query = "SELECT unit_key,unit_name,embed_text,text_sha256,provenance,embedding,source_dataset_id FROM work24_bge.snapshot_unit WHERE dataset_id=%s AND course_id='synthetic-A' ORDER BY unit_key"
            before_units = conn.execute(query, (first["dataset_id"],)).fetchall()
            stored = conn.execute("SELECT metadata FROM work24_bge.snapshot_course WHERE dataset_id=%s ORDER BY course_id,tme", (first["dataset_id"],)).fetchall()
            mixed = normalized([record(listed_self_pay_krw=99), record("synthetic-B", listed_self_pay_krw=None, region="Busan")],
                               parent=first["dataset_id"], previous={"dataset_id": first["dataset_id"], "courses": [row[0] for row in stored]}, revision="mixed")
            mixed_manifest = write_package(mixed, supplied, root / "mixed", created_on="2026-11-01")
            self.assertEqual(mixed_manifest["batch"]["offering_keys"], [["synthetic-B", "01"]])
            second = stage(conn, root / "mixed", file_hash(root / "mixed" / "manifest.json"))
            self.assertEqual(second["state"], "ready", second)
            conn.commit()
            activate(conn, second["batch_id"])
            conn.commit()
            self.assertEqual(conn.execute("SELECT metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-A'", (second["dataset_id"],)).fetchone(), before_course)
            after_units = conn.execute(query, (second["dataset_id"],)).fetchall()
            self.assertEqual(len(after_units), len(before_units))
            for before, after in zip(before_units, after_units, strict=True):
                self.assertEqual(before[:5], after[:5])
                self.assertTrue(np.array_equal(vector_array(before[5]), vector_array(after[5])))
                self.assertEqual(before[6], after[6])
            self.assertEqual(conn.execute("SELECT metadata->>'region' FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-B'", (second["dataset_id"],)).fetchone()[0], "Busan")
            self.assertEqual(manifest["embedding_profile_id"], mixed_manifest["embedding_profile_id"])


if __name__ == "__main__":
    unittest.main()
