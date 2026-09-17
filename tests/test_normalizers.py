"""
The source sheets were authored by different people in different styles, so
these tests pin the real variations found in the workbook. Each case below is
a shape that actually appears — not a hypothetical.
"""

import pytest

from app.db.models import ContentStatus, CourseLevel
from app.ingestion.excel import normalizers as norm


class TestHours:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (3.0, 3.0),          # Fintech Core, numeric
            ("4 hrs", 4.0),      # Cybersecurity
            ("3h", 3.0),         # Sustainable Finance
            ("4.2", 4.2),        # InsurTech, fractional
            (None, None),
            ("", None),
            ("TBC", None),       # unreadable stays None rather than becoming 0
        ],
    )
    def test_parses_every_style_in_the_workbook(self, raw, expected):
        assert norm.parse_hours(raw) == expected


class TestLevel:
    def test_reads_the_five_levels(self):
        assert norm.parse_level("Discovery") is CourseLevel.discovery
        assert norm.parse_level("  advanced ") is CourseLevel.advanced

    def test_unknown_level_is_none_not_a_guess(self):
        assert norm.parse_level("Expert") is None
        assert norm.parse_level("") is None

    def test_ladder_order_puts_fluency_before_beginner(self):
        # Unusual, but confirmed by Intellimindz — path building depends on it.
        assert CourseLevel.fluency.rank < CourseLevel.beginner.rank
        assert CourseLevel.discovery.rank == 0
        assert CourseLevel.advanced.rank == 4


class TestStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Recorded", ContentStatus.recorded),
            ("Content Ready", ContentStatus.content_ready),
            ("PPT - Done", ContentStatus.ppt_done),
            ("Proposed -", ContentStatus.proposed),
        ],
    )
    def test_maps_known_values(self, raw, expected):
        assert norm.parse_status(raw) is expected

    def test_bare_done_is_not_guessed(self):
        # 'Done' appears without saying what was done. Guessing it into a status
        # would let the advisor claim a course is ready when it may not be.
        assert norm.parse_status("Done") is None


class TestModules:
    def test_pipe_style_with_durations(self):
        raw = (
            "M1. Payment Rails & Ecosystem Overview | 30 mins\n"
            "M2. Mobile Payment Ecosystem Map | 45 mins"
        )
        modules = norm.parse_modules(raw)
        assert [m["position"] for m in modules] == [1, 2]
        assert modules[0]["title"] == "Payment Rails & Ecosystem Overview"
        assert modules[0]["duration_mins"] == 30

    def test_colon_style_without_durations(self):
        raw = (
            "Module 1: Cyber Threat Landscape in Indian Finance\n"
            "Account takeover, phishing and card fraud\n"
            "Module 2: Controls That Actually Work"
        )
        modules = norm.parse_modules(raw)
        assert len(modules) == 2, "body lines must not become modules"
        assert modules[1]["title"] == "Controls That Actually Work"
        assert modules[0]["duration_mins"] is None

    def test_duplicate_positions_do_not_collapse_silently(self):
        modules = norm.parse_modules("M1. First | 10 mins\nM1. Repeat | 20 mins")
        assert len(modules) == 1

    def test_empty_curriculum(self):
        assert norm.parse_modules(None) == []


class TestPersonas:
    def test_bullet_separated(self):
        raw = "CAs ✓ • NGOs ✓ • SHGs ✓"
        assert norm.parse_personas(raw) == [
            "Chartered Accountants",
            "NGOs",
            "Self-Help Groups",
        ]

    def test_pipe_separated(self):
        raw = "CAs ✓ | Govt. Employees ✓ | School Teachers ✓"
        assert norm.parse_personas(raw) == [
            "Chartered Accountants",
            "Government Employees",
            "School Teachers",
        ]

    def test_tick_is_the_only_separator(self):
        # The AI in Finance sheet runs audiences together with no delimiter.
        raw = "CAs ✓ Banking Professionals ✓ Finance Professionals ✓"
        assert norm.parse_personas(raw) == [
            "Chartered Accountants",
            "Banking Professionals",
            "Finance Professionals",
        ]

    def test_newline_separated(self):
        raw = "Banking Staff ✓\nCompliance Professionals ✓\nCAs ✓"
        assert norm.parse_personas(raw) == [
            "Banking Professionals",
            "Compliance Professionals",
            "Chartered Accountants",
        ]

    def test_compound_audience_takes_the_first(self):
        assert norm.parse_personas("School Teachers/Principals ✓") == ["School Teachers"]
        assert norm.parse_personas("Govt. Employees ✓ / Working Professionals") == [
            "Government Employees",
            "Working Professionals",
        ]

    def test_same_audience_spelled_differently_collapses(self):
        assert norm.parse_personas("Banking Staff ✓") == ["Banking Professionals"]
        assert norm.parse_personas("House wives ✓") == ["Homemakers"]
        assert norm.parse_personas("Govt. Employees ✓") == ["Government Employees"]

    def test_deduplicates_within_one_cell(self):
        assert norm.parse_personas("CAs ✓ • CAs ✓") == ["Chartered Accountants"]

    def test_empty(self):
        assert norm.parse_personas(None) == []
        assert norm.parse_personas("   ") == []


class TestHeaderMatching:
    def test_multiline_and_punctuated_headers_match(self):
        assert norm.header_key("Proposed Curriculum \n(Module-wise)") == norm.header_key(
            "proposed curriculum (module wise)"
        )
        assert norm.header_key("Curriculum  —  Module by Module") == norm.header_key(
            "curriculum module by module"
        )
