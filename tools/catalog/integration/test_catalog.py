"""Explicit real local-DB suite; run only after the catalog bootstrap command."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

CATALOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CATALOG))

from artifacts import archive_path, extract_verified, load_artifacts  # noqa: E402
from database import (  # noqa: E402
    attest,
    execute,
    local_connections,
    verify_catalog,
    verify_roles,
)


class CatalogIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connections = local_connections(os.environ)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        data = Path(cls.temporary.name) / "data"
        extract_verified(archive_path(CATALOG.parents[1]), data)
        cls.artifacts = load_artifacts(data)

    def test_read_only_verification_checks_real_vectors_and_search(self):
        report = execute(self.connections, self.artifacts, verify_only=True)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["full_float32_roundtrip_rows"], 6099)
        self.assertTrue(report["exact_search"]["passed"])
        self.assertTrue(report["hnsw"]["index_used"])
        self.assertTrue(report["roles_verified"])

    def test_corrupt_unit_metadata_is_rejected_and_rolled_back(self):
        with psycopg.connect(**self.connections["catalog"]) as conn:
            try:
                attest(conn, "today_bootstrap")
                register_vector(conn)
                conn.execute("UPDATE work24_bge.course_unit SET unit_name='tampered fixture' WHERE vector_row=0")
                with self.assertRaisesRegex(ValueError, "roundtrip_metadata_mismatch"):
                    verify_catalog(conn, self.artifacts)
            finally:
                conn.rollback()

    def test_app_can_read_but_cannot_modify_education(self):
        with psycopg.connect(**self.connections["app"]) as conn:
            attest(conn, "today_app")
            self.assertEqual(conn.execute("SELECT count(*) FROM work24_bge.course_unit").fetchone(), (6099,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE work24_bge.course SET metadata='{}' WHERE false")
            conn.rollback()

    def test_wrong_existing_role_password_does_not_pass_authentication(self):
        bad = {**self.connections, "app": {**self.connections["app"], "password": "incorrect-test-password"}}
        with self.assertRaises(psycopg.OperationalError):
            verify_roles(bad)
        self.assertTrue(verify_roles(self.connections))


if __name__ == "__main__":
    unittest.main()
