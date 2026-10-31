# 추천 파이프라인 S0–S9

[문서 안내](../README.md) · [작업 계획](../planning/phases.md)

최종 제출 이후에만 추천을 시작합니다. [설정](recommendation-config.json)은 측정 전 초기 제안이며, 내부 점수는 사용자 능력·취업 확률이 아닙니다.

> v0.8 원문을 주제별로 재분류했습니다. 구현 제안·미검증 표시를 유지하며, 문서 존재를 구현 완료로 해석하지 않습니다.

<!-- source: index.html#stages -->

## 추천 Pipeline · 단계별 입출력

| 단계 | 입력 → 출력 | 담당 / 완료 기준 |
| --- | --- | --- |
| S0 · freezing | 전체 확인된 프로필·자료 → immutable snapshot | 서버. 사용자·revision·자료 해시·catalog_version·as_of·설정 버전 고정. 미완료 422, 충돌 409. |
| S1 · profiling | 수락한 응답·문서 사실 → LearnerProfile / TargetIntent | 서버 + Gemma. 현재 경험, 목표 직무, JD 요구사항, 참여 제약 분리. 추출 결과에 source item ID 연결. |
| S2 · mapping_needs | 목표·현재 경험 + 검색한 NCS 참조 → LearningNeeds | 검색 + Gemma. 사용자 요청/공고 요구/참조 표준을 구분. 학습 필요·보유 자기보고·미확인 상태로 정리. |
| S3 · retrieving | 목표 용어·학습 필요 + 활성 색인 → CandidateSet | PostgreSQL 검색 + 서버 순위 융합. 실제 K-MOOC·고용24 course/offerings만 후보. NCS는 연결 경로다. |
| S4 · checking_constraints | 후보 회차·원문 조건·사용자 제약 → EligibleCandidates | 서버 규칙. pass/fail/unknown/not_applicable. 확인된 명백한 fail 제외, unknown 유지·표시. |
| S5 · assessing | 남은 후보 묶음·학습 필요·내용 근거 → AxisAssessments | Gemma. 목표·선수지식·원하는 학습 결과에 대해 구조화 평가와 근거. 근거가 없으면 null. |
| S6 · ranking | 검증된 축별 평가·조건·검색 순위 → RankedSelection | 서버. 점수 범위 계산, 동일 과정 회차 정리, 중복/기관 편중 제어, 안정적인 tie-break. |
| S7 · explaining | 선정 교육·입력 근거·교육 근거 → ExplanationDraft | Gemma. 선정된 ID만 설명. 목표와 학습내용의 연결·확인할 조건을 작성. |
| S8 · validating | 설명 초안·평가·원천 사실 → 검증된 결과 또는 실패 | 서버. ID·출처·인용·수치·조건 검사. 제한된 수정 재시도 후에도 실패하면 failed. |
| S9 · persisting | 검증된 결과·단계 기록 → RecommendationRun | 서버. 원자적 저장 후 완료. 이때 화면에 교육을 표시한다. |

한 번의 사용자 추천 요청 안에 여러 내부 단계가 있다. 내부 단계별 처리와 “첫 추천을 보여준 뒤 추가 질문하는 UX”는 별개다. 이 명세는 전체 입력 이후에만 교육 결과를 반환한다.

각 단계는 `input_hash, output_json, model_id/prompt_version, catalog_version, duration_ms, input_count, output_count, reason_counts`를 기록한다. 단계 기록은 판단 결과와 근거를 저장하며 모델의 숨은 사고 과정을 저장·표시하지 않는다.

**v0.8 입력 연결:** S0에 슬롯별 최신 선택 문구·질문 ID·선택 ID·정규화 값·수락한 보조 해석을 포함한다. S1은 선택한 문구의 구체적 제한을 보존하며 넓은 분류 코드만으로 목표나 조건을 바꾸지 않는다.

<!-- source: index.html#needs -->

## 목표·경험·NCS를 연결하는 방법

### S1 · 프로필 구조화

