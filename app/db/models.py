"""
Authoritative structured catalogue.

Everything the advisor states as fact — titles, levels, hours, modules, prices,
audiences — is read from here. Qdrant holds explanatory prose only, so a wrong
answer traces back to a row in this schema rather than to model memory.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CourseLevel(str, enum.Enum):
    """
    Intellimindz's ladder. Declared in teaching order — Fluency sits before
    Beginner, which is unusual but confirmed, and path building depends on it.
    """

    discovery = "discovery"
    fluency = "fluency"
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"

    @property
    def rank(self) -> int:
        return LEVEL_ORDER.index(self)


LEVEL_ORDER: list[CourseLevel] = [
    CourseLevel.discovery,
    CourseLevel.fluency,
    CourseLevel.beginner,
    CourseLevel.intermediate,
    CourseLevel.advanced,
]


class ContentStatus(str, enum.Enum):
    proposed = "proposed"
    content_ready = "content_ready"
    ppt_done = "ppt_done"
    recorded = "recorded"
    published = "published"


level_enum = Enum(
    CourseLevel,
    name="course_level",
    values_callable=lambda e: [m.value for m in e],
)
status_enum = Enum(
    ContentStatus,
    name="content_status",
    values_callable=lambda e: [m.value for m in e],
)


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    courses: Mapped[list[Course]] = relationship(back_populates="domain")


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), nullable=False)
    level: Mapped[CourseLevel] = mapped_column(level_enum, nullable=False)
    duration_hours: Mapped[float | None] = mapped_column(Numeric(5, 1))
    status: Mapped[ContentStatus | None] = mapped_column(status_enum)
    track: Mapped[str | None] = mapped_column(String(40))
    faculty: Mapped[str | None] = mapped_column(String(160))

    # Per-course override. NULL means the level's band in price_bands applies;
    # a value here means someone confirmed a price for this specific course.
    price_inr: Mapped[float | None] = mapped_column(Numeric(10, 2))

    # Provenance, so any disputed answer can be traced to a spreadsheet cell.
    source_sheet: Mapped[str] = mapped_column(String(80), nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    domain: Mapped[Domain] = relationship(back_populates="courses")
    modules: Mapped[list[Module]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="Module.position"
    )
    personas: Mapped[list[Persona]] = relationship(
        secondary="course_personas", back_populates="courses"
    )


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = (UniqueConstraint("course_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    duration_mins: Mapped[int | None] = mapped_column(Integer)

    course: Mapped[Course] = relationship(back_populates="modules")


class Persona(Base):
    """
    The audiences each course is mapped to in the source sheet. This is the
    backbone of recommendation: matching a learner to a persona is grounded in
    Intellimindz's own tagging rather than inferred by the model.
    """

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)

    courses: Mapped[list[Course]] = relationship(
        secondary="course_personas", back_populates="personas"
    )


class CoursePersona(Base):
    __tablename__ = "course_personas"

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), primary_key=True
    )


class PriceBand(Base):
    """
    Pricing is published per level, and Advanced is a range. Storing min and max
    separately means the advisor can never collapse a band into a single figure
    it was not given.
    """

    __tablename__ = "price_bands"

    level: Mapped[CourseLevel] = mapped_column(level_enum, primary_key=True)
    amount_min_inr: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    amount_max_inr: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    is_indicative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[dt.date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )


class LearningOutcome(Base):
    """Empty until authored — the source sheet has modules, not outcomes."""

    __tablename__ = "learning_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)


class CourseRelationship(Base):
    """
    `source` separates a prerequisite somebody authored from a sequence derived
    from level ordering. The advisor phrases the two differently: a derived edge
    is a suggested next step, never a requirement.
    """

    __tablename__ = "course_relationships"

    from_course: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    to_course: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
