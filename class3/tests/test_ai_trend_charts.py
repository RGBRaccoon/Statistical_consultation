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


def test_year_label_keeps_both_events_of_the_same_year_in_order():
    label = charts.year_tick_label(2023, sample_size=149)
    assert label.splitlines() == ["2023", "n=149", "3월 GPT-4", "12월 Gemini"]
    assert "ChatGPT" not in label


def test_year_label_preserves_partial_year_and_sample_size():
    label = charts.year_tick_label(2026, partial=True, sample_size=0)
    assert label.splitlines() == ["2026*", "n=0", "6월 Fable 5"]
    assert charts.year_tick_label(2021) == "2021"


def test_grouped_chart_displays_events_below_the_year(monkeypatch):
    captured = []

    def capture(figure, *args, **kwargs):
        captured.extend(tick.get_text() for tick in figure.axes[0].get_xticklabels())

    monkeypatch.setattr(charts.plt.Figure, "savefig", capture)
    rows = [
        {
            "year": year,
            "is_partial_year": year == 2026,
            **{item["field"]: 10 for item in charts.COUNT_SERIES},
        }
        for year in (2023, 2026)
    ]
    charts.render_grouped_chart(
        rows,
        charts.COUNT_SERIES,
        MODULE.parent / "reports" / "figures" / "unused.png",
        "테스트",
        "권수",
    )
    assert captured == [
        "2023\n3월 GPT-4\n12월 Gemini",
        "2026*\n6월 Fable 5",
    ]
