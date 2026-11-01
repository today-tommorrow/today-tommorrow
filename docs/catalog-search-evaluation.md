# Work24 search quality evaluation

This operator tool evaluates the existing `SearchService` without changing retrieval, ranking, filters, schemas, application APIs, workers or model services. It runs the fixed cases against one captured catalog snapshot and writes a JSON artifact plus a readable Markdown report. It is separate from the public application and the [single-query retrieval probe](catalog-search.md).

The evaluation set is a set of preconfirmed expectations, not an exhaustive relevance judgment over the entire corpus. A result absent from the expected list is **unjudged**, unless it is explicitly listed as irrelevant before execution. Do not turn the observed top results into expected results, label a vaguely related result as relevant, or infer that an unfamiliar topic must return an empty list. Retain failures and unfavorable results.

The checked-in [version 1 fixture](../backend/tests/fixtures/work24-search-evaluation-v1.json) contains 20 cases: 13 known-positive cases and seven unjudged review probes. It covers seven exact titles, Korean paraphrases, mixed English/Korean terms, broad goals, similar-topic ambiguity, potentially absent topics, and unknown cost/timetable conditions. The source package/course/unit identity hashes and minimum curriculum facts are retained in provenance. No explicit negative-course or empty-result judgments are established in this version; zero false positives therefore cannot establish precision. Original source records, full curriculum and vectors are excluded.

## Dataset contract

The UTF-8 JSON file has `schema_version: 1`, an `id`, a version string, a description, a `catalog` anchor containing exact `profile_id`, `dataset_id` and model `revision`, a `provenance` object, and a nonempty `cases` array. Each case contains:

| Field | Meaning |
| --- | --- |
| `id` | Unique stable case identifier. |
| `query` | Curated synthetic/public query, passed verbatim to `SearchService`. |
| `as_of` | Fixed ISO calendar date, never the execution day's automatic default. |
| `filters` | Existing `SearchFilters` fields; explicit dates are ISO strings. Missing values retain existing search semantics. |
| `judgment.status` | `judged` or `unjudged`. Unjudged cases must have empty relevant/irrelevant lists. |
| `judgment.relevant_course_ids` | Preconfirmed expected source course IDs. Their exact strings, including leading zeroes, are preserved. |
| `judgment.irrelevant_course_ids` | Explicitly reviewed negative source course IDs; never the complement of the positive list. |
| `judgment.reason` | Basis and limitations of the fixed judgment. Record curriculum evidence, source identity and review method in the fixture/provenance. |
| `expected_empty` | Optional independently justified boolean. Omit when an empty-result expectation cannot be established. This is separate from relevance judgments. |

Duplicate keys, case IDs or judgment IDs, conflicting positive/negative labels, unjudged labels carrying course judgments, and an empty expectation with positive expected IDs are rejected. Changing any input bytes changes the recorded evaluation SHA256. Version the fixture when reviewing or changing its expectations. Maintain separate baseline artifacts; the tool refuses to overwrite its dataset or existing output files.

## Live execution

From `backend`, use the existing application-role database connection and a compatible live CUDA BGE service. Configure these through the operator's existing environment; never put credentials, connection strings or private endpoint addresses in the fixture or committed reports.

```sh
python -m app.catalog.evaluate \
  --dataset tests/fixtures/work24-search-evaluation-v1.json \
  --profile-id "$CATALOG_PROFILE_ID" \
  --dataset-id "$CATALOG_DATASET_ID" \
  --base-url "$BGE_BASE_URL" \
  --timeout 30 \
  --limit 30 --evidence-limit 3 --k 1 5 10 30 \
  --output-json /tmp/work24-search-evaluation.json \
  --output-report /tmp/work24-search-evaluation.md
```

`DATABASE_URL` must already identify the authorized application role. The dataset's profile/dataset anchors must match the explicit arguments and the prepared snapshot; its model revision must also match. The tool captures the active snapshot once and passes it to every `SearchService.search(..., snapshot=pin)` call. Every returned snapshot must match, including the manifest hash. Snapshot validation failures abort rather than mixing versions. No stored-vector substitution, model fallback, automatic activation, model deployment or database configuration is performed.

