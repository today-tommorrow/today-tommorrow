# Work24 기존 카탈로그의 원격 적용

이 절차는 이미 적재된 고정 Work24 패키지를 현재 앱의 스냅샷·활성 배치 구조에 연결한다. 기존 원천 테이블과 자료/추천 영역을 다시 적재하거나 전역 역할을 바꾸지 않는다. 운영 적용 전후 결과는 아래 기록한다.

## 범위와 사전 조건

- 대상 원천은 `docs/catalog-search.md`의 고정 프로필·데이터셋과 같은 패키지다. 새로운 수집, 교육 내용/가격/일정 추정은 하지 않는다.
- 대상 DB·소유자·읽기 역할·프로필·데이터셋을 명시한다. 기존 기본 테이블의 모든 metadata, 단위와 float32 벡터 바이트가 패키지와 정확히 같아야 한다.
- 소유자가 기존 schema를 소유하고 읽기 역할은 비특권 역할이어야 한다. 도구는 사용자/비밀번호/역할 속성이나 기본 테이블 권한을 변경하지 않는다.
- 변경 전에 카탈로그 스키마의 일관된 `pg_dump -Fc --no-owner --no-privileges --schema=work24_bge` 백업을 별도 비공개 위치에 보관한다. 백업 원본·벡터·인증정보는 저장소에 넣지 않는다.
- 기존 개발용 bootstrap은 계속 로컬 전용이다. 운영 적용을 위해 그 안전장치를 끄지 않는다.

## 계획과 적용

별도 깨끗한 checkout에서 기존 패키지를 사용한다. DB 연결은 환경 변수로 주입하며 아래 값에는 인증정보를 작성하지 않는다. Unix 소켓 peer 인증과 비밀번호가 있는 TCP 연결을 지원한다. 다른 libpq 환경변수가 라우팅을 바꾸면 거부한다.

```sh
python tools/catalog/upgrade.py \
  --archive "$WORK24_ARCHIVE" \
  --expected-database "$CATALOG_DATABASE" \
  --expected-user "$CATALOG_OWNER" \
  --expected-profile-id "$CATALOG_PROFILE_ID" \
  --expected-dataset-id "$CATALOG_DATASET_ID" \
  --reader-role "$CATALOG_READER" \
  --report /tmp/work24-upgrade-plan.json
```

`CATALOG_UPGRADE_DATABASE_URL`에 명시한 DB를 읽기 전용 트랜잭션으로 검사한다. 계획에는 테이블·제약·인덱스·identity sequence, 406개 과정과 6,099개 단위 복사, 최초 포인터·이력 생성, 신규 테이블 SELECT 권한 부여가 각각 기록된다. 출력의 대상·원천·변경 목록을 검토하고 `plan_sha256`을 적용 명령에 전달한다.

```sh
python tools/catalog/upgrade.py \
  --archive "$WORK24_ARCHIVE" \
  --expected-database "$CATALOG_DATABASE" \
  --expected-user "$CATALOG_OWNER" \
  --expected-profile-id "$CATALOG_PROFILE_ID" \
  --expected-dataset-id "$CATALOG_DATASET_ID" \
  --reader-role "$CATALOG_READER" \
  --apply --expected-plan-sha256 "$REVIEWED_PLAN_SHA256" \
  --report /tmp/work24-upgrade-applied.json
```

적용은 기존 loader와 같은 advisory lock 및 원천 테이블 잠금을 잡은 뒤 원본을 다시 검사한다. READ COMMITTED에서 잠금 뒤 읽기 때문에 기다리는 동안 커밋된 원천 변경을 놓치지 않는다. 새 구조와 기준 스냅샷, 최초 포인터/이력, 해당 신규 테이블 SELECT 권한은 한 트랜잭션으로 처리된다. 구조 서명을 복사 전에 확인하여 잘못 상속한 sequence도 원천에 영향을 주기 전에 거부한다.

중간 실패는 전체 롤백된다. 보고서 저장 실패가 커밋 후 발생하면 `committed_report_write_failed`와 별도 종료코드 2를 반환하므로 이를 DB 롤백으로 해석하지 않는다. 기존 보고서나 패키지 경로를 출력으로 덮어쓸 수 없다. 부분 구조, metadata/벡터 불일치, 다른 계획 해시, 예상과 다른 권한/컬럼/제약/인덱스는 실패한다.

완료된 상태에서 재실행하면 `already_upgraded`로 전체 기준 스냅샷·권한·포인터 이력을 검증하고 쓰지 않는다. 나중에 정당한 새 배치가 활성화되어 있어도 최초 패키지로 되돌리지 않는다.

## 앱 연결 순서와 롤백

