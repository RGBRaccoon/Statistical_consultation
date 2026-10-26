"""로또 6/45 V2 전처리와 회차·등수별 Step 1 기술 분석.

이 모듈은 원본 CSV의 15개 칼럼을 그대로 보존한 뒤 가격 제도 차이를
1,000원 게임 기준으로 환산한다. Step 1에서는 1~5등을 합치지 않고
각 등수의 회차별 관측값과 이론적 기준값을 계산한다.

실행 예:
    python class2_rottery/lotto_analysis_v2.py
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter, PercentFormatter


TOTAL_COMBINATIONS = math.comb(45, 6)
RANK_FAVORABLE_COMBINATIONS = {
    "1등": 1,
    "2등": 6,
    "3등": math.comb(6, 5) * 38,
    "4등": math.comb(6, 4) * math.comb(39, 2),
    "5등": math.comb(6, 3) * math.comb(39, 3),
}
RANKS = tuple(RANK_FAVORABLE_COMBINATIONS)
ORIGINAL_COLUMNS = (
    "회차",
    "순위",
    "등위별 총 당첨금",
    "당첨게임 수",
    "1게임당 당첨금",
    "당첨기준",
    "비고",
    "총판매금액",
    "당첨번호1",
    "당첨번호2",
    "당첨번호3",
    "당첨번호4",
    "당첨번호5",
    "당첨번호6",
    "보너스번호",
)
STEP1_METRICS = (
    "1000원기준_1게임당당첨금",
    "당첨게임 수",
    "1억게임당_당첨게임수",
    "이론_기대당첨게임수",
    "관측_기대_비율",
    "등위별_지급률",
)
PRICE_SOURCES = (
    {
        "title": "국가법령정보센터 판결문(초기 게임 가격 2,000원)",
        "url": "https://law.go.kr/LSW/precInfoP.do?evtNo=2007%EB%82%9810421",
        "use": "초기 1게임 가격 근거",
    },
    {
        "title": "동아일보 2004-07-21 기사(제88회 판매분부터 1,000원)",
        "url": "https://www.donga.com/news/Series/article/all/20040721/8086157/1",
        "use": "가격 전환 경계 근거",
    },
    {
        "title": "동행복권 로또6/45 소개",
        "url": "https://www.dhlottery.co.kr/lt645/intro",
        "use": "현재 게임 가격과 당첨 구조 참고",
    },
)


def sha256_file(path: str | Path) -> str:
    """파일의 SHA-256 해시를 계산한다."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def game_price_for_round(round_number: int) -> int:
    """회차별 실제 1게임 판매 가격을 반환한다."""
    return 2_000 if int(round_number) <= 87 else 1_000


def _parse_purchase_modes(value: object) -> tuple[int, int, int]:
    text = str(value)
    match = re.search(r"자동([0-9]+)\s*수동([0-9]+)\s*반자동([0-9]+)", text)
    if not match:
        return (0, 0, 0)
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _validate_source_schema(source: pd.DataFrame) -> None:
    missing = [column for column in ORIGINAL_COLUMNS if column not in source.columns]
    if missing:
        raise ValueError(f"원본 필수 칼럼 누락: {missing}")
    if source[list(ORIGINAL_COLUMNS)].isna().any().any():
        raise ValueError("원본 필수 칼럼에 결측값이 있다.")
    if source.duplicated(["회차", "순위"]).any():
        raise ValueError("(회차, 순위) 중복 행이 있다.")
    rank_sets = source.groupby("회차")["순위"].agg(set)
    expected = set(RANKS)
    if not rank_sets.map(lambda values: values == expected).all():
        raise ValueError("모든 회차가 1~5등 행을 정확히 하나씩 갖지 않는다.")


