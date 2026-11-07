import csv
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "yes24_crawler.py"
spec = importlib.util.spec_from_file_location("yes24_crawler", MODULE)
crawler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crawler
spec.loader.exec_module(crawler)


def listing(ids, total):
    items = "".join(
        f'<li data-goods-no="{i}"><a class="gd_name" '
        f'href="/product/goods/{i}">책</a></li>'
        for i in ids
    )
    return f'<div class="cateGoodsSecTit">상품 ({total:,})</div><ul id="yesNewList">{items}</ul>'


DETAIL = """
<div class="gd_infoTop"><h2 class="gd_name">테스트 AI</h2>
<h3 class="gd_nameE">입문</h3><span class="gd_auth">가 저 / 나 역</span>
<span class="gd_pub">출판사</span><span class="gd_date">2024년 02월 03일</span>
<span class="gd_sellNum">판매지수 1,234 판매지수란?</span>
<span class="gd_rating"><em class="yes_b">9.6</em></span>
<span class="moreRatingLi"><li>종이책 리뷰 (3건)</li><li>eBook 리뷰 (2건)</li>
<li>종이책 한줄평 (0건)</li><li>종이책 사락 별점 (1건)</li></span></div>
<div id="infoset_specific"><table><tr><th>발행일</th><td>2024년 02월 03일</td></tr>
<tr><th>쪽수, 무게, 크기</th><td>320쪽 | 600g | 150*220*20mm</td></tr>
<tr><th>ISBN13</th><td>9781234567890</td></tr></table></div>
<div class="gd_infoTb"><table><tr><th>정가</th><td>20,000원</td></tr>
<tr><th>판매가</th><td>18,000원 (10% 할인)</td></tr></table></div>
<div id="infoset_goodsCate"><ul><li><a>국내도서</a> &gt; <a>IT 모바일</a></li></ul></div>
<div id="infoset_introduce"><div class="infoWrap_txtInner">소개<br>두 번째 줄</div></div>
<div id="infoset_toc"><div class="infoWrap_txtInner">1장 AI</div></div>
"""


def test_detail_numbers_dates_missing_and_reaction_scope():
    row = crawler.parse_detail(DETAIL, "10")
    assert row["publication_date"] == "2024-02-03"
    assert row["page_count"] == 320
    assert row["sales_index"] == 1234
    assert row["list_price"] == 20000
    assert row["sale_price"] == 18000
    assert row["paper_review_count"] == 3
    assert row["ebook_review_count"] == 2
    assert row["paper_short_review_count"] == 0
    assert row["ebook_short_review_count"] == ""
    assert row["isbn10"] == ""
    assert row["contributors"] == "가 저 / 나 역"
    assert "두 번째 줄" in row["description"]
    assert row["table_of_contents"] == "1장 AI"


def test_listing_keeps_only_main_list_and_deduplicates():
    html = '<a href="/product/goods/999">광고</a>' + listing([1, 1, 2], 2)
    assert crawler.parse_listing(html) == (["1", "2"], 2)


def test_list_only_resume_without_detail_requests(tmp_path):
    out = tmp_path / "list.csv"
    client = FakeClient()
    assert not crawler.run_list(client, out, max_pages=1, page_size=2)
    assert crawler.run_list(client, out, page_size=2)
    assert client.detail_calls == []
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["product_id"] for r in rows] == ["1", "2", "3", "4"]
    assert all(r["title"] == "책" for r in rows)


def test_list_fields_and_missing_values():
    html = listing([1], 1).replace(
        "</a></li>",
        '</a><span class="gd_nameE">부제</span>'
        '<span class="info_auth">가 저</span><span class="info_pub">출판</span>'
        '<span class="info_date">2024년 02월</span>'
        '<span class="info_price"><strong class="txt_num"><em>18,000</em></strong>'
        '<span class="txt_num dash">20,000원</span><span class="txt_sale">10%</span></span>'
        '<span class="rating_grade"><em class="yes_b">9.5</em></span></li>',
    )
    rows, total = crawler.parse_list_rows(html, "001001003")
    assert total == 1
    assert rows[0]["publication_month"] == "2024-02"
    assert rows[0]["sale_price"] == 18000
    assert rows[0]["sales_index"] == ""


def test_structure_change_is_not_empty_category():
    with pytest.raises(ValueError):
        crawler.parse_listing("<html>접근을 확인하세요</html>")
    with pytest.raises(ValueError):
        crawler.parse_listing(listing([], 30))
    with pytest.raises(ValueError):
        crawler.parse_detail("<html>오류</html>", "1")


def test_hidden_textarea_toc_is_decoded():
    html = DETAIL.replace(
        '<div class="infoWrap_txtInner">1장 AI</div>',
        '<textarea class="txtContentText">&lt;b&gt;1장&lt;/b&gt;&lt;br/&gt;AI</textarea>',
    )
    assert crawler.parse_detail(html, "1")["table_of_contents"] == "1장\nAI"


