"""Synthetic provider tests; no live model, source database or fallback results."""

import copy
import unittest
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from app.catalog.contracts import CatalogSnapshot, SearchResult, freeze_json
from app.catalog.repository import assemble
from app.recommendations.contracts import (
    RecommendationError,
    score_bounds,
    validate_result,
)
from app.recommendations.pipeline import (
    RecommendationPipeline,
    assessments,
    candidate_view,
    conditions,
    confirmed_sources,
    explanation_items,
    ranked_selection,
)

CATALOG = CatalogSnapshot("a" * 64, "b" * 64, "revision", "c" * 64, date(2026, 11, 1))


def catalog_result(count: int = 1) -> SearchResult:
    courses = [{"course_id": str(i), "tme": "1", "metadata": {
        "course_name": f"파이썬 분석 교육 {i}", "institution": f"기관 {i}", "mode": "온라인",
        "objective": "파이썬 데이터 분석 기초와 실습", "start_date": "2026-11-02T00:00:00",
        "displayed_self_pay": None, "training_fee": 100000, "training_hours": 20,
    }} for i in range(count)]
    units = [{"course_id": str(i), "tme": "1", "unit_key": f"unit-{i}",
              "unit_name": "파이썬 분석", "embed_text": "파이썬 데이터 분석 기초와 실습",
              "text_sha256": "d" * 64, "unit_type": "content_item", "ncs_unit_code": None,
              "source": "public-synthetic", "hours": None, "is_inferred": False, "is_noise": False,
              "provenance": [], "distance": .1} for i in range(count)]
    from app.catalog.contracts import SearchFilters
    return assemble(CATALOG, courses, units, as_of=CATALOG.snapshot_created_on,
                    filters=SearchFilters(delivery_mode="online"), limit=30)


