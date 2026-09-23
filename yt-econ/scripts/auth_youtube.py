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

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    # 字幕ファイル（CC）の登録に要る（captions.insert は youtube だけでは 403）
    "https://www.googleapis.com/auth/youtube.force-ssl",
    # ytecon goal が日別の再生・総再生時間・視聴率を取るのに使う（無くても投稿はできる）
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def exchange_code(client_id: str, client_secret: str, code: str) -> int:
    """ブラウザが開けない環境用: 認可 URL を別の端末で開き、戻り先 URL の code をここに渡す.

        python scripts/auth_youtube.py --code 4/0A...   （code= の値、または戻り先 URL 全体）
    """
    import requests
    from urllib.parse import parse_qs, urlparse

    if "code=" in code:
        code = parse_qs(urlparse(code).query).get("code", [code])[0]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": "http://localhost", "grant_type": "authorization_code",
    }, timeout=30)
    d = r.json()
    if not r.ok or not d.get("refresh_token"):
        print(f"交換に失敗しました: {d.get('error')} {d.get('error_description', '')}")
        return 1
    out = Path(__file__).resolve().parents[1] / ".env"
    key = f"{PREFIX}YOUTUBE_REFRESH_TOKEN"
    lines = [ln for ln in (out.read_text().splitlines() if out.exists() else [])
             if not ln.startswith(key + "=")]
    lines.append(f"{key}={d['refresh_token']}")
    out.write_text("\n".join(lines) + "\n")
    print(f"refresh token を {out} に保存しました（スコープ: {d.get('scope', '')}）")
    return 0


PREFIX = ""


def main() -> int:
    global PREFIX
    args = sys.argv[1:]
    # --prefix PSY_ : 2 つ目のチャンネル用。PSY_YOUTUBE_CLIENT_ID / PSY_YOUTUBE_CLIENT_SECRET を読み、
    #                 PSY_YOUTUBE_REFRESH_TOKEN を書く（GitHub の Secrets にも同じ名前で入れる）
    if "--prefix" in args:
        i = args.index("--prefix")
        PREFIX = args[i + 1].strip() if i + 1 < len(args) else ""
        del args[i:i + 2]
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    client_id = os.environ.get(f"{PREFIX}YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get(f"{PREFIX}YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(f"{PREFIX}YOUTUBE_CLIENT_ID と {PREFIX}YOUTUBE_CLIENT_SECRET を .env に設定してください")
        return 1
    if len(args) >= 2 and args[0] == "--code":
        return exchange_code(client_id, client_secret, args[1])
    from google_auth_oauthlib.flow import InstalledAppFlow   # ブラウザを開く経路でだけ要る

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
    print(f"{PREFIX}YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
