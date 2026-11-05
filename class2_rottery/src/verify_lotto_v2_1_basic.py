"""승인된 일반 현황 보고서를 원본 CSV로 독립 대조한다."""

import base64
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from paths import DATA, REPORTS


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> None:
    output = REPORTS / "v2_1" / "analysis_results_v2_1_basic"
    raw = pd.read_csv(DATA / "lotto_rank_1_1245.csv", encoding="utf-8-sig")
    raw = (
        raw.loc[raw["순위"].isin(["1등", "2등", "3등"])]
        .sort_values(["순위", "회차"])
        .reset_index(drop=True)
    )
    focus = pd.read_csv(output / "focus.csv", encoding="utf-8-sig")
    pd.testing.assert_frame_equal(focus[raw.columns], raw)
    raw["prev_round"] = raw.groupby("순위")["회차"].shift(1)
    raw["prev_prize"] = raw.groupby("순위")["1게임당 당첨금"].shift(1)
    raw["prev_k"] = raw.groupby("순위")["당첨게임 수"].shift(1)
    raw["factor"] = np.where(raw["회차"] <= 87, 0.5, 1.0)
    raw["paid_adjusted"] = raw["1게임당 당첨금"] * raw["factor"]
    raw["prev_adjusted"] = raw.groupby("순위")["paid_adjusted"].shift(1)
    observed = pd.read_csv(
        output / "observations.csv", dtype={"주제": str}, encoding="utf-8-sig"
    )
    codes = ["01", "02", "03", "08", "09A", "09B", "09C", "09D", "10"]
    checked = 0
    for code in codes:
        mask = pd.Series(True, index=raw.index)
        if code in ("01", "08", "09A", "09D", "10"):
            mask &= raw["당첨게임 수"].gt(0)
        if code.startswith("09"):
            mask &= raw["prev_round"].eq(raw["회차"] - 1)
        if code in ("09A", "09C"):
            mask &= raw["prev_k"].gt(0)
        selected = raw.loc[mask].copy()
        y = selected["1게임당 당첨금"].astype(float)
        x = selected["회차"].astype(float)
        comparable = selected["paid_adjusted"]
        if code == "02":
            y = comparable = selected["당첨게임 수"].astype(float)
        elif code == "03":
            y = selected["등위별 총 당첨금"].astype(float)
            comparable = y * selected["factor"]
        elif code == "08":
            x = selected["총판매금액"] / np.where(selected["회차"] <= 87, 2000, 1000)
        elif code == "09A":
            y = selected["1게임당 당첨금"] - selected["prev_prize"]
            comparable = selected["paid_adjusted"] - selected["prev_adjusted"]
        elif code == "09B":
            y = comparable = selected["당첨게임 수"] - selected["prev_k"]
        elif code == "09C":
            x = selected["prev_prize"]
            y = comparable = selected["당첨게임 수"].astype(float)
        elif code == "09D":
            x = selected["prev_k"]
        elif code == "10":
            x = pd.Series(
                np.where(
                    selected["회차"] <= 87, 0, np.where(selected["회차"] <= 400, 1, 2)
                ),
                index=selected.index,
            )
            y = comparable = selected["paid_adjusted"]
        subset = observed.loc[observed["주제"].eq(code)].sort_values(["순위", "회차"])
        np.testing.assert_array_equal(subset["회차"], selected["회차"])
        np.testing.assert_array_equal(subset["순위"], selected["순위"])
        np.testing.assert_allclose(subset["x"], x, rtol=1e-12, atol=1e-10)
        np.testing.assert_allclose(subset["y"], y, rtol=1e-12, atol=1e-10)
        np.testing.assert_allclose(subset["비교값"], comparable, rtol=1e-12, atol=1e-10)
        checked += len(subset)
    coefficients = pd.read_csv(
        output / "normalization.csv", dtype={"주제": str}, encoding="utf-8-sig"
    )
    joined = observed.merge(coefficients, on=["주제", "순위"], validate="many_to_one")
    np.testing.assert_allclose(
        joined["비교Z"],
        (joined["비교값"] - joined["중심"]) / joined["표준편차"],
        rtol=1e-10,
        atol=1e-10,
    )
    md = (REPORTS / "v2_1" / "lotto_rank_report_v2_1.md").read_text(encoding="utf-8")
    html = (REPORTS / "v2_1" / "lotto_rank_report_v2_1.html").read_text(
        encoding="utf-8"
    )
    assert md == (output / "lotto_rank_report_v2_1.md").read_text(encoding="utf-8")
    assert html == (output / "lotto_rank_report_v2_1.html").read_text(encoding="utf-8")
    for document in (md, html):
        prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[image]", document)
        assert not re.search(
            r"상관계수|순위상관|spearman|pearson|correlation", prose, re.IGNORECASE
        )
    for path in (output / "summary.csv", *(output / "tables").glob("*_comparison.csv")):
        columns = pd.read_csv(path, encoding="utf-8-sig").columns
        assert not any(
            re.search(r"상관|spearman|pearson|correl", column, re.IGNORECASE)
            for column in columns
        )
    images = re.findall(r"!\[([0-9A-D]+) (.*?)\]\(data:image/png;base64,([^)]*)\)", md)
    assert len(images) == 36 and html.count("data:image/png;base64,") == 36
    for code in codes:
        assert (
            md.index(f"### {code}-1등")
            < md.index(f"### {code}-2등")
            < md.index(f"### {code}-3등")
            < md.index(f"### {code}-비교")
        )
    for code, rank, payload in images:
        name = (
            f"{code}_violin.png" if rank == "violin" else f"{code}_rank_{rank[0]}.png"
        )
        binary = base64.b64decode(payload, validate=True)
        assert binary == (output / "charts" / name).read_bytes()
        with Image.open(BytesIO(binary)) as image:
            image.verify()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        path = output / item["path"]
        assert path.stat().st_size == item["size_bytes"]
        assert digest(path) == item["sha256"]
    result = {
        "status": "passed",
        "command": "python -X utf8 class2_rottery/src/verify_lotto_v2_1_basic.py",
        "original_source_sha256": digest(DATA / "lotto_rank_1_1245.csv"),
        "v2_input_sha256": digest(DATA / "lotto_preprocessed_v2.csv"),
        "source_rows_preserved": len(raw),
        "approved_topic_ids": codes,
        "independently_verified_xy_and_comparison_rows": checked,
        "reconstructed_comparison_scores": len(joined),
        "embedded_pngs_verified": 36,
        "rank_then_comparison_order_verified": True,
        "correlation_free_reports_and_csvs": True,
        "verified_artifact_hashes": len(manifest["artifacts"]),
    }
    (output / "independent_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
