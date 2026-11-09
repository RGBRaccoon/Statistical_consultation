"""기존 AI 키워드 사전을 유지하며 추가 분석과 보조 민감도 결과를 생성한다."""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ai_trend_analysis import MONTH_PATTERN, KeywordClassifier, load_books, write_csv
from ai_trend_charts import configure_korean_font

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "reports" / "analysis_results"
FIGURES = BASE / "reports" / "figures"
GROUPS = ("general", "technical", "service")
COMBINATIONS = {
    "100": "일반만",
    "010": "기술만",
    "001": "서비스만",
    "110": "일반+기술",
    "101": "일반+서비스",
    "011": "기술+서비스",
    "111": "일반+기술+서비스",
}
PALETTE = ["#e6a05f", "#25877d", "#8e6c9f", "#516d8a"]


def exclusive_group(row):
    return "".join(str(int(row[group])) for group in GROUPS)


def conditional_overlap(rows, denominator, other):
    selected = [row for row in rows if row[denominator]]
    if not selected:
        return None
    return sum(row[other] for row in selected) * 100 / len(selected)


def growth_rate(previous, current):
    return (current - previous) * 100 / previous if previous else None


def service_families(labels):
    # ChatGPT와 GPT가 같은 책에 일치해도 서비스 계열별로는 한 권이다.
    return {"ChatGPT/GPT" if label in {"ChatGPT", "GPT"} else label for label in labels}


def is_title_variant(title):
    return bool(
        re.search(
            r"큰\s*(?:글자|글씨)|대활자|(?<![가-힣A-Za-z])세트(?![가-힣A-Za-z])", title
        )
    )


def classify_books(rows, classifier, cutoff="2026-11"):
    records = []
    for row in rows:
        month = row["publication_month"].strip()
        if not MONTH_PATTERN.fullmatch(month) or month > cutoff:
            continue
        result = classifier.classify(row["title"], row["subtitle"])
        records.append(
            {
                "year": int(month[:4]),
                "month": month,
                "title": row["title"],
                "subtitle": row["subtitle"],
                "general": result["is_ai_general"],
                "technical": result["is_ai_technical"],
                "service": result["is_ai_service"],
                "any": result["is_ai_any"],
                "technical_keywords": result["technical_keywords"],
                "service_keywords": sorted(
                    service_families(result["service_keywords"])
                ),
                "title_only_ai": classifier.classify(row["title"], "")["is_ai_any"],
                "title_variant": is_title_variant(row["title"]),
            }
        )
    return records


def pct(numerator, denominator):
    return numerator * 100 / denominator if denominator else None


