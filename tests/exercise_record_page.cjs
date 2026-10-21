// The exercise app's recording screen (exercises/static/index.html), headless Chromium, muted, on the fake device: an
// exercise with its positions (tests/fixtures/dobro-002-g-scale.musicxml through tools/push-exercise: 33 bars on its
// pages) recorded at 240 on an iPad (1180x820): the crumbs gone and the score most of the screen; the cursor waiting
// on the first note in the count-in, then in the bar the page says ("Bar N"), in view as the score scrolls itself to
// the system played; Stop, the take, the crumbs back. Then a phone (390x844): no sideways scroll, Stop on screen.
// No console errors.
//   node tests/exercise_record_page.cjs URL EXERCISE_ID SHOTS_DIR   (tests/test_exercise_record_page.py)
const { chromium } = require("playwright");
const [U, EX, SHOTS] = process.argv.slice(2);
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); console.log((ok ? "ok   " : "FAIL ") + what); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ["--mute-audio", "--autoplay-policy=user-gesture-required"] });
  const errors = [];
  async function open(w, h, name) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: true, isMobile: w < 700 });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errors.push(`${name}: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`${name}: ${e}`));
    await page.addInitScript(() => {
      const mute = (el) => { el.muted = true; el.volume = 0; };
      document.addEventListener("play", (e) => mute(e.target), true);
      new MutationObserver(() => document.querySelectorAll("audio,video").forEach(mute)).observe(document, { childList: true, subtree: true });
    });
    await page.request.put(U + "api/choices", { data: { instruments: ["beard"], inputs: {}, kind: "exercise", exercise: EX,
      tempo: { [EX]: 240 }, countIn: true, clickThrough: true } });
    await page.goto(U);
    await page.waitForSelector("[data-act='go'][data-to='record'], .recbtn");
    await page.evaluate(() => { const b = document.querySelector("[data-act='go'][data-to='record']"); if (b) b.click(); });
    await page.waitForSelector(".recbtn:not([disabled])");
    return page;
  }
  const sample = (page) => page.evaluate(() => {
    const live = document.querySelector(".live"), cur = document.querySelector(".sc-cur"), hl = document.querySelector(".sc-bar");
    const r = (el) => { const b = el.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, h: b.height }; };
    return { where: (document.querySelector("[data-where]") || {}).textContent || "", shown: !!cur && cur.style.display === "block",
             wait: !!cur && cur.classList.contains("wait"), bar: hl ? Number(hl.dataset.bar) : null, cur: cur ? r(cur) : null,
             live: live ? r(live) : null, scroll: live ? live.scrollTop : 0, top: getComputedStyle(document.querySelector(".top")).display,
             inner: innerHeight };
  });

  // A. an iPad, landscape
  const ip = await open(1180, 820, "ipad");
  check(await ip.$$eval("[data-cursor] .pg", (els) => els.length) >= 2, "record screen: the score's pages, each its own frame");
  await ip.click(".recbtn");
  let s = null, waited = false;
  for (let i = 0; i < 40 && !waited; i++) { s = await sample(ip); waited = s.shown && s.wait; if (!waited) await sleep(50); }
  check(waited && s.bar === 0, `count-in: the cursor waits on the first note (${JSON.stringify(s && { where: s.where, bar: s.bar })})`);
  check(s.top === "none" && s.live.h >= 0.85 * s.inner, `the crumbs gone, the score ${Math.round(s.live.h)} of ${s.inner} px`);
  await ip.screenshot({ path: `${SHOTS}/ipad-count-in.png` });
  const rows = [];
  for (let i = 0; i < 56; i++) { rows.push(await sample(ip)); await sleep(250); }  // 14 s: bars 1 to 14 or so at 240
  await ip.screenshot({ path: `${SHOTS}/ipad-recording.png` });
  const inBar = rows.filter((x) => /^Bar \d+$/.test(x.where) && x.shown && !x.wait);
  const exact = inBar.filter((x) => x.bar === Number(x.where.slice(4)) - 1).length;
  const near = inBar.filter((x) => Math.abs(x.bar - (Number(x.where.slice(4)) - 1)) <= 1).length;
  check(inBar.length >= 40 && near === inBar.length && exact >= 0.8 * inBar.length,
    `the cursor's bar is the bar the page says (${exact} exactly, ${near} within one, of ${inBar.length})`);
  const seen = inBar.filter((x) => x.cur.top >= x.live.top - 2 && x.cur.bottom <= x.live.bottom + 2).length;
  check(seen >= 0.95 * inBar.length, `the cursor in view (${seen} of ${inBar.length})`);
  const scrolled = Math.max(...rows.map((x) => x.scroll));
  check(scrolled > 100, `the score scrolled itself to the system played (${Math.round(scrolled)} px)`);
  const bars = new Set(inBar.map((x) => x.bar));
  check(bars.size >= 10, `the cursor went through the bars (${bars.size})`);
  await ip.click("[data-act='stop-rec']");
  await ip.waitForFunction(() => !document.querySelector(".sc-cur") || document.querySelector(".sc-cur").style.display === "none", null, { timeout: 8000 })
    .catch(() => {});
  await ip.waitForSelector(".done-head", { timeout: 15000 });
  s = await sample(ip);
  check(s.top !== "none", "after the take: the crumbs back");

  // B. a phone: the slim bar wraps; no sideways scroll; Stop on screen
  const ph = await open(390, 844, "phone");
  await ph.click(".recbtn");
  await sleep(2500);
  const fit = await ph.evaluate(() => {
    const stop = document.querySelector("[data-act='stop-rec']").getBoundingClientRect();
    return { doc: document.documentElement.scrollWidth, w: innerWidth, stop: stop.bottom <= innerHeight && stop.top >= 0 };
  });
  check(fit.doc <= fit.w && fit.stop, `phone: no sideways scroll, Stop on screen (${JSON.stringify(fit)})`);
  s = await sample(ph);
  check(s.shown, "phone: the cursor");
  await ph.screenshot({ path: `${SHOTS}/phone-recording.png` });
  await ph.click("[data-act='stop-rec']");
  await ph.waitForSelector(".done-head", { timeout: 15000 });

  check(errors.length === 0, `no console errors (${errors.join(" | ")})`);
  await browser.close();
  console.log(failures.length ? `${failures.length} FAILED` : "all ok");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
