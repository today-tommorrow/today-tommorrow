"""Normalize supplied Work24 records; publish only with verified precomputed vectors."""

import argparse
import copy
import hashlib
import json
import math
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from artifacts import (
    DATA_FILES,
    canonical,
    file_hash,
    load_artifacts,
    require,
    value_hash,
)

FIELDS = ("course_name", "provider_name", "region", "start_date", "end_date", "training_hours",
          "tuition_krw", "listed_self_pay_krw", "curriculum", "eligibility", "url")
IDENTITIES = ("course_id", "tme", "cohort", "offering_id")
NA = ("N/A", "not_applicable")


def provenance(source_hash: str | None, collected_at: str | None) -> dict:
    require(source_hash is None or isinstance(source_hash, str)
            and re.fullmatch(r"[a-f0-9]{64}", source_hash) is not None, "invalid_source_hash")
    if collected_at is not None:
        require(isinstance(collected_at, str), "invalid_source_collection_time")
        parsed = datetime.fromisoformat(collected_at)
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "invalid_source_collection_time")
    return {"source_hash": source_hash, "collected_at": collected_at}


def source_key(record: dict) -> tuple[str, str] | None:
    for field in IDENTITIES:
        value = record.get(field)
        require(value is None or isinstance(value, str) and bool(value), "invalid_source_identity")
    require(record.get("tme") is None or record.get("cohort") is None
            or record["tme"] == record["cohort"], "conflicting_source_cohort")
    occurrence = record.get("tme") if record.get("tme") is not None else record.get("cohort")
    return (record["course_id"], occurrence) if record.get("course_id") and occurrence else None


def finite_nonnegative_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def validate_record(record: dict) -> None:
    require(isinstance(record.get("course_name"), str) and bool(record["course_name"].strip()), "invalid_course_name")
    for field in ("provider_name", "region", "eligibility", "url"):
        require(record.get(field) is None or isinstance(record[field], str), "invalid_record_text")
    for field in ("start_date", "end_date"):
        value = record.get(field)
        if value is not None and value not in NA:
            require(isinstance(value, str) and len(value) == 10, "invalid_record_date")
            date.fromisoformat(value)
    for field in ("training_hours", "tuition_krw", "listed_self_pay_krw"):
        value = record.get(field)
        require(value is None or value in NA or finite_nonnegative_number(value), "invalid_record_number")
        if field != "training_hours" and type(value) in (int, float):
            require(int(value) == value, "invalid_record_number")
    curriculum = record.get("curriculum")
    require(curriculum is None or isinstance(curriculum, str) or isinstance(curriculum, list)
            and all(isinstance(item, str) and bool(item.strip()) for item in curriculum), "invalid_curriculum")


def merge_values(previous: dict, incoming: dict) -> tuple[dict, list[str]]:
    """Only null is unknown. An observed zero or explicit N/A cannot be replaced."""
    conflicts = [field for field in FIELDS + IDENTITIES if previous.get(field) is not None
                 and incoming.get(field) is not None and canonical(previous[field]) != canonical(incoming[field])]
    merged = {field: previous.get(field) if previous.get(field) is not None else incoming.get(field)
              for field in FIELDS + IDENTITIES}
    return merged, conflicts


def has_adapter_history(history: object) -> bool:
    return (isinstance(history, dict)
            and isinstance(history.get("values"), dict)
            and isinstance(history.get("raw_json"), list)
            and all(isinstance(raw, dict) for raw in history["raw_json"])
            and isinstance(history.get("field_provenance"), dict)
            and all(isinstance(source, dict) for source in history["field_provenance"].values())
            and isinstance(history.get("sources"), list) and bool(history["sources"])
            and all(isinstance(source, dict) for source in history["sources"]))


def course_metadata(values: dict, raw: list[dict], field_sources: dict, sources: list[dict]) -> dict:
    key = source_key(values)
    require(key is not None, "missing_source_identity")
    return {**values, "course_id": key[0], "tme": key[1], "institution": values["provider_name"],
            "training_fee": values["tuition_krw"], "displayed_self_pay": values["listed_self_pay_krw"],
            "metadata": {"훈련대상요건": values["eligibility"]},
            # A supplemented row is not a recollection of the carried fields.
            **sources[0], "work24": {"values": values, "raw_json": raw,
                                     "field_provenance": field_sources, "sources": sources}}


