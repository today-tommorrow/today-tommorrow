# 로컬 Google 로그인

두 사람 모두 브라우저에서 **`http://127.0.0.1:5173`** 을 연다. Google 콘솔의 웹 애플리케이션 클라이언트에 등록한 아래 값을 그대로 사용한다.

| 항목 | 값 |
| --- | --- |
| 승인된 JavaScript 원본 | `http://127.0.0.1:5173` |
| 승인된 리디렉션 URI | `http://127.0.0.1:5173/api/auth/google/callback` |
| `WEB_PORT` | `5173` |
| `PUBLIC_ORIGIN` | `http://127.0.0.1:5173` |
| `GOOGLE_REDIRECT_URI` | `http://127.0.0.1:5173/api/auth/google/callback` |
| `VITE_API_BASE_URL` | `/api` |

각 PC의 `127.0.0.1`은 그 PC 자신이므로 둘 다 5173을 써도 서로 충돌하지 않는다. `localhost`와 혼용하거나 웹 포트를 임의로 바꾸지 않는다. 주소·포트를 바꾸려면 Google 콘솔 등록과 환경 설정도 함께 바꿔야 한다. 백엔드 포트는 PC마다 달라도 된다. 예를 들어 `API_TARGET=http://127.0.0.1:18000`이면 `./dev`가 해당 포트에서 API를 시작하고 Vite가 `/api` 요청을 전달한다. Google에는 계속 웹 포트 5173의 콜백을 등록한다.

## 각 PC에서 준비

1. `./setup`을 실행한다. 기존 `.env`는 덮어쓰지 않으므로 [`env.example`](../env.example)의 새 항목을 직접 추가한다.
2. `.env`에 Google 웹 클라이언트의 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`을 넣는다. Google 동의 화면이 테스트 상태라면 사용할 계정을 테스트 사용자로 등록한다.
3. `backend/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'`로 각 PC의 `SESSION_SECRET`을 생성해 `.env`에 넣는다. 최소 32 UTF-8 바이트가 필요하다. 값을 공유하거나 문서·PR·로그에 붙이지 않는다.
4. 위 표의 주소를 맞추고 `./dev`를 실행한다. 환경 설정을 바꿨다면 서버를 재시작한다.

클라이언트 비밀과 세션 키는 백엔드 전용이다. `VITE_` 변수, 브라우저 코드, 저장소에 넣지 않는다. 세션 키가 바뀌면 기존 세션은 다시 로그인해야 한다. 설정이 비어 있으면 로그인 시작은 `503`으로 거절하지만, 세션 없는 `/api/auth/me`의 `401`과 health 검사는 사용할 수 있다.

이번 구현의 개발 기본값은 `SESSION_TTL_SECONDS=28800`(8시간 고정 세션), `OAUTH_STATE_TTL_SECONDS=600`(10분 로그인 시도)이다. 사용자 활동에 따라 자동 연장하지 않는다.

## API와 저장 흐름

| 요청 | 결과 |
| --- | --- |
| `GET /api/auth/google/start` | state·browser binding·nonce를 발급하고 DB에 해시를 저장한 뒤 Google로 `302` 이동 |
| `GET /api/auth/google/callback` | state·브라우저를 확인해 한 번 소비하고 commit한 뒤 Google 토큰 교환·검증. 성공하면 서비스 세션 쿠키를 발급하고 `/onboarding`으로 `302` 이동 |
| 로그인 취소 콜백 | 유효한 state 확인 후 `/login?error=oauth_cancelled`로 `302` 이동 |
| `GET /api/auth/me` | 유효한 세션이면 `200 {user, csrf_token, expires_at}`, 세션이 없거나 유효하지 않으면 `401` |
| `POST /api/auth/logout` | 유효한 세션과 `X-CSRF-Token` 확인 후 세션 취소·쿠키 삭제·`204`. CSRF가 틀리면 `403`. `Origin`이 있으면 `PUBLIC_ORIGIN`과도 일치해야 함 |

`google-auth[requests]`로 Google ID 토큰의 서명·발급자·대상 클라이언트·만료를 검증하고, 로그인 시도의 nonce도 대조한다. 이메일 대신 Google `sub`로 기존 사용자를 찾는다. Google 토큰을 서비스 쿠키나 DB에 보관하지 않는다. 기존 [`AuthStore`](../backend/app/auth/README.md)를 사용하므로 추가 DB 스키마는 없다.

`today_tommorrow_session`과 `today_tommorrow_oauth_binding` 쿠키는 `HttpOnly`, `SameSite=Lax`, `Path=/`로 설정한다. HTTPS에서는 `Secure`도 적용한다. 한 브라우저에서 새 로그인을 시작하면 이전 로그인 시도의 binding 쿠키를 대체하므로 한 번에 하나의 로그인 시도를 완료한다.

## 재현 확인

`./dev` 실행 상태에서 `./check`를 실행한다. 실제 `/api/auth/me`의 비로그인 `401`, 화면의 Google 로그인 링크, DB health를 확인한다. 백엔드 검사는 합성 Google 응답·서명 토큰과 로컬 DB를 사용하며 실제 Google 계정 로그인을 대신하지 않는다.

실제 로그인은 위 주소를 열어 Google 로그인 → 화면 복귀 → 새로고침 후 로그인 유지 → 로그아웃 순서로 확인한다. 취소 후 취소 안내와 다시 로그인 링크도 확인한다. 결과를 PR에 기록하되 계정 정보·토큰·쿠키 원문은 남기지 않는다.

실제 Google 브라우저 로그인 결과는 아래 기록을 참조한다. 다른 물리적 Mac에서의 재현은 별도 검증이 필요하다. GPU는 이 로그인 구현 범위에 해당하지 않는다.

## Recorded verification — 2026-11-01

An actual Google authorization flow was tested in Chrome using the same origin, `http://127.0.0.1:5173`, with the API on port `62731` and an isolated PostgreSQL 16.14 database with pgvector 0.6.0. Cancelling Google authorization returned the browser to `/login?error=oauth_cancelled` and displayed the cancellation message.

After approving the Google basic-profile consent, the local app displayed the authenticated name and onboarding/start screen. Reloading retained the session, and visiting `/api/auth/me` returned session JSON containing a CSRF token. Logging out through the UI returned to the anonymous login screen; a subsequent visit to `/api/auth/me` returned the `unauthenticated` code. The browser checks did not capture HTTP status codes. Private session values were withheld from this record.

Reproduction on another physical Mac remains pending.

Focused regression checks passed on Python 3.14.6 with the isolated database: 29 authentication tests and 12 Google-provider tests. Load the server environment from `.env` using the launcher parser, then run from `backend`:

```sh
L0_DATABASE_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_auth*.py'
.venv/bin/python -m unittest discover -s tests -p 'test_google.py'
```

These automated checks use synthetic Google responses/tokens and real local database state; they establish protocol status and security regressions separately from the genuine browser flow above. The unchanged application code passed the full `./check` and CI in PR #61. This documentation-only follow-up reran the focused checks above, not the full suite.
