"""V3.1: 당첨게임수가 많은·적은 회차 그룹과 전체를 1·2·3등별로 비교한다.

그룹은 값의 백분위가 아니라 회차 수 기준(상·하위 5·10·25%와 전체)으로 뽑는다.
경계값에 동률이 있으면 대표 중앙값은 동률 회차들의 중앙값으로 남은 자리를 채우고,
분포는 동률 회차 모두에 `남은 자리 ÷ 동률 수` 가중치를 준다.
확인용으로 시드 고정 무작위 선택을 함께 기록한다.
"""

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RANKS = ["1등", "2등", "3등"]
BASIS = "당첨게임 수"
SEEDS = list(range(100))


@dataclass(frozen=True)
class Group:
    name: str
    side: str  # "bottom" | "all" | "top"
    share: float


GROUPS = [
    Group("하위 5%", "bottom", 0.05),
    Group("하위 10%", "bottom", 0.10),
    Group("하위 25%", "bottom", 0.25),
    Group("전체", "all", 1.0),
    Group("상위 25%", "top", 0.25),
    Group("상위 10%", "top", 0.10),
    Group("상위 5%", "top", 0.05),
]

# (칼럼, 보류 여부). 1000원기준 칼럼은 정규화 정의가 확정될 때까지 보류로 표시한다.
CSV_COLUMNS = [
    ("등위별 총 당첨금", False),
    ("1게임당 당첨금", False),
    ("총판매금액", False),
    ("판매게임수", False),
    ("1000원기준_1게임당당첨금", True),
    ("1000원기준_등위별총당첨금", True),
    ("당첨게임률", False),
    ("1억게임당_당첨게임수", False),
    ("등위별_지급률", False),
    ("이론_기대당첨게임수", False),
    ("관측_기대_비율", False),
]
FEATURE_COLUMNS = [
    "생일범위_번호수",
    "연속번호_쌍수",
    "같은끝자리_번호수",
    "번호합",
    "홀수_개수",
    "직전회차_겹침수",
]
COMPARE_COLUMNS = [BASIS] + [c for c, _ in CSV_COLUMNS] + FEATURE_COLUMNS
PENDING_COLUMNS = {c for c, pending in CSV_COLUMNS if pending}
NUMBER_COLUMNS = [f"당첨번호{i}" for i in range(1, 7)]


def group_size(total: int, share: float) -> int:
    return int(round(total * share))


@dataclass
class Selection:
    definite: list[int]
    ties: list[int]
    slots: int
    boundary: float | None

    def weights(self) -> pd.Series:
        """확정 회차는 1, 경계 동률 회차는 남은 자리 ÷ 동률 수."""
        weights = {r: 1.0 for r in self.definite}
        if self.ties:
            share = self.slots / len(self.ties)
            weights.update({r: share for r in self.ties})
        return pd.Series(weights, dtype=float)

    def sample(self, seed: int) -> list[int]:
        """확인용: 동률 회차 중 남은 자리만큼을 시드 고정 무작위로 고른다."""
        rng = np.random.default_rng(seed)
        picked = (
            rng.choice(self.ties, size=self.slots, replace=False) if self.slots else []
        )
        return sorted(self.definite + [int(r) for r in picked])


def select(series: pd.Series, side: str, size: int) -> Selection:
    """회차 수 기준으로 위(top)나 아래(bottom)에서 size개 자리를 정한다."""
    if side == "all":
        return Selection(sorted(series.index.tolist()), [], 0, None)
    ordered = series.sort_values(ascending=(side == "bottom"), kind="mergesort")
    boundary = ordered.iloc[size - 1]
    beyond = series > boundary if side == "top" else series < boundary
    definite = sorted(series.index[beyond].tolist())
    ties = sorted(series.index[series == boundary].tolist())
    slots = size - len(definite)
    if slots == len(ties):
        definite, ties, slots = sorted(definite + ties), [], 0
    return Selection(definite, ties, slots, float(boundary))


def representative_median(values: pd.Series, selection: Selection) -> float:
    """확정 회차 값과, 남은 자리를 채운 동률 회차 값의 중앙값으로 중앙값을 구한다."""
    pool = values.reindex(selection.definite).dropna().tolist()
    if selection.slots:
        tie_values = values.reindex(selection.ties).dropna()
        if len(tie_values):
            pool += [float(tie_values.median())] * selection.slots
    return float(np.median(pool)) if pool else float("nan")


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """가중치의 중심점 사이를 선형 보간하는 가중 분위수."""
    order = np.argsort(values)
    values, weights = np.asarray(values)[order], np.asarray(weights)[order]
    centers = np.cumsum(weights) - weights / 2
    return float(np.interp(q * weights.sum(), centers, values))


def number_features(frame: pd.DataFrame) -> pd.DataFrame:
    """보너스를 뺀 당첨번호 6개에서 회차별 특징을 계산한다."""
    rounds = frame.drop_duplicates("회차").sort_values("회차")
    draws = rounds[NUMBER_COLUMNS].to_numpy()
    rows = []
    previous: set[int] | None = None
    for round_no, numbers in zip(rounds["회차"], draws):
        numbers = sorted(int(n) for n in numbers)
        digits = pd.Series([n % 10 for n in numbers]).value_counts()
        rows.append(
            {
                "회차": int(round_no),
                "생일범위_번호수": sum(n <= 31 for n in numbers),
                "연속번호_쌍수": sum(b - a == 1 for a, b in zip(numbers, numbers[1:])),
                "같은끝자리_번호수": int(digits[digits >= 2].sum()),
                "번호합": sum(numbers),
                "홀수_개수": sum(n % 2 for n in numbers),
                "직전회차_겹침수": (
                    np.nan if previous is None else len(previous & set(numbers))
                ),
            }
        )
        previous = set(numbers)
    return pd.DataFrame(rows)


