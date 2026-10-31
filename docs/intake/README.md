# 질문 생성·답변 확정·보조 입력

[문서 안내](../README.md) · [작업 계획](../planning/phases.md)

서버가 완료를 판정하고 저장된 선택지 바인딩만 적용합니다. [모델 JSON 계약](intake-question-contract.json), [API 계약](../contracts/README.md), [미결정 사항](../planning/decisions.md)을 함께 읽으세요.

> v0.8 원문을 주제별로 재분류했습니다. 구현 제안·미검증 표시를 유지하며, 문서 존재를 구현 완료로 해석하지 않습니다.

<!-- source: index.html#question_contract -->

## 질문 생성 JSON·서버 검증·선택 확정

| 경계 | 계약 |
| --- | --- |
| 모델 입력 | 확정 답변·선택 이력·확인된 문서 근거, eligible_slots와 companion 허용 목록, 이미 완료/미정인 슬롯, 정책·프롬프트·스키마 버전. 전체 원본을 매번 넣지 않는다. |
| Gemma 출력 | QuestionDraft: schema_version, target_slot, acknowledgement, question, helper_text, selection_mode, options[]. 각 선택지는 key·label·description·answer_patch. |
| 서버 부가 정보 | 검증 후 question_id와 option UUID를 부여하고 base_revision·context_hash·자료 해시·원문 선택지·값 바인딩을 저장한다. 모델이 사용자 ID·DB ID·revision을 발급하지 않는다. |
| 프론트 응답 | IntakeQuestion의 제목·선택지 ID/문구만 기존 컴포넌트에 전달. answer_patch·프롬프트는 클라이언트 응답에서 제외. plain text로 표시한다. |
| 선택 제출 | expected_revision + selected_option_id만 전송. 서버는 저장된 바인딩을 조회해 원자적으로 적용한다. 클라이언트가 임의 답변 patch를 만들지 않는다. |
| 전체 확인과 추천 입력 | Profile.selection_summaries에 슬롯별 최신 선택 문구를 제공한다. S0 snapshot에는 정규화 값과 해당 선택 문구·질문/선택 ID를 함께 고정한다. “주말만 가능”을 evening_weekend 코드만으로 넓혀 해석하지 않는다. |

### Gemma 구조화 출력 예시

확인된 데이터 분석·기초 경험을 바탕으로 학습 목표를 묻는 예시다. 처음부터 아래 문장을 사용자에게 작성시키는 방식이 아니다.

```text
{
  "schema_version": "1.0",
  "target_slot": "learning_goal",
  "acknowledgement": "파이썬 기초와 예제 경험을 알려주셨어요.",
  "question": "배운 내용을 활용해서, 이번에는 무엇을 완성하고 싶나요?",
  "helper_text": "마음에 가까운 결과를 골라주세요.",
  "selection_mode": "single",
  "options": [
    {
      "key": "analysis_project",
      "label": "직접 고른 데이터로 분석 결과물 만들기",
      "description": "질문을 정하고 분석 결과를 설명하는 경험",
      "answer_patch": {
        "learning_goal": "portfolio",
        "goal_text": "직접 고른 데이터로 분석 결과물을 만들고 싶다"
      }
    },
    {
      "key": "foundation",
      "label": "예제를 보지 않고 기초 코드를 작성하기",
      "description": "아직 헷갈리는 기초를 내 것으로 정리",
      "answer_patch": {
        "learning_goal": "foundation",
        "goal_text": "예제 없이 기초 코드를 작성하고 싶다"
      }
    },
    {
      "key": "team_project",
      "label": "다른 사람과 역할을 나눠 프로젝트 완성하기",
      "description": "코드와 결과를 함께 정리하는 경험",
      "answer_patch": {
        "learning_goal": "collaboration",
        "goal_text": "역할을 나눠 데이터 프로젝트를 완성하고 싶다"
      }
    }
  ]
}
```

[입력 문맥·JSON Schema·출력 예시 전체](intake-question-contract.json)

### 검증과 답변 확정

