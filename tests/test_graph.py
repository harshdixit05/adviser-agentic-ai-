"""
End-to-end runs of the graph with a scripted model.

No Gemini call happens here. The model is replaced by a stub that returns a
fixed sequence of replies, which lets the interesting behaviour — a tool loop
followed by a grounded or ungrounded draft — be exercised deterministically.

A grounded turn consumes exactly the replies queued below. `act` writes the
draft itself, so `compose` is only reached when verification sends one back;
each test's queue is therefore also a record of what a turn costs.

The test that matters is test_invented_price_never_reaches_the_user.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from app.agent.graph import build_graph
from app.agent.state import LearnerProfile
from app.db.models import Course
from app.db.session import SessionFactory


class ScriptedModel:
    """Returns queued replies in order, recording what it was asked."""

    def __init__(self, replies: list[AIMessage]):
        self.replies = list(replies)
        self.calls: list[list] = []

    def invoke(self, messages, **_):
        self.calls.append(messages)
        if not self.replies:
            return AIMessage(content="(no more scripted replies)")
        return self.replies.pop(0)

    def bind_tools(self, _tools):
        return self


def no_profile() -> AIMessage:
    """The understand node's extraction call, revealing nothing."""
    return AIMessage(
        content='{"background":null,"career_goal":null,"current_level":null,'
        '"audience":null,"domain_interest":null,"hours_available":null}'
    )


def search_call(**args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "search_courses", "args": args, "id": "call_1", "type": "tool_call"}],
    )


@pytest.fixture(scope="module", autouse=True)
def require_catalogue():
    with SessionFactory() as session:
        if session.scalar(select(Course).limit(1)) is None:
            pytest.skip("catalogue not loaded — run python -m scripts.ingest_excel")


def run(model, text: str) -> dict:
    graph = build_graph(model)
    return graph.invoke(
        {"messages": [HumanMessage(content=text)], "profile": LearnerProfile()},
        {"recursion_limit": 25},
    )


class TestToolLoop:
    def test_a_tool_call_is_executed_and_its_results_captured(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="Digital Payments Ecosystem runs 3 hours."),
            ]
        )
        state = run(model, "What payments courses do you have?")

        assert state["tool_results"], "the tool call should have run"
        assert state["tool_results"][0]["tool"] == "search_courses"
        assert state["tool_results"][0]["results"], "search should have found courses"

    def test_the_answer_is_published_when_grounded(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="Digital Payments Ecosystem runs 3 hours."),
            ]
        )
        state = run(model, "What payments courses do you have?")

        assert state["answer"] == "Digital Payments Ecosystem runs 3 hours."
        assert state["unsupported_claims"] == []
        assert isinstance(state["messages"][-1], AIMessage)


class TestCallCost:
    """
    Every model call is a paid Gemini request on a public endpoint. `act` used
    to draft an answer that `compose` then regenerated, spending two calls to
    produce one reply. These pin the cost so that regression is noisy.
    """

    def test_a_grounded_tool_turn_costs_three_calls(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="Digital Payments Ecosystem runs 3 hours."),
            ]
        )
        run(model, "What payments courses do you have?")

        # understand, act (tool call), act (draft) — and no compose.
        assert len(model.calls) == 3

    def test_a_turn_needing_no_tools_costs_two_calls(self):
        model = ScriptedModel(
            [no_profile(), AIMessage(content="Tell me what you're hoping to learn.")]
        )
        state = run(model, "Hello")

        assert len(model.calls) == 2
        assert state["answer"] == "Tell me what you're hoping to learn."

    def test_only_a_rejected_draft_pays_for_a_rewrite(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="It costs ₹7,499."),  # rejected
                AIMessage(content="It costs ₹2,999."),  # compose, the 4th call
            ]
        )
        run(model, "How much is the payments course?")

        assert len(model.calls) == 4


