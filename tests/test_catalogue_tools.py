"""
These run against the loaded catalogue.

Most assert ordinary behaviour. The ones under TestCannotInvent are the point of
the whole design: they check that the tools report absence rather than filling
it, because that is the only thing standing between the agent and a confidently
wrong answer about somebody's money.
"""

import pytest
from sqlalchemy import select

from app.agent.tools import catalogue as tools
from app.agent.tools.schemas import CourseDetail, LearningPath, NotFound
from app.db.models import Course, CourseLevel
from app.db.session import SessionFactory


@pytest.fixture(scope="module")
def session():
    db = SessionFactory()
    if db.scalar(select(Course).limit(1)) is None:
        pytest.skip("catalogue not loaded — run python -m scripts.ingest_excel")
    yield db
    db.close()


@pytest.fixture(scope="module")
def any_slug(session):
    return session.scalar(select(Course.slug).limit(1))


class TestSearch:
    def test_filters_by_domain(self, session):
        results = tools.search_courses(session, domain="digital payments", limit=20)
        assert results
        assert all(r.domain == "Digital Payments" for r in results)

    def test_filters_by_level(self, session):
        results = tools.search_courses(session, level=CourseLevel.discovery, limit=20)
        assert all(r.level is CourseLevel.discovery for r in results)

    def test_respects_a_time_budget(self, session):
        results = tools.search_courses(session, max_hours=4, limit=20)
        assert results
        assert all(r.duration_hours <= 4 for r in results)

    def test_filters_by_audience(self, session):
        results = tools.search_courses(session, audience="Chartered Accountants", limit=5)
        assert results

    def test_no_match_returns_empty_not_an_invention(self, session):
        assert tools.search_courses(session, domain="underwater basket weaving") == []

    def test_result_count_is_capped(self, session):
        assert len(tools.search_courses(session, limit=999)) <= tools.MAX_RESULTS


class TestCourseDetail:
    def test_returns_modules_and_audiences(self, session, any_slug):
        detail = tools.get_course(session, any_slug)
        assert isinstance(detail, CourseDetail)
        assert detail.modules and detail.audiences
        assert [m.position for m in detail.modules] == sorted(m.position for m in detail.modules)

    def test_carries_provenance_back_to_the_spreadsheet(self, session, any_slug):
        detail = tools.get_course(session, any_slug)
        assert "!row" in detail.source

    def test_unknown_slug_is_reported_not_improvised(self, session):
        result = tools.get_course(session, "course-that-does-not-exist")
        assert isinstance(result, NotFound)
        assert result.suggestion


class TestPricing:
    def test_every_level_has_a_band(self, session):
        for level in CourseLevel:
            quote = tools.get_pricing(session, level)
            assert not isinstance(quote, NotFound)
            assert quote.amount_min_inr > 0

    def test_advanced_stays_a_range(self, session):
        quote = tools.get_pricing(session, CourseLevel.advanced)
        assert quote.is_range, "collapsing the Advanced band would quote a wrong fee"
        assert quote.amount_min_inr == 34999
        assert quote.amount_max_inr == 44999

    def test_single_price_levels_are_not_ranges(self, session):
        assert not tools.get_pricing(session, CourseLevel.discovery).is_range

    def test_all_published_prices_are_flagged_indicative(self, session):
        # Nothing supplied so far is a confirmed fee.
        assert all(tools.get_pricing(session, level).is_indicative for level in CourseLevel)

    def test_course_price_comes_from_the_level_band(self, session, any_slug):
        detail = tools.get_course(session, any_slug)
        assert detail.price.source == "band"


class TestComparison:
    def test_returns_a_matrix_not_prose(self, session):
        slugs = [c.slug for c in tools.search_courses(session, limit=2)]
        comparison = tools.compare_courses(session, slugs)
        fields = {row.field for row in comparison.rows}
        assert {"Title", "Level", "Duration", "Price"} <= fields

    def test_needs_two_courses(self, session, any_slug):
        assert isinstance(tools.compare_courses(session, [any_slug]), NotFound)

    def test_one_unknown_slug_fails_the_whole_comparison(self, session, any_slug):
        result = tools.compare_courses(session, [any_slug, "nope"])
        assert isinstance(result, NotFound)


