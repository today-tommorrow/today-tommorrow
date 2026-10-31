# 프론트엔드 구현과 연결

[프로젝트 안내](../README.md) · [개발 가이드](development.md) · [개발 공정](https://github.com/orgs/today-tommorrow/projects/1)

이 변경은 v0.8 HTTP 계약을 사용하는 화면과 요청 흐름을 구현한다. 현재 백엔드는 health와 [Google 로그인·세션·로그아웃 API](google-login.md)를 제공한다. 입력·자료·작업·교육·추천 API는 아직 없으므로 로그인 이후 전체 시나리오 완료를 의미하지 않는다. 서버가 없거나 API가 미구현이면 실제 오류를 표시하며 임의 사용자·질문·과정·추천 결과를 생성하지 않는다.

## 화면과 코드 범위

| 코드 | 구현한 프론트엔드 흐름 | 실제 서버 연동 상태 |
| --- | --- | --- |
| `web/src/App.tsx` | 세션 확인, Google 로그인 시작, 로그아웃, 초안 생성·재조회, 결과·관심 교육 이동, 연결 확인 | 실제 인증 401에서는 로그인 링크를 표시한다. 실제 Google 브라우저 로그인은 별도 검증 필요. 초안 API는 미구현. |
| `web/src/Intake.tsx` | 서버 질문·선택지 표시, 답변·수정, 자유 입력 해석 수락/거절, 파일·링크·텍스트 자료, 자료 확인, 전체 확인·제출, 실제 job 조회 | 입력·자료·job·모델 서버 구현 후 검증 필요 |
| `web/src/Courses.tsx` | 추천 결과 상태·근거·출처, 상세, 2~3개 과정 비교, 관심 교육 저장·해제·조회 | 카탈로그·추천·상세·비교·관심 교육 서버 구현 후 검증 필요 |
| `web/src/api.ts` | 쿠키 포함 요청, CSRF, 요청 제한 시간·중단, HTTP 오류 안내, job polling, 샘플 응답 거부 | 실제 health와 비로그인 인증 401을 CI 통합 검증 대상으로 삼음 |
| `web/src/types.ts` | v0.8 응답·요청 타입 | 정적 타입은 서버 JSON 검증이나 연동 완료의 증거가 아님 |

추천 결과는 `runtime_mode=real_model`, 과정은 `is_demo=false`인 서버 응답을 요구한다. 이 표시는 모델 실행·데이터 출처의 독립적인 검증을 대신하지 않는다. 빈 목록과 미확인 정보는 서버 응답대로 표시하고 성공 결과로 채우지 않는다. 입력의 `revision`, 서버의 `review_token`, 선택지 ID를 사용하며 서버 검증을 프론트에서 대신하지 않는다.

## API 주소 설정

저장소 루트 `.env`를 사용한다. [`env.example`](../env.example)에 공개 API 경로의 기본값이 있다. Vite의 `envDir`는 저장소 루트이며, 프로세스 환경변수가 파일보다 우선한다.

| 변수 | 기본값 | 역할 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api` | 브라우저가 요청할 API 경로. `health` 요청은 `/api/health`가 된다. 별도 API 서버를 연결할 때는 공개 HTTPS 주소에 `/api`까지 포함한다. |
| `API_TARGET` | `http://127.0.0.1:8000` | Vite 개발 서버가 `/api` 요청을 전달할 대상. 요청 경로의 `/api`를 제거하지 않는다. 배포된 정적 파일의 요청 주소를 바꾸는 값은 아니다. |
| `WEB_PORT` | `5173` | Google 로컬 로그인은 두 사람 모두 이 포트와 `http://127.0.0.1:5173` 주소를 사용 |

현재 별도 서버가 없어도 기본값을 유지할 수 있다. GitHub CI는 runner 안에서 실제 API·DB·Vite를 시작하므로 외부 서버 주소나 PAT가 필요하지 않다. 사용자 환경에 서버가 없다면 화면은 연결 오류를 표시한다.

Google 콘솔에 등록한 콜백은 `http://127.0.0.1:5173/api/auth/google/callback`이다. 로컬 로그인에서는 `localhost`나 다른 웹 포트와 혼용하지 않는다. 백엔드 포트가 다르면 각 PC의 `API_TARGET`만 맞춘다. [Google 로그인 설정](google-login.md)을 따른다.

`VITE_API_BASE_URL`은 **공개되는 빌드 시점 값**이다. 변경하면 개발 서버를 재시작하고 배포용 파일은 CI에서 다시 빌드해야 한다. PAT, OAuth client secret, DB 비밀번호, 모델 키를 `VITE_` 변수에 넣지 않는다. 서버와 웹을 다른 origin으로 구성하면 서버의 쿠키·CORS·OAuth callback 설정까지 함께 맞춰야 한다. 같은 origin의 `/api` 구성은 운영 서버의 프록시 설정이 필요하다. [Vite 환경변수 문서](https://vite.dev/guide/env-and-mode)와 [envDir 설정](https://vite.dev/config/shared-options#envdir)을 따른다.

## GitHub CI 검증 범위

리뷰 수정의 회귀 테스트·타입·린트·포맷·빌드는 로컬에서도 확인한다. [Frontend CI](../.github/workflows/frontend-ci.yml)는 push, pull request, 수동 실행으로 동작한다. 자동 배포·자동 승인·자동 병합은 수행하지 않는다.

| 작업 | 실행하는 검사 | 범위의 한계 |
| --- | --- | --- |
| Frontend quality | 잠금 파일 설치, TypeScript, ESLint, Prettier, production build, 입력·설정 회귀 테스트 | 정적 검사·대역 테스트·빌드 성공만으로 사용자 시나리오를 완료 처리하지 않음 |
| Real API and database integration | 정확한 Node/Python·잠금 설치, PostgreSQL 16.14+pgvector 0.6.0, 고정 교육 데이터 적재·검증, 앱 migration, Python 검사·전체 웹 테스트·실제 HTTP 요청 | `WEB_PORT=5273` 사용. 단위 대역과 실제 DB 검사를 구분. [교육 DB 범위](local-database.md)를 따르며 외부 운영 서버를 사용하지 않음 |
| 프론트 테스트 4개 | 실제 health 성공, 비로그인 auth 401, 화면의 Google 로그인 링크 표시, 연결 확인 버튼 성공 | jsdom 렌더 테스트. 실제 브라우저 OAuth·E2E·시각 검증을 대신하지 않음 |
| 입력·설정 회귀 테스트 | 등록한 종류만 초기화, 해석 전후 입력 보존, 실패 문서의 재시도 표시, 조회 오류 시 작업 ID 보존, 루트 `.env`·환경변수·API 경로 | 합성 계약 응답과 임시 설정을 사용. 실제 업무 API·모델 검증을 대신하지 않음 |
| DB 장애·복구 | 실제 DB 컨테이너 중지 후 health 503 확인, 재시작 후 성공 확인 | 업무 데이터 저장·재조회 및 개인정보 삭제 검증은 미수행 |

`App.test.tsx`는 실제 HTTP 응답을 사용한다. `Intake.test.tsx`는 요청 경계에서 합성 계약 응답을 제공하고 실제 요청 처리·작업 polling·React 화면 동작을 검증한다. 설정 회귀 테스트는 실제 저장소 `.env`를 수정하지 않고 임시 디렉터리에서 Vite 설정을 로드한다. 실제 사용자 데이터·계정·추천 결과를 만들지 않는다. 포맷 검사에 실패하면 CI runner에서 만든 `formatting-correction` 패치 아티팩트를 제공하며 저장소에 자동 커밋하지 않는다. 실패 로그는 `real-integration-logs`에서 확인한다.

검증 완료 여부는 해당 커밋의 실제 Actions 결과로 확인한다. OAuth 전체 흐름, 입력 저장·복원, 문서 처리, 실제 모델 질문·추천, 교육 상세·비교·관심 저장은 서버 구현과 실제 통합 후 별도 증거가 필요하다. GPU는 이 CI 작업에서 N/A이며 실제 GPU 검증 완료로 표시하지 않는다.

## #7 프론트엔드 공통 샘플 확인

[`Courses.fixtures.ts`](../web/src/Courses.fixtures.ts)는 v0.8 타입을 사용하는 합성 HTTP 응답 후보 샘플이다. 교육 상세 성공(200), 관심 교육 성공·빈 목록(200), 교육 없음(404)을 포함한다. 테스트에서만 가져오며 운영 화면의 대체 데이터로 사용하지 않는다. `is_demo=false`도 합성 계약 필드이며 실제 교육 원천을 검증했다는 뜻이 아니다. 오류 code를 포함한 샘플의 서버 채택은 API 담당자와 확인해야 한다.

[`Courses.test.tsx`](../web/src/Courses.test.tsx)는 실제 요청 함수의 fetch 경계에 이 응답을 제공한다. 공개 `/api` 경로·쿠키 포함 요청, 출처 보존, null 비용과 0원 구분, 온라인과 자율 수강 구분, 빈 목록, 404 재시도, 다른 교육 ID·가상 데이터 거부를 확인한다. 다음 명령은 `web` 디렉터리에서 실행한다.

```sh
npm run typecheck
npm run lint
npm run format:check
npm test -- src/Courses.test.tsx src/Intake.test.tsx vitest.config.test.ts
npm run build
```

2026-11-01 macOS의 Node 26.7.0/npm 11.19.0에서 위 검사와 테스트 21개가 통과했다. 저장소 지정 Node 26.5.0의 [GitHub CI](https://github.com/today-tommorrow/today-tommorrow/actions/runs/41242727869)에서도 Frontend quality와 Real API and database integration이 통과했다. CI는 전체 웹 테스트·기존 Python 검사·실제 Vite→FastAPI→PostgreSQL health 및 DB 장애 복구를 확인했다. 교육 업무 API·OAuth·브라우저 E2E는 미검증이며, 로컬 전체 `./check`는 미실행이다. GPU는 화면 계약 검사 범위 밖이므로 N/A다. 이 변경은 #7의 웹 영역 일부이며 D01·D02·D04·D05 합의나 전체 이슈 완료를 의미하지 않는다.

## 아직 합의가 필요한 계약

| 결정 | 현재 프론트 처리 | 남은 계약 |
| --- | --- | --- |
| D06 · [초안 이어가기 #12](https://github.com/today-tommorrow/today-tommorrow/issues/12) | URL의 profile ID 또는 같은 브라우저 탭의 계정별 sessionStorage에 남은 ID로 재조회 | 계정의 전체 초안 목록·현재 초안 식별 API가 없음. 다른 기기·새 브라우저의 자동 복원은 제공하지 않음. |
| D08 · [추천 입력 근거 #26](https://github.com/today-tommorrow/today-tommorrow/issues/26) | 서버 추천 응답의 근거만 표시하며 당시 선택 문구는 표시하지 않음 | `profile_snapshot=Answers`에는 당시 질문·선택 ID와 선택 문구가 없음. 최신 프로필로 과거 선택을 추정하지 않음. |
| D10 · [관심 교육 #29](https://github.com/today-tommorrow/today-tommorrow/issues/29) | 현재 목록·저장·해제 API 호출 | 카탈로그 버전 교체 시 과거/현재 과정 식별, 목록 cursor, 중복 정책 합의 필요 |
| D13 · [교육 비교 #28](https://github.com/today-tommorrow/today-tommorrow/issues/28) | 현재 OpenAPI의 2~3개 서로 다른 과정 ID 제약 적용 | 없는 ID·중복 ID·빈 목록의 서버 오류 예시와 상세 UX 합의 필요 |

리뷰어 지정은 [AGENTS.md](../AGENTS.md)를 따른다. 서버와 전체 연동을 확인하기 전에는 관련 시나리오 이슈를 닫지 않는다.

## 초안 API 연동 범위

[초안 저장 계약](profile-drafts.md)의 POST/GET/PATCH와 onboarding/options를 제공한다. 저장은 전체 answers 교체이며 stale revision은 409다. 현재 URL·계정별 sessionStorage로 복원하고 기기 간 최근 초안 자동 조회는 제공하지 않는다. 질문 생성·해석·업로드 옵션은 비활성화되어 있으며 기존 질문 화면의 전체 입력은 L2 이후 연결한다. 실제 DB API 시험과 기존 웹 fixture 시험은 실제 Google 브라우저 재현을 대신하지 않는다.
