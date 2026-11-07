# Task: 목록 전용 CSV 수집

- Goal: 상세 페이지를 호출하지 않고 목록 정보만 별도 CSV로 수집한다.
- Reason: 요청 수를 줄이고 기존 상세 자료를 보존한다.
- Scope: 목록 파서·독립 체크포인트·CLI·테스트·사용법. 실제 수집 실행은 제외한다.
- Acceptance Criteria: `--list-only`의 기본 파일명은 `yes24_it_mobile_list.csv`; 상세 요청 없음; 중복 제거와 재개 지원; 기존 상세 스키마와 파일 보존.
- Tests: 목록 필드/누락값, 상세 요청 없는 재개 테스트를 먼저 실패시킨 후 구현했다. 전체 29개 테스트 및 Ruff 검사 통과.
- Dependencies: 기존 requests/BeautifulSoup. 추가 의존성 없음.
- 검증 범위: 오프라인 테스트만 실행했다. 사용자 요청에 따라 실사이트 수집은 실행하지 않았다.
