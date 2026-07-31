# Research Assistant Agent

A self-contained research agent that turns a question into a cited, evidence-checked summary by searching live news and web sources, reading the top articles, scoring their relevance, and evaluating whether the evidence is strong enough to answer.

It ships with three interfaces: a modern web UI, a REST API, and a terminal CLI — all backed by the same pipeline.

---

## Features

- **Multi-provider LLM with automatic fallback.** Ollama (local, free), Gemini (free tier), or any OpenAI-compatible endpoint. Providers are tried in order; if one fails, the next is used automatically.
- **Free, no-account search.** Google News RSS and SearXNG (multi-instance with failover) work with zero configuration; SerpApi is supported when a key is available.
- **Two-stage relevance ranking.** A lightweight heuristic pre-filters the candidate pool, then the LLM scores each source 0–10 with a reason. Falls back to pure heuristic scoring if the LLM is unavailable.
- **Robust article extraction.** Trafilatura with boilerplate pre-cleaning, backed by a dependency-free HTML parser fallback.
- **Evidence quality checks.** The model judges sufficiency, confidence, gaps, and conflicts — and the agent refines its search query when evidence is weak (up to `MAX_RESEARCH_ATTEMPTS`).
- **Live progress reporting.** The web UI streams every stage ("searching…", "reading articles…", "writing answer…") so you always know what's happening.
- **Modern web UI.** Tailwind-based, responsive, no build step required.

---

## How it works

```
┌────────────┐   ┌──────────────────┐   ┌─────────────────────────────┐
│  Question  │──▶│  1. Research     │──▶│  2. Search (Google News,    │
└────────────┘   │     plan (LLM)   │   │     SearXNG, SerpApi)       │
                 └──────────────────┘   └──────────────┬──────────────┘
                                                        ▼
                 ┌───────────────────────────────────────────────┐
                 │  3. Normalize & rank                          │
                 │     dedup → freshness filter → heuristic      │
                 │     pre-rank → LLM relevance score (0–10)     │
                 └──────────────────────┬────────────────────────┘
                                        ▼
                 ┌───────────────────────────────────────────────┐
                 │  4. Read top sources (trafilatura extraction) │
                 └──────────────────────┬────────────────────────┘
                                        ▼
                 ┌───────────────────────────────────────────────┐
                 │  5. Evidence evaluation (LLM)                 │
                 │     enough? confidence? gaps? conflicts?      │
                 └───────────────┬───────────────┬───────────────┘
                                 │               │ not enough
                                 │ enough        ▼
                                 │      ┌───────────────────────┐
                                 │      │ refine search query   │
                                 │      │ (up to MAX_RESEARCH_  │
                                 │      │  ATTEMPTS)            │
                                 │      └───────────────────────┘
                                 ▼
                 ┌───────────────────────────────────────────────┐
                 │  6. Final answer (LLM) with [n] citations     │
                 │     + sources, evidence check, attempt trace  │
                 └───────────────────────────────────────────────┘
```

### Pipeline stages in detail

1. **Planning** — The LLM chooses the research mode (`news`, `web`, or `hybrid`), the search tools, a search query, how many results to inspect, how many to keep, and a freshness window. Patent/document/historical questions automatically skip the freshness requirement.
2. **Search** — Each selected tool is queried and results are merged and de-duplicated (by URL with query string stripped, or by normalized title).
3. **Filtering & ranking** — Results are dropped if they are older than `max_age_days` (or undated) when freshness is required. A fast heuristic (keyword overlap + authority hints like `.gov`, `.edu`, `reuters.com` + freshness) keeps a candidate pool, then the LLM scores each candidate's relevance to the question with a short reason. The top `final_source_count` sources are kept.
4. **Reading** — The top articles are fetched and their main text extracted (Trafilatura after boilerplate removal, with an HTML-parser fallback).
5. **Evidence evaluation** — The LLM judges whether the sources are sufficient, how confident it is, what's missing, and whether sources conflict. If information is weak, it suggests a refined query and the pipeline retries.
6. **Answer generation** — The LLM writes a direct answer plus 3–5 key findings, each citing a source number. The program appends the evidence check, the source list, and the research-attempt trace.