class TestGrounding:
    def test_invented_price_never_reaches_the_user(self):
        """
        The model is scripted to insist on a price the catalogue does not
        contain, twice. The user must end up with the refusal, not the number.
        """
        invented = "Digital Payments Ecosystem costs ₹7,499."
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content=invented),  # first draft
                AIMessage(content=invented),  # ignores the correction
            ]
        )
        state = run(model, "How much is the payments course?")

        assert "7,499" not in state["answer"]
        assert "can't give you those details reliably" in state["answer"]
        assert "price ₹7,499" in state["unsupported_claims"]
        assert "7,499" not in str(state["messages"][-1].content)

    def test_a_corrected_second_draft_is_accepted(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="It costs ₹7,499."),  # rejected
                AIMessage(content="It costs ₹2,999."),  # corrected
            ]
        )
        state = run(model, "How much is the payments course?")

        assert state["answer"] == "It costs ₹2,999."
        assert state["verification_attempts"] == 1

    def test_the_rewrite_instruction_names_the_bad_claim(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="It costs ₹7,499."),
                AIMessage(content="It costs ₹2,999."),
            ]
        )
        run(model, "How much?")

        rewrite_prompt = str(model.calls[-1][-1].content)
        assert "price ₹7,499" in rewrite_prompt
        assert "not published" in rewrite_prompt


class TestProfile:
    def test_extracted_details_are_kept_on_the_state(self):
        model = ScriptedModel(
            [
                AIMessage(
                    content='{"background":"chartered accountant","career_goal":null,'
                    '"current_level":"discovery","audience":"Chartered Accountants",'
                    '"domain_interest":"digital payments","hours_available":5}'
                ),
                AIMessage(content="Here are some options."),
            ]
        )
        state = run(model, "I'm a CA with about 5 hours a week, new to payments.")

        profile = state["profile"]
        assert profile.audience == "Chartered Accountants"
        assert profile.hours_available == 5
        assert profile.current_level.value == "discovery"

    def test_the_profile_is_given_to_the_model(self):
        model = ScriptedModel(
            [
                AIMessage(
                    content='{"background":null,"career_goal":null,"current_level":null,'
                    '"audience":"Chartered Accountants","domain_interest":null,'
                    '"hours_available":null}'
                ),
                AIMessage(content="Options."),
            ]
        )
        run(model, "I'm a CA.")

        system_prompt = str(model.calls[1][0].content)
        assert "Chartered Accountants" in system_prompt

    def test_unparseable_extraction_does_not_break_the_turn(self):
        model = ScriptedModel(
            [
                AIMessage(content="sorry, I can't do that"),  # not JSON
                AIMessage(content="Here are some options."),
            ]
        )
        state = run(model, "I'm a CA.")
        assert state["answer"] == "Here are some options."


class TestToolFailures:
    def test_a_failing_tool_does_not_produce_an_invented_answer(self):
        """
        `get_course` returns a not-found object rather than raising. The model
        is scripted to paper over it with a plausible fee, twice; the refusal
        is what must reach the user.
        """
        model = ScriptedModel(
            [
                no_profile(),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_course",
                            "args": {"slug": "does-not-exist"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="That course costs ₹5,000."),  # invented
                AIMessage(content="That course costs ₹5,000."),  # still invented
            ]
        )
        state = run(model, "Tell me about the quantum payments course.")

        assert "5,000" not in state["answer"]
        assert "can't give you those details reliably" in state["answer"]
        assert state["unsupported_claims"] == ["price ₹5,000"]

    def test_a_tool_that_raises_is_reported_not_hidden(self):
        """
        A broken tool must surface as an error in the results the draft is
        checked against, never as a silent empty result the model can fill in.
        """
        model = ScriptedModel(
            [
                no_profile(),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_courses",
                            # max_hours must be a number; a string blows up the
                            # tool's argument validation.
                            "args": {"max_hours": "as long as it takes"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="I couldn't look that up just now."),
            ]
        )
        state = run(model, "Show me something short.")

        assert "error" in state["tool_results"][0]["results"]
        assert state["answer"] == "I couldn't look that up just now."
