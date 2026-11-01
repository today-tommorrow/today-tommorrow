# Document-free recommendation processing

The backend recommendation runner implements the L4 processing boundary after final input submission. The lifecycle owns authentication, review tokens, idempotency, worker leases and atomic result persistence. The runner owns frozen catalog/configuration validation, live embeddings, evidence-bound assessment and result construction. It never publishes partial candidates or writes the source catalog.

## Runtime configuration

The production factory `app.recommendations.settings.build_runner(engine)` requires all of:

```text
RECOMMENDATION_MODEL_BASE_URL=http://127.0.0.1:8080
RECOMMENDATION_MODEL_ID=gemma4-checklist
RECOMMENDATION_MODEL_REVISION=29d097773436b69ff9feafd636ab4cf873786537
RECOMMENDATION_EMBEDDING_BASE_URL=http://127.0.0.1:8081
CATALOG_PROFILE_ID=770be6a5f97f6ed60075c24f64e8d8ea91786a86e7ba22e5fe8df1e6c5477a90
CATALOG_DATASET_ID=7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80
```

The URLs describe services reachable from the running worker. A local SSH tunnel requires its local forwarded URLs instead. `DATABASE_URL` remains the existing SQLAlchemy application connection. The configured model revision is operator-supplied provenance, not independent proof of the weights served by an HTTP alias.

Optional limits: `RECOMMENDATION_MODEL_TIMEOUT_SECONDS` (60), `RECOMMENDATION_EMBEDDING_TIMEOUT_SECONDS` (30), `RECOMMENDATION_MODEL_MAX_TOKENS` (2048), `RECOMMENDATION_MODEL_MAX_RESPONSE_BYTES` (131072). Socket timeouts bound individual HTTP operations, not a whole-job wall-clock deadline. Responses and request context have explicit size limits. The adapter conservatively reserves UTF-8 prompt bytes, output tokens and 256 template-overhead tokens within the known 8192-token context; oversized confirmed input fails explicitly instead of being silently shortened. Candidate evidence excerpts are at most 160 characters, and full source text remains in internal provenance.

Configuration validation and `capture_context()` do not call either model. They capture the active catalog manifest and model/configuration identity. The runner validates and retrieves that retained version even if an operator [activates a later batch](catalog-batches.md) before execution. It checks model/configuration identity and the returned catalog snapshot again; modified or unavailable historical snapshots fail explicitly. Public result retrieval continues returning the stored result independently of the active pointer.

## Processing and grounding

The supported input has `document_decision=none` and no reviewed material items. Materials are rejected explicitly until their separate implementation is connected. Confirmed goal text, relevant selected wording and server-defined field/goal labels form the query; the model does not rewrite it. BGE-M3 uses the existing revision-pinned 512-token dense provider.

The runner retrieves at most ten distinct course candidates, preserving exact search ranking and metadata. Empty retrieval skips all recommendation-model calls. Otherwise, the model selects learning needs as exact substrings of confirmed goal sources, assesses one candidate at a time, and selects exact source quotations for explanations. NCS identifiers are never invented; absent external mappings remain unmapped.

Assessments preserve `0`, `0.5`, `1` and `null`. Goal, prerequisite and outcome use weights `0.45`, `0.25`, `0.20`; the server computes the `0.10` preference axis only from a confirmed delivery-mode match. Prerequisite assessment remains unknown without both explicit prerequisite evidence and user-described experience. Lower/upper bounds and coverage use the frozen weights: `1/null/0.5/1` yields `0.65/0.90/0.75`. These are internal ordering values, not ability or employment probabilities.

The server excludes unrelated courses and preserves pass, fail, unknown and not-applicable states across the seven public condition checks. It orders by grounded goal basis, score lower bound, coverage, unknown-condition count, dense retrieval similarity and stable IDs, then applies a maximum of five courses and two per known institution. Unknown institutions are not assumed identical. Caps are never relaxed. Unknown personal cost, schedule, recruitment and eligibility are not converted into confirmed matches; institution regions are not treated as verified commuting locations.

Model JSON has a strict schema. Duplicate keys, non-finite values, foreign IDs, duplicate candidates, nonexistent quotations, unsupported non-null assessments and ungrounded content claims fail validation. A validation failure permits one bounded repair attempt; transport errors do not. The phase callback checks worker ownership/cancellation before and immediately after each blocking call. The remote GPU operation itself is not cancelled by a Python callback.

Explanations contain only model-selected goal/source IDs and exact quotations. The server composes a restricted sentence connecting them; the model cannot add free-form dates, prices or assertions that unknown conditions are satisfied. Inferred evidence is marked `unknown` and explicitly described as inferred reference material. Exact quote/ID checks do not independently prove that the semantic relationship is useful; real-model evaluation remains necessary.

## Saved result boundary

`RecommendationRunner.run(snapshot, report_phase)` returns the public `RecommendationRun` fields except `id`, `profile_id`, `profile_revision`, `profile_snapshot` and `created_at`, which belong to the lifecycle. `_snapshot` is reserved internal provenance: the original search query, source identifiers, full candidate metadata and evidence, assessments, model/configuration identity, and selected explanation quotations. It must be stored separately and excluded from the public response.

`validate_result` validates injected runners as well as production output. `sample` providers stay labelled `sample`; the production factory has no fixture fallback. A zero-candidate result has `model_outcome=not_needed`. When the model assessed candidates but excluded all as unrelated, the `empty` result truthfully records `model_outcome=succeeded`. A bounded candidate set cannot prove an entire field is unsupported; `unsupported_field` requires reviewed coverage information that this corpus does not supply. Unavailable models or invalid output stay failures and never become successful recommendation cards.

