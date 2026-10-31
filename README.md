# today-tommorrow

[프론트엔드 구현 범위·API 주소 설정·GitHub CI 검증](docs/frontend.md)

## macOS 개발 시작

React/TypeScript/Vite/Tailwind 웹과 FastAPI/SQLAlchemy/PostgreSQL 기반이다. 웹은 실제 `GET /api/health`로 API·DB 연결을 확인한다. 추천·이미지 판정, GPU, Nginx 배포와 샘플 모드는 아직 없다. 코드 규칙은 [개발 가이드](docs/development.md), 작업·리뷰 책임은 [AGENTS.md](AGENTS.md)를 따른다.

### 다섯 MacBook의 공통 준비

Git, uv, Docker Compose v2 이상을 준비하고 Docker 엔진을 실행한다. `docker info`로 엔진 연결을 확인한다. Node/npm은 미리 설치하지 않아도 된다. 세 진입점은 `.node-version`의 정확한 Node(현재 26.5.0)와 같은 설치의 작동하는 npm을 PATH에서 먼저 찾고, 없으면 Git에서 제외한 프로젝트 `.local/node` 캐시를 사용한다. setup만 nodejs.org의 공식 macOS arm64/x64 archive를 받아 공식 manifest의 SHA256을 확인한 뒤 설치한다. 지원 빌드가 없거나 검증에 실패하면 중단하며 다른 도구로 대체하지 않는다. 다시 `./setup`을 실행해 재시도할 수 있다. Node 캐시 설치는 잠금과 임시 디렉터리를 사용해 완성된 설치만 공개한다. dev/check는 다운로드 없이 setup을 안내한다. 선택한 Node와 npm의 PATH는 자식 명령에만 적용하고 시스템 기본값·셸 프로필은 바꾸지 않는다.

Python을 미리 설치하거나 `python3`의 PATH를 바꿀 필요는 없다. 세 진입점은 호출 위치와 관계없이 저장소의 `.python-version`(현재 3.14.6)을 읽고 uv로 정확한 기존 설치를 먼저 찾는다. `./setup`만 누락된 Python을 uv 관리 디렉터리에 설치하며 기본 명령 링크를 만들지 않는다(`--no-bin`). 시스템 기본 Python·PATH·셸 프로필은 변경하지 않는다. 첫 설치에는 인터넷이 필요하고 이미 있으면 오프라인 재사용할 수 있다. 새 Python 다운로드를 검증한 uv 버전은 0.12.5다. 이전 uv에서 `No download found`가 나오면 uv를 업데이트한 뒤 재시도한다. 기존 정확한 Python은 이전 uv에서도 재사용할 수 있다. uv가 없거나 다운로드가 실패하면 안내와 함께 비정상 종료한다. uv 설치·네트워크 문제를 해결한 뒤 `./setup`을 다시 실행한다. `./dev`·`./check`는 Python을 다운로드하지 않으며 없으면 setup을 안내한다.

Apple Silicon과 Intel은 같은 잠금 파일과 아래 명령을 사용한다. 두 번째 **물리적 Mac**의 완료 검증은 아직 미수행이며, 한 Mac의 격리된 복사본 검증으로 대신 완료 처리하지 않는다.

### 준비 → 실행 → 검사

저장소 루트에서 실행한다.

```sh
./setup
./dev
# 별도 터미널에서
./check
```

`./setup`은 도구·버전·Docker 엔진을 확인하고 `.env`가 없을 때만 `env.example`을 복사한다. `web`에서 `npm ci`, `backend`에서 진입점의 Python 경로를 `--python`으로 명시해 `uv sync --locked --dev`로 설치하고 `.venv`의 버전·실제 실행 경로를 확인한 뒤 Compose PostgreSQL의 healthcheck 성공을 기다리고 `alembic upgrade head`를 적용한다. 실패한 단계에서는 비정상 종료하므로 해당 오류를 해결한 뒤 다시 실행한다. 반복 실행은 기존 `.env`와 DB 데이터를 지우지 않는다. baseline은 업무 테이블 없이 Alembic 버전만 기록한다.

`.env`는 줄마다 `KEY=value` 형식이며 공백이 있는 값은 따옴표로 감싼다. 주석을 지원하지만 변수 확장·명령 치환·`export` 문법은 지원하지 않는다. 셸로 source하지 않는다. 호출한 터미널의 환경변수가 파일보다 우선한다. uv의 모든 실행에도 진입점 Python 경로를 명시해 다른 patch 버전으로 자동 전환되지 않게 한다. 예제 계정은 개인 컴퓨터의 개발용이며 공유 서버에는 사용하지 않는다. `.env`를 커밋하지 않는다.

