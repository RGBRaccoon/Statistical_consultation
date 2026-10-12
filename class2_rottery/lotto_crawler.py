"""동행복권 로또 6/45 회차별 순위 당첨 정보 크롤러.

https://www.dhlottery.co.kr/lt645/result 페이지가 내부적으로 호출하는
JSON API(/lt645/selectPstLt645InfoNew.do)를 사용해 1회~1245회의
순위별 당첨 정보와 회차별 총판매금액을 수집하고 CSV로 저장한다.

사용법:
    python lotto_crawler.py                      # 1~1245회 -> lotto_rank_1_1245.csv
    python lotto_crawler.py --start 1 --end 100 --out sample.csv
"""

import argparse
import csv
import time
from collections.abc import Callable
from pathlib import Path

import requests

API_URL = "https://www.dhlottery.co.kr/lt645/selectPstLt645InfoNew.do"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.dhlottery.co.kr/lt645/result",
}

COLUMNS = [
    "회차",
    "순위",
    "등위별 총 당첨금",
    "당첨게임 수",
    "1게임당 당첨금",
    "당첨기준",
    "비고",
    "총판매금액",
]

# 사이트 표의 당첨기준 문구
CRITERIA = {
    1: "6개번호 일치",
    2: "5개번호 일치 + 보너스번호 일치",
    3: "5개번호 일치",
    4: "4개번호 일치",
    5: "3개번호 일치",
}


def fetch_batch(epsd: int, session: requests.Session | None = None) -> list[dict]:
    """epsd 회차를 포함한 최대 10개 회차 정보를 API에서 가져온다."""
    http = session or requests
    params = {"srchDir": "center", "srchLtEpsd": str(epsd)}
    for attempt in range(3):
        try:
            resp = http.get(API_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json()["data"]["list"]
        except (requests.RequestException, ValueError, KeyError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def make_remarks(item: dict) -> str:
    """사이트와 동일한 규칙으로 비고(1등 자동/수동/반자동 수)를 만든다."""
    if item.get("winType0") != 0:
        return ""
    return (
        f"1등 자동{item.get('winType1', 0)} "
        f"수동{item.get('winType2', 0)} "
        f"반자동{item.get('winType3', 0)}"
    )


def parse_round(item: dict) -> list[dict]:
    """API 회차 데이터 1건을 순위별 5개 행으로 변환한다.

    등위별 총 당첨금은 사이트 화면과 동일하게 당첨게임 수 x 1게임당 당첨금으로 계산한다.
    (초기 회차의 rnkNSumWnAmt 필드는 이월금 등 다른 의미로 채워져 있어 사용하지 않는다.)
    """
    remarks = make_remarks(item)
    # 사이트의 "총판매금액" 표시값과 동일한 필드(회차 단위 값이므로 5개 행에 반복)
    total_sales = int(item["wholEpsdSumNtslAmt"])
    rows = []
    for rank in range(1, 6):
        count = int(item[f"rnk{rank}WnNope"])
        amount = int(item[f"rnk{rank}WnAmt"])
        rows.append(
            {
                "회차": int(item["ltEpsd"]),
                "순위": f"{rank}등",
                "등위별 총 당첨금": count * amount,
                "당첨게임 수": count,
                "1게임당 당첨금": amount,
                "당첨기준": CRITERIA[rank],
                "비고": remarks,
                "총판매금액": total_sales,
            }
        )
    return rows


def crawl(
    start: int = 1,
    end: int = 1245,
    fetch: Callable[[int], list[dict]] | None = None,
    delay: float = 0.5,
) -> list[dict]:
    """start~end 회차를 수집해 회차·순위 오름차순 행 리스트로 반환한다."""
    if fetch is None:
        session = requests.Session()

        def fetch(epsd: int) -> list[dict]:
            return fetch_batch(epsd, session)

    collected: dict[int, dict] = {}
    for epsd in range(start, end + 1):
        if epsd in collected:
            continue
        for item in fetch(epsd):
            e = int(item["ltEpsd"])
            if start <= e <= end:
                collected[e] = item
        if epsd not in collected:
            raise RuntimeError(f"{epsd}회 데이터를 가져오지 못했습니다.")
        print(f"수집 진행: {len(collected)}/{end - start + 1}회", flush=True)
        if delay:
            time.sleep(delay)

    rows = []
    for e in sorted(collected):
        rows.extend(parse_round(collected[e]))
    return rows


def save_csv(rows: list[dict], path: str | Path) -> None:
    """엑셀에서 한글이 깨지지 않도록 UTF-8 BOM으로 저장한다."""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="로또 6/45 순위별 당첨 정보 크롤러")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=1245)
    parser.add_argument("--out", default=None, help="저장할 CSV 경로")
    parser.add_argument("--delay", type=float, default=0.5, help="요청 간 대기(초)")
    args = parser.parse_args()

    out = args.out or Path(__file__).parent / f"lotto_rank_{args.start}_{args.end}.csv"
    rows = crawl(args.start, args.end, delay=args.delay)
    save_csv(rows, out)
    print(f"저장 완료: {out} ({len(rows)}행)")


if __name__ == "__main__":
    main()
