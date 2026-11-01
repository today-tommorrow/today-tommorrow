"""Pure retrieval contracts and read-only checks against the guarded local corpus."""

import json
import os
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from unittest.mock import Mock

from sqlalchemy import event, text
from test_database_boundary import local_engine

from app.catalog.contracts import (
    CatalogError,
    CatalogSnapshot,
    SearchFilters,
    json_value,
    validated_vector,
)
from app.catalog.policy import course_id, evaluate, offering_id, source_date
from app.catalog.repository import (
    REQUIRED_CONFIG,
    CatalogRepository,
    assemble,
    canonical_hash,
)
from app.catalog.service import SearchService

AS_OF = date(2026, 11, 1)
SNAPSHOT = CatalogSnapshot("a" * 64, "b" * 64, "revision", "c" * 64, AS_OF)
VECTOR = (1.0,) + (0.0,) * 1023


def course(identifier: str, tme: str = "1", **metadata: object) -> dict[str, object]:
    return {"course_id": identifier, "tme": tme, "metadata": {
        "course_name": "synthetic training", "mode": "온라인", "start_date": "2026-11-02T00:00:00",
        "region": "서울", "training_fee": None, "displayed_self_pay": None, **metadata,
    }}


def unit(identifier: str, similarity: float, key: str = "unit", tme: str = "1", **extra: object) -> dict[str, object]:
    return {"course_id": identifier, "tme": tme, "unit_key": key, "unit_name": "synthetic unit",
            "embed_text": "synthetic public competency", "text_sha256": "d" * 64,
            "unit_type": "content_item", "ncs_unit_code": None, "source": "page", "hours": None,
            "is_inferred": False, "is_noise": False, "provenance": [{"file": "synthetic.csv"}],
            "distance": 1.0 - similarity, **extra}


def search(courses: list[dict[str, object]], units: list[dict[str, object]], *, filters: SearchFilters = SearchFilters(), limit: int = 30):
    return assemble(SNAPSHOT, courses, units, as_of=AS_OF, filters=filters, limit=limit)


