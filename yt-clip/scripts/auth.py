#!/usr/bin/env python3
"""むらいクリップの YouTube の鍵（refresh token）を取る。初回だけ手で 1 回.

    python3 yt-clip/scripts/auth.py

クライアント ID とシークレット（Google Cloud → 認証情報 → OAuth クライアント。経済と同じもので可）を聞かれるので貼る。
ブラウザが開いたら、むらいクリップのチャンネルがある Google アカウントで許可する。
最後に出る 3 つを GitHub の Secrets（CLIP_YOUTUBE_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN）に入れる。
"""

from __future__ import annotations

import getpass
import http.server
import json
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def main() -> int:
    cid = input("クライアント ID: ").strip()
    sec = getpass.getpass("クライアント シークレット（入力は表示されません）: ").strip()
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("許可を受け取りました。このタブは閉じて大丈夫です。".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "select_account consent", "state": state})
    t = threading.Thread(target=srv.handle_request)
    t.start()
    print("\nブラウザで許可してください（開かなければ下の URL を開く）:\n" + url + "\n")
    webbrowser.open(url)
    t.join()
    if got.get("state") != state or "code" not in got:
        print("許可を受け取れませんでした:", got.get("error", "不明"))
        return 1
    data = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": sec,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    tok = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data))
    if "refresh_token" not in tok:
        print("refresh token が返りませんでした。Google アカウントの「サードパーティのアクセス」からアプリを外してやり直してください")
        return 1
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {tok['access_token']}"})
    items = json.load(urllib.request.urlopen(req)).get("items", [])
    ch = f"{items[0]['snippet']['title']}（{items[0]['id']}）" if items else "（チャンネルなし）"
    print(f"\n許可したチャンネル: {ch}")
    if not items or items[0]["id"] != "UCzHObRAWlTh97yX1XSfumcw":
        print("※ むらいクリップ（UCzHObRAWlTh97yX1XSfumcw）ではありません。アカウント／チャンネルを選び直してください")
    print("\nGitHub → Settings → Secrets and variables → Actions に次の 3 つを入れてください:")
    print("  CLIP_YOUTUBE_CLIENT_ID      = （さっき貼ったクライアント ID）")
    print("  CLIP_YOUTUBE_CLIENT_SECRET  = （さっき貼ったシークレット）")
    print(f"  CLIP_YOUTUBE_REFRESH_TOKEN  = {tok['refresh_token']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
