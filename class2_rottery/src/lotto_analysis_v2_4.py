"""전체 회차의 정규화 Y와 정상·상방 이상 집단을 공통 절차로 비교한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from build_lotto_v2_4_scaffold import FEATURES, RANKS, TARGET_FEATURE, TOPICS
from paths import DATA, REPORTS


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def upper_rule(values: pd.Series) -> dict:
    values = values.dropna().astype(float)
    if values.empty or not np.isfinite(values).all():
        raise ValueError("유효한 유한 Y값이 필요하다.")
    q1, q3 = values.quantile([0.25, 0.75], interpolation="linear")
    iqr = q3 - q1
    return {
        "Q1": float(q1),
        "Q3": float(q3),
        "IQR": float(iqr),
        "상한": float(q3 + 1.5 * iqr),
        "강한상한": float(q3 + 3 * iqr),
    }


def classify_y(values: pd.Series, upper: float, strong: float) -> np.ndarray:
    return np.select([values.gt(strong), values.gt(upper)], [2, 1], default=0)


def label_sections(observations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    groups, fences = [], []
    for (code, rank), group in observations.groupby(["주제", "순위"], sort=False):
        frame = group.copy()
        rule = upper_rule(frame["y"])
        for key, value in rule.items():
            frame[key] = value
        frame["강도"] = classify_y(frame["y"], rule["상한"], rule["강한상한"])
        frame["상방"] = frame["강도"].gt(0)
        groups.append(frame)
        fences.append(
            {
                "주제": code,
                "순위": rank,
                "관측수": len(frame),
                **rule,
                "IQR퇴화": rule["IQR"] == 0,
                "상방수": int(frame["상방"].sum()),
                "강한상방수": int(frame["강도"].eq(2).sum()),
            }
        )
    return pd.concat(groups, ignore_index=True), pd.DataFrame(fences)


def make_features(data: pd.DataFrame) -> pd.DataFrame:
    frame = data[["회차", "순위"]].copy()
    factor = 1000 / data["게임가격_외부결합"]
    frame["current_sales"] = data["총판매금액"] * factor
    frame["current_total"] = data["등위별 총 당첨금"] * factor
    frame["current_winners"] = data["당첨게임 수"].astype(float)
    frame["current_prize"] = (data["1게임당 당첨금"] * factor).where(
        data["당첨게임 수"].gt(0)
    )
    previous = frame.rename(
        columns={
            "회차": "직전회차",
            **{
                key: key.replace("current_", "previous_")
                for key in frame
                if key.startswith("current_")
            },
        }
    ).copy()
    previous["회차"] = previous["직전회차"] + 1
    return frame.merge(previous, on=["회차", "순위"], how="left", validate="one_to_one")


def compare(cases: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (code, rank), group in cases.groupby(["주제", "순위"], sort=False):
        for feature, name, unit, _ in FEATURES:
            row = {
                "주제": code,
                "순위": rank,
                "특징": feature,
                "특징명": name,
                "단위": unit,
                "선정Y자체": TARGET_FEATURE.get(code) == feature,
            }
            for prefix, flag in (("정상", False), ("이상", True)):
                observations = group.loc[group["상방"].eq(flag), feature]
                values = observations.dropna()
                row.update(
                    {
                        prefix + "집단수": len(observations),
                        prefix + "유효수": len(values),
                        prefix + "결측수": int(observations.isna().sum()),
                        prefix + "중앙값": float(values.median())
                        if len(values)
                        else np.nan,
                        prefix + "25%": float(values.quantile(0.25))
                        if len(values)
                        else np.nan,
                        prefix + "75%": float(values.quantile(0.75))
                        if len(values)
                        else np.nan,
                    }
                )
            row["비교가능"] = row["정상유효수"] > 0 and row["이상유효수"] > 0
            row["중앙값차이"] = row["이상중앙값"] - row["정상중앙값"]
            rows.append(row)
    return pd.DataFrame(rows)


def overlap(labeled: pd.DataFrame) -> pd.DataFrame:
    groups = {
        (code, rank): group.set_index("회차")["상방"]
        for (code, rank), group in labeled.groupby(["주제", "순위"], sort=False)
    }
    rows = []
    for (code, rank), left in groups.items():
        for (other, other_rank), right in groups.items():
            common = left.index.intersection(right.index)
            a, b = left.loc[common], right.loc[common]
            row = {
                "기준주제": code,
                "기준등수": rank,
                "비교주제": other,
                "비교등수": other_rank,
                "공통관측수": len(common),
                "같은Y계열": TARGET_FEATURE.get(code, code)
                == TARGET_FEATURE.get(other, other),
            }
            for prefix, flag in (("정상", False), ("이상", True)):
                mask = a.eq(flag)
                count, numerator = int(mask.sum()), int(b.loc[mask].sum())
                row.update(
                    {
                        prefix + "분모": count,
                        prefix + "동시수": numerator,
                        prefix + "발생비율": numerator / count if count else np.nan,
                    }
                )
            row["비율차이"] = row["이상발생비율"] - row["정상발생비율"]
            rows.append(row)
    return pd.DataFrame(rows)


def analyze(source: str | Path) -> dict:
    original = pd.read_csv(source, encoding="utf-8-sig")
    data = (
        original.loc[original["순위"].isin(RANKS)]
        .sort_values(["순위", "회차"])
        .reset_index(drop=True)
    )
    if data.duplicated(["회차", "순위"]).any():
        raise ValueError("회차·등수 키 중복")
    if (
        not data.groupby("회차")["순위"]
        .agg(set)
        .map(lambda values: values == set(RANKS))
        .all()
    ):
        raise ValueError("회차별 등수 누락")
    features = make_features(data)
    frames, omissions = [], []
    for code, *_ in TOPICS:
        frame = features.copy()
        reasons = pd.Series("", index=frame.index)
        if code in ("01", "08", "09A", "09D", "10"):
            reasons.loc[frame["current_prize"].isna()] = (
                "현재 무당첨: 한 게임당 지급액 결측"
            )
        if code.startswith("09"):
            reasons.loc[frame["직전회차"].isna()] = "정확한 직전 회차 없음"
        if code in ("09A", "09C"):
            mask = frame["직전회차"].notna() & frame["previous_prize"].isna()
            reasons.loc[mask] = reasons.loc[mask].map(
                lambda value: (value + "; " if value else "")
                + "직전 무당첨: 한 게임당 지급액 결측"
            )
        omitted = frame.loc[reasons.ne(""), ["회차", "순위"]].copy()
        omitted["주제"] = code
        omitted["제외이유"] = reasons.loc[omitted.index]
        omissions.append(omitted)
        frame = frame.loc[reasons.eq("")].copy()
        frame["주제"], frame["x"], frame["y"] = (
            code,
            frame["회차"].astype(float),
            frame["current_prize"],
        )
        if code in ("02", "09C"):
            frame["y"] = frame["current_winners"]
        elif code == "03":
            frame["y"] = frame["current_total"]
        elif code == "09A":
            frame["y"] = frame["current_prize"] - frame["previous_prize"]
        elif code == "09B":
            frame["y"] = frame["current_winners"] - frame["previous_winners"]
        if code == "08":
            frame["x"] = frame["current_sales"] / 1000
        elif code == "09C":
            frame["x"] = frame["previous_prize"]
        elif code == "09D":
            frame["x"] = frame["previous_winners"]
        frames.append(frame[["주제", "순위", "회차", "x", "y"]])
    observations = pd.concat(frames, ignore_index=True)
    labeled, fences = label_sections(observations)
    cases = labeled.merge(features, on=["회차", "순위"], validate="many_to_one")
    outliers = cases.loc[cases["상방"]].copy()
    rounds = (
        outliers.groupby("회차")
        .agg(
            검출항목수=("주제", "size"),
            섹션수=("주제", "nunique"),
            등수수=("순위", "nunique"),
        )
        .reset_index()
    )
    return {
        "focus": data,
        "observations": observations,
        "labeled": labeled,
        "fences": fences,
        "features": features,
        "cases": cases,
        "outliers": outliers,
        "comparisons": compare(cases),
        "overlaps": overlap(labeled),
        "excluded": pd.concat(omissions, ignore_index=True),
        "rounds": rounds,
        "source_hash": digest(Path(source)),
    }


def run(source: str | Path, output: str | Path) -> dict:
    from lotto_report_v2_4 import render

    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    before = digest(source)
    result = analyze(source)
    for key, value in result.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(output / f"{key}.csv", index=False, encoding="utf-8-sig")
    rendered = render(result, output)
    validation = {
        "section_rank_rules": len(result["fences"]) == 27,
        "comparisons": len(result["comparisons"]) == 216,
        "overlaps": len(result["overlaps"]) == 729,
        "figures": rendered["figure_count"] == 99,
        "input_unchanged": digest(source) == before,
    }
    validation["status"] = "passed" if all(validation.values()) else "failed"
    (output / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if validation["status"] != "passed":
        raise RuntimeError("V2.4 산출물 생성 검증 실패")
    manifest = {
        "version": "2.4",
        "stage": "actual_experiment",
        "output_format": "markdown_only",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -X utf8 class2_rottery/src/lotto_analysis_v2_4.py",
        "source": str(source),
        "source_sha256": before,
        "raw_source_sha256": digest(DATA / "lotto_rank_1_1245.csv"),
        "outlier_rows": len(result["outliers"]),
        "unique_rounds": len(result["rounds"]),
        "images": rendered["figure_count"],
        "normalization": "all money * 1000 / game price; normalize before differencing",
        "rules": "own section/rank Y, strict upper 1.5/3 IQR, all eligible draws",
        "artifacts": [
            {
                "path": str(path.relative_to(output)),
                "size_bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in sorted(output.rglob("*"))
            if path.is_file()
            and path.suffix != ".html"
            and path.name
            not in {"manifest.json", "execution_log.json", "independent_audit.json"}
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"result": result, "validation": validation, "rendered": rendered}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=DATA / "lotto_preprocessed_v2.csv"
    )
    parser.add_argument(
        "--output", type=Path, default=REPORTS / "v2_4" / "analysis_results_v2_4"
    )
    args = parser.parse_args()
    generated = run(args.source, args.output)
    for extension in ("md",):
        name = f"lotto_rank_report_v2_4.{extension}"
        (REPORTS / "v2_4" / name).write_bytes((args.output / name).read_bytes())
    print(
        json.dumps(
            {
                "status": generated["validation"]["status"],
                "images": 99,
                "outlier_rows": len(generated["result"]["outliers"]),
                "unique_rounds": len(generated["result"]["rounds"]),
                "report": str(REPORTS / "v2_4" / "lotto_rank_report_v2_4.md"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
