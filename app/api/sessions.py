"""
In-memory conversation store.

A conversation is a `LearnerProfile` and a transcript, which is small; holding
a few thousand costs little. It is deliberately not a database: nothing here
is worth persisting, and a learner who comes back tomorrow starting fresh is a
better outcome than a chat transcript accumulating on disk.

Two consequences worth naming. The store is per process, so more than one
worker means a learner can land on a worker that has never heard of them —
fine for a single uvicorn process, and the thing to replace with Redis before
scaling out. And eviction is by age and then by least-recent use, so the store
has a ceiling instead of growing until the process dies.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.agent.conversation import Advisor


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Entry:
    advisor: Advisor
    last_seen: datetime


class SessionStore:
    def __init__(self, ttl_minutes: int, max_sessions: int):
        self._ttl = timedelta(minutes=ttl_minutes)
        self._max = max_sessions
        self._entries: OrderedDict[str, Entry] = OrderedDict()
        self._lock = threading.Lock()

    def _evict(self) -> None:
        cutoff = _now() - self._ttl
        for key in [k for k, e in self._entries.items() if e.last_seen < cutoff]:
            del self._entries[key]
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def get_or_create(self, session_id: str | None, make: callable) -> tuple[str, Advisor]:
        """
        Returns the conversation for `session_id`, or a brand new one.

        An unknown or expired id quietly becomes a new conversation rather than
        an error: from the learner's side a long pause should mean starting
        over, not a failure. Ids are generated here and never taken from the
        caller, so one cannot be guessed into.
        """
        with self._lock:
            self._evict()

            if session_id and session_id in self._entries:
                entry = self._entries[session_id]
                entry.last_seen = _now()
                self._entries.move_to_end(session_id)
                return session_id, entry.advisor

            new_id = secrets.token_urlsafe(24)
            self._entries[new_id] = Entry(advisor=make(), last_seen=_now())
            self._evict()
            return new_id, self._entries[new_id].advisor

    def forget(self, session_id: str) -> bool:
        with self._lock:
            return self._entries.pop(session_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            self._evict()
            return len(self._entries)
