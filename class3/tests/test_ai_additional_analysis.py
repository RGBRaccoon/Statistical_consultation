import importlib
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

analysis = importlib.import_module("ai_additional_analysis")
classifier_module = importlib.import_module("ai_trend_analysis")


def test_overlap_percentages_use_the_requested_denominator():
    rows = [
        {"general": True, "technical": True, "service": False},
        {"general": True, "technical": False, "service": False},
        {"general": True, "technical": False, "service": False},
    ]
    assert analysis.conditional_overlap(rows, "technical", "general") == 100
    assert analysis.conditional_overlap(rows, "general", "technical") == 100 / 3
    assert analysis.conditional_overlap(rows, "service", "general") is None


def test_exclusive_groups_partition_the_union():
    assert (
        analysis.exclusive_group({"general": True, "technical": True, "service": False})
        == "110"
    )
    assert (
        analysis.exclusive_group(
            {"general": False, "technical": False, "service": False}
        )
        == "000"
    )


def test_gpt_family_counts_one_book_once():
    assert analysis.service_families(["ChatGPT", "GPT", "Claude"]) == {
        "ChatGPT/GPT",
        "Claude",
    }


def test_growth_with_zero_baseline_is_undefined():
    assert analysis.growth_rate(0, 5) is None
    assert analysis.growth_rate(10, 15) == 50


def test_title_variants_are_only_the_explicit_forms():
    assert analysis.is_title_variant("AI 입문 (큰글자도서)")
    assert analysis.is_title_variant("AI 전집 세트")
    assert not analysis.is_title_variant("데이터세트로 배우는 AI")
    assert not analysis.is_title_variant("AI 입문 개정판")


def test_tables_partition_ai_and_recompute_sensitivity_denominators():
    classifier = classifier_module.KeywordClassifier.from_json(
        MODULE_DIR / "ai_keywords.json"
    )
    rows = [
        {"title": "AI 입문", "subtitle": "LLM", "publication_month": "2025-01"},
        {"title": "AI 입문 (큰글자책)", "subtitle": "", "publication_month": "2025-02"},
        {"title": "일반 도서", "subtitle": "ChatGPT", "publication_month": "2025-03"},
        {"title": "파이썬", "subtitle": "", "publication_month": "2025-04"},
        {"title": "AI 입문", "subtitle": "", "publication_month": "2026-01"},
        {"title": "누락", "subtitle": "AI", "publication_month": ""},
        {"title": "예약", "subtitle": "AI", "publication_month": "2026-12"},
    ]
    records = analysis.classify_books(rows, classifier)
    tables = analysis.compute_tables(records)
    analysis.validate_tables(tables, 5)
    sensitivity = {
        (row["year"], row["scenario"]): row for row in tables["ai_sensitivity"]
    }
    assert sensitivity[2025, "baseline"]["ai_share_pct"] == 75
    assert sensitivity[2025, "title_only"]["ai_share_pct"] == 50
    filtered = sensitivity[2025, "exclude_title_variants"]
    assert filtered["total"] == 3
    assert filtered["ai_count"] == 2
    assert filtered["excluded_ai_count"] == 1
    assert tables["ai_growth"][-1]["ai_yoy_pct"] is None
