# 인증·자료 처리·단일 서버 운영

[문서 안내](../README.md) · [작업 계획](../planning/phases.md)

아래는 v0.8 요구사항입니다. 설치 절차는 [현재 README](../../README.md), 코드 규칙은 [개발 가이드](../development.md), 변경된 사실은 [최신 기준](../planning/baseline.md)을 따릅니다. **CI 필수 / 자동 배포·자동 승인 제외 / 수동 배포 검증 유지**. [서버 구성도](architecture.svg).

> v0.8 원문을 주제별로 재분류했습니다. 구현 제안·미검증 표시를 유지하며, 문서 존재를 구현 완료로 해석하지 않습니다.

<!-- source: index.html#execution -->

## 한 서버에서 질문 생성과 추천 추론 공유

| 항목 | 실행 계약 |
| --- | --- |
| 같은 모델·서버 | 질문 생성·보조 해석·추천 평가·설명은 같은 local Gemma 4 12B Q4를 공유한다. 웹·API·DB·Worker·자료 저장도 기존 할당 GPU 서버에서 실행한다. |
| 호출 시점 | 로그인 후 첫 질문과 답변 저장 뒤 필요한 다음 질문에서 생성한다. 키 입력·React 재렌더링·GET 조회마다 모델을 호출하지 않는다. |
| GPU 큐 | jobs.kind=intake_question 추가. 짧은 질문 작업의 대기와 추천 작업 기아를 함께 관리한다. 동시성 1은 초기 측정안이며 별도 모델 복제 로딩을 기본으로 하지 않는다. |
| 캐시 | 동일 사용자·revision·확인된 자료 해시·eligible slots·모델/프롬프트/정책 버전의 검증된 질문을 재사용한다. 공통 첫 질문의 검증된 본문을 캐시해도 question/option ID와 소유권 바인딩은 사용자별로 생성한다. |
| 상태 일관성 | 질문 생성 중 DB 트랜잭션을 유지하지 않는다. 게시 직전에 revision과 자료 해시 재검사. 답변 저장 성공 후 질문 생성 실패가 발생해도 답변을 잃지 않는다. |
| 문맥 관리 | 확정 값과 최근 유효 선택·필요한 근거만 전달한다. 모델의 숨은 사고 과정은 저장·표시하지 않는다. 질문·선택지·적용 값과 버전은 기록한다. |
| 성능 검증 | 첫 질문 대기, 선택 저장 시간, 다음 질문 생성 p50/p95, 큐 대기, JSON 실패·수정률, 중복 질문률, 추천 작업 대기를 실제 L4에서 측정한다. 미측정 응답시간을 보장하지 않는다. |
| 장애 상태 | 질문 생성 실패 / 자료 처리 실패 / 추천 실패를 구분. 입력은 동일 화면에서 복구하며 추천 실패를 정상 교육 결과로 대체하지 않는다. |

<!-- source: index.html#repository -->

## 기존 저장소·로그인·자료 처리 기준

