"""Public source HTTP contracts against the prepared read-only PostgreSQL catalog."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_profile_drafts as profiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from test_database_boundary import local_engine

from app.courses.errors import CourseError
from app.courses.identity import course_id, identity, offering_id
from app.courses.presentation import present_course, source_number, source_url
from app.courses.repository import CatalogPin, CourseRepository, canonical_hash
from app.main import app


class CoursePresentationTests(unittest.TestCase):
    def test_stable_exact_source_keys(self):
        self.assertEqual(str(course_id("x")), "cecb7c98-f518-524d-a456-7e799be4db43")
        self.assertEqual(str(offering_id("x", "01")), "83ba254d-ca04-5b1a-bba9-ce1694916443")
        self.assertEqual(str(identity("evidence", "dataset", "x", "01", "units.unit")), "5beebae7-ce79-5c87-8786-daecb3d0cbd6")
        self.assertNotEqual(offering_id("x", "1"), offering_id("x", "01"))
        self.assertNotEqual(course_id("x"), course_id("X"))

    def test_null_zero_inferred_and_differing_offerings(self):
        courses = [
            {"tme": "01", "metadata": {"course_name": "공개 과정", "institution": "기관", "mode": "온라인", "objective": "목표", "training_fee": 0, "displayed_self_pay": None}},
            {"tme": "1", "metadata": {"course_name": "공개 과정", "institution": "기관", "mode": "서울 집체", "objective": "다른 목표", "training_fee": None, "displayed_self_pay": 0}},
        ]
        units = [{"tme": "01", "unit_key": "unit", "embed_text": "추정 교육내용", "source": "inferred", "is_inferred": True, "is_noise": False}]
        result = present_course("public", courses, units, "dataset")
        self.assertIsNone(result.description)
        self.assertIsNone(result.prerequisites)
        self.assertEqual(result.learning_outcomes, [])
        self.assertEqual(result.offerings[0].tuition_krw, 0)
        self.assertIsNone(result.offerings[0].listed_self_pay_krw)
        self.assertIsNone(result.offerings[1].tuition_krw)
        self.assertEqual(result.offerings[1].listed_self_pay_krw, 0)
        self.assertIsNone(result.offerings[0].schedule)
        self.assertIsNone(result.evidence[-1].source_url)
        self.assertEqual(result.evidence[-1].verification_state, "unknown")
        self.assertEqual(result.evidence[-1].field_path, "inferred_units.unit")
        self.assertTrue(all(item.checked_at is None for item in result.evidence))
        self.assertTrue(any(item.verification_state == "conflicting" for item in result.evidence))

    def test_invalid_urls_numbers_and_noise_are_not_presented_as_facts(self):
        for value in [True, -1, float("inf"), float("nan"), "0"]:
            self.assertIsNone(source_number(value))
        self.assertIsNone(source_number(.5, integer=True))
        for value in ["javascript:alert(1)", "https://user:secret@example.com", "https://example.com:invalid"]:
            self.assertIsNone(source_url(value))
        with self.assertRaises(CourseError):
            present_course("x", [{"tme": "1", "metadata": {"course_name": "공개"}}], [{"tme": "1", "unit_key": "x", "embed_text": "x", "is_inferred": False, "is_noise": True}], "dataset")

    def test_routes_require_authentication_without_provider_configuration(self):
        client = TestClient(app)
        for path in [f"/api/courses/{uuid4()}", f"/api/courses/compare?ids={uuid4()},{uuid4()}"]:
            self.assertEqual(client.get(path).status_code, 401)


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "run after local DB setup")
class CourseAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.connect() as connection:
            row = connection.execute(text("SELECT profile_id,dataset_id,manifest FROM work24_bge.dataset_snapshot")).one()
            cls.pin = CatalogPin(row.profile_id, row.dataset_id, canonical_hash(row.manifest))
            cls.sources = connection.execute(text("SELECT DISTINCT course_id FROM work24_bge.course_unit WHERE active ORDER BY course_id LIMIT 3")).scalars().all()
            cls.ids = [str(course_id(value)) for value in cls.sources]
            cls.inferred_source = connection.execute(text("SELECT course_id FROM work24_bge.course_unit WHERE active AND is_inferred ORDER BY course_id LIMIT 1")).scalar_one()

    def setUp(self):
        self.fixture = profiles.ProfileDraftTests()
        self.fixture.engine = self.engine
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        config = patch.dict(os.environ, {"CATALOG_PROFILE_ID": self.pin.profile_id, "CATALOG_DATASET_ID": self.pin.dataset_id, "CATALOG_MANIFEST_SHA256": self.pin.manifest_sha256})
        config.start()
        self.addCleanup(config.stop)

    def test_detail_reads_real_metadata_all_offerings_and_units(self):
        response = self.client.get(f"/api/courses/{self.ids[0]}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["id"], self.ids[0])
        self.assertFalse(body["is_demo"])
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT tme,metadata FROM work24_bge.course WHERE course_id=:id ORDER BY tme"), {"id": self.sources[0]}).all()
            units = connection.execute(text("SELECT unit_key,embed_text FROM work24_bge.course_unit WHERE course_id=:id AND active ORDER BY unit_key"), {"id": self.sources[0]}).all()
        self.assertEqual(body["name"], rows[0].metadata["course_name"])
        self.assertEqual([item["id"] for item in body["offerings"]], [str(offering_id(self.sources[0], row.tme)) for row in rows])
        for unit in units:
            found = [e for e in body["evidence"] if e["field_path"].endswith(unit.unit_key)]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["quote"], unit.embed_text)
        self.assertTrue(all(item["checked_at"] is None for item in body["evidence"]))

    def test_authenticated_catalog_read_needs_only_one_pool_connection(self):
        engine = create_engine(
            self.engine.url, pool_size=1, max_overflow=0, pool_timeout=0.1,
            connect_args={"hostaddr": "127.0.0.1", "options": "", "connect_timeout": 3},
        )
        self.addCleanup(engine.dispose)
        with patch.object(self.fixture, "engine", engine):
            for path in [f"/api/courses/{self.ids[0]}",
                         f"/api/courses/compare?ids={','.join(self.ids[:2])}"]:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(engine.pool.checkedout(), 0)

    def test_inferred_source_is_preserved_without_claiming_verification(self):
        body = self.client.get(f"/api/courses/{course_id(self.inferred_source)}").json()
        inferred = [e for e in body["evidence"] if e["field_path"].startswith("inferred_units.")]
        self.assertTrue(inferred)
        self.assertTrue(all(e["verification_state"] == "unknown" and e["checked_at"] is None for e in inferred))

    def test_compare_static_route_preserves_order_and_exact_details(self):
        for ids in [self.ids[:2][::-1], self.ids[::-1]]:
            response = self.client.get('/api/courses/compare', params={"ids": ','.join(ids)})
            self.assertEqual(response.status_code, 200, response.text)
            items = response.json()["items"]
            self.assertEqual([item["id"] for item in items], ids)
            for item in items:
                self.assertEqual(item, self.client.get('/api/courses/' + item["id"]).json())

    def test_compare_input_is_strict_and_case_duplicates_rejected(self):
        queries = ["", "ids=", f"ids={self.ids[0]}", "ids=" + ','.join(self.ids + [str(uuid4())]),
                   f"ids={self.ids[0]},{self.ids[0].upper()}", f"ids={self.ids[0]},bad", f"ids={self.ids[0]},{self.ids[1]}&ids={self.ids[2]}",
                   f"ids={self.ids[0]},%20{self.ids[1]}"]
        for query in queries:
            with self.subTest(query=query):
                response = self.client.get('/api/courses/compare?' + query)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "invalid_course_request")
        self.assertEqual(self.client.get('/api/courses/not-a-uuid').status_code, 422)

    def test_missing_id_rejects_whole_comparison_without_partial_results(self):
        for path in [f'/api/courses/{uuid4()}', f'/api/courses/compare?ids={self.ids[0]},{uuid4()}']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertNotIn("items", response.json())

    def test_unconfigured_or_changed_catalog_is_not_empty_success(self):
        for name in ["CATALOG_PROFILE_ID", "CATALOG_DATASET_ID", "CATALOG_MANIFEST_SHA256"]:
            with patch.dict(os.environ, {name: "0" * 64}):
                response = self.client.get('/api/courses/' + self.ids[0])
                self.assertEqual(response.status_code, 503, response.text)
                self.assertNotIn("items", response.json())
        with patch.dict(os.environ, {"CATALOG_MANIFEST_SHA256": ""}):
            self.assertEqual(self.client.get('/api/courses/' + self.ids[0]).status_code, 503)

    def test_read_only_transaction_and_source_write_permission(self):
        observations = []
        def verify(connection, cursor, statement, parameters, context, executemany):
            if 'SELECT c.course_id,c.tme,c.metadata' in statement:
                with connection.connection.cursor() as probe:
                    probe.execute('SHOW transaction_read_only')
                    observations.append(probe.fetchone()[0])
                    probe.execute('SHOW transaction_isolation')
                    observations.append(probe.fetchone()[0])
        event.listen(self.engine, 'before_cursor_execute', verify)
        try:
            result = CourseRepository(self.engine, self.pin).details([course_id(self.sources[0])])
            self.assertEqual(len(result), 1)
        finally:
            event.remove(self.engine, 'before_cursor_execute', verify)
        self.assertEqual(observations, ['on', 'repeatable read'])
        with self.engine.connect() as connection:
            with self.assertRaises(DBAPIError):
                connection.execute(text('UPDATE work24_bge.course SET metadata=metadata WHERE false'))
