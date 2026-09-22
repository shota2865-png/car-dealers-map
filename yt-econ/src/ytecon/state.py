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

-- 海外で先行している話題が日本に降りてくるまでの時差を扱うためのテーブル。
-- 「いつ刺さるか」を記録しておき、実際に日本で話題化したら掘り起こす。
CREATE TABLE IF NOT EXISTS revivals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL,
    matched     TEXT,               -- 反応したキーワード
    headline    TEXT,               -- 検知した見出し
    source_url  TEXT,
    applied     INTEGER NOT NULL DEFAULT 0,
    plan_json   TEXT,
    created_at  REAL NOT NULL,
    FOREIGN KEY(video_id) REFERENCES videos(id)
);
CREATE INDEX IF NOT EXISTS idx_revivals_video ON revivals(video_id);

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

# 日本での普及段階。海外発の話題は S0 から順に降りてくる
DIFFUSION_STAGES = {
    0: "海外のみ。日本ではまだ誰も話していない",
    1: "感度の高い一部の層が知り始めた",
    2: "日本のメディアが報じ始めた",
    3: "一般化して既出。競合が多い",
}

# 企画の賞味期限。ポートフォリオを組むときの単位
HORIZONS = ("flow", "bridge", "stock")

# あとから足した列。既存 DB でも起動時に自動で追加される
_ADDED_COLUMNS = {
    "topics": [
        ("horizon", "TEXT"),            # flow | bridge | stock
        ("diffusion_stage", "INTEGER"),  # 0..3
        ("lag_months", "REAL"),          # 日本で一般化するまでの推定ヶ月数
        ("watch_json", "TEXT"),          # 日本で話題化したら見出しに出る語
    ],
    "videos": [
        ("horizon", "TEXT"),
        ("revived_at", "REAL"),
    ],
}


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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """後から増えた列を既存 DB にも足す（作り直さなくて済むように）."""
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for name, sql_type in columns:
                if name not in existing:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
                    )

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
        horizon: str = "flow",
        diffusion_stage: int = 3,
        lag_months: float = 0.0,
        watch_keywords: list[str] | None = None,
    ) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO topics(title, angle, kind, source_json, score, created_at,"
                " horizon, diffusion_stage, lag_months, watch_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (title, angle, kind, json.dumps(sources or [], ensure_ascii=False),
                 score, time.time(), horizon, diffusion_stage, lag_months,
                 json.dumps(watch_keywords or [], ensure_ascii=False)),
            )
        return int(cur.lastrowid)

    def horizon_counts(self, days: int) -> dict[str, int]:
        """直近の企画が flow/bridge/stock にどう振れているか."""
        since = time.time() - days * 86400
        rows = self._conn.execute(
            "SELECT horizon, COUNT(*) AS n FROM topics"
            " WHERE created_at >= ? GROUP BY horizon",
            (since,),
        ).fetchall()
        counts = {h: 0 for h in HORIZONS}
        for row in rows:
            if row["horizon"] in counts:
                counts[row["horizon"]] = int(row["n"])
        return counts

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

    def watchlist(self) -> list[dict[str, Any]]:
        """公開済みのうち、日本での話題化を待っている動画とその監視語."""
        rows = self._conn.execute(
            "SELECT v.id AS video_id, v.slug, v.title, v.youtube_id, v.revived_at,"
            "       t.watch_json, t.horizon, t.lag_months, t.created_at"
            "  FROM videos v JOIN topics t ON v.topic_id = t.id"
            " WHERE v.status = 'uploaded' AND v.youtube_id IS NOT NULL"
            "   AND t.horizon IN ('stock','bridge')"
            " ORDER BY t.created_at",
        ).fetchall()
        out = []
        for row in rows:
            keywords = json.loads(row["watch_json"] or "[]")
            if keywords:
                out.append({
                    "video_id": row["video_id"],
                    "slug": row["slug"],
                    "title": row["title"],
                    "youtube_id": row["youtube_id"],
                    "revived_at": row["revived_at"],
                    "keywords": keywords,
                    "horizon": row["horizon"],
                    "lag_months": row["lag_months"],
                    "published_at": row["created_at"],
                })
        return out

    def add_revival(self, video_id: int, matched: str, headline: str,
                    source_url: str, plan: dict[str, Any] | None = None) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO revivals(video_id, matched, headline, source_url,"
                " plan_json, created_at) VALUES(?,?,?,?,?,?)",
                (video_id, matched, headline, source_url,
                 json.dumps(plan or {}, ensure_ascii=False), time.time()),
            )
        return int(cur.lastrowid)

    def revival_seen(self, video_id: int, matched: str) -> bool:
        """同じ動画×同じキーワードで二度通知しないための確認."""
        row = self._conn.execute(
            "SELECT 1 FROM revivals WHERE video_id=? AND matched=? LIMIT 1",
            (video_id, matched),
        ).fetchone()
        return row is not None

    def revived_recently(self, video_id: int, within_days: float) -> bool:
        """直近で掘り起こし済みか.

        1本の動画には監視語を複数持たせるので、キーワード単位で判定すると
        同じニュースに別の語が反応して二度三度鳴る。動画単位で抑える。
        """
        since = time.time() - within_days * 86400
        row = self._conn.execute(
            "SELECT 1 FROM revivals WHERE video_id=? AND created_at >= ? LIMIT 1",
            (video_id, since),
        ).fetchone()
        return row is not None

    def mark_revival_applied(self, revival_id: int, video_id: int) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE revivals SET applied=1 WHERE id=?", (revival_id,))
            conn.execute("UPDATE videos SET revived_at=? WHERE id=?",
                         (time.time(), video_id))

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