| 객체 | 주요 필드 / 의미 |
| --- | --- |
| LearnerProfile | self_reported_skills, experience_evidence, project_evidence, explicit_unknowns, selected_option_evidence. 문서가 언급한 기술과 사용자가 실제 할 수 있다고 확인한 상태를 분리. |
| TargetIntent | goal_text, target_role, desired_outcome, jd_requirements, importance. JD가 없으면 사용자가 말한 목표를 그대로 사용. |
| ConstraintSet | 시간·방식·지역·예산·시점과 값의 출처, 확정/미정 상태. 학습 필요 추정으로 제약을 변경하지 않음. |
| 구체적인 표현 보존 | goal_text·constraints_text에 사용자가 수락한 구체적 목표와 조건을 보존한다. 예: “주말만 가능”을 넓은 evening_weekend 분류만으로 해석해 평일 저녁까지 허용하지 않는다. 분류보다 수락한 구체적 제약이 우선한다. |
| NCS 후보 | 제공된 모듈명·능력단위명·분류·키워드에서 관련 참조를 검색. 없는 NCS 코드·세부 수행준거를 모델이 만들어 반환하지 않도록 ID whitelist 적용. |

### S2 · 학습 필요 항목 도출

- 사용자가 배우고 싶다고 말한 것과 JD의 명시된 요구사항을 목표 항목으로 만든다.

- 현재 경험 근거와 의미가 같은 항목을 연결한다. 단어 일치만으로 숙련도를 확정하지 않는다.

- NCS는 용어 정리와 연관 학습내용의 근거로 연결한다. 분류가 맞지 않거나 자료가 없으면 unmapped로 둔다.

- 각 목표 항목을 `self_reported_have / want_to_learn / unknown`으로 구분한다. 이력서에 없다는 사실만으로 부족하다고 판정하지 않는다.

- NCS에서 확장한 관련 항목은 `suggested`로 표시한다. 사용자가 확정한 목표보다 우선하거나 필수 선수지식으로 바뀌지 않는다.

| 입력 예시 | 정리 결과 | 금지되는 해석 |
| --- | --- | --- |
| “파이썬 기초를 배웠고 SQL은 처음이에요.” | Python 기초: 자기보고 경험. SQL: 처음/학습 희망. 데이터 분석 목표와 각각 연결. | Python 실무 숙련도 80점, SQL 능력 0점처럼 평가하지 않음. |
| 이력서에 SQL 언급이 없음 | SQL 경험 unknown. 관련 내용 학습의 필요 여부도 확정하지 않음. | SQL을 못 한다고 단정하지 않음. |
| NCS 표준 수준 5·훈련시간 40 | 참조 표준의 원문 메타데이터로 저장. | 사용자 수준 5, 추천 강좌 40시간으로 복사하지 않음. |
| 현재 NCS 표본에 목표 직무가 없음 | unmapped 표시 후 사용자 목표 용어로 교육 검색 계속. | NCS 부재를 이유로 목표를 임의 변경하지 않음. |

```text
LearningNeed {
  id, label, basis: user_requested | jd_required | ncs_reference,
  learner_state: self_reported_have | want_to_learn | unknown,
  source_item_ids, ncs_reference_ids,
  mapping_state: supported | suggested | unmapped
}
```

<!-- source: index.html#retrieval -->

## 검색과 후보 구성 · S3

추가된 데이터를 포함한 활성 카탈로그의 전체 색인을 검색한다. 모든 교육 원문을 매번 Gemma 프롬프트에 넣지 않는다. 기본안은 PostgreSQL과 기존 Gemma를 사용하며 별도 임베딩 모델·벡터 DB 도입을 필수로 가정하지 않는다.

| 검색 경로 | 입력과 처리 | 유의점 |
| --- | --- | --- |
| 제목·용어 | 사용자 표현과 확인된 동의어를 정규화해 title_terms 검색. | 한국어 공백·영문 표기·동의어 사전을 버전 관리. simple FTS를 한국어 의미 이해 모델로 표현하지 않는다. |
| 설명·학습내용 | 본문에서 실제 확보한 body_terms와 학습 필요 용어 검색. | 목록에 제목만 있으면 본문 근거를 만들어 채우지 않는다. |
| NCS 연결 | 관련 NCS 참조에 연결된 course_skill_links 검색. | content_supported / topic_only / suggested를 보존. 모델 연결은 실제 커리큘럼 증거가 아니다. |
| 질의 보완 | Gemma가 사용자 목표를 source vocabulary와 연결한 검색어 후보 제시. | 원문 목표와 연결 근거가 있는 확장만 사용. 질의 보완은 지역·예산·시간 제약 완화가 아니다. |
| 순위 결합 | 서로 다른 경로의 순위를 RRF로 합치고 course/회차 ID 기준 중복 정리. | 채널에 없는 문서는 해당 채널 점수 0. 한 채널이 비었다고 실패하지 않는다. 모든 경로가 비면 데이터 범위/검색어 근거를 확인해 빈 결과 분류. |

