"""
A multi-turn conversation with the advisor.

The graph handles one turn. This holds what has to survive between turns —
the message history and the learner profile — and, just as importantly,
decides what must *not* survive.

Both callers go through here: the terminal harness and, later, the HTTP API.
Putting the turn loop in one place keeps them from drifting apart on the
question of what an answer is allowed to be grounded in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from app.agent.graph import build_graph
from app.agent.state import LearnerProfile


@dataclass
class Turn:
    """One exchange, with the evidence behind it."""

    answer: str
    tool_results: list[dict] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)

    @property
    def tools_used(self) -> list[str]:
        return sorted({entry["tool"] for entry in self.tool_results})

    @property
    def was_refused(self) -> bool:
        """True when the gate blocked the draft and substituted a refusal."""
        return bool(self.unsupported_claims) and "can't give you those details" in self.answer


class Advisor:
    """
    A single learner's conversation.

    Not thread-safe and not shared: one instance per conversation. The state it
    keeps is small enough that a server can hold one per session cheaply.
    """

    def __init__(self, model: BaseChatModel):
        self._graph = build_graph(model)
        self.history: list[BaseMessage] = []
        self.profile = LearnerProfile()

    def ask(self, text: str) -> Turn:
        state = self._graph.invoke(
            {
                "messages": self.history + [HumanMessage(content=text)],
                "profile": self.profile,
                # Every turn is graded against its own evidence. Carrying tool
                # results forward would let a fee fetched three questions ago
                # vouch for a number in today's answer — stale grounding is
                # precisely what the gate exists to catch, so the slate is
                # wiped rather than accumulated.
                "tool_results": [],
                "unsupported_claims": [],
                "verification_attempts": 0,
            },
            {"recursion_limit": 25},
        )

        # The profile is the exception: a learner who mentions their goal once
        # should not have to repeat it.
        self.history = state["messages"]
        self.profile = state.get("profile") or self.profile

        return Turn(
            answer=state.get("answer") or "",
            tool_results=state.get("tool_results") or [],
            unsupported_claims=state.get("unsupported_claims") or [],
        )

    def reset(self) -> None:
        self.history = []
        self.profile = LearnerProfile()
