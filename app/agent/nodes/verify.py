"""
The grounding gate.

A prompt asking a model not to invent prices is a hope. This node checks the
drafted answer against the rows the tools actually returned this turn, and
rejects anything it cannot find. It is ordinary Python: no model is consulted
about whether the model was truthful.

What gets checked are the claims that cost someone money or time if wrong —
rupee amounts, hour figures and course titles. Prose is left alone; the point
is not to police wording but to stop a number appearing that nobody supplied.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

# ₹34,999 / Rs 2999 / INR 11,999
_MONEY = re.compile(r"(?:₹|Rs\.?\s*|INR\s*)\s*([\d,]+(?:\.\d+)?)", re.I)
# "3 hours", "38 hrs", "4.2 hour"
_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", re.I)

MAX_ATTEMPTS = 2


def _walk(value: Any):
    """Yield every scalar in an arbitrarily nested tool result."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def _numbers_in(results: list[dict]) -> set[Decimal]:
    found: set[Decimal] = set()
    for value in _walk(results):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float, Decimal)):
            found.add(Decimal(str(value)))
        elif isinstance(value, str):
            for token in re.findall(r"\d+(?:\.\d+)?", value):
                found.add(Decimal(token))
    return found


def _titles_in(results: list[dict]) -> set[str]:
    titles: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"title", "name"} and isinstance(value, str):
                    titles.add(value.lower())
                collect(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                collect(item)

    collect(results)
    return titles


def _normalise(amount: str) -> Decimal:
    return Decimal(amount.replace(",", ""))


def find_unsupported_claims(draft: str, tool_results: list[dict]) -> list[str]:
    """
    Returns the claims in `draft` that no tool result backs up.

    With no tool results at all, any figure is unsupported — an answer carrying
    numbers has to have come from somewhere.
    """
    supported_numbers = _numbers_in(tool_results)
    supported_titles = _titles_in(tool_results)
    unsupported: list[str] = []

    for match in _MONEY.finditer(draft):
        amount = _normalise(match.group(1))
        if amount not in supported_numbers:
            unsupported.append(f"price ₹{match.group(1)}")

    for match in _HOURS.finditer(draft):
        hours = Decimal(match.group(1))
        if hours not in supported_numbers:
            unsupported.append(f"duration {match.group(1)} hours")

    # Title-case runs of three or more words look like a course name being
    # asserted. Checked only when the tools returned titles to compare against.
    if supported_titles:
        for candidate in re.findall(r"\b(?:[A-Z][\w&-]*\s+){2,}[A-Z][\w&-]*\b", draft):
            phrase = candidate.strip().lower()
            if len(phrase) < 12:
                continue
            if not any(phrase in title or title in phrase for title in supported_titles):
                unsupported.append(f"course title '{candidate.strip()}'")

    # De-duplicate, keeping order.
    seen: set[str] = set()
    ordered: list[str] = []
    for claim in unsupported:
        if claim not in seen:
            seen.add(claim)
            ordered.append(claim)
    return ordered


def verify_node(state: dict) -> dict:
    """
    Accepts the draft, or sends it back once with the offending claims named.

    On a second failure the draft is refused outright rather than published
    with a warning: a wrong fee quoted to a prospective learner is worse than
    an answer that admits it cannot be given.
    """
    draft = state.get("draft", "")
    results = state.get("tool_results", [])
    attempts = state.get("verification_attempts", 0)

    unsupported = find_unsupported_claims(draft, results)

    if not unsupported:
        return {"answer": draft, "unsupported_claims": [], "verification_attempts": attempts}

    if attempts + 1 < MAX_ATTEMPTS:
        return {
            "unsupported_claims": unsupported,
            "verification_attempts": attempts + 1,
        }

    return {
        "answer": (
            "I can't give you those details reliably — I only state course "
            "information that comes from Intellimindz's catalogue, and I "
            "couldn't confirm all of it just now. The team can give you exact "
            "figures at hello@intellimindz.in."
        ),
        "unsupported_claims": unsupported,
        "verification_attempts": attempts + 1,
    }


def route_after_verify(state: dict) -> str:
    """Retry composing while a draft is still salvageable, else finish."""
    if state.get("answer"):
        return "done"
    return "retry"
