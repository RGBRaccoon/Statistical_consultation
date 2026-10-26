"""승인된 일반 분석 9개와 등수별 3개+비교1개 구성을 검증한다."""

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lotto_analysis_v2_1_basic as basic  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "lotto_preprocessed_v2.csv"


def test_only_approved_questions_are_executed():
    result = basic.analyze(SOURCE)
    assert [item["id"] for item in result["topics"]] == [
        "01",
        "02",
        "03",
        "08",
        "09A",
        "09B",
        "09C",
        "09D",
        "10",
    ]
    assert set(result["data"]["순위"]) == {"1등", "2등", "3등"}


def test_money_and_winner_analysis_use_original_measurements():
    result = basic.analyze(SOURCE)
    source = pd.read_csv(SOURCE, encoding="utf-8-sig")
    for rank in basic.RANKS:
        original = source[source["순위"].eq(rank)].sort_values("회차")
        winning = original[original["당첨게임 수"].gt(0)]
        prize = result["observations"].query("주제 == '01' and 순위 == @rank")
        np.testing.assert_array_equal(prize["y"], winning["1게임당 당첨금"])
        counts = result["observations"].query("주제 == '02' and 순위 == @rank")
        np.testing.assert_array_equal(counts["y"], original["당첨게임 수"])
        payout = result["observations"].query("주제 == '03' and 순위 == @rank")
        np.testing.assert_array_equal(payout["y"], original["등위별 총 당첨금"])


def test_no_winner_prize_is_excluded_without_dropping_count_or_total_payout():
    result = basic.analyze(SOURCE)
    observations = result["observations"]
    assert len(observations.query("주제 == '01' and 순위 == '1등'")) == 1_231
    assert len(observations.query("주제 == '01' and 순위 == '2등'")) == 1_243
    assert len(observations.query("주제 == '02' and 순위 == '1등'")) == 1_245
    assert len(observations.query("주제 == '03' and 순위 == '1등'")) == 1_245
    omitted = result["excluded"].query("주제 == '01' and 순위 == '1등'")
    assert len(omitted) == 14
    assert omitted["제외이유"].str.contains("당첨게임 없음").all()


def test_four_previous_round_comparisons_use_adjacent_rounds():
    result = basic.analyze(SOURCE)
    source = pd.read_csv(SOURCE, encoding="utf-8-sig")
    rank_data = source[source["순위"].eq("2등")].set_index("회차")
    observations = result["observations"]
    for code in ("09A", "09B", "09C", "09D"):
        row = observations.query(
            "주제 == @code and 순위 == '2등' and 회차 == 1057"
        ).iloc[0]
        assert row["직전회차"] == 1056
        if code == "09A":
            assert (
                row["y"]
                == rank_data.loc[1057, "1게임당 당첨금"]
                - rank_data.loc[1056, "1게임당 당첨금"]
            )
        elif code == "09B":
            assert (
                row["y"]
                == rank_data.loc[1057, "당첨게임 수"]
                - rank_data.loc[1056, "당첨게임 수"]
            )
        elif code == "09C":
            assert row["x"] == rank_data.loc[1056, "1게임당 당첨금"]
            assert row["y"] == 664
        else:
            assert row["x"] == rank_data.loc[1056, "당첨게임 수"]
            assert row["y"] == rank_data.loc[1057, "1게임당 당첨금"]
    # 무당첨 직전 회차를 건너뛰어 두 회차 전과 비교하면 안 된다.
    assert not len(observations.query("주제 == '09A' and 순위 == '1등' and 회차 == 2"))
    assert len(observations.query("주제 == '09B' and 순위 == '1등' and 회차 == 2")) == 1


def test_violin_scores_can_be_reconstructed_but_main_y_is_not_z_score():
    result = basic.analyze(SOURCE)
    values = result["observations"].merge(
        result["normalization"], on=["주제", "순위"], validate="many_to_one"
    )
    np.testing.assert_allclose(
        values["비교Z"],
        (values["비교값"] - values["중심"]) / values["표준편차"],
        atol=1e-10,
    )
    assert values.query("주제 == '01'")["y"].median() > 1_000_000
    assert values.query("주제 == '02'")["y"].max() >= 664


def test_scatter_replacement_has_explicit_reason():
    topics = {item["id"]: item for item in basic.TOPICS}
    assert topics["10"]["plot"] == "box"
    assert "구간" in topics["10"]["plot_reason"]
    assert all(item["plot_reason"] for item in basic.TOPICS)


def test_relationship_violin_conditioning_uses_predictor_not_outcome():
    result = basic.analyze(SOURCE)
    for code in ("08", "09C", "09D"):
        for rank in basic.RANKS:
            group = result["observations"].query("주제 == @code and 순위 == @rank")
            lower = group[group["비교구간"].eq("X 하위")]
            upper = group[group["비교구간"].eq("X 상위")]
            assert lower["구간X"].max() <= upper["구간X"].min()
            assert len(lower) > 100 and len(upper) > 100


def test_run_outputs_nine_sets_of_four_self_contained_figures():
    output = BASE / ".test_output_v2_1_basic"
    assert not output.exists()
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    output.mkdir()
    try:
        basic.run(SOURCE, output)
        report = (output / "lotto_rank_report_v2_1.md").read_text(encoding="utf-8")
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert report.count("data:image/png;base64,") == 36
        assert len(list((output / "charts").glob("*_rank_*.png"))) == 27
        assert len(list((output / "charts").glob("*_violin.png"))) == 9
        assert all(
            f"## {code}." in report
            for code in ("01", "02", "03", "08", "09A", "09B", "09C", "09D", "10")
        )
        assert (
            report.index("### 01-3등")
            < report.index("### 01-비교")
            < report.index("## 02.")
        )
        prose = re.sub(r"data:image/png;base64,[^)]+", "[image]", report)
        assert "학습" not in prose and "NB2" not in prose
        assert len(manifest["topics"]) == 9
        assert manifest["status"] == "passed"
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before
    finally:
        shutil.rmtree(output)
