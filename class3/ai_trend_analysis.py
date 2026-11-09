import argparse
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

GROUPS = ("general", "technical", "service")
REQUIRED_COLUMNS = ("title", "subtitle", "publication_month")
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class KeywordClassifier:
    def __init__(self, keywords):
        self.keywords = {}
        for group in GROUPS:
            entries = []
            for entry in keywords[group]:
                entries.append(
                    (
                        entry["label"],
                        [
                            re.compile(pattern, re.IGNORECASE)
                            for pattern in entry["patterns"]
                        ],
                    )
                )
            self.keywords[group] = entries

    @classmethod
    def from_json(cls, path):
        with Path(path).open(encoding="utf-8") as file:
            keywords = json.load(file)
        missing = set(GROUPS) - set(keywords)
        if missing:
            raise ValueError(f"키워드 그룹 누락: {sorted(missing)}")
        return cls(keywords)

    def classify(self, title, subtitle):
        text = unicodedata.normalize("NFKC", f"{title or ''} {subtitle or ''}")
        matches = {}
        for group in GROUPS:
            labels = []
            for label, patterns in self.keywords[group]:
                if any(pattern.search(text) for pattern in patterns):
                    labels.append(label)
            matches[group] = labels

        all_labels = []
        for group in GROUPS:
            for label in matches[group]:
                if label not in all_labels:
                    all_labels.append(label)
        return {
            "is_ai_general": bool(matches["general"]),
            "is_ai_technical": bool(matches["technical"]),
            "is_ai_service": bool(matches["service"]),
            "is_ai_any": bool(all_labels),
            "general_keywords": matches["general"],
            "technical_keywords": matches["technical"],
            "service_keywords": matches["service"],
            "matched_keywords": all_labels,
        }


def load_books(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"필수 컬럼 누락: {sorted(missing)}")
        return [{column: row[column] for column in REQUIRED_COLUMNS} for row in reader]


def percentage(count, total):
    return round(count / total * 100, 4) if total else 0.0


def summarize_yearly(rows, classifier, cutoff_month):
    if not MONTH_PATTERN.fullmatch(cutoff_month):
        raise ValueError(f"잘못된 기준월: {cutoff_month}")

    cutoff_year = int(cutoff_month[:4])
    cutoff_month_number = int(cutoff_month[5:])
    yearly = defaultdict(
        lambda: {
            "total_books": 0,
            "ai_any_count": 0,
            "ai_general_count": 0,
            "ai_technical_count": 0,
            "ai_service_count": 0,
        }
    )
    matched_rows = []

    for row in rows:
        month = (row.get("publication_month") or "").strip()
        if not MONTH_PATTERN.fullmatch(month) or month > cutoff_month:
            continue

        year = int(month[:4])
        result = classifier.classify(row.get("title", ""), row.get("subtitle", ""))
        values = yearly[year]
        values["total_books"] += 1
        for group in GROUPS:
            if result[f"is_ai_{group}"]:
                values[f"ai_{group}_count"] += 1
        if result["is_ai_any"]:
            values["ai_any_count"] += 1
            matched_rows.append(
                {
                    "publication_month": month,
                    "title": row.get("title", ""),
                    "subtitle": row.get("subtitle", ""),
                    "is_ai_general": result["is_ai_general"],
                    "is_ai_technical": result["is_ai_technical"],
                    "is_ai_service": result["is_ai_service"],
                    "general_keywords": result["general_keywords"],
                    "technical_keywords": result["technical_keywords"],
                    "service_keywords": result["service_keywords"],
                    "matched_keywords": result["matched_keywords"],
                }
            )

    summaries = []
    for year in sorted(yearly):
        values = yearly[year]
        total = values["total_books"]
        summaries.append(
            {
                "year": year,
                **values,
                "ai_any_pct": percentage(values["ai_any_count"], total),
                "ai_general_pct": percentage(values["ai_general_count"], total),
                "ai_technical_pct": percentage(values["ai_technical_count"], total),
                "ai_service_pct": percentage(values["ai_service_count"], total),
                "is_partial_year": year == cutoff_year and cutoff_month_number < 12,
            }
        )
    return summaries, matched_rows


def write_csv(path, rows, fieldnames, list_fields=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output = dict(row)
            for field in list_fields:
                output[field] = json.dumps(output[field], ensure_ascii=False)
            writer.writerow(output)


def parse_args():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="AI 도서 키워드 분류와 연도별 집계")
    parser.add_argument(
        "--input", default=base / "data" / "yes24_it_mobile_list.csv", type=Path
    )
    parser.add_argument("--keywords", default=base / "ai_keywords.json", type=Path)
    parser.add_argument("--cutoff-month", default="2026-11")
    parser.add_argument(
        "--output-dir", default=base / "reports" / "analysis_results", type=Path
    )
    return parser.parse_args()


def main():
    args = parse_args()
    classifier = KeywordClassifier.from_json(args.keywords)
    summaries, matched = summarize_yearly(
        load_books(args.input), classifier, args.cutoff_month
    )
    write_csv(
        args.output_dir / "ai_book_yearly.csv",
        summaries,
        [
            "year",
            "total_books",
            "ai_any_count",
            "ai_general_count",
            "ai_technical_count",
            "ai_service_count",
            "ai_any_pct",
            "ai_general_pct",
            "ai_technical_pct",
            "ai_service_pct",
            "is_partial_year",
        ],
    )
    write_csv(
        args.output_dir / "ai_keyword_matches.csv",
        matched,
        [
            "publication_month",
            "title",
            "subtitle",
            "is_ai_general",
            "is_ai_technical",
            "is_ai_service",
            "general_keywords",
            "technical_keywords",
            "service_keywords",
            "matched_keywords",
        ],
        list_fields=(
            "general_keywords",
            "technical_keywords",
            "service_keywords",
            "matched_keywords",
        ),
    )
    print(f"연도별 집계: {len(summaries)}개 연도")
    print(f"AI 키워드 일치 도서: {len(matched):,}권")


if __name__ == "__main__":
    main()
