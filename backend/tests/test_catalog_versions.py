"""Version-pinned consumers against an explicitly prepared local test catalog."""

import json
import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

import test_recommendation_flow as flow_tests
from sqlalchemy import event, text
from test_database_boundary import local_engine
from test_recommendation_pipeline import FakeModel
from test_recommendation_pipeline import snapshot as recommendation_input

from app.catalog.policy import course_id
from app.catalog.repository import CatalogRepository, canonical_hash
from app.catalog.service import SearchService
from app.courses.repository import CatalogPin, CourseRepository
from app.recommendations.pipeline import RecommendationPipeline, identity
from app.saved_courses.catalog import read_catalog


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "requires isolated local catalog")
class CatalogVersionTests(unittest.TestCase):
    def setUp(self):
        self.admin = local_engine("CATALOG_DATABASE_URL", "today_bootstrap")
        self.app = local_engine("DATABASE_URL", "today_app")
        self.addCleanup(self.admin.dispose)
        self.addCleanup(self.app.dispose)
        with self.admin.begin() as conn:
            self.original = conn.execute(text("SELECT dataset_id FROM work24_bge.active_catalog")).scalar_one()
            profile, manifest = conn.execute(text("SELECT profile_id,manifest FROM work24_bge.dataset_snapshot WHERE dataset_id=:id"), {"id": self.original}).one()
            self.pin = CatalogPin(profile, self.original, canonical_hash(manifest))
            self.repository = CatalogRepository(self.app, profile_id=profile, dataset_id=self.original)
            self.old = self.repository.prepare()
            self.vector = tuple(json.loads(conn.execute(text("SELECT embedding::text FROM work24_bge.snapshot_unit WHERE dataset_id=:id ORDER BY unit_key LIMIT 1"), {"id": self.original}).scalar_one()))
            inputs = {"synthetic_consumer_version": {"sha256": canonical_hash([self.original, "consumer-tests"])}}
            self.new = canonical_hash(inputs)
            updated = {**manifest, "dataset_id": self.new, "input_files": inputs}
            conn.execute(text("INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES(:id,:profile,CAST(:manifest AS jsonb))"), {"id": self.new, "profile": profile, "manifest": json.dumps(updated)})
            conn.execute(text("""INSERT INTO work24_bge.snapshot_course
                SELECT :new,course_id,tme,jsonb_set(metadata,'{course_name}','\"Updated public course\"'),source_dataset_id
                FROM work24_bge.snapshot_course WHERE dataset_id=:old"""), {"new": self.new, "old": self.original})
            columns = "unit_key,course_id,tme,vector_row,unit_type,ncs_unit_code,unit_name,embed_text,text_sha256,hours,source,is_noise,is_inferred,provenance,embedding,profile_id,active,source_dataset_id"
            conn.execute(text(f"INSERT INTO work24_bge.snapshot_unit(dataset_id,{columns}) SELECT :new,{columns} FROM work24_bge.snapshot_unit WHERE dataset_id=:old"), {"new": self.new, "old": self.original})
            self.sources = conn.execute(text("SELECT DISTINCT course_id FROM work24_bge.snapshot_course WHERE dataset_id=:id ORDER BY course_id LIMIT 2"), {"id": self.original}).scalars().all()
            self.source = self.sources[0]
        self.addCleanup(self.cleanup_snapshot)

    def cleanup_snapshot(self):
        with self.admin.begin() as conn:
            conn.execute(text("UPDATE work24_bge.active_catalog SET dataset_id=:id"), {"id": self.original})
            for table in ("snapshot_unit", "snapshot_course", "dataset_snapshot"):
                conn.execute(text(f"DELETE FROM work24_bge.{table} WHERE dataset_id=:id"), {"id": self.new})

    def activate(self, dataset):
        with self.admin.begin() as conn:
            conn.execute(text("UPDATE work24_bge.active_catalog SET dataset_id=:id"), {"id": dataset})

    def test_search_and_queued_recommendation_keep_the_captured_version(self):
        provider = Mock()
        provider.embed.return_value = self.vector
        pipeline = RecommendationPipeline(SearchService(self.repository, provider), FakeModel())
        submitted = recommendation_input(pipeline)
        self.activate(self.new)
        self.assertEqual(self.repository.prepare().dataset_id, self.new)
        old = self.repository.search(self.vector, self.old, as_of=date(2026, 11, 1), limit=1)
        current = SearchService(self.repository, provider).search("public synthetic query", as_of=date(2026, 11, 1), limit=1)
        self.assertEqual(old.snapshot.dataset_id, self.original)
        self.assertEqual(current.candidates[0].metadata["course_name"], "Updated public course")
        result = pipeline.run(submitted, lambda _phase: None)
        self.assertEqual(result["_snapshot"]["search"]["snapshot"]["dataset_id"], self.original)
        self.assertNotEqual(result["catalog_version_id"], str(read_catalog(self.app).version))

    def test_detail_compare_and_saved_catalog_follow_one_active_snapshot(self):
        self.activate(self.new)
        detail = CourseRepository(self.app, self.pin).details([course_id(self.source)])[0]
        self.assertEqual(detail.name, "Updated public course")
        self.assertEqual(read_catalog(self.app).courses[course_id(self.source)].summary.name, detail.name)
        # Activation between pointer resolution and row reads cannot mix versions.
        switched = []
        def after_read(_connection, _cursor, statement, _parameters, _context, _many):
            if "FROM work24_bge.active_catalog a" in statement and not switched:
                switched.append(True)
                self.activate(self.original)
        event.listen(self.app, "after_cursor_execute", after_read)
        try:
            compared = CourseRepository(self.app, self.pin).details([course_id(source) for source in self.sources])
        finally:
            event.remove(self.app, "after_cursor_execute", after_read)
        self.assertEqual([detail.name for detail in compared], ["Updated public course"] * 2)
        self.assertEqual(len(switched), 1)
        self.assertNotEqual(CourseRepository(self.app, self.pin).details([course_id(self.source)])[0].name, "Updated public course")

    def test_persisted_recommendation_reloads_after_activation(self):
        fixture = flow_tests.RecommendationFlowTests()
        fixture.engine = self.app
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        provider = Mock()
        provider.embed.return_value = self.vector
        pipeline = RecommendationPipeline(SearchService(self.repository, provider), FakeModel())
        with patch("app.recommendations.routes.build_runner", return_value=pipeline):
            submitted = fixture.submit()
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.activate(self.new)
        self.assertTrue(fixture.work(pipeline))
        job = fixture.job(submitted)
        self.assertEqual(job["status"], "succeeded", job)
        url = "/api/recommendations/" + job["result"]["recommendation_run_id"]
        saved = fixture.client.get(url)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["catalog_version_id"], identity("catalog", self.original, self.pin.profile_id, self.pin.manifest_sha256))
        self.assertNotEqual(saved.json()["catalog_version_id"], str(read_catalog(self.app).version))
        self.activate(self.original)
        self.assertEqual(fixture.client.get(url).json(), saved.json())
