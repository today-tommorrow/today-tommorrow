# 교육 원천·정규화·추가 배치

[문서 안내](../README.md) · [작업 계획](../planning/phases.md)

K-MOOC·고용24는 교육 후보, NCS는 학습내용 참조입니다. [적재 계약](ingestion-contract.json)을 사용하며 실제 원천 데이터는 이 저장소에 포함하지 않습니다.

> v0.8 원문을 주제별로 재분류했습니다. 구현 제안·미검증 표시를 유지하며, 문서 존재를 구현 완료로 해석하지 않습니다.

<!-- source: index.html#data -->

## 세 원천과 추가 데이터 계약

| 원천 | 실제 제공 형식 | 추천에서 맡는 역할 |
| --- | --- | --- |
| K-MOOC | 목록 `response.items`, 상세 `response.results`. `id`로 결합. | 실제 온라인 교육 후보·일정·설명. 목록과 상세를 별도 교육으로 세지 않는다. |
| 고용24 | `records[]`. `course_name, provider_name, region, start_date, end_date, training_hours, tuition_krw, listed_self_pay_krw, curriculum, eligibility`. | 실제 훈련 과정·회차 후보. null 필드가 후속 배치에서 채워져도 동일 adapter 계약으로 처리. |
| NCS | `datasets[].rows`. 학습모듈: `학습모듈명, 분류(소>세), 주요 키워드`. 훈련기준: `능력단위(과정), 수준, 훈련시간, 훈련방법, 시설`. | 목표와 학습내용을 같은 용어로 연결하는 참조. 모집 과정·기관·회차·수강료를 생성하지 않는다. |

기존 K-MOOC 20행·고용24 10행·NCS 20행은 **형식과 결측 패턴을 확인한 표본**이다. 추천 수, 검색 상한, 분야 범위, 전체 카탈로그 건수의 근거로 사용하지 않는다. 새 배치의 실제 배열 길이와 정규화 결과로 건수를 계산한다.

| 입력 필드/상황 | 정규화 규칙 |
| --- | --- |
| K-MOOC summary | HTML 본문·미디어 위치를 보존하고 안전하게 텍스트 추출. 이미지나 홍보 영상만 있으면 커리큘럼 미확인으로 둔다. |
| K-MOOC 시간 숫자 | 원문과 단위를 함께 관리. 단위 검증 전 enrollment/study/course_playtime을 임의 변환하거나 수강 가능 시간으로 해석하지 않는다. |
| 고용24 ID·회차 없음 | 내부 UUID 부여. 기관·과정명·지역·시작/종료의 보수적 fingerprint로 중복 후보 식별. 원본 행 번호는 출처 위치이며 전역 ID가 아니다. 충돌한 회차를 자동 합치지 않는다. |
| 표시 자비부담금 | 개인별 최종 부담금과 분리. 확인되지 않은 지원금 차감을 하지 않는다. 금액 미확인은 무료가 아니다. |
| NCS 코드·분류 없음 | 실제 제공된 코드만 사용. 내부 식별자와 표준 코드를 구분. 이름이 같다고 학습모듈과 훈련기준을 같은 실체로 강제 결합하지 않는다. |
| 누락·추가 필드 | null·0·해당없음을 구분. 기존 필수 배열이 깨지면 배치 격리. 새 선택 필드는 raw_json에 보존하고 adapter 변경 여부를 보고한다. |
| 수집 방식 | API·MCP·공식 다운로드·공개 웹 크롤링 등 실제 수집 방법과 원 URL·파일 위치를 source_records에 보존한다. 이번 배포에서 모든 원천을 실시간 API로 다시 수집할 수 있다고 가정하지 않는다. |
| 배치 manifest | 원본 payload 필드는 바꾸지 않는다. 별도 manifest에 source_system, source_schema_version, adapter_version, file hash, collected_at, partial/full, coverage를 기록한다. |
| 부분 배치 | 기존 데이터와 합친 새 draft 스냅샷을 만든다. 이번 배치에 없다는 이유로 기존 과정을 삭제하거나 마감시키지 않는다. 전체 배치도 명시된 coverage 안에서만 교체한다. |
| 이월 데이터의 출처 | 변경되지 않은 원본·정규화·근거를 새 버전에 복사할 때 원 수집 시각·파일 해시를 보존하고 validation_report_json에 이전 배치/레코드 ID 매핑을 기록한다. 복사 시각을 새 수집 시각으로 표시하지 않는다. 수집 시각이 없으면 null이다. |
| 활성화 | 원본·정규화·출처·중복·색인 검사 후 active catalog pointer를 짧은 트랜잭션으로 교체한다. 이전 추천은 이전 카탈로그 버전을 계속 참조한다. |
| 개인 파일·링크 | 사용자 경험/JD를 이해하기 위한 입력이다. 교육 카탈로그의 네 번째 원천으로 취급하거나 다른 사용자 검색 색인에 넣지 않는다. |

[필드 매핑·manifest 계약](ingestion-contract.json)

[SVG 크게 보기](data-ingestion.svg)[Mermaid 원본](data-ingestion.mmd)
