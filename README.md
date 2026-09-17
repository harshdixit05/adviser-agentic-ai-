# Intellimindz AI Learning Advisor

An agentic advisor that understands a learner's background and goal, then uses
Intellimindz's own course data to answer questions, recommend courses and build
a learning path.

**Status: Phase 1 complete.** The catalogue is loaded and queryable. The agent,
retrieval and API are not built yet.

## The rule this system is built around

The advisor must never invent a course, price, duration or prerequisite. That is
enforced structurally, not by prompting:

- **Postgres is authoritative** for anything factual — titles, levels, hours,
  modules, audiences, prices. Every such claim comes from a tool that reads a
  row, never from model memory.
- **Qdrant is explanatory only**, and every chunk carries the `course_id` it
  came from so an explanation can be checked against the catalogue.
- **Prices are bands, not figures.** Advanced is ₹34,999–₹44,999, and the schema
  stores min and max separately so the range can never collapse into one number.
  All bands are marked `is_indicative`.
- **No prerequisites exist**, so none are asserted. The only ordering is derived
  from the level ladder and stored as `kind='follows'`,
  `source='derived_from_level'` — presented as a suggested next step, never a
  requirement.
- **Unrecognised values stay NULL** and are reported, rather than being guessed
  into a plausible-looking value.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env.local        # set DATABASE_URL
createdb advisor

python -m scripts.ingest_excel    # load the workbook
pytest
```

Put the source workbook at `data/raw/courses.xlsx`. Re-running the ingest
replaces the catalogue, so it is the single command to run after any spreadsheet
change.

## Ingestion

The nine domain sheets were authored independently and disagree with each other:
header rows sit at different depths, column order flips, hours appear as `3.0`,
`"4 hrs"` and `"3h"`, audiences are separated by pipes, bullets, newlines or
nothing but a tick, and level banner rows are interleaved between courses.

`data/mappings/sheet_columns.yaml` declares how to read them — columns resolve
by matching header aliases, not fixed positions, so a new sheet is usually a
config edit. Every ingest prints a reconciliation report showing what parsed,
what was rejected and which fields the sheet left empty.

Current load: **89 courses, 570 modules, 52 audiences, 430 course↔audience
links** across 9 domains.

## What's next

| Phase | Scope |
|---|---|
| 1 ✅ | Excel ingestion, Postgres catalogue, reconciliation report |
| 2 | Document ingestion (DOCX/PDF/PPTX), chunking, Qdrant, `explain` tool |
| 3 | LangGraph agent over 5 Digital Payments courses, FastAPI, Streamlit harness |
| 4 | Full catalogue, website chat widget, lead capture |

Phase 2 is currently limited: the only unstructured text available is the
module-wise curriculum, which already lives in Postgres. Real course documents
are needed before retrieval adds much.

## Layout

```
app/
  core/         settings
  db/           SQLAlchemy models, session
  ingestion/
    excel/      loader, normalizers, writer
data/
  mappings/     sheet_columns.yaml — how to read each sheet
  raw/          source workbook (gitignored)
scripts/        ingest_excel.py
tests/          parser tests, pinned to shapes that appear in the real sheets
```
