"""사용자 합의의 공통 판정과 직전 회차·비교 집단·완전한 산출물을 검증한다."""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lotto_analysis_v2_3 as analysis  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "lotto_preprocessed_v2.csv"


def test_rule_is_upper_only_and_boundaries_are_strict():
    rule = analysis.upper_rule(pd.Series([-100, 1, 2, 3, 4, 5, 6, 100]))
    assert rule["Q1"] == 1.75 and rule["Q3"] == 5.25
    assert rule["상한"] == 10.5 and rule["강한상한"] == 15.75
    np.testing.assert_array_equal(
        analysis.classify_y(pd.Series([-100, 10.5, 10.6, 15.75, 16]), 10.5, 15.75),
        [0, 0, 1, 1, 2],
    )


def test_section_rule_uses_own_y_and_ignores_x():
    frame = pd.DataFrame(
        {
            "주제": ["A"] * 8 + ["B"] * 8,
            "순위": ["1등"] * 16,
            "y": [0, 1, 2, 3, 4, 5, 6, 100] + list(range(0, 80, 10)),
            "x": [1e20] * 16,
        }
    )
    labeled, rules = analysis.label_sections(frame)
    assert labeled.query("주제 == 'A'")["상방"].sum() == 1
    assert labeled.query("주제 == 'B'")["상방"].sum() == 0
    assert (
        rules.set_index("주제").loc["A", "상한"]
        != rules.set_index("주제").loc["B", "상한"]
    )
    assert rules.set_index("주제").loc["B", "상한"] == 105
    changed = frame.assign(x=-1e25)
    np.testing.assert_array_equal(
        labeled["강도"], analysis.label_sections(changed)[0]["강도"]
    )


def test_real_analysis_preserves_main_y_and_all_section_rules():
    result = analysis.analyze(SOURCE)
    assert len(result["fences"]) == 27
    assert set(result["labeled"]["순위"]) == {"1등", "2등", "3등"}
    raw = pd.read_csv(SOURCE, encoding="utf-8-sig").set_index(["순위", "회차"])
    prize = result["labeled"].query("주제 == '01'").set_index(["순위", "회차"])
    np.testing.assert_array_equal(prize["y"], raw.loc[prize.index, "1게임당 당첨금"])
    assert (
        result["labeled"]
        .loc[result["labeled"]["상방"], "y"]
        .gt(result["labeled"].loc[result["labeled"]["상방"], "상한"])
        .all()
    )
    assert result["labeled"]["상방"].sum() == len(result["outliers"])


def test_previous_features_are_exactly_adjacent_and_missing_prize_stays_missing():
    result = analysis.analyze(SOURCE)
    features = result["features"].set_index(["순위", "회차"])
    raw = pd.read_csv(SOURCE, encoding="utf-8-sig")
    row = raw.query("순위 == '2등' and 회차 == 1056").iloc[0]
    assert features.loc[("2등", 1057), "previous_winners"] == row["당첨게임 수"]
    assert (
        features.loc[("2등", 1057), "previous_prize"] == row["1000원기준_1게임당당첨금"]
    )
    assert pd.isna(features.loc[("1등", 2), "previous_prize"])
    assert features.loc[("1등", 2), "previous_winners"] == 0
    assert pd.isna(features.loc[("1등", 1), "previous_winners"])


def test_controls_are_within_section_rank_period_and_target_is_not_rediscovered():
    result = analysis.analyze(SOURCE)
    comparison = result["comparisons"]
    assert not len(comparison.query("주제 == '01' and 특징 == 'current_prize'"))
    assert not len(comparison.query("주제 == '02' and 특징 == 'current_winners'"))
    row = comparison.query(
        "주제 == '01' and 순위 == '1등' and 특징 == 'previous_prize' and 기간 == '401회 이후'"
    ).iloc[0]
    group = result["labeled"].query(
        "주제 == '01' and 순위 == '1등' and 기간 == '401회 이후'"
    )
    assert row["상방집단수"] == int(group["상방"].sum())
    assert row["비상방집단수"] == int((~group["상방"]).sum())
    assert row["상방유효수"] + row["상방결측수"] == row["상방집단수"]


def test_overlap_is_a_round_intersection_with_eligible_denominators():
    result = analysis.analyze(SOURCE)
    pair = (
        result["overlaps"]
        .query(
            "기준주제 == '01' and 기준등수 == '1등' and 비교주제 == '02' and 비교등수 == '1등'"
        )
        .iloc[0]
    )
    left = result["labeled"].query("주제 == '01' and 순위 == '1등'")
    right = result["labeled"].query("주제 == '02' and 순위 == '1등'")
    assert pair["공통관측수"] == len(set(left["회차"]) & set(right["회차"]))
    assert pair["동시상방수"] == len(
        set(left.loc[left["상방"], "회차"]) & set(right.loc[right["상방"], "회차"])
    )


def test_report_is_self_contained_and_has_followups_in_all_sections(tmp_path):
    result = analysis.run(SOURCE, tmp_path)
    report = (tmp_path / "lotto_rank_report_v2_3.md").read_text(encoding="utf-8")
    prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[image]", report)
    assert not re.search(r"상관계수|spearman|pearson|correlation", prose, re.IGNORECASE)
    assert report.count("data:image/png;base64,") == 46
    for code in ("01", "02", "03", "08", "09A", "09B", "09C", "09D", "10"):
        assert f"### {code}-후속. 공통 이상치 분석" in report
        assert (tmp_path / "tables" / f"{code}_outliers.csv").exists()
    assert "전체 이상치 회차 통합표" in report
    assert "비교값 기준 극단 회차" not in report
    assert "표시기준3초과" not in report
    assert result["validation"]["status"] == "passed"