def preprocess_lotto_data(source: pd.DataFrame) -> pd.DataFrame:
    """원본을 보존하며 가격 제도와 회차별 비교용 파생 칼럼을 추가한다."""
    _validate_source_schema(source)
    result = source.copy(deep=True)

    result["게임가격_외부결합"] = (
        result["회차"].map(game_price_for_round).astype("int64")
    )
    result["가격구간"] = np.where(
        result["회차"] <= 87, "1~87회: 2000원", "88회 이후: 1000원"
    )
    result["가격정규화계수"] = 1_000 / result["게임가격_외부결합"]

    sales_remainder = result["총판매금액"] % result["게임가격_외부결합"]
    if not sales_remainder.eq(0).all():
        bad_rounds = result.loc[sales_remainder.ne(0), "회차"].unique().tolist()
        raise ValueError(
            f"총판매금액이 게임 가격으로 나누어지지 않는 회차: {bad_rounds}"
        )
    result["추정_판매게임수"] = (
        result["총판매금액"] // result["게임가격_외부결합"]
    ).astype("int64")

    result["1000원기준_1게임당당첨금"] = (
        result["1게임당 당첨금"] * result["가격정규화계수"]
    )
    result["1000원기준_등위별총당첨금"] = (
        result["등위별 총 당첨금"] * result["가격정규화계수"]
    )
    result["당첨게임률"] = result["당첨게임 수"] / result["추정_판매게임수"]
    result["1억게임당_당첨게임수"] = result["당첨게임률"] * 100_000_000
    result["등위별_지급률"] = result["등위별 총 당첨금"] / result["총판매금액"]

    probability_map = {
        rank: favorable / TOTAL_COMBINATIONS
        for rank, favorable in RANK_FAVORABLE_COMBINATIONS.items()
    }
    result["이론_등수당첨확률"] = result["순위"].map(probability_map)
    result["이론_기대당첨게임수"] = (
        result["추정_판매게임수"] * result["이론_등수당첨확률"]
    )
    result["관측_기대_비율"] = result["당첨게임 수"] / result["이론_기대당첨게임수"]

    result["4등지급구조"] = np.where(result["회차"] <= 400, "변동 지급", "고정 50000원")
    result["5등지급구조"] = np.where(
        result["회차"] <= 87, "고정 10000원", "고정 5000원"
    )
    result["구매방식자료_사용가능"] = result["회차"] >= 262

    modes = result["비고"].map(_parse_purchase_modes)
    mode_frame = pd.DataFrame(
        modes.tolist(),
        columns=["1등_자동", "1등_수동", "1등_반자동"],
        index=result.index,
    )
    for column in mode_frame.columns:
        result[column] = mode_frame[column].where(result["회차"] >= 262).astype("Int64")

    return result


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def validate_preprocessed_data(
    source: pd.DataFrame, processed: pd.DataFrame
) -> dict[str, Any]:
    """V2 전처리의 보존성·경계·산식을 독립된 검사 항목으로 기록한다."""
    checks: list[dict[str, Any]] = []
    original_preserved = False
    try:
        pd.testing.assert_frame_equal(
            processed[list(source.columns)].reset_index(drop=True),
            source.reset_index(drop=True),
        )
        original_preserved = True
    except AssertionError:
        pass
    checks.append(
        _check(
            "원본 행·칼럼 값 보존",
            original_preserved,
            f"원본 {len(source):,}행, 전처리 {len(processed):,}행",
        )
    )

    five_rows = processed.groupby("회차").size().eq(5).all()
    rank_complete = (
        processed.groupby("회차")["순위"]
        .agg(set)
        .map(lambda values: values == set(RANKS))
        .all()
    )
    checks.append(
        _check(
            "회차별 1~5등 분리·완전성",
            bool(five_rows and rank_complete),
            f"회차 {processed['회차'].nunique():,}개, 각 5행",
        )
    )

    price_ok = (
        processed.loc[processed["회차"] <= 87, "게임가격_외부결합"].eq(2_000).all()
        and processed.loc[processed["회차"] >= 88, "게임가격_외부결합"].eq(1_000).all()
    )
    checks.append(
        _check("가격 경계", bool(price_ok), "1~87회 2,000원; 88회 이후 1,000원")
    )

    sold_expected = processed["총판매금액"] // processed["게임가격_외부결합"]
    sold_ok = processed["추정_판매게임수"].eq(sold_expected).all()
    checks.append(
        _check("판매게임 수 산식", bool(sold_ok), "총판매금액 ÷ 실제 게임 가격")
    )

    normalized_expected = (
        processed["1게임당 당첨금"] * 1_000 / processed["게임가격_외부결합"]
    )
    normalized_ok = np.allclose(
        processed["1000원기준_1게임당당첨금"], normalized_expected, rtol=0, atol=1e-9
    )
    checks.append(
        _check(
            "금액 정규화 산식", bool(normalized_ok), "원금액 × 1,000 ÷ 실제 게임 가격"
        )
    )

    rank5 = processed.loc[processed["순위"] == "5등"]
    rank5_ok = rank5["1000원기준_1게임당당첨금"].eq(5_000).all()
    checks.append(
        _check("5등 가격구간 정규화", bool(rank5_ok), "전 회차 1,000원 기준 5,000원")
    )

    rank4_fixed = (
        processed.loc[
            (processed["순위"] == "4등") & (processed["회차"] >= 401), "1게임당 당첨금"
        ]
        .eq(50_000)
        .all()
    )
    checks.append(
        _check("4등 고정 지급 경계", bool(rank4_fixed), "401회 이후 50,000원")
    )

    rank1_modes = processed.loc[
        (processed["순위"] == "1등") & (processed["회차"] >= 262)
    ]
    mode_sum = rank1_modes[["1등_자동", "1등_수동", "1등_반자동"]].sum(axis=1)
    mode_ok = mode_sum.eq(rank1_modes["당첨게임 수"]).all()
    checks.append(
        _check(
            "1등 구매방식 자료 경계·합계",
            bool(mode_ok),
            "262회 이후 자동+수동+반자동 = 1등 당첨게임 수",
        )
    )

    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "checks": checks,
    }


