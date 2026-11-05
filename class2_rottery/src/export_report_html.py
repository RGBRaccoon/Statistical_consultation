"""기존 MD를 이미지 내장 공유용 HTML로 변환한다. 분석은 재실행하지 않는다."""

import base64
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import re

import markdown
from paths import DOCS, REPORTS

STEMS = (
    "lotto_rank_report_v2",
    "lotto_rank_report_v2_1",
    "lotto_rank_report_v2_3",
    "lotto_rank_report_v2_4",
)
STYLE = """
html { color-scheme: light; }
body { margin: 0; background: #f4f6f8; color: #17212b;
       font: 16px/1.75 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }
main { max-width: 1450px; margin: 24px auto; padding: 36px;
       background: white; border-radius: 12px; overflow-wrap: anywhere; }
h1, h2, h3, h4 { line-height: 1.4; scroll-margin-top: 20px; }
h2 { margin-top: 54px; padding-top: 20px; border-top: 1px solid #dce2e8; }
img { display: block; max-width: 100%; height: auto; margin: 24px auto; }
table { display: block; width: 100%; overflow-x: auto; border-collapse: collapse;
        font-size: 14px; margin: 22px 0; }
th, td { border: 1px solid #dce2e8; padding: 8px 12px; white-space: nowrap; }
th { background: #edf2f7; }
pre { overflow-x: auto; background: #f4f6f8; padding: 16px; }
code { font-family: Consolas, monospace; }
a { color: #165fa5; }
blockquote { border-left: 4px solid #a7bed3; margin-left: 0; padding-left: 20px; }
@media (max-width: 700px) { main { margin: 0; padding: 18px; border-radius: 0; } }
@media print { body { background: white; } main { margin: 0; padding: 0; }
              img { break-inside: avoid; } }
"""


class ResourceCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.external_resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.images.append(attrs.get("src", ""))
        if tag in ("img", "script", "iframe", "source") and attrs.get("src"):
            if not attrs["src"].startswith("data:"):
                self.external_resources.append(attrs["src"])
        if tag == "link" and attrs.get("href"):
            self.external_resources.append(attrs["href"])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    results = []
    for stem in STEMS:
        source = REPORTS / stem.removeprefix("lotto_rank_report_") / f"{stem}.md"
        before = sha(source)
        document = source.read_text(encoding="utf-8-sig")
        title = document.splitlines()[0].lstrip("# ")
        body = markdown.markdown(document, extensions=["tables", "fenced_code"])
        html = (
            '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
            f"<body><main>{body}</main></body></html>\n"
        )
        target = source.with_suffix(".html")
        target.write_text(html, encoding="utf-8")
        saved = target.read_text(encoding="utf-8")
        parser = ResourceCheck()
        parser.feed(saved)
        expected = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", document)
        assert parser.images == [f"data:image/png;base64,{value}" for value in expected]
        assert not parser.external_resources
        for image in parser.images:
            binary = base64.b64decode(image.split(",", 1)[1], validate=True)
            assert binary.startswith(b"\x89PNG\r\n\x1a\n")
        assert sha(source) == before
        results.append(
            {
                "source": source.name,
                "source_sha256": before,
                "html": target.name,
                "html_sha256": sha(target),
                "size_bytes": target.stat().st_size,
                "embedded_images": len(parser.images),
                "image_payloads_identical_to_md": True,
                "external_resources": parser.external_resources,
                "md_unchanged": True,
            }
        )
    log = {
        "command": "python -X utf8 class2_rottery/src/export_report_html.py",
        "status": "verified",
        "validation": "Saved HTML parsed; PNG payloads compared to MD and decoded; no external rendering resources.",
        "limitation": "Browser rendering not verified in this execution.",
        "reports": results,
    }
    (DOCS / "logs" / "html_export_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
