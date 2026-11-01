# Work24/BGE 실제 검색 평가 — 2026-11-01

첫 실행에서 20개 질의 모두 성공했다. 사전에 기대 과정을 확인한 13개 질의에서 기대집합 Recall@1은 0.923077, Recall@5는 1.0이었다. 미판정 7개와 반환 후보 대부분에 정답 라벨이 없어 전체 검색 정확도·정밀도·오탐률은 판단하지 않는다.

원본 [결과 JSON](work24-bge-2026-11-01-live.json)은 첫 실행 그대로 보존했다. 기존 [미실행 기록](work24-bge-2026-11-01.md)은 이전 시도의 이력이다.

## 평가 범위와 기준

- 고정 평가셋 1.0.0의 20개 질의: 정확한 과정명 7개, 한국어 표현·한영 용어 6개, 미판정 탐색 7개. 기대값과 근거는 검색 전에 고정했고 이번 실행에서 수정하지 않았다.
- Recall·MRR 분모는 성공한 사전 양성 13개 질의다. 각 기대집합은 확인된 일부 과정이며 전체 관련 과정 집합이 아니다.
- 오탐으로 사전 판정한 과정은 없다. 따라서 `cases_with_false_positives=0`은 정밀도 100%나 오탐 없음의 근거가 아니다.
- 실제 NVIDIA L4의 기존 BGE-M3로 질의 벡터를 만들고, 원본 저장 벡터 6,099개를 보존한 별도 PostgreSQL 카탈로그에서 기존 `SearchService`를 호출했다. 저장 벡터를 질의 벡터로 대신 사용하지 않았다.

## 지표

| k | 기대집합 Recall@k | MRR@k | 기대 과정 누락 질의 |
| --- | ---: | ---: | ---: |
| 1 | 0.923077 | 0.923077 | 1 |
| 5 | 1.000000 | 0.961538 | 0 |
| 10 | 1.000000 | 0.961538 | 0 |
| 30 | 1.000000 | 0.961538 | 0 |

오류 0건, 빈 결과 0건, 반복된 원천 과정 ID 0건이다. 중복 검사는 동일 `source_course_id` 기준이며 이름이 같은 서로 다른 원천 과정을 자동 병합하거나 중복 오류로 판정하지 않는다.

## 발견 사항과 후속 후보

1. `korean-network-paraphrase`의 기대 과정 `AIG20250000539886`은 2위였다. 1위 `ABA20263001218982`는 미판정이므로 관련 없는 과정이라고 결론 내리지 않는다. 두 과정의 실제 교과목을 별도로 검토해 다음 버전의 라벨을 정할 수 있다.
2. 천문 관측·고문서 복원 질의에도 각각 30개 결과가 반환됐다. 현재 dense 검색에는 관련성으로 응답을 보류하는 판정이 없으므로, 후속으로 범위 밖 질의 평가와 부정 라벨 확보를 검토할 수 있다. 두 주제의 코퍼스 내 부재와 반환 결과의 오탐 여부는 이번 평가에서 확정하지 않았다.
3. 넓은 목표·부정 표현·개인 비용과 시간 조건 질의는 미판정 상태다. 특히 가격·일정 정보가 알려지지 않은 결과를 무료 또는 주말 수강 가능으로 간주하면 안 된다.
4. 다음 평가는 독립적인 관련성 판정과 부정 사례를 늘리고, 별도 평가셋으로 검증해야 한다. 이번 결과를 근거로 임계값·순위·FTS/RRF/NCS를 즉시 변경하지 않는다.

## 질의별 결과