def compute_tables(records):
    by_year = defaultdict(list)
    for record in records:
        by_year[record["year"]].append(record)
    growth = []
    composition = []
    sensitivity = []
    for year, rows in sorted(by_year.items()):
        ai = sum(row["any"] for row in rows)
        non_ai = len(rows) - ai
        previous = by_year.get(year - 1, [])
        previous_ai = sum(row["any"] for row in previous)
        share = pct(ai, len(rows))
        growth.append(
            {
                "year": year,
                "total": len(rows),
                "ai_count": ai,
                "non_ai_count": non_ai,
                "ai_share_pct": share,
                "ai_yoy_pct": growth_rate(previous_ai, ai)
                if previous and year < 2026
                else None,
                "non_ai_yoy_pct": growth_rate(len(previous) - previous_ai, non_ai)
                if previous and year < 2026
                else None,
                "ai_share_change_pp": share - pct(previous_ai, len(previous))
                if previous and year < 2026
                else None,
                "partial_year": year == 2026,
            }
        )
        patterns = Counter(exclusive_group(row) for row in rows if row["any"])
        # 4개 집단은 상호 배타적이며 합이 전체 AI 도서 수와 같다.
        counts = {
            "general_only": patterns["100"],
            "technical_without_service": patterns["010"] + patterns["110"],
            "service_without_technical": patterns["001"] + patterns["101"],
            "technical_and_service": patterns["011"] + patterns["111"],
        }
        composition.append(
            {"year": year, "ai_count": ai}
            | counts
            | {f"{key}_pct": pct(value, ai) for key, value in counts.items()}
        )
        for scenario in ("baseline", "title_only", "exclude_title_variants"):
            retained = (
                [row for row in rows if not row["title_variant"]]
                if scenario == "exclude_title_variants"
                else rows
            )
            ai_count = sum(
                row["title_only_ai"] if scenario == "title_only" else row["any"]
                for row in retained
            )
            sensitivity.append(
                {
                    "year": year,
                    "scenario": scenario,
                    "total": len(retained),
                    "ai_count": ai_count,
                    "ai_share_pct": pct(ai_count, len(retained)),
                    "excluded_count": len(rows) - len(retained),
                    "excluded_ai_count": ai - sum(row["any"] for row in retained),
                }
            )
    patterns = Counter(exclusive_group(row) for row in records if row["any"])
    union_total = sum(patterns.values())
    overlap = [
        {
            "combination": key,
            "label": label,
            "count": patterns[key],
            "pct_of_ai": pct(patterns[key], union_total),
        }
        for key, label in COMBINATIONS.items()
    ]
    conditional = [
        {
            "denominator": denominator,
            "other": other,
            "denominator_count": sum(row[denominator] for row in records),
            "overlap_count": sum(row[denominator] and row[other] for row in records),
            "overlap_pct": conditional_overlap(records, denominator, other),
        }
        for denominator in GROUPS
        for other in GROUPS
        if other != denominator
    ]
    keyword_rows = []
    for group in ("technical", "service"):
        all_labels = sorted(
            {label for row in records for label in row[f"{group}_keywords"]}
        )
        for year, rows in sorted(by_year.items()):
            group_total = sum(row[group] for row in rows)
            counts = Counter(
                label for row in rows for label in row[f"{group}_keywords"]
            )
            for label in all_labels:
                keyword_rows.append(
                    {
                        "group": group,
                        "year": year,
                        "keyword": label,
                        "count": counts[label],
                        "group_count": group_total,
                        "pct_within_group": pct(counts[label], group_total),
                    }
                )
    return {
        "ai_growth": growth,
        "ai_overlap": overlap,
        "ai_conditional_overlap": conditional,
        "ai_composition": composition,
        "ai_keyword_yearly": keyword_rows,
        "ai_sensitivity": sensitivity,
    }


