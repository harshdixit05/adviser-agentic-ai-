# Intellimindz AI Learning Advisor

An agentic advisor that understands a learner's background and goal, then uses
Intellimindz's own course data to answer questions, recommend courses and build
a learning path.

**Status: Phases 1 and 3 complete.** The catalogue is loaded, the agent runs
over it end to end, and the HTTP endpoint is up. Phase 2 (document retrieval) is
waiting on source documents. No live model call has been made yet — that needs a
key in `.env.local`.

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
- **Every drafted answer is checked before it is sent.** `app/agent/nodes/verify.py`
  pulls each rupee amount, hour figure and course-name-shaped phrase out of the
  draft and requires it to appear in what the tools returned *for that turn*. A
  failing draft goes back once with the offending claims named; a second failure
  is refused outright and the learner is pointed at the team. A wrong fee quoted
  to someone deciding whether to enrol is worse than an answer admitting it
  cannot be given.

The last point is the one that makes the others hold. It is ordinary Python, not
a second model asked whether the first one told the truth.

## Setup

Put the course workbook at `data/raw/courses.xlsx` first — it is deliberately
not in git — then:

```bash
./setup.sh
```

That makes the virtualenv, installs, builds the catalogue and runs the tests.
No database server is needed: with no `DATABASE_URL` set, the catalogue lives in
`data/advisor.db`. The schema is dialect-agnostic, so point `DATABASE_URL` at
Postgres for anything deployed and the same ingest produces the same catalogue.

The long way, if you would rather see each step:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env.local        # add your GEMINI_API_KEY
python -m scripts.ingest_excel    # load the workbook
pytest
```

Then talk to it in a terminal:

```bash
python -m scripts.chat            # /profile, /why, /reset, /quit
```

Ask it something, then type `/why`: that prints the exact catalogue rows the
answer was checked against, which is the quickest way to see the grounding gate
doing its job.

Or run the service:

```bash
uvicorn app.api.main:app --reload
curl localhost:8000/health
```

Both need `GEMINI_API_KEY` in `.env.local`. Without it the harness says so and
exits; the service starts anyway, reports `degraded` on `/health` and returns
503 from `/api/chat`, so a deployment missing its key is diagnosable rather than
dead.

Re-running the ingest replaces the catalogue, so it is the single command to run
after any spreadsheet change.

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
| 3 ✅ | LangGraph agent, grounding gate, terminal harness, FastAPI |
| 4 | Website chat widget, lead capture |

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
  agent/
    tools/      catalogue queries, and their LangChain wrappers
    nodes/      verify.py — the grounding gate
    graph.py    understand → act ⇄ tools → verify
    conversation.py  what crosses between turns, and what must not
    model.py    the only module that knows about Gemini
  api/          FastAPI app, session store, rate limiters
data/
  mappings/     sheet_columns.yaml — how to read each sheet
  raw/          source workbook (gitignored)
scripts/        ingest_excel.py, chat.py
tests/          109 tests; the ones in test_verify.py matter most
```

## The agent

One agent with one tool loop, not a crew of specialists:

```
understand → act ⇄ tools → verify → END
                    │          │
                    │       compose      (only on a failed check)
                    └──────────┘
```

`understand` keeps the learner profile current across turns and never blanks a
field an earlier turn established. `act` is the only node that may call tools,
and once it stops calling them the reply it just wrote *is* the draft, so an
ordinary turn costs one drafting call rather than two. `compose` exists solely
to rewrite a draft the gate rejected.

The profile carries between turns; the tool results deliberately do not. Every
turn is graded against its own evidence, so a fee looked up two questions ago
cannot vouch for a number in today's answer.

## The service

`POST /api/chat` takes `{message, session_id?}` and returns the answer, the
catalogue lookups behind it, and whether the gate withheld anything.

Conversations are held in memory with a TTL and a ceiling; the rate limiters are
per conversation *and* per address, because a per-conversation limit alone is
defeated by asking for a new conversation each time. Both are per process —
single uvicorn worker for now, Redis before scaling out. CORS is an allow-list
(`ALLOWED_ORIGINS`) with credentials off. Behind a reverse proxy, run uvicorn
with `--proxy-headers --forwarded-allow-ips=<proxy>`; `X-Forwarded-For` is
otherwise ignored, since any caller can set it.

## Talking to the website

The Foundation's site (the `Edtech-platform-website-` repo) does not call this
service from the browser. Its own server proxies chat at `/api/advisor`, which
keeps this service's URL and token off the client and means no public origin
needs a CORS grant.

Set the same `ADVISOR_API_TOKEN` on both sides. It does two jobs: it is how this
service tells the website apart from anyone who found the URL, and it is what
lets the website send `X-Client-Id` to say which visitor a request belongs to.
Without that header every request would arrive from the website's single address
and the per-address limit would become one bucket for the whole site — the first
busy visitor would lock out everyone else. An unauthenticated caller's
`X-Client-Id` is ignored, since honouring it would turn the header into a way of
resetting your own limit.

The id is an HMAC of the visitor's address computed on the website's side, so no
visitor's IP reaches this service at all.
