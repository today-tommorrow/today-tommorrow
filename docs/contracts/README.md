# HTTP API와 TypeScript 계약

[문서 안내](../README.md) · [작업 계획](../planning/phases.md)

[OpenAPI YAML](openapi.yaml) · [OpenAPI JSON](openapi.json) · [TypeScript 타입](api-types.ts). OpenAPI servers=/api이므로 paths와 합쳐 /api/...로 호출합니다. 명세상 27개 경로·30개 연산·53개 스키마이며 실제 구현 완료 수가 아닙니다.

> v0.8 원문을 주제별로 재분류했습니다. 구현 제안·미검증 표시를 유지하며, 문서 존재를 구현 완료로 해석하지 않습니다.

<!-- source: index.html#api -->

## 질문 생성·선택 확정 API

**27개 경로 · 30개 연산.** 질문 생성·조회·선택 확정 API 3개 추가. 기존 health·Google OAuth·자료·최종 추천 계약 유지.

[OpenAPI YAML](openapi.yaml)[JSON](openapi.json)[TypeScript 타입](api-types.ts)

```text
POST /api/profiles/{profile_id}/questions
{ expected_revision }
→ 202 JobAccepted 또는 200 IntakeState

GET /api/jobs/{job_id}
→ completed이면 result.question_id

GET /api/questions/{question_id}
→ 검증된 질문 + 선택지 ID/문구

POST /api/profiles/{profile_id}/questions/{question_id}/answer
{ expected_revision, selected_option_id }
→ Profile(revision+1, intake_state, selection_summaries)

완료 전: 다음 질문 생성 또는 필요한 자료 확인
완료 후: 전체 확인 → 기존 POST /profiles/{profile_id}/submit
```

v0.7 계약에서 Profile.next_step은 intake_state로 대체한다. Interpretation.suggested_step은 suggested_slots로 대체하며, 직접 입력·자료 해석은 보조 경로다. 정상 첫 진입과 매 선택 후 질문은 새 questions API가 담당한다.

