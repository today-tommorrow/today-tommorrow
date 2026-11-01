"""Synthetic records and fixture-only vectors; never source collection or inference."""

import json
from pathlib import Path

import numpy as np
from artifacts import canonical, load_artifacts, value_hash
from batch_fixture import package, refresh
from work24_records import normalize, unit_templates


def record(name: str = "synthetic-A", **changes: object) -> dict:
    return {"course_id": name, "cohort": "01", "course_name": "Synthetic public course",
            "provider_name": "Synthetic provider", "region": None, "start_date": None,
            "end_date": None, "training_hours": 0, "tuition_krw": 0,
            "listed_self_pay_krw": "N/A", "curriculum": ["Synthetic competency"],
            "eligibility": None, "source_excel_row": 12, **changes}


def normalized(rows: list[dict], *, parent: str = "0" * 64, previous: dict | None = None,
               revision: str = "first", legacy_mapping: dict | None = None) -> dict:
    return normalize({"records": rows}, source_hash=value_hash(revision), collected_at=None,
                     parent_dataset_id=parent, previous=previous, legacy_mapping=legacy_mapping)


def mapping_for(root: Path, keys: list[tuple[str, str]] | None = None, *, parent: str | None = None) -> dict:
    manifest, courses, units, _vectors, _query = load_artifacts(root, pinned=False)
    return {"version": 1, "profile": "work24_catalog_v1", "reviewed_by": "synthetic-reviewer",
            "parent_dataset_id": parent or manifest["dataset_id"],
            "offerings": [{"offering_key": [course["course_id"], course["tme"]],
                           "metadata_sha256": value_hash(course),
                           "unit_keys": sorted(unit["unit_key"] for unit in units
                                               if (unit["course_id"], unit["tme"]) == (course["course_id"], course["tme"]))}
                          for course in courses if keys is None or (course["course_id"], course["tme"]) in keys]}


def legacy_precomputed(root: Path) -> Path:
    """Legacy page/timetable/NCS evidence cannot be reconstructed from curriculum texts."""
    manifest, _digest = package(root, {"model": "synthetic-only"}, "0" * 64, [" legacy ", "blocked"])
    courses = [json.loads(row) for row in (root / "courses.jsonl").read_text().splitlines()]
    for course in courses:
        course.update(region=None, start_date="2026-11-01T00:00:00", end_date=None,
                      training_hours=0, displayed_self_pay="N/A", collected_at=None,
                      metadata={"훈련내용": "Original curriculum", "훈련대상요건": None,
                                "unmapped_fact": "preserve"}, original_csv={"raw": "preserve"},
                      provenance=[{"file": "synthetic-legacy.csv"}])
    original = [json.loads(row) for row in (root / "units.jsonl").read_text().splitlines()]
    units = []
    for unit in original:
        for kind in ("goal", "timetable", "ncs_unit"):
            current = {**unit, "unit_type": kind, "vector_row": len(units)}
            current["unit_key"] = value_hash([current[field] for field in (
                "course_id", "tme", "unit_type", "ncs_unit_code", "unit_name", "embed_text")])
            units.append(current)
    vectors = np.zeros((len(units), 1024), dtype=np.float32)
    for row in range(len(units)):
        vectors[row, row] = 1
    np.save(root / "vectors.npy", vectors)
    for name, rows in (("courses.jsonl", courses), ("units.jsonl", units)):
        (root / name).write_text("".join(canonical(row) + "\n" for row in rows))
    manifest["counts"] = {"courses": len(courses), "units": len(units)}
    refresh(root, manifest)
    return root


def precomputed(root: Path, result: dict, config: dict | None = None) -> Path:
    """Create a valid independently supplied test package with distinguishable vector rows."""
    manifest, _digest = package(root, config or {"model": "synthetic-only"}, result["parent_dataset_id"], ["fixture"])
    dataset, profile = manifest["dataset_id"], manifest["embedding_profile_id"]
    units = []
    for course in result["courses"]:
        for template in unit_templates(course):
            units.append({**template, "vector_row": len(units), "hours": None, "source": None,
                          "is_noise": False, "is_inferred": False,
                          "provenance": [{"fixture": True, "source_hash": course["source_hash"], "collected_at": None}],
                          "dataset_id": dataset, "embedding_profile_id": profile})
    vectors = np.zeros((len(units), 1024), dtype=np.float32)
    for row in range(len(units)):
        vectors[row, row] = 1
    np.save(root / "vectors.npy", vectors)
    for name, rows in (("courses.jsonl", result["courses"]), ("units.jsonl", units)):
        (root / name).write_text("".join(canonical(row) + "\n" for row in rows))
    manifest["counts"] = {"courses": len(result["courses"]), "units": len(units)}
    refresh(root, manifest)
    return root
