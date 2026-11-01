# 로컬 고용24 교육 데이터 초기화

See the [batch activation guide](../../docs/catalog-batches.md) for additional Work24 batches, atomic activation, history, and pinned recommendations. The fixed bootstrap package remains supported and never resets an existing active pointer.

For supplied `records` JSON, see the [records adapter guide](../../docs/work24-records.md): exact source identities, null-only supplementation, offering quarantine, and verified precomputed-vector admission.

고정된 실제 과정 406개·교육 단위 6,099개·기존 float32 1024차원 벡터를 로컬 PostgreSQL 16/pgvector 0.6.0에 넣고 검증한다. 모델 호출·GPU·재임베딩은 사용하지 않는다. 이 프로젝트의 의존성은 앱과 별도 `.venv` 및 `uv.lock`으로 관리한다.

일반 사용은 저장소의 `./setup`과 `./check`가 맡는다. 아래 명령은 저장소 루트에서 실행하는 개별 진단이다. `<python>`은 저장소 `.python-version`에 해당하는 인터프리터 경로로 바꾼다.

```sh
# 데이터 파일만 검증: DB 연결 불필요
uv run --project tools/catalog --locked --python <python> python tools/catalog/bootstrap.py --dry-run

# 실제 초기화: setup이 공급하는 세 개발 DB URL이 필요
uv run --project tools/catalog --locked --python <python> python tools/catalog/bootstrap.py --report .local/catalog/bootstrap.json

# 재적재 없이 읽기 전용 무결성·검색 검사
uv run --project tools/catalog --locked --python <python> python tools/catalog/bootstrap.py --verify-only --report .local/catalog/verification.json

# 계약·대상 제한·최적화 옵션 회귀
uv run --project tools/catalog --locked --python <python> python -m unittest discover -s tools/catalog/tests -v

# 초기화가 끝난 로컬 DB의 벡터·metadata·실제 계정 권한 검사
uv run --project tools/catalog --locked --python <python> python -m unittest discover -s tools/catalog/integration -v
```

`--archive PATH`를 주면 해당 ZIP을 쓴다. 생략하면 저장소의 `tdtemp/work24-bge-m3-20261101.zip`을 먼저 찾고, 없으면 고정된 원격 커밋에서 `.local/catalog`로 다운로드한다. 어느 경로든 고정 SHA256이 다르면 중단한다. ZIP의 Python이나 SQL은 실행하지 않고 허용된 데이터 파일 9개만 임시 디렉터리에 읽는다. 실행할 SQL은 이 폴더에서 검토·관리하는 `schema.sql`과 `index.sql`이다.

## DB 대상과 권한

- `CATALOG_DATABASE_URL`: `today_bootstrap` 관리 계정, `postgresql://` 형식.
- `MIGRATION_DATABASE_URL`: `today_migrator`, SQLAlchemy의 `postgresql+psycopg://` 형식도 허용.
- `DATABASE_URL`: `today_app` 실행 계정, 같은 형식.
- 세 URL은 모두 **같은 포트의 `127.0.0.1`, DB 이름 `work24_dev`**여야 한다. 이름이나 주소가 다르면 원격/운영 초기화 방지를 위해 거부한다. `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS` 및 DSN의 우회 옵션도 거부한다.
- 실제 연결 후 DB·계정과 PostgreSQL 16을 확인한다. 초기화 시 pgvector 확장 0.6.0을 확인한다.
- 관리 계정이 `work24_bge`를 소유하고 적재한다. 앱에는 교육 스키마 USAGE와 테이블 SELECT만 준다. migration 계정에는 교육 권한을 주지 않는다.
- `today_app` 스키마의 소유자는 `today_migrator`다. 새 업무 테이블과 시퀀스의 앱 DML 기본 권한을 설정한다. Alembic 버전 테이블에 대한 앱 권한은 앱 migration 측에서 별도로 제한한다.
- 계정이 이미 존재하면 비밀번호나 소유권을 변경하지 않는다. 강한 관리자 권한이나 다른 역할 상속이 발견되면 중단한다. 기존 비밀번호와 현재 설정이 다르면 접속 설정을 명시적으로 맞춘다.

