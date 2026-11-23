"""날짜 폴더의 Npay 증권 HTML을 종목·자료 유형별 CSV로 변환한다."""

import argparse
import csv
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup


def txt(node):
    return " ".join(node.stripped_strings) if node else ""


def number(value):
    return (
        value.replace(",", "")
        if re.fullmatch(r"[+-]?[\d,]+(?:\.\d+)?", value)
        else value
    )


def table_records(table, caption):
    rows = [
        [txt(c) for c in tr.find_all(["th", "td"], recursive=False)]
        for tr in table.select("tr")
        if tr.find_parent("table") is table
    ]
    if caption == "외국인 기관":
        for row in rows:
            if len(row) == 5 and re.fullmatch(r"\d\d/\d\d", row[0]):
                yield (
                    "investor_flows",
                    dict(
                        zip(
                            (
                                "trade_date_raw",
                                "close",
                                "change_raw",
                                "foreign_net",
                                "institution_net",
                            ),
                            map(number, row),
                        )
                    ),
                )
    elif caption == "기업실적분석 테이블":
        groups = [
            txt(c)
            for c in table.select("tr")[0].select("th")[1:]
            for _ in range(int(c.get("colspan", 1)))
        ]
        for row in rows[3:]:
            for group, period, basis, value in zip(
                groups, rows[1], rows[2], row[1:], strict=True
            ):
                yield (
                    "financials",
                    dict(
                        metric=row[0],
                        period_type=group,
                        period=period.split(" ")[0],
                        estimate="(E)" in period,
                        basis=basis,
                        value=number(value),
                    ),
                )
    elif caption == "동종업종비교":
        for row in rows[1:]:
            for peer, value in zip(rows[0][1:], row[1:], strict=True):
                yield (
                    "peer_comparison",
                    dict(
                        peer=peer,
                        peer_code=peer.split()[-1],
                        metric=row[0],
                        value=number(value),
                    ),
                )
    elif caption == "거래원 정보":
        rank = 0
        for row in rows[1:]:
            if len(row) == 4:
                if row[0] == "외국계추정합":
                    yield (
                        "broker_totals",
                        dict(
                            label=row[0],
                            sell=number(row[1]),
                            net=number(row[2]),
                            buy=number(row[3]),
                        ),
                    )
                else:
                    rank += 1
                    for side, name, volume in [("sell", *row[:2]), ("buy", *row[2:])]:
                        yield (
                            "brokers",
                            dict(
                                side=side, rank=rank, broker=name, volume=number(volume)
                            ),
                        )
    elif caption in {
        "시가총액",
        "외국인한도주식수",
        "투자의견",
        "PER/EPS",
        "동일업종 PER",
    }:
        for row in rows:
            if len(row) == 2:
                yield (
                    "indicators",
                    dict(section=caption, metric_raw=row[0], value_raw=row[1]),
                )
    elif caption == "호가 10단계":
        for row in rows[1:]:
            if len(row) == 3 and any(row):
                yield (
                    "order_book",
                    dict(
                        sell_quantity=number(row[0]),
                        price_or_label=number(row[1]),
                        buy_quantity=number(row[2]),
                        heading=rows[0][1],
                    ),
                )
    elif table.find_parent(id=re.compile("^fav_(kospi|kosdaq)$")):
        market = table.find_parent(id=re.compile("^fav_(kospi|kosdaq)$"))["id"][4:]
        for rank, row in enumerate(rows[1:], 1):
            if len(row) == 3:
                yield (
                    "search_rankings",
                    dict(
                        market=market,
                        rank=rank,
                        name=row[0],
                        displayed_value=row[1],
                        change_raw=row[2],
                    ),
                )


def extract(html, code):
    soup = BeautifulSoup(html, "html.parser")
    if txt(soup.select_one(".wrap_company .code")) != code:
        raise ValueError("페이지 종목코드 누락 또는 불일치")
    data = defaultdict(list)
    for i, table in enumerate(soup.select("table"), 1):
        caption = txt(table.caption)
        try:
            records = list(table_records(table, caption))
        except (ValueError, IndexError):
            records = []
        if caption == "주요 시세":
            region = table.find_parent(class_="rate_info")
            if region:
                quote = dict(
                    market_region=region.get("id", ""),
                    as_of=txt(soup.select_one("#time")),
                    price=number(txt(region.select_one(".no_today .blind"))),
                    change_raw=txt(region.select_one(".no_exday .ico"))
                    + " "
                    + txt(region.select_one(".no_exday .blind")),
                )
                fields = {
                    "전일": "previous_close",
                    "시가": "open",
                    "고가": "high",
                    "저가": "low",
                    "상한가": "upper_limit",
                    "하한가": "lower_limit",
                    "거래량": "volume",
                    "거래대금": "turnover",
                }
                for key in fields.values():
                    quote[key] = ""
                for label in table.select("td .sptxt"):
                    key = fields.get(txt(label).strip("() "))
                    el = label.find_next_sibling("em")
                    if key and el:
                        quote[key] = number(
                            txt(el.select_one(".blind")) or "".join(el.stripped_strings)
                        )
                quote["turnover_unit_raw"] = txt(table.select_one(".sp_txt11"))
                quote["change_percent_raw"] = txt(
                    region.select_one(".no_exday em:nth-of-type(2) .ico")
                ) + txt(region.select_one(".no_exday em:nth-of-type(2) .blind"))
                records = [("quotes", quote)]
        if not records:
            data["unclassified"].append(dict(section=caption, html=str(table)))
        for kind, row in records:
            data[kind].append(row)
        data["sections"].append(
            dict(
                table_index=i,
                caption=caption,
                destinations="|".join(sorted({k for k, _ in records}))
                or "unclassified",
                rows=len(records),
            )
        )
    profile = soup.select_one(".summary_info")
    if profile:
        data["company_profile"].append(dict(description=txt(profile)))
    for kind, selector in [
        ("news_disclosures", ".news_section li"),
        ("discussions", ".sub_section.right li"),
    ]:
        for item in soup.select(selector):
            link = item.select_one("a[href]")
            if link:
                data[kind].append(
                    dict(
                        title=txt(link),
                        href=link["href"],
                        displayed_time=txt(item.select_one("em")),
                        raw=txt(item),
                    )
                )
    if not data["sections"]:
        raise ValueError("데이터 표 없음")
    return data


