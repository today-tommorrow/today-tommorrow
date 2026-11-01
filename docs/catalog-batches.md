# Work24 batch activation and history

Issue #24 adds a local operator flow for the existing public Work24 vector package. It does not collect K-MOOC or external NCS data, normalize Work24 records, infer missing values, or run models. Those adapter concerns remain outside this flow.

Run `./setup` once to initialize the original 406-course/6,099-unit package. The bootstrap keeps its fixed archive checksum, counts, and read-only verification. It also creates an immutable baseline in `work24_bge.snapshot_course` and `snapshot_unit`. Subsequent setup runs preserve the active pointer. Application Alembic migrations still manage only `today_app`; education DDL remains in `tools/catalog/schema.sql` and `snapshot_schema.sql` under the bootstrap role.

## Existing production catalog upgrade

`tools/catalog/upgrade.py` is a separate, explicit path for an existing database containing only the original four catalog tables. It does not relax the local bootstrap target guard. Take and verify an external database backup before applying. Supply the existing pinned ZIP, the existing schema owner's connection through `CATALOG_UPGRADE_DATABASE_URL`, expected database/user/profile/dataset identities, and an existing unprivileged reader role. Absolute Unix socket paths support peer authentication without a database password; TCP connections require their own database password. Neither path reads SSH credentials.

Run the following first without `--apply`. Replace the identity placeholders with the independently verified values. Keep reports outside the source ZIP and use a new report path for each invocation; existing files are never replaced.

```sh
python tools/catalog/upgrade.py --archive /path/to/work24-bge-m3-20261101.zip \
  --expected-database work24 --expected-user ncloud \
  --expected-profile-id "$CATALOG_PROFILE_ID" --expected-dataset-id "$CATALOG_DATASET_ID" \
  --reader-role today_app --report /path/to/new-plan.json
```

The default transaction is enforced read-only. Review `plan.operations`, which lists tables and constraints, primary/HNSW indexes, identity sequences, exact baseline copy counts, initial pointer/history inserts, and table-specific reader grants. The deterministic `plan_sha256` binds the database identity, source state, pinned package, dataset, profile, shared DDL, and operations. To apply that reviewed plan, repeat the same command with a different report path and add `--apply --expected-plan-sha256 <reviewed-plan-hash>`.

Apply obtains the existing loader advisory lock and locks the base tables before reading the latest committed rows. It compares every original metadata field and float32 vector byte, creates only the five snapshot/versioning tables and their indexes/sequences, copies the baseline, inserts the active pointer/history, and grants only `SELECT` on the five new tables. All changes share one transaction. Existing tables, views, roles, passwords, global/default privileges, and other schemas are preserved. Partial schemas, changed content, unexpected owners/ACLs, and incompatible columns/constraints/indexes fail closed.

A completed upgrade is verified and returns `already_upgraded`, retaining any valid later active snapshot without another write; its old plan hash need not be supplied again. Exit `0` means success. Exit `1` means validation/database failure; the CLI does not emit a success report. Exit `2` means the requested report file could not be published: stdout retains the database result and explicitly says `committed_report_write_failed` after apply. A report-file error does not imply database rollback. Re-run the default read-only check to verify state.

## Incoming package

Supply a local directory in the existing package format: `manifest.json`, `embedding-config.json`, `courses.jsonl`, `units.jsonl`, `vectors.npy`, `smoke-query.npy`, `truncation-report.json`, `local-search-results.json`, and `validation-report.json`. Package Python and SQL are never executed. Only the existing local development database/role allowlist is accepted.

The manifest retains the existing `embedding_config`, canonical profile hash, `input_files`, canonical source dataset hash, artifact byte lengths/SHA256 hashes, `created_on`, and `counts.courses/units`. Counts are checked against the incoming arrays and vectors rather than the bootstrap's fixed counts. It additionally requires this batch declaration:

```json
{
  "batch": {
    "source_system": "work24",
    "parent_dataset_id": "<64-character active dataset SHA256>",
    "coverage_mode": "partial",
    "offering_keys": [["public-source-course-A", "01"]]
  }
}
```

Every incoming course also explicitly supplies `source_hash` and `collected_at`. A known source hash is a lowercase SHA256 of its source record; a known collection timestamp is an ISO timestamp with a timezone. Explicit `null` means unknown. Missing keys, malformed hashes, and timezone-free timestamps fail admission. Artifact checksums and source hashes have different meanings. `created_on`, import time, activation time, and public `checked_at` do not establish collection or verification time. The original public package lacks these source fields; its unknown values stay unknown. Carried course metadata, unit provenance, vector rows, and original source dataset IDs are copied without rewriting their hashes/times.

Coverage uses exact source `(course_id, tme)` strings. Whitespace, case, and leading zeroes remain significant. In `partial`, coverage must exactly name incoming offerings; absent existing offerings survive. Incoming offerings replace their corresponding course metadata and complete unit collection, with no field-level missing-value merge. In `full`, incoming offerings must be within the declared coverage, and covered offerings absent from the package are removed from the new snapshot. Offerings outside coverage survive. Both incoming course and unit collections must remain nonempty, including full batches. Duplicate keys, courses without units, orphan units, invalid unit/text hashes, nonfinite or incompatible vectors, and profile changes fail validation.