def build_step1_rank_summary(processed: pd.DataFrame) -> pd.DataFrame:
    """등수별로 분리한 Step 1 기술통계표를 만든다."""
    rows: list[dict[str, Any]] = []
    for rank in RANKS:
        rank_data = processed.loc[processed["순위"] == rank]
        for metric in STEP1_METRICS:
            values = rank_data[metric].astype(float)
            rows.append(
                {
                    "순위": rank,
                    "지표": metric,
                    "회차수": int(values.count()),
                    "평균": float(values.mean()),
                    "표준편차": float(values.std(ddof=1)),
                    "최솟값": float(values.min()),
                    "25%": float(values.quantile(0.25)),
                    "중앙값": float(values.median()),
                    "75%": float(values.quantile(0.75)),
                    "최댓값": float(values.max()),
                }
            )
    return pd.DataFrame(rows)


def build_step1_period_summary(processed: pd.DataFrame) -> pd.DataFrame:
    """가격 구간별·등수별 관측/기대 수준과 지급률을 요약한다."""
    return (
        processed.groupby(["가격구간", "순위"], sort=False)
        .agg(
            시작회차=("회차", "min"),
            종료회차=("회차", "max"),
            회차수=("회차", "count"),
            평균_판매게임수=("추정_판매게임수", "mean"),
            평균_당첨게임수=("당첨게임 수", "mean"),
            평균_이론기대당첨게임수=("이론_기대당첨게임수", "mean"),
            평균_관측기대비율=("관측_기대_비율", "mean"),
            중앙_관측기대비율=("관측_기대_비율", "median"),
            평균_등위별지급률=("등위별_지급률", "mean"),
            평균_1000원기준당첨금=("1000원기준_1게임당당첨금", "mean"),
        )
        .reset_index()
    )


def _configure_plots() -> None:
    plt.rcParams.update(
        {
            "font.family": "Malgun Gothic",
            "axes.unicode_minus": False,
            "figure.dpi": 120,
            "savefig.dpi": 150,
            "axes.grid": True,
            "grid.alpha": 0.25,
        }
    )


def _won_formatter(value: float, _position: int) -> str:
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.1f}억"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.0f}만"
    return f"{value:,.0f}"


