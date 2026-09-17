"""
Sets the advisor up from a fresh clone, in one command.

    Windows:      python bootstrap.py
    macOS/Linux:  python3 bootstrap.py

Creates the virtualenv, installs, builds the catalogue and checks it. Safe to
re-run; re-running rebuilds the catalogue from the workbook.

Standard library only, on purpose: this is what runs *before* anything is
installed, and it is the one file that has to work on a machine where nothing
is set up yet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WORKBOOK = ROOT / "data" / "raw" / "courses.xlsx"

WINDOWS = os.name == "nt"
# The one platform difference that matters here.
PYTHON = VENV / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")


def say(message: str) -> None:
    print(f"\n{message}", flush=True)


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    raise SystemExit(1)


def run(*command: str) -> None:
    result = subprocess.run(command)
    if result.returncode != 0:
        fail(f"Failed: {' '.join(str(c) for c in command)}")


def main() -> int:
    if sys.version_info < (3, 10):
        fail(
            f"Python 3.10 or newer is required; this is {sys.version.split()[0]}.\n"
            "Get a current version from https://www.python.org/downloads/"
        )

    if not WORKBOOK.exists():
        fail(
            f"Missing {WORKBOOK.relative_to(ROOT)}\n\n"
            "The course workbook is deliberately not in git. Copy it there and\n"
            "run this again. On Windows you can drag it into data\\raw\\ and\n"
            "rename it to courses.xlsx."
        )

    say("1/4  Creating the virtualenv")
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    else:
        print("     already there")

    say("2/4  Installing (this takes a few minutes the first time)")
    run(str(PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip")
    run(
        str(PYTHON), "-m", "pip", "install", "--quiet",
        "--timeout", "120", "--retries", "5", "-e", ".[dev]",
    )

    env_local = ROOT / ".env.local"
    if env_local.exists():
        say("3/4  Keeping your existing .env.local")
    else:
        say("3/4  Writing .env.local")
        shutil.copyfile(ROOT / ".env.example", env_local)
        print("     Add your GEMINI_API_KEY to it before chatting.")

    say("4/4  Building the catalogue")
    run(str(PYTHON), "-m", "scripts.ingest_excel")

    say("Checking it works")
    run(str(PYTHON), "-m", "pytest", "-q")

    chat = PYTHON.relative_to(ROOT)
    print(
        "\n"
        "Done. The catalogue is loaded and everything passes.\n"
        "\n"
        "Next:\n"
        "  1. Get a key at https://aistudio.google.com/apikey and put it in\n"
        '     .env.local as GEMINI_API_KEY="..."\n'
        "  2. Talk to it:\n"
        f"       {chat} -m scripts.chat\n"
        "\n"
        '     Ask "what payments courses do you have?", then type /why to see\n'
        "     the exact catalogue rows the answer was checked against.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
