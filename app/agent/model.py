"""
Where the graph gets its model.

Kept in one small module so that everything else — the tools, the gate, the
tests — stays model-agnostic. `build_graph` takes a model as an argument
precisely so the suite can drive it with a stub; this is the other caller.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from app.core.config import PROJECT_ROOT, get_settings


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
        env_file = PROJECT_ROOT / ".env.local"
        state = (
            f"Open this file:\n\n  {env_file}\n\n"
            'and put the key between the quotes on the GEMINI_API_KEY="" line.'
            if env_file.exists()
            else f"That file does not exist yet. Create it by copying .env.example:"
            f"\n\n  {PROJECT_ROOT / '.env.example'}\n  ->  {env_file}\n\n"
            'then put the key between the quotes on the GEMINI_API_KEY="" line.'
        )
        raise ModelNotConfigured(
            f"GEMINI_API_KEY is not set.\n\n{state}\n\n"
            "Get a key at https://aistudio.google.com/apikey\n\n"
            ".env.local is gitignored, so it will not be committed. Never paste a "
            "key into a chat, a commit or a log — if one has been, rotate it first."
        )

    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.0,
        max_retries=2,
    )
