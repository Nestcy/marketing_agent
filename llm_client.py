# ---------------------------------------------------------
# llm_client.py
# Wraps ChatGroq (langchain-groq) with .with_structured_output()
# so callers get back validated Pydantic objects instead of
# raw strings they have to json.loads() and hope are correct.
#
# Also handles Groq 429 rate limits by waiting and retrying
# transparently instead of surfacing an error — on the free tier,
# per-minute limits reset quickly, so a short wait-and-retry is
# invisible to the caller as long as the frontend shows something
# engaging during the wait (see /api/campaign/{id}/status "is_running"
# and the rotating loading-message pattern in the frontend).
# ---------------------------------------------------------

import time
from typing import Type, TypeVar

try:
    from langchain_groq import ChatGroq
    from langchain_core.messages import SystemMessage, HumanMessage
    _HAS_LANGCHAIN = True
except ImportError:
    _HAS_LANGCHAIN = False
    ChatGroq = None
    SystemMessage = None
    HumanMessage = None

from pydantic import BaseModel
import config

T = TypeVar("T", bound=BaseModel)

_llm_cache = {}

# Cap on how long any single retry-wait is allowed to sleep. Groq's
# free-tier per-minute windows reset well within this, so 90s comfortably
# covers a worst-case "just hit the limit" wait without holding a request
# open indefinitely if something else is wrong.
_MAX_RETRY_WAIT_SECONDS = 90
_MAX_RETRIES = 2


def _get_llm(model: str = None, temperature: float = 0.7) -> ChatGroq:
    model = model or config.groq_model()
    key = (model, temperature)
    if key not in _llm_cache:
        _llm_cache[key] = ChatGroq(
            model=model,
            temperature=temperature,
            api_key=config.groq_api_key(),
        )
    return _llm_cache[key]


def _extract_retry_after_seconds(exc: Exception) -> float:
    """
    Groq (via the underlying openai-python client) attaches the raw HTTP
    response to rate-limit exceptions. Prefer the server-provided
    Retry-After header when present — it's the actual reset time, not a
    guess — falling back to a fixed wait otherwise.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None) or {}
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_RETRY_WAIT_SECONDS)
            except ValueError:
                pass
    return _MAX_RETRY_WAIT_SECONDS


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status == 429:
        return True
    return "rate_limit" in str(exc).lower() or "429" in str(exc)


def generate_structured(
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    model: str = None,
    temperature: float = 0.7,
) -> T:
    """
    Calls ChatGroq constrained to the given Pydantic schema and returns a
    validated instance of it. On a 429 rate limit, waits (using the
    server's Retry-After hint when available, capped at 90s) and retries
    up to _MAX_RETRIES times before giving up — callers still get a
    normal exception on genuine failures, they just don't see transient
    per-minute rate limits at all.
    """
    llm = _get_llm(model=model, temperature=temperature)
    structured_llm = llm.with_structured_output(schema)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    attempt = 0
    while True:
        try:
            result = structured_llm.invoke(messages)
            break
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < _MAX_RETRIES:
                wait_seconds = _extract_retry_after_seconds(e)
                time.sleep(wait_seconds)
                attempt += 1
                continue
            raise

    # with_structured_output can return either the schema instance or a
    # dict depending on langchain-groq version; normalize to the schema.
    if isinstance(result, schema):
        return result
    return schema.model_validate(result)
