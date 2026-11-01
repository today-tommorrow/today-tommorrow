"""Synthetic records and fixture-only vectors; never source collection or inference."""

from pathlib import Path

import numpy as np
from artifacts import canonical, value_hash
from batch_fixture import package, refresh
from work24_records import normalize, unit_templates


def record(name: str = "synthetic-A", **changes: object) -> dict:
    return {"course_id": name, "cohort": "01", "course_name": "Synthetic public course",
            "provider_name": "Synthetic provider", "region": None, "start_date": None,
            "end_date": None, "training_hours": 0, "tuition_krw": 0,
            "listed_self_pay_krw": "N/A", "curriculum": ["Synthetic competency"],
            "eligibility": None, "source_excel_row": 12, **changes}


def normalized(rows: list[dict], *, parent: str = "0" * 64, previous: dict | None = None,
               revision: str = "first") -> dict:
    return normalize({"records": rows}, source_hash=value_hash(revision), collected_at=None,
                     parent_dataset_id=parent, previous=previous)


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
