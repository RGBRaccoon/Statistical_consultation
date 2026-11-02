"""분석 모듈 없이 원본 CSV에서 V2.4 수치와 보고서 구조를 독립 검증한다."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
from io import BytesIO
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd
from PIL import Image

BASE = Path(__file__).resolve().parent
CODES = ("01", "02", "03", "08", "09A", "09B", "09C", "09D", "10")
RANKS = ("1등", "2등", "3등")
FEATURES = tuple(
    prefix + suffix
    for prefix in ("current_", "previous_")
    for suffix in ("sales", "prize", "winners", "total")
)
TARGET = {
    "01": "current_prize",
    "02": "current_winners",
    "03": "current_total",
    "08": "current_prize",
    "09C": "current_winners",
    "09D": "current_prize",
    "10": "current_prize",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def quantile(values: list[float], probability: float) -> float:
    """순서통계량을 직접 보간하며 pandas.quantile을 재사용하지 않는다."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    fraction = position - low
    return ordered[low] + fraction * (
        ordered[min(low + 1, len(ordered) - 1)] - ordered[low]
    )


def close(actual: float, expected: float) -> None:
    if pd.isna(actual) and pd.isna(expected):
        return
    assert math.isclose(float(actual), float(expected), rel_tol=1e-11, abs_tol=1e-8), (
        actual,
        expected,
    )


def select(frame: pd.DataFrame, **criteria) -> pd.DataFrame:
    for key, value in criteria.items():
        frame = frame.loc[frame[key].eq(value)]
    return frame


def expected_from_raw(source: Path):
    raw = pd.read_csv(source, encoding="utf-8-sig")
    raw = raw.loc[raw["순위"].isin(RANKS)].sort_values(["순위", "회차"])
    lookup = {(r["순위"], int(r["회차"])): r for r in raw.to_dict("records")}
    features = {}
    for key, row in lookup.items():
        factor = 0.5 if key[1] <= 87 else 1.0
        features[key] = {
            "current_sales": row["총판매금액"] * factor,
            "current_prize": row["1게임당 당첨금"] * factor
            if row["당첨게임 수"] > 0
            else np.nan,
            "current_winners": float(row["당첨게임 수"]),
            "current_total": row["등위별 총 당첨금"] * factor,
        }
    for (rank, draw), values in features.items():
        previous = features.get((rank, draw - 1), {})
        for suffix in ("sales", "prize", "winners", "total"):
            values["previous_" + suffix] = previous.get("current_" + suffix, np.nan)
    groups = {}
    for code in CODES:
        for rank in RANKS:
            observations = {}
            for (current_rank, draw), f in features.items():
                if current_rank != rank:
                    continue
                if code.startswith("09") and (rank, draw - 1) not in features:
                    continue
                if code in ("01", "08", "09A", "09D", "10") and pd.isna(
                    f["current_prize"]
                ):
                    continue
                if code in ("09A", "09C") and pd.isna(f["previous_prize"]):
                    continue
                y = (
                    f[TARGET[code]]
                    if code in TARGET
                    else (
                        f["current_prize"] - f["previous_prize"]
                        if code == "09A"
                        else f["current_winners"] - f["previous_winners"]
                    )
                )
                x = (
                    f["current_sales"] / 1000
                    if code == "08"
                    else f["previous_prize"]
                    if code == "09C"
                    else f["previous_winners"]
                    if code == "09D"
                    else float(draw)
                )
                observations[draw] = (float(x), float(y))
            ys = [pair[1] for pair in observations.values()]
            q1, q3 = quantile(ys, 0.25), quantile(ys, 0.75)
            iqr = q3 - q1
            upper, strong = q3 + 1.5 * iqr, q3 + 3 * iqr
            flags = {
                draw: 2 if y > strong else 1 if y > upper else 0
                for draw, (_, y) in observations.items()
            }
            groups[(code, rank)] = (observations, flags, (q1, q3, iqr, upper, strong))
    return raw, features, groups


