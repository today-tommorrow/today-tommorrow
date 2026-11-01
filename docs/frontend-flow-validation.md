# #31 프론트엔드 흐름 검증

현재 판정은 **부분 진행**이다. App 전체를 합성 HTTP 응답으로 연결한 회귀와 실제 Chrome의 API 부재 화면을 확인했다. 실제 로그인부터 자료 처리·추천·저장까지의 A/B 브라우저 E2E는 미검증이며 #31의 완료 조건을 충족하지 않았다.

## 환경과 검증 경계

2026-11-01, 기준 main `7511a08c0c922b95d65ef19ac6d97c461a312a84`의 독립 worktree에서 진행했다. macOS 26.5(25F71), arm64, Node 26.7.0, npm 11.19.0, Vite 7.3.7, Google Chrome 156.0.8078.79를 사용했다. 최종 PR의 실행 커밋·CI 결과는 PR 본문에 기록한다.

| 검사 | 실행 경계 | 결과와 한계 |
| --- | --- | --- |
| 합성 A/B 연결·오류 복구 6개 | 실제 App·Workspace·Intake·Results·상세/비교/관심·request/polling, fetch 응답만 대체 | PASS. jsdom 회귀이며 실제 브라우저·OAuth·서버 저장·모델 실행 증거가 아니다. |
| Chrome의 API 부재 화면 | 실제 Vite, 비어 있는 API 대상 포트, 브라우저 클릭·접근성 트리 | PASS(실패 표시 검사). 로그인 화면에서 오류를 표시하고 연결 확인은 “서버에 연결하지 못했습니다.”였다. 성공·로그인된 작업 화면으로 전환하지 않았다. |
| 실제 A/B 전체 브라우저 E2E | Google 로그인→DB 초안→자료/Worker/모델→추천→상세/비교/관심 | 미검증. 실행 중 서비스와 후속 업무 API가 준비되지 않았다. |
| #24 실제 배치 갱신·과거 추천 재조회 | 활성 배치·snapshot·이전 결과의 실제 저장/재조회 | 미검증. A의 배치·snapshot 기능과 추천 API 제공 후 검증한다. |
| 실제 모델 CPU / GPU | 모델 서버·장치에서 추론 | 미검증. #31의 실제 GPU 전체 흐름 완료 조건을 대체하지 않는다. |

[App.flow.fixtures.ts](../web/src/App.flow.fixtures.ts)는 고정 경로를 재현하는 테스트 전용 응답 생산자다. [App.flow.test.tsx](../web/src/App.flow.test.tsx)에서 사용한다. 운영 API나 입력 정책 구현이 아니며 DB·권한·모델·추출·배치 활성화를 구현하지 않는다. `runtime_mode: real_model`, `is_demo: false`는 기존 타입의 성공 응답을 검사하기 위한 합성 표식이다. 실제 데이터·추론의 증거가 아니며 운영 화면의 대체 응답으로 가져오지 않는다. 기존 sample/demo 거부 코드는 유지한다.

질문은 고정된 8개 대표 선택을 사용한다. 모든 서버 분기·선택지·모델 품질을 검증한 것은 아니다. 자료 확인 후 단계 전환, review token, 제출·추천 응답은 현재 타입을 소비하기 위한 합성 계약 예시다. 현재 main의 서버가 이 전체 경로를 제공한다는 뜻이 아니다.

## 합성 샘플과 확인한 결과

두 경로의 선택은 `entry_intent=explore`, `field_code=software_cloud`, `experience=beginner`, `learning_goal=foundation`, `availability=unknown`, `delivery_mode=online`, `budget_status=unknown`, `start_timing=browse`다. 초안은 빈 answers에서 시작하며 선택 전 값을 채우지 않는다.

- **A · 자료 없음:** 질문 선택 → `document_decision=none` → 남은 질문 → 명시적 제출 → 추천 결과 → 교육 상세/원문 → 2개 비교 → 관심 저장/목록/상세/해제 → 로그아웃. 마지막 answers는 선택한 8개 값과 자료 없음만 포함한다.
- **B · 자료 있음:** 초기 질문 → 합성 TXT 이력서와 `https://example.org/synthetic-jd` 접수/ready 응답 → 원문 인용·출처 대조 → “주말에만 수강할 수 있어요.” 제안의 명시적 수락 → 경험/학습 상태 확인 → 남은 질문 → A와 같은 결과 이후 흐름. 제안 수락 전 constraints_text는 없으며 수락 후 원문 그대로 보존한다. 마지막 answers에는 선택한 값·자료 제공·수락한 제약만 포함된다. TXT나 URL 본문을 실제로 읽거나 OCR을 실행하지 않는다.
- **선택 409:** 답을 자동 재적용하지 않고 같은 초안을 유지한다. 새 질문에서 사용자가 다시 선택한 후 진행한다.
- **추천 작업 실패:** 결과 화면으로 가지 않고 입력을 보존한다. 명시적 재시도는 같은 본문·새 멱등 키로 새 작업을 요청한다.
- **결과 조회 503·App 재마운트:** 같은 run ID만 재조회한다. 질문·제출을 다시 만들지 않고 응답 snapshot을 유지한다. App 재마운트는 실제 브라우저 새로고침 검증이 아니다.
- **관심 저장 401:** 실제 request의 세션 만료 이벤트를 통해 App 전체가 로그인 화면으로 돌아간다. 초안 링크는 계정별 sessionStorage에 유지한다. 실제 계정 간 DB 소유권 검증은 아니다.