| 질의 ID | 판정 | 반환 수 | 기대 과정 첫 순위 |
| --- | --- | ---: | --- |
| exact-title-network | 사전 양성 | 30 | 1 |
| exact-title-semiconductor | 사전 양성 | 30 | 1 |
| exact-title-automation | 사전 양성 | 30 | 1 |
| exact-title-tax | 사전 양성 | 30 | 1 |
| exact-title-labor | 사전 양성 | 30 | 1 |
| exact-title-safety | 사전 양성 | 30 | 1 |
| exact-title-security | 사전 양성 | 30 | 1 |
| korean-network-paraphrase | 사전 양성 | 30 | 2 |
| korean-semiconductor-paraphrase | 사전 양성 | 30 | 1 |
| korean-labor-paraphrase | 사전 양성 | 30 | 1 |
| mixed-network-terms | 사전 양성 | 30 | 1 |
| mixed-semiconductor-terms | 사전 양성 | 30 | 1 |
| mixed-automation-terms | 사전 양성 | 30 | 1 |
| broad-career-goal | 미판정 | 30 | — |
| broad-digital-goal | 미판정 | 30 | — |
| similar-but-unreviewed-tax | 미판정 | 30 | — |
| similar-but-unreviewed-security | 미판정 | 30 | — |
| empty-result-candidate-astronomy | 미판정 | 30 | — |
| empty-result-candidate-preservation | 미판정 | 30 | — |
| unknown-personal-cost-and-timetable | 미판정 | 30 | — |

## 재현 정보

- 평가 코드: `d337455d1795a99b6aa84058d7f8894f3d8e4c3d`, 깨끗한 작업 트리.
- 평가셋 SHA-256: `597133d3c63833e7d7ab1d2e73648c0f7b2984db63726bed0f7c051ebae25ca5`.
- 결과 JSON SHA-256: `109da31886fd783ac61ee2c0ccd31da8902dfe3427dbafbe4bda105b2d52bc5d`.
- 데이터셋: `7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80`; 406개 과정·6,099개 원본 단위.
- 임베딩 프로필: `770be6a5f97f6ed60075c24f64e8d8ea91786a86e7ba22e5fe8df1e6c5477a90`.
- BGE revision: `5617a9f61b028005a4858fdac845db406aefb181`; NVIDIA L4, CUDA, FP16, CLS, 최대 512토큰, dense 1024차원, 기존 정규화 설정.
- 검색: `work24-exact-max-v1`, exact, limit 30, evidence limit 3, k=1/5/10/30, 타임아웃 30초, `as_of=2026-11-01`.
- 실행기: macOS arm64 / CPython 3.14.6 / SQLAlchemy 2.1.6 / psycopg 3.3.6. DB: 격리 PostgreSQL 16.14 / pgvector 0.6.0. 질의 임베딩만 기존 GPU 서버에서 실행했다.
- 실행 시각: `2026-11-01T03:22:42.139378+00:00` (UTC). 질의별 시간은 단일 실행 관측이며 서비스 부하·지연 SLO 검증이 아니다.

```sh
cd backend
python -m app.catalog.evaluate \
  --dataset tests/fixtures/work24-search-evaluation-v1.json \
  --profile-id "$CATALOG_PROFILE_ID" --dataset-id "$CATALOG_DATASET_ID" \
  --base-url "$BGE_BASE_URL" --timeout 30 \
  --limit 30 --evidence-limit 3 --k 1 5 10 30 \
  --output-json /tmp/work24-live.json --output-report /tmp/work24-live.md
```

`DATABASE_URL`은 원본 패키지가 적재된 격리 DB의 읽기 전용 앱 계정으로 주입한다. 출력 파일명은 새 이름을 사용한다. [평가 도구 설명](../catalog-search-evaluation.md)에 데이터 준비·검색 계약과 지표 정의가 있다.

## 검증·정리와 남은 범위

[실행 후 확인·정리 기록](work24-bge-2026-11-01-live-checks.json)에 장치 관측, DB 수량·권한, 모델 메타데이터 유지, 자원 정리 여부를 남겼다. 실행 후에도 격리 DB의 과정·단위 수와 BGE 메타데이터가 그대로임을 확인했다. 앱 계정은 SELECT 가능·INSERT 불가였다. 평가용 컨테이너·SSH 터널·임시 비밀번호/접속 파일을 정리했고 원격 DB·모델 설정은 바꾸지 않았다.

이번 작업은 실제 검색 평가 실행과 보고서다. 자료 API, 문서 분석, 추천 Worker·모델 변경, 운영 DB 마이그레이션, 검색 정책 개선은 포함하지 않는다. #25 전체 완료 여부와 구 FTS/NCS/RRF 명세 정합성은 별도 결정으로 남는다.
