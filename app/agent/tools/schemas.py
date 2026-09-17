"""
The contracts the agent is allowed to speak through.

Every field the advisor can state about a course is declared here and filled
from a database row. Two conventions carry the anti-hallucination guarantee:

  * Optional fields are Optional because the catalogue genuinely may not know
    them — never because the caller may omit them. `None` means "Intellimindz
    has not published this", and the agent must say so rather than fill it in.

  * Anything derived rather than stated carries a flag saying so, so the agent
    can phrase a suggestion as a suggestion.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.db.models import CourseLevel


class PriceQuote(BaseModel):
    """
    Pricing is published per level and Advanced is a range, so a quote is a
    band. `is_indicative` is true for every band Intellimindz has given so far;
    the agent must present those as "to be confirmed", never as a final fee.
    """

    level: CourseLevel
    amount_min_inr: Decimal
    amount_max_inr: Decimal
    is_indicative: bool
    source: str = Field(description="'band' (per level) or 'course' (confirmed override)")
    currency: str = "INR"

    @property
    def is_range(self) -> bool:
        return self.amount_min_inr != self.amount_max_inr


class ModuleInfo(BaseModel):
    position: int
    title: str
    duration_mins: int | None = None


class CourseSummary(BaseModel):
    slug: str
    title: str
    domain: str
    level: CourseLevel
    duration_hours: Decimal | None = None
    module_count: int
    price: PriceQuote | None = None


class CourseDetail(CourseSummary):
    modules: list[ModuleInfo] = []
    audiences: list[str] = []
    status: str | None = Field(
        default=None,
        description="Production status. None means the sheet did not state one.",
    )
    faculty: str | None = None
    track: str | None = None
    source: str = Field(description="Sheet and row this record came from")


class Recommendation(BaseModel):
    course: CourseSummary
    match_score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(
        description="Why this matched, each traceable to catalogue data"
    )


class PathStep(BaseModel):
    order: int
    course: CourseSummary
    rationale: str


class LearningPath(BaseModel):
    domain: str
    steps: list[PathStep]
    total_hours: Decimal
    total_price_min_inr: Decimal
    total_price_max_inr: Decimal
    is_prerequisite_enforced: bool = Field(
        default=False,
        description=(
            "False for every path today: Intellimindz has published no "
            "prerequisites, so the order is derived from the level ladder and "
            "must be described as a suggested sequence, not a requirement."
        ),
    )


class ComparisonRow(BaseModel):
    field: str
    values: dict[str, str | None] = Field(
        description="Course slug -> value. None renders as 'not published'."
    )


class CourseComparison(BaseModel):
    slugs: list[str]
    rows: list[ComparisonRow]


class NotFound(BaseModel):
    """
    Returned instead of an empty result so the agent cannot mistake "nothing
    matched" for "I should improvise something".
    """

    query: str
    reason: str
    suggestion: str | None = None
