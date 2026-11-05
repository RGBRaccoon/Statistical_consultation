"""각 섹션의 Y에 같은 상방 IQR 규칙과 해당·직전 회차 비교를 적용한다."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

import lotto_analysis_v2_1_basic as basic
from paths import DATA, REPORTS

RANKS = basic.RANKS
TOPICS = basic.TOPICS
FEATURES = {
    "current_sales": ("해당 판매게임 수", "게임"),
    "current_prize": ("해당 한 게임당 당첨금", "1,000원 가격 기준 원"),
    "current_winners": ("해당 당첨게임 수", "게임"),
    "current_total": ("해당 등수별 총당첨금", "1,000원 가격 기준 원"),
    "previous_sales": ("직전 판매게임 수", "게임"),
    "previous_prize": ("직전 한 게임당 당첨금", "1,000원 가격 기준 원"),
    "previous_winners": ("직전 당첨게임 수", "게임"),
    "previous_total": ("직전 등수별 총당첨금", "1,000원 가격 기준 원"),
}
TARGET_FEATURE = {
    "01": "current_prize",
    "02": "current_winners",
    "03": "current_total",
    "08": "current_prize",
    "09C": "current_winners",
    "09D": "current_prize",
    "10": "current_prize",
}
NIST = "https://www.itl.nist.gov/div898/handbook/prc/section1/prc16.htm"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def select(frame: pd.DataFrame, **criteria: Any) -> pd.DataFrame:
    """반복 호출에서도 명시적인 열별 동등 비교로 부분집합을 만든다."""
    mask = pd.Series(True, index=frame.index)
    for column, value in criteria.items():
        mask &= frame[column].eq(value)
    return frame.loc[mask]


def upper_rule(values: pd.Series) -> dict[str, float]:
    values = values.dropna().astype(float)
    if values.empty or not np.isfinite(values).all():
        raise ValueError("유한한 유효 Y값이 필요하다.")
    q1, q3 = values.quantile([0.25, 0.75], interpolation="linear")
    spread = q3 - q1
    return {
        "Q1": float(q1),
        "Q3": float(q3),
        "IQR": float(spread),
        "상한": float(q3 + 1.5 * spread),
        "강한상한": float(q3 + 3 * spread),
    }


def classify_y(values: pd.Series, upper: float, strong: float) -> np.ndarray:
    return np.select([values.gt(strong), values.gt(upper)], [2, 1], default=0)


def label_sections(observations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    labeled, fences = [], []
    for (code, rank), frame in observations.groupby(["주제", "순위"], sort=False):
        frame = frame.copy()
        rule = upper_rule(frame["y"])
        for key, value in rule.items():
            frame[key] = value
        frame["강도"] = classify_y(frame["y"], rule["상한"], rule["강한상한"])
        frame["상방"] = frame["강도"].gt(0)
        if "기간" in frame:
            frame["동일시기상방"] = False
            for _, subset in frame.groupby("기간", sort=False):
                local = upper_rule(subset["y"])
                frame.loc[subset.index, "동일시기상방"] = subset["y"].gt(local["상한"])
        fences.append(
            {
                "주제": code,
                "순위": rank,
                "관측수": len(frame),
                **rule,
                "IQR퇴화": rule["IQR"] == 0,
                "상방수": int(frame["상방"].sum()),
                "강한상방수": int(frame["강도"].eq(2).sum()),
            }
        )
        labeled.append(frame)
    return pd.concat(labeled, ignore_index=True), pd.DataFrame(fences)


def make_features(data: pd.DataFrame) -> pd.DataFrame:
    features = data[["회차", "순위", "기간"]].copy()
    features["current_sales"] = data["추정_판매게임수"]
    features["current_prize"] = data["1000원기준_1게임당당첨금"].where(
        data["당첨게임 수"].gt(0)
    )
    features["current_winners"] = data["당첨게임 수"]
    features["current_total"] = data["1000원기준_등위별총당첨금"]
    previous = features.drop(columns="기간").rename(
        columns={
            "회차": "특징직전회차",
            **{
                key: key.replace("current_", "previous_")
                for key in FEATURES
                if key.startswith("current_")
            },
        }
    )
    previous["회차"] = previous["특징직전회차"] + 1
    return features.merge(
        previous, on=["회차", "순위"], how="left", validate="one_to_one"
    )


def compare_features(labeled: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    joined = labeled.merge(
        features.drop(columns="기간"), on=["회차", "순위"], validate="many_to_one"
    )
    records = []
    for (code, rank, period), group in joined.groupby(
        ["주제", "순위", "기간"], sort=False
    ):
        high = group.loc[group["상방"]]
        control = group.loc[~group["상방"]]
        for feature, (name, unit) in FEATURES.items():
            if TARGET_FEATURE.get(code) == feature:
                continue
            left, right = high[feature].dropna(), control[feature].dropna()
            available = len(left) > 0 and len(right) > 0
            record = {
                "주제": code,
                "순위": rank,
                "기간": period,
                "특징": feature,
                "특징명": name,
                "단위": unit,
                "상방집단수": len(high),
                "비상방집단수": len(control),
                "상방유효수": len(left),
                "비상방유효수": len(right),
                "상방결측수": len(high) - len(left),
                "비상방결측수": len(control) - len(right),
                "비교가능": available,
                "해석주의": "Y 변화량의 구성요소"
                if code in ("09A", "09B")
                and feature.split("_")[1] == ("prize" if code == "09A" else "winners")
                else "기술적 차이; 원인 확정 아님",
            }
            for prefix, values in (("상방", left), ("비상방", right)):
                record[prefix + "중앙값"] = (
                    float(values.median()) if len(values) else np.nan
                )
                record[prefix + "25%"] = (
                    float(values.quantile(0.25)) if len(values) else np.nan
                )
                record[prefix + "75%"] = (
                    float(values.quantile(0.75)) if len(values) else np.nan
                )
            record["중앙값차이"] = (
                record["상방중앙값"] - record["비상방중앙값"] if available else np.nan
            )
            records.append(record)
    return pd.DataFrame(records)


def overlaps(labeled: pd.DataFrame) -> pd.DataFrame:
    groups = {
        (code, rank): group.set_index("회차")
        for (code, rank), group in labeled.groupby(["주제", "순위"], sort=False)
    }
    rows = []
    for (code, rank), left in groups.items():
        for (other_code, other_rank), right in groups.items():
            common = left.index.intersection(right.index)
            source = left.loc[common, "상방"]
            target = right.loc[common, "상방"]
            source_n, count = int(source.sum()), int((source & target).sum())
            rows.append(
                {
                    "기준주제": code,
                    "기준등수": rank,
                    "비교주제": other_code,
                    "비교등수": other_rank,
                    "공통관측수": len(common),
                    "공통관측내기준상방수": source_n,
                    "동시상방수": count,
                    "기준상방중동시비율": count / source_n if source_n else np.nan,
                    "같은Y계열": TARGET_FEATURE.get(code, code)
                    == TARGET_FEATURE.get(other_code, other_code),
                }
            )
    return pd.DataFrame(rows)


def analyze(source: str | Path) -> dict[str, Any]:
    base = basic.analyze(source)
    labeled, fences = label_sections(base["observations"])
    features = make_features(base["data"])
    outliers = labeled.loc[labeled["상방"]].merge(
        features.drop(columns="기간"), on=["회차", "순위"], validate="many_to_one"
    )
    periods = []
    for (code, rank, period), group in labeled.groupby(
        ["주제", "순위", "기간"], sort=False
    ):
        local = upper_rule(group["y"])
        periods.append(
            {
                "주제": code,
                "순위": rank,
                "기간": period,
                "관측수": len(group),
                "상방수": int(group["상방"].sum()),
                "상방비율": float(group["상방"].mean()),
                "강한상방수": int(group["강도"].eq(2).sum()),
                "상방중동일시기상방수": int(
                    (group["상방"] & group["동일시기상방"]).sum()
                ),
                "동일시기상한": local["상한"],
            }
        )
    periods = pd.DataFrame(periods)
    aggregate = outliers.groupby("회차", sort=True).agg(
        검출항목수=("주제", "size"),
        섹션수=("주제", "nunique"),
        등수수=("순위", "nunique"),
        강한검출수=("강도", lambda x: int(x.eq(2).sum())),
    )
    aggregate["검출목록"] = outliers.groupby("회차").apply(
        lambda g: ", ".join(g["주제"] + "/" + g["순위"]), include_groups=False
    )
    aggregate["검출목록"] = aggregate["검출목록"].fillna("")
    return {
        **base,
        "labeled": labeled,
        "fences": fences,
        "features": features,
        "outliers": outliers,
        "comparisons": compare_features(labeled, features),
        "overlaps": overlaps(labeled),
        "periods": periods,
        "rounds": aggregate.reset_index(),
    }


def draw_followups(result: dict, directory: Path) -> dict[str, Path]:
    directory.mkdir(exist_ok=True, parents=True)
    paths = {}
    for item in TOPICS:
        code = item["id"]
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.1))
        for ax, rank in zip(axes, RANKS):
            group = select(result["labeled"], 주제=code, 순위=rank)
            rule = select(result["fences"], 주제=code, 순위=rank).iloc[0]
            for strength, color, label in (
                (0, "#8d9baa", "비상방"),
                (1, "#d57b00", "상방"),
                (2, "#b8173e", "강한 상방"),
            ):
                part = group.loc[group["강도"].eq(strength)]
                ax.scatter(
                    part["회차"],
                    part["y"],
                    s=12 if strength else 6,
                    color=color,
                    alpha=0.8 if strength else 0.35,
                    label=label,
                    linewidths=0,
                )
            ax.axhline(
                rule["상한"], color="#d57b00", linestyle="--", label="1.5 IQR 상한"
            )
            ax.axhline(
                rule["강한상한"], color="#b8173e", linestyle=":", label="3 IQR 상한"
            )
            if code not in ("09A", "09B"):
                ax.set_ylim(bottom=0)
            ax.set_title(f"{rank}: {rule['상방수']}회 / 강한 {rule['강한상방수']}회")
            ax.set_xlabel("회차")
            ax.yaxis.set_major_formatter(
                FuncFormatter(
                    (lambda value, position: f"{value:,.0f}")
                    if code in ("02", "09B", "09C")
                    else basic.money_tick
                )
            )
            ax.set_ylabel(item["ylabel"].replace("−", "-"))
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(
            handles,
            labels,
            loc="lower center",
            bbox_to_anchor=(0.5, 0.005),
            ncol=5,
            fontsize=8,
        )
        fig.suptitle(
            f"{code}. {item['title']} - 해당 섹션의 Y로만 상방 판정", fontweight="bold"
        )
        fig.tight_layout(rect=(0, 0.10, 1, 0.93))
        path = directory / f"{code}_upper_iqr.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        paths[code] = path
    labels = [f"{item['id']}/{rank}" for item in TOPICS for rank in RANKS]
    values = result["overlaps"].copy()
    values["left"] = values["기준주제"] + "/" + values["기준등수"]
    values["right"] = values["비교주제"] + "/" + values["비교등수"]
    matrix = values.pivot(index="left", columns="right", values="동시상방수").reindex(
        index=labels, columns=labels
    )
    fig, ax = plt.subplots(figsize=(14, 12))
    picture = ax.imshow(matrix.to_numpy(), cmap="Blues", aspect="equal")
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(
                j,
                i,
                str(int(matrix.iloc[i, j])),
                ha="center",
                va="center",
                fontsize=6,
                color="black"
                if matrix.iloc[i, j] < matrix.to_numpy().max() * 0.55
                else "white",
            )
    ax.set_xticks(range(len(labels)), labels, rotation=90, fontsize=8)
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.set_title(
        "섹션·등수별 동시 상방 회차 수 (대각선: 각 목록 크기)\n같은 Y의 반복 검출과 수학적 연결을 독립 증거로 해석하지 않음",
        pad=18,
    )
    fig.colorbar(picture, ax=ax, shrink=0.7, label="동시 회차 수")
    fig.tight_layout()
    path = directory / "overlap_all.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    paths["overlap"] = path
    return paths


def embedded(path: Path, caption: str) -> str:
    return f"![{caption}](data:image/png;base64,{base64.b64encode(path.read_bytes()).decode('ascii')})"


def followup_report(result: dict, code: str, path: Path) -> str:
    parts = [
        f"### {code}-후속. 공통 이상치 분석",
        embedded(path, f"{code} 상방 IQR 판정"),
        "동일한 공통 규칙을 이 섹션·등수의 유효한 본문 Y에 적용했다. X나 바이올린 Z점수로 선정하지 않았다.",
        basic.table(select(result["fences"], 주제=code)),
        "#### 시기별 집중과 보조 판정",
        basic.table(select(result["periods"], 주제=code)),
        "분모는 해당 섹션·등수·시기의 유효 관측 수다. 동일시기상방은 그 시기의 Y로 경계를 다시 계산한 보조 표시이며 전체 기간 기준 선정 목록을 바꾸지 않는다.",
    ]
    for rank in RANKS:
        high = select(result["outliers"], 주제=code, 순위=rank)
        rule = select(result["fences"], 주제=code, 순위=rank).iloc[0]
        parts += [f"#### {code}/{rank}: 선정 회차 전체 ({len(high)}회)"]
        if high.empty:
            parts.append(
                "상방 이상치가 없다. 특징의 공통점과 집단 차이는 이 목록으로 평가할 수 없다."
            )
        else:
            parts += [
                basic.table(
                    high[
                        [
                            "회차",
                            "기간",
                            "y",
                            "상한",
                            "강도",
                            "동일시기상방",
                            "특징직전회차",
                        ]
                    ]
                ),
                f"상한은 {rule['상한']:,.2f}, 강한 상한은 {rule['강한상한']:,.2f}다. 강도 1은 상방, 2는 강한 상방이며 강한 상방도 전체 상방 수에 포함된다.",
            ]
        pairs = select(result["overlaps"], 기준주제=code, 기준등수=rank, 비교등수=rank)
        pairs = pairs.loc[pairs["비교주제"].ne(code)]
        parts += [
            "같은 등수의 다른 섹션과의 겹침:",
            basic.table(
                pairs[
                    [
                        "비교주제",
                        "공통관측수",
                        "공통관측내기준상방수",
                        "동시상방수",
                        "기준상방중동시비율",
                        "같은Y계열",
                    ]
                ]
            ),
        ]
        cross = select(result["overlaps"], 기준주제=code, 기준등수=rank)
        cross = (
            cross.loc[cross["비교등수"].ne(rank)]
            .sort_values(
                ["동시상방수", "비교주제", "비교등수"], ascending=[False, True, True]
            )
            .head(5)
        )
        parts += [
            "다른 등수와 겹친 상위 5개 항목(전체 조합은 overlaps.csv):",
            basic.table(
                cross[
                    [
                        "비교주제",
                        "비교등수",
                        "공통관측수",
                        "동시상방수",
                        "기준상방중동시비율",
                    ]
                ]
            ),
        ]
        comp = select(result["comparisons"], 주제=code, 순위=rank)
        for heading, prefix in (
            ("해당 회차", "current_"),
            ("정확한 직전 회차", "previous_"),
        ):
            subset = comp.loc[comp["특징"].str.startswith(prefix)]
            view = subset[
                [
                    "기간",
                    "특징명",
                    "단위",
                    "상방집단수",
                    "비상방집단수",
                    "상방유효수",
                    "비상방유효수",
                    "상방중앙값",
                    "비상방중앙값",
                    "중앙값차이",
                ]
            ]
            parts += [f"{heading} 특징: 같은 등수·시기 안에서 비교", basic.table(view)]
            valid = subset.loc[subset["비교가능"]]
            if valid.empty:
                parts.append(
                    "양쪽 집단의 유효 관측이 모두 있는 비교가 없어 방향을 요약하지 않는다."
                )
            else:
                sentences = []
                for feature in valid["특징"].unique():
                    rowset = valid.loc[valid["특징"].eq(feature)]
                    signs = np.sign(rowset["중앙값차이"])
                    direction = (
                        "높음"
                        if signs.gt(0).all()
                        else "낮음"
                        if signs.lt(0).all()
                        else "같음"
                        if signs.eq(0).all()
                        else "시기별 방향이 다름"
                    )
                    detail = "; ".join(
                        f"{row['기간']} {row['상방중앙값']:,.2f} 대 {row['비상방중앙값']:,.2f} ({int(row['상방유효수'])}/{int(row['비상방유효수'])}회)"
                        for _, row in rowset.iterrows()
                    )
                    sentences.append(
                        f"- {FEATURES[feature][0]}: 비교 가능한 {len(rowset)}개 시기에서 {direction}. {detail}."
                    )
                parts.append(
                    "관찰된 중앙값 차이(단위는 위 표, 괄호는 상방/비상방 유효 관측 수):\n\n"
                    + "\n".join(sentences)
                )
        parts.append(
            "이 차이는 집단의 기술적 특징이다. 소수 회차의 중앙값일 수 있으며 모든 이상치에 공통된 속성 또는 원인의 증거로 확정하지 않는다. 결측 수와 25~75% 범위는 comparisons.csv에 모두 저장했다."
        )
        if code in ("09A", "09B"):
            parts.append(
                "이 섹션은 현재값-직전값의 변화량으로 선정했다. 현재 또는 직전 구성요소의 차이는 선정식에서 생길 수 있으므로 별도의 외부 원인으로 해석하지 않는다."
            )
    return "\n\n".join(parts)


def make_report(
    result: dict, base_paths: dict, extra_paths: dict, source_hash: str
) -> str:
    report = basic.make_report(result, base_paths, source_hash)
    # 이전 |Z| 극단 목록·건수를 새 상방 IQR 판정과 혼동하지 않게 교체한다.
    report = re.sub(
        r"비교값 기준 극단 회차\(유의성 검정 아님\):\n\n(?:\|[^\n]*\n)+", "", report
    )
    for item in TOPICS:
        code = item["id"]
        section_summary = select(result["summary"], 주제=code)
        old_columns = [
            "순위",
            "포함회차수",
            "본문Y중앙값",
            "비교값중앙값",
            "표준화최소",
            "표준화최대",
            "표시기준3초과",
            "최대절대편차회차",
        ]
        view = section_summary[
            ["순위", "포함회차수", "본문Y중앙값", "비교값중앙값"]
        ].merge(
            select(result["fences"], 주제=code)[["순위", "상방수", "강한상방수"]],
            on="순위",
            validate="one_to_one",
        )
        report = report.replace(
            basic.table(section_summary[old_columns]), basic.table(view), 1
        )
    # 기존 일반 현황의 표·그림은 보존하고 각 섹션 말미에 같은 후속 분석을 삽입한다.
    for index, item in enumerate(TOPICS):
        marker = (
            f"## {TOPICS[index + 1]['id']}."
            if index + 1 < len(TOPICS)
            else "## 범위 제한과 재현"
        )
        report = report.replace(
            marker,
            followup_report(result, item["id"], extra_paths[item["id"]])
            + "\n\n"
            + marker,
            1,
        )
    report = (
        report.replace("V2.1", "V2.3")
        .replace("lotto_analysis_v2_1_basic.py", "lotto_analysis_v2_3.py")
        .replace("verify_lotto_v2_1_basic.py", "verify_lotto_v2_3.py")
        .replace("analysis_results_v2_1_basic", "analysis_results_v2_3")
    )
    report = report.replace(
        "가공된 편차 중심의 이전 초안을 정정했다",
        "V2.1 현황 분석에 섹션별 상방 이상치와 공통 특징 분석을 추가했다",
    )
    report = report.replace(
        "총 36개 그림이다.",
        "기존 현황 그림은 36개다. 섹션별 상방 판정 그림 9개와 전체 겹침 그림 1개를 추가해 총 46개를 내장했다.",
    )
    intro = [
        "## V2.3 판정과 비교 원칙",
        "각 섹션·각 등수의 유효한 본문 Y에서 Q1·Q3를 선형 보간으로 계산한다. IQR=Q3-Q1이며 Y>Q3+1.5×IQR은 상방, Y>Q3+3×IQR은 강한 상방이다. 경계와 같은 값은 초과로 세지 않는다. 낮은 값은 삭제하지 않고 비상방 집단에 남긴다. IQR=0이면 퇴화를 표시하고 같은 부등식을 적용한다.",
        "Y는 앞선 본문과 동일하다. 01·03·08·09A·09C·09D의 금액은 명목 원이고 10은 1,000원 가격 기준 원이다. 바이올린의 Z와 ±3선은 기존 분포 표시이며 V2.3 이상치 판정 기준이 아니다. 09A의 상방은 큰 증가량이며 단순히 현재 당첨금이 높다는 뜻이 아니다.",
        "공통 규칙·공통 후속 절차에 입력 Y만 달라진다. 각 섹션의 관측 대상과 결측 조건을 유지한다. 같은 Y와 같은 포함 회차이면 결과가 같을 수 있다. 동일 Y의 반복 표시나 총당첨금·당첨게임 수·한 게임당 당첨금의 수학적 연결을 독립 증거로 세지 않는다.",
        "특징 비교는 해당 섹션·등수·제도 구간 안의 상방 집단과 비상방 집단을 비교한다. 특징의 금액은 모두 1,000원 가격 기준이며 판매량은 추정 판매게임 수다. Y 자체에 해당하는 현재 특징은 비교에서 제외한다. 변화량의 구성요소는 표시하되 선정식의 영향을 명시한다. 빈 집단은 비교 불가이며 원인·유의성은 확정하지 않는다.",
        "겹침 비율의 분모는 두 항목 모두 관측 가능한 회차 중 기준 항목이 상방인 회차 수다. 같은 회차가 두 항목에서 동시에 상방인 수를 나눈다. 제외 회차를 비상방으로 간주하지 않는다.",
        f"판정의 외부 근거: [NIST IQR 경계]({NIST}). 코드·모든 CSV·제외 사유·검증 기록은 analysis_results_v2_3에 저장한다.",
    ]
    count_view = (
        result["fences"]
        .pivot(index="주제", columns="순위", values="상방수")
        .reset_index()
    )
    intro += [
        "### 전체 선정 규모",
        f"섹션/등수별 상방 검출은 총 {len(result['outliers']):,}행이고, 중복 회차를 합치면 {len(result['rounds']):,}개 회차다. 검출 행 수는 서로 독립된 이상 현상 수가 아니다.",
        basic.table(count_view),
    ]
    report = report.replace(
        "## 먼저 알아둘 데이터의 뜻",
        "\n\n".join(intro) + "\n\n## 먼저 알아둘 데이터의 뜻",
        1,
    )
    summary = [
        "## 전체 이상치 회차 통합표",
        "검출항목수는 섹션/등수 조합의 수이며 독립된 이상 현상의 개수가 아니다. 동일한 회차를 한 행으로 모았으며 상세 원값·해당·직전 특징은 outliers.csv에 있다.",
        embedded(extra_paths["overlap"], "전체 섹션 등수의 동시 상방 회차"),
        basic.table(result["rounds"]),
    ]
    return report + "\n\n" + "\n\n".join(summary) + "\n"


def run(source: str | Path, output: str | Path) -> dict:
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    source_hash = digest(source)
    result = analyze(source)
    base_paths = basic.draw(result, output / "charts")
    extra_paths = draw_followups(result, output / "charts")
    basic.review_sheets(base_paths, output / "visual_review")
    for key in (
        "observations",
        "excluded",
        "normalization",
        "summary",
        "groups",
        "labeled",
        "fences",
        "features",
        "outliers",
        "comparisons",
        "overlaps",
        "periods",
        "rounds",
    ):
        result[key].to_csv(output / f"{key}.csv", index=False, encoding="utf-8-sig")
    result["data"].to_csv(output / "focus.csv", index=False, encoding="utf-8-sig")
    tables = output / "tables"
    tables.mkdir(exist_ok=True)
    for item in TOPICS:
        code = item["id"]
        for key in ("fences", "outliers", "comparisons", "periods"):
            result[key].loc[result[key]["주제"].eq(code)].to_csv(
                tables / f"{code}_{key}.csv", index=False, encoding="utf-8-sig"
            )
    report = make_report(result, base_paths, extra_paths, source_hash)
    (output / "lotto_rank_report_v2_3.md").write_text(report, encoding="utf-8")
    body = markdown.markdown(report, extensions=["tables", "fenced_code"])
    html = (
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>로또 V2.3 상방 이상치</title><style>body{font:16px/1.65 sans-serif;max-width:1280px;margin:32px auto;padding:0 22px;color:#192633}img{max-width:100%;height:auto}table{border-collapse:collapse;font-size:12px;display:block;overflow:auto}th,td{border:1px solid #ccd3db;padding:5px 8px}th{background:#edf3f8}h2{margin-top:45px;border-bottom:1px solid #ccc}@media print{h2{break-before:page}img{break-inside:avoid}}</style><body>'
        + body
        + "</body></html>"
    )
    (output / "lotto_rank_report_v2_3.html").write_text(html, encoding="utf-8")
    prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[image]", report)
    validation = {
        "section_rank_rules": len(result["fences"]) == 27,
        "outlier_rows_match_flags": int(result["labeled"]["상방"].sum())
        == len(result["outliers"]),
        "all_outliers_strictly_upper": bool(
            result["outliers"]["y"].gt(result["outliers"]["상한"]).all()
        ),
        "embedded_images": report.count("data:image/png;base64,")
        == html.count("data:image/png;base64,")
        == 46,
        "no_correlation_text": not bool(
            re.search(r"상관계수|spearman|pearson|correlation", prose, re.IGNORECASE)
        ),
        "input_unchanged": digest(source) == source_hash,
    }
    validation["status"] = "passed" if all(validation.values()) else "failed"
    (output / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if validation["status"] != "passed":
        raise RuntimeError("V2.3 산출물 검증 실패")
    artifacts = [
        {
            "path": str(path.relative_to(output)),
            "size_bytes": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
        and path.name
        not in {"manifest.json", "execution_log.json", "independent_audit.json"}
    ]
    manifest = {
        "version": "2.3",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -X utf8 class2_rottery/src/lotto_analysis_v2_3.py",
        "source": str(source),
        "source_sha256": source_hash,
        "raw_source_sha256": digest(DATA / "lotto_rank_1_1245.csv"),
        "section_rank_rules": 27,
        "images": 46,
        "outlier_rows": len(result["outliers"]),
        "unique_rounds": len(result["rounds"]),
        "rule": "strictly upper 1.5/3 IQR, own section/rank main Y",
        "quartile_interpolation": "linear",
        "sources": [NIST],
        "artifacts": artifacts,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"result": result, "validation": validation}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=DATA / "lotto_preprocessed_v2.csv"
    )
    parser.add_argument(
        "--output", type=Path, default=REPORTS / "v2_3" / "analysis_results_v2_3"
    )
    args = parser.parse_args()
    result = run(args.source, args.output)
    for extension in ("md", "html"):
        name = f"lotto_rank_report_v2_3.{extension}"
        (REPORTS / "v2_3" / name).write_bytes((args.output / name).read_bytes())
    print(
        json.dumps(
            {
                "status": result["validation"]["status"],
                "outlier_rows": len(result["result"]["outliers"]),
                "unique_rounds": len(result["result"]["rounds"]),
                "images": 46,
                "report": str(REPORTS / "v2_3" / "lotto_rank_report_v2_3.md"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
