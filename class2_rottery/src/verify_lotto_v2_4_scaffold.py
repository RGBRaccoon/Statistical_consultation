"""문서 구조와 빈 그림을 대조한다. 복권 통계나 이상치를 계산하지 않는다."""

import ast
import argparse
import base64
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

from PIL import Image
from paths import DATA, REPORTS

BASE = Path(__file__).resolve().parent
CODES = ("01", "02", "03", "08", "09A", "09B", "09C", "09D", "10")
FEATURES = (
    "current_sales",
    "current_prize",
    "current_winners",
    "current_total",
    "previous_sales",
    "previous_prize",
    "previous_winners",
    "previous_total",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before-analysis",
        action="store_true",
        help="실제 분석 승인 전 단계에서만 분석 파일 부재까지 검증",
    )
    args = parser.parse_args()
    output = REPORTS / "v2_4" / "report_scaffold_v2_4"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "review_scaffold"
    assert manifest["statistical_analysis_executed"] is False
    assert manifest["sections"] == list(CODES)
    assert len(manifest["plot_slots"]) == 99
    assert len(list((output / "placeholders").glob("*.png"))) == 35
    if args.before_analysis:
        assert not (REPORTS / "v2_4" / "analysis_results_v2_4").exists()
        assert not (BASE / "lotto_analysis_v2_4.py").exists()
    source = (BASE / "build_lotto_v2_4_scaffold.py").read_text(encoding="utf-8")
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any(
        name.split(".")[0] in {"pandas", "numpy", "scipy", "matplotlib"}
        or name.startswith("lotto_analysis")
        for name in imports
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"read_csv", "read_excel", "loadtxt", "genfromtxt"}
        for node in ast.walk(ast.parse(source))
    )
    for suffix in ("md", "html"):
        name = f"lotto_rank_report_v2_4.{suffix}"
        document = (output / name).read_text(encoding="utf-8")
        assert document == (
            REPORTS / "v2_4" / f"lotto_rank_report_v2_4_scaffold.{suffix}"
        ).read_text(encoding="utf-8")
        assert "검토 단계" in document and "계산 대기" in document
        assert "원본 SHA-256" in document
        assert not re.search(r"<table\b|<iframe\b|<script\b", document, re.IGNORECASE)
        assert not re.search(r"^\s*\|.*\|\s*$", document, re.MULTILINE)
        payloads = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", document)
        assert len(payloads) == 99
        assert len(payloads) == len(manifest["plot_slots"])
        for slot, payload in zip(manifest["plot_slots"], payloads):
            assert slot["status"] == "awaiting_calculation"
            binary = base64.b64decode(payload, validate=True)
            assert binary == (output / slot["image"]).read_bytes()
            with Image.open(BytesIO(binary)) as image:
                assert image.size == (1260, 420)
                image.verify()
        prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[image]", document)
        assert not re.search(
            r"상관계수|spearman|pearson|correlation", prose, re.IGNORECASE
        )
        assert "1,163" not in prose and "244개 회차" not in prose
    md = (output / "lotto_rank_report_v2_4.md").read_text(encoding="utf-8")
    for index, code in enumerate(CODES):
        part = md.split(f'<a id="section-{code}"></a>', 1)[1]
        if index + 1 < len(CODES):
            part = part.split(f'<a id="section-{CODES[index + 1]}"></a>', 1)[0]
        assert (
            part.index(f"### {code} 해당 회차의 정상과 이상 비교")
            < part.index(f"### {code} 직전 회차의 정상과 이상 비교")
            < part.index(f"### {code} 다른 분석에서도 같은 회차가 튀는가")
        )
        slots = [slot for slot in manifest["plot_slots"] if slot["section"] == code]
        assert len(slots) == 11
        assert {
            slot["id"] for slot in slots if slot["kind"] == "feature_comparison"
        } == {f"{code}-{feature}" for feature in FEATURES}
        assert "총 6개 자리" in part
    artifact_count = 0
    for artifact in manifest["artifacts"]:
        path = output / artifact["path"]
        assert path.stat().st_size == artifact["size_bytes"]
        assert digest(path) == artifact["sha256"]
        artifact_count += 1
    prior = json.loads(
        (
            REPORTS / "v2_3" / "analysis_results_v2_3" / "independent_audit.json"
        ).read_text(encoding="utf-8")
    )
    assert digest(DATA / "lotto_rank_1_1245.csv") == prior["raw_source_sha256"]
    assert digest(DATA / "lotto_preprocessed_v2.csv") == prior["v2_source_sha256"]
    audit = {
        "status": "passed",
        "command": "python -X utf8 class2_rottery/src/verify_lotto_v2_4_scaffold.py",
        "stage": "review_scaffold",
        "statistical_analysis_executed": False,
        "section_count": 9,
        "embedded_empty_plot_slots_per_report": 99,
        "feature_comparison_slots": 72,
        "distinct_placeholder_images": 35,
        "tables_in_report": 0,
        "source_and_previous_comparison_before_overlap": True,
        "analysis_output_directory_absent": not (
            REPORTS / "v2_4" / "analysis_results_v2_4"
        ).exists(),
        "analysis_module_absent": not (BASE / "lotto_analysis_v2_4.py").exists(),
        "analysis_absence_required": args.before_analysis,
        "builder_uses_no_statistical_modules_or_csv_loader": True,
        "input_hashes_match_prior_audit": True,
        "artifact_hashes_checked": artifact_count,
    }
    (output / "scaffold_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
