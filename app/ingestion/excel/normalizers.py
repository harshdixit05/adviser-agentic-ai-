"""
Turning the spreadsheet's many spellings of the same thing into one shape.

Every function here returns None rather than guessing when the input is not
recognised. The caller records those as unmapped in the reconciliation report,
so a value nobody anticipated surfaces as a question instead of a silent
wrong answer in the catalogue.
"""

from __future__ import annotations

import re
import unicodedata

from app.db.models import ContentStatus, CourseLevel

# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def clean(value: object) -> str:
    """Collapse whitespace and normalise the dashes and ticks used as bullets."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("–", "-").replace("—", "-")
    return _WS.sub(" ", text).strip()


def header_key(value: object) -> str:
    """Normalise a header cell so 'Proposed Curriculum\\n(Module-wise)' matches."""
    text = clean(value).lower()
    text = text.replace("—", "").replace("–", "").replace("-", " ")
    text = re.sub(r"[^\w\s().#/]", "", text)
    return _WS.sub(" ", text).strip()


def slugify(value: str, max_length: int = 150) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return _WS.sub("-", text)[:max_length].strip("-")


# --------------------------------------------------------------------------
# scalars
# --------------------------------------------------------------------------

_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)?\b", re.I)


def parse_hours(value: object) -> float | None:
    """'4 hrs', '3h', 3.0 and '4.2' all mean the same thing."""
    if value is None or clean(value) == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    match = _HOURS.search(clean(value))
    return round(float(match.group(1)), 1) if match else None


def parse_level(value: object) -> CourseLevel | None:
    text = clean(value).lower()
    for level in CourseLevel:
        if text.startswith(level.value):
            return level
    return None


# Only mappings the sheet states unambiguously. Bare 'Done' is deliberately
# absent: it appears without saying what was done, so it stays NULL and is
# reported rather than guessed into a status.
_STATUS = {
    "recorded": ContentStatus.recorded,
    "content ready": ContentStatus.content_ready,
    "ppt done": ContentStatus.ppt_done,
    "ppt - done": ContentStatus.ppt_done,
    "proposed": ContentStatus.proposed,
    "published": ContentStatus.published,
}


def parse_status(value: object) -> ContentStatus | None:
    text = clean(value).lower().rstrip("-– ").strip()
    return _STATUS.get(text)


# --------------------------------------------------------------------------
# curriculum
# --------------------------------------------------------------------------

# Two authoring styles appear across the sheets:
#   A) "M1. Payment Rails & Ecosystem Overview | 30 mins"
#   B) "Module 1: Cyber Threat Landscape" followed by body lines
_MODULE_A = re.compile(r"^M\s*(\d+)\s*[.):]\s*(.+?)(?:\s*\|\s*(\d+)\s*mins?)?$", re.I)
_MODULE_B = re.compile(r"^Module\s*(\d+)\s*[:.]\s*(.+?)(?:\s*[|–-]\s*(\d+)\s*mins?)?$", re.I)


def parse_modules(value: object) -> list[dict]:
    """
    Returns [{position, title, duration_mins}] in sheet order.

    Lines that are neither heading style are treated as body text belonging to
    the module above, which is how style B carries its detail.
    """
    raw = str(value or "")
    modules: list[dict] = []

    for line in raw.splitlines():
        line = clean(line)
        if not line:
            continue
        match = _MODULE_A.match(line) or _MODULE_B.match(line)
        if not match:
            continue  # body text; the RAG pipeline keeps it, the catalogue does not
        position, title, mins = match.groups()
        modules.append(
            {
                "position": int(position),
                "title": clean(title),
                "duration_mins": int(mins) if mins else None,
            }
        )

    # Guard against a duplicated module number collapsing two real modules.
    seen: set[int] = set()
    unique: list[dict] = []
    for module in modules:
        if module["position"] in seen:
            continue
        seen.add(module["position"])
        unique.append(module)
    return sorted(unique, key=lambda m: m["position"])


# --------------------------------------------------------------------------
# personas
# --------------------------------------------------------------------------

# Sheets separate audiences with pipes, bullets or newlines — and the AI in
# Finance sheet uses no separator at all, relying on the tick that follows each
# audience. Splitting on the tick as well handles every sheet uniformly.
_PERSONA_SPLIT = re.compile(r"[|•·\n✓✔]+")

# Different sheets name the same audience differently.
_PERSONA_ALIASES = {
    "cas": "Chartered Accountants",
    "ca": "Chartered Accountants",
    "ngos": "NGOs",
    "shgs": "Self-Help Groups",
    "govt teachers": "Government Teachers",
    "government teachers": "Government Teachers",
    "govt employees": "Government Employees",
    "government employees": "Government Employees",
    "school teachers": "School Teachers",
    "school teachers/principals": "School Teachers",
    "school children": "School Children",
    "housewives": "Homemakers",
    "house wives": "Homemakers",
    "compliance officers": "Compliance Professionals",
    "banking staff": "Banking Professionals",
    "banking professionals": "Banking Professionals",
    "fintech professionals": "FinTech Professionals",
    "finance professionals": "Finance Professionals",
    "compliance professionals": "Compliance Professionals",
    "risk professionals": "Risk Professionals",
    "risk managers": "Risk Professionals",
    "technology teams": "Technology Teams",
    "product managers": "Product Managers",
    "data scientists": "Data Scientists",
    "working professionals": "Working Professionals",
}


def parse_personas(value: object) -> list[str]:
    """Returns canonical audience labels, de-duplicated, in first-seen order."""
    raw = str(value or "")
    labels: list[str] = []

    for part in _PERSONA_SPLIT.split(raw):
        token = clean(part)
        if not token:
            continue
        # A slash means two different things depending on where it falls.
        # After a tick ("Govt. Employees ✓ / Working Professionals") it starts
        # a second audience; inside a label ("School Teachers/Principals") it
        # describes one. Strip the leading case first, then collapse the rest.
        token = token.lstrip("/ ").strip()
        token = re.split(r"\s*/\s*(?=[A-Z])", token)[0]
        token = clean(token).strip("-,;:. ")
        if not token or len(token) > 60:
            continue
        # "Govt. Employees" and "govt employees" are the same audience.
        key = re.sub(r"[.\s]+", " ", token.lower()).strip()
        label = _PERSONA_ALIASES.get(key, token)
        if label not in labels:
            labels.append(label)
    return labels
