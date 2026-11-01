"""Bounded read-only metadata access; comparison observes a single pinned snapshot."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from .errors import CourseError
from .identity import course_id
from .presentation import present_course, source_object
from .schemas import CourseDetail


def canonical_hash(value: object) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        raise CourseError(503, "catalog_invalid") from None
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class CatalogPin:
    profile_id: str
    dataset_id: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        if any(re.fullmatch(r"[a-f0-9]{64}", value) is None for value in (self.profile_id, self.dataset_id, self.manifest_sha256)):
            raise CourseError(503, "catalog_unavailable")

    @classmethod
    def load(cls) -> "CatalogPin":
        return cls(*(os.environ.get(name, "") for name in ("CATALOG_PROFILE_ID", "CATALOG_DATASET_ID", "CATALOG_MANIFEST_SHA256")))


class CourseRepository:
    def __init__(self, engine: Engine, pin: CatalogPin) -> None:
        self.engine, self.pin = engine, pin

    def details(self, identifiers: list[UUID]) -> list[CourseDetail]:
        if not 1 <= len(identifiers) <= 3 or len(set(identifiers)) != len(identifiers):
            raise CourseError(422, "invalid_course_request")
        params = {"profile": self.pin.profile_id, "dataset": self.pin.dataset_id}
        try:
            with self.engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                connection.execute(text("SELECT set_config('statement_timeout','10000',true)"))
                row = connection.execute(text("""
                    SELECT p.config,d.manifest FROM work24_bge.embedding_profile p
                    JOIN work24_bge.dataset_snapshot d ON d.profile_id=p.profile_id
                    WHERE p.profile_id=:profile AND d.dataset_id=:dataset
                """), params).first()
                if row is None:
                    raise CourseError(503, "catalog_unavailable")
                config, manifest = row
                manifest = source_object(manifest)
                if (canonical_hash(config) != self.pin.profile_id
                        or canonical_hash(manifest) != self.pin.manifest_sha256
                        or manifest.get("embedding_profile_id") != self.pin.profile_id
                        or manifest.get("dataset_id") != self.pin.dataset_id
                        or manifest.get("embedding_config") != config
                        or canonical_hash(manifest.get("input_files")) != self.pin.dataset_id):
                    raise CourseError(503, "catalog_changed")
                active = connection.execute(text("""SELECT d.dataset_id,d.profile_id,d.manifest
                    FROM work24_bge.active_catalog a JOIN work24_bge.dataset_snapshot d
                    ON d.dataset_id=a.dataset_id WHERE a.singleton""")).first()
                if active is None:
                    raise CourseError(503, "catalog_unavailable")
                dataset, profile, manifest = active
                manifest = source_object(manifest)
                if (profile != self.pin.profile_id or manifest.get("embedding_config") != config
                        or manifest.get("embedding_profile_id") != profile
                        or manifest.get("dataset_id") != dataset
                        or canonical_hash(manifest.get("input_files")) != dataset):
                    raise CourseError(503, "catalog_changed")
                params = {"profile": profile, "dataset": dataset}
                source_ids = connection.execute(text("""
                    SELECT DISTINCT course_id FROM work24_bge.snapshot_unit
                    WHERE active AND profile_id=:profile AND dataset_id=:dataset
                """), params).scalars().all()
                mapping = {course_id(value): value for value in source_ids}
                if any(identifier not in mapping for identifier in identifiers):
                    raise CourseError(404, "course_not_found")
                params["ids"] = [mapping[identifier] for identifier in identifiers]
                courses = connection.execute(text("""
                    SELECT c.course_id,c.tme,c.metadata FROM work24_bge.snapshot_course c
                    WHERE c.dataset_id=:dataset AND c.course_id IN :ids AND EXISTS (
                        SELECT 1 FROM work24_bge.snapshot_unit u WHERE u.course_id=c.course_id AND u.tme=c.tme
                        AND u.active AND u.profile_id=:profile AND u.dataset_id=:dataset
                    ) ORDER BY c.course_id,c.tme
                """).bindparams(bindparam("ids", expanding=True)), params).mappings().all()
                units = connection.execute(text("""
                    SELECT course_id,tme,unit_key,embed_text,is_inferred,is_noise,source
                    FROM work24_bge.snapshot_unit WHERE course_id IN :ids
                    AND active AND profile_id=:profile AND dataset_id=:dataset
                    ORDER BY course_id,tme,unit_key
                """).bindparams(bindparam("ids", expanding=True)), params).mappings().all()
                grouped_courses: dict[str, list[dict[str, object]]] = {}
                grouped_units: dict[str, list[dict[str, object]]] = {}
                for record in courses:
                    grouped_courses.setdefault(record["course_id"], []).append(dict(record))
                for record in units:
                    grouped_units.setdefault(record["course_id"], []).append(dict(record))
                if any(source not in grouped_courses for source in params["ids"]):
                    raise CourseError(503, "catalog_invalid")
                return [present_course(source, grouped_courses[source], grouped_units[source], dataset)
                        for source in params["ids"]]
        except SQLAlchemyError:
            raise CourseError(503, "catalog_unavailable") from None
