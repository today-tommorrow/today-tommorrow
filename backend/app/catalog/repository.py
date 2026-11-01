"""Read-only exact cosine retrieval over a pinned Work24 corpus snapshot."""

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from .contracts import (
    CatalogError,
    CatalogSnapshot,
    CourseCandidate,
    SearchFilters,
    SearchResult,
    UnitEvidence,
    freeze_json,
    validate_options,
    validated_vector,
)
from .policy import course_id, evaluate, offering_id

BGE_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
REQUIRED_CONFIG: dict[str, object] = {
    "repository": "BAAI/bge-m3", "revision": BGE_REVISION,
    "dimensions": 1024, "max_length": 512, "query_max_length": 512,
    "passage_max_length": 512, "pooling": "cls", "inference_dtype": "float16",
    "storage_dtype": "float32", "normalize_embeddings": True,
    "return_dense": True, "return_sparse": False, "return_colbert_vecs": False,
    "quantization": None, "truncate_dim": None, "attention_implementation": "sdpa",
    "postprocessing": "FlagEmbedding internal L2 -> numpy.float32 -> L2 again with denominator max(norm,1e-12)",
    "query_preprocessing": "verbatim competency sentence; no strip, rewrite or prefix",
    "document_preprocessing": "verbatim embed_text; no strip, rewrite, chunking, Unicode normalization or prefix",
    "tokenizer": {
        "class": "XLMRobertaTokenizerFast", "is_fast": True,
        "padding": "dynamic_per_batch", "padding_side": "right", "truncation_side": "right",
        "add_special_tokens": True, "truncation": True,
    },
}

# Materialize every matching unit before window ranking; no unit LIMIT can starve a course.
UNITS_SQL = """
WITH matching AS MATERIALIZED (
  SELECT course_id,tme,unit_key,unit_name,embed_text,text_sha256,unit_type,ncs_unit_code,
         source,hours,is_inferred,is_noise,provenance,
         embedding <=> CAST(:vector AS vector) AS distance
   FROM work24_bge.snapshot_unit
  WHERE active AND profile_id=:profile AND dataset_id=:dataset
), ranked AS (
  SELECT matching.*,row_number() OVER (
    PARTITION BY course_id,tme ORDER BY distance,unit_key
  ) AS evidence_rank FROM matching
)
SELECT * FROM ranked WHERE evidence_rank<=:evidence_limit
ORDER BY course_id,tme,evidence_rank
"""
COURSES_SQL = """
SELECT c.course_id,c.tme,c.metadata FROM work24_bge.snapshot_course c
WHERE c.dataset_id=:dataset AND EXISTS (
 SELECT 1 FROM work24_bge.snapshot_unit u
 WHERE u.course_id=c.course_id AND u.tme=c.tme
   AND u.active AND u.profile_id=:profile AND u.dataset_id=:dataset
)
"""


def canonical_hash(value: object) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise CatalogError("invalid_catalog_profile") from None
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_only(connection: Connection) -> None:
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    connection.execute(text("SELECT set_config('statement_timeout',:timeout,true)"), {"timeout": "10000"})


def _source_string(row: Mapping[str, object], key: str) -> str:
    value = row[key]
    if not isinstance(value, str) or not value:
        raise CatalogError("invalid_catalog_metadata")
    return value


def _evidence(row: Mapping[str, object]) -> UnitEvidence:
    distance = row["distance"]
    if not isinstance(distance, (int, float)) or not math.isfinite(distance):
        raise CatalogError("invalid_catalog_vector")
    source, hours, ncs_code = row["source"], row["hours"], row["ncs_unit_code"]
    if any(value is not None and not isinstance(value, str) for value in (source, ncs_code)):
        raise CatalogError("invalid_catalog_metadata")
    if hours is not None and (type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0):
        raise CatalogError("invalid_catalog_metadata")
    if type(row["is_inferred"]) is not bool or row["is_noise"] is not False:
        raise CatalogError("invalid_catalog_metadata")
    return UnitEvidence(
        unit_key=_source_string(row, "unit_key"), unit_name=_source_string(row, "unit_name"),
        embed_text=_source_string(row, "embed_text"), text_sha256=_source_string(row, "text_sha256"),
        unit_type=_source_string(row, "unit_type"), ncs_unit_code=ncs_code, source=source, hours=hours,
        is_inferred=row["is_inferred"], is_noise=False,
        provenance=freeze_json(row["provenance"]), similarity=1.0 - distance,
    )


