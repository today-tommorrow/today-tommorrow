"""Recommendation/detail correspondence: synthetic providers, real persistence and catalog."""

import json
import os
import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_database_boundary import local_engine
from test_catalog_search import course, unit, search
from test_recommendation_flow import ANSWERS
from test_recommendation_pipeline import FakeModel
import test_profile_drafts as profiles

from app.catalog.repository import CatalogRepository, canonical_hash
from app.catalog.service import SearchService
from app.courses.identity import identity
from app.courses.presentation import present_course
from app.recommendations.models import RecommendationRun
from app.recommendations.pipeline import RecommendationPipeline, candidate_view
from app.recommendations.worker import run_once


class CourseCorrespondenceTests(unittest.TestCase):
    def test_selected_offering_evidence_identity_and_provenance(self):
        long_quote = "synthetic content " * 20
        courses = [course("same", "01", url="https://course.invalid/01", objective="first"),
                   course("same", "1", url="https://course.invalid/1", objective="second")]
        units = [unit("same", .2, tme="01"),
                 unit("same", .9, key="observed", tme="1", embed_text=long_quote,
                      source="https://unit.invalid/observed"),
                 unit("same", .8, key="inferred", tme="1", is_inferred=True, source="ncs_csv"),
                 unit("same", .7, key="bad-port", tme="1", source="https://unit.invalid:bad/path")]
        found = search(courses, units)
        candidate = found.candidates[0]
        self.assertEqual(candidate.source_tme, "1")
        view = candidate_view(candidate, found.snapshot)
        detail = present_course("same", courses, units, found.snapshot.dataset_id).model_dump(mode="json")
        self.assertNotEqual(detail["offerings"][0]["id"], view["offering"]["id"])
        selected = next(row for row in detail["offerings"] if row["id"] == view["offering"]["id"])
        self.assertEqual(selected, view["offering"])
        self.assertIsNone(selected["schedule"])
        self.assertIsNone(selected["tuition_krw"])
        self.assertIsNone(selected["listed_self_pay_krw"])
        by_id = {item["id"]: item for item in detail["evidence"]}
        for item in view["evidence"]:
            actual = by_id[item["id"]]
            self.assertEqual(item["field_path"], actual["field_path"])
            self.assertTrue(actual["quote"].startswith(item["quote"]))
            self.assertEqual(item["source_url"], actual["source_url"])
            self.assertIsNone(item["checked_at"])
            self.assertIsNone(actual["checked_at"])
        evidence = {item["field_path"]: item for item in view["evidence"]}
        self.assertEqual(evidence["units.observed"]["source_url"], "https://unit.invalid/observed")
        self.assertIsNone(evidence["inferred_units.inferred"]["source_url"])
        self.assertEqual(evidence["inferred_units.inferred"]["verification_state"], "unknown")
        self.assertEqual(evidence["units.bad-port"]["source_url"], "https://course.invalid/1")
        self.assertLess(len(evidence["units.observed"]["quote"]), len(long_quote))


class StoredVector:
    def __init__(self, vector):
        self.vector = vector

    def embed(self, text, *, revision):
        return self.vector


