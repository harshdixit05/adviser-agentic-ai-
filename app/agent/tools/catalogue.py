"""
The only path from the agent to course facts.

Each function reads rows and returns a typed object. None of them accept free
text that reaches SQL, none of them return prose, and none of them fill a gap
in the data with a plausible value — an absent field arrives as None and the
agent is required to say the catalogue does not have it.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agent.tools.schemas import (
    ComparisonRow,
    CourseComparison,
    CourseDetail,
    CourseSummary,
    LearningPath,
    ModuleInfo,
    NotFound,
    PathStep,
    PriceQuote,
    Recommendation,
)
from app.db.models import (
    LEVEL_ORDER,
    Course,
    CoursePersona,
    CourseLevel,
    Domain,
    Module,
    Persona,
    PriceBand,
)

MAX_RESULTS = 25


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------


def _price_for(session: Session, course: Course) -> PriceQuote | None:
    """A confirmed per-course price wins; otherwise the level band applies."""
    if course.price_inr is not None:
        return PriceQuote(
            level=course.level,
            amount_min_inr=course.price_inr,
            amount_max_inr=course.price_inr,
            is_indicative=False,
            source="course",
        )

    band = session.get(PriceBand, course.level)
    if band is None:
        return None  # no band published for this level — say nothing

    return PriceQuote(
        level=course.level,
        amount_min_inr=band.amount_min_inr,
        amount_max_inr=band.amount_max_inr,
        is_indicative=band.is_indicative,
        source="band",
    )


def get_pricing(session: Session, level: CourseLevel) -> PriceQuote | NotFound:
    band = session.get(PriceBand, level)
    if band is None:
        return NotFound(
            query=level.value,
            reason="No price band is published for this level.",
            suggestion="Ask the Intellimindz team for a quote.",
        )
    return PriceQuote(
        level=level,
        amount_min_inr=band.amount_min_inr,
        amount_max_inr=band.amount_max_inr,
        is_indicative=band.is_indicative,
        source="band",
    )


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------


def _summary(session: Session, course: Course, module_count: int | None = None) -> CourseSummary:
    if module_count is None:
        module_count = session.scalar(
            select(func.count(Module.id)).where(Module.course_id == course.id)
        )
    return CourseSummary(
        slug=course.slug,
        title=course.title,
        domain=course.domain.name,
        level=course.level,
        duration_hours=course.duration_hours,
        module_count=module_count or 0,
        price=_price_for(session, course),
    )


# --------------------------------------------------------------------------
# search and detail
# --------------------------------------------------------------------------


def search_courses(
    session: Session,
    *,
    domain: str | None = None,
    level: CourseLevel | None = None,
    max_hours: float | None = None,
    audience: str | None = None,
    text: str | None = None,
    limit: int = 10,
) -> list[CourseSummary]:
    """Structured filter over the catalogue. Returns [] when nothing matches."""
    stmt = select(Course).join(Domain).options(selectinload(Course.domain))

    if domain:
        stmt = stmt.where(
            func.lower(Domain.name).contains(domain.lower())
            | func.lower(Domain.slug).contains(domain.lower())
        )
    if level:
        stmt = stmt.where(Course.level == level)
    if max_hours is not None:
        stmt = stmt.where(Course.duration_hours <= Decimal(str(max_hours)))
    if text:
        stmt = stmt.where(func.lower(Course.title).contains(text.lower()))
    if audience:
        stmt = (
            stmt.join(CoursePersona, CoursePersona.course_id == Course.id)
            .join(Persona, Persona.id == CoursePersona.persona_id)
            .where(func.lower(Persona.label).contains(audience.lower()))
        )

    stmt = stmt.order_by(Course.duration_hours).limit(min(limit, MAX_RESULTS))
    courses = session.execute(stmt).scalars().unique().all()
    return [_summary(session, course) for course in courses]


def get_course(session: Session, slug: str) -> CourseDetail | NotFound:
    course = session.execute(
        select(Course)
        .where(Course.slug == slug)
        .options(
            selectinload(Course.domain),
            selectinload(Course.modules),
            selectinload(Course.personas),
        )
    ).scalar_one_or_none()

    if course is None:
        return NotFound(
            query=slug,
            reason="No course with that identifier is in the catalogue.",
            suggestion="Search by title or domain instead.",
        )

    base = _summary(session, course, len(course.modules))
    return CourseDetail(
        **base.model_dump(),
        modules=[
            ModuleInfo(position=m.position, title=m.title, duration_mins=m.duration_mins)
            for m in course.modules
        ],
        audiences=[p.label for p in course.personas],
        status=course.status.value if course.status else None,
        faculty=course.faculty,
        track=course.track,
        source=f"{course.source_sheet}!row{course.source_row}",
    )


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def compare_courses(session: Session, slugs: list[str]) -> CourseComparison | NotFound:
    """
    Returns a field matrix rather than prose, so the agent presents differences
    it was given instead of composing them.
    """
    if len(slugs) < 2:
        return NotFound(query=", ".join(slugs), reason="Comparison needs at least two courses.")

    details: dict[str, CourseDetail] = {}
    for slug in slugs:
        found = get_course(session, slug)
        if isinstance(found, NotFound):
            return found
        details[slug] = found

    def row(field: str, render) -> ComparisonRow:
        return ComparisonRow(field=field, values={s: render(d) for s, d in details.items()})

    def price_text(detail: CourseDetail) -> str | None:
        if detail.price is None:
            return None
        quote = detail.price
        amount = (
            f"₹{quote.amount_min_inr:,.0f}-₹{quote.amount_max_inr:,.0f}"
            if quote.is_range
            else f"₹{quote.amount_min_inr:,.0f}"
        )
        return f"{amount} (indicative)" if quote.is_indicative else amount

    return CourseComparison(
        slugs=slugs,
        rows=[
            row("Title", lambda d: d.title),
            row("Domain", lambda d: d.domain),
            row("Level", lambda d: d.level.value),
            row("Duration", lambda d: f"{d.duration_hours} hrs" if d.duration_hours else None),
            row("Modules", lambda d: str(d.module_count)),
            row("Price", price_text),
            row("Audiences", lambda d: ", ".join(d.audiences) or None),
            row("Status", lambda d: d.status),
        ],
    )


# --------------------------------------------------------------------------
# recommendation
# --------------------------------------------------------------------------


def recommend_courses(
    session: Session,
    *,
    audience: str | None = None,
    domain: str | None = None,
    current_level: CourseLevel | None = None,
    max_hours: float | None = None,
    limit: int = 5,
) -> list[Recommendation]:
    """
    Scored against Intellimindz's own persona tagging first, then level fit and
    time budget. Every reason returned names the catalogue fact behind it, so a
    recommendation can be defended rather than just asserted.
    """
    candidates = search_courses(
        session, domain=domain, max_hours=max_hours, audience=audience, limit=MAX_RESULTS
    )

    recommendations: list[Recommendation] = []
    for summary in candidates:
        score = 0.0
        reasons: list[str] = []

        if audience:
            score += 0.5
            reasons.append(f"Intellimindz maps this course to {audience}")

        if current_level:
            distance = abs(LEVEL_ORDER.index(summary.level) - LEVEL_ORDER.index(current_level))
            if distance == 0:
                score += 0.35
                reasons.append(f"Matches your stated level ({summary.level.value})")
            elif distance == 1:
                score += 0.2
                reasons.append(f"One step from your level, at {summary.level.value}")
        else:
            # Without a stated level, favour the entry rungs.
            if summary.level in (CourseLevel.discovery, CourseLevel.fluency):
                score += 0.15
                reasons.append(f"An entry-level starting point ({summary.level.value})")

        if max_hours and summary.duration_hours is not None:
            score += 0.15
            reasons.append(f"Fits your time budget at {summary.duration_hours} hours")

        if domain:
            reasons.append(f"In the {summary.domain} domain")

        if score > 0:
            recommendations.append(
                Recommendation(course=summary, match_score=min(score, 1.0), reasons=reasons)
            )

    recommendations.sort(key=lambda r: (-r.match_score, r.course.duration_hours or 0))
    return recommendations[:limit]


# --------------------------------------------------------------------------
# learning path
# --------------------------------------------------------------------------


def build_learning_path(
    session: Session,
    *,
    domain: str,
    start_level: CourseLevel | None = None,
    target_level: CourseLevel | None = None,
    one_per_level: bool = True,
) -> LearningPath | NotFound:
    """
    Sequences a domain by the level ladder.

    Intellimindz publishes no prerequisites, so this is explicitly a suggested
    order: `is_prerequisite_enforced` is always False and the agent must not
    describe a step as required.
    """
    courses = search_courses(session, domain=domain, limit=MAX_RESULTS)
    if not courses:
        return NotFound(
            query=domain,
            reason="No courses are catalogued for that domain.",
            suggestion="Ask which domains Intellimindz covers.",
        )

    lower = LEVEL_ORDER.index(start_level) if start_level else 0
    upper = LEVEL_ORDER.index(target_level) if target_level else len(LEVEL_ORDER) - 1
    if lower > upper:
        lower, upper = upper, lower

    in_range = [c for c in courses if lower <= LEVEL_ORDER.index(c.level) <= upper]
    in_range.sort(key=lambda c: (LEVEL_ORDER.index(c.level), c.duration_hours or 0))

    chosen: list[CourseSummary] = []
    seen_levels: set[CourseLevel] = set()
    for course in in_range:
        if one_per_level and course.level in seen_levels:
            continue
        seen_levels.add(course.level)
        chosen.append(course)

    if not chosen:
        return NotFound(query=domain, reason="No courses fall within that level range.")

    steps = [
        PathStep(
            order=index,
            course=course,
            rationale=f"{course.level.value.title()} level, {course.duration_hours or '?'} hours",
        )
        for index, course in enumerate(chosen, start=1)
    ]

    return LearningPath(
        domain=chosen[0].domain,
        steps=steps,
        total_hours=sum((c.duration_hours or Decimal(0) for c in chosen), Decimal(0)),
        total_price_min_inr=sum(
            (c.price.amount_min_inr for c in chosen if c.price), Decimal(0)
        ),
        total_price_max_inr=sum(
            (c.price.amount_max_inr for c in chosen if c.price), Decimal(0)
        ),
        is_prerequisite_enforced=False,
    )