class TestRecommendation:
    def test_every_recommendation_states_its_reasons(self, session):
        results = tools.recommend_courses(session, audience="Chartered Accountants", limit=5)
        assert results
        assert all(r.reasons for r in results)

    def test_reasons_cite_the_catalogue_not_the_model(self, session):
        results = tools.recommend_courses(session, audience="Chartered Accountants", limit=3)
        assert any("Intellimindz maps this course" in reason for reason in results[0].reasons)

    def test_level_match_scores_above_a_distant_level(self, session):
        near = tools.recommend_courses(
            session, domain="digital payments", current_level=CourseLevel.discovery, limit=10
        )
        by_level = {r.course.level: r.match_score for r in near}
        if CourseLevel.discovery in by_level and CourseLevel.advanced in by_level:
            assert by_level[CourseLevel.discovery] > by_level[CourseLevel.advanced]

    def test_respects_the_time_budget(self, session):
        results = tools.recommend_courses(session, max_hours=5, limit=10)
        assert all(r.course.duration_hours <= 5 for r in results)


class TestLearningPath:
    def test_orders_by_the_level_ladder(self, session):
        from app.db.models import LEVEL_ORDER

        path = tools.build_learning_path(session, domain="digital payments")
        assert isinstance(path, LearningPath)
        ranks = [LEVEL_ORDER.index(step.course.level) for step in path.steps]
        assert ranks == sorted(ranks)

    def test_fluency_precedes_beginner(self, session):
        path = tools.build_learning_path(session, domain="digital payments")
        levels = [step.course.level for step in path.steps]
        if CourseLevel.fluency in levels and CourseLevel.beginner in levels:
            assert levels.index(CourseLevel.fluency) < levels.index(CourseLevel.beginner)

    def test_totals_add_up(self, session):
        path = tools.build_learning_path(session, domain="digital payments")
        assert path.total_hours == sum(s.course.duration_hours for s in path.steps)
        assert path.total_price_max_inr >= path.total_price_min_inr

    def test_respects_a_level_range(self, session):
        path = tools.build_learning_path(
            session,
            domain="digital payments",
            start_level=CourseLevel.discovery,
            target_level=CourseLevel.fluency,
        )
        assert {s.course.level for s in path.steps} <= {
            CourseLevel.discovery,
            CourseLevel.fluency,
        }

    def test_unknown_domain_is_reported(self, session):
        assert isinstance(tools.build_learning_path(session, domain="astrology"), NotFound)


class TestCannotInvent:
    """The guarantees the whole architecture exists to provide."""

    def test_a_path_never_claims_prerequisites_exist(self, session):
        # Intellimindz has published none, so no path may be described as required.
        for domain in ("digital payments", "regtech", "blockchain"):
            path = tools.build_learning_path(session, domain=domain)
            assert path.is_prerequisite_enforced is False

    def test_derived_ordering_is_labelled_as_derived_in_the_database(self, session):
        from app.db.models import CourseRelationship

        rows = session.execute(select(CourseRelationship)).scalars().all()
        assert rows
        assert all(r.source == "derived_from_level" for r in rows)
        assert all(r.kind != "prerequisite" for r in rows), (
            "a derived edge must never be recorded as a prerequisite"
        )

    def test_unstated_status_surfaces_as_none(self, session):
        # Several courses have no status in the sheet; none may acquire one.
        details = [
            tools.get_course(session, c.slug)
            for c in tools.search_courses(session, limit=tools.MAX_RESULTS)
        ]
        assert any(d.status is None for d in details)

    def test_no_course_carries_a_confirmed_price_yet(self, session):
        courses = session.execute(select(Course)).scalars().all()
        assert all(c.price_inr is None for c in courses), (
            "a per-course price would have to come from Intellimindz, not from us"
        )

    def test_comparison_renders_missing_values_as_none(self, session):
        slugs = [c.slug for c in tools.search_courses(session, limit=3)]
        comparison = tools.compare_courses(session, slugs)
        status_row = next(r for r in comparison.rows if r.field == "Status")
        assert all(v is None or isinstance(v, str) for v in status_row.values.values())
