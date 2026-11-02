"""V2.4 계산 결과를 정상·이상 비교 그림과 표 없는 보고서로 표현한다."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from build_lotto_v2_4_scaffold import (  # noqa: E402
    FEATURES,
    RANKS,
    TARGET_FEATURE,
    TOPICS,
)

NORMAL = "#527c9e"
HIGH = "#cc6527"


def number(value: float) -> str:
    """원자료의 수치를 생략하지 않고 읽기 좋은 단위로 표시한다."""
    if not np.isfinite(value):
        return "자료 없음"
    from report_units import display_number

    return display_number(value)


def save(fig: plt.Figure, path: Path) -> Path:
    fig.savefig(path, dpi=125, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


def subset(frame: pd.DataFrame, code: str, rank: str) -> pd.DataFrame:
    return frame.loc[frame["주제"].eq(code) & frame["순위"].eq(rank)]


def axis_style(ax: plt.Axes, unit: str) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: number(value)))
    ax.set_ylabel(unit)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.15)
    ax.set_axisbelow(True)


def paired_violin(
    frame: pd.DataFrame, column: str, title: str, unit: str, path: Path
) -> Path:
    """동일 등수 안에서 같은 축으로 정상·상방 이상 두 집단을 비교한다."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5))
    fig.suptitle(title, fontsize=15, y=0.99)
    rng = np.random.default_rng(240)
    for ax, rank in zip(axes, RANKS, strict=True):
        ranked = frame.loc[frame["순위"].eq(rank)]
        labels = []
        for pos, abnormal, color, name in (
            (1, False, NORMAL, "정상"),
            (2, True, HIGH, "상방 이상"),
        ):
            raw = ranked.loc[ranked["상방"].eq(abnormal), column]
            values = raw.dropna().to_numpy(dtype=float)
            count = len(values)
            labels.append(
                f"{name}\nn={count:,} · 결측 {raw.isna().sum():,}\n"
                + (
                    f"중앙값 {number(float(np.median(values)))}"
                    if count
                    else "자료 없음"
                )
            )
            if count > 1 and np.ptp(values) > 0:
                violin = ax.violinplot(
                    [values],
                    positions=[pos],
                    widths=0.74,
                    showmedians=False,
                    showextrema=False,
                )
                for body in violin["bodies"]:
                    body.set_facecolor(color)
                    body.set_edgecolor(color)
                    body.set_alpha(0.28)
            if count:
                jitter = rng.uniform(-0.11, 0.11, count)
                ax.scatter(
                    pos + jitter,
                    values,
                    color=color,
                    s=12 if abnormal else 5,
                    alpha=0.64 if abnormal else 0.18,
                    rasterized=True,
                    zorder=3,
                )
                ax.hlines(
                    np.median(values),
                    pos - 0.21,
                    pos + 0.21,
                    color=color,
                    linewidth=2.8,
                    zorder=4,
                )
            else:
                ax.text(
                    pos,
                    0.5,
                    "집단 없음",
                    ha="center",
                    transform=ax.get_xaxis_transform(),
                    color="#6c7580",
                )
        ax.set_title(rank, fontsize=13, pad=12)
        ax.set_xticks([1, 2], labels, fontsize=9)
        ax.set_xlim(0.45, 2.55)
        ax.margins(y=0.10)
        axis_style(ax, unit)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.22, wspace=0.35)
    return save(fig, path)


def selection_figure(frame: pd.DataFrame, topic: tuple, path: Path) -> Path:
    code, title, _, xlabel, ylabel, unit = topic
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    fig.suptitle(f"{code} · {title} — 정규화한 Y의 상방 IQR 판정", fontsize=15)
    for ax, rank in zip(axes, RANKS, strict=True):
        ranked = frame.loc[frame["순위"].eq(rank)]
        normal = ranked.loc[~ranked["상방"]]
        high = ranked.loc[ranked["상방"]]
        ax.scatter(normal["x"], normal["y"], color=NORMAL, s=8, alpha=0.28)
        ax.scatter(high["x"], high["y"], color=HIGH, s=20, alpha=0.8)
        if len(ranked):
            upper, strong = ranked.iloc[0][["상한", "강한상한"]]
            ax.axhline(upper, color=HIGH, linestyle="--", linewidth=1)
            ax.axhline(strong, color="#8c293a", linestyle=":", linewidth=1)
            for _, row in high.nlargest(2, "y").iterrows():
                ax.annotate(
                    f"{int(row['회차'])}회",
                    (row["x"], row["y"]),
                    xytext=(3, 4),
                    textcoords="offset points",
                    fontsize=8,
                )
            ax.set_title(
                f"{rank} · 정상 {len(normal):,} / 이상 {len(high):,}\n"
                f"상한 {number(upper)} · 강한 상한 {number(strong)}",
                fontsize=10,
            )
        ax.set_xlabel(xlabel, fontsize=9)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: number(value)))
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
        axis_style(ax, f"{ylabel}\n({unit})")
        ax.margins(y=0.12)
    fig.text(
        0.5,
        0.015,
        "파랑: 정상  /  주황: 상방 이상  /  점선: 1.5×IQR  /  촘촘한 점선: 3×IQR",
        ha="center",
        fontsize=10,
    )
    fig.subplots_adjust(left=0.075, right=0.985, top=0.80, bottom=0.15, wspace=0.38)
    return save(fig, path)


