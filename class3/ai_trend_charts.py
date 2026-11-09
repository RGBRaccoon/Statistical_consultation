import argparse
import csv
import unicodedata
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager

COLORS = {
    "전체 AI": "#284B63",
    "일반": "#F4A261",
    "기술": "#2A9D8F",
    "서비스": "#8E6C9F",
}

COUNT_SERIES = [
    {"field": "ai_any_count", "label": "전체 AI"},
    {"field": "ai_general_count", "label": "일반"},
    {"field": "ai_technical_count", "label": "기술"},
    {"field": "ai_service_count", "label": "서비스"},
]

PERCENT_SERIES = [
    {"field": "ai_any_pct", "label": "전체 AI"},
    {"field": "ai_general_pct", "label": "일반"},
    {"field": "ai_technical_pct", "label": "기술"},
    {"field": "ai_service_pct", "label": "서비스"},
]

# 공개·발표 시점이다. 짧은 축 표기의 전체 명칭과 출처는 보고서 3.1에 둔다.
AI_EVENTS = (
    (2016, 3, "알파고 대국"),
    (2017, 6, "Transformer"),
    (2022, 11, "ChatGPT"),
    (2023, 3, "GPT-4"),
    (2023, 12, "Gemini"),
    (2024, 3, "Opus"),
    (2025, 8, "나노 바나나"),
    (2026, 6, "Fable 5"),
)


def year_tick_label(year, *, partial=False, sample_size=None):
    """연도·기존 표본 수 아래에 해당 연도의 사건을 월순으로 표시한다."""
    lines = [f"{year}{'*' if partial else ''}"]
    if sample_size is not None:
        lines.append(f"n={sample_size:,}")
    for event_year, month, label in AI_EVENTS:
        if event_year != year:
            continue
        event = f"{month}월 {label}"
        columns = sum(
            2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in event
        )
        lines.append(event if columns <= 14 else f"{month}월\n{label}")
    return "\n".join(lines)


def configure_korean_font():
    for name in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
        except ValueError:
            continue
        plt.rcParams["font.family"] = name
        break
    plt.rcParams["axes.unicode_minus"] = False


def load_summary(path):
    integer_fields = {
        "year",
        "total_books",
        "ai_any_count",
        "ai_general_count",
        "ai_technical_count",
        "ai_service_count",
    }
    percent_fields = {
        "ai_any_pct",
        "ai_general_pct",
        "ai_technical_pct",
        "ai_service_pct",
    }
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            parsed = dict(row)
            for field in integer_fields:
                parsed[field] = int(parsed[field])
            for field in percent_fields:
                parsed[field] = float(parsed[field])
            parsed["is_partial_year"] = parsed["is_partial_year"].lower() == "true"
            rows.append(parsed)
    return rows


def select_years(rows, start_year, end_year):
    return [row for row in rows if start_year <= row["year"] <= end_year]


def trailing_average(values, window=3):
    averages = []
    for index in range(len(values)):
        start = max(0, index - window + 1)
        current = values[start : index + 1]
        averages.append(sum(current) / len(current))
    return averages


def value_label(value, percentage):
    if value == 0:
        return ""
    return f"{value:.1f}%" if percentage else f"{int(value):,}"


def render_grouped_chart(rows, series, output, title, y_label, percentage=False):
    configure_korean_font()
    positions = list(range(len(rows)))
    width = 0.19
    offsets = (-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width)
    figure, axis = plt.subplots(figsize=(18, 9))

    max_value = 0.0
    for item, offset in zip(series, offsets, strict=True):
        values = [row[item["field"]] for row in rows]
        max_value = max(max_value, max(values, default=0))
        color = COLORS[item["label"]]
        bars = axis.bar(
            [position + offset for position in positions],
            values,
            width=width,
            label=item["label"],
            color=color,
            alpha=0.78,
            edgecolor="white",
            linewidth=0.5,
        )
        axis.bar_label(
            bars,
            labels=[value_label(value, percentage) for value in values],
            padding=2,
            fontsize=7,
            rotation=90,
        )
        axis.plot(
            positions,
            trailing_average(values),
            color=color,
            linewidth=2.2,
            marker="o",
            markersize=3.5,
            zorder=4,
        )

    partial_positions = [
        index for index, row in enumerate(rows) if row["is_partial_year"]
    ]
    for position in partial_positions:
        axis.axvspan(position - 0.5, position + 0.5, color="#777777", alpha=0.08)

    tick_labels = [
        year_tick_label(row["year"], partial=row["is_partial_year"]) for row in rows
    ]
    axis.set_xticks(positions, tick_labels, fontsize=8)
    axis.set_title(title, fontsize=18, fontweight="bold", pad=22)
    axis.set_xlabel("발행연도")
    axis.set_ylabel(y_label)
    axis.set_ylim(0, max_value * 1.19 if max_value else 1)
    axis.grid(axis="y", alpha=0.22, linestyle="--")
    axis.legend(ncol=4, loc="upper left", frameon=False)
    axis.text(
        0.995,
        1.015,
        "실선: 분류별 3년 후행 이동평균",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=10,
        color="#555555",
    )
    figure.text(
        0.01,
        0.012,
        "일반·기술·서비스는 중복될 수 있으며, 전체 AI는 세 분류의 합집합이다. "
        "*2026년은 1~11월 부분 집계.",
        fontsize=10,
        color="#555555",
    )
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def parse_args():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="AI 도서 연도별 그림 생성")
    parser.add_argument(
        "--input",
        type=Path,
        default=base / "reports" / "analysis_results" / "ai_book_yearly.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=base / "reports" / "figures")
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2026)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = select_years(load_summary(args.input), args.start_year, args.end_year)
    if not rows:
        raise ValueError("표시할 연도별 집계가 없습니다.")
    render_grouped_chart(
        rows,
        COUNT_SERIES,
        args.output_dir / "ai_books_yearly_count.png",
        "연도별 AI 관련 도서 수",
        "도서 수(권)",
    )
    render_grouped_chart(
        rows,
        PERCENT_SERIES,
        args.output_dir / "ai_books_yearly_share.png",
        "연도별 전체 IT·모바일 도서 중 AI 관련 도서 비율",
        "비율(%)",
        percentage=True,
    )
    print(f"그림 생성 완료: {args.output_dir}")


if __name__ == "__main__":
    main()
