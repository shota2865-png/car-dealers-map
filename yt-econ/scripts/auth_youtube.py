#!/usr/bin/env python3
"""YouTube の refresh token を取得する（初回だけ手で1回実行）.

    python scripts/auth_youtube.py

ブラウザが開くので、投稿したいチャンネルの Google アカウントで許可する。
表示された refresh token を .env の YOUTUBE_REFRESH_TOKEN に貼れば、
以降はサーバでも CI でも無人でアップロードできる。

事前準備（Google Cloud Console）:
  1. プロジェクトを作る
  2. 「YouTube Data API v3」を有効化
  3. OAuth 同意画面を作る（外部／テストユーザーに自分を追加）
  4. 認証情報 → OAuth クライアント ID → アプリの種類「デスクトップ」
  5. クライアント ID とシークレットを .env に入れる
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    # ytecon goal が日別の再生・総再生時間・視聴率を取るのに使う（無くても投稿はできる）
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("YOUTUBE_CLIENT_ID と YOUTUBE_CLIENT_SECRET を .env に設定してください")
        return 1

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )
    # ブラウザが開けない環境（SSH 先など）ではコンソール方式に落ちる
    try:
        creds = flow.run_local_server(port=0, prompt="consent",
                                      access_type="offline")
    except Exception:
        creds = flow.run_console()

    if not creds.refresh_token:
        print("refresh token が返りませんでした。"
              "Google アカウントの『サードパーティ アクセス』から本アプリを削除し、"
              "もう一度実行してください。")
        return 1

    print("\n" + "=" * 60)
    print("以下を .env に貼ってください:\n")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
