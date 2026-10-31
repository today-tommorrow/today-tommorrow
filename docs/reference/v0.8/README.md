> 보존된 v0.8 작성 당시 기록입니다. 현재 실행·협업 기준은 [문서 안내](../../README.md)와 [최신 기준](../../planning/baseline.md)을 먼저 읽으세요. 파일 재배치 목록은 [manifest](../source-manifest.json)에 있습니다.

# 다음 개발 명세 v0.8

Google OAuth → local Gemma가 만든 선택형 질문 → 전체 입력·자료 확인 → 최종 제출 → 교육 추천.

## 이번 변경

- 첫 화면의 글쓰기 요구를 없애고 선택 카드로 시작한다. 기존 색상·배치·질문/선택지 UI를 유지한다.
- local Gemma 4 12B Q4가 앞선 답변·확인된 자료를 바탕으로 질문과 선택지를 QuestionDraft JSON으로 생성한다.
- 서버는 허용 슬롯·스키마·근거·중복을 검증하고 질문/선택지 바인딩을 저장한다. 프론트는 기존 카드에 표시한다.
- 사용자는 selected_option_id만 제출하며 서버가 연결된 답변을 원자적으로 확정한다. 미선택 후보는 사용자 사실이 아니다.
- 직접 입력과 파일·링크는 보조 경로로 유지한다. 완료 판정과 review_token은 서버가 관리한다.
- 질문은 세 개로 제한하지 않는다. 필요한 정보를 모두 처리한 후에만 교육 추천을 시작한다. 결과 뒤 추가 질문은 없다.

## 산출물

ZIP을 풀고 index.html을 열면 명세와 연결된 모든 파일을 볼 수 있다. wireframe.html은 단독 실행 가능하다.

| 파일 | 내용 |
| --- | --- |
| index.html | 19개 영역의 통합 명세 |
| wireframe.html | 선택으로 시작하고 문맥에 따라 질문·선택지가 달라지는 HTML 시안 |
| intake-question-contract.json | Gemma 입력 예시·JSON Schema·출력 예시·서버 검증 계약 |
| intake-architecture.svg / .mmd | 입력 조정·Gemma 생성·검증·사용자 선택 확정 구조 |
| intake-sequence.svg / .mmd | 첫 질문과 다음 질문 생성, 선택 확정, 버전 충돌 시퀀스 |
| recommendation-architecture.svg / .mmd | 같은 Gemma를 공유하는 추천 구조 |
| recommendation-sequence.svg / .mmd | 최종 제출 이후 추천 요청 흐름 |
| recommendation-flow.svg / .mmd | 추천 조건·검증·실패 분기 |
| data-ingestion.svg / .mmd | K-MOOC·고용24·NCS 추가 배치 적재 |
| architecture.svg | 지정 GPU 서버 한 대의 실행 구성 |
| erd.svg / erd.mmd / schema.sql | 23개 테이블·38개 관계, 참고 PostgreSQL DDL |
| openapi.yaml / openapi.json | 27개 경로·30개 연산·53개 스키마 |
| api-types.ts | API 계약 TypeScript 타입 |
| recommendation-config.json | 기존 추천 설정 + 입력 생성 정책 |
| ingestion-contract.json | 기존 세 원천 형식·추가 배치 계약 |

## API·DB 변경

POST /api/profiles/{profile_id}/questions, GET /api/questions/{question_id}, POST /api/profiles/{profile_id}/questions/{question_id}/answer를 추가했다.

Profile.next_step은 intake_state로 대체하고 selection_summaries로 구체적 선택 문구를 제공한다. Interpretation.suggested_step은 suggested_slots로 대체해 보조 입력 역할을 명확히 했다. jobs.kind에 intake_question을 추가했다. intake_questions와 intake_answers는 표시한 선택지와 적용된 답변의 관계를 보존한다.

모델 선택지 2~5개, 단일 선택, 검증 수정 1회, GPU 동시성 1은 초기 구현 제안이다. 총 질문 수 또는 측정된 성능을 뜻하지 않는다.

## 시안 확인

로그인 시연 → 하고 싶은 일이 있어요 → 데이터 분석 → 기초 경험 → 자료 없음 → 혼자 적용 → 결과물 → 저녁·주말 → 온라인 → 예산 → 시작 시기 → 전체 확인 → 추천으로 진행할 수 있다. 웹 서비스·프로젝트 경험이나 방향 탐색을 선택하면 질문 문구와 후보가 달라진다. 미정을 선택하면 같은 내용을 재질문하지 않으며 온라인은 통학 지역을 생략한다.

이 HTML은 구조화된 JSON fixture로 생성 응답을 흉내 낸다. 실제 local Gemma·Google OAuth·파일 전송/추출·URL 수집은 연결하지 않았다. 파일 선택과 URL 형식 확인만 수행하고 교육 결과는 가상 데이터다. 입력은 외부로 전송하지 않으며 새로고침하면 초기화된다. 실제 모델 생성과 통합 구현은 명세의 완료 기준을 충족해야 한다.

## 유지하는 기준

- Vite·React·TypeScript, FastAPI, 동기 SQLAlchemy 2 + psycopg, PostgreSQL 17.11, 기존 /api/health와 Alembic baseline 유지.
- 프론트·백엔드·DB·파일·Worker·Gemma는 지정 GPU 서버 한 대에서 실행한다.
- K-MOOC·고용24는 교육 후보, NCS는 학습내용 참조다. 표본 수를 전체 카탈로그 크기로 고정하지 않는다.
- 기존 S0~S9 추천 단계·검색·정렬·근거 검증·추가 배치 적재 설계를 유지한다.
- 저장소 기준 commit: 76f932ef9ca382214ee2acd6c706b0d5e92481cc. 이 개정에서 GitHub 코드를 변경하거나 배포하지 않았다.

## 검증 기록 · 2026-11-01 KST

수행한 검사:

- OpenAPI YAML/JSON 일치, 참조 307개 해석, operationId·path parameter, 기존 health 계약 보존.
- 질문 생성·조회·선택 확정 계약, 클라이언트 임의 patch 불허, 서버의 완료 상태 및 선택 요약 필드 확인.
- SQL/ERD 23개 테이블과 FK 대상 일치, SVG 8개 XML 파싱, HTML 링크·ID 검사.
- 시안 JS 구문과 선택 시작, JSON 질문 렌더링, 서로 다른 맥락의 질문·선택지, 10개 질문의 완결 경로 검사.
- 미정·온라인 분기, 잘못된 선택지·오래된 입력 차단, 자료 보존, 보조 직접 입력, 최종 제출 전 교육 비노출 검사.
- 브라우저에서 첫 선택 화면과 경험별 다음 질문, 기존 카드 UI 유지 확인.

미실행:

- 실제 local Gemma JSON 생성·의미 품질·GPU 지연/부하 평가.
- 실제 OAuth·자료 추출·PostgreSQL 적용·Alembic migration·React/FastAPI 통합.
- 공식 OpenAPI validator·TypeScript compiler·Mermaid 엔진 렌더링. 별도 생성 SVG는 XML과 화면에서 확인한다.

원본 수집 자료·사용자 파일·인증키는 이 패키지에 포함하지 않는다.
