# today-tommorrow

[프론트엔드 구현 범위·API 주소 설정·GitHub CI 검증](docs/frontend.md)

[실제 고용24 데이터·벡터를 사용하는 로컬 DB 초기화](docs/local-database.md)

[사용자·세션·OAuth 상태 저장 기반](backend/app/auth/README.md)

[두 사람의 로컬 Google 로그인 주소·설정·재현](docs/google-login.md)

[입력 초안 저장 API·revision·복원 범위](docs/profile-drafts.md)

[선택형 질문·비동기 작업 API와 Worker](docs/question-jobs.md)

## macOS 개발 시작

React/TypeScript/Vite/Tailwind 웹과 FastAPI/SQLAlchemy/PostgreSQL 기반이다. 웹은 실제 `GET /api/health`로 API·DB 연결을 확인한다. 프로필 초안과 선택형 질문 생성·작업·답변 API가 있으며, 질문 생성은 별도 Worker와 설정한 모델 서버가 필요하다. 추천·이미지 판정, GPU 실물 검증, Nginx 배포와 샘플 모드는 아직 없다. 코드 규칙은 [개발 가이드](docs/development.md), 작업·리뷰 책임은 [AGENTS.md](AGENTS.md)를 따른다.

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

`./setup`은 도구·버전·Docker 엔진을 확인하고 `.env`가 없을 때만 `env.example`을 복사한다. 웹·API와 별도 loader 환경을 잠금 파일로 설치하고 PostgreSQL 16.14 + pgvector 0.6.0 개발 이미지를 빌드한다. 실제 고정 교육 데이터 406개 과정·6,099개 단위를 적재·검증한 뒤 `today_app`의 앱 migration을 적용한다. 모든 uv 실행은 진입점 Python을 사용한다. 실패하면 해당 단계를 해결해 재실행하며 기존 `.env`·앱 입력·이전 PostgreSQL 17 볼륨은 보존한다. [데이터 출처·역할·검증 범위](docs/local-database.md)를 따른다.

`.env`는 줄마다 `KEY=value` 형식이며 공백이 있는 값은 따옴표로 감싼다. 주석을 지원하지만 변수 확장·명령 치환·`export` 문법은 지원하지 않는다. 셸로 source하지 않는다. 호출한 터미널의 환경변수가 파일보다 우선한다. uv의 모든 실행에도 진입점 Python 경로를 명시해 다른 patch 버전으로 자동 전환되지 않게 한다. 예제 계정은 개인 컴퓨터의 개발용이며 공유 서버에는 사용하지 않는다. `.env`를 커밋하지 않는다.

| 설정 | 기본값과 변경 방법 |
| --- | --- |
| `DB_PORT` | `55432`. 변경하면 세 DB URL의 포트도 함께 맞춘다. |
| `DATABASE_URL` | `today_app` runtime 계정. `work24_dev`에서 교육 읽기·앱 업무 테이블 DML만 수행한다. |
| `MIGRATION_DATABASE_URL` | `today_migrator` 계정. `today_app`의 앱 migration만 수행한다. |
| `CATALOG_DATABASE_URL` | Compose의 `POSTGRES_USER`/`POSTGRES_PASSWORD`에 맞춘 로컬 `today_bootstrap` 계정. 교육 초기화 전용이다. |
| `API_TARGET` | `http://127.0.0.1:8000`. `./dev`가 이 주소의 포트에 API를 실행하고 Vite가 같은 대상으로 `/api`를 유지해 프록시한다. |
| `WEB_PORT` | Google 로컬 로그인은 두 사람 모두 `5173`, 브라우저 주소는 `http://127.0.0.1:5173`으로 맞춘다. |
| `PUBLIC_ORIGIN` / `GOOGLE_REDIRECT_URI` | 등록한 원본 `http://127.0.0.1:5173`과 콜백 `http://127.0.0.1:5173/api/auth/google/callback`. [로그인 설정](docs/google-login.md)을 따른다. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET` | 로컬 `.env`에 직접 설정하는 백엔드 전용 값. 예제에는 비워 두며 `VITE_`로 공개하지 않는다. |
| `COMPOSE_PROJECT_NAME` | 필요하면 독립된 DB/볼륨 이름을 선택한다. 같은 이름은 같은 데이터를 사용한다. |

`./dev`는 웹과 API를 한 터미널에서 실행한다. Ctrl-C는 둘 다 종료하며 종료 코드 130을 반환한다. 한 서버가 실패하면 함께 시작한 서버도 종료하고 실패 코드를 반환한다. 포트가 사용 중이면 시작하지 않으며 기존 프로세스를 종료하지 않는다. DB는 종료하지 않으므로 재시작에 재사용할 수 있다. 기본 API 문서는 `http://127.0.0.1:8000/docs`다.

