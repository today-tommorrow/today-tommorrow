# 선택형 질문과 비동기 작업

프로필의 미응답 슬롯에서 질문 하나를 생성하고, 사용자가 선택한 답을 초안에 반영한다. API는 job을 저장하고 별도 Worker가 설정된 모델 서버에 질문을 요청한다. 프로필·질문·답변·요청 응답은 PostgreSQL `today_app`에 저장한다. `0004_job_queue`, `0005_intake_questions`, `0006_answer_history_index` migration이 필요하며 `./setup`이 적용한다.

## 준비와 실행

질문 생성은 모델 서비스 주소와 ID 설정이 유효할 때 활성화된다. 실제 연결이나 응답 검증 실패는 job의 failed 상태로 확인한다. 저장소 루트 `.env`에 실제 서비스 주소와 모델 ID를 설정한다. `env.example`은 주소와 ID를 비워 두므로 기본 상태에서 질문 생성은 비활성화된다. 임의 기본 모델이나 성공 대체 응답은 제공하지 않는다.

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `QUESTION_MODEL_BASE_URL` | 빈 값 | OpenAI 호환 모델 서비스의 origin(base URL에는 path, query, fragment를 넣지 않는다). 서버가 `/v1/chat/completions`를 호출한다. |
| `QUESTION_MODEL_ID` | 빈 값 | 실제 서비스가 인식하는 모델 ID |
| `QUESTION_MODEL_TIMEOUT_SECONDS` | `60` | 모델 소켓 I/O 제한 시간(허용 범위 0 초과~300초) |
| `QUESTION_MODEL_MAX_TOKENS` | `512` | 생성 토큰 상한(16~8192) |
| `QUESTION_MODEL_MAX_RESPONSE_BYTES` | `65536` | 응답 본문 바이트 상한(1024~1048576) |
| `WORKER_LEASE_SECONDS` | `120` | 작업 lease 시간(5~3600초) |
| `WORKER_MAX_ATTEMPTS` | `3` | 최초 실행을 포함한 총 claim 횟수(1~10) |
| `WORKER_POLL_SECONDS` | `1` | 대기 중인 작업이 없을 때 확인 간격(0.1~60초) |

API와 Worker는 같은 루트 `.env`를 읽는다. `./dev`는 웹과 API만 시작하므로 질문 생성이 필요하면 별도 터미널에서 Worker를 실행한다.

```sh
cd backend
uv run --env-file ../.env --locked python -m app.intake.worker
```

한 번만 확인하려면 마지막에 `--once`를 붙인다. 이 명령의 종료 코드 0은 큐 처리 성공을 뜻하며, 모델 생성의 성공·실패는 job 상태로 확인한다. 설정이 없거나 범위를 벗어나면 Worker는 안내를 출력하고 종료 코드 1로 끝난다. API의 `GET /api/onboarding/options`는 그때 `question_generation_enabled: false`를 반환하며 생성 요청은 503이다. DB 연결 오류로 `--once` 실행이 실패해도 1을 반환한다.

## API 흐름

모든 경로에는 `/api` 접두사가 있다. 요청·응답 필드와 예시는 아래와 같다. 변경 요청은 로그인 세션의 `X-CSRF-Token`이 필요하고, 브라우저 요청의 `Origin`은 등록된 `PUBLIC_ORIGIN`과 일치해야 한다. 요청 키가 있는 두 POST는 `Idempotency-Key` 헤더도 필요하다.

| 요청 | 성공 응답 | 주요 거절 |
| --- | --- | --- |
| `POST /api/profiles/{id}/questions` | `202 JobAccepted` 또는 `200 IntakeState` | 본인 소유가 아니면 404, CSRF 실패 403, 오래된 revision·키 충돌 409, 잘못된 본문·키 422, 모델 설정 누락 503 |
| `GET /api/jobs/{id}` | `200 Job` | 없거나 타인 소유면 404, DB 장애 503 |
| `POST /api/jobs/{id}/cancel` | `200 Job` | CSRF 실패 403, 없거나 타인 소유면 404, DB 장애 503 |
| `GET /api/questions/{id}` | `200 Question` | 없거나 타인 소유면 404, DB 장애 503 |
| `POST /api/profiles/{id}/questions/{question_id}/answer` | `200 Profile` | CSRF 실패 403, 소유하지 않은 초안·질문 404, stale revision·질문 409, 선택지나 요청이 잘못되면 422, DB 장애 503 |