| 검사 | 처리 |
| --- | --- |
| 구조와 크기 | Pydantic/JSON Schema로 허용 키·타입·enum·문자 길이·선택지 수·중복 key를 검사. 엔진의 구조화 출력 기능을 사용해도 서버 검증 유지. 해당 런타임의 JSON Schema 지원 범위는 구현 때 확인. |
| 수집 범위 | target은 eligible_slots에 있어야 한다. patch는 그 target과 허용된 companion만 변경하며 target을 실제로 채워야 한다. 관련 없는 기존 값을 몰래 덮어쓰거나 의미 없는 빈 patch를 허용하지 않는다. |
| 의미와 근거 | 질문·반응 문구는 확인된 사실만 사용. 선택지 label과 patch가 같은 답변을 표현해야 한다. 규칙 검사만으로 의미 일치를 보장한다고 주장하지 않고, 평가셋에서 별도로 검수한다. |
| 구체적 값 | 금액 선택은 budget_status와 budget_krw를 함께 표시·적용. other 분야는 custom_field 필요. 미정은 explicit_unknown이며 추천 정확도를 위한 필수 추정으로 대체하지 않는다. |
| 동시 수정 | question base_revision·요청 revision·현재 profile revision과 자료 해시가 일치해야 적용. 답변 변경·자료 변경·직접 입력 수락 시 기존 질문 stale. 409에서 최신 입력 보존. |
| 중복 요청 | 생성은 context_hash와 정책/모델 버전으로 재사용. 같은 질문을 새로고침할 때 다시 생성하지 않는다. 선택은 Idempotency-Key로 한 번만 적용. |
| 장애 복구 | 초기 제안: JSON·검증 오류에 수정 1회. 이후에는 실패를 표시하고 재시도 또는 origin=fallback인 표준 선택형 질문으로 진행. 기본 질문을 Gemma 응답으로 가장하지 않는다. 추천 추론 실패에는 이 대체 경로를 적용하지 않는다. |
| 입력 응답성 | 선택 저장과 다음 질문 생성을 분리한다. 생성 대기 중 이전 답변은 보존. 실제 완료 전 JSON 조각을 화면에 출력하거나 가짜 진행률을 만들지 않는다. |

### 허용되는 companion 예시

field_code → custom_field/interest_activity, learning_goal → goal_text, budget_status → budget_krw처럼 하나의 선택에 명시된 부가 값만 허용한다. 구체적 시간·방식 제한은 선택 문구와 슬롯별 이력을 함께 보존해 추천 제약에 사용한다. 자동 생성된 선택지를 아직 고르지 않았는데 사용자 사실로 저장하지 않는다.

[SVG 크게 보기](intake-sequence.svg)[Mermaid 원본](intake-sequence.mmd)

<!-- source: index.html#implementation -->

## 구현 단위와 완료 기준

```text
backend/app/intake/
  policy.py       필수 슬롯·미정·의존성·완료 판정
  context.py      확정 답변·선택 이력·자료 근거
  questions.py    Gemma 호출·질문/선택지 JSON 계약
  validate.py     구조·허용 값·중복·근거 검사
  answers.py      선택지 바인딩·소유권·revision·원자적 저장
  interpret.py    보조 자유문장·자료 해석
web/src/features/onboarding/
  QuestionView    기존 제목·선택 카드·버튼 렌더러
  QuestionState   생성 대기·실패·stale·재시도
  ReviewView     구체적 선택 문구·자료·전체 확인
```

| 검증 대상 | 완료 기준 |
| --- | --- |
| 첫 진입 | 빈 글쓰기 칸을 요구하지 않고 선택 카드로 시작. 채팅 UI로 바뀌지 않음. |
| 맥락 반영 | 같은 목표 슬롯이라도 데이터 분석/웹 개발, 초보/프로젝트 경험에 따라 질문과 선택지 내용이 달라짐. 알려주지 않은 경험을 단정하지 않음. |
| 구조화 출력 | 실제 local Gemma가 QuestionDraft를 생성. JSON 유효율·수정률·허용 슬롯 위반·문구/값 의미 일치를 평가. fixture를 모델 호출 성공으로 계산하지 않음. |
| 질문 범위 | 서버가 필요한 정보가 모두 처리됐는지 확인. 미정과 완료된 항목 재질문 없음. 3개 질문으로 강제 종료하지 않음. |
| 선택 확정 | 임의 option_id/다른 사용자/오래된 revision 거부. 중복 클릭은 한 번 적용. 생성된 미선택 후보를 사실로 저장하지 않음. |
| 이력과 수정 | 뒤로 가기·직접 수정·자료 추가 후 오래된 질문 무효화. 분야 변경 시 관련 경험·목표 재확인, 원본 자료 보존. |
| 자료와 보조 입력 | 선택형 흐름 안에서 실제 파일/링크 추출과 사용자 확인 가능. 글쓰기는 선택 사항. |
| 장애 | 질문 생성 실패에도 입력 보존. 표준 질문 경로는 구분 표시. 실패한 추천을 규칙 추천 성공으로 대체하지 않음. |
| 최종 추천 | 교육은 전체 확인·제출 이후에만 표시. K-MOOC·고용24 후보와 NCS 참조, 기존 S0~S9·정렬·근거 검증 유지. |

기존 데이터 적재·검색 품질·순위 품질·조건 검사·자료 처리·OAuth·L4 성능 검증도 그대로 필요하다. 이번 정적 시안은 구조화 JSON fixture로 UI 동작을 보여주며 실제 모델 추론·서버 통합은 별도 구현이다.
