"""V2.1의 분석 범위, 계산식, 정규화와 독립 검증 구간 검사."""

import base64
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import skew
from statsmodels.stats.multitest import multipletests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lotto_analysis_v2_1 as analysis  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "lotto_preprocessed_v2.csv"


def test_z_score_preserves_skew_and_does_not_force_normality():
    values = np.array([1.0, 1.0, 1.0, 2.0, 10.0])
    normalized, center, scale = analysis.standardize(values)
    assert center == pytest.approx(3)
    assert scale == pytest.approx(np.std(values, ddof=1))
    assert normalized.mean() == pytest.approx(0, abs=1e-12)
    assert normalized.std(ddof=1) == pytest.approx(1)
    assert skew(normalized) == pytest.approx(skew(values))


def test_multiple_comparison_correction_matches_statsmodels_reference():
    values = np.array([0.9, 0.00003, 0.01, 0.04, 0.05, 0.01])
    np.testing.assert_allclose(
        analysis.adjust_pvalues(values, "BH"),
        multipletests(values, method="fdr_bh")[1],
    )
    np.testing.assert_allclose(
        analysis.adjust_pvalues(values, "BY"),
        multipletests(values, method="fdr_by")[1],
    )


def test_focus_preserves_source_rows_and_excludes_other_ranks():
    original = pd.read_csv(SOURCE, encoding="utf-8-sig")
    focused = analysis.load_focus(SOURCE)
    assert set(focused["순위"]) == {"1등", "2등", "3등"}
    assert len(focused) == 3_735
    pd.testing.assert_frame_equal(
        focused[original.columns].sort_values(["회차", "순위"]).reset_index(drop=True),
        original[original["순위"].isin(analysis.RANKS)]
        .sort_values(["회차", "순위"])
        .reset_index(drop=True),
    )


def test_exposure_and_pearson_residual_match_manual_formula():
    focused = analysis.load_focus(SOURCE)
    result = analysis.build_experiments(focused)
    row = focused.loc[(focused["회차"] == 1) & (focused["순위"] == "1등")].iloc[0]
    p = 1 / 8_145_060
    mu = 1_840_891 * p
    expected = (0 - mu) / np.sqrt(mu * (1 - p))
    item = (
        result["observations"]
        .loc[
            (result["observations"]["분석ID"] == "03")
            & (result["observations"]["회차"] == 1)
            & (result["observations"]["순위"] == "1등")
        ]
        .iloc[0]
    )
    assert row["추정_판매게임수"] == 1_840_891
    assert item["값"] == pytest.approx(expected)


def test_twelve_analyses_each_include_all_three_ranks_and_normalization():
    result = analysis.build_experiments(analysis.load_focus(SOURCE))
    assert len(result["experiments"]) >= 10
    assert len(result["experiments"]) == 12
    observations = result["observations"]
    for experiment in result["experiments"]:
        subset = observations.loc[observations["분석ID"] == experiment["id"]]
        assert set(subset["순위"]) == set(analysis.RANKS)
        assert np.isfinite(subset[["값", "표준화값"]]).all().all()
        if experiment["normalization"] == "등수별 Z점수":
            for _, group in subset.groupby("순위"):
                assert group["표준화값"].mean() == pytest.approx(0, abs=1e-10)
                assert group["표준화값"].std(ddof=1) == pytest.approx(1)


def test_every_displayed_score_can_be_reconstructed_from_saved_coefficients():
    result = analysis.build_experiments(analysis.load_focus(SOURCE))
    joined = result["observations"].merge(
        result["normalization"][["분석ID", "순위", "중심", "척도"]],
        on=["분석ID", "순위"],
        validate="many_to_one",
    )
    np.testing.assert_allclose(
        joined["표준화값"],
        (joined["값"] - joined["중심"]) / joined["척도"],
        rtol=1e-12,
        atol=1e-12,
    )


