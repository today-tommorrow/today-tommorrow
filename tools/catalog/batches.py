"""Stage and activate checksum-verified Work24 batches; no inference or normalization."""

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

import numpy as np
import psycopg
from artifacts import file_hash, load_artifacts, require, value_hash
from database import (
    ROLES,
    UNIT_FIELDS,
    attest,
    grant_catalog_read,
    local_connections,
    vector_array,
)
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb


def identity(value: object) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid_batch_identity")
    return value


def read_manifest(root: Path, expected_hash: str) -> dict:
    require(file_hash(root / "manifest.json") == identity(expected_hash), "batch_manifest_checksum_mismatch")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "invalid_batch_manifest")
    return manifest


def coverage(manifest: dict) -> tuple[str, str, list[tuple[str, str]]]:
    batch = manifest.get("batch")
    require(isinstance(batch, dict) and batch.get("source_system") == "work24", "invalid_batch_source")
    parent = identity(batch.get("parent_dataset_id"))
    mode = batch.get("coverage_mode")
    require(mode in ("partial", "full"), "invalid_batch_coverage")
    items = batch.get("offering_keys")
    require(isinstance(items, list) and bool(items), "invalid_batch_coverage")
    require(all(isinstance(item, list) and len(item) == 2
                and all(isinstance(value, str) and value for value in item) for item in items), "invalid_batch_coverage")
    keys = [tuple(item) for item in items]
    require(len(set(keys)) == len(keys), "duplicate_batch_coverage")
    date.fromisoformat(manifest["created_on"])
    return parent, mode, keys


def validate_provenance(courses: list[dict]) -> None:
    for course in courses:
        require("source_hash" in course and "collected_at" in course, "batch_source_provenance_required")
        if course["source_hash"] is not None:
            identity(course["source_hash"])
        collected = course["collected_at"]
        require(collected is None or isinstance(collected, str), "invalid_source_collection_time")
        if collected is not None:
            parsed = datetime.fromisoformat(collected)
            require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "invalid_source_collection_time")


def validate_records_parent(conn: psycopg.Connection, manifest: dict, courses: list[dict],
                            units: list[dict], vectors: np.ndarray, parent: str) -> None:
    """Adapter replacements must attest actual parent metadata and preserve its units."""
    guard = manifest.get("records_adapter")
    if guard is None:
        require(manifest["batch"].get("adapter") != "work24_records_v1", "records_parent_guard_required")
        return
    require(isinstance(guard, dict) and guard.get("version") == 1, "invalid_records_parent_guard")
    require(manifest["batch"].get("adapter") == "work24_records_v1", "invalid_records_parent_guard")
    require(manifest["batch"]["coverage_mode"] == "partial", "records_partial_coverage_required")
    expectations = guard.get("parent_records")
    require(isinstance(expectations, list), "invalid_records_parent_guard")
    expected = {tuple(item["offering_key"]): item["metadata_sha256"] for item in expectations}
    incoming = {(course["course_id"], course["tme"]) for course in courses}
    require(len(expected) == len(expectations) and set(expected) == incoming, "invalid_records_parent_guard")
    for key, digest in expected.items():
        stored = conn.execute("SELECT metadata FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id=%s AND tme=%s",
                              (parent, *key)).fetchone()
        require((value_hash(stored[0]) if stored else None) == digest, "records_parent_metadata_mismatch")
        if stored is None:
            continue
        # Metadata-only supplementation carries the complete parent unit collection.
        fields = [field for field in UNIT_FIELDS if field not in ("dataset_id", "vector_row", "embedding")]
        previous = conn.execute(f"SELECT {','.join(fields)},embedding FROM work24_bge.snapshot_unit WHERE dataset_id=%s AND course_id=%s AND tme=%s",
                                (parent, *key)).fetchall()
        rows = {unit["unit_key"]: unit for unit in units if (unit["course_id"], unit["tme"]) == key}
        require({row[0] for row in previous} == set(rows), "records_parent_units_mismatch")
        for row in previous:
            unit = rows[row[0]]
            values = {**unit, "profile_id": unit["embedding_profile_id"], "active": True}
            require(tuple(values[field] for field in fields) == tuple(row[:-1])
                    and np.array_equal(vector_array(row[-1]), vectors[unit["vector_row"]]), "records_parent_units_mismatch")


