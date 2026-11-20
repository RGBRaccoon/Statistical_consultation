"""승인된 기간·10개 컬럼·단일 세션과 두 단계 무작위 대기로 상세 정보를 수집한다."""

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://www.yes24.com"
ROOT = Path(__file__).resolve().parent
USER_AGENT = "Class3BookResearch/2.0"
REQUEST_DELAY = (2, 5)
BATCH_SIZE = (200, 300)
BATCH_REST = (180, 300)
SNAPSHOT = ROOT / "data/yes24_details_2010_2026.resume.jsonl"
COLUMNS = [
    "product_id",
    "product_url",
    "title",
    "subtitle",
    "publication_date",
    "publication_date_raw",
    "isbn13",
    "categories",
    "description",
    "table_of_contents",
]


class BlockedError(RuntimeError):
    """접근 제한·robots 정책 변경으로 자동 요청을 중단한다."""


class StructureError(ValueError):
    """정상 도서 페이지인지 확인할 수 없거나 구조가 바뀐 경우다."""


class NotFoundError(RuntimeError):
    pass


class AdultCheckError(RuntimeError):
    """인증이 필요한 개별 상품은 인증 경로를 요청하지 않고 검토 대상으로 남긴다."""


def is_adult_check_redirect(status, path, location):
    product = re.fullmatch(r"/product/goods/(\d+)", path, re.I)
    redirect = urlparse(location)
    return bool(
        status in (302, 303, 307, 308)
        and product
        and redirect.scheme.lower() in ("", "https")
        and redirect.netloc.lower() in ("", "www.yes24.com")
        and redirect.path.lower() == f"/product/base/adultcheck/{product.group(1)}"
    )


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def select_targets(rows):
    selected = [
        row
        for row in rows
        if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", row.get("publication_month", ""))
        and "2010-01" <= row["publication_month"] <= "2026-11"
    ]
    ids = [row["product_id"] for row in selected]
    if len(set(ids)) != len(ids):
        raise ValueError("대상 상품 ID 중복")
    if any(not product_id.isdigit() for product_id in ids):
        raise ValueError("상품 ID 형식 오류")
    return selected


def target_fingerprint(rows):
    payload = sorted(
        (r["product_id"], r["publication_month"], r.get("source_category", ""))
        for r in rows
    )
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def partition_targets(rows, part, parts):
    if parts < 1 or not 1 <= part <= parts:
        raise ValueError("파트 번호는 1 이상 전체 파트 수 이하여야 합니다.")
    return [
        row
        for row in rows
        if int.from_bytes(
            hashlib.sha256(row["product_id"].encode()).digest()[:8], "big"
        )
        % parts
        + 1
        == part
    ]


