// TikTok の投稿・タグのページから、投稿文と数字（再生・いいね）を読む。
//   node tiktok_info.cjs <投稿の URL ...>        → 1 行に 1 つ JSON {url, author, desc, plays, likes, comments, date}
//   node tiktok_info.cjs --tag <タグの URL>      → そのページにある投稿の URL を JSON の配列で
// Playwright が要る。HTTPS_PROXY / DOCU_BROWSER_SPKI は tiktok_grab.cjs と同じ。
const { chromium } = require('playwright');

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';

async function launch() {
  const args = [];
  if (process.env.DOCU_BROWSER_SPKI) args.push('--ignore-certificate-errors-spki-list=' + process.env.DOCU_BROWSER_SPKI);
  return chromium.launch({ headless: true, args, proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined });
}

(async () => {
  const argv = process.argv.slice(2);
  const browser = await launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1600 }, userAgent: UA });
  if (argv[0] === '--tag') {
    const p = await ctx.newPage();
    await p.goto(argv[1], { waitUntil: 'domcontentloaded', timeout: 60000 });
    await p.waitForTimeout(8000);
    for (let i = 0; i < 3; i++) { await p.mouse.wheel(0, 2500); await p.waitForTimeout(2000); }
    const html = await p.content();
    const urls = [...new Set(html.match(/https:\/\/www\.tiktok\.com\/@[\w.]+\/video\/\d+/g) || [])];
    console.log(JSON.stringify(urls));
    await browser.close();
    return;
  }
  for (const url of argv) {
    const p = await ctx.newPage();
    try {
      await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await p.waitForTimeout(3000);
      const info = await p.evaluate(() => {
        const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
        if (!el) return null;
        const d = JSON.parse(el.textContent);
        const it = (((d.__DEFAULT_SCOPE__ || {})['webapp.video-detail'] || {}).itemInfo || {}).itemStruct;
        if (!it) return null;
        const st = it.stats || {};
        return { author: (it.author || {}).uniqueId, desc: it.desc, plays: st.playCount, likes: st.diggCount,
                 comments: st.commentCount, date: it.createTime ? new Date(it.createTime * 1000).toISOString().slice(0, 10) : null,
                 seconds: (it.video || {}).duration };
      });
      console.log(JSON.stringify({ url, ...(info || { error: 'no-data' }) }));
    } catch (e) {
      console.log(JSON.stringify({ url, error: String(e).slice(0, 120) }));
    }
    await p.close();
  }
  await browser.close();
})();
