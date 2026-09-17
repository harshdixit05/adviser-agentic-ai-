"""
What the graph carries between nodes.

`tool_results` is the important field: it accumulates everything the tools
returned this turn, and the verify node checks the drafted answer against it.
Nothing may be stated that did not come through here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.db.models import CourseLevel

Intent = Literal["facts", "explain", "both", "chat"]


class LearnerProfile(BaseModel):
    """
    Built up across turns. Every field is optional because a learner reveals
    things gradually, and a half-known profile still produces better
    recommendations than none.
    """

    background: str | None = None
    career_goal: str | None = None
    current_level: CourseLevel | None = None
    audience: str | None = Field(
        default=None,
        description="Matched to Intellimindz's own persona tags, e.g. 'Chartered Accountants'",
    )
    domain_interest: str | None = None
    hours_available: float | None = None

    def merge(self, update: LearnerProfile) -> LearnerProfile:
        """
        Later turns add to the profile; they never blank a field. A learner who
        mentions their goal once should not lose it by asking an unrelated
        question next.
        """
        merged = self.model_dump()
        for key, value in update.model_dump().items():
            if value is not None:
                merged[key] = value
        return LearnerProfile(**merged)

    def is_empty(self) -> bool:
        return all(value is None for value in self.model_dump().values())


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    profile: LearnerProfile
    intent: Intent

    # Everything the tools returned this turn, as plain dicts. The verify node
    # treats this as the complete set of facts the answer may contain.
    tool_results: list[dict[str, Any]]

    draft: str
    answer: str

    # Set when verification rejected a draft; drives the single retry.
    unsupported_claims: list[str]
    verification_attempts: int
