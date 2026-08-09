# ---------------------------------------------------------
# llm_client.py
# Wraps ChatGroq (langchain-groq) with .with_structured_output()
# so callers get back validated Pydantic objects instead of
# raw strings they have to json.loads() and hope are correct.
# ---------------------------------------------------------

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


def generate_structured(
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    model: str = None,
    temperature: float = 0.7,
) -> T:
    """
    Calls ChatGroq constrained to the given Pydantic schema and returns a
    validated instance of it. Raises (ValidationError, or whatever the
    underlying call throws) if the model can't produce a conforming
    response — callers catch this the same way they caught JSON parse
    errors before, and fall back to mock data.
    """
    llm = _get_llm(model=model, temperature=temperature)
    structured_llm = llm.with_structured_output(schema)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    result = structured_llm.invoke(messages)

    # with_structured_output can return either the schema instance or a
    # dict depending on langchain-groq version; normalize to the schema.
    if isinstance(result, schema):
        return result
    return schema.model_validate(result)
