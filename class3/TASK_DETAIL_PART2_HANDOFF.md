# Task: 파트 2 최종 수집 결과 인계

- Goal: 2026년까지 파트 2 수집 결과와 검토 사유를 원격 main에 보관해 실제 작업 PC로 전달한다.
- Reason: 분석 작업 위치에서 파트 1과 합칠 최종 자료가 필요하다.
- Scope: 파트 2 전체/1차 CSV, 분류 사전, 진행 JSON, SQLite 압축 백업. 수집 코드와 다른 파트 데이터는 제외한다.
- Acceptance Criteria: SQLite 무결성 정상, 미처리 0건, 전체/1차 CSV가 SQLite 결과와 일치, 압축 백업 검증 통과, 원격 main 반영.
- Tests: SQLite integrity_check, 상태 집계, CSV 상품 ID 유일성 및 전체 필드 비교, 1차 구간 비교, 복사 SHA-256 일치, ZIP CRC 확인.
- Dependencies: 완료된 파트 2 SQLite 및 CSV. 실행 코드 변경은 없어 기능 테스트는 추가하지 않는다.

## 인계 내용 및 검증 결과

- 수집 종료: 2026-11-15 19:45 한국시간. `finished_with_review`, 미처리 0건.
- 대상 13,894건: 정상 완료 12,760건, 검토 대상 1,134건.
- 전체 CSV 13,890행, 2016년까지 CSV 5,047행. 상품 ID 중복 없음, SQLite 원자료와 모든 필드 일치.
- 상세 결과가 없는 검토 대상 4건은 CSV에 포함되지 않는다. SQLite의 `jobs.status`, `metadata_json`에 사유가 보존된다.
- SQLite 무결성 및 ZIP CRC 확인 통과, CSV/JSON 원본과 복사본 SHA-256 일치.

## 실제 작업 PC에서 받기

- 현재 작업을 보존한 상태에서 최신 main을 받는다. 파트 2 CSV를 파트 1 CSV와 `product_id` 기준으로 합친다.
- 주 분석 자료는 `data/yes24_details_2010_2026.part2.csv`, 분류 사전은 같은 이름의 `.categories.csv`다.
- `data/yes24_details_2010_2026.part2.sqlite3.zip`은 상품별 검토 사유와 수집 상태를 포함한 전체 백업이다. 별도 폴더에 압축을 풀어 확인한다. 기존 실행 중인 SQLite를 덮어쓰지 않는다.
- 검토 대상은 분석 목적에 맞게 확인하고 사용한다. 정상 완료 수와 CSV 행 수는 검토 대상의 상세 데이터 존재 여부 때문에 다르다.