def export_snapshot(source, output):
    """중단된 통합 작업의 결과·분류·결측 사유를 다른 PC에 옮길 수 있게 저장한다."""
    connection = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        meta = {
            r["key"]: json.loads(r["value"])
            for r in connection.execute("SELECT * FROM meta")
        }
        if meta.get("run_status") in ("blocked", "structure_error", "running"):
            raise BlockedError(
                "기존 수집을 정상적으로 저장·중단한 뒤 파트를 준비하세요. 접근 제한 상태를 분할로 초기화하지 않습니다."
            )
        nodes = [
            {
                "code": r["code"],
                "name": r["name"],
                "parent_code": r["parent_code"],
                "path": json.loads(r["path_json"]),
            }
            for r in connection.execute("SELECT * FROM categories ORDER BY code")
        ]
        header = {
            "type": "metadata",
            "version": 1,
            "columns": COLUMNS,
            "source_fingerprint": meta.get("target_fingerprint"),
            "source_status": meta.get("run_status"),
            "created_at": now_iso(),
            "pilot_passed": meta.get("pilot_passed", False),
            "not_before": (meta.get("pacing") or {}).get("next_at", 0),
            "categories": nodes,
        }
        output = Path(output)
        temporary = output.with_suffix(".tmp")
        count = 0
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(header, ensure_ascii=False) + "\n")
            for row in connection.execute(
                "SELECT status,data_json,metadata_json FROM jobs WHERE data_json IS NOT NULL ORDER BY position"
            ):
                handle.write(
                    json.dumps(
                        {
                            "type": "result",
                            "status": row["status"],
                            "data": json.loads(row["data_json"]),
                            "metadata": json.loads(row["metadata_json"]),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                count += 1
        temporary.replace(output)
        return count
    finally:
        connection.close()


def node_text(node):
    return node.get_text(" ", strip=True) if node else ""


def category_code(href):
    parsed = urlparse(href)
    match = re.search(r"/category/(?:display|more)/(\d+)", parsed.path, re.I)
    if match:
        return match.group(1)
    params = {key.lower(): value for key, value in parse_qs(parsed.query).items()}
    for key in ("dispno", "categorynumber", "cateno"):
        value = params.get(key, [""])[0]
        if value.isdigit():
            return value
    return ""


def parse_categories(soup):
    section = soup.select_one("#infoset_goodsCate")
    if section:
        headings = section.select("dt")
        if headings:
            section = next(
                (
                    heading.find_next_sibling("dd")
                    for heading in headings
                    if node_text(heading) == "카테고리 분류"
                ),
                None,
            )
    categories, nodes = [], []
    for item in section.select("li") if section else []:
        anchors = [a for a in item.select("a") if node_text(a)]
        if not anchors:
            continue
        names, parent = [], ""
        for anchor in anchors:
            names.append(node_text(anchor))
            code = category_code(anchor.get("href", ""))
            if code:
                nodes.append(
                    {
                        "code": code,
                        "name": names[-1],
                        "parent_code": parent,
                        "path": list(names),
                    }
                )
                parent = code
        category = {
            "code": category_code(anchors[-1].get("href", "")),
            "name": names[-1],
            "path": names,
        }
        if category not in categories:
            categories.append(category)
    return categories, nodes


def extract_text(soup, selector):
    section = soup.select_one(selector)
    if section is None:
        return "", "unknown"
    hidden = section.select_one("textarea.txtContentText")
    node = (
        BeautifulSoup(hidden.get_text(), "html.parser")
        if hidden
        else (
            section.select_one(".infoWrap_txtInner")
            or section.select_one(".infoWrap_txt")
        )
    )
    if node is None:
        # 빈 섹션을 원문 미등록으로 단정하지 않는다.
        return "", "unknown"
    for unwanted in node.select("script, style"):
        unwanted.decompose()
    value = node.get_text("\n", strip=True)
    if re.search(r"(?:등록된|등록된\s*책소개가|등록된\s*목차가).*없습니다", value):
        return "", "source_absent"
    return value, "present" if value else "unknown"


def parse_detail(html, product_id):
    soup = BeautifulSoup(html, "html.parser")
    title = node_text(soup.select_one(".gd_infoTop .gd_name"))
    if not title:
        raise StructureError(f"{product_id}: 도서 제목을 확인할 수 없습니다.")
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        product_id=product_id,
        product_url=f"{BASE}/product/goods/{product_id}",
        title=title,
        subtitle=node_text(soup.select_one(".gd_infoTop .gd_nameE")),
    )
    specs = {
        node_text(tr.select_one("th")): node_text(tr.select_one("td"))
        for tr in soup.select("#infoset_specific tr")
    }
    raw_date = specs.get("발행일") or node_text(soup.select_one(".gd_infoTop .gd_date"))
    row["publication_date_raw"] = raw_date
    match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", raw_date)
    if match:
        try:
            row["publication_date"] = (
                datetime(*map(int, match.groups())).date().isoformat()
            )
        except ValueError:
            pass
    row["isbn13"] = specs.get("ISBN13", "")
    categories, nodes = parse_categories(soup)
    row["categories"] = json.dumps(categories, ensure_ascii=False)
    statuses = {
        "categories": "present"
        if categories and all(c["code"] for c in categories)
        else "unknown",
        "publication_date": "present" if row["publication_date"] else "unknown",
        "isbn13": "present" if row["isbn13"] else "unknown",
    }
    for field, selector in (
        ("description", "#infoset_introduce"),
        ("table_of_contents", "#infoset_toc"),
    ):
        row[field], statuses[field] = extract_text(soup, selector)
    meta = {"field_status": statuses, "collected_at": now_iso(), "source": "detail"}
    return row, meta, nodes


class Store:
    def __init__(self, connection):
        self.connection = connection
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                product_id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                publication_month TEXT NOT NULL, source_category TEXT NOT NULL,
                pilot INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                data_json TEXT, metadata_json TEXT
            );
            CREATE TABLE IF NOT EXISTS categories (
                code TEXT PRIMARY KEY, name TEXT NOT NULL, parent_code TEXT NOT NULL,
                path_json TEXT NOT NULL
            );
        """)

    def get_meta(self, key, default=None):
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def initialize(self, targets, rng, pilot_ids=()):
        fingerprint = target_fingerprint(targets)
        existing = self.get_meta("target_fingerprint")
        if existing:
            if existing != fingerprint:
                raise ValueError(
                    "대상 목록이 기존 작업과 다릅니다. 별도 출력 경로를 사용하세요."
                )
            return
        shuffled = list(targets)
        rng.shuffle(shuffled)
        order = {product_id: i for i, product_id in enumerate(pilot_ids)}
        shuffled.sort(key=lambda r: order.get(r["product_id"], len(order)))
        with self.connection:
            for position, row in enumerate(shuffled):
                self.connection.execute(
                    "INSERT INTO jobs(product_id,position,publication_month,source_category,pilot) VALUES (?,?,?,?,?)",
                    (
                        row["product_id"],
                        position,
                        row["publication_month"],
                        row.get("source_category", ""),
                        int(row["product_id"] in order),
                    ),
                )
            self.connection.execute(
                "INSERT INTO meta VALUES (?,?)",
                ("target_fingerprint", json.dumps(fingerprint)),
            )

    def save_nodes(self, nodes):
        with self.connection:
            for node in nodes:
                existing = self.connection.execute(
                    "SELECT name,path_json FROM categories WHERE code=?",
                    (node["code"],),
                ).fetchone()
                if existing and (
                    existing["name"] != node["name"]
                    or json.loads(existing["path_json"]) != node["path"]
                ):
                    raise StructureError(f"분류 코드/경로 충돌: {node['code']}")
                self.connection.execute(
                    "INSERT OR IGNORE INTO categories VALUES (?,?,?,?)",
                    (
                        node["code"],
                        node["name"],
                        node["parent_code"],
                        json.dumps(node["path"], ensure_ascii=False),
                    ),
                )

    def save_result(self, product_id, data, metadata, nodes=()):
        self.save_nodes(nodes)
        job = self.connection.execute(
            "SELECT publication_month FROM jobs WHERE product_id=?", (product_id,)
        ).fetchone()
        date = data["publication_date"]
        metadata["publication_month_changed"] = bool(date and date[:7] != job[0])
        metadata["future_publication"] = bool(
            date and date > datetime.now().date().isoformat()
        )
        needs_review = (
            any(s == "unknown" for s in metadata["field_status"].values())
            or metadata["publication_month_changed"]
        )
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status=?,data_json=?,metadata_json=? WHERE product_id=?",
                (
                    "review" if needs_review else "complete",
                    json.dumps(data, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    product_id,
                ),
            )

    def mark_error(self, product_id, status, error):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status=?,metadata_json=? WHERE product_id=?",
                (
                    status,
                    json.dumps(
                        {"error": str(error), "collected_at": now_iso()},
                        ensure_ascii=False,
                    ),
                    product_id,
                ),
            )

    def counts(self):
        result = Counter(
            {
                r[0]: r[1]
                for r in self.connection.execute(
                    "SELECT status,count(*) FROM jobs GROUP BY status"
                )
            }
        )
        result["reused"] = self.connection.execute(
            "SELECT count(*) FROM jobs WHERE json_extract(metadata_json,'$.source') IN ('legacy','snapshot')"
        ).fetchone()[0]
        result["total"] = self.connection.execute(
            "SELECT count(*) FROM jobs"
        ).fetchone()[0]
        return result

    def phase_counts(self):
        result = {}
        for name, condition in (
            ("2010_2016", "publication_month <= '2016-12'"),
            ("2017_2026", "publication_month > '2016-12'"),
        ):
            counts = Counter(
                {
                    key: 0
                    for key in ("pending", "complete", "review", "failed", "not_found")
                }
            )
            counts.update(
                dict(
                    self.connection.execute(
                        f"SELECT status,count(*) FROM jobs WHERE {condition} GROUP BY status"
                    )
                )
            )
            counts["total"] = sum(counts.values())
            result[name] = dict(counts)
        return result

    def import_snapshot(self, path, expected_fingerprint):
        if self.get_meta("run_status") in ("blocked", "structure_error"):
            raise BlockedError(
                "중단된 파트는 원인 확인 없이 재개 자료로 초기화하지 않습니다."
            )
        with Path(path).open(encoding="utf-8") as handle:
            header = json.loads(next(handle))
            if (
                header.get("type") != "metadata"
                or header.get("version") != 1
                or header.get("columns") != COLUMNS
            ):
                raise ValueError("재개 자료 형식이 맞지 않습니다.")
            if header.get("source_fingerprint") != expected_fingerprint:
                raise ValueError("재개 자료와 전체 수집 대상이 다릅니다.")
            if header.get("source_status") in ("blocked", "structure_error", "running"):
                raise BlockedError("재개 자료의 접근 제한/실행 중 상태를 확인하세요.")
            self.save_nodes(header["categories"])
            pending = {
                r[0]
                for r in self.connection.execute(
                    "SELECT product_id FROM jobs WHERE status='pending'"
                )
            }
            count = 0
            with self.connection:
                for line in handle:
                    item = json.loads(line)
                    data = item["data"]
                    if set(data) != set(COLUMNS) or item["status"] not in (
                        "complete",
                        "review",
                    ):
                        raise ValueError(
                            "재개 자료의 컬럼 또는 결과 상태가 맞지 않습니다."
                        )
                    if data["product_id"] not in pending:
                        continue
                    metadata = dict(item["metadata"])
                    metadata["original_source"] = metadata.get("source", "")
                    metadata["source"] = "snapshot"
                    self.connection.execute(
                        "UPDATE jobs SET status=?,data_json=?,metadata_json=? WHERE product_id=?",
                        (
                            item["status"],
                            json.dumps(data, ensure_ascii=False),
                            json.dumps(metadata, ensure_ascii=False),
                            data["product_id"],
                        ),
                    )
                    pending.remove(data["product_id"])
                    count += 1
            pacing = self.get_meta(
                "pacing", {"remaining": 0, "batch": 0, "requests": 0, "next_at": 0}
            )
            pacing["next_at"] = max(pacing["next_at"], header.get("not_before", 0))
            self.set_meta("pacing", pacing)
            if header.get("pilot_passed"):
                self.set_meta("pilot_passed", True)
            return count

    def reuse_legacy(self, rows):
        lookup = {}
        for node in self.connection.execute("SELECT * FROM categories"):
            key = tuple(json.loads(node["path_json"]))
            lookup.setdefault(key, []).append(node)
        saved = 0
        pending = {
            r[0]
            for r in self.connection.execute(
                "SELECT product_id FROM jobs WHERE status='pending' AND pilot=0"
            )
        }
        for row in rows:
            if row["product_id"] not in pending or not all(
                row.get(key)
                for key in (
                    "description",
                    "table_of_contents",
                    "publication_date",
                    "isbn13",
                )
            ):
                continue
            paths = [
                tuple(part.strip() for part in path.split(" > "))
                for path in json.loads(row["categories"] or "[]")
            ]
            if not paths or any(len(lookup.get(path, [])) != 1 for path in paths):
                continue
            data = {key: row.get(key, "") for key in COLUMNS}
            data["categories"] = json.dumps(
                [
                    {
                        "code": lookup[path][0]["code"],
                        "name": lookup[path][0]["name"],
                        "path": list(path),
                    }
                    for path in paths
                ],
                ensure_ascii=False,
            )
            self.save_result(
                row["product_id"],
                data,
                {
                    "field_status": {
                        k: "present"
                        for k in (
                            "categories",
                            "description",
                            "table_of_contents",
                            "publication_date",
                            "isbn13",
                        )
                    },
                    "source": "legacy",
                    "collected_at": row.get("collected_at", ""),
                    "imported_at": now_iso(),
                },
            )
            saved += 1
        return saved

    def export(self, output):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        for path, condition in (
            (output, "1=1"),
            (
                output.with_name(output.stem + ".phase1.csv"),
                "publication_month<='2016-12'",
            ),
        ):
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=COLUMNS)
                writer.writeheader()
                for row in self.connection.execute(
                    f"SELECT data_json FROM jobs WHERE data_json IS NOT NULL AND {condition} ORDER BY position"
                ):
                    writer.writerow(json.loads(row[0]))
            temporary.replace(path)
        lookup_path = output.with_name(output.stem + ".categories.csv")
        temporary = lookup_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["code", "name", "parent_code", "path"])
            writer.writerows(
                tuple(row)
                for row in self.connection.execute(
                    "SELECT code,name,parent_code,path_json FROM categories ORDER BY code"
                )
            )
        temporary.replace(lookup_path)
        source_path = output.with_name(output.stem + ".source_categories.csv")
        temporary = source_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["source_category", "category_name", "category_path", "target_count"]
            )
            writer.writerows(
                tuple(row)
                for row in self.connection.execute("""
                SELECT j.source_category,COALESCE(c.name,''),COALESCE(c.path_json,''),count(*)
                FROM jobs j LEFT JOIN categories c ON j.source_category=c.code
                GROUP BY j.source_category ORDER BY j.source_category
            """)
            )
        temporary.replace(source_path)
        summary = {
            "updated_at": now_iso(),
            "status": self.get_meta("run_status", "ready"),
            "counts": dict(self.counts()),
            "pacing": self.get_meta("pacing"),
            "last_request": self.get_meta("last_request"),
            "last_error": self.get_meta("last_error"),
            "pilot_passed": self.get_meta("pilot_passed", False),
            "partition": self.get_meta("partition", {"part": 1, "parts": 1}),
            "collection_through_year": self.get_meta("collection_through_year", 2016),
            "phases": self.phase_counts(),
            "pacing_settings": {
                "request_seconds": REQUEST_DELAY,
                "batch_requests": BATCH_SIZE,
                "batch_rest_seconds": BATCH_REST,
            },
        }
        status_path = output.with_suffix(".progress.json")
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(status_path)


class Pacer:
    def __init__(self, store, rng=None, clock=time):
        self.store, self.rng, self.clock = store, rng or random.SystemRandom(), clock
        self.minimum_delay = 0
        if not store.get_meta("pacing"):
            store.set_meta(
                "pacing", {"remaining": 0, "batch": 0, "requests": 0, "next_at": 0}
            )

    def before_request(self):
        state = self.store.get_meta("pacing")
        while (remaining := state["next_at"] - self.clock.time()) > 0:
            self.clock.sleep(min(remaining, 30))
        if state["remaining"] == 0:
            state["remaining"] = self.rng.randint(*BATCH_SIZE)
            state["batch"] += 1
            logging.info("묶음 %s 시작: %s회 요청", state["batch"], state["remaining"])
        state["remaining"] -= 1
        state["requests"] += 1
        # 요청 중 비정상 종료돼도 재시작 직후 요청이 몰리지 않도록 예약한다.
        state["next_at"] = (
            self.clock.time()
            + 30
            + max(REQUEST_DELAY[1], self.minimum_delay)
            + (BATCH_REST[1] if state["remaining"] == 0 else 0)
        )
        self.store.set_meta("pacing", state)

    def after_request(self, minimum_pause=0):
        state = self.store.get_meta("pacing")
        gap = max(self.minimum_delay, self.rng.uniform(*REQUEST_DELAY))
        rest = self.rng.uniform(*BATCH_REST) if state["remaining"] == 0 else 0
        state["next_at"] = self.clock.time() + max(gap + rest, minimum_pause)
        self.store.set_meta("pacing", state)
        if rest:
            logging.info("묶음 종료: 건별 %.1f초 + 추가 휴식 %.1f초", gap, rest)


def retry_after_seconds(value):
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    try:
        return max(0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return 0


class Client:
    def __init__(self, store, pacer, session=None):
        self.store, self.pacer = store, pacer
        self.session = session or requests.Session()
        if session is None:
            self.session.headers.update({"User-Agent": USER_AGENT})
        self.robots = None

    def fetch(self, path, params=None, check_robots=True):
        url = BASE + path
        policy_url = requests.Request("GET", url, params=params).prepare().url
        if check_robots and (
            self.robots is None or not self.robots.can_fetch(USER_AGENT, policy_url)
        ):
            raise BlockedError(f"robots.txt 정책상 요청 불가: {path}")
        for attempt in range(3):
            self.pacer.before_request()
            pause = 0
            try:
                response = self.session.get(
                    url, params=params, timeout=30, allow_redirects=False
                )
                pause = retry_after_seconds(response.headers.get("Retry-After", ""))
                self.store.set_meta(
                    "last_request",
                    {
                        "at": now_iso(),
                        "path": path,
                        "status": response.status_code,
                        "attempt": attempt + 1,
                        "redirect_host": urlparse(
                            response.headers.get("Location", "")
                        ).netloc,
                        "redirect_path": urlparse(
                            response.headers.get("Location", "")
                        ).path,
                    },
                )
                if is_adult_check_redirect(
                    response.status_code, path, response.headers.get("Location", "")
                ):
                    raise AdultCheckError(
                        f"{path}: 성인 인증 필요. 인증 페이지를 요청하지 않고 건너뜁니다."
                    )
                if response.status_code in (401, 403, 429) or response.is_redirect:
                    raise BlockedError(
                        f"HTTP {response.status_code}: {path}; 자동 재시도 중단"
                    )
                if response.status_code in (404, 410):
                    raise NotFoundError(f"HTTP {response.status_code}: {path}")
                response.raise_for_status()
                response.encoding = (
                    requests.utils.get_encoding_from_headers(response.headers)
                    or "utf-8"
                )
                return response.text
            except requests.RequestException as exc:
                if attempt == 2 or (
                    getattr(exc, "response", None) is not None
                    and exc.response.status_code < 500
                ):
                    raise
                pause = max(pause, 30 * 2**attempt)
                logging.warning("통신/서버 오류 %s/3: %s", attempt + 1, exc)
            finally:
                self.pacer.after_request(pause)
        raise RuntimeError("응답을 얻지 못했습니다.")

    def initialize_robots(self):
        try:
            html = self.fetch("/robots.txt", check_robots=False)
        except NotFoundError:
            html = ""
        self.robots = RobotFileParser()
        self.robots.parse(html.splitlines())
        self.pacer.minimum_delay = (
            self.robots.crawl_delay(USER_AGENT) or self.robots.crawl_delay("*") or 0
        )
        rate = self.robots.request_rate(USER_AGENT) or self.robots.request_rate("*")
        if rate:
            self.pacer.minimum_delay = max(
                self.pacer.minimum_delay, rate.seconds / rate.requests
            )


@contextmanager
def single_process_lock(path):
    handle = Path(path).open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def choose_pilot(targets, legacy, rng):
    selected = []
    for year in (2010, 2013, 2016, 2019, 2022, 2023, 2024, 2025, 2026):
        choices = [
            row["product_id"]
            for row in targets
            if row["publication_month"].startswith(str(year))
        ]
        if choices:
            selected.append(rng.choice(choices))
    ids = {r["product_id"] for r in targets}
    missing = [
        r["product_id"]
        for r in legacy
        if r["product_id"] in ids
        and r["product_id"] not in selected
        and (not r["description"] or not r["table_of_contents"])
    ]
    choices = missing or [
        r["product_id"] for r in targets if r["product_id"] not in selected
    ]
    if choices:
        selected.append(rng.choice(choices))
    return selected


def collect_source_categories(client, store, cache=None):
    missing = [
        r[0]
        for r in store.connection.execute("""
        SELECT DISTINCT source_category FROM jobs
        WHERE source_category NOT IN (SELECT code FROM categories)
    """)
    ]
    if not missing:
        return
    # 현재 목록의 14개 출처는 모두 IT 모바일의 바로 아래 분류다.
    html = client.fetch(
        "/product/category/CategoryProductSubCategory", {"dispNo": "001001003"}
    )
    if cache:
        cache.write_text(html, encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    nodes = []
    for node in soup.select('li[id^="category"]'):
        code = node.get("id", "")[8:]
        if not re.fullmatch(r"001001003\d{3}", code):
            continue
        anchor = node.select_one("a")
        name = node_text(anchor)
        if name:
            nodes.append(
                {
                    "code": code,
                    "name": name,
                    "parent_code": "001001003",
                    "path": ["국내도서", "IT 모바일", name],
                }
            )
    store.save_nodes(nodes)
    logging.info("출처 분류 한글명 %s개 확인", len(nodes))


def partition_output(base, part, parts):
    return (
        base if parts == 1 else base.with_name(f"{base.stem}.part{part}{base.suffix}")
    )


def next_job(store, through_year):
    return store.connection.execute(
        "SELECT * FROM jobs WHERE status='pending' AND publication_month<=? "
        "ORDER BY (publication_month>'2016-12'),position LIMIT 1",
        (f"{through_year}-12",),
    ).fetchone()


def finish_phase(store, through_year):
    counts = (
        store.phase_counts()["2010_2016"] if through_year == 2016 else store.counts()
    )
    review = any(counts[s] for s in ("review", "failed", "not_found"))
    status = "phase1_finished" if through_year == 2016 else "finished"
    store.set_meta("run_status", status + ("_with_review" if review else ""))
    if through_year == 2016:
        logging.info(
            "1차 목표 2010~2016년 미수집 0건. 확인 필요/실패 %s건. 2017년 이후는 --through-year 2026으로 별도 실행하세요.",
            sum(counts[s] for s in ("review", "failed", "not_found")),
        )


def prepare_parts(args):
    """네트워크 요청 없이 동일한 재개 자료와 두 개의 고정 작업 명단을 만든다."""
    targets = select_targets(read_csv(args.input))
    legacy = read_csv(args.legacy) if args.legacy.exists() else []
    with single_process_lock(args.output.parent / "yes24_details_2010_2026.lock"):
        saved = export_snapshot(args.output.with_suffix(".sqlite3"), args.snapshot)
        report = {"saved_results": saved, "target_total": len(targets), "parts": []}
        for part in (1, 2):
            owned = partition_targets(targets, part, 2)
            output = partition_output(args.output, part, 2)
            manifest = output.with_name(output.stem + ".targets.csv")
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "product_id",
                        "product_url",
                        "publication_month",
                        "source_category",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    {key: row.get(key, "") for key in writer.fieldnames}
                    for row in owned
                )
            store = Store(sqlite3.connect(output.with_suffix(".sqlite3")))
            try:
                store.initialize(
                    owned,
                    random.SystemRandom(),
                    choose_pilot(owned, legacy, random.SystemRandom()),
                )
                store.import_snapshot(args.snapshot, target_fingerprint(targets))
                store.set_meta("partition", {"part": part, "parts": 2})
                store.set_meta("collection_through_year", 2016)
                store.set_meta("run_status", "ready_partition")
                store.export(output)
                report["parts"].append(
                    {
                        "part": part,
                        "output": str(output),
                        "counts": dict(store.counts()),
                        "phases": store.phase_counts(),
                    }
                )
            finally:
                store.connection.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run(args):
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    # 두 파트도 한 PC에서는 하나만 실행한다. 이전 단일 수집기와도 같은 잠금을 사용한다.
    with single_process_lock(output.parent / "yes24_details_2010_2026.lock"):
        store = Store(sqlite3.connect(output.with_suffix(".sqlite3")))
        try:
            if (
                store.get_meta("run_status") in ("blocked", "structure_error")
                and not args.resume_after_stop
            ):
                logging.error(
                    "이전 접근 제한/구조 오류로 중단된 작업입니다. 원인 확인 후 --resume-after-stop으로 재개하세요."
                )
                return 1
            rng = random.SystemRandom()
            if args.resume_after_stop and store.get_meta("run_status") in (
                "blocked",
                "structure_error",
            ):
                store.set_meta("run_status", "resume_authorized")
            all_targets = select_targets(read_csv(args.input))
            targets = partition_targets(all_targets, args.part, args.parts)
            legacy = read_csv(args.legacy) if args.legacy.exists() else []
            store.initialize(targets, rng, choose_pilot(targets, legacy, rng))
            store.set_meta("partition", {"part": args.part, "parts": args.parts})
            store.set_meta("collection_through_year", args.through_year)
            if args.parts > 1:
                if not args.snapshot.exists():
                    raise ValueError(
                        "공통 재개 자료가 없습니다. 원격 저장소의 최신 코드를 git pull로 받아 주세요."
                    )
                imported = store.import_snapshot(
                    args.snapshot, target_fingerprint(all_targets)
                )
                logging.info(
                    "파트 %s/%s 기존 재개 자료 %s건 반영",
                    args.part,
                    args.parts,
                    imported,
                )
            store.set_meta("run_status", "running")
            store.set_meta("last_error", None)
            store.export(output)
            logging.info("대상 %s건, 현재 %s", len(targets), dict(store.counts()))
            logging.info(
                "이번 실행 목표: 2010~%s년; 단계별 현황 %s",
                args.through_year,
                store.phase_counts(),
            )
            logging.info(
                "대기 설정: 건별 %s~%s초 / 묶음 %s~%s회 / 휴식 %s~%s초",
                *REQUEST_DELAY,
                *BATCH_SIZE,
                *BATCH_REST,
            )
            if next_job(store, args.through_year) is None:
                finish_phase(store, args.through_year)
                return 0
            client = Client(store, Pacer(store))
            client.initialize_robots()
            collect_source_categories(
                client,
                store,
                output.with_name(output.stem + "_categories_sample.html"),
            )
            processed = 0
            while True:
                job = next_job(store, args.through_year)
                if job is None:
                    break
                product_id = job["product_id"]
                try:
                    html = client.fetch(f"/product/goods/{product_id}")
                    if job["pilot"]:
                        output.with_name(
                            f"{output.stem}_{product_id}_sample.html"
                        ).write_text(html, encoding="utf-8")
                    data, meta, nodes = parse_detail(html, product_id)
                    meta.update(
                        http_status=200,
                        attempts=store.get_meta("last_request")["attempt"],
                    )
                    store.save_result(product_id, data, meta, nodes)
                    if job["pilot"] and meta["field_status"]["categories"] != "present":
                        raise StructureError(
                            f"{product_id}: 소량 점검에서 코드 포함 분류를 확인하지 못했습니다."
                        )
                except AdultCheckError as exc:
                    store.mark_error(product_id, "review", exc)
                    with store.connection:
                        store.connection.execute(
                            "UPDATE jobs SET metadata_json=? WHERE product_id=?",
                            (
                                json.dumps(
                                    {
                                        "reason": "adult_check",
                                        "error": str(exc),
                                        "http_status": store.get_meta(
                                            "last_request", {}
                                        ).get("status"),
                                        "collected_at": now_iso(),
                                    },
                                    ensure_ascii=False,
                                ),
                                product_id,
                            ),
                        )
                    logging.warning(
                        "성인 인증 상품 %s: 검토 대상으로 보존하고 다음 상품으로 진행",
                        product_id,
                    )
                except NotFoundError as exc:
                    store.mark_error(product_id, "not_found", exc)
                except requests.RequestException as exc:
                    store.mark_error(product_id, "failed", exc)
                processed += 1
                if job["pilot"] or processed % 25 == 0:
                    pilot_pending = store.connection.execute(
                        "SELECT count(*) FROM jobs WHERE pilot=1 AND status='pending'"
                    ).fetchone()[0]
                    if not pilot_pending and not store.get_meta("pilot_passed"):
                        unsuccessful = store.connection.execute(
                            "SELECT count(*) FROM jobs WHERE pilot=1 AND data_json IS NULL "
                            "AND COALESCE(json_extract(metadata_json,'$.reason'),'')!='adult_check'"
                        ).fetchone()[0]
                        if unsuccessful:
                            raise StructureError(
                                "소량 점검에 수집 실패 항목이 있어 전체 수집을 보류합니다."
                            )
                        store.set_meta("pilot_passed", True)
                        logging.info(
                            "소량 10건 점검 종료. 전체 수집에 이어서 적용합니다."
                        )
                    reused = store.reuse_legacy(legacy)
                    if reused:
                        logging.info("기존 상세 %s건 재사용", reused)
                    store.export(output)
                logging.info("상세 저장 %s: %s", product_id, dict(store.counts()))
                if args.limit and processed >= args.limit:
                    store.set_meta("run_status", "paused_limit")
                    return 2
            finish_phase(store, args.through_year)
            return 0
        except (BlockedError, StructureError) as exc:
            store.set_meta(
                "run_status",
                "blocked" if isinstance(exc, BlockedError) else "structure_error",
            )
            store.set_meta("last_error", str(exc))
            logging.error("저장 후 중단: %s", exc)
            return 1
        except KeyboardInterrupt:
            store.set_meta("run_status", "interrupted")
            logging.info("사용자 중단. 저장한 상태에서 재개할 수 있습니다.")
            return 2
        except Exception as exc:
            store.set_meta("run_status", "error")
            store.set_meta("last_error", str(exc))
            logging.exception("수집 오류")
            return 1
        finally:
            try:
                store.export(output)
            finally:
                store.connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "data/yes24_it_mobile_list.csv"
    )
    parser.add_argument(
        "--legacy", type=Path, default=ROOT / "data/yes24_it_mobile.csv"
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--part", type=int, choices=(1, 2), help="이 컴퓨터가 담당할 파트 번호"
    )
    parser.add_argument(
        "--through-year",
        type=int,
        choices=(2016, 2026),
        default=2016,
        help="기본값 2016: 1차 구간만 수집 후 종료. 2026으로 2017년 이후 이어서 수집",
    )
    parser.add_argument(
        "--parts", type=int, choices=(1, 2), default=2, help="기본값 2: 두 PC 분할"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=SNAPSHOT,
        help="두 PC에 공유할 기존 결과와 분류 사전",
    )
    parser.add_argument(
        "--prepare-parts",
        action="store_true",
        help="현재 PC의 통합 SQLite에서 두 파트 명단·재개 자료만 준비. HTTP 요청 없음",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="이번 실행의 상세 시도 수. 0은 전체 대상"
    )
    parser.add_argument(
        "--resume-after-stop",
        action="store_true",
        help="접근 제한/구조 오류의 원인 확인 후 명시적으로 재개",
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit는 0 이상이어야 합니다.")
    if args.prepare_parts and (args.part is not None or args.parts != 2):
        parser.error("--prepare-parts는 --part 없이 --parts 2로 사용하세요.")
    if not args.prepare_parts and args.part is None:
        parser.error("PC별로 --part 1 또는 --part 2를 지정하세요.")
    if args.part is not None and args.part > args.parts:
        parser.error("파트 번호가 전체 파트 수보다 큽니다.")
    if args.output is None:
        base_output = ROOT / "data/yes24_details_2010_2026.csv"
        args.output = (
            base_output
            if args.prepare_parts
            else partition_output(base_output, args.part, args.parts)
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(args.output.with_suffix(".log"), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    try:
        return prepare_parts(args) if args.prepare_parts else run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        logging.error(
            "실행 중단: %s. 기존 수집이 실행 중이면 먼저 Ctrl+C로 중단하세요.", exc
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