def assemble(
    snapshot: CatalogSnapshot, courses: Sequence[Mapping[str, object]],
    units: Sequence[Mapping[str, object]], *, as_of: date,
    filters: SearchFilters, limit: int,
) -> SearchResult:
    """Group all eligible offerings before top-K; input units are ranked per offering."""
    evidence: dict[tuple[str, str], list[UnitEvidence]] = {}
    for row in units:
        key = (_source_string(row, "course_id"), _source_string(row, "tme"))
        evidence.setdefault(key, []).append(_evidence(row))
    winners: dict[str, CourseCandidate] = {}
    excluded: Counter[str] = Counter()
    for row in courses:
        source_id, tme = _source_string(row, "course_id"), _source_string(row, "tme")
        items = tuple(sorted(evidence.get((source_id, tme), []), key=lambda item: (-item.similarity, item.unit_key)))
        if not items:
            raise CatalogError("catalog_snapshot_inconsistent")
        metadata = freeze_json(row["metadata"])
        if not isinstance(metadata, Mapping):
            raise CatalogError("invalid_catalog_metadata")
        conditions = evaluate(metadata, filters)
        failed = [condition for condition in conditions if condition.status == "fail"]
        if failed:
            excluded.update(condition.reason_code for condition in failed)
            continue
        candidate = CourseCandidate(
            course_id=course_id(source_id), offering_id=offering_id(source_id, tme),
            source_system="work24", source_course_id=source_id, source_tme=tme,
            similarity=items[0].similarity, metadata=metadata, evidence=items, conditions=conditions,
        )
        previous = winners.get(source_id)
        if previous is None or (-candidate.similarity, tme) < (-previous.similarity, previous.source_tme):
            winners[source_id] = candidate
    candidates = tuple(sorted(winners.values(), key=lambda item: (-item.similarity, item.source_course_id, item.source_tme))[:limit])
    empty = None if candidates else ("all_filtered" if courses else "no_active_catalog")
    return SearchResult(
        snapshot=snapshot, as_of=as_of, candidates=candidates,
        active_offerings=len(courses), eligible_courses=len(winners),
        excluded_counts=MappingProxyType(dict(excluded)),
        unsupported_conditions=("field_code",) if filters.field_code is not None else (),
        empty_reason=empty,
    )


class CatalogRepository:
    def __init__(self, engine: Engine, *, profile_id: str, dataset_id: str) -> None:
        if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (profile_id, dataset_id)):
            raise CatalogError("invalid_catalog_identity")
        self.engine, self.profile_id, self.dataset_id = engine, profile_id, dataset_id

    def _snapshot(self, connection: Connection, snapshot: CatalogSnapshot | None = None) -> CatalogSnapshot:
        profile = snapshot.profile_id if snapshot else self.profile_id
        dataset = snapshot.dataset_id if snapshot else self.dataset_id
        row = connection.execute(text("""
            SELECT p.config,d.manifest FROM work24_bge.embedding_profile p
            JOIN work24_bge.dataset_snapshot d ON d.profile_id=p.profile_id
            WHERE p.profile_id=:profile AND d.dataset_id=:dataset
        """), {"profile": profile, "dataset": dataset}).first()
        if row is None:
            raise CatalogError("catalog_snapshot_unavailable")
        config, manifest = row
        if not isinstance(config, dict) or not isinstance(manifest, dict):
            raise CatalogError("invalid_catalog_profile")
        if any(key not in config or canonical_hash(config[key]) != canonical_hash(value) for key, value in REQUIRED_CONFIG.items()):
            raise CatalogError("incompatible_embedding_profile")
        if (canonical_hash(config) != profile or manifest.get("embedding_config") != config
                or manifest.get("embedding_profile_id") != profile
                or manifest.get("dataset_id") != dataset
                or canonical_hash(manifest.get("input_files")) != dataset):
            raise CatalogError("catalog_identity_mismatch")
        try:
            created = date.fromisoformat(manifest["created_on"])
        except (ValueError, TypeError, KeyError):
            raise CatalogError("invalid_catalog_snapshot_date") from None
        return CatalogSnapshot(profile, dataset, BGE_REVISION, canonical_hash(manifest), created)

    def prepare(self, snapshot: CatalogSnapshot | None = None) -> CatalogSnapshot:
        try:
            with self.engine.connect() as connection, connection.begin():
                _read_only(connection)
                anchor = self._snapshot(connection)
                if snapshot is not None:
                    if snapshot.profile_id != anchor.profile_id or self._snapshot(connection, snapshot) != snapshot:
                        raise CatalogError("catalog_snapshot_changed")
                    return snapshot
                dataset = connection.execute(text("SELECT dataset_id FROM work24_bge.active_catalog WHERE singleton")).scalar_one_or_none()
                if dataset is None:
                    raise CatalogError("catalog_snapshot_unavailable")
                selected = CatalogSnapshot(anchor.profile_id, dataset, anchor.revision,
                                           anchor.manifest_sha256, anchor.snapshot_created_on)
                return self._snapshot(connection, selected)
        except SQLAlchemyError:
            raise CatalogError("catalog_database_unavailable") from None

    def search(
        self, vector: Sequence[float], snapshot: CatalogSnapshot, *, as_of: date,
        filters: SearchFilters = SearchFilters(), limit: int = 30, evidence_limit: int = 3,
    ) -> SearchResult:
        validate_options(as_of, filters, limit, evidence_limit)
        values = validated_vector(vector)
        params = {"profile": snapshot.profile_id, "dataset": snapshot.dataset_id,
                  "vector": json.dumps(values, allow_nan=False), "evidence_limit": evidence_limit}
        try:
            with self.engine.connect() as connection, connection.begin():
                _read_only(connection)
                if snapshot.profile_id != self.profile_id or self._snapshot(connection, snapshot) != snapshot:
                    raise CatalogError("catalog_snapshot_changed")
                units = connection.execute(text(UNITS_SQL), params).mappings().all()
                courses = connection.execute(text(COURSES_SQL), params).mappings().all()
                return assemble(snapshot, courses, units, as_of=as_of, filters=filters, limit=limit)
        except SQLAlchemyError:
            raise CatalogError("catalog_database_unavailable") from None
