# Work24 검색 결과 원천 대조 사후 판정

PR #80의 첫 실제 결과를 고정한 뒤 상위 3개씩 60쌍을 원천과 대조했다. 두 독립 모델 검토가 모두 같은 판정을 내리고 발췌가 원천에 존재하는 경우만 관련/무관으로 반영했다. 결과는 **관련 13, 무관 15, 미판정 32**다. 표본 밖 540쌍을 포함한 전체 600쌍에서는 미판정이 572쌍이다.

이것은 사후 표본 진단이다. 원래 평가셋과 Recall/MRR은 바꾸지 않았다. 원천 의미를 검토한 주체는 에이전트 두 개이며 사람 전문가 판정이나 비편향 정답셋으로 주장하지 않는다. 입력 분리와 독립 실행은 이번 작업의 절차 기록이며, 보존된 판정 파일 자체가 검토자의 사전 노출 여부를 증명하지는 않는다.

## 고정 입력과 판정 절차

- 결과 해시: `109da31886fd783ac61ee2c0ccd31da8902dfe3427dbafbe4bda105b2d52bc5d`.
- 평가셋 해시: `597133d3c63833e7d7ab1d2e73648c0f7b2984db63726bed0f7c051ebae25ca5`.
- 원천 과정 파일: `f342acab6b2ee02824b09820c40f4cd736c1ceb5b13aca1d00634308c3f34a7b`.
- 원천 단위 파일: `9f3557f2b7f1e870d540e9a9805b30b9d8ceea9d93579d458e9f87dd81b2c90b`.
- 표본 선정: 기존 20개 질의에서 상위 3개를 선택했다. 상위 결과에 편향된 표본이며 무작위 전체 카탈로그 표본이 아니다.
- 검토 입력: 순위·점수·기존 정답 라벨을 제거하고 질의/과정 ID를 해시 순서로 섞었다. 검토자는 `gpt-6.1-sol`과 `gpt-6.1-terra`, 각각 독립 실행했다.
- 판정: 명시적 학습 목표/비추정 내용이 핵심 질의와 일치하면 관련, 다른 학습 대상을 가리키는 근거가 명확하면 무관, 나머지는 미판정. 키워드 부재나 추정 NCS만으로 무관/관련을 확정하지 않았다.
- 두 검토의 의견이 달랐던 10쌍은 모두 미판정이다. 같은 미판정 22쌍과 합해 표본의 미판정은 32쌍이다.
- 모든 근거의 과정 ID·원천 파일 해시·발췌 문자열을 실제 원천과 대조했다. 이는 발췌 존재 검증이며 의미 판정의 객관적 진실이나 원천 재배포 허락을 입증하지 않는다.

## 결과와 한계

| 범위 | 반환 쌍 | 판정 완료 | 관련 | 명시적 무관 | 미판정 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 상위 1 | 20 | 15 | 12 | 3 | 5 |
| 상위 5 | 100 | 28 | 13 | 15 | 72 |
| 상위 10 | 200 | 28 | 13 | 15 | 172 |
| 상위 30 | 600 | 28 | 13 | 15 | 572 |

상위 3개 전체 표본에서 15개의 명시적 무관 결과가 확인됐다. 이 값은 이 표본에 대한 부정 결과 수이며 전체 검색의 오탐률 또는 모든 관련 교육의 정밀도가 아니다. 상위 1개에는 관련 12·무관 3·미판정 5가 있다. 미판정과 표본 밖 결과를 어느 방향으로도 임의 보정하지 않았다.

천문 관측·고문서 복원 질의의 상위 3개씩은 모두 무관으로 판정됐다. 해당 과정들의 교과 목표가 각각 다른 학습 과제라는 근거다. 이것만으로 코퍼스 전체에 관련 과정이 없다고 단정하거나 새 임계값을 정하지 않는다.
개인 세무·법인 세무처럼 경계가 모호한 항목과 다른 제목의 대체 과정은 검토 의견이 엇갈리거나 근거가 부족하면 미판정으로 남겼다. 비용·일정 조건은 원천에 없을 때 만족한다고 간주하지 않았다.

## 산출물과 재현

- [원천 대조 및 양쪽 독립 판정 기록](review-audit.json)
- [600쌍 제출 파일](submission.json): 표본 밖은 명시적 미판정이다.
- [진단 JSON](diagnostics.json) 및 [도구 생성 보고서](diagnostics.md)

아래 명령은 저장된 결과에 대한 검토만 실행하며 GPU/DB/네트워크를 사용하지 않는다. 출력 경로는 매번 새 이름을 사용한다.

```sh
cd backend
python -m app.catalog.review export \
  --result ../docs/evaluations/work24-bge-2026-11-01-live.json \
  --output-json /tmp/work24-source-packet.json \
  --output-review /tmp/work24-unjudged-template.json \
  --output-report /tmp/work24-source-packet.md
python -m app.catalog.review import \
  --result ../docs/evaluations/work24-bge-2026-11-01-live.json \
  --packet /tmp/work24-source-packet.json \
  --review ../docs/evaluations/work24-source-review-2026-11-01/submission.json \
  --output-json /tmp/work24-source-adjudication.json \
  --output-report /tmp/work24-source-import.md
python -m app.catalog.review report \
  --result ../docs/evaluations/work24-bge-2026-11-01-live.json \
  --packet /tmp/work24-source-packet.json \
  --review ../docs/evaluations/work24-source-review-2026-11-01/submission.json \
  --adjudication /tmp/work24-source-adjudication.json \
  --output-json /tmp/work24-source-diagnostics.json \
  --output-report /tmp/work24-source-diagnostics.md
```

## 후속 검토

이 결과로 범위 밖 질의의 응답 보류와 용어/대체 과정의 판정 확장 필요성을 검토할 수 있다. 정책 변경을 하기 전 독립적인 추가 질의와 사람 검토를 포함한 별도 평가가 필요하다. FTS·NCS 검색·RRF 도입이나 모델·Worker 변경은 이 판정 작업에 포함하지 않았다.
