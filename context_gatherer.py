# ---------------------------------------------------------
# context_gatherer.py
# Pulls extra business context from a website and/or Facebook
# Page when provided. Both sources are optional — failures or
# missing URLs never block the campaign pipeline, they just
# mean the agents plan with less context than they could have.
# ---------------------------------------------------------

import re
import requests
from typing import Optional

import config

_TIMEOUT = 8  # seconds — keep this snappy, it's a nice-to-have, not a blocker


def fetch_website_context(url: str) -> Optional[str]:
    """
    Fetches a business's homepage and returns a trimmed, text-only summary
    suitable for dropping into an LLM prompt. Returns None on any failure.
    """
    if not url:
        return None

    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception:
        return None

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta_desc_tag = soup.find("meta", attrs={"name": "description"})
        meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag and meta_desc_tag.get("content") else ""

        body_text = soup.get_text(separator=" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text)[:2000]  # cap so we don't blow the prompt budget

        parts = [p for p in [f"Title: {title}", f"Meta description: {meta_desc}", f"Page content: {body_text}"] if p.strip() and not p.endswith(": ")]
        return "\n".join(parts) if parts else None
    except Exception:
        return None


def fetch_facebook_context(page_url_or_id: str) -> Optional[str]:
    """
    Fetches public info from a Facebook Page via the Graph API, if a
    FACEBOOK_ACCESS_TOKEN is configured. Falls back to None if the token
    is missing, the page can't be resolved, or the request fails —
    this is enrichment, never a prerequisite.
    """
    if not page_url_or_id:
        return None

    try:
        token = config.facebook_access_token()
    except Exception:
        return None  # no token configured — skip silently, not a hard failure

    page_id = page_url_or_id.rstrip("/").split("/")[-1]

    try:
        resp = requests.get(
            f"https://graph.facebook.com/v19.0/{page_id}",
            params={
                "fields": "name,about,category,description,fan_count",
                "access_token": token,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    parts = []
    if data.get("name"):
        parts.append(f"Page name: {data['name']}")
    if data.get("category"):
        parts.append(f"Category: {data['category']}")
    if data.get("about"):
        parts.append(f"About: {data['about']}")
    if data.get("description"):
        parts.append(f"Description: {data['description']}")
    if data.get("fan_count") is not None:
        parts.append(f"Followers: {data['fan_count']}")

    return "\n".join(parts) if parts else None
