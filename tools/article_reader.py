from urllib.parse import urlparse

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import trafilatura
except ImportError:
    trafilatura = None

from html import unescape
from html.parser import HTMLParser


DROP_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "header",
    "form",
    "aside",
]


class FallbackArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._capture_tag: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}:
            self._skip_depth += 1
            return

        if self._skip_depth:
            return

        if tag in {"h1", "h2", "h3", "p", "li"}:
            self._capture_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return

        if tag == self._capture_tag:
            self._parts.append("\n")
            self._capture_tag = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._capture_tag:
            return

        text = " ".join(unescape(data).split())
        if text:
            self._parts.append(text)

    def text(self) -> str:
        lines = []
        for raw_line in "".join(self._parts).splitlines():
            line = " ".join(raw_line.split())
            if len(line) >= 40:
                lines.append(line)
        return "\n".join(lines)


def extract_text_with_fallback_parser(html: str) -> str:
    parser = FallbackArticleTextParser()
    parser.feed(html)
    return parser.text()


def clean_html_with_soup(html: str) -> str:
    if BeautifulSoup is None:
        return html

    soup = BeautifulSoup(html, "html.parser")
    for selector in DROP_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()
    return str(soup)


def extract_text_from_html(html: str) -> str:
    cleaned_html = clean_html_with_soup(html)

    if trafilatura is not None:
        extracted = trafilatura.extract(
            cleaned_html,
            include_comments=False,
            include_tables=False,
            favor_recall=False,
            include_formatting=False,
        )
        if extracted and extracted.strip():
            return extracted.strip()

    if BeautifulSoup is None:
        return extract_text_with_fallback_parser(cleaned_html)

    soup = BeautifulSoup(cleaned_html, "html.parser")

    main_content = soup.find("article") or soup.find("main") or soup.body or soup
    text_blocks = []

    for tag in main_content.find_all(["h1", "h2", "h3", "p", "li"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if len(text) >= 40:
            text_blocks.append(text)

    return "\n".join(text_blocks)


def read_article(url: str, max_chars: int = 1800) -> str | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 research-assistant-agent/0.1"},
            timeout=25,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        return None

    text = extract_text_from_html(response.text)
    if not text:
        return None

    return text[:max_chars]


def enrich_with_article_text(
    results: list[dict[str, str]],
    max_articles: int = 3,
) -> list[dict[str, str]]:
    enriched = []

    for index, result in enumerate(results):
        item = result.copy()
        if index < max_articles:
            article_text = read_article(result["url"])
            if article_text:
                item["article_text"] = article_text
                item["article_host"] = urlparse(result["url"]).netloc
        enriched.append(item)

    return enriched
