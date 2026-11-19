// 引用するショート動画（TikTok など）をブラウザで開いて、映像を保存する。
//   node tiktok_grab.cjs <投稿の URL> <保存先.mp4> [--record 秒]
// 1. ページが読み込む動画のファイルを、同じブラウザの通信でそのまま保存する
// 2. 取れなければ、画面収録（動画の部分だけ）で保存する（--record の秒数。既定 12 秒）
// Playwright（npm i -g playwright && npx playwright install chromium）が要る。
// 環境変数: HTTPS_PROXY があれば通す。DOCU_BROWSER_SPKI は中継サーバーの証明書を信頼させるとき（クラウドの作業環境用）。
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const [url, out] = process.argv.slice(2);
  const ri = process.argv.indexOf('--record');
  const recordSec = ri > 0 ? Number(process.argv[ri + 1] || 12) : 12;
  const args = ['--autoplay-policy=no-user-gesture-required', '--mute-audio'];
  if (process.env.DOCU_BROWSER_SPKI) args.push('--ignore-certificate-errors-spki-list=' + process.env.DOCU_BROWSER_SPKI);
  const browser = await chromium.launch({
    headless: true, args,
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined,
  });
  const ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';
  const ctx = await browser.newContext({ viewport: { width: 540, height: 960 }, userAgent: ua });
  const page = await ctx.newPage();
  const urls = [];
  page.on('response', r => {
    const ct = r.headers()['content-type'] || '';
    if (ct.includes('video/mp4') && !r.url().includes('playback1.mp4') && !urls.includes(r.url())) urls.push(r.url());
  });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(10000);
  let best = null;
  for (const u of urls) {
    try {
      const res = await ctx.request.get(u, { headers: { Referer: new URL(url).origin + '/' }, timeout: 90000 });
      const b = await res.body();
      if (res.ok() && b.length > 100000 && (!best || b.length > best.length)) best = b;
    } catch (e) { /* 次の候補へ */ }
  }
  if (best) {
    fs.writeFileSync(out, best);
    console.log(JSON.stringify({ mode: 'download', bytes: best.length }));
    await browser.close();
    return;
  }
  // 画面収録: ログインの案内を閉じ、動画の部分の位置を記録して録る
  await ctx.close();
  const dir = path.dirname(out);
  const rctx = await browser.newContext({ viewport: { width: 540, height: 960 }, userAgent: ua,
    recordVideo: { dir, size: { width: 540, height: 960 } } });
  const rp = await rctx.newPage();
  await rp.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await rp.waitForTimeout(4000);
  for (const label of ['Skip', 'スキップ', 'Close', '閉じる']) {
    try { await rp.getByText(label, { exact: true }).first().click({ timeout: 1500 }); } catch (e) { /* 無ければ何もしない */ }
  }
  const box = await rp.evaluate(() => {
    const v = document.querySelector('video');
    if (!v) return null;
    const r = v.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  });
  await rp.waitForTimeout(recordSec * 1000);
  const video = rp.video();
  await rctx.close();
  const webm = video ? await video.path() : null;
  await browser.close();
  // 動画の部分が無い（非公開・削除など）ときは録ったものを使わない
  if (!webm || !box || box.w < 50 || box.h < 50) { console.log(JSON.stringify({ mode: 'none', reason: box ? 'small' : 'no-video' })); return; }
  console.log(JSON.stringify({ mode: 'record', webm, box, skip: 4 }));
})().catch(e => { console.log(JSON.stringify({ mode: 'error', error: String(e).slice(0, 300) })); process.exit(0); });
