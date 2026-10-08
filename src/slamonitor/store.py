"""SQLite-Ablage. Liegt lokal auf dem Mac (data_dir), nie im Repo."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .model import Case, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    ts TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS audit_sendas (
    internet_message_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    mailbox TEXT,
    ts TEXT
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS owners (
    client TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    source TEXT
);
"""


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o))


def _event_from(data: dict) -> Event:
    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
    return Event(**data)


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def upsert_event(self, ev: Event) -> None:
        """Gleiche Nachricht aus mehreren Postfächern: Postfächer und Thread-Schlüssel vereinigen."""
        row = self.db.execute("SELECT data FROM events WHERE uid=?", (ev.uid,)).fetchone()
        if row:
            old = _event_from(json.loads(row[0]))
            for mb in ev.mailboxes:
                if mb not in old.mailboxes:
                    old.mailboxes.append(mb)
            for h in ev.thread_hints:
                if h not in old.thread_hints:
                    old.thread_hints.append(h)
            ev = old
        self.db.execute(
            "INSERT OR REPLACE INTO events(uid, source, ts, data) VALUES (?,?,?,?)",
            (ev.uid, ev.source, ev.timestamp.isoformat(), json.dumps(asdict(ev), default=_default)),
        )

    def events(self, since: datetime | None = None) -> list[Event]:
        q, args = "SELECT data FROM events", ()
        if since:
            q, args = q + " WHERE ts >= ?", (since.isoformat(),)
        return [_event_from(json.loads(r[0])) for r in self.db.execute(q, args)]

    def add_audit(self, rows: list[tuple[str, str, str, str]]) -> int:
        self.db.executemany(
            "INSERT OR REPLACE INTO audit_sendas(internet_message_id, user_id, mailbox, ts) VALUES (?,?,?,?)",
            rows,
        )
        return len(rows)

    def audit_map(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT internet_message_id, user_id FROM audit_sendas"))

    def set_owners(self, mapping: dict[str, str], source: str) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO owners(client, owner, source) VALUES (?,?,?)",
            [(c, o, source) for c, o in mapping.items()],
        )

    def owners(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT client, owner FROM owners"))

    def llm_get(self, key: str) -> dict | None:
        row = self.db.execute("SELECT data FROM llm_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def llm_put(self, key: str, data: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO llm_cache(key, data) VALUES (?,?)", (key, json.dumps(data)))
        self.db.commit()

    def save_cases(self, cases: list[Case]) -> None:
        self.db.execute("DELETE FROM cases")
        self.db.executemany(
            "INSERT INTO cases(case_id, data) VALUES (?,?)",
            [(c.case_id, json.dumps(asdict(c), default=_default)) for c in cases],
        )
        self.db.commit()
