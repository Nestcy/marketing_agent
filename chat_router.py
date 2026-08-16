# ---------------------------------------------------------
# chat_router.py
#
# Translates free-text chat into calls against the SAME
# CampaignManager methods main.py's REST endpoints use — a thin
# routing layer, not a parallel implementation.
# ---------------------------------------------------------

import json
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

import config
from campaign_manager import CampaignManager

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

_manager: Optional[CampaignManager] = None


def _get_manager() -> CampaignManager:
    global _manager
    if _manager is None:
        _manager = CampaignManager()
    return _manager


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_campaign",
            "description": (
                "Start a brand new content calendar for a business — researches the "
                "business and drafts a focused 3-day (3d) content calendar outline for review. "
                "Ask for goal and audience if missing before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "business_context": {"type": "string"},
                    "campaign_goal": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "timeframe_days": {"type": "integer", "default": 3},
                    "user_plan": {"type": "string", "enum": ["free", "paid"]},
                    "business_website_url": {"type": "string"},
                    "facebook_page_url": {"type": "string"},
                },
                "required": ["business_context", "campaign_goal", "target_audience"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_campaign_status",
            "description": "Fetch the current plan, per-day drafts, and their approval status for a campaign.",
            "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}}, "required": ["campaign_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_plan",
            "description": "Approve the draft calendar as-is. Locks it in and lets daily content generation begin.",
            "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}}, "required": ["campaign_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refine_plan",
            "description": "The user wants changes to the overall calendar (not just one day). Records their feedback and regenerates the whole calendar.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}, "feedback": {"type": "string"}},
                "required": ["campaign_id", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_day",
            "description": "Generate (or regenerate) the caption, ad copy variants, and image prompt for one specific day. Only works once the plan is approved.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}, "date": {"type": "string", "description": "ISO date, e.g. 2026-08-10"}},
                "required": ["campaign_id", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_days_ahead",
            "description": "Generate several upcoming not-yet-generated days all at once (e.g. a week's worth) so the user can review multiple drafts in one sitting, instead of one per day. Each still needs individual review/approval afterward.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}, "count": {"type": "integer", "description": "How many upcoming days to generate, e.g. 5"}},
                "required": ["campaign_id", "count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_day",
            "description": "Approve one specific day's draft — marks it finished and ready for the business to use. This platform doesn't post/crosspost anywhere yet.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}, "date": {"type": "string"}},
                "required": ["campaign_id", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tweak_day",
            "description": "The user wants changes to ONE specific day's draft (not the whole calendar). Records feedback and regenerates just that day. Does not publish.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}, "date": {"type": "string"}, "feedback": {"type": "string"}},
                "required": ["campaign_id", "date", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_campaigns",
            "description": "List all campaign_ids that have ever been started.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_SYSTEM_PROMPT = """You are the conversational front-end for an organic
content marketing platform. Flow: a campaign starts with a 3-day (3d)
content calendar draft, which the business owner must approve (or ask for
changes to) before any daily content gets generated. Once approved, each
day's content — caption, a few ad copy variants, and a ready-to-use
image_prompt (a text-to-image prompt the business can paste into an
image generation tool of their own choice) — gets generated via daily
tasks or on demand. Each day's draft must be approved individually by
the business owner, who can also tweak or refine it whenever they wish.

Distinguish carefully between:
- refine_plan: feedback about the OVERALL 3-day calendar/strategy
- tweak_day: feedback about ONE specific day's draft (caption, ad copy, or image prompt)
Ask which the user means if it's ambiguous.

If the user hasn't given a campaign_id for an action that needs one, ask, or offer to list their campaigns. Never invent campaign_id or date values.

If a day's draft is flagged as needing a reference photo, mention that the business should use their own photo for that one rather than the AI-imagined prompt — this platform doesn't generate images itself.
"""


def _execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    manager = _get_manager()

    try:
        if name == "start_campaign":
            campaign_id = args.get("campaign_id") or f"chat-{uuid.uuid4().hex[:8]}"
            brief = {k: v for k, v in args.items() if k != "campaign_id"}
            manager.start_campaign(campaign_id, brief)
            return {"campaign_id": campaign_id, "status": "started"}

        if name == "get_campaign_status":
            state = manager.get_status(args["campaign_id"])
            if state is None:
                return {"error": f"No campaign found for campaign_id={args['campaign_id']!r}"}

            # Deliberately NOT dumping every day's full caption/ad-copy/
            # image-prompt text here — on a long-running campaign that
            # payload only grows, and it gets re-sent to the model on
            # EVERY chat turn (the frontend resends full history each
            # message), which was compounding token usage badly the
            # longer a campaign went on. Give the model just enough to
            # answer "what's going on" and point it at generate_day/
            # get a specific date's detail only when actually needed.
            asset_status = state.get("asset_status") or {}
            status_counts: Dict[str, int] = {}
            for s in asset_status.values():
                status_counts[s] = status_counts.get(s, 0) + 1

            today_str = None
            calendar_dates = sorted(state.get("calendar_dates") or [])
            import datetime
            today = datetime.date.today().isoformat()
            upcoming_or_today = [d for d in calendar_dates if d >= today]
            focus_date = upcoming_or_today[0] if upcoming_or_today else (calendar_dates[-1] if calendar_dates else None)

            focus_detail = None
            if focus_date and focus_date in asset_status:
                focus_detail = {
                    "date": focus_date,
                    "status": asset_status.get(focus_date),
                    "caption": (state.get("generated_captions") or {}).get(focus_date),
                    "ad_copy_variants": (state.get("ad_copy_variants") or {}).get(focus_date),
                    "image_prompt": (state.get("image_prompts") or {}).get(focus_date),
                }

            return {
                "plan_status": state.get("plan_status"),
                "strategy_outline": state.get("strategy_outline"),
                "total_days_in_calendar": len(calendar_dates),
                "days_by_status": status_counts,
                "generated_dates": sorted(asset_status.keys()),
                "most_relevant_day": focus_detail,
                "note": "Full detail for other specific dates is available via generate_day (regenerates) — ask the user which date they mean if they want an older day's content re-surfaced.",
            }

        if name == "approve_plan":
            manager.approve_plan(args["campaign_id"])
            return {"status": "approved"}

        if name == "refine_plan":
            manager.refine_plan(args["campaign_id"], args["feedback"])
            return {"status": "refining"}

        if name == "generate_day":
            return manager.generate_day_asset(args["campaign_id"], args["date"])

        if name == "generate_days_ahead":
            return {"generated": manager.generate_days_ahead(args["campaign_id"], args["count"], source="chat")}

        if name == "approve_day":
            return manager.approve_day(args["campaign_id"], args["date"])

        if name == "tweak_day":
            return manager.tweak_day(args["campaign_id"], args["date"], args["feedback"])

        if name == "list_campaigns":
            return {"campaign_ids": manager.list_campaign_ids()}

        return {"error": f"Unknown tool: {name}"}
    except ValueError as e:
        return {"error": str(e)}


def _post_groq_chat_with_retry(headers: dict, payload: dict, max_retries: int = 2) -> requests.Response:
    """
    Same rate-limit-tolerant retry behavior as llm_client.generate_structured
    — waits out a 429 (using Retry-After when the response provides it,
    capped at 90s) and retries, instead of surfacing the error to the
    caller. On the free tier, per-minute windows reset quickly enough
    that this is invisible as long as the frontend shows something
    engaging (rotating status text) during the wait.
    """
    attempt = 0
    while True:
        resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=100)
        if resp.status_code == 429 and attempt < max_retries:
            retry_after = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            try:
                wait_seconds = min(float(retry_after), 90) if retry_after else 90
            except ValueError:
                wait_seconds = 90
            time.sleep(wait_seconds)
            attempt += 1
            continue
        resp.raise_for_status()
        return resp


def handle_chat_message(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Returns {"reply": str, "tool_calls": [{"name", "arguments", "result"}, ...]}
    """
    headers = {"Authorization": f"Bearer {config.groq_api_key()}", "Content-Type": "application/json"}
    payload_messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + messages

    executed_tool_calls: List[Dict[str, Any]] = []

    for _ in range(4):
        resp = _post_groq_chat_with_retry(
            headers,
            {
                "model": config.groq_chat_model(),
                "messages": payload_messages,
                "tools": TOOLS,
                "tool_choice": "auto",
            },
        )
        message = resp.json()["choices"][0]["message"]

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return {"reply": message.get("content", ""), "tool_calls": executed_tool_calls}

        payload_messages.append(message)

        for call in tool_calls:
            fn_name = call["function"]["name"]
            try:
                fn_args = json.loads(call["function"]["arguments"])
            except json.JSONDecodeError:
                fn_args = {}

            result = _execute_tool(fn_name, fn_args)
            executed_tool_calls.append({"name": fn_name, "arguments": fn_args, "result": result})

            payload_messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result),
            })

    return {
        "reply": "I ran into trouble finishing that — could you rephrase or try again?",
        "tool_calls": executed_tool_calls,
    }
