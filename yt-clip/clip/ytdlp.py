"""yt-dlp の共通の引数（GitHub のサーバーで弾かれたときだけ cookies を使う）."""

from __future__ import annotations

import os


def base() -> list[str]:
    cmd = ["yt-dlp", "--quiet", "--no-warnings", "--retries", "5", "--sleep-requests", "1"]
    cookies = os.environ.get("YTDLP_COOKIES_FILE")
    if cookies and os.path.exists(cookies):
        cmd += ["--cookies", cookies]
    return cmd
