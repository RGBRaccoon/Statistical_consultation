"""V3.2 그림·CSV·MD 보고서를 만든다. 계산은 lotto_analysis_v3_1에서 가져오고,
V3.1 검토에서 제외한 칼럼을 뺀다."""

import base64
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from scipy.stats import gaussian_kde  # noqa: E402

import lotto_analysis_v3_1 as analysis  # noqa: E402
from paths import DATA, REPORTS  # noqa: E402

SOURCE = DATA / "lotto_preprocessed_v3_1.csv"
OUTPUT = REPORTS / "v3_2" / "analysis_results_v3_2"
REPORT = REPORTS / "v3_2" / "lotto_rank_report_v3_2.md"

# V3.1 검토(plan_v3_1.md 6절)에서 제외한 칼럼과 사유.
EXCLUSION_REASONS = {
    "1게임당 당첨금": "총 당첨금 ÷ 당첨게임 수라 내용이 뻔함",
    "총판매금액": "금액에 2,000원 구간이 섞임. 판매게임수가 대신함",
    "1000원기준_1게임당당첨금": "제외 결정",
    "1000원기준_등위별총당첨금": "제외 결정",
    "1억게임당_당첨게임수": "당첨게임률 × 1억과 같은 값",
    "등위별_지급률": "총 당첨금 ÷ 총판매금액. 거의 일정한 비율이라 그룹 차이가 없음",
}
EXCLUDED = list(EXCLUSION_REASONS)
COLUMNS = [c for c in analysis.COMPARE_COLUMNS if c not in EXCLUSION_REASONS]

# 하위(파랑) ← 전체(회색) → 상위(빨강). 바깥 그룹일수록 진하다.
GROUP_COLORS = [
    "#1c5cab",
    "#3987e5",
    "#86b6ef",
    "#c3c2b7",
    "#f2a3a2",
    "#e66767",
    "#b22f2e",
]
COMBO_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"]
TEXT, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"
UNITS = [("억", 1e8), ("백만", 1e6), ("천", 1e3)]


