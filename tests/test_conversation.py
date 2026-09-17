"""
Multi-turn behaviour.

Two things have to hold across turns, and they pull in opposite directions:
what the learner told us must persist, and what the tools returned must not.
The second is the one that protects the learner — a fee looked up two
questions ago must not vouch for a number in today's answer.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from app.agent.conversation import Advisor
from app.db.models import Course
from app.db.session import SessionFactory
from tests.test_graph import ScriptedModel, no_profile, search_call


@pytest.fixture(scope="module", autouse=True)
def require_catalogue():
    with SessionFactory() as session:
        if session.scalar(select(Course).limit(1)) is None:
            pytest.skip("catalogue not loaded — run python -m scripts.ingest_excel")


class TestWhatPersists:
    def test_the_profile_survives_a_later_unrelated_question(self):
        model = ScriptedModel(
            [
                AIMessage(
                    content='{"background":null,"career_goal":null,"current_level":null,'
                    '"audience":"Chartered Accountants","domain_interest":null,'
                    '"hours_available":null}'
                ),
                AIMessage(content="Noted."),
                # Second turn reveals only the hours.
                AIMessage(
                    content='{"background":null,"career_goal":null,"current_level":null,'
                    '"audience":null,"domain_interest":null,"hours_available":4}'
                ),
                AIMessage(content="Understood."),
            ]
        )
        advisor = Advisor(model)
        advisor.ask("I'm a CA.")
        advisor.ask("I have about 4 hours a week.")

        assert advisor.profile.audience == "Chartered Accountants"
        assert advisor.profile.hours_available == 4

    def test_the_transcript_grows(self):
        model = ScriptedModel(
            [no_profile(), AIMessage(content="Hello."), no_profile(), AIMessage(content="Again.")]
        )
        advisor = Advisor(model)
        advisor.ask("Hi")
        first = len(advisor.history)
        advisor.ask("Hi again")

        assert len(advisor.history) > first
        assert isinstance(advisor.history[0], HumanMessage)


class TestWhatDoesNot:
    def test_grounding_does_not_carry_over_between_turns(self):
        """
        Turn one looks up a real price. Turn two runs no tool and quotes that
        same number from the model's memory of the conversation. Because the
        evidence was wiped, the figure is unsupported and must be caught — the
        gate grades each answer against the tools that ran *for it*.
        """
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="Digital Payments Ecosystem runs 3 hours."),
                # Turn two: no tool call, but a figure anyway.
                no_profile(),
                AIMessage(content="It costs ₹2,999."),
                AIMessage(content="It costs ₹2,999."),  # rewrite insists
            ]
        )
        advisor = Advisor(model)
        first = advisor.ask("What payments courses do you have?")
        assert first.answer == "Digital Payments Ecosystem runs 3 hours."
        assert first.tool_results

        second = advisor.ask("Remind me of the price.")
        assert second.tool_results == [], "turn two ran no tools"
        assert "2,999" not in second.answer
        assert second.was_refused

    def test_a_rejected_turn_does_not_poison_the_next_one(self):
        model = ScriptedModel(
            [
                no_profile(),
                AIMessage(content="It costs ₹9,999."),  # ungrounded
                AIMessage(content="It costs ₹9,999."),  # refused
                no_profile(),
                AIMessage(content="Happy to help with something else."),
            ]
        )
        advisor = Advisor(model)
        refused = advisor.ask("How much?")
        assert refused.was_refused

        clean = advisor.ask("Never mind, thanks.")
        assert clean.answer == "Happy to help with something else."
        assert clean.unsupported_claims == []
        assert not clean.was_refused


class TestTurnReporting:
    def test_a_turn_names_the_tools_behind_it(self):
        model = ScriptedModel(
            [
                no_profile(),
                search_call(domain="digital payments", level="discovery"),
                AIMessage(content="Digital Payments Ecosystem runs 3 hours."),
            ]
        )
        turn = Advisor(model).ask("What payments courses do you have?")
        assert turn.tools_used == ["search_courses"]

    def test_a_chat_turn_reports_no_tools_rather_than_failing(self):
        model = ScriptedModel([no_profile(), AIMessage(content="Hello — what are you after?")])
        turn = Advisor(model).ask("Hi")

        assert turn.tools_used == []
        assert turn.answer == "Hello — what are you after?"
        assert not turn.was_refused

    def test_reset_clears_both_history_and_profile(self):
        model = ScriptedModel(
            [
                AIMessage(
                    content='{"background":null,"career_goal":null,"current_level":null,'
                    '"audience":"NGOs","domain_interest":null,"hours_available":null}'
                ),
                AIMessage(content="Noted."),
            ]
        )
        advisor = Advisor(model)
        advisor.ask("I work at an NGO.")
        advisor.reset()

        assert advisor.history == []
        assert advisor.profile.is_empty()
