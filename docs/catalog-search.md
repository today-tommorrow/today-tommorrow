# Work24 internal dense search

`app.catalog` supplies the read-only candidate retrieval boundary for the future recommendation service. It does not register a public search route, generate recommendations before intake submission, persist results, or change either database schema. This implements the current Work24/BGE plan; the earlier docs-branch #25 design based only on FTS, NCS and K-MOOC does not describe the loaded corpus.

## Call contract

```python
repository = CatalogRepository(engine, profile_id=expected_profile, dataset_id=expected_dataset)
service = SearchService(repository, embedding_provider)
result = service.search(query, as_of=explicit_date, filters=SearchFilters(), limit=30, evidence_limit=3)
```

The caller explicitly selects the dataset and embedding profile. `prepare()` validates their metadata in a short read-only, repeatable-read transaction and releases the connection. Then `provider.embed(query, revision=snapshot.revision)` runs outside any catalog transaction. The query is passed verbatim, including whitespace; blank queries and invalid/oversized UTF-8 input fail before model or database access. The repository rechecks the profile and full manifest fingerprint in a new read-only transaction before retrieval. A database failure, incompatible profile, changed snapshot or invalid vector is an explicit failure; none triggers another model, stored-vector substitution or empty success.

The provider interface is independent of its HTTP adapter. The concrete `app.ai.embeddings.BgeM3Embeddings` adapter is used only by the explicit probe. No GPU/NumPy dependencies are introduced into the backend.

The pinned profile must describe BAAI/bge-m3 revision `5617a9f61b028005a4858fdac845db406aefb181`, 1024 dimensions, 512-token query/passage limits, CLS pooling, FP16 inference, dense-only output, SDPA, the fast XLM-R tokenizer with right padding/truncation and special tokens, and verbatim query/document preprocessing. Postprocessing is FlagEmbedding L2, float32 conversion and L2 again with denominator `max(norm,1e-12)`. Query vectors must already be finite and normalized (norm tolerance `1e-5`); the repository only converts to pgvector's float32 storage format. Config and input-file identity hashes use the loader's canonical JSON definition: UTF-8, `ensure_ascii=False`, sorted keys, compact separators and no non-finite numbers. The full corpus profile also records its source MPS runtime; CUDA query execution does not imply bitwise equality with MPS embeddings.

Current verified package identities (configuration inputs, not automatic defaults):

- Profile: `770be6a5f97f6ed60075c24f64e8d8ea91786a86e7ba22e5fe8df1e6c5477a90`
- Dataset: `7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80`
- Snapshot: created 2026-11-01; 406 offerings, 6,099 active units, including 1,115 inferred units.

## Ranking and identity

Retrieval version `work24-exact-max-v1` computes pgvector cosine distance for **every** active unit with the requested profile and dataset. A materialized CTE separates this full computation from per-offering window ranking. It does not use an HNSW top-unit limit. The best 1–10 evidence units per `(course_id, tme)` are retained, then offering conditions are checked, then the best eligible offering per source course is selected, then the final 1–100 course limit is applied. Defaults are three evidence units and 30 courses. A course with many units cannot starve another course before aggregation. Inferred units remain eligible.

The course score is the maximum included unit cosine similarity, `1 - distance`. It is a retrieval signal, can be negative, and is neither a probability nor a calibrated recommendation quality threshold. Unit ties use `unit_key`; offering ties use lexicographic source `tme`; final ties use source `course_id`, then `tme`. No reranker, sparse/RRF component or ANN mode is claimed by this slice.

Identity version 1 uses UUIDv5 namespace `a4b29d71-4f3e-50ab-9e95-dfba02578443`. The UUID name is compact, non-ASCII-escaped JSON: `["work24", source_course_id]` for courses, `["work24", source_course_id, source_tme]` for offerings. Both keys are the exact PostgreSQL TEXT values: no integer conversion, trimming, case folding or Unicode normalization. Therefore `"1"` and `"01"` are distinct offerings, consistent with the source composite primary key. Course names are never identifiers. The namespace and serialization must remain stable when a future `today_app` mapping table/public course API is introduced; this change creates neither.

## Conditions and provenance

| Input | Current behavior |
| --- | --- |
| delivery_mode | Known `온라인` maps to online and `서울 집체` to offline. Known mismatch, including a requested hybrid mode, fails. Unrecognized/missing source values remain unknown. `any` has no restriction. |
| start_from/start_to | Inclusive explicit calendar bounds on listed `start_date`. Missing/invalid dates remain unknown. No conversion from `soon`, no automatic exclusion based on today's date. |
| listed_region | Exact equality against the source region label; missing labels remain unknown. This is not commute or attendance feasibility and can be an institution address even for online training. |
| budget_krw | A bound remains unknown. `training_fee` and conditional `displayed_self_pay` are preserved, never treated as confirmed personal cost or free enrollment. |
| availability | Specific requested times remain unknown because total training hours are not a timetable; flexible has no restriction. |
| field_code | Explicitly reported in `unsupported_conditions`, with unknown condition status. Raw Korean field labels have no reviewed mapping to application field codes. |

