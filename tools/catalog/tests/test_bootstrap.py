import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

CATALOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CATALOG))


def environment():
    return {
        "CATALOG_DATABASE_URL": "postgresql://today_bootstrap:bootstrap@127.0.0.1:55432/work24_dev",
        "MIGRATION_DATABASE_URL": "postgresql+psycopg://today_migrator:migration@127.0.0.1:55432/work24_dev",
        "DATABASE_URL": "postgresql+psycopg://today_app:app@127.0.0.1:55432/work24_dev",
    }


class TargetTests(unittest.TestCase):
    def setUp(self):
        from database import local_connections
        self.local_connections = local_connections

    def test_accepts_only_the_same_local_development_database(self):
        targets = self.local_connections(environment())
        self.assertEqual(targets["catalog"]["dbname"], "work24_dev")
        self.assertEqual(targets["migration"]["user"], "today_migrator")
        self.assertEqual(targets["app"]["host"], "127.0.0.1")

    def test_rejects_remote_database_and_libpq_overrides(self):
        for dsn in [
            "postgresql://today_bootstrap:secret@example.org:55432/work24_dev",
            "postgresql://today_bootstrap:secret@127.0.0.1:55432/work24",
            "postgresql://today_bootstrap:secret@127.0.0.1:55432/work24_dev?hostaddr=203.0.113.1",
            "postgresql://today_bootstrap:secret@127.0.0.1:55432/work24_dev?options=-csearch_path=evil",
            "postgresql://today_bootstrap:secret@127.0.0.1:55432/work24_dev?service=remote",
            "postgresql:///work24_dev",
            "host=127.0.0.1,example.org dbname=work24_dev user=today_bootstrap password=secret",
        ]:
            with self.subTest(dsn=dsn):
                env = {**environment(), "CATALOG_DATABASE_URL": dsn}
                with self.assertRaisesRegex(ValueError, "local_database_target") as error:
                    self.local_connections(env)
                self.assertNotIn("secret", str(error.exception))

    def test_rejects_different_port_roles_and_inherited_overrides(self):
        for key, value in [
            ("DATABASE_URL", environment()["DATABASE_URL"].replace("55432", "5432")),
            ("DATABASE_URL", environment()["DATABASE_URL"].replace("today_app:", "today_bootstrap:")),
            ("PGHOSTADDR", "203.0.113.1"),
            ("PGSERVICE", "remote"),
        ]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.local_connections({**environment(), key: value})


class ArtifactTests(unittest.TestCase):
    def test_existing_local_archive_is_used_without_network(self):
        from artifacts import ARCHIVE_NAME, archive_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "tdtemp" / ARCHIVE_NAME
            local.parent.mkdir()
            local.write_bytes(b"local archive, checked before extraction")
            with patch("artifacts.urllib.request.urlopen", side_effect=RuntimeError("network must not run")):
                self.assertEqual(archive_path(root), local)

    def test_missing_archive_downloads_verified_bytes_and_reuses_cache(self):
        from artifacts import archive_path
        # SHA256 of literal b'abc', not calculated by the implementation under test.
        digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("artifacts.ARCHIVE_SHA256", digest), patch("artifacts.urllib.request.urlopen", return_value=io.BytesIO(b"abc")):
                archive = archive_path(root)
            self.assertEqual(archive.read_bytes(), b"abc")
            with patch("artifacts.ARCHIVE_SHA256", digest), patch("artifacts.urllib.request.urlopen", side_effect=RuntimeError("offline cache reuse")):
                self.assertEqual(archive_path(root), archive)

    def test_bad_download_is_not_published_and_temporary_file_is_removed(self):
        from artifacts import archive_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("artifacts.urllib.request.urlopen", return_value=io.BytesIO(b"wrong archive")):
                with self.assertRaisesRegex(ValueError, "archive_checksum_mismatch"):
                    archive_path(root)
            self.assertEqual(list((root / ".local" / "catalog").iterdir()), [])

    def test_checksum_failure_does_not_create_extracted_files(self):
        from artifacts import extract_verified
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            archive.write_bytes(b"not the fixed archive")
            with self.assertRaisesRegex(ValueError, "archive_checksum"):
                extract_verified(archive, root / "out")
            self.assertFalse((root / "out").exists())

    def test_extracts_only_data_not_package_python(self):
        from artifacts import DATA_FILES, extract_verified
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as z:
                for name in DATA_FILES:
                    z.writestr("work24-bge-m3-20261101/" + name, b"fixture")
                z.writestr("work24-bge-m3-20261101/execute.py", b"raise RuntimeError('must not execute')")
                z.writestr("../../outside", b"unsafe")
            with patch("artifacts.ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()):
                extract_verified(archive, root / "out")
            self.assertEqual({p.name for p in (root / "out").iterdir()}, set(DATA_FILES))
            self.assertFalse((root / "outside").exists())

    def test_validation_remains_enabled_with_python_optimization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(json.dumps({
                "embedding_config": {}, "embedding_profile_id": "wrong", "input_files": {}, "dataset_id": "wrong",
            }))
            code = "from artifacts import load_artifacts; from pathlib import Path; load_artifacts(Path(__import__('sys').argv[1]))"
            result = subprocess.run([sys.executable, "-O", "-c", code, str(root)],
                                    env={**os.environ, "PYTHONPATH": str(CATALOG)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("embedding_profile_mismatch", result.stderr)


class SearchTests(unittest.TestCase):
    def test_ranking_accepts_ties_but_rejects_wrong_scores(self):
        from database import ranking_comparison
        scores = {"a": 0.9, "b": 0.8, "c": 0.8}
        good = ranking_comparison(scores, [("a", 0.9), ("b", 0.8), ("c", 0.8)], top_k=2)
        self.assertTrue(good["passed"])
        bad = ranking_comparison(scores, [("a", 0.9), ("b", 0.1), ("c", 0.8)], top_k=2)
        self.assertFalse(bad["passed"])

    def test_vector_conversion_uses_explicit_pgvector_adapter(self):
        from database import vector_array
        class StoredVector:
            def to_numpy(self):
                import numpy as np
                return np.array([1.0, 2.0], dtype=np.float32)
        self.assertEqual(vector_array(StoredVector()).tolist(), [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
