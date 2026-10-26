"""로또 V2 전처리와 Step 1 분석 테스트."""

import json
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lotto_analysis_v2 as v2  # noqa: E402


SOURCE_PATH = Path(__file__).resolve().parents[1] / "lotto_rank_1_1245.csv"


def test_game_price_boundary_uses_round_88_change():
    assert v2.game_price_for_round(1) == 2_000
    assert v2.game_price_for_round(87) == 2_000
    assert v2.game_price_for_round(88) == 1_000
    assert v2.game_price_for_round(1_245) == 1_000


def test_preprocessing_preserves_source_and_normalizes_money_only():
    source = pd.read_csv(SOURCE_PATH, encoding="utf-8-sig")
    processed = v2.preprocess_lotto_data(source)

    pd.testing.assert_frame_equal(
        processed[source.columns].reset_index(drop=True), source.reset_index(drop=True)
    )
    round_87_rank_5 = processed.loc[
        (processed["회차"] == 87) & (processed["순위"] == "5등")
    ].iloc[0]
    round_88_rank_5 = processed.loc[
        (processed["회차"] == 88) & (processed["순위"] == "5등")
    ].iloc[0]

    assert round_87_rank_5["게임가격_외부결합"] == 2_000
    assert round_88_rank_5["게임가격_외부결합"] == 1_000
    assert round_87_rank_5["1000원기준_1게임당당첨금"] == 5_000
    assert round_88_rank_5["1000원기준_1게임당당첨금"] == 5_000
    assert round_87_rank_5["당첨게임 수"] == 890_859
    assert round_87_rank_5["추정_판매게임수"] == 41_896_821


def test_preprocessing_keeps_each_rank_separate_and_adds_expected_counts():
    source = pd.read_csv(SOURCE_PATH, encoding="utf-8-sig")
    processed = v2.preprocess_lotto_data(source)

    assert len(processed) == 6_225
    assert processed.groupby("회차").size().eq(5).all()
    assert set(processed["순위"]) == {"1등", "2등", "3등", "4등", "5등"}
    assert (processed["이론_기대당첨게임수"] > 0).all()
    assert processed["당첨게임률"].between(0, 1).all()


def test_validation_records_all_required_checks_as_passed():
    source = pd.read_csv(SOURCE_PATH, encoding="utf-8-sig")
    processed = v2.preprocess_lotto_data(source)
    validation = v2.validate_preprocessed_data(source, processed)

    assert validation["status"] == "passed"
    assert validation["checks"]
    assert all(item["passed"] for item in validation["checks"])


def test_step1_summary_has_every_rank_and_metric():
    source = pd.read_csv(SOURCE_PATH, encoding="utf-8-sig")
    processed = v2.preprocess_lotto_data(source)
    summary = v2.build_step1_rank_summary(processed)

    assert set(summary["순위"]) == {"1등", "2등", "3등", "4등", "5등"}
    assert set(summary["지표"]) == set(v2.STEP1_METRICS)
    assert summary["회차수"].eq(1_245).all()


def test_run_v2_writes_traceable_outputs_and_embedded_report():
    temporary_path = SOURCE_PATH.parent / ".test_output_v2"
    if temporary_path.exists():
        shutil.rmtree(temporary_path)
    temporary_path.mkdir()
    try:
        output_dir = temporary_path / "results"
        processed_path = temporary_path / "lotto_preprocessed_v2.csv"
        report_path = temporary_path / "lotto_rank_report_v2.md"

        result = v2.run_v2(SOURCE_PATH, processed_path, output_dir, report_path)

        assert result["validation"]["status"] == "passed"
        assert processed_path.exists()
        assert (output_dir / "step0_metadata.json").exists()
        assert (output_dir / "step0_validation.json").exists()
        assert (output_dir / "step1_rank_summary.csv").exists()
        assert len(list((output_dir / "charts").glob("step1_rank_*.png"))) == 5

        report = report_path.read_text(encoding="utf-8")
        assert report.count("data:image/png;base64,") == 5
        assert "Step 2는 아직 수행하지 않았다" in report

        metadata = json.loads(
            (output_dir / "step0_metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["input_sha256"] == v2.sha256_file(SOURCE_PATH)
        assert metadata["output_sha256"] == v2.sha256_file(processed_path)
    finally:
        shutil.rmtree(temporary_path)