def create_step1_charts(processed: pd.DataFrame, chart_dir: str | Path) -> list[Path]:
    """각 등수를 별도의 3단 패널 그림으로 저장한다."""
    _configure_plots()
    chart_path = Path(chart_dir)
    chart_path.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []

    for rank_number, rank in enumerate(RANKS, start=1):
        data = processed.loc[processed["순위"] == rank].sort_values("회차").copy()
        data["당첨금_25회중앙값"] = (
            data["1000원기준_1게임당당첨금"]
            .rolling(25, center=True, min_periods=1)
            .median()
        )
        data["관측기대비율_25회중앙값"] = (
            data["관측_기대_비율"].rolling(25, center=True, min_periods=1).median()
        )
        data["지급률_25회중앙값"] = (
            data["등위별_지급률"].rolling(25, center=True, min_periods=1).median()
        )

        figure, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)
        figure.suptitle(f"{rank} 회차별 Step 1 관측값", fontsize=16, fontweight="bold")

        axes[0].plot(
            data["회차"],
            data["1000원기준_1게임당당첨금"],
            color="#8fb9dc",
            linewidth=0.7,
            alpha=0.75,
            label="회차 관측값",
        )
        axes[0].plot(
            data["회차"],
            data["당첨금_25회중앙값"],
            color="#174a7e",
            linewidth=2,
            label="25회 이동 중앙값",
        )
        axes[0].set_ylabel("1게임당 당첨금")
        axes[0].yaxis.set_major_formatter(FuncFormatter(_won_formatter))
        axes[0].legend(loc="upper right")

        axes[1].plot(
            data["회차"],
            data["관측_기대_비율"],
            color="#f2ad6f",
            linewidth=0.7,
            alpha=0.75,
            label="관측/기대",
        )
        axes[1].plot(
            data["회차"],
            data["관측기대비율_25회중앙값"],
            color="#c44e00",
            linewidth=2,
            label="25회 이동 중앙값",
        )
        axes[1].axhline(
            1, color="#333333", linestyle="--", linewidth=1, label="이론 기준 1"
        )
        axes[1].set_ylabel("관측/기대 비율")
        axes[1].legend(loc="upper right")

        axes[2].plot(
            data["회차"],
            data["등위별_지급률"],
            color="#87c38f",
            linewidth=0.7,
            alpha=0.75,
            label="회차 관측값",
        )
        axes[2].plot(
            data["회차"],
            data["지급률_25회중앙값"],
            color="#246b32",
            linewidth=2,
            label="25회 이동 중앙값",
        )
        axes[2].set_ylabel("총판매금액 대비 지급률")
        axes[2].yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
        axes[2].set_xlabel("회차")
        axes[2].legend(loc="upper right")

        for axis in axes:
            axis.axvline(87.5, color="#6f42c1", linestyle=":", linewidth=1.2)
            if rank == "4등":
                axis.axvline(400.5, color="#7a5c00", linestyle=":", linewidth=1.2)
        axes[0].text(
            87.5,
            0.98,
            "가격 전환",
            transform=axes[0].get_xaxis_transform(),
            ha="left",
            va="top",
            color="#6f42c1",
        )
        if rank == "4등":
            axes[0].text(
                400.5,
                0.86,
                "4등 고정 지급",
                transform=axes[0].get_xaxis_transform(),
                ha="left",
                va="top",
                color="#7a5c00",
            )

        figure.tight_layout(rect=(0, 0, 1, 0.97))
        output = chart_path / f"step1_rank_{rank_number}.png"
        figure.savefig(output, bbox_inches="tight")
        plt.close(figure)
        output_paths.append(output)

    return output_paths


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def _image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def write_report(
    processed: pd.DataFrame,
    validation: dict[str, Any],
    rank_summary: pd.DataFrame,
    period_summary: pd.DataFrame,
    chart_paths: list[Path],
    report_path: str | Path,
    source_path: str | Path,
    processed_path: str | Path,
) -> None:
    """검증 결과와 등수별 그림을 포함한 자체 포함형 Markdown을 쓴다."""
    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)

    validation_rows = [
        [item["name"], "통과" if item["passed"] else "실패", item["detail"]]
        for item in validation["checks"]
    ]
    source_rows = [
        [item["use"], f"[{item['title']}]({item['url']})"] for item in PRICE_SOURCES
    ]

    headline_rows: list[list[str]] = []
    for rank in RANKS:
        rank_data = processed.loc[processed["순위"] == rank]
        headline_rows.append(
            [
                rank,
                f"{rank_data['당첨게임 수'].mean():,.2f}",
                f"{rank_data['이론_기대당첨게임수'].mean():,.2f}",
                f"{rank_data['관측_기대_비율'].median():.4f}",
                f"{rank_data['등위별_지급률'].mean():.4%}",
                f"{rank_data['1000원기준_1게임당당첨금'].median():,.0f}원",
            ]
        )

    sections = [
        "# 로또 6/45 회차·등수별 분석 V2 — Step 0~1\n",
        "## 이번 문서의 범위\n",
        "이 문서는 **Step 0(가격 제도 표준화)**과 **Step 1(회차 단위·등수별 기술 분석)**까지만 기록한다. "
        "이상 회차의 판정 기준과 클러스터링은 정하지 않았다. **Step 2는 아직 수행하지 않았다.**\n",
        "## 데이터와 외부 근거\n",
        f"- 원본: `{Path(source_path).name}` ({len(processed):,}행, {processed['회차'].nunique():,}회차)\n"
        f"- 원본 SHA-256: `{sha256_file(source_path)}`\n"
        f"- 전처리본: `{Path(processed_path).name}`\n"
        f"- 전처리본 SHA-256: `{sha256_file(processed_path)}`\n",
        _markdown_table(["용도", "출처"], source_rows) + "\n",
        "가격 자료는 원본 CSV 칼럼이 아니라 위 외부 자료에서 결합했다. 적용 경계는 1~87회 2,000원, "
        "88회 이후 1,000원이다. 원본의 5등 당첨금도 87회 10,000원, 88회 5,000원으로 같은 경계를 보인다.\n",
        "자료 간 표현 차이도 남긴다. 국가법령정보센터 판결문에는 가격 인하 시점을 ‘제87회부터’라고 적은 "
        "대목이 있으나, 당시 기사에는 ‘제88회차(8월 7일 추첨) 판매분부터’라고 적혀 있다. 이 전처리는 "
        "후자의 구체적 회차 표기와 CSV의 5등 지급액 전환 경계가 일치하므로 제88회부터 1,000원을 적용했다.\n",
        "## Step 0: 전처리 정의\n",
        "- `추정_판매게임수 = 총판매금액 ÷ 해당 회차 게임 가격`\n"
        "- `가격정규화계수 = 1,000 ÷ 해당 회차 게임 가격`\n"
        "- `1000원기준_당첨금 = 원 당첨금 × 가격정규화계수`\n"
        "- `당첨게임률 = 당첨게임 수 ÷ 추정_판매게임수`\n"
        "- `1억게임당_당첨게임수 = 당첨게임률 × 100,000,000`\n"
        "- `등위별_지급률 = 등위별 총 당첨금 ÷ 총판매금액`\n"
        "- `이론_기대당첨게임수 = 추정_판매게임수 × 이론_등수당첨확률`\n"
        "- `관측_기대_비율 = 당첨게임 수 ÷ 이론_기대당첨게임수`\n\n"
        "당첨게임 수는 가격으로 환산하지 않았다. 세금, 물가, 결측 대체, 이상치 제거, 표준점수화는 적용하지 않았다.\n",
        "### 전처리 검증\n",
        _markdown_table(["검사", "결과", "세부"], validation_rows) + "\n",
        "## Step 1: 등수별 회차 분석\n",
        "다음 표와 그림은 1~5등을 각각 분리해 계산했다. `관측/기대 비율`의 1은 판매된 모든 게임이 "
        "동일한 조합 확률을 갖는다는 수학적 기준이다. 이 단계에서는 큰 값이나 작은 값을 이상으로 판정하지 않는다.\n",
        _markdown_table(
            [
                "순위",
                "평균 당첨게임",
                "평균 이론기대",
                "관측/기대 중앙값",
                "평균 지급률",
                "1000원기준 당첨금 중앙값",
            ],
            headline_rows,
        )
        + "\n",
        "상세 기술통계는 `analysis_results_v2/step1_rank_summary.csv`, 가격 구간별 비교는 "
        "`analysis_results_v2/step1_period_summary.csv`에 저장했다.\n",
    ]

    for rank, chart_path in zip(RANKS, chart_paths):
        sections.extend(
            [
                f"### {rank}\n",
                f"![{rank} 회차별 Step 1 관측값]({_image_data_uri(chart_path)})\n",
            ]
        )

    sections.extend(
        [
            "## Step 2 논의 전에 남겨 둔 판단\n",
            "Step 2에서는 이 그림과 표를 육안으로 확인한 뒤, 어떤 값을 ‘통계적으로 따라야 하는 표준’으로 "
            "둘지 먼저 정해야 한다. 후보 지표는 관측/기대 비율, 등위별 지급률, 1,000원 기준 당첨금이다. "
            "기준 분포·기간 효과·여러 회차를 동시에 검사할 때의 보정 방법은 아직 선택하지 않았다.\n",
            "## 재현 경로\n",
            "```powershell\npython -X utf8 class2_rottery/lotto_analysis_v2.py\n```\n",
            "프로그램 실행 시 전처리 CSV, 검증 JSON, 메타데이터 JSON, Step 1 표, 그림 5개와 이 보고서를 다시 생성한다.\n",
        ]
    )
    report.write_text("\n".join(sections), encoding="utf-8")


