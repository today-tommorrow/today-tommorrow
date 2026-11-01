"""Read only the current Work24 catalog; neither inference nor fuzzy identity mapping."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TypeAlias
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from sqlalchemy import Engine, text

from .schemas import CourseSummary

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
NAMESPACE = UUID("a4b29d71-4f3e-50ab-9e95-dfba02578443")


def canonical(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def course_uuid(source_id: str) -> UUID:
    # Same versioned identity as the recommendation/search contract; exact TEXT keys.
    return uuid5(NAMESPACE, canonical(["work24", source_id]))


def optional_text(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def safe_url(value: JsonValue) -> str | None:
    value = optional_text(value)
    if value is None:
        return None
    try:
        url = urlsplit(value)
        return value if url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


@dataclass(frozen=True)
class CatalogCourse:
    source_id: str
    summary: CourseSummary


@dataclass(frozen=True)
class Catalog:
    version: UUID
    fingerprint: str
    courses: dict[UUID, CatalogCourse]


class CatalogUnavailable(Exception):
    pass


def read_catalog(engine: Engine) -> Catalog:
    # Auth already used its request transaction; obtain an independent, short,
    # read-only repeatable-read snapshot instead of changing its isolation level.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        pairs = connection.execute(text("SELECT DISTINCT dataset_id,profile_id FROM work24_bge.course_unit WHERE active")).all()
        if not pairs:
            pairs = connection.execute(text("SELECT dataset_id,profile_id FROM work24_bge.dataset_snapshot")).all()
        if len(pairs) != 1:
            raise CatalogUnavailable()
        dataset, profile = pairs[0]
        manifest = connection.execute(text("SELECT manifest FROM work24_bge.dataset_snapshot WHERE dataset_id=:dataset AND profile_id=:profile"), {"dataset": dataset, "profile": profile}).scalar_one()
        digest = hashlib.sha256(canonical(manifest).encode()).hexdigest()
        version = uuid5(NAMESPACE, canonical(["catalog", dataset, profile, digest]))
        rows = connection.execute(text("""
            SELECT DISTINCT ON (c.course_id) c.course_id,c.metadata
            FROM work24_bge.course c
            WHERE EXISTS (
                SELECT 1 FROM work24_bge.course_unit u
                WHERE u.course_id=c.course_id AND u.tme=c.tme AND u.active
                AND u.dataset_id=:dataset AND u.profile_id=:profile
            ) ORDER BY c.course_id,c.tme
        """), {"dataset": dataset, "profile": profile}).all()
        courses: dict[UUID, CatalogCourse] = {}
        for source_id, metadata in rows:
            if not isinstance(source_id, str) or not isinstance(metadata, dict):
                raise CatalogUnavailable()
            identifier = course_uuid(source_id)
            summary = CourseSummary(id=identifier, name=optional_text(metadata.get("course_name")) or "교육명 미확인",
                                    provider_name=optional_text(metadata.get("institution")), detail_url=safe_url(metadata.get("url")))
            courses[identifier] = CatalogCourse(source_id, summary)
        # Also fence accidental same-version source edits or active-set changes.
        summaries = [course.summary.model_dump(mode="json") for course in courses.values()]
        fingerprint = hashlib.sha256(canonical([str(version), summaries]).encode()).hexdigest()
        return Catalog(version, fingerprint, courses)
