# Work24 records adapter

`tools/catalog/work24_records.py` accepts supplied `{"records": [...]}` JSON. It does not collect data, call an embedding model, or infer personal prices. This change uses synthetic fixtures; the original source's collection method, collection time and permission evidence remain unverified. It is partial implementation of [#22](https://github.com/today-tommorrow/today-tommorrow/issues/22), not evidence that those prerequisites or catalog API work are complete.

Each record needs a nonblank `course_name`. Optional source fields are `provider_name`, `region`, `start_date`, `end_date`, `training_hours`, `tuition_krw`, `listed_self_pay_krw`, `curriculum`, `eligibility`, and `url`. Dates use `YYYY-MM-DD`; hours and amounts are finite nonnegative numbers (amounts are whole KRW). Explicit `N/A` and `not_applicable` remain distinct from null and zero. Curriculum accepts source text or a list of nonblank source texts; unsupported shapes quarantine the offering. Unknown fields and `source_excel_row` remain in `work24.raw_json`; unknown fields are also reported. Empty/malformed records arrays reject the whole input.

Source `course_id` and `tme` are exact nonempty strings: no trimming, case folding or numeric conversion. Historical `cohort` maps to `tme` only when `tme` is absent; when both exist they must match exactly. `offering_id` is preserved independently, never silently substituted for a cohort. A record missing either public key receives an internal observation UUID and a conservative provider/name/region/start/end fingerprint **in its quarantine report only**. The UUID is stable only for identical-content replay and row reordering: filling a null changes it, while identical observations share one. It is not a durable offering ID. Fingerprints identify duplicate candidates and never authorize merging or publishing. Row numbers are provenance, never identity. No source URL is fabricated. Missing-ID publication needs a separately reviewed durable mapping and consumer contract.

Normalization reports success, offering-level quarantine and duplicate candidates separately. Conflicting observations for the same key quarantine that entire offering; identical duplicate observations count once as an offering. Invalid entries sharing a valid offering key also block that offering. No records are published by normalization alone.

For supplementation, explicitly supply `--previous` with `{"dataset_id": "<parent snapshot>", "courses": [<stored metadata>, ...]}`. This is an operator-supplied export, not a database attestation. The adapter checks the explicit parent ID and records both its canonical content hash and input file hash. Export the metadata from that exact snapshot; `stage` and `activate` retain the existing parent/profile/stale-parent checks. Only matching source keys can supplement. Previously observed values, including zero and N/A, win over incoming nulls. New observations fill only previous nulls. Two different observed values (including different curriculum) quarantine the offering; there is no row-order winner. Raw history, original top-level provenance, and per-field `source_hash`/`collected_at` track carried versus supplied facts separately. Unknown collection times stay null. This adapter does not authorize replacing known course content.

Consumer fields map `provider_name → institution`, `tuition_krw → training_fee`, `listed_self_pay_krw → displayed_self_pay`, and `eligibility → metadata.훈련대상요건`; source fields remain alongside them. The current API presents nonnumeric N/A as an unknown numeric price while preserving raw metadata and evidence. A listed amount is never a personalized final price; the report states `PERSONAL_PRICE_UNKNOWN`.

```sh
uv run --project tools/catalog --locked --python 3.14.6 \
  python tools/catalog/work24_records.py supplied-records.json \
  --parent-dataset-id "$PARENT_DATASET_ID" \
  --normalized-output normalized.json
```

Use `--source-hash` only for a known original file SHA256 and `--collected-at` only for a known timezone-aware collection time. Neither is guessed from import time. With no values, both are null.

Publication additionally requires `--precomputed <verified-package-directory> --package-output <new-directory> --created-on YYYY-MM-DD`. The package must pass the existing artifact checksum, profile, text hash, unit key, vector row, float32 1024-dimensional shape and normalization checks. Each source curriculum text maps to one `content_item`, with an empty NCS code and the exact text as its unit name and embedding text. Matching vectors are selected by unit key and verified text/profile, never positional coincidence. Unit provenance and vector bytes come from the supplied package; metadata-only supplementation reuses them. Changed or missing content has no vector fallback. Missing vectors or missing curriculum reject package creation. Synthetic vectors exist only in test fixtures. The saved smoke query is also reused, never generated.

The output always uses **partial** coverage consisting exclusively of accepted offerings. Quarantined keys occur in neither course/unit artifacts nor `offering_keys`, so activating a mixed batch cannot delete or replace a quarantined parent offering. Adapter packages carry per-key expected parent metadata hashes or expected absence. The existing stage boundary verifies these against the actual database parent; omitted or incomplete previous exports cannot overwrite an existing offering. Parent unit keys, fields, provenance and vector bytes must also match exactly for supplementation. The stage rejects missing guards and full coverage for adapter packages. A previous export may include legacy metadata without the adapter history; those rows protect identity but cannot be supplemented until their source mapping is reviewed. Follow the existing [batch guide](catalog-batches.md) to stage and activate; this adapter does not duplicate that database lifecycle.

`source_dataset_id` retains #24's artifact lineage meaning: a supplied complete replacement points to the incoming package dataset. It is not a collection timestamp or recollection claim. Original source collection evidence remains in unit `provenance` and course/field `source_hash` and `collected_at`; metadata-only supplementation preserves those. Untouched/quarantined offerings retain their parent `source_dataset_id` as well.

`batch.adapter=work24_records_v1` explicitly identifies adapter output; ordinary #24 packages retain their existing authoritative replacement behavior and may contain arbitrary extra metadata, including a `work24` key. The adapter enforces null-only supplementation during normalization. Its stage guard verifies agreement with the real parent and preservation of units; it is not an authorization policy or a proof of null-only merging for manually rewritten artifact metadata. Normal package checksum review and explicit activation remain required.

Local validation:

```sh
uv run --project tools/catalog --locked --python 3.14.6 python -m unittest discover -s tools/catalog/tests
# Only a newly owned disposable PostgreSQL16 + pgvector0.6 database:
WORK24_RECORDS_TEST_DATABASE_URL='<isolated local test DSN>' \
  uv run --project tools/catalog --locked --python 3.14.6 \
  python -m unittest discover -s tools/catalog/integration -p test_records_database.py
```

The integration test stores, activates and rereads null/zero/N/A, supplementary facts, field provenance, vectors and a mixed valid/conflicting batch. It seeds only synthetic records into a new database. Existing developer databases must not be used. Model CPU/GPU checks are N/A: no model executes. GitHub Actions remain disabled; these commands are local only.

## Validation on 2026-11-01

Local checks used Python 3.14.6, Node 26.5.0, PostgreSQL 16.14 and pgvector 0.6.0. The 26 catalog unit tests and the owned synthetic database test passed, including persisted metadata through the existing course presenter. Web type/lint/format checks, 276 web tests, Ruff, 22 script tests and the production web build passed. A separate owned database loaded and verified the existing pinned 406-course/6,099-unit package; this reused the supplied artifact and performed no new collection or model inference. The complete catalog integration discovery ran 11 tests successfully with the opt-in synthetic database test skipped there; that test passed separately on its own disposable database.

The required `./check` invocation did **not** pass completely: existing `test_saved_courses.SavedDatabaseTests.test_parallel_put_is_idempotent` received a 503. The owned database log showed `UniqueViolation` on `uq_saved_source_owner` while that INSERT handled a different conflict key. The complete saved-course module passed on a focused rerun (12 tests); that does not turn the failed full check into a success. This unrelated saved-course race is outside #22 and no backend behavior was changed here. Credentials and account identifiers are omitted.
