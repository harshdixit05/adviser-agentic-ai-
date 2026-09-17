"""What crosses the wire. Deliberately narrow."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.config import get_settings

# Read once at import: the cap is a deployment decision, not a per-request one.
MAX_MESSAGE_CHARS = get_settings().max_message_chars


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    session_id: str
    answer: str

    # Which catalogue lookups stand behind the answer. Exposed because a
    # learner being told a fee deserves to know it was looked up, and because
    # it makes a regression in grounding visible from outside the service.
    sources: list[str] = []

    # True when the gate stopped a draft rather than the model choosing not to
    # answer. Worth logging and worth alerting on if it stops being rare.
    withheld: bool = False


class HealthResponse(BaseModel):
    status: str
    catalogue_courses: int
    model_configured: bool
    active_sessions: int
