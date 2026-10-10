"""切り抜き済みの場面と投稿履歴を JSON に持つ（Actions のキャッシュで日をまたいで持ち越す）."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"clips": [], "archive_done": []}
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def used_ranges(self, video_id: str) -> list[tuple[float, float]]:
        return [(c["start"], c["end"]) for c in self.data["clips"] if c["source"] == video_id]

    def overlaps(self, video_id: str, start: float, end: float, gap: float = 0) -> bool:
        return any(start < e + gap and end > s - gap for s, e in self.used_ranges(video_id))

    def add_clip(self, rec: dict[str, Any]) -> None:
        self.data["clips"].append(rec)

    def uploaded_on(self, day: str) -> int:
        return sum(1 for c in self.data["clips"] if c.get("day") == day and c.get("youtube_id"))
