# Research Assistant Agent

Small beginner-friendly research agent using:

- Google News RSS for no-account current news search
- SearXNG for no-account web search
- SerpApi for Google Search results when you have an API key
- A Markdown system prompt
- Optional Ollama for local/free LLM responses

## Setup

1. Copy `.env.example` to `.env`.

2. For free no-account current news search, keep:

```text
SEARCH_PROVIDER=google_news
OLLAMA_MODEL=qwen2.5-coder:latest
```

For broader no-account web search, try:

```text
SEARCH_PROVIDER=searxng
SEARXNG_INSTANCES=https://search.inetol.net,https://searx.tiekoetter.com,https://opnxng.com,https://northboot.xyz
OLLAMA_MODEL=qwen2.5-coder:latest
```

3. For Google Search through SerpApi later, change:

```text
SEARCH_PROVIDER=serpapi
SERPAPI_API_KEY=your_key_here
```

3. Install dependencies:

```bash
git clone <your-repo-url>
cd research-assistant
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

4. Optional: install and run Ollama if you want local LLM answers:

```bash
uvicorn app:app --reload
# open http://127.0.0.1:8000
```

5. Run:

```bash
python main.py
```

If Ollama is not running, the agent still searches Google and prints source results.
