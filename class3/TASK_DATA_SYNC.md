# Task: 수집 데이터의 Git 보관과 PC 간 재개

- Goal: 도서 데이터와 체크포인트를 함께 Git에 보관할 수 있게 한다.
- Reason: 여러 PC에서 동일한 수집을 이어갈 때 데이터와 진행 기록이 필요하다.
- Scope: Git 제외 규칙과 수집 인계 안내. 수집 코드 변경과 원격 push는 제외한다.
- Acceptance Criteria: CSV·엑셀·목록/상세 체크포인트는 제외되지 않고, 임시 파일과 오류 로그는 계속 제외된다. 동시 실행 금지와 중단 후 인계 절차를 설명한다.
- Tests: `git check-ignore`로 파일 유형별 규칙을 확인하고 `git diff --check`를 수행한다. 실행 코드 변경은 없어 기능 테스트는 추가하지 않는다.
- Dependencies: 기존 목록/상세 수집기와 Git.