def test_temporal_features_never_cross_regime_boundaries():
    result = analysis.build_experiments(analysis.load_focus(SOURCE))
    obs = result["observations"]
    changes = obs[obs["분석ID"] == "07"]
    assert not changes["회차"].isin([1, 88, 401]).any()
    blocks = result["tables"]["08_blocks"]
    assert blocks["시작회차"].le(blocks["종료회차"]).all()
    assert blocks["포함회차수"].eq(25).all()
    assert not ((blocks["시작회차"] < 88) & (blocks["종료회차"] >= 88)).any()
    assert not ((blocks["시작회차"] < 401) & (blocks["종료회차"] >= 401)).any()


def test_validation_residuals_use_only_earlier_training_rounds():
    result = analysis.build_experiments(analysis.load_focus(SOURCE))
    models = result["tables"]["11_models"]
    assert len(models) == 9
    assert (models["학습종료회차"] < models["검증시작회차"]).all()
    assert (models["alpha"] >= 0).all()
    held_out = result["observations"].query("분석ID == '11'")
    for _, model in models.iterrows():
        group = held_out[
            held_out["순위"].eq(model["순위"]) & held_out["기간"].eq(model["기간"])
        ]
        assert group["회차"].min() == model["검증시작회차"]
        assert group["회차"].max() == model["검증종료회차"]


def test_changing_future_winners_does_not_change_trained_parameters():
    focused = analysis.load_focus(SOURCE)
    baseline = analysis.build_experiments(focused)
    changed = focused.copy()
    mask = changed["회차"].ge(1_000)
    changed.loc[mask, "당첨게임 수"] *= 2
    changed.loc[mask, "관측_기대_비율"] *= 2
    alternative = analysis.build_experiments(changed)
    parameters = ["순위", "기간", "평균배율", "alpha", "학습종료회차"]
    pd.testing.assert_frame_equal(
        baseline["tables"]["11_models"][parameters],
        alternative["tables"]["11_models"][parameters],
    )


def test_case_is_selected_from_data_and_uses_same_round_for_every_rank():
    focused = analysis.load_focus(SOURCE)
    result = analysis.build_experiments(focused)
    selected = focused.sort_values(
        ["관측_기대_비율", "회차", "순위"], ascending=[False, True, True]
    ).iloc[0]
    case = result["tables"]["12_case"]
    assert case["회차"].nunique() == 1
    assert case["회차"].iloc[0] == selected["회차"]
    assert set(case["순위"]) == set(analysis.RANKS)
    assert "p값" not in case.columns


def test_report_is_self_contained_and_preserves_input():
    output = BASE / ".test_output_v2_1"
    assert not output.exists(), (
        "고정된 테스트 경로에 다른 작업이 있으면 덮어쓰지 않는다."
    )
    original_hash = analysis.sha256_file(SOURCE)
    output.mkdir()
    (output / "independent_audit.json").write_text(
        '{"status": "previous audit"}', encoding="utf-8"
    )
    try:
        result = analysis.run(SOURCE, output)
        metadata = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert result["validation"]["status"] == "passed"
        assert metadata["input_sha256"] == original_hash
        assert analysis.sha256_file(SOURCE) == original_hash
        assert len(list((output / "charts").glob("*_rank_*.png"))) == 36
        assert len(list((output / "charts").glob("*_violin.png"))) == 12
        report = (output / "lotto_rank_report_v2_1.md").read_text(encoding="utf-8")
        assert report.count("data:image/png;base64,") == 48
        assert "표준화가 정규성을 보장하지 않는다" in report
        for embedded in result["embedded_images"]:
            assert base64.b64decode(embedded).startswith(b"\x89PNG\r\n\x1a\n")
        html = (output / "lotto_rank_report_v2_1.html").read_text(encoding="utf-8")
        assert html.count("data:image/png;base64,") == 48
        appendix_start = report.index("## 부록: 분석별 1·2·3등 비교")
        assert report[:appendix_start].count("data:image/png;base64,") == 36
        assert report[appendix_start:].count("data:image/png;base64,") == 12
        assert "<table>" in html
        assert all(
            analysis.sha256_file(output / item["path"]) == item["sha256"]
            for item in metadata["artifacts"]
        )
        assert "independent_audit.json" not in {
            item["path"] for item in metadata["artifacts"]
        }
    finally:
        shutil.rmtree(output)
