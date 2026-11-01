# 관심 교육 API와 카탈로그 교체 정책

## 범위와 실행

로그인한 계정의 관심 교육 저장·목록·해제를 구현한다. 원본 `work24_bge`는 읽기만 하고, 관심 정보는 `today_app.saved_courses`에 저장한다. 모델과 GPU는 사용하지 않는다. 자료 없는 실제 교육 패키지와 PostgreSQL로 검증할 수 있다.

`./setup`으로 원본 패키지와 개발 DB를 준비하고 Alembic `0007_saved_courses`까지 적용한다. 기존 DB는 migration 계정으로 `uv run --locked alembic upgrade head`를 backend에서 실행한다. 새 migration은 main의 `0006_answer_history_index` 다음이다. 별도 개발 브랜치의 recommendation migration과 합칠 때는 Alembic의 복수 head를 명시적으로 병합해야 하며, 이 PR에 추천 기능을 포함하지 않는다.

## 계약

- `PUT /api/me/saved-courses/{course_id}` → 204. 최초 저장 시 서버가 현재 교육의 출처 ID·공개 목록 정보·카탈로그 버전·저장 시각을 고정한다. 동일 계정/과정의 반복·동시 저장은 중복 행을 만들거나 당시 snapshot/시각을 갱신하지 않는다. 현재 존재하지 않는 새 과정은 404다.
- `DELETE /api/me/saved-courses/{course_id}` → 204. 해당 계정에서만 해제하며, 이미 해제되었거나 다른 계정에만 있는 과정도 204다. 카탈로그에서 사라진 과정 역시 해제할 수 있다.
- `GET /api/me/saved-courses?limit=20&cursor=...` → `CoursePage`. limit은 1–100이다. 새 저장 순서로 조회하며 같은 시각이면 공개 과정 UUID 내림차순이다. 목록은 사용자 ID를 요청 인자로 받지 않는다.
- 읽기/쓰기 모두 인증이 필요하다(401). 쓰기는 세션 CSRF와 전달된 Origin을 검증한다(403). 잘못된 UUID/limit은 422, 위조·타 계정 cursor는 400, cursor의 카탈로그가 바뀌면 409, 원본 카탈로그가 모호하거나 DB 오류가 발생하면 503이다. 개인 응답은 `Cache-Control: no-store`다.

성공 목록의 형태:

```json
{
  "items": [{
    "id": "ca75d392-5c68-5ff0-a341-058e57d09a7c",
    "name": "합성 교육 예시",
    "provider_name": null,
    "field_code": null,
    "source_system": "work24",
    "course_type": "vocational_training",
    "evidence_level": "listing_only",
    "detail_url": null,
    "is_demo": false,
    "saved_catalog_version_id": "b6316a03-cf30-43a5-b4df-08c9bb235dab",
    "catalog_status": "current"
  }],
  "next_cursor": null,
  "catalog_version_id": "b6316a03-cf30-43a5-b4df-08c9bb235dab"
}
```

위 JSON의 이름과 UUID는 계약 설명용 합성 값이다. 실제 서비스는 원본 교육만 저장하며 합성 교육을 주입하지 않는다. 빈 목록은 같은 필드를 가진 `items: []`, `next_cursor: null`이다. 오류는 `{code,message,request_id}` 형태이며 DB 원문/비밀번호를 노출하지 않는다.

기존 웹의 `{items}`만 소비하던 계약을 `items,next_cursor,catalog_version_id`로 명확히 했다. 각 관심 항목에는 `saved_catalog_version_id`와 `catalog_status`가 필수다. 페이지 이동 중 409를 받으면 화면에 갱신 사실을 알리고 첫 페이지부터 다시 조회한다.

## D10 — 최신 정보 우선 (사용자 결정, 2026-11-01)

- 정확히 같은 Work24 원본 과정 ID가 현재 카탈로그에도 있으면 최신 목록 정보를 보여준다. 다른 버전이거나 목록 정보가 바뀌면 `changed`다. 이름 유사도·기관명으로 다른 과정에 자동 연결하지 않는다.
- 현재 카탈로그에서 사라졌으면 최초 저장 snapshot을 유지하고 `unavailable`을 반환한다. 화면에는 **현재 제공되지 않음**을 표시하고 최신 상세로 이동시키지 않는다. 과거 정보가 현재 모집·비용·일정임을 의미하지 않는다.
- 같은 버전/같은 목록 정보이면 `current`다. `saved_catalog_version_id`는 재저장으로 변경하지 않는다. 해제 후 새로 저장한 경우에만 새 snapshot을 만든다.
- 원본 과정 ID는 TEXT 그대로 UUIDv5로 변환한다. namespace `a4b29d71-4f3e-50ab-9e95-dfba02578443`, 이름은 공백 없는 JSON `["work24", source_course_id]`다. 앞자리 0·대소문자·공백을 정규화하지 않는다. 여러 회차는 과정 단위로 한 번 저장하며 목록 대표 정보는 원본 회차 TEXT 오름차순으로 선택한다. 날짜나 숫자 변환을 통한 최신 회차 추정은 하지 않는다.
- 카탈로그 버전은 dataset/profile/manifest hash의 결정적 UUID다. 원본은 한 번의 read-only repeatable-read 트랜잭션에서 읽는다. 여러 활성 dataset/profile이 섞이면 어느 것이 최신인지 추측하지 않고 503으로 실패한다.
- cursor는 계정·카탈로그 버전·현재 목록 fingerprint·마지막 저장 시각/UUID를 HMAC으로 묶는다. 카탈로그 교체 또는 활성 목록의 같은 버전 수정도 409를 반환해 페이지 혼합을 막는다. 다음 페이지 도중 새로 저장한 앞쪽 항목은 처음부터 새로 조회하면 나타난다. 기존 항목 해제는 이후 페이지에 중복을 만들지 않는다.

현재 원본에는 신뢰할 수 있는 분야 코드 대응표가 없어 `field_code`는 null이다. 화면의 출처 URL은 http/https이며 사용자정보가 없는 경우만 전달한다. 알려지지 않은 교육기관은 null, 원문 검증 수준은 보수적으로 `listing_only`다.

## 검증

격리된 개발 DB 환경 변수를 준비한 뒤 backend에서:

```sh
L0_DATABASE_TESTS=1 uv run --locked python -m unittest discover -s tests -p 'test_saved_courses.py' -v
uv run --locked ruff check . ../scripts ../tools/catalog
```

12개 집중 검사가 실제 PostgreSQL 16.14/pgvector 0.6.0과 고정 원본 406개 과정/6,099개 단위에서 통과했다. 합성 인증 세션으로 실제 인증·CSRF를 거쳐 저장/재조회/해제를 검사했다. 동시 PUT, 원본 ID 보존, 다른 계정 분리, 누락/변조 cursor, 동점 keyset, 페이지 중 저장/해제, 카탈로그 교체와 사라진 과정 snapshot, 원본 서비스 실패도 포함한다. 교체 검사는 테스트 관리자만 원본을 잠시 변경하고 복구한다; 서비스 계정에는 원본 쓰기 권한이 없다.

최종 PR에는 통합 `./check`와 실제 웹/API 연결 검증 결과를 별도로 기록한다. 이 집중 검사만으로 웹 전체 동작을 검증했다고 표시하지 않는다. GPU 검증은 N/A다.
