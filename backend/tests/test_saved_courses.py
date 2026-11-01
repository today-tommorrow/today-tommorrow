"""Saved courses against real PostgreSQL, public catalog data and synthetic sessions."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import test_profile_drafts as profiles
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.main import app
from app.saved_courses.catalog import canonical, course_uuid, read_catalog, safe_url
from app.saved_courses.models import SavedCourse
from app.saved_courses.service import save


class SavedContractTests(unittest.TestCase):
    def test_routes_require_authentication(self):
        with TestClient(app) as client:
            for method, url in [("GET", "/api/me/saved-courses"), ("PUT", f"/api/me/saved-courses/{uuid4()}"), ("DELETE", f"/api/me/saved-courses/{uuid4()}")]:
                self.assertEqual(client.request(method, url).status_code, 401)

    def test_identity_preserves_exact_source_keys(self):
        self.assertEqual(course_uuid("sample"), course_uuid("sample"))
        self.assertNotEqual(course_uuid("01"), course_uuid("1"))
        self.assertNotEqual(course_uuid("ABC"), course_uuid("abc"))
        self.assertEqual(str(course_uuid("sample")), 'a366ebba-213c-5343-8806-9996bd6c3c7a')

    def test_source_urls_do_not_allow_credentials_or_non_http_schemes(self):
        for url in (None, "javascript:alert(1)", "https://user:pass@example.org", "https://[invalid"):
            self.assertIsNone(safe_url(url))
        self.assertEqual(safe_url("https://www.work24.go.kr/path"), "https://www.work24.go.kr/path")

    def test_openapi_course_page_contract(self):
        schema = TestClient(app).get("/openapi.json").json()
        self.assertEqual(set(schema["components"]["schemas"]["CoursePage"]["required"]), {"items", "next_cursor", "catalog_version_id"})
        self.assertIn("204", schema["paths"]["/api/me/saved-courses/{course_id}"]["put"]["responses"])


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "disposable PostgreSQL required")
class SavedDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.admin = local_engine("CATALOG_DATABASE_URL", "today_bootstrap")
        cls.addClassCleanup(cls.engine.dispose)
        cls.addClassCleanup(cls.admin.dispose)

    def setUp(self):
        self.fixture = profiles.ProfileDraftTests()
        self.fixture.engine = self.engine
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client, self.csrf, self.owner = self.fixture.client, self.fixture.csrf, self.fixture.user_id
        self.catalog = read_catalog(self.engine)
        self.ids = list(self.catalog.courses)[:5]

    def save(self, identifier, client=None, csrf=None):
        return (client or self.client).put(f"/api/me/saved-courses/{identifier}", headers={"X-CSRF-Token": csrf or self.csrf})

    def remove(self, identifier, client=None, csrf=None):
        return (client or self.client).delete(f"/api/me/saved-courses/{identifier}", headers={"X-CSRF-Token": csrf or self.csrf})

    def listing(self, **params):
        return self.client.get("/api/me/saved-courses", params=params)

    def test_save_replay_reload_and_repeated_delete(self):
        identifier = self.ids[0]
        self.assertEqual(self.save(identifier).status_code, 204)
        with Session(self.engine) as db:
            before = db.get(SavedCourse, (self.owner, identifier))
            saved_at, snapshot = before.created_at, before.saved_snapshot
        self.assertEqual(self.save(identifier).status_code, 204)
        response = self.listing()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["id"], str(identifier))
        self.assertEqual(item["catalog_status"], "current")
        self.assertFalse(item["is_demo"])
        self.assertEqual(item["saved_catalog_version_id"], body["catalog_version_id"])
        self.assertIsNone(body["next_cursor"])
        with Session(self.engine) as db:
            row = db.get(SavedCourse, (self.owner, identifier))
            self.assertEqual(row.created_at, saved_at)
            self.assertEqual(row.saved_snapshot, snapshot)
        for _ in range(2):
            self.assertEqual(self.remove(identifier).status_code, 204)
        self.assertEqual(self.listing().json()["items"], [])

    def test_parallel_put_is_idempotent(self):
        barrier = Barrier(4)

        def synchronize_insert(connection, cursor, statement, parameters, context, executemany):
            # All requests have observed no save before PostgreSQL arbitrates
            # concurrent inserts against both owner-scoped unique constraints.
            if context.isinsert and context.compiled.statement.table.name == "saved_courses":
                barrier.wait(timeout=10)

        def put(identifier):
            with TestClient(app, base_url=profiles.ORIGIN) as client:
                client.cookies.set("today_tommorrow_session", self.fixture.token)
                return self.save(identifier, client=client)

        event.listen(self.engine, "before_cursor_execute", synchronize_insert)
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for identifier in self.ids:
                    with self.subTest(identifier=identifier):
                        responses = list(pool.map(put, [identifier] * 4))
                        self.assertEqual([r.status_code for r in responses], [204] * 4)
        finally:
            event.remove(self.engine, "before_cursor_execute", synchronize_insert)
        with Session(self.engine) as db:
            rows = db.scalars(select(SavedCourse).where(SavedCourse.user_id == self.owner)).all()
            self.assertEqual(len(rows), len(self.ids))
            before = {row.course_id: (row.created_at, row.saved_catalog_version_id, row.saved_snapshot) for row in rows}
            for row in rows:
                self.assertEqual(row.saved_catalog_version_id, self.catalog.version)
                self.assertEqual(row.saved_snapshot, self.catalog.courses[row.course_id].summary.model_dump(mode="json"))
        for identifier in self.ids:
            self.assertEqual(self.save(identifier).status_code, 204)
        with Session(self.engine) as db:
            rows = db.scalars(select(SavedCourse).where(SavedCourse.user_id == self.owner)).all()
            self.assertEqual({row.course_id: (row.created_at, row.saved_catalog_version_id, row.saved_snapshot) for row in rows}, before)

    def test_save_does_not_suppress_foreign_key_errors(self):
        with Session(self.engine) as db:
            with self.assertRaises(IntegrityError) as raised:
                save(db, uuid4(), self.ids[0], self.catalog)
            self.assertEqual(raised.exception.orig.sqlstate, "23503")
            db.rollback()

    def test_accounts_are_isolated_even_for_same_public_course(self):
        self.assertEqual(self.save(self.ids[0]).status_code, 204)
        other, csrf, _, _ = self.fixture.login()
        self.assertEqual(other.get("/api/me/saved-courses").json()["items"], [])
        self.assertEqual(self.remove(self.ids[0], client=other, csrf=csrf).status_code, 204)
        self.assertEqual(len(self.listing().json()["items"]), 1)
        self.assertEqual(self.save(self.ids[0], client=other, csrf=csrf).status_code, 204)
        self.assertEqual(self.remove(self.ids[0]).status_code, 204)
        self.assertEqual(len(other.get("/api/me/saved-courses").json()["items"]), 1)

    def test_csrf_invalid_id_unknown_course_and_query_bounds(self):
        url = f"/api/me/saved-courses/{self.ids[0]}"
        for method in ("PUT", "DELETE"):
            self.assertEqual(self.client.request(method, url).status_code, 403)
            self.assertEqual(self.client.request(method, url, headers={"X-CSRF-Token": "wrong"}).status_code, 403)
            self.assertEqual(self.client.request(method, url, headers={"X-CSRF-Token": self.csrf, "Origin": "https://foreign.invalid"}).status_code, 403)
        self.assertEqual(self.save(uuid4()).status_code, 404)
        self.assertEqual(self.save("not-uuid").status_code, 422)
        for params in ({"limit": 0}, {"limit": 101}, {"limit": "no"}, {"cursor": ""}):
            self.assertEqual(self.listing(**params).status_code, 422)

    def test_keyset_ties_mutations_and_signed_owner_cursor(self):
        for identifier in self.ids[:4]:
            self.assertEqual(self.save(identifier).status_code, 204)
        with Session(self.engine) as db, db.begin():
            db.execute(update(SavedCourse).where(SavedCourse.user_id == self.owner).values(created_at=datetime(2026, 1, 1, tzinfo=UTC)))
        first = self.listing(limit=2).json()
        self.assertEqual(len(first["items"]), 2)
        cursor = first["next_cursor"]
        self.assertTrue(cursor)
        self.assertEqual(self.listing(cursor=cursor + "x").status_code, 400)
        self.assertEqual(self.listing(cursor="not-a-cursor").status_code, 400)
        other, _, _, _ = self.fixture.login()
        self.assertEqual(other.get("/api/me/saved-courses", params={"cursor": cursor}).status_code, 400)
        # New head rows do not duplicate previously visited items; deleting the
        # cursor row remains valid because ordering uses its immutable key.
        self.assertEqual(self.save(self.ids[4]).status_code, 204)
        self.assertEqual(self.remove(first["items"][-1]["id"]).status_code, 204)
        second = self.listing(limit=2, cursor=cursor).json()
        ids = [item["id"] for item in first["items"] + second["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {str(x) for x in self.ids[:4]})
        self.assertIsNone(second["next_cursor"])

    def test_catalog_switch_preserves_snapshots_and_invalidates_cursor(self):
        for identifier in self.ids[:3]:
            self.assertEqual(self.save(identifier).status_code, 204)
        old = self.listing(limit=1).json()
        changed = self.catalog.courses[self.ids[0]]
        removed = self.catalog.courses[self.ids[1]]
        original_name = changed.summary.name
        with self.admin.connect() as connection:
            dataset, profile, manifest = connection.execute(text("SELECT dataset_id,profile_id,manifest FROM work24_bge.dataset_snapshot")).one()
        import hashlib
        inputs = {"synthetic_switch": {"sha256": "e" * 64}}
        replacement = hashlib.sha256(canonical(inputs).encode()).hexdigest()
        def restore():
            with self.admin.begin() as connection:
                connection.execute(text("UPDATE work24_bge.active_catalog SET dataset_id=:old"), {"old": dataset})
                for table in ("snapshot_unit", "snapshot_course", "dataset_snapshot"):
                    connection.execute(text(f"DELETE FROM work24_bge.{table} WHERE dataset_id=:id"), {"id": replacement})
        self.addCleanup(restore)
        with self.admin.begin() as connection:
            connection.execute(text("INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES (:new,:profile,CAST(:manifest AS jsonb))"), {"new": replacement, "profile": profile, "manifest": json.dumps({**manifest, "dataset_id": replacement, "input_files": inputs})})
            connection.execute(text("""INSERT INTO work24_bge.snapshot_course
                SELECT :new,course_id,tme,metadata,source_dataset_id FROM work24_bge.snapshot_course
                WHERE dataset_id=:old AND course_id<>:removed"""), {"new": replacement, "old": dataset, "removed": removed.source_id})
            columns = "unit_key,course_id,tme,vector_row,unit_type,ncs_unit_code,unit_name,embed_text,text_sha256,hours,source,is_noise,is_inferred,provenance,embedding,profile_id,active,source_dataset_id"
            connection.execute(text(f"INSERT INTO work24_bge.snapshot_unit(dataset_id,{columns}) SELECT :new,{columns} FROM work24_bge.snapshot_unit WHERE dataset_id=:old AND course_id<>:removed"), {"new": replacement, "old": dataset, "removed": removed.source_id})
            connection.execute(text("UPDATE work24_bge.snapshot_course SET metadata=jsonb_set(metadata,'{course_name}',to_jsonb(CAST(:name AS text))) WHERE dataset_id=:new AND course_id=:id"), {"new": replacement, "name": original_name + " (합성 갱신)", "id": changed.source_id})
            connection.execute(text("UPDATE work24_bge.active_catalog SET dataset_id=:new"), {"new": replacement})
        page = self.listing().json()
        self.assertNotEqual(page["catalog_version_id"], old["catalog_version_id"])
        items = {item["id"]: item for item in page["items"]}
        current = items[str(self.ids[0])]
        self.assertEqual(current["name"], original_name + " (합성 갱신)")
        self.assertEqual(current["catalog_status"], "changed")
        self.assertEqual(current["saved_catalog_version_id"], old["catalog_version_id"])
        unavailable = items[str(self.ids[1])]
        self.assertEqual(unavailable["name"], removed.summary.name)
        self.assertEqual(unavailable["catalog_status"], "unavailable")
        self.assertEqual(self.listing(cursor=old["next_cursor"]).status_code, 409)
        # Re-saving never overwrites the original saved snapshot or time.
        self.assertEqual(self.save(self.ids[0]).status_code, 204)
        self.assertEqual(self.save(self.ids[1]).status_code, 204)
        with Session(self.engine) as db:
            self.assertEqual(db.get(SavedCourse, (self.owner, self.ids[0])).saved_snapshot["name"], original_name)
        self.assertEqual(self.remove(self.ids[1]).status_code, 204)
        self.assertEqual(len(self.listing().json()["items"]), 2)

    def test_catalog_failure_is_explicit_but_delete_still_works(self):
        self.assertEqual(self.save(self.ids[0]).status_code, 204)
        from app.saved_courses.catalog import CatalogUnavailable
        with patch("app.saved_courses.routes.read_catalog", side_effect=CatalogUnavailable):
            self.assertEqual(self.listing().status_code, 503)
            self.assertEqual(self.save(self.ids[1]).status_code, 503)
            self.assertEqual(self.remove(self.ids[0]).status_code, 204)

    def test_user_deletion_cascades_only_own_saves(self):
        self.assertEqual(self.save(self.ids[0]).status_code, 204)
        self.fixture.clean()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(SavedCourse).where(SavedCourse.user_id == self.owner)), 0)
