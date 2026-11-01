# B → A: Gemma 질문 모듈 (#9)

API 라우트·DB·jobs·GPU 패키지를 변경하지 않는 동기 HTTP 모듈이다. 기존 Pydantic과 Python 표준 라이브러리만 사용한다. Worker가 DB 트랜잭션 밖에서 `GemmaQuestions.generate(context, cancelled=event)`를 호출한다. A가 서버 정책에서 `eligible_slots`, `allowed_companion_slots`, `confirmed_answers`, `resolved_unknown_slots`를 제공한다. 예시는 `tests/test_questions.py`의 `CONTEXT`에 있다.

반환은 검증된 `QuestionDraft`이며 공개 질문/선택지 ID는 없다. A가 UUID와 명시적 미정 선택지를 추가하고 선택지와 patch를 저장한 뒤 공개한다. 모델이 답변이나 완료 상태를 직접 저장하지 않는다. `QuestionError`는 원문·서버 응답을 포함하지 않는 실패 코드다. 재시도·lease·job 상태·모델 슬롯 관리는 A가 담당한다. Event는 I/O 전후에 확인하며 진행 중인 socket의 즉시 취소나 원격 GPU 중단을 보장하지 않는다. socket timeout을 별도로 지정해야 한다.

`question-output.schema.json`과 합성 예시는 문서 브랜치 `e866058`의 `docs/intake/intake-question-contract.json`에서 가져왔으며, 스키마에 슬롯 코드 뜻을 추가했다. JSON/schema·선택지 키/문구 중복·동일한 answer_patch·허용 슬롯·이미 확정/미정 처리된 값 수정·빈 patch·URL/HTML을 거부한다. 선택 문구와 patch의 의미 일치 및 인용의 사실 여부는 실제 모델 평가가 추가로 필요하다. 이 모듈을 통과했다고 의미적 정확성이 검증된 것은 아니다.

`question-0.8.0-2`는 한국어가 출력 언어이고 교육 분야는 `field_code`라는 점을 구분한다. 구조화 출력의 스키마만으로 코드 의미가 전달된다고 가정하지 않고, 서버 스키마에서 확정·질문 대상·허용 companion 슬롯의 타입·enum·설명을 시스템 메시지에도 전달한다. 예를 들어 `ai_data`는 AI·데이터, `budget_status:no_limit`은 무료가 아닌 예산 상한 없음이다. 온라인 실시간과 온라인 자율 학습은 이 계약에서 같은 `online` 값이므로 별도 선택지로 생성하지 않도록 안내한다.

예산 상한은 원 단위 정수다. 무료만 원하면 `{budget_status:"amount",budget_krw:0}`이며, 서로 다른 상한 금액은 별도 선택지로 허용한다. `budget_krw` companion이 허용되고 아직 확정되지 않았다면 `amount` 선택지에 정수 금액이 필요하다. companion을 수집할 수 없으면 `amount`만 반환하여 후속 금액 질문을 허용한다. 금액을 함께 지정하면서 상태를 `no_limit` 또는 `unknown`으로 쓰는 patch는 거부한다. 실패 코드는 `duplicate_question_patch`, `missing_budget_amount`, `inconsistent_budget_patch`이며 응답 원문은 포함하지 않는다.

중복 비교에서 `no_limit`·`unknown`의 `budget_krw:null`은 금액 생략과 같은 예산 선택으로 취급한다. 반환 patch는 바꾸지 않으며 다른 슬롯의 null은 기존 값 삭제를 뜻할 수 있어 생략과 구분한다.

실제 GPU의 이전 프롬프트 출력에서 무료·소액·중간·고액 선택지가 모두 `no_limit`에 연결되고, 온라인 선택지 둘이 같은 patch에 연결된 사례가 발견되었다. 이 사례는 이제 중복 patch로 거부된다. 수정 프롬프트는 아래 실제 GPU의 합성 입력 3건으로 재확인했다. 서로 다른 patch 사이의 잘못된 문구나 다른 교육 분야 언급까지 자동 판별하는 것은 아니다.

요청은 `/v1/chat/completions`의 비스트리밍 `response_format=json_schema` 형식이다. 아래 llama.cpp 런타임에서 이 요청 형식과 검증된 응답을 확인했으며 다른 런타임의 지원 여부까지 보장하지 않는다. 미지원 응답을 성공이나 기본 질문으로 대체하지 않는다. model 이름은 실제 metadata 확인 후 호출자가 지정한다. timeout·출력 토큰·응답 byte 제한은 호출자 설정이며 아래 숫자는 실제 GPU 검증에 사용한 예산이다. 토크나이저 기반 입력 예산·설명 JSON·보정 재시도는 이 PR 범위에 포함하지 않는다.

실제 모델에 접근 가능한 환경에서 backend 디렉터리에서 실행한다. `--model`은 실제 metadata의 모델 이름으로 바꾼다. 원문과 전체 모델 응답은 출력하지 않고 검증 여부·지연·프롬프트 버전만 기록한다.

```sh
uv run --locked python -m app.ai.probe \
  --base-url http://127.0.0.1:18080 --model MODEL_NAME \
  --timeout 60 --max-tokens 2048 --max-response-bytes 65536
```

로컬 합성 검증:

```sh
uv run --locked python -m unittest discover -s tests -p test_questions.py
uv run --locked ruff check --config pyproject.toml app/ai tests/test_questions.py
```

로컬 HTTP 서버는 합성 응답을 제공한다. 실제 Gemma·GPU 검증을 대체하지 않는다.

## 실제 GPU 확인 — 2026-11-01

검증한 코드는 `ca24605d6ce9a6e0e136a6a75ccf13fb290ebfd0`, 프롬프트는 `question-0.8.0-2`다. 기존 NVIDIA L4에서 llama.cpp `b11960`, 모델 별칭 `gemma4-checklist`, Gemma 4 12B Q4_0을 사용했다. Python 3.14.6 HTTP 어댑터로 합성 입력을 전달했으며 모델·서비스·가중치를 변경하지 않았다. 요청 예산은 출력 2,048토큰, timeout 60초, 응답 65,536바이트다.

| 질문 대상 | 관측 시간 | 구조 검증과 응답 문구 수동 확인 |
| --- | --- | --- |
| `budget_status` | 18.684초 | 상한 없음은 `no_limit`, 무료는 `amount`와 0원, 50만·100만 원 상한은 문구와 숫자가 일치 |
| `delivery_mode` | 15.448초 | AI·데이터 문맥 유지, 온라인·방문·혼합·방식 무관이 서로 다른 올바른 patch에 연결 |
| `learning_goal` | 15.098초 | AI·데이터 문맥에서 기초·결과물·협업 목표의 문구와 patch가 일치 |

증거 파일은 GPU 서버의 `/home1/ncloud/llm-bench-work/results/today-integration-20261101-4eec1d9/gemma-candidate-cases.json`이다. 이 결과는 실제 모델을 호출한 합성 입력 3건의 관측값이며 전체 품질 평가, 지연 p50/p95, 부하·공유 GPU 큐, 실제 사용자 데이터, Worker·DB 저장·웹 전체 흐름을 검증하지 않았다. #9 전체 완료나 Worker·웹 통합 완료로 처리하지 않는다.