## 검증과 출력

manifest의 artifact hash·설정/profile hash·dataset hash, 과정/단위 개수·키 연결·원문 hash·단위 key·벡터 순서·유한값·L2 정규화를 검사한다. DB에서는 과정 metadata와 모든 단위 필드·벡터 float32 왕복을 대조한다. 같은 트랜잭션의 두 번째 upsert로 중복 증가가 없는지 확인한다.

저장된 `smoke-query.npy`의 전체 exact cosine을 NumPy float64 기준과 오차 `1e-5` 이내로 비교한 뒤에만 HNSW 인덱스를 생성한다. 초기화 때 `ANALYZE`로 profile/dataset 컬럼 통계도 갱신한다. 실제 최초 적재에서 통계 없이 두 조건이 1행으로 추정되어 snapshot B-tree와 정렬을 선택하는 것을 확인했기 때문이다. 읽기 전용 검사는 통계를 수정하지 않는다. 저장 질의의 인덱스 사용 여부와 exact 대비 top-10 겹침을 JSON으로 기록한다. HNSW가 사용되지 않으면 사용됐다고 표시하지 않는다. 이 단일 저장 질의는 DB·벡터 보존 검사이며 새 입력의 BGE/Gemma 품질을 검증하지 않는다.

정상 종료는 0, 검증/연결/적재 실패는 1이다. DB 적재·역할 준비·무결성 검사·인덱스 생성은 한 트랜잭션이며 그 검사에 실패하면 커밋하지 않는다. 이후 새 연결에서 앱·migration의 실제 인증과 읽기/쓰기 경계를 확인한다. 이 후속 인증 검사 실패는 이미 커밋한 교육 데이터를 되돌렸다는 뜻이 아니며, 비밀번호를 자동 변경하지 않고 현재 설정을 수정해 다시 검사한다. 성공 JSON 및 `--report` 파일은 커밋과 후속 인증 검사가 모두 끝난 뒤에만 출력한다. DSN·비밀번호·드라이버 원문 오류는 출력하지 않는다.

## 원본과 수정 근거

[Work24 패키지 출처 기록](../../docs/work24-provenance.md)은 원 URL·입력 추적·결측과 아직 확인하지 못한 수집·이용허락 근거를 구분한다.

- 원본: `https://github.com/seven2762/tdtemp/tree/043f8b8e96620994aff06b0edc2d235f793f0b5a`
- ZIP SHA256: `c6cd4618bc4b9fcca925161ee09ed683faf77f8ebf45cfa5831bb6bffdbaeb8e`.
- 원본 `schema.sql`, `index.sql`은 같은 교육 스키마 계약으로 재사용했다.
- 원본 `common.py`, `load_pgvector.py`, `query_smoke_test.py`의 hash·key·COPY/upsert·왕복·cosine/tie-aware 비교를 기반으로, 실행 모델 코드와 경로를 제거하고 검토된 코드만 이 폴더에 둔다.
- 원본의 핵심 `assert`를 명시적 검사로 바꿔 `python -O`에서도 검증한다. 드라이버가 반환한 pgvector `Vector`는 `.to_numpy()`로 명시적으로 변환한다.
- 원본에 없던 고정 ZIP 검사, 허용 파일 추출, 개발 대상 제한, 역할 분리, read-only verify 경로를 추가했다. 다른 dataset/profile을 기존 namespace에 자동 교체하지 않는다.
- Python 패키지는 원본 `requirements-load.txt`의 `numpy==2.5.4`, `pgvector==0.5.1`, `psycopg[binary]==3.3.6`만 사용한다. Python pgvector 패키지 버전과 PostgreSQL 확장 버전은 별개다. 현재 세션에 Sonatype 조회 도구가 제공되지 않아 해당 보안 조회는 수행하지 못했으며, 안전성 검증 완료로 표시하지 않는다.