```text
RRF(c) = Σ_channel 1 / (k + rank_channel(c))
초기 설정 제안: k=60, 경로별 최대 100, 융합 뒤 내용 평가 최대 30.
반환 순서는 동일 점수일 때 source/course/offering ID로 고정.
후보 수는 고정 표본 수와 무관하며 설정 파일로 조정한다.
```

색인은 정규화한 제목·본문 용어를 `to_tsvector('simple', ...)`로 구성하고 GIN index를 사용한다. 입력 검색어는 구조화·정규화 후 parameterized query로 전달한다. 한국어 형태 변화 대응은 버전 관리하는 용어 사전과 검색 평가가 필요하다. 사용자 텍스트를 SQL이나 도구 명령으로 실행하지 않는다.

현재 기본안의 검색 품질을 실제 확장 데이터로 평가하고, 필요한 경우 후속 검색 방식을 비교한다. 이 개정에서는 새로운 임베딩 모델이나 성능 수치를 확정하지 않는다.

<!-- source: index.html#constraints -->

## 참여 조건 검증 · S4

**fail = 확인된 충돌**, **unknown = 판단할 근거 부족**이다. unknown을 탈락이나 통과로 바꾸지 않는다. “맞는 교육이 없다”는 이유로 조건을 자동 완화하지 않는다.

| 조건 | pass / fail을 판단할 근거 | unknown 처리 |
| --- | --- | --- |
| 모집·시작 일정 | 검증된 단위의 enrollment/end와 as_of, 명시된 모집상태. 현재 회차가 종료됨이 확인되면 제외. | 목록의 모집중 표시만으로 현재 신청 가능 확정 금지. 강좌 접근 가능 여부와 모집 접수 여부를 구분. |
| 시간 | 사용자 가능 시간과 실제 요일·시작/종료 시간표. 평일 주간만 가능한 강좌 vs 저녁·주말만 가능한 사용자 → fail. | 총 훈련시간 80시간만 있으면 시간표 unknown. 온라인이라는 이유만으로 비동기 수업으로 가정하지 않음. |
| 방식·지역 | 확인된 온라인/대면/혼합, 실제 교육장 위치와 사용자가 허용한 통학 범위. | 기관 주소만으로 모든 회차 교육장을 확정하지 않음. 온라인만 선택한 사용자의 통학은 N/A. |
| 예산 | 확인된 개인 부담액 또는 지원과 무관하게 적용되는 확정 수강료가 예산을 넘으면 fail. | 표시 자비부담금만 있으면 개인 최종 부담은 unknown. estimated_cost는 참고로 표시하고 근거 없는 지원금 차감 금지. |
| 자격·선수지식 | 명시된 참여자격과 사용자가 확인한 사실이 분명히 충돌할 때 fail. | 고용·학력·나이 미입력이나 조건 문구가 모호하면 unknown. 문서가 침묵한다고 불충족으로 간주하지 않음. |
| 시작 희망 | 사용자가 hard 제약으로 확인한 날짜 범위만 제외 조건. “가능하면 바로” 등 기본 선택은 soft preference. | 시작 시점 미정은 일정 검사를 생략한 unknown으로 기록. |

규칙 결과는 `field, status, reason_code, evidence_ids`로 저장한다. 예: `SCHEDULE_CONFLICT, RECRUITMENT_CLOSED, PERSONAL_PRICE_UNKNOWN, COURSE_LOCATION_UNKNOWN`. 정보 신선도가 기준 밖이면 확정값의 유효성을 낮추고 재확인 필요를 표시한다. 갱신주기는 원천별 운영 설정으로 관리한다.

<!-- source: index.html#ranking -->

## 내용 평가와 최종 순위 · S5–S6

