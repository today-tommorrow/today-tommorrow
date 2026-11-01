"""Explicitly owned, initially empty disposable DB only; never a production URL."""

import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

CATALOG = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CATALOG), str(CATALOG / "tests")]

import upgrade as upgrade_module  # noqa: E402
from artifacts import load_artifacts  # noqa: E402
from batch_fixture import package  # noqa: E402
from batches import activate, stage  # noqa: E402
from database import UNIT_FIELDS  # noqa: E402
from upgrade import SNAPSHOT_TABLES, Expected, upgrade  # noqa: E402


@unittest.skipUnless(os.environ.get("WORK24_UPGRADE_TEST_DATABASE_URL"), "requires owned disposable database")
class UpgradeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["WORK24_UPGRADE_TEST_DATABASE_URL"]
        cls.reader = os.environ.get("WORK24_UPGRADE_TEST_READER_ROLE", "today_app")
        with psycopg.connect(cls.dsn) as conn:
            if conn.execute("SELECT to_regnamespace('work24_bge')").fetchone() != (None,):
                raise RuntimeError("upgrade suite requires an initially empty disposable database")
            cls.database, cls.user = conn.execute("SELECT current_database(),current_user").fetchone()
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.manifest, _digest = package(self.source, {}, "f" * 64, ["A", "B"])
        self.artifacts = load_artifacts(self.source, pinned=False)
        self.expected = Expected(self.database, self.user, self.manifest["embedding_profile_id"],
                                 self.manifest["dataset_id"], self.reader)
        self.conn = psycopg.connect(self.dsn, autocommit=True)
        self.addCleanup(self.conn.close)
        self.conn.execute((CATALOG / "schema.sql").read_text(), prepare=False)
        self.conn.execute("INSERT INTO work24_bge.embedding_profile(profile_id,config) VALUES(%s,%s)",
                          (self.expected.profile_id, Jsonb({})))
        self.conn.execute("INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES(%s,%s,%s)",
                          (self.expected.dataset_id, self.expected.profile_id, Jsonb(self.manifest)))
        for course in self.artifacts[1]:
            self.conn.execute("INSERT INTO work24_bge.course VALUES(%s,%s,%s)",
                              (course["course_id"], course["tme"], Jsonb(course)))
        # Register before loading the synthetic vector rows.
        from pgvector.psycopg import register_vector
        register_vector(self.conn)
        fields = sql.SQL(",").join(map(sql.Identifier, UNIT_FIELDS))
        with self.conn.cursor().copy(sql.SQL("COPY work24_bge.course_unit({}) FROM STDIN").format(fields)) as copy:
            for unit, vector in zip(self.artifacts[2], self.artifacts[3], strict=True):
                record = {**unit, "embedding": vector, "profile_id": self.expected.profile_id,
                          "provenance": Jsonb(unit["provenance"]), "active": True}
                copy.write_row(tuple(record[field] for field in UNIT_FIELDS))
        self.conn.execute(sql.SQL("GRANT USAGE ON SCHEMA work24_bge TO {}").format(sql.Identifier(self.reader)))
        self.conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA work24_bge TO {}").format(sql.Identifier(self.reader)))
        self.conn.execute("CREATE VIEW work24_bge.existing_display AS SELECT course_id,tme FROM work24_bge.course")
        self.original = self.base_state()

    def tearDown(self):
        self.conn.execute("DROP SCHEMA work24_bge CASCADE")

    def base_state(self):
        content = []
        for name, key in (("embedding_profile", "profile_id"), ("dataset_snapshot", "dataset_id"),
                          ("course", "course_id,tme"), ("course_unit", "id")):
            content.append(self.conn.execute(sql.SQL("SELECT jsonb_agg(to_jsonb(t) ORDER BY {}) FROM work24_bge.{} t").format(
                sql.SQL(key), sql.Identifier(name))).fetchone()[0])
        content.append(self.conn.execute("SELECT pg_get_viewdef('work24_bge.existing_display'::regclass)").fetchone())
        content.append(self.conn.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin FROM pg_roles ORDER BY rolname").fetchall())
        content.append(self.conn.execute("SELECT roleid,member,grantor,admin_option FROM pg_auth_members ORDER BY roleid,member").fetchall())
        return content

    def run_upgrade(self, *, apply=False, plan_sha256=None):
        if apply and plan_sha256 is None:
            with self.conn.transaction():
                plan_sha256 = upgrade(self.conn, self.artifacts, self.expected)["plan_sha256"]
        with self.conn.transaction():
            return upgrade(self.conn, self.artifacts, self.expected, apply=apply, expected_plan_sha256=plan_sha256)

    def test_plan_then_atomic_apply_repeat_and_existing_view_preserved(self):
        planned = self.run_upgrade()
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.active_catalog')").fetchone(), (None,))
        applied = self.run_upgrade(apply=True)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["existing_views"], ["existing_display"])
        self.assertEqual(self.base_state(), self.original)
        for table in SNAPSHOT_TABLES:
            self.assertEqual(self.conn.execute("SELECT has_table_privilege(%s,%s,'SELECT'),has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE')",
                             (self.reader, f"work24_bge.{table}", self.reader, f"work24_bge.{table}")).fetchone(), (True, False))
        self.assertEqual(self.run_upgrade(apply=True)["status"], "already_upgraded")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM work24_bge.catalog_activation").fetchone(), (1,))
        self.assertEqual(self.base_state(), self.original)

    def test_apply_failure_rolls_back_ddl_baseline_pointer_and_grants(self):
        with patch("upgrade.verify_upgraded", side_effect=ValueError("injected_verification_failure")):
            with self.assertRaisesRegex(ValueError, "injected_verification_failure"):
                self.run_upgrade(apply=True)
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.active_catalog')").fetchone(), (None,))
        self.assertEqual(self.base_state(), self.original)

    def test_apply_requires_the_reviewed_target_and_mutation_plan(self):
        planned = self.run_upgrade()
        self.assertEqual(len(planned["plan"]["operations"]), 8)
        self.assertEqual(planned["plan"]["operations"][4]["rows"], 2)
        with self.assertRaisesRegex(ValueError, "upgrade_plan_mismatch"):
            self.run_upgrade(apply=True, plan_sha256="0" * 64)
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.active_catalog')").fetchone(), (None,))
        self.assertEqual(self.run_upgrade(apply=True, plan_sha256=planned["plan_sha256"])["status"], "applied")
        self.assertEqual(self.run_upgrade(apply=True, plan_sha256=planned["plan_sha256"])["status"], "already_upgraded")

    def test_partial_existing_upgrade_is_rejected_without_repair(self):
        self.conn.execute("CREATE TABLE work24_bge.active_catalog(singleton boolean)")
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaisesRegex(ValueError, "partial_catalog_upgrade"):
                self.run_upgrade(apply=apply)
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.snapshot_course')").fetchone(), (None,))
        self.assertEqual(self.base_state(), self.original)

    def test_changed_base_metadata_is_rejected_before_ddl(self):
        self.conn.execute("UPDATE work24_bge.course SET metadata=metadata || '{\"tampered\":true}'::jsonb")
        with self.assertRaisesRegex(ValueError, "upgrade_course_content_mismatch"):
            self.run_upgrade(apply=True)
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.active_catalog')").fetchone(), (None,))

    def test_legacy_serial_default_cannot_advance_a_base_sequence(self):
        self.conn.execute("ALTER TABLE work24_bge.course_unit ALTER COLUMN id DROP IDENTITY")
        self.conn.execute("CREATE SEQUENCE work24_bge.legacy_sequence START 100")
        self.conn.execute("ALTER TABLE work24_bge.course_unit ALTER COLUMN id SET DEFAULT nextval('work24_bge.legacy_sequence')")
        before = self.conn.execute("SELECT last_value,is_called FROM work24_bge.legacy_sequence").fetchone()
        with self.assertRaisesRegex(ValueError, "catalog_upgrade_schema_mismatch"):
            self.run_upgrade(apply=True)
        self.assertEqual(self.conn.execute("SELECT last_value,is_called FROM work24_bge.legacy_sequence").fetchone(), before)
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.active_catalog')").fetchone(), (None,))

    def test_corrupt_existing_pointer_is_rejected_without_repair(self):
        self.run_upgrade(apply=True)
        self.conn.execute("DELETE FROM work24_bge.active_catalog")
        with self.assertRaisesRegex(ValueError, "upgrade_active_pointer_mismatch"):
            self.run_upgrade(apply=True)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM work24_bge.active_catalog").fetchone(), (0,))

    def test_matching_relation_names_do_not_hide_schema_or_acl_drift(self):
        self.run_upgrade(apply=True)
        for mutation, code in (
            ("ALTER TABLE work24_bge.snapshot_unit ALTER COLUMN unit_name DROP NOT NULL", "catalog_upgrade_schema_mismatch"),
            ("ALTER TABLE work24_bge.catalog_activation DROP CONSTRAINT catalog_activation_dataset_id_fkey", "catalog_upgrade_schema_mismatch"),
            ("DROP INDEX work24_bge.snapshot_unit_embedding_hnsw; CREATE INDEX snapshot_unit_embedding_hnsw ON work24_bge.snapshot_unit USING hnsw(embedding vector_l2_ops)", "catalog_upgrade_schema_mismatch"),
            ("GRANT SELECT ON work24_bge.active_catalog TO PUBLIC", "upgrade_reader_snapshot_permissions_mismatch"),
        ):
            with self.subTest(mutation=mutation), self.conn.transaction(force_rollback=True):
                self.conn.execute(mutation, prepare=False)
                with self.assertRaisesRegex(ValueError, code):
                    self.run_upgrade(apply=True, plan_sha256="0" * 64)

    def test_plan_is_enforced_read_only_by_postgresql(self):
        def try_write(conn, *_arguments):
            conn.execute("DELETE FROM work24_bge.course_unit")
        with patch("upgrade.verify_base", side_effect=try_write):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                self.run_upgrade()
        self.assertEqual(self.base_state(), self.original)

    def test_writer_commit_before_base_lock_is_seen_and_rejected(self):
        reviewed_hash = self.run_upgrade()["plan_sha256"]
        attested, committed = Event(), Event()
        original_attest = upgrade_module.attest

        def delayed_attest(conn, expected):
            original_attest(conn, expected)
            attested.set()
            if not committed.wait(5):
                raise RuntimeError("writer did not commit")

        def attempt():
            with psycopg.connect(self.dsn) as connection:
                return upgrade(connection, self.artifacts, self.expected, apply=True,
                               expected_plan_sha256=reviewed_hash)

        with psycopg.connect(self.dsn, autocommit=True) as writer, ThreadPoolExecutor(max_workers=1) as pool:
            with patch("upgrade.attest", side_effect=delayed_attest):
                with writer.transaction():
                    writer.execute("UPDATE work24_bge.course SET metadata=metadata || '{\"concurrent\":true}'::jsonb")
                    result = pool.submit(attempt)
                    self.assertTrue(attested.wait(5))
                committed.set()
                with self.assertRaisesRegex(ValueError, "upgrade_course_content_mismatch"):
                    result.result(timeout=5)
        self.assertEqual(self.conn.execute("SELECT to_regclass('work24_bge.active_catalog')").fetchone(), (None,))

    def test_repeat_preserves_a_valid_later_batch_activation(self):
        self.run_upgrade(apply=True)
        incoming = self.root / "incoming"
        _manifest, digest = package(incoming, {}, self.expected.dataset_id, ["A"], revision="2")
        with self.conn.transaction():
            ready = stage(self.conn, incoming, digest)
        self.assertEqual(ready["state"], "ready", ready)
        with self.conn.transaction():
            activate(self.conn, ready["batch_id"])
        # dataset_snapshot legitimately gains the immutable later version.
        before = self.base_state()
        report = self.run_upgrade(apply=True)
        self.assertEqual(report["active_dataset_id"], ready["dataset_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM work24_bge.catalog_activation").fetchone(), (2,))
        self.assertEqual(self.base_state(), before)


if __name__ == "__main__":
    unittest.main()
