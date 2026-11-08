"""예스24 IT 모바일 공개 목록·상세 정보를 CSV로 수집한다."""

import argparse
import csv
import json
import logging
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from urllib.parse import urlencode
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://www.yes24.com"
CATEGORY = "001001003"
USER_AGENT = "Class3BookResearch/1.0"
KST = timezone(timedelta(hours=9))
COLUMNS = [
    "product_id",
    "product_url",
    "title",
    "subtitle",
    "contributors",
    "publisher",
    "publication_date",
    "publication_date_raw",
    "isbn13",
    "isbn10",
    "categories",
    "page_count",
    "weight_g",
    "dimensions",
    "list_price",
    "sale_price",
    "discount_rate",
    "sales_index",
    "average_rating",
    "paper_review_count",
    "ebook_review_count",
    "paper_short_review_count",
    "ebook_short_review_count",
    "paper_star_count",
    "ebook_star_count",
    "description",
    "table_of_contents",
    "cover_url",
    "collected_at",
]


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def number(value, pattern=r"([\d,]+)", decimal=False):
    match = re.search(pattern, value)
    if not match:
        return ""
    raw = match.group(1).replace(",", "")
    return float(raw) if decimal else int(raw)


def parse_children(html, parent):
    soup = BeautifulSoup(html, "html.parser")
    return sorted(
        {
            node["id"][8:]
            for node in soup.select('li[id^="category"]')
            if re.fullmatch(re.escape(parent) + r"\d{3}", node["id"][8:])
        }
    )


def parse_listing(html):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#yesNewList")
    total = number(text(soup.select_one(".cateGoodsSecTit")))
    if container is None or total == "":
        raise ValueError(
            "목록 구조를 인식하지 못했습니다. HTML/차단 여부를 확인하세요."
        )
    ids = []
    for node in container.select("li[data-goods-no]"):
        product_id = node.get("data-goods-no", "")
        if product_id.isdigit() and product_id not in ids:
            ids.append(product_id)
    if total and not ids:
        raise ValueError("상품 수는 있으나 목록이 비어 있습니다.")
    return ids, total


def parse_detail(html, product_id):
    soup = BeautifulSoup(html, "html.parser")
    title = text(soup.select_one(".gd_infoTop .gd_name"))
    if not title:
        raise ValueError(f"{product_id}: 도서 제목을 찾지 못했습니다.")
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        product_id=product_id,
        product_url=f"{BASE}/product/goods/{product_id}",
        title=title,
        subtitle=text(soup.select_one(".gd_infoTop .gd_nameE")),
        contributors=text(soup.select_one(".gd_infoTop .gd_auth")),
        publisher=text(soup.select_one(".gd_infoTop .gd_pub")),
        collected_at=datetime.now(KST).isoformat(timespec="seconds"),
    )
    specs = {}
    for tr in soup.select("#infoset_specific tr"):
        specs[text(tr.select_one("th"))] = text(tr.select_one("td"))
    raw_date = specs.get("발행일") or text(soup.select_one(".gd_infoTop .gd_date"))
    row["publication_date_raw"] = raw_date
    match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", raw_date)
    if match:
        row["publication_date"] = datetime(*map(int, match.groups())).date().isoformat()
    row["isbn13"] = specs.get("ISBN13", "")
    row["isbn10"] = specs.get("ISBN10", "")
    physical = specs.get("쪽수, 무게, 크기", "")
    row["page_count"] = number(physical, r"([\d,]+)쪽")
    row["weight_g"] = number(physical, r"([\d,]+)g")
    row["dimensions"] = next((x.strip() for x in physical.split("|") if "mm" in x), "")
    category_section = soup.select_one("#infoset_goodsCate")
    category_list = category_section
    if category_section:
        for heading in category_section.select("dt"):
            if text(heading) == "카테고리 분류":
                category_list = heading.find_next_sibling("dd")
                break
    row["categories"] = json.dumps(
        [
            " > ".join(text(a) for a in li.select("a"))
            for li in (category_list.select("li") if category_list else [])
            if li.select("a")
        ],
        ensure_ascii=False,
    )
    for tr in soup.select(".gd_infoTb tr"):
        label, value = text(tr.select_one("th")), text(tr.select_one("td"))
        if label == "정가":
            row["list_price"] = number(value)
        elif label == "판매가":
            row["sale_price"] = number(value)
            row["discount_rate"] = number(value, r"([\d.]+)\s*%", decimal=True)
    row["sales_index"] = number(
        text(soup.select_one(".gd_sellNum")), r"판매지수\s*([\d,]+)"
    )
    row["average_rating"] = number(
        text(soup.select_one(".gd_rating .yes_b")), r"([\d.]+)", decimal=True
    )
    for medium, prefix in [("종이책", "paper"), ("eBook", "ebook")]:
        for label, suffix in [
            ("리뷰", "review_count"),
            ("한줄평", "short_review_count"),
            ("사락 별점", "star_count"),
        ]:
            row[f"{prefix}_{suffix}"] = number(
                text(soup.select_one(".moreRatingLi")),
                re.escape(f"{medium} {label}") + r"\s*\(([\d,]+)건\)",
            )
    for field, selector in [
        ("description", "#infoset_introduce"),
        ("table_of_contents", "#infoset_toc"),
    ]:
        node = soup.select_one(f"{selector} .infoWrap_txtInner")
        if node is None:
            node = soup.select_one(f"{selector} .infoWrap_txt")
        hidden = soup.select_one(f"{selector} textarea.txtContentText")
        if hidden:
            node = BeautifulSoup(hidden.get_text(), "html.parser")
        if node:
            for unwanted in node.select("script, style"):
                unwanted.decompose()
            row[field] = node.get_text("\n", strip=True)
    cover = soup.select_one('meta[property="og:image"]')
    row["cover_url"] = cover.get("content", "") if cover else ""
    return row


