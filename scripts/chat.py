"""
A terminal harness for the advisor.

    python -m scripts.chat

This is the internal tool for watching the thing work, not a product surface.
Alongside the reply it can show the profile the agent has built and the exact
tool rows the answer was checked against, which is how you tell a grounded
answer from a lucky one.

Commands: /profile, /why, /reset, /quit
"""

from __future__ import annotations

import json
import sys

from app.agent.conversation import Advisor, Turn
from app.agent.model import ModelNotConfigured, build_model
from app.core.console import use_unicode_output

BANNER = """Intellimindz learning advisor — internal harness
Course facts come from the catalogue database; anything the tools did not
return is refused rather than guessed.
Commands: /profile  /why  /reset  /quit
"""


def _show_profile(advisor: Advisor) -> None:
    known = {k: v for k, v in advisor.profile.model_dump(mode="json").items() if v is not None}
    print(json.dumps(known, indent=2) if known else "(nothing established yet)")


def _show_grounding(turn: Turn | None) -> None:
    if turn is None:
        print("(nothing asked yet)")
        return
    if not turn.tool_results:
        print("(no tools ran on that turn — the reply asserted no course facts)")
        return
    for entry in turn.tool_results:
        print(f"\n{entry['tool']}:")
        print(json.dumps(entry["results"], indent=2, default=str)[:2000])
    if turn.unsupported_claims:
        print(f"\nclaims the catalogue did not support: {turn.unsupported_claims}")


def main() -> int:
    use_unicode_output()
    try:
        model = build_model()
    except ModelNotConfigured as error:
        print(error, file=sys.stderr)
        return 1

    advisor = Advisor(model)
    last: Turn | None = None
    print(BANNER)

    while True:
        try:
            text = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not text:
            continue
        if text in {"/quit", "/exit"}:
            return 0
        if text == "/profile":
            _show_profile(advisor)
            continue
        if text == "/why":
            _show_grounding(last)
            continue
        if text == "/reset":
            advisor.reset()
            last = None
            print("(conversation cleared)")
            continue

        try:
            last = advisor.ask(text)
        except Exception as error:  # a transport failure must not look like an answer
            print(f"\n[the model call failed: {type(error).__name__}: {error}]\n")
            continue

        print(f"\nadvisor › {last.answer}\n")
        if last.tools_used:
            print(f"          [checked against: {', '.join(last.tools_used)}]\n")


if __name__ == "__main__":
    raise SystemExit(main())
