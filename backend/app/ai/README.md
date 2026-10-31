# B → A: Gemma 질문 모듈 (#9)

API 라우트·DB·jobs·GPU 패키지를 변경하지 않는 동기 HTTP 모듈이다. 기존 Pydantic과 Python 표준 라이브러리만 사용한다. Worker가 DB 트랜잭션 밖에서 `GemmaQuestions.generate(context, cancelled=event)`를 호출한다. A가 서버 정책에서 `eligible_slots`, `allowed_companion_slots`, `confirmed_answers`, `resolved_unknown_slots`를 제공한다. 예시는 `tests/test_questions.py`의 `CONTEXT`에 있다.

반환은 검증된 `QuestionDraft`이며 공개 질문/선택지 ID는 없다. A가 UUID와 명시적 미정 선택지를 추가하고 선택지와 patch를 저장한 뒤 공개한다. 모델이 답변이나 완료 상태를 직접 저장하지 않는다. `QuestionError`는 원문·서버 응답을 포함하지 않는 실패 코드다. 재시도·lease·job 상태·모델 슬롯 관리는 A가 담당한다. Event는 I/O 전후에 확인하며 진행 중인 socket의 즉시 취소나 원격 GPU 중단을 보장하지 않는다. socket timeout을 별도로 지정해야 한다.

`question-output.schema.json`과 합성 예시는 문서 브랜치 `e866058`의 `docs/intake/intake-question-contract.json`에서 가져왔다. JSON/schema·선택지 키/문구 중복·허용 슬롯·이미 확정/미정 처리된 값 수정·빈 patch·URL/HTML을 거부한다. 선택 문구와 patch의 의미 일치 및 인용의 사실 여부는 실제 모델 평가가 추가로 필요하다. 이 모듈을 통과했다고 의미적 정확성이 검증된 것은 아니다.

요청은 `/v1/chat/completions`의 비스트리밍 `response_format=json_schema` 형식이다. 원격 런타임의 이 형식 지원 여부는 아직 미검증이며, 미지원 응답을 성공이나 기본 질문으로 대체하지 않는다. model 이름은 실제 metadata 확인 후 호출자가 지정한다. timeout·출력 토큰·응답 byte 제한은 호출자 설정이며 아래 숫자는 검증용 예시다. 토크나이저 기반 입력 예산·설명 JSON·보정 재시도는 이 PR 범위에 포함하지 않는다.

실제 모델에 접근 가능한 환경에서 backend 디렉터리에서 실행한다. `--model`은 실제 metadata의 모델 이름으로 바꾼다. 원문과 전체 모델 응답은 출력하지 않고 검증 여부·지연·프롬프트 버전만 기록한다.

```sh
uv run --locked python -m app.ai.probe \
  --base-url http://127.0.0.1:18080 --model MODEL_NAME \
  --timeout 60 --max-tokens 512 --max-response-bytes 65536
```

로컬 합성 검증:

```sh
uv run --locked python -m unittest discover -s tests -p test_questions.py
uv run --locked ruff check --config pyproject.toml app/ai tests/test_questions.py
```

로컬 HTTP 서버는 합성 응답을 제공한다. 실제 Gemma·GPU 검증을 대체하지 않는다. 실제 모델 metadata·artifact·문맥·출력 예산·JSON 지원·의미 품질·GPU 지연은 원격 접속 후 기록해야 한다. #9 전체 완료나 Worker·웹 통합 완료로 처리하지 않는다.
