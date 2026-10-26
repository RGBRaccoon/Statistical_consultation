"""V2.1: 1·2·3등의 회차별 편차를 12개 관점으로 비교한다.

각 분석은 등수별 표·그림 3개와 부록의 비교표·바이올린 1개를 만든다.
기존 V2 입력과 산출물은 수정하지 않는다.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import scipy  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy.stats import binom, kurtosis, nbinom, poisson, skew, spearmanr  # noqa: E402

RANKS = ("1등", "2등", "3등")
COLORS = {"1등": "#1768ac", "2등": "#db6b00", "3등": "#208251"}
SOURCES = [
    (
        "Z점수의 정의",
        "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.zscore.html",
    ),
    (
        "이항분포의 평균·분산",
        "https://www.itl.nist.gov/div898/handbook/eda/section3/eda366i.htm",
    ),
    (
        "NB2 분산 함수",
        "https://www.statsmodels.org/stable/generated/statsmodels.genmod.families.varfuncs.NegativeBinomial.html",
    ),
    (
        "음이항 꼬리확률",
        "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.nbinom.html",
    ),
    (
        "FDR BH/BY 적용 조건",
        "https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.fdrcorrection.html",
    ),
    (
        "바이올린 표현",
        "https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.violinplot.html",
    ),
]


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def standardize(values: np.ndarray) -> tuple[np.ndarray, float, float]:
    """평균·표본표준편차로 척도를 맞추며 분포 모양은 유지한다."""
    array = np.asarray(values, dtype=float)
    if len(array) < 2 or not np.isfinite(array).all():
        raise ValueError("표준화에는 유한한 관측값 두 개 이상이 필요하다.")
    center, scale = float(array.mean()), float(array.std(ddof=1))
    if scale <= 0:
        raise ValueError("상수인 값은 분포 비교용 Z점수로 표준화할 수 없다.")
    return (array - center) / scale, center, scale


def adjust_pvalues(values: np.ndarray, method: str) -> np.ndarray:
    """전체 검정군의 BH 또는 BY 보정값을 반환한다."""
    pvalues = np.asarray(values, dtype=float)
    order = np.argsort(pvalues)
    factor = np.sum(1 / np.arange(1, len(values) + 1)) if method == "BY" else 1
    ordered = pvalues[order] * len(values) * factor / np.arange(1, len(values) + 1)
    adjusted = np.minimum.accumulate(ordered[::-1])[::-1].clip(0, 1)
    result = np.empty_like(adjusted)
    result[order] = adjusted
    return result


def load_focus(path: str | Path) -> pd.DataFrame:
    source = pd.read_csv(path, encoding="utf-8-sig")
    focused = source.loc[source["순위"].isin(RANKS)].copy()
    if focused.duplicated(["회차", "순위"]).any():
        raise ValueError("중복 회차·등수 키가 있다.")
    if (
        not focused.groupby("회차")["순위"]
        .agg(set)
        .map(lambda s: s == set(RANKS))
        .all()
    ):
        raise ValueError("회차별 세 등수 중 일부가 누락됐다.")
    focused = focused.sort_values(["순위", "회차"]).reset_index(drop=True)
    focused["기간"] = np.select(
        [focused["회차"] <= 87, focused["회차"] <= 400],
        ["1~87회", "88~400회"],
        default="401회 이후",
    )
    sales = focused.drop_duplicates("회차").set_index("회차")["추정_판매게임수"]
    quarters = pd.qcut(sales, 4, labels=["판매 Q1", "판매 Q2", "판매 Q3", "판매 Q4"])
    focused["판매구간"] = focused["회차"].map(quarters.astype(str))
    return focused


def _spec(
    code: str,
    title: str,
    question: str,
    formula: str,
    note: str,
    normalization: str = "등수별 Z점수",
) -> dict[str, str]:
    return {
        "id": code,
        "title": title,
        "question": question,
        "formula": formula,
        "note": note,
        "normalization": normalization,
    }


EXPERIMENTS = (
    _spec(
        "01",
        "당첨게임 수의 분포",
        "원당첨게임 수가 얼마나 불균형한가?",
        "K = 당첨게임 수",
        "판매량 차이가 포함된다. 단독 이상 판정에는 쓰지 않는다.",
    ),
    _spec(
        "02",
        "판매량 대비 관측·기대 비율",
        "판매량을 나누어도 큰 편차가 남는가?",
        "R = K / (N × p)",
        "이론 기준은 1이다. 큰 비율이 곧 작은 p값을 뜻하지 않는다.",
    ),
    _spec(
        "03",
        "이항분산 기준 편차",
        "독립 이항모형으로 설명되지 않는 변동은 얼마나 큰가?",
        "e = (K − Np) / sqrt(Np(1−p))",
        "양측 이중 꼬리확률과 BH/BY를 계산한다. 독립 게임 가정이 맞지 않아 확정 유의성에 사용하지 않는다.",
    ),
    _spec(
        "04",
        "로그 비율의 비대칭",
        "상승과 하락을 로그 척도에서 비교하면 어떻게 보이는가?",
        "L = log((K + 0.5) / (Np + 0.5))",
        "0.5는 K=0의 로그를 계산하기 위한 명시적 연속성 보정이다. 원값을 대체하지 않는다.",
    ),
    _spec(
        "05",
        "제도 구간의 수준 효과",
        "1~87, 88~400, 401회 이후의 수준 차이를 제거하면 무엇이 남는가?",
        "L − 해당 등수·기간의 L 평균",
        "같은 자료로 중심을 추정한 기술적 잔차다. 기간 내 평균 0은 계산 결과의 구조이지 발견이 아니다.",
    ),
    _spec(
        "06",
        "판매규모의 수준 효과",
        "판매량 사분위별 중심을 제거해도 편차가 남는가?",
        "L − 해당 등수·판매사분위의 L 중앙값",
        "사분위 경계는 회차별 판매량으로 한 번만 계산해 세 등수에 공유한다. 시간과 판매량의 인과 효과를 구분하지 않는다.",
    ),
    _spec(
        "07",
        "직전 회차 대비 급변",
        "높은 수준이 이어지는가, 한 번에 튀는가?",
        "R(t) − R(t−1), 동일 제도 구간만",
        "1·88·401회는 직전 구간과 연결하지 않는다. 큰 차이를 자료 오류로 간주하지 않는다.",
    ),
    _spec(
        "08",
        "25회 비중복 누적 비율",
        "25회씩 합쳐도 편차가 남는가?",
        "sum(K) / sum(Np), 구간별 25회 블록",
        "이동창의 중복을 피한다. 각 기간 마지막 25회 미만 묶음은 이 분석에만 사용하지 않고 별도 기록한다.",
    ),
    _spec(
        "09",
        "극단값에 강건한 표준화",
        "극단값이 평균·표준편차를 키워 다른 편차를 가리는가?",
        "(L − median(L)) / (1.4826 × MAD(L))",
        "04와 같은 값의 척도 민감도 분석이다. 별개의 독립 증거가 아니다. ±3은 표시 기준이며 유의성 검정이 아니다.",
        "등수별 중앙값/MAD",
    ),
    _spec(
        "10",
        "등수 간 동반 변동",
        "한 등수가 많이 나온 회차에 다른 등수도 같이 움직이는가?",
        "자기 등수 강건점수 × 나머지 두 등수 강건점수 평균",
        "양수는 같은 방향, 음수는 반대 방향이다. Spearman 상관계수도 저장하며 인과관계를 주장하지 않는다.",
    ),
    _spec(
        "11",
        "과산포 보정의 시간 분리 검증",
        "과거에 학습한 분산으로 이후 회차의 극단값을 설명할 수 있는가?",
        "(K − fitted μ) / sqrt(fitted μ + alpha × fitted μ²)",
        "각 등수·기간의 앞 70%만 학습한다. 뒤 30%의 NB2 이중 꼬리확률을 전체 검증군에서 BY 보정한다. 모수 불확실성을 포함하지 않은 탐색적 후보다.",
    ),
    _spec(
        "12",
        "한 회차의 세 등수 상세 비교",
        "가장 큰 관측/기대 비율 회차에서는 세 등수가 함께 튀는가?",
        "전 회차 R의 등수별 Z점수로 사례 ±12회 비교",
        "전체 자료에서 최고 비율 회차를 선택했다. 사후 선택 사례이므로 발견적 설명이며 선택 자체를 독립 검정으로 취급하지 않는다.",
        "전체 회차 Z점수 참조",
    ),
)


def build_experiments(focused: pd.DataFrame) -> dict[str, Any]:
    data = focused.copy()
    k = data["당첨게임 수"].astype(float)
    mu = data["이론_기대당첨게임수"]
    probability = data["이론_등수당첨확률"]
    data["로그비율"] = np.log((k + 0.5) / (mu + 0.5))
    data["이항잔차"] = (k - mu) / np.sqrt(mu * (1 - probability))
    data["기간보정"] = data["로그비율"] - data.groupby(["순위", "기간"])[
        "로그비율"
    ].transform("mean")
    data["판매보정"] = data["로그비율"] - data.groupby(["순위", "판매구간"])[
        "로그비율"
    ].transform("median")
    data["직전차이"] = data.groupby(["순위", "기간"], sort=False)[
        "관측_기대_비율"
    ].diff()
    for rank in RANKS:
        mask = data["순위"].eq(rank)
        log_values = data.loc[mask, "로그비율"]
        median = float(log_values.median())
        mad = float(np.median(np.abs(log_values - median)))
        if mad == 0:
            raise ValueError(f"{rank} MAD가 0이라 강건표준화를 적용할 수 없다.")
        data.loc[mask, "강건점수"] = (log_values - median) / (1.4826 * mad)

    wide = data.pivot(index="회차", columns="순위", values="강건점수")
    for rank in RANKS:
        other = [item for item in RANKS if item != rank]
        companion = wide[rank] * wide[other].mean(axis=1)
        mask = data["순위"].eq(rank)
        data.loc[mask, "동반점수"] = data.loc[mask, "회차"].map(companion)

    observations: list[pd.DataFrame] = []
    coefficients: list[dict[str, Any]] = []

    def add(
        code: str,
        frame: pd.DataFrame,
        column: str,
        robust: bool = False,
        reference: pd.DataFrame | None = None,
    ) -> None:
        for rank in RANKS:
            subset = frame.loc[frame["순위"].eq(rank) & frame[column].notna()].copy()
            values = subset[column].to_numpy(dtype=float)
            if robust:
                original = data.loc[data["순위"].eq(rank), "로그비율"].to_numpy()
                center = float(np.median(original))
                scale = 1.4826 * float(np.median(np.abs(original - center)))
                normalized = values
            elif reference is not None:
                ref = reference.loc[reference["순위"].eq(rank), column].to_numpy()
                _, center, scale = standardize(ref)
                normalized = (values - center) / scale
            else:
                normalized, center, scale = standardize(values)
            result = subset[["회차", "순위", "기간", "판매구간"]].copy()
            result["분석ID"] = code
            result["값"] = (
                subset["로그비율"].to_numpy(dtype=float) if robust else values
            )
            result["표준화값"] = normalized
            observations.append(result)
            coefficients.append(
                {
                    "분석ID": code,
                    "순위": rank,
                    "중심": center,
                    "척도": scale,
                    "표시관측수": len(values),
                    "참조관측수": len(ref) if reference is not None else len(values),
                }
            )

    for code, column in (
        ("01", "당첨게임 수"),
        ("02", "관측_기대_비율"),
        ("03", "이항잔차"),
        ("04", "로그비율"),
        ("05", "기간보정"),
        ("06", "판매보정"),
        ("07", "직전차이"),
    ):
        add(code, data, column)

    blocks, partials = [], []
    for (rank, period), group in data.groupby(["순위", "기간"], sort=False):
        group = group.sort_values("회차")
        for start in range(0, len(group), 25):
            block = group.iloc[start : start + 25]
            item = {
                "순위": rank,
                "기간": period,
                "시작회차": int(block["회차"].min()),
                "종료회차": int(block["회차"].max()),
                "포함회차수": len(block),
            }
            if len(block) < 25:
                partials.append(item)
                continue
            item.update(
                {
                    "회차": item["종료회차"],
                    "판매구간": "25회 블록",
                    "블록비율": float(
                        block["당첨게임 수"].sum() / block["이론_기대당첨게임수"].sum()
                    ),
                }
            )
            blocks.append(item)
    block_frame = pd.DataFrame(blocks)
    add("08", block_frame, "블록비율")
    add("09", data, "강건점수", robust=True)
    add("10", data, "동반점수")

    models, validation_parts = [], []
    for (rank, period), group in data.groupby(["순위", "기간"], sort=False):
        group = group.sort_values("회차")
        split = int(len(group) * 0.7)
        train, valid = group.iloc[:split], group.iloc[split:].copy()
        multiplier = float(
            train["당첨게임 수"].sum() / train["이론_기대당첨게임수"].sum()
        )
        train_mu = train["이론_기대당첨게임수"].to_numpy() * multiplier
        train_k = train["당첨게임 수"].to_numpy()
        alpha = max(
            0.0,
            float(np.sum((train_k - train_mu) ** 2 - train_mu) / np.sum(train_mu**2)),
        )
        fitted = valid["이론_기대당첨게임수"].to_numpy() * multiplier
        observed = valid["당첨게임 수"].to_numpy()
        valid["fitted_mu"] = fitted
        valid["alpha"] = alpha
        valid["NB2잔차"] = (observed - fitted) / np.sqrt(fitted + alpha * fitted**2)
        if alpha > 1e-12:
            size = 1 / alpha
            success = size / (size + fitted)
            low = nbinom.cdf(observed, size, success)
            high = nbinom.sf(observed - 1, size, success)
            lower, upper = (
                nbinom.ppf(0.025, size, success),
                nbinom.ppf(0.975, size, success),
            )
        else:
            low, high = poisson.cdf(observed, fitted), poisson.sf(observed - 1, fitted)
            lower, upper = poisson.ppf(0.025, fitted), poisson.ppf(0.975, fitted)
        valid["예측p"] = np.minimum(1, 2 * np.minimum(low, high))
        valid["예측95내"] = (observed >= lower) & (observed <= upper)
        models.append(
            {
                "순위": rank,
                "기간": period,
                "학습수": len(train),
                "검증수": len(valid),
                "학습시작회차": int(train["회차"].min()),
                "학습종료회차": int(train["회차"].max()),
                "검증시작회차": int(valid["회차"].min()),
                "검증종료회차": int(valid["회차"].max()),
                "평균배율": multiplier,
                "alpha": alpha,
                "검증95예측구간포함률": float(valid["예측95내"].mean()),
            }
        )
        validation_parts.append(valid)
    validation_frame = pd.concat(validation_parts, ignore_index=True)
    validation_frame["BH보정"] = adjust_pvalues(
        validation_frame["예측p"].to_numpy(), "BH"
    )
    validation_frame["BY보정"] = adjust_pvalues(
        validation_frame["예측p"].to_numpy(), "BY"
    )
    add("11", validation_frame, "NB2잔차")

    case_row = data.sort_values(
        ["관측_기대_비율", "회차", "순위"], ascending=[False, True, True]
    ).iloc[0]
    case_round = int(case_row["회차"])
    case_window = data.loc[data["회차"].between(case_round - 12, case_round + 12)]
    add("12", case_window, "관측_기대_비율", reference=data)
    all_observations = pd.concat(observations, ignore_index=True)
    case_table = data.loc[
        data["회차"].eq(case_round),
        [
            "회차",
            "순위",
            "추정_판매게임수",
            "당첨게임 수",
            "이론_기대당첨게임수",
            "관측_기대_비율",
            "비고",
        ],
    ].copy()
    all_ratio = all_observations.loc[
        all_observations["분석ID"].eq("02") & all_observations["회차"].eq(case_round)
    ]
    case_table["전체기준_Z점수"] = case_table["순위"].map(
        all_ratio.set_index("순위")["표준화값"]
    )
    case_table["전체비율_백분위"] = [
        float(
            data.loc[data["순위"].eq(row["순위"]), "관측_기대_비율"]
            .le(row["관측_기대_비율"])
            .mean()
        )
        for _, row in case_table.iterrows()
    ]
    for rank in RANKS:
        reference = data.loc[data["순위"].eq(rank) & data["회차"].ne(case_round)]
        neighbor = case_window.loc[
            case_window["순위"].eq(rank) & case_window["회차"].ne(case_round)
        ]
        row_mask = case_table["순위"].eq(rank)
        case_ratio = float(case_table.loc[row_mask, "관측_기대_비율"].iloc[0])
        center = float(reference["관측_기대_비율"].mean())
        scale = float(reference["관측_기대_비율"].std(ddof=1))
        neighbor_median = float(neighbor["관측_기대_비율"].median())
        case_table.loc[row_mask, "사례제외기준_Z점수"] = (case_ratio - center) / scale
        case_table.loc[row_mask, "주변비율_중앙값"] = neighbor_median
        case_table.loc[row_mask, "주변중앙값대비_배율"] = case_ratio / neighbor_median
        for label, round_number in (
            ("직전회차비율", case_round - 1),
            ("다음회차비율", case_round + 1),
        ):
            neighboring_row = data.loc[
                data["순위"].eq(rank) & data["회차"].eq(round_number)
            ]
            case_table.loc[row_mask, label] = (
                float(neighboring_row["관측_기대_비율"].iloc[0])
                if len(neighboring_row)
                else np.nan
            )

    binomial = data[["회차", "순위", "당첨게임 수", "추정_판매게임수"]].copy()
    counts = data["당첨게임 수"].to_numpy()
    n = data["추정_판매게임수"].to_numpy()
    p = probability.to_numpy()
    binomial["이항_이중꼬리p"] = np.minimum(
        1, 2 * np.minimum(binom.cdf(counts, n, p), binom.sf(counts - 1, n, p))
    )
    binomial["BH보정"] = adjust_pvalues(binomial["이항_이중꼬리p"].to_numpy(), "BH")
    binomial["BY보정"] = adjust_pvalues(binomial["이항_이중꼬리p"].to_numpy(), "BY")
    period_summary = (
        data.groupby(["순위", "기간"], sort=False)
        .agg(
            회차수=("회차", "size"),
            평균비율=("관측_기대_비율", "mean"),
            중앙비율=("관측_기대_비율", "median"),
            로그평균=("로그비율", "mean"),
        )
        .reset_index()
    )
    sales_summary = (
        data.groupby(["순위", "판매구간"], sort=False)
        .agg(
            회차수=("회차", "size"),
            최소판매=("추정_판매게임수", "min"),
            최대판매=("추정_판매게임수", "max"),
            중앙비율=("관측_기대_비율", "median"),
        )
        .reset_index()
    )
    correlations = []
    for rank in RANKS:
        others = [value for value in RANKS if value != rank]
        correlations.append(
            {
                "순위": rank,
                **{
                    f"{other}_Spearman": float(
                        spearmanr(wide[rank], wide[other]).statistic
                    )
                    for other in others
                },
            }
        )
    tables = {
        "03_binomial": binomial,
        "05_periods": period_summary,
        "06_sales": sales_summary,
        "08_blocks": block_frame,
        "08_unused_partial_blocks": pd.DataFrame(partials),
        "10_correlations": pd.DataFrame(correlations),
        "11_models": pd.DataFrame(models),
        "11_predictions": validation_frame[
            [
                "회차",
                "순위",
                "기간",
                "당첨게임 수",
                "이론_기대당첨게임수",
                "fitted_mu",
                "alpha",
                "NB2잔차",
                "예측p",
                "BH보정",
                "BY보정",
                "예측95내",
            ]
        ],
        "12_case": case_table,
    }
    return {
        "focus": data,
        "experiments": list(EXPERIMENTS),
        "observations": all_observations,
        "normalization": pd.DataFrame(coefficients),
        "tables": tables,
        "case_round": case_round,
        "case_rank": str(case_row["순위"]),
    }


def summarize(result: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for (code, rank), group in result["observations"].groupby(
        ["분석ID", "순위"], sort=False
    ):
        values, scores = group["값"], group["표준화값"]
        peak = group.loc[scores.abs().idxmax()]
        rows.append(
            {
                "분석ID": code,
                "순위": rank,
                "관측수": len(group),
                "원값평균": float(values.mean()),
                "원값중앙값": float(values.median()),
                "원값표준편차": float(values.std(ddof=1)),
                "원값최소": float(values.min()),
                "원값최대": float(values.max()),
                "표준화중앙값": float(scores.median()),
                "표준화최소": float(scores.min()),
                "표준화최대": float(scores.max()),
                "절대편차3초과수": int(scores.abs().gt(3).sum()),
                "왜도": float(skew(values, bias=False)),
                "초과첨도": float(kurtosis(values, bias=False)),
                "최대절대편차회차": int(peak["회차"]),
                "최대절대편차": float(peak["표준화값"]),
                "판정": "기술통계; |점수|>3은 유의성 검정 아님",
            }
        )
    return pd.DataFrame(rows)


def plot_results(
    result: dict[str, Any], directory: Path
) -> dict[tuple[str, str], Path]:
    plt.rcParams.update(
        {
            "font.family": "Malgun Gothic",
            "axes.unicode_minus": False,
            "axes.grid": True,
            "grid.alpha": 0.2,
            "font.size": 9,
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    rng = np.random.default_rng(20261026)
    for experiment in result["experiments"]:
        code = experiment["id"]
        subset = result["observations"].loc[result["observations"]["분석ID"].eq(code)]
        all_scores = subset["표준화값"].to_numpy()
        limit = max(3.5, float(np.max(np.abs(all_scores))) * 1.12)
        for rank_index, rank in enumerate(RANKS, 1):
            group = subset.loc[subset["순위"].eq(rank)].sort_values("회차")
            scores = group["표준화값"].to_numpy()
            figure, axes = plt.subplots(
                1, 2, figsize=(11.2, 3.5), gridspec_kw={"width_ratios": [1, 1.65]}
            )
            figure.suptitle(
                f"{code}. {experiment['title']} · {rank}",
                fontweight="bold",
                fontsize=12,
            )
            axes[0].hist(
                scores,
                bins=np.linspace(-limit, limit, 61),
                density=True,
                color=COLORS[rank],
                alpha=0.7,
            )
            axes[0].set_xlabel("표준화 편차 (정규분포 가정 없음)")
            axes[0].set_ylabel("밀도")
            axes[0].set_xlim(-limit, limit)
            segments = (
                group["회차"].diff().gt(1).cumsum()
                if code in ("07", "11")
                else pd.Series(0, index=group.index)
            )
            for _, segment in group.groupby(segments):
                axes[1].plot(
                    segment["회차"],
                    segment["표준화값"],
                    linewidth=0.65,
                    color=COLORS[rank],
                )
            axes[1].scatter(group["회차"], scores, s=5, color=COLORS[rank], alpha=0.4)
            for boundary in (87.5, 400.5):
                if group["회차"].min() < boundary < group["회차"].max():
                    axes[1].axvline(
                        boundary, color="#999999", linestyle=":", linewidth=0.8
                    )
            extremes = group.loc[group["표준화값"].abs().nlargest(3).index]
            for position, (_, row) in enumerate(extremes.iterrows()):
                axes[1].scatter(
                    row["회차"], row["표준화값"], s=24, color="#a60021", zorder=4
                )
                horizontal_offset = (4, 12, -25)[position]
                span = max(1, int(group["회차"].max() - group["회차"].min()))
                if row["회차"] < group["회차"].min() + span * 0.12:
                    horizontal_offset = (8, 30, 45)[position]
                if row["회차"] > group["회차"].max() - span * 0.12:
                    horizontal_offset = (-35, -65, -85)[position]
                axes[1].annotate(
                    f"{int(row['회차'])}회",
                    (row["회차"], row["표준화값"]),
                    xytext=(horizontal_offset, (8, 24, -20)[position]),
                    textcoords="offset points",
                    fontsize=8,
                    arrowprops={"arrowstyle": "-", "color": "#777777", "lw": 0.5},
                )
            for axis in axes:
                if axis is axes[0]:
                    axis.axvline(0, color="#444444", linewidth=0.7)
                else:
                    axis.axhline(0, color="#444444", linewidth=0.7)
                    axis.axhline(3, color="#999999", linestyle="--", linewidth=0.6)
                    axis.axhline(-3, color="#999999", linestyle="--", linewidth=0.6)
                    axis.set_ylim(-limit, limit)
            axes[1].set_xlabel("회차 (08은 25회 블록 종료 회차)")
            axes[1].set_ylabel("표준화 편차; ±3은 시각적 참고선")
            figure.tight_layout(rect=(0, 0, 1, 0.92))
            path = directory / f"{code}_rank_{rank_index}.png"
            figure.savefig(path, dpi=115)
            plt.close(figure)
            paths[(code, rank)] = path

        figure, axes = plt.subplots(
            1, 2, figsize=(11.2, 4.0), gridspec_kw={"width_ratios": [1.5, 1]}
        )
        figure.suptitle(
            f"부록 {code}. {experiment['title']} · 1·2·3등 비교",
            fontweight="bold",
            fontsize=12,
        )
        arrays = [
            subset.loc[subset["순위"].eq(rank), "표준화값"].to_numpy() for rank in RANKS
        ]
        violins = axes[0].violinplot(
            arrays, showmeans=False, showmedians=True, showextrema=True, points=180
        )
        for rank, body in zip(RANKS, violins["bodies"]):
            body.set_facecolor(COLORS[rank])
            body.set_alpha(0.55)
        for index, rank in enumerate(RANKS, 1):
            group = subset.loc[subset["순위"].eq(rank)]
            axes[0].scatter(
                index + rng.uniform(-0.08, 0.08, len(group)),
                group["표준화값"],
                s=3,
                alpha=0.12,
                color=COLORS[rank],
            )
            peak = group.loc[group["표준화값"].abs().idxmax()]
            axes[0].scatter(index, peak["표준화값"], color="#a60021", s=22, zorder=5)
            axes[0].annotate(
                f"{int(peak['회차'])}회",
                (index, peak["표준화값"]),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=8,
            )
            ordered = np.sort(group["표준화값"].to_numpy())
            axes[1].plot(
                ordered,
                np.arange(1, len(ordered) + 1) / len(ordered),
                color=COLORS[rank],
                label=rank,
            )
        axes[0].set_xticks([1, 2, 3], RANKS)
        axes[0].set_ylim(-limit, limit)
        axes[0].set_ylabel("동일 척도의 표준화 편차")
        axes[0].axhline(0, color="#444444", linewidth=0.7)
        for y in (-3, 3):
            axes[0].axhline(y, linestyle="--", linewidth=0.6, color="#999999")
        axes[1].set_xlabel("표준화 편차 (전체 꼬리 포함)")
        axes[1].set_ylabel("경험적 누적 비율")
        axes[1].legend()
        figure.tight_layout(rect=(0, 0, 1, 0.92))
        path = directory / f"{code}_violin.png"
        figure.savefig(path, dpi=115)
        plt.close(figure)
        paths[(code, "violin")] = path
    return paths


def create_review_sheets(charts: dict[tuple[str, str], Path], directory: Path) -> None:
    """48개 원본 그림을 분석별 네 장씩 모아 육안 검사용 시트를 만든다."""
    directory.mkdir(parents=True, exist_ok=True)
    for experiment in EXPERIMENTS:
        code = experiment["id"]
        sheet = Image.new("RGB", (1932, 690), "white")
        draw = ImageDraw.Draw(sheet)
        for position, rank in enumerate((*RANKS, "violin")):
            path = charts[(code, rank)]
            with Image.open(path) as original:
                preview = original.convert("RGB")
                preview.thumbnail((966, 315))
                x, y = (position % 2) * 966, (position // 2) * 345
                draw.text((x + 10, y + 2), path.name, fill="black")
                sheet.paste(preview, (x, y + 20))
        sheet.save(directory / f"{code}_review.png")


def _format(value: Any) -> str:
    if pd.isna(value):
        return "해당 없음"
    if isinstance(value, (float, np.floating)):
        if value != 0 and abs(value) < 0.001:
            return f"{value:.3e}"
        return f"{value:,.4f}"
    return str(value).replace("|", "/").replace("\n", " ")


def table_markdown(frame: pd.DataFrame) -> str:
    rows = [
        "| " + " | ".join(map(str, frame.columns)) + " |",
        "| " + " | ".join("---" for _ in frame.columns) + " |",
    ]
    rows.extend(
        "| " + " | ".join(_format(item) for item in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    )
    return "\n".join(rows)


def report_text(
    result: dict[str, Any],
    summary: pd.DataFrame,
    charts: dict[tuple[str, str], Path],
    input_hash: str,
) -> tuple[str, list[str]]:
    embedded = []

    def image(code: str, rank: str) -> str:
        encoded = base64.b64encode(charts[(code, rank)].read_bytes()).decode("ascii")
        embedded.append(encoded)
        return f"![분석 {code} {rank}](data:image/png;base64,{encoded})"

    obs = result["observations"]
    case = result["tables"]["12_case"]
    model = result["tables"]["11_predictions"]
    model_summary = (
        model.groupby("순위")
        .agg(
            검증회차수=("회차", "size"),
            예측95구간포함률=("예측95내", "mean"),
            BY005미만=("BY보정", lambda x: int(x.lt(0.05).sum())),
        )
        .reset_index()
    )
    sections = [
        "# 로또 보고서 V2.1 — 1·2·3등의 회차별 편차를 깊게 보기",
        "## 범위와 읽는 순서",
        "분석 단위는 회차·등수이며, 1~1,245회 1·2·3등의 3,735행을 사용한다. "
        "중심 질문은 **판매량을 고려한 뒤에도 어떤 회차에서 당첨게임 수가 얼마나 크게 튀는가**다. "
        "12개 분석마다 본문의 1등·2등·3등 개별 분석 3개와 마지막 부록의 비교표·바이올린 1개를 제공한다. "
        "총 48개 결과 묶음이며, 바이올린은 부록에만 배치한다. 이번 보고서는 당첨게임 수에 집중한다. "
        "이 분석을 당첨금, 기대수익, 추첨 조작의 증거로 해석하지 않는다.",
        "## 실제 결과에서 먼저 확인할 사항",
        f"전체 관측/기대 비율이 가장 큰 값은 **{result['case_round']}회 {result['case_rank']}**다. "
        "같은 회차의 세 등수는 다음과 같다. 이 회차를 12번 분석에서 같은 기준으로 비교한다.",
        table_markdown(
            case[
                [
                    "회차",
                    "순위",
                    "당첨게임 수",
                    "이론_기대당첨게임수",
                    "관측_기대_비율",
                    "전체기준_Z점수",
                ]
            ]
        ),
        "시간 분리 과산포 보정의 검증 구간 결과는 다음과 같다. 예측 95% 구간 포함률이 95%와 "
        "다를 수 있으므로 모형의 보정 정도도 함께 읽어야 한다. BY<0.05는 이 모형과 고정 모수에 "
        "조건부인 탐색 후보 수다. 정상·비정상이나 비리의 확정 판정이 아니다.",
        table_markdown(model_summary),
        "## 표준화·극단값·유의성의 구분",
        "일반 분석은 등수별 `Z=(값−평균)/표본표준편차(ddof=1)`로 척도를 맞춘다. "
        "**표준화가 정규성을 보장하지 않는다.** 따라서 종 모양 곡선을 강제로 그리거나 "
        "`|Z|>3이면 유의하다`고 주장하지 않는다. 본문 그림은 실제 히스토그램과 회차별 편차다. "
        "같은 분석의 세 등수는 동일 축 범위를 사용해 꼬리를 자르지 않는다. 큰 극단값 때문에 "
        "중심이 압축될 수 있으며 그 자체도 결과다.",
        "09는 평균·표준편차 대신 중앙값/MAD를 사용한다. 12는 전체 자료의 중심·척도를 참조해 "
        "사례 주변 25회만 표시한다. 따라서 09·12의 표시값은 평균 0·표준편차 1일 필요가 없다. "
        "평균을 제거하므로 등수 간 절대 수준의 차이는 원값 표에서 확인해야 한다.",
        "통계검정은 03과 11에만 둔다. 03은 독립 게임 이항모형의 이중 꼬리확률 "
        "`min(1, 2 min(P(X≤K),P(X≥K)))`를 3,735개 회차·등수 전체에서 BH/BY 보정한다. "
        "이항모형의 과산포 부적합 때문에 참고 모형으로만 쓴다. 11은 각 등수·제도 구간에서 "
        "앞 70%로 평균배율·NB2 분산을 모멘트 추정하고 뒤 30%를 검증한다. 그 검증군 전체에서 "
        "동일한 방식의 꼬리확률을 BH/BY 보정한다. BY는 검정 간 의존에 더 보수적이지만 "
        "잘못된 모형이나 추정 모수의 불확실성까지 해결하지 않는다. 다른 분석은 기술적·탐색적 "
        "분석이며 같은 자료의 여러 표현을 독립 증거로 합산하지 않는다.",
        "일부 이항 꼬리확률은 매우 작아 부동소수점 계산에서 0으로 저장될 수 있다. 이때 출력 0을 "
        "사건의 확률이 수학적으로 정확히 0이라는 뜻으로 읽지 않는다.",
        "## 입력·전처리·경계",
        f"입력은 `lotto_preprocessed_v2.csv`, SHA-256은 `{input_hash}`다. "
        "V2의 가격 결합(1~87회 2,000원, 88회 이후 1,000원)과 판매게임 수를 재사용한다. "
        "원자료와 V2는 변경하지 않는다. 회차 필터로 초반을 버리지 않는다. "
        "제도 경계는 88회·401회로 표시하되, 경계 전후의 차이를 인과관계로 단정하지 않는다. "
        "표본별 포함·제외는 `analysis_coverage.csv`, 정규화 계수는 `normalization.csv`에 저장한다. "
        "4·5등은 이 보고서의 분석 대상에 포함하지 않는다.",
        "## 분석 목록",
        table_markdown(
            pd.DataFrame(
                [
                    {"분석": x["id"], "제목": x["title"], "확인할 질문": x["question"]}
                    for x in result["experiments"]
                ]
            )
        ),
    ]
    for experiment in result["experiments"]:
        code = experiment["id"]
        sections += [
            f"## {code}. {experiment['title']}",
            experiment["question"],
            f"산식: `{experiment['formula']}`. 표준화: {experiment['normalization']}.",
            experiment["note"],
        ]
        for rank in RANKS:
            row = summary.loc[
                summary["분석ID"].eq(code) & summary["순위"].eq(rank)
            ].iloc[0]
            group = obs.loc[obs["분석ID"].eq(code) & obs["순위"].eq(rank)]
            extremes = group.assign(절대편차=group["표준화값"].abs()).nlargest(
                3, "절대편차"
            )
            metrics = pd.DataFrame(
                [
                    {"지표": label, "결과": row[column]}
                    for label, column in (
                        ("포함 관측 수", "관측수"),
                        ("원값 평균", "원값평균"),
                        ("원값 중앙값", "원값중앙값"),
                        ("원값 표준편차", "원값표준편차"),
                        ("원값 최소", "원값최소"),
                        ("원값 최대", "원값최대"),
                        ("표준화 최소", "표준화최소"),
                        ("표준화 최대", "표준화최대"),
                        ("|표준화값|>3 관측 수(검정 아님)", "절대편차3초과수"),
                    )
                ]
            )
            metrics = pd.concat(
                [
                    metrics,
                    pd.DataFrame(
                        [
                            {
                                "지표": f"절대편차 {i}위 회차 / 점수",
                                "결과": f"{int(r['회차'])}회 / {r['표준화값']:.4f}",
                            }
                            for i, (_, r) in enumerate(extremes.iterrows(), 1)
                        ]
                    ),
                ],
                ignore_index=True,
            )
            sections += [f"### {code}-{rank}. 개별 분석", table_markdown(metrics)]
            peak = group.loc[group["표준화값"].abs().idxmax()]
            sections.append(
                f"계산 결과: {rank}의 가장 큰 절대편차는 **{int(peak['회차'])}회**, "
                f"원값 {_format(peak['값'])}, 표준화값 {peak['표준화값']:.4f}다. "
                f"포함 {_format(row['관측수'])}개 중 |표준화값|>3은 {int(row['절대편차3초과수'])}개다. "
                "이는 극단값의 위치와 크기에 대한 기술적 결과다."
            )
            if code == "03":
                stats = result["tables"]["03_binomial"].query("순위 == @rank")
                sections.append(
                    f"이항 참고모형: 전체 3,735개를 보정한 BY<0.05 회차는 {int(stats['BY보정'].lt(0.05).sum())}개다. "
                    "모형 부적합을 고려해 이를 확정 유의 회차로 사용하지 않는다."
                )
            if code in ("05", "06"):
                key = "05_periods" if code == "05" else "06_sales"
                sections.append(
                    table_markdown(result["tables"][key].query("순위 == @rank"))
                )
            if code == "08":
                partial = result["tables"]["08_unused_partial_blocks"].query(
                    "순위 == @rank"
                )
                sections.append(
                    f"이 분석에서만 사용하지 않은 마지막 미완성 블록의 합은 {int(partial['포함회차수'].sum())}회다. 원자료에서는 보존했다."
                )
            if code == "10":
                sections.append(
                    table_markdown(
                        result["tables"]["10_correlations"].query("순위 == @rank")
                    )
                )
            if code == "11":
                predictions = result["tables"]["11_predictions"].query("순위 == @rank")
                sections.append(
                    f"검증회차 {len(predictions)}개, 95% 예측구간 포함률 {predictions['예측95내'].mean():.2%}, "
                    f"전체 검증군에서 보정한 BY<0.05 후보 {int(predictions['BY보정'].lt(0.05).sum())}개."
                )
                sections.append(
                    table_markdown(result["tables"]["11_models"].query("순위 == @rank"))
                )
            if code == "12":
                sections.append(table_markdown(case.query("순위 == @rank")))
                prediction = result["tables"]["11_predictions"].loc[
                    result["tables"]["11_predictions"]["순위"].eq(rank)
                    & result["tables"]["11_predictions"]["회차"].eq(
                        result["case_round"]
                    )
                ]
                if len(prediction):
                    sections.append(
                        "같은 회차가 11번의 학습 밖 검증 구간에 포함된다. 아래는 당시 이전 학습 자료만 사용한 결과다."
                    )
                    sections.append(
                        table_markdown(
                            prediction[
                                [
                                    "순위",
                                    "회차",
                                    "fitted_mu",
                                    "NB2잔차",
                                    "예측p",
                                    "BY보정",
                                    "예측95내",
                                ]
                            ]
                        )
                    )
                else:
                    sections.append(
                        "이 사례는 11번 모형의 검증 구간에 없으므로 별도 모형 유의성을 주장하지 않는다."
                    )
                sections.append(
                    "주변 중앙값은 사례 자체를 제외한 ±12회 자료에서 계산했다. 사례제외 Z점수는 전체 기준의 중심·척도가 극단 회차 자체에 얼마나 영향을 받는지 확인하는 민감도 지표다. 이 비교를 새 p값으로 해석하지 않는다."
                )
            sections.append(image(code, rank))
    sections += [
        "## 부록: 분석별 1·2·3등 비교",
        "각 분석의 네 번째 결과다. 동일한 분석 안에서 세 등수의 표준화값을 한 그림에 비교한다. "
        "바이올린의 폭은 커널 밀도 추정이며 당첨확률이 아니다. 가는 범위선은 전체 극값, "
        "가운데 표시는 중앙값, 작은 점은 실제 관측값, 빨간 점·번호는 최대 절대편차 회차다. "
        "오른쪽 경험적 누적분포는 전체 꼬리를 포함한다. ±3선은 시각적 참고다.",
    ]
    for experiment in result["experiments"]:
        code = experiment["id"]
        compared = summary.loc[
            summary["분석ID"].eq(code),
            [
                "순위",
                "관측수",
                "원값중앙값",
                "표준화중앙값",
                "표준화최소",
                "표준화최대",
                "절대편차3초과수",
                "최대절대편차회차",
            ],
        ]
        sections += [
            f"### 부록 {code}. {experiment['title']}",
            table_markdown(compared),
            image(code, "violin"),
        ]
    sections += [
        "## 한계와 다음 논의",
        "최대 편차 회차와 조합 특성의 관계는 원인을 입증하지 않는다. 당첨게임 수는 사람 수가 아니고, "
        "동일 번호 반복 구매는 서로 독립적인 시행이 아니다. 03의 이항분포와 11의 NB2는 서로 다른 "
        "가정의 참고 모형이다. 11의 BY 후보도 모형 교체·모수 불확실성·시간 의존을 검증하기 전에는 "
        "확정 이상 회차로 선언할 수 없다. 사례의 자동·수동 비고는 당첨된 게임의 방식만 알리며 "
        "전체 구매자 방식 비율을 알려주지 않는다. 클러스터링은 수행하지 않았다.",
        "## 재현과 근거",
        "```powershell\npython -X utf8 class2_rottery/lotto_analysis_v2_1.py\npython -m pytest -q -p no:cacheprovider\n```",
        "분석별 관측값 CSV와 결과표, 모수, 예측, 검증 JSON 및 파일 해시는 `analysis_results_v2_1`에 저장한다. "
        "문장의 해석은 LLM 직접 작성이며 외부 사실의 근거로 간주하지 않는다. 표·그림의 수치는 "
        "사용자 CSV를 코드로 계산한 산출물이며 계산식·테스트·입력 해시로 추적할 수 있다.",
        "\n".join(f"- [{title}]({url})" for title, url in SOURCES),
    ]
    return "\n\n".join(sections) + "\n", embedded


def validate(
    result: dict[str, Any], summary: pd.DataFrame, charts: dict
) -> dict[str, Any]:
    checks = []

    def check(name: str, passed: bool) -> None:
        checks.append({"check": name, "passed": bool(passed)})

    data, obs = result["focus"], result["observations"]
    check("세 등수만 포함", set(data["순위"]) == set(RANKS))
    check("입력 회차별 세 등수 완전성", data.groupby("회차").size().eq(3).all())
    check(
        "분석 12개와 등수별 결과표 36개",
        len(result["experiments"]) == 12 and len(summary) == 36,
    )
    check(
        "분석별 세 등수 모두 포함",
        all(set(g["순위"]) == set(RANKS) for _, g in obs.groupby("분석ID")),
    )
    check(
        "모든 관측값·정규화 값 유한", np.isfinite(obs[["값", "표준화값"]]).all().all()
    )
    check("그림 48개", len(charts) == 48)
    models = result["tables"]["11_models"]
    check(
        "미래 검증 자료를 학습에 사용하지 않음",
        (models["학습종료회차"] < models["검증시작회차"]).all(),
    )
    check(
        "연속 차이의 제도 경계 배제",
        not obs.loc[obs["분석ID"].eq("07"), "회차"].isin([1, 88, 401]).any(),
    )
    check(
        "같은 회차로 세 등수 사례 비교",
        result["tables"]["12_case"]["회차"].nunique() == 1,
    )
    z_ok = True
    for experiment in result["experiments"]:
        if experiment["normalization"] != "등수별 Z점수":
            continue
        for _, g in obs.loc[obs["분석ID"].eq(experiment["id"])].groupby("순위"):
            z_ok &= bool(
                np.isclose(g["표준화값"].mean(), 0, atol=1e-10)
                and np.isclose(g["표준화값"].std(ddof=1), 1)
            )
    check("Z점수 평균0·표본표준편차1", z_ok)
    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "checks": checks,
    }


def run(source: str | Path, output: str | Path) -> dict[str, Any]:
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    initial_hash = sha256_file(source)
    focused = load_focus(source)
    result = build_experiments(focused)
    summary = summarize(result)
    result["summary"] = summary
    charts = plot_results(result, output / "charts")
    create_review_sheets(charts, output / "visual_review")
    validation = validate(result, summary, charts)
    if validation["status"] != "passed":
        raise RuntimeError(json.dumps(validation, ensure_ascii=False))
    focused.to_csv(output / "lotto_focus_v2_1.csv", index=False, encoding="utf-8-sig")
    result["observations"].to_csv(
        output / "analysis_observations.csv", index=False, encoding="utf-8-sig"
    )
    summary.to_csv(output / "analysis_summary.csv", index=False, encoding="utf-8-sig")
    result["normalization"].to_csv(
        output / "normalization.csv", index=False, encoding="utf-8-sig"
    )
    tables_dir = output / "tables"
    tables_dir.mkdir(exist_ok=True)
    coverage = []
    for experiment in result["experiments"]:
        code = experiment["id"]
        summary.loc[summary["분석ID"].eq(code)].to_csv(
            tables_dir / f"{code}_comparison.csv", index=False, encoding="utf-8-sig"
        )
        for rank_index, rank in enumerate(RANKS, 1):
            group = result["observations"].loc[
                result["observations"]["분석ID"].eq(code)
                & result["observations"]["순위"].eq(rank)
            ]
            group.to_csv(
                tables_dir / f"{code}_rank_{rank_index}.csv",
                index=False,
                encoding="utf-8-sig",
            )
            reason = {
                "07": "제도 구간 첫 회차 3개 제외",
                "08": "25회 완성 비중복 블록만 사용; 미완성 블록 별도 저장",
                "11": "앞70% 학습, 뒤30%만 표시",
                "12": "선택 사례 ±12회만 표시",
            }.get(code, "전체 회차 사용")
            coverage.append(
                {
                    "분석ID": code,
                    "순위": rank,
                    "입력회차수": int(focused["순위"].eq(rank).sum()),
                    "표시관측수": len(group),
                    "단위": "25회 블록" if code == "08" else "회차",
                    "포함제외규칙": reason,
                }
            )
    for name, table in result["tables"].items():
        table.to_csv(tables_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(coverage).to_csv(
        output / "analysis_coverage.csv", index=False, encoding="utf-8-sig"
    )
    report, embedded = report_text(result, summary, charts, initial_hash)
    report_path = output / "lotto_rank_report_v2_1.md"
    report_path.write_text(report, encoding="utf-8")
    body = markdown.markdown(report, extensions=["tables", "fenced_code"])
    html = (
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>로또 V2.1</title><style>body{font:16px/1.65 sans-serif;max-width:1200px;margin:40px auto;padding:0 24px;color:#17212b}img{max-width:100%;height:auto}table{border-collapse:collapse;font-size:13px;display:block;overflow-x:auto}td,th{border:1px solid #ccd3db;padding:6px 10px}th{background:#edf3f8}h2{margin-top:48px;border-bottom:1px solid #ccc}pre{background:#edf3f8;padding:14px;overflow:auto}@media print{img{break-inside:avoid}h2{break-before:page}}</style><body>'
        + body
        + "</body></html>"
    )
    (output / "lotto_rank_report_v2_1.html").write_text(html, encoding="utf-8")
    validation["checks"].append(
        {
            "check": "실행 전후 입력 해시 보존",
            "passed": sha256_file(source) == initial_hash,
        }
    )
    validation["checks"].append(
        {
            "check": "보고서 그림48개 임베딩",
            "passed": report.count("data:image/png;base64,") == 48
            and html.count("data:image/png;base64,") == 48,
        }
    )
    validation["status"] = (
        "passed" if all(item["passed"] for item in validation["checks"]) else "failed"
    )
    (output / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifacts = [
        {
            "path": str(path.relative_to(output)),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
        and path.name
        not in {"manifest.json", "execution_log.json", "independent_audit.json"}
    ]
    manifest = {
        "version": "2.1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -X utf8 class2_rottery/lotto_analysis_v2_1.py",
        "input_path": str(source),
        "input_sha256": initial_hash,
        "ranks": list(RANKS),
        "input_rows": len(focused),
        "analysis_count": 12,
        "rank_outputs": 36,
        "appendix_outputs": 12,
        "case_selection": "전체 자료 최고 관측/기대 비율, 동률이면 앞 회차·등수",
        "case_round": result["case_round"],
        "case_rank": result["case_rank"],
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
        "experiments": result["experiments"],
        "sources": SOURCES,
        "inference_caveat": "NB2 모수 불확실성을 제외한 탐색적 검정; 비리나 원인 판정 아님",
        "clustering_executed": False,
        "artifacts": artifacts,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "validation": validation,
        "report": report_path,
        "case_round": result["case_round"],
        "embedded_images": embedded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument(
        "--source", type=Path, default=base / "lotto_preprocessed_v2.csv"
    )
    parser.add_argument("--output", type=Path, default=base / "analysis_results_v2_1")
    args = parser.parse_args()
    result = run(args.source, args.output)
    for extension in ("md", "html"):
        name = f"lotto_rank_report_v2_1.{extension}"
        (base / name).write_bytes((args.output / name).read_bytes())
    print(
        json.dumps(
            {
                "validation": result["validation"]["status"],
                "analyses": 12,
                "rank_outputs": 36,
                "appendix_outputs": 12,
                "report": str(base / "lotto_rank_report_v2_1.md"),
                "case_round": result["case_round"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["validation"]["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
