"""
Tests for the grounding gate.

These are the tests that matter most in the project: they assert that a drafted
answer cannot carry a price, duration or course name the catalogue did not
supply. Each case is written as the failure it prevents.
"""

from app.agent.nodes.verify import MAX_ATTEMPTS, find_unsupported_claims, verify_node

# What a tool call actually returns, trimmed to the fields verification reads.
TOOL_RESULTS = [
    {
        "tool": "search_courses",
        "results": [
            {
                "slug": "digital-payments-digital-payments-ecosystem",
                "title": "Digital Payments Ecosystem",
                "level": "discovery",
                "duration_hours": "3.0",
                "price": {"amount_min_inr": "2999", "amount_max_inr": "2999"},
            },
            {
                "slug": "digital-payments-payments-platform-architecture",
                "title": "Payments Platform Architecture",
                "level": "advanced",
                "duration_hours": "38.0",
                "price": {"amount_min_inr": "34999", "amount_max_inr": "44999"},
            },
        ],
    }
]


class TestAcceptsGroundedAnswers:
    def test_price_present_in_results(self):
        draft = "Digital Payments Ecosystem is ₹2,999 and runs 3 hours."
        assert find_unsupported_claims(draft, TOOL_RESULTS) == []

    def test_both_ends_of_a_range(self):
        draft = "Advanced courses run ₹34,999–₹44,999."
        assert find_unsupported_claims(draft, TOOL_RESULTS) == []

    def test_prose_without_numbers_is_left_alone(self):
        draft = "That course suits someone starting out in payments."
        assert find_unsupported_claims(draft, TOOL_RESULTS) == []

    def test_accepts_alternate_currency_spellings(self):
        for draft in ("Rs 2999", "INR 2,999", "₹2999"):
            assert find_unsupported_claims(draft, TOOL_RESULTS) == []


class TestCatchesInventedFacts:
    def test_a_price_nobody_supplied(self):
        # The classic failure: a number that looks right and is not.
        draft = "Digital Payments Ecosystem is ₹3,499."
        claims = find_unsupported_claims(draft, TOOL_RESULTS)
        assert claims == ["price ₹3,499"]

    def test_a_midpoint_invented_from_a_range(self):
        # Collapsing ₹34,999–₹44,999 into "about ₹39,999" quotes a fee that
        # does not exist.
        draft = "It's around ₹39,999."
        assert find_unsupported_claims(draft, TOOL_RESULTS) == ["price ₹39,999"]

    def test_a_duration_nobody_supplied(self):
        draft = "Digital Payments Ecosystem takes 6 hours."
        assert find_unsupported_claims(draft, TOOL_RESULTS) == ["duration 6 hours"]

    def test_a_course_that_does_not_exist(self):
        draft = "You should take Advanced Quantum Payments Engineering first."
        claims = find_unsupported_claims(draft, TOOL_RESULTS)
        assert any("course title" in claim for claim in claims)

    def test_several_inventions_are_all_reported(self):
        draft = "Blockchain Mastery Programme costs ₹9,999 and runs 11 hours."
        claims = find_unsupported_claims(draft, TOOL_RESULTS)
        assert len(claims) >= 3

    def test_numbers_are_unsupported_when_no_tool_ran(self):
        # An answer with figures must have come from somewhere.
        assert find_unsupported_claims("It costs ₹2,999.", []) == ["price ₹2,999"]


class TestVerifyNode:
    def test_grounded_draft_is_published(self):
        state = {"draft": "It runs 3 hours.", "tool_results": TOOL_RESULTS}
        result = verify_node(state)
        assert result["answer"] == "It runs 3 hours."
        assert result["unsupported_claims"] == []

    def test_first_failure_asks_for_a_rewrite(self):
        state = {"draft": "It costs ₹7,777.", "tool_results": TOOL_RESULTS}
        result = verify_node(state)
        assert "answer" not in result, "a failing draft must not be published"
        assert result["unsupported_claims"] == ["price ₹7,777"]
        assert result["verification_attempts"] == 1

    def test_second_failure_refuses_rather_than_publishing(self):
        state = {
            "draft": "It costs ₹7,777.",
            "tool_results": TOOL_RESULTS,
            "verification_attempts": MAX_ATTEMPTS - 1,
        }
        result = verify_node(state)
        assert "₹7,777" not in result["answer"]
        assert "can't give you those details reliably" in result["answer"]
        assert "hello@intellimindz.in" in result["answer"]

    def test_the_refusal_names_what_failed_for_the_logs(self):
        state = {
            "draft": "It costs ₹7,777.",
            "tool_results": TOOL_RESULTS,
            "verification_attempts": MAX_ATTEMPTS - 1,
        }
        assert verify_node(state)["unsupported_claims"] == ["price ₹7,777"]