def intersections(sets: dict[str, set[int]]) -> dict[str, set[int]]:
    """등수 조합(2개·3개)별 공통 회차."""
    result = {}
    for size in (2, 3):
        for combo in combinations(sets, size):
            result["∩".join(combo)] = set.intersection(*(sets[k] for k in combo))
    return result


def build_frames(source: pd.DataFrame) -> dict[str, pd.DataFrame]:
    features = number_features(source).set_index("회차")
    frames = {}
    for rank in RANKS:
        rows = source[source["순위"] == rank].set_index("회차").sort_index()
        frames[rank] = rows[[BASIS] + [c for c, _ in CSV_COLUMNS]].join(features)
    return frames


def sign_pattern(medians: list[float]) -> tuple[int, ...]:
    return tuple(int(s) for s in np.sign(np.diff(medians)))


def analyze(path: Path) -> dict[str, Any]:
    source = pd.read_csv(path)
    frames = build_frames(source)
    selections: dict[tuple[str, str], Selection] = {}
    membership_rows, tie_rows, summary_rows = [], [], []

    for rank, frame in frames.items():
        basis = frame[BASIS]
        for group in GROUPS:
            size = group_size(len(frame), group.share)
            selection = select(basis, group.side, size)
            selections[(rank, group.name)] = selection
            for round_no, weight in selection.weights().items():
                membership_rows.append(
                    {
                        "순위": rank,
                        "그룹": group.name,
                        "회차": int(round_no),
                        "구분": "동률후보" if round_no in selection.ties else "확정",
                        "가중치": weight,
                    }
                )
            tie_rows.append(
                {
                    "순위": rank,
                    "그룹": group.name,
                    "자리수": size,
                    "경계값": selection.boundary,
                    "확정수": len(selection.definite),
                    "동률수": len(selection.ties),
                    "남은자리": selection.slots,
                }
            )
            weights = selection.weights()
            for column in COMPARE_COLUMNS:
                values = frame[column].reindex(weights.index)
                valid = values.notna()
                v, w = values[valid].to_numpy(float), weights[valid].to_numpy(float)
                summary_rows.append(
                    {
                        "순위": rank,
                        "칼럼": column,
                        "그룹": group.name,
                        "대표중앙값": representative_median(frame[column], selection),
                        "가중Q1": weighted_quantile(v, w, 0.25),
                        "가중Q3": weighted_quantile(v, w, 0.75),
                        "최소": float(v.min()),
                        "최대": float(v.max()),
                        "유효가중합": float(w.sum()),
                        "결측가중합": float(weights[~valid].sum()),
                    }
                )

    summary = pd.DataFrame(summary_rows)
    return {
        "frames": frames,
        "selections": selections,
        "memberships": pd.DataFrame(membership_rows),
        "ties": pd.DataFrame(tie_rows),
        "summary": summary,
        "overlaps": overlap_table(selections),
        "seed_check": seed_check(frames["1등"], selections, summary),
    }


def overlap_table(selections: dict[tuple[str, str], Selection]) -> pd.DataFrame:
    """질문 1: 경계값과 같은 회차를 모두 그룹에 넣고 등수 조합별 공통 회차를 센다."""
    rows = []
    for group in GROUPS:
        if group.side == "all":
            continue
        members = {
            r: set(selections[(r, group.name)].definite)
            | set(selections[(r, group.name)].ties)
            for r in RANKS
        }
        for combo, common in intersections(members).items():
            rows.append(
                {
                    "그룹": group.name,
                    "조합": combo,
                    "겹침수": len(common),
                    "겹침회차": " ".join(map(str, sorted(common))),
                }
            )
    return pd.DataFrame(rows)


def seed_check(
    frame: pd.DataFrame,
    selections: dict[tuple[str, str], Selection],
    summary: pd.DataFrame,
) -> pd.DataFrame:
    """1등: 시드별 무작위 선택의 7그룹 중앙값 증감 패턴이 대표 중앙값과 같은지 센다."""
    rows = []
    for column in COMPARE_COLUMNS:
        reference = (
            summary[(summary["순위"] == "1등") & (summary["칼럼"] == column)]
            .set_index("그룹")
            .loc[[g.name for g in GROUPS], "대표중앙값"]
            .tolist()
        )
        target = sign_pattern(reference)
        matches, deviations = 0, []
        for seed in SEEDS:
            medians = [
                float(
                    frame.loc[selections[("1등", g.name)].sample(seed), column].median()
                )
                for g in GROUPS
            ]
            matches += sign_pattern(medians) == target
            deviations.append(max(abs(m - r) for m, r in zip(medians, reference)))
        rows.append(
            {
                "칼럼": column,
                "시드": f"{SEEDS[0]}~{SEEDS[-1]}",
                "패턴일치시드수": matches,
                "시드수": len(SEEDS),
                "대표패턴": " ".join(
                    "+" if s > 0 else "-" if s < 0 else "0" for s in target
                ),
                "최대중앙값차": max(deviations),
            }
        )
    return pd.DataFrame(rows)
