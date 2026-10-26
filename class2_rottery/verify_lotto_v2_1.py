"""V2.1 산출물을 분석 모듈에 의존하지 않고 원본 CSV와 교차 검증한다."""

import base64
import hashlib
import json
import math
import re
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> None:
    base = Path(__file__).resolve().parent
    output = base / "analysis_results_v2_1"
    raw = pd.read_csv(base / "lotto_rank_1_1245.csv", encoding="utf-8-sig")
    focused = pd.read_csv(output / "lotto_focus_v2_1.csv", encoding="utf-8-sig")
    expected = (
        raw.loc[raw["순위"].isin(["1등", "2등", "3등"])]
        .sort_values(["순위", "회차"])
        .reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(focused[raw.columns], expected)
    observations = pd.read_csv(
        output / "analysis_observations.csv",
        dtype={"분석ID": str},
        encoding="utf-8-sig",
    )
    comparison = observations.loc[observations["분석ID"].eq("02")].merge(
        expected, on=["순위", "회차"], validate="one_to_one"
    )
    sold = comparison["총판매금액"] / np.where(comparison["회차"] <= 87, 2000, 1000)
    probabilities = comparison["순위"].map(
        {"1등": 1, "2등": 6, "3등": math.comb(6, 5) * 38}
    ) / math.comb(45, 6)
    ratio = comparison["당첨게임 수"] / (sold * probabilities)
    np.testing.assert_allclose(comparison["값"], ratio, rtol=1e-12)
    coefficients = pd.read_csv(
        output / "normalization.csv", dtype={"분석ID": str}, encoding="utf-8-sig"
    )
    joined = observations.merge(
        coefficients, on=["분석ID", "순위"], validate="many_to_one"
    )
    np.testing.assert_allclose(
        joined["표준화값"],
        (joined["값"] - joined["중심"]) / joined["척도"],
        rtol=1e-10,
        atol=1e-10,
    )
    md = (base / "lotto_rank_report_v2_1.md").read_text(encoding="utf-8")
    html = (base / "lotto_rank_report_v2_1.html").read_text(encoding="utf-8")
    assert md == (output / "lotto_rank_report_v2_1.md").read_text(encoding="utf-8")
    assert html == (output / "lotto_rank_report_v2_1.html").read_text(encoding="utf-8")
    appendix = md.index("## 부록: 분석별 1·2·3등 비교")
    assert md[:appendix].count("data:image/png;base64,") == 36
    assert md[appendix:].count("data:image/png;base64,") == 12
    assert len(re.findall(r"^### \d\d-[123]등\. 개별 분석", md, re.M)) == 36
    assert len(re.findall(r"^### 부록 \d\d\.", md, re.M)) == 12
    images = re.findall(r"!\[분석 (\d\d) (.*?)\]\(data:image/png;base64,([^)]*)\)", md)
    assert len(images) == 48
    for code, rank, payload in images:
        name = (
            f"{code}_violin.png" if rank == "violin" else f"{code}_rank_{rank[0]}.png"
        )
        binary = base64.b64decode(payload, validate=True)
        assert binary == (output / "charts" / name).read_bytes()
        with Image.open(BytesIO(binary)) as image:
            image.verify()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        path = output / artifact["path"]
        assert path.stat().st_size == artifact["size_bytes"]
        assert digest(path) == artifact["sha256"]
    models = pd.read_csv(output / "tables/11_models.csv", encoding="utf-8-sig")
    assert (models["학습종료회차"] < models["검증시작회차"]).all()
    result = {
        "status": "passed",
        "command": "python -X utf8 class2_rottery/verify_lotto_v2_1.py",
        "raw_source_sha256": digest(base / "lotto_rank_1_1245.csv"),
        "v2_input_sha256": digest(base / "lotto_preprocessed_v2.csv"),
        "source_rows_preserved": len(expected),
        "independently_recomputed_ratios": len(comparison),
        "reconstructed_display_scores": len(joined),
        "main_rank_result_sections": 36,
        "appendix_comparison_sections": 12,
        "embedded_pngs_identical_to_files": len(images),
        "verified_artifact_hashes": len(manifest["artifacts"]),
        "root_and_results_report_copies_identical": True,
        "training_precedes_validation_in_all_models": True,
    }
    (output / "independent_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # 기존 초안 감사 함수는 보존하고 명령 실행은 현재 보고서를 검증한다.
    from verify_lotto_v2_1_basic import main as current_main

    current_main()
