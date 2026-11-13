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
    for _ in range(200):
        pace.before_request()
        pace.after_request()
    state = store.get_meta("pacing")
    assert state["remaining"] == 0 and state["requests"] == 200
    assert state["next_at"] - clock.time() == 182
    restarted = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    previous = clock.time()
    restarted.before_request()
    assert clock.time() - previous == 182
    assert store.get_meta("pacing")["remaining"] == 199


def test_interruption_during_last_request_keeps_conservative_batch_rest():
    store, clock = memory_store(), Clock()
    store.set_meta("pacing", {"remaining": 1, "batch": 1, "requests": 99, "next_at": 0})
    pace = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    pace.before_request()
    before = clock.time()
    restarted = crawler.Pacer(store, rng=FixedRandom(), clock=clock)
    restarted.before_request()
    assert clock.time() - before == 335


def test_partition_is_disjoint_exhaustive_and_independent_of_input_order():
    rows = [{"product_id": str(i)} for i in range(1000)]
    first = crawler.partition_targets(rows, 1, 2)
    second = crawler.partition_targets(rows, 2, 2)
    first_ids = {r["product_id"] for r in first}
    second_ids = {r["product_id"] for r in second}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {str(i) for i in range(1000)}
    assert first_ids == {
        r["product_id"] for r in crawler.partition_targets(rows[::-1], 1, 2)
    }
    assert first and second
    with pytest.raises(ValueError):
        crawler.partition_targets(rows, 0, 2)


def test_new_pacing_keeps_upper_bounds_and_site_minimum():
    class UpperRandom(FixedRandom):
        def randint(self, low, high):
            return high

        def uniform(self, low, high):
            return high

    store, clock = memory_store(), Clock()
    pace = crawler.Pacer(store, rng=UpperRandom(), clock=clock)
    pace.before_request()
    assert store.get_meta("pacing")["remaining"] == 299
    pace.after_request()
    assert store.get_meta("pacing")["next_at"] - clock.time() == 5
    state = store.get_meta("pacing")
    state["remaining"] = 0
    store.set_meta("pacing", state)
    pace.minimum_delay = 10
    pace.after_request()
    assert store.get_meta("pacing")["next_at"] - clock.time() == 310


def test_snapshot_restores_only_owned_ids_without_overwriting_results(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    source = crawler.Store(sqlite3.connect(source_path))
    rows = [
        {
            "product_id": str(i),
            "publication_month": "2024-02",
            "source_category": "001001003031",
        }
        for i in range(10, 30)
    ]
    source.initialize(rows, random.Random(7))
    for product in rows[:6]:
        data, meta, nodes = crawler.parse_detail(HTML, product["product_id"])
        if product["product_id"] == "10":
            meta["field_status"]["description"] = "unknown"
        source.save_result(product["product_id"], data, meta, nodes)
    source.set_meta("pilot_passed", True)
    source.set_meta("pacing", {"next_at": 9000})
    source.connection.close()
    snapshot = tmp_path / "resume.jsonl"
    crawler.export_snapshot(source_path, snapshot)
    imported_ids = set()
    for part in (1, 2):
        store = memory_store()
        owned = crawler.partition_targets(rows, part, 2)
        store.initialize(owned, random.Random(9))
        count = store.import_snapshot(snapshot, crawler.target_fingerprint(rows))
        actual = list(
            store.connection.execute("SELECT * FROM jobs WHERE data_json IS NOT NULL")
        )
        assert count == len(actual)
        assert all(r["product_id"] in {p["product_id"] for p in owned} for r in actual)
        assert store.import_snapshot(snapshot, crawler.target_fingerprint(rows)) == 0
        store.set_meta("run_status", "blocked")
        with pytest.raises(crawler.BlockedError):
            store.import_snapshot(snapshot, crawler.target_fingerprint(rows))
        assert store.get_meta("pilot_passed") is True
        assert store.get_meta("pacing")["next_at"] == 9000
        imported_ids.update(r["product_id"] for r in actual)
        reviewed = [r for r in actual if r["product_id"] == "10"]
        assert not reviewed or reviewed[0]["status"] == "review"
    assert imported_ids == {str(i) for i in range(10, 16)}


def test_snapshot_cannot_reset_access_restriction(tmp_path):
    source = crawler.Store(sqlite3.connect(tmp_path / "source.sqlite3"))
    source.set_meta("run_status", "blocked")
    source.connection.close()
    with pytest.raises(crawler.BlockedError):
        crawler.export_snapshot(tmp_path / "source.sqlite3", tmp_path / "resume.jsonl")


def test_one_computer_cannot_run_two_collectors(tmp_path):
    path = tmp_path / "collector.lock"
    with crawler.single_process_lock(path):
        with pytest.raises(OSError):
            with crawler.single_process_lock(path):
                pytest.fail("두 번째 수집기가 잠금을 획득했습니다.")


def test_first_phase_never_requests_after_2016_and_can_continue_later():
    store = memory_store()
    rows = [
        {"product_id": "1", "publication_month": "2025-01"},
        {"product_id": "2", "publication_month": "2016-12"},
        {"product_id": "3", "publication_month": "2017-01"},
    ]
    store.initialize(rows, random.Random(7), pilot_ids=["1", "3", "2"])
    assert crawler.next_job(store, 2016)["product_id"] == "2"
    assert crawler.next_job(store, 2026)["product_id"] == "2"
    store.mark_error("2", "not_found", "삭제된 상품")
    assert crawler.next_job(store, 2016) is None
    assert crawler.next_job(store, 2026)["product_id"] == "1"
    phases = store.phase_counts()
    assert phases["2010_2016"]["total"] == 1
    assert phases["2010_2016"]["pending"] == 0
    assert phases["2017_2026"]["pending"] == 2
    crawler.finish_phase(store, 2016)
    assert store.get_meta("run_status") == "phase1_finished_with_review"


def test_phase1_csv_preserves_only_first_phase_results(tmp_path):
    store = memory_store()
    rows = [
        {"product_id": "1", "publication_month": "2016-12"},
        {"product_id": "2", "publication_month": "2017-01"},
    ]
    store.initialize(rows, random.Random(7))
    for product in rows:
        data, meta, nodes = crawler.parse_detail(HTML, product["product_id"])
        store.save_result(product["product_id"], data, meta, nodes)
    output = tmp_path / "part1.csv"
    store.export(output)
    assert {r["product_id"] for r in crawler.read_csv(output)} == {"1", "2"}
    assert [
        r["product_id"] for r in crawler.read_csv(tmp_path / "part1.phase1.csv")
    ] == ["1"]


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