`--help` and invalid dataset/options/output paths do not contact the model or database. `--k` values must be unique integers between one and the retrieval limit. Existing search bounds still apply: 1–100 courses and 1–10 evidence units. Create the output directories first and choose unused output filenames. Each HTTP call uses the explicit timeout; this is not a total-run deadline or a load test.

The live command exclusively uses the existing `BgeM3Embeddings` adapter. It verifies pinned model/runtime metadata before every query and rejects incompatible responses. `execution_mode: live_bge_cuda` identifies the requested path; `actual_bge_results` is true only when that path produced a successful search. A run with failed cases returns exit code 1 and preserves the successful case diagnostics alongside safe error categories. Infrastructure failures are not empty results. A setup/snapshot failure emits safe error JSON and exits without a completed report. If artifact writing itself fails, a partial output file may remain; use new filenames on a retry.

The artifact automatically records the exact evaluation-file SHA256 and version, precommitted judgments, expected/actual profile and dataset identities, model revision, snapshot manifest hash/date, fixed dates/filters, retrieval limit, evidence limit, cutoffs, observed retrieval version/mode, required normalization/tokenizer/preprocessing configuration, adapter runtime contract, request timeout, code commit and dirty flag, Python/OS/architecture, SQLAlchemy/psycopg versions, and PostgreSQL/pgvector versions. A dirty worktree is explicitly recorded; use a clean recorded commit for a reproducible baseline. The environment record contains no database URL, endpoint, host name, environment dump or authentication material. Results include course IDs, offering IDs and similarity signals, without raw course metadata, evidence text or embedding vectors. Curated queries and judgment reasons are included, so they must be safe to retain.

## Reading the metrics

For every requested `k`, expected-set Recall@k is the number of **distinct** preconfirmed expected course IDs returned in the first k positions divided by the number of preconfirmed expected IDs. It does not estimate recall over all relevant courses in Work24. Reciprocal rank at k is `1 / first_expected_rank` when a preconfirmed expected course appears within k, otherwise zero. MRR is the macro mean of these reciprocal ranks. Actual ranks include unjudged results and duplicate positions; duplicate rows never increase recall.

Only successfully completed `judged` cases with at least one positive expectation contribute to these means. The report exposes both requested and successful metric-eligible case counts, failed case count, and unjudged count. With no eligible successful cases the metrics are JSON `null`, never zero or a perfect score. A judged case with negatives but no positives can diagnose false positives, but cannot define a recall denominator.

Diagnostics retain separate meanings:

- **Missing expected IDs:** the preconfirmed expected courses absent from the first k results. The report records these at every cutoff.
- **False positives:** only returned IDs explicitly judged irrelevant before execution.
- **Unjudged returns:** returned IDs with neither a positive nor negative judgment. They are candidates for independent review, not automatic errors.
- **Duplicates:** repeated exact source course IDs in a result, regardless of offering or identical course names. The existing engine normally returns one offering per course.
- **Empty results:** successful zero-candidate searches, with the repository's `no_active_catalog` or `all_filtered` reason. Unknown-relevance empty results remain unjudged. Known positive expectations make an empty result an unexpected empty; an explicit `expected_empty` is checked independently.
- **Search errors:** failures categorized as catalog, embedding or database errors, never counted as successful empty searches. Arbitrary exception messages are omitted.

Similarity is the existing dense retrieval signal, not a calibrated relevance threshold. Individual elapsed times describe this run only and do not establish p50/p95 latency or service capacity. This work does not resolve the older FTS/NCS/RRF issue specification or establish final recommendation quality.

## GPU-free verification

```sh
python -m unittest discover -s tests -p 'test_catalog_evaluation.py' -v
ruff check app/catalog/evaluation.py app/catalog/evaluate.py tests/test_catalog_evaluation.py
```

The tests use synthetic repository responses and a synthetic vector behind the actual `SearchService` orchestration. They verify evaluator arithmetic, source identity, fixed snapshot/query forwarding, safe error handling, output summaries, dataset validation and no-connect help/options. These results validate the evaluation tool only; they are neither actual BGE relevance results nor CPU/GPU model verification. Actual GPU/DB execution requires the explicit command above and a separately retained live artifact. No model or database execution is required by these unit tests.
