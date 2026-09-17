#!/usr/bin/env bash
#
# Sets up the advisor from a fresh clone, in one command:
#
#     ./setup.sh
#
# Creates the virtualenv, installs, builds the catalogue database and checks
# it. Safe to re-run — re-running rebuilds the catalogue from the workbook.

set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m%s\033[0m\n\n' "$1" >&2; exit 1; }

command -v python3 >/dev/null || fail "Python 3 is not installed. Get it from python.org."

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 3.11 or newer is required. You have $(python3 -V)."

if [ ! -f data/raw/courses.xlsx ]; then
  fail "Missing data/raw/courses.xlsx.
The course workbook is deliberately not in git. Copy it there, then run this again:
    cp /path/to/courses.xlsx data/raw/courses.xlsx"
fi

say "1/4  Creating the virtualenv"
[ -d .venv ] || python3 -m venv .venv

say "2/4  Installing (a few minutes the first time)"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet --timeout 120 --retries 5 -e ".[dev]"

if [ ! -f .env.local ]; then
  say "3/4  Writing .env.local"
  cp .env.example .env.local
  printf '%s\n' "  Created .env.local. Add your GEMINI_API_KEY to it before chatting."
else
  say "3/4  Keeping your existing .env.local"
fi

say "4/4  Building the catalogue"
./.venv/bin/python -m scripts.ingest_excel

say "Checking it works"
./.venv/bin/python -m pytest -q

cat <<'DONE'

Done. The catalogue is loaded and everything passes.

Next:
  1. Put your Gemini key in .env.local as GEMINI_API_KEY="..."
     (get one at https://aistudio.google.com/apikey)
  2. Talk to it:
       ./.venv/bin/python -m scripts.chat

     Ask "what payments courses do you have?", then type /why to see the
     exact catalogue rows the answer was checked against.

DONE
