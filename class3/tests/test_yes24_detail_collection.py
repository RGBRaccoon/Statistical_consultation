import csv
import importlib
import json
import random
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
crawler = importlib.import_module("yes24_detail_collection")

HTML = """
<div class="gd_infoTop"><h2 class="gd_name">AI 책</h2>
<span class="gd_nameE">부제</span></div>
<div id="infoset_specific"><table>
<tr><th>발행일</th><td>2024년 02월 03일</td></tr>
<tr><th>ISBN13</th><td>9781234567890</td></tr></table></div>
<div id="infoset_goodsCate"><dl><dt>카테고리 분류</dt><dd><ul><li>
<a href="/Product/Category/Display/001">국내도서</a>
<a href="/Product/Category/Display/001001003">IT 모바일</a>
<a href="/Product/Category/Display/001001003031">인공지능</a>
</li></ul></dd><dt>수상내역 및 미디어 추천 분류</dt><dd><ul><li>
<a href="/Product/Category/Display/999">추천</a></li></ul></dd></dl></div>
<div id="infoset_introduce"><div class="infoWrap_txtInner">소개<br>본문</div></div>
<div id="infoset_toc"><textarea class="txtContentText">&lt;b&gt;1장&lt;/b&gt;&lt;br&gt;AI</textarea></div>
"""


def memory_store():
    return crawler.Store(sqlite3.connect(":memory:"))


def test_target_boundaries_and_duplicate_ids():
    rows = [
        {"product_id": str(i), "publication_month": month}
        for i, month in enumerate(("2009-12", "2010-01", "2026-11", "2026-12", ""))
    ]
    assert [r["product_id"] for r in crawler.select_targets(rows)] == ["1", "2"]
    with pytest.raises(ValueError, match="중복"):
        crawler.select_targets([rows[1], rows[1]])


def test_parser_keeps_linked_korean_paths_and_ten_columns():
    row, meta, nodes = crawler.parse_detail(HTML, "10")
    assert list(row) == crawler.COLUMNS
    assert len(row) == 10
    assert row["publication_date"] == "2024-02-03"
    assert row["description"] == "소개\n본문"
    assert row["table_of_contents"] == "1장\nAI"
    assert json.loads(row["categories"]) == [
        {
            "code": "001001003031",
            "name": "인공지능",
            "path": ["국내도서", "IT 모바일", "인공지능"],
        }
    ]
    assert len(nodes) == 3 and nodes[-1]["parent_code"] == "001001003"
    assert meta["field_status"]["categories"] == "present"


def test_missing_text_does_not_become_confirmed_absence():
    row, meta, _ = crawler.parse_detail(
        HTML.replace('<div class="infoWrap_txtInner">소개<br>본문</div>', ""), "10"
    )
    assert row["description"] == ""
    assert meta["field_status"]["description"] == "unknown"
    _, meta, _ = crawler.parse_detail(
        HTML.replace("소개<br>본문", "등록된 책소개가 없습니다."), "10"
    )
    assert meta["field_status"]["description"] == "source_absent"
    with pytest.raises(crawler.StructureError):
        crawler.parse_detail("<html>인증이 필요합니다</html>", "10")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FixedRandom:
    def randint(self, low, high):
        return low

    def uniform(self, low, high):
        return low


def test_pacing_accumulates_batch_rest_and_survives_restart():
    store, clock = memory_store(), Clock()
    pace = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    for _ in range(100):
        pace.before_request()
        pace.after_request()
    state = store.get_meta("pacing")
    assert state["remaining"] == 0 and state["requests"] == 100
    assert state["next_at"] - clock.time() == 305
    restarted = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    previous = clock.time()
    restarted.before_request()
    assert clock.time() - previous == 305
    assert store.get_meta("pacing")["remaining"] == 99


def test_interruption_during_last_request_keeps_conservative_batch_rest():
    store, clock = memory_store(), Clock()
    store.set_meta("pacing", {"remaining": 1, "batch": 1, "requests": 99, "next_at": 0})
    pace = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    pace.before_request()
    before = clock.time()
    restarted = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    restarted.before_request()
    assert clock.time() - before == 650


def test_category_conflict_does_not_silently_change_names():
    store = memory_store()
    _, _, nodes = crawler.parse_detail(HTML, "10")
    store.save_nodes(nodes)
    with pytest.raises(crawler.StructureError, match="충돌"):
        store.save_nodes([{**nodes[-1], "name": "다른 분류"}])


def test_http_block_stops_without_retry_or_new_session():
    store, clock = memory_store(), Clock()
    pace = crawler.Pacer(store, rng=FixedRandom(), clock=clock)

    class Session:
        def __init__(self):
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            response = crawler.requests.Response()
            response.status_code = 429
            response.headers["Retry-After"] = "600"
            return response

    session = Session()
    client = crawler.Client(store, pace, session=session)
    with pytest.raises(crawler.BlockedError):
        client.fetch("/robots.txt", check_robots=False)
    assert session.calls == 1
    assert store.get_meta("pacing")["requests"] == 1
    assert store.get_meta("pacing")["next_at"] >= clock.time() + 600


def test_result_is_durable_and_csv_roundtrips_without_extra_columns(tmp_path):
    db = tmp_path / "run.sqlite3"
    store = crawler.Store(sqlite3.connect(db))
    target = {
        "product_id": "10",
        "publication_month": "2024-02",
        "source_category": "001001003031",
    }
    store.initialize([target], random.Random(7))
    row, meta, nodes = crawler.parse_detail(HTML, "10")
    store.save_result("10", row, meta, nodes)
    store.connection.close()
    restored = crawler.Store(sqlite3.connect(db))
    assert restored.counts()["complete"] == 1
    output = tmp_path / "books.csv"
    restored.export(output)
    with output.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == crawler.COLUMNS
        assert list(reader)[0] == row


def test_legacy_reuse_requires_verified_codes_for_every_path():
    store = memory_store()
    store.initialize(
        [
            {
                "product_id": "10",
                "publication_month": "2024-02",
                "source_category": "001001003031",
            }
        ],
        random.Random(2),
    )
    row, meta, nodes = crawler.parse_detail(HTML, "10")
    legacy = {**row, "categories": json.dumps(["국내도서 > IT 모바일 > 인공지능"])}
    assert store.reuse_legacy([legacy]) == 0
    store.save_nodes(nodes)
    assert store.reuse_legacy([legacy]) == 1
    assert store.counts()["reused"] == 1