모든 경로에서 제출 전 추천 조회/제출 요청이 없고 결과 뒤 추가 질문 생성이 없는지 확인한다. 비교에는 선택한 UUID 2개를 보내며 관심 변경은 같은 교육 ID에 PUT/DELETE한다. 운영 컴포넌트는 mock하지 않는다.

## 실행 명령

독립 worktree의 `web`에서 잠금 파일로 의존성을 준비하고 검사한다. 로컬 `App.test.tsx`는 실제 서비스가 필요하므로 합성 묶음에서 제외했다. CI는 별도로 실제 서비스를 시작해 전체 웹·기존 API/DB 검사를 실행한다.

```sh
npm ci
npm run typecheck
npm run lint
npm run format:check
npm test -- src/App.flow.test.tsx src/Intake.review.test.tsx src/Intake.links.test.tsx src/Intake.documents.test.tsx src/Intake.interpretations.test.tsx src/SavedCourses.test.tsx src/Courses.compare.test.tsx src/Results.test.tsx src/Courses.catalog.test.tsx src/Intake.edits.test.tsx src/Intake.questions.test.tsx src/Intake.jobs.test.tsx src/App.drafts.test.tsx src/App.auth.test.tsx src/Courses.test.tsx src/Intake.test.tsx vitest.config.test.ts
npm run build
```

## 실제 Chrome의 API 부재 검사

기존 기본 웹/API 포트에 리스너가 없었고 `http://127.0.0.1:5173/api/health` 사전 조회는 연결 실패(HTTP 000)였다. 검증 전용 5317/5318 포트가 비어 있음을 확인한 뒤 Vite만 실행했다. API 서버나 Docker는 시작하지 않았고 환경 파일을 수정하지 않았다.

```sh
# web에서 실행. 5318에는 API를 실행하지 않는다.
WEB_PORT=5317 API_TARGET=http://127.0.0.1:5318 npm run dev -- --strictPort
```

터미널에 출력된 `http://127.0.0.1:5317/`을 Chrome 새 탭으로 열었다. 브라우저 연결 호스트를 통한 탭 생성은 사용할 수 없어 Chrome 네이티브 UI로 확인했다.

| 단계 | 예상 | 실제 |
| --- | --- | --- |
| 웹 문서 GET | 웹 렌더 가능 | HTTP 200 |
| `/api/auth/me` | API 부재로 인증 성공 불가 | 프록시 HTTP 500, Vite의 ECONNREFUSED 127.0.0.1:5318 |
| 로그인 화면 | 오류 표시, 인증된 작업 화면 없음 | “요청을 처리하지 못했습니다.” 표시 |
| 연결 확인 펼치기→연결 확인하기 | 성공으로 표시하지 않음 | “서버에 연결하지 못했습니다.” 표시, `/api/health` HTTP 500 |
| 정리 | 만든 탭·서버만 종료 | 검증 탭 닫음, Vite Ctrl-C 종료, 5317/5318 리스너 없음 |

이 500은 의도적으로 API가 없는 대상에 연결한 프록시 결과다. 실제 FastAPI·PostgreSQL 장애 복구를 실행한 결과가 아니다. 실제 A/B 브라우저 성공이나 DB health 200으로 표시하지 않는다.

## 실제 A/B E2E를 시작할 조건

기준 main의 FastAPI 등록 경로는 인증·초안·질문·jobs·health다. 자료 등록/해석/확인/재시도/삭제, 제출/추천, 교육 상세/비교/관심 API가 없어 현재 실제 A/B 전체 경로를 진행할 수 없다. 이 상태에서 인증 우회·가짜 운영 응답을 넣어 통과시키지 않는다.

A의 구현이 준비되면 같은 검토된 main 커밋에서 다음을 진행한다.

1. README와 기능 문서에 따라 실제 웹/API/DB/Worker 및 테스트 계정·모델 설정을 준비하고 커밋·도구·데이터·모델 버전을 기록한다. 비밀 값은 기록하지 않는다.
2. 작성자 외 팀원이 Google 로그인 후 A/B 샘플을 브라우저에서 수행한다. 자료는 합성/공개 허용 데이터만 사용한다. 각 선택의 실제 요청, 재조회한 answers, revision·원문/출처를 대조한다.
3. 자료 유/무, 미정·온라인, 주말 제한을 구분한다. 제출 전 추천 요청이 없고, 선택/수락하지 않은 사실이 저장되지 않고, 결과 뒤 추가 질문이 없는지 확인한다.
4. 실제 실패·409·세션 만료·새로고침·Worker 복구에서 원문과 미등록 입력 보존을 확인한다. 서버 오류 주입과 DB/Worker 조작은 A가 소유한 테스트 환경에서 수행한다.
5. 실제 교육 상세·비교·관심 저장/재조회/해제와 타인 ID 거절을 검증한다. #24는 배치 활성 전환 후에도 이전 run의 버전·snapshot·근거가 유지되는지 별도 확인한다.
6. 실제 모델 CPU와 GPU는 장치·모델 버전·같은 샘플의 결과를 분리해 기록한다. GPU 미실행을 완료로 표시하지 않는다. 테스트 모델 경로와 실제 추론을 구분한다.

각 결과에는 명령/브라우저 조작, 샘플 ID, 예상·실제 상태/응답, 환경, 증거, 미검증 이유를 남긴다. 현재 이 문서나 합성 테스트의 통과로 #24·#31을 닫지 않는다.
