"""승인된 일반 현황 9개 질문으로 로또 보고서 V2.1을 작성한다.

본문은 원단위의 금액·당첨게임 수, 비교 바이올린만 가격 환산 후 Z점수다.
분석별 개별 등수 결과 3개와 비교 결과 1개를 연속으로 배치한다.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from paths import DATA, REPORTS  # noqa: E402

RANKS = ("1등", "2등", "3등")
COLORS = {"1등": "#1768ac", "2등": "#c46100", "3등": "#208251"}
PERIODS = ("1~87회", "88~400회", "401회 이후")


def topic(
    code: str,
    title: str,
    question: str,
    xlabel: str,
    ylabel: str,
    kind: str = "scatter",
    reason: str = "",
) -> dict[str, str]:
    return {
        "id": code,
        "title": title,
        "question": question,
        "xlabel": xlabel,
        "ylabel": ylabel,
        "plot": kind,
        "plot_reason": reason
        or "산점도로 각 관측값의 X·Y 위치와 극단 회차를 직접 확인한다.",
    }


TOPICS = (
    topic(
        "01",
        "한 게임당 당첨금",
        "한 게임이 당첨됐을 때 받는 금액은 얼마인가?",
        "회차",
        "당첨금 (당시 명목 원)",
    ),
    topic(
        "02",
        "당첨게임 수",
        "회차마다 당첨게임은 몇 개인가?",
        "회차",
        "당첨게임 수 (게임)",
    ),
    topic(
        "03",
        "등수별 총당첨금",
        "각 등수에 지급된 총당첨금은 얼마인가?",
        "회차",
        "등수별 총당첨금 (당시 명목 원)",
    ),
    topic(
        "08",
        "같은 회차 판매량과 당첨금",
        "판매량이 많은 회차일수록 한 게임당 당첨금이 커지는가?",
        "판매게임 수 (게임)",
        "한 게임당 당첨금 (당시 명목 원)",
    ),
    topic(
        "09A",
        "직전 회차 대비 당첨금 증감",
        "한 게임당 당첨금은 직전 회차보다 증가했는가, 감소했는가?",
        "회차",
        "현재−직전 당첨금 (명목 원)",
    ),
    topic(
        "09B",
        "직전 회차 대비 당첨게임 수 증감",
        "당첨게임 수는 직전 회차보다 증가했는가, 감소했는가?",
        "회차",
        "현재−직전 당첨게임 수 (게임)",
    ),
    topic(
        "09C",
        "직전 당첨금과 현재 당첨게임 수",
        "직전 회차 한 게임당 당첨금과 이번 회차 당첨게임 수는 어떤 관계인가?",
        "직전 한 게임당 당첨금 (명목 원)",
        "현재 당첨게임 수 (게임)",
    ),
    topic(
        "09D",
        "직전 당첨게임 수와 현재 당첨금",
        "직전 회차 당첨게임 수와 이번 회차 한 게임당 당첨금은 어떤 관계인가?",
        "직전 당첨게임 수 (게임)",
        "현재 한 게임당 당첨금 (명목 원)",
    ),
    topic(
        "10",
        "시기별 당첨금 수준",
        "시기가 지나면서 한 게임당 당첨금의 수준이 어떻게 달라졌는가?",
        "제도 구간",
        "한 게임당 당첨금 (1,000원 가격 기준 원)",
        "box",
        "회차별 산점도는 01에서 제공한다. 여기서는 구간별 중앙값·사분위 범위·극단값을 비교하므로 상자그림으로 대체한다. 구간은 알려진 가격·지급 구조 경계인 88회·401회로 나눈다. 이는 그래프 선택에 대한 설계 판단이며 인과관계의 증거가 아니다.",
    ),
)

SOURCES = (
    (
        "산점도의 X·Y 표현",
        "https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.scatter.html",
    ),
    (
        "상자그림의 중앙값·사분위·수염",
        "https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.boxplot.html",
    ),
    (
        "바이올린의 밀도·극값·중앙값 표시",
        "https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.violinplot.html",
    ),
    (
        "직전 행 정렬 원리",
        "https://pandas.pydata.org/pandas-docs/version/2.2/reference/api/pandas.Series.shift.html",
    ),
    (
        "V2의 88회 가격 전환 근거",
        "https://www.donga.com/news/Series/article/all/20040721/8086157/1",
    ),
)


def digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def analyze(source: str | Path) -> dict[str, Any]:
    original = pd.read_csv(source, encoding="utf-8-sig")
    data = (
        original.loc[original["순위"].isin(RANKS)]
        .sort_values(["순위", "회차"])
        .reset_index(drop=True)
        .copy()
    )
    if data.duplicated(["순위", "회차"]).any():
        raise ValueError("회차·등수 키 중복")
    if not data.groupby("회차")["순위"].agg(set).map(lambda x: x == set(RANKS)).all():
        raise ValueError("회차별 1·2·3등 누락")
    data["기간"] = np.select(
        [data["회차"].le(87), data["회차"].le(400)], PERIODS[:2], default=PERIODS[2]
    )
    # 필터하기 전에 회차를 명시적으로 t−1 키로 결합한다. 무당첨 회차를 건너뛰지 않는다.
    previous = data[
        ["회차", "순위", "당첨게임 수", "1게임당 당첨금", "1000원기준_1게임당당첨금"]
    ].copy()
    previous = previous.rename(
        columns={
            "회차": "직전회차",
            "당첨게임 수": "직전게임수",
            "1게임당 당첨금": "직전당첨금",
            "1000원기준_1게임당당첨금": "직전환산당첨금",
        }
    )
    previous["회차"] = previous["직전회차"] + 1
    data = data.merge(previous, on=["회차", "순위"], how="left", validate="one_to_one")

    observations, excluded, normalization, summaries, groups = [], [], [], [], []
    for item in TOPICS:
        code = item["id"]
        for rank in RANKS:
            frame = data.loc[data["순위"].eq(rank)].copy()
            reasons = pd.Series("", index=frame.index)
            requires_current_prize = code in ("01", "08", "09A", "09D", "10")
            requires_previous_prize = code in ("09A", "09C")
            if requires_current_prize:
                reasons.loc[frame["당첨게임 수"].eq(0)] = (
                    "현재 당첨게임 없음: 1게임당 당첨금은 지급액 관측이 아님"
                )
            if code.startswith("09"):
                reasons.loc[frame["직전회차"].isna()] = (
                    "정확한 직전 회차가 입력 자료에 없음"
                )
            if requires_previous_prize:
                mask = frame["직전게임수"].eq(0)
                reasons.loc[mask] = reasons.loc[mask].map(
                    lambda s: (s + "; " if s else "")
                    + "직전 당첨게임 없음: 직전 당첨금은 지급액 관측이 아님"
                )
            omitted = frame.loc[reasons.ne(""), ["회차", "순위"]].copy()
            omitted["주제"] = code
            omitted["제외이유"] = reasons.loc[omitted.index]
            excluded.append(omitted)
            frame = frame.loc[reasons.eq("")].copy()
            frame["x"] = frame["회차"].astype(float)
            frame["y"] = frame["1게임당 당첨금"].astype(float)
            frame["비교값"] = frame["1000원기준_1게임당당첨금"].astype(float)
            if code == "02":
                frame["y"] = frame["당첨게임 수"].astype(float)
                frame["비교값"] = frame["y"]
            elif code == "03":
                frame["y"] = frame["등위별 총 당첨금"].astype(float)
                frame["비교값"] = frame["1000원기준_등위별총당첨금"].astype(float)
            elif code == "08":
                frame["x"] = frame["추정_판매게임수"].astype(float)
            elif code == "09A":
                frame["y"] = frame["1게임당 당첨금"] - frame["직전당첨금"]
                frame["비교값"] = (
                    frame["1000원기준_1게임당당첨금"] - frame["직전환산당첨금"]
                )
            elif code == "09B":
                frame["y"] = frame["당첨게임 수"] - frame["직전게임수"]
                frame["비교값"] = frame["y"]
            elif code == "09C":
                frame["x"] = frame["직전당첨금"]
                frame["y"] = frame["당첨게임 수"].astype(float)
                frame["비교값"] = frame["y"]
            elif code == "09D":
                frame["x"] = frame["직전게임수"]
            elif code == "10":
                frame["x"] = frame["기간"].map({p: i for i, p in enumerate(PERIODS)})
                frame["y"] = frame["1000원기준_1게임당당첨금"].astype(float)
                frame["비교값"] = frame["y"]

            frame["주제"] = code
            center = float(frame["비교값"].mean())
            scale = float(frame["비교값"].std(ddof=1))
            if scale <= 0:
                raise ValueError(f"{code} {rank}: 비교값의 분산이 없다.")
            frame["비교Z"] = (frame["비교값"] - center) / scale
            frame["비교구간"] = "전체"
            predictor = frame["x"]
            if code == "09C":
                predictor = frame["직전환산당첨금"]
            frame["구간X"] = predictor
            if code in ("08", "09C", "09D"):
                lo, hi = predictor.quantile([1 / 3, 2 / 3])
                frame["비교구간"] = np.select(
                    [predictor.le(lo), predictor.gt(hi)],
                    ["X 하위", "X 상위"],
                    default="X 중위",
                )
                normalization.append(
                    {
                        "주제": code,
                        "순위": rank,
                        "중심": center,
                        "표준편차": scale,
                        "X하위경계": float(lo),
                        "X상위경계": float(hi),
                        "X구간정의": "09C는 가격환산 직전 당첨금; 그 외 본문 X",
                    }
                )
            else:
                if code == "10":
                    frame["비교구간"] = frame["기간"]
                normalization.append(
                    {
                        "주제": code,
                        "순위": rank,
                        "중심": center,
                        "표준편차": scale,
                        "X하위경계": None,
                        "X상위경계": None,
                        "X구간정의": "해당 없음",
                    }
                )

            y = frame["y"]
            largest = frame.loc[y.idxmax()]
            smallest = frame.loc[y.idxmin()]
            extreme = frame.loc[frame["비교Z"].abs().idxmax()]
            summaries.append(
                {
                    "주제": code,
                    "순위": rank,
                    "입력회차수": len(reasons),
                    "포함회차수": len(frame),
                    "제외회차수": len(omitted),
                    "본문Y평균": float(y.mean()),
                    "본문Y중앙값": float(y.median()),
                    "본문Y표준편차": float(y.std(ddof=1)),
                    "본문Y최소": float(y.min()),
                    "최소회차": int(smallest["회차"]),
                    "본문Y최대": float(y.max()),
                    "최대회차": int(largest["회차"]),
                    "본문Y25%": float(y.quantile(0.25)),
                    "본문Y75%": float(y.quantile(0.75)),
                    "비교값평균": center,
                    "비교값중앙값": float(frame["비교값"].median()),
                    "표준화최소": float(frame["비교Z"].min()),
                    "표준화최대": float(frame["비교Z"].max()),
                    "표시기준3초과": int(frame["비교Z"].abs().gt(3).sum()),
                    "최대절대편차회차": int(extreme["회차"]),
                    "증가회차수": int(y.gt(0).sum())
                    if code in ("09A", "09B")
                    else None,
                    "감소회차수": int(y.lt(0).sum())
                    if code in ("09A", "09B")
                    else None,
                    "동일회차수": int(y.eq(0).sum())
                    if code in ("09A", "09B")
                    else None,
                }
            )
            for group_name, group in frame.groupby("비교구간", sort=False):
                groups.append(
                    {
                        "주제": code,
                        "순위": rank,
                        "비교구간": group_name,
                        "관측수": len(group),
                        "본문Y중앙값": float(group["y"].median()),
                        "비교값중앙값": float(group["비교값"].median()),
                        "비교Z중앙값": float(group["비교Z"].median()),
                        "비교Z최소": float(group["비교Z"].min()),
                        "비교Z최대": float(group["비교Z"].max()),
                    }
                )
            observations.append(
                frame[
                    [
                        "주제",
                        "회차",
                        "순위",
                        "기간",
                        "직전회차",
                        "x",
                        "y",
                        "구간X",
                        "비교값",
                        "비교Z",
                        "비교구간",
                    ]
                ]
            )
    return {
        "topics": list(TOPICS),
        "data": data,
        "observations": pd.concat(observations, ignore_index=True),
        "excluded": pd.concat(excluded, ignore_index=True),
        "normalization": pd.DataFrame(normalization),
        "summary": pd.DataFrame(summaries),
        "groups": pd.DataFrame(groups),
    }


def money_tick(value: float, _position: int) -> str:
    from report_units import display_number

    return display_number(value)


def draw(result: dict[str, Any], output: Path) -> dict[tuple[str, str], Path]:
    plt.rcParams.update(
        {
            "font.family": "Malgun Gothic",
            "axes.unicode_minus": False,
            "axes.grid": True,
            "grid.alpha": 0.2,
            "font.size": 10,
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    paths = {}
    rng = np.random.default_rng(20261026)
    for item in TOPICS:
        code = item["id"]
        all_data = result["observations"].loc[result["observations"]["주제"].eq(code)]
        for rank_index, rank in enumerate(RANKS, 1):
            data = all_data.loc[all_data["순위"].eq(rank)].sort_values("회차")
            fig, ax = plt.subplots(figsize=(10.5, 4.1))
            ax.set_title(f"{code}. {item['title']} · {rank}", fontweight="bold", pad=15)
            if item["plot"] == "box":
                arrays = [data.loc[data["기간"].eq(p), "y"].to_numpy() for p in PERIODS]
                ax.boxplot(
                    arrays,
                    tick_labels=PERIODS,
                    showmeans=True,
                    flierprops={
                        "marker": ".",
                        "markersize": 4,
                        "alpha": 0.5,
                        "markeredgecolor": COLORS[rank],
                    },
                )
                for pos, array in enumerate(arrays, 1):
                    ax.scatter(
                        pos + rng.uniform(-0.12, 0.12, len(array)),
                        array,
                        s=5,
                        alpha=0.12,
                        color=COLORS[rank],
                    )
                ax.text(
                    0.99,
                    0.97,
                    "상자: 25~75% / 선: 중앙값 / 삼각형: 평균",
                    transform=ax.transAxes,
                    ha="right",
                    va="top",
                    fontsize=8,
                )
            else:
                ax.scatter(
                    data["x"],
                    data["y"],
                    s=11,
                    alpha=0.55,
                    color=COLORS[rank],
                    linewidths=0,
                )
                if item["xlabel"] == "회차":
                    for boundary in (87.5, 400.5):
                        ax.axvline(
                            boundary, color="#999999", linestyle=":", linewidth=0.8
                        )
                largest = data.loc[data["y"].abs().nlargest(3).index]
                for label_index, (_, row) in enumerate(largest.iterrows()):
                    ax.scatter(row["x"], row["y"], color="#9b0036", s=24, zorder=5)
                    fraction = (row["x"] - data["x"].min()) / max(
                        1, data["x"].max() - data["x"].min()
                    )
                    dx = 8 if fraction < 0.8 else -65
                    dy = (8, -20, 22)[label_index]
                    ax.annotate(
                        f"{int(row['회차'])}회",
                        (row["x"], row["y"]),
                        xytext=(dx, dy),
                        textcoords="offset points",
                        fontsize=9,
                        arrowprops={"arrowstyle": "-", "lw": 0.6, "color": "#555555"},
                    )
                if code in ("09A", "09B"):
                    ax.axhline(0, color="#333333", linewidth=0.8)
                ax.margins(x=0.07, y=0.18)
                if code not in ("09A", "09B"):
                    ax.set_ylim(bottom=0)
            ax.set_xlabel(item["xlabel"].replace("−", "-"))
            ax.set_ylabel(item["ylabel"].replace("−", "-"))
            if code not in ("02", "09B", "09C"):
                ax.yaxis.set_major_formatter(FuncFormatter(money_tick))
            if code == "09C":
                ax.xaxis.set_major_formatter(FuncFormatter(money_tick))
            elif code in ("08", "09D"):
                ax.xaxis.set_major_formatter(FuncFormatter(money_tick))
            else:
                ax.ticklabel_format(axis="x", style="plain", useOffset=False) if item[
                    "plot"
                ] != "box" else None
            if code in ("02", "09B", "09C"):
                ax.yaxis.set_major_formatter(
                    FuncFormatter(lambda value, pos: f"{value:,.0f}")
                )
            fig.tight_layout()
            path = output / f"{code}_rank_{rank_index}.png"
            fig.savefig(path, dpi=120)
            plt.close(fig)
            paths[(code, rank)] = path

        fig, axes = plt.subplots(
            1, 2, figsize=(11.5, 4.5), gridspec_kw={"width_ratios": [1, 1.8]}
        )
        fig.suptitle(
            f"{code}. {item['title']} · 1·2·3등 비교 바이올린",
            fontweight="bold",
            fontsize=13,
        )
        values = [
            all_data.loc[all_data["순위"].eq(rank), "비교Z"].to_numpy()
            for rank in RANKS
        ]
        artists = axes[0].violinplot(
            values, showmedians=True, showextrema=True, points=180
        )
        for rank, body in zip(RANKS, artists["bodies"]):
            body.set_facecolor(COLORS[rank])
            body.set_alpha(0.5)
        for index, rank in enumerate(RANKS, 1):
            data = all_data.loc[all_data["순위"].eq(rank)]
            extreme = data.loc[data["비교Z"].abs().idxmax()]
            axes[0].scatter(
                index + rng.uniform(-0.08, 0.08, len(data)),
                data["비교Z"],
                s=3,
                alpha=0.1,
                color=COLORS[rank],
            )
            axes[0].scatter(index, extreme["비교Z"], color="#9b0036", s=22)
            axes[0].annotate(
                f"{int(extreme['회차'])}회",
                (index, extreme["비교Z"]),
                xytext=(5, 6),
                textcoords="offset points",
                fontsize=8,
            )
        axes[0].set_xticks([1, 2, 3], RANKS)
        axes[0].set_ylabel("등수별 Z점수 (금액만 가격 환산)")
        axes[0].axhline(0, color="#444444", linewidth=0.7)
        for boundary in (-3, 3):
            axes[0].axhline(boundary, linestyle="--", color="#888888", linewidth=0.6)
        if code in ("08", "09C", "09D", "10"):
            group_names = PERIODS if code == "10" else ("X 하위", "X 중위", "X 상위")
            for group_index, group_name in enumerate(group_names):
                for rank_index, rank in enumerate(RANKS):
                    data = all_data.loc[
                        all_data["순위"].eq(rank) & all_data["비교구간"].eq(group_name)
                    ]
                    position = group_index * 4 + rank_index + 1
                    array = data["비교Z"].to_numpy()
                    if np.std(array) > 0:
                        one = axes[1].violinplot(
                            [array],
                            positions=[position],
                            widths=0.8,
                            showmedians=True,
                            showextrema=True,
                        )
                        one["bodies"][0].set_facecolor(COLORS[rank])
                        one["bodies"][0].set_alpha(0.5)
                        for line_name in ("cmins", "cmaxes", "cbars", "cmedians"):
                            one[line_name].set_color(COLORS[rank])
                    else:
                        axes[1].scatter([position], [array[0]], color=COLORS[rank])
            axes[1].set_xticks([2, 6, 10], group_names)
            axes[1].set_xlabel("좌→우 색상: 1등·2등·3등 / 등수별 동일 기준 Z")
            axes[1].set_ylabel(
                "X 구간별 Y 비교Z" if code != "10" else "시기별 당첨금 비교Z"
            )
        else:
            for rank in RANKS:
                array = np.sort(
                    all_data.loc[all_data["순위"].eq(rank), "비교Z"].to_numpy()
                )
                axes[1].step(
                    array,
                    np.arange(1, len(array) + 1) / len(array),
                    where="post",
                    color=COLORS[rank],
                    label=rank,
                )
            axes[1].set_xlabel("등수별 Z점수 (금액만 가격 환산)")
            axes[1].set_ylabel("경험적 누적비율")
            axes[1].legend()
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        path = output / f"{code}_violin.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        paths[(code, "violin")] = path
    return paths


def fmt(value: Any) -> str:
    if pd.isna(value):
        return "해당 없음"
    if isinstance(value, (float, np.floating)):
        return f"{value:,.4f}" if abs(value) < 10 else f"{value:,.2f}"
    return str(value).replace("|", "/").replace("\n", " ")


def table(frame: pd.DataFrame) -> str:
    lines = [
        "| " + " | ".join(map(str, frame.columns)) + " |",
        "| " + " | ".join("---" for _ in frame.columns) + " |",
    ]
    lines += [
        "| " + " | ".join(fmt(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    ]
    return "\n".join(lines)


def make_report(result: dict[str, Any], paths: dict, source_hash: str) -> str:
    def image(code: str, rank: str) -> str:
        payload = base64.b64encode(paths[(code, rank)].read_bytes()).decode("ascii")
        return f"![{code} {rank}](data:image/png;base64,{payload})"

    sections = [
        "# 로또 보고서 V2.1 — 1·2·3등의 일반 현황과 분포 비교",
        "## 범위와 구성",
        "사용자 피드백에 따라 가공된 편차 중심의 이전 초안을 정정했다. 승인된 질문 1·2·3·8·10과 "
        "9-A/B/C/D만 실행한다. 거절된 질문 4·5·6·7 및 추가 제안 A/B는 다루지 않는다. "
        "각 주제는 **1등 분석 → 2등 분석 → 3등 분석 → 비교표·바이올린** 순서다. "
        "9개 주제, 등수별 원단위 그림 27개와 비교 바이올린 9개로 총 36개 그림이다.",
        "## 먼저 알아둘 데이터의 뜻",
        "‘당첨자’의 원자료 칼럼은 `당첨게임 수`다. 사람 수를 확인할 수 없으므로 이 보고서는 게임 수로 표현한다. "
        "본문 01·03·08·09A·09C·09D의 금액은 당시 명목 원이다. 10의 시기 비교와 비교 바이올린의 "
        "금액은 V2의 게임 가격(1~87회 2,000원, 88회 이후 1,000원)을 이용해 "
        "`금액 × 1,000 / 게임가격`으로 환산한다. 명목금액과 가격 환산 금액을 같은 값으로 부르지 않는다. "
        "세금과 물가는 적용하지 않는다.",
        "당첨게임이 없는 회차의 `1게임당 당첨금=0`은 당첨자가 0원을 받았다는 관측이 아니다. "
        "따라서 당첨금 관측이 필요한 분석에서만 제외한다. 당첨게임 수와 지급 총액에서의 0은 "
        "실제 관측값으로 유지한다. 정확한 직전 회차를 연결한 다음 필요한 관측이 없는 쌍을 제외하며 "
        "무당첨 회차를 건너뛰어 두 회차 전과 비교하지 않는다. 전체 제외 목록은 `excluded.csv`에 저장했다.",
        "## 그림과 ‘튀는 값’ 읽기",
        "본문은 실제 금액과 게임 수로 읽는다. 비교 바이올린만 `Z=(비교값−등수별 평균)/등수별 표본표준편차`로 "
        "척도를 맞춘다. 정규화는 정규분포로 바꾸는 작업이 아니다. 바이올린의 폭은 밀도 추정, 선은 "
        "중앙값·전체 극값, 점은 관측값을 뜻한다. |Z|>3은 시각적 표시 기준이며 통계적 유의성 판정이 아니다. "
        "극단값이 포함되어 표준편차가 커질 수 있으므로 원값·중앙값·최소·최대도 함께 확인한다.",
        "관계 분석 08·09C·09D의 바이올린은 세 등수의 전체 분포뿐 아니라 X 하위·중위·상위 구간별 Y 분포를 "
        "함께 표시한다. 09C의 X 구간은 가격 환산 직전 당첨금으로 정한다. X 구간은 원인이나 유의성 기준이 "
        "아닌 기술적 비교용 3분위 구간이다. 단순한 Y의 바이올린 하나로 두 변수의 관계를 "
        "판정하지 않는다.",
        table(
            pd.DataFrame(
                [
                    {
                        "주제": item["id"],
                        "질문": item["question"],
                        "본문 그림": "상자그림" if item["plot"] == "box" else "산점도",
                    }
                    for item in TOPICS
                ]
            )
        ),
    ]
    summary = result["summary"]
    for item in TOPICS:
        code = item["id"]
        sections += [
            f"## {code}. {item['title']}",
            item["question"],
            f"**그림 선택 근거:** {item['plot_reason']}",
            f"본문 X축: {item['xlabel']}. Y축: {item['ylabel']}.",
        ]
        comparison_unit = (
            "게임 수의 차이"
            if code == "09B"
            else "당첨게임 수"
            if code in ("02", "09C")
            else "1,000원 가격 기준 당첨금의 차이(원)"
            if code == "09A"
            else "1,000원 가격 기준 금액(원)"
        )
        sections.append(
            f"비교 바이올린의 Z점수 계산 전 비교값 단위: **{comparison_unit}**."
        )
        if code == "09A":
            sections.append(
                "변화량은 현재 명목 당첨금−직전 명목 당첨금이다. 비교 바이올린은 각 회차의 당첨금을 먼저 1,000원 가격 기준으로 환산한 뒤 차이를 계산한다. 88회 경계에서는 두 차이가 다를 수 있다."
            )
        if code == "09B":
            sections.append(
                "변화량은 현재 당첨게임 수−직전 당첨게임 수다. 증가·감소·동일 회차를 센다. 0인 게임 수를 포함하며 ‘한 사람이 몇 번 당첨됐는가’를 뜻하지 않는다."
            )
        if code == "10":
            sections.append(
                "달력 날짜가 없으므로 연도·월 효과를 추정하지 않는다. 알려진 제도 구간인 1~87회, 88~400회, 401회 이후를 비교한다. 기간에 따른 수준 차이가 제도 변경의 인과 효과라는 주장은 하지 않는다."
            )
        for rank in RANKS:
            row = summary.loc[summary["주제"].eq(code) & summary["순위"].eq(rank)].iloc[
                0
            ]
            group = result["observations"].loc[
                result["observations"]["주제"].eq(code)
                & result["observations"]["순위"].eq(rank)
            ]
            metrics = [
                ("포함 회차 수", row["포함회차수"]),
                ("제외 회차 수", row["제외회차수"]),
                ("Y 평균", row["본문Y평균"]),
                ("Y 중앙값", row["본문Y중앙값"]),
                ("Y 표준편차", row["본문Y표준편차"]),
                ("Y 25%", row["본문Y25%"]),
                ("Y 75%", row["본문Y75%"]),
                (
                    "Y 최소 / 회차",
                    f"{fmt(row['본문Y최소'])} / {int(row['최소회차'])}회",
                ),
                (
                    "Y 최대 / 회차",
                    f"{fmt(row['본문Y최대'])} / {int(row['최대회차'])}회",
                ),
            ]
            if code in ("09A", "09B"):
                metrics += [
                    ("증가 회차 수", row["증가회차수"]),
                    ("감소 회차 수", row["감소회차수"]),
                    ("동일 회차 수", row["동일회차수"]),
                ]
            sections += [
                f"### {code}-{rank}. 개별 분석",
                table(pd.DataFrame(metrics, columns=["지표", "결과"])),
            ]
            sections.append(
                f"{rank}의 Y 중앙값은 **{fmt(row['본문Y중앙값'])}**, 최대는 **{int(row['최대회차'])}회 {fmt(row['본문Y최대'])}**다. "
                f"이 표의 단위는 `{item['ylabel']}`이며 본문 그래프를 Z점수로 바꾸지 않았다."
            )
            if code in ("09A", "09B"):
                sections.append(
                    f"직전 회차보다 증가 {int(row['증가회차수'])}회, 감소 {int(row['감소회차수'])}회, "
                    f"동일 {int(row['동일회차수'])}회다. 제외된 관측쌍은 이 빈도에 넣지 않았다."
                )
            if code == "10":
                period_rows = result["groups"].loc[
                    result["groups"]["주제"].eq(code)
                    & result["groups"]["순위"].eq(rank)
                ]
                period_text = ", ".join(
                    f"{r['비교구간']} {r['비교값중앙값']:,.0f}원"
                    for _, r in period_rows.iterrows()
                )
                sections.append(
                    f"가격을 맞춘 시기별 중앙값은 **{period_text}**이다. 회차 수가 서로 다르므로 관측 수도 함께 읽는다."
                )
            if code in ("08", "09C", "09D", "10"):
                sections.append(
                    table(
                        result["groups"].loc[
                            result["groups"]["주제"].eq(code)
                            & result["groups"]["순위"].eq(rank),
                            ["비교구간", "관측수", "본문Y중앙값", "비교값중앙값"],
                        ]
                    )
                )
            if code.startswith("09"):
                selected = group.loc[
                    group["비교Z"].abs().nlargest(3).index,
                    ["직전회차", "회차", "x", "y", "비교Z"],
                ]
                sections += [
                    "비교값 기준 극단 회차(유의성 검정 아님):",
                    table(selected),
                ]
            sections.append(image(code, rank))
        sections += [
            f"### {code}-비교. 1·2·3등 바이올린",
            "세 등수의 원값 수준은 앞 표에서 확인한다. 여기서는 가격 환산과 Z점수로 분포 모양 및 튀는 회차를 비교한다.",
            table(
                summary.loc[
                    summary["주제"].eq(code),
                    [
                        "순위",
                        "포함회차수",
                        "본문Y중앙값",
                        "비교값중앙값",
                        "표준화최소",
                        "표준화최대",
                        "표시기준3초과",
                        "최대절대편차회차",
                    ],
                ]
            ),
            image(code, "violin"),
        ]
    sections += [
        "## 범위 제한과 재현",
        "이번 실험은 승인된 질문의 현황·관계·분포를 기록한다. 이론 확률 검정이나 극단값 회차의 원인 규명은 "
        "별도 실험이며 여기서 확정하지 않는다. 단위가 다른 변수의 관계를 비율로 바꾸거나 새로운 주제를 "
        "추가하지 않았다.",
        "```powershell\npython -X utf8 class2_rottery/src/lotto_analysis_v2_1_basic.py\npython -X utf8 class2_rottery/src/verify_lotto_v2_1_basic.py\npython -m pytest -q -p no:cacheprovider\n```",
        f"입력: `lotto_preprocessed_v2.csv`, SHA-256 `{source_hash}`. "
        "정정 산출물은 `analysis_results_v2_1_basic`에 저장한다. 기존 12개 편차 초안 결과와 혼합하지 않는다. "
        "표·그림의 수치는 CSV를 코드로 계산한 산출물이다. 해석문과 그래프 선택 이유는 LLM 작성이며 "
        "그 자체를 데이터 근거로 삼지 않는다. 실제 명령·결과는 실행 로그와 감사 JSON으로 추적한다.",
        "## 표현과 전처리의 근거",
        "\n".join(f"- [{name}]({url})" for name, url in SOURCES),
    ]
    return "\n\n".join(sections) + "\n"


def review_sheets(paths: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for item in TOPICS:
        sheet = Image.new("RGB", (1932, 780), "white")
        draw_text = ImageDraw.Draw(sheet)
        for index, rank in enumerate((*RANKS, "violin")):
            path = paths[(item["id"], rank)]
            with Image.open(path) as original:
                preview = original.convert("RGB")
                preview.thumbnail((966, 355))
                x, y = (index % 2) * 966, (index // 2) * 390
                draw_text.text((x + 8, y + 1), path.name, fill="black")
                sheet.paste(preview, (x, y + 20))
        sheet.save(directory / f"{item['id']}_review.png")


def run(source: str | Path, output: str | Path) -> dict[str, Any]:
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    initial_hash = digest(source)
    result = analyze(source)
    paths = draw(result, output / "charts")
    review_sheets(paths, output / "visual_review")
    for key in ("observations", "summary", "normalization", "excluded", "groups"):
        result[key].to_csv(output / f"{key}.csv", index=False, encoding="utf-8-sig")
    result["data"].to_csv(output / "focus.csv", index=False, encoding="utf-8-sig")
    tables_dir = output / "tables"
    tables_dir.mkdir(exist_ok=True)
    for item in TOPICS:
        code = item["id"]
        for index, rank in enumerate(RANKS, 1):
            frame = result["observations"].loc[
                result["observations"]["주제"].eq(code)
                & result["observations"]["순위"].eq(rank)
            ]
            frame.to_csv(
                tables_dir / f"{code}_rank_{index}.csv",
                index=False,
                encoding="utf-8-sig",
            )
        result["summary"].loc[result["summary"]["주제"].eq(code)].to_csv(
            tables_dir / f"{code}_comparison.csv", index=False, encoding="utf-8-sig"
        )
    report = make_report(result, paths, initial_hash)
    (output / "lotto_rank_report_v2_1.md").write_text(report, encoding="utf-8")
    body = markdown.markdown(report, extensions=["tables", "fenced_code"])
    html = (
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>로또 V2.1 일반 현황</title><style>body{font:16px/1.65 sans-serif;max-width:1150px;margin:36px auto;padding:0 24px;color:#192633}img{max-width:100%;height:auto}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}th,td{border:1px solid #ccd3db;padding:6px 10px}th{background:#edf3f8}h2{margin-top:50px;border-bottom:1px solid #ccc}pre{background:#edf3f8;padding:14px}@media print{h2{break-before:page}img{break-inside:avoid}}</style><body>'
        + body
        + "</body></html>"
    )
    (output / "lotto_rank_report_v2_1.html").write_text(html, encoding="utf-8")
    validation = {
        "approved_topic_ids": [item["id"] for item in TOPICS],
        "three_ranks_only": set(result["data"]["순위"]) == set(RANKS),
        "topic_rank_tables": len(result["summary"]) == 27,
        "main_figures": sum(rank != "violin" for _, rank in paths) == 27,
        "comparison_figures": sum(rank == "violin" for _, rank in paths) == 9,
        "embedded_figures": report.count("data:image/png;base64,")
        == html.count("data:image/png;base64,")
        == 36,
        "input_hash_unchanged": digest(source) == initial_hash,
    }
    joined = result["observations"].merge(
        result["normalization"], on=["주제", "순위"], validate="many_to_one"
    )
    validation["scores_reconstructed"] = bool(
        np.allclose(
            joined["비교Z"],
            (joined["비교값"] - joined["중심"]) / joined["표준편차"],
            atol=1e-10,
        )
    )
    passed = all(
        value for key, value in validation.items() if key != "approved_topic_ids"
    )
    validation["status"] = "passed" if passed else "failed"
    (output / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not passed:
        raise RuntimeError("산출물 검증 실패")
    excluded_names = {"manifest.json", "execution_log.json", "independent_audit.json"}
    artifacts = [
        {
            "path": str(path.relative_to(output)),
            "size_bytes": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name not in excluded_names
    ]
    manifest = {
        "version": "2.1 corrected basic",
        "status": "passed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -X utf8 class2_rottery/src/lotto_analysis_v2_1_basic.py",
        "source": str(source),
        "source_sha256": initial_hash,
        "source_rows": len(result["data"]),
        "topics": list(TOPICS),
        "rank_figures": 27,
        "comparison_figures": 9,
        "python": platform.python_version(),
        "inference": "기술통계와 분포 비교; 통계적 유의성 또는 인과관계 확정 아님",
        "artifacts": artifacts,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"validation": validation, "result": result, "paths": paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=DATA / "lotto_preprocessed_v2.csv"
    )
    parser.add_argument(
        "--output", type=Path, default=REPORTS / "v2_1" / "analysis_results_v2_1_basic"
    )
    args = parser.parse_args()
    result = run(args.source, args.output)
    for suffix in ("md", "html"):
        name = f"lotto_rank_report_v2_1.{suffix}"
        (REPORTS / "v2_1" / name).write_bytes((args.output / name).read_bytes())
    print(
        json.dumps(
            {
                "status": result["validation"]["status"],
                "topics": 9,
                "rank_figures": 27,
                "violin_figures": 9,
                "report": str(REPORTS / "v2_1" / "lotto_rank_report_v2_1.md"),
                "results": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
