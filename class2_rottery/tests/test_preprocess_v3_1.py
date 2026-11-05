"""V3.1 전처리 복사본의 실제 판매게임수 열을 검증한다."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preprocess_v3_1 as prep  # noqa: E402

BASE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pair():
    source = pd.read_csv(BASE / "lotto_preprocessed_v2.csv")
    return source, prep.build(source)


def test_estimated_column_is_replaced_by_actual_count(pair):
    _, result = pair
    assert "추정_판매게임수" not in result.columns
    assert "판매게임수" in result.columns


def test_actual_count_is_exact_sales_divided_by_price(pair):
    _, result = pair
    assert (result["총판매금액"] % result["게임가격_외부결합"]).eq(0).all()
    expected = result["총판매금액"] // result["게임가격_외부결합"]
    assert result["판매게임수"].eq(expected).all()
    assert pd.api.types.is_integer_dtype(result["판매게임수"])


def test_other_values_and_order_are_unchanged(pair):
    source, result = pair
    renamed = source.rename(columns={"추정_판매게임수": "판매게임수"})
    assert list(result.columns) == list(renamed.columns)
    pd.testing.assert_frame_equal(result, renamed)


def test_inexact_sales_amount_is_rejected():
    frame = pd.DataFrame(
        {"총판매금액": [3001], "게임가격_외부결합": [2000], "추정_판매게임수": [1]}
    )
    with pytest.raises(ValueError):
        prep.build(frame)