## Commands and reports

Use the existing three local database variables from `.env`; do not place credentials in reports. `<python>` is the interpreter pinned by `.python-version`. `<manifest-sha256>` is the SHA256 of the exact manifest file bytes, supplied independently by the operator. It differs from the canonical batch ID returned in the report.

```sh
# Stage and validate an isolated draft; keep the current catalog active.
uv run --env-file .env --project tools/catalog --locked --python <python> \
  python tools/catalog/batches.py --directory /tmp/work24-batch \
  --manifest-sha256 <manifest-sha256> --stage-only --report .local/catalog/batch.json

# Activate that validated draft in a separate short transaction.
uv run --env-file .env --project tools/catalog --locked --python <python> \
  python tools/catalog/batches.py --activate <batch-id> --report .local/catalog/activation.json

# Without --stage-only, staging commits before the separate activation transaction.
```

Example success report (identities abbreviated here only):

```json
{"state":"ready","batch_id":"<batch-id>","parent_dataset_id":"<parent-id>","dataset_id":"<effective-id>","courses":408,"units":6101,"coverage_mode":"partial","incoming_courses":2,"incoming_units":2}
```

Activation returns the same report with `state: "activated"`. An empty package yields `{"state":"failed","error":"empty_catalog_batch","batch_id":"<batch-id>"}`. A missing or malformed artifact yields a safe fixed error, never a DSN, raw record, or driver message. Reports are written atomically. Exit status is 0 for ready/activated, 1 for failure.

Validated manifest attempts are recorded in `catalog_batch`; failures inside draft construction roll back draft rows and retain a failure report/history. Fixing artifact bytes to match the same manifest permits retry. An untrusted/unreadable manifest or invalid coverage fails before a trusted DB attempt exists and still returns a CLI failure report. Repeating an already ready/activated batch returns its existing report and does not reactivate an older version.

`active_catalog` contains one pointer. Activation takes the existing loader advisory lock, locks the pointer and batch, verifies readiness and the expected parent, and changes the pointer plus `catalog_activation` history in one transaction. A competing activation from the same old parent leaves the losing ready draft intact and fails with `batch_parent_changed`. No failure or empty package clears the active catalog. Snapshot metadata, unit links, float32 round trips, and the valid HNSW index are checked before readiness; model-free search remains exact.

## Consumers and retained versions

Search, saved lists, course detail, and comparisons follow the active pointer. Each comparison reads its pointer and all rows in one read-only repeatable-read transaction. Existing `CATALOG_PROFILE_ID`, `CATALOG_DATASET_ID`, and course API `CATALOG_MANIFEST_SHA256` settings validate the reviewed bootstrap anchor; an activation does not require changing those settings or restarting the worker. Compatible active snapshots use the same profile.

A recommendation captures the active version at submission and validates/reuses that historical version during execution, including across worker retries. An activation between submission, embedding, and retrieval cannot switch its catalog. Model/config identity checks remain in force. The existing recommendation worker stores the public result and private provenance, and authenticated historical retrieval returns that saved result without reading the current catalog. Course/offering UUID derivation and public response schemas are unchanged. Saved-course summaries and original saved version IDs remain preserved; existing signed cursors fail with 409 after an activation.

Snapshots and activation/failure history are retained. This flow does not delete old versions or provide a history-pruning policy.

## Verification evidence

Run `./check` against an isolated local database. Focused commands:

```sh
uv run --project tools/catalog --locked --python <python> python -m unittest discover -s tools/catalog/tests
uv run --env-file .env --project tools/catalog --locked --python <python> python -m unittest discover -s tools/catalog/integration
cd backend
uv run --env-file ../.env --locked --python <python> python -m unittest discover -s tests -p test_catalog_versions.py
```

`test_batches.py` covers partial carry-forward, bounded full replacement, original metadata/source IDs, failure isolation, stale-parent rejection, retry history, and repeated setup/import pointer protection. `test_catalog_versions.py` covers historical search, a queued recommendation surviving activation, saved/detail/compare consumers, and an activation during comparison reads. Providers in the recommendation test are explicitly synthetic. The baseline vector check uses all original float32 vectors and exact NumPy cosine comparison. No live model, CPU inference, remote GPU, or recommendation quality claim is involved; GPU is N/A for this issue.

Observed on 2026-11-01 using macOS arm64, Python 3.14.6, Node 26.5.0, an isolated PostgreSQL 16.14/pgvector 0.6.0 container, and the locked dependencies: `./check` passed 269 web tests, 232 backend tests, 22 script tests, 15 catalog unit tests, seven catalog integration tests, Ruff, web type/lint/format checks, and production build. The one backend skip was the existing opt-in destructive reimport mode. After adding the final CLI/report test and course-metadata roundtrip check, all eight affected catalog integration tests passed. The additional full-bootstrap reimport test then passed separately while a newer catalog was active, verifying that the actual reload/role/vector path preserved the pointer. All nine current catalog integration cases therefore passed across these final runs. The original 6,099 float32 rows round-tripped exactly; exact cosine error was `2.0570669723163348e-7`, below `1e-5`, and the stored-query HNSW diagnostic used the index with full top-10 overlap. No model/GPU execution was run or required.