def overlap_figure(overlaps: pd.DataFrame, code: str, path: Path) -> Path:
    """정상·이상 발생 비율의 차이를 같은 등수별 연결점으로 비교한다."""
    targets = [item[0] for item in TOPICS if item[0] != code]
    data = overlaps.loc[overlaps["기준주제"].eq(code)]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.8), sharey=True)
    for ax, rank in zip(axes, RANKS, strict=True):
        for position, topic in enumerate(targets):
            row = data.loc[
                data["기준등수"].eq(rank)
                & data["비교주제"].eq(topic)
                & data["비교등수"].eq(rank)
            ].iloc[0]
            normal, high = row["정상발생비율"], row["이상발생비율"]
            if np.isfinite(normal) and np.isfinite(high):
                ax.plot(
                    [normal, high], [position, position], color="#c0c6ce", linewidth=2
                )
            if np.isfinite(normal):
                ax.scatter(normal, position, color=NORMAL, s=45, zorder=3)
            if np.isfinite(high):
                ax.scatter(high, position, color=HIGH, s=45, zorder=4)
            else:
                ax.text(0.55, position, "이상 집단 없음", fontsize=8, va="center")
        ax.set_yticks(range(len(targets)), targets)
        ax.set_xlim(-0.04, 1.04)
        ax.set_title(rank)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0%}"))
        ax.set_xlabel("다른 섹션에서 상방으로 나타난 비율")
        ax.grid(axis="x", alpha=0.15)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].invert_yaxis()
    axes[0].set_ylabel("같은 등수의 비교 섹션")
    fig.suptitle(f"{code} · 다른 섹션에서 상방 이상으로 나타난 비율", fontsize=15)
    fig.text(
        0.5,
        0.012,
        "파랑: 정상 집단 / 주황: 이상 집단. 같은 Y 반복은 독립 증거가 아님. 다른 등수의 전체 조합은 CSV에 보존.",
        ha="center",
        fontsize=10,
    )
    fig.subplots_adjust(left=0.07, right=0.985, top=0.84, bottom=0.20, wspace=0.20)
    return save(fig, path)


def embedded(path: Path, caption: str) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"![{caption}](data:image/png;base64,{payload})\n"


def comparison_text(rows: pd.DataFrame) -> str:
    statements = []
    for rank in RANKS:
        found = rows.loc[rows["순위"].eq(rank)]
        if found.empty:
            continue
        row = found.iloc[0]
        if not row["비교가능"]:
            statements.append(f"{rank}: 유효 관측 부족으로 두 집단 비교 불가.")
            continue
        normal, high = row["정상중앙값"], row["이상중앙값"]
        direction = (
            "높습니다" if high > normal else "낮습니다" if high < normal else "같습니다"
        )
        statements.append(
            f"{rank}: 정상 중앙값 {number(normal)}, 이상 중앙값 {number(high)}"
            f"({row['단위']}). 이상 집단의 중앙값이 {direction}."
        )
    return " ".join(statements)


