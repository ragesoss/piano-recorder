// The transcribe app's score in headless Chrome (Playwright), muted: a take drawn at one size, a bar tapped (the
// take plays from it, the cursor on it), then the window at another (a phone or an iPad turned). At both: every bar
// and everything drawn inside the score's card, nothing of the score in the page's cursor, no sideways scroll; a tap
// plays the take from that bar, the cursor in it. No console errors.
//   node tests/transcribe_page.cjs URL TAKE_ID FROM_SIZE TO_SIZE SHOTS_DIR      (tests/test_transcribe_page.py runs it)
const { chromium } = require("playwright");

const [url, take, from, to, shots] = process.argv.slice(2);
const size = (s) => { const [width, height] = s.split("x").map(Number); return { width, height }; };
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); };

(async () => {
  const browser = await chromium.launch({ args: ["--mute-audio", "--autoplay-policy=no-user-gesture-required"] });
  const first = size(from);
  const ctx = await browser.newContext({ viewport: first, hasTouch: true, isMobile: Math.min(first.width, first.height) < 700, deviceScaleFactor: 1 });
  await ctx.addInitScript(() => {  // nothing may sound: every media element muted, at volume 0
    const quiet = (m) => { try { m.muted = true; m.volume = 0; } catch (e) {} };
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () { quiet(this); return play.call(this); };
    document.addEventListener("DOMContentLoaded", () => document.querySelectorAll("audio, video").forEach(quiet));
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => typeof S !== "undefined" && S.takes && S.takes.length > 0, null, { timeout: 15000 });
  await page.evaluate((id) => openTake(id, "takes"), take);
  // drawn at the width it is shown at, at the scale for it
  const settled = () => page.waitForFunction((id) => {
    const el = document.querySelector("#scoreHost > div");
    return SC.take === id && SC.ready && el && SC.api.renderer.width === el.offsetWidth && SC.api.settings.display.scale <= scaleFor() + 1e-9;
  }, take, { timeout: 30000 });

  const fits = async (where) => {
    await page.waitForTimeout(600);
    const r = await page.evaluate(() => {
      const card = document.querySelector(".score-card").getBoundingClientRect();
      const surface = document.querySelector("#scoreHost .at-surface").getBoundingClientRect();
      let bars = -1e9, n = 0;
      SC.api.renderer.boundsLookup.staffSystems.forEach((sys) => sys.bars.forEach((mb) => {
        n++;
        bars = Math.max(bars, surface.left + Math.max(mb.realBounds.x + mb.realBounds.w, mb.visualBounds.x + mb.visualBounds.w) - card.right);
      }));
      let drawn = -1e9;
      document.querySelectorAll("#scoreHost svg").forEach((s) => { drawn = Math.max(drawn, s.getBoundingClientRect().right - card.right); });
      const body = document.getElementById("takeBody");
      return { n, bars, drawn, inCursor: document.querySelectorAll("#scoreHost .at-cursor svg, #scoreHost .at-bar svg").length,
        doc: document.documentElement.scrollWidth - innerWidth, body: body.scrollWidth - body.clientWidth, rows: SC.api.renderer.boundsLookup.staffSystems.length };
    });
    check(r.n > 0, `${where}: no bars drawn`);
    check(r.bars <= 1, `${where}: a bar runs ${Math.round(r.bars)} px past the card's edge`);
    check(r.drawn <= 1, `${where}: the drawing runs ${Math.round(r.drawn)} px past the card's edge`);
    check(r.inCursor === 0, `${where}: the score drawn in the take's cursor (${r.inCursor})`);
    check(r.doc <= 0 && r.body <= 0, `${where}: the page scrolls sideways (${r.doc}, ${r.body})`);
    return r;
  };
  const tap = async (where) => {  // the first row's last bar: the take plays from it, the cursor in it
    await page.evaluate(() => {  // the first row in view
      const body = document.getElementById("takeBody"), sys = SC.api.renderer.boundsLookup.staffSystems[0];
      const s = document.querySelector("#scoreHost .at-surface").getBoundingClientRect();
      body.scrollTop += s.top + sys.realBounds.y - body.getBoundingClientRect().top - 20;
    });
    await page.waitForTimeout(200);
    const t = await page.evaluate(() => {
      const sys = SC.api.renderer.boundsLookup.staffSystems[0], mb = sys.bars[sys.bars.length - 1];
      const s = document.querySelector("#scoreHost .at-surface").getBoundingClientRect();
      const bar = SC.bars.find((b) => b.tick === SC.api.score.masterBars[mb.index].start);
      return { x: s.left + mb.realBounds.x + mb.realBounds.w / 2, y: s.top + sys.realBounds.y + sys.realBounds.h / 2,
        from: Math.max(0, timeAt(bar.tick / 960) - 0.12), bar, n: mb.index + 1 };
    });
    await page.mouse.click(t.x, t.y);
    await page.waitForTimeout(700);
    const p = await page.evaluate(() => {
      const a = document.getElementById("player"), c = document.querySelector("#scoreHost .at-cursor");
      const o = typeof surfaceOffset === "function" ? surfaceOffset() : { x: 0 };
      return { at: a.currentTime * (P.rate || 1), paused: a.paused, quiet: a.muted && a.volume === 0,
        cursor: c && c.style.display === "block" ? parseFloat(c.style.left) - o.x : null };
    });
    await page.evaluate(() => document.getElementById("player").pause());
    check(p.quiet, `${where}: the take was not muted`);
    check(!p.paused && p.at >= t.from - 0.05 && p.at <= t.from + 1.5, `${where}: a tap on bar ${t.n} played from ${p.at.toFixed(2)} s, not ${t.from.toFixed(2)}`);
    check(p.cursor != null && p.cursor >= t.bar.x - 2 && p.cursor <= t.bar.x + t.bar.w + 2, `${where}: the cursor (${p.cursor}) is not in bar ${t.n} (${t.bar.x}-${t.bar.x + t.bar.w})`);
  };

  await settled();
  const a = await fits(from);
  await tap(from);
  await page.screenshot({ path: `${shots}/transcribe-${from}.png` });
  await page.setViewportSize(size(to));
  await settled();
  const b = await fits(`${from} turned to ${to}`);
  await tap(to);
  await page.screenshot({ path: `${shots}/transcribe-${from}-${to}.png` });
  check(errors.length === 0, `console errors: ${errors.join(" | ")}`);
  await browser.close();
  console.log(JSON.stringify({ from: a.rows, to: b.rows }));
  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
})().catch((e) => { console.error(e); process.exit(1); });
