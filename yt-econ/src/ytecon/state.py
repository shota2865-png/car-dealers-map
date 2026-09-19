"""SQLite による進行状態の保存.

「どの話題を使ったか」「どの動画がどこまで進んだか」を永続化する。
これがあるので途中で落ちても `run` をもう一度叩けば続きから再開できる。
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    angle       TEXT,
    kind        TEXT,                -- news | evergreen
    source_json TEXT,
    score       REAL,
    created_at  REAL NOT NULL,
    used_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_topics_created ON topics(created_at);

CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT UNIQUE NOT NULL,
    topic_id     INTEGER,
    title        TEXT,
    status       TEXT NOT NULL,      -- planned/scripted/voiced/rendered/uploaded/failed
    stage_data   TEXT,               -- 各工程の成果物パスなど
    youtube_id   TEXT,
    publish_at   TEXT,
    error        TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    FOREIGN KEY(topic_id) REFERENCES topics(id)
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS quota (
    day    TEXT PRIMARY KEY,
    units  INTEGER NOT NULL DEFAULT 0
);
"""

STATUSES = ("planned", "scripted", "voiced", "rendered", "uploaded", "failed")


@dataclass
class VideoRecord:
    id: int
    slug: str
    topic_id: int | None
    title: str
    status: str
    stage: dict[str, Any]
    youtube_id: str | None
    publish_at: str | None
    error: str | None


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    # --- topics --------------------------------------------------------
    def recent_topic_titles(self, days: int) -> list[str]:
        since = time.time() - days * 86400
        rows = self._conn.execute(
            "SELECT title FROM topics WHERE created_at >= ?", (since,)
        ).fetchall()
        return [r["title"] for r in rows]

    def add_topic(
        self,
        title: str,
        angle: str,
        kind: str,
        sources: list[dict[str, Any]] | None = None,
        score: float = 0.0,
    ) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO topics(title, angle, kind, source_json, score, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (title, angle, kind, json.dumps(sources or [], ensure_ascii=False),
                 score, time.time()),
            )
        return int(cur.lastrowid)

    def mark_topic_used(self, topic_id: int) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE topics SET used_at=? WHERE id=?", (time.time(), topic_id))

    # --- videos --------------------------------------------------------
    def create_video(self, slug: str, topic_id: int | None, title: str) -> int:
        now = time.time()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO videos(slug, topic_id, title, status, stage_data,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (slug, topic_id, title, "planned", "{}", now, now),
            )
        return int(cur.lastrowid)

    def get_video(self, slug: str) -> VideoRecord | None:
        row = self._conn.execute("SELECT * FROM videos WHERE slug=?", (slug,)).fetchone()
        return _to_record(row) if row else None

    def update_video(self, slug: str, **fields: Any) -> None:
        stage = fields.pop("stage", None)
        if stage is not None:
            current = self.get_video(slug)
            merged = dict(current.stage) if current else {}
            merged.update(stage)
            fields["stage_data"] = json.dumps(merged, ensure_ascii=False)
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._tx() as conn:
            conn.execute(f"UPDATE videos SET {cols} WHERE slug=?",
                         (*fields.values(), slug))

    def videos_by_status(self, *statuses: str) -> list[VideoRecord]:
        marks = ",".join("?" * len(statuses))
        rows = self._conn.execute(
            f"SELECT * FROM videos WHERE status IN ({marks}) ORDER BY created_at",
            statuses,
        ).fetchall()
        return [_to_record(r) for r in rows]

    def uploaded_count_today(self, day: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM videos WHERE status='uploaded'"
            " AND date(updated_at,'unixepoch','localtime')=?",
            (day,),
        ).fetchone()
        return int(row["n"])

    # --- quota ---------------------------------------------------------
    def add_quota(self, day: str, units: int) -> int:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO quota(day, units) VALUES(?, ?)"
                " ON CONFLICT(day) DO UPDATE SET units = units + excluded.units",
                (day, units),
            )
        row = self._conn.execute("SELECT units FROM quota WHERE day=?", (day,)).fetchone()
        return int(row["units"])

    def quota_used(self, day: str) -> int:
        row = self._conn.execute("SELECT units FROM quota WHERE day=?", (day,)).fetchone()
        return int(row["units"]) if row else 0


def _to_record(row: sqlite3.Row) -> VideoRecord:
    return VideoRecord(
        id=row["id"],
        slug=row["slug"],
        topic_id=row["topic_id"],
        title=row["title"] or "",
        status=row["status"],
        stage=json.loads(row["stage_data"] or "{}"),
        youtube_id=row["youtube_id"],
        publish_at=row["publish_at"],
        error=row["error"],
    )
