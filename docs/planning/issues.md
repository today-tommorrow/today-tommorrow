# 시나리오 이슈 목록

[프론트·백엔드 공정도](workflow.md) · [문서 안내](../README.md) · [Phase](phases.md) · [전체 작업 정의](issue-plan.json)

임시 T번호는 추적용이며 실제 브랜치/PR에는 오른쪽 GitHub 이슈 번호를 사용합니다. 계획 단계 체크박스는 구현 완료를 의미하지 않습니다.

등록 상태: 26개 이슈(#7–#32), Phase 마일스톤 6개, 작업 라벨 12개를 등록했습니다. 각 이슈에 마일스톤과 라벨을 연결했습니다. [개발 공정 Project](https://github.com/orgs/today-tommorrow/projects/1)에 26개 이슈를 연결했습니다. 구현 완료를 뜻하지 않으며 실제 담당자는 착수 전에 지정합니다.

| ID | Phase | 시나리오 | 주담당 역할 | 선행 | GitHub |
| --- | --- | --- | --- | --- | --- |
| T01 | P0 | 팀원이 공통 명세와 계약을 찾아 같은 샘플로 개발한다 | B | 없음 | [#7](https://github.com/today-tommorrow/today-tommorrow/issues/7) · [본문](issues/T01.md) |
| T02 | P0 | 다른 팀원의 실제 Mac 새 클론에서 개발환경을 실행한다 | D | 없음 | [#8](https://github.com/today-tommorrow/today-tommorrow/issues/8) · [본문](issues/T02.md) |
| T03 | P0 | 지정 GPU에서 실제 모델의 질문과 설명 생성을 확인한다 | C | 없음 | [#9](https://github.com/today-tommorrow/today-tommorrow/issues/9) · [본문](issues/T03.md) |
| T04 | P0 | 운영자가 사용 가능한 교육 원천 배치와 출처를 준비한다 | D | 없음 | [#10](https://github.com/today-tommorrow/today-tommorrow/issues/10) · [본문](issues/T04.md) |
| T05 | P1 | 사용자가 Google 로그인 후 본인 세션을 확인하고 로그아웃한다 | B | T01 | [#11](https://github.com/today-tommorrow/today-tommorrow/issues/11) · [본문](issues/T05.md) |
| T06 | P1 | 사용자가 입력 초안을 저장하고 중단 후 이어간다 | B | T01, T05 | [#12](https://github.com/today-tommorrow/today-tommorrow/issues/12) · [본문](issues/T06.md) |
| T07 | P1 | 사용자가 긴 작업의 상태를 확인하고 중단 후 복구한다 | B | T01, T03, T06 | [#13](https://github.com/today-tommorrow/today-tommorrow/issues/13) · [본문](issues/T07.md) |
| T08 | P1 | 개발자가 모든 PR에서 필수 CI 검사 결과를 확인한다 | D | 없음 | [#14](https://github.com/today-tommorrow/today-tommorrow/issues/14) · [본문](issues/T08.md) |
| T09 | P2A | 사용자가 선택으로 시작해 실제 Gemma의 맞춤 질문에 답한다 | C | T01, T03, T06, T07 | [#15](https://github.com/today-tommorrow/today-tommorrow/issues/15) · [본문](issues/T09.md) |
| T10 | P2A | 사용자가 이전 답변을 수정해도 최신 입력이 보존된다 | B | T06, T09 | [#16](https://github.com/today-tommorrow/today-tommorrow/issues/16) · [본문](issues/T10.md) |
| T11 | P2A | 사용자가 직접 쓴 답변의 해석 제안을 확인하고 수락한다 | C | T06, T07, T09 | [#17](https://github.com/today-tommorrow/today-tommorrow/issues/17) · [본문](issues/T11.md) |
| T12 | P2A | 사용자가 파일이나 텍스트 자료를 등록해 추출 근거를 확인한다 | D | T01, T06, T07 | [#18](https://github.com/today-tommorrow/today-tommorrow/issues/18) · [본문](issues/T12.md) |
| T13 | P2A | 사용자가 공개 링크의 본문을 읽고 실패하면 대체 입력한다 | D | T06, T07, T12 | [#19](https://github.com/today-tommorrow/today-tommorrow/issues/19) · [본문](issues/T13.md) |
| T14 | P2A | 사용자가 자료 해석을 확인하고 재시도하거나 삭제한다 | B | T11, T12, T13 | [#20](https://github.com/today-tommorrow/today-tommorrow/issues/20) · [본문](issues/T14.md) |
| T15 | P2B | 운영자가 K-MOOC 목록과 상세를 중복 없이 적재한다 | D | T04 | [#21](https://github.com/today-tommorrow/today-tommorrow/issues/21) · [본문](issues/T15.md) |
| T16 | P2B | 운영자가 고용24 과정과 회차의 결측을 보존해 적재한다 | D | T04 | [#22](https://github.com/today-tommorrow/today-tommorrow/issues/22) · [본문](issues/T16.md) |
| T17 | P2B | 운영자가 NCS를 교육 후보와 구분한 학습 참조로 적재한다 | D | T04 | [#23](https://github.com/today-tommorrow/today-tommorrow/issues/23) · [본문](issues/T17.md) |
| T18 | P2B | 운영자가 추가 배치를 활성화해도 기존 추천 근거가 유지된다 | D | T15, T16, T17 | [#24](https://github.com/today-tommorrow/today-tommorrow/issues/24) · [본문](issues/T18.md) |
| T19 | P2B | 사용자 목표로 활성 카탈로그 전체에서 교육 후보를 찾는다 | D | T01, T15, T16, T17, T18 | [#25](https://github.com/today-tommorrow/today-tommorrow/issues/25) · [본문](issues/T19.md) |
| T20 | P3 | 사용자가 전체 입력을 제출해 근거 있는 실제 교육 추천을 확인한다 | C | T06, T07, T09, T19 | [#26](https://github.com/today-tommorrow/today-tommorrow/issues/26) · [본문](issues/T20.md) |
| T21 | P3 | 사용자가 추천 교육의 상세와 출처를 확인한다 | A | T18, T20 | [#27](https://github.com/today-tommorrow/today-tommorrow/issues/27) · [본문](issues/T21.md) |
| T22 | P3 | 사용자가 선택한 교육을 같은 기준으로 비교한다 | A | T21 | [#28](https://github.com/today-tommorrow/today-tommorrow/issues/28) · [본문](issues/T22.md) |
| T23 | P3 | 사용자가 관심 교육을 저장하고 다시 조회하거나 해제한다 | A | T05, T18, T21 | [#29](https://github.com/today-tommorrow/today-tommorrow/issues/29) · [본문](issues/T23.md) |
| T24 | P4 | 사용자가 지정 서버의 HTTPS 주소에서 서비스를 이용한다 | D | T05, T07, T08, T20 | [#30](https://github.com/today-tommorrow/today-tommorrow/issues/30) · [본문](issues/T24.md) |
| T25 | P4 | 사용자가 자료 유무와 오류 상황에서도 전체 흐름을 완료한다 | B | T10, T11, T14, T20, T21, T22, T23, T24 | [#31](https://github.com/today-tommorrow/today-tommorrow/issues/31) · [본문](issues/T25.md) |
| T26 | P4 | 팀원이 실제 L4에서 질문과 추천의 품질·대기시간을 판정한다 | C | T03, T09, T19, T20, T24 | [#32](https://github.com/today-tommorrow/today-tommorrow/issues/32) · [본문](issues/T26.md) |
