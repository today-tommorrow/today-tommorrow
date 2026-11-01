"""Offline judgments and diagnostics around SearchService; never a ranking policy."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from sqlalchemy.exc import SQLAlchemyError

from app.ai.embeddings import EmbeddingError

from .contracts import (
    CatalogError,
    CatalogSnapshot,
    SearchFilters,
    SearchResult,
    freeze_json,
    json_value,
    validate_options,
)
from .service import SearchService


class EvaluationError(ValueError):
    """A safe evaluator code; input text and infrastructure details are excluded."""


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    query: str
    as_of: date
    filters: SearchFilters
    judgment_status: Literal["judged", "unjudged"]
    relevant: tuple[str, ...]
    irrelevant: tuple[str, ...]
    reason: str
    expected_empty: bool | None

    @property
    def metric_eligible(self) -> bool:
        return self.judgment_status == "judged" and bool(self.relevant)


@dataclass(frozen=True)
class EvaluationDataset:
    id: str
    version: str
    description: str
    sha256: str
    profile_id: str
    dataset_id: str
    revision: str
    provenance: object
    cases: tuple[EvaluationCase, ...]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise EvaluationError("invalid_evaluation_dataset")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError("invalid_evaluation_dataset")
    value.encode("utf-8")
    return value


def _identifiers(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise EvaluationError("invalid_evaluation_dataset")
    result = tuple(_string(item) for item in value)
    if len(set(result)) != len(result):
        raise EvaluationError("duplicate_judgment_identifier")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError("duplicate_evaluation_json_key")
        result[key] = value
    return result


def _case(value: object) -> EvaluationCase:
    row = _object(value)
    if set(row) - {"id", "query", "as_of", "filters", "judgment", "expected_empty"}:
        raise EvaluationError("invalid_evaluation_case_fields")
    judgment = _object(row["judgment"])
    if set(judgment) != {"status", "relevant_course_ids", "irrelevant_course_ids", "reason"}:
        raise EvaluationError("invalid_evaluation_judgment_fields")
    status = judgment["status"]
    if status not in ("judged", "unjudged"):
        raise EvaluationError("invalid_evaluation_judgment")
    relevant = _identifiers(judgment["relevant_course_ids"])
    irrelevant = _identifiers(judgment["irrelevant_course_ids"])
    if set(relevant) & set(irrelevant) or (status == "unjudged" and (relevant or irrelevant)):
        raise EvaluationError("contradictory_evaluation_judgment")
    expected_empty = row.get("expected_empty")
    if expected_empty is not None and type(expected_empty) is not bool:
        raise EvaluationError("invalid_empty_expectation")
    if expected_empty is True and relevant:
        raise EvaluationError("contradictory_empty_expectation")
    options = _object(row.get("filters", {})).copy()
    for key in ("start_from", "start_to"):
        if options.get(key) is not None:
            options[key] = date.fromisoformat(_string(options[key]))
    filters = SearchFilters(**options)
    query = _string(row["query"])
    if len(query.encode("utf-8")) > 65536:
        raise EvaluationError("invalid_evaluation_query")
    return EvaluationCase(
        _string(row["id"]), query, date.fromisoformat(_string(row["as_of"])), filters,
        cast(Literal["judged", "unjudged"], status), relevant, irrelevant,
        _string(judgment["reason"]), expected_empty,
    )


def load_dataset(path: Path) -> EvaluationDataset:
    """Freeze judgments before any model/database access. Hash exact input bytes."""
    try:
        raw = path.read_bytes()
        row = _object(json.loads(raw, object_pairs_hook=_unique_object))
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise EvaluationError("unsupported_evaluation_schema")
        catalog = _object(row["catalog"])
        for key in ("profile_id", "dataset_id"):
            value = _string(catalog[key])
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise EvaluationError("invalid_evaluation_catalog_identity")
        values = row["cases"]
        if not isinstance(values, list) or not values:
            raise EvaluationError("empty_evaluation_dataset")
        cases = tuple(_case(value) for value in values)
        if len({case.id for case in cases}) != len(cases):
            raise EvaluationError("duplicate_evaluation_case")
        provenance = freeze_json(_object(row["provenance"]))
        return EvaluationDataset(
            _string(row["id"]), _string(row["version"]), _string(row["description"]),
            hashlib.sha256(raw).hexdigest(), _string(catalog["profile_id"]),
            _string(catalog["dataset_id"]), _string(catalog["revision"]), provenance, cases,
        )
    except EvaluationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise EvaluationError("invalid_evaluation_dataset") from None


def diagnose(case: EvaluationCase, result: SearchResult, cutoffs: tuple[int, ...]) -> dict[str, object]:
    """Use source IDs exactly; unjudged results are never counted as false positives."""
    identifiers = [candidate.source_course_id for candidate in result.candidates]
    relevant, irrelevant = set(case.relevant), set(case.irrelevant)
    counts = Counter(identifiers)
    first_rank = next((rank for rank, key in enumerate(identifiers, 1) if key in relevant), None)
    metrics: dict[str, object] = {}
    for cutoff in cutoffs:
        found = set(identifiers[:cutoff]) & relevant
        metrics[str(cutoff)] = {
            "expected_set_recall": len(found) / len(relevant) if case.metric_eligible else None,
            "reciprocal_rank": 1 / first_rank if case.metric_eligible and first_rank and first_rank <= cutoff else (0.0 if case.metric_eligible else None),
            "missing_expected_course_ids": sorted(relevant - found),
        }
    return {
        "status": "ok", "returned_count": len(identifiers), "unique_course_count": len(counts),
        "metric_eligible": case.metric_eligible, "metrics_at_k": metrics,
        "candidates": [{"rank": rank, "source_course_id": candidate.source_course_id,
                        "source_tme": candidate.source_tme, "similarity": candidate.similarity,
                        "judgment": "relevant" if candidate.source_course_id in relevant else (
                            "irrelevant" if candidate.source_course_id in irrelevant else "unjudged")}
                       for rank, candidate in enumerate(result.candidates, 1)],
        "false_positive_course_ids": sorted(set(identifiers) & irrelevant),
        "unjudged_course_ids": sorted(set(identifiers) - relevant - irrelevant),
        "duplicate_course_ids": {key: count for key, count in sorted(counts.items()) if count > 1},
        "empty": not identifiers, "empty_reason": result.empty_reason,
        "empty_expectation_matches": (not identifiers) == case.expected_empty if case.expected_empty is not None else None,
        "unexpected_empty": not identifiers and (bool(relevant) or case.expected_empty is False),
        "eligible_courses": result.eligible_courses, "active_offerings": result.active_offerings,
        "excluded_counts": dict(result.excluded_counts),
        "unsupported_conditions": list(result.unsupported_conditions),
    }


def validate_evaluation_options(
    dataset: EvaluationDataset, cutoffs: tuple[int, ...], limit: int, evidence_limit: int,
) -> None:
    if not cutoffs or any(type(k) is not int or k < 1 or k > limit for k in cutoffs) or len(set(cutoffs)) != len(cutoffs):
        raise EvaluationError("invalid_evaluation_cutoffs")
    for case in dataset.cases:
        validate_options(case.as_of, case.filters, limit, evidence_limit)


def evaluate_dataset(
    dataset: EvaluationDataset, service: SearchService, snapshot: CatalogSnapshot, *,
    cutoffs: tuple[int, ...], limit: int, evidence_limit: int,
    execution_mode: Literal["synthetic", "live_bge_cuda"], environment: Mapping[str, object],
) -> dict[str, object]:
    """Run the unchanged service against one retained snapshot; no retries/fallbacks."""
    if (dataset.profile_id, dataset.dataset_id, dataset.revision) != (snapshot.profile_id, snapshot.dataset_id, snapshot.revision):
        raise EvaluationError("evaluation_catalog_mismatch")
    validate_evaluation_options(dataset, cutoffs, limit, evidence_limit)
    if execution_mode not in ("synthetic", "live_bge_cuda"):
        raise EvaluationError("invalid_evaluation_execution_mode")
    rows: list[dict[str, object]] = []
    versions: set[str] = set()
    for case in dataset.cases:
        started = monotonic()
        row: dict[str, object] = {
            "id": case.id, "query": case.query, "as_of": case.as_of.isoformat(),
            "filters": json_value(case.filters), "judgment_status": case.judgment_status,
            "judgment_reason": case.reason, "expected_course_ids": list(case.relevant),
            "judged_irrelevant_course_ids": list(case.irrelevant), "expected_empty": case.expected_empty,
        }
        try:
            result = service.search(case.query, as_of=case.as_of, filters=case.filters,
                                    limit=limit, evidence_limit=evidence_limit, snapshot=snapshot)
            if result.snapshot != snapshot:
                raise EvaluationError("evaluation_snapshot_changed")
            if result.retrieval_mode != "exact" or result.approximate:
                raise EvaluationError("evaluation_retrieval_mode_changed")
            versions.add(result.retrieval_config_version)
            row.update(diagnose(case, result, cutoffs))
        except (CatalogError, EmbeddingError, SQLAlchemyError) as error:
            if isinstance(error, CatalogError) and str(error) == "catalog_snapshot_changed":
                raise EvaluationError("evaluation_snapshot_changed") from None
            # Never include arbitrary driver/provider exception text in artifacts.
            kind = "catalog" if isinstance(error, CatalogError) else (
                "embedding" if isinstance(error, EmbeddingError) else "database")
            row.update(status="error", error="search_execution_failed", error_kind=kind, metric_eligible=False)
        row["elapsed_seconds"] = round(monotonic() - started, 6)
        rows.append(row)
    summary: dict[str, object] = {
        "requested_cases": len(rows), "successful_cases": sum(row["status"] == "ok" for row in rows),
        "failed_cases": sum(row["status"] == "error" for row in rows),
        "unjudged_cases": sum(case.judgment_status == "unjudged" for case in dataset.cases),
        "requested_metric_eligible_cases": sum(case.metric_eligible for case in dataset.cases),
        "successful_metric_eligible_cases": sum(row["metric_eligible"] is True for row in rows),
        "empty_cases": sum(row.get("empty") is True for row in rows),
        "unexpected_empty_cases": sum(row.get("unexpected_empty") is True for row in rows),
        "empty_expectation_mismatches": sum(row.get("empty_expectation_matches") is False for row in rows),
        "cases_with_false_positives": sum(bool(row.get("false_positive_course_ids")) for row in rows),
        "cases_with_duplicates": sum(bool(row.get("duplicate_course_ids")) for row in rows),
    }
    aggregate: dict[str, object] = {}
    for cutoff in cutoffs:
        eligible = [cast(dict[str, dict[str, float]], row["metrics_at_k"])[str(cutoff)]
                    for row in rows if row["metric_eligible"] is True]
        aggregate[str(cutoff)] = {
            "macro_expected_set_recall": sum(metric["expected_set_recall"] for metric in eligible) / len(eligible) if eligible else None,
            "mrr": sum(metric["reciprocal_rank"] for metric in eligible) / len(eligible) if eligible else None,
            "cases_with_missing_expected": sum(metric["expected_set_recall"] < 1 for metric in eligible),
        }
    summary["metrics_at_k"] = aggregate
    return {
        "report_schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": execution_mode,
        "actual_bge_results": execution_mode == "live_bge_cuda" and summary["successful_cases"] > 0,
        "metric_scope": "Recall measures only preconfirmed expected courses, not exhaustive corpus relevance. Unjudged returns are not false positives. Failed searches are excluded from successful metrics and counted separately.",
        "evaluation_dataset": {"id": dataset.id, "version": dataset.version, "sha256": dataset.sha256,
                               "description": dataset.description, "provenance": json_value(dataset.provenance),
                               "catalog": {"profile_id": dataset.profile_id, "dataset_id": dataset.dataset_id,
                                           "revision": dataset.revision}},
        "snapshot": json_value(snapshot), "search_settings": {
            "limit": limit, "evidence_limit": evidence_limit, "cutoffs": list(cutoffs),
            "retrieval_mode": "exact", "approximate": False,
            "retrieval_config_versions": sorted(versions),
        },
        "environment": dict(environment), "summary": summary, "cases": rows,
    }


def render_report(report: Mapping[str, object]) -> str:
    """A compact companion to the JSON artifact; no evidence text or vectors."""
    summary = cast(dict[str, object], report["summary"])
    dataset = cast(dict[str, object], report["evaluation_dataset"])
    lines = ["# Work24 search evaluation", "", f"Mode: {report['execution_mode']}",
             f"Actual BGE results: {report['actual_bge_results']}",
             f"Dataset: {dataset['id']} / {dataset['version']} / SHA256 `{dataset['sha256']}`", "",
             str(report["metric_scope"]), "", "```json",
             json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), "```", "",
             "| Case | Status | Returned | Missing expected at largest k | False positives | Unjudged | Duplicates | Empty reason |",
             "| --- | --- | ---: | --- | --- | --- | --- | --- |"]
    settings = cast(dict[str, object], report["search_settings"])
    largest_k = str(max(cast(list[int], settings["cutoffs"])))
    for row in cast(list[dict[str, object]], report["cases"]):
        metrics = cast(dict[str, dict[str, object]], row.get("metrics_at_k", {}))
        cells = [row["id"], row["status"], row.get("returned_count", "—"),
                 metrics.get(largest_k, {}).get("missing_expected_course_ids", "—"),
                 row.get("false_positive_course_ids", []), row.get("unjudged_course_ids", []),
                 row.get("duplicate_course_ids", {}),
                 row.get("empty_reason") or row.get("error") or "—"]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    lines.extend(["", "The JSON companion records each fixed judgment, missing expected IDs at every k, unjudged returns, filters, snapshot, settings and environment.", ""])
    return "\n".join(lines)