def normalize(payload: dict, *, source_hash: str | None, collected_at: str | None,
              parent_dataset_id: str, previous: dict | None = None) -> dict:
    """Return accepted complete replacements and isolated reports, without I/O or inference."""
    source = provenance(source_hash, collected_at)
    require(isinstance(parent_dataset_id, str) and re.fullmatch(r"[a-f0-9]{64}", parent_dataset_id) is not None,
            "invalid_parent_dataset")
    require(isinstance(payload, dict) and isinstance(payload.get("records"), list)
            and bool(payload["records"]) and all(isinstance(row, dict) for row in payload["records"]), "invalid_records_array")
    prior = {}
    if previous is not None:
        require(previous.get("dataset_id") == parent_dataset_id, "previous_parent_mismatch")
        for course in previous["courses"]:
            key = (course["course_id"], course["tme"])
            require(key not in prior, "invalid_previous_records")
            prior[key] = course
    groups, isolated, candidates, unknown = {}, [], {}, set()
    for raw in sorted(copy.deepcopy(payload["records"]), key=canonical):
        unknown.update(set(raw) - set(FIELDS + IDENTITIES) - {"source_excel_row"})
        fingerprint = value_hash([raw.get(field) for field in ("provider_name", "course_name", "region", "start_date", "end_date")])
        substantive = {key: value for key, value in raw.items() if key != "source_excel_row"}
        internal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "work24:internal:" + value_hash(substantive)))
        candidates.setdefault(fingerprint, []).append(internal_id)
        key = None
        try:
            key = source_key(raw)
            validate_record(raw)
            require(key is not None, "missing_source_identity")
        except (ValueError, TypeError) as exc:
            reason = str(exc) if re.fullmatch(r"[a-z_]+", str(exc)) else "invalid_record"
            isolated.append({"offering_key": list(key) if key else None, "reason": reason,
                             "internal_id": internal_id, "fingerprint": fingerprint, "raw_json": raw, **source})
            if key:
                groups.setdefault(key, []).append(None)
            elif isinstance(raw.get("course_id"), str) and raw["course_id"]:
                for field in ("tme", "cohort"):
                    if isinstance(raw.get(field), str) and raw[field]:
                        groups.setdefault((raw["course_id"], raw[field]), []).append(None)
            continue
        groups.setdefault(key, []).append(raw)
    courses, supplemented, parent_records = [], [], []
    for key, rows in sorted(groups.items()):
        if None in rows:
            isolated.append({"offering_key": list(key), "reason": "offering_contains_invalid_record",
                             "raw_json": [row for row in rows if row is not None]})
            continue
        values = {field: rows[0].get(field) for field in FIELDS + IDENTITIES}
        conflicts = set()
        for row in rows[1:]:
            values, differences = merge_values(values, row)
            conflicts.update(differences)
        # Same-batch duplicate observations are never a field supplementation authority.
        if any({field: row.get(field) for field in FIELDS + IDENTITIES} !=
               {field: rows[0].get(field) for field in FIELDS + IDENTITIES} for row in rows[1:]):
            conflicts.add("duplicate_observation")
        old = prior.get(key)
        raw_history, sources, field_sources = [], [], {}
        if old:
            history = old.get("work24")
            if not has_adapter_history(history):
                isolated.append({"offering_key": list(key), "reason": "legacy_parent_requires_mapping", "raw_json": rows})
                continue
            values, differences = merge_values(history["values"], values)
            conflicts.update(differences)
            raw_history = copy.deepcopy(history["raw_json"])
            sources = copy.deepcopy(history["sources"])
            field_sources = copy.deepcopy(history["field_provenance"])
        if conflicts:
            isolated.append({"offering_key": list(key), "reason": "conflicting_offering",
                             "fields": sorted(conflicts), "raw_json": rows})
            continue
        for field in FIELDS + IDENTITIES:
            if values[field] is not None and field not in field_sources:
                field_sources[field] = source
        raw_history += [{"record": row, **source} for row in rows]
        sources = list({canonical(item): item for item in sources + [source]}.values())
        courses.append(course_metadata(values, raw_history, field_sources, sources))
        parent_records.append({"offering_key": list(key), "metadata_sha256": value_hash(old) if old else None})
        if old:
            supplemented.append({"offering_key": list(key), "parent_dataset_id": parent_dataset_id,
                                 "filled_fields": sorted(field for field in FIELDS if old["work24"]["values"].get(field) is None and values[field] is not None)})
    # Invalid/conflicting entries must never remain in partial replacement coverage.
    blocked = {tuple(item["offering_key"]) for item in isolated if item["offering_key"] is not None}
    courses = [course for course in courses if (course["course_id"], course["tme"]) not in blocked]
    return {"adapter_version": 1, "parent_dataset_id": parent_dataset_id, "courses": courses,
            "parent_records": parent_records,
            "report": {"quarantined": sorted(isolated, key=canonical), "supplemented": supplemented,
                       "input_provenance": source,
                       "previous_input_sha256": value_hash(previous) if previous is not None else None,
                       "unknown_fields": sorted(unknown), "records": len(payload["records"]),
                       "accepted_offerings": len(courses), "personal_price": "PERSONAL_PRICE_UNKNOWN",
                       "duplicate_candidates": [{"fingerprint": fp, "internal_ids": sorted(set(ids)), "observations": len(ids)} for fp, ids in sorted(candidates.items()) if len(ids) > 1]}}


