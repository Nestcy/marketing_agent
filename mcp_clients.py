# ---------------------------------------------------------
# mcp_clients.py
#
# Thin wrappers around remote MCP servers, called via the official
# `mcp` Python SDK over Streamable HTTP. Used by marketing_engine.py's
# business_research_node (Brave Search + Firecrawl) and
# calendar_scheduling_node (Calendar MCP).
#
# IMPORTANT — you need to fill in / verify three things once you've
# picked your actual hosted MCP endpoints, since exact URLs and tool
# names vary by provider and change over time:
#   1. BRAVE_SEARCH_MCP_URL   + the tool name used below ("brave_web_search")
#   2. FIRECRAWL_MCP_URL      + tool names ("firecrawl_scrape", "firecrawl_search")
#   3. CALENDAR_MCP_URL       + tool name ("create_event") and its argument
#      shape — this differs a lot between Google Calendar MCP servers,
#      Cal.com MCP, Nylas MCP, etc. Check whichever server you land on
#      via its `tools/list` response and adjust call_mcp_tool's arguments
#      dict accordingly.
#
# All functions here fail soft (return None) rather than raising, so a
# missing/misconfigured MCP server degrades the pipeline gracefully
# instead of crashing a campaign run — consistent with how
# context_gatherer.py already treats website/Facebook enrichment as
# optional.
# ---------------------------------------------------------

import asyncio
import os
from typing import Any, Dict, List, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import config


# ---------------------------------------------------------
# Generic MCP tool-call helper
# ---------------------------------------------------------

async def _call_tool_async(
    server_url: str, headers: Dict[str, str], tool_name: str, arguments: Dict[str, Any]
) -> str:
    async with streamable_http_client(server_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text_parts: List[str] = []
            for block in result.content:
                if hasattr(block, "text") and block.text:
                    text_parts.append(block.text)
            return "\n".join(text_parts)


def call_mcp_tool(
    server_url: str, headers: Dict[str, str], tool_name: str, arguments: Dict[str, Any]
) -> Optional[str]:
    """
    Sync wrapper — every node in marketing_engine.py is a plain sync
    function (LangGraph doesn't require async here), so this hides the
    asyncio plumbing from callers.
    """
    try:
        return asyncio.run(_call_tool_async(server_url, headers, tool_name, arguments))
    except Exception:
        return None


# ---------------------------------------------------------
# Brave Search MCP
# https://github.com/brave/brave-search-mcp-server (or Brave's hosted
# remote MCP endpoint, if/when using that instead of self-hosting)
# ---------------------------------------------------------

BRAVE_SEARCH_MCP_URL = os.environ.get("BRAVE_SEARCH_MCP_URL", "https://mcp.brave.com/mcp")


def brave_web_search(query: str, count: int = 5) -> Optional[str]:
    """
    General web search — used to research a business's public
    reputation, reviews, competitors, and recent news that isn't on
    their own website.
    """
    try:
        api_key = config.brave_api_key()
    except Exception:
        return None

    return call_mcp_tool(
        BRAVE_SEARCH_MCP_URL,
        headers={"X-Subscription-Token": api_key},
        tool_name="brave_web_search",
        arguments={"query": query, "count": count},
    )


# ---------------------------------------------------------
# Firecrawl MCP
# https://docs.firecrawl.dev/mcp-server
# ---------------------------------------------------------

FIRECRAWL_MCP_URL = os.environ.get("FIRECRAWL_MCP_URL", "https://mcp.firecrawl.dev/mcp")


def firecrawl_scrape(url: str) -> Optional[str]:
    """
    Deep-scrapes a single known URL (typically the business's own
    website) into clean markdown — richer and more reliable than the
    raw BeautifulSoup scrape in context_gatherer.py, at the cost of
    needing a Firecrawl API key.
    """
    try:
        api_key = config.firecrawl_api_key()
    except Exception:
        return None

    return call_mcp_tool(
        FIRECRAWL_MCP_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        tool_name="firecrawl_scrape",
        arguments={"url": url, "formats": ["markdown"]},
    )


def firecrawl_search(query: str, limit: int = 3) -> Optional[str]:
    """
    Used when no website URL was supplied — searches the web via
    Firecrawl and scrapes the top results in one call, so we can still
    ground the plan in real content instead of just the business's own
    text description.
    """
    try:
        api_key = config.firecrawl_api_key()
    except Exception:
        return None

    return call_mcp_tool(
        FIRECRAWL_MCP_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        tool_name="firecrawl_search",
        arguments={"query": query, "limit": limit},
    )


# ---------------------------------------------------------
# Calendar MCP
# Generic — swap in your chosen provider's actual tool name/argument
# shape (Google Calendar MCP, Cal.com MCP, Nylas MCP, etc.)
# ---------------------------------------------------------

CALENDAR_MCP_URL = os.environ.get("CALENDAR_MCP_URL", "")


def calendar_create_event(
    calendar_id: str, summary: str, description: str, start_iso: str, end_iso: str
) -> Optional[str]:
    """
    Creates one calendar event per scheduled asset (see
    calendar_scheduling_node in marketing_engine.py). Returns the
    provider's event id/text confirmation, or None if the calendar MCP
    isn't configured or the call fails — scheduling never blocks the
    rest of the pipeline.
    """
    if not CALENDAR_MCP_URL:
        return None

    try:
        api_key = config.calendar_mcp_api_key()
    except Exception:
        return None

    return call_mcp_tool(
        CALENDAR_MCP_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        tool_name="create_event",
        arguments={
            "calendarId": calendar_id,
            "summary": summary,
            "description": description,
            "start": start_iso,
            "end": end_iso,
        },
    )