class SyntheticModel(FakeModel):
    def generate(self, stage, context, schema):
        if stage != "assessing":
            return super().generate(stage, context, schema)
        rows = []
        for candidate in context["candidates"]:
            proof = next((item for item in candidate["evidence"]
                          if item["kind"] == "content" and len(item["quote"].strip()) >= 4), None)
            axis = {"axis": "goal", "value": 1 if proof else None,
                    "evidence_ids": [proof["id"]] if proof else [],
                    "quotes": [proof["quote"]] if proof else []}
            rows.append({"course_id": candidate["course_id"], "offering_id": candidate["offering_id"],
                         "goal_basis": "content_supported" if proof else "unrelated", "axes": [axis,
                         {"axis": "prerequisite", "value": None, "evidence_ids": [], "quotes": []},
                         {"axis": "outcome", "value": None, "evidence_ids": [], "quotes": []}]})
        return {"assessments": rows}


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "requires isolated local catalog and PostgreSQL")
class PersistedCourseCorrespondenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.connect() as connection:
            row = connection.execute(text("SELECT profile_id,dataset_id,manifest FROM work24_bge.dataset_snapshot")).one()
            cls.profile, cls.dataset, cls.manifest = row.profile_id, row.dataset_id, canonical_hash(row.manifest)
            cls.vector = tuple(json.loads(connection.execute(text(
                "SELECT embedding::text FROM work24_bge.course_unit WHERE active AND NOT is_inferred ORDER BY unit_key LIMIT 1"
            )).scalar_one()))

    def setUp(self):
        self.fixture = profiles.ProfileDraftTests()
        self.fixture.engine = self.engine
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        environment = patch.dict(os.environ, {"CATALOG_PROFILE_ID": self.profile,
            "CATALOG_DATASET_ID": self.dataset, "CATALOG_MANIFEST_SHA256": self.manifest})
        environment.start()
        self.addCleanup(environment.stop)
        repository = CatalogRepository(self.engine, profile_id=self.profile, dataset_id=self.dataset)
        self.pipeline = RecommendationPipeline(SearchService(repository, StoredVector(self.vector)), SyntheticModel())
        configured = patch("app.recommendations.routes.build_runner", return_value=self.pipeline)
        configured.start()
        self.addCleanup(configured.stop)

    def test_persisted_recommendations_open_matching_details_and_pin_drift_fails_closed(self):
        profile = self.fixture.create()
        saved = self.fixture.save(profile, ANSWERS)
        self.assertEqual(saved.status_code, 200, saved.text)
        profile = saved.json()
        response = self.client.post(f"/api/profiles/{profile['id']}/submit",
            headers={"X-CSRF-Token": self.fixture.csrf, "Idempotency-Key": "synthetic-course-integration"},
            json={"expected_revision": profile["revision"], "review_token": profile["review_token"]})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(run_once(lambda: Session(self.engine), self.pipeline))
        job = self.client.get("/api/jobs/" + response.json()["job_id"]).json()
        self.assertEqual(job["status"], "succeeded", job)
        path = "/api/recommendations/" + job["result"]["recommendation_run_id"]
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["runtime_mode"], "sample")
        self.assertEqual(result["model_outcome"], "succeeded")
        self.assertTrue(result["items"])
        with Session(self.engine) as db:
            run = db.get(RecommendationRun, UUID(result["id"]))
            pin = deepcopy(run.provenance_json["context"]["catalog"])
        self.assertEqual((pin["profile_id"], pin["dataset_id"], pin["manifest_sha256"]),
                         (self.profile, self.dataset, self.manifest))
        self.assertEqual(result["catalog_version_id"], str(identity("catalog", self.dataset, self.profile, self.manifest)))
        for item in result["items"]:
            detail_response = self.client.get("/api/courses/" + item["course"]["id"])
            self.assertEqual(detail_response.status_code, 200, detail_response.text)
            detail = detail_response.json()
            self.assertEqual(detail["id"], item["course"]["id"])
            selected = [offering for offering in detail["offerings"] if offering["id"] == item["offering"]["id"]]
            self.assertEqual(selected, [item["offering"]])
            evidence = {entry["id"]: entry for entry in detail["evidence"]}
            for entry in item["evidence"]:
                self.assertIn(entry["id"], evidence)
                actual = evidence[entry["id"]]
                self.assertEqual(actual["field_path"], entry["field_path"])
                self.assertTrue(actual["quote"].startswith(entry["quote"]))
                self.assertEqual(actual["source_url"], entry["source_url"])
                self.assertIsNone(actual["checked_at"])
                self.assertIsNone(entry["checked_at"])
                if entry["field_path"].startswith("inferred_units."):
                    self.assertEqual(actual["verification_state"], "unknown")
                    self.assertEqual(entry["verification_state"], "unknown")
        with patch.dict(os.environ, {"CATALOG_MANIFEST_SHA256": "0" * 64}):
            self.assertEqual(self.client.get("/api/courses/" + result["items"][0]["course"]["id"]).status_code, 503)
            self.assertEqual(self.client.get(path).json(), result)
