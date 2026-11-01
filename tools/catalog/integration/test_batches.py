"""Real PostgreSQL batch/history checks; run only on the isolated local test DB."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

CATALOG = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CATALOG), str(CATALOG / "tests")]

from artifacts import value_hash  # noqa: E402
from batch_fixture import package, refresh  # noqa: E402
from batches import activate, stage  # noqa: E402
from database import attest, initialize_snapshot, local_connections  # noqa: E402


class BatchDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connections = local_connections(os.environ)
        self.conn = psycopg.connect(**self.connections["catalog"])
        attest(self.conn, "today_bootstrap")
        register_vector(self.conn)
        self.parent = self.conn.execute("SELECT dataset_id FROM work24_bge.active_catalog").fetchone()[0]
        self.manifest = self.conn.execute("SELECT manifest FROM work24_bge.dataset_snapshot WHERE dataset_id=%s", (self.parent,)).fetchone()[0]
        self.before = {row[0] for row in self.conn.execute("SELECT batch_id FROM work24_bge.catalog_batch")}
        self.conn.commit()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.cleanup_database)

    def cleanup_database(self):
        self.conn.rollback()
        created = [row for row in self.conn.execute("SELECT batch_id,dataset_id FROM work24_bge.catalog_batch") if row[0] not in self.before]
        ids, datasets = [row[0] for row in created], [row[1] for row in created if row[1]]
        self.conn.execute("UPDATE work24_bge.active_catalog SET dataset_id=%s", (self.parent,))
        self.conn.execute("DELETE FROM work24_bge.catalog_activation WHERE batch_id=ANY(%s)", (ids,))
        self.conn.execute("DELETE FROM work24_bge.catalog_batch WHERE batch_id=ANY(%s)", (ids,))
        self.conn.execute("DELETE FROM work24_bge.snapshot_unit WHERE dataset_id=ANY(%s)", (datasets,))
        self.conn.execute("DELETE FROM work24_bge.snapshot_course WHERE dataset_id=ANY(%s)", (datasets,))
        self.conn.execute("DELETE FROM work24_bge.dataset_snapshot WHERE dataset_id=ANY(%s)", (datasets,))
        self.conn.commit()
        self.conn.close()

    def batch(self, label, names, **kwargs):
        root = Path(self.temporary.name) / label
        manifest, digest = package(root, self.manifest["embedding_config"], kwargs.pop("parent", self.parent), names, **kwargs)
        return root, manifest, digest

    def active(self):
        return self.conn.execute("SELECT dataset_id FROM work24_bge.active_catalog").fetchone()[0]

    def test_partial_full_history_source_preservation_and_reimport(self):
        root, _manifest, digest = self.batch("first", ["synthetic-A", "synthetic-B"])
        first = stage(self.conn, root, digest)
        self.assertEqual(first["state"], "ready", first)
        self.assertEqual(self.active(), self.parent)
        self.conn.commit()
        activate(self.conn, first["batch_id"])
        self.conn.commit()
        self.assertEqual(self.active(), first["dataset_id"])
        source = self.conn.execute("SELECT metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-B'", (first["dataset_id"],)).fetchone()
        root2, _m2, digest2 = self.batch("partial", ["synthetic-A"], parent=first["dataset_id"], revision="2")
        second = stage(self.conn, root2, digest2)
        self.assertEqual(second["state"], "ready", second)
        self.assertEqual(second["courses"], first["courses"])
        carried = self.conn.execute("SELECT metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-B'", (second["dataset_id"],)).fetchone()
        self.assertEqual(carried, source)
        self.conn.commit()
        activate(self.conn, second["batch_id"])
        self.conn.commit()
        root3, _m3, digest3 = self.batch("full", ["synthetic-A"], parent=second["dataset_id"], revision="3", mode="full", covered=["synthetic-A", "synthetic-B"])
        third = stage(self.conn, root3, digest3)
        self.assertEqual(third["state"], "ready", third)
        self.assertEqual(third["courses"], first["courses"] - 1)
        self.conn.commit()
        activate(self.conn, third["batch_id"])
        self.conn.commit()
        # Repeating a package or setup never moves the pointer backwards.
        self.assertEqual(stage(self.conn, root, digest)["state"], "activated")
        activate(self.conn, first["batch_id"])
        initialize_snapshot(self.conn, self.parent)
        self.assertEqual(self.active(), third["dataset_id"])
        self.assertEqual(self.conn.execute("SELECT metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-B'", (first["dataset_id"],)).fetchone(), source)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id='synthetic-B'", (third["dataset_id"],)).fetchone())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM work24_bge.catalog_activation WHERE batch_id IS NOT NULL").fetchone()[0], len(self.before) + 3)

    def test_empty_corrupt_and_stale_batches_do_not_change_active(self):
        for label, corrupt in [("empty", True), ("tampered", False)]:
            root, manifest, digest = self.batch(label, ["synthetic-A"])
            (root / "courses.jsonl").write_text("")
            if corrupt:
                manifest, digest = refresh(root, manifest)
            report = stage(self.conn, root, digest)
            self.assertEqual(report["state"], "failed", report)
            self.assertEqual(self.active(), self.parent)
            self.assertIsNone(self.conn.execute("SELECT dataset_id FROM work24_bge.catalog_batch WHERE batch_id=%s", (value_hash(manifest),)).fetchone()[0])
        root, _m, digest = self.batch("valid", ["synthetic-A"])
        first = stage(self.conn, root, digest)
        other, _m, other_digest = self.batch("stale", ["synthetic-B"])
        stale = stage(self.conn, other, other_digest)
        self.conn.commit()
        activate(self.conn, first["batch_id"])
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "batch_parent_changed"):
            activate(self.conn, stale["batch_id"])
        self.conn.rollback()
        self.assertEqual(self.active(), first["dataset_id"])
        self.assertEqual(self.conn.execute("SELECT state FROM work24_bge.catalog_batch WHERE batch_id=%s", (stale["batch_id"],)).fetchone()[0], "ready")

    def test_missing_artifact_is_quarantined_and_corrected_retry_preserves_failure(self):
        root, _manifest, digest = self.batch("retry", ["synthetic-A"])
        artifact = root / "units.jsonl"
        original = artifact.read_bytes()
        artifact.unlink()
        failed = stage(self.conn, root, digest)
        self.assertEqual(failed["state"], "failed", failed)
        self.conn.commit()
        artifact.write_bytes(original)
        retried = stage(self.conn, root, digest)
        self.assertEqual(retried["state"], "ready", retried)
        self.assertEqual(retried["batch_id"], failed["batch_id"])
        history = self.conn.execute("SELECT failure_history FROM work24_bge.catalog_batch WHERE batch_id=%s", (retried["batch_id"],)).fetchone()[0]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["report"], failed)
        self.assertEqual(self.active(), self.parent)

    def test_cli_stages_then_activates_with_atomic_reports(self):
        root, _manifest, digest = self.batch("cli", ["synthetic-A"])
        report_path = Path(self.temporary.name) / "report.json"
        command = [sys.executable, str(CATALOG / "batches.py"), "--directory", str(root),
                   "--manifest-sha256", digest, "--stage-only", "--report", str(report_path)]
        staged = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(staged.returncode, 0, staged.stdout)
        ready = json.loads(staged.stdout)
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(json.loads(report_path.read_text()), ready)
        self.assertEqual(self.active(), self.parent)
        activated = subprocess.run([sys.executable, str(CATALOG / "batches.py"), "--activate", ready["batch_id"],
                                    "--report", str(report_path)], capture_output=True, text=True)
        self.assertEqual(activated.returncode, 0, activated.stdout)
        self.assertEqual(json.loads(report_path.read_text())["state"], "activated")
        self.assertEqual(self.active(), ready["dataset_id"])
        failed = subprocess.run([sys.executable, str(CATALOG / "batches.py"), "--activate", "0" * 64,
                                 "--report", str(report_path)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(report_path.read_text())["error"], "batch_not_ready")
        self.assertEqual(self.active(), ready["dataset_id"])

    def test_full_bootstrap_reimport_keeps_the_activated_version(self):
        root, _manifest, digest = self.batch("reimport", ["synthetic-A"])
        ready = stage(self.conn, root, digest)
        self.assertEqual(ready["state"], "ready", ready)
        self.conn.commit()
        activate(self.conn, ready["batch_id"])
        self.conn.commit()
        completed = subprocess.run([sys.executable, str(CATALOG / "bootstrap.py")], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(json.loads(completed.stdout)["status"], "committed")
        self.assertEqual(self.active(), ready["dataset_id"])