def verify(output: Path, root_reports: bool = True) -> dict:
    raw, features, groups = expected_from_raw(BASE / "lotto_rank_1_1245.csv")
    tables = {
        name: pd.read_csv(
            output / f"{name}.csv",
            encoding="utf-8-sig",
            dtype={key: str for key in ("주제", "기준주제", "비교주제")},
        )
        for name in (
            "observations",
            "labeled",
            "fences",
            "features",
            "cases",
            "outliers",
            "comparisons",
            "overlaps",
        )
    }
    for frame in tables.values():
        assert "기간" not in frame.columns, "분석 집단에 기간 분할이 남아 있다."
    assert len(tables["features"]) == len(features)
    checked_features = 0
    for row in tables["features"].to_dict("records"):
        for name, value in features[(row["순위"], int(row["회차"]))].items():
            close(row[name], value)
            checked_features += 1
    assert len(tables["fences"]) == 27
    checked_y = 0
    for (code, rank), (observations, flags, rule) in groups.items():
        fence = select(tables["fences"], 주제=code, 순위=rank)
        assert len(fence) == 1
        fence = fence.iloc[0]
        assert fence["관측수"] == len(observations)
        for name, value in zip(("Q1", "Q3", "IQR", "상한", "강한상한"), rule):
            close(fence[name], value)
        assert fence["상방수"] == sum(flag > 0 for flag in flags.values())
        assert fence["강한상방수"] == sum(flag == 2 for flag in flags.values())
        for name in ("observations", "labeled", "cases"):
            group = select(tables[name], 주제=code, 순위=rank).set_index("회차")
            assert len(group) == len(observations)
            assert set(group.index) == set(observations)
            for draw, (x, y) in observations.items():
                close(group.loc[draw, "x"], x)
                close(group.loc[draw, "y"], y)
                if name != "observations":
                    assert group.loc[draw, "강도"] == flags[draw]
                    assert bool(group.loc[draw, "상방"]) == (flags[draw] > 0)
                if name == "cases":
                    for feature in FEATURES:
                        close(group.loc[draw, feature], features[(rank, draw)][feature])
        checked_y += len(observations)
    expected_outliers = {
        (code, rank, draw)
        for (code, rank), (_, flags, _) in groups.items()
        for draw, flag in flags.items()
        if flag > 0
    }
    actual_outliers = list(
        tables["outliers"][["주제", "순위", "회차"]].itertuples(index=False, name=None)
    )
    assert len(actual_outliers) == len(expected_outliers)
    assert set(actual_outliers) == expected_outliers
    assert len(tables["comparisons"]) == 27 * 8
    comparison_keys = set()
    for row in tables["comparisons"].to_dict("records"):
        code, rank, feature = row["주제"], row["순위"], row["특징"]
        key = (code, rank, feature)
        assert key not in comparison_keys
        comparison_keys.add(key)
        _, flags, _ = groups[(code, rank)]
        for prefix, flag in (("정상", False), ("이상", True)):
            draws = [draw for draw, value in flags.items() if (value > 0) == flag]
            values = [features[(rank, draw)][feature] for draw in draws]
            values = [value for value in values if not pd.isna(value)]
            assert row[prefix + "집단수"] == len(draws)
            assert row[prefix + "유효수"] == len(values)
            assert row[prefix + "결측수"] == len(draws) - len(values)
            close(row[prefix + "중앙값"], median(values) if values else np.nan)
            close(row[prefix + "25%"], quantile(values, 0.25) if values else np.nan)
            close(row[prefix + "75%"], quantile(values, 0.75) if values else np.nan)
        close(row["중앙값차이"], row["이상중앙값"] - row["정상중앙값"])
        assert bool(row["비교가능"]) == (
            row["정상유효수"] > 0 and row["이상유효수"] > 0
        )
    assert comparison_keys == {
        (code, rank, feature)
        for code in CODES
        for rank in RANKS
        for feature in FEATURES
    }
    assert len(tables["overlaps"]) == 729
    for row in tables["overlaps"].to_dict("records"):
        left = groups[(row["기준주제"], row["기준등수"])][1]
        right = groups[(row["비교주제"], row["비교등수"])][1]
        common = set(left) & set(right)
        for prefix, flag in (("정상", False), ("이상", True)):
            draws = [draw for draw in common if (left[draw] > 0) == flag]
            numerator = sum(right[draw] > 0 for draw in draws)
            assert row[prefix + "분모"] == len(draws)
            assert row[prefix + "동시수"] == numerator
            close(row[prefix + "발생비율"], numerator / len(draws) if draws else np.nan)
        close(row["비율차이"], row["이상발생비율"] - row["정상발생비율"])
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        path = output / artifact["path"]
        assert path.stat().st_size == artifact["size_bytes"]
        assert digest(path) == artifact["sha256"]
    assert digest(BASE / "lotto_preprocessed_v2.csv") == manifest["source_sha256"]
    assert digest(BASE / "lotto_rank_1_1245.csv") == manifest["raw_source_sha256"]
    image_paths = list((output / "figures").glob("*.png"))
    assert len(image_paths) == 99
    images = {p.read_bytes() for p in image_paths}
    image_counts = {}
    for extension in ("md",):
        name = f"lotto_rank_report_v2_4.{extension}"
        document = (output / name).read_text(encoding="utf-8")
        if root_reports:
            assert document == (BASE / name).read_text(encoding="utf-8")
        payloads = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", document)
        for payload in payloads:
            binary = base64.b64decode(payload, validate=True)
            assert binary in images
            with Image.open(BytesIO(binary)) as image:
                image.verify()
        image_counts[extension] = len(payloads)
        prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[image]", document)
        assert not re.search(
            r"<table\b|^\s*\|.*\|\s*$", prose, re.IGNORECASE | re.MULTILINE
        )
        assert not re.search(r"spearman|pearson|correlation", prose, re.IGNORECASE)
    assert image_counts["md"] == 99
    metadata = json.loads((output / "plot_metadata.json").read_text(encoding="utf-8"))
    assert len(metadata) == 81
    for plot in metadata:
        assert plot["panels"] == 3 and plot["distribution_slots"] == 6
        assert len(plot["groups"]) == 6
        assert {(g["rank"], g["outlier"]) for g in plot["groups"]} == {
            (rank, abnormal) for rank in RANKS for abnormal in (False, True)
        }
        for group in plot["groups"]:
            code, rank = plot["topic"], group["rank"]
            observations, flags, _ = groups[(code, rank)]
            draws = [
                draw for draw in observations if (flags[draw] > 0) == group["outlier"]
            ]
            values = [
                observations[draw][1]
                if plot["feature"] == "y"
                else features[(rank, draw)][plot["feature"]]
                for draw in draws
            ]
            valid = [value for value in values if not pd.isna(value)]
            assert group["n"] == len(valid)
            assert group["missing"] == len(values) - len(valid)
            assert group["density_drawn"] == (
                len(valid) > 1 and max(valid) > min(valid)
            )
    md = (output / "lotto_rank_report_v2_4.md").read_text(encoding="utf-8")
    for index, code in enumerate(CODES):
        fragment = md.split(f"## {code}.", 1)[1]
        if index + 1 < len(CODES):
            fragment = fragment.split(f"## {CODES[index + 1]}.", 1)[0]
        assert (
            fragment.index("### 해당 회차:")
            < fragment.index("### 직전 회차:")
            < fragment.index("### 다른 섹션에서도")
        )
        assert fragment.count("data:image/png;base64,") == 11
    audit = {
        "status": "passed",
        "command": "python -X utf8 class2_rottery/verify_lotto_v2_4.py",
        "raw_source_sha256": digest(BASE / "lotto_rank_1_1245.csv"),
        "v2_source_sha256": digest(BASE / "lotto_preprocessed_v2.csv"),
        "original_rows": len(raw),
        "independent_section_rank_rules": len(groups),
        "main_xy_and_labels_checked": checked_y,
        "current_previous_feature_values_checked": checked_features,
        "comparison_rows_checked": len(tables["comparisons"]),
        "normal_and_outlier_populations_independently_checked": True,
        "overlap_pairs_checked": len(tables["overlaps"]),
        "outlier_rows": len(expected_outliers),
        "unique_outlier_rounds": len({draw for _, _, draw in expected_outliers}),
        "embedded_images_per_report": image_counts["md"],
        "output_format": "markdown_only",
        "six_group_violin_metadata_checked": len(metadata),
        "artifact_hashes_checked": len(manifest["artifacts"]),
        "no_analysis_module_import": True,
        "quartiles": "independent sorted linear interpolation",
    }
    (output / "independent_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=BASE / "analysis_results_v2_4")
    args = parser.parse_args()
    print(json.dumps(verify(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
