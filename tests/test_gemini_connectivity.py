"""Opt-in live Gemini connectivity check.

Skipped by default. Run it explicitly to verify the configured Gemini API key,
base URL, and model still work together -- e.g. after Gemini changes its base
URL or retires a model, this test fails with a clear message instead of every
real run silently failing:

    RUN_LIVE_GEMINI=1 python -m pytest tests/test_gemini_connectivity.py -s -v
"""

import os

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.core.config import settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_GEMINI") != "1",
    reason="Set RUN_LIVE_GEMINI=1 to run the live Gemini connectivity check.",
)


class _Ping(BaseModel):
    ok: bool


def test_gemini_key_url_and_model_are_reachable():
    """Fails loudly if the key, base URL, or model is missing or no longer works."""
    keys = settings.resolved_api_keys()
    assert keys, "No Gemini API key configured (set GEMINI_API_KEY or GEMINI_API_KEYS)."

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=settings.gemini_model,
        api_key=keys[0],
        base_url=settings.gemini_base_url,
        timeout=settings.gemini_timeout_seconds,
        max_retries=0,
        temperature=0,
    )
    try:
        result = llm.with_structured_output(_Ping).invoke(
            [HumanMessage(content="Respond with JSON ok=true.")]
        )
    except Exception as exc:
        pytest.fail(
            "Gemini connectivity failed | "
            f"model={settings.gemini_model} | url={settings.gemini_base_url} | "
            f"error={exc}"
        )
    assert isinstance(result, _Ping), (
        "Gemini did not return structured output; the model or base URL may have "
        "changed."
    )
