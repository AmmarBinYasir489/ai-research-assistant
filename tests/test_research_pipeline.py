from datetime import UTC, datetime, timedelta

from main import deduplicate_results
from tools.evidence_evaluator import evaluate_evidence
from tools.result_ranker import freshness_score, rank_results


def test_deduplicate_results_keeps_one_copy_of_each_url():
    results = [
        {"url": "https://example.com/a", "title": "First"},
        {"url": "https://example.com/a", "title": "Duplicate"},
        {"url": "https://example.com/b", "title": "Second"},
    ]

    assert [item["url"] for item in deduplicate_results(results)] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_freshness_score_prefers_recent_results():
    now = datetime.now(UTC)
    recent = {"published_at": (now - timedelta(hours=12)).isoformat()}
    old = {"published_at": (now - timedelta(days=90)).isoformat()}

    assert freshness_score(recent) > freshness_score(old)


def test_rank_results_prefers_relevant_authoritative_sources():
    results = [
        {
            "url": "https://example.com/article",
            "title": "Other topic",
            "snippet": "Unrelated content",
            "published_at": "",
        },
        {
            "url": "https://www.reuters.com/technology/ai",
            "title": "AI regulation update",
            "snippet": "Latest AI regulation developments",
            "published_at": datetime.now(UTC).isoformat(),
        },
    ]

    ranked = rank_results("AI regulation", results, freshness_required=True)

    assert ranked[0]["url"] == "https://www.reuters.com/technology/ai"


def test_evidence_evaluator_normalizes_model_output():
    def fake_model(*_args, **_kwargs):
        return (
            '{"enough_information": true, "confidence": "high", '
            '"missing_information": [], "conflicts": [], '
            '"recommended_next_query": null, "reason": "Multiple sources agree."}',
            None,
        )

    evaluation = evaluate_evidence("What changed?", "[1] Source", fake_model)

    assert evaluation["enough_information"] is True
    assert evaluation["confidence"] == "high"
    assert evaluation["reason"] == "Multiple sources agree."
