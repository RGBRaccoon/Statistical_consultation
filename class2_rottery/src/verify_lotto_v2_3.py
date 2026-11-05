"""생성 모듈을 import하지 않고 원본 CSV로 V2.3 전체 계산을 독립 대조한다."""

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
from paths import DATA, REPORTS

CODES = ("01", "02", "03", "08", "09A", "09B", "09C", "09D", "10")
RANKS = ("1등", "2등", "3등")
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


def rows(frame: pd.DataFrame, **criteria) -> pd.DataFrame:
    for column, value in criteria.items():
        frame = frame.loc[frame[column] == value]
    return frame


def quantile(values: list[float], probability: float) -> float:
    """정렬과 선형 보간을 직접 수행해 생성 코드의 pandas.quantile을 재사용하지 않는다."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    index = math.floor(position)
    fraction = position - index
    return ordered[index] + fraction * (
        ordered[min(index + 1, len(ordered) - 1)] - ordered[index]
    )


def close(left: float, right: float) -> None:
    if pd.isna(left) and pd.isna(right):
        return
    assert math.isclose(float(left), float(right), rel_tol=1e-11, abs_tol=1e-8), (
        left,
        right,
    )


def main() -> None:
    output = REPORTS / "v2_3" / "analysis_results_v2_3"
    raw = pd.read_csv(DATA / "lotto_rank_1_1245.csv", encoding="utf-8-sig")
    raw = (
        raw[raw["순위"].isin(RANKS)]
        .sort_values(["순위", "회차"])
        .reset_index(drop=True)
    )
    lookup = {(row["순위"], int(row["회차"])): row for row in raw.to_dict("records")}
    tables = {}
    for name in (
        "labeled",
        "fences",
        "features",
        "outliers",
        "comparisons",
        "overlaps",
        "periods",
        "rounds",
        "focus",
    ):
        tables[name] = pd.read_csv(
            output / f"{name}.csv",
            dtype={key: str for key in ("주제", "기준주제", "비교주제")},
            encoding="utf-8-sig",
        )
    pd.testing.assert_frame_equal(tables["focus"][raw.columns], raw)
    expected_features = {}
    for key, row in lookup.items():
        rank, draw = key
        price = 2000 if draw <= 87 else 1000
        expected_features[key] = {
            "current_sales": row["총판매금액"] / price,
            "current_prize": row["1게임당 당첨금"] * 1000 / price
            if row["당첨게임 수"] > 0
            else np.nan,
            "current_winners": row["당첨게임 수"],
            "current_total": row["등위별 총 당첨금"] * 1000 / price,
        }
    for key, features in expected_features.items():
        previous = expected_features.get((key[0], key[1] - 1), {})
        for suffix in ("sales", "prize", "winners", "total"):
            features["previous_" + suffix] = previous.get("current_" + suffix, np.nan)
    checked_features = 0
    for row in tables["features"].to_dict("records"):
        for key, value in expected_features[(row["순위"], row["회차"])].items():
            close(row[key], value)
            checked_features += 1
    expected_groups = {}
    checked_y = 0
    for code in CODES:
        for rank in RANKS:
            expected = {}
            for (row_rank, draw), row in lookup.items():
                if row_rank != rank:
                    continue
                previous = lookup.get((rank, draw - 1))
                if code in ("01", "08", "09A", "09D", "10") and row["당첨게임 수"] == 0:
                    continue
                if code.startswith("09") and previous is None:
                    continue
                if code in ("09A", "09C") and previous["당첨게임 수"] == 0:
                    continue
                y = row["1게임당 당첨금"]
                if code in ("02", "09C"):
                    y = row["당첨게임 수"]
                elif code == "03":
                    y = row["등위별 총 당첨금"]
                elif code == "09A":
                    y -= previous["1게임당 당첨금"]
                elif code == "09B":
                    y = row["당첨게임 수"] - previous["당첨게임 수"]
                elif code == "10":
                    y *= 0.5 if draw <= 87 else 1
                expected[draw] = float(y)
            values = list(expected.values())
            q1, q3 = quantile(values, 0.25), quantile(values, 0.75)
            upper, strong = q3 + 1.5 * (q3 - q1), q3 + 3 * (q3 - q1)
            rule = rows(tables["fences"], 주제=code, 순위=rank).iloc[0]
            for name, value in (
                ("Q1", q1),
                ("Q3", q3),
                ("상한", upper),
                ("강한상한", strong),
            ):
                close(rule[name], value)
            group = rows(tables["labeled"], 주제=code, 순위=rank).set_index("회차")
            assert set(group.index) == set(expected)
            flags = {}
            for draw, value in expected.items():
                close(group.loc[draw, "y"], value)
                strength = 2 if value > strong else 1 if value > upper else 0
                assert group.loc[draw, "강도"] == strength
                assert bool(group.loc[draw, "상방"]) == (strength > 0)
                flags[draw] = strength
                checked_y += 1
            assert rule["상방수"] == sum(s > 0 for s in flags.values())
            assert rule["강한상방수"] == sum(s == 2 for s in flags.values())
            expected_groups[(code, rank)] = (expected, flags)
            for period, subset in group.groupby("기간"):
                local = [expected[draw] for draw in subset.index]
                local_q1, local_q3 = quantile(local, 0.25), quantile(local, 0.75)
                local_upper = local_q3 + 1.5 * (local_q3 - local_q1)
                np.testing.assert_array_equal(
                    subset["동일시기상방"], subset["y"] > local_upper
                )
                period_row = rows(
                    tables["periods"], 주제=code, 순위=rank, 기간=period
                ).iloc[0]
                close(period_row["동일시기상한"], local_upper)
                assert period_row["관측수"] == len(subset)
                assert period_row["상방수"] == sum(
                    flags[draw] > 0 for draw in subset.index
                )
                close(period_row["상방비율"], period_row["상방수"] / len(subset))
    expected_outliers = {
        (code, rank, draw)
        for (code, rank), (_, flags) in expected_groups.items()
        for draw, strength in flags.items()
        if strength > 0
    }
    actual_outliers = set(
        tables["outliers"][["주제", "순위", "회차"]].itertuples(index=False, name=None)
    )
    assert actual_outliers == expected_outliers
    assert len(tables["outliers"]) == len(actual_outliers)
    checked_comparisons = 0
    expected_comparison_keys = set()
    for (code, rank), (_, flags) in expected_groups.items():
        group = rows(tables["labeled"], 주제=code, 순위=rank)
        for period in group["기간"].unique():
            for feature in next(iter(expected_features.values())):
                if TARGET.get(code) != feature:
                    expected_comparison_keys.add((code, rank, period, feature))
    assert (
        set(
            tables["comparisons"][["주제", "순위", "기간", "특징"]].itertuples(
                index=False, name=None
            )
        )
        == expected_comparison_keys
    )
    for row in tables["comparisons"].to_dict("records"):
        code, rank, period, feature = row["주제"], row["순위"], row["기간"], row["특징"]
        group = rows(tables["labeled"], 주제=code, 순위=rank, 기간=period)
        flags = expected_groups[(code, rank)][1]
        assert set(group["회차"]).issubset(flags), (
            code,
            rank,
            period,
            set(group["회차"]) - set(flags),
        )
        for prefix, selected in (("상방", True), ("비상방", False)):
            draws = [draw for draw in group["회차"] if (flags[draw] > 0) == selected]
            values = [expected_features[(rank, draw)][feature] for draw in draws]
            values = [value for value in values if not pd.isna(value)]
            assert row[prefix + "집단수"] == len(draws), (
                code,
                rank,
                period,
                feature,
                prefix,
                row[prefix + "집단수"],
                len(draws),
            )
            assert row[prefix + "유효수"] == len(values)
            assert row[prefix + "결측수"] == len(draws) - len(values)
            close(row[prefix + "중앙값"], median(values) if values else np.nan)
            close(row[prefix + "25%"], quantile(values, 0.25) if values else np.nan)
            close(row[prefix + "75%"], quantile(values, 0.75) if values else np.nan)
        close(row["중앙값차이"], row["상방중앙값"] - row["비상방중앙값"])
        checked_comparisons += 1
    assert len(tables["overlaps"]) == 27 * 27
    for row in tables["overlaps"].to_dict("records"):
        left = expected_groups[(row["기준주제"], row["기준등수"])][1]
        right = expected_groups[(row["비교주제"], row["비교등수"])][1]
        common = set(left) & set(right)
        numerator = sum(left[draw] > 0 and right[draw] > 0 for draw in common)
        denominator = sum(left[draw] > 0 for draw in common)
        assert row["공통관측수"] == len(common)
        assert row["동시상방수"] == numerator
        assert row["공통관측내기준상방수"] == denominator
        close(
            row["기준상방중동시비율"],
            numerator / denominator if denominator else np.nan,
        )
    assert set(tables["rounds"]["회차"]) == {draw for _, _, draw in expected_outliers}
    for row in tables["rounds"].to_dict("records"):
        selected = [
            (code, rank)
            for code, rank, draw in expected_outliers
            if draw == row["회차"]
        ]
        assert row["검출항목수"] == len(selected)
        assert row["섹션수"] == len({code for code, _ in selected})
        assert row["등수수"] == len({rank for _, rank in selected})
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        path = output / artifact["path"]
        assert path.stat().st_size == artifact["size_bytes"]
        assert digest(path) == artifact["sha256"]
    assert digest(DATA / "lotto_preprocessed_v2.csv") == manifest["source_sha256"]
    assert digest(DATA / "lotto_rank_1_1245.csv") == manifest["raw_source_sha256"]
    chart_bytes = {path.read_bytes() for path in (output / "charts").glob("*.png")}
    for extension in ("md", "html"):
        name = f"lotto_rank_report_v2_3.{extension}"
        document = (output / name).read_text(encoding="utf-8")
        assert document == (REPORTS / "v2_3" / name).read_text(encoding="utf-8")
        payloads = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", document)
        assert len(payloads) == 46
        for payload in payloads:
            binary = base64.b64decode(payload, validate=True)
            assert binary in chart_bytes
            with Image.open(BytesIO(binary)) as image:
                image.verify()
        prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[image]", document)
        assert not re.search(
            r"상관계수|spearman|pearson|correlation", prose, re.IGNORECASE
        )
    report = (output / "lotto_rank_report_v2_3.md").read_text(encoding="utf-8")
    for index, code in enumerate(CODES):
        assert (
            report.index(f"### {code}-3등")
            < report.index(f"### {code}-비교")
            < report.index(f"### {code}-후속")
        )
        if index + 1 < len(CODES):
            assert report.index(f"### {code}-후속") < report.index(
                f"## {CODES[index + 1]}."
            )
    for code in CODES:
        for rank in RANKS:
            wanted = sorted(
                draw
                for draw, flag in expected_groups[(code, rank)][1].items()
                if flag > 0
            )
            header = f"#### {code}/{rank}: 선정 회차 전체 ({len(wanted)}회)"
            fragment = report.split(header, 1)[1].split(
                "같은 등수의 다른 섹션과의 겹침:", 1
            )[0]
            displayed = [
                int(draw)
                for draw in re.findall(r"^\| (\d+) \|", fragment, re.MULTILINE)
            ]
            assert displayed == wanted, (code, rank)
    audit = {
        "status": "passed",
        "command": "python -X utf8 class2_rottery/src/verify_lotto_v2_3.py",
        "raw_source_sha256": digest(DATA / "lotto_rank_1_1245.csv"),
        "v2_source_sha256": digest(DATA / "lotto_preprocessed_v2.csv"),
        "original_rows_preserved": len(raw),
        "section_rank_rules": len(expected_groups),
        "main_y_and_labels_checked": checked_y,
        "current_previous_feature_values_checked": checked_features,
        "comparison_rows_checked": checked_comparisons,
        "overlap_pairs_checked": len(tables["overlaps"]),
        "outlier_rows": len(expected_outliers),
        "unique_outlier_rounds": len(tables["rounds"]),
        "embedded_images_per_report": 46,
        "artifact_hashes_checked": len(manifest["artifacts"]),
        "quartiles": "independent sorted linear interpolation",
        "no_analysis_module_import": True,
    }
    (output / "independent_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