def unit_templates(course: dict) -> list[dict]:
    curriculum = course["curriculum"]
    texts = [curriculum] if isinstance(curriculum, str) else curriculum or []
    require(bool(texts) and all(text.strip() and text not in NA for text in texts), "missing_curriculum_vectors")
    units = {}
    for text in texts:
        unit = {"course_id": course["course_id"], "tme": course["tme"], "unit_type": "content_item",
                "ncs_unit_code": "", "unit_name": text, "embed_text": text}
        unit["text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        unit["unit_key"] = value_hash([unit[field] for field in ("course_id", "tme", "unit_type", "ncs_unit_code", "unit_name", "embed_text")])
        units[unit["unit_key"]] = unit
    return [units[key] for key in sorted(units)]


def write_package(normalized: dict, precomputed: Path, output: Path, *, created_on: str) -> dict:
    """Reuse supplied verified unit/vector pairs. Never synthesize embeddings or a query."""
    import numpy as np

    date.fromisoformat(created_on)
    manifest, _courses, supplied, vectors, query = load_artifacts(precomputed, pinned=False)
    courses = normalized["courses"]
    require(bool(courses), "no_publishable_offerings")
    expected = {unit["unit_key"]: unit for course in courses for unit in unit_templates(course)}
    by_key = {unit["unit_key"]: unit for unit in supplied}
    require(expected.keys() <= by_key.keys(), "missing_precomputed_vectors")
    for key, unit in expected.items():
        require(all(by_key[key][field] == value for field, value in unit.items()), "precomputed_unit_mismatch")
    inputs = {"normalized_records": {"sha256": value_hash(normalized)},
              "precomputed_manifest": {"sha256": file_hash(precomputed / "manifest.json")}}
    dataset = value_hash(inputs)
    units, selected = [], []
    for row, key in enumerate(sorted(expected)):
        unit = copy.deepcopy(by_key[key])
        selected.append(vectors[unit["vector_row"]])
        units.append({**unit, "vector_row": row, "dataset_id": dataset})
    result = {"format_version": 1, "created_on": created_on, "dataset_id": dataset,
              "embedding_profile_id": manifest["embedding_profile_id"], "embedding_config": manifest["embedding_config"],
              "input_files": inputs, "counts": {"courses": len(courses), "units": len(units)},
              "batch": {"source_system": "work24", "parent_dataset_id": normalized["parent_dataset_id"],
                        "adapter": "work24_records_v1",
                        "coverage_mode": "partial", "offering_keys": [[row["course_id"], row["tme"]] for row in courses]},
              "records_adapter": {"version": 1, "parent_records": normalized["parent_records"]}}
    output.mkdir(parents=True, exist_ok=False)
    for name in DATA_FILES - {"manifest.json"}:
        (output / name).write_text(canonical({}), encoding="utf-8")
    for name, rows in (("courses.jsonl", courses), ("units.jsonl", units)):
        (output / name).write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    (output / "embedding-config.json").write_text(canonical(result["embedding_config"]), encoding="utf-8")
    (output / "validation-report.json").write_text(canonical(normalized["report"]), encoding="utf-8")
    np.save(output / "vectors.npy", np.asarray(selected, dtype=np.float32))
    np.save(output / "smoke-query.npy", query)
    result["artifacts"] = {name: {"bytes": (output / name).stat().st_size, "sha256": file_hash(output / name)}
                           for name in DATA_FILES - {"manifest.json"}}
    (output / "manifest.json").write_text(canonical(result), encoding="utf-8")
    load_artifacts(output, pinned=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument("--parent-dataset-id", required=True)
    parser.add_argument("--source-hash")
    parser.add_argument("--collected-at")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--normalized-output", type=Path, required=True)
    parser.add_argument("--precomputed", type=Path)
    parser.add_argument("--package-output", type=Path)
    parser.add_argument("--created-on")
    args = parser.parse_args()
    require(bool(args.precomputed) == bool(args.package_output) == bool(args.created_on), "incomplete_package_arguments")
    normalized = normalize(json.loads(args.records.read_text()), source_hash=args.source_hash,
                           collected_at=args.collected_at, parent_dataset_id=args.parent_dataset_id,
                           previous=json.loads(args.previous.read_text()) if args.previous else None)
    normalized["report"]["previous_file_sha256"] = file_hash(args.previous) if args.previous else None
    args.normalized_output.write_text(canonical(normalized) + "\n", encoding="utf-8")
    if args.precomputed:
        write_package(normalized, args.precomputed, args.package_output, created_on=args.created_on)
    print(canonical(normalized["report"]))


if __name__ == "__main__":
    main()
