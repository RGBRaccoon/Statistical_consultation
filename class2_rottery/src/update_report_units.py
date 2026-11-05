"""저장된 결과로 그림 표시만 갱신하고 모든 보고서 MD에 다시 임베딩한다.

실행: python -X utf8 class2_rottery/src/update_report_units.py
분석 엔진을 재실행하지 않으며 CSV 및 HTML은 변경하지 않는다.
"""

import base64
import hashlib
import json
import re
import time

import pandas as pd

from report_units import convert_text
from paths import DATA, DOCS, REPORTS, ROOT

LOG = DOCS / "logs" / "report_units_log.json"
IMAGE = re.compile(r"data:image/png;base64,([A-Za-z0-9+/=]+)")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_results(directory):
    result = {}
    for path in directory.glob("*.csv"):
        frame = pd.read_csv(path, dtype={"주제": str, "기준주제": str, "비교주제": str})
        result[path.stem] = frame
    result["data"] = result.get("focus")
    return result


def main():
    log = json.loads(LOG.read_text(encoding="utf-8"))
    log["commands"] = [
        "python -X utf8 class2_rottery/src/update_report_units.py",
        "초기 본문 변환: python -X utf8 -c 에서 report_units.convert_text 실행. 도구 실행 결과 eb04cb 참조.",
    ]
    # 원본 데이터는 저장된 CSV에서 직접 읽고 표시만 변경한다.
    originals = {}
    for path in ROOT.rglob("*.png"):
        originals.setdefault(digest(path), []).append(path)
    reports = [p for p in ROOT.rglob("lotto*report*.md")]

    import lotto_analysis_v2 as v2
    import lotto_analysis as v1
    import lotto_analysis_v2_1_basic as basic
    import lotto_analysis_v2_3 as v23
    import lotto_report_v2_4 as v24
    import matplotlib.pyplot as plt

    started = time.monotonic()
    regenerated = []

    def record(paths, name):
        regenerated.extend(str(path.relative_to(ROOT)) for path in paths)
        print(f"{name}: {len(paths)} figures rendered", flush=True)

    crowd_path = (
        REPORTS / "v1" / "analysis_results" / "charts" / "04_crowd_patterns.png"
    )
    v1.configure_plots()
    v1._plot_crowd(
        pd.read_csv(
            REPORTS / "v1" / "analysis_results" / "04_crowd_pattern_comparison.csv"
        ),
        crowd_path,
    )
    record([crowd_path], "V1 단위 문구")

    record(
        v2.create_step1_charts(
            pd.read_csv(DATA / "lotto_preprocessed_v2.csv"),
            REPORTS / "v2" / "analysis_results_v2" / "charts",
        ),
        "V2",
    )
    for folder in ("analysis_results_v2_1_basic", "analysis_results_v2_3"):
        output = REPORTS / ("v2_1" if "v2_1" in folder else "v2_3") / folder
        result = load_results(output)
        record(list(basic.draw(result, output / "charts").values()), folder)
        if folder.endswith("v2_3"):
            record(
                list(v23.draw_followups(result, output / "charts").values()),
                "V2.3 followups",
            )

    output = REPORTS / "v2_4" / "analysis_results_v2_4"
    result = load_results(output)
    charts = output / "figures"
    plt.rcParams.update(
        {"font.family": "Malgun Gothic", "axes.unicode_minus": False, "font.size": 10}
    )
    for topic in v24.TOPICS:
        code, _, _, _, _, unit = topic
        cases = result["cases"].loc[result["cases"]["주제"].eq(code)]
        paths = [v24.selection_figure(cases, topic, charts / f"{code}_selection.png")]
        paths.append(
            v24.paired_violin(
                cases,
                "y",
                f"{code} · 선정된 Y의 정상·이상 분포",
                unit,
                charts / f"{code}_y_violin.png",
            )
        )
        for key, name, feature_unit, _ in v24.FEATURES:
            paths.append(
                v24.paired_violin(
                    cases,
                    key,
                    f"{code} · {name} — 정상 회차 vs 상방 이상 회차",
                    feature_unit,
                    charts / f"{code}_{key}.png",
                )
            )
        paths.append(
            v24.overlap_figure(result["overlaps"], code, charts / f"{code}_overlap.png")
        )
        record(paths, f"V2.4 section {code}")

    replacements = {}
    for old_hash, paths in originals.items():
        changed = [path for path in paths if digest(path) != old_hash]
        if changed:
            replacements[old_hash] = base64.b64encode(changed[0].read_bytes()).decode(
                "ascii"
            )
    audits = []
    for path in reports:
        text = path.read_text(encoding="utf-8-sig")
        count = text.count("data:image/")
        changed_images = 0

        def replace_image(match):
            nonlocal changed_images
            key = hashlib.sha256(base64.b64decode(match[1], validate=True)).hexdigest()
            if key not in replacements:
                return match[0]
            changed_images += 1
            return "data:image/png;base64," + replacements[key]

        updated = IMAGE.sub(replace_image, text)
        updated = convert_text(updated, english=path.name.endswith("_en.md"))
        note = (
            "Display units: values above 1,000,000 in magnitude use million; smaller values retain the original unit. "
            if path.name.endswith("_en.md")
            else "표시 단위: 절댓값이 1,000,000을 초과하는 값만 백만 단위로 표시합니다. 그 이하는 기존 단위를 유지합니다. "
        )
        # 문서 구조를 유지하고 제목 바로 아래에 표시 기준만 명시한다.
        if note.strip() not in updated:
            first, separator, rest = updated.partition("\n")
            updated = first + separator + "\n" + note.strip() + "\n" + rest
        path.write_text(updated, encoding="utf-8")
        assert updated.count("data:image/") == count
        audits.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": digest(path),
                "images": count,
                "images_refreshed": changed_images,
            }
        )

    # 기존 manifest의 파일 무결성 필드만 갱신하고 과거 검증 기록은 덮어쓰지 않는다.
    manifests = []
    for path in ROOT.rglob("manifest.json"):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        changed = False
        for entry in data.get("artifacts", []):
            if not isinstance(entry, dict) or "path" not in entry:
                continue
            target = path.parent / entry["path"]
            if target.is_file() and target.suffix in (".md", ".png"):
                if "sha256" in entry:
                    entry["sha256"] = digest(target).upper()
                if "size_bytes" in entry:
                    entry["size_bytes"] = target.stat().st_size
                changed = True
        if changed:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            manifests.append(str(path.relative_to(ROOT)))
    csv_after = {
        str(path.relative_to(ROOT.parent)): digest(path) for path in ROOT.rglob("*.csv")
    }
    before = {key.replace("\\", "/"): value for key, value in log["csv_before"].items()}
    after = {key.replace("\\", "/"): value for key, value in csv_after.items()}
    assert before == after, "CSV content or file set changed"
    log.update(
        {
            "status": "verified",
            "reports_after": audits,
            "csv_unchanged": True,
            "regenerated_figures": regenerated,
            "updated_manifests": manifests,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "issues": [
                "PowerShell inline Python replacement failed with SyntaxError; no files changed by that command; apply_patch used successfully.",
                "Initial rg wildcard file argument was invalid on Windows; repeated with -g.",
                "HTML intentionally not updated. Historical visual review sheets and past audit logs are not new verification results.",
            ],
        }
    )
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": log["status"],
                "reports": len(audits),
                "figures": len(regenerated),
                "csv_unchanged": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
