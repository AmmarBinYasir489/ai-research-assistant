import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import (  # noqa: E402
    deduplicate_results,
    format_sources,
    make_research_plan,
    search_with_tool,
    select_latest_results,
    select_results_without_freshness_filter,
)
from tools.evidence_evaluator import evaluate_evidence  # noqa: E402
from tools.google_news import GoogleNewsError  # noqa: E402
from tools.google_search import SearchError  # noqa: E402
from tools.llm import ask_llm  # noqa: E402
from tools.result_ranker import rank_results  # noqa: E402
from tools.searxng_search import SearxngSearchError  # noqa: E402


def print_result_titles(results: list[dict[str, str]], limit: int = 5) -> None:
    for index, result in enumerate(results[:limit], start=1):
        title = result.get("title", "(no title)")
        published = result.get("published_at") or result.get("published") or "unknown date"
        tool = result.get("search_tool", "unknown tool")
        print(f"  {index}. [{tool}] {title} ({published})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug the research planner and search tools.")
    parser.add_argument("question", nargs="*", help="Research question to debug.")
    args = parser.parse_args()

    question = " ".join(args.question).strip()
    if not question:
        question = input("Research question: ").strip()
    if not question:
        print("Please enter a research question.")
        return

    plan = make_research_plan(question)
    search_query = str(plan["search_query"])
    search_tools = list(plan["search_tools"])
    search_result_count = int(plan["search_result_count"])
    final_source_count = int(plan["final_source_count"])
    max_age_days = int(plan["max_age_days"])
    requires_freshness = plan["requires_freshness"] == "yes"

    print("\n=== Research Plan ===")
    print(f"Mode: {plan['research_mode']}")
    print(f"Tools: {', '.join(search_tools)}")
    print(f"Query: {search_query}")
    print(f"Inspect: {search_result_count}")
    print(f"Keep: {final_source_count}")
    print(f"Freshness: {'yes' if requires_freshness else 'no'}")
    if requires_freshness:
        print(f"Max age days: {max_age_days}")
    print(f"Reason: {plan['reason']}")

    raw_results = []
    errors = []
    print("\n=== Search Tools ===")
    for tool in search_tools:
        try:
            tool_results = search_with_tool(tool, search_query, search_result_count)
        except (GoogleNewsError, SearchError, SearxngSearchError, requests.RequestException) as error:
            errors.append(f"{tool}: {error}")
            print(f"{tool}: failed ({error})")
            continue

        for result in tool_results:
            result["search_tool"] = tool
        raw_results.extend(tool_results)
        print(f"{tool}: {len(tool_results)} results")

    if errors:
        print("\n=== Tool Errors ===")
        for error in errors:
            print(f"- {error}")

    print("\n=== Raw Results ===")
    print_result_titles(raw_results)

    unique_results = deduplicate_results(raw_results)
    print("\n=== Deduplication ===")
    print(f"Before: {len(raw_results)}")
    print(f"After: {len(unique_results)}")
    print(f"Removed: {len(raw_results) - len(unique_results)}")

    if requires_freshness:
        selected, rejected = select_latest_results(unique_results, max_age_days)
    else:
        selected, rejected = select_results_without_freshness_filter(unique_results)
    selected = rank_results(search_query, selected, requires_freshness)
    extra_results = selected[final_source_count:]
    selected = selected[:final_source_count]
    rejected = rejected + extra_results

    print("\n=== Filtering ===")
    print(f"Selected: {len(selected)}")
    print(f"Rejected/extra: {len(rejected)}")

    print("\n=== Selected Results ===")
    print_result_titles(selected, limit=final_source_count)
    print("\n=== Relevance Scores ===")
    for index, result in enumerate(selected[:final_source_count], start=1):
        print(f"  {index}. {result.get('relevance_score', '0.00')} - {result.get('title', '(no title)')}")

    print("\n=== Evidence Evaluation ===")
    sources = format_sources(selected, final_source_count)
    evaluation = evaluate_evidence(question, sources, ask_llm)
    print(json.dumps(evaluation, indent=2))


if __name__ == "__main__":
    main()
