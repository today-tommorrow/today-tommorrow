# 고정 Work24 패키지의 출처와 미확인 범위

이 문서는 [#10](https://github.com/today-tommorrow/today-tommorrow/issues/10)의 Work24 범위에 대한 **부분 검증 기록**이다. 기존 패키지의 메타데이터와 내부 추적 정보를 확인했다. 원천 수집 절차와 이용·재배포 허락은 아래 증거만으로 확정할 수 없어 이슈를 닫지 않는다. K-MOOC·외부 NCS 신규 수집, DB 접속, 모델·GPU 실행은 포함하지 않는다.

## 고정 대상과 출처

| 항목          | 확인한 값과 근거                                                                                                                                |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 전달 저장소   | [`seven2762/tdtemp`의 고정 커밋](https://github.com/seven2762/tdtemp/tree/043f8b8e96620994aff06b0edc2d235f793f0b5a)                             |
| 패키지        | `work24-bge-m3-20261101.zip`, SHA256 `c6cd4618bc4b9fcca925161ee09ed683faf77f8ebf45cfa5831bb6bffdbaeb8e`                                         |
| 입력 식별자   | `dataset_id=7c1a3046ed398ab05cf15b800eb07bd936c8665d3803293d44f46ab73f8f9b80`; 입력 파일명·크기·SHA256의 정규 JSON 해시                         |
| 공식 상세 URL | 406개 과정 모두 `https://www.work24.go.kr/hr/a/a/3100/selectTracseDetl.do?tracseId=<course_id>&tracseTme=<tme>` 형식이며 두 값이 저장 키와 일치 |
| 검증 시점     | 2026-11-01, 기존 ZIP을 읽기 전용으로 확인. 공식 상세 페이지를 다시 수집하거나 현재 모집 상태를 확인하지 않음                                    |

고정 커밋의 파일 목록은 ZIP과 SHA256 sidecar뿐이다. ZIP 내부 경로는 아래 표에서 패키지 루트 `work24-bge-m3-20261101/`를 생략했다. 이 저장소에는 원본 CSV·XLSX·JSONL이나 패키지를 새로 복사하지 않는다. 확보·캐시 위치와 고정 다운로드 주소는 [로컬 DB 안내](local-database.md#고정-교육-패키지)를 따른다.

## 무엇을 어디까지 추적할 수 있는가

| 대상           | 확인한 근거                                                                                                     | 한계                                                                                                                                                |
| -------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 전달 경로      | 사용자 확인: 다른 사람이 만든 파일을 전달받음                                                                   | 직접 수집한 데이터로 표시하지 않음. 제작자의 원천 수집 방식·시각·이용허락을 확인한 사실은 아님                                                      |
| 수집·파싱 단계 | `inputs/EMBEDDING_HANDOFF.md` 3행은 수집·파싱·시간표·NCS 보충(T1–T5)이 upstream에서 완료됐다고 설명             | 수집 코드·실행 명령·HTTP 응답 보관·요청 시각·수집 주체를 재현할 기록은 패키지에서 확인되지 않음. API 수집 또는 특정 브라우저 자동화로 단정하지 않음 |
| 과정 입력      | `inputs/out/courses.csv` 300행, `inputs/out_security/courses.csv` 107행 → `(course_id, tme)` 406개              | 중복된 같은 키가 있어 입력 행 수가 최종 과정 수와 다름. 행 번호는 전역 ID가 아님                                                                    |
| 과정 추적      | 각 `courses.jsonl`의 `provenance`는 입력 dataset·파일, `original_csv`는 입력 메타데이터 보존                    | 파일 SHA256은 파일 동일성 근거이며 원천 HTTP 응답 해시가 아님                                                                                       |
| 일정·지역·금액 | `prepare.py` 115–161행: XLSX `전체 목록`을 과정 ID·대표 회차로 조인. `excel_provenance`에 sheet·row·values 보존 | 엑셀 날짜·패키지 날짜로 수집 시각을 대신할 수 없음. 표기 자부담은 개인 최종 부담금이 아님                                                           |
| 단위 전처리    | handoff 33–50행과 `manifest.json.counts`: 입력 6,228행, noise 99행 제외, 중복 30행 제거, 최종 6,099행           | 이 기록은 이미 받은 입력의 통합 절차다. `prepare.py`를 원천 수집기로 간주하지 않음                                                                  |
| 단위 추적      | `units.jsonl.provenance`에 dataset·file·csv_line·seq, `text_sha256`·`unit_key`·`vector_row` 보존                | `seq`·`vector_row`는 원천의 전역 ID가 아님. `text_sha256`은 임베딩 텍스트의 해시이며 원천 레코드 전체 해시가 아님                                   |
| 단위 출처 표시 | `page` 4,409개, `timetable` 315개, `curriculum` 240개, `page+timetable` 20개, `ncs_csv` 1,115개                 | `source`는 전달된 출처 분류다. 단위별 원 URL·라이선스·조회 시각을 증명하지 않음                                                                     |
| 기존 NCS 추정  | 1,115개에 `is_inferred=true`, 모두 `source=ncs_csv`; 기존 패키지 안의 항목만 유지                               | 외부 NCS 원본의 URL·버전·이용허락은 별도 미확인. 과정 상세 URL을 추정 항목의 직접 출처로 표시하지 않음                                              |

`manifest.json`에는 입력 파일 9개, 생성 코드 4개, 산출물 8개의 해시가 있다. `created_on`과 `inputs/input_manifest.json.dataset_date`는 모두 `2026-11-01`이지만 원천별 수집·검증 시각을 뜻하지 않는다. 과정 406개와 단위 6,099개 모두 최상위 `source_hash`·`collected_at` 필드가 없다. 기존 데이터의 이 값은 **미상**으로 유지하며, 새 배치의 명시적 필드 계약은 [배치 안내](catalog-batches.md#incoming-package)를 따른다.

결측도 그대로 보존한다. 과정의 `training_fee` 25개, `displayed_self_pay` 86개와 단위의 `hours` 4,083개가 JSON `null`이다. 단위의 `ncs_unit_code` 4,632개는 빈 문자열이다. 이 패키지에는 금액·단위 시간의 수치 0이 없으므로 **실제 0원 입력의 처리 검증 근거로 사용할 수 없다**. 날짜·지역·과정 훈련시간은 406개 모두 존재한다. 표시 정책은 [교육 상세 API 안내](course-api.md#truthful-source-presentation)를 따른다.

## 사용·재배포 판단에 남은 증거

공개 URL이나 GitHub 다운로드 가능 여부만으로 이용·재배포 허락을 확정하지 않는다. 고정 커밋의 파일 목록과 ZIP의 README·handoff 등 텍스트에서 이 데이터셋에 적용되는 이용허락서·재배포 범위 기록을 찾지 못했다. 이는 허락이 없다는 법적 판정이 아니라 **이 패키지에 그 증거가 없다는 관측**이다. BGE 모델의 이용 조건과 원천 교육 데이터의 이용 조건도 구분해야 한다.

[고용24 이용약관](https://m.work24.go.kr/cm/c/d/0130/retrieveUtzeStpt.do)은 일반 서비스 약관이다. 별도 [공통코드-훈련 OpenAPI](https://www.data.go.kr/data/15037382/openapi.do)의 이용허락 범위가 제한 없음이어도, 그 데이터셋이 이 패키지의 과정 상세·시간표·교과 내용과 같다고 확인되지 않았으므로 여기의 재배포 허락으로 옮겨 적지 않는다.

#10을 완료하려면 원천 담당자가 다음 증거를 제공하고 관련 담당자가 확인해야 한다.

- 고용24 상세·시간표·교과 내용별 적용 정책 또는 허락의 정확한 URL·버전·확인일, 출처 표시와 가공·임베딩·서비스 표시·원본 재배포의 허용 범위.
- 기관이 제공한 설명·교재 및 기존 `ncs_csv` 추정 근거에 별도 권리가 적용되는지와 그 출처·허락.
- 기존 수집 코드·실행 절차·주체·원천 URL 및 수집 시각 기록. 소급 확인할 수 없는 값은 미상으로 남기고, 앞으로의 배치에서는 신뢰할 수 있는 값만 기록.

확인 전에는 공개 이슈·PR에 원본 레코드나 전체 패키지를 추가 첨부하지 않는다. 이 문서의 집계·필드명·고정 해시만으로 출처 확인 진행 상황을 공유한다. 이용 범위 확인은 **미완료**, 파일 무결성·형식·추적 정보 확인은 아래 명령 범위에서 **Done**이다.

## DB·모델 없이 재현

이미 확보한 ZIP의 경로를 지정한다. 다음 Python 표준 라이브러리 명령은 다운로드·추출·패키지 코드 실행·DB 연결을 하지 않고, 원문 값 대신 집계만 출력한다. 정상 종료 코드 0과 아래 집계가 기대 결과다. Python 최적화 옵션 `-O`는 사용하지 않는다.

```sh
python3 - /existing/path/work24-bge-m3-20261101.zip <<'PY'
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

archive = Path(sys.argv[1])
assert hashlib.sha256(archive.read_bytes()).hexdigest() == (
    "c6cd4618bc4b9fcca925161ee09ed683faf77f8ebf45cfa5831bb6bffdbaeb8e"
)
prefix = "work24-bge-m3-20261101/"
with zipfile.ZipFile(archive) as package:
    manifest = json.loads(package.read(prefix + "manifest.json"))
    canonical_inputs = json.dumps(manifest["input_files"], ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert hashlib.sha256(canonical_inputs.encode()).hexdigest() == manifest["dataset_id"]
    input_rows = {}
    for name in ("out/courses.csv", "out_security/courses.csv",
                 "out/course_units.csv", "out_security/course_units.csv"):
        text = package.read(prefix + "inputs/" + name).decode("utf-8-sig")
        input_rows[name] = len(list(csv.DictReader(io.StringIO(text))))
    assert input_rows == {"out/courses.csv": 300, "out_security/courses.csv": 107,
                          "out/course_units.csv": 4682, "out_security/course_units.csv": 1546}
    for group, base in (("input_files", "inputs/"), ("artifacts", "")):
        for name, info in manifest[group].items():
            raw = package.read(prefix + base + name)
            assert len(raw) == info["bytes"]
            assert hashlib.sha256(raw).hexdigest() == info["sha256"]
    for name, expected in manifest["generation_code_sha256"].items():
        assert hashlib.sha256(package.read(prefix + name)).hexdigest() == expected
    courses = [json.loads(line) for line in package.read(prefix + "courses.jsonl").splitlines()]
    units = [json.loads(line) for line in package.read(prefix + "units.jsonl").splitlines()]
    assert len(courses) == 406 and len(units) == 6099
    keys = {(row["course_id"], row["tme"]) for row in courses}
    assert len(keys) == 406
    assert all((row["course_id"], row["tme"]) in keys for row in units)
    for row in courses:
        url = urlsplit(row["url"])
        assert (url.scheme, url.netloc, url.path) == (
            "https", "www.work24.go.kr", "/hr/a/a/3100/selectTracseDetl.do"
        )
        query = parse_qs(url.query)
        assert query["tracseId"] == [row["course_id"]]
        assert query["tracseTme"] == [row["tme"]]
    assert all("source_hash" not in row and "collected_at" not in row for row in courses + units)
    assert all(set(item) == {"dataset", "file"} for row in courses for item in row["provenance"])
    assert all(set(item) == {"dataset", "file", "csv_line", "seq"} for row in units for item in row["provenance"])
    sources = dict(Counter(row["source"] for row in units))
    assert sources == {"page": 4409, "timetable": 315, "ncs_csv": 1115, "curriculum": 240, "page+timetable": 20}
    assert sum(row["is_inferred"] is True for row in units) == 1115
    assert all(row["source"] == "ncs_csv" for row in units if row["is_inferred"])
    nulls = {
        "training_fee": sum(row["training_fee"] is None for row in courses),
        "displayed_self_pay": sum(row["displayed_self_pay"] is None for row in courses),
        "hours": sum(row["hours"] is None for row in units),
    }
    assert nulls == {"training_fee": 25, "displayed_self_pay": 86, "hours": 4083}
    assert sum(row["ncs_unit_code"] == "" for row in units) == 4632
    assert all(row[field] is not None for row in courses for field in ("start_date", "end_date", "region", "training_hours"))
    assert not any(type(row[field]) in (int, float) and row[field] == 0 for row in courses for field in ("training_fee", "displayed_self_pay"))
    assert not any(type(row["hours"]) in (int, float) and row["hours"] == 0 for row in units)
    print(json.dumps({"courses": len(courses), "units": len(units), "sources": sources, "nulls": nulls}, sort_keys=True))
PY
```

실행 결과: `courses=406`, `units=6099`, source별 수와 null 수는 위 표와 일치했다. 2026-11-01 macOS arm64의 Python 3.14.4 표준 라이브러리로 실행했다. 이 문서 변경에서는 `./check`·DB 적재·현재 상세 페이지 조회·모델 재실행을 하지 않았다. 기존 구현 검증 기록은 [배치 안내](catalog-batches.md#verification-evidence)에 있으며 이번 출처 확인으로 재검증했다고 표시하지 않는다. GPU는 **N/A — 메타데이터 문서화만 수행**이다.
