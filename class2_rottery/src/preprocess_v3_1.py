"""V2 전처리 CSV(V3 작업 시작 시 그대로 복사해 쓰던 것과 동일)에서 실제 판매게임수 열을 담은 V3.1 CSV를 만든다.

API의 총판매금액(wholEpsdSumNtslAmt)은 실제 판매액이고 한 게임 가격이 고정이므로
총판매금액 ÷ 게임가격은 추정이 아니라 실제 판매게임수다. 나누어떨어지지 않으면 중단한다.
"""

import pandas as pd
from paths import DATA

SOURCE = DATA / "lotto_preprocessed_v2.csv"
TARGET = DATA / "lotto_preprocessed_v3_1.csv"


def build(source: pd.DataFrame) -> pd.DataFrame:
    """추정_판매게임수를 같은 위치의 판매게임수로 바꾸고 정확성을 검사한다."""
    sales = source["총판매금액"]
    price = source["게임가격_외부결합"]
    if not (sales % price).eq(0).all():
        raise ValueError("총판매금액이 게임가격으로 나누어떨어지지 않는 회차가 있다.")
    result = source.rename(columns={"추정_판매게임수": "판매게임수"})
    result["판매게임수"] = (sales // price).astype("int64")
    return result


def main() -> None:
    source = pd.read_csv(SOURCE)
    result = build(source)
    result.to_csv(TARGET, index=False, encoding="utf-8-sig")
    print(f"{TARGET.name}: {len(result)}행, 판매게임수 정확성 확인")


if __name__ == "__main__":
    main()
