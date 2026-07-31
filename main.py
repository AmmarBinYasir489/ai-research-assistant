import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from tools.article_reader import enrich_with_article_text
from tools.evidence_evaluator import evaluate_evidence, format_evaluation_summary
from tools.google_news import GoogleNewsError, google_news_search
from tools.google_search import SearchError, google_search
from tools.llm import ask_llm
from tools.relevance_ranker import rank_results_with_llm
from tools.result_ranker import rank_results
from tools.searxng_search import SearxngSearchError, searxng_search


ROOT = Path(__file__).parent
SYSTEM_PROMPT = (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
load_dotenv()
SEARCH_RESULT_COUNT = int(os.getenv("SEARCH_RESULT_COUNT", "15"))
FINAL_SOURCE_COUNT = int(os.getenv("FINAL_SOURCE_COUNT", "5"))
MAX_ARTICLE_AGE_DAYS = int(os.getenv("MAX_ARTICLE_AGE_DAYS", "30"))
MAX_RESEARCH_ATTEMPTS = int(os.getenv("MAX_RESEARCH_ATTEMPTS", "2"))
MAX_ALLOWED_SEARCH_RESULTS = 20
MAX_ALLOWED_FINAL_SOURCES = 8
MAX_ALLOWED_ARTICLE_AGE_DAYS = 365
MAX_ALLOWED_RESEARCH_ATTEMPTS = 3


@dataclass
class ResearchResult:
    question: str
    plan: dict[str, object] = field(default_factory=dict)
    answer: str = ""
    evaluation: dict[str, object] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    trace: list[dict[str, str]] = field(default_factory=list)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def normalize_search_tools(research_mode: str, search_tools: list[str]) -> list[str]:
    preferred_by_mode = {
        "news": ["google_news"],
        "web": ["searxng", "serpapi"],
        "hybrid": ["google_news", "searxng"],
    }
    preferred = preferred_by_mode.get(research_mode, ["searxng"])
    ordered_tools = []

    for tool in preferred + search_tools:
        if tool not in {"google_news", "brave", "searxng", "serpapi"}:
            continue
        if tool in ordered_tools:
            continue
        ordered_tools.append(tool)

    return ordered_tools


def make_search_query(user_question: str) -> str:
    prompt = f"""
Convert the user's research question into one Google search query.
Return JSON only.

Schema:
{{
  "query": "..."
}}

User question:
{user_question}
"""
    response, _error = ask_llm(prompt, json_mode=True, max_tokens=80)
    if not response:
        return user_question

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        return user_question

    query = parsed.get("query")
    if not isinstance(query, str) or not query.strip():
        return user_question

    return query.strip()


def make_research_plan(user_question: str) -> dict[str, int | str | list[str]]:
    prompt = f"""
You are planning a research task.
Choose the best research mode, query, freshness rule, and source count.

Rules:
- research_mode must be one of: "web", "hybrid", "news".
- search_tools can include: "searxng", "google_news", "serpapi".
- "web" is the general default and works for most questions, including historical and reference ones.
- "news" is only for questions explicitly about very recent events or announcements ("today", "this week", "latest news").
- "hybrid" combines news and web when the question needs both recent developments and background.
- requires_freshness should be false unless the user is specifically asking about current or recent events.
- Older sources are valid evidence for historical, reference, and background questions.
- Even when recent sources are preferred, older sources can still be used if recent coverage is thin.
- final_source_count must be smaller than or equal to search_result_count.
- Return JSON only.

Schema:
{{
  "research_mode": "hybrid",
  "search_tools": ["searxng", "google_news"],
  "search_query": "...",
  "search_result_count": 15,
  "final_source_count": 5,
  "max_age_days": 30,
  "requires_freshness": false,
  "reason": "..."
}}

User question:
{user_question}
"""
    response, _error = ask_llm(prompt, json_mode=True, max_tokens=160)
    if not response:
        return {
            "research_mode": "web",
            "search_tools": ["searxng"],
            "search_query": user_question,
            "search_result_count": SEARCH_RESULT_COUNT,
            "final_source_count": FINAL_SOURCE_COUNT,
            "max_age_days": MAX_ARTICLE_AGE_DAYS,
            "requires_freshness": "no",
            "reason": "Fallback plan because the LLM did not return JSON.",
        }

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = {}

    research_mode = parsed.get("research_mode", "web")
    search_tools = parsed.get("search_tools", [])
    search_query = parsed.get("search_query")
    if not isinstance(search_query, str) or not search_query.strip():
        search_query = make_search_query(user_question)

    search_result_count = parsed.get("search_result_count", SEARCH_RESULT_COUNT)
    final_source_count = parsed.get("final_source_count", FINAL_SOURCE_COUNT)
    max_age_days = parsed.get("max_age_days", MAX_ARTICLE_AGE_DAYS)
    requires_freshness = parsed.get("requires_freshness", False)
    reason = parsed.get("reason", "Model-created research plan.")

    if not isinstance(research_mode, str):
        research_mode = "web"
    research_mode = research_mode.lower().strip()
    if research_mode not in {"news", "web", "hybrid"}:
        research_mode = "web"

    if not isinstance(search_tools, list):
        search_tools = []
    search_tools = [tool for tool in search_tools if isinstance(tool, str)]
    search_tools = [tool.lower().strip() for tool in search_tools]
    search_tools = [tool for tool in search_tools if tool in {"google_news", "searxng", "serpapi"}]
    search_tools = normalize_search_tools(research_mode, search_tools)

    if not isinstance(search_result_count, int):
        search_result_count = SEARCH_RESULT_COUNT
    if not isinstance(final_source_count, int):
        final_source_count = FINAL_SOURCE_COUNT
    if not isinstance(max_age_days, int):
        max_age_days = MAX_ARTICLE_AGE_DAYS
    if not isinstance(requires_freshness, bool):
        requires_freshness = False
    if not isinstance(reason, str):
        reason = "Model-created research plan."

    search_result_count = clamp(search_result_count, 5, MAX_ALLOWED_SEARCH_RESULTS)
    final_source_count = clamp(final_source_count, 1, MAX_ALLOWED_FINAL_SOURCES)
    final_source_count = min(final_source_count, search_result_count)
    max_age_days = clamp(max_age_days, 1, MAX_ALLOWED_ARTICLE_AGE_DAYS)

    return {
        "research_mode": research_mode,
        "search_tools": search_tools,
        "search_query": search_query.strip(),
        "search_result_count": search_result_count,
        "final_source_count": final_source_count,
        "max_age_days": max_age_days,
        "requires_freshness": "yes" if requires_freshness else "no",
        "reason": reason.strip(),
    }


def format_sources(results: list[dict[str, str]], final_source_count: int) -> str:
    lines = []
    for index, result in enumerate(results[:final_source_count], start=1):
        article_text = result.get("article_text")
        evidence = f"Article excerpt: {article_text}" if article_text else f"Snippet: {result['snippet']}"
        published = result.get("published_at") or result.get("published") or "unknown date"
        lines.append(
            f"[{index}] {result['title']}\n"
            f"Published: {published}\n"
            f"URL: {result['url']}\n"
            f"{evidence}"
        )
    return "\n\n".join(lines)


def format_source_links(results: list[dict[str, str]], final_source_count: int) -> str:
    lines = []
    for index, result in enumerate(results[:final_source_count], start=1):
        published = result.get("published_at") or result.get("published") or "unknown date"
        lines.append(f"[{index}] {result['title']}\nPublished: {published}\n{result['url']}")
    return "\n\n".join(lines)


def format_research_trace(trace: list[dict[str, str]]) -> str:
    if not trace:
        return ""

    lines = ["### Research Attempts"]
    for item in trace:
        lines.append(f"- Attempt {item['attempt']}: {item['query']}")
    return "\n".join(lines)


def parse_result_date(result: dict[str, str]) -> datetime | None:
    published_at = result.get("published_at")
    if not published_at:
        return None

    try:
        value = datetime.fromisoformat(published_at)
    except ValueError:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def select_latest_results(
    results: list[dict[str, str]],
    max_age_days: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    selected_results = []
    rejected_results = []

    for result in results:
        published_date = parse_result_date(result)
        if published_date and published_date < cutoff:
            rejected_results.append(result)
            continue

        selected_results.append(result)

    return selected_results, rejected_results


def select_results_without_freshness_filter(
    results: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return results, []


def deduplicate_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    unique_results = []

    for result in results:
        url = result.get("url", "").split("?")[0].rstrip("/")
        title = " ".join(result.get("title", "").lower().split())
        key = url or title
        if not key or key in seen:
            continue

        seen.add(key)
        unique_results.append(result)

    return unique_results


def answer_with_sources(
    user_question: str,
    results: list[dict[str, str]],
    final_source_count: int,
    trace: list[dict[str, str]] | None = None,
) -> str:
    sources = format_sources(results, final_source_count)
    prompt = f"""
{SYSTEM_PROMPT}

Do not write a Sources section. The program will add source links after your answer.

User question:
{user_question}

Search results:
{sources}
"""
    source_links = format_source_links(results, final_source_count)
    trace_text = format_research_trace(trace or [])
    response, error = ask_llm(prompt, max_tokens=450)
    if response and response.strip():
        parts = [response.strip()]
        if trace_text:
            parts.append(trace_text)
        parts.append(f"### Sources Checked\n{source_links}")
        return "\n\n".join(parts)

    return (
        "The LLM did not return the final summary.\n"
        f"Reason: {error or 'empty response'}\n\n"
        "Here are the sources I found:\n\n"
        f"{source_links}"
    )


def answer_with_evidence_check(
    user_question: str,
    results: list[dict[str, str]],
    final_source_count: int,
    evaluation: dict[str, object],
    trace: list[dict[str, str]],
) -> str:
    evaluation_summary = format_evaluation_summary(evaluation)
    answer = answer_with_sources(user_question, results, final_source_count, trace)

    return f"{evaluation_summary}\n\n{answer}"


def search_with_tool(tool: str, query: str, max_results: int) -> list[dict[str, str]]:
    if tool == "google_news":
        return google_news_search(query, max_results=max_results)

    if tool == "searxng":
        return searxng_search(query, max_results=max_results)

    if tool == "serpapi":
        return google_search(query, max_results=max_results)

    raise SearchError(f"Unknown search tool: {tool}")


def search_web(query: str, search_result_count: int, search_tools: list[str]) -> list[dict[str, str]]:
    results = []
    errors = []
    per_tool_count = max(3, search_result_count)

    for tool in search_tools:
        try:
            tool_results = search_with_tool(tool, query, per_tool_count)
        except (GoogleNewsError, SearchError, SearxngSearchError, requests.RequestException) as error:
            errors.append(f"{tool}: {error}")
            continue

        for result in tool_results:
            result["search_tool"] = tool
        results.extend(tool_results)

    if not results and "searxng" not in search_tools:
        try:
            fallback_results = search_with_tool("searxng", query, per_tool_count)
            for result in fallback_results:
                result["search_tool"] = "searxng"
            results.extend(fallback_results)
        except (SearxngSearchError, requests.RequestException) as error:
            errors.append(f"searxng: {error}")

    results = deduplicate_results(results)
    if results:
        return results[:search_result_count]

    error_details = "; ".join(errors) if errors else "no tools were selected"
    raise SearchError(f"All search tools failed: {error_details}")


def process_results(
    search_query: str,
    results: list[dict[str, str]],
    requires_freshness: bool,
    max_age_days: int,
    final_source_count: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if requires_freshness:
        recent_results, older_results = select_latest_results(results, max_age_days)
        if len(recent_results) < min(final_source_count, len(results)):
            selected_results = recent_results + older_results
            rejected_results = []
        else:
            selected_results = recent_results
            rejected_results = older_results
    else:
        selected_results, rejected_results = select_results_without_freshness_filter(results)

    heuristic_ranked = rank_results(search_query, selected_results, requires_freshness)
    candidate_count = min(len(heuristic_ranked), final_source_count * 3)
    candidates = heuristic_ranked[:candidate_count]
    pre_rejected = heuristic_ranked[candidate_count:]

    llm_ranked = rank_results_with_llm(search_query, candidates, ask_llm)
    if llm_ranked:
        selected_results = llm_ranked[:final_source_count]
        rejected_results = rejected_results + pre_rejected + llm_ranked[final_source_count:]
    else:
        selected_results = heuristic_ranked[:final_source_count]
        rejected_results = rejected_results + heuristic_ranked[final_source_count:]

    return selected_results, rejected_results


def run_research_pass(
    user_question: str,
    search_query: str,
    search_tools: list[str],
    search_result_count: int,
    final_source_count: int,
    requires_freshness: bool,
    max_age_days: int,
    on_progress: Callable[[dict[str, str]], None] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    def report(stage: str, message: str) -> None:
        if on_progress is not None:
            on_progress({"stage": stage, "message": message})

    report("search", "Searching the web for sources...")
    results = search_web(search_query, search_result_count, search_tools)
    report("rank", "Filtering and ranking sources by relevance...")
    selected_results, rejected_results = process_results(
        search_query,
        results,
        requires_freshness,
        max_age_days,
        final_source_count,
    )
    report("read", "Reading and extracting content from the top sources...")
    selected_results = enrich_with_article_text(selected_results, max_articles=final_source_count)
    report("evaluate", "Evaluating evidence quality and coverage...")
    sources = format_sources(selected_results, final_source_count)
    evaluation = evaluate_evidence(user_question, sources, ask_llm)
    return selected_results, rejected_results, evaluation


def run_research(
    user_question: str,
    on_progress: Callable[[dict[str, str]], None] | None = None,
) -> ResearchResult:
    def report(stage: str, message: str) -> None:
        if on_progress is not None:
            on_progress({"stage": stage, "message": message})

    report("plan", "Planning the research strategy and choosing sources...")
    plan = make_research_plan(user_question)
    search_query = str(plan["search_query"])
    search_tools = list(plan["search_tools"])
    search_result_count = int(plan["search_result_count"])
    final_source_count = int(plan["final_source_count"])
    max_age_days = int(plan["max_age_days"])
    requires_freshness = plan["requires_freshness"] == "yes"
    max_attempts = clamp(MAX_RESEARCH_ATTEMPTS, 1, MAX_ALLOWED_RESEARCH_ATTEMPTS)

    all_results = []
    all_rejected = []
    evaluation: dict[str, object] = {}
    trace = []
    attempted_queries = set()

    for attempt in range(1, max_attempts + 1):
        if search_query in attempted_queries:
            break
        attempted_queries.add(search_query)
        trace.append({"attempt": str(attempt), "query": search_query})

        try:
            pass_results, rejected_results, evaluation = run_research_pass(
                user_question,
                search_query,
                search_tools,
                search_result_count,
                final_source_count,
                requires_freshness,
                max_age_days,
                on_progress=on_progress,
            )
        except (GoogleNewsError, SearchError, SearxngSearchError, requests.RequestException) as error:
            report("error", "Search failed, stopping.")
            return ResearchResult(
                question=user_question,
                plan=plan,
                answer=f"Search failed: {error}",
                evaluation={"enough_information": False, "confidence": "low", "reason": str(error)},
                trace=trace,
            )

        all_results = deduplicate_results(all_results + pass_results)
        all_rejected.extend(rejected_results)
        merged = rank_results_with_llm(search_query, all_results, ask_llm)
        if merged:
            all_results = merged[:final_source_count]
        else:
            all_results = rank_results(search_query, all_results, requires_freshness)[:final_source_count]

        enough_information = bool(evaluation.get("enough_information"))
        next_query = evaluation.get("recommended_next_query")
        if enough_information or not isinstance(next_query, str) or not next_query.strip():
            break
        if attempt >= max_attempts:
            break

        search_query = next_query.strip()
        report("retry", "Refining the search query for more specific results...")

    if not all_results:
        return ResearchResult(
            question=user_question,
            plan=plan,
            answer="I could not find any relevant sources to answer this question.",
            evaluation={
                "enough_information": False,
                "confidence": "low",
                "reason": "No sources were found.",
                "missing_information": [],
                "conflicts": [],
                "recommended_next_query": None,
            },
            trace=trace,
        )

    final_sources = format_sources(all_results, final_source_count)
    report("evaluate", "Running a final evidence check...")
    evaluation = evaluate_evidence(user_question, final_sources, ask_llm)
    report("answer", "Writing your answer with citations...")
    answer = answer_with_evidence_check(user_question, all_results, final_source_count, evaluation, trace)

    return ResearchResult(
        question=user_question,
        plan=plan,
        answer=answer,
        evaluation=evaluation,
        sources=all_results,
        rejected=all_rejected,
        trace=trace,
    )


def main() -> None:
    user_question = input("Research question: ").strip()
    if not user_question:
        print("Please enter a research question.")
        return

    print("Planning research...")
    result = run_research(user_question)

    if not result.sources:
        print("No results found.")
        return

    print("Preparing answer...\n")
    print(result.answer)


if __name__ == "__main__":
    main()
