"""Persistent prospect memory (SQLite, local). The "brain" shared across calls and with the
Hermes agent tools. Deliberately plain: a regulator can read it, a user can delete it.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
  number TEXT PRIMARY KEY, name TEXT, company TEXT, audience TEXT, sector TEXT,
  notes TEXT, consent_json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY, number TEXT, call_id TEXT, at TEXT, summary TEXT, outcome TEXT,
  delete_after TEXT);
"""


class ProspectMemory:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "memory.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def upsert(self, number: str, **fields):
        fields["updated_at"] = datetime.now(UTC).isoformat()
        if "consent" in fields:
            fields["consent_json"] = json.dumps(fields.pop("consent"), default=str)
        cols = ", ".join(fields)
        sets = ", ".join(f"{k}=excluded.{k}" for k in fields)
        self.db.execute(
            f"INSERT INTO prospects (number, {cols}) VALUES (?, {', '.join('?' * len(fields))}) "
            f"ON CONFLICT(number) DO UPDATE SET {sets}", (number, *fields.values()))
        self.db.commit()

    def get(self, number: str) -> dict | None:
        row = self.db.execute("SELECT * FROM prospects WHERE number=?", (number,)).fetchone()
        return dict(row) if row else None

    def add_interaction(self, number: str, call_id: str, summary: str, outcome: str, retention_days: int):
        delete_after = datetime.now(UTC).date().toordinal() + retention_days
        self.db.execute(
            "INSERT INTO interactions (number, call_id, at, summary, outcome, delete_after) VALUES (?,?,?,?,?,?)",
            (number, call_id, datetime.now(UTC).isoformat(), summary, outcome,
             datetime.fromordinal(delete_after).date().isoformat()))
        self.db.commit()

    def history(self, number: str, limit: int = 5) -> list[dict]:
        rows = self.db.execute("SELECT * FROM interactions WHERE number=? ORDER BY at DESC LIMIT ?",
                               (number, limit)).fetchall()
        return [dict(r) for r in rows]

    def as_text(self, number: str) -> str:
        p = self.get(number)
        if not p:
            return ""
        lines = [f"{k}: {v}" for k, v in p.items() if v and k not in ("number", "updated_at", "consent_json")]
        lines += [f"- {h['at'][:10]}: {h['outcome']} - {h['summary']}" for h in self.history(number)]
        return "\n".join(lines)

    def purge_expired(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        cur = self.db.execute("DELETE FROM interactions WHERE delete_after < ?", (today,))
        self.db.commit()
        return cur.rowcount

    def forget(self, number: str) -> None:
        """Right to erasure."""
        self.db.execute("DELETE FROM interactions WHERE number=?", (number,))
        self.db.execute("DELETE FROM prospects WHERE number=?", (number,))
        self.db.commit()