class BlockedError(RuntimeError):
    """차단 또는 접근 정책 변경 시 수집을 멈춘다."""


class Client:
    def __init__(self, delay=2.0):
        self.delay = delay
        self.last_request = 0.0
        self.rate_lock = threading.Lock()
        self.stopped = threading.Event()
        self.local = threading.local()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.robots = RobotFileParser()
        response = self.session.get(f"{BASE}/robots.txt", timeout=30)
        response.raise_for_status()
        self.robots.parse(response.text.splitlines())

    def get(self, path, params=None):
        url = BASE + path
        if params:
            url += "?" + urlencode(params)
        if not self.robots.can_fetch(USER_AGENT, url):
            self.stopped.set()
            raise BlockedError(f"robots.txt에서 제한한 경로입니다: {path}")
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers.update({"User-Agent": USER_AGENT})
        for attempt in range(3):
            # 모든 스레드가 같은 요청 간격을 공유한다. 병렬 실행도 요청률을 늘리지 않는다.
            with self.rate_lock:
                if self.stopped.wait(
                    max(0, self.delay - (time.monotonic() - self.last_request))
                ):
                    raise BlockedError(
                        "다른 요청에서 접근 제한이 감지되어 중단했습니다."
                    )
                self.last_request = time.monotonic()
            try:
                response = self.local.session.get(
                    url, timeout=30, allow_redirects=False
                )
                if response.status_code in (401, 403, 429) or response.is_redirect:
                    self.stopped.set()
                    raise BlockedError(
                        f"접근 제한/리다이렉트: HTTP {response.status_code} {path}"
                    )
                response.raise_for_status()
                # 현재 페이지는 UTF-8이다. BeautifulSoup의 meta 기반 오판을 피한다.
                response.encoding = (
                    requests.utils.get_encoding_from_headers(response.headers)
                    or "utf-8"
                )
                return response.text
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("응답 없음")


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            logging.warning("체크포인트 교체 접근 거부. 잠시 후 재시도: %s", path)
            time.sleep(min(0.5 * 2**attempt, 4))


def detail_results(client, product_ids, workers):
    """작은 배치만 예약하고 CSV 쓰기는 호출 스레드 한 곳에서 수행한다."""

    def fetch(product_id):
        try:
            return (
                product_id,
                parse_detail(client.get(f"/product/goods/{product_id}"), product_id),
                None,
            )
        except (requests.RequestException, ValueError) as exc:
            return product_id, None, exc

    iterator = iter(product_ids)
    if workers == 1:
        for product_id in iterator:
            yield fetch(product_id)
        return
    with ThreadPoolExecutor(max_workers=workers) as executor:
        while batch := list(islice(iterator, workers)):
            yield from executor.map(fetch, batch)


