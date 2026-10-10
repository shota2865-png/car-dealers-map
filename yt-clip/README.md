# yt-clip — マックスむらい 切り抜き Shorts の全自動投稿

むらいクリップ【切り抜き】（@maxmuraiclip / UCzHObRAWlTh97yX1XSfumcw）に、毎日 10 本の Shorts を **18:00（日本時間）に予約投稿**する。
GitHub Actions（`.github/workflows/clip-daily.yml`）が毎朝 9:20 ごろに動くので、Mac も Claude も起動していなくていい。

## Claude のクレジットは使わない

| 工程 | やり方 | 費用 |
|---|---|---|
| 新しい素材 | 宇宙株LIVE・本チャンネルの RSS（直近 4 日） | 無料 |
| 盛り上がり探し | 生配信＝チャットの勢い（「ｗ」「草」・スパチャは重め）／人気動画＝よく見返された場所（ヒートマップ）／それ以外＝自動字幕の [笑い] | 無料 |
| つなぐ場所・タイトル・見出し | Google の Gemini API の無料枠（鍵は Secret `GEMINI_API_KEY`。GitHub Models は 2026-07-30 に終了）が、山の前後 3 分ほどの字幕から「起承転結になる区間」を 2〜8 個選ぶ（ジャンプカット）。小見出しは文章（例「果たしてバレるのか！？」）。使えない日は山の周りを間（ま）で刻んでつなぐ | 無料 |
| 縦型にする | 黒帯（番組名）→ 水色の帯（タイトル）→ 映像 → 濃紺の背景。生配信は顔カメラを横長のまま映し、話している人のほうへ画角を寄せる（顔検出＋口の動き。1 人のときはカットごとに引きと寄りを入れ替える）。チャート・画面の話の区間だけ画面全体に切り替える。字幕は生配信だけ（映像の下に色つきの箱・1 行 12 字まで・文節で区切る。文字は自動字幕、時刻は切り抜いた音を Whisper で聞き取り直して話し始めに合わせる）。かたまり全体は上下の余白が同じになる高さに置く。生配信でない動画は字幕なしでタイトルと小見出しだけ。字は角ゴシックの極太 | 無料 |
| 投稿 | YouTube Data API（private＋publishAt で予約） | 無料 |

1 日の内訳は「新しい配信から 7 本＋パズドラ・モンスト時代などのアーカイブから 3 本」（`config.yaml` の `archive_per_day`）。
新しい素材が足りない日はアーカイブで埋める。同じ場面は二度出さない（`state/state.json`、Actions のキャッシュで持ち越し）。

## ガイドライン（2026-05-20 改訂）への対応

- 説明欄に決まりのクレジットを毎回入れる（`config.yaml` の `upload.credit`。切り抜き元 URL は秒数つき）
- 対象は @TheMaxMurai / @MaxMuraiSpaceLIVE / @makusonsaidai だけ。Shorts・メンバー限定・「切り抜き禁止」の題名は取らない
- 無編集の転載はしない（縦型の再構成・字幕・冒頭の一言を必ず付ける）。投資の話には「個人の見解」の注意書き
- 事前登録は 2026-10-10 に済み。**収益化（YPP）に入ったら事務局（get-clip@razil.jp）へ連絡**

## 初回だけの設定

1. **鍵を取る**（むらいクリップのアカウントで許可する）
   - `python3 yt-clip/scripts/auth.py`（追加のインストール不要）
   - クライアント ID／シークレットは Google Cloud → 認証情報 → OAuth クライアント（経済と同じもので可。種類は「デスクトップ」）
   - ブラウザで **むらいクリップのチャンネルがある Google アカウント**を選んで許可。最後に許可したチャンネル名が出るので確認
   - OAuth 同意画面が「テスト」のままなら、そのアカウントをテストユーザーに追加する（本番公開にしておくと鍵が 7 日で切れない）
2. **GitHub の Secrets に入れる**: `CLIP_YOUTUBE_CLIENT_ID` / `CLIP_YOUTUBE_CLIENT_SECRET` / `CLIP_YOUTUBE_REFRESH_TOKEN`、
   それと `GEMINI_API_KEY`（https://aistudio.google.com/apikey で作る無料の鍵）
3. Actions →「毎日 マックスむらい切り抜き…」→ Run workflow（upload を外すと作るだけ）で試運転

鍵が違うチャンネルのものだと、投稿の前に止まる（経済のチャンネルに誤投稿しない）。

## 困ったとき

- **「Sign in to confirm you're not a bot」で失敗する** → GitHub のサーバーが YouTube に弾かれている。
  捨ての Google アカウントでログインしたブラウザから cookies.txt を書き出し、Secret `YTDLP_COOKIES` に中身を貼る
- **タイトルが字幕の切れ端っぽい** → LLM が使えなかった日（ログに「LLM … に聞けませんでした」。無料枠の回数切れなど）。翌日は戻る
- 本数・時刻・取る元を変える → `config.yaml`（`schedule.per_day` / `publish_times`、`sources`）

## 手元で試す

```
cd yt-clip && pip install -r requirements.txt
python -m clip --no-upload --count 2 --state /tmp/clip-state.json   # out/ に mp4 ができる
```
