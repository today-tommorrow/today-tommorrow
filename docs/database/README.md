# DB 모델과 마이그레이션

[문서 안내](../README.md) · [API 계약](../contracts/README.md) · [미결정 사항](../planning/decisions.md)

- [참고 PostgreSQL DDL](schema.sql): 23개 업무 테이블. 실제 적용된 migration이 아닙니다.
- [Mermaid ERD](erd.mmd) · [SVG ERD](erd.svg).
- 현재 업무 테이블 없는 `0001_baseline`을 유지하고 SQLAlchemy 모델 + 후속 Alembic revision으로 구현합니다. 참고 SQL을 운영 DB에 덮어쓰지 않습니다.

| 묶음 | 테이블 | 구현 책임 |
| --- | --- | --- |
| 인증 | users, auth_sessions, oauth_login_states | B API |
| 입력·자료 | intake_profiles, documents, document_chunks, profile_interpretations, intake_questions, intake_answers | B API, C 모델 계약, D 추출 |
| 실행 | jobs, recommendation_steps | B 상태·트랜잭션, D Worker 실행, C 단계 계약 |
| 카탈로그 | catalog_versions, import_batches, source_records, courses, offerings, skill_references, evidence, catalog_search_documents, course_skill_links | D 데이터 |
| 추천·관심 | recommendation_runs, recommendation_items, saved_courses | B 저장, C 추천 |

소유권·revision·자료 해시·카탈로그 버전의 일치는 FK만으로 보장되지 않는 부분까지 서비스 트랜잭션으로 검증합니다. 긴 GPU/OCR/fetch 동안 DB 트랜잭션을 열어두지 않습니다. 마이그레이션 작성은 영역별로 하되 B가 연결 순서와 병합을 조정하고, 테스트 DB 적용·저장·재조회 증거를 남깁니다.

동일 요청 재응답 보관, 오래된 추천의 API 표시, 카탈로그 갱신 후 관심 교육의 정체성, 개인 자료 삭제와 파생 스냅샷 보존은 [결정 목록](../planning/decisions.md)에서 먼저 확인합니다.
