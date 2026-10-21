// The room's inputs (recorder/static/inputs.js) on the four apps' pages, headless Chromium, muted, on the fake
// device with its four inputs (1 the piezo, 3 the Nadine, 4 the room mic recorded; 2 nothing named, hearing the fake
// note): the practice recorder on a phone (the recorded inputs named; Inputs… lists all four with live meters; input 2
// "hears something" and is named from "Record it"); the exercise app on an iPad (the name there; a new mic on input 3
// named from its sheet, which the practice page then shows by itself); a practice take with every recorded input, each
// named in its sidecar; the transcribe app (the bass's usual input the Nadine, by name: gone, so the piezo, and the page
// says so); the chord app (its chips the recorded inputs; its sheet). No console errors, no sideways scroll.
//   node tests/inputs_page.cjs PRACTICE_URL EXERCISES_URL CHORDS_URL TRANSCRIBE_URL SHOTS_DIR   (tests/test_inputs_page.py)
const { chromium } = require("playwright");
const [P, E, C, T, SHOTS] = process.argv.slice(2);
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); console.log((ok ? "ok   " : "FAIL ") + what); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sideways(page, where) {
  const r = await page.evaluate(() => {
    const box = document.querySelector(".ri-box"), body = document.querySelector(".ri-body");
    return { doc: document.documentElement.scrollWidth, w: innerWidth, box: box ? [box.scrollWidth, box.clientWidth] : null,
             body: body ? [body.scrollWidth, body.clientWidth] : null };
  });
  check(r.doc <= r.w, `${where}: no sideways scroll (${r.doc} <= ${r.w})`);
  if (r.box) check(r.box[0] <= r.box[1] && r.body[0] <= r.body[1], `${where}: the sheet fits (${r.box}, ${r.body})`);
}

