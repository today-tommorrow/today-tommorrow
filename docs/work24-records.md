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

Publication additionally requires `--precomputed <verified-package-directory> --package-output <new-directory> --created-on YYYY-MM-DD`. The package must pass the existing artifact checksum, profile, text hash, unit key, vector row, float32 1024-dimensional shape and normalization checks. For new and ordinary adapter rows, each source curriculum text maps to one `content_item`, with an empty NCS code and the exact text as its unit name and embedding text. Matching vectors are selected by unit key and verified text/profile, never positional coincidence. Unit provenance and vector bytes come from the supplied package; metadata-only supplementation reuses them. Changed or missing content has no vector fallback. For that ordinary path, missing vectors or missing curriculum reject package creation. Reviewed legacy rows instead carry the complete original unit collection, as described below. Synthetic vectors exist only in test fixtures. The saved smoke query is also reused, never generated.

The output always uses **partial** coverage consisting exclusively of accepted offerings. Quarantined keys occur in neither course/unit artifacts nor `offering_keys`, so activating a mixed batch cannot delete or replace a quarantined parent offering. Adapter packages carry per-key expected parent metadata hashes or expected absence. The existing stage boundary verifies these against the actual database parent; omitted or incomplete previous exports cannot overwrite an existing offering. Parent unit keys, fields, provenance and vector bytes must also match exactly for supplementation. The stage rejects missing guards and full coverage for adapter packages. A previous export may include legacy metadata without the adapter history; those rows protect identity but cannot be supplemented until their source mapping is reviewed. Follow the existing [batch guide](catalog-batches.md) to stage and activate; this adapter does not duplicate that database lifecycle.

`source_dataset_id` retains #24's artifact lineage meaning: a supplied complete replacement points to the incoming package dataset. It is not a collection timestamp or recollection claim. Original source collection evidence remains in unit `provenance` and course/field `source_hash` and `collected_at`; metadata-only supplementation preserves those. Untouched/quarantined offerings retain their parent `source_dataset_id` as well.

`batch.adapter=work24_records_v1` explicitly identifies adapter output; ordinary #24 packages retain their existing authoritative replacement behavior and may contain arbitrary extra metadata, including a `work24` key. The adapter enforces null-only supplementation during normalization. Its stage guard verifies agreement with the real parent and preservation of units; it is not an authorization policy or a proof of null-only merging for manually rewritten artifact metadata. Normal package checksum review and explicit activation remain required.

## Reviewed legacy mapping

Legacy metadata stays quarantined by default. An operator may supply `--legacy-mapping reviewed-mapping.json` alongside `--previous` to authorize the documented `work24_catalog_v1` mapping for explicitly reviewed offerings only:

```json
{
  "version": 1,
  "profile": "work24_catalog_v1",
  "parent_dataset_id": "<exact current parent snapshot SHA256>",
  "reviewed_by": "<review reference or reviewer>",
  "offerings": [{
    "offering_key": ["<exact source course_id>", "<exact source tme>"],
    "metadata_sha256": "<canonical stored course metadata SHA256>",
    "unit_keys": ["<every original unit key for this exact offering>"]
  }]
}
```

The placeholder hashes must be replaced with actual lowercase SHA256 values. Compute metadata hashes with `artifacts.value_hash` against the complete metadata object exported from that parent. The reviewer must compare the mapping and full unit-key collection with the actual parent and the supplied original artifact. `reviewed_by` records that review; it is not a signature, independent source permission, or database attestation. No missing mapping, fingerprint, row number, name similarity or stale metadata hash authorizes supplementation. Malformed mapping documents reject the input; an unmapped or mismatched legacy offering remains quarantined.

The profile maps `institution → provider_name`, `training_fee → tuition_krw`, `displayed_self_pay → listed_self_pay_krw`, and `metadata.훈련대상요건 → eligibility`. Other supported fields retain their same top-level names. Contradictory known aliases quarantine the offering. Exact timezone-free midnight ISO timestamps can be represented as dates in `work24.values`; the original top-level timestamps and full legacy metadata remain unchanged. Other timestamp shapes require a separately reviewed mapping. `metadata.훈련내용` remains legacy content and does not become adapter curriculum. If legacy curriculum is null, incoming nonnull curriculum quarantines the offering because this mapping does not authorize changes to embedded content.

The complete legacy object, including unknown keys, `metadata`, `original_csv`, `excel_provenance` and `provenance`, is retained in `work24.legacy_parent.metadata` and initial raw history. Existing top-level facts and nested metadata remain, while new facts fill only nulls. Missing `source_hash` and `collected_at` become explicit nulls; neither is invented from artifact hashes or import time. Carried field provenance uses the original known source hash/time or those explicit unknowns. Mapping evidence is retained with the original metadata and unit keys across subsequent supplementary batches.