def stage(conn: psycopg.Connection, root: Path, expected_hash: str) -> dict:
    """Persist the attempt, then isolate the draft in a savepoint on any failure."""
    manifest = read_manifest(root, expected_hash)
    parent, mode, keys = coverage(manifest)
    batch_id = value_hash(manifest)
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("work24_batch:" + batch_id,))
    previous = conn.execute("SELECT state,report FROM work24_bge.catalog_batch WHERE batch_id=%s", (batch_id,)).fetchone()
    if previous and previous[0] != "failed":
        return {**previous[1], "state": previous[0], "batch_id": batch_id}
    conn.execute("""INSERT INTO work24_bge.catalog_batch(batch_id,parent_dataset_id,manifest,state)
        VALUES(%s,%s,%s,'draft') ON CONFLICT(batch_id) DO UPDATE SET state='draft'""", (batch_id, parent, Jsonb(manifest)))
    try:
        with conn.transaction():
            report = build_snapshot(conn, root, manifest, batch_id, parent, mode, keys)
    except (ValueError, KeyError, TypeError, OverflowError, OSError, psycopg.Error) as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "invalid_batch_artifacts"
        report = {"state": "failed", "error": code, "batch_id": batch_id}
        conn.execute("""UPDATE work24_bge.catalog_batch SET state='failed',report=%s,
            failure_history=failure_history || jsonb_build_array(jsonb_build_object('failed_at',now(),'report',%s::jsonb))
            WHERE batch_id=%s""", (Jsonb(report), Jsonb(report), batch_id))
        return report
    conn.execute("UPDATE work24_bge.catalog_batch SET state='ready',dataset_id=%s,report=%s WHERE batch_id=%s",
                 (report["dataset_id"], Jsonb(report), batch_id))
    return report


def build_snapshot(conn: psycopg.Connection, root: Path, manifest: dict, batch_id: str,
                   parent: str, mode: str, keys: list[tuple[str, str]]) -> dict:
    loaded, courses, units, vectors, _query = load_artifacts(root, pinned=False)
    validate_provenance(courses)
    require(loaded == manifest, "batch_manifest_changed")
    incoming = {(course["course_id"], course["tme"]) for course in courses}
    require(incoming <= set(keys) and (mode == "full" or incoming == set(keys)), "batch_outside_coverage")
    row = conn.execute("SELECT profile_id,manifest FROM work24_bge.dataset_snapshot WHERE dataset_id=%s", (parent,)).fetchone()
    require(row is not None and row[0] == manifest["embedding_profile_id"], "batch_profile_mismatch")
    require(row[1]["embedding_config"] == manifest["embedding_config"], "batch_profile_mismatch")
    require(conn.execute("SELECT 1 FROM work24_bge.snapshot_course WHERE dataset_id=%s LIMIT 1", (parent,)).fetchone() is not None, "batch_parent_unavailable")
    validate_records_parent(conn, manifest, courses, units, vectors, parent)
    inputs = {"parent_snapshot": {"sha256": value_hash(row[1])}, "batch_manifest": {"sha256": batch_id}}
    dataset = value_hash(inputs)
    effective = {"format_version": 2, "created_on": manifest["created_on"], "dataset_id": dataset,
                 "embedding_profile_id": row[0], "embedding_config": manifest["embedding_config"],
                 "input_files": inputs, "parent_dataset_id": parent, "batch_id": batch_id}
    conn.execute("INSERT INTO work24_bge.dataset_snapshot(dataset_id,profile_id,manifest) VALUES(%s,%s,%s)", (dataset, row[0], Jsonb(effective)))
    conn.execute("CREATE TEMP TABLE batch_coverage(course_id text,tme text,PRIMARY KEY(course_id,tme)) ON COMMIT DROP")
    with conn.cursor().copy("COPY batch_coverage FROM STDIN") as copy:
        for key in keys:
            copy.write_row(key)
    # Partial replaces supplied offerings. Full additionally removes absent offerings
    # only within its explicit coverage; all untouched rows retain their source bytes.
    conn.execute("""INSERT INTO work24_bge.snapshot_course
        SELECT %s,c.course_id,c.tme,c.metadata,c.source_dataset_id FROM work24_bge.snapshot_course c
        WHERE c.dataset_id=%s AND NOT EXISTS(SELECT 1 FROM batch_coverage k WHERE k.course_id=c.course_id AND k.tme=c.tme)""", (dataset, parent))
    fields = ",".join(UNIT_FIELDS)
    copied = ",".join("%s" if field == "dataset_id" else f"u.{field}" for field in UNIT_FIELDS)
    conn.execute(f"""INSERT INTO work24_bge.snapshot_unit ({fields},source_dataset_id)
        SELECT {copied},u.source_dataset_id FROM work24_bge.snapshot_unit u
        WHERE u.dataset_id=%s AND NOT EXISTS(SELECT 1 FROM batch_coverage k WHERE k.course_id=u.course_id AND k.tme=u.tme)""", (dataset, parent))
    with conn.cursor().copy("COPY work24_bge.snapshot_course(dataset_id,course_id,tme,metadata,source_dataset_id) FROM STDIN") as copy:
        for course in courses:
            copy.write_row((dataset, course["course_id"], course["tme"], Jsonb(course), manifest["dataset_id"]))
    with conn.cursor().copy(f"COPY work24_bge.snapshot_unit ({fields},source_dataset_id) FROM STDIN") as copy:
        for unit, vector in zip(units, vectors, strict=True):
            values = {**unit, "embedding": np.asarray(vector), "profile_id": row[0], "dataset_id": dataset,
                      "active": True, "provenance": Jsonb(unit["provenance"])}
            copy.write_row(tuple(values[field] for field in UNIT_FIELDS) + (manifest["dataset_id"],))
    counts = conn.execute("""SELECT (SELECT count(*) FROM work24_bge.snapshot_course WHERE dataset_id=%s),
        (SELECT count(*) FROM work24_bge.snapshot_unit WHERE dataset_id=%s)""", (dataset, dataset)).fetchone()
    require(counts[0] > 0 and counts[1] > 0, "empty_catalog_batch")
    # Verify every inserted vector and metadata field, not merely row counts.
    for course in courses:
        stored = conn.execute("SELECT metadata,source_dataset_id FROM work24_bge.snapshot_course WHERE dataset_id=%s AND course_id=%s AND tme=%s", (dataset, course["course_id"], course["tme"])).fetchone()
        require(stored == (course, manifest["dataset_id"]), "batch_course_roundtrip_mismatch")
    for unit, vector in zip(units, vectors, strict=True):
        stored = conn.execute(f"SELECT {fields} FROM work24_bge.snapshot_unit WHERE dataset_id=%s AND unit_key=%s", (dataset, unit["unit_key"])).fetchone()
        expected = {**unit, "profile_id": row[0], "dataset_id": dataset, "active": True}
        actual = dict(zip(UNIT_FIELDS, stored, strict=True))
        require(np.array_equal(vector_array(actual.pop("embedding")), vector), "batch_vector_roundtrip_mismatch")
        require(actual == {field: expected[field] for field in UNIT_FIELDS if field != "embedding"}, "batch_metadata_roundtrip_mismatch")
    require(conn.execute("""SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='work24_bge'
        AND c.relname='snapshot_unit_embedding_hnsw'""").fetchone() == (True,), "batch_index_unavailable")
    conn.execute("DROP TABLE batch_coverage")
    return {"state": "ready", "batch_id": batch_id, "parent_dataset_id": parent, "dataset_id": dataset,
            "courses": counts[0], "units": counts[1], "coverage_mode": mode,
            "incoming_courses": len(courses), "incoming_units": len(units)}