Source evidence `checked_at` is nullable. Snapshot creation and query dates do not establish source verification dates. The frontend accepts null and displays `미확인`. Public catalog UUIDs are deterministic over the pinned dataset, profile and manifest; original 64-character dataset/profile hashes remain internal provenance.

## Verification scope

Focused tests run from `backend`:

```sh
python -m unittest discover -s tests -p 'test_recommendation*.py' -v
ruff check app/recommendations tests/test_recommendation_pipeline.py tests/test_recommendation_model.py
```

Pipeline tests inject explicitly synthetic providers. HTTP adapter tests use a loopback test server; they verify transport contracts, not actual GPU inference. Their results do not satisfy issue #26's final web-to-GPU acceptance criterion or its material-input path. Actual model quality, full application integration and durable lifecycle checks must be reported separately.

### Integrated verification, 2026-11-01

At executable commit `042fa11`, `./check` passed on retry: 253 frontend tests, 206 backend tests (one skipped by configuration), 22 launcher tests, 11 catalog unit tests, four catalog integration tests, formatting/lint/type checks, and the web build. The first final regression run failed the worker-restart test with a job still running on attempt one. Ten instrumented targeted reruns passed; the cause remains unconfirmed, so the first failure is retained as a known intermittent test risk.

The final live run used prompt `work24-recommendation-grounded-4`, actual remote BGE-M3 and Gemma over an SSH tunnel, and the pinned corpus in an isolated PostgreSQL 16.14/pgvector 0.6.0 database. A synthetic authenticated service session exercised the real FastAPI routes through TestClient: profile save, reviewed submission, identical-key replay, worker processing, job polling, and two saved-result reads. The replay returned the original accepted job, and both result reads were identical. Four courses were saved with `runtime_mode=real_model`, `model_outcome=succeeded`, and `result_state=unknown_conditions`; unknown conditions were preserved. One complete run took 218.486 seconds. This is a single integration observation, not a latency percentile or a recommendation-quality benchmark.

Live testing exposed zero scores without evidence, contradictory relevance/score combinations, and explanations selecting evidence outside the assessed subset. The output schema now constrains those score combinations, and explanation input contains only assessed evidence. Exact ID/quotation validation remains in place; invalid output still fails after the bounded repair attempt.

Evidence is retained outside Git under `.outline/recommendation-verification` in the parent working workspace, including `live-flow.json`, per-call synthetic/public model traces, and failed-case replay records. Test users and their results were removed after verification. No remote application migration or service deployment was performed. Browser Google login, the complete browser-to-GPU journey, material-input recommendations, broader relevance evaluation, and latency improvement remain separate work; issue #26 is not complete on this evidence alone.

The existing remote device was an NVIDIA L4. BGE used `BAAI/bge-m3` revision `5617a9f61b028005a4858fdac845db406aefb181`, 512-token normalized CLS embeddings. Gemma used alias `gemma4-checklist`, configured for `google/gemma-4-12B-it-qat-q4_0-gguf` revision `29d097773436b69ff9feafd636ab4cf873786537`, llama.cpp `b11960`, an 8192-token context and 2048-token output limit. The configured weight revision records provenance; the run did not independently checksum served weights.

### Browser submission and reload, 2026-11-01

At baseline `b558748`, a real browser submitted a complete, HTTP-seeded synthetic profile through the real API against a dedicated catalog database and actual remote L4 models. A synthetic identity and service session were issued with `AuthStore`; Google OAuth and browser question-based input acquisition were not exercised. The first attempt failed in `assessing` after 171.538 seconds with `recommendation_failed` and no stored result; its exact cause remains unconfirmed. One instrumented diagnostic retry succeeded with `runtime_mode=real_model`, model `gemma4-checklist`, `model_calls_performed=true`, and a pipeline duration of 184829 ms. It saved two `unknown_conditions` items. The browser navigated automatically to the stored result, displayed both items, and retained the same items, title and time after an actual reload. Two API reads and the DB result also matched.

This verifies the bounded browser submission → real GPU → persisted result → browser reload path on that retry. It does not establish general reliability, recommendation quality, percentile latency, document-inclusive processing, or whole-issue completion. Reopening the profile in a replacement tab during the first job exposed a separate recovery gap: the tab-local job ID was lost while the authenticated server job continued. Sanitized observations and screenshots remain outside Git in the parent workspace's `.outline/issue26-browser-verification`; private configuration and session artifacts must not be published.

The recommendation worker now logs a fixed `recommendation_failed` event with the job ID, last accepted processing phase and an allowlisted failure category. Known model/validation codes remain distinguishable; unknown exceptions use a fixed fallback. It logs no exception message, model payload, prompt or traceback, and the public job error remains `recommendation_failed`. These diagnostics improve future failure investigation; they do not retrospectively identify the first attempt's cause or change validation and retry behavior.

After the recovery change, a new browser tab opened only the existing profile URL, without a tab-local job ID. The owner- and revision-scoped lookup automatically recovered the previously saved real-GPU run and displayed the same two results without another submission or model call. This checks the completed-job recovery path with the actual API and browser; queued/running recovery, failed lookup, cross-user isolation and revision races are covered by automated tests. The model worker and tunnel were stopped during this read-only recovery check. `fixed-new-tab-recovery.jpg` records the result outside Git beside the earlier evidence.