| 설정 | 기본값과 변경 방법 |
| --- | --- |
| `DB_PORT` | `5432`. 변경하면 `DATABASE_URL`의 포트도 함께 맞춘다. |
| `DATABASE_URL` | 루프백 PostgreSQL 연결 URL. 사용자·비밀번호·DB명은 Compose의 `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`와 맞춘다. |
| `API_TARGET` | `http://127.0.0.1:8000`. `./dev`가 이 주소의 포트에 API를 실행하고 Vite가 같은 대상으로 `/api`를 유지해 프록시한다. |
| `WEB_PORT` | `5173`. `./dev`가 출력한 실제 웹 URL로 접속한다. |
| `COMPOSE_PROJECT_NAME` | 필요하면 독립된 DB/볼륨 이름을 선택한다. 같은 이름은 같은 데이터를 사용한다. |

`./dev`는 웹과 API를 한 터미널에서 실행한다. Ctrl-C는 둘 다 종료하며 종료 코드 130을 반환한다. 한 서버가 실패하면 함께 시작한 서버도 종료하고 실패 코드를 반환한다. 포트가 사용 중이면 시작하지 않으며 기존 프로세스를 종료하지 않는다. DB는 종료하지 않으므로 재시작에 재사용할 수 있다. 기본 API 문서는 `http://127.0.0.1:8000/docs`다.

웹 연결 확인의 성공은 실제 DB `SELECT 1`의 `200 {"status":"ok"}`, DB 장애는 `503 {"detail":"database_unavailable"}`다. 로딩·성공·실패와 다시 확인을 제공하며 샘플 성공을 사용하지 않는다. Compose는 `postgres:17.11`을 루프백에만 열고 named volume에 보존한다. 기존 볼륨의 계정·비밀번호는 `.env` 변경만으로 바뀌지 않는다. 연결 오류 시 볼륨을 삭제하지 말고 설정을 확인한다.

`./check`는 TypeScript, ESLint, Prettier, 웹 Vitest, Ruff, API·진입점 unittest, Vite 빌드를 순서대로 검사한다. 첫 실패에서 비정상 종료하며 파일을 자동 수정하지 않는다. 웹/API 단위 테스트는 목을 사용해 성공·장애·재시도 계약을 검증하고 실제 DB/GPU 검증을 대체하지 않는다. 필요하면 `cd web && npx prettier --write src`로 포맷을 수정하고 diff를 확인한다.

본인의 DB를 멈출 때는 기본 설정에서 `docker compose stop db`를 사용한다. `COMPOSE_PROJECT_NAME`을 사용했다면 같은 이름을 명령에 지정한다. 볼륨 삭제는 이 절차에 포함하지 않는다. DB 장애 확인은 본인의 DB에만 수행하고 `./setup`으로 복구한다.

의존성 변경 때만 `web/package-lock.json`과 `backend/uv.lock`을 갱신한다. GPU 비밀은 백엔드 환경에만 두고 공개되는 `VITE_`에 넣지 않는다. 샘플·로그·프롬프트에 개인정보를 넣지 않는다. 초기 온보딩 완료 조건은 구현자 외 팀원 한 명의 Mac 새 클론에서 위 절차·재실행 보존을 확인하는 것이며 아직 미완료다.

## 해커톤 협업 규칙

`main`은 웹, API, AI가 함께 동작하는 데모 기준 브랜치다. 보통 최신 `main`에서 분기하고, 팀원이 확인할 수 있는 작은 단위로 PR을 연다.

### 시나리오 하나에 이슈 하나

이슈 제목은 사용자가 수행하는 행동과 확인할 결과로 짓는다. 아래는 예시 이슈 `#12 이미지 업로드 후 판정 결과 확인`의 본문이다. 실제 작업에서는 GitHub가 부여한 이슈 번호를 사용한다.

> **완료 조건** — 사용자가 샘플 이미지 한 장을 올리면 실제 모델의 판정과 신뢰도가 화면에 표시된다. 이미지 없이 요청하면 오류 안내가 보인다.
>
> **연동 예시** — `POST /api/predict`에 `multipart/form-data`의 `image` 필드로 샘플 이미지를 보낸다. 성공 응답 예시는 `200 {"label":"cat","score":0.94}`다. API는 0~1 범위의 점수를 보내고 웹은 이를 94%로 표시한다. 이미지가 없으면 `400 {"error":"image_required"}`를 반환한다.

| 이슈의 작업 목록 | 작업 브랜치 예시 | PR에서 확인할 결과 |
| --- | --- | --- |
| 웹 · 업로드와 결과 화면 | `feat/web/12-result-view` | 샘플 성공·오류 응답으로 두 화면 확인 |
| API · 이미지 검증과 판정 응답 | `feat/api/12-predict` | 샘플 이미지와 빈 요청으로 상태 코드 확인 |
| AI · 모델 추론 연결 | `feat/ai/12-inference` | 샘플 이미지의 레이블과 점수 범위 확인 |
| 인프라 · 모델 실행 환경 준비 (필요할 때) | `feat/infra/12-model-runtime` | 데모 환경에서 모델 로딩 확인 |

이슈에서 영역별 담당자를 정하고 작업 완료를 표시한다. 각 담당자는 최신 `main`에서 `<종류>/<영역>/<이슈번호>-<작업>` 브랜치를 만든다. 오류 수정에는 `fix`, 모델 비교 실험에는 `exp`를 사용한다. 예를 들어 `exp/ai/12-model-v2`에서 비교한 뒤 채택한 변경만 추론 PR에 담는다.

