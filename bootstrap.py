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
RAW = ROOT / "data" / "raw"
WORKBOOK = RAW / "courses.xlsx"

WINDOWS = os.name == "nt"
# The one platform difference that matters here.
PYTHON = VENV / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")


def say(message: str) -> None:
    print(f"\n{message}", flush=True)


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    raise SystemExit(1)


def spreadsheets() -> list[Path]:
    """Every workbook in data/raw, ignoring Excel's lock files."""
    return sorted(p for p in RAW.glob("*.xlsx") if not p.name.startswith("~$"))


def find_workbook() -> Path | None:
    """
    The workbook, under whatever name it arrived with.

    Windows hides known extensions by default, so renaming a download to
    "courses.xlsx" in Explorer commonly produces courses.xlsx.xlsx. Insisting
    on the exact name turns that into a missing-file error the person cannot
    see the cause of, so a single spreadsheet in the folder is accepted
    whatever it is called.
    """
    if WORKBOOK.exists():
        return WORKBOOK
    found = spreadsheets()
    return found[0] if len(found) == 1 else None


def describe_raw_folder() -> str:
    if not RAW.is_dir():
        return "  That folder does not exist yet."
    entries = sorted(p.name for p in RAW.iterdir() if p.name != ".gitkeep")
    if not entries:
        return "  That folder is empty."
    listed = "\n".join(f"    {name}" for name in entries)
    return f"  That folder currently holds:\n{listed}"


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

    workbook = find_workbook()
    if workbook is None:
        found = spreadsheets()
        if len(found) > 1:
            names = "\n".join(f"    {p.name}" for p in found)
            fail(
                f"More than one spreadsheet in {RAW}\n\n{names}\n\n"
                "Leave only the course workbook there, or name it courses.xlsx."
            )
        fail(
            f"Could not find the course workbook. I looked in:\n\n  {RAW}\n\n"
            f"{describe_raw_folder()}\n\n"
            "The workbook is deliberately not in git, so copy it into that exact\n"
            "folder. Any .xlsx there will do — it does not have to be called\n"
            "courses.xlsx.\n\n"
            "If you believe you already put it there, check you are in the right\n"
            "copy of the project: the path above is the only one this run looks at."
        )

    if workbook != WORKBOOK:
        say(f"Using {workbook.name} as the course workbook")

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