`./dev`는 질문 Worker를 시작하지 않는다. 질문 생성이 필요하면 `.env`의 `QUESTION_MODEL_BASE_URL`과 `QUESTION_MODEL_ID`를 실제 서비스 값으로 채우고 별도 터미널에서 Worker를 실행한다. API와 Worker는 같은 루트 `.env`를 읽는다. 변수와 작업 방식은 [질문 API·Worker 안내](docs/question-jobs.md)를 따른다.

웹 연결 확인의 성공은 실제 DB `SELECT 1`의 `200 {"status":"ok"}`, DB 장애는 `503 {"detail":"database_unavailable"}`다. 로딩·성공·실패와 다시 확인을 제공하며 샘플 성공을 사용하지 않는다. Compose는 DB를 루프백에만 열고 새 `postgres16_data` 볼륨을 사용한다. 기존 PostgreSQL 17 볼륨은 연결·삭제하지 않는다. 기존 역할의 비밀번호는 `.env` 변경만으로 바뀌지 않으므로 오류 시 설정을 확인한다.

`./check`는 교육 DB의 읽기 전용 검증, TypeScript, ESLint, Prettier, 웹 Vitest, Ruff, API·진입점·loader unittest, Vite 빌드를 검사한다. 첫 실패에서 종료하며 소스를 자동 수정하지 않는다. 웹 전체 테스트에는 실행 중인 Vite·FastAPI·PostgreSQL이 필요하다. 루트 `.env`의 `WEB_PORT`와 API 경로를 사용한다. 일부 UI/API 단위 검사는 합성 대역을 쓰며, DB 경계·재적재 보존 검사는 실제 개발 DB를 사용한다. 모델·GPU 검증을 대체하지 않는다.

리뷰 수정은 로컬에서 회귀 테스트·타입·린트·포맷·빌드를 확인하고, [GitHub CI](.github/workflows/frontend-ci.yml)에서 전체 웹 테스트와 Python 검사를 실행한다. CI는 기본값과 다른 웹 포트에서 실제 서비스를 시작해 웹 연동·DB 장애 복구도 확인한다. 포맷 실패 시 CI가 수정 패치 아티팩트를 제공한다. [검증 범위와 미검증 항목](docs/frontend.md#github-ci-검증-범위)을 확인한다.

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
- `main` 변경은 관리자도 PR을 거친다. 작성자 외 리뷰어 **1명 이상 승인**이 필요하다. 리뷰어 지정은 [AGENTS.md](AGENTS.md)를 따른다. [.github/CODEOWNERS](.github/CODEOWNERS)가 대상 브랜치에 반영되면 자동 리뷰 요청에 적용된다. 리뷰어는 변경 범위·연동 계약·검증 결과를 확인하고, 계약 변경은 관련 영역 담당자에게 영향 확인을 받는다.
- PR 제목은 아래 Conventional Commit 형식을 사용하고, [PR 양식](.github/pull_request_template.md)에 목적·영향·이슈 연결·실행한 검사와 미검증 항목을 적는다. 목·실제 모델·GPU 결과를 구분한다.
- 병합은 **Squash and merge**를 사용한다. squash 커밋 제목도 PR 제목의 Conventional Commit 형식을 유지한다. 병합 후 작업 브랜치는 자동 삭제된다. `main`의 강제 푸시와 삭제는 차단한다.
- 초기에는 필수 CI 검사를 지정하지 않는다. 팀의 검사 워크플로가 실제 PR에서 한 번 이상 성공한 뒤 해당 검사를 필수로 지정한다.

### 커밋과 코드 규칙

로컬 커밋과 PR 제목은 `type[(scope)]: summary` 형식으로 쓴다. `feat(web): show health status`, `fix(api): sanitize database error`, `docs: clarify onboarding`처럼 동작·변경 목적을 짧게 적는다. 종류는 `feat`, `fix`, `docs`, `refactor`, `test`, `chore` 등을 사용하고 scope는 필요할 때만 붙인다.

호환성을 깨는 변경은 `feat(api)!: change response format`처럼 `!`를 붙이거나 footer에 `BREAKING CHANGE: ...`와 영향·이전 방법을 적는다. 한 커밋은 한 목적을 담고 관련 없는 정리와 섞지 않는다. [Python·프런트엔드 코드 규칙과 검사](docs/development.md#코드-규칙과-검사)를 따르고, 검사를 통과해도 사람이 읽기·계약·완료 조건을 리뷰한다.