def render(result: dict[str, Any], output: Path) -> dict[str, Any]:
    """실제 계산 결과만 사용하여 9개 섹션의 일관된 비교 보고서를 만든다."""
    output.mkdir(parents=True, exist_ok=True)
    charts = output / "figures"
    charts.mkdir(exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "Malgun Gothic",
            "axes.unicode_minus": False,
            "font.size": 10,
            "figure.max_open_warning": 5,
        }
    )
    figures: list[Path] = []
    plot_metadata: list[dict[str, Any]] = []
    lines = [
        "# 로또 분석 보고서 V2.4",
        "",
        "**실험 결과 · 1·2·3등 · 정상 회차와 상방 이상치 회차의 비교**",
        "",
        "## 읽는 방법",
        "금액은 `금액 × 1,000 / 해당 회차의 1게임 가격`으로 환산했습니다. "
        "판매액·한 게임당 당첨금·등수별 총당첨금에 같은 환산을 적용하고, "
        "가격 이외의 시기별 집단 분리는 하지 않았습니다. 당첨게임 수는 사람 수가 아닙니다.",
        "각 섹션·등수의 유효한 Y에서 Q1·Q3를 구해 `Y > Q3 + 1.5×IQR`만 "
        "상방 이상치로 선정했습니다. `3×IQR` 초과는 강한 이상치입니다. "
        "정상은 이 기준에 해당하지 않는 회차를 뜻하며 낮은 이상치를 별도로 찾지 않습니다.",
        "모든 비교 그림은 1·2·3등 패널별 정상·이상 두 분포, 총 여섯 자리를 사용합니다. "
        "같은 패널은 같은 축을 사용합니다. 등수별 크기가 달라 패널 사이의 축 범위는 다를 수 있습니다. "
        "실제 관측점·관측 수·결측 수·중앙값을 함께 표시하고, 한 건 또는 값이 모두 같으면 "
        "밀도를 추정하지 않고 점만 표시합니다. 바이올린의 폭은 집단 규모가 아닙니다.",
        "직전 값은 정확한 t−1입니다. 무당첨 회차의 한 게임당 당첨금은 결측으로 두고, "
        "당첨게임 수 0과 총당첨금 0은 유지합니다. 직전 값이 없으면 더 이전 회차로 대체하지 않습니다.",
        "비교는 관찰된 분포 차이를 설명합니다. 이상치라는 이유만으로 비리·원인·통계적 유의성을 "
        "확정하지 않습니다. 같은 Y를 사용하는 섹션의 반복 검출도 독립 증거로 세지 않습니다.",
        "",
    ]
    lines += [
        "## 먼저 확인할 관찰",
        f"각 섹션·등수의 상방 검출은 {len(result['outliers']):,}건이며 중복 회차를 합치면 {len(result['rounds']):,}회입니다. 동일 Y를 사용하는 섹션의 중복을 포함하므로 독립된 사건 수가 아닙니다.",
    ]
    for feature, label in (
        ("current_winners", "현재 한 게임당 당첨금이 높은 회차의 당첨게임 수"),
        ("previous_prize", "현재 한 게임당 당첨금이 높은 회차의 직전 당첨금"),
    ):
        selected = result["comparisons"].loc[
            result["comparisons"]["주제"].eq("01")
            & result["comparisons"]["특징"].eq(feature)
        ]
        lines += [f"**{label}:** " + comparison_text(selected)]
    lines += [
        "현재 당첨금과 당첨게임 수는 총당첨금 배분으로 수학적으로 연결됩니다. 직전 회차의 차이도 관찰된 특징이며 원인으로 확정하지 않습니다. 전체 기간 병합 결과이므로 가격 환산이 판매 규모나 장기 추세까지 제거한 것은 아닙니다.",
        "아래 01번의 해당·직전 비교 그림에서 분포와 실제 관측점을 함께 확인할 수 있습니다.",
    ]
    for topic in TOPICS:
        code, title, question, _, _, unit = topic
        cases = result["cases"].loc[result["cases"]["주제"].eq(code)]
        lines += [f"## {code}. {title}", "", question, "", "### 이상치 선정", ""]
        if code == "10":
            lines += [
                "구간 분리를 제거하고 같은 가격 기준으로 합쳐 이 섹션의 X·Y는 01번과 같습니다. 기존 섹션을 보존해 결과를 보여주며 독립적인 발견으로 세지 않습니다."
            ]
        path = selection_figure(cases, topic, charts / f"{code}_selection.png")
        figures.append(path)
        lines += [embedded(path, f"{code} 정규화 Y 산점도와 상방 경계")]
        fences = result["fences"].loc[result["fences"]["주제"].eq(code)]
        lines.append(
            " ".join(
                f"{row['순위']}: 유효 {int(row['관측수']):,}회 중 이상 {int(row['상방수']):,}회"
                f"(강한 이상 {int(row['강한상방수']):,}회)."
                for _, row in fences.iterrows()
            )
        )
        path = paired_violin(
            cases,
            "y",
            f"{code} · 선정된 Y의 정상·이상 분포",
            unit,
            charts / f"{code}_y_violin.png",
        )
        figures.append(path)
        plot_metadata.append(violin_metadata(cases, code, "y", path))
        lines += [
            embedded(path, f"{code} 선정 Y의 정상·이상 6개 분포"),
            "이 그림의 Y 차이는 선정 기준을 확인하는 결과입니다. 공통 특징의 새로운 발견으로 해석하지 않습니다.",
            "",
        ]
        for prefix, heading in (
            ("current_", "해당 회차: 정상과 이상의 차이"),
            ("previous_", "직전 회차: 정상과 이상의 차이"),
        ):
            lines += [f"### {heading}", ""]
            for key, name, feature_unit, feature_question in FEATURES:
                if not key.startswith(prefix):
                    continue
                lines += [f"#### {name}", "", feature_question, ""]
                if TARGET_FEATURE.get(code) == key:
                    lines += [
                        "**선정 기준 확인용:** 이 변수는 해당 섹션의 Y 자체입니다. 두 집단의 차이가 새로운 설명은 아닙니다.",
                        "",
                    ]
                elif code in ("09A", "09B") and key.split("_")[1] == (
                    "prize" if code == "09A" else "winners"
                ):
                    lines += [
                        "**해석 주의:** 이 변수는 Y 변화량의 구성요소이므로 차이가 선정 방식과 연결될 수 있습니다.",
                        "",
                    ]
                path = paired_violin(
                    cases,
                    key,
                    f"{code} · {name} — 정상 회차 vs 상방 이상 회차",
                    feature_unit,
                    charts / f"{code}_{key}.png",
                )
                figures.append(path)
                plot_metadata.append(violin_metadata(cases, code, key, path))
                lines += [embedded(path, f"{code} {name} 정상·이상 6개 분포")]
                compared = result["comparisons"].loc[
                    result["comparisons"]["주제"].eq(code)
                    & result["comparisons"]["특징"].eq(key)
                ]
                lines += [comparison_text(compared), ""]
        lines += [
            "### 다른 섹션에서도 같은 회차가 튀었는가?",
            "",
            "앞의 정상·이상 집단 비교 이후에 확인하는 보조 결과입니다. "
            "같은 등수의 다른 섹션에서 상방이 발생한 비율을 정상·이상 집단의 두 점과 연결선으로 비교했습니다. 양쪽에 모두 관측이 있는 회차만 분모에 포함합니다. 다른 등수까지 포함한 전체 조합은 overlaps.csv에 있습니다.",
            "",
        ]
        path = overlap_figure(result["overlaps"], code, charts / f"{code}_overlap.png")
        figures.append(path)
        lines += [embedded(path, f"{code} 정상·이상 집단별 다른 섹션 상방 비율"), ""]
    lines += [
        "## 재현과 근거",
        "",
        "수치는 원본 CSV에서 가격을 환산하고 실제 실행한 계산 결과입니다. "
        "설명문은 LLM 직접 작성물입니다. 개별 회차와 계산값은 같은 결과 폴더의 CSV로 보존합니다.",
        "상방 IQR의 1.5·3배 경계는 "
        "[NIST 설명](https://www.itl.nist.gov/div898/handbook/prc/section1/prc16.htm)을 따릅니다.",
        "그림은 문서 안에 PNG로 내장하여 외부 이미지 서버 없이 배포할 수 있습니다.",
        "",
    ]
    document = "\n\n".join(lines)
    md_path = output / "lotto_rank_report_v2_4.md"
    md_path.write_text(document, encoding="utf-8")
    metadata_path = output / "plot_metadata.json"
    metadata_path.write_text(
        json.dumps(plot_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "markdown": md_path,
        "figures": figures,
        "figure_count": len(figures),
        "plot_metadata": metadata_path,
    }


def violin_metadata(
    frame: pd.DataFrame, code: str, column: str, path: Path
) -> dict[str, Any]:
    """렌더링에 실제 전달된 모든 유효 관측 수를 별도로 보존한다."""
    groups = []
    for rank in RANKS:
        for abnormal in (False, True):
            raw = frame.loc[frame["순위"].eq(rank) & frame["상방"].eq(abnormal), column]
            values = raw.dropna()
            groups.append(
                {
                    "rank": rank,
                    "outlier": abnormal,
                    "n": len(values),
                    "missing": int(raw.isna().sum()),
                    "density_drawn": len(values) > 1
                    and float(values.max() - values.min()) > 0,
                }
            )
    return {
        "topic": code,
        "feature": column,
        "path": str(path),
        "panels": 3,
        "distribution_slots": 6,
        "groups": groups,
    }