def sources(root):
    if root.is_file():
        match = re.fullmatch(
            r"stock_sample_(\d{4}-\d\d-\d\d)_([A-Za-z0-9]+)\.html", root.name
        )
        if match:
            yield root, *match.groups()
        else:
            yield root, date.fromisoformat(root.parent.name).isoformat(), root.stem
        return
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d", folder.name):
            for path in sorted(folder.glob("*.html")):
                yield path, folder.name, path.stem


def append(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        if new:
            writer.writeheader()
        writer.writerows(rows)


def convert(root, output, yearly=False):
    if output.exists():
        raise FileExistsError(f"새 출력 경로를 지정하세요: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    total = failed = 0
    instruments = {}
    schemas = {}
    with TemporaryDirectory(dir=output.parent) as temp:
        work = Path(temp) / "result"
        for path, day, code in sources(root):
            total += 1
            meta = dict(
                snapshot_date=day,
                stock_code=code,
                source=path.name
                if root.is_file()
                else path.relative_to(root).as_posix(),
            )
            try:
                data = extract(path.read_bytes(), code)
            except (OSError, ValueError) as err:
                failed += 1
                status, detail = "failed", str(err)
            else:
                status, detail = ("review" if data.get("unclassified") else "ok"), ""
                instrument = instruments.setdefault(
                    code,
                    dict(
                        stock_code=code,
                        first_snapshot=day,
                        last_snapshot=day,
                        snapshots=0,
                    ),
                )
                instrument["last_snapshot"] = day
                instrument["snapshots"] += 1
                for kind, rows in data.items():
                    folder = work / "stocks" / code
                    if kind in {"sections", "unclassified"}:
                        folder = work / "catalog"
                    elif kind == "search_rankings":
                        folder = work / "market"
                    dest = (
                        folder / kind / f"{day[:4]}.csv"
                        if yearly
                        else folder / f"{kind}.csv"
                    )
                    output_rows = [dict(**meta, **row) for row in rows]
                    if output_rows:
                        schemas[kind] = tuple(output_rows[0])
                    append(dest, output_rows)
            append(
                work / "catalog/source_files.csv",
                [dict(**meta, status=status, detail=detail)],
            )
        if not total:
            raise ValueError("입력 HTML이 없습니다.")
        append(work / "catalog/instruments.csv", list(instruments.values()))
        append(
            work / "catalog/schema.csv",
            [dict(dataset=k, column=c) for k in sorted(schemas) for c in schemas[k]],
        )
        work.rename(output)
    return total, failed


def daily_row(html, code):
    # 페이지 전체 대신 접근성용 시세 요약만 파싱한다.
    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8-sig")
        except UnicodeDecodeError:
            html = html.decode("cp949")
    for block in re.finditer(r"<dl\b[^>]*>.*?</dl\s*>", html, re.I | re.S):
        if "종목코드" not in block[0]:
            continue
        summary = BeautifulSoup(block[0], "html.parser")
        lines = [" ".join(txt(dd).split()) for dd in summary.select("dd")]
        if not any(line.split()[:2] == ["종목코드", code] for line in lines):
            continue
        stamp = re.search(r"(\d{4})년\s*(\d+)월\s*(\d+)일[^|]*장마감", "|".join(lines))
        if not stamp:
            raise ValueError("장마감 거래일 미확인")
        values = []
        for label in ("고가", "저가", "현재가", "거래량", "거래대금"):
            line = next((v for v in lines if v.startswith(label + " ")), "")
            suffix = r"\s*백만(?:원)?$" if label == "거래대금" else r"(?:\s|$)"
            match = re.match(label + r"\s+([0-9][0-9,]*)" + suffix, line)
            if not match:
                raise ValueError(label + " 값 또는 단위 미확인")
            values.append(int(match[1].replace(",", "")))
        return (date(*map(int, stamp.groups())).isoformat(), *values)
    raise ValueError("종목코드에 맞는 시세 요약 없음")


# SSH 표준 출력은 JSON 줄 단위로 전송한다. 마지막 건수로 중도 단절을 구별한다.
REMOTE_READER = r"""
import base64, json, re, sys
from pathlib import Path

root = Path(sys.argv[1])
pattern = re.compile(br"<dl\b[^>]*>.*?</dl\s*>", re.I | re.S)
markers = ["종목코드".encode(enc) for enc in ("utf8", "cp949")]
count = 0
for folder in sorted(root.iterdir()):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", folder.name) or not folder.is_dir():
        continue
    for path in sorted(folder.glob("*.html")):
        try:
            blocks = [m[0] for m in pattern.finditer(path.read_bytes())
                      if any(marker in m[0] for marker in markers)]
            payload = base64.b64encode(b"\n".join(blocks)).decode("ascii")
            error = ""
        except OSError as exc:
            payload, error = "", str(exc)
        print(json.dumps([folder.name, path.stem, payload, error]))
        count += 1
print(json.dumps({"complete": count}))
"""


def remote_inputs(host, root):
    import base64
    import json
    import shlex
    import subprocess

    cmd = shlex.join(["python3", "-c", REMOTE_READER, root])
    with subprocess.Popen(
        ["ssh", "-T", "--", host, cmd], stdout=subprocess.PIPE
    ) as proc:
        try:
            received = 0
            complete = None
            for line in proc.stdout:
                item = json.loads(line)
                if isinstance(item, dict):
                    complete = item["complete"]
                    break
                day, code, payload, error = item
                received += 1
                yield f"{day}/{code}.html", day, code, base64.b64decode(payload), error
            if proc.wait() or complete != received:
                raise RuntimeError("SSH 전송이 완료되지 않았습니다.")
        finally:
            if proc.poll() is None:
                proc.kill()


def convert_daily(root, output, yearly=False, host=None):
    import sqlite3
    import sys
    from itertools import groupby
    from time import monotonic

    if output.exists():
        raise FileExistsError(f"새 출력 경로를 지정하세요: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    count = errors = 0
    started = monotonic()
    with TemporaryDirectory(dir=output.parent) as temp:
        work = Path(temp) / "result"
        work.mkdir()
        db = sqlite3.connect(Path(temp) / "rows.db")
        try:
            db.execute(
                "CREATE TABLE prices(code, day, high, low, close, volume, amount, PRIMARY KEY(code,day)) WITHOUT ROWID"
            )
            inputs = (
                remote_inputs(host, str(root))
                if host
                else ((p, d, c, None, "") for p, d, c in sources(Path(root)))
            )
            for path, day, stem, payload, error in inputs:
                count += 1
                try:
                    if error:
                        raise OSError(error)
                    code = stem.rsplit("_", 1)[-1]
                    if not re.fullmatch(r"[A-Za-z0-9]+", code):
                        raise ValueError(
                            f"파일명에서 종목코드를 확인할 수 없음: {stem!r}"
                        )
                    row = daily_row(
                        path.read_bytes() if payload is None else payload, code
                    )
                    if not db.execute(
                        "INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?)",
                        (code, *row),
                    ).rowcount:
                        old = db.execute(
                            "SELECT day,high,low,close,volume,amount FROM prices WHERE code=? AND day=?",
                            (code, row[0]),
                        ).fetchone()
                        if old != row:
                            raise ValueError("같은 거래일의 기존 값과 다름")
                except (ValueError, OSError) as err:
                    errors += 1
                    print(f"{path}: {err}", file=sys.stderr)
                if count % 10000 == 0:
                    speed = count / (monotonic() - started)
                    print(
                        f"{day} | 입력 {count:,}건 | 실패 {errors:,}건 | 초당 {speed:.0f}건",
                        flush=True,
                    )
            if not count:
                raise ValueError("입력 HTML이 없습니다.")
            rows = db.execute("SELECT * FROM prices ORDER BY code,day")
            for (code, year), group in groupby(
                rows, lambda r: (r[0], r[1][:4] if yearly else "")
            ):
                dest = work / code / f"{year}.csv" if yearly else work / f"{code}.csv"
                dest.parent.mkdir(exist_ok=True)
                with dest.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(
                        ["거래일", "고가", "저가", "종가", "거래량", "거래대금_백만원"]
                    )
                    writer.writerows(r[1:] for r in group)
        finally:
            db.close()
        work.rename(output)
    return count, errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--ssh", help="원격 HTML을 읽을 SSH 호스트")
    parser.add_argument("output", type=Path)
    parser.add_argument("--yearly", action="store_true")
    args = parser.parse_args()
    count, errors = convert_daily(args.source, args.output, args.yearly, args.ssh)
    print(f"입력 {count}건, 실패 {errors}건")
    raise SystemExit(int(bool(errors)))
