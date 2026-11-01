"""Offline, evidence-recorded adjudication of a retained search result (no rerun)."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import cast

MAX_BYTES = 16 * 1024 * 1024
SCOPE = (
    "Post-hoc diagnostics only; not a new live benchmark or replacement gold fixture. "
    "Unjudged pairs are not negatives. No corpus precision or expected-empty claim. "
    "Source hashes and excerpts are reviewer assertions: source bytes, factual relevance "
    "and redistribution permission are not verified by this tool."
)


class ReviewError(ValueError):
    """Safe error codes, without input text or paths."""


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReviewError("invalid_review_object")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ReviewError("invalid_review_array")
    return cast(list[object], value)


def _text(value: object, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ReviewError("invalid_review_text")
    value.encode("utf-8")
    return value


def _hash(value: object) -> str:
    text = _text(value, 64)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ReviewError("invalid_review_sha256")
    return text


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    row: dict[str, object] = {}
    for key, value in pairs:
        if key in row:
            raise ReviewError("duplicate_review_json_key")
        row[key] = value
    return row


def _constant(value: str) -> object:
    raise ReviewError("nonfinite_review_json")


def _encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read(path: Path) -> tuple[dict[str, object], str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ReviewError("review_input_too_large")
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    return _object(value), hashlib.sha256(raw).hexdigest()


def _fields(row: dict[str, object], fields: set[str]) -> None:
    if set(row) != fields:
        raise ReviewError("invalid_review_fields")


def _version(row: dict[str, object], field: str) -> None:
    if type(row[field]) is not int or row[field] != 1:
        raise ReviewError("unsupported_review_schema")


def _result(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    result, digest = _read(path)
    _version(result, "report_schema_version")
    if result["execution_mode"] not in ("synthetic", "live_bge_cuda"):
        raise ReviewError("invalid_review_execution_mode")
    dataset = _object(result["evaluation_dataset"])
    catalog, snapshot = _object(dataset["catalog"]), _object(result["snapshot"])
    _fields(snapshot, {"profile_id", "dataset_id", "revision", "manifest_sha256", "snapshot_created_on"})
    for key in ("profile_id", "dataset_id"):
        if _hash(snapshot[key]) != _hash(catalog[key]):
            raise ReviewError("review_catalog_mismatch")
    if _text(snapshot["revision"]) != _text(catalog["revision"]):
        raise ReviewError("review_catalog_mismatch")
    _hash(snapshot["manifest_sha256"])
    date.fromisoformat(_text(snapshot["snapshot_created_on"]))
    binding = {
        "result_sha256": digest,
        "evaluation_dataset": {"id": _text(dataset["id"]), "version": _text(dataset["version"]),
                               "sha256": _hash(dataset["sha256"])},
        "snapshot": snapshot,
    }
    cases = _array(result["cases"])
    seen: set[str] = set()
    for value in cases:
        case = _object(value)
        identifier = _text(case["id"])
        if identifier in seen:
            raise ReviewError("duplicate_review_case")
        seen.add(identifier)
        _text(case["query"], 65536)
        date.fromisoformat(_text(case["as_of"]))
        filters = _object(case["filters"])
        if set(filters) - {"delivery_mode", "start_from", "start_to", "listed_region", "budget_krw", "availability", "field_code"}:
            raise ReviewError("invalid_review_filter")
        for key, value in filters.items():
            if value is None:
                continue
            if key == "budget_krw":
                if type(value) is not int or value < 0:
                    raise ReviewError("invalid_review_filter")
            elif key in ("start_from", "start_to"):
                date.fromisoformat(_text(value))
            else:
                _text(value)
        if case["status"] == "error":
            if "candidates" in case:
                raise ReviewError("failed_review_case_has_candidates")
            continue
        if case["status"] != "ok":
            raise ReviewError("invalid_review_case_status")
        candidates = _array(case["candidates"])
        for index, candidate in enumerate(candidates, 1):
            row = _object(candidate)
            _text(row["source_course_id"])
            if type(row["rank"]) is not int or row["rank"] != index:
                raise ReviewError("invalid_review_rank")
        if type(case["returned_count"]) is not int or case["returned_count"] != len(candidates):
            raise ReviewError("invalid_review_returned_count")
    cutoffs = _array(_object(result["search_settings"])["cutoffs"])
    if not cutoffs or any(type(k) is not int or k < 1 or k > 100 for k in cutoffs) or len(set(cutoffs)) != len(cutoffs):
        raise ReviewError("invalid_review_cutoffs")
    return result, binding


def _packet(result: dict[str, object], binding: dict[str, object]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for value in _array(result["cases"]):
        case = _object(value)
        if case["status"] == "ok":
            identifiers = {_text(_object(row)["source_course_id"]) for row in _array(case["candidates"])}
            for identifier in sorted(identifiers):
                items.append({"case_id": case["id"], "query": case["query"], "as_of": case["as_of"],
                              "filters": case["filters"], "source_course_id": identifier})
    items.sort(key=lambda row: (_text(row["case_id"]), _text(row["source_course_id"])))
    return {"schema_version": 1, "artifact_type": "offline_relevance_packet", "binding": binding, "items": items}


def _bound_packet(path: Path, result: dict[str, object], binding: dict[str, object]) -> tuple[dict[str, object], str]:
    packet, digest = _read(path)
    _version(packet, "schema_version")
    if packet != _packet(result, binding):
        raise ReviewError("review_packet_result_mismatch")
    return packet, digest


def _key(row: dict[str, object]) -> tuple[str, str]:
    return _text(row["case_id"]), _text(row["source_course_id"])


def _template(packet: dict[str, object], digest: str) -> dict[str, object]:
    return {"schema_version": 1, "artifact_type": "offline_relevance_submission", "binding": packet["binding"],
            "packet_sha256": digest, "judgments": [
                {"case_id": row["case_id"], "source_course_id": row["source_course_id"], "label": "unjudged",
                 "reviewer": "", "reason": "", "evidence": None} for value in _array(packet["items"])
                if (row := _object(value))]}


def _judgments(submission: dict[str, object], packet: dict[str, object], digest: str, kind: str) -> list[dict[str, object]]:
    fields = {"schema_version", "artifact_type", "binding", "packet_sha256", "judgments"}
    if kind == "offline_relevance_adjudication":
        fields.update({"submission_sha256", "scope"})
        _hash(submission["submission_sha256"])
        if submission["scope"] != SCOPE:
            raise ReviewError("invalid_review_scope")
    _fields(submission, fields)
    _version(submission, "schema_version")
    if submission["artifact_type"] != kind or submission["binding"] != packet["binding"] or submission["packet_sha256"] != digest:
        raise ReviewError("review_submission_binding_mismatch")
    expected = {_key(_object(row)) for row in _array(packet["items"])}
    seen: set[tuple[str, str]] = set()
    judgments: list[dict[str, object]] = []
    for value in _array(submission["judgments"]):
        row = _object(value)
        _fields(row, {"case_id", "source_course_id", "label", "reviewer", "reason", "evidence"})
        key = _key(row)
        if key in seen:
            raise ReviewError("duplicate_or_conflicting_review_judgment")
        if key not in expected:
            raise ReviewError("unknown_review_judgment")
        seen.add(key)
        if row["label"] not in ("relevant", "irrelevant", "unjudged"):
            raise ReviewError("invalid_review_label")
        _text(row["reviewer"], 200)
        _text(row["reason"], 2000)
        evidence = row["evidence"]
        if evidence is None and row["label"] != "unjudged":
            raise ReviewError("review_evidence_required")
        if evidence is not None:
            source = _object(evidence)
            _fields(source, {"actual_source_course_id", "source_sha256", "pointer", "excerpt"})
            if _text(source["actual_source_course_id"]) != key[1]:
                raise ReviewError("review_evidence_course_mismatch")
            _hash(source["source_sha256"])
            if source["pointer"] is None and source["excerpt"] is None:
                raise ReviewError("review_evidence_location_required")
            if source["pointer"] is not None:
                _text(source["pointer"], 512)
            if source["excerpt"] is not None:
                _text(source["excerpt"], 500)
        judgments.append(row)
    if seen != expected:
        raise ReviewError("missing_review_judgment")
    return sorted(judgments, key=_key)


def _counts(labels: list[str]) -> dict[str, object]:
    counts = Counter(labels)
    judged = counts["relevant"] + counts["irrelevant"]
    return {"returned_pairs": len(labels), "judged_pairs": judged, "relevant_pairs": counts["relevant"],
            "explicit_negative_hits": counts["irrelevant"], "unjudged_pairs": counts["unjudged"],
            "judgment_coverage": judged / len(labels) if labels else None}


def _adjudication(submission: dict[str, object], digest: str, judgments: list[dict[str, object]]) -> dict[str, object]:
    return {**submission, "artifact_type": "offline_relevance_adjudication", "judgments": judgments,
            "submission_sha256": digest, "scope": SCOPE}


def _diagnostics(result: dict[str, object], adjudication: dict[str, object]) -> dict[str, object]:
    labels = {_key(_object(row)): _text(_object(row)["label"]) for row in _array(adjudication["judgments"])}
    cases: list[dict[str, object]] = []
    all_labels: list[str] = []
    aggregate: dict[str, list[str]] = {str(k): [] for k in _array(_object(result["search_settings"])["cutoffs"])}
    for value in _array(result["cases"]):
        case = _object(value)
        if case["status"] != "ok":
            cases.append({"case_id": case["id"], "status": "error"})
            continue
        identifiers = [_text(_object(row)["source_course_id"]) for row in _array(case["candidates"])]
        unique = set(identifiers)
        case_labels = [labels[(_text(case["id"]), identifier)] for identifier in sorted(unique)]
        all_labels.extend(case_labels)
        at_k: dict[str, object] = {}
        for cutoff, collected in aggregate.items():
            subset = [labels[(_text(case["id"]), identifier)] for identifier in sorted(set(identifiers[:int(cutoff)]))]
            collected.extend(subset)
            at_k[cutoff] = _counts(subset)
        cases.append({"case_id": case["id"], "status": "ok", **_counts(case_labels),
                      "returned_rows": len(identifiers), "duplicate_rows": len(identifiers) - len(unique),
                      "explicit_negative_course_ids": sorted(identifier for identifier in unique
                                                             if labels[(_text(case["id"]), identifier)] == "irrelevant"),
                      "at_k": at_k})
    return {"schema_version": 1, "artifact_type": "offline_relevance_diagnostics", "scope": SCOPE,
            "binding": adjudication["binding"], "packet_sha256": adjudication["packet_sha256"],
            "submission_sha256": adjudication["submission_sha256"],
            "summary": {**_counts(all_labels), "successful_cases": sum(row["status"] == "ok" for row in cases),
                        "failed_cases": sum(row["status"] == "error" for row in cases)},
            "at_k": {cutoff: _counts(labels) for cutoff, labels in aggregate.items()}, "cases": cases}


def _markdown(value: dict[str, object]) -> str:
    lines = ["# Offline relevance review", "", SCOPE, "", "```json",
             json.dumps({key: value[key] for key in ("artifact_type", "binding", "packet_sha256", "summary", "at_k")
                         if key in value}, ensure_ascii=False, indent=2), "```", ""]
    if value["artifact_type"] == "offline_relevance_adjudication":
        labels = [_text(_object(row)["label"]) for row in _array(value["judgments"])]
        lines.extend(["Validated submission structure and bindings; no source fact verification.", "", "```json",
                      json.dumps(_counts(labels), indent=2), "```", ""])
    if value["artifact_type"] == "offline_relevance_diagnostics":
        lines.extend(["Counts use distinct (case, course) pairs; repeated offerings do not add judgments.", "",
                      "| Case | Status | Pairs | Judged | Explicit negative hits | Unjudged | Duplicate rows |",
                      "| --- | --- | ---: | ---: | ---: | ---: | ---: |"])
        for item in _array(value["cases"]):
            row = _object(item)
            cells = [row.get(key, "—") for key in ("case_id", "status", "returned_pairs", "judged_pairs",
                                                  "explicit_negative_hits", "unjudged_pairs", "duplicate_rows")]
            lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ").replace("\r", " ") for cell in cells) + " |")
    if value["artifact_type"] == "offline_relevance_packet":
        lines.extend(["Items are ordered by case/course ID, with rank, similarity and original labels hidden.",
                      "Complete the separate submission template; preserve this packet unchanged.", "",
                      "| Case | Course | Query | As of | Filters |", "| --- | --- | --- | --- | --- |"])
        for item in _array(value["items"]):
            row = _object(item)
            cells = [row[key] for key in ("case_id", "source_course_id", "query", "as_of", "filters")]
            lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ").replace("\r", " ") for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def _write(outputs: dict[Path, bytes], inputs: list[Path]) -> None:
    paths = [path.resolve() for path in [*inputs, *outputs]]
    if len(set(paths)) != len(paths):
        raise ReviewError("review_path_conflict")
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise ReviewError("review_output_already_exists")
    if any(not path.parent.is_dir() for path in outputs):
        raise ReviewError("review_output_directory_required")
    if any(len(content) > MAX_BYTES for content in outputs.values()):
        raise ReviewError("review_output_too_large")
    # Exclusive creation protects against an output appearing after validation.
    for path, content in outputs.items():
        with path.open("xb") as stream:
            stream.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("export", "import", "report"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--result", required=True, type=Path)
        subparser.add_argument("--output-json", required=True, type=Path)
        subparser.add_argument("--output-report", required=True, type=Path)
        if command == "export":
            subparser.add_argument("--output-review", required=True, type=Path)
        else:
            subparser.add_argument("--packet", required=True, type=Path)
            subparser.add_argument("--review", required=True, type=Path)
            if command == "report":
                subparser.add_argument("--adjudication", required=True, type=Path)
    args = parser.parse_args()
    try:
        result, binding = _result(args.result)
        inputs = [args.result]
        if args.command == "export":
            output = _packet(result, binding)
            content = _encode(output)
            digest = hashlib.sha256(content).hexdigest()
            outputs = {args.output_json: content, args.output_review: _encode(_template(output, digest))}
        else:
            inputs.append(args.packet)
            packet, digest = _bound_packet(args.packet, result, binding)
            inputs.append(args.review)
            submission, submission_hash = _read(args.review)
            judgments = _judgments(submission, packet, digest, "offline_relevance_submission")
            adjudication = _adjudication(submission, submission_hash, judgments)
            if args.command == "import":
                output = adjudication
            else:
                inputs.append(args.adjudication)
                retained, retained_hash = _read(args.adjudication)
                _judgments(retained, packet, digest, "offline_relevance_adjudication")
                if retained != adjudication:
                    raise ReviewError("review_adjudication_submission_mismatch")
                output = {**_diagnostics(result, retained), "adjudication_sha256": retained_hash}
            outputs = {args.output_json: _encode(output)}
        outputs[args.output_report] = _markdown(output).encode("utf-8")
        expected_outputs = 3 if args.command == "export" else 2
        if len(outputs) != expected_outputs:
            raise ReviewError("review_path_conflict")
        _write(outputs, inputs)
        print(json.dumps({"status": "ok", "artifact_type": output["artifact_type"]}))
        return 0
    except ReviewError as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({"status": "failed", "error": "invalid_review_input_or_output"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