Gemma는 같은 평가표로 후보를 비교하고, 서버가 검증된 값으로 정렬한다. 직업 성공률이나 사람의 능력을 점수화하지 않는다. 내부 점수는 교육 간 순서 결정을 위한 값이다.

| 축 | 평가 근거 | 초기 가중치 제안 |
| --- | --- | --- |
| 목표 연결 G | 확인된 목표/학습 필요와 실제 교육내용이 얼마나 직접 연결되는가. 제목만 관련되면 topic_only. | 0.45 |
| 선수지식 P | 강좌가 명시한 선수지식과 사용자가 확인한 경험의 관계. 별도 근거가 없으면 null. | 0.25 |
| 원하는 결과 O | 프로젝트·실습·협업 등 사용자가 원하는 결과가 교육 원문에 있는가. | 0.20 |
| 선호 F | 시작 시기·운영 방식 등 확인된 soft preference 일치. hard 조건 fail을 상쇄하지 못함. | 0.10 |

각 축은 `0 / 0.5 / 1 / null`이다. 1은 명확한 연결, 0.5는 부분 연결, 0은 근거로 확인되는 비연결, null은 근거 부족이다. 0과 null 모두 설명 근거를 구분해 저장한다. 특히 학습 결과가 원문에 없다는 이유만으로 “프로젝트가 없는 과정”이라고 쓰지 않는다.

```text
S_low(c) = Σ_known w_i × x_i
S_high(c) = S_low(c) + Σ_unknown w_i
coverage(c) = Σ_known w_i

정렬 키: goal_basis(content_supported > topic_only),
          S_low 내림차순, coverage 내림차순,
          미확인 참여 조건 수 오름차순, RRF 내림차순, 안정적 ID 순.
unrelated 후보는 제거. unknown은 null로 보존하며 낮은 경계 계산에만 미기여.
```

예: G=1, P=null, O=0.5, F=1이면 S_low=0.65, S_high=0.90, coverage=0.75다. 이는 “적합도 65%” 또는 “취업 성공 확률”이 아니다. 근거가 확보된 범위와 불확실성을 표시하는 내부 계산이다.

### 후보 선택과 중복 제어

- 내용과 목표가 명확히 무관한 후보를 제거한다. 관련성을 판단할 자료 자체가 없는 후보는 빈 칸을 만들어 추천 수를 채우지 않는다.

- 같은 과정의 여러 회차는 조건 통과 여부 → 위 정렬 키 → 시작 일정 → 회차 ID 순으로 하나를 선택한다. 과정 묶음의 동일성이 불명확하면 자동 합치지 않는다.

- 최대 5개, 동일 기관 최대 2개를 초기 기본안으로 둔다. 실제 시연 품질 평가 후 조정한다.

- 중복 제한 때문에 결과가 적어도 무관한 강좌를 넣거나 조건을 완화하지 않는다. 결과는 0개 또는 1개일 수 있다.

- 모든 hard 제약이 pass/N/A인 결과와 unknown이 남은 결과를 구분해 표시한다.

[정렬 키·가중치·후보 수 설정 원본](recommendation-config.json)

<!-- source: index.html#model -->

## Gemma 호출 계약과 검증 · S7–S8

| 호출 | 모델에 주는 정보 | 허용 출력 |
| --- | --- | --- |
| 질문·선택지 생성 | 확정 답변·허용 슬롯·확인된 자료 근거·질문 이력 | QuestionDraft JSON. 답변 확정·입력 완료·교육 후보 생성 금지. |
| 보조 입력 해석 | 사용자가 쓴 문장·ready 자료의 인용·허용 슬롯·현재 확정 값 | 짧은 반응, 슬롯 제안, 원문 인용, 상충 표시. 자동 확정·교육 후보 반환 금지. |
| 프로필/NCS 연결 | 확정 목표·경험·검색한 NCS 후보와 ID | 학습 필요와 근거 ID, 연결 상태. 없는 표준 코드 생성 금지. |
| 교육 내용 평가 | 같은 프로필 snapshot과 제한된 후보 묶음, 실제 교육 원문 | 후보 ID별 G/P/O 근거 평가. 같은 루브릭을 사용하고 묶음 순서가 점수 기준을 바꾸지 않음. F는 서버 계산. |
| 추천 설명 | 선정된 교육 ID·검증된 평가·조건 결과·원문 인용 | 사용자 목표와 배울 내용의 연결, 선택 이유, 확인할 조건. 새로운 과정·금액·일정 생성 금지. |

