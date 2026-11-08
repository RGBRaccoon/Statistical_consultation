# Task: 체크포인트 교체의 일시적 접근 거부 처리

- Goal: Windows에서 체크포인트 교체 시 일시적 접근 거부를 제한적으로 재시도한다.
- Reason: 목록 수집 중 `PermissionError`로 종료된 문제를 완화한다.
- Scope: 체크포인트 저장, 오류 안내, 회귀 테스트. 수집 범위와 원자료 변경은 제외한다.
- Acceptance Criteria: 일시적 접근 거부 후 저장에 성공하고, 지속 오류는 기존 체크포인트와 임시 파일을 보존하며 유한 횟수 후 중단한다. CSV 저장 후 체크포인트 실패에도 중복 없이 재개한다.
- Tests: 교체 실패 모의 테스트, 실패 후 목록 재개 테스트, 전체 pytest 및 Ruff 검사.
- Dependencies: 기존 수집기. 추가 패키지 없음.

## 검증 결과

- RED: 접근 거부 재시도 테스트 2개가 기존 구현에서 실패했다.
- GREEN: 전체 92개 테스트 및 수정 후 class3 테스트 17개 통과.
- Ruff check, format --check, git diff --check 통과.
- 실사이트 수집은 재개하지 않았다.
