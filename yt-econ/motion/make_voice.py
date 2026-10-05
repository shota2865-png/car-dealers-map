"""めたんの声（VOICEVOX 四国めたん）→ $MOTION_DIR/audio/v*.wav と lines.json（いつ話すか）."""
import io
import json
import os
import urllib.parse
import urllib.request
import wave

M = os.environ.get("MOTION_DIR", os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
LINES = [
    (26.6, "解析担当の、四国めたんです。"),
    (29.0, "よく聞く心理学の話を、研究のデータで、ひとつずつ確かめていきます。"),
    (34.4, "その思いこみ、データで確かめてみませんか。"),
    (56.0, "毎日20時に、お会いしましょう。"),
]
os.makedirs(f"{M}/audio", exist_ok=True)
out = []
for i, (t, txt) in enumerate(LINES):
    q = json.loads(urllib.request.urlopen(urllib.request.Request(f"{URL}/audio_query?speaker=2&text={urllib.parse.quote(txt)}", method="POST")).read())
    q.update(speedScale=1.05, intonationScale=1.15, prePhonemeLength=0.05, postPhonemeLength=0.1)
    wav = urllib.request.urlopen(urllib.request.Request(f"{URL}/synthesis?speaker=2", data=json.dumps(q).encode(),
                                                        headers={"Content-Type": "application/json"}, method="POST")).read()
    p = f"{M}/audio/v{i}.wav"
    open(p, "wb").write(wav)
    with wave.open(io.BytesIO(wav)) as w:
        out.append({"t": t, "text": txt, "file": p, "dur": w.getnframes() / w.getframerate()})
json.dump(out, open(f"{M}/audio/lines.json", "w"), ensure_ascii=False, indent=1)