def short_number(value: float, position: int = 0) -> str:
    """유효 숫자 최대 4자리. 1,000 이상은 천·백만·억 단위로 줄인다."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    for unit, scale in UNITS:
        if abs(value) >= scale:
            return f"{_sig4(value / scale)}{unit}"
    if value != 0 and abs(value) < 0.001:
        mantissa, exponent = f"{value:.3e}".split("e")
        return f"{_sig4(float(mantissa))}×10^{int(exponent)}"
    return _sig4(value)


def _sig4(value: float) -> str:
    text = f"{value:.4g}"
    if "e" in text:
        text = f"{value:.0f}"
    whole, _, frac = text.partition(".")
    whole = f"{int(whole):,}" if whole.lstrip("-").isdigit() else whole
    if whole == "0" and text.startswith("-"):
        whole = "-0"
    return f"{whole}.{frac}" if frac else whole


def violin_figure(
    rank: str, column: str, frame, selections, summary, path: Path
) -> None:
    fig, ax = plt.subplots(figsize=(11, 5.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    rows = summary[(summary["순위"] == rank) & (summary["칼럼"] == column)].set_index(
        "그룹"
    )
    medians, lows, highs, labels = [], [], [], []
    for x, (group, color) in enumerate(zip(analysis.GROUPS, GROUP_COLORS)):
        selection = selections[(rank, group.name)]
        weights = selection.weights()
        values = frame[column].reindex(weights.index)
        valid = values.notna()
        v, w = values[valid].to_numpy(float), weights[valid].to_numpy(float)
        row = rows.loc[group.name]
        if np.ptp(v) > 0:
            kde = gaussian_kde(v, weights=w)
            grid = np.linspace(v.min(), v.max(), 200)
            density = kde(grid)
            half = density / density.max() * 0.38
            ax.fill_betweenx(grid, x - half, x + half, color=color, alpha=0.55, lw=0)
        else:
            ax.plot([x - 0.3, x + 0.3], [v[0], v[0]], color=color, lw=3)
        ax.vlines(x, row["최소"], row["최대"], color=MUTED, lw=1)
        ax.add_patch(
            plt.Rectangle(
                (x - 0.06, row["가중Q1"]),
                0.12,
                row["가중Q3"] - row["가중Q1"],
                facecolor="white",
                edgecolor=TEXT,
                lw=1,
                zorder=3,
            )
        )
        medians.append(row["대표중앙값"])
        lows.append(row["최소"])
        highs.append(row["최대"])
        size = analysis.group_size(len(frame), group.share)
        tie = (
            f"\n동률 {len(selection.ties)}중 {selection.slots}자리"
            if selection.ties
            else ""
        )
        missing = f"\n결측 {row['결측가중합']:.0f}" if row["결측가중합"] else ""
        labels.append(f"{group.name}\nn={size}{tie}{missing}")
    # 최대·최소도 7개 그룹을 이어 범위가 바깥으로 어떻게 바뀌는지 보이게 한다.
    for values, name, offset, va in (
        (highs, "최대", 6, "bottom"),
        (lows, "최소", -6, "top"),
    ):
        ax.plot(
            range(7),
            values,
            color=MUTED,
            lw=1.2,
            ls="--",
            marker="o",
            ms=4,
            zorder=4,
            label=name,
        )
        for x, value in enumerate(values):
            ax.annotate(
                short_number(value),
                (x, value),
                xytext=(0, offset),
                textcoords="offset points",
                ha="center",
                va=va,
                fontsize=8,
                color=MUTED,
                zorder=5,
            )
    ax.plot(
        range(7),
        medians,
        color=TEXT,
        lw=2,
        marker="o",
        ms=8,
        markerfacecolor="white",
        zorder=4,
        label="대표 중앙값",
    )
    for x, m in enumerate(medians):
        ax.annotate(
            short_number(m),
            (x, m),
            xytext=(10, 6),
            textcoords="offset points",
            fontsize=9,
            color=TEXT,
            zorder=5,
        )
    ax.set_xticks(range(7), labels, fontsize=9, color=MUTED)
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    ax.tick_params(axis="y", colors=MUTED, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#d8d7d2")
    ax.spines["bottom"].set_color("#d8d7d2")
    ax.grid(axis="y", color="#ebeae6", lw=0.8)
    ax.set_axisbelow(True)
    ax.set_title(
        f"{column} · {rank} (당첨게임수 기준 그룹)",
        fontsize=13,
        color=TEXT,
        loc="left",
    )
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def overlap_figure(overlaps, path: Path) -> None:
    combos = list(dict.fromkeys(overlaps["조합"]))
    order = ["하위 5%", "하위 10%", "하위 25%", "상위 25%", "상위 10%", "상위 5%"]
    fig, ax = plt.subplots(figsize=(11, 5), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    width = 0.2
    for i, (combo, color) in enumerate(zip(combos, COMBO_COLORS)):
        rows = overlaps[overlaps["조합"] == combo].set_index("그룹").loc[order]
        xs = np.arange(len(order)) + (i - 1.5) * width
        ax.bar(xs, rows["겹침수"], width * 0.9, color=color, label=combo)
        for x, value in zip(xs, rows["겹침수"]):
            ax.text(
                x, value, f"{value}", ha="center", va="bottom", fontsize=8, color=TEXT
            )
    ax.set_xticks(range(len(order)), order, color=MUTED)
    ax.set_ylabel("겹치는 회차 수", color=MUTED)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#ebeae6", lw=0.8)
    ax.set_axisbelow(True)
    ax.set_title(
        "1·2·3등 그룹 사이에 겹치는 회차 수 (경계값과 같은 회차는 모두 포함)",
        fontsize=12,
        color=TEXT,
        loc="left",
    )
    ax.set_ylim(0, overlaps["겹침수"].max() * 1.08)
    ax.legend(
        frameon=False, fontsize=9, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.06)
    )
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def embedded(path: Path, caption: str) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"![{caption}](data:image/png;base64,{payload})\n"


def describe(summary, rank: str, column: str) -> str:
    rows = summary[(summary["순위"] == rank) & (summary["칼럼"] == column)].set_index(
        "그룹"
    )
    medians = [rows.loc[g.name, "대표중앙값"] for g in analysis.GROUPS]
    pattern = analysis.sign_pattern(medians)
    if all(s > 0 for s in pattern):
        trend = "하위 5%에서 상위 5%로 갈수록 중앙값이 계속 커진다"
    elif all(s < 0 for s in pattern):
        trend = "하위 5%에서 상위 5%로 갈수록 중앙값이 계속 작아진다"
    elif all(s == 0 for s in pattern):
        trend = "7개 그룹의 중앙값이 모두 같다"
    else:
        signs = " ".join("+" if s > 0 else "-" if s < 0 else "0" for s in pattern)
        trend = f"중앙값 증감이 한 방향이 아니다(이웃 그룹 간 {signs})"
    return (
        f"{rank}: 중앙값 하위 5% {short_number(medians[0])} → 전체 {short_number(medians[3])}"
        f" → 상위 5% {short_number(medians[6])}. {trend}."
    )


def render(result: dict[str, Any]) -> dict[str, Any]:
    OUTPUT.mkdir(exist_ok=True)
    figures_dir = OUTPUT / "figures"
    figures_dir.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False})
    result = {
        **result,
        "summary": result["summary"][result["summary"]["칼럼"].isin(COLUMNS)],
        "seed_check": result["seed_check"][result["seed_check"]["칼럼"].isin(COLUMNS)],
        "frames": {
            rank: frame[[c for c in frame.columns if c not in EXCLUSION_REASONS]]
            for rank, frame in result["frames"].items()
        },
    }
    for name in ("memberships", "ties", "summary", "overlaps", "seed_check"):
        result[name].to_csv(OUTPUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    for rank, frame in result["frames"].items():
        frame.to_csv(OUTPUT / f"values_{rank}.csv", encoding="utf-8-sig")

    summary, overlaps, ties = result["summary"], result["overlaps"], result["ties"]
    seeds = result["seed_check"]
    figures: list[str] = []

    overlap_path = figures_dir / "00_overlap.png"
    overlap_figure(overlaps, overlap_path)
    figures.append(overlap_path.name)

    tie_rank1 = ties[(ties["순위"] == "1등") & (ties["남은자리"] > 0)]
    full_match = int((seeds["패턴일치시드수"] == seeds["시드수"]).sum())
    lines = [
        "# 로또 분석 보고서 V3.2",
        "",
        "**큰 질문: 당첨게임수가 매우 많은 회차들의 특징은 무엇인가?**",
        "",
        "## 읽는 방법",
        "",
        "- 기준 값은 각 등수의 당첨게임수 그대로의 값이다. 1·2·3등을 따로 본다.",
        "- 1,245회를 당첨게임수로 줄 세워 위·아래에서 회차 수 기준 5%(62회)·10%(124회)·25%(311회)를 뽑고, 전체와 함께 7개 그룹을 비교한다. 그룹은 포함 관계다.",
        "- 그림마다 왼쪽부터 하위 5% → 하위 10% → 하위 25% → 전체 → 상위 25% → 상위 10% → 상위 5%다. 바이올린은 분포, 흰 상자는 가중 사분위 범위, 세로선은 최소~최대, 검은 선은 7개 대표 중앙값을, 점선은 7개 최대값·최소값을 이은 것이다.",
        "- 경계값에 동률이 있어 일부 자리만 남으면, 대표 중앙값은 남은 자리를 동률 회차들의 중앙값으로 채워 구하고, 분포는 동률 회차 모두에 `남은 자리 ÷ 동률 수` 가중치를 준다. 그림 아래 'n=62 / 동률 40중 20자리'처럼 표시한다.",
        "- 숫자는 유효 숫자 최대 4자리로 줄여 쓴다(천·백만·억). 상세 수치는 `analysis_results_v3_2/` CSV에 있다.",
        "- 이 보고서는 그룹 간 분포 차이를 보여 준다. 원인이나 통계적 유의성을 주장하지 않는다.",
        "",
        "## 데이터와 정규화",
        "",
        "- 입력: `lotto_preprocessed_v3_1.csv` (1~1245회). `판매게임수`는 총판매금액 ÷ 게임가격으로 정확한 값이다.",
        "- 1~87회는 게임가격 2,000원 구간으로 `가격구간`·`가격정규화계수` 칼럼이 표시한다.",
        "- 당첨번호 특징은 보너스번호를 뺀 6개 번호로 계산한다. `직전회차_겹침수`는 1회가 결측이다.",
        "",
        "## 질문 1. 1·2·3등의 상·하위 회차는 얼마나 겹치는가",
        "",
        "- 질문 1에서는 경계값과 같은 당첨게임수의 회차를 모두 그룹에 넣고 센다. 그래서 그룹 크기가 62·124·311보다 클 수 있다(대부분 1등).",
        "",
        embedded(overlap_path, "1·2·3등 그룹 겹침"),
    ]
    for group in ("상위 5%", "하위 5%"):
        part = overlaps[overlaps["그룹"] == group].set_index("조합")
        triple = part.loc["1등∩2등∩3등"]
        lines.append(
            f"- {group}: 세 등수 모두에 든 회차 {triple['겹침수']}개"
            + (f" ({triple['겹침회차']})" if triple["겹침회차"] else "")
            + ". 두 등수 겹침은 "
            + ", ".join(
                f"{c} {part.loc[c, '겹침수']}개"
                for c in part.index
                if c.count("∩") == 1
            )
            + "."
        )
    lines += [
        "- 모든 그룹의 겹치는 회차 번호는 `overlaps.csv`에 있다.",
        "",
        "## 질문 2. 당첨게임수가 많은 회차는 다른 칼럼에서 무엇이 다른가",
        "",
        f"V3.1에서 계산한 칼럼 {len(analysis.COMPARE_COLUMNS)}개 중 검토에서 제외한 {len(EXCLUDED)}개를 빼고 {len(COLUMNS)}개를 싣는다. 첫 칼럼은 그룹을 나눈 기준 값 자체다.",
        "",
    ]
    for index, column in enumerate(COLUMNS, start=1):
        lines += [f"### {index:02d}. {column}", ""]
        for rank in analysis.RANKS:
            path = figures_dir / f"{index:02d}_{rank}.png"
            violin_figure(
                rank,
                column,
                result["frames"][rank],
                result["selections"],
                summary,
                path,
            )
            figures.append(path.name)
            lines.append(embedded(path, f"{column} {rank}"))
        lines += [f"- {describe(summary, rank, column)}" for rank in analysis.RANKS]
        lines.append("")

    lines += [
        "## 경계 동률과 시드 확인",
        "",
        "- 1등은 당첨게임수가 작은 정수라 경계 동률이 크다: "
        + "; ".join(
            f"{r['그룹']} 경계 {short_number(r['경계값'])}, 동률 {r['동률수']}중 {r['남은자리']}자리"
            for _, r in tie_rank1.iterrows()
        )
        + ". 2·3등을 포함한 전체는 `ties.csv`에 있다.",
        f"- 1등 시드 {seeds['시드'].iloc[0]} 무작위 선택 100번: 칼럼 {len(seeds)}개 중 {full_match}개는 "
        "7개 그룹 중앙값의 증감 패턴이 100번 모두 대표 중앙값 방식과 같았다. "
        "나머지 칼럼의 일치 횟수: "
        + ", ".join(
            f"{r['칼럼']} {r['패턴일치시드수']}/100"
            for _, r in seeds[seeds["패턴일치시드수"] < seeds["시드수"]].iterrows()
        )
        + ". 상세는 `seed_check.csv`.",
        "",
        "## 부록",
        "",
        "- V3.1 검토에서 제외한 칼럼(그림은 `../v3_1/lotto_rank_report_v3_1.md`에 있다):",
        *[f"  - {c}: {reason}" for c, reason in EXCLUSION_REASONS.items()],
        "- CSV: `memberships.csv`(그룹 소속·가중치), `ties.csv`(경계 동률), `summary.csv`(그룹별 대표 중앙값·가중 사분위·최소·최대), `overlaps.csv`(겹침), `seed_check.csv`(시드 확인), `values_<등수>.csv`(회차별 값).",
        "",
    ]
    text = "\n".join(lines)
    REPORT.write_text(text, encoding="utf-8")
    manifest = {
        "source": SOURCE.name,
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "report": REPORT.name,
        "figures": figures,
        "embedded_images": text.count("data:image/png;base64,"),
        "seeds": "0~99 (1등 확인용)",
        "excluded_columns": EXCLUDED,
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    manifest = render(analysis.analyze(SOURCE))
    print(f"그림 {len(manifest['figures'])}개, 내장 {manifest['embedded_images']}개")


if __name__ == "__main__":
    main()
