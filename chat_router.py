# ---------------------------------------------------------
# chat_router.py
#
# Translates free-text chat into calls against the SAME functions
# main.py's REST endpoints already use (CampaignManager methods) — this
# is a thin routing layer, not a parallel implementation. Every action
# taken here also gets written to campaign_events.py so it shows up in
# both the chat thread and the Hub timeline.
#
# Uses Groq's OpenAI-compatible tool-calling API directly (same
# provider as the planner LLM) rather than routing through langgraph,
# since this is a much simpler single-turn "pick a tool, call it"
# pattern than the campaign pipeline itself.
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


# ---------------------------------------------------------
# Tool definitions (OpenAI/Groq function-calling schema)
# ---------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_campaign",
            "description": (
                "Start a brand new marketing campaign for a business. Use "
                "this when the user describes a business and wants a "
                "campaign launched, even if some details are missing — "
                "ask a clarifying question first if budget or goal is "
                "completely absent, otherwise use reasonable defaults."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "business_context": {"type": "string"},
                    "campaign_goal": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "total_budget": {"type": "number"},
                    "user_plan": {"type": "string", "enum": ["free", "paid"]},
                    "business_website_url": {"type": "string"},
                    "facebook_page_url": {"type": "string"},
                },
                "required": ["business_context", "campaign_goal", "target_audience", "total_budget"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_campaign_status",
            "description": "Fetch the current status, plan, generated assets, and publish state for an existing campaign.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}},
                "required": ["campaign_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reroute_campaign",
            "description": (
                "Update an existing campaign's brief (goal, budget, audience, "
                "context) and trigger a replan. Only call this for a campaign "
                "the user has already started and referenced by campaign_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"},
                    "business_context": {"type": "string"},
                    "campaign_goal": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "total_budget": {"type": "number"},
                },
                "required": ["campaign_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_campaigns",
            "description": "List all campaign_ids that have ever been started, for when the user asks 'what campaigns do I have'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_SYSTEM_PROMPT = """You are the conversational front-end for an autonomous
marketing platform. You can start campaigns, check their status, reroute
them, and list them, via the tools provided. Always confirm what you're
about to do in plain language before or after calling a tool. If the user
hasn't given you a campaign_id for an action that needs one, ask which
campaign they mean (or offer to list their campaigns). Never invent
campaign_id values — either use one the user gave you, or one returned by
list_campaigns/start_campaign.

Note: uploading a reference photo happens via a separate file-upload UI
element in the chat, not through you — if the user says they want to
upload a photo, tell them to use the upload button that appears next to
a pending reference-photo request.
"""


# ---------------------------------------------------------
# Tool execution — calls straight into CampaignManager, same objects
# main.py's REST endpoints use
# ---------------------------------------------------------

def _execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    manager = _get_manager()

    if name == "start_campaign":
        campaign_id = args.get("campaign_id") or f"chat-{uuid.uuid4().hex[:8]}"
        brief = {k: v for k, v in args.items() if k != "campaign_id"}
        manager.start_campaign(campaign_id, brief)  # logs its own "campaign_started" event
        return {"campaign_id": campaign_id, "status": "started"}

    if name == "get_campaign_status":
        campaign_id = args["campaign_id"]
        state = manager.get_status(campaign_id)
        if state is None:
            return {"error": f"No campaign found for campaign_id={campaign_id!r}"}
        return {
            "campaign_id": campaign_id,
            "is_running": manager.is_running(campaign_id),
            "campaign_plan": state.get("campaign_plan"),
            "generated_images": state.get("generated_images"),
            "pending_reference_requests": state.get("pending_reference_requests"),
            "publish_schedule": state.get("publish_schedule"),
            "publishing_status": state.get("publishing_status"),
        }

    if name == "reroute_campaign":
        campaign_id = args["campaign_id"]
        updates = {k: v for k, v in args.items() if k != "campaign_id" and v is not None}
        try:
            manager.reroute_campaign(campaign_id, updates)  # logs its own "campaign_rerouted" event
        except ValueError as e:
            return {"error": str(e)}
        return {"campaign_id": campaign_id, "status": "rerouting"}

    if name == "list_campaigns":
        return {"campaign_ids": manager.list_campaign_ids()}

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------

def handle_chat_message(
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Args:
        messages: Full conversation history, OpenAI-style
                   [{"role": "user"|"assistant", "content": "..."}, ...]

    Returns:
        {
          "reply": "<assistant's text response>",
          "tool_calls": [{"name": ..., "arguments": {...}, "result": {...}}, ...]
        }
        The frontend renders `reply` as a chat bubble, and each entry in
        `tool_calls` as its own inline card (campaign started, status,
        etc.) — see the last conversation turn for the card mapping.
    """
    headers = {
        "Authorization": f"Bearer {config.groq_api_key()}",
        "Content-Type": "application/json",
    }
    payload_messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + messages

    executed_tool_calls: List[Dict[str, Any]] = []

    # Allow a couple of rounds in case the model wants to call a tool,
    # see the result, then respond — but cap it so a bad loop can't hang.
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
            timeout=60,
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        message = choice["message"]

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

    # Ran out of rounds — return whatever we've got rather than looping forever
    return {
        "reply": "I ran into trouble finishing that — could you rephrase or try again?",
        "tool_calls": executed_tool_calls,
    }
