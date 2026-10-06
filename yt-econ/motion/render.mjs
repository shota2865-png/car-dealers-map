// node render.mjs <root> <page> <outdir> <fps> <from_frame> <to_frame>  または  times=1,2,3
import { chromium } from 'playwright-core';
import http from 'http'; import fs from 'fs'; import path from 'path';
const [root, page_, outdir, fpsS, a, b] = process.argv.slice(2);
const fps = +fpsS;
const port = 8700 + Math.floor(Math.random() * 200);
const types = {'.js':'text/javascript','.mjs':'text/javascript','.html':'text/html','.png':'image/png','.json':'application/json'};
const srv = http.createServer((q, s) => { const f = path.join(root, decodeURIComponent(q.url.split('?')[0])); fs.readFile(f, (e, d) => { if (e) { s.writeHead(404); s.end(); return; } s.writeHead(200, {'Content-Type': types[path.extname(f)] || 'application/octet-stream'}); s.end(d); }); }).listen(port);
const br = await chromium.launch({executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']});
const VW = +(process.env.VW || 1920), VH = +(process.env.VH || 1080);   // 書き出しの大きさ（1440p なら VW=2560 VH=1440）
const p = await br.newPage({viewport: {width: VW, height: VH}});
p.on('pageerror', e => console.log('err:', e.message));
await p.goto(`http://127.0.0.1:${port}/${page_}${process.env.PAGEQ || ''}`); await p.waitForFunction('window.ready===true', {timeout: 120000});
await p.waitForTimeout(1500);
fs.mkdirSync(outdir, {recursive: true});
let list = [];
if (a.startsWith('times=')) list = a.slice(6).split(',').map(x => [Math.round(+x * fps), +x]);
else for (let i = +a; i < +b; i++) list.push([i, i / fps]);
const meta = {};
const t0 = Date.now();
// META_ONLY=1: 画像は撮らず、3D の点の位置（meta）だけ / SKIP_EXISTING=1: もうあるコマは飛ばす（途中で止まったときの続き）
const metaPath = `${outdir}/meta_${a.replace(/[^0-9]/g, '').slice(0, 12) || '0'}_${b || ''}.json`;
let n = 0;
for (const [i, t] of list) {
  const png = `${outdir}/f${String(i).padStart(5, '0')}.png`;
  if (process.env.SKIP_EXISTING && fs.existsSync(png)) continue;
  meta[i] = await p.evaluate(t => window.renderAt(t), t);
  if (!process.env.META_ONLY) await p.screenshot({path: png});
  if (++n % 30 === 0) fs.writeFileSync(metaPath, JSON.stringify(meta));   // 途中で落ちても位置は残る
}
fs.writeFileSync(metaPath, JSON.stringify(meta));
console.log('frames', list.length, 'ms/frame', Math.round((Date.now() - t0) / list.length));
await br.close(); srv.close();