(async () => {
  const browser = await chromium.launch({ args: ["--mute-audio", "--autoplay-policy=user-gesture-required"] });
  const errors = [];
  async function open(url, w, h, name) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: w < 1100, isMobile: w < 700 });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errors.push(`${name}: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`${name}: ${e}`));
    await page.addInitScript(() => {
      const mute = (el) => { el.muted = true; el.volume = 0; };
      document.addEventListener("play", (e) => mute(e.target), true);
      new MutationObserver(() => document.querySelectorAll("audio,video").forEach(mute)).observe(document, { childList: true, subtree: true });
    });
    await page.goto(url);
    return page;
  }

  // A. the practice recorder, on a phone
  const pr = await open(P, 390, 844, "practice");
  await pr.waitForSelector("[data-inp-strip] .ri-row");
  const names = await pr.$$eval("[data-inp-strip] .ri-row .ri-name", (els) => els.map((e) => e.textContent));
  check(JSON.stringify(names) === JSON.stringify(["In 1 · Piezo", "In 3 · Nadine", "In 4 · Room mic"]), `practice: the strip names the recorded inputs (${names})`);
  await pr.screenshot({ path: `${SHOTS}/practice-390-ready.png`, fullPage: true });
  await pr.click("[data-inp-strip] [data-inp-open]:not([data-inp-open^='1']):not([data-inp-open^='2'])");
  await pr.waitForSelector(".ri-sheet .ri-item");
  await sleep(1800);
  const st = await (await pr.request.get(P + "api/status")).json();
  check(Array.isArray(st.input_levels) && st.input_levels.length === 4, `practice: every input's level while the sheet is open (${st.input_levels})`);
  const w1 = await pr.$eval(".ri-item[data-n='1'] [data-inp-meter] i", (e) => parseFloat(e.style.width) || 0);
  check(w1 > 0, `practice: input 1's meter moves in the sheet (${w1}%)`);
  const items = await pr.$$eval(".ri-item .ri-title", (els) => els.map((e) => e.textContent));
  check(items.length === 4 && items[1] === "Nothing named", `practice: the sheet lists all four inputs (${items})`);
  await sideways(pr, "practice sheet 390");
  await pr.screenshot({ path: `${SHOTS}/practice-390-sheet.png` });
  await pr.click(".ri-close");
  await sleep(600);
  const hears = await pr.$$eval("[data-inp-strip] .ri-hears", (els) => els.map((e) => e.textContent));
  check(hears.some((t) => t.includes("In 2 hears something")), `practice: an input not recorded that hears something is said (${hears})`);
  // name it from "Record it"
  await pr.click("[data-inp-strip] [data-inp-open='2']");
  await pr.waitForSelector("#riName");
  await pr.fill("#riName", "Test mic");
  await pr.fill("#riDetails", "A small condenser on the piano");
  await pr.screenshot({ path: `${SHOTS}/practice-390-form.png` });
  await pr.click("[data-ri='save']");
  await pr.waitForFunction(() => [...document.querySelectorAll(".ri-item .ri-title")].some((e) => e.textContent === "Test mic"));
  const after = await (await pr.request.get(P + "api/inputs")).json();
  const two = after.inputs.find((i) => i.input === 2);
  check(two.name === "Test mic" && two.record && two.channel === 1, `practice: input 2 named and recorded, channel 1 (${JSON.stringify(two)})`);
  await pr.click(".ri-close");

  // B. the exercise app on an iPad: the name is there; a new mic on input 3, named from this page
  const ex = await open(E + "#on", 1180, 820, "exercises");
  await sleep(1500);
  const exin = await (await ex.request.get(E + "api/inputs")).json();
  check(exin.inputs.find((i) => i.input === 2).name === "Test mic", "exercises: the room's file has the practice page's name");
  await ex.screenshot({ path: `${SHOTS}/exercises-1180-on.png` });
  await ex.click("[data-inp-open]:not([data-inp-open^='1']):not([data-inp-open^='2']):not([data-inp-open^='3']):not([data-inp-open^='4'])");
  await ex.waitForSelector(".ri-item[data-n='3'] [data-ri='new']");
  await ex.click(".ri-item[data-n='3'] [data-ri='new']");
  await ex.fill("#riName", "Bluebird SL");
  await ex.fill("#riDetails", "Blue Bluebird SL, large-diaphragm condenser");
  await ex.click("[data-ri='save']");
  await ex.waitForFunction(() => [...document.querySelectorAll(".ri-item .ri-title")].some((e) => e.textContent === "Bluebird SL"));
  await sideways(ex, "exercises sheet 1180");
  await ex.screenshot({ path: `${SHOTS}/exercises-1180-sheet.png` });
  await ex.click(".ri-close");
  // the practice page sees it by itself (its status's inputs_version)
  await pr.waitForFunction(() => [...document.querySelectorAll("[data-inp-strip] .ri-row .ri-name")].some((e) => e.textContent === "In 3 · Bluebird SL"), null, { timeout: 8000 })
    .then(() => check(true, "practice: input 3's new name shows by itself"), () => check(false, "practice: input 3's new name shows by itself"));

  // a take on the practice recorder: every recorded input, each named
  await pr.click("#recBtn");
  await sleep(1500);
  const rowsW = await pr.$$eval("[data-inp-strip] .ri-row [data-inp-meter] i", (els) => els.map((e) => parseFloat(e.style.width) || 0));
  check(rowsW.length === 4 && rowsW[0] > 0, `practice: each recorded input's meter while recording (${rowsW})`);
  await pr.screenshot({ path: `${SHOTS}/practice-390-recording.png`, fullPage: true });
  await pr.click("#recBtn");
  await sleep(1200);
  const takes = await (await pr.request.get(P + "api/takes")).json();
  const ch = takes[0].channels || [];
  check(JSON.stringify(ch.map((c) => [c.channel, c.input, c.input_name])) === JSON.stringify([[0, 1, "Piezo"], [1, 2, "Test mic"], [2, 3, "Bluebird SL"], [3, 4, "Room mic"]]),
    `practice: the take's sidecar names each channel (${JSON.stringify(ch)})`);

  // C. the transcribe app on a phone: the bass's usual input is the Nadine, by name; it is not there, so the piezo
  const tr = await open(T + "#on", 390, 844, "transcribe");
  await tr.waitForSelector("[data-act='inst'][data-kind='bass']");
  await tr.click("[data-act='inst'][data-kind='bass']");
  await sleep(500);
  const setup = await (await tr.request.get(T + "api/setup")).json();
  const bass = setup.instruments.find((i) => i.kind === "bass"), dobro = setup.instruments.find((i) => i.kind === "dobro");
  check(bass.input === 1 && bass.found === "kind" && bass.input_name === "Nadine", `transcribe: the bass falls back to the piezo (${JSON.stringify(bass)})`);
  check(dobro.input === 4 && dobro.found === "name", `transcribe: the dobro on the room mic by name (${JSON.stringify(dobro)})`);
  const body = await tr.textContent("body");
  check(body.includes("No recorded input is called Nadine"), "transcribe: the page says the Nadine is not there");
  await sideways(tr, "transcribe on 390");
  await tr.screenshot({ path: `${SHOTS}/transcribe-390-on.png`, fullPage: true });

  // D. the chord app on a phone: it hears the recorded inputs, named; Inputs… opens the sheet
  const cd = await open(C, 390, 844, "chords");
  await cd.waitForSelector("#inputs .chip");
  const chips = await cd.$$eval("#inputs .chip", (els) => els.map((e) => e.textContent));
  check(chips.includes("Bluebird SL") && chips.includes("Test mic"), `chords: the chips name the recorded inputs (${chips})`);
  await cd.click("#inputs [data-inp-open]");
  await cd.waitForSelector(".ri-sheet .ri-item");
  await sideways(cd, "chords sheet 390");
  await cd.screenshot({ path: `${SHOTS}/chords-390-sheet.png` });

  check(errors.length === 0, `no console errors (${errors.join(" | ")})`);
  await browser.close();
  console.log(failures.length ? `${failures.length} FAILED` : "all ok");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
