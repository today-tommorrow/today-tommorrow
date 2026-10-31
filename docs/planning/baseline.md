# 2026-11-01 기준: 최신 코드와 명세

[문서 안내](../README.md)

사용자 요청으로 `git pull --ff-only origin main`을 실행해 `f15575d`에서 `c9cb41b5bfad39e39985160a66e0a5a39a5bd044`로 최신화했습니다. v0.8 패키지의 작성 기준은 `76f932e`입니다.

| 항목 | 최신 코드·규칙 | 계획에 반영 |
| --- | --- | --- |
| Python | 공통 `scripts/launch`가 uv로 `.python-version`의 정확한 버전 선택. setup만 설치, dev/check는 재사용 | 시스템 Python·PATH 수동 변경 작업을 만들지 않음 |
| Node/npm | PATH의 정확한 버전 우선, `.local/node` 캐시, setup만 공식 macOS archive SHA256 검증 설치 | 이미 구현된 준비 기능을 재구현하지 않음. 다른 실제 Mac 검증만 남음 |
| 품질 | 기존 ./check, Ruff E4/E7/E9/F/I, TS strict·ESLint·Prettier·테스트·빌드 | CI가 같은 검사를 재사용. Linux CI는 macOS 전용 다운로드에 의존하지 않도록 정확한 런타임 사전 준비 |
| PR | `.github/pull_request_template.md`, Conventional Commit 제목·커밋, 사람 1명 이상 승인 | 옛 0명 승인 설명 폐기. 리뷰어 xifoxy-ru·zxcv718, 작성자 제외 |
| CI/CD | 사용자 확정: CI 필수, 자동 배포·자동 승인 제외 | CI 구현·실제 PR 성공 후 필수 검사 지정. 수동 배포·복구 검증은 유지 |
| 업무 구현 | 실제 /api/health, DB SELECT 1, 업무 테이블 없는 baseline | OAuth·입력·자료·추천·Worker·GPU·Nginx는 미구현으로 계획 |

원격 보호 규칙을 이 문서 작업으로 변경하지 않습니다. 문서상 사람 1명 이상 승인을 준수하고 실제 설정 검증은 CI 도입 이슈에서 확인합니다. 최신 main이 다시 바뀌면 작업 전 pull/fetch 후 이 기준과 관련 이슈를 갱신합니다.