Only explicit `fail` conditions exclude an offering; unknown does not mean pass. `as_of` records caller context and does not transform snapshot recruitment status into live availability. `snapshot_created_on` is the package date. Per-course `checked_at` remains null because the package has no trustworthy source verification timestamp. Public evidence DTOs that currently require a string timestamp need a separate reviewed mapping; do not fill it with package creation, DB loading or query time.

Frozen result objects retain raw course metadata, including nullable dates, prices and objective; unit evidence retains text/hash, NCS unit code, nullable source/hours, provenance, `is_inferred` and `is_noise`. The loader and source schema enforce `is_noise=false`. A noise-flag violation fails explicitly rather than silently filtering it or dropping inferred units. Source tables are read-only and all query values are bound parameters. `eligible_courses` counts all distinct courses after conditions, before limit. `excluded_counts` counts failing offering conditions (one offering can contribute multiple reasons). An empty result distinguishes `no_active_catalog` from `all_filtered`.

## Explicit operator probe

After configuring the existing `DATABASE_URL` for the application role and an actual compatible BGE service:

```sh
python -m app.catalog.probe \
  --query "$SEARCH_QUERY" \
  --profile-id "$CATALOG_PROFILE_ID" \
  --dataset-id "$CATALOG_DATASET_ID" \
  --as-of 2026-11-01 \
  --base-url "$BGE_BASE_URL" \
  --timeout 30
```

The probe has no automatic demonstration query or saved-vector fallback. It prints summary JSON without the query text or candidate evidence by default; `--include-candidates` explicitly includes public source metadata/evidence. Optional filter flags mirror `SearchFilters`, including `--availability`. Output records exact mode, snapshot/profile identity, aggregation version, empty state and elapsed time. Errors use safe codes. Running `--help` does not connect to the model or database.

## Verification and remaining integration

Run `python -m unittest discover -s tests -p 'test_catalog_search.py' -v` from `backend`. Pure tests cover aggregation before limit, multiple offerings, ties/negative scores, UUID boundaries, nullable cost, inferred provenance, unknown conditions, explicit dates, incompatible profile/tokenizer settings, identity mismatch and verbatim model inputs. With the existing guarded disposable local database variables and `L0_DATABASE_TESTS=1`, additional tests compare all-course exact ordering with an independent SQL aggregate, bound evidence and inspect real connection/read-only transaction boundaries. These checks use real stored corpus vectors as a retrieval oracle, not as a live query-generation substitute.

On 2026-11-01, the combined change at `e25504c2316f3691573e1ad39f0828bfa259cf4e` passed Ruff and all **164 backend tests with no skips**, using an isolated PostgreSQL 16.14/pgvector 0.6.0 database. This includes 18 catalog tests and 13 HTTP embedding tests. The four existing catalog integration tests also passed, including source write-permission protection and reimport verification. The application dependencies remain locked; no NumPy dependency was added to the backend.

The same code was then exercised on the existing remote NVIDIA L4 BGE-M3 service, with the pinned revision above and explicit `max_length=512`. Each request passed through `SearchService`, live HTTP embedding, and read-only PostgreSQL retrieval. The 406 source offerings and 6,099 units were preserved.

| Synthetic query | Returned distinct courses | Complete call time |
| --- | --- | --- |
| 파이썬을 활용한 데이터 분석 기초 학습 | 10 | 0.242 s |
| 클라우드 서버 운영과 리눅스 네트워크 | 10 | 0.217 s |
| 반도체 공정 기초 | 10 | 0.211 s |

A separate float64 NumPy calculation evaluated every pinned source vector using the exact query vector for each run. The three live-GPU results, a stored-query top-30 result, and its online-only top-10 result all matched the course-level top-K boundary within tolerance `1e-5`; maximum observed cosine error was `3.033e-7`. Raw course metadata and returned evidence text/hash/source/inferred flags/provenance were unchanged. This comparison checks retrieval correctness, not MPS/CUDA embedding equivalence or recommendation relevance.

Remote evidence: `/home1/ncloud/llm-bench-work/results/work24-search-20261101-e25504c/actual-gpu-search.json`. The full local verification record and independent oracle are retained under `.outline/search-verification` in the working workspace. The three timings are single, unloaded observations, not p50/p95 or a load/performance SLO. Existing model services were reused; no application API/Worker service or database migration was deployed by this check.

Next integration work is the submitted-intake-to-query mapping, recommendation scoring and explanations, durable `today_app` course/result mappings, and public course/result APIs. No final recommendation quality claim or public endpoint is part of this internal slice.