For mapped legacy rows, `--precomputed` must contain that exact original course metadata and **every** mapped unit for the offering. Packaging selects those units directly, including page, timetable, NCS, inferred and other existing unit types. It never calls `unit_templates` for them. All unit fields and vector bytes are carried; only package dataset identity and vector-row position change. The existing adapter stage guard compares the full unit collection and metadata against the actual database parent. Its float32 vector comparison uses shape and canonical little-endian bytes, so a signed-zero change is rejected too. Ordinary non-adapter batch behavior is unchanged. Later supplementation can reuse the original artifact without a new mapping file when the stored reviewed history remains valid; the normal current-parent guard still applies.

Local validation:

```sh
uv run --project tools/catalog --locked --python 3.14.6 python -m unittest discover -s tools/catalog/tests
# Only a newly owned disposable PostgreSQL16 + pgvector0.6 database:
WORK24_RECORDS_TEST_DATABASE_URL='<isolated local test DSN>' \
  uv run --project tools/catalog --locked --python 3.14.6 \
  python -m unittest discover -s tools/catalog/integration -p test_records_database.py
```

Legacy coverage has separate opt-ins, each requiring its own new empty disposable database:

```sh
WORK24_LEGACY_TEST_DATABASE_URL='<owned synthetic test DSN>' \
WORK24_PINNED_TEST_DATABASE_URL='<different owned pinned-artifact test DSN>' \
WORK24_PINNED_ARCHIVE='<existing work24-bge-m3-20261101.zip>' \
  uv run --project tools/catalog --locked --python 3.14.6 \
  python -m unittest discover -s tools/catalog/integration -p test_legacy_records_database.py
```

The legacy tests stage and activate synthetic enrichment, conflicting observations and repeated supplementation; reject changed parent metadata/units and signed-zero vector changes; and compare all stored unit fields and vector bytes. The optional pinned-artifact test reuses the existing 406-course/6,099-unit package with synthetic supplementary facts, preserving a quarantined offering and all original units. This is local DB validation of supplied artifacts, not new source collection, permission verification or model execution.

The integration test stores, activates and rereads null/zero/N/A, supplementary facts, field provenance, vectors and a mixed valid/conflicting batch. It seeds only synthetic records into a new database. Existing developer databases must not be used. Model CPU/GPU checks are N/A: no model executes. GitHub Actions remain disabled; these commands are local only.

## Validation on 2026-11-01

Local checks used Python 3.14.6, Node 26.5.0, PostgreSQL 16.14 and pgvector 0.6.0. The 26 catalog unit tests and the owned synthetic database test passed, including persisted metadata through the existing course presenter. Web type/lint/format checks, 276 web tests, Ruff, 22 script tests and the production web build passed. A separate owned database loaded and verified the existing pinned 406-course/6,099-unit package; this reused the supplied artifact and performed no new collection or model inference. The complete catalog integration discovery ran 11 tests successfully with the opt-in synthetic database test skipped there; that test passed separately on its own disposable database.

The required `./check` invocation did **not** pass completely: existing `test_saved_courses.SavedDatabaseTests.test_parallel_put_is_idempotent` received a 503. The owned database log showed `UniqueViolation` on `uq_saved_source_owner` while that INSERT handled a different conflict key. The complete saved-course module passed on a focused rerun (12 tests); that does not turn the failed full check into a success. This unrelated saved-course race is outside #22 and no backend behavior was changed here. Credentials and account identifiers are omitted.

## Legacy mapping validation on 2026-11-01

The earlier failure above describes the adapter work before PR #73. With the legacy mapping changes on base `f986985` (merged PR #73), `./check` passed on an owned disposable PostgreSQL 16.14/pgvector 0.6.0 database using cached Python 3.14.6 and Node 26.5.0. Results: web type/lint/format and 276 tests, configured Ruff, 264 backend tests (one skip), 22 script tests, 32 catalog unit tests, 13 catalog integration tests (three opt-in records tests skipped), and the production build passed. The skipped records checks passed separately on owned empty databases: the existing synthetic adapter test, the new legacy synthetic test including signed-zero rejection, and the pinned-artifact test covering all 406 courses/6,099 units with one conflicting offering preserved. The focused unit tests also cover the mapping CLI, contradictory nested aliases, invalid stored parent hashes, carried-value/provenance inconsistencies and incomplete unit mappings.

The final change was rebased onto `2f68445` (PR #72), which modifies file intake outside this catalog scope. Catalog unit tests, configured Ruff and whitespace checks passed after that clean rebase; the complete check above ran before the rebase and was not repeated. Existing pinned artifacts were reused locally without collection or inference. Real additional source batches, original collection permission/time verification, API contract changes and model CPU/GPU execution remain outside this validation; this remains `Refs #22`.

## 원격 운영 적용 검증

현재 고정 원천에 대한 [운영 카탈로그 적용과 최신 검증](work24-production.md)을 완료했다. 기존 결측 보완 계약은 유지하고, 원천 ID 없는 관측은 내부 UUID/fingerprint와 함께 격리한다. 자동 병합이나 새로운 영구 발행 정책을 이 범위에서 도입하지 않는다. 과거 검증 기록과 현재 운영 적용 기록은 구분한다.
