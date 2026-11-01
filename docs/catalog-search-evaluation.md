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

## 실제 평가 기록

2026-11-01 첫 실제 BGE 실행의 [한국어 결과 보고서](evaluations/work24-bge-2026-11-01-live.md)와 [원본 JSON](evaluations/work24-bge-2026-11-01-live.json)을 보존했다. 20개 질의 성공 중 지표 대상은 사전 양성 13개다. 미판정·오탐률 한계와 재현 환경을 보고서에 함께 기록했다.

## 오프라인 관련성 검토

`app.catalog.review`는 저장된 검색 결과를 다시 검토하는 도구다. 모델·DB·네트워크·검색 엔진을 호출하지 않는다. 원래 평가셋, 실제 결과 JSON, 순위와 사전 지표를 바꾸지 않는다. 여기서 입력한 판정은 **사후 진단**이며 새로운 실검색 벤치마크나 버전 1 정답셋이 아니다. 다음 벤치마크의 기대값은 별도로 검토·버전 관리하고 실행 전에 고정해야 한다.

`backend`에서 검토 목록과 편집용 제출 파일을 만든다.

```sh
mkdir -p /tmp/work24-review
python -m app.catalog.review export \
  --result ../docs/evaluations/work24-bge-2026-11-01-live.json \
  --output-json /tmp/work24-review/packet.json \
  --output-review /tmp/work24-review/submission.json \
  --output-report /tmp/work24-review/packet.md
```

`packet.json`은 수정하지 않는다. 원본 결과 바이트 해시, 평가셋 ID·버전·해시, 프로필·데이터셋·revision과 스냅샷을 기록한다. 각 항목에는 질의 ID·문장·기준 날짜·필터·원천 과정 ID가 있다. 항목은 질의와 과정 ID로 정렬하며 순위·유사도·기존 판정·원본 자료·벡터는 제외한다. 같은 질의의 같은 과정은 회차가 여러 개여도 하나의 검토 항목이다. 실패·빈 결과는 보고서에서 별도로 보존한다. 순위 정보가 없다는 사실이 검토자의 사전 노출 여부까지 증명하지는 않는다.

`submission.json`만 편집한다. 모든 항목에 `relevant`, `irrelevant`, `unjudged` 중 하나를 명시한다. 누락·중복·알 수 없는 ID·충돌 판정은 거부하며, 누락을 부정 판정으로 간주하지 않는다. 기본값은 모두 `unjudged`다. 미판정 항목도 `reviewer`와 `reason`을 작성하고 근거가 없으면 `evidence`는 null로 둔다. 검토자에는 불필요한 개인정보 대신 안전한 식별자를 쓴다.

관련/무관 판정에는 원천 과정 ID, 근거 파일 SHA-256, 최소한의 위치 또는 발췌가 필요하다. 과정 ID는 해당 항목과 정확히 같아야 하며 앞자리 0도 보존한다.

```json
{
  "actual_source_course_id": "EXACT_SOURCE_COURSE_ID",
  "source_sha256": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS",
  "pointer": "source-units.jsonl: record COURSE_ID; field curriculum",
  "excerpt": null
}
```

위치 문자열은 최대 512자, 발췌는 500자, 판정 이유는 2,000자, 검토자 식별자는 200자다. 비슷한 주제라는 사실만으로 관련 있다고 판정하지 않는다. 원천이 불충분하거나 비용·시간·내용을 확인하지 못하면 미판정으로 남긴다. `source_sha256`은 **검토자가 기재한 근거**다. 도구는 형식과 과정 ID 일치만 확인하며 실제 파일·발췌의 진위나 재배포 권한을 확인하지 않는다. 인증정보·비공개 주소·전체 원문·커리큘럼·벡터를 넣지 않는다. 검토자가 직접 입력한 민감한 내용까지 도구가 탐지하지는 않는다.

제출 내용을 검증해 별도 판정 파일을 만들고 보고서를 생성한다.

```sh
python -m app.catalog.review import \
  --result ../docs/evaluations/work24-bge-2026-11-01-live.json \
  --packet /tmp/work24-review/packet.json \
  --review /tmp/work24-review/submission.json \
  --output-json /tmp/work24-review/adjudication.json \
  --output-report /tmp/work24-review/import.md

python -m app.catalog.review report \
  --result ../docs/evaluations/work24-bge-2026-11-01-live.json \
  --packet /tmp/work24-review/packet.json \
  --review /tmp/work24-review/submission.json \
  --adjudication /tmp/work24-review/adjudication.json \
  --output-json /tmp/work24-review/diagnostics.json \
  --output-report /tmp/work24-review/diagnostics.md
```

가져오기는 원본 결과·검토 목록·해시·항목 집합이 달라지면 거부한다. 보고서는 제출 원본도 다시 읽어 그 해시와 정규화된 모든 필드를 판정 파일과 비교한다. 가져온 뒤 판정·검토자·이유·근거를 바꾸면 실패한다. 해시는 파일 간 연결을 확인하지만 검토자의 서명이나 근거 사실의 독립 검증을 대신하지 않는다.

보고서는 전체 `(질의, 과정)` 쌍, 판정 완료, 관련, 명시적 부정, 미판정 수와 판정 비율(`판정 수 / 전체 쌍 수`)을 제공한다. 기존 k와 질의별 집계, 중복 회차, 실패도 구분한다. 같은 과정이라도 질의가 다르면 다른 판정 항목이다. 빈 결과의 판정 비율은 null이다. 미판정의 반대 집합을 오탐으로 간주하거나 전체 정밀도를 만들지 않으며, 사후 판정으로 기존 Recall/MRR을 다시 계산하지 않는다.

출력 파일은 모두 새 경로여야 하고 입력·다른 출력과 같을 수 없다. 심볼릭 링크가 같은 경로를 가리키는 경우도 거부한다. 디렉터리는 미리 만든다. 각 입력·출력은 16 MiB 이하여야 하며 모든 출력의 크기를 파일 생성 전에 확인한다. 중복 JSON 키와 비유한 숫자는 거부한다. 동일 원본의 검토 목록은 결정적으로 생성된다. I/O 실패 시 일부 파일이 남을 수 있으므로 재실행에는 새 경로를 사용한다.

```sh
python -m unittest discover -s tests -p 'test_catalog_review.py' -v
ruff check app/catalog/review.py tests/test_catalog_review.py
```

검사는 해시 불일치, 항목 누락·중복·충돌, 정확한 ID, 근거 요구, 미판정·빈 결과·실패, 중복 회차, 출력 경로와 크기, 제출 후 변조를 포함한다. 합성 판정은 실제 교육 관련성을 입증하지 않는다.

### 현재 검증 기록

PR #80의 원본 결과로 내보내기 → 미판정 제출 → 가져오기 → 보고서 생성까지 실행했다. 20개 질의의 600개 검토 항목을 처리했고, 의도적으로 모두 미판정으로 두어 판정 완료 0·미판정 600·판정 비율 0을 확인했다. 이는 작업 흐름 검사이며 새로운 관련성 판정이 아니다. 원본 결과 SHA-256 `109da31886fd783ac61ee2c0ccd31da8902dfe3427dbafbe4bda105b2d52bc5d`는 변하지 않았다. 검토 도구 테스트 14개와 기존 평가 도구 테스트 16개, Ruff 및 whitespace 검사가 통과했다.