def save_figure(figure, name, footnote):
    figure.text(0.015, 0.015, footnote, fontsize=9, color="#555555")
    figure.tight_layout(rect=(0, 0.06, 1, 1))
    FIGURES.mkdir(parents=True, exist_ok=True)
    figure.savefig(FIGURES / name, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def market_context_series(rows, start=2010, end=2026):
    selected = []
    for row in sorted(rows, key=lambda item: item["year"]):
        if not start <= row["year"] <= end:
            continue
        total = row["total"]
        ai_count = row["ai_count"]
        other_count = row["non_ai_count"]
        if min(total, ai_count, other_count) < 0 or ai_count + other_count != total:
            raise ValueError(f"{row['year']}년 누적 합계가 전체 상품 수와 다릅니다.")
        selected.append({**row, "ai_share_pct": pct(ai_count, total)})
    return selected


def draw_market_context(rows):
    selected = market_context_series(rows)
    if not selected:
        raise ValueError("전체 규모 그림에 표시할 자료가 없습니다.")
    positions = list(range(len(selected)))
    ai_counts = [row["ai_count"] for row in selected]
    totals = [row["total"] for row in selected]
    figure, axis = plt.subplots(figsize=(15, 8))
    ai_bars = axis.bar(
        positions,
        ai_counts,
        width=0.64,
        color=PALETTE[1],
        label="AI 키워드 일치 상품",
        edgecolor="white",
        linewidth=0.5,
    )
    other_bars = axis.bar(
        positions,
        [row["non_ai_count"] for row in selected],
        bottom=ai_counts,
        width=0.64,
        color="#d9dfe5",
        label="AI 키워드 미검출 상품",
        edgecolor="white",
        linewidth=0.5,
    )
    complete_positions = [
        i for i, row in enumerate(selected) if not row["partial_year"]
    ]
    axis.plot(
        complete_positions,
        [totals[i] for i in complete_positions],
        color="#465564",
        marker="o",
        markersize=4,
        linewidth=1.7,
        label="전체 IT·모바일 상품 수",
        zorder=4,
    )
    for index, (ai_bar, other_bar, row) in enumerate(
        zip(ai_bars, other_bars, selected, strict=True)
    ):
        axis.text(
            index,
            row["total"] + 38,
            f"{row['total']:,}",
            ha="center",
            va="bottom",
            fontsize=9,
            color="#344454",
        )
        if row["ai_count"]:
            large_segment = row["ai_count"] >= 200
            axis.text(
                index,
                row["ai_count"] / 2 if large_segment else row["ai_count"] + 18,
                f"AI\n{row['ai_count']:,}"
                if large_segment
                else f"AI {row['ai_count']}",
                ha="center",
                va="center" if large_segment else "bottom",
                color="white" if large_segment else "#176b63",
                fontsize=9 if large_segment else 8,
                bbox={"facecolor": PALETTE[1], "edgecolor": "none", "pad": 1}
                if large_segment
                else None,
            )
        if row["partial_year"]:
            ai_bar.set_hatch("///")
            other_bar.set_hatch("///")
            axis.axvspan(index - 0.48, index + 0.48, color="#777777", alpha=0.06)
            if index:
                axis.plot(
                    [index - 1, index],
                    [totals[index - 1], row["total"]],
                    color="#465564",
                    linestyle="--",
                    linewidth=1.2,
                    marker="o",
                    markersize=4,
                )
    axis.set_xticks(
        positions,
        [f"{row['year']}{'*' if row['partial_year'] else ''}" for row in selected],
        rotation=45,
        ha="right",
    )
    axis.set_ylim(0, max(totals) * 1.19)
    axis.set_ylabel("도서 상품 수(개)")
    axis.set_xlabel("표시 발행연도")
    axis.set_title(
        "전체 IT·모바일 상품 규모와 그 안의 AI 관련 상품", fontsize=17, pad=18
    )
    axis.legend(loc="upper left", ncol=3, frameon=False)
    axis.grid(axis="y", alpha=0.2, linestyle="--")
    axis.set_axisbelow(True)
    save_figure(
        figure,
        "ai_market_context.png",
        "연도별 막대 높이=전체 상품 수, 녹색=AI 일치 상품. 선은 전체 관측값을 연결한 것. "
        "*2026년은 11월 8일 수집 당시 1~11월 표시 상품(출간 예정 포함).",
    )


def draw_growth(rows):
    selected = [row for row in rows if 2016 <= row["year"] <= 2025]
    figure, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    years = [row["year"] for row in selected]
    for key, label, color in (
        ("ai_yoy_pct", "AI 관련", PALETTE[1]),
        ("non_ai_yoy_pct", "AI 키워드 미검출", PALETTE[3]),
    ):
        axes[0].plot(
            years, [row[key] for row in selected], "o-", color=color, label=label
        )
    axes[0].set_title("AI 관련 도서와 나머지 도서의 연도별 증가율")
    axes[0].set_ylabel("전년 대비 증감률(%)")
    axes[0].legend(frameon=False)
    axes[0].axhline(0, color="#888888", linewidth=0.8)
    bars = axes[1].bar(
        years, [row["ai_share_change_pp"] for row in selected], color=PALETTE[1]
    )
    axes[1].bar_label(bars, fmt="%.1f", padding=3)
    axes[1].set_ylabel("AI 비중의 전년 대비 변화(%p)")
    axes[1].set_xticks(years)
    axes[1].set_xlabel("발행연도")
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
    save_figure(
        figure,
        "ai_growth.png",
        "2026년은 부분 연도이므로 전년 대비 증가율 비교에서 제외했다.",
    )


def draw_overlap(rows):
    figure, axis = plt.subplots(figsize=(11, 6))
    ordered = sorted(rows, key=lambda row: row["count"])
    bars = axis.barh(
        [row["label"] for row in ordered],
        [row["count"] for row in ordered],
        color=PALETTE[3],
    )
    axis.bar_label(
        bars,
        labels=[f"{row['count']:,}권 · {row['pct_of_ai']:.1f}%" for row in ordered],
        padding=5,
    )
    axis.set_xlim(0, max(row["count"] for row in rows) * 1.3)
    axis.set_title("AI 관련 도서의 중복 없는 7개 분류 조합")
    axis.set_xlabel("도서 수(권)")
    union_count = sum(row["count"] for row in rows)
    save_figure(
        figure,
        "ai_overlap.png",
        f"발행월이 유효하고 2026-11 이하인 전체 자료. 7개 조합의 합은 전체 AI 도서 {union_count:,}권이다.",
    )


def draw_composition(rows):
    selected = [row for row in rows if 2016 <= row["year"] <= 2026]
    figure, axis = plt.subplots(figsize=(12, 7))
    positions = list(range(len(selected)))
    bottom = [0] * len(selected)
    for key, label, color in zip(
        (
            "general_only",
            "technical_without_service",
            "service_without_technical",
            "technical_and_service",
        ),
        (
            "일반만",
            "기술 포함·서비스 없음",
            "서비스 포함·기술 없음",
            "기술+서비스 동시 포함",
        ),
        PALETTE,
        strict=True,
    ):
        values = [row[f"{key}_pct"] for row in selected]
        bars = axis.bar(positions, values, bottom=bottom, color=color, label=label)
        for bar, value, base in zip(bars, values, bottom, strict=True):
            if value >= 7:
                axis.text(
                    bar.get_x() + bar.get_width() / 2,
                    base + value / 2,
                    f"{value:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=9,
                )
        bottom = [base + value for base, value in zip(bottom, values, strict=True)]
    labels = [
        f"{row['year']}{'*' if row['year'] == 2026 else ''}\nn={row['ai_count']:,}"
        for row in selected
    ]
    axis.set_xticks(positions, labels)
    axis.set_ylim(0, 100)
    axis.set_ylabel("해당 연도 AI 도서 중 비율(%)")
    axis.set_title("AI 관련 도서 내부 구성의 변화")
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False)
    save_figure(
        figure,
        "ai_composition.png",
        "4개 집단은 상호 배타적이다. *2026년은 수집 시점의 1~11월 표시 상품이며 완결 연도가 아니다.",
    )


