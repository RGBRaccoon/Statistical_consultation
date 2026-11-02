"""표시 단위 수정의 경계·무결성·임베딩을 확인하고 실행 근거를 저장한다."""

import base64
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re

from PIL import Image

from report_units import PROTECTED, convert_text, display_number

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    examples = [
        ("1,000,000", "1,000,000"),
        ("2,000,000", "2백만"),
        ("-2,000,000", "-2백만"),
        ("50,000원", "50,000원"),
        ("5만원", "50,000원"),
        ("1억원", "100백만원"),
        ("1/8,145,060", "1/8,145,060"),
        ("상한은 5,001,549,682.50,", "상한은 5,001.5496825백만,"),
        ("`100,000,000`", "`100,000,000`"),
        ("https://example.com/10000000", "https://example.com/10000000"),
    ]
    for original, expected in examples:
        assert convert_text(original) == expected, (original, convert_text(original))
        assert convert_text(expected) == expected, ("not idempotent", expected)
    assert display_number(1_000_000) == "1,000,000"
    assert display_number(2_000_000) == "2.00백만"
    assert display_number(-2_000_000) == "-2.00백만"
    assert display_number(999_999) == "999,999"

    # 쉼표·마침표 직전 숫자의 변환 누락을 수정한 후 같은 검증을 적용한다.
    reports = []
    residual = re.compile(r"(?<![A-Za-z0-9_/,])(?:\d{1,3},){2,}\d{3}(?:\.\d+)?")
    for path in ROOT.rglob("lotto*report*.md"):
        old = path.read_text(encoding="utf-8")
        updated = convert_text(old, path.name.endswith("_en.md"))
        path.write_text(updated, encoding="utf-8")
        assert convert_text(updated, path.name.endswith("_en.md")) == updated
        assert old.count("data:image/") == updated.count("data:image/")
        images = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", updated)
        for encoded in images:
            with Image.open(
                io.BytesIO(base64.b64decode(encoded, validate=True))
            ) as image:
                image.verify()
        remaining = []
        for match in residual.finditer(PROTECTED.sub("", updated)):
            if abs(Decimal(match[0].replace(",", ""))) > 1_000_000:
                remaining.append(match[0])
        assert not remaining, (path, remaining[:5])
        reports.append(
            {
                "path": str(path.relative_to(ROOT)),
                "images_verified": len(images),
                "sha256": sha(path),
            }
        )

    # MD의 최종 바이트가 바뀌었으므로 기존 산출물 목록의 해시만 갱신한다.
    for path in ROOT.rglob("manifest.json"):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        changed = False
        for entry in data.get("artifacts", []):
            if not isinstance(entry, dict) or "path" not in entry:
                continue
            target = path.parent / entry["path"]
            if target.is_file() and target.suffix == ".md":
                if "sha256" in entry:
                    entry["sha256"] = sha(target).upper()
                if "size_bytes" in entry:
                    entry["size_bytes"] = target.stat().st_size
                changed = True
        if changed:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    log_path = ROOT / "report_units_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8"))
    before = {
        str(Path(key)).replace("\\", "/"): value
        for key, value in log["csv_before"].items()
    }
    after = {
        str(path.relative_to(ROOT.parent)).replace("\\", "/"): sha(path)
        for path in ROOT.rglob("*.csv")
    }
    assert before == after
    log["final_verification"] = {
        "command": "python -X utf8 class2_rottery/verify_report_units.py",
        "status": "passed",
        "format_cases": len(examples),
        "csv_files_unchanged": len(after),
        "reports": reports,
        "embedded_images_verified": sum(row["images_verified"] for row in reports),
    }
    log["issues"].extend(
        [
            "First apply_patch for comment cleanup failed because a context line did not match; a corrected patch succeeded.",
            "Post-render audit found numbers immediately before punctuation were skipped; regex was fixed and all report MDs converted again and checked.",
        ]
    )
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in log["final_verification"].items()
                if key != "reports"
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