class FakeSearch:
    def __init__(self, result: SearchResult) -> None:
        self.result = result
        self.repository = SimpleNamespace(prepare=lambda: result.snapshot)
        self.calls = []

    def search(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        return self.result


class FakeModel:
    model_id = "synthetic-model"
    runtime_mode = "sample"
    identity = {"model_id": "synthetic-model", "version": "test-1"}

    def __init__(self) -> None:
        self.calls = []
        self.mutate = lambda stage, result: result

    def generate(self, stage, context, schema):
        self.calls.append((stage, copy.deepcopy(context)))
        if stage == "profiling":
            source = context["sources"][0]
            result = {"needs": [{"source_id": source["id"], "label": source["text"]}]}
        elif stage == "assessing":
            rows = []
            for candidate in context["candidates"]:
                proof = next(item for item in candidate["evidence"] if item["kind"] == "content")
                rows.append({"course_id": candidate["course_id"], "offering_id": candidate["offering_id"],
                             "goal_basis": "content_supported", "axes": [
                                 {"axis": "goal", "value": 1, "evidence_ids": [proof["id"]], "quotes": [proof["quote"]]},
                                 {"axis": "prerequisite", "value": None, "evidence_ids": [], "quotes": []},
                                 {"axis": "outcome", "value": .5, "evidence_ids": [proof["id"]], "quotes": [proof["quote"]]},
                             ]})
            result = {"assessments": rows}
        else:
            rows = []
            for candidate in context["candidates"]:
                proof = next(item for item in candidate["evidence"] if item["kind"] == "content")
                rows.append({"course_id": candidate["course_id"], "offering_id": candidate["offering_id"],
                             "claims": [{"source_id": context["sources"][0]["id"],
                                         "evidence_id": proof["id"], "quote": proof["quote"]}]})
            result = {"explanations": rows}
        return self.mutate(stage, result)


def snapshot(pipeline: RecommendationPipeline) -> dict:
    return {"profile_id": "00000000-0000-4000-8000-000000000001", "user_id": "synthetic-user",
            "profile_revision": 8, "answers": {
                "document_decision": "none", "field_code": "ai_data", "learning_goal": "foundation",
                "goal_text": "파이썬 데이터 분석 기초", "experience": "unknown", "availability": "evening_weekend",
                "delivery_mode": "online", "budget_status": "amount", "budget_krw": 0, "start_timing": "soon",
            }, "reviewed_items": [], "selection_summaries": [], "context": pipeline.capture_context(), "as_of": "2026-11-01"}


class RecommendationPipelineTests(unittest.TestCase):
    def make_pipeline(self, count=1):
        search, model = FakeSearch(catalog_result(count)), FakeModel()
        return RecommendationPipeline(search, model), search, model

    def test_document_free_flow_preserves_scores_unknowns_and_sources(self):
        pipeline, search, model = self.make_pipeline()
        source = snapshot(pipeline)
        before = copy.deepcopy(source)
        phases = []
        result = pipeline.run(source, phases.append)
        self.assertEqual(source, before)
        self.assertEqual(result["runtime_mode"], "sample")
        self.assertEqual(result["model_outcome"], "succeeded")
        self.assertEqual(result["result_state"], "unknown_conditions")
        breakdown = result["items"][0]["rank_breakdown"]
        self.assertEqual((breakdown["score_lower"], breakdown["score_upper"], breakdown["coverage"]), (.65, .9, .75))
        checks = {item["field"]: item["status"] for item in breakdown["constraint_checks"]}
        self.assertEqual(checks["budget"], "unknown")
        self.assertEqual(checks["schedule"], "unknown")
        self.assertEqual(checks["region"], "not_applicable")
        self.assertIsNone(result["items"][0]["offering"]["checked_at"])
        self.assertTrue(all(e["checked_at"] is None for e in result["items"][0]["evidence"]))
        self.assertEqual(result["_snapshot"]["query"], search.calls[0][0])
        self.assertIn("파이썬 데이터 분석 기초", search.calls[0][0])
        self.assertEqual(result["_snapshot"]["search"]["candidates"][0]["metadata"]["training_fee"], 100000)
        self.assertEqual([stage for stage, _ in model.calls], ["profiling", "assessing", "explaining"])
        self.assertEqual(phases[-1], "validating")

    def test_capture_context_does_not_call_models_and_rejects_drift(self):
        pipeline, search, model = self.make_pipeline()
        source = snapshot(pipeline)
        self.assertFalse(model.calls)
        source["context"]["config"]["candidate_limit"] = 999
        with self.assertRaisesRegex(RecommendationError, "context_changed"):
            pipeline.run(source, lambda _: None)
        self.assertFalse(search.calls)

    def test_catalog_changed_after_embedding_fails_before_model(self):
        pipeline, search, model = self.make_pipeline()
        source = snapshot(pipeline)
        search.result = replace(search.result, snapshot=replace(CATALOG, dataset_id="e" * 64))
        with self.assertRaisesRegex(RecommendationError, "context_changed"):
            pipeline.run(source, lambda _: None)
        self.assertFalse(model.calls)

    def test_empty_candidates_skip_model_and_do_not_fabricate_success_items(self):
        pipeline, _, model = self.make_pipeline(0)
        result = pipeline.run(snapshot(pipeline), lambda _: None)
        self.assertEqual((result["result_state"], result["model_outcome"], result["items"]), ("empty", "not_needed", []))
        self.assertEqual(result["explanation_mode"], "none")
        self.assertFalse(model.calls)

    def test_unrelated_model_outcome_is_succeeded_not_not_needed(self):
        pipeline, _, model = self.make_pipeline()
        def unrelated(stage, result):
            if stage == "assessing":
                result["assessments"][0]["goal_basis"] = "unrelated"
                result["assessments"][0]["axes"][0]["value"] = 0
            return result
        model.mutate = unrelated
        result = pipeline.run(snapshot(pipeline), lambda _: None)
        self.assertEqual(result["result_state"], "empty")
        self.assertEqual(result["model_outcome"], "succeeded")
        self.assertEqual(result["items"], [])
        self.assertNotIn("explaining", [stage for stage, _ in model.calls])

    def test_grounding_failure_repairs_once_then_fails_no_fallback(self):
        pipeline, _, model = self.make_pipeline()
        def invented(stage, result):
            if stage == "explaining":
                result["explanations"][0]["claims"][0]["quote"] = "무료 취업 보장 100%"
            return result
        model.mutate = invented
        with self.assertRaisesRegex(RecommendationError, "invalid_model_grounding"):
            pipeline.run(snapshot(pipeline), lambda _: None)
        self.assertEqual([stage for stage, _ in model.calls].count("explaining"), 2)

    def test_unrelated_positive_goal_contradiction_repairs_then_fails(self):
        pipeline, _, model = self.make_pipeline()
        def contradictory(stage, result):
            if stage == "assessing":
                result["assessments"][0]["goal_basis"] = "unrelated"
            return result
        model.mutate = contradictory
        with self.assertRaisesRegex(RecommendationError, "invalid_model_grounding"):
            pipeline.run(snapshot(pipeline), lambda _: None)
        self.assertEqual([stage for stage, _ in model.calls].count("assessing"), 2)
        self.assertNotIn("explaining", [stage for stage, _ in model.calls])

    def test_unsupported_zero_receives_specific_repair_without_relaxing_grounding(self):
        pipeline, _, model = self.make_pipeline()
        def repair_zero(stage, result):
            if stage == "assessing":
                row = result["assessments"][0]
                row["goal_basis"] = "unrelated"
                repair = model.calls[-1][1].get("validation_error", "")
                for axis in row["axes"]:
                    axis.update(value=None if "including 0" in repair else 0, evidence_ids=[], quotes=[])
            return result
        model.mutate = repair_zero
        result = pipeline.run(snapshot(pipeline), lambda _: None)
        self.assertEqual([stage for stage, _ in model.calls].count("assessing"), 2)
        self.assertEqual((result["result_state"], result["model_outcome"]), ("empty", "succeeded"))
        self.assertTrue(all(axis["value"] is None for axis in result["_snapshot"]["assessments"][0]["axes"][:3]))

    def test_topic_only_zero_gets_symmetric_repair_guidance(self):
        pipeline, _, model = self.make_pipeline()
        def repair_basis(stage, result):
            if stage == "assessing":
                row = result["assessments"][0]
                repair = model.calls[-1][1].get("validation_error", "")
                row["goal_basis"] = "unrelated" if "Goal 0/null requires unrelated" in repair else "topic_only"
                row["axes"][0]["value"] = 0
            return result
        model.mutate = repair_basis
        result = pipeline.run(snapshot(pipeline), lambda _: None)
        self.assertEqual([stage for stage, _ in model.calls].count("assessing"), 2)
        self.assertEqual((result["result_state"], result["model_outcome"]), ("empty", "succeeded"))
        assessment = result["_snapshot"]["assessments"][0]
        self.assertEqual(assessment["goal_basis"], "unrelated")
        self.assertEqual(assessment["axes"][0]["value"], 0)
        self.assertTrue(assessment["axes"][0]["evidence_ids"])

    def test_phase_fence_interrupts_after_model_before_next_stage(self):
        pipeline, _, model = self.make_pipeline()
        def report(stage):
            if stage == "profiling" and model.calls:
                raise RuntimeError("lease_lost")
        with self.assertRaisesRegex(RuntimeError, "lease_lost"):
            pipeline.run(snapshot(pipeline), report)
        self.assertEqual(len(model.calls), 1)

    def test_selected_wording_is_retained_and_field_code_not_used_as_unsupported_gate(self):
        pipeline, _, _ = self.make_pipeline()
        source = snapshot(pipeline)
        source["selection_summaries"] = [{"target_slot": "learning_goal", "label": "주말 파이썬 실습만 희망"}]
        sources, query = confirmed_sources(source)
        self.assertIn("주말 파이썬 실습만 희망", query)
        self.assertIn("인공지능 데이터 분석", query)
        self.assertTrue(any(item["text"] == "주말 파이썬 실습만 희망" for item in sources))

    def test_documents_are_not_silently_ignored(self):
        pipeline, _, _ = self.make_pipeline()
        source = snapshot(pipeline)
        source["answers"]["document_decision"] = "provided"
        with self.assertRaisesRegex(RecommendationError, "materials_unsupported"):
            pipeline.run(source, lambda _: None)

    def test_score_unknowns_are_not_zero(self):
        self.assertEqual(score_bounds({"goal": 1, "prerequisite": None, "outcome": .5, "preference": 1}), (.65, .9, .75))
        with self.assertRaises(RecommendationError):
            score_bounds({"goal": True, "prerequisite": None, "outcome": .5, "preference": 1})

    def test_validator_rejects_corrupt_persisted_score_or_reference(self):
        pipeline, _, _ = self.make_pipeline()
        result = pipeline.run(snapshot(pipeline), lambda _: None)
        for edit in (lambda r: r["items"][0]["rank_breakdown"].update(score_lower=.99),
                     lambda r: r["items"][0]["reasons"][0].update(evidence_ids=["00000000-0000-4000-8000-000000000099"])):
            bad = copy.deepcopy(result)
            edit(bad)
            with self.assertRaisesRegex(RecommendationError, "invalid_recommendation_result"):
                validate_result(bad)

    def test_provider_cap_stable_ties_and_unknown_provider_identity(self):
        pipeline, _, _ = self.make_pipeline(4)
        result = pipeline.run(snapshot(pipeline), lambda _: None)
        rows = copy.deepcopy(result["_snapshot"]["assessments"])
        for row in rows:
            row["course"]["provider_name"] = "same provider"
        kept, excluded = ranked_selection(rows[::-1])
        self.assertEqual(len(kept), 2)
        self.assertEqual(excluded["PROVIDER_CAP"], 2)
        self.assertEqual([r["course"]["id"] for r in kept], sorted(r["course"]["id"] for r in rows)[:2])
        for row in rows:
            row["course"]["provider_name"] = None
        self.assertEqual(len(ranked_selection(rows)[0]), 4)

    def test_inferred_units_preserve_unknown_and_cannot_be_content_supported(self):
        candidate = catalog_result().candidates[0]
        candidate = replace(candidate, metadata=freeze_json({**candidate.metadata, "objective": None}),
                            evidence=(replace(candidate.evidence[0], is_inferred=True),))
        view = candidate_view(candidate, CATALOG)
        view["checks"] = conditions(candidate, view, {"availability": "unknown"})
        inferred = next(e for e in view["evidence"] if e["kind"] == "inferred")
        self.assertEqual(inferred["verification_state"], "unknown")
        row = {"course_id": view["course"]["id"], "offering_id": view["offering"]["id"],
               "goal_basis": "content_supported", "axes": [
                   {"axis": "goal", "value": 1, "evidence_ids": [inferred["id"]], "quotes": [inferred["quote"]]},
                   {"axis": "prerequisite", "value": None, "evidence_ids": [], "quotes": []},
                   {"axis": "outcome", "value": None, "evidence_ids": [], "quotes": []},
               ]}
        with self.assertRaisesRegex(RecommendationError, "invalid_model_grounding"):
            assessments({"assessments": [row]}, [view])
        row["goal_basis"] = "topic_only"
        self.assertEqual(assessments({"assessments": [row]}, [view])[0]["goal_basis"], "topic_only")

    def test_foreign_explanation_evidence_is_rejected(self):
        pipeline, _, _ = self.make_pipeline()
        source = snapshot(pipeline)
        result = pipeline.run(source, lambda _: None)
        candidate = result["_snapshot"]["assessments"][0]
        sources, _ = confirmed_sources(source)
        output = {"explanations": [{"course_id": candidate["course"]["id"], "offering_id": candidate["offering"]["id"],
                                   "claims": [{"source_id": sources[0]["id"], "evidence_id": "foreign", "quote": "파이썬 분석"}]}]}
        with self.assertRaisesRegex(RecommendationError, "invalid_model_grounding"):
            explanation_items(output, [candidate], sources)


if __name__ == "__main__":
    unittest.main()