def draw_keywords(rows, group, start, end, top_n):
    selected = [
        row for row in rows if row["group"] == group and start <= row["year"] <= end
    ]
    totals = Counter()
    for row in selected:
        totals[row["keyword"]] += row["count"]
    labels = [label for label, _ in totals.most_common(top_n)]
    lookup = {(row["keyword"], row["year"]): row for row in selected}
    years = list(range(start, end + 1))
    values = [
        [lookup[label, year]["pct_within_group"] or 0 for year in years]
        for label in labels
    ]
    figure, axis = plt.subplots(
        figsize=(max(9, len(years) * 1.1), max(6, len(labels) * 0.48))
    )
    heatmap = axis.imshow(values, cmap="YlGnBu", aspect="auto", vmin=0, vmax=100)
    axis.set_yticks(range(len(labels)), labels)
    axis.set_xticks(
        range(len(years)),
        [
            f"{year}{'*' if year == 2026 else ''}\nn={lookup[labels[0], year]['group_count']}"
            for year in years
        ],
    )
    for y, label in enumerate(labels):
        for x, year in enumerate(years):
            row = lookup[label, year]
            axis.text(
                x,
                y,
                f"{row['count']}권\n{(row['pct_within_group'] or 0):.1f}%",
                ha="center",
                va="center",
                color="white" if values[y][x] >= 55 else "#222222",
                fontsize=8,
            )
    title = "기술" if group == "technical" else "서비스"
    axis.set_title(f"{title} 키워드별 도서 수와 해당 분류 내 비율", pad=15)
    figure.colorbar(heatmap, ax=axis, label=f"{title} 분류 도서 중 비율(%)", shrink=0.8)
    save_figure(
        figure,
        f"ai_{group}_keywords.png",
        "같은 연도에 여러 키워드가 중복될 수 있어 비율 합은 100%가 아니다. *2026년은 부분 연도.",
    )


