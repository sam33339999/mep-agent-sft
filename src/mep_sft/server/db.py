"""SQLite store for the review queue. Single reviewer, no auth (see PLAN section 8)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  id TEXT PRIMARY KEY, seed INTEGER, difficulty INTEGER, variant TEXT,
  scene_hash TEXT UNIQUE, scene_json TEXT, issue_types TEXT, violations_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending', approved_version INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS versions (
  sample_id TEXT, version INTEGER, fixed_pipes_json TEXT, assistant_text TEXT,
  source TEXT, note TEXT, created_at TEXT, PRIMARY KEY (sample_id, version)
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT, version INTEGER, decision TEXT,
  reason TEXT, override INTEGER DEFAULT 0, reviewer TEXT DEFAULT 'owner', created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_status ON samples(status, difficulty);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # -- samples
    def add_sample(self, sample: dict, variant: str, fixed_pipes: list, text: str) -> bool:
        """Insert as pending with version 1; False when the same scene is already stored."""
        with self.lock:
            if self.db.execute(
                "SELECT 1 FROM samples WHERE scene_hash=? OR id=?",
                (sample["scene_hash"], sample["id"]),
            ).fetchone():
                return False
            ts = now()
            self.db.execute(
                "INSERT INTO samples (id, seed, difficulty, variant, scene_hash, scene_json,"
                " issue_types, violations_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sample["id"], sample["seed"], sample["difficulty"], variant,
                 sample["scene_hash"], json.dumps(sample["scene"], sort_keys=True),
                 json.dumps(sorted({v["type"] for v in sample["violations"]})),
                 json.dumps(sample["violations"]), ts),
            )
            self.db.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?)",
                (sample["id"], 1, json.dumps(fixed_pipes), text, "auto", "solver", ts),
            )
            self.db.commit()
            return True

    def list_samples(self, status=None, difficulty=None, limit=100, offset=0) -> list[dict]:
        q, args = "SELECT * FROM samples WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        if difficulty:
            q += " AND difficulty=?"
            args.append(difficulty)
        q += " ORDER BY difficulty, id LIMIT ? OFFSET ?"
        with self.lock:
            rows = self.db.execute(q, [*args, limit, offset]).fetchall()
        return [self._summary(r) for r in rows]

    @staticmethod
    def _summary(r) -> dict:
        return {
            "id": r["id"], "seed": r["seed"], "difficulty": r["difficulty"],
            "variant": r["variant"], "status": r["status"],
            "issue_types": json.loads(r["issue_types"]),
            "approved_version": r["approved_version"],
        }

    def get_sample(self, sample_id: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
        if not r:
            return None
        out = self._summary(r)
        out["scene"] = json.loads(r["scene_json"])
        out["violations"] = json.loads(r["violations_json"])
        out["scene_hash"] = r["scene_hash"]
        return out

    # -- versions
    def versions(self, sample_id: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM versions WHERE sample_id=? ORDER BY version", (sample_id,)
            ).fetchall()
        return [
            {"version": r["version"], "fixed_pipes": json.loads(r["fixed_pipes_json"]),
             "assistant_text": r["assistant_text"], "source": r["source"], "note": r["note"],
             "created_at": r["created_at"]}
            for r in rows
        ]

    def add_version(self, sample_id: str, fixed_pipes: list, text: str, source: str,
                    note: str = "") -> int:
        """New version; any earlier approval is withdrawn because the content changed."""
        with self.lock:
            n = self.db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM versions WHERE sample_id=?", (sample_id,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?)",
                (sample_id, n, json.dumps(fixed_pipes), text, source, note, now()),
            )
            self.db.execute(
                "UPDATE samples SET status='pending', approved_version=NULL WHERE id=?",
                (sample_id,),
            )
            self.db.commit()
            return n

    # -- reviews
    def review(self, sample_id: str, version: int, decision: str, reason: str,
               override: bool) -> None:
        status = {"approve": "approved", "reject": "rejected", "reset": "pending"}[decision]
        with self.lock:
            self.db.execute(
                "INSERT INTO reviews (sample_id, version, decision, reason, override, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (sample_id, version, decision, reason, int(override), now()),
            )
            self.db.execute(
                "UPDATE samples SET status=?, approved_version=? WHERE id=?",
                (status, version if decision == "approve" else None, sample_id),
            )
            self.db.commit()

    def reviews(self, sample_id: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM reviews WHERE sample_id=? ORDER BY id", (sample_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def approved(self) -> list[dict]:
        with self.lock:
            ids = [r["id"] for r in self.db.execute(
                "SELECT id FROM samples WHERE status='approved' ORDER BY id").fetchall()]
        return [self.get_sample(i) for i in ids]

    def stats(self) -> dict:
        with self.lock:
            rows = self.db.execute(
                "SELECT status, difficulty, COUNT(*) c FROM samples GROUP BY status, difficulty"
            ).fetchall()
        by_status: dict[str, int] = {}
        by_diff: dict[str, dict[str, int]] = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + r["c"]
            by_diff.setdefault(str(r["difficulty"]), {})[r["status"]] = r["c"]
        return {"by_status": by_status, "by_difficulty": by_diff}