검토 기준: [today-tommorrow/today-tommorrow · main 76f932e](https://github.com/today-tommorrow/today-tommorrow/tree/76f932ef9ca382214ee2acd6c706b0d5e92481cc). README, AGENTS.md, docs/development.md, web/package.json, web/vite.config.ts, backend/pyproject.toml, app/db.py, app/main.py, 마이그레이션과 compose.yaml을 확인했다.

| 항목 | 저장소에서 확인한 내용 | 이번 명세 반영 |
| --- | --- | --- |
| 웹 | React 19.2 범위 · TypeScript 5.9 범위 · Vite 7 범위 · Tailwind 4.1 범위 | web/ 구조와 strict TypeScript 유지. 구체 설치 버전은 package-lock.json 준수 |
| 런타임 | Node 기준 26.5.0 / Python 기준 3.14.6 | Node 엔진 범위와 Python >=3.14,<3.15 유지. 새 파서/OAuth 라이브러리는 해당 환경 호환성 검사 |
| DB | Compose postgres:17.11, loopback 노출, named volume | PostgreSQL 18로 바꾸지 않음. 기존 볼륨과 환경 파일 보존 |
| ORM | 동기 SQLAlchemy create_engine + psycopg, 요청 단위 Session | asyncpg로 전환하지 않음. 동기 DB 라우트/서비스와 작업 단위 세션 사용 |
| API | /api/health, 실제 SELECT 1 성공 200; 실패 503 detail=database_unavailable | /api 경로 유지. 기존 health 계약과 화면 연결 유지 |
| 마이그레이션 | 0001_baseline은 업무 테이블 없는 baseline | 새 업무 테이블은 후속 Alembic revision으로 추가. baseline을 덮어쓰지 않음 |
| 구현 상태 | 웹·API·DB 연결 확인까지만 구현. 로그인·업무 추천·GPU·Nginx 배포 미구현 | 새 기능의 구현 위치와 완료 조건을 명시. 목 응답을 실제 OAuth·추론 결과로 취급하지 않음 |

README의 이미지 판정 예시와 개발 가이드의 산책 추천 예시는 협업 설명용이다. 교육 추천 서비스의 확정 요구사항이나 구현된 API로 가져오지 않는다.

Google OAuth 계약 펼치기

| 항목 | 구현 계약 |
| --- | --- |
| 인증 방식 | 서버 측 Google OAuth 2.0 Authorization Code + OpenID Connect. scope=openid email profile |
| 시작 | 브라우저가 GET /api/auth/google/start로 이동. 서버는 일회용 state·nonce·브라우저 바인딩을 저장하고 Google 인증 화면으로 redirect |
| 콜백 | GET /api/auth/google/callback. state·바인딩·만료·재사용 검증 후 서버가 code를 토큰으로 교환. ID token 서명·iss·aud·exp·nonce 확인 |
| 계정 키 | users.google_sub를 고유 식별자로 사용. 이메일만으로 계정을 합치지 않음 |
| 서비스 세션 | 로그인 후 서비스용 불투명 쿠키 발급. HttpOnly·SameSite=Lax·운영 Secure. Google 토큰을 localStorage나 VITE_ 환경변수에 두지 않음 |
| 로그인 확인 / 로그아웃 | GET /api/auth/me / POST /api/auth/logout. 로그아웃은 세션만 종료하고 입력·추천 기록을 자동 삭제하지 않음 |
| 권한 범위 | Google 로그인은 신원 확인용. Drive·Gmail 읽기 권한을 요청하지 않으며 비공개 링크 접근 권한으로 간주하지 않음 |
| 실패 | 사용자 취소는 로그인 화면 복귀. state 불일치·만료·토큰 검증 실패는 인증 거부. 기존 초안 보호. 로그인되지 않으면 개인 자료 API는 401 |
| 설정 책임 | 팀이 Google Cloud의 OAuth 클라이언트와 허용 redirect URI를 설정한다. 구현은 환경변수를 읽도록 작성. 이번 산출물에서 실제 계정 설정이나 인증을 수행한 것은 아님 |

```text
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://127.0.0.1:5173/api/auth/google/callback
PUBLIC_ORIGIN=http://127.0.0.1:5173
SESSION_SECRET=...
# 배포 시 PUBLIC_ORIGIN과 REDIRECT_URI를 같은 HTTPS 서비스 도메인으로 변경.
# Google Console에 문자열이 정확히 일치하는 redirect URI 등록.
# 기존 DATABASE_URL / API_TARGET / WEB_PORT는 유지.
```

개발 callback도 Vite의 /api 프록시를 거쳐 같은 브라우저 origin으로 돌아온다. 운영 callback은 Nginx가 /api를 유지하여 전달한다. 비밀값은 서버 환경에만 둔다. Google token은 로그인 검증 후 장기 저장하지 않는 구현안을 사용한다.

파일·링크 처리 계약 펼치기

| 입력 방식 | 처리 단계 | 실패 처리 |
| --- | --- | --- |
| 파일 | multipart 업로드 → 실제 형식/크기 검증 → 비공개 저장 → 텍스트 추출·필요 시 OCR → 청크/출처 저장 → 로컬 Gemma 정보 추출 | 손상·암호화·지원 밖 형식·추출 실패를 자료별로 표시. 같은 단계에서 파일 교체 또는 직접 입력 |
| 링크 | URL 접수 → 안전한 HTTP 수집 → HTML 본문 또는 문서 추출 → 필요 시 공개 동적 페이지 렌더링 → 청크 저장 → Gemma 추출 | 로그인벽·수집 차단·빈 본문·timeout이면 상태와 원인을 표시하고 파일/텍스트 대체. URL만 읽었다고 가장하지 않음 |
| 직접 입력 | 경험/JD 텍스트 저장 → 청크와 원문 근거 생성 → 동일한 로컬 분석 파이프라인 | 빈 텍스트 검증. 다른 자료 입력과 동일하게 확인·수정 가능 |
| 자료 없음 | document_decision=none으로 저장. 사용자 질문 응답과 직접 적은 경험을 사용 | 이력서 제출을 강제하지 않음. 추천 전 다른 질문은 모두 완료 |

**파일 지원 기본안:** PDF(스캔 문서는 OCR), DOCX, TXT, PNG/JPG. 확장자만 확인하지 않고 실제 파일 형식과 크기를 검증한다. 다른 형식은 조용히 무시하지 않고 지원 불가와 변환 방법을 안내한다. 이 형식 목록과 처리 한도는 구현 기본안이며 /api/onboarding/options를 통해 UI와 서버가 같은 값을 사용한다.

| 자료 처리 규칙 | 명세 |
| --- | --- |
| 자료 상태 | queued → processing → ready / failed. 페이지·청크 단위 원문 위치와 해시 보존 |
| 모델 입력 | 파일 바이너리나 링크 문자열을 그대로 모델에게 읽으라고 하지 않는다. 실제 추출 텍스트와 출처를 전달 |
| 장문 | 청크별 분석 후 구조화 결과를 병합. 문서를 앞부분만 조용히 잘라서 사용하지 않음. 처리 범위·누락/제한을 최종 확인 전에 표시 |
| URL 안전성 | http/https만 허용. 사용자정보 포함 URL과 내부·loopback·link-local·metadata IP 차단. DNS 검사 후 실제 연결 IP 고정, redirect·재시도마다 검증. 요청 크기·시간·redirect 횟수 제한 |
| 추출 결과 | 경험, 사용 기술, 프로젝트, 목표 직무 요구사항에 원문 인용과 위치를 연결. 문서에 없다는 이유만으로 능력 부족이라고 단정하지 않음 |
| 자료 확인 | ready 자료의 추출 내용과 원문을 마지막 확인 화면에 제시. 사용자 수정은 원문 자체를 바꾸지 않고 별도 상태로 보존 |
| 실패 해결 | 처리 중에는 최종 제출 대기. 실패 자료는 재시도·교체·제거 중 하나로 해결. 자료를 실패 상태 그대로 모델이 읽은 것으로 처리하지 않음 |
| 보관 | 원본은 웹 정적 디렉터리 밖의 서버 저장소, 내용과 소유자는 DB로 연결. 로그인 세션 만료와 자료 보관을 분리. 이전 설계의 60분/24시간 일괄 삭제는 적용하지 않음 |
| 비공개 링크 | 로그인한 Google 계정의 개인 Drive 링크를 자동 열람하지 않음. 공개 공유 링크를 사용하거나 파일로 업로드. 비밀번호·쿠키를 입력받는 우회 경로를 만들지 않음 |

저장소 기준은 v0.6에서 검토한 commit이다. 이 개정은 명세와 시안을 수정하며 GitHub 코드·의존성·배포를 변경하지 않는다.
