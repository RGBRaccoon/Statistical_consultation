"""로또 계산 없이 V2.4 검토용 문서와 빈 그림 배치를 생성한다."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import markdown
from PIL import Image, ImageDraw, ImageFont
from paths import REPORTS

RANKS = ("1등", "2등", "3등")
NORMALIZATION = "금액 × 1,000 / 해당 회차의 1게임 가격"
TOPICS = (
    (
        "01",
        "한 게임당 당첨금",
        "한 게임이 당첨됐을 때 받는 금액은 어떻게 분포하는가?",
        "회차",
        "정규화한 한 게임당 당첨금",
        "환산 원",
    ),
    (
        "02",
        "당첨게임 수",
        "회차마다 당첨게임은 몇 개인가?",
        "회차",
        "당첨게임 수",
        "게임",
    ),
    (
        "03",
        "등수별 총당첨금",
        "각 등수에 지급된 총당첨금은 어떻게 분포하는가?",
        "회차",
        "정규화한 등수별 총당첨금",
        "환산 원",
    ),
    (
        "08",
        "같은 회차 판매량과 당첨금",
        "판매량과 한 게임당 당첨금은 어떤 모습으로 함께 나타나는가?",
        "판매게임 수",
        "정규화한 한 게임당 당첨금",
        "환산 원",
    ),
    (
        "09A",
        "직전 회차 대비 당첨금 증감",
        "정규화한 당첨금은 직전 회차보다 얼마나 증가하거나 감소했는가?",
        "회차",
        "정규화한 현재 당첨금 - 정규화한 직전 당첨금",
        "환산 원 차이",
    ),
    (
        "09B",
        "직전 회차 대비 당첨게임 수 증감",
        "당첨게임 수는 직전 회차보다 얼마나 증가하거나 감소했는가?",
        "회차",
        "현재 당첨게임 수 - 직전 당첨게임 수",
        "게임 차이",
    ),
    (
        "09C",
        "직전 당첨금과 현재 당첨게임 수",
        "직전 당첨금과 현재 당첨게임 수는 어떤 모습으로 함께 나타나는가?",
        "정규화한 직전 한 게임당 당첨금",
        "현재 당첨게임 수",
        "게임",
    ),
    (
        "09D",
        "직전 당첨게임 수와 현재 당첨금",
        "직전 당첨게임 수와 현재 당첨금은 어떤 모습으로 함께 나타나는가?",
        "직전 당첨게임 수",
        "정규화한 현재 한 게임당 당첨금",
        "환산 원",
    ),
    (
        "10",
        "기존 시기별 당첨금 분석의 통합",
        "구간을 합친 전체 회차에서 정규화한 당첨금의 흐름은 어떠한가?",
        "회차",
        "정규화한 한 게임당 당첨금",
        "환산 원",
    ),
)
FEATURES = (
    (
        "current_sales",
        "해당 회차 판매액",
        "환산 원",
        "이상치 회차의 판매액은 정상 회차의 판매액과 다른가?",
    ),
    (
        "current_prize",
        "해당 회차 한 게임당 당첨금",
        "환산 원",
        "이상치 회차의 한 게임당 당첨금은 정상 회차와 다른가?",
    ),
    (
        "current_winners",
        "해당 회차 당첨게임 수",
        "게임",
        "이상치 회차의 당첨게임 수는 정상 회차와 다른가?",
    ),
    (
        "current_total",
        "해당 회차 등수별 총당첨금",
        "환산 원",
        "이상치 회차의 등수별 총당첨금은 정상 회차와 다른가?",
    ),
    (
        "previous_sales",
        "직전 회차 판매액",
        "환산 원",
        "이상치 회차의 직전 판매액은 정상 회차의 직전 판매액과 다른가?",
    ),
    (
        "previous_prize",
        "직전 회차 한 게임당 당첨금",
        "환산 원",
        "이상치 회차의 직전 당첨금은 정상 회차의 직전 당첨금보다 많거나 적었는가?",
    ),
    (
        "previous_winners",
        "직전 회차 당첨게임 수",
        "게임",
        "이상치 회차의 직전 당첨게임 수는 정상 회차의 직전 당첨게임 수와 다른가?",
    ),
    (
        "previous_total",
        "직전 회차 등수별 총당첨금",
        "환산 원",
        "이상치 회차의 직전 총당첨금은 정상 회차의 직전 총당첨금과 다른가?",
    ),
)
TARGET_FEATURE = {
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


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf", size
    )


def centered(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    value: str,
    size: int = 19,
    color: str = "#425366",
    bold: bool = False,
) -> None:
    draw.text((x, y), value, font=font(size, bold), fill=color, anchor="mt")


def blank_figure(path: Path, kind: str, title: str, unit: str) -> None:
    """수치·실제 곡선·관측점을 넣지 않는 레이아웃 전용 그림이다."""
    image = Image.new("RGB", (1260, 420), "#ffffff")
    draw = ImageDraw.Draw(image)
    centered(draw, 630, 9, title, 24, bold=True)
    centered(
        draw,
        630,
        46,
        "배치 검토용 빈 그림 · 실제 계산값과 데이터 도형 없음",
        17,
        "#925b00",
    )
    for index, rank in enumerate(RANKS):
        left = 16 + index * 416
        right = left + 396
        draw.rounded_rectangle(
            (left, 82, right, 407), radius=10, outline="#c4ced8", width=2
        )
        centered(draw, left + 198, 92, rank, 22, bold=True)
        centered(draw, left + 198, 125, f"세로축 단위: {unit}", 16)
        draw.line(
            (left + 38, 166, left + 38, 325, right - 18, 325), fill="#a3b1bf", width=2
        )
        if kind == "violin":
            for offset, label, color in (
                (125, "정상 회차", "#526d82"),
                (285, "이상치 회차", "#b76512"),
            ):
                draw.rounded_rectangle(
                    (left + offset - 48, 177, left + offset + 48, 309),
                    radius=8,
                    outline=color,
                    width=2,
                )
                centered(draw, left + offset, 213, "분포 자리", 17, color)
                centered(draw, left + offset, 242, "계산 전", 17, color)
                centered(draw, left + offset, 334, label, 18, color)
                centered(draw, left + offset, 364, "n —  중앙값 —", 16, color)
        elif kind == "scatter":
            centered(draw, left + 207, 211, "전체 관측점·상방 경계 자리", 18)
            centered(draw, left + 207, 245, "데이터와 경계는 계산 후 표시", 16)
            centered(draw, left + 198, 341, "정상 n —  이상 n —", 18)
            centered(draw, left + 198, 369, "Q1 —  Q3 —  상한 —", 16)
        else:
            centered(draw, left + 207, 203, "다른 섹션의 상방 발생 비율", 18)
            centered(draw, left + 207, 239, "정상 집단 / 이상치 집단 비교 자리", 17)
            centered(draw, left + 198, 341, "동시 회차 —  정상 집단 비율 —", 16)
            centered(draw, left + 198, 369, "이상치 집단 비율 —", 16)
    image.save(path)


def embed(path: Path, alt: str) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"![{alt}](data:image/png;base64,{payload})"


def placeholder_text(label: str) -> str:
    return f"**계산 대기 — {label}:** 결과를 계산한 뒤 채웁니다."


def build(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "placeholders"
    assets.mkdir(exist_ok=True)
    provenance = json.loads(
        (
            REPORTS / "v2_3" / "analysis_results_v2_3" / "independent_audit.json"
        ).read_text(encoding="utf-8")
    )
    figures = {}
    for feature, name, unit, _ in FEATURES:
        path = assets / f"{feature}_six_slots.png"
        blank_figure(path, "violin", f"{name} · 정상과 이상 비교", unit)
        figures[feature] = path
    slots = []
    sections = []
    for code, name, question, xlabel, ylabel, unit in TOPICS:
        for kind, title in (
            ("scatter", f"{code} {name} · Y 분포와 이상치 선정"),
            ("violin", f"{code} 선정한 Y의 정상과 이상 분포"),
            ("overlap", f"{code} 다른 분석과 겹치는 정도도 정상과 이상 비교"),
        ):
            path = assets / f"{code}_{kind}.png"
            blank_figure(path, kind, title, "%" if kind == "overlap" else unit)
            figures[f"{code}_{kind}"] = path
        parts = [
            f'<a id="section-{code}"></a>',
            f"## {code} {name}",
            question,
            f"**X:** {xlabel}. **판정 Y:** {ylabel}. **Y 단위:** {unit}.",
            "이 섹션의 Y가 있는 전체 회차를 등수별로 합쳐 같은 상방 IQR 규칙을 적용합니다. 이 섹션에서 선정한 회차로 아래 모든 정상·이상 비교 집단을 고정합니다.",
        ]
        if code == "10":
            parts.append(
                "**구조 검토 지점:** 기존 구간별 비교를 제거하면 이 섹션은 01번과 같은 X·Y를 사용합니다. 현재 틀은 기존 9개 섹션을 남겨 이 중복을 드러냈습니다. 새 주제로 임의 대체하지 않았으며, 두 결과를 독립적인 발견으로 세지 않습니다."
            )
        if code == "09A":
            parts.append(
                "현재와 직전 당첨금을 각각 가격 환산한 뒤 빼서 Y를 만듭니다. 큰 증가량을 선정하므로 구성요소의 차이는 선정식의 영향을 함께 확인합니다."
            )
        if code == "09B":
            parts.append(
                "Y는 현재 당첨게임 수에서 정확한 직전 당첨게임 수를 뺀 값입니다. 큰 증가량이 선택되며 현재·직전 게임 수는 이 식의 구성요소입니다."
            )

        def add_slot(
            slot_id: str, heading: str, path: Path, caption: str, kind: str
        ) -> None:
            parts.extend(
                [
                    heading,
                    caption,
                    embed(path, f"{slot_id} 계산 전 빈 그림"),
                    placeholder_text("등수별 관측 수와 결과"),
                    placeholder_text("관찰한 차이와 해석"),
                ]
            )
            slots.append(
                {
                    "id": slot_id,
                    "section": code,
                    "kind": kind,
                    "image": str(path.relative_to(output)),
                    "status": "awaiting_calculation",
                }
            )

        add_slot(
            f"{code}-selection",
            f"### {code} 이상치 선정",
            figures[f"{code}_scatter"],
            f"1·2·3등의 {xlabel}에 대한 Y 산점도를 배치합니다. 정상 점, 상방 점, 강한 상방 점과 두 경계를 표시합니다. 숫자와 점은 새 계산 후 채웁니다.",
            "selection",
        )
        add_slot(
            f"{code}-target",
            f"### {code} 선정 지표의 정상과 이상 분포",
            figures[f"{code}_violin"],
            "한 그림에 3개 등수 패널을 두고 각 패널은 정상·이상 두 분포를 보여줍니다. 총 6개 자리입니다. 이 그림은 선정 기준을 확인하며, Y 자체가 다르다는 것을 새로운 설명으로 삼지 않습니다.",
            "target_distribution",
        )
        for phase, heading in (
            ("current_", "해당 회차의 정상과 이상 비교"),
            ("previous_", "직전 회차의 정상과 이상 비교"),
        ):
            parts.append(f"### {code} {heading}")
            if phase == "previous_":
                parts.append(
                    "현재 회차 t의 정상·이상 구분을 유지한 채 두 집단 각각의 정확한 t−1 값을 비교합니다. 직전 값으로 집단을 새로 나누지 않습니다."
                )
            for feature, feature_name, _, feature_question in FEATURES:
                if not feature.startswith(phase):
                    continue
                note = feature_question
                if TARGET_FEATURE.get(code) == feature:
                    note += " 이 항목은 위 선정 Y의 분포와 같은 내용이므로 기준 확인용입니다. 새로운 인사이트로 해석하지 않습니다. 이 중복 자리를 유지할지는 틀 검토 시 확인합니다."
                add_slot(
                    f"{code}-{feature}",
                    f"#### {feature_name}",
                    figures[feature],
                    note,
                    "feature_comparison",
                )
        add_slot(
            f"{code}-overlap",
            f"### {code} 다른 분석에서도 같은 회차가 튀는가",
            figures[f"{code}_overlap"],
            "먼저 위 해당·직전 특징을 비교한 다음 겹침을 봅니다. 같은 등수의 다른 섹션에 대해, 이 섹션의 정상 집단과 이상치 집단에서 각각 상방이 얼마나 발생했는지 비율로 비교합니다. 두 섹션의 Y가 모두 있는 회차만 각 분모에 넣습니다. 같은 Y의 반복 검출을 표시하고 독립 증거로 세지 않습니다.",
            "overlap_comparison",
        )
        parts.extend(
            [
                f"### {code} 이 섹션에서 설명할 수 있는 것",
                placeholder_text("정상 집단과 달랐던 해당 회차의 특징"),
                placeholder_text("정상 집단과 달랐던 직전 회차의 특징"),
                placeholder_text("다른 분석과 겹친 특징"),
                placeholder_text("선정식으로 설명되는 차이와 아직 설명되지 않은 점"),
            ]
        )
        sections.append("\n\n".join(parts))
    known = [
        "# 로또 보고서 V2.4 계산 전 검토용 틀",
        "**검토 단계입니다.** 구성·변수·단위·비교 방법을 먼저 채웠습니다. 빈 그림은 배치 확인용이며 실제 통계 결과가 아닙니다. 새로 계산할 경계값, 이상치 회차, 관측 수, 분포와 해석은 비워 두었습니다.",
        "## 분석 대상과 이미 확정한 내용",
        "기존 자료의 1~1245회를 대상으로 1·2·3등을 분석합니다. 전체 회차를 합치며 88회·401회를 기준으로 판정·비교 집단을 나누지 않습니다. 보고서의 ‘당첨자 수’는 사람 수가 아니라 CSV의 당첨게임 수입니다.",
        "가격 환산은 기존 V2 정의를 이어받습니다. 1~87회의 1게임 가격은 2,000원, 88회 이후는 1,000원으로 처리되어 있습니다. 이 경계는 환산에만 쓰고 분석 집단을 나누는 데 사용하지 않습니다.",
        f"**모든 금액의 기준:** {NORMALIZATION}. 한 게임당 당첨금·등수별 총당첨금·판매액에 같은 가격 환산을 적용합니다. 당첨게임 수는 그대로 쓰고, 판매게임 수는 판매액을 해당 회차 게임가격으로 나눈 값입니다. 세금과 물가는 적용하지 않습니다.",
        "**변화량의 기준:** 금액을 회차별로 먼저 환산한 뒤 현재−직전을 계산합니다. 가격이 다른 회차의 명목금액을 그대로 빼지 않습니다.",
        "**이상치 규칙:** 각 섹션·등수의 정규화된 Y에서 Q1·Q3와 IQR=Q3−Q1을 구합니다. Y>Q3+1.5×IQR은 상방 이상치, Y>Q3+3×IQR은 강한 상방 이상치입니다. 경계와 같은 값은 초과로 세지 않습니다. X로 선정하지 않습니다.",
        "**비교 집단:** ‘정상’은 해당 섹션·등수에서 상방 경계를 넘지 않은 유효 회차입니다. 낮은 값도 정상 집단에 남깁니다. ‘이상’은 그 경계를 넘은 회차입니다. 각 섹션은 같은 규칙에 자기 Y를 넣으며, 이렇게 정한 두 집단을 해당·직전 특징 비교에 공통으로 사용합니다.",
        "**결측 처리:** 당첨게임 수가 0인 회차의 한 게임당 지급액은 관측이 없는 값으로 처리합니다. 게임 수와 총지급액의 0은 유지합니다. 직전 값이 없으면 정확한 t−1의 결측으로 남기며 전전 회차로 대체하지 않습니다. 유효·결측 관측 수를 그림에 표시합니다.",
        "## 본문을 읽는 순서와 그림 배치",
        "각 섹션은 이상치 선정 → 선정 Y의 두 집단 분포 → 해당 회차 특징 4개 → 직전 회차 특징 4개 → 다른 분석과의 겹침 → 관찰한 차이 순서입니다. 특징 비교가 겹침보다 먼저 나옵니다. 본문에는 표를 넣지 않습니다.",
        "각 바이올린 비교 그림은 1등·2등·3등의 3개 패널이며 각 패널에 정상·이상 2개 자리, 총 6개 분포를 둡니다. 같은 패널의 두 집단은 같은 축 범위를 사용합니다. 그림에 실제 관측 수와 중앙값을 표시합니다. 1건 또는 분산이 없는 집단은 실제 점으로, 빈 집단은 관측 없음으로 표시합니다. 이 틀에는 아직 실제 점도 그리지 않았습니다.",
        "## 새 계산 후 채울 전체 결과",
        placeholder_text("전체 상방 회차 수와 중복을 합친 회차 수"),
        placeholder_text("등수별 가장 분명하게 달랐던 특징과 대응 그림"),
        "## 섹션 바로가기",
        "\n\n".join(f"- [{code} {name}](#section-{code})" for code, name, *_ in TOPICS),
    ]
    ending = [
        "## 전체 비교의 해석",
        placeholder_text("정상과 이상을 구분했던 공통 특징"),
        placeholder_text("직전 회차에서 반복해서 관찰한 차이"),
        placeholder_text("겹침과 선정식의 영향을 구분한 설명"),
        "## 상세 결과의 별도 보존",
        "실제 계산 단계에서 경계값·전체 이상치 목록·집단별 관측 수와 요약값·결측 사유·겹침 분모를 CSV로 보존합니다. 본문은 그림과 짧은 해석으로 구성하고 수치표를 나열하지 않습니다.",
        "## 정의와 출처",
        "입력은 `lotto_rank_1_1245.csv`와 기존 전처리 `lotto_preprocessed_v2.csv`입니다. 출처 식별값은 기존 V2.3 독립 감사에 기록된 해시를 인용합니다.",
        f"원본 SHA-256: `{provenance['raw_source_sha256']}`. 전처리 SHA-256: `{provenance['v2_source_sha256']}`.",
        "IQR 경계의 근거: [NIST의 이상치 정의](https://www.itl.nist.gov/div898/handbook/prc/section1/prc16.htm). 이 문서의 문장과 빈 배치 도식은 LLM 직접 작성물입니다. 새 수치 결과의 근거로 사용하지 않습니다.",
    ]
    document = "\n\n".join(known + sections + ending) + "\n"
    name = "lotto_rank_report_v2_4"
    (output / f"{name}.md").write_text(document, encoding="utf-8")
    body = markdown.markdown(document, extensions=["fenced_code"])
    html = (
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>로또 V2.4 계산 전 검토용 틀</title><style>body{font:16px/1.7 sans-serif;max-width:1260px;margin:30px auto;padding:0 24px;color:#263848}img{display:block;width:100%;height:auto;border:1px solid #e0e5ea;margin:18px 0}h2{margin-top:52px;border-bottom:2px solid #d5dee8;padding-bottom:8px}h3{margin-top:32px}strong{color:#7b4e00}a{color:#165ea0}p{margin:14px 0}code{overflow-wrap:anywhere}@media print{h2{break-before:page}img{break-inside:avoid}}</style><body>'
        + body
        + "</body></html>"
    )
    (output / f"{name}.html").write_text(html, encoding="utf-8")
    manifest = {
        "version": "2.4",
        "stage": "review_scaffold",
        "statistical_analysis_executed": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -X utf8 class2_rottery/src/build_lotto_v2_4_scaffold.py",
        "sections": [item[0] for item in TOPICS],
        "comparison_features": [item[0] for item in FEATURES],
        "source_audit": "../analysis_results_v2_3/independent_audit.json",
        "plot_slots": slots,
        "embedded_image_references": len(slots),
        "distinct_placeholder_images": len(figures),
        "pending_values": [
            "IQR 경계",
            "집단별 관측 수",
            "이상치 회차",
            "분포",
            "차이",
            "해석",
        ],
        "known_structure_review": "10번은 구간 제거 후 01번과 중복. 그대로 표시하고 임의로 새 주제로 바꾸지 않음",
        "artifacts": [
            {
                "path": str(path.relative_to(output)),
                "size_bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in sorted(output.rglob("*"))
            if path.is_file()
            and path.name
            not in {"manifest.json", "execution_log.json", "scaffold_audit.json"}
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=REPORTS / "v2_4" / "report_scaffold_v2_4"
    )
    args = parser.parse_args()
    manifest = build(args.output)
    for extension in ("md", "html"):
        name = f"lotto_rank_report_v2_4.{extension}"
        (REPORTS / "v2_4" / f"lotto_rank_report_v2_4_scaffold.{extension}").write_bytes(
            (args.output / name).read_bytes()
        )
    print(
        json.dumps(
            {
                "stage": manifest["stage"],
                "statistical_analysis_executed": False,
                "sections": len(TOPICS),
                "plot_slots": len(manifest["plot_slots"]),
                "placeholder_images": manifest["distinct_placeholder_images"],
                "report": str(
                    REPORTS / "v2_4" / "lotto_rank_report_v2_4_scaffold.html"
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
