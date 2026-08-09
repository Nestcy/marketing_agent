# ---------------------------------------------------------
# chat_router.py
#
# Translates free-text chat into calls against the SAME
# CampaignManager methods main.py's REST endpoints use — a thin
# routing layer, not a parallel implementation.
# ---------------------------------------------------------

import json
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
                "business and drafts a full day-by-day content calendar for review. "
                "Ask for goal, audience, and timeframe (30 or 90 days) if missing "
                "before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "business_context": {"type": "string"},
                    "campaign_goal": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "timeframe_days": {"type": "integer", "enum": [30, 90]},
                    "user_plan": {"type": "string", "enum": ["free", "paid"]},
                    "business_website_url": {"type": "string"},
                    "facebook_page_url": {"type": "string"},
                },
                "required": ["business_context", "campaign_goal", "target_audience", "timeframe_days"],
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
            "description": "Generate (or regenerate) the caption + image draft for one specific day. Only works once the plan is approved.",
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
content marketing platform. Flow: a campaign starts with a full
day-by-day content calendar draft, which the business owner must
approve (or ask for changes to) before any daily content gets
generated. Once approved, each day's actual ad (image + caption) gets
generated and must ALSO be approved individually — approving a day
marks it finished and ready for the business to use. This platform
does not post or crosspost anywhere yet (that's a separate, future
feature) — be clear about that if the user asks whether something has
been "posted."

Distinguish carefully between:
- refine_plan: feedback about the OVERALL calendar/strategy
- tweak_day: feedback about ONE specific day's draft
Ask which the user means if it's ambiguous whether their feedback
applies to the whole calendar or just one day.

If the user hasn't given a campaign_id for an action that needs one,
ask, or offer to list their campaigns. Never invent campaign_id or date
values.

Reference photo uploads happen via a separate file-upload UI element,
not through you — direct users there if they mention uploading a photo.
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
            return {
                "plan_status": state.get("plan_status"),
                "calendar_plan": state.get("calendar_plan"),
                "asset_status": state.get("asset_status"),
                "generated_captions": state.get("generated_captions"),
            }

        if name == "approve_plan":
            manager.approve_plan(args["campaign_id"])
            return {"status": "approved"}

        if name == "refine_plan":
            manager.refine_plan(args["campaign_id"], args["feedback"])
            return {"status": "refining"}

        if name == "generate_day":
            return manager.generate_day_asset(args["campaign_id"], args["date"])

        if name == "approve_day":
            return manager.approve_day(args["campaign_id"], args["date"])

        if name == "tweak_day":
            return manager.tweak_day(args["campaign_id"], args["date"], args["feedback"])

        if name == "list_campaigns":
            return {"campaign_ids": manager.list_campaign_ids()}

        return {"error": f"Unknown tool: {name}"}
    except ValueError as e:
        return {"error": str(e)}


def handle_chat_message(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Returns {"reply": str, "tool_calls": [{"name", "arguments", "result"}, ...]}
    """
    headers = {"Authorization": f"Bearer {config.groq_api_key()}", "Content-Type": "application/json"}
    payload_messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + messages

    executed_tool_calls: List[Dict[str, Any]] = []

    for _ in range(4):
        resp = requests.post(
            GROQ_CHAT_URL,
            headers=headers,
            json={
                "model": config.groq_model(),
                "messages": payload_messages,
                "tools": TOOLS,
                "tool_choice": "auto",
            },
            timeout=20,
        )
        resp.raise_for_status()
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
