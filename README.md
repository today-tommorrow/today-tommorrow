# today-tommorrow

## macOS 개발 시작

React/TypeScript/Vite/Tailwind 웹과 FastAPI/SQLAlchemy/PostgreSQL 기반을 구현했다. 웹은 실제 `GET /api/health`로 API와 DB 연결을 확인한다. 추천·이미지 판정, GPU, Nginx 배포, 샘플 모드와 공통 setup/dev/check 스크립트는 아직 없다. 코드 규칙은 [개발 가이드](docs/development.md), 작업·리뷰 책임은 [AGENTS.md](AGENTS.md)를 따른다.

### 다섯 MacBook의 공통 준비

Git, Node/npm, Python/uv, Docker Compose v2 이상을 준비하고 Docker 엔진을 실행한다. 기준은 `.node-version`의 Node 26.5.0, `.python-version`의 Python 3.14.6이다. Node는 프로젝트 엔진 범위 내에서 사용할 수 있지만 팀은 기준 버전을 우선 맞춘다. uv는 Python 설치·선택을 지원한다. Apple Silicon과 Intel은 같은 잠금 파일을 사용한다. 아키텍처별 실제 실행은 각 환경에서 확인한다.

```sh
git --version
node --version
npm --version
python3 --version
uv --version
docker compose version
docker info
```

### 수동 설치와 DB 준비

저장소 루트에서 시작한다. 환경 파일은 없을 때만 복사하고 기존 값을 덮지 않는다. 예제 값은 이 컴퓨터의 개발 DB 전용이며 공유 서버에서 쓰지 않는다. `.env`는 커밋하지 않는다.

```sh
test -f .env || cp env.example .env
cd web
npm ci
cd ../backend
uv sync --locked
cd ..
docker compose up -d --wait db
cd backend
uv run --locked --env-file ../.env alembic upgrade head
```

Compose는 `postgres:17.11`을 루프백에만 열고 named volume에 데이터를 보존한다. DB healthcheck 성공 후에만 마이그레이션한다. 현재 baseline은 업무 테이블 없이 Alembic 버전만 기록한다. 재실행해도 `.env`·데이터를 지우지 않는다. DB 계정·비밀번호 변경은 이미 생성된 볼륨에 자동 적용되지 않는다. 연결 오류가 나면 설정을 확인하며 볼륨 삭제로 해결하지 않는다.

### API와 웹 실행

별도 터미널에서 각각 실행한다. API는 기본 `http://127.0.0.1:8000`, 웹은 Vite가 출력하는 `http://127.0.0.1:5173`에 접속한다.

```sh
# 터미널 1: 저장소 루트에서
cd backend
uv run --locked --env-file ../.env uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```sh
# 터미널 2: 저장소 루트에서
cd web
npm run dev
```

웹의 연결 확인은 실제 DB의 `SELECT 1` 성공 시 `200 {"status":"ok"}`, DB 연결 실패 시 `503 {"detail":"database_unavailable"}`를 사용한다. 로딩·성공·실패와 다시 확인을 제공한다. API 문서는 `http://127.0.0.1:8000/docs`에서 볼 수 있다. 샘플 응답으로 성공을 대체하지 않는다.

포트 충돌 시 `.env`의 `DB_PORT`와 `DATABASE_URL` 포트를 함께 바꾼다. API 포트는 uvicorn의 `--port`와 `.env`의 `API_TARGET`을 함께 바꾸고, 웹은 `WEB_PORT`를 바꾼다. 변경 후 서버를 재시작한다. Vite는 `/api`를 유지해 API로 프록시하며 포트가 사용 중이면 임의 포트로 옮기지 않고 실패한다.

### 검사와 종료

```sh
# 저장소 루트에서
cd web
npm run typecheck
npm run build
cd ..
curl --fail http://127.0.0.1:5173/api/health
# 본인이 실행한 서버는 각 터미널에서 Ctrl-C
docker compose stop db
```

DB 장애 검증은 본인의 개발 DB를 중지한 뒤 웹에서 다시 확인해 실패 표시와 503을 확인하고, 다시 `docker compose up -d --wait db`로 복구한다. 다른 팀원의 공유 DB에는 수행하지 않는다. `npm ci`는 `web/package-lock.json`, `uv sync --locked`는 `backend/uv.lock`을 사용한다. 의존성 변경 때만 잠금 파일을 갱신한다. ESLint/Prettier/Ruff와 공통 check는 후속 작업이다.

GPU 비밀은 백엔드 환경에만 둔다. `VITE_`는 공개값이므로 토큰을 넣지 않는다. 샘플·로그·프롬프트에 개인정보를 넣지 않는다. 첫 온보딩의 초기 완료 조건은 구현자 외 팀원 한 명의 두 번째 MacBook 새 클론에서 설치·마이그레이션·웹 연결·검사·재실행 보존을 확인하는 것이다. 다섯 대는 같은 절차를 공유하며, 이 두 번째 Mac 검증은 아직 미수행이다.

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
- `main` 변경은 관리자도 PR을 거친다. 필수 승인 수는 0명이며, 계약 변경 리뷰 요청은 팀의 협업 규칙이다.
- 병합은 **Squash and merge**를 사용한다. 병합 후 작업 브랜치는 자동 삭제된다. `main`의 강제 푸시와 삭제는 차단한다.
- 초기에는 필수 CI 검사를 지정하지 않는다. 팀의 검사 워크플로가 실제 PR에서 한 번 이상 성공한 뒤 해당 검사를 필수로 지정한다.
