"""
The catalogue tools, exposed to the model.

Each wrapper opens its own session, calls the plain function in
`catalogue.py`, and returns JSON-safe data. Keeping the wrappers this thin
means the logic stays testable without LangChain, and the model never receives
anything the typed tool did not return.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel

from app.agent.tools import catalogue
from app.db.models import CourseLevel
from app.db.session import SessionFactory


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(item) for item in value]
    return value


def _level(value: str | None) -> CourseLevel | None:
    if not value:
        return None
    try:
        return CourseLevel(value.lower().strip())
    except ValueError:
        return None


@tool
def search_courses(
    domain: str | None = None,
    level: str | None = None,
    max_hours: float | None = None,
    audience: str | None = None,
    text: str | None = None,
) -> list[dict]:
    """Find Intellimindz courses by domain, level, duration, audience or title.

    Levels are discovery, fluency, beginner, intermediate, advanced.
    Returns an empty list when nothing matches — do not invent alternatives.
    """
    with SessionFactory() as session:
        return _dump(
            catalogue.search_courses(
                session,
                domain=domain,
                level=_level(level),
                max_hours=max_hours,
                audience=audience,
                text=text,
            )
        )


@tool
def get_course(slug: str) -> dict:
    """Full detail for one course: modules, audiences, price, status, source.

    Returns a not-found object if the slug is unknown.
    """
    with SessionFactory() as session:
        return _dump(catalogue.get_course(session, slug))


@tool
def compare_courses(slugs: list[str]) -> dict:
    """Compare two or more courses field by field.

    Returns a matrix of values; a null means Intellimindz has not published
    that field, which must be reported as such rather than filled in.
    """
    with SessionFactory() as session:
        return _dump(catalogue.compare_courses(session, slugs))


@tool
def recommend_courses(
    audience: str | None = None,
    domain: str | None = None,
    current_level: str | None = None,
    max_hours: float | None = None,
) -> list[dict]:
    """Recommend courses for a learner, with the reason behind each match.

    `audience` should be one of Intellimindz's persona tags, such as
    'Chartered Accountants' or 'Banking Professionals'.
    """
    with SessionFactory() as session:
        return _dump(
            catalogue.recommend_courses(
                session,
                audience=audience,
                domain=domain,
                current_level=_level(current_level),
                max_hours=max_hours,
            )
        )


@tool
def build_learning_path(
    domain: str,
    start_level: str | None = None,
    target_level: str | None = None,
) -> dict:
    """Build a suggested course sequence for a domain.

    Intellimindz publishes no prerequisites, so the order follows the level
    ladder and must be described as a suggested sequence, never a requirement.
    """
    with SessionFactory() as session:
        return _dump(
            catalogue.build_learning_path(
                session,
                domain=domain,
                start_level=_level(start_level),
                target_level=_level(target_level),
            )
        )


@tool
def get_pricing(level: str) -> dict:
    """Published price band for a level.

    Prices are indicative bands. Advanced is a range and must be quoted as a
    range, never as a single figure or a midpoint.
    """
    parsed = _level(level)
    if parsed is None:
        return {"query": level, "reason": f"{level!r} is not one of the five levels."}
    with SessionFactory() as session:
        return _dump(catalogue.get_pricing(session, parsed))


ALL_TOOLS = [
    search_courses,
    get_course,
    compare_courses,
    recommend_courses,
    build_learning_path,
    get_pricing,
]

TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}