def activate(conn: psycopg.Connection, batch_id: str) -> dict:
    """Only pointer/history changes occur here; all expensive preparation is committed."""
    identity(batch_id)
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('work24_bge_snapshot_load'))")
    batch = conn.execute("SELECT parent_dataset_id,dataset_id,state,report FROM work24_bge.catalog_batch WHERE batch_id=%s FOR UPDATE", (batch_id,)).fetchone()
    require(batch is not None and batch[2] in ("ready", "activated"), "batch_not_ready")
    if batch[2] == "activated":
        return {**batch[3], "state": "activated"}
    active = conn.execute("SELECT dataset_id FROM work24_bge.active_catalog WHERE singleton FOR UPDATE").fetchone()
    require(active == (batch[0],), "batch_parent_changed")
    require(conn.execute("SELECT 1 FROM work24_bge.snapshot_course WHERE dataset_id=%s LIMIT 1", (batch[1],)).fetchone() is not None, "batch_snapshot_unavailable")
    conn.execute("UPDATE work24_bge.active_catalog SET dataset_id=%s WHERE singleton", (batch[1],))
    conn.execute("INSERT INTO work24_bge.catalog_activation(previous_dataset_id,dataset_id,batch_id) VALUES(%s,%s,%s)", (batch[0], batch[1], batch_id))
    conn.execute("UPDATE work24_bge.catalog_batch SET state='activated' WHERE batch_id=%s", (batch_id,))
    return {**batch[3], "state": "activated"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--directory", type=Path)
    modes.add_argument("--activate", metavar="BATCH_ID")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"state": "failed", "error": "invalid_batch"}
    try:
        connections = local_connections(os.environ)
        with psycopg.connect(**connections["catalog"]) as conn:
            attest(conn, ROLES["catalog"])
            register_vector(conn)
            if args.directory:
                require(args.manifest_sha256 is not None, "batch_manifest_checksum_required")
                report = stage(conn, args.directory, args.manifest_sha256)
                grant_catalog_read(conn)
            else:
                report = activate(conn, args.activate)
        if args.directory and not args.stage_only and report["state"] == "ready":
            with psycopg.connect(**connections["catalog"]) as conn:
                attest(conn, ROLES["catalog"])
                report = activate(conn, report["batch_id"])
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "batch_operation_failed"
        report = {**report, "state": "failed", "error": code}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.report)
    print(json.dumps(report))
    return 1 if report["state"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
