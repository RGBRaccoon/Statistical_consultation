"""V3.1의 회차 수 기준 그룹 선정, 경계 동률 처리, 당첨번호 특징, 겹침을 검증한다."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lotto_analysis_v3_1 as analysis  # noqa: E402

BASE = Path(__file__).resolve().parents[1]


def test_group_sizes_follow_round_counts():
    assert [analysis.group_size(1245, g.share) for g in analysis.GROUPS] == [
        62,
        124,
        311,
        1245,
        311,
        124,
        62,
    ]


def test_top_selection_splits_definite_and_ties():
    series = pd.Series([10, 9, 9, 9, 8, 7], index=[1, 2, 3, 4, 5, 6])
    selection = analysis.select(series, side="top", size=3)
    assert selection.definite == [1]
    assert selection.ties == [2, 3, 4]
    assert selection.slots == 2
    assert selection.boundary == 9


def test_bottom_selection_without_ties_has_no_slots():
    series = pd.Series([1, 2, 3, 4], index=[11, 12, 13, 14])
    selection = analysis.select(series, side="bottom", size=2)
    assert selection.definite == [11, 12]
    assert selection.ties == []
    assert selection.slots == 0


def test_weights_give_ties_slot_share_and_sum_to_size():
    series = pd.Series([10, 9, 9, 9, 8, 7], index=[1, 2, 3, 4, 5, 6])
    weights = analysis.select(series, side="top", size=3).weights()
    assert weights.to_dict() == pytest.approx({1: 1.0, 2: 2 / 3, 3: 2 / 3, 4: 2 / 3})
    assert weights.sum() == pytest.approx(3)


def test_representative_median_fills_slots_with_tie_median():
    series = pd.Series([10, 9, 9, 9, 8], index=[1, 2, 3, 4, 5])
    selection = analysis.select(series, side="top", size=3)
    values = pd.Series([100.0, 1.0, 2.0, 30.0, 0.0], index=[1, 2, 3, 4, 5])
    # 확정 100, 동률 중앙값 2가 남은 2자리를 채운다: [100, 2, 2]
    assert analysis.representative_median(values, selection) == 2.0


def test_seeded_selection_is_reproducible_and_sized():
    series = pd.Series([10, 9, 9, 9, 9, 8], index=[1, 2, 3, 4, 5, 6])
    selection = analysis.select(series, side="top", size=3)
    first = selection.sample(seed=7)
    assert first == selection.sample(seed=7)
    assert len(first) == 3 and 1 in first


def test_weighted_quantile_matches_unweighted_for_equal_weights():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert analysis.weighted_quantile(values, np.ones(5), 0.5) == 3.0


def test_number_features():
    draws = {1: [1, 2, 3, 11, 32, 45], 2: [3, 13, 23, 33, 40, 41]}
    frame = pd.DataFrame(
        [
            {"회차": r, **{f"당첨번호{i}": n for i, n in enumerate(nums, 1)}}
            for r, nums in draws.items()
        ]
    )
    features = analysis.number_features(frame).set_index("회차")
    assert features.loc[1, "생일범위_번호수"] == 4
    assert features.loc[1, "연속번호_쌍수"] == 2
    assert features.loc[1, "같은끝자리_번호수"] == 4  # 1·11, 2·32
    assert features.loc[1, "번호합"] == 94
    assert features.loc[1, "홀수_개수"] == 4
    assert np.isnan(features.loc[1, "직전회차_겹침수"])
    assert features.loc[2, "같은끝자리_번호수"] == 4  # 3·13·23·33
    assert features.loc[2, "직전회차_겹침수"] == 1  # 3


def test_overlap_counts_definite_intersections():
    sets = {"1등": {1, 2, 3}, "2등": {2, 3, 4}, "3등": {3, 5}}
    overlap = analysis.intersections(sets)
    assert overlap["1등∩2등"] == {2, 3}
    assert overlap["1등∩2등∩3등"] == {3}


def test_overlap_table_counts_boundary_ties_as_members():
    # 질문 1은 경계값과 같은 회차를 모두 그룹에 넣고 센다(확정·후보 구분 없음).
    by_rank = {
        "1등": analysis.Selection([1], [2, 3], 1, 9.0),
        "2등": analysis.Selection([2], [], 0, 8.0),
        "3등": analysis.Selection([3], [2], 0, 7.0),
    }
    selections = {
        (rank, g.name): selection
        for rank, selection in by_rank.items()
        for g in analysis.GROUPS
        if g.side != "all"
    }
    table = analysis.overlap_table(selections)
    assert list(table.columns) == ["그룹", "조합", "겹침수", "겹침회차"]
    row = table[(table["그룹"] == "상위 5%") & (table["조합"] == "1등∩2등∩3등")]
    assert row["겹침수"].item() == 1
    assert row["겹침회차"].item() == "2"


@pytest.fixture(scope="module")
def result():
    return analysis.analyze(BASE / "lotto_preprocessed_v3_1.csv")


def test_real_data_groups_have_exact_weighted_size(result):
    sizes = result["memberships"].groupby(["순위", "그룹"])["가중치"].sum()
    for (rank, group), total in sizes.items():
        share = next(g.share for g in analysis.GROUPS if g.name == group)
        assert total == pytest.approx(analysis.group_size(1245, share)), (rank, group)


def test_real_data_rank1_bottom5_contains_all_zero_rounds(result):
    members = result["memberships"]
    chosen = members[(members["순위"] == "1등") & (members["그룹"] == "하위 5%")]
    rank1 = result["frames"]["1등"]
    zeros = set(rank1.index[rank1["당첨게임 수"] == 0])
    assert zeros <= set(chosen.loc[chosen["구분"] == "확정", "회차"])
