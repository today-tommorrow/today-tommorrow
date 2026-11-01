# 입력 초안 저장

A의 L1 API 범위다. [Google 로그인](google-login.md)의 서비스 세션으로 본인 초안을 생성·조회·수정한다. 데이터는 `today_app.intake_profiles`에 저장하며 교육 원본은 기존 `work24_bge`에서 유지한다. ORM과 `0003_profile_drafts` migration을 함께 적용한다.

## API 계약

| 요청 | 성공 | 거절 |
| --- | --- | --- |
| `POST /api/profiles` | `201 Profile`, 새 UUID·revision 0·빈 답변 | 비로그인 401, CSRF 403 |
| `GET /api/profiles/{id}` | `200 Profile`, 저장된 답변과 진행 상태 | 비로그인 401, 없는 ID·잘못된 UUID·타인 소유 404 |
| `PATCH /api/profiles/{id}` | `200 Profile`, 전체 answers 교체·revision +1·draft | 비로그인 401, CSRF 403, 타인 404, 오래된 revision 409, 입력 422, 64 KiB 초과 413 |
| `GET /api/onboarding/options` | `200 OnboardingOptions`, 고용24 범위와 실제 지원 기능 | 비로그인 401 |

변경 요청은 `/api/auth/me`에서 받은 `X-CSRF-Token`을 보낸다. `Origin` 헤더가 있으면 등록한 `PUBLIC_ORIGIN`과 같아야 한다. 성공·오류 응답 모두 `Cache-Control: no-store`다. DB 장애는 인증 단계 또는 저장 단계에서 원문을 숨긴 503으로 응답한다.

```json
{"expected_revision":0,"answers":{"entry_intent":"explore","field_code":"undecided","experience":"unknown"}}
```

위 PATCH 성공 응답의 주요 값은 `revision:1`, `status:"draft"`, 입력과 같은 `answers`, `slot_origins.field_code:"explicit_unknown"`다. `Profile`은 기존 `web/src/types.ts` 계약의 모든 공개 필드를 반환하며 소유자 ID·DB 내부 필드는 공개하지 않는다.

PATCH의 `answers`는 일부 필드를 합치는 patch가 아니라 **현재까지의 전체 답변**이다. 빠진 키는 제거한다. 예를 들어 위 저장 후 `{"expected_revision":1,"answers":{"experience":"unknown"}}`을 보내면 답변에는 experience만 남는다. 빈 초안도 저장할 수 있다. 같은 revision에서 동시에 수정하면 한 요청만 성공하고 다른 요청은 409이며, 최신 답변과 수정 시각을 덮어쓰지 않는다.

```json
{"code":"revision_conflict","message":"다른 곳에서 입력이 변경됐습니다. 최신 초안을 다시 확인해주세요.","request_id":"서버가 생성한 요청 ID"}
```

409 뒤에는 GET으로 최신 초안을 불러온다. 자동으로 오래된 본문을 재전송하지 않는다. 잘못된 답변의 오류는 `invalid_profile_input`이며 입력 원문이나 Pydantic 내부 오류를 반환하지 않는다. 허용하지 않은 키·enum·문자열 숫자·boolean 금액·음수 revision은 거절한다. `goal_text`/`constraints_text` 등 짧은 텍스트는 2,000자, `self_description`은 6,000자까지 받는다. null 허용 필드와 미전송을 구분한다.

## 현재 완료 정책(D02)

작성 중에는 미응답과 조건부 필수 누락을 허용하고 `missing_slots`로 표시한다. `unknown`, `undecided`는 명시적으로 답한 미정이며 반복해서 미응답으로 다루지 않는다.

| 구역 | 기본 슬롯 | 추가로 필요한 경우 |
| --- | --- | --- |
| 방향 | entry_intent, field_code | other 분야는 custom_field |
| 경험 | experience | basic/project/work는 previous_gap |
| 목표 | learning_goal | goal_known 진입은 goal_text |
| 조건 | availability, delivery_mode, budget_status, start_timing | offline/hybrid는 commute_region, amount는 budget_krw(0 허용) |

비적용 하위 슬롯은 완료 판정과 응답의 `unresolved_conflicts`에서 제외한다. 예를 들어 경험을 beginner로 바꾸면 previous_gap의 기존 재확인은 현재 진행을 막지 않는다. 답변과 재확인 이력은 DB에 보존하며, 부모 답변을 다시 바꿔 해당 슬롯이 적용되면 아직 확인하지 않은 재확인이 다시 나타난다. 문서 선택은 별도로 필요하며 `document_decision:none`은 자료 없는 경로다. 자료를 제공한다고 선택해도 실제 처리 완료로 표시하지 않는다.

분야를 바꾸면 기존 경험·목표 답변을 보존하고 `unresolved_conflicts`로 재확인을 요구한다. 같은 전체 답변을 다시 보내는 것으로 재확인을 해제하지 않는다. 관련 키를 제거해 다시 선택하거나 다른 값을 명시하면 해당 재확인이 해제된다. 선택형 질문과 답변 적용은 [질문 API·Worker](question-jobs.md)에서 이 revision 계약과 연결한다.

Question generation is described in [question jobs](question-jobs.md). Text registration is available as described in [text documents](text-documents.md). Registered documents appear in `documents`; their semantic analysis is not yet available and `latest_interpretation_id` remains null. A profile with documents has no review token, and it cannot switch to `document_decision:none`. Document-free completed profiles receive a revision-bound review token. `selection_summaries` shows matching current question selections. GET does not invoke a model or create questions.

## 다시 열기(D06)

현재 화면의 `/onboarding?profile=<UUID>`와 `sessionStorage`의 사용자별 ID를 사용한다. 새로고침이나 재로그인 후 동일 계정·동일 서버 DB라면 같은 URL로 초안을 다시 열 수 있다. 별도 ID 없이 매번 POST하면 새 초안을 만든다.

기기 간 최근 초안을 자동으로 찾는 API·초안 목록은 이번 범위에 없다. 다른 브라우저는 URL을 알아야 하며 계정은 같아야 한다. 각 PC의 독립된 로컬 DB 사이로 초안을 복제하지 않는다. sessionStorage는 해당 탭 범위이며 지워질 수 있으므로 중요한 초안은 URL로 다시 연다.

## 검증과 한계

`./setup`으로 migration을 적용하고 `./dev` 실행 중 다른 터미널에서 `./check`를 실행한다. 기본 주소는 Google 가이드와 같은 `127.0.0.1:5173`이다. API 포트만 바꾸면 두 명령의 `API_TARGET`을 동일하게 지정한다.

백엔드 시험은 실제 로컬 PostgreSQL과 합성 서비스 사용자·세션을 사용한다. 생성·저장·새 요청 조회, 타인 404, CSRF, 만료, 동시 수정 200/409, 입력 검증·분야 재확인을 확인한다. 합성 세션은 시험에서만 직접 발급하며 일반 실행에 테스트 로그인 경로를 추가하지 않는다.

실제 Google 계정으로 로그인한 뒤 브라우저에서 초안 생성·새로고침·재진입하는 확인은 별도로 필요하다. 질문을 통한 전체 입력, 실제 자료, 추천과 GPU 검증도 이 API 저장 시험에 포함되지 않는다. downgrade는 기존 초안을 삭제하므로 사용 중인 DB에서 재현 명령으로 실행하지 않는다.
