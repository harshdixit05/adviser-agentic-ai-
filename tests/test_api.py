"""
The HTTP surface.

No model is called: the scripted stub from the graph tests is injected in
place of Gemini. What is being tested here is not the advice — that is covered
elsewhere — but who is allowed to ask, what a conversation costs, and whether
a failure leaks anything it shouldn't.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.agent.conversation import Advisor
from app.api import main as api
from app.api.rate_limit import RateLimiter
from app.api.sessions import SessionStore
from app.db.models import Course
from app.db.session import SessionFactory
from tests.test_graph import ScriptedModel, no_profile, search_call


@pytest.fixture(scope="module", autouse=True)
def require_catalogue():
    with SessionFactory() as session:
        if session.scalar(select(Course).limit(1)) is None:
            pytest.skip("catalogue not loaded — run python -m scripts.ingest_excel")


@pytest.fixture
def fresh_state(monkeypatch):
    """Each test gets its own store and limiters, with the real settings."""
    monkeypatch.setattr(api, "sessions", SessionStore(ttl_minutes=60, max_sessions=100))
    monkeypatch.setattr(api, "per_session", RateLimiter(60))
    monkeypatch.setattr(api, "per_client", RateLimiter(120))


@pytest.fixture
def client(fresh_state):
    # Not used as a context manager: that would run the lifespan, which tries
    # to build a real Gemini client. The model is injected per test instead.
    return TestClient(api.app)


def chatty(*replies: str) -> ScriptedModel:
    """A model that answers without calling tools."""
    queue = []
    for reply in replies:
        queue += [no_profile(), AIMessage(content=reply)]
    return ScriptedModel(queue)


def install(monkeypatch, model) -> None:
    monkeypatch.setattr(api, "_model", model)


class TestWithoutAModel:
    def test_chat_is_unavailable_rather_than_crashing(self, client, monkeypatch):
        install(monkeypatch, None)
        response = client.post("/api/chat", json={"message": "Hi"})

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]

    def test_health_says_so_instead_of_claiming_ok(self, client, monkeypatch):
        install(monkeypatch, None)
        body = client.get("/health").json()

        assert body["status"] == "degraded"
        assert body["model_configured"] is False
        assert body["catalogue_courses"] > 0, "the catalogue is loaded even if the model isn't"


class TestChat:
    def test_an_answer_comes_back_with_a_session_id(self, client, monkeypatch):
        install(monkeypatch, chatty("What are you hoping to learn?"))
        body = client.post("/api/chat", json={"message": "Hi"}).json()

        assert body["answer"] == "What are you hoping to learn?"
        assert body["session_id"]
        assert body["withheld"] is False

    def test_the_lookups_behind_an_answer_are_named(self, client, monkeypatch):
        install(
            monkeypatch,
            ScriptedModel(
                [
                    no_profile(),
                    search_call(domain="digital payments", level="discovery"),
                    AIMessage(content="Digital Payments Ecosystem runs 3 hours."),
                ]
            ),
        )
        body = client.post("/api/chat", json={"message": "payments courses?"}).json()

        assert body["sources"] == ["search_courses"]

    def test_a_blocked_answer_is_flagged_not_dressed_up(self, client, monkeypatch):
        install(
            monkeypatch,
            ScriptedModel(
                [
                    no_profile(),
                    AIMessage(content="It costs ₹8,888."),  # nothing looked up
                    AIMessage(content="It costs ₹8,888."),  # insists
                ]
            ),
        )
        body = client.post("/api/chat", json={"message": "How much?"}).json()

        assert body["withheld"] is True
        assert "8,888" not in body["answer"]

    def test_a_conversation_continues_when_its_id_comes_back(self, client, monkeypatch):
        model = ScriptedModel(
            [
                AIMessage(
                    content='{"background":null,"career_goal":null,"current_level":null,'
                    '"audience":"Chartered Accountants","domain_interest":null,'
                    '"hours_available":null}'
                ),
                AIMessage(content="Noted."),
                no_profile(),
                AIMessage(content="Still noted."),
            ]
        )
        install(monkeypatch, model)

        first = client.post("/api/chat", json={"message": "I'm a CA."}).json()
        second = client.post(
            "/api/chat", json={"message": "What next?", "session_id": first["session_id"]}
        ).json()

        assert second["session_id"] == first["session_id"]
        _, advisor = api.sessions.get_or_create(first["session_id"], lambda: None)
        assert advisor.profile.audience == "Chartered Accountants"

    def test_an_unknown_id_starts_a_new_conversation_rather_than_failing(
        self, client, monkeypatch
    ):
        install(monkeypatch, chatty("Hello again."))
        body = client.post(
            "/api/chat", json={"message": "Hi", "session_id": "not-a-real-session"}
        ).json()

        assert body["session_id"] != "not-a-real-session"
        assert body["answer"] == "Hello again."

    def test_ending_a_conversation_forgets_it(self, client, monkeypatch):
        install(monkeypatch, chatty("Hi."))
        session_id = client.post("/api/chat", json={"message": "Hi"}).json()["session_id"]

        assert client.post(f"/api/chat/{session_id}/end").status_code == 204
        assert len(api.sessions) == 0


class TestInputLimits:
    def test_an_empty_message_is_rejected(self, client, monkeypatch):
        install(monkeypatch, chatty("unused"))
        assert client.post("/api/chat", json={"message": ""}).status_code == 422

    def test_an_oversized_message_is_rejected_before_it_costs_anything(
        self, client, monkeypatch
    ):
        model = chatty("unused")
        install(monkeypatch, model)
        response = client.post("/api/chat", json={"message": "x" * 5000})

        assert response.status_code == 422
        assert model.calls == [], "the model must not be called for a rejected request"

    def test_an_oversized_session_id_is_rejected(self, client, monkeypatch):
        install(monkeypatch, chatty("unused"))
        response = client.post("/api/chat", json={"message": "Hi", "session_id": "x" * 500})
        assert response.status_code == 422


class TestRateLimits:
    def test_a_conversation_has_a_ceiling(self, client, monkeypatch):
        monkeypatch.setattr(api, "per_session", RateLimiter(2))
        install(monkeypatch, chatty("one", "two", "three"))

        session_id = client.post("/api/chat", json={"message": "1"}).json()["session_id"]
        assert (
            client.post("/api/chat", json={"message": "2", "session_id": session_id}).status_code
            == 200
        )
        blocked = client.post("/api/chat", json={"message": "3", "session_id": session_id})

        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0

    def test_opening_fresh_conversations_does_not_get_around_it(self, client, monkeypatch):
        """The per-conversation limit alone would be defeated by a new id each time."""
        monkeypatch.setattr(api, "per_client", RateLimiter(2))
        install(monkeypatch, chatty("one", "two", "three"))

        client.post("/api/chat", json={"message": "1"})
        client.post("/api/chat", json={"message": "2"})
        blocked = client.post("/api/chat", json={"message": "3"})

        assert blocked.status_code == 429


class TestFailureHandling:
    def test_an_agent_failure_does_not_leak_its_detail(self, client, monkeypatch):
        class Exploding(Advisor):
            def __init__(self):  # no graph needed
                pass

            def ask(self, text):
                raise RuntimeError("API key AIzaSy-SECRET rejected by upstream")

        monkeypatch.setattr(api, "sessions", SessionStore(60, 100))
        monkeypatch.setattr(api, "_model", object())
        monkeypatch.setattr(api.sessions, "get_or_create", lambda _id, _make: ("s1", Exploding()))

        response = client.post("/api/chat", json={"message": "Hi"})

        assert response.status_code == 502
        assert "SECRET" not in response.text
        assert "AIzaSy" not in response.text
        assert response.json()["detail"] == "I couldn't answer that just now. Please try again."


class TestCORS:
    def test_an_unlisted_origin_gets_no_allow_header(self, client, monkeypatch):
        install(monkeypatch, chatty("Hi."))
        response = client.post(
            "/api/chat",
            json={"message": "Hi"},
            headers={"Origin": "https://not-intellimindz.example"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}

    def test_a_listed_origin_is_allowed(self, client, monkeypatch):
        install(monkeypatch, chatty("Hi."))
        response = client.post(
            "/api/chat", json={"message": "Hi"}, headers={"Origin": "http://localhost:3000"}
        )
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
