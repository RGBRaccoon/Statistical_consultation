"""lotto_crawler 단위 테스트 (네트워크 호출 없음)."""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lotto_crawler as lc  # noqa: E402

# 실제 API(1245회) 응답 일부
ROUND_1245 = {
    "winType0": 0,
    "winType1": 10,
    "winType2": 0,
    "winType3": 0,
    "ltEpsd": 1245,
    "rnk1WnNope": 10,
    "rnk1WnAmt": 3006515775,
    "rnk2WnNope": 91,
    "rnk2WnAmt": 55064392,
    "rnk3WnNope": 3102,
    "rnk3WnAmt": 1615365,
    "rnk4WnNope": 163177,
    "rnk4WnAmt": 50000,
    "rnk5WnNope": 2786728,
    "rnk5WnAmt": 5000,
    "rlvtEpsdSumNtslAmt": 124358734000,
    "wholEpsdSumNtslAmt": 124358734000,
}


def test_parse_round_returns_five_rank_rows_matching_site():
    rows = lc.parse_round(ROUND_1245)

    assert [r["순위"] for r in rows] == ["1등", "2등", "3등", "4등", "5등"]
    assert rows[0] == {
        "회차": 1245,
        "순위": "1등",
        "등위별 총 당첨금": 30065157750,
        "당첨게임 수": 10,
        "1게임당 당첨금": 3006515775,
        "당첨기준": "6개번호 일치",
        "비고": "1등 자동10 수동0 반자동0",
        "총판매금액": 124358734000,
    }
    assert rows[1]["당첨기준"] == "5개번호 일치 + 보너스번호 일치"
    assert rows[1]["등위별 총 당첨금"] == 5010859672
    assert rows[2]["등위별 총 당첨금"] == 5010862230
    assert rows[4]["등위별 총 당첨금"] == 13933640000
    assert rows[4]["당첨게임 수"] == 2786728
    assert all(r["총판매금액"] == 124358734000 for r in rows)


def test_parse_round_total_sales_uses_site_display_field():
    # 사이트는 총판매금액에 wholEpsdSumNtslAmt 값을 표시한다
    item = dict(ROUND_1245, rlvtEpsdSumNtslAmt=1, wholEpsdSumNtslAmt=3681782000)
    assert lc.parse_round(item)[0]["총판매금액"] == 3681782000


def test_parse_round_remarks_empty_when_wintype0_nonzero():
    item = dict(ROUND_1245, winType0=1)
    assert all(r["비고"] == "" for r in lc.parse_round(item))


def test_crawl_collects_requested_range_using_batches():
    calls = []

    def fake_fetch(epsd):
        calls.append(epsd)
        # 실제 API처럼 요청 회차 기준 10개를 내림차순으로 반환
        start = min(epsd, 21)
        return [dict(ROUND_1245, ltEpsd=e) for e in range(start + 9, start - 1, -1)]

    rows = lc.crawl(1, 25, fetch=fake_fetch, delay=0)

    assert sorted({r["회차"] for r in rows}) == list(range(1, 26))
    assert len(rows) == 25 * 5
    assert len(calls) < 25  # 회차마다 요청하지 않고 묶음으로 수집
    assert [r["회차"] for r in rows[:6]] == [1, 1, 1, 1, 1, 2]


def test_save_csv_writes_header_and_rows(tmp_path):
    out = tmp_path / "out.csv"
    lc.save_csv(lc.parse_round(ROUND_1245), out)

    with out.open(encoding="utf-8-sig", newline="") as f:
        reader = list(csv.reader(f))
    assert reader[0] == lc.COLUMNS
    assert len(reader) == 6
    assert reader[1][:3] == ["1245", "1등", "30065157750"]
