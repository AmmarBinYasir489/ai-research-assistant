import os
from functools import lru_cache

from openai import OpenAI

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def get_providers() -> list[str]:
    raw = os.getenv("LLM_PROVIDERS", "").strip()
    providers = [provider.strip().lower() for provider in raw.split(",") if provider.strip()]

    single = os.getenv("LLM_PROVIDER", "").strip().lower()
    if single and single not in providers:
        providers.append(single)

    if not providers:
        providers = ["ollama"]

    seen = set()
    ordered = []
    for provider in providers:
        if provider in {"ollama", "gemini", "openai"} and provider not in seen:
            seen.add(provider)
            ordered.append(provider)
    return ordered


def _ollama_base_url() -> str:
    host = os.getenv("OLLAMA_HOST", "").strip()
    if host:
        url = host.rstrip("/")
        if not url.startswith("http"):
            url = f"http://{url}"
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        return url
    return os.getenv("OLLAMA_BASE_URL", OLLAMA_DEFAULT_BASE_URL).strip()


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or "your_" in lowered or lowered in {"sk-placeholder", "placeholder"}


@lru_cache(maxsize=8)
def _client(provider: str) -> OpenAI | None:
    if provider == "ollama":
        return OpenAI(api_key="ollama", base_url=_ollama_base_url(), timeout=240)

    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if _looks_like_placeholder(api_key):
            return None
        return OpenAI(
            api_key=api_key,
            base_url=os.getenv("GEMINI_BASE_URL", GEMINI_DEFAULT_BASE_URL).strip(),
            timeout=240,
        )

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if _looks_like_placeholder(api_key):
            return None
        base_url = os.getenv("OPENAI_BASE_URL", OPENAI_DEFAULT_BASE_URL).strip().rstrip("/")
        return OpenAI(api_key=api_key, base_url=f"{base_url}/", timeout=240)

    return None


def _model(provider: str) -> str:
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL") or os.getenv("LLM_MODEL") or "gemini-3.5-flash-lite"
    if provider == "openai":
        return os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini"
    return os.getenv("OLLAMA_MODEL") or os.getenv("LLM_MODEL") or "qwen2.5-coder:latest"


def get_provider_status() -> list[dict[str, str | bool]]:
    statuses = []
    for provider in get_providers():
        statuses.append(
            {
                "name": provider,
                "model": _model(provider),
                "available": _client(provider) is not None,
            }
        )
    return statuses


def ask_llm(prompt: str, json_mode: bool = False, max_tokens: int = 350) -> tuple[str | None, str | None]:
    errors = []
    providers = get_providers()

    for provider in providers:
        client = _client(provider)
        if client is None:
            errors.append(f"{provider}: not configured")
            continue

        payload = {
            "model": _model(provider),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**payload)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{provider}: {error}")
            continue

        content = response.choices[0].message.content
        if not content:
            errors.append(f"{provider}: empty response")
            continue

        return content.strip(), None

    if not errors:
        errors.append("no LLM provider configured")
    return None, "; ".join(errors)


def ask_ollama(prompt: str, json_mode: bool = False, max_tokens: int = 350) -> tuple[str | None, str | None]:
    return ask_llm(prompt, json_mode=json_mode, max_tokens=max_tokens)