카탈로그 추가 구조 검증 → 현재 앱의 읽기 경계로 스냅샷/상세/검색 확인 → 앱 릴리스 순서로 진행한다. 이 작업은 다른 가지의 자료 API·추천 Worker·모델 배포를 수행하지 않는다. 새 카탈로그 테이블은 추가 구조이므로 구버전 앱이 쓰는 원천 테이블은 남아 있다.

커밋 전 오류는 트랜잭션 롤백으로 복구한다. 커밋 후 앱 문제가 발견되면 기존 앱 릴리스를 유지하면서 원인을 조사한다. 자동으로 테이블을 삭제하거나 전체 백업을 덮어써 후속 데이터를 잃게 하지 않는다. 백업 복구는 별도 점검된 운영 절차로 수행한다.

## #22 수용 기준

| 조건 | 근거 |
| --- | --- |
| 행 순서 독립 식별과 충돌 회차 격리 | `work24_records.py` 및 records 단위/DB 검사 |
| null·0·N/A 보존, 표시 금액을 개인 최종 가격으로 확정하지 않음 | [records 계약](work24-records.md) |
| 실제 ID 없는 관측의 내부 UUID·보수적 fingerprint | 격리 보고서에 보존하고 자동 병합/발행하지 않음. 영구 발행 registry는 원래 필수 조건이 아니며 별도 소비자 계약이 필요 |
| 추가 배치 결측 보완·DB 재조회·원본/벡터 보존 | records, synthetic legacy, pinned legacy 통합 검사 |
| 기존 운영 원천 위에 현재 앱용 스냅샷을 안전하게 적용 | 본 도구의 실제 계획·적용·재실행 및 읽기 검증 결과 |
| 실제 수집/이용 근거 | [출처 문서](work24-provenance.md)와 #10에서 기록. 패키지 해시는 수집 사실이나 이용허락의 증거를 대신하지 않음 |

## 적용 및 검증 결과

2026-11-01 원격 `work24_bge`에 적용하고 [기계 판독 기록](operations/work24-catalog-2026-11-01/receipt.json)을 보존했다. 적용 코드 커밋은 `0b36d5c`다. 원격의 기존 서버 checkout은 변경하지 않고 임시 checkout에서만 실행했다.

| 확인 항목 | 결과 |
| --- | --- |
| 적용 전 원본과 고정 패키지 | 406개 과정·6,099개 단위의 모든 metadata와 float32 벡터 바이트 일치 |
| 계획 | 읽기 전용 검사 통과, 대상·DDL·복사·권한 변경 해시 확인 |
| 최초 적용 | 단일 트랜잭션 성공, 기준 스냅샷·활성 포인터·이력 생성 |
| 동일 도구 재실행 | `already_upgraded`, 추가 쓰기 없음 |
| 기존 데이터 보존 | 원천 과정 406·단위 6,099 유지, 스냅샷에도 같은 수량 |
| 앱 읽기 권한 | 신규 5개 테이블 SELECT 가능, INSERT/UPDATE/DELETE 등 불가 |
| 현재 코드의 읽기 경계 | 검색 후보 3개·교육 상세 3개·관심 교육 카탈로그 406개 조회 성공 |
| 활성 이력 | 기준 배치 이력 1건, 같은 데이터셋 유지 |
| 정리 | 전용 원격 임시 checkout 및 로컬 테스트 컨테이너·credential 정리 |

원격 읽기 검증은 소유자 연결에서 현재 `CatalogRepository`, `CourseRepository`, 관심 교육 카탈로그 코드를 호출했다. 앱 역할 자체의 세션 로그인/HTTP 사용자 흐름을 검증했다고 주장하지 않는다. 앱 역할의 SELECT 전용 권한은 별도 검사했다. 검색 벡터는 기존 저장 벡터를 사용한 SQL 계약 검사이며, 모델 품질 또는 실제 BGE 질의 실행으로 표시하지 않는다. 모델 서비스와 다른 가지의 API/Worker 설정은 변경하지 않았다.

전체 `./check`와 관련 records/legacy opt-in 검사를 전용 로컬 DB에서 수행했다. 검색/배치 기존 검사와 새 업그레이드 검사 결과 및 제외 항목은 기계 판독 기록에 함께 남겼다. 기존 GitHub Actions와 스케줄러는 켜지 않았다.

이 기록은 기술적 적용 근거다. 사용자는 원천 수집·사용 허락 자료를 확보했다고 알렸으나 아직 위치를 전달받지 못했다. 실제 문서 확인은 #10에서 별도 기록하며, 이 결과로 이용/재배포 범위를 확인했다고 주장하지 않는다.