def run_v2(
    source_path: str | Path,
    processed_path: str | Path,
    output_dir: str | Path,
    report_path: str | Path,
) -> dict[str, Any]:
    """Step 0과 Step 1 전체 파이프라인을 실행하고 산출물 위치를 반환한다."""
    source_file = Path(source_path)
    processed_file = Path(processed_path)
    results_dir = Path(output_dir)
    report_file = Path(report_path)
    results_dir.mkdir(parents=True, exist_ok=True)
    processed_file.parent.mkdir(parents=True, exist_ok=True)

    source = pd.read_csv(source_file, encoding="utf-8-sig")
    processed = preprocess_lotto_data(source)
    validation = validate_preprocessed_data(source, processed)
    if validation["status"] != "passed":
        raise RuntimeError("전처리 검증 실패: step0_validation.json을 확인해야 한다.")

    processed.to_csv(processed_file, index=False, encoding="utf-8-sig")
    rank_summary = build_step1_rank_summary(processed)
    period_summary = build_step1_period_summary(processed)
    rank_summary.to_csv(
        results_dir / "step1_rank_summary.csv", index=False, encoding="utf-8-sig"
    )
    period_summary.to_csv(
        results_dir / "step1_period_summary.csv", index=False, encoding="utf-8-sig"
    )

    validation_path = results_dir / "step0_validation.json"
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    chart_paths = create_step1_charts(processed, results_dir / "charts")
    write_report(
        processed,
        validation,
        rank_summary,
        period_summary,
        chart_paths,
        report_file,
        source_file,
        processed_file,
    )

    outputs = [
        processed_file,
        validation_path,
        results_dir / "step1_rank_summary.csv",
        results_dir / "step1_period_summary.csv",
        *chart_paths,
        report_file,
    ]
    artifact_records = [
        {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in outputs
    ]
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_command": "python -X utf8 class2_rottery/lotto_analysis_v2.py",
        "source_path": str(source_file),
        "input_sha256": sha256_file(source_file),
        "processed_path": str(processed_file),
        "output_sha256": sha256_file(processed_file),
        "source_rows": len(source),
        "processed_rows": len(processed),
        "rounds": int(processed["회차"].nunique()),
        "price_rule": {"1-87": 2_000, "88+": 1_000},
        "tax_applied": False,
        "step2_executed": False,
        "price_sources": list(PRICE_SOURCES),
        "outputs": [str(path) for path in outputs],
        "artifact_records": artifact_records,
    }
    metadata_path = results_dir / "step0_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "validation": validation,
        "processed_path": processed_file,
        "report_path": report_file,
        "metadata_path": metadata_path,
        "chart_paths": chart_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--source", type=Path, default=base_dir / "lotto_rank_1_1245.csv"
    )
    parser.add_argument(
        "--processed", type=Path, default=base_dir / "lotto_preprocessed_v2.csv"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=base_dir / "analysis_results_v2"
    )
    parser.add_argument(
        "--report", type=Path, default=base_dir / "lotto_rank_report_v2.md"
    )
    args = parser.parse_args()

    result = run_v2(args.source, args.processed, args.output_dir, args.report)
    print(f"전처리 검증: {result['validation']['status']}")
    print(f"전처리본: {result['processed_path']}")
    print(f"보고서: {result['report_path']}")
    print(f"그림 수: {len(result['chart_paths'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