class CatalogContractTests(unittest.TestCase):
    def test_course_grouping_precedes_final_limit(self):
        rows = [unit("many", 0.99, key=f"u{i:03}") for i in range(150)]
        rows += [unit("second", 0.8), unit("third", 0.7)]
        result = search([course("many"), course("second"), course("third")], rows, limit=3)
        self.assertEqual([c.source_course_id for c in result.candidates], ["many", "second", "third"])
        self.assertEqual(result.eligible_courses, 3)

    def test_multiple_offerings_are_filtered_before_course_winner(self):
        result = search([course("same", "1", mode="서울 집체"), course("same", "2")],
                        [unit("same", .99, tme="1"), unit("same", .7, tme="2")],
                        filters=SearchFilters(delivery_mode="online"))
        self.assertEqual(result.candidates[0].source_tme, "2")
        self.assertEqual(result.excluded_counts["LISTED_DELIVERY_MODE"], 1)

    def test_ties_negative_scores_and_names_do_not_merge_distinct_keys(self):
        result = search([course("b"), course("a", "2"), course("a", "1")],
                        [unit("b", -.3), unit("a", -.3, key="z", tme="2"), unit("a", -.3, key="x", tme="1")])
        self.assertEqual([(c.source_course_id, c.source_tme) for c in result.candidates], [("a", "1"), ("b", "1")])
        self.assertLess(result.candidates[0].similarity, 0)

    def test_ids_preserve_course_offering_and_delimiter_boundaries(self):
        self.assertEqual(str(course_id("A")), "2bcab23b-4ba1-567e-9489-1f6b42a272a7")
        self.assertEqual(str(offering_id("A", "1")), "29175ab0-f8a6-53b6-b64b-a1400f4e1b23")
        self.assertNotEqual(course_id("A"), offering_id("A", "1"))
        self.assertNotEqual(offering_id("A", "1"), offering_id("A", "01"))
        self.assertNotEqual(offering_id("A,B", "C"), offering_id("A", "B,C"))

    def test_unknown_price_and_timetable_do_not_exclude_or_become_free(self):
        result = search([course("a", training_fee=5000000, displayed_self_pay=0)], [unit("a", .5)],
                        filters=SearchFilters(budget_krw=0, availability="evening_weekend", field_code="ai_data"))
        candidate = result.candidates[0]
        self.assertEqual({c.status for c in candidate.conditions if c.field != "delivery_mode"}, {"unknown"})
        self.assertEqual(candidate.metadata["training_fee"], 5000000)
        self.assertEqual(result.unsupported_conditions, ("field_code",))
        self.assertIsNone(candidate.checked_at)

    def test_null_inferred_provenance_are_preserved_and_immutable(self):
        row = unit("a", .5, is_inferred=True, source="ncs_csv", unit_type="ncs_fallback", ncs_unit_code="2001020101")
        result = search([course("a", objective=None)], [row])
        candidate = result.candidates[0]
        self.assertTrue(candidate.evidence[0].is_inferred)
        self.assertIsNone(candidate.metadata["objective"])
        self.assertEqual(candidate.evidence[0].source, "ncs_csv")
        self.assertEqual(candidate.evidence[0].ncs_unit_code, "2001020101")
        with self.assertRaises(TypeError):
            candidate.metadata["objective"] = "changed"
        self.assertEqual(json_value(result)["candidates"][0]["evidence"][0]["provenance"], [{"file": "synthetic.csv"}])

    def test_source_noise_violation_is_not_silently_filtered(self):
        with self.assertRaisesRegex(CatalogError, "invalid_catalog_metadata"):
            search([course("a")], [unit("a", .5, is_noise=True)])

    def test_date_unknown_and_explicit_bounds_and_exact_region(self):
        rows = [course("a"), course("b", start_date=None), course("c", start_date="2026-12-01")]
        result = search(rows, [unit(key, .5) for key in "abc"], filters=SearchFilters(start_to=date(2026, 11, 30)))
        self.assertEqual([c.source_course_id for c in result.candidates], ["a", "b"])
        self.assertEqual(result.candidates[1].conditions[-1].status, "unknown")
        self.assertIsNone(source_date("2026-11-01Tbad"))
        self.assertEqual(source_date("2026-11-01T00:00:00+09:00"), AS_OF)
        self.assertEqual(evaluate({"region": "서울 강남구"}, SearchFilters(listed_region="서울"))[-1].status, "fail")

    def test_empty_reasons_and_closed_snapshot_not_live_status(self):
        self.assertEqual(search([], []).empty_reason, "no_active_catalog")
        self.assertEqual(search([course("a")], [unit("a", .5)], filters=SearchFilters(delivery_mode="offline")).empty_reason, "all_filtered")
        self.assertEqual(len(search([course("a", recruitment_status="모집마감", end_date="2020-01-01")], [unit("a", .5)]).candidates), 1)

    def test_unknown_modes_remain_unknown_and_hybrid_is_not_invented(self):
        for value in (None, "", "new mode", ["온라인"]):
            with self.subTest(value=value):
                result = search([course("a", mode=value)], [unit("a", .5)], filters=SearchFilters(delivery_mode="online"))
                self.assertEqual(result.candidates[0].conditions[0].status, "unknown")
        result = search([course("a")], [unit("a", .5)], filters=SearchFilters(delivery_mode="hybrid"))
        self.assertEqual(result.empty_reason, "all_filtered")

    def test_profile_semantics_and_manifest_identity_fail_closed(self):
        config = deepcopy(REQUIRED_CONFIG)
        inputs = {"synthetic.csv": {"sha256": "d" * 64}}
        profile, dataset = canonical_hash(config), canonical_hash(inputs)
        manifest = {"embedding_config": config, "embedding_profile_id": profile,
                    "dataset_id": dataset, "input_files": inputs, "created_on": "2026-11-01"}
        repository, connection = CatalogRepository(Mock(), profile_id=profile, dataset_id=dataset), Mock()
        connection.execute.return_value.first.return_value = (config, manifest)
        self.assertEqual(repository._snapshot(connection).snapshot_created_on, AS_OF)
        for key, value in (("revision", "unreviewed"), ("max_length", 8192), ("normalize_embeddings", 1),
                           ("pooling", "mean"), ("postprocessing", "one L2 only"), ("document_preprocessing", "strip")):
            changed = {**config, key: value}
            connection.execute.return_value.first.return_value = (changed, manifest)
            with self.subTest(key=key), self.assertRaisesRegex(CatalogError, "incompatible_embedding_profile"):
                repository._snapshot(connection)
        for key, value in (("is_fast", 1), ("padding_side", "left")):
            changed = deepcopy(config)
            changed["tokenizer"][key] = value
            connection.execute.return_value.first.return_value = (changed, manifest)
            with self.subTest(key=key), self.assertRaisesRegex(CatalogError, "incompatible_embedding_profile"):
                repository._snapshot(connection)
        for changed in ({**manifest, "dataset_id": "0" * 64}, {**manifest, "input_files": {}},
                        {**manifest, "embedding_profile_id": "0" * 64}, {**manifest, "embedding_config": {}}):
            connection.execute.return_value.first.return_value = (config, changed)
            with self.assertRaisesRegex(CatalogError, "catalog_identity_mismatch"):
                repository._snapshot(connection)

    def test_input_validation_does_not_normalize_bad_vectors(self):
        self.assertEqual(validated_vector(VECTOR), VECTOR)
        for value in [VECTOR[:-1], (2.0,) + VECTOR[1:], (float("nan"),) + VECTOR[1:], (True,) + VECTOR[1:]]:
            with self.subTest(value=value[0]), self.assertRaisesRegex(CatalogError, "invalid_query_vector"):
                validated_vector(value)
        for kwargs in [{"budget_krw": -1}, {"budget_krw": True}, {"start_from": datetime.now()}, {"delivery_mode": "remote"}, {"start_from": date(2027, 1, 1), "start_to": AS_OF}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(CatalogError):
                SearchFilters(**kwargs)

    def test_embedding_call_is_outside_preparation_and_query_is_verbatim(self):
        repository, provider = Mock(), Mock()
        order = []
        repository.prepare.side_effect = lambda: (order.append("prepared-and-closed"), SNAPSHOT)[1]
        provider.embed.side_effect = lambda query, revision: (order.append("embedding"), VECTOR)[1]
        repository.search.side_effect = lambda *args, **kwargs: (order.append("search"), "result")[1]
        self.assertEqual(SearchService(repository, provider).search("  기초\n학습  ", as_of=AS_OF), "result")
        provider.embed.assert_called_once_with("  기초\n학습  ", revision="revision")
        self.assertEqual(order, ["prepared-and-closed", "embedding", "search"])

    def test_invalid_request_and_provider_failure_never_search(self):
        repository, provider = Mock(), Mock()
        service = SearchService(repository, provider)
        for query in [" ", "\ud800", "a" * 65537]:
            with self.assertRaisesRegex(CatalogError, "invalid_search_query"):
                service.search(query, as_of=AS_OF)
        with self.assertRaisesRegex(CatalogError, "invalid_search_limit"):
            service.search("valid", as_of=AS_OF, limit=0)
        repository.prepare.assert_not_called()
        repository.prepare.return_value = SNAPSHOT
        provider.embed.side_effect = ValueError("embedding_failed")
        with self.assertRaisesRegex(ValueError, "embedding_failed"):
            service.search("valid", as_of=AS_OF)
        repository.search.assert_not_called()


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "requires isolated local catalog")
class CatalogDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.connect() as connection:
            cls.profile, cls.dataset = connection.execute(text("SELECT DISTINCT profile_id,dataset_id FROM work24_bge.course_unit WHERE active")).one()
            cls.vector = tuple(json.loads(connection.execute(text("SELECT embedding::text FROM work24_bge.course_unit ORDER BY unit_key LIMIT 1")).scalar_one()))
        cls.repository = CatalogRepository(cls.engine, profile_id=cls.profile, dataset_id=cls.dataset)

    def test_real_all_course_exact_oracle_and_bounded_evidence(self):
        snapshot = self.repository.prepare()
        result = self.repository.search(self.vector, snapshot, as_of=AS_OF, limit=30, evidence_limit=2)
        with self.engine.connect() as connection:
            oracle = connection.execute(text("""SELECT course_id,min(embedding <=> CAST(:vector AS vector)) distance
                FROM work24_bge.course_unit WHERE active AND profile_id=:profile AND dataset_id=:dataset
                GROUP BY course_id ORDER BY distance,course_id LIMIT 30"""),
                {"vector": json.dumps(self.vector), "profile": self.profile, "dataset": self.dataset}).all()
        self.assertEqual(result.active_offerings, 406)
        self.assertEqual(result.eligible_courses, 406)
        self.assertEqual([c.source_course_id for c in result.candidates], [row.course_id for row in oracle])
        for candidate, row in zip(result.candidates, oracle, strict=True):
            self.assertAlmostEqual(candidate.similarity, 1-row.distance, places=6)
            self.assertLessEqual(len(candidate.evidence), 2)
        self.assertFalse(result.approximate)

    def test_identity_snapshot_and_vector_failures_are_explicit(self):
        snapshot = self.repository.prepare()
        with self.assertRaisesRegex(CatalogError, "catalog_snapshot_changed"):
            self.repository.search(self.vector, replace(snapshot, manifest_sha256="0" * 64), as_of=AS_OF)
        wrong = CatalogRepository(self.engine, profile_id="0" * 64, dataset_id=self.dataset)
        with self.assertRaisesRegex(CatalogError, "catalog_snapshot_unavailable"):
            wrong.prepare()
        with self.assertRaisesRegex(CatalogError, "invalid_query_vector"):
            self.repository.search(VECTOR[:-1], snapshot, as_of=AS_OF)

    def test_actual_provider_boundary_has_no_checked_out_db_connection(self):
        checked_out = []
        def checkout(*_args): checked_out.append(True)
        def checkin(*_args): checked_out.pop()
        event.listen(self.engine, "checkout", checkout)
        event.listen(self.engine, "checkin", checkin)
        try:
            provider = Mock()
            def embed(query, *, revision):
                self.assertEqual(checked_out, [])
                self.assertEqual(query, "actual stored-vector boundary fixture")
                return self.vector
            provider.embed.side_effect = embed
            SearchService(self.repository, provider).search("actual stored-vector boundary fixture", as_of=AS_OF, limit=1)
            self.assertEqual(checked_out, [])
        finally:
            event.remove(self.engine, "checkout", checkout)
            event.remove(self.engine, "checkin", checkin)

    def test_every_repository_transaction_is_read_only(self):
        observed = []
        def inspect_transaction(connection, _cursor, statement, _parameters, _context, _many):
            if "work24_bge" in statement:
                observed.append(connection.exec_driver_sql("SHOW transaction_read_only").scalar_one())
        event.listen(self.engine, "before_cursor_execute", inspect_transaction)
        try:
            snapshot = self.repository.prepare()
            self.repository.search(self.vector, snapshot, as_of=AS_OF, limit=1)
        finally:
            event.remove(self.engine, "before_cursor_execute", inspect_transaction)
        self.assertTrue(observed)
        self.assertEqual(set(observed), {"on"})
