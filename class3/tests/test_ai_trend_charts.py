import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "ai_trend_charts.py"
spec = importlib.util.spec_from_file_location("ai_trend_charts", MODULE)
charts = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = charts
spec.loader.exec_module(charts)


def test_select_years_keeps_requested_period():
    rows = [{"year": year} for year in range(2008, 2027)]
    selected = charts.select_years(rows, 2010, 2026)
    assert [row["year"] for row in selected] == list(range(2010, 2027))


def test_trailing_average_uses_up_to_three_years():
    assert charts.trailing_average([3, 6, 12, 21], window=3) == [3.0, 4.5, 7.0, 13.0]


def test_chart_series_has_union_and_three_groups():
    assert [series["field"] for series in charts.COUNT_SERIES] == [
        "ai_any_count",
        "ai_general_count",
        "ai_technical_count",
        "ai_service_count",
    ]
    assert [series["field"] for series in charts.PERCENT_SERIES] == [
        "ai_any_pct",
        "ai_general_pct",
        "ai_technical_pct",
        "ai_service_pct",
    ]
