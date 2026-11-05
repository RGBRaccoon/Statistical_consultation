"""V2.4의 전체 회차·가격 환산·정상 대 이상 비교를 검증한다."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import lotto_analysis_v2_4 as analysis  # noqa: E402

BASE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def result():
    return analysis.analyze(BASE / "data" / "lotto_preprocessed_v2.csv")


def select(frame, **criteria):
    for key, value in criteria.items():
        frame = frame.loc[frame[key].eq(value)]
    return frame


def test_upper_rule_is_common_and_ignores_low_values():
    rule = analysis.upper_rule(pd.Series([-100, 1, 2, 3, 4, 5, 6, 100]))
    assert rule["Q1"] == 1.75 and rule["Q3"] == 5.25
    np.testing.assert_array_equal(
        analysis.classify_y(pd.Series([-100, 10.5, 10.6, 15.75, 16]), 10.5, 15.75),
        [0, 0, 1, 1, 2],
    )


def test_section_rule_depends_only_on_own_y():
    obs = pd.DataFrame(
        {
            "주제": ["A"] * 8 + ["B"] * 8,
            "순위": ["1등"] * 16,
            "회차": list(range(1, 9)) * 2,
            "x": [1e20] * 16,
            "y": [0, 1, 2, 3, 4, 5, 6, 100] + list(range(0, 80, 10)),
        }
    )
    left, fences = analysis.label_sections(obs)
    right, _ = analysis.label_sections(obs.assign(x=-1e25))
    np.testing.assert_array_equal(left["강도"], right["강도"])
    assert select(left, 주제="A")["상방"].sum() == 1
    assert select(left, 주제="B")["상방"].sum() == 0
    assert len(fences) == 2


def test_all_money_uses_common_game_price_before_selection(result):
    raw = pd.read_csv(BASE / "data" / "lotto_rank_1_1245.csv", encoding="utf-8-sig")
    raw = raw[raw["순위"].isin(["1등", "2등", "3등"])].set_index(["순위", "회차"])
    features = result["features"].set_index(["순위", "회차"])
    for draw in (2, 87, 88, 400, 401, 1245):
        for rank in ("1등", "2등", "3등"):
            actual, source = features.loc[(rank, draw)], raw.loc[(rank, draw)]
            ratio = 0.5 if draw <= 87 else 1
            assert actual["current_sales"] == source["총판매금액"] * ratio
            assert actual["current_total"] == source["등위별 총 당첨금"] * ratio
            assert actual["current_winners"] == source["당첨게임 수"]
            if source["당첨게임 수"]:
                assert actual["current_prize"] == source["1게임당 당첨금"] * ratio
    prize = select(result["labeled"], 주제="01").set_index(["순위", "회차"])
    np.testing.assert_allclose(prize["y"], features.loc[prize.index, "current_prize"])


def test_transition_delta_normalizes_each_draw_before_subtracting(result):
    raw = pd.read_csv(BASE / "data" / "lotto_rank_1_1245.csv", encoding="utf-8-sig")
    raw = raw.set_index(["순위", "회차"])
    observed = select(result["labeled"], 주제="09A", 순위="1등", 회차=88).iloc[0]
    expected = (
        raw.loc[("1등", 88), "1게임당 당첨금"]
        - raw.loc[("1등", 87), "1게임당 당첨금"] / 2
    )
    assert observed["y"] == expected


def test_no_period_split_and_fences_recomputed_from_section_y(result):
    assert len(result["fences"]) == 27
    assert "기간" not in result["comparisons"]
    for (code, rank), group in result["labeled"].groupby(["주제", "순위"]):
        fence = select(result["fences"], 주제=code, 순위=rank).iloc[0]
        q1, q3 = group["y"].quantile([0.25, 0.75])
        assert fence["관측수"] == len(group)
        assert fence["상한"] == q3 + 1.5 * (q3 - q1)
        np.testing.assert_array_equal(group["상방"], group["y"].gt(fence["상한"]))
    assert result["labeled"]["상방"].sum() == len(result["outliers"])


def test_previous_is_exactly_adjacent_and_zero_prize_is_missing(result):
    feature = result["features"].set_index(["순위", "회차"])
    assert pd.isna(feature.loc[("1등", 1), "previous_winners"])
    assert pd.isna(feature.loc[("1등", 2), "previous_prize"])
    assert feature.loc[("1등", 2), "previous_winners"] == 0
    assert feature.loc[("1등", 1), "current_total"] == 0
    assert (
        feature.loc[("2등", 1057), "previous_winners"]
        == feature.loc[("2등", 1056), "current_winners"]
    )


def test_both_normal_and_outlier_populations_are_compared(result):
    for row in result["comparisons"].to_dict("records"):
        cases = select(result["cases"], 주제=row["주제"], 순위=row["순위"])
        for name, flag in (("정상", False), ("이상", True)):
            group = cases.loc[cases["상방"].eq(flag), row["특징"]]
            values = group.dropna()
            assert row[f"{name}집단수"] == len(group)
            assert row[f"{name}유효수"] == len(values)
            assert row[f"{name}결측수"] == group.isna().sum()
            if len(values):
                assert row[f"{name}중앙값"] == values.median()
    assert set(result["comparisons"]["특징"]) == {
        prefix + suffix
        for prefix in ("current_", "previous_")
        for suffix in ("sales", "prize", "winners", "total")
    }


def test_overlap_rates_use_common_eligible_draws_for_both_populations(result):
    assert len(result["overlaps"]) == 729
    for row in result["overlaps"].to_dict("records"):
        left = select(result["labeled"], 주제=row["기준주제"], 순위=row["기준등수"])
        right = select(result["labeled"], 주제=row["비교주제"], 순위=row["비교등수"])
        joined = left[["회차", "상방"]].merge(
            right[["회차", "상방"]], on="회차", suffixes=("_left", "_right")
        )
        for name, flag in (("정상", False), ("이상", True)):
            group = joined.loc[joined["상방_left"].eq(flag)]
            assert row[f"{name}분모"] == len(group)
            assert row[f"{name}동시수"] == group["상방_right"].sum()
            if len(group):
                assert row[f"{name}발생비율"] == group["상방_right"].mean()
