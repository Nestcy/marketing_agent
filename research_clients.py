# ---------------------------------------------------------
# research_clients.py
#
# Direct REST calls to Tavily (search) and Firecrawl (scrape/search) —
# no MCP session/protocol involved, just plain HTTP with an API key.
# Both fail soft (return None) rather than raising, so a missing key
# or a down API degrades the pipeline gracefully instead of crashing
# a campaign run.
# ---------------------------------------------------------

import requests
from typing import Optional

import config

TAVILY_URL = "https://api.tavily.com/search"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"


def tavily_search(query: str, max_results: int = 5) -> Optional[str]:
    """
    General web search — used to research a business's public
    reputation, reviews, competitors, and recent news.
    Returns a flattened text blob of result titles + snippets, or None.
    """
    try:
        api_key = config.tavily_api_key()
    except Exception:
        return None

    try:
        resp = requests.post(
            TAVILY_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None
        return "\n\n".join(
            f"{r.get('title', '')}\n{r.get('content', '')}" for r in results
        )
    except Exception:
        return None


def firecrawl_scrape(url: str) -> Optional[str]:
    """Deep-scrapes a single known URL (typically the business's own website) into clean markdown."""
    try:
        api_key = config.firecrawl_api_key()
    except Exception:
        return None

    try:
        resp = requests.post(
            FIRECRAWL_SCRAPE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"url": url, "formats": ["markdown"]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("markdown")
    except Exception:
        return None


def firecrawl_search(query: str, limit: int = 3) -> Optional[str]:
    """Used when no website URL was supplied — searches the web via Firecrawl and returns scraped content."""
    try:
        api_key = config.firecrawl_api_key()
    except Exception:
        return None

    try:
        resp = requests.post(
            FIRECRAWL_SEARCH_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "limit": limit},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("data", [])
        if not results:
            return None
        return "\n\n".join(
            f"{r.get('title', '')}\n{r.get('description', '')}" for r in results
        )
    except Exception:
        return None