독립적으로 병합해도 데모가 동작하면 각 PR을 바로 `main`으로 보낸다. 중간 PR 본문에는 `Refs #12`를 적어 이슈를 연결한다. 실제 모델까지 연결해 완료 조건을 확인한 마지막 `main` 대상 PR에만 `Closes #12`를 적는다. 모든 PR을 합친 뒤에야 확인할 수 있다면, 이슈에 검증 결과를 남기고 직접 닫는다.

### 임시 통합 브랜치를 쓰는 경우

서로 의존하는 변경을 따로 병합하면 현재 데모가 깨지고, 호환 가능한 작은 변경으로 나누기도 어려울 때 사용한다. 예를 들어 API가 신규 결과 저장 스키마를 요구하고, 새 스키마에서는 기존 API가 결과를 읽지 못하는 경우다. 두 변경을 각각 배포할 수 있다면 평소처럼 `main` 대상 PR로 진행한다.

1. 시나리오 담당자 한 명이 최신 `main`에서 `integration/12-predict`를 만들고 이슈에 통합 대상과 담당자를 적는다.
2. 각 담당자는 통합 브랜치에서 작업 브랜치(예: `feat/api/12-result-schema`, `feat/api/12-result-reader`)를 딴다. 각 PR의 **base**로 `integration/12-predict`를 선택하고 본문에 `Refs #12`를 적는다.
3. 담당자는 두 PR을 통합 브랜치에 병합하고 최신 `main` 변경을 가져와 충돌을 해결한다. 테스트용 DB에 마이그레이션을 적용한 뒤 API로 결과를 저장하고 다시 조회한다. 웹에서도 샘플 이미지를 올려 판정 결과까지 확인한다.
4. 통합 브랜치에서 `main`으로 PR 하나를 열고 검증 결과를 기록한다. 이 PR로 시나리오가 완료되면 `Closes #12`를 붙인다. squash 병합한 뒤 통합 브랜치의 사용을 끝낸다.

### 영역 간 연동

1. 연동을 시작할 때 요청 필드, 모델 입력 형식, 성공·오류 응답을 정하고 팀이 함께 쓸 샘플 입력과 예상 응답을 PR에 기록한다.
2. 웹은 샘플 응답으로 화면을 만들고, API와 AI는 같은 예시로 구현을 확인한다. 계약을 바꾸면 영향을 받는 영역 담당자에게 리뷰를 요청하고 변경 전후 예시를 남긴다.
3. 실제 모델을 연결한 PR에서는 같은 샘플을 웹에서 입력해 API와 모델을 거쳐 결과가 표시되는지 확인하고 결과를 PR에 남긴다.

### PR과 병합

- 기본 PR 대상은 `main`이며, 임시 통합에 참여하는 작업 PR만 해당 통합 브랜치를 대상으로 한다. PR 본문에 변경 목적, 영향받는 영역, 실행한 검증과 결과를 적는다.
- `main` 변경은 관리자도 PR을 거친다. 작성자 외 리뷰어 **1명 이상 승인**이 필요하다. 리뷰어는 변경 범위·연동 계약·검증 결과를 확인하고, 계약 변경은 관련 영역 담당자에게 리뷰를 요청한다.
- PR 제목은 아래 Conventional Commit 형식을 사용하고, [PR 양식](.github/pull_request_template.md)에 목적·영향·이슈 연결·실행한 검사와 미검증 항목을 적는다. 목·실제 모델·GPU 결과를 구분한다.
- 병합은 **Squash and merge**를 사용한다. squash 커밋 제목도 PR 제목의 Conventional Commit 형식을 유지한다. 병합 후 작업 브랜치는 자동 삭제된다. `main`의 강제 푸시와 삭제는 차단한다.
- 초기에는 필수 CI 검사를 지정하지 않는다. 팀의 검사 워크플로가 실제 PR에서 한 번 이상 성공한 뒤 해당 검사를 필수로 지정한다.

### 커밋과 코드 규칙

로컬 커밋과 PR 제목은 `type[(scope)]: summary` 형식으로 쓴다. `feat(web): show health status`, `fix(api): sanitize database error`, `docs: clarify onboarding`처럼 동작·변경 목적을 짧게 적는다. 종류는 `feat`, `fix`, `docs`, `refactor`, `test`, `chore` 등을 사용하고 scope는 필요할 때만 붙인다.

호환성을 깨는 변경은 `feat(api)!: change response format`처럼 `!`를 붙이거나 footer에 `BREAKING CHANGE: ...`와 영향·이전 방법을 적는다. 한 커밋은 한 목적을 담고 관련 없는 정리와 섞지 않는다. [Python·프런트엔드 코드 규칙과 검사](docs/development.md#코드-규칙과-검사)를 따르고, 검사를 통과해도 사람이 읽기·계약·완료 조건을 리뷰한다.