```text
CandidateAssessment {
  course_id, offering_id, goal_basis,
  axes: [{axis, value: 0 | 0.5 | 1 | null,
          evidence_ids, reason_code}],
  linked_need_ids, missing_fields
}

Explanation {
  course_id, offering_id,
  claims: [{text, user_item_ids, course_evidence_ids}],
  cautions: [{field, reason_code, evidence_ids}]
}
```

| 검증 | 실패 시 동작 |
| --- | --- |
| JSON·허용 값 | 스키마로 검사. 구조화 출력 기능을 사용하더라도 서버 검증을 생략하지 않는다. |
| ID·소유권·버전 | 반환 ID가 해당 후보·같은 카탈로그·본인 입력에 속해야 한다. 위반은 해당 단계 실패. |
| 인용 존재 | 인용이 지정된 source/chunk의 텍스트 범위에 실제 존재하는지 검사. 인용 ID만 맞고 문장 내용이 다른 경우도 거부. |
| 수치 사실 | 가격·시간·날짜는 원문 정규화 값과 대조. 생성 문장의 수치를 그대로 신뢰하지 않는다. |
| 조건 보존 | unknown을 충족으로 표현하거나 fail 후보를 되살리면 거부. |
| 의미 주장 | 최종 설명은 검증된 축·원문 단위의 제한된 표현으로 생성. 모델이 인용한 원문이 주장까지 지지하는지 평가셋/리뷰로 검증한다. 출처 ID 검증만으로 의미적 정답을 보장한다고 주장하지 않는다. |
| 실패 한도 | 초기 기본안: 검증 오류를 전달한 수정 1회. 동일 오류·기한 초과면 failed. 추론 실패를 규칙 추천 성공으로 표시하지 않는다. |

첨부 문서·강좌 본문의 명령문은 분석 데이터로 취급한다. 외부 텍스트가 SQL, URL fetch, 파일 접근, 모델의 시스템 지시를 바꾸지 못하도록 도구와 근거 경계를 둔다. 후보가 전혀 없으면 데이터 상태를 반환하며, 모델 추천을 수행하지 않았다는 `model_outcome=not_needed`를 남긴다.

<!-- source: index.html#recommendation_architecture -->

## 추천 로직 아키텍처

[SVG 크게 보기](recommendation-architecture.svg)[Mermaid 원본](recommendation-architecture.mmd)

입력 질문 생성도 같은 local Gemma를 사용한다. 완료된 사용자 입력 이후에만 기존 S0~S9 추천 처리를 실행한다.

<!-- source: index.html#recommendation_sequence -->

## 추천 요청 시퀀스

[SVG 크게 보기](recommendation-sequence.svg)[Mermaid 원본](recommendation-sequence.mmd)

자료 추출과 입력 해석은 제출 전에 완료될 수 있다. 최종 submit은 교육 추천을 시작하는 유일한 진입점이다. 재시도는 같은 입력 snapshot과 설정을 사용하고 단계별 성공 출력이 유효할 때 재사용한다.

<!-- source: index.html#recommendation_flow -->

## 분기·실패를 포함한 순서도

[SVG 크게 보기](recommendation-flow.svg)[Mermaid 원본](recommendation-flow.mmd)

| 상태 | 사용자에게 보여줄 결과 |
| --- | --- |
| matches | 확인한 참여 조건을 통과한 교육과 학습 이유. 실제 최종 등록 승인과 구분. |
| unknown_conditions | 관련 후보가 있지만 일부 조건이 미확인임을 명시. 별도 추가 질문을 붙이지 않는다. |
| empty | 관련 교육은 찾았으나 확인된 조건과 맞는 후보가 없음. 입력을 보존한다. |
| unsupported_field | 해당 목표를 다룰 데이터 근거가 부족함. 사용자 능력이나 목표의 문제로 표현하지 않는다. |
| failed | 추천 처리를 완료하지 못함. 입력·자료를 보존한 재시도. 정상 교육 카드로 위장하지 않는다. |
