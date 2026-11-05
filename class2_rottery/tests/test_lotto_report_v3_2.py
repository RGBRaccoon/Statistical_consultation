"""V3.2 보고서가 검토에서 제외한 칼럼을 빼고 남은 칼럼만 싣는지 검증한다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lotto_analysis_v3_1 as analysis  # noqa: E402
import lotto_report_v3_2 as report  # noqa: E402


def test_excluded_columns_are_removed_in_original_order():
    assert report.EXCLUDED == [
        "1게임당 당첨금",
        "총판매금액",
        "1000원기준_1게임당당첨금",
        "1000원기준_등위별총당첨금",
        "1억게임당_당첨게임수",
        "등위별_지급률",
    ]
    expected = [c for c in analysis.COMPARE_COLUMNS if c not in report.EXCLUDED]
    assert report.COLUMNS == expected
    assert len(report.COLUMNS) == 12
