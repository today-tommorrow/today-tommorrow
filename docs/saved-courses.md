# 관심 교육 API와 카탈로그 교체 정책

## 범위와 실행

로그인한 계정의 관심 교육 저장·목록·해제를 구현한다. 원본 `work24_bge`는 읽기만 하고, 관심 정보는 `today_app.saved_courses`에 저장한다. 모델과 GPU는 사용하지 않는다. 자료 없는 실제 교육 패키지와 PostgreSQL로 검증할 수 있다.

`./setup`으로 원본 패키지와 개발 DB를 준비하고 Alembic `0008_merge_saved_recommendations`까지 적용한다. 기존 DB는 migration 계정의 `MIGRATION_DATABASE_URL`을 설정한 뒤 backend에서 `uv run --locked alembic upgrade head`를 실행한다. `0007_saved_courses`와 main의 `0007_recommendation_runs`는 모두 `0006_answer_history_index`에서 분기하며, `0008_merge_saved_recommendations`가 두 경로를 스키마 변경 없이 병합한다.

이미 `0007_saved_courses`를 적용한 DB에서는 같은 명령이 추천 migration만 추가 적용하고 기존 관심 교육을 보존한다. `0007_recommendation_runs`를 적용한 DB에서는 관심 교육 migration을 추가 적용한다. 새 DB에서는 두 migration을 모두 적용한다. 기존 revision을 수정하거나 DB를 다시 만들거나 `stamp`로 건너뛰지 않는다. 적용 후 `uv run --locked alembic current`와 `uv run --locked alembic heads`가 모두 단일 head `0008_merge_saved_recommendations`를 가리키는지 확인한다.

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

위 JSON의 이름과 UUID는 계약 설명용 합성 값이다. 실제 서비스는 원본 교육만 저장하며 합성 교육을 주입하지 않는다. 빈 목록은 같은 필드를 가진 `items: []`, `next_cursor: null`이다. 인증·업무 오류는 `{code,message,request_id}` 형태이며 DB 원문/비밀번호를 노출하지 않는다. UUID와 limit의 형식 검증 실패는 FastAPI의 422 `detail` 응답을 사용한다.

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

2026-11-01 통합 `./check`가 통과했다. 웹 250개, backend 145개 실행(명시적 CI 전용 재적재 검사 1개 제외), 실행 도구 22개, catalog 단위 11개, catalog 통합 4개를 검사했다. TypeScript·ESLint·Prettier·Ruff·웹 build도 통과했다. 제외된 `DatabaseBoundaryTests.test_reimport_keeps_application_rows_and_education_keys`는 표준 `./check`가 `L0_REIMPORT_TESTS=0`으로 지정하는 검사다.

이후 main `b43249d`를 합치면서 `App.flow`의 관심 목록 fixture를 `SavedCoursePage` 계약에 맞췄다. `App.flow`, 관심 교육 기본·페이지 이동, 교육 기본·카탈로그, 추천 제출 UI 검사가 통과했고 TypeScript, fixture ESLint·Prettier, backend 설정을 사용한 Ruff도 통과했다. Alembic의 단일 head와 병합 revision의 스키마 변경 부재, 두 기능의 모델 metadata·API 경로 등록을 확인했다.

병합 commit `4c43be4`를 새 격리 PostgreSQL에서 다시 검증했다. 빈 DB와 각 공개 `0007` revision에서 `upgrade head`가 모두 성공했다. 관심 교육 경로에서는 사용자·세션·저장 snapshot과 API 목록을, 추천 경로에서는 사용자·세션·입력·작업·결과 snapshot을 전후 비교해 보존을 확인했다. 모든 migration 경로 전후 원본 교육·단위 테이블의 내용 fingerprint가 동일했다.

같은 commit의 `./check`가 웹 267개, backend 218개(표준 재적재 검사 1개 제외), 실행 도구 22개, catalog 단위 11개·통합 4개와 전체 lint·typecheck·build를 통과했다. 별도로 환경 변수를 설정한 backend 전체 `L0_DATABASE_TESTS=1 L0_REIMPORT_TESTS=1 uv run --locked python -m unittest discover -s tests -v`도 218개 모두 제외 없이 통과했다. `./dev`의 실제 API·웹 응답과 아래 React 실제 HTTP 검사를 다시 확인했고, 합성 계정·세션 파일과 검증용 서버·DB는 검사 후 정리했다. 추천 검사의 provider는 합성 주입이며 실제 모델·GPU 검증을 의미하지 않는다.

별도 실제 uvicorn HTTP 요청 47개로 반복·동시 PUT, DELETE, 계정 격리, 인증·CSRF, 오류 상태, keyset 페이지, 카탈로그 교체와 snapshot 복구를 확인했다. 검사 후 원본 카탈로그 버전과 fingerprint를 복구했다. 실행 환경은 Python 3.14.6, Node 26.5.0, npm 11.17.0, uv 0.12.5, PostgreSQL 16.14, pgvector 0.6.0이다.