질문 생성 요청 본문은 다음 형태다.

```json
{"expected_revision":0}
```

질문 생성을 이어갈 수 있는 상태이고 모델 설정이 유효하면 요청은 `202`와 `{"job_id":"…","status":"queued","poll_after_ms":1000}`를 반환한다. 상태상 질문을 만들 필요가 없으면 `200`으로 현재 `IntakeState`를 반환한다. job은 `queued`, `running`, `succeeded`, `failed`, `superseded`, `cancelled` 상태를 가진다. 성공한 `Job.result.question_id`로 질문을 조회한다. 질문 결과에는 공개 질문·선택지 ID만 포함하고 내부 option binding은 포함하지 않는다.

선택 적용 요청은 다음 형태다. `selected_option_id`는 질문 응답에 포함된 ID를 그대로 보낸다.

```json
{"expected_revision":0,"selected_option_id":"질문이 반환한 선택지 UUID"}
```

선택이 확인되면 연결된 patch를 답변에 적용하고 revision을 1 올린 `Profile`을 반환한다. 선택지 ID는 사용자 입력 값을 직접 저장하는 용도가 아니다. 모델이 제안한 patch는 서버가 질문 생성 시 검증하고 저장하며, 답변 요청에서는 해당 질문에 묶인 patch만 적용한다.

## 재시도, 취소와 상태 보존

- 같은 사용자·작업 종류에서 같은 `Idempotency-Key`와 같은 본문을 다시 보내면 기록된 응답 상태 코드와 본문을 재사용한다. 같은 키로 다른 본문을 보내면 409다. 키는 프로필 삭제 시 함께 삭제된다.
- 다른 키를 써도 동일한 프로필 revision·생성 문맥에 대기 중인 job이나 준비된 질문이 있으면 재사용한다. 모델 설정이나 입력 문맥이 바뀐 요청은 이전 준비 질문을 stale 처리한다.
- 모델 생성 실패는 job을 `failed`로 끝낸다. 같은 요청 키의 응답은 재생되므로 새로 시도할 때 새 키를 쓴다. Worker 프로세스 중단이나 lease 만료로 claim이 회수될 때는 `WORKER_MAX_ATTEMPTS`까지 다시 claim하며, 이 한도는 모델 호출 실패 재시도 횟수가 아니다.
- `POST /api/jobs/{id}/cancel`은 DB의 job 결과를 취소 상태로 만들고, 이후 늦게 도착한 결과를 저장하지 못하게 한다. 이미 시작된 원격 모델/GPU 요청 자체를 즉시 중지한다고 보장하지 않는다.
- 답변 적용이나 전체 초안 `PATCH`는 기존 ready 질문을 stale 처리하고 대기·실행 중인 job을 superseded로 만든다. 질문·답변 이력은 프로필이 삭제될 때까지 보존된다. `selection_summaries`에는 슬롯마다 최신 선택만 표시하며 현재 답변과 다르면 해당 선택 라벨을 다시 표시하지 않는다.

## 미정 선택과 범위

각 질문에는 서버가 추가한 미정 선택지가 있다. 이를 고르면 서버 매핑을 적용한다. `entry_intent`는 `explore`, `field_code`는 `undecided`, 예산 슬롯은 `budget_status: "unknown"`과 `budget_krw: null`, `document_decision`은 `none`으로 저장한다. 그 밖의 슬롯은 해당 슬롯에 문자열 `"unknown"`을 저장한다. 미정 선택도 답변으로 확정되며 이후 질문 후보에서 제외된다. 질문 provider 실패를 대신하는 fallback 질문은 없다.

