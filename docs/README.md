# 개발 문서 안내

처음 참여한 사람과 에이전트는 이 페이지에서 시작합니다. 제품은 **Google 로그인 → 선택형 맞춤 질문 → 자료 확인 → 전체 확인·제출 → 근거 있는 교육 추천**입니다.

## 읽는 순서

1. [팀 작업 규칙](../AGENTS.md), [현재 실행·협업 규칙](../README.md), [개발 가이드](development.md).
2. [최신 코드와 명세의 차이](planning/baseline.md), [제품 흐름](product/README.md).
3. 담당 이슈의 [Phase·소유 영역](planning/phases.md)과 [이슈 목록](planning/issues.md).
4. 해당 영역 명세와 [API 계약](contracts/README.md), [결정할 사항](planning/decisions.md).
5. 수정 전 선행 이슈와 겹치는 파일 담당을 확인하고, 완료 전 [검증 기준](validation/README.md)을 확인합니다.

## 영역별 탐색

| 영역 | 시작 문서 | 주요 계약·산출물 |
| --- | --- | --- |
| 제품·웹 | [제품 흐름](product/README.md) | [HTML 시안](product/wireframe.html), 질문 카드·자료·결과 흐름 |
| 입력·AI 질문 | [입력 명세](intake/README.md) | [QuestionDraft JSON](intake/intake-question-contract.json), 구조·시퀀스 도면 |
| 추천·AI 평가 | [추천 S0–S9](recommendation/README.md) | [설정](recommendation/recommendation-config.json), 검색·조건·정렬·근거 검증 |
| 원천 데이터 | [적재 명세](data/README.md) | [manifest·adapter 계약](data/ingestion-contract.json), 추가 배치 도면 |
| HTTP·타입 | [API 안내](contracts/README.md) | [YAML](contracts/openapi.yaml), [JSON](contracts/openapi.json), [TypeScript](contracts/api-types.ts) |
| DB | [DB 안내](database/README.md) | [참고 DDL](database/schema.sql), [ERD](database/erd.mmd) |
| 인증·자료·운영 | [운영 요구사항](operations/README.md) | OAuth, 파일·URL, Worker·GPU, 단일 서버 구성 |
| 검증 | [검증 기준](validation/README.md) | 실제·목·GPU 구분, 미실행 항목 |
| 작업 분배 | [Phase](planning/phases.md) | [미결정 사항](planning/decisions.md), [이슈 목록](planning/issues.md) |
| 원문·출처 | [v0.8 원문 안내](reference/v0.8/README.md) | [통합 HTML](reference/v0.8/index.html), [파일 이동·해시](reference/source-manifest.json) |

## 기준과 변경 방법

- 설치·협업은 최신 저장소 README/AGENTS/development를 따릅니다. 과거 v0.8의 저장소 설명이 최신 코드를 덮어쓰지 않습니다.
- 제품 요구사항은 영역 문서, 필드·타입·HTTP 상태는 OpenAPI, 모델 입력·출력은 해당 JSON 계약을 함께 확인합니다. 서로 다르면 임의로 선택하지 말고 결정 목록과 관련 이슈에 변경 전후 예시를 기록합니다.
- 계약 변경 시 YAML/JSON/TypeScript와 관련 영역 문서·샘플을 같은 PR에서 갱신합니다. TypeScript 타입만으로 JSON Schema의 const·범위·길이 검증을 대신하지 않습니다.
- reference/v0.8은 작성 당시 기록입니다. 새 요구사항은 영역 문서에 반영하고 원문을 현재 구현 상태처럼 수정하지 않습니다.
- `.docs.local`은 개인 원본 보관용입니다. 공유 개발·이슈에서는 `docs`를 참조합니다. 원본 자료·개인 파일·인증키는 이 문서 분류에 포함하지 않습니다.
- CI는 필수 도입합니다. 자동 배포(CD)와 자동 승인 워크플로는 만들지 않습니다. 리뷰어 후보는 xifoxy-ru·zxcv718이며 작성자를 제외하고 사람 1명 이상 승인 후 Squash and merge합니다.