원본 패키지는 commit `043f8b8e96620994aff06b0edc2d235f793f0b5a`, ZIP SHA256 `c6cd4618bc4b9fcca925161ee09ed683faf77f8ebf45cfa5831bb6bffdbaeb8e`다. Dataset은 `7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80`, embedding profile은 `770be6a5f97f6ed60075c24f64e8d8ea91786a86e7ba22e5fe8df1e6c5477a90`으로 고정했다.

### Concurrent-save regression (2026-11-01, #29)

On main `d68a949`, concurrent first saves could return 503: the insert handled
conflicts on `(user_id, course_id)` while `uq_saved_source_owner` independently
enforced `(user_id, source_course_id)`. A preceding lookup does not serialize
concurrent requests. The insert now uses `ON CONFLICT DO NOTHING` without a target
so PostgreSQL arbitrates both existing identity constraints. It keeps the winning
row unchanged. Foreign-key errors still propagate; this is not a general SQL-error
catch. See the [PostgreSQL INSERT contract](https://www.postgresql.org/docs/16/sql-insert.html).

Reproduce after preparing the same isolated PostgreSQL database and pinned archive
described above, then run from `backend`:

```sh
L0_DATABASE_TESTS=1 PYTHONPATH=.:tests uv run --locked python -m unittest test_saved_courses.SavedDatabaseTests.test_parallel_put_is_idempotent
L0_DATABASE_TESTS=1 uv run --locked python -m unittest discover -s tests -p 'test_saved_courses.py'
```

The concurrency test runs four authenticated TestClients per course across five
public course IDs. An SQLAlchemy execution barrier aligns inserts after every
request has observed an absent save; PostgreSQL executes the production SQL against
the unchanged schema. Before the fix, every course group failed the all-204
assertion, and PostgreSQL logged `duplicate key value violates unique constraint
"uq_saved_source_owner"`. After the fix, all 20 concurrent PUTs returned 204 with
one row per account/course. Replays preserved `created_at`,
`saved_catalog_version_id`, and `saved_snapshot`.

All 13 saved-course tests passed on Python 3.14.6, PostgreSQL 16.14 and pgvector
0.6.0. They also cover account isolation, catalog-switch snapshot retention,
authentication/CSRF, cursor validation and deletion. A real missing-owner insert
still raises foreign-key SQLSTATE `23503`. No schema or API contract changed;
model/GPU execution is N/A.

The full local `./check` passed: 276 web tests; 263 backend tests with the standard
reimport check skipped; 22 launcher tests; 30 catalog unit tests; and 11 catalog
integration tests with the separately enabled records-database check skipped.
TypeScript, ESLint, Prettier, Ruff, pinned-catalog verification and the production
web build passed. The owned disposable database and volume were removed afterward.
GitHub Actions and the scheduler remained disabled.

### React와 실제 HTTP 연결 재현

`./setup`과 migration을 마치고 `./dev`로 API를 실행한다. `.env`의 `PUBLIC_ORIGIN`은 기존 개발 계약인 `http://127.0.0.1:5173`을 사용하고, `API_TARGET`은 실행 중인 API 주소와 일치시킨다. 별도 터미널에서 저장소 루트 기준으로 실행한다. 아래 임시 파일이 이미 있으면 다른 새 경로를 사용한다.

```sh
backend/.venv/bin/python scripts/seed-synthetic-saved-session.py --root "$PWD" --output /tmp/saved-ui-session.json
cd web
TEST_SESSION_FILE=/tmp/saved-ui-session.json npx vitest run --config integration/saved-courses.config.ts
cd ..
backend/.venv/bin/python scripts/seed-synthetic-saved-session.py --root "$PWD" --output /tmp/saved-ui-session.json --cleanup
```

이 선택 실행 검사는 통과했다. 실제 `SaveCourse`와 `SavedCourses` 컴포넌트를 jsdom에서 렌더링하고, 실제 API·DB로 저장(204)·목록·재진입·해제(204)·빈 목록 재조회를 확인한다. 테스트 전용 메모리 export로 기존 컴포넌트에 접근하며 동작을 대체하지 않는다. 진입 데이터는 실제 공개 교육이고 인증 계정만 합성이다. seed 도구는 로컬 `work24_dev` 경계를 검사하고 자격정보 파일을 0600으로 독점 생성한다. 성공·실패 후 cleanup 명령으로 테스트 계정과 파일을 제거하고 자격정보는 커밋하지 않는다.

이 결과는 실제 HTTP에 연결한 React 컴포넌트 검증이다. Google 로그인·교육 상세 진입·추천 모델을 포함한 전체 브라우저 E2E 검증을 의미하지 않는다. 카탈로그 교체·페이지 복구는 실제 DB/HTTP 검사와 별도 UI 상태 검사로 확인했다. 모델 호출이 없는 #29의 GPU 검증은 N/A다.