질문 Worker는 `intake_question` 종류만 처리하며, 추천 Worker는 `recommendation`만 처리한다. 두 Worker는 같은 물리 실행 잠금을 사용한다. claim·heartbeat·완료 저장은 짧은 DB transaction으로 처리하고, 블로킹 모델 호출 동안 DB transaction은 열어 두지 않는다. DB lease와 fencing token은 오래된 Worker가 결과를 덮어쓰지 못하게 한다. 실제 물리 GPU 중단·동시성·다른 job 종류와의 스케줄 공정성은 아직 검증하지 않았다. 자료 업로드·해석은 별도 범위다. `review_token`과 추천 제출은 [추천 작업](recommendation-jobs.md)에서 처리한다. 실제 질문 GPU 실행의 확인 범위는 아래 검증 기록을 따른다.

공백·탭·유니코드 공백만 있는 텍스트는 초안에 보존하지만 미응답으로 다룬다. 진행 정책과 생성 문맥은 같은 답변 판정 기준을 사용하며, 이런 값은 확정 답변에 넣지 않아 해당 필수 슬롯의 질문을 생성하고 선택할 수 있다.

모델 선택지와 서버 미정 선택지 모두 실제 `Answers` 스키마로 저장 전에 검사한다. 생성 단계의 검증만 통과하고 사용자가 고를 수 없는 선택지를 공개하지 않는다.

## 실제 브라우저·질문 GPU 검증, 2026-11-01

합성 `AuthStore` 서비스 세션과 빈 프로필로 시작해 실제 브라우저·API·PostgreSQL·NVIDIA L4의 `gemma4-checklist`를 연결했다. HTTP로 답변을 미리 채우지 않고 생성된 질문 여덟 개의 선택지를 화면에서 선택했다. 자료 없이 진행을 명시적으로 선택했고, 최종 확인 화면에서 여덟 선택 문구와 예산 상한 100000원을 확인한 뒤 추천을 제출했다. S0의 고정 revision 9에 여덟 질문·선택지 ID, 문구, 슬롯, 답변 revision이 저장된 binding과 일치하며 `budget_status=amount`, `budget_krw=100000`, `document_decision=none`도 유지됐음을 확인했다. 질문의 `origin`은 모두 `llm`이며 수정 전후 프롬프트 버전 이력은 그대로 보존했다.

일곱 번째 예산 질문의 최초 실패는 `question_generation_failed`였으며 원인은 미확정이다. 이후 별도의 진단 재시도에서는 `provider_generation` 단계의 `missing_budget_amount`를 확인했다. 기존 구조화 출력 스키마가 `amount`와 금액을 독립적인 선택 필드로 허용했지만 서버 검증은 허용된 미확정 금액 companion을 요구했다. `question-0.8.0-3`은 이 문맥에서 `amount`와 0 이상 정수 금액을 함께 요구하는 응답 스키마를 사용한다. `no_limit`·`unknown`의 금액 생략/명시적 null과 확정·차단 companion의 기존 처리는 유지한다. 수정 후 실제 질문에서 0원·100000원 상한을 생성했고 100000원을 선택해 입력을 이어갔다. 실패 전 답변은 보존됐으며 실패를 성공 질문이나 임의 금액으로 대체하지 않았다.

추천은 `runtime_mode=real_model`, `model_calls_performed=true`로 완료됐고 `unknown_conditions` 결과 네 개를 원자 저장했다. 파이프라인은 191223 ms, 작업은 191.273초의 단일 관측이었다. 브라우저가 저장된 결과로 자동 이동했고 실제 새로고침 뒤에도 같은 결과 URL과 네 제목을 표시했다. 두 API 조회와 DB의 공개 결과가 일치했고, 저장된 실행 입력도 S0와 일치했다. 미확인 조건은 검증된 조건으로 바꾸지 않았다.

질문 Worker는 고정 이벤트에 작업 ID, 내부 처리 단계와 허용 목록의 오류 분류만 기록한다. 예외 원문·모델 출력·프롬프트·traceback은 기록하지 않으며 공개 오류는 계속 `question_generation_failed`다. 후속 main 통합 전 전체 검사는 웹 275개, 백엔드 245개(설정에 따른 1개 skip), 실행 스크립트 22개, 카탈로그 단위 18개·통합 10개와 타입·린트·포맷·빌드를 통과했다. 실제 Google OAuth, 자료 포함 경로, 질문·추천의 의미적 품질, 실행 공정성과 일반적인 안정성은 이 검증으로 확정하지 않으며 #26·#31 전체 완료도 뜻하지 않는다.
