"""로또 6/45 기대수익 분석과 자체 포함형 Markdown 보고서 생성기.

실행:
    python lotto_analysis.py

원본 CSV는 수정하지 않는다. 분석 표와 그림은 ``analysis_results``에 저장하고,
그림을 data URI로 내장한 ``lotto_rank_report.md``를 재생성한다.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from matplotlib.ticker import FuncFormatter
from scipy.stats import binomtest, chi2, kruskal, mannwhitneyu, spearmanr

TOTAL_COMBINATIONS = math.comb(45, 6)
RANK_FAVORABLE_COMBINATIONS = {
    "1등": 1,
    "2등": 6,
    "3등": math.comb(6, 5) * 38,
    "4등": math.comb(6, 4) * math.comb(39, 2),
    "5등": math.comb(6, 3) * math.comb(39, 3),
}
RANKS = tuple(RANK_FAVORABLE_COMBINATIONS)
CURRENT_RULE_START_ROUND = 401
MODE_DATA_START_ROUND = 262
SIMULATION_SEED = 20261017


def configure_plots() -> None:
    """Windows 환경에서 한글을 포함한 그림 스타일을 통일한다."""
    plt.rcParams.update(
        {
            "font.family": "Malgun Gothic",
            "axes.unicode_minus": False,
            "figure.dpi": 120,
            "savefig.dpi": 140,
            "axes.grid": True,
            "grid.alpha": 0.25,
        }
    )


def rank_probability_table() -> pd.DataFrame:
    """등수별 정확한 조합 수와 이론적 확률을 반환한다."""
    rows = []
    for rank, favorable in RANK_FAVORABLE_COMBINATIONS.items():
        probability = favorable / TOTAL_COMBINATIONS
        rows.append(
            {
                "순위": rank,
                "유리한 조합 수": favorable,
                "전체 조합 수": TOTAL_COMBINATIONS,
                "확률": probability,
                "약_몇분의1": 1 / probability,
            }
        )
    return pd.DataFrame(rows)


def current_net_prize(amount: float) -> float:
    """2026년 기준 복권 당첨금의 지방소득세 포함 실수령액을 계산한다.

    200만원 이하는 비과세다. 과세 대상이면 3억원까지 22%, 3억원을
    넘는 부분에는 33%를 적용한다. 과거 시점의 세법을 소급 적용하는 함수가 아니다.
    """
    if amount <= 2_000_000:
        return float(amount)
    if amount <= 300_000_000:
        return float(amount) * 0.78
    return 300_000_000 * 0.78 + (float(amount) - 300_000_000) * 0.67


def ticket_price_for_round(round_number: int) -> int:
    """자료에 나타난 가격 제도 구간에 따라 1게임 가격을 반환한다."""
    return 2_000 if round_number <= 87 else 1_000


def load_rank_data(path: str | Path) -> pd.DataFrame:
    """CSV를 읽고 분석에 필요한 수치 칼럼을 정수형으로 변환한다."""
    frame = pd.read_csv(path, encoding="utf-8-sig")
    required = {
        "회차",
        "순위",
        "등위별 총 당첨금",
        "당첨게임 수",
        "1게임당 당첨금",
        "비고",
        "총판매금액",
        *(f"당첨번호{i}" for i in range(1, 7)),
        "보너스번호",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"필수 칼럼 누락: {sorted(missing)}")

    numeric = [
        "회차",
        "등위별 총 당첨금",
        "당첨게임 수",
        "1게임당 당첨금",
        "총판매금액",
        *(f"당첨번호{i}" for i in range(1, 7)),
        "보너스번호",
    ]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="raise")
    return frame.sort_values(["회차", "순위"]).reset_index(drop=True)


def _parse_purchase_modes(text: str) -> tuple[int, int, int]:
    values = re.findall(r"(?:자동|수동|반자동)(\d+)", str(text))
    if len(values) != 3:
        return (0, 0, 0)
    return tuple(map(int, values))  # type: ignore[return-value]


def build_round_table(rank_data: pd.DataFrame) -> pd.DataFrame:
    """등수별 5행 데이터를 회차별 1행 데이터로 변환한다."""
    ordered = rank_data.sort_values(["회차", "순위"]).copy()
    base = ordered.drop_duplicates("회차").copy()
    number_columns = [f"당첨번호{i}" for i in range(1, 7)]
    base["당첨번호"] = list(
        base[number_columns].astype(int).itertuples(index=False, name=None)
    )

    keep = ["회차", "총판매금액", "비고", "보너스번호", "당첨번호"]
    rounds = base[keep].copy()
    for rank in RANKS:
        subset = ordered.loc[ordered["순위"] == rank].set_index("회차")
        rounds[f"{rank}_등위별 총 당첨금"] = rounds["회차"].map(
            subset["등위별 총 당첨금"]
        )
        rounds[f"{rank}_당첨게임 수"] = rounds["회차"].map(subset["당첨게임 수"])
        rounds[f"{rank}_1게임당 당첨금"] = rounds["회차"].map(subset["1게임당 당첨금"])

    modes = rounds["비고"].map(_parse_purchase_modes)
    rounds[["1등_자동", "1등_수동", "1등_반자동"]] = pd.DataFrame(
        modes.tolist(), index=rounds.index
    )
    rounds["구매방식_완전"] = (
        rounds[["1등_자동", "1등_수동", "1등_반자동"]].sum(axis=1)
        == rounds["1등_당첨게임 수"]
    ) & (rounds["회차"] >= MODE_DATA_START_ROUND)
    rounds["1게임_가격"] = rounds["회차"].map(ticket_price_for_round)
    rounds["추정_판매게임 수"] = rounds["총판매금액"] / rounds["1게임_가격"]
    return rounds.sort_values("회차").reset_index(drop=True)


def combination_features(
    numbers: tuple[int, ...], previous_numbers: set[int] | None = None
) -> dict[str, int]:
    """구매자 선택 쏠림과 관련될 수 있는 조합 특성을 계산한다."""
    ordered = tuple(sorted(numbers))
    ending_counts = Counter(number % 10 for number in ordered)
    return {
        "합계": sum(ordered),
        "범위": ordered[-1] - ordered[0],
        "홀수_개수": sum(number % 2 for number in ordered),
        "22이하_개수": sum(number <= 22 for number in ordered),
        "31이하_개수": sum(number <= 31 for number in ordered),
        "생일형_전체": int(max(ordered) <= 31),
        "연속쌍_수": sum(b - a == 1 for a, b in zip(ordered, ordered[1:])),
        "같은끝자리쌍_수": sum(
            math.comb(count, 2) for count in ending_counts.values() if count >= 2
        ),
        "직전회차_중복수": len(set(ordered) & (previous_numbers or set())),
    }


def add_combination_features(rounds: pd.DataFrame) -> pd.DataFrame:
    """모든 회차에 조합 특성을 추가한다."""
    result = rounds.copy()
    feature_rows = []
    previous: set[int] | None = None
    for numbers in result["당첨번호"]:
        feature_rows.append(combination_features(numbers, previous))
        previous = set(numbers)
    return pd.concat(
        [result.reset_index(drop=True), pd.DataFrame(feature_rows)], axis=1
    )


def _holm_adjust(pvalues: dict[int, float]) -> dict[int, float]:
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    adjusted: dict[int, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (number, pvalue) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * pvalue))
        adjusted[number] = running
    return adjusted


def _wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    z = 1.959963984540054
    rate = successes / trials
    denominator = 1 + z**2 / trials
    center = (rate + z**2 / (2 * trials)) / denominator
    half = (
        z * math.sqrt(rate * (1 - rate) / trials + z**2 / (4 * trials**2)) / denominator
    )
    return center - half, center + half


def analyze_number_frequency(
    rounds: pd.DataFrame, *, bonus: bool = False
) -> tuple[pd.DataFrame, dict[str, float]]:
    """본번호 또는 보너스번호의 균등성을 검정한다."""
    counter: Counter[int] = Counter()
    if bonus:
        counter.update(rounds["보너스번호"].astype(int))
        selected_per_round = 1
    else:
        for numbers in rounds["당첨번호"]:
            counter.update(numbers)
        selected_per_round = 6

    trials = len(rounds)
    probability = selected_per_round / 45
    expected = trials * probability
    raw_pvalues = {
        number: binomtest(counter[number], trials, probability).pvalue
        for number in range(1, 46)
    }
    adjusted = _holm_adjust(raw_pvalues)
    rows = []
    for number in range(1, 46):
        count = counter[number]
        lower, upper = _wilson_interval(count, trials)
        rows.append(
            {
                "번호": number,
                "출현횟수": count,
                "기대횟수": expected,
                "출현률": count / trials,
                "95%CI_하한": lower,
                "95%CI_상한": upper,
                "개별_p값": raw_pvalues[number],
                "Holm_보정_p값": adjusted[number],
            }
        )

    variance = trials * probability * (1 - probability)
    contrast_variance = variance * 45 / 44
    squared_deviation = sum((counter[n] - expected) ** 2 for n in range(1, 46))
    statistic = squared_deviation / contrast_variance
    summary = {
        "회차수": trials,
        "회차당_선택수": selected_per_round,
        "번호당_기대횟수": expected,
        "균등성_검정통계량": statistic,
        "균등성_p값": float(chi2.sf(statistic, 44)),
        "최소횟수": min(counter.values()),
        "최대횟수": max(counter.values()),
        "최소번호": min(counter, key=counter.get),
        "최대번호": max(counter, key=counter.get),
    }
    return pd.DataFrame(rows), summary


def covariance_matched_extreme_simulation(
    rounds: int,
    selected_per_round: int,
    observed_counts: np.ndarray,
    simulations: int = 300_000,
    seed: int = SIMULATION_SEED,
) -> dict[str, float]:
    """고정 개수 비복원 추출의 공분산을 맞춘 정규근사 모의실험."""
    probability = selected_per_round / 45
    expected = rounds * probability
    variance = rounds * probability * (1 - probability) * 45 / 44
    observed_range = float(observed_counts.max() - observed_counts.min())
    observed_abs = float(np.abs(observed_counts - expected).max())
    observed_max = float(observed_counts.max())
    observed_min = float(observed_counts.min())

    rng = np.random.default_rng(seed)
    chunk = 50_000
    completed = 0
    range_ge = abs_ge = max_ge = min_le = 0
    while completed < simulations:
        size = min(chunk, simulations - completed)
        standard = rng.standard_normal((size, 45))
        standard -= standard.mean(axis=1, keepdims=True)
        simulated = expected + math.sqrt(variance) * standard
        maxima = simulated.max(axis=1)
        minima = simulated.min(axis=1)
        range_ge += int(np.count_nonzero(maxima - minima >= observed_range - 1))
        abs_ge += int(
            np.count_nonzero(
                np.abs(simulated - expected).max(axis=1) >= observed_abs - 0.5
            )
        )
        max_ge += int(np.count_nonzero(maxima >= observed_max - 0.5))
        min_le += int(np.count_nonzero(minima <= observed_min + 0.5))
        completed += size

    return {
        "반복수": simulations,
        "seed": seed,
        "관측_최대최소차": observed_range,
        "최대최소차_이상_확률": range_ge / simulations,
        "최대절대편차_이상_확률": abs_ge / simulations,
        "관측최대_이상_확률": max_ge / simulations,
        "관측최소_이하_확률": min_le / simulations,
    }


def analyze_combinations(
    rounds: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    dict[str, Any],
]:
    """정확한 조합 반복, 동반 번호, 구조적 패턴을 요약한다."""
    exact = Counter(tuple(numbers) for numbers in rounds["당첨번호"])
    pair_counts: Counter[tuple[int, int]] = Counter()
    triple_counts: Counter[tuple[int, int, int]] = Counter()
    for numbers in rounds["당첨번호"]:
        pair_counts.update(combinations(numbers, 2))
        triple_counts.update(combinations(numbers, 3))

    pair_table = pd.DataFrame(
        [
            {"번호1": pair[0], "번호2": pair[1], "동반출현횟수": count}
            for pair, count in pair_counts.most_common()
        ]
    )
    triple_table = pd.DataFrame(
        [
            {
                "번호1": triple[0],
                "번호2": triple[1],
                "번호3": triple[2],
                "동반출현횟수": count,
            }
            for triple, count in triple_counts.most_common()
        ]
    )

    pattern_rows = []
    for feature, condition in {
        "생일형 전체(모두 31 이하)": rounds["생일형_전체"] == 1,
        "연속번호 포함": rounds["연속쌍_수"] >= 1,
        "같은 끝자리 쌍 포함": rounds["같은끝자리쌍_수"] >= 1,
        "홀짝 3:3": rounds["홀수_개수"] == 3,
        "직전 회차 번호 포함": rounds["직전회차_중복수"] >= 1,
    }.items():
        pattern_rows.append(
            {
                "조합특성": feature,
                "회차수": int(condition.sum()),
                "비율": float(condition.mean()),
                "평균_1등당첨게임": float(
                    rounds.loc[condition, "1등_당첨게임 수"].mean()
                ),
                "평균_수동1등": float(rounds.loc[condition, "1등_수동"].mean()),
            }
        )

    repeated = {combo: count for combo, count in exact.items() if count > 1}
    summary = {
        "서로다른_정확조합수": len(exact),
        "반복된_정확조합수": len(repeated),
        "최대_정확조합_반복수": max(exact.values()),
    }
    exact_table = pd.DataFrame(
        [
            {
                **{f"번호{i + 1}": number for i, number in enumerate(combo)},
                "횟수": count,
            }
            for combo, count in exact.most_common()
        ]
    )
    return exact_table, pair_table, triple_table, pd.DataFrame(pattern_rows), summary


def analyze_crowd_sharing(
    rounds: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """조합 특성과 1등 공동당첨게임 수의 연관성을 탐색한다."""
    data = rounds.loc[rounds["구매방식_완전"]].copy()
    data["1억게임당_1등"] = (
        data["1등_당첨게임 수"] / data["추정_판매게임 수"] * 100_000_000
    )
    data["1억게임당_수동1등"] = (
        data["1등_수동"] / data["추정_판매게임 수"] * 100_000_000
    )

    binary_features = {
        "생일형 전체": data["생일형_전체"] == 1,
        "연속번호 포함": data["연속쌍_수"] >= 1,
        "같은 끝자리 쌍 포함": data["같은끝자리쌍_수"] >= 1,
        "직전 회차 번호 포함": data["직전회차_중복수"] >= 1,
    }
    comparisons = []
    for label, condition in binary_features.items():
        yes = data.loc[condition, "1억게임당_수동1등"]
        no = data.loc[~condition, "1억게임당_수동1등"]
        pvalue = mannwhitneyu(yes, no, alternative="two-sided").pvalue
        comparisons.append(
            {
                "조합특성": label,
                "해당_회차수": len(yes),
                "비해당_회차수": len(no),
                "해당_평균_수동1등_1억게임당": yes.mean(),
                "비해당_평균_수동1등_1억게임당": no.mean(),
                "평균차이": yes.mean() - no.mean(),
                "Mann_Whitney_p값": pvalue,
            }
        )

    model_features = [
        "생일형_전체",
        "연속쌍_수",
        "같은끝자리쌍_수",
        "직전회차_중복수",
        "합계",
        "범위",
        "홀수_개수",
    ]
    design = data[model_features].astype(float).copy()
    for column in ["합계", "범위", "홀수_개수"]:
        design[column] = (design[column] - design[column].mean()) / design[column].std(
            ddof=0
        )
    design = sm.add_constant(design)
    model = sm.GLM(
        data["1등_당첨게임 수"],
        design,
        family=sm.families.NegativeBinomial(alpha=1.0),
        offset=np.log(data["추정_판매게임 수"]),
    ).fit(cov_type="HC0")
    coefficients = pd.DataFrame(
        {
            "변수": model.params.index,
            "계수": model.params.values,
            "발생률비": np.exp(model.params.values),
            "p값": model.pvalues.values,
            "95%CI_하한": np.exp(model.conf_int()[0].values),
            "95%CI_상한": np.exp(model.conf_int()[1].values),
        }
    )

    spearman = {}
    for column in model_features:
        rho, pvalue = spearmanr(data[column], data["1억게임당_수동1등"])
        spearman[column] = {"rho": float(rho), "p값": float(pvalue)}
    summary = {
        "분석회차수": len(data),
        "모형_deviance": float(model.deviance),
        "모형_aic": float(model.aic),
        "수동1등_평균": float(data["1등_수동"].mean()),
        "수동1등_중앙값": float(data["1등_수동"].median()),
        "상관분석": spearman,
    }
    return pd.DataFrame(comparisons), coefficients, summary


def expected_value_for_prizes(
    prizes: dict[str, float], ticket_cost: float = 1_000
) -> dict[str, float]:
    """주어진 등수별 당첨금에서 1게임의 세전·세후 기대값을 계산한다."""
    probabilities = rank_probability_table().set_index("순위")["확률"]
    gross = sum(probabilities[rank] * prizes[rank] for rank in RANKS)
    net = sum(probabilities[rank] * current_net_prize(prizes[rank]) for rank in RANKS)
    return {
        "세전_기대수령액": float(gross),
        "세후_기대수령액": float(net),
        "세전_순기대이익": float(gross - ticket_cost),
        "세후_순기대이익": float(net - ticket_cost),
        "세전_ROI": float(gross / ticket_cost - 1),
        "세후_ROI": float(net / ticket_cost - 1),
    }


def counterfactual_prizes_for_added_ticket(row: pd.Series) -> dict[str, float]:
    """구매할 1게임이 당첨된 경우의 등수별 당첨금을 추정한다.

    1~3등은 공동당첨게임 수가 하나 늘어난다고 가정한다. 1등 당첨게임이
    없던 회차의 표시 총액은 0이므로 3등 배분액의 6배로 기본 1등 배분액을
    추정한다. 4~5등은 고정 지급액을 그대로 사용한다.
    """
    pools = {
        rank: float(row[f"{rank}_등위별 총 당첨금"]) for rank in ("1등", "2등", "3등")
    }
    counts = {rank: int(row[f"{rank}_당첨게임 수"]) for rank in ("1등", "2등", "3등")}
    if counts["1등"] == 0:
        pools["1등"] = 6 * pools["3등"]
    if counts["2등"] == 0:
        pools["2등"] = pools["3등"]
    if counts["3등"] == 0:
        pools["3등"] = pools["2등"]

    return {
        "1등": pools["1등"] / (counts["1등"] + 1),
        "2등": pools["2등"] / (counts["2등"] + 1),
        "3등": pools["3등"] / (counts["3등"] + 1),
        "4등": float(row["4등_1게임당 당첨금"]),
        "5등": float(row["5등_1게임당 당첨금"]),
    }


def calculate_round_expected_values(rounds: pd.DataFrame) -> pd.DataFrame:
    """추가로 구매할 1게임의 회차별 사후적 기대수익을 계산한다."""
    result = rounds.copy()
    records = []
    for _, row in result.iterrows():
        prizes = counterfactual_prizes_for_added_ticket(row)
        record = expected_value_for_prizes(prizes, ticket_cost=float(row["1게임_가격"]))
        record.update(
            {f"{rank}_추가게임_가정당첨금": prize for rank, prize in prizes.items()}
        )
        records.append(record)
    values = pd.DataFrame(records)
    result = pd.concat([result.reset_index(drop=True), values], axis=1)
    result["직전회차_1등없음"] = (
        result["1등_당첨게임 수"].shift(1).fillna(-1) == 0
    ).astype(int)
    result["회차_mod5"] = ((result["회차"] - 1) % 5 + 1).astype(int)
    result["세전_기대수령액_50회평균"] = (
        result["세전_기대수령액"].rolling(50, min_periods=10).mean()
    )
    return result


def expanding_mod5_selector(
    data: pd.DataFrame, minimum_history: int = 100
) -> pd.Series:
    """각 회차 직전까지의 자료로만 가장 높았던 mod5 그룹을 선택한다."""
    decisions = []
    history = data.iloc[0:0].copy()
    for _, row in data.iterrows():
        if len(history) < minimum_history:
            decisions.append(False)
        else:
            best = history.groupby("회차_mod5")["세전_기대수령액"].mean().idxmax()
            decisions.append(row["회차_mod5"] == best)
        history = pd.concat([history, row.to_frame().T], ignore_index=True)
    return pd.Series(decisions, index=data.index)


def analyze_rank_ev(
    round_ev: pd.DataFrame, start_round: int = CURRENT_RULE_START_ROUND
) -> pd.DataFrame:
    """현행 고정 4·5등 구간에서 등수별 기대수익 기여도를 계산한다."""
    data = round_ev.loc[round_ev["회차"] >= start_round]
    probabilities = rank_probability_table().set_index("순위")["확률"]
    rows = []
    for rank in RANKS:
        prizes = data[f"{rank}_추가게임_가정당첨금"].astype(float)
        gross = probabilities[rank] * prizes
        net = probabilities[rank] * prizes.map(current_net_prize)
        rows.append(
            {
                "순위": rank,
                "확률": probabilities[rank],
                "평균_당첨금": prizes.mean(),
                "평균_세전기대수령액": gross.mean(),
                "평균_세후기대수령액": net.mean(),
                "세전기대수령액_비중": 0.0,
            }
        )
    result = pd.DataFrame(rows)
    total = result["평균_세전기대수령액"].sum()
    result["세전기대수령액_비중"] = result["평균_세전기대수령액"] / total
    return result


def analyze_round_timing(
    round_ev: pd.DataFrame,
    start_round: int = CURRENT_RULE_START_ROUND,
    permutations: int = 50_000,
    seed: int = SIMULATION_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """5회 주기, 자기상관, 이월 다음 회차의 기대수익 차이를 검정한다."""
    data = round_ev.loc[round_ev["회차"] >= start_round].copy()
    cycle = (
        data.groupby("회차_mod5")["세전_기대수령액"]
        .agg(["count", "mean", "median", "std"])
        .reset_index()
        .rename(
            columns={
                "count": "회차수",
                "mean": "평균_세전기대수령액",
                "median": "중앙_세전기대수령액",
                "std": "표준편차",
            }
        )
    )
    groups = [
        group["세전_기대수령액"].to_numpy() for _, group in data.groupby("회차_mod5")
    ]
    kruskal_p = float(kruskal(*groups).pvalue)
    observed_range = float(
        cycle["평균_세전기대수령액"].max() - cycle["평균_세전기대수령액"].min()
    )
    rng = np.random.default_rng(seed)
    values = data["세전_기대수령액"].to_numpy()
    labels = data["회차_mod5"].to_numpy()
    exceed = 0
    for _ in range(permutations):
        shuffled = rng.permutation(values)
        means = [shuffled[labels == label].mean() for label in range(1, 6)]
        exceed += max(means) - min(means) >= observed_range
    permutation_p = (exceed + 1) / (permutations + 1)

    autocorrelation = pd.DataFrame(
        {
            "시차": range(1, 21),
            "자기상관": [
                data["세전_기대수령액"].autocorr(lag=lag) for lag in range(1, 21)
            ],
        }
    )
    rollover = (
        data.groupby("직전회차_1등없음")[["세전_기대수령액", "세후_기대수령액"]]
        .agg(["count", "mean", "median"])
        .reset_index()
    )
    rollover.columns = [
        "직전회차_1등없음",
        "회차수",
        "평균_세전기대수령액",
        "중앙_세전기대수령액",
        "세후_회차수",
        "평균_세후기대수령액",
        "중앙_세후기대수령액",
    ]
    rollover = rollover.drop(columns="세후_회차수")

    backtest_rows = []
    strategies = {
        "매회 1게임": pd.Series(True, index=data.index),
        "직전 회차 1등 없음": data["직전회차_1등없음"] == 1,
    }
    strategies["과거자료로 선택한 mod5"] = expanding_mod5_selector(data)

    for name, selector in strategies.items():
        selected = data.loc[selector]
        backtest_rows.append(
            {
                "전략": name,
                "구매회차수": len(selected),
                "평균_세전기대수령액": selected["세전_기대수령액"].mean(),
                "평균_세후기대수령액": selected["세후_기대수령액"].mean(),
                "평균_세전ROI": selected["세전_ROI"].mean(),
                "평균_세후ROI": selected["세후_ROI"].mean(),
            }
        )

    summary = {
        "분석시작회차": start_round,
        "분석회차수": len(data),
        "mod5_평균차이": observed_range,
        "mod5_Kruskal_p값": kruskal_p,
        "mod5_순열검정_p값": permutation_p,
        "순열반복수": permutations,
        "seed": seed,
    }
    return cycle, autocorrelation, rollover, pd.DataFrame(backtest_rows), summary


def embedded_png_tag(path: str | Path, alt: str) -> str:
    """PNG 파일을 외부 경로가 필요 없는 HTML data URI 태그로 변환한다."""
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f'<img alt="{alt}" src="data:image/png;base64,{encoded}">'


def _save_figure(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _plot_rank_probabilities(table: pd.DataFrame, path: Path) -> None:
    fig, axis = plt.subplots(figsize=(8, 4.6))
    axis.bar(table["순위"], table["확률"], color="#4C78A8")
    axis.set_yscale("log")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0e}"))
    axis.set_ylabel("당첨확률(로그 축)")
    axis.set_title("등수별 당첨확률")
    for index, row in table.iterrows():
        axis.text(index, row["확률"] * 1.25, f"1/{row['약_몇분의1']:,.0f}", ha="center")
    _save_figure(fig, path)


def _plot_frequency(
    table: pd.DataFrame, summary: dict[str, float], title: str, path: Path
) -> None:
    fig, axis = plt.subplots(figsize=(11, 4.5))
    colors = ["#E45756" if p < 0.05 else "#4C78A8" for p in table["Holm_보정_p값"]]
    axis.bar(table["번호"], table["출현횟수"], color=colors)
    axis.axhline(
        summary["번호당_기대횟수"], color="black", linestyle="--", label="이론적 기대값"
    )
    axis.set_xticks(range(1, 46, 2))
    axis.set_xlabel("번호")
    axis.set_ylabel("출현 횟수")
    axis.set_title(title)
    axis.legend()
    _save_figure(fig, path)


def _plot_top_pairs(pair_table: pd.DataFrame, path: Path) -> None:
    top = pair_table.head(15).copy()
    labels = top["번호1"].astype(str) + "-" + top["번호2"].astype(str)
    fig, axis = plt.subplots(figsize=(9, 5))
    axis.barh(labels.iloc[::-1], top["동반출현횟수"].iloc[::-1], color="#72B7B2")
    axis.set_xlabel("동반 출현 횟수")
    axis.set_title("가장 자주 함께 나온 본번호 쌍 15개")
    _save_figure(fig, path)


def _plot_crowd(comparisons: pd.DataFrame, path: Path) -> None:
    fig, axis = plt.subplots(figsize=(9, 5))
    x = np.arange(len(comparisons))
    width = 0.36
    axis.bar(
        x - width / 2,
        comparisons["해당_평균_수동1등_1억게임당"],
        width,
        label="해당",
        color="#F58518",
    )
    axis.bar(
        x + width / 2,
        comparisons["비해당_평균_수동1등_1억게임당"],
        width,
        label="비해당",
        color="#54A24B",
    )
    axis.set_xticks(x, comparisons["조합특성"], rotation=15)
    axis.set_ylabel("1억 판매게임당 수동 1등")
    axis.set_title("조합 특성과 수동 공동당첨 규모")
    axis.legend()
    _save_figure(fig, path)


def _plot_round_ev(round_ev: pd.DataFrame, path: Path) -> None:
    data = round_ev.loc[round_ev["회차"] >= CURRENT_RULE_START_ROUND]
    fig, axis = plt.subplots(figsize=(11, 5))
    axis.plot(
        data["회차"],
        data["세전_기대수령액"],
        alpha=0.35,
        label="회차별 사후 기대수령액",
    )
    axis.plot(
        data["회차"],
        data["세전_기대수령액_50회평균"],
        linewidth=2,
        label="50회 이동평균",
    )
    rollover = data.loc[data["직전회차_1등없음"] == 1]
    axis.scatter(
        rollover["회차"],
        rollover["세전_기대수령액"],
        color="#E45756",
        zorder=3,
        label="직전 회차 1등 없음",
    )
    axis.axhline(500, color="black", linestyle="--", label="공식 장기 환급률 50%")
    axis.set_xlabel("회차")
    axis.set_ylabel("1게임당 세전 기대수령액(원)")
    axis.set_title("회차별 1게임 기대수령액")
    axis.legend()
    _save_figure(fig, path)


def _plot_cycle(cycle: pd.DataFrame, path: Path) -> None:
    fig, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(cycle["회차_mod5"], cycle["평균_세전기대수령액"], color="#B279A2")
    axis.axhline(500, color="black", linestyle="--", label="500원")
    axis.set_xticks(range(1, 6))
    axis.set_xlabel("회차를 5개 그룹으로 나눈 위치")
    axis.set_ylabel("평균 세전 기대수령액(원)")
    axis.set_title("5회 주기 가설 비교")
    axis.legend()
    _save_figure(fig, path)


def _plot_rank_ev(rank_ev: pd.DataFrame, path: Path) -> None:
    fig, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(
        rank_ev["순위"], rank_ev["평균_세전기대수령액"], color="#4C78A8", label="세전"
    )
    axis.bar(
        rank_ev["순위"],
        rank_ev["평균_세후기대수령액"],
        color="#F58518",
        alpha=0.8,
        label="현재 세법 적용",
    )
    axis.set_ylabel("1게임당 기대수령액 기여(원)")
    axis.set_title("등수별 기대수익 기여도")
    axis.legend()
    _save_figure(fig, path)


def _markdown_table(frame: pd.DataFrame, formats: dict[str, str] | None = None) -> str:
    output = frame.copy()
    for column, format_spec in (formats or {}).items():
        output[column] = output[column].map(lambda value: format(value, format_spec))
    return output.to_markdown(index=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def write_report(
    report_path: Path,
    charts: dict[str, Path],
    rank_data: pd.DataFrame,
    probability: pd.DataFrame,
    main_frequency: pd.DataFrame,
    bonus_frequency: pd.DataFrame,
    main_summary: dict[str, float],
    bonus_summary: dict[str, float],
    main_simulation: dict[str, float],
    bonus_simulation: dict[str, float],
    combination_summary: dict[str, Any],
    pair_table: pd.DataFrame,
    triple_table: pd.DataFrame,
    pattern_table: pd.DataFrame,
    crowd_comparisons: pd.DataFrame,
    crowd_coefficients: pd.DataFrame,
    rank_ev: pd.DataFrame,
    round_ev: pd.DataFrame,
    cycle: pd.DataFrame,
    autocorrelation: pd.DataFrame,
    rollover: pd.DataFrame,
    backtest: pd.DataFrame,
    timing_summary: dict[str, float],
) -> None:
    """표와 base64 그림을 포함한 자체 포함형 최종 보고서를 작성한다."""
    current = round_ev.loc[round_ev["회차"] >= CURRENT_RULE_START_ROUND]
    mean_gross = current["세전_기대수령액"].mean()
    mean_net = current["세후_기대수령액"].mean()
    top_main = main_frequency.nlargest(10, "출현횟수")[
        ["번호", "출현횟수", "출현률", "Holm_보정_p값"]
    ]
    top_bonus = bonus_frequency.nlargest(10, "출현횟수")[
        ["번호", "출현횟수", "출현률", "Holm_보정_p값"]
    ]
    numeric_summary = (
        rank_data[
            [
                "회차",
                "등위별 총 당첨금",
                "당첨게임 수",
                "1게임당 당첨금",
                "총판매금액",
            ]
        ]
        .agg(["count", "nunique", "mean", "median", "min", "max"])
        .T.reset_index()
        .rename(
            columns={
                "index": "칼럼명",
                "count": "유효값 수",
                "nunique": "고유값 수",
                "mean": "평균",
                "median": "중앙값",
                "min": "최솟값",
                "max": "최댓값",
            }
        )
    )
    rank_summary = (
        rank_data.groupby("순위", sort=False)
        .agg(
            회차수=("회차", "size"),
            평균_당첨게임=("당첨게임 수", "mean"),
            중앙_당첨게임=("당첨게임 수", "median"),
            평균_1게임당_당첨금=("1게임당 당첨금", "mean"),
            중앙_1게임당_당첨금=("1게임당 당첨금", "median"),
        )
        .reset_index()
    )
    max_autocorrelation = autocorrelation.iloc[
        autocorrelation["자기상관"].abs().argmax()
    ]
    sum_effect = crowd_coefficients.loc[crowd_coefficients["변수"] == "합계"].iloc[0]
    column_description = pd.DataFrame(
        [
            ("회차", "정수", "추첨 회차. 한 회차가 1~5등 행에 반복된다."),
            ("순위", "범주", "1등부터 5등까지의 당첨 등수"),
            (
                "등위별 총 당첨금",
                "정수(원)",
                "해당 등수 모든 당첨게임의 세전 당첨금 합계",
            ),
            (
                "당첨게임 수",
                "정수(게임)",
                "당첨된 게임 수. 사람 수와 같지 않을 수 있다.",
            ),
            ("1게임당 당첨금", "정수(원)", "당첨게임 하나가 받는 세전 금액"),
            ("당첨기준", "범주", "등수 판정에 필요한 번호 일치 조건"),
            ("비고", "문자열", "1등 자동·수동·반자동 게임 수"),
            ("총판매금액", "정수(원)", "회차 전체 판매액. 같은 회차에서 5번 반복된다."),
            (
                "당첨번호1~6",
                "정수",
                "오름차순으로 저장된 본번호 6개. 추첨 순서가 아니다.",
            ),
            ("보너스번호", "정수", "2등 판정에 쓰는 보너스번호"),
        ],
        columns=["칼럼명", "형태", "설명 및 주의사항"],
    )

    sections = [
        "# 로또 6/45 최대 기대수익 분석 보고서",
        "",
        "## 1. 분석 목표",
        "",
        "이 보고서의 질문은 **구매 시점과 번호 선택 방법을 조정하면 1게임당 기대수익을 높일 수 있는가**이다. 당첨번호 예측, 공동당첨 회피, 회차 선택을 구분하고 제1회부터 제1245회까지의 자료를 사용한다.",
        "",
        "- 분석 단위: 번호 6개를 선택한 1게임",
        "- 현행 게임 가격: 1,000원",
        f"- 현행 고정 4·5등 구간: {CURRENT_RULE_START_ROUND}~1245회",
        "- 세후 금액: 2026년 세법을 과거 당첨금에 가정 적용한 비교값",
        "- 회차별 기대수익: 실제 당첨금표를 사용한 사후적 분석값",
        "",
        "## 2. 데이터 구조와 칼럼 설명",
        "",
        f"원본은 1~{int(rank_data['회차'].max()):,}회, {len(rank_data):,}행, {rank_data.shape[1]}개 칼럼이며 결측값은 {int(rank_data.isna().sum().sum()):,}개다. 등수 자료 때문에 한 회차가 5개 행으로 반복되므로 판매액과 번호를 집계할 때는 회차별 한 행만 사용했다.",
        "",
        _markdown_table(column_description),
        "",
        "`등위별 총 당첨금 ≈ 당첨게임 수 × 1게임당 당첨금`이다. 원 단위 절사 때문에 소액의 차이가 날 수 있다.",
        "",
        "### 2.1 주요 숫자형 칼럼의 간단 통계",
        "",
        _markdown_table(
            numeric_summary,
            {
                "유효값 수": ",.0f",
                "고유값 수": ",.0f",
                "평균": ",.1f",
                "중앙값": ",.1f",
                "최솟값": ",.0f",
                "최댓값": ",.0f",
            },
        ),
        "",
        "### 2.2 등수별 간단 통계",
        "",
        _markdown_table(
            rank_summary,
            {
                "평균_당첨게임": ",.2f",
                "중앙_당첨게임": ",.0f",
                "평균_1게임당_당첨금": ",.0f",
                "중앙_1게임당_당첨금": ",.0f",
            },
        ),
        "",
        "## 3. 등수별 당첨 가능성",
        "",
        _markdown_table(
            probability[["순위", "유리한 조합 수", "확률", "약_몇분의1"]],
            {"확률": ".10f", "약_몇분의1": ",.1f"},
        ),
        "",
        embedded_png_tag(charts["probability"], "등수별 당첨확률"),
        "",
        "모든 6개 번호 조합은 동일한 등수별 확률을 가진다. 번호 선택으로 당첨확률을 높일 수 있다는 가설은 이후 빈도 검정에서 별도로 확인한다.",
        "",
        "## 4. 본번호와 보너스번호 출현 빈도",
        "",
        f"본번호 전체 균등성 검정의 p값은 **{main_summary['균등성_p값']:.4f}**이다. 45개 번호를 동시에 비교한 Holm 보정 후 유의한 번호는 {(main_frequency['Holm_보정_p값'] < 0.05).sum()}개다.",
        "",
        _markdown_table(top_main, {"출현률": ".2%", "Holm_보정_p값": ".4f"}),
        "",
        embedded_png_tag(charts["main_frequency"], "본번호별 출현 횟수"),
        "",
        f"공분산을 맞춘 정규근사 모의실험 {int(main_simulation['반복수']):,}회에서 관측된 본번호 최대·최소 차이 이상이 나온 비율은 **{main_simulation['최대최소차_이상_확률']:.2%}**였다.",
        "",
        f"보너스번호 전체 균등성 검정의 p값은 **{bonus_summary['균등성_p값']:.4f}**이며, Holm 보정 후 유의한 번호는 {(bonus_frequency['Holm_보정_p값'] < 0.05).sum()}개다.",
        "",
        _markdown_table(top_bonus, {"출현률": ".2%", "Holm_보정_p값": ".4f"}),
        "",
        embedded_png_tag(charts["bonus_frequency"], "보너스번호별 출현 횟수"),
        "",
        f"같은 방식의 모의실험에서 관측된 보너스번호 최대·최소 차이 이상이 나온 비율은 **{bonus_simulation['최대최소차_이상_확률']:.2%}**였다. 이 모의실험은 이산 추첨을 그대로 반복한 값이 아니라 공분산 구조를 보존한 정규근사다.",
        "",
        "과거 출현 횟수의 차이는 미래 추첨 확률의 차이로 해석하지 않는다. 공정한 추첨 가정에서 다음 회차 각 본번호의 포함 확률은 모두 6/45이다.",
        "",
        "## 5. 어떤 조합이 많이 나타나고 많이 선택되는가",
        "",
        f"1,245개 당첨 조합 중 서로 다른 exact 조합은 {combination_summary['서로다른_정확조합수']:,}개이며, 두 번 이상 반복된 exact 조합은 {combination_summary['반복된_정확조합수']:,}개다.",
        "",
        "### 5.1 동반 출현 조합",
        "",
        _markdown_table(pair_table.head(10)),
        "",
        _markdown_table(triple_table.head(10)),
        "",
        embedded_png_tag(charts["pairs"], "자주 함께 나온 번호 쌍"),
        "",
        "### 5.2 조합 형태와 공동당첨",
        "",
        _markdown_table(
            pattern_table,
            {"비율": ".2%", "평균_1등당첨게임": ".2f", "평균_수동1등": ".2f"},
        ),
        "",
        _markdown_table(
            crowd_comparisons,
            {
                "해당_평균_수동1등_1억게임당": ".2f",
                "비해당_평균_수동1등_1억게임당": ".2f",
                "평균차이": ".2f",
                "Mann_Whitney_p값": ".4f",
            },
        ),
        "",
        embedded_png_tag(charts["crowd"], "조합 특성과 수동 공동당첨 규모"),
        "",
        "판매게임 수를 offset으로 둔 음이항 회귀 결과는 다음과 같다. 발생률비가 1보다 크면 해당 특성이 있을 때 공동당첨게임이 더 많은 방향의 연관성을 뜻한다.",
        "",
        _markdown_table(
            crowd_coefficients,
            {
                "계수": ".4f",
                "발생률비": ".3f",
                "p값": ".4f",
                "95%CI_하한": ".3f",
                "95%CI_상한": ".3f",
            },
        ),
        "",
        f"여러 특성과 판매게임 수를 함께 통제하면 조합 합계가 1표준편차 커질 때 1등 당첨게임 발생률비는 **{sum_effect['발생률비']:.3f}**(p={sum_effect['p값']:.4g})였다. 반면 연속번호, 생일형, 같은 끝자리, 홀짝 수의 효과는 5% 수준에서 유의하지 않았다. 따라서 이 자료에서 비교적 일관된 신호는 높은 합계와 공동당첨 감소의 연관성이며, 특정 번호 조합의 우월성을 뜻하지 않는다.",
        "",
        "이 결과는 당첨확률을 높이는 분석이 아니다. 다른 구매자가 많이 고르는 형태를 피하면 1~3등 당첨 시 상금이 나뉠 위험을 줄일 수 있는지를 탐색한다. 전체 판매 조합 자료가 없으므로 인과관계가 아닌 연관성으로 해석한다.",
        "",
        "## 6. 회차에 따라 기대수익이 변하는가",
        "",
        f"401회 이후 회차별 세전 기대수령액 평균은 **{mean_gross:,.1f}원**, 2026년 세법을 적용한 세후 평균은 **{mean_net:,.1f}원**이다.",
        "",
        embedded_png_tag(charts["round_ev"], "회차별 기대수령액"),
        "",
        "### 6.1 5회 주기 가설",
        "",
        _markdown_table(
            cycle,
            {
                "평균_세전기대수령액": ",.2f",
                "중앙_세전기대수령액": ",.2f",
                "표준편차": ",.2f",
            },
        ),
        "",
        embedded_png_tag(charts["cycle"], "5회 주기 비교"),
        "",
        f"5개 그룹 평균의 최대 차이는 {timing_summary['mod5_평균차이']:,.2f}원이다. 순열검정 p값은 **{timing_summary['mod5_순열검정_p값']:.4f}**, Kruskal-Wallis p값은 **{timing_summary['mod5_Kruskal_p값']:.4f}**이다.",
        "",
        f"1~20회 시차 중 절댓값이 가장 큰 자기상관은 시차 {int(max_autocorrelation['시차'])}의 **{max_autocorrelation['자기상관']:.3f}**이었다. 뚜렷한 반복 주기 신호는 확인되지 않았다.",
        "",
        _markdown_table(autocorrelation, {"자기상관": ".4f"}),
        "",
        "### 6.2 이월 정보와 시점 전략",
        "",
        _markdown_table(
            rollover,
            {
                "평균_세전기대수령액": ",.2f",
                "중앙_세전기대수령액": ",.2f",
                "평균_세후기대수령액": ",.2f",
                "중앙_세후기대수령액": ",.2f",
            },
        ),
        "",
        _markdown_table(
            backtest,
            {
                "평균_세전기대수령액": ",.2f",
                "평균_세후기대수령액": ",.2f",
                "평균_세전ROI": ".2%",
                "평균_세후ROI": ".2%",
            },
        ),
        "",
        "실제 당첨게임 수와 실제 당첨금은 추첨 후에 알려지는 값이다. 전략 백테스트에는 직전 회차 정보와 이전까지의 자료만 사용했다. 이월 표본이 적으면 추정 불확실성이 매우 크다.",
        "",
        "## 7. 어느 등수가 기대수익에 가장 많이 기여하는가",
        "",
        _markdown_table(
            rank_ev,
            {
                "확률": ".10f",
                "평균_당첨금": ",.0f",
                "평균_세전기대수령액": ",.2f",
                "평균_세후기대수령액": ",.2f",
                "세전기대수령액_비중": ".2%",
            },
        ),
        "",
        embedded_png_tag(charts["rank_ev"], "등수별 기대수익 기여도"),
        "",
        "한 게임으로 특정 등수만 노릴 수는 없다. 이 표는 전체 기대수령액을 등수별로 분해한 것이다. 4·5등은 고정 지급이고 1~3등은 판매액·이월·공동당첨게임 수의 영향을 받는다.",
        "",
        "## 8. 복권 1게임의 기대수익과 결론",
        "",
        f"현행 구간의 과거 당첨금표를 기준으로 한 평균 세전 기대수령액은 **{mean_gross:,.1f}원**, 평균 순기대이익은 **{mean_gross - 1_000:,.1f}원**이다. 현재 세법을 가정한 평균 세후 기대수령액은 **{mean_net:,.1f}원**, 순기대이익은 **{mean_net - 1_000:,.1f}원**이다.",
        "",
        "이 값은 각 과거 회차에 균등한 번호 조합의 게임을 하나 더 넣었다고 가정한 반사실적 추정치다. 1~3등은 그 게임도 공동당첨 수에 포함해 배분액을 다시 계산했다. 공식 환급률 50%는 전체 판매게임의 장기 총지급률이고, 여기의 회차별 단순평균·균등조합 가정과 가중 방식이 달라 500원과 정확히 일치하지 않는다. 구매 시점에 알 수 있는 확정 수익률도 아니다.",
        "",
        "분석이 지지할 수 있는 현실적인 방향은 다음과 같다.",
        "",
        "1. 과거에 많이 나온 번호를 선택해도 다음 회차 당첨확률은 높아지지 않는다.",
        "2. 이월은 구매 전에 확인할 수 있는 기대당첨금 증가 요인이지만 표본 수와 세금 효과를 함께 봐야 한다.",
        "3. 생일 숫자나 눈에 띄는 패턴처럼 사람들이 몰릴 수 있는 조합을 피하는 것은 당첨확률이 아니라 공동당첨 위험을 낮추는 접근이다.",
        "4. 기대수익이 가장 높은 전략도 순기대이익이 음수일 수 있다. 최대 기대수익은 수익 보장을 뜻하지 않는다.",
        "",
        "## 9. 데이터와 방법의 한계",
        "",
        "- 전체 구매자가 선택한 모든 조합 자료가 없어 조합 인기 분석은 실제 당첨 조합과 공동당첨 수를 이용한 간접 분석이다.",
        "- 회차별 기대수익은 실제 당첨금표를 사용한 사후 지표이며 구매 전에 정확히 알 수 없다.",
        "- 과거 세법 대신 2026년 세법을 일괄 적용한 세후 비교값이다.",
        "- 초기 회차는 가격과 지급 규칙이 달라 현행 기대수익 비교에서 제외했다.",
        "- 통계적 연관성은 추첨 조작이나 인과관계를 증명하지 않는다.",
        "",
        "## 10. 재현 방법과 공식 기준",
        "",
        "```bash",
        "python class2_rottery/lotto_analysis.py",
        "```",
        "",
        "- [동행복권 로또6/45 소개 및 당첨구조](https://www.dhlottery.co.kr/lt645/intro)",
        "- [국세청 기타소득 원천징수세율](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7893&mi=6467)",
        "- [국가법령정보센터 소득세법 제84조](https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900029004)",
    ]
    report_path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def run_analysis(csv_path: Path, output_dir: Path, report_path: Path) -> dict[str, Any]:
    """7개 분석을 실행하고 모든 표·그림·요약·보고서를 저장한다."""
    configure_plots()
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_dir = output_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    rank_data = load_rank_data(csv_path)
    rounds = add_combination_features(build_round_table(rank_data))
    probability = rank_probability_table()
    main_frequency, main_summary = analyze_number_frequency(rounds)
    bonus_frequency, bonus_summary = analyze_number_frequency(rounds, bonus=True)
    main_simulation = covariance_matched_extreme_simulation(
        len(rounds), 6, main_frequency["출현횟수"].to_numpy()
    )
    bonus_simulation = covariance_matched_extreme_simulation(
        len(rounds), 1, bonus_frequency["출현횟수"].to_numpy()
    )
    (
        exact_combinations,
        pair_table,
        triple_table,
        pattern_table,
        combination_summary,
    ) = analyze_combinations(rounds)
    crowd_comparisons, crowd_coefficients, crowd_summary = analyze_crowd_sharing(rounds)
    round_ev = calculate_round_expected_values(rounds)
    rank_ev = analyze_rank_ev(round_ev)
    cycle, autocorrelation, rollover, backtest, timing_summary = analyze_round_timing(
        round_ev
    )

    tables = {
        "01_rank_probabilities.csv": probability,
        "02_main_number_frequency.csv": main_frequency,
        "03_bonus_number_frequency.csv": bonus_frequency,
        "04_exact_combination_frequency.csv": exact_combinations,
        "04_pair_frequency.csv": pair_table,
        "04_triple_frequency.csv": triple_table,
        "04_combination_patterns.csv": pattern_table,
        "04_crowd_pattern_comparison.csv": crowd_comparisons,
        "04_crowd_negative_binomial.csv": crowd_coefficients,
        "05_round_expected_value.csv": round_ev.drop(columns=["당첨번호"]),
        "05_mod5_cycle.csv": cycle,
        "05_autocorrelation.csv": autocorrelation,
        "05_rollover_comparison.csv": rollover,
        "05_timing_backtest.csv": backtest,
        "06_rank_expected_value.csv": rank_ev,
    }
    for filename, table in tables.items():
        table.to_csv(output_dir / filename, index=False, encoding="utf-8-sig")

    charts = {
        "probability": chart_dir / "01_rank_probabilities.png",
        "main_frequency": chart_dir / "02_main_number_frequency.png",
        "bonus_frequency": chart_dir / "03_bonus_number_frequency.png",
        "pairs": chart_dir / "04_top_pairs.png",
        "crowd": chart_dir / "04_crowd_patterns.png",
        "round_ev": chart_dir / "05_round_expected_value.png",
        "cycle": chart_dir / "05_mod5_cycle.png",
        "rank_ev": chart_dir / "06_rank_expected_value.png",
    }
    _plot_rank_probabilities(probability, charts["probability"])
    _plot_frequency(
        main_frequency, main_summary, "본번호별 출현 횟수", charts["main_frequency"]
    )
    _plot_frequency(
        bonus_frequency,
        bonus_summary,
        "보너스번호별 출현 횟수",
        charts["bonus_frequency"],
    )
    _plot_top_pairs(pair_table, charts["pairs"])
    _plot_crowd(crowd_comparisons, charts["crowd"])
    _plot_round_ev(round_ev, charts["round_ev"])
    _plot_cycle(cycle, charts["cycle"])
    _plot_rank_ev(rank_ev, charts["rank_ev"])

    current = round_ev.loc[round_ev["회차"] >= CURRENT_RULE_START_ROUND]
    summary = {
        "metadata": {
            "input": str(csv_path),
            "rounds": len(rounds),
            "current_rule_start": CURRENT_RULE_START_ROUND,
            "mode_data_start": MODE_DATA_START_ROUND,
            "simulation_seed": SIMULATION_SEED,
        },
        "01_probabilities": probability.to_dict("records"),
        "02_main_frequency": {
            **main_summary,
            "simulation": main_simulation,
        },
        "03_bonus_frequency": {
            **bonus_summary,
            "simulation": bonus_simulation,
        },
        "04_combinations": {
            **combination_summary,
            "crowd": crowd_summary,
        },
        "05_timing": timing_summary,
        "06_rank_ev": rank_ev.to_dict("records"),
        "07_one_game_ev": {
            "mean_gross_receipt": current["세전_기대수령액"].mean(),
            "mean_net_receipt_current_tax": current["세후_기대수령액"].mean(),
            "mean_gross_profit": current["세전_순기대이익"].mean(),
            "mean_net_profit_current_tax": current["세후_순기대이익"].mean(),
        },
        "outputs": sorted(
            [*tables, *(str(path.relative_to(output_dir)) for path in charts.values())]
        ),
    }
    (output_dir / "experiment_summary.json").write_text(
        json.dumps(_jsonable(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(
        report_path,
        charts,
        rank_data,
        probability,
        main_frequency,
        bonus_frequency,
        main_summary,
        bonus_summary,
        main_simulation,
        bonus_simulation,
        combination_summary,
        pair_table,
        triple_table,
        pattern_table,
        crowd_comparisons,
        crowd_coefficients,
        rank_ev,
        round_ev,
        cycle,
        autocorrelation,
        rollover,
        backtest,
        timing_summary,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="로또 기대수익 분석 보고서 생성")
    base = Path(__file__).resolve().parent
    parser.add_argument("--csv", type=Path, default=base / "lotto_rank_1_1245.csv")
    parser.add_argument("--output", type=Path, default=base / "analysis_results")
    parser.add_argument("--report", type=Path, default=base / "lotto_rank_report.md")
    args = parser.parse_args()
    summary = run_analysis(args.csv, args.output, args.report)
    print(
        "분석 완료:",
        f"{summary['metadata']['rounds']}개 회차,",
        f"결과={args.output},",
        f"보고서={args.report}",
    )


if __name__ == "__main__":
    main()
