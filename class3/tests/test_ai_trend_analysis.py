import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "ai_trend_analysis.py"
spec = importlib.util.spec_from_file_location("ai_trend_analysis", MODULE)
analysis = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = analysis
spec.loader.exec_module(analysis)


@pytest.fixture(scope="module")
def classifier():
    return analysis.KeywordClassifier.from_json(
        Path(__file__).resolve().parents[1] / "ai_keywords.json"
    )


@pytest.mark.parametrize(
    ("title", "subtitle", "general", "technical", "service"),
    [
        ("인공지능 입문", "", True, False, False),
        ("실전 머신러닝과 LLM", "", False, True, False),
        ("미드저니 실전", "", False, False, True),
        ("ChatGPT로 배우는 AI", "", True, False, True),
        ("업무 자동화", "클로드와 제미나이 활용", False, False, True),
    ],
)
def test_three_independent_keyword_groups(
    classifier, title, subtitle, general, technical, service
):
    result = classifier.classify(title, subtitle)
    assert result["is_ai_general"] is general
    assert result["is_ai_technical"] is technical
    assert result["is_ai_service"] is service
    assert result["is_ai_any"] is (general or technical or service)


@pytest.mark.parametrize(
    "title",
    [
        "RAILWAY 시스템 구축",
        "Make 자동화 입문",
        "스프링 WebFlux 완전정복",
        "소라의 여행",
        "데이터베이스 Cursor 활용",
        "세상에 없던 금융, 디파이",
    ],
)
def test_ambiguous_words_do_not_create_false_ai_matches(classifier, title):
    result = classifier.classify(title, "")
    assert not result["is_ai_any"]
    assert result["matched_keywords"] == []


def test_matched_keywords_are_kept_for_audit(classifier):
    result = classifier.classify("AI와 ChatGPT", "딥러닝 입문")
    assert result["is_ai_general"]
    assert result["is_ai_technical"]
    assert result["is_ai_service"]
    assert {"AI", "딥러닝", "ChatGPT"}.issubset(result["matched_keywords"])


def test_yearly_summary_uses_union_and_excludes_invalid_or_future_months(classifier):
    rows = [
        {"title": "AI 입문", "subtitle": "", "publication_month": "2024-01"},
        {
            "title": "머신러닝 입문",
            "subtitle": "",
            "publication_month": "2024-02",
        },
        {
            "title": "미드저니 실전",
            "subtitle": "",
            "publication_month": "2024-03",
        },
        {"title": "파이썬 입문", "subtitle": "", "publication_month": "2024-04"},
        {
            "title": "ChatGPT와 AI",
            "subtitle": "",
            "publication_month": "2025-01",
        },
        {"title": "제외", "subtitle": "", "publication_month": ""},
        {"title": "예약", "subtitle": "", "publication_month": "2026-12"},
    ]

    summaries, matched = analysis.summarize_yearly(rows, classifier, "2026-11")

    assert summaries == [
        {
            "year": 2024,
            "total_books": 4,
            "ai_any_count": 3,
            "ai_general_count": 1,
            "ai_technical_count": 1,
            "ai_service_count": 1,
            "ai_any_pct": 75.0,
            "ai_general_pct": 25.0,
            "ai_technical_pct": 25.0,
            "ai_service_pct": 25.0,
            "is_partial_year": False,
        },
        {
            "year": 2025,
            "total_books": 1,
            "ai_any_count": 1,
            "ai_general_count": 1,
            "ai_technical_count": 0,
            "ai_service_count": 1,
            "ai_any_pct": 100.0,
            "ai_general_pct": 100.0,
            "ai_technical_pct": 0.0,
            "ai_service_pct": 100.0,
            "is_partial_year": False,
        },
    ]
    assert len(matched) == 4
    assert matched[-1]["is_ai_general"]
    assert matched[-1]["is_ai_service"]
