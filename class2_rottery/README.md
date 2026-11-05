# 로또 6/45 통계 분석 (class2_rottery)

동행복권 로또 6/45 1~1245회의 회차·등수별 당첨 자료를 분석하는 수업 과제다. 버전마다 질문과 방법을 바꿔 가며 보고서를 만들었다.

## 폴더 구조

```
class2_rottery/
├── data/        원본·전처리 CSV
├── src/         분석·보고서·검증 코드 (공통 경로는 src/paths.py)
├── tests/       pytest 테스트
├── reports/     버전별 보고서와 분석 결과 폴더
│   ├── v1/  v2/  v2_1/  v2_3/  v2_4/  v3_1/  v3_2/
└── docs/
    ├── insights.md   주제별 인사이트 정리
    ├── feedback/     수업 피드백 (요약, 녹취)
    ├── plans/        버전별 계획·지침·아이디어
    ├── tasks/        V2 시리즈 Task 문서
    └── logs/         HTML 변환·단위 갱신 실행 기록
```

보고서는 그림을 base64로 내장한 단일 MD(일부 HTML)라서 파일만 열면 그림까지 보인다. 보고서 옆의 `analysis_results_*` 폴더에 그림 원본과 상세 CSV가 있다.

## 데이터

| 파일 | 내용 |
|---|---|
| `data/lotto_rank_1_1245.csv` | 동행복권 API에서 받은 원본(`src/lotto_crawler.py`) |
| `data/lotto_preprocessed_v2.csv` | V2 전처리: 게임가격 결합(1~87회 2,000원, 88회~ 1,000원), 이론 기대값 등 |
| `data/lotto_preprocessed_v3_1.csv` | V2 전처리 + 실제 판매게임수(총판매금액 ÷ 게임가격) |

## 버전

| 버전 | 보고서 | 질문·방법 | 코드 |
|---|---|---|---|
| V1 | `reports/v1/lotto_rank_report.md` (+ 영문) | 최대 기대수익 분석 | `lotto_analysis.py` |
| V2 | `reports/v2/lotto_rank_report_v2.md` | 전처리 정의(Step 0)와 등수별 회차 분석(Step 1) | `lotto_analysis_v2.py` |
| V2.1 | `reports/v2_1/lotto_rank_report_v2_1.md` | 1·2·3등 일반 현황과 분포 비교. 회차별 편차 12분석 초안은 `analysis_results_v2_1/` | `lotto_analysis_v2_1_basic.py`, `lotto_analysis_v2_1.py` |
| V2.3 | `reports/v2_3/lotto_rank_report_v2_3.md` | 상방 IQR 기준 이상 회차와 후속 비교 | `lotto_analysis_v2_3.py` |
| V2.4 | `reports/v2_4/lotto_rank_report_v2_4.md` | 가격 환산 후 전체 회차의 정상·이상 비교(계산 전 틀: `*_scaffold.md`) | `lotto_analysis_v2_4.py`, `lotto_report_v2_4.py` |
| V3.1 | `reports/v3_1/lotto_rank_report_v3_1.md` | 당첨게임 수 원값 기준 상·하위 5·10·25%와 전체 7그룹 비교, 칼럼 18개 | `lotto_analysis_v3_1.py`, `lotto_report_v3_1.py` |
| V3.2 | `reports/v3_2/lotto_rank_report_v3_2.md` (+ 영문) | V3.1에서 검토 후 제외한 칼럼을 뺀 12개 | `lotto_report_v3_2.py` |

V2 시리즈 지침은 `docs/plans/rules_v2.md`, V3 계획과 검토 메모는 `docs/plans/plan_v3_1.md`, `docs/plans/plan_v3_2.md`에 있다. V3까지의 결론과 다음 방향(추첨 확률 기반 이항 기대값과의 비교)은 `docs/insights.md`와 `docs/feedback/피드백 1102.txt`를 참고한다.

## 실행

저장소 루트에서 실행한다.

```bash
python -X utf8 class2_rottery/src/preprocess_v3_1.py     # data/lotto_preprocessed_v3_1.csv
python -X utf8 class2_rottery/src/lotto_report_v3_1.py   # reports/v3_1/
python -X utf8 class2_rottery/src/lotto_report_v3_2.py   # reports/v3_2/
```

V1·V2 시리즈 스크립트도 같은 방식(`class2_rottery/src/<스크립트>.py`)으로 실행하며, 기본 입출력 경로는 `data/`와 `reports/<버전>/`이다.

## 검증

```bash
cd class2_rottery
python -m pytest -q -p no:cacheprovider
ruff check .
ruff format --check src tests
```

## 알려진 문제

- V2 시리즈 검증 스크립트 중 `verify_lotto_v2_1_basic.py`, `verify_lotto_v2_1.py`, `verify_lotto_v2_3.py`, `verify_report_units.py`는 실패한다. 폴더 재구성 전 커밋에서도 같은 단언에서 실패하므로 재구성과 무관하다. 생성 후 HTML 재변환·단위 갱신으로 보고서와 결과 폴더 사본이 달라진 것으로 보인다.
- V2 검증 스크립트는 실행할 때 `independent_audit.json` 등 감사 파일을, `verify_report_units.py`는 보고서 MD의 단위 표기를 다시 쓴다. 산출물을 보존하려면 실행 후 `git diff`로 확인한다.
- `docs/logs/report_units_log.json`의 CSV 해시 키는 재구성 전 경로(`class2_rottery/analysis_results*/...`) 기준이다.