def test_subcategory_boundary():
    html = '<li id="category001001003031"></li><li id="category001001004"></li>'
    assert crawler.parse_children(html, "001001003") == ["001001003031"]


def test_full_description_fallback_and_category_paths():
    html = DETAIL.replace(
        '<div class="infoWrap_txtInner">소개<br>두 번째 줄</div>',
        '<div class="infoSetCont_wrap"><div class="infoWrap_txt">'
        "소개<br>마지막 문장<script>광고()</script></div></div>",
    ).replace(
        "<ul><li><a>국내도서</a> &gt; <a>IT 모바일</a></li></ul>",
        "<dl><dt>카테고리 분류</dt><dd><ul><li><a>국내도서</a>"
        "<a>IT 모바일</a><a>인공지능</a><a>머신러닝</a></li></ul></dd>"
        "<dt>수상내역 및 미디어 추천 분류</dt><dd><ul><li><a>미디어 추천</a>"
        "</li></ul></dd></dl>",
    )
    row = crawler.parse_detail(html, "1")
    assert row["description"] == "소개\n마지막 문장"
    assert json.loads(row["categories"]) == [
        "국내도서 > IT 모바일 > 인공지능 > 머신러닝"
    ]


def test_parallel_details_only_preserves_discovery_and_csv(tmp_path):
    out = tmp_path / "books.csv"
    first = FakeClient()
    assert not crawler.run(first, out, max_pages=1, max_books=1, page_size=2)
    before = json.loads(out.with_suffix(".state.json").read_text(encoding="utf-8"))

    class DetailsOnly(FakeClient):
        def get(self, path, params=None):
            assert "/goods/" in path
            return super().get(path, params)

    client = DetailsOnly()
    assert not crawler.run(client, out, page_size=2, workers=2, details_only=True)
    after = json.loads(out.with_suffix(".state.json").read_text(encoding="utf-8"))
    assert after == before
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert {r["product_id"] for r in rows} == {"1", "2"}
    assert all(r["description"] and r["table_of_contents"] for r in rows)
    assert len(client.detail_calls) == 1


class FakeClient:
    def __init__(self):
        self.detail_calls = []

    def get(self, path, params=None):
        if "SubCategory" in path:
            return ""
        if "Contents" in path:
            return listing([1, 2] if params["page"] == 1 else [3, 4], 4)
        self.detail_calls.append(path)
        return DETAIL


def test_resume_partial_collection_and_utf8_csv(tmp_path):
    client = FakeClient()
    out = tmp_path / "books.csv"
    assert not crawler.run(client, out, "001001003", 1, 1, 2)
    with out.open(encoding="utf-8-sig", newline="") as f:
        assert [r["product_id"] for r in csv.DictReader(f)] == ["1"]
    assert crawler.run(client, out, "001001003", 0, 0, 2)
    with out.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["product_id"] for r in rows] == ["1", "2", "3", "4"]
    assert rows[0]["title"] == "테스트 AI"
    assert len(client.detail_calls) == 4
    state = json.loads(out.with_suffix(".state.json").read_text(encoding="utf-8"))
    assert state["discovery_complete"]


def test_repeated_page_does_not_claim_completion(tmp_path):
    class Repeating(FakeClient):
        def get(self, path, params=None):
            if "Contents" in path:
                return listing([1, 2], 5)
            return super().get(path, params)

    with pytest.raises(ValueError, match="반복"):
        crawler.run(Repeating(), tmp_path / "books.csv", "001001003", 0, 0, 2)


def test_detail_failure_is_retried_on_resume(tmp_path):
    class Broken(FakeClient):
        def get(self, path, params=None):
            if "/goods/" in path:
                raise ValueError("일시 오류")
            return super().get(path, params)

    out = tmp_path / "books.csv"
    assert not crawler.run(Broken(), out, "001001003", 1, 1, 2)
    client = FakeClient()
    assert crawler.run(client, out, "001001003", 0, 0, 2)
    assert len(client.detail_calls) == 4


def test_overlapping_pages_are_not_silently_complete(tmp_path):
    class Overlap(FakeClient):
        def get(self, path, params=None):
            if "Contents" in path:
                return listing([1, 2] if params["page"] == 1 else [2, 3], 4)
            return super().get(path, params)

    with pytest.raises(ValueError, match="중복"):
        crawler.run(Overlap(), tmp_path / "books.csv", "001001003", 0, 0, 2)


def test_parallel_fetch_overlaps_and_propagates_blocking():
    barrier = threading.Barrier(2)

    class Parallel(FakeClient):
        def get(self, path, params=None):
            barrier.wait(timeout=5)
            return DETAIL

    results = list(crawler.detail_results(Parallel(), ["1", "2"], workers=2))
    assert [product_id for product_id, _, _ in results] == ["1", "2"]
    assert all(row["description"] and error is None for _, row, error in results)

    class Blocked(FakeClient):
        def get(self, path, params=None):
            raise crawler.BlockedError("접근 제한")

    with pytest.raises(crawler.BlockedError):
        list(crawler.detail_results(Blocked(), ["1", "2"], workers=2))