def run(
    client,
    output,
    category=CATEGORY,
    max_pages=0,
    max_books=0,
    page_size=120,
    workers=1,
    details_only=False,
):
    """0 한도는 무제한. 반환값 True는 발견·상세 수집 모두 완료한 경우다."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    state_path = output.with_suffix(".state.json")
    state = {
        "version": 1,
        "category": category,
        "page_size": page_size,
        "queue": [category],
        "visited": [],
        "current": None,
        "products": [],
        "discovery_complete": False,
    }
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state["category"], state["page_size"], state["version"]) != (
            category,
            page_size,
            1,
        ):
            raise ValueError(
                "재개 시 카테고리와 page-size를 바꿀 수 없습니다. 다른 --out을 사용하세요."
            )
    elif details_only:
        raise ValueError("--details-only에는 기존 .state.json이 필요합니다.")
    elif output.exists() and output.stat().st_size:
        raise ValueError(
            "CSV만 존재합니다. 체크포인트를 복원하거나 다른 --out을 사용하세요."
        )
    saved = set()
    if output.exists() and output.stat().st_size:
        with output.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                raise ValueError("기존 CSV 스키마가 다릅니다.")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(
                        "CSV의 마지막 행이 손상되었습니다. 복구 후 재실행하세요."
                    )
                if row["product_id"] in saved:
                    raise ValueError("기존 CSV에 중복 상품 ID가 있습니다.")
                saved.add(row["product_id"])
    page_count = 0
    known = set(state["products"])
    while (
        not details_only
        and not state["discovery_complete"]
        and (not max_pages or page_count < max_pages)
    ):
        if state["current"] is None:
            if not state["queue"]:
                state["discovery_complete"] = True
                save_state(state_path, state)
                break
            code = state["queue"][0]
            children = parse_children(
                client.get(
                    "/product/category/CategoryProductSubCategory", {"dispNo": code}
                ),
                code,
            )
            for child in children:
                if child not in state["visited"] and child not in state["queue"]:
                    state["queue"].append(child)
            state["current"] = {
                "code": code,
                "page": 1,
                "seen": [],
                "count": 0,
                "total": None,
            }
            save_state(state_path, state)
        current = state["current"]
        html = client.get(
            "/product/category/CategoryProductContents",
            {
                "dispNo": current["code"],
                "order": "SINDEX_ONLY",
                "page": current["page"],
                "size": page_size,
                "statGbYn": "N",
                "addOptionTp": 0,
                "viewMode": "",
                "_options": "",
                "directDelvYn": "",
                "usedTp": 0,
                "elemNo": 0,
                "elemSeq": 0,
                "seriesNumber": 0,
            },
        )
        ids, total = parse_listing(html)
        if current["total"] is not None and total != current["total"]:
            raise ValueError(
                "수집 중 카테고리 상품 수가 바뀌었습니다. 새 출력 경로로 다시 수집하세요."
            )
        if ids and not set(ids).difference(current["seen"]):
            raise ValueError(
                "목록 페이지가 반복됩니다. 전체 수집으로 처리하지 않습니다."
            )
        if set(ids).intersection(current["seen"]):
            raise ValueError(
                "페이지 사이에 중복 상품이 있습니다. 목록 순서 변동을 확인하세요."
            )
        for product_id in ids:
            if product_id not in known:
                state["products"].append(product_id)
                known.add(product_id)
        current["total"] = total
        current["count"] += len(ids)
        current["seen"] = list(set(current["seen"]) | set(ids))
        current["page"] += 1
        page_count += 1
        logging.info(
            "목록 %s: %s/%s, 고유 상품 %s",
            current["code"],
            current["count"],
            total,
            len(known),
        )
        if current["count"] >= total:
            state["visited"].append(current["code"])
            state["queue"].pop(0)
            state["current"] = None
            state["discovery_complete"] = not state["queue"]
        save_state(state_path, state)
    errors_path = output.with_suffix(".errors.jsonl")
    pending = (
        product_id for product_id in state["products"] if product_id not in saved
    )
    if max_books:
        pending = islice(pending, max_books)
    with output.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if handle.tell() == 0:
            writer.writeheader()
        try:
            for product_id, row, exc in detail_results(client, pending, workers):
                if exc is not None:
                    with errors_path.open("a", encoding="utf-8") as errors:
                        errors.write(
                            json.dumps(
                                {
                                    "product_id": product_id,
                                    "error": str(exc),
                                    "at": datetime.now(KST).isoformat(),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                    logging.error("상세 실패 %s: %s", product_id, exc)
                    continue
                writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
                saved.add(product_id)
                logging.info("상세 저장 %s (%s/%s)", product_id, len(saved), len(known))
        finally:
            logging.info(
                "CSV: %s / 저장 %s건 / 발견 %s건", output, len(saved), len(known)
            )
    complete = state["discovery_complete"] and known.issubset(saved)
    logging.info(
        "상태: %s", "수집 완료" if complete else "부분 수집: 같은 명령으로 재개 가능"
    )
    return complete


LIST_COLUMNS = [
    "product_id",
    "product_url",
    "title",
    "subtitle",
    "contributors",
    "publisher",
    "publication_month",
    "publication_date_raw",
    "list_price",
    "sale_price",
    "discount_rate",
    "points",
    "average_rating",
    "rating_count",
    "sales_index",
    "cover_url",
    "source_category",
    "collected_at",
]


def parse_list_rows(html, category):
    ids, total = parse_listing(html)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for product_id in ids:
        node = soup.select_one(f'#yesNewList li[data-goods-no="{product_id}"]')
        row = dict.fromkeys(LIST_COLUMNS, "")
        row.update(
            product_id=product_id,
            product_url=f"{BASE}/product/goods/{product_id}",
            source_category=category,
            collected_at=datetime.now(KST).isoformat(timespec="seconds"),
        )
        for field, selector in {
            "title": ".gd_name",
            "subtitle": ".gd_nameE",
            "contributors": ".info_auth",
            "publisher": ".info_pub",
            "publication_date_raw": ".info_date",
        }.items():
            row[field] = text(node.select_one(selector))
        if not row["title"]:
            raise ValueError(f"목록 제목 누락: {product_id}")
        match = re.search(r"(\d{4})년\s*(\d{1,2})월", row["publication_date_raw"])
        if match:
            row["publication_month"] = f"{int(match[1]):04d}-{int(match[2]):02d}"
        for field, selector in {
            "list_price": ".info_price .dash",
            "sale_price": ".info_price strong.txt_num",
            "points": ".yPoint",
            "rating_count": ".rating_rvCount",
        }.items():
            row[field] = number(text(node.select_one(selector)))
        row["discount_rate"] = number(
            text(node.select_one(".txt_sale")), r"([\d.]+)", decimal=True
        )
        row["average_rating"] = number(
            text(node.select_one(".rating_grade .yes_b")), r"([\d.]+)", decimal=True
        )
        row["sales_index"] = number(
            text(node.select_one(".info_rating")), r"판매지수\s*([\d,]+)"
        )
        image = node.select_one(".item_img img")
        if image:
            row["cover_url"] = image.get("data-original") or image.get("src", "")
        rows.append(row)
    return rows, total


def run_list(client, output, category=CATEGORY, max_pages=0, page_size=120):
    """목록 전용 CSV와 독립 체크포인트. 상세 경로는 호출하지 않는다."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    state_path = output.with_suffix(".list.state.json")
    state = {
        "mode": "list",
        "category": category,
        "page_size": page_size,
        "queue": [category],
        "visited": [],
        "current": None,
    }
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("mode"), state["category"], state["page_size"]) != (
            "list",
            category,
            page_size,
        ):
            raise ValueError("목록 체크포인트 설정 불일치")
    elif output.exists() and output.stat().st_size:
        raise ValueError(
            "기존 CSV가 있습니다. 다른 --out 또는 목록 체크포인트가 필요합니다."
        )
    saved = set()
    if output.exists() and output.stat().st_size:
        with output.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != LIST_COLUMNS:
                raise ValueError(
                    "목록 CSV 스키마 불일치. 상세 CSV와 별도 경로를 사용하세요."
                )
            for row in reader:
                if (
                    None in row
                    or any(v is None for v in row.values())
                    or row["product_id"] in saved
                ):
                    raise ValueError("목록 CSV 손상 또는 중복")
                saved.add(row["product_id"])
    pages = 0
    with output.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIST_COLUMNS)
        if handle.tell() == 0:
            writer.writeheader()
        while state["queue"] and (not max_pages or pages < max_pages):
            if state["current"] is None:
                code = state["queue"][0]
                children = parse_children(
                    client.get(
                        "/product/category/CategoryProductSubCategory", {"dispNo": code}
                    ),
                    code,
                )
                state["queue"].extend(
                    c
                    for c in children
                    if c not in state["queue"] and c not in state["visited"]
                )
                state["current"] = {"code": code, "page": 1, "seen": [], "total": None}
                save_state(state_path, state)
            current = state["current"]
            rows, total = parse_list_rows(
                client.get(
                    "/product/category/CategoryProductContents",
                    {
                        "dispNo": current["code"],
                        "order": "SINDEX_ONLY",
                        "page": current["page"],
                        "size": page_size,
                        "statGbYn": "N",
                        "addOptionTp": 0,
                        "viewMode": "",
                        "_options": "",
                        "directDelvYn": "",
                        "usedTp": 0,
                        "elemNo": 0,
                        "elemSeq": 0,
                        "seriesNumber": 0,
                    },
                ),
                current["code"],
            )
            ids = [r["product_id"] for r in rows]
            if current["total"] is not None and total != current["total"]:
                raise ValueError("목록 상품 수 변경. 새 출력 경로로 수집하세요.")
            if set(ids).intersection(current["seen"]):
                raise ValueError("목록 페이지 반복 또는 중복")
            for row in rows:
                if row["product_id"] not in saved:
                    writer.writerow(row)
                    saved.add(row["product_id"])
            handle.flush()
            os.fsync(handle.fileno())
            current["seen"].extend(ids)
            current["total"] = total
            current["page"] += 1
            if len(current["seen"]) >= total:
                state["visited"].append(current["code"])
                state["queue"].pop(0)
                state["current"] = None
            save_state(state_path, state)
            pages += 1
            logging.info("목록 CSV 저장 %s건 / 이번 실행 %s페이지", len(saved), pages)
    complete = not state["queue"]
    logging.info(
        "목록 %s: %s (%s건)", "완료" if complete else "부분 수집", output, len(saved)
    )
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
    )
    parser.add_argument("--category", default=CATEGORY)
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="상세 요청 없이 목록 정보만 별도 CSV에 저장",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, 5),
        default=1,
        help="상세 수집 동시 요청 수 (1~4). 요청 간격은 전체가 공유",
    )
    parser.add_argument(
        "--details-only",
        action="store_true",
        help="기존 체크포인트에서 발견한 상품만 상세 수집. 목록 탐색 생략",
    )
    parser.add_argument(
        "--delay", type=float, default=2.0, help="요청 사이 최소 대기 초 (최소 1)"
    )
    parser.add_argument("--page-size", type=int, choices=[24, 40, 80, 120], default=120)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="이번 실행의 전체 목록 페이지 한도. 0은 무제한",
    )
    parser.add_argument(
        "--max-books",
        type=int,
        default=0,
        help="이번 실행의 상세 요청 도서 한도. 0은 무제한",
    )
    args = parser.parse_args()
    if args.list_only and (args.details_only or args.max_books or args.workers != 1):
        parser.error(
            "--list-only는 --details-only, --max-books, --workers와 함께 사용할 수 없습니다."
        )
    if args.out is None:
        name = "yes24_it_mobile_list.csv" if args.list_only else "yes24_it_mobile.csv"
        args.out = Path(__file__).parent / "data" / name
    if not re.fullmatch(CATEGORY + r"(?:\d{3})*", args.category):
        parser.error("IT 모바일 또는 그 하위 카테고리 코드만 지원합니다.")
    if args.delay < 1 or args.max_pages < 0 or args.max_books < 0:
        parser.error("delay는 1 이상, 수집 한도는 0 이상이어야 합니다.")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        if args.list_only:
            complete = run_list(
                Client(args.delay),
                args.out,
                args.category,
                args.max_pages,
                args.page_size,
            )
            return 0 if complete else 2
        complete = run(
            Client(args.delay),
            args.out,
            args.category,
            args.max_pages,
            args.max_books,
            args.page_size,
            args.workers,
            args.details_only,
        )
        return 0 if complete else 2
    except PermissionError as exc:
        logging.error(
            "파일 접근 거부로 수집 중단: %s. 파일을 사용하는 프로그램과 중복 실행을 "
            "확인하세요. CSV와 체크포인트를 보존하고 같은 명령으로 재개하세요.",
            exc,
        )
        return 1
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        logging.error("수집 중단: %s", exc)
        return 1
    except KeyboardInterrupt:
        logging.warning("사용자 중단. 같은 명령으로 재개할 수 있습니다.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
