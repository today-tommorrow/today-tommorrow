# 실제 교육 데이터를 사용하는 로컬 개발 DB

고용24 데이터와 완성된 벡터를 로컬 DB에 넣고, 모델 없이 적재·검색·권한을 검사한다. `today_app`에는 [인증 저장 기반](../backend/app/auth/README.md)의 사용자·세션·OAuth 상태도 migration으로 준비한다. [Google 로그인 API](google-login.md)는 이 테이블을 사용한다. [초안 API](profile-drafts.md)와 [질문·작업 API](question-jobs.md)는 `today_app`에 저장하며, 질문 생성만 별도 Worker와 설정된 모델 서버를 사용한다. 로컬 DB 준비와 교육 검색 검사는 모델이나 GPU 서버 접속을 요구하지 않는다.

## 준비와 실행

Git, Docker Compose v2, 실행 중인 Docker 엔진, uv를 준비한다. Python과 Node는 저장소 버전 파일을 따른다. 새 Python 다운로드를 확인한 uv 버전은 0.12.5다. 첫 실행에는 런타임·고정 패키지·Docker 빌드용 다운로드가 필요하다.

```sh
./setup
./dev
# 다른 터미널
./check
```

`./setup`은 기존 `.env`를 덮어쓰지 않는다. 웹/API 의존성과 별도 `tools/catalog` 환경을 설치하고, PostgreSQL 이미지를 빌드한 뒤 교육 데이터 bootstrap과 앱 migration을 순서대로 실행한다. 앱 migration은 인증·초안·작업·질문·답변·idempotency 요청 테이블을 준비한다. loader에는 NumPy·pgvector Python 어댑터·psycopg만 설치하며 torch·FlagEmbedding·모델 가중치는 설치하지 않는다.

기본 개발 DB는 `127.0.0.1:55432/work24_dev`다. `.env`의 세 URL은 같은 호스트·포트·DB를 가리켜야 하며 사용자 역할은 아래 표와 맞춘다. `POSTGRES_USER`/`POSTGRES_PASSWORD`는 bootstrap 계정에 대응한다. 포트를 바꾸면 `DB_PORT`와 세 URL을 모두 바꾼다. 이전 `today` DB 설정이 남은 `.env`는 먼저 보관하고 `env.example`을 참고해 개발 설정을 맞춘다. 초기화는 원격 주소·다른 DB 이름·libpq 연결 우회를 거부한다.

| 설정 | 역할 |
| --- | --- |
| `CATALOG_DATABASE_URL` | `today_bootstrap`: 이 로컬 개발 DB의 교육 초기화 관리자 |
| `MIGRATION_DATABASE_URL` | `today_migrator`: `today_app` 스키마 소유자·migration 실행 |
| `DATABASE_URL` | `today_app`: 교육 데이터 읽기, 앱 업무 테이블 DML |

개발용 예제 비밀번호는 개인 PC용이다. 기존 역할의 비밀번호를 자동 변경하지 않으므로 URL이 실제 역할과 맞지 않으면 접속 검사가 실패한다. URL·드라이버 원문 오류는 bootstrap 로그에 출력하지 않는다.

## PostgreSQL과 스키마 경계

PostgreSQL 16.14와 pgvector 0.6.0으로 원격 환경에 맞춘다. `docker/postgres16/Dockerfile`은 PostgreSQL 기본 이미지 digest와 pgvector 소스 archive의 SHA256을 고정한다. 공식 구형 pgvector 이미지에 포함된 PostgreSQL 16.1을 사용하지 않고 16.14 위에서 확장을 빌드한다. 빌드 도구는 최종 실행 이미지에 포함하지 않는다.

Compose 프로젝트 기본 이름은 `today-tommorrow-local16`, 볼륨은 `postgres16_data`다. 이전 PostgreSQL 17의 볼륨과 분리되며 기존 데이터를 복사하거나 삭제하지 않는다. `COMPOSE_PROJECT_NAME`을 지정하면 시작·검사·중지 명령에 같은 값을 사용한다. 이 컨테이너는 개발용이며 운영 GPU 서버의 서비스를 바꾸지 않는다.

| 스키마 | 관리 주체 | 앱 권한 |
| --- | --- | --- |
| `work24_bge` | 검토된 교육 bootstrap·loader | SELECT만 허용 |
| `today_app` | SQLAlchemy metadata·Alembic | 업무 테이블 읽기·쓰기, DDL 금지 |

bootstrap이 역할과 `today_app` namespace를 먼저 준비한다. Alembic은 기존 빈 `0001_baseline`을 유지하고 `today_app.alembic_version`에 버전을 기록한다. runtime은 이 버전 테이블을 읽을 수 있지만 수정할 수 없다. 이후 업무 모델도 `today_app`을 기본 스키마로 사용한다. autogenerate는 교육·public·등록되지 않은 앱 테이블의 삭제를 제안하지 않도록 제한한다. 의도한 테이블 삭제는 검토한 명시적 migration으로 기록한다.

