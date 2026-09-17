"""
Where the graph gets its model.

Kept in one small module so that everything else — the tools, the gate, the
tests — stays model-agnostic. `build_graph` takes a model as an argument
precisely so the suite can drive it with a stub; this is the other caller.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from app.core.config import get_settings


class ModelNotConfigured(RuntimeError):
    """Raised when GEMINI_API_KEY is absent, with what to do about it."""


def build_model() -> BaseChatModel:
    """
    The advisor's model, configured for factual work.

    Temperature is zero: this assistant quotes fees and durations, and there
    is no upside to varied phrasing when the cost of a varied *number* is a
    learner being told the wrong price.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    if not settings.gemini_api_key:
        raise ModelNotConfigured(
            "GEMINI_API_KEY is not set. Put it in .env.local (see .env.example), "
            "which is gitignored. Never paste a key into a chat, a commit or a log — "
            "if one has been, rotate it at https://aistudio.google.com/apikey first."
        )

    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.0,
        max_retries=2,
    )
