"""
Persists parsed course records.

The load is idempotent: re-running replaces the catalogue wholesale rather than
accumulating duplicates, because the spreadsheet is the source of truth and a
course removed from it should disappear here too.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    Course,
    CourseLevel,
    CoursePersona,
    CourseRelationship,
    Domain,
    Module,
    Persona,
    PriceBand,
)
from app.ingestion.excel.loader import CourseRecord
from app.ingestion.excel.normalizers import slugify

# Published per level; Advanced is a range, so min and max differ there.
# Marked indicative: the advisor must present these as bands to confirm, never
# as a settled fee.
PRICE_BANDS: list[tuple[CourseLevel, int, int]] = [
    (CourseLevel.discovery, 2999, 2999),
    (CourseLevel.fluency, 5999, 5999),
    (CourseLevel.beginner, 11999, 11999),
    (CourseLevel.intermediate, 19999, 19999),
    (CourseLevel.advanced, 34999, 44999),
]


def _reset_catalogue(session: Session) -> None:
    session.execute(delete(CourseRelationship))
    session.execute(delete(CoursePersona))
    session.execute(delete(Module))
    session.execute(delete(Course))
    session.execute(delete(Persona))
    session.execute(delete(Domain))


def _upsert_price_bands(session: Session) -> None:
    session.execute(delete(PriceBand))
    for level, minimum, maximum in PRICE_BANDS:
        session.add(
            PriceBand(
                level=level,
                amount_min_inr=Decimal(minimum),
                amount_max_inr=Decimal(maximum),
                is_indicative=True,
            )
        )


def write_catalogue(session: Session, records: list[CourseRecord]) -> dict[str, int]:
    _reset_catalogue(session)
    session.flush()
    _upsert_price_bands(session)

    domains: dict[str, Domain] = {}
    personas: dict[str, Persona] = {}
    module_count = 0
    link_count = 0

    for record in records:
        if record.domain not in domains:
            domain = Domain(slug=slugify(record.domain), name=record.domain)
            session.add(domain)
            domains[record.domain] = domain
        session.flush()

        course = Course(
            slug=record.slug,
            title=record.title,
            domain_id=domains[record.domain].id,
            level=record.level,
            duration_hours=(
                Decimal(str(record.duration_hours))
                if record.duration_hours is not None
                else None
            ),
            status=record.status,
            track=record.track,
            faculty=record.faculty,
            price_inr=None,  # no per-course price exists; the level band applies
            source_sheet=record.source_sheet,
            source_row=record.source_row,
        )
        session.add(course)
        session.flush()

        for module in record.modules:
            session.add(
                Module(
                    course_id=course.id,
                    position=module["position"],
                    title=module["title"],
                    duration_mins=module["duration_mins"],
                )
            )
            module_count += 1

        for label in record.personas:
            slug = slugify(label)
            if slug not in personas:
                persona = Persona(slug=slug, label=label)
                session.add(persona)
                session.flush()
                personas[slug] = persona
            session.add(
                CoursePersona(course_id=course.id, persona_id=personas[slug].id)
            )
            link_count += 1

    session.flush()
    derived = _derive_level_sequence(session)

    return {
        "domains": len(domains),
        "courses": len(records),
        "modules": module_count,
        "persona_links": link_count,
        "personas": len(personas),
        "sequence_edges": derived,
    }


def _derive_level_sequence(session: Session) -> int:
    """
    No prerequisites exist in the source, so the only defensible ordering is the
    level ladder within a domain. These edges are written with
    source='derived_from_level' and kind='follows' — never 'prerequisite' — so
    the advisor presents them as a suggested next step rather than a rule.
    """
    order = {level: index for index, level in enumerate(CourseLevel)}
    courses = session.execute(select(Course)).scalars().all()

    by_domain: dict[int, list[Course]] = {}
    for course in courses:
        by_domain.setdefault(course.domain_id, []).append(course)

    edges = 0
    for domain_courses in by_domain.values():
        domain_courses.sort(key=lambda c: (order[c.level], c.title))
        for earlier, later in zip(domain_courses, domain_courses[1:]):
            if order[later.level] <= order[earlier.level]:
                continue  # same level: siblings, not a sequence
            session.add(
                CourseRelationship(
                    from_course=earlier.id,
                    to_course=later.id,
                    kind="follows",
                    source="derived_from_level",
                )
            )
            edges += 1
    return edges