---

## Quickstart

### Prerequisites

- Python 3.10+
- At least one LLM provider:
  - [Ollama](https://ollama.com) with a pulled model (local/free), or
  - a [Gemini API key](https://aistudio.google.com/apikey) (free tier), or
  - an OpenAI-compatible API key (OpenAI, Groq, OpenRouter, LM Studio, …)

### 1. Install

```bash
git clone <your-repo-url>
cd research-assistant
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Then set your provider and keys in `.env` (see [Configuration](#configuration)).

### 3. Run

**Web UI (recommended):**

```bash
uvicorn app:app --reload
# open http://127.0.0.1:8000
```

**CLI:**

```bash
python main.py
```

**Debug a single question step by step:**

```bash
python scripts/debug_research.py "your research question"
```

---

## Configuration

All configuration is read from environment variables (`.env`). A complete template lives in `.env.example`.

### LLM providers

Providers are tried in `LLM_PROVIDERS` order at every LLM call. A provider is skipped if its key is missing or still a placeholder; if a call throws, the next provider is tried.

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_PROVIDERS` | `ollama` | Comma-separated provider fallback order: `ollama`, `gemini`, `openai`. |
| `LLM_PROVIDER` | — | Legacy single-provider override (appended to `LLM_PROVIDERS`). |
| `LLM_MODEL` | `qwen2.5-coder:latest` | Fallback model used when a provider has no specific model. |
| `OLLAMA_MODEL` | `qwen2.5-coder:latest` | Model for the Ollama provider. |
| `OLLAMA_HOST` | — | Ollama server, e.g. `localhost:11434` or a remote/WSL host `192.168.1.20:11434`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Full OpenAI-compatible base URL override. |
| `GEMINI_API_KEY` | — | Google AI Studio API key (free tier). Starts with `AIza…`. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model for the Gemini provider. |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` | Gemini OpenAI-compatible endpoint. |
| `OPENAI_API_KEY` | — | OpenAI or any OpenAI-compatible service key. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for the OpenAI provider. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible base URL (Groq, OpenRouter, LM Studio, …). |

> **Example:** `LLM_PROVIDERS=ollama,gemini` means *use Ollama first; if it's down or errors, use Gemini.*

### Search & pipeline tuning

| Variable | Default | Clamp | Description |
| --- | --- | --- | --- |
| `SEARCH_PROVIDER` | — | — | Legacy hint only; the LLM plan selects actual tools. |
| `SEARCH_RESULT_COUNT` | `15` | 5–20 | Results inspected per search pass. |
| `FINAL_SOURCE_COUNT` | `5` | 1–8 | Sources kept and cited in the answer. |
| `MAX_ARTICLE_AGE_DAYS` | `30` | 1–365 | Freshness cutoff when the plan requires fresh sources. |
| `MAX_RESEARCH_ATTEMPTS` | `2` | 1–3 | Refinement attempts when evidence is weak. |
| `SEARXNG_INSTANCES` | built-in list | — | Comma-separated SearXNG instances, tried in order. |
| `SEARXNG_INSTANCE` | — | — | Single-instance alternative to `SEARXNG_INSTANCES`. |
| `SERPAPI_API_KEY` | — | — | Enables Google Search through SerpApi. |

---

## API

Base URL: `http://127.0.0.1:8000`

### `GET /`
Serves the web UI.

### `GET /health`
Health check.

### `GET /api/config`
Lists configured LLM providers and whether each is ready.

```json
{
  "llm_providers": [
    { "name": "ollama", "model": "qwen2.5-coder:latest", "available": true },
    { "name": "gemini", "model": "gemini-3.5-flash-lite", "available": true }
  ]
}
```

### `POST /api/research`
Starts a research job in the background.

```bash
curl -X POST http://127.0.0.1:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the latest solid-state battery developments?"}'
```

```json
{ "job_id": "4a3ee3457c1e4fc590fa11ec74f03475" }
```

### `GET /api/research/{job_id}`
Polls the job. Returns `status` (`running` / `done` / `error`), a live `progress` array of `{stage, message}` events, and the final `result` when complete.

```json
{
  "status": "done",
  "progress": [
    { "stage": "plan", "message": "Planning the research strategy and choosing sources..." },
    { "stage": "search", "message": "Searching the web for sources..." },
    { "stage": "read", "message": "Reading and extracting content from the top sources..." },
    { "stage": "evaluate", "message": "Evaluating evidence quality and coverage..." },
    { "stage": "answer", "message": "Writing your answer with citations..." }
  ],
  "result": {
    "question": "...",
    "plan": { "research_mode": "news", "search_tools": ["google_news"], "..." : "..." },
    "answer": "### Evidence Check\n...\n**Short Direct Answer:** ...",
    "evidence": { "enough_information": true, "confidence": "high", "..." : "..." },
    "sources": [ { "title": "...", "url": "...", "relevance_score": "9.00", "..." : "..." } ],
    "trace": [ { "attempt": "1", "query": "..." } ]
  }
}
```

> **Note:** `sources` and `result` are omitted (null) while the job is `running`. Jobs are kept in memory; finished jobs are pruned when more than 50 accumulate.

---

## Project structure

```
.
├── app.py                     # FastAPI web app + background job API
├── main.py                    # Pipeline orchestration + CLI (run_research)
├── requirements.txt
├── .env.example               # Configuration template
├── prompts/
│   └── system_prompt.md       # Answer-generation system prompt
├── scripts/
│   └── debug_research.py      # Step-by-step pipeline debugger
├── tools/
│   ├── article_reader.py      # Article fetching + text extraction (trafilatura + fallback)
│   ├── evidence_evaluator.py  # LLM evidence sufficiency/confidence check
│   ├── google_news.py         # Google News RSS search (no account)
│   ├── google_search.py       # SerpApi/Google search
│   ├── llm.py                 # Multi-provider LLM client with fallback
│   ├── relevance_ranker.py    # LLM relevance scoring
│   ├── result_ranker.py       # Heuristic pre-ranking (keywords, authority, freshness)
│   └── searxng_search.py      # SearXNG search with instance failover
└── web/
    └── index.html             # Tailwind web UI (no build step)
```

---

## Troubleshooting

**"No usable LLM provider configured"** — Every provider is missing a key or still has a placeholder. Set a real `GEMINI_API_KEY`, `OPENAI_API_KEY`, or start Ollama.

**Ollama runs on the Windows host but not in WSL** — WSL cannot reach `localhost` on Windows. Set `OLLAMA_HOST` to your Windows host IP (find it with `ip route | grep default`), e.g. `OLLAMA_HOST=172.24.112.1:11434`.

**SearXNG instances time out** — Public instances are unreliable. Host your own or list several in `SEARXNG_INSTANCES`. Google News RSS works without any setup.

**Every source gets rejected by the freshness filter** — SearXNG/SerpApi results have no publication date, so they are rejected when the plan requires freshness. Use `google_news` for fresh-news questions; the planner uses `research_mode: web` (freshness off) for patents, documents, and historical topics.

**The UI stays on "Starting up..."** — Confirm the server is running and reachable (`curl http://127.0.0.1:8000/health`). Progress events only appear once the background job emits its first stage.

---

## Security

- API keys live only in `.env`, which is gitignored. **Never commit `.env`.**
- The API has no authentication — run it on `127.0.0.1` for personal use; expose it only behind your own auth/reverse proxy if you intend to make it reachable.
- All output from the LLM is HTML-escaped before rendering in the UI.

---

## License

MIT — use it, adapt it, ship it. Attribution appreciated but not required.