## 고정 교육 패키지

| 항목 | 값 |
| --- | --- |
| 원본 | `seven2762/tdtemp`, commit `043f8b8e96620994aff06b0edc2d235f793f0b5a` |
| 파일 | `work24-bge-m3-20261101.zip` |
| SHA256 | `c6cd4618bc4b9fcca925161ee09ed683faf77f8ebf45cfa5831bb6bffdbaeb8e` |
| 과정 / 교육 단위 | 406 / 6,099 |
| 벡터 | 6,099 × 1,024, float32 |

저장소의 `tdtemp` 폴더에 이 ZIP이 있으면 먼저 사용한다. 없으면 [고정 커밋의 ZIP](https://raw.githubusercontent.com/seven2762/tdtemp/043f8b8e96620994aff06b0edc2d235f793f0b5a/work24-bge-m3-20261101.zip)을 `.local/catalog` 캐시에 내려받는다. 두 위치는 새 코드 저장소에서 제외한다. 검사에 실패한 로컬 원본을 조용히 바꾸지 않는다. `--archive`로 파일을 지정해도 같은 고정 SHA256을 확인한다.

ZIP 전체 해시를 확인하고 필요한 데이터 파일만 추출한다. ZIP 안의 Python·SQL을 실행하지 않으며 이 프로젝트의 검토된 loader·SQL을 사용한다. 내부 artifact 해시·manifest·profile/dataset·원문 키·유한값·정규화도 확인한다. 기존 출처와 `is_inferred`를 보존하고 같은 단위의 벡터를 재계산하지 않는다. loader 수정과 재사용 출처는 [tools/catalog 안내](../tools/catalog/README.md)를 따른다.

같은 패키지는 upsert로 다시 적용하며 과정·단위 키와 앱 입력을 보존한다. 다른 dataset/profile로 바꾸는 배치 갱신은 이 bootstrap의 범위 밖이며 자동으로 기존 교육을 비활성화하지 않는다.

## 검사 결과와 범위

`./setup`은 실제 적재 후 다음을 검사한다. `./check`는 `--verify-only`로 같은 검사를 읽기 전용으로 수행하며 재적재하지 않는다.

- 과정 406개·단위 6,099개와 모든 metadata·원문 키 연결.
- float32 벡터 전체 왕복 값과 `vector_row` 순서 보존.
- 저장된 smoke 질의의 NumPy exact cosine과 DB exact 검색 비교.
- exact 검사 후 HNSW 생성과 `ANALYZE`. HNSW 사용 여부·exact 대비 상위 결과 중복률을 보고한다. 인덱스 진단에는 명시적인 planner 설정을 사용하며 일반 앱 쿼리의 자동 선택을 보장하지 않는다.
- 실제 runtime/migration 계정 접속과 교육·앱 권한 경계.

성공 보고서는 commit과 계정 검증 후 `.local/catalog/bootstrap.json` 또는 `verify.json`에 기록한다. 교육 commit 후 계정 인증 검사가 실패할 수 있으며, 이때 이미 commit한 데이터까지 rollback했다고 간주하지 않는다. 비밀번호를 맞춘 뒤 재실행한다.

`./check`는 앱 migration 버전의 runtime 수정 거부, 교육 데이터 DDL/DML 거부, autogenerate 범위를 실제 로컬 DB로 검사한다. 변조 거부 시험의 쓰기는 rollback하며 교육 데이터 변경을 commit하지 않는다. 초기화와 검사는 같은 DB에서 병렬 실행하지 않는다.

재적재 후 앱 입력·교육 키 보존은 `L0_REIMPORT_TESTS=1`을 명시한 별도 통합 시험이며 CI에서 실행한다. 이 시험은 임시 앱 행을 만든 뒤 교육 bootstrap을 다시 commit하고 앱 행·교육 키가 유지되는지 확인하며, 임시 테이블은 정리한다. 일반 `./check`는 이 플래그를 0으로 고정해 재적재하지 않는다. DB 장애·복구도 CI의 전용 개발 컨테이너에서 확인한다.

저장된 질의는 새 문장을 BGE로 변환한 것이 아니다. 이 결과는 새 입력의 검색 품질·Gemma 질문/추천·GPU 품질을 검증하지 않는다. 해당 단계는 후속 로컬 구현과 원격 통합으로 남긴다. 다른 물리적 Mac에서의 재현 확인은 B가 별도로 기록해야 한다.

DB를 중지할 때는 `docker compose stop db`를 사용한다. 볼륨 삭제는 초기화·재실행·복구 절차에 포함하지 않는다.
