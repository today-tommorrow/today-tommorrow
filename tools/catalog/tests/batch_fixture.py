"""Small, public synthetic packages with the same vector/profile boundary as Work24."""

import json
from pathlib import Path

import numpy as np
from artifacts import DATA_FILES, file_hash, value_hash


def package(root: Path, config: dict, parent: str, names: list[str], *, mode: str = "partial",
            covered: list[str] | None = None, revision: str = "1") -> tuple[dict, str]:
    root.mkdir(parents=True)
    inputs = {"synthetic.json": {"sha256": value_hash([names, revision])}}
    dataset, profile = value_hash(inputs), value_hash(config)
    courses, units = [], []
    for index, name in enumerate(names):
        course = {"course_id": name, "tme": "01", "course_name": f"Synthetic {name} {revision}",
                  "institution": "Public synthetic provider", "mode": "온라인", "url": "https://example.org/course",
                  "objective": "Synthetic public competency", "training_fee": 0, "displayed_self_pay": None,
                  "source_hash": value_hash([name, revision]), "collected_at": "2026-10-30T10:00:00+09:00"}
        unit = {"course_id": name, "tme": "01", "unit_type": "content_item", "ncs_unit_code": "",
                "unit_name": f"Synthetic unit {revision}", "embed_text": f"Synthetic public competency {revision}",
                "vector_row": index, "hours": None, "source": "page", "is_noise": False, "is_inferred": False,
                "provenance": [{"file": "synthetic.json", "source_hash": course["source_hash"], "collected_at": course["collected_at"]}],
                "embedding_profile_id": profile, "dataset_id": dataset}
        import hashlib
        unit["text_sha256"] = hashlib.sha256(unit["embed_text"].encode()).hexdigest()
        unit["unit_key"] = value_hash([unit[key] for key in ("course_id", "tme", "unit_type", "ncs_unit_code", "unit_name", "embed_text")])
        courses.append(course)
        units.append(unit)
    vectors = np.zeros((len(units), 1024), dtype=np.float32)
    vectors[:, 0] = 1
    query = np.zeros(1024, dtype=np.float32)
    query[0] = 1
    for name in DATA_FILES - {"manifest.json"}:
        (root / name).write_text("{}", encoding="utf-8")
    (root / "courses.jsonl").write_text("".join(json.dumps(row) + "\n" for row in courses))
    (root / "units.jsonl").write_text("".join(json.dumps(row) + "\n" for row in units))
    (root / "embedding-config.json").write_text(json.dumps(config))
    np.save(root / "vectors.npy", vectors)
    np.save(root / "smoke-query.npy", query)
    manifest = {"format_version": 1, "created_on": "2026-11-01", "dataset_id": dataset,
                "embedding_profile_id": profile, "embedding_config": config, "input_files": inputs,
                "counts": {"courses": len(courses), "units": len(units)},
                "batch": {"source_system": "work24", "parent_dataset_id": parent,
                          "coverage_mode": mode, "offering_keys": [[name, "01"] for name in (covered if covered is not None else names)]}}
    return refresh(root, manifest)


def refresh(root: Path, manifest: dict) -> tuple[dict, str]:
    manifest["artifacts"] = {name: {"bytes": (root / name).stat().st_size, "sha256": file_hash(root / name)}
                             for name in DATA_FILES - {"manifest.json"}}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, file_hash(root / "manifest.json")