| API | 역할 | 계약 |
| --- | --- | --- |
| GET /api/health | 기존 실제 DB 연결 확인 유지 | 기존 main.py 계약 유지. SELECT 1 성공 200. 실패 503 {detail: database_unavailable}. 일반 신규 API 오류 포맷과 구분. |
| GET /api/auth/google/start | Google 로그인 시작 | 서버에서 일회용 state/nonce와 브라우저 바인딩 생성. Google OAuth authorization code flow, openid email profile. 성공 후 /onboarding으로 이동. 임의 외부 return URL을 받지 않는다. |
| GET /api/auth/google/callback | Google 인증 후 계정과 서비스 세션 생성 | state·브라우저 바인딩·일회성·만료 검증, 서버에서 code 교환, Google 서명/iss/aud/exp/nonce 확인. google_sub로 users upsert. 성공 Location=/onboarding, 사용자 취소 Location=/login?error=oauth_cancelled. code·토큰을 브라우저 응답 본문/로그에 노출하지 않는다. |
| GET /api/auth/me | 로그인 사용자와 CSRF 토큰 조회 |  |
| POST /api/auth/logout | 현재 로그인 세션 종료 | 현재 세션 폐기와 쿠키 만료. 계정·입력 초안·추천 기록은 삭제하지 않는다. |
| GET /api/onboarding/options | 질문 선택지·파일 지원 범위 조회 |  |
| POST /api/profiles | 로그인 사용자 입력 초안 생성 | users.id가 소유자. 익명 세션 생성 API는 없다. |
| GET /api/profiles/{profile_id} | 입력 초안과 자료 처리·확인 상태 조회 | 초안·자료·확인 항목과 서버가 계산한 intake_state를 조회. 질문 본문은 IntakeQuestion에서 조회한다. GET은 Gemma를 호출하지 않는다. review_token은 모든 적용 항목 완료·상충 해소·자료 확인 후에만 발급한다. |
| PATCH /api/profiles/{profile_id} | 입력 응답 저장 | answers는 현재까지의 전체 응답 묶음. 버전 일치 시 revision+1, status=draft, 진행 추천 job은 superseded. 자료 처리 job은 해당 자료가 바뀌거나 삭제되지 않는 한 계속 실행한다. 작성 중에는 빈 항목을 허용하고 제출 시 완결성을 검증. 이미 입력한 내용은 유지. 분야 변경 시 관련 경험 항목을 재확인 표시하되 업로드 원본을 자동 삭제하지 않는다. 자유문장 해석을 자동 확정하지 않는다. 명시적 직접 수정이 수락한 해석보다 우선한다. 이전 해석 작업은 stale 처리하고 미정은 같은 내용으로 반복 질문하지 않는다. 질문 생성 job과 ready 질문도 stale 처리한다. 미정 답변은 unanswered와 구분하고 재질문하지 않는다. |
| PATCH /api/profiles/{profile_id}/review-items | 입력 완료 전 추출 내용 수정 | 원문 인용·document/chunk ID는 서버가 생성하고 클라이언트 수정 불가. 항목 상태와 표시 라벨만 수정. revision+1. 결과 화면에서 추가 질문하지 않는다. |
| POST /api/profiles/{profile_id}/documents/files | 이력서·JD 등 파일 업로드 후 추출 작업 시작 | PDF(스캔 OCR 포함), DOCX, TXT, PNG/JPG 지원을 구현 기본안으로 둔다. 파일 1개씩 multipart, 복수 자료는 반복 등록. 실제 허용형식·한도는 options에서 조회. 저장 위치는 GPU 서버 비공개 업로드 디렉터리. 확장자/MIME/실제 내용 검증, 본문 스트리밍 제한. 업로드마다 revision+1. |
| POST /api/profiles/{profile_id}/documents/links | 공개 링크 본문 수집 후 추출 작업 시작 | 공개 채용공고·이력서·포트폴리오 HTML 또는 지원 문서 링크를 실제 서버에서 읽는다. URL 문자열만 모델에 넘기는 방식이 아니다. HTTP(S)만, 내부/loopback/link-local/metadata 주소 차단, DNS와 연결 IP 및 redirect 매 단계 재검증. 제한 초과·로그인벽·수집 차단은 실패 상태와 같은 입력 단계의 파일/텍스트 대체 제공. Google 로그인은 Drive 파일 권한을 부여하지 않는다. |
| POST /api/profiles/{profile_id}/documents/texts | 직접 입력한 텍스트 분석 | 파일·링크와 동등한 입력 경로. 자료가 없는 사용자는 이력/경험을 직접 적거나 문서 없음으로 진행할 수 있다. |
| GET /api/documents/{document_id} | 자료 처리 결과·추출 미리보기 조회 |  |
| DELETE /api/documents/{document_id} | 선택 자료와 파생 추출 내용 제거 | 소유권·expected_revision 확인. 원본/텍스트/청크와 프로필의 해당 출처 항목 정리, 관련 진행 작업 취소, revision+1. 기존 자료 기반 추천은 stale 표시. 세션이나 다른 입력은 유지. |
| POST /api/documents/{document_id}/retry | 파일·링크 추출 재시도 | 실패 자료를 같은 입력 단계에서 재처리. 성공 자료를 재수집하려면 revision 변경과 사용자 확인을 다시 거친다. |
| POST /api/profiles/{profile_id}/submit | 입력 완료를 확정하고 교육 추천 실행 | 유일한 교육 추천 진입점. 적용되는 슬롯이 직접 입력/수락/미정/없음으로 완료되고 상충이 해소되었는지 검사. 자료 없음이면 0개, 있음이면 1개 이상 모두 ready. review_token·revision·자료 해시 검증 후 사용자 정보·카탈로그·설정·as_of를 고정한다. profiling→mapping_needs→retrieving→checking_constraints→assessing→ranking→explaining→validating→persisting을 실행. 중간 교육 노출과 결과 후 추가 질문은 없다. 동일 키·본문은 동일 job. 입력 누락 422, stale/자료 미완료 409. |
| GET /api/jobs/{job_id} | 자료 처리 또는 추천 진행 상태 조회 | 본인 소유 문서·질문 생성·보조 입력 해석·추천 작업의 상태. intake_question은 generating_question→validating_question→completed/failed. 성공 result.question_id로 검증된 질문 조회. JSON 토큰을 화면에 스트리밍하지 않는다. 추천 완료 전 교육 후보는 반환하지 않는다. |
| GET /api/recommendations/{run_id} | 완료된 추천·입력 요약·근거 조회 | 완료된 추천, 입력 요약, 학습 필요 항목, 단계별 처리 요약, 근거와 조건 상태를 반환. 사용자용 UI에는 역량 점수나 취업 확률로 내부 순위 점수를 표시하지 않는다. 후보 없음은 model_outcome=not_needed/explanation_mode=none으로 구분하며 추론 실패는 성공 추천으로 대체하지 않는다. |
| GET /api/courses/{course_id} | 추천 교육의 상세와 출처 조회 |  |
| GET /api/courses/compare | 선택한 교육 비교 |  |
| GET /api/me/saved-courses | 계정의 관심 교육 조회 |  |
| PUT /api/me/saved-courses/{course_id} | 관심 교육 저장 |  |
| DELETE /api/me/saved-courses/{course_id} | 관심 교육 저장 해제 |  |
| POST /api/profiles/{profile_id}/interpretations | 입력 문장과 ready 자료의 해석 제안 생성 | 보조 경로: 선택지 대신 직접 적은 문장 또는 ready 자료에서 해석 제안 생성. 초기 글쓰기를 요구하지 않는다. 질문 맥락이 있으면 question_id를 전달. 원문 인용과 서버 허용 슬롯을 검증하고 수락 전 자동 반영하지 않는다. 빈 text라도 ready_document_ids가 있으면 허용. 둘 다 없으면 422. stale 입력은 기존 규칙대로 거부한다. |
| GET /api/interpretations/{interpretation_id} | 입력 해석과 다음 입력 제안 조회 | 본인 소유 보조 입력 해석. suggested_slots는 참고이며 완료 판정과 다음 질문은 서버 정책 및 질문 생성 job이 담당한다. |
| POST /api/profiles/{profile_id}/interpretations/{interpretation_id}/apply | 사용자가 수락한 해석만 초안에 반영 | expected_revision=base_revision=현재 revision과 자료 해시 일치, 제안 소유권·서로소 accepted/rejected 검증. 양쪽 배열은 해당 해석의 모든 제안 ID를 정확히 분할한다. 미결정은 적용 전 UI에서 해결한다. 트랜잭션으로 수락 값만 반영하고 revision+1. 원문은 수정하지 않는다. 동일 Idempotency-Key 재전송은 원 응답, 다른 본문은 409. 해석 결과의 임의 값·ID를 클라이언트가 추가할 수 없다. 적용 후 현재 질문도 stale 처리한다. intake_state를 반환하며 필요할 때 질문 생성 API를 별도로 호출한다. |
| POST /api/profiles/{profile_id}/questions | local Gemma로 다음 질문과 선택지 생성 | 로그인 후 첫 질문에도 사용. 서버가 확인된 답변·자료 해시·미완료 슬롯·이전 질문 의미를 조회해 intake_question job 생성. 동등 context_hash의 ready 질문/진행 job 재사용. 부작용 없는 화면 조회나 재렌더링으로 새 질문을 만들지 않는다. 모델의 QuestionDraft를 검증한 뒤 ID를 붙여 저장. 수집할 슬롯이 없거나 자료 처리가 필요하면 200 IntakeState. 답변은 이 호출에서 변경하지 않는다. |
| GET /api/questions/{question_id} | 검증·저장된 질문과 선택지 조회 | 본인 소유 질문만 반환. 생성 원문 patch와 내부 프롬프트는 제외. React는 기존 제목·카드·버튼에 plain text로 렌더링. stale 질문은 선택 불가. origin=fallback이면 표준 질문임을 표시한다. 교육 후보와 course URL은 포함하지 않는다. |
| POST /api/profiles/{profile_id}/questions/{question_id}/answer | 표시된 선택지 ID로 답변 확정 | 서버에 저장된 question+option binding만 사용한다. 소유권·ready·base_revision=expected_revision=현재 revision·자료 해시를 원자적으로 확인하고 해당 answer_patch만 적용한다. 질문 answered, profile revision+1, 다른 ready 질문 stale. 이력 저장 후 intake_state를 재계산한다. 동일 Idempotency-Key와 본문은 원 응답, 다른 선택지는 409. 선택지 문자열·임의 patch는 받지 않는다. 초안 저장 후 다음 질문 생성 실패는 이미 저장한 답변을 롤백하지 않는다. |
