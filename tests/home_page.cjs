// The home page (home/static/index.html), headless Chromium, muted, with the room's nine tiles: the practice recorder a
// stand-in over HTTP, the contribution app a stand-in over HTTPS (its certificate for practice.local, which the
// browser is told to accept here), the rest not running. At a phone's and an iPad's sizes: the contribution app's tile
// links with https and reads "Ready" (its status asked by the home server over HTTPS), the others with http; on an
// iPad every tile in view; no sideways scroll; no console errors. Then its tile, tapped, opens the HTTPS page.
//   node tests/home_page.cjs HOME_URL HTTP_PORT HTTPS_PORT N_APPS SHOTS_DIR   (tests/test_home.py)
const { chromium } = require("playwright");
const [HOME, HTTP_PORT, HTTPS_PORT, N_APPS, SHOTS] = process.argv.slice(2);
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); console.log((ok ? "ok   " : "FAIL ") + what); };

(async () => {
  const browser = await chromium.launch({ args: ["--mute-audio", "--autoplay-policy=user-gesture-required"] });
  const errors = [];
  for (const [w, h] of [[390, 844], [820, 1180], [1180, 820]]) {
    const where = `${w}x${h}`;
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: true, isMobile: w < 700,
                                           ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errors.push(`${where}: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`${where}: ${e}`));
    await page.addInitScript(() => {
      const mute = (el) => { el.muted = true; el.volume = 0; };
      document.addEventListener("play", (e) => mute(e.target), true);
      new MutationObserver(() => document.querySelectorAll("audio,video").forEach(mute)).observe(document, { childList: true, subtree: true });
    });
    await page.goto(HOME);
    await page.waitForFunction(() => document.querySelector("#app-contribute .text") &&
      document.querySelector("#app-contribute .text").textContent === "Ready", null, { timeout: 10000 });
    const tiles = await page.$$eval("a.app", (els) => els.map((e) => ({
      id: e.id.slice(4), href: e.getAttribute("href"), text: e.querySelector(".text").textContent, cls: e.className,
      name: e.querySelector(".name").textContent, about: e.querySelector(".about").textContent,
      top: e.getBoundingClientRect().top, bottom: e.getBoundingClientRect().bottom })));
    check(tiles.length === Number(N_APPS), `${where}: ${tiles.length} tiles`);
    const c = tiles.find((t) => t.id === "contribute"), p = tiles.find((t) => t.id === "practice");
    check(c && c.href === `https://127.0.0.1:${HTTPS_PORT}/`, `${where}: Contribute links with https (${c && c.href})`);
    check(c && c.text === "Ready" && c.cls === "app" && c.name === "Contribute" && c.about.length > 0,
      `${where}: Contribute reads Ready, up (${c && [c.text, c.cls, c.about]})`);
    check(p && p.href === `http://127.0.0.1:${HTTP_PORT}/` && p.text === "Ready", `${where}: Practice links with http (${p && [p.href, p.text]})`);
    const others = tiles.filter((t) => t.id !== "contribute" && t.id !== "practice");
    check(others.every((t) => t.href.startsWith("http://") && t.text === "Not running" && t.cls.includes("down")),
      `${where}: the rest with http, not running`);
    if (w >= 700) {
      const low = Math.max(...tiles.map((t) => t.bottom));
      check(low <= h, `${where}: every tile on one screen (the lowest ends at ${Math.round(low)} of ${h})`);
    }
    const sw = await page.evaluate(() => document.documentElement.scrollWidth);
    check(sw <= w, `${where}: no sideways scroll (${sw} <= ${w})`);
    await page.screenshot({ path: `${SHOTS}/home-${where}.png`, fullPage: true });
    if (w === 820) {
      await Promise.all([page.waitForURL(`https://127.0.0.1:${HTTPS_PORT}/`), page.click("#app-contribute")]);
      const head = await page.textContent("h1");
      check(head === "Contribute", `${where}: the tile opens the app over HTTPS (${page.url()}, "${head}")`);
    }
    await ctx.close();
  }
  check(errors.length === 0, `no console errors (${errors.join("; ")})`);
  await browser.close();
  console.log(failures.length ? `${failures.length} failed` : "all ok");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
