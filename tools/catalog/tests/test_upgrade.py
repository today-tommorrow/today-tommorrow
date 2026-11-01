import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artifacts import load_artifacts, value_hash
from batch_fixture import package
from database import UNIT_FIELDS, local_connections
from upgrade import (
    SNAPSHOT_DDL_SHA256,
    Expected,
    connection_parameters,
    main,
    mutation_plan,
    snapshot_state,
    verify_rows,
)


class UpgradeTargetTests(unittest.TestCase):
    def setUp(self):
        self.expected = Expected("work24", "catalog_owner", "a" * 64, "b" * 64, "catalog_reader")
        self.env = {"CATALOG_UPGRADE_DATABASE_URL": "postgresql://catalog_owner:secret@db.example.org:5432/work24"}

    def test_explicit_production_target_is_separate_from_local_bootstrap(self):
        params = connection_parameters(self.env, self.expected)
        self.assertEqual(params["host"], "db.example.org")
        self.assertEqual(params["options"], "")
        with self.assertRaisesRegex(ValueError, "invalid_local_database_target"):
            local_connections({"CATALOG_DATABASE_URL": self.env["CATALOG_UPGRADE_DATABASE_URL"]})

    def test_target_mismatch_and_inherited_libpq_routing_are_rejected(self):
        for key in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPORT"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "upgrade_libpq_override"):
                connection_parameters({**self.env, key: "untrusted"}, self.expected)
        for dsn in (
            "postgresql://other:secret@db.example.org/work24",
            "postgresql://catalog_owner:secret@db.example.org/other",
            "postgresql://catalog_owner:secret@db.example.org/work24?hostaddr=203.0.113.1",
            "postgresql://catalog_owner:secret@db.example.org/work24?options=-csearch_path=evil",
            "postgresql:///work24",
            "host=db.example.org,other dbname=work24 user=catalog_owner password=secret",
        ):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError) as error:
                connection_parameters({"CATALOG_UPGRADE_DATABASE_URL": dsn}, self.expected)
            self.assertNotIn("secret", str(error.exception))

    def test_unix_socket_peer_auth_does_not_need_database_password(self):
        params = connection_parameters({"CATALOG_UPGRADE_DATABASE_URL":
                                        "host=/var/run/postgresql dbname=work24 user=catalog_owner"}, self.expected)
        self.assertEqual(params["host"], "/var/run/postgresql")
        self.assertNotIn("password", params)
        with self.assertRaisesRegex(ValueError, "upgrade_password_required"):
            connection_parameters({"CATALOG_UPGRADE_DATABASE_URL":
                                   "host=127.0.0.1 dbname=work24 user=catalog_owner"}, self.expected)

    def test_plan_binds_the_socket_path_and_port_when_server_address_is_null(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = ("work24", "catalog_owner", None, None, 42)
        connection.info.host, connection.info.port = "/tmp/catalog-one", 5432
        artifacts = ({}, [], [])
        first = value_hash(mutation_plan(connection, artifacts, self.expected, "legacy"))
        connection.info.host = "/tmp/catalog-two"
        second = value_hash(mutation_plan(connection, artifacts, self.expected, "legacy"))
        self.assertNotEqual(first, second)
        connection.info.host, connection.info.port = "/tmp/catalog-one", 5433
        self.assertNotEqual(first, value_hash(mutation_plan(connection, artifacts, self.expected, "legacy")))

    def test_cli_defaults_to_plan_and_redacts_driver_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            arguments = ["upgrade.py", "--archive", temporary, "--expected-database", "work24",
                         "--expected-user", "catalog_owner", "--expected-profile-id", "a" * 64,
                         "--expected-dataset-id", "b" * 64, "--reader-role", "catalog_reader"]
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            with patch.object(sys, "argv", arguments), patch("upgrade.connection_parameters", return_value={}), \
                    patch("upgrade.extract_verified"), patch("upgrade.load_artifacts", return_value=()), \
                    patch("upgrade.psycopg.connect", return_value=connection), \
                    patch("upgrade.upgrade", return_value={"status": "planned"}) as execute, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                self.assertFalse(execute.call_args.kwargs["apply"])
            error_output = io.StringIO()
            with patch.object(sys, "argv", arguments), \
                    patch("upgrade.connection_parameters", side_effect=RuntimeError("password=secret host=private")), \
                    contextlib.redirect_stderr(error_output):
                self.assertEqual(main(), 1)
            self.assertNotIn("secret", error_output.getvalue())
            self.assertNotIn("private", error_output.getvalue())

    def test_committed_database_status_survives_report_output_failure(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        arguments = ["upgrade.py", "--archive", "fixture.zip", "--expected-database", "work24",
                     "--expected-user", "catalog_owner", "--expected-profile-id", "a" * 64,
                     "--expected-dataset-id", "b" * 64, "--reader-role", "catalog_reader",
                     "--apply", "--report", str(Path(temporary.name) / "report.json")]
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        output = io.StringIO()
        with patch.object(sys, "argv", arguments), patch("upgrade.connection_parameters", return_value={}), \
                patch("upgrade.extract_verified"), patch("upgrade.load_artifacts", return_value=()), \
                patch("upgrade.psycopg.connect", return_value=connection), \
                patch("upgrade.upgrade", return_value={"status": "applied", "applied": True}), \
                patch("upgrade.os.link", side_effect=OSError("private path")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(), 2)
        self.assertIn('"status": "committed_report_write_failed"', output.getvalue())
        self.assertIn('"database_status": "applied"', output.getvalue())
        self.assertIn('"applied": true', output.getvalue())
        self.assertNotIn("private path", output.getvalue())

    def test_report_cannot_overwrite_archive_or_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, report = root / "archive.zip", root / "report.json"
            archive.write_bytes(b"preserved archive")
            report.write_bytes(b"preserved report")
            for destination in (archive, report):
                arguments = ["upgrade.py", "--archive", str(archive), "--expected-database", "work24",
                             "--expected-user", "catalog_owner", "--expected-profile-id", "a" * 64,
                             "--expected-dataset-id", "b" * 64, "--reader-role", "catalog_reader",
                             "--apply", "--report", str(destination)]
                with patch.object(sys, "argv", arguments), patch("upgrade.psycopg.connect") as connect, \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(), 1)
                    connect.assert_not_called()
            self.assertEqual(archive.read_bytes(), b"preserved archive")
            self.assertEqual(report.read_bytes(), b"preserved report")

    def test_reviewed_ddl_checksum_matches_the_shared_schema(self):
        from artifacts import file_hash
        self.assertEqual(file_hash(Path(__file__).resolve().parents[1] / "snapshot_schema.sql"), SNAPSHOT_DDL_SHA256)


class UpgradeContentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name) / "package"
        package(root, {}, "f" * 64, ["A", "B"])
        self.artifacts = load_artifacts(root, pinned=False)

    def connection(self, *, snapshot=False):
        manifest, courses, units, vectors, _query = self.artifacts
        course_rows = [(c["course_id"], c["tme"], c) + ((manifest["dataset_id"],) if snapshot else ()) for c in courses]
        unit_rows = []
        for unit, vector in zip(units, vectors, strict=True):
            record = {**unit, "profile_id": manifest["embedding_profile_id"], "embedding": vector, "active": True}
            unit_rows.append(tuple(record[field] for field in UNIT_FIELDS) + ((manifest["dataset_id"],) if snapshot else ()))
        conn = Mock()
        conn.execute.side_effect = [Mock(fetchall=Mock(return_value=course_rows)), Mock(fetchall=Mock(return_value=unit_rows))]
        return conn, course_rows, unit_rows

    def test_all_base_and_snapshot_fields_and_vector_bytes_match(self):
        for snapshot in (False, True):
            conn, _courses, _units = self.connection(snapshot=snapshot)
            verify_rows(conn, self.artifacts, snapshot=snapshot)

    def test_extra_course_and_changed_metadata_are_rejected(self):
        conn, rows, _units = self.connection()
        rows.append(("extra", "01", {}))
        with self.assertRaisesRegex(ValueError, "upgrade_course_content_mismatch"):
            verify_rows(conn, self.artifacts)
        conn, _rows, units = self.connection()
        row = list(units[0])
        row[UNIT_FIELDS.index("unit_name")] = "Changed"
        units[0] = tuple(row)
        with self.assertRaisesRegex(ValueError, "upgrade_unit_content_mismatch"):
            verify_rows(conn, self.artifacts)

    def test_signed_zero_vector_change_is_rejected(self):
        conn, _courses, rows = self.connection()
        row = list(rows[0])
        vector = row[UNIT_FIELDS.index("embedding")].copy()
        vector[1] = np.float32(-0.0)
        self.assertTrue(np.array_equal(vector, self.artifacts[3][0]))
        row[UNIT_FIELDS.index("embedding")] = vector
        rows[0] = tuple(row)
        with self.assertRaisesRegex(ValueError, "upgrade_vector_bytes_mismatch"):
            verify_rows(conn, self.artifacts)

    def test_partial_or_wrong_relation_state_is_rejected(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = []
        self.assertEqual(snapshot_state(conn), "legacy")
        conn.execute.return_value.fetchall.return_value = [("snapshot_course", "r")]
        with self.assertRaisesRegex(ValueError, "partial_catalog_upgrade"):
            snapshot_state(conn)


if __name__ == "__main__":
    unittest.main()
