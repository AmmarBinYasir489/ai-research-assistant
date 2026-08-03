import json
import re


def parse_json_array(text: str) -> list[dict]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL)

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []

    return [item for item in parsed if isinstance(item, dict)]


def rank_results_with_llm(
    query: str,
    results: list[dict[str, str]],
    ask_llm,
) -> list[dict[str, str]] | None:
    if not results:
        return []

    numbered = []
    for index, result in enumerate(results, start=1):
        snippet = (result.get("snippet") or result.get("article_text") or "")[:200]
        numbered.append(
            f"{index}. {result.get('title', '')}\n"
            f"   URL: {result.get('url', '')}\n"
            f"   {snippet}"
        )

    prompt = f"""
Rate how relevant each source is to the research query.
Score each source from 0 to 10. 0 means completely irrelevant, 10 means it directly answers the query.
Return JSON only, an array of objects. Include every index exactly once.

Schema:
[
  {{
    "index": 1,
    "score": 8,
    "reason": "one short line"
  }}
]

Do not answer the research query itself.

Research query:
{query}

Sources:
{chr(10).join(numbered)}
"""
    response, _error = ask_llm(prompt, json_mode=True, max_tokens=400)
    if not response:
        return None

    parsed = parse_json_array(response)
    if not parsed:
        return None

    scores_by_index = {}
    for item in parsed:
        index = item.get("index")
        score = item.get("score")
        if isinstance(index, int) and isinstance(score, (int, float)):
            scores_by_index[index] = (float(score), str(item.get("reason", "")))

    if len(scores_by_index) < max(1, int(len(results) * 0.5)):
        return None

    ranked = []
    for index, result in enumerate(results, start=1):
        item = result.copy()
        if index in scores_by_index:
            score, reason = scores_by_index[index]
            item["relevance_score"] = f"{score:.2f}"
            item["relevance_reason"] = reason
        else:
            item["relevance_score"] = "0.00"
            item["relevance_reason"] = "Not scored by the LLM."
        ranked.append(item)

    ranked.sort(key=lambda item: float(item["relevance_score"]), reverse=True)
    return ranked