def draw_sensitivity(rows):
    selected = [row for row in rows if 2020 <= row["year"] <= 2026]
    figure, axis = plt.subplots(figsize=(11, 6))
    for scenario, label, color in zip(
        ("baseline", "title_only", "exclude_title_variants"),
        ("기준: 제목+부제", "제목만 검색", "큰글자·세트 명시 상품 제외"),
        (PALETTE[3], PALETTE[2], PALETTE[1]),
        strict=True,
    ):
        current = [row for row in selected if row["scenario"] == scenario]
        axis.plot(
            [row["year"] for row in current],
            [row["ai_share_pct"] for row in current],
            "o-",
            color=color,
            label=label,
        )
        for row in current:
            if row["year"] < 2023:
                continue
            offset = (
                10
                if scenario == "baseline"
                else (-14 if scenario == "title_only" else 3)
            )
            axis.annotate(
                f"{row['ai_share_pct']:.1f}",
                (row["year"], row["ai_share_pct"]),
                textcoords="offset points",
                xytext=(0, offset),
                ha="center",
                fontsize=8,
                color=color,
            )
    axis.set_title("검색 범위와 상품 형태에 따른 AI 도서 비율 변화")
    axis.set_xticks(
        list(range(2020, 2027)),
        [str(year) + ("*" if year == 2026 else "") for year in range(2020, 2027)],
    )
    axis.set_ylabel("각 시나리오의 분석 대상 중 AI 도서 비율(%)")
    axis.set_ylim(0, 85)
    axis.legend(frameon=False)
    axis.grid(axis="y", alpha=0.2)
    save_figure(
        figure,
        "ai_sensitivity.png",
        "상품 제외 시 분자와 분모를 모두 재계산했다. 제목에 명시된 큰글자·큰글씨·대활자·독립된 ‘세트’만 제외. *부분 연도.",
    )


def main():
    configure_korean_font()
    classifier = KeywordClassifier.from_json(BASE / "ai_keywords.json")
    records = classify_books(
        load_books(BASE / "data" / "yes24_it_mobile_list.csv"), classifier
    )
    tables = compute_tables(records)
    validate_tables(tables, len(records))
    for name, rows in tables.items():
        write_csv(OUTPUT / f"{name}.csv", rows, list(rows[0]))
    draw_growth(tables["ai_growth"])
    draw_overlap(tables["ai_overlap"])
    draw_composition(tables["ai_composition"])
    draw_keywords(tables["ai_keyword_yearly"], "technical", 2016, 2026, 10)
    draw_keywords(tables["ai_keyword_yearly"], "service", 2023, 2026, 10)
    draw_sensitivity(tables["ai_sensitivity"])
    draw_market_context(tables["ai_growth"])
    print(
        json.dumps(
            {
                "eligible": len(records),
                "ai_count": sum(row["any"] for row in records),
                "figures": 7,
                "csv_files": len(tables),
            },
            ensure_ascii=False,
        )
    )


def validate_tables(tables, eligible_count):
    growth = tables["ai_growth"]
    assert sum(row["total"] for row in growth) == eligible_count
    assert sum(row["ai_count"] for row in growth) == sum(
        row["count"] for row in tables["ai_overlap"]
    )
    for row in growth:
        assert row["ai_count"] + row["non_ai_count"] == row["total"]
        if row["partial_year"]:
            assert row["ai_yoy_pct"] is None
            assert row["non_ai_yoy_pct"] is None
            assert row["ai_share_change_pp"] is None
    for row in tables["ai_composition"]:
        count = sum(
            row[key]
            for key in (
                "general_only",
                "technical_without_service",
                "service_without_technical",
                "technical_and_service",
            )
        )
        assert count == row["ai_count"]
    for row in tables["ai_keyword_yearly"]:
        assert 0 <= row["count"] <= row["group_count"]


if __name__ == "__main__":
    main()
