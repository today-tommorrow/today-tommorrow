"""Real local DB checks, enabled by ./check after the L0 setup has succeeded."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.db import Base, include_migration_name, include_migration_object

ROOT = Path(__file__).resolve().parents[2]


def local_engine(key, role):
    url = make_url(os.environ[key])
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg"}
        or url.host != "127.0.0.1" or url.database != "work24_dev" or url.username != role
        or not url.password or url.query
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"))
    ):
        raise ValueError("local development database required for integration tests")
    return create_engine(url.set(drivername="postgresql+psycopg"), connect_args={
        "hostaddr": "127.0.0.1", "options": "", "connect_timeout": 3,
    })


class TestDatabaseTargetTests(unittest.TestCase):
    def test_independent_db_tests_reject_connection_overrides(self):
        base = "postgresql://today_bootstrap:test@127.0.0.1:55432/work24_dev"
        for suffix in ("?hostaddr=203.0.113.1", "?host=example.org", "?options=-csearch_path=public"):
            with self.subTest(suffix=suffix), patch.dict(os.environ, {"CATALOG_DATABASE_URL": base + suffix}):
                with self.assertRaises(ValueError):
                    local_engine("CATALOG_DATABASE_URL", "today_bootstrap")
        with patch.dict(os.environ, {"CATALOG_DATABASE_URL": base, "PGHOSTADDR": "203.0.113.1"}):
            with self.assertRaises(ValueError):
                local_engine("CATALOG_DATABASE_URL", "today_bootstrap")


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "run ./setup then ./check for local DB integration")
class DatabaseBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = local_engine("CATALOG_DATABASE_URL", "today_bootstrap")
        cls.migrator = local_engine("MIGRATION_DATABASE_URL", "today_migrator")
        cls.app = local_engine("DATABASE_URL", "today_app")
        for engine in (cls.admin, cls.migrator, cls.app):
            cls.addClassCleanup(engine.dispose)

    def test_application_migration_history_is_isolated_and_not_runtime_writable(self):
        self.assertTrue(inspect(self.admin).has_table("alembic_version", schema="today_app"))
        self.assertFalse(inspect(self.admin).has_table("alembic_version", schema="public"))
        with self.app.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT version_num FROM today_app.alembic_version")).scalar(), "0001_baseline")
        with self.app.connect() as conn, self.assertRaises(DBAPIError):
            conn.execute(text("UPDATE today_app.alembic_version SET version_num=version_num"))

    def test_runtime_can_read_but_cannot_write_or_create_education_objects(self):
        with self.app.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM work24_bge.course")).scalar(), 406)
            self.assertEqual(conn.execute(text("SELECT count(*) FROM work24_bge.course_unit")).scalar(), 6099)
        for statement in (
            "UPDATE work24_bge.course SET metadata=metadata WHERE false",
            "CREATE TABLE work24_bge.l0_should_not_exist(id integer)",
            "CREATE TABLE today_app.l0_should_not_exist(id integer)",
        ):
            with self.subTest(statement=statement), self.app.connect() as conn, self.assertRaises(DBAPIError):
                conn.execute(text(statement))

    def test_migrator_cannot_modify_education_schema(self):
        with self.migrator.connect() as conn, self.assertRaises(DBAPIError):
            conn.execute(text("CREATE TABLE work24_bge.l0_should_not_exist(id integer)"))

    def test_autogeneration_does_not_propose_catalog_or_unmanaged_table_deletions(self):
        with self.migrator.connect() as conn:
            context = MigrationContext.configure(conn, opts={
                "include_schemas": True,
                "include_name": include_migration_name,
                "include_object": include_migration_object,
                "version_table_schema": "today_app",
            })
            script = produce_migrations(context, Base.metadata)
        operations = script.upgrade_ops
        if operations is None:
            self.fail("autogeneration did not return an upgrade operation set")
        self.assertTrue(operations.is_empty())

    @unittest.skipUnless(os.environ.get("L0_REIMPORT_TESTS") == "1", "committed reimport is an explicit CI-only check")
    def test_reimport_keeps_application_rows_and_education_keys(self):
        table = Table(
            "l0_preserve_" + uuid4().hex,
            MetaData(schema="today_app"),
            Column("id", Integer, primary_key=True),
            Column("body", String, nullable=False),
        )
        table.create(self.migrator)
        self.addCleanup(table.drop, self.migrator)
        with self.app.begin() as conn:
            conn.execute(table.insert().values(id=1, body="preserve existing app input"))
        with self.admin.connect() as conn:
            before = conn.execute(text("SELECT unit_key,id FROM work24_bge.course_unit ORDER BY unit_key")).all()
        result = subprocess.run([
            "uv", "run", "--project", str(ROOT / "tools/catalog"), "--python", sys.executable,
            "--locked", "python", str(ROOT / "tools/catalog/bootstrap.py"),
        ], cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        with self.app.connect() as conn:
            self.assertEqual(conn.execute(table.select()).one().body, "preserve existing app input")
        with self.admin.connect() as conn:
            after = conn.execute(text("SELECT unit_key,id FROM work24_bge.course_unit ORDER BY unit_key")).all()
        self.assertEqual(before, after)
