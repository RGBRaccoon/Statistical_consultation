"""로또 기대수익 분석의 핵심 계산 테스트."""

import base64
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lotto_analysis as la  # noqa: E402


def test_rank_probabilities_match_combinatorial_counts():
    probabilities = la.rank_probability_table().set_index("순위")
    total = math.comb(45, 6)

    assert probabilities.loc["1등", "유리한 조합 수"] == 1
    assert probabilities.loc["2등", "유리한 조합 수"] == 6
    assert probabilities.loc["3등", "유리한 조합 수"] == 228
    assert probabilities.loc["4등", "유리한 조합 수"] == 11_115
    assert probabilities.loc["5등", "유리한 조합 수"] == 182_780
    assert probabilities.loc["1등", "확률"] == 1 / total


def test_current_net_prize_uses_current_lottery_tax_bands():
    assert la.current_net_prize(2_000_000) == 2_000_000
    assert la.current_net_prize(3_000_000) == 2_340_000
    assert la.current_net_prize(300_000_000) == 234_000_000
    assert la.current_net_prize(301_000_000) == 234_670_000
    assert la.current_net_prize(1_000_000_000) == 703_000_000


def test_build_round_table_removes_five_rank_repetitions():
    rows = []
    for rank in range(1, 6):
        rows.append(
            {
                "회차": 10,
                "순위": f"{rank}등",
                "등위별 총 당첨금": rank * 100,
                "당첨게임 수": rank,
                "1게임당 당첨금": 100,
                "당첨기준": "조건",
                "비고": "1등 자동1 수동0 반자동0",
                "총판매금액": 1_000_000,
                "당첨번호1": 1,
                "당첨번호2": 2,
                "당첨번호3": 3,
                "당첨번호4": 4,
                "당첨번호5": 5,
                "당첨번호6": 6,
                "보너스번호": 7,
            }
        )

    rounds = la.build_round_table(pd.DataFrame(rows))

    assert len(rounds) == 1
    assert rounds.iloc[0]["당첨번호"] == (1, 2, 3, 4, 5, 6)
    assert rounds.iloc[0]["1등_당첨게임 수"] == 1


def test_combination_features_capture_crowd_relevant_patterns():
    features = la.combination_features((1, 2, 3, 11, 21, 31), {1, 9, 20, 30, 40, 45})

    assert features["31이하_개수"] == 6
    assert features["생일형_전체"] == 1
    assert features["연속쌍_수"] == 2
    assert features["같은끝자리쌍_수"] == 6
    assert features["홀수_개수"] == 5
    assert features["직전회차_중복수"] == 1


def test_expected_value_sums_rank_contributions_and_subtracts_cost():
    prizes = {
        "1등": 2_000_000_000,
        "2등": 50_000_000,
        "3등": 1_500_000,
        "4등": 50_000,
        "5등": 5_000,
    }

    result = la.expected_value_for_prizes(prizes, ticket_cost=1_000)
    expected_gross = sum(
        row["확률"] * prizes[row["순위"]]
        for row in la.rank_probability_table().to_dict("records")
    )

    assert math.isclose(result["세전_기대수령액"], expected_gross)
    assert math.isclose(result["세전_순기대이익"], expected_gross - 1_000)
    assert result["세후_기대수령액"] < result["세전_기대수령액"]


def test_counterfactual_prizes_add_the_purchased_game_to_shared_ranks():
    row = pd.Series(
        {
            "1등_당첨게임 수": 9,
            "1등_등위별 총 당첨금": 18_000_000_000,
            "2등_당첨게임 수": 49,
            "2등_등위별 총 당첨금": 2_500_000_000,
            "3등_당첨게임 수": 999,
            "3등_등위별 총 당첨금": 1_500_000_000,
            "4등_1게임당 당첨금": 50_000,
            "5등_1게임당 당첨금": 5_000,
        }
    )

    prizes = la.counterfactual_prizes_for_added_ticket(row)

    assert prizes["1등"] == 1_800_000_000
    assert prizes["2등"] == 50_000_000
    assert prizes["3등"] == 1_500_000
    assert prizes["4등"] == 50_000
    assert prizes["5등"] == 5_000


def test_expanding_mod5_selector_never_uses_future_first_100_rows():
    positions = [((number - 1) % 5) + 1 for number in range(1, 121)]
    data = pd.DataFrame(
        {
            "회차": range(1, 121),
            "회차_mod5": positions,
            "세전_기대수령액": [
                1_000 if position == 5 else 0 for position in positions
            ],
        }
    )

    selector = la.expanding_mod5_selector(data, minimum_history=100)

    assert not selector.iloc[:100].any()
    assert selector.iloc[100:].sum() > 0


def test_embedded_png_is_self_contained(tmp_path):
    image = tmp_path / "tiny.png"
    payload = b"\x89PNG\r\n\x1a\nexample"
    image.write_bytes(payload)

    tag = la.embedded_png_tag(image, "검증 그림")

    assert tag.startswith('<img alt="검증 그림" src="data:image/png;base64,')
    encoded = tag.split("base64,", 1)[1].split('"', 1)[0]
    assert base64.b64decode(encoded) == payload


def test_english_table_translates_headers_and_category_values():
    frame = pd.DataFrame({"순위": ["1등"], "확률": [1 / la.TOTAL_COMBINATIONS]})

    table = la._english_table(
        frame,
        {"순위": "Rank", "확률": "Probability"},
        {"Probability": ".10f"},
    )

    assert "Rank" in table
    assert "Probability" in table
    assert "1st" in table
    assert "1등" not in table
