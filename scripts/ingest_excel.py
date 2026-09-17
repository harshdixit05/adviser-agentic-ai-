"""
Load the course workbook into Postgres.

    python -m scripts.ingest_excel [--dry-run]

Idempotent: the catalogue is replaced on every run, so this is the one command
to re-run whenever the spreadsheet changes.
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import engine, session_scope
from app.ingestion.excel.loader import WorkbookLoader
from app.ingestion.excel.writer import write_catalogue
from app.core.console import use_unicode_output


def main() -> int:
    use_unicode_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and report without touching the database",
    )
    args = parser.parse_args()
    settings = get_settings()

    report = WorkbookLoader(settings.workbook_path, settings.sheet_mapping_path).load()
    print(report.render())

    if not report.courses:
        print("Nothing parsed — refusing to write an empty catalogue.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("  --dry-run: database untouched\n")
        return 0

    Base.metadata.create_all(engine)

    with session_scope() as session:
        counts = write_catalogue(session, report.courses)

    print("  Written to the catalogue:")
    for label, value in counts.items():
        print(f"    {label:<20} {value:>5}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
