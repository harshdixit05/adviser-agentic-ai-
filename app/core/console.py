"""
Make the terminal safe for the characters this project prints.

Rupee amounts, the box rules in the ingest report and the level markers carried
over from the workbook are all outside cp1252, which is still the console
encoding on many Windows machines. Printing one there does not mangle the
output — it raises UnicodeEncodeError and takes the whole command down. The
ingest report was unrunnable on Windows for exactly this reason.

Called at the top of anything that writes to a terminal.
"""

from __future__ import annotations

import sys


def use_unicode_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            # `replace` keeps a console that cannot render a glyph from taking
            # the process down with it; the text is what matters, not the shape.
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A redirected or wrapped stream that will not be reconfigured.
            # Nothing to do, and not worth failing a command over.
            pass
