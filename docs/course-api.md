# Work24 course detail and comparison API

Authenticated users can read `GET /api/courses/{id}` and `GET /api/courses/compare?ids=<uuid>,<uuid>[,<uuid>]`. Both use the existing session cookie. They perform no model calls and require no model configuration. GPU verification is N/A for these endpoints.

Comparison returns `{ "items": [CourseDetail, CourseDetail] }` in requested order. Exactly two or three distinct hyphenated UUIDs are required; case-insensitive duplicates, missing/empty/repeated `ids`, whitespace, invalid UUIDs and unsupported counts return 422. The static comparison route precedes the dynamic detail route. A missing or inactive course makes the entire request 404, never a partial success. Both success and application errors are `Cache-Control: no-store`. Anonymous requests return 401. These are shared public-source course records, not private recommendation records; the existing contract does not require a prior recommendation or a `run_id` parameter.

## Configuration and source boundary

Set these server-only variables from the reviewed package:

```dotenv
CATALOG_PROFILE_ID=770be6a5f97f6ed60075c24f64e8d8ea91786a86e7ba22e5fe8df1e6c5477a90
CATALOG_DATASET_ID=7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80
CATALOG_MANIFEST_SHA256=f4a79774ae2a470d7a9b80626cece532ad821900923069ba6875c9c9816cd88c
```

The manifest hash uses UTF-8 canonical JSON (`ensure_ascii=False`, sorted keys, compact separators, no non-finite numbers). Missing configuration, unavailable source, or changed configuration/manifest fails with 503. Settings validate the reviewed bootstrap anchor, then reads resolve the operator's active snapshot and validate its profile/config and dataset/input-file hashes. Detail and comparison use one read-only repeatable-read transaction with a 10-second SQL statement limit. Comparison cannot mix snapshots. These routes never write source data or invoke embeddings. See [batch activation](catalog-batches.md) for the additive education snapshot tables and pointer. Historical recommendation snapshots remain a separate API responsibility; these endpoints expose the active catalog.

Public UUIDs preserve the existing search contract: namespace `a4b29d71-4f3e-50ab-9e95-dfba02578443`, compact JSON `['work24', source_course_id]` for courses and `['work24', source_course_id, exact_text_tme]` for offerings. Source text is never trimmed or converted to numbers. Evidence uses `['evidence', dataset_id, source_course_id, tme, field_key]` in the same namespace. The repository scans source course keys to resolve these stable UUIDs, then fetches only requested courses and active units. The pinned corpus has 406 offerings and 6,099 units; no vector data is fetched.

## Truthful source presentation

All active offerings and units for the selected course are returned. Source quotes are not truncated to an LLM prompt budget. Nullable prices, schedule, eligibility, dates and source-verification timestamps remain null; zero remains zero. `listed_self_pay_krw` is the listed conditional amount, not the user's confirmed payment. Known source delivery labels map to online/offline; unsupported labels remain unknown. Recruitment is unknown because a static package cannot establish current enrollment availability.

Description is the observed objective, when consistent across offerings. Eligibility text is not silently relabelled as a prerequisite. Units/objectives are not converted into asserted learning outcomes. Missing explicit prerequisite/outcome values remain absent. Conflicting common descriptions/provider/prerequisite fields remain null and their evidence is marked conflicting; individual offering values and quotes remain available. Course name and official URL use the first offering in stable exact-source-key order.

Inferred units are preserved as `inferred_units.<unit_key>` evidence with `verification_state=unknown`. Without a direct source URL, inferred material has `source_url=null`; its host course page is not claimed as the source of an inferred quote. `checked_at` stays null everywhere because the package has no trustworthy source-verification timestamp. Package creation/import/query dates are never substituted. `missing_fields` lists unresolved course and offering fields; missing values do not mean free, unavailable or ineligible.

## Verification

After the existing local DB setup and migrations, export `DATABASE_URL` and `L0_DATABASE_TESTS=1`, then run from `backend`:

```sh
python -m unittest discover -s tests -p 'test_course_api.py' -v
ruff check app/courses app/main.py tests/test_course_api.py
```

Tests use actual pinned source rows and real PostgreSQL sessions, with synthetic authenticated users. They verify immutable ID examples, exact metadata/quote preservation, multiple offerings, unknown versus zero values, inferred evidence, comparison order and equality to detail, malformed/duplicate parameters, all-or-nothing missing IDs, changed pins, source write denial and read-only repeatable-read transactions. Browser and complete repository checks are recorded separately in the implementation PR; focused backend checks alone do not establish issue completion.
