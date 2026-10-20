// The board's page in headless Chrome (Playwright), at one screen size: the views, a card opened, moved with
// the sheet's buttons, a note, "Claude: do this next", a card added, filters and lanes kept over a reload, the
// older Done folded, a drag (with a mouse only); no console errors, no sideways scroll, 44 px targets.
//   node tests/board_page.cjs URL WIDTHxHEIGHT SHOTS_DIR      (tests/test_board_page.py runs it)
const { chromium } = require("playwright");

const [url, size, shots] = process.argv.slice(2);
const [W, H] = size.split("x").map(Number);
const touch = W < 1100;  // the iPad and the phone: no drag, the sheet's buttons
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); };
const tag = `${W}x${H}`;
const LANE = W <= 640 ? ".cell h3" : ".lane";  // lanes across the columns; on a phone (one column at a time), headings in it

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, hasTouch: touch, isMobile: W < 700, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date("2026-10-19T20:00:00"));
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  const shot = (name) => page.screenshot({ path: `${shots}/board-${tag}-${name}.png` });
  const changes = async () => (await (await page.request.get(url + "api/changes")).json()).changes;
  const sideways = async (where) => {
    const r = await page.evaluate(() => {
      const d = document.querySelector("dialog[open]");
      return { doc: document.documentElement.scrollWidth, body: document.body.scrollWidth, w: innerWidth,
        dlg: d ? [d.scrollWidth, d.clientWidth, d.querySelector(".sh-body") ? d.querySelector(".sh-body").scrollWidth - d.querySelector(".sh-body").clientWidth : 0] : null };
    });
    check(r.doc <= r.w && r.body <= r.w, `${where}: the page scrolls sideways (${r.doc}/${r.body} > ${r.w})`);
    if (r.dlg) check(r.dlg[0] <= r.dlg[1] && r.dlg[2] <= 0, `${where}: the sheet scrolls sideways (${r.dlg})`);
  };
  const targets = async (where) => {
    const small = await page.evaluate(() => {
      const root = document.querySelector("dialog[open]") || document;
      const els = [...root.querySelectorAll("button, select, input, textarea, .links a, a.home, summary")];
      return els.filter((e) => e.offsetParent !== null || e.closest("dialog[open]")).map((e) => {
        const r = e.getBoundingClientRect();
        return { t: (e.textContent || e.getAttribute("aria-label") || e.id || e.tagName).trim().slice(0, 30), h: r.height, w: r.width };
      }).filter((r) => r.h > 0 && (r.h < 43.5 || r.w < 43.5));
    });
    check(!small.length, `${where}: targets under 44 px: ${JSON.stringify(small.slice(0, 5))}`);
    const unnamed = await page.evaluate(() => [...document.querySelectorAll("button")].filter((b) => !(b.textContent.trim() || b.getAttribute("aria-label"))).length);
    check(!unnamed, `${where}: ${unnamed} buttons without a name`);
  };
  const view = async (id) => { await page.click(`#views [data-view="${id}"]`); await page.waitForSelector(`#views [data-view="${id}"][aria-pressed="true"]`); };
  const openCard = async (id) => {
    await page.click(`.card[data-id="${id}"]`);
    await page.waitForSelector("#sheet[open] .sh-body");
  };
  const closeSheet = async () => { await page.click("#sheet[open] [data-act=close]"); await page.waitForSelector("#sheet:not([open])", { state: "attached" }); };
  const jumpTo = async (col) => { if (touch) { await page.click(`#colnav [data-col="${col}"]`); await page.waitForTimeout(500); } };

  await page.goto(url);
  await page.waitForSelector(".card");
  await page.waitForSelector(".card.live");  // the live cards from the stand-ins for the review and exercise apps

  // the views: the five kinds of work and All, each with its count of open cards
  const names = await page.$$eval("#views button", (bs) => bs.map((b) => b.querySelector(".lbl").firstChild.textContent));
  check(JSON.stringify(names) === JSON.stringify(["Listen & evaluate", "Play & sing", "Manage the agent", "People", "Hardware", "All"]), `the views: ${names}`);
  check(await page.$$eval("#views button svg", (s) => s.length) === 6, "each view has its glyph");
  await shot("all");
  await sideways("all"); await targets("the board");
  const allCards = await page.$$eval(".card", (cs) => cs.map((c) => ({ work: c.dataset.work, chip: !!c.querySelector(".work"), imp: c.querySelector(".imp").textContent.trim() })));
  check(allCards.every((c) => c.chip), "in All, every card says its work");
  check(allCards.every((c) => ["High", "Medium", "Low"].includes(c.imp)), "every card says its importance in words");
  for (const id of ["listen", "play", "agent", "people", "hardware"]) {
    await view(id);
    const r = await page.evaluate((id) => {
      const cards = [...document.querySelectorAll(".card")];
      const n = +document.querySelector(`#views [data-view="${id}"] .n`).textContent;
      return { wrong: cards.filter((c) => c.dataset.work !== id).length, open: cards.filter((c) => c.dataset.status !== "done").length, n,
        live: cards.filter((c) => c.classList.contains("live")).length, chips: cards.filter((c) => c.querySelector(".work")).length };
    }, id);
    check(r.wrong === 0, `${id}: ${r.wrong} cards of another work`);
    check(r.open === r.n, `${id}: ${r.open} open cards shown, the view says ${r.n}`);
    check(r.chips === 0, `${id}: the work chip is left off (the view says it)`);
    if (id === "listen") {
      check(r.live === 1, `listen: the open review no card links to, live (${r.live})`);
      const on = await page.$eval('.card[data-id="guitalele-key"] .summary', (e) => e.textContent).catch(() => "");
      check(on.includes("Live") && on.includes("6 questions to answer"), `the review a card links to, on that card: ${on}`);
    }
    if (id === "play") check(r.live === 1, `play: the exercises to record, live (${r.live})`);
    if (["listen", "play", "agent"].includes(id)) await shot(id);
    await sideways(id);
  }
  await view("all");

  // a live card opens read-only, with its link
  await openCard("live:exercises");
  check(await page.$eval("#sheet .links a.big", (a) => a.href.endsWith("/")), "the exercise board's link");
  check(!(await page.$("#sheet [data-status]")), "a live card has no moves");
  await closeSheet();

  // open a card, move it with the sheet's buttons
  await openCard("record-pi-bass-takes");
  check((await page.textContent("#sheetTitle")).includes("Record the 11 bass takes"), "the sheet's title");
  check((await page.textContent("#sheet .md")).includes("4-17 gaps"), "the details");
  await shot("sheet");
  await sideways("the sheet"); await targets("the sheet");
  await page.click('#sheet [data-status="doing"]');
  await page.waitForSelector('#sheet [data-status="doing"][aria-pressed="true"]');
  check((await page.textContent("#sheet .sh-head")).includes("Not synced yet"), "the sheet says not synced");
  check((await page.textContent("#sheet .said")).includes("Moved to Doing"), "the sheet says what was done");
  let ch = await changes();
  check(ch.length === 1 && ch[0].card === "record-pi-bass-takes" && ch[0].set.status === "doing", `the move in the log: ${JSON.stringify(ch)}`);
  await closeSheet();
  check(await page.$('.cell[data-status="doing"] .card[data-id="record-pi-bass-takes"] .flag.unsynced'), "moved to Doing, marked not synced");
  await jumpTo("doing");
  await shot("moved");

  // a note
  await openCard("record-pi-bass-takes");
  await page.fill("#noteText", "The LIVA's T9 passed: record them on Saturday.");
  await page.click("#sheet [data-act=note]");
  await page.waitForSelector("#sheet .notes li");
  const note = await page.textContent("#sheet .notes li");
  check(note.includes("on Saturday") && note.includes("not synced yet"), `the note shown: ${note}`);
  check(await page.inputValue("#noteText") === "", "the note field emptied");
  ch = await changes();
  check(ch.length === 2 && ch[1].note === "The LIVA's T9 passed: record them on Saturday.", "the note in the log");
  await shot("note");
  await closeSheet();

  // "Claude: do this next": on someone else's card it adds Claude; on the agent's, it is the first action
  await openCard("dobro-vibrato-key");
  await page.click("#sheet [data-act=claude-next]");
  await page.waitForSelector("#sheet [data-act=claude-next]:disabled");
  check((await page.textContent("#sheet [data-act=claude-next]")).includes("Queued for Claude"), "queued");
  ch = await changes();
  const q = ch[ch.length - 1];
  check(q.card === "dobro-vibrato-key" && q.set.status === "next" && q.set.importance === 1 && JSON.stringify(q.set.who) === '["player","claude"]', `claude-next in the log: ${JSON.stringify(q)}`);
  await closeSheet();
  check(await page.$('.cell[data-status="next"] .card[data-id="dobro-vibrato-key"].imp1'), "in Next, high importance");
  await openCard("retro-record");
  const order = await page.$$eval("#sheet .sh-body > *", (els) => els.map((e) => e.className));
  check(order.indexOf("qnext") === 2, `on the agent's card "Claude: do this next" follows Move to (${order})`);
  await page.click("#sheet [data-act=claude-next]");
  await page.waitForSelector("#sheet [data-act=claude-next]:disabled");
  await shot("claude-next");
  await closeSheet();

  // a new card: a title, an area, who and work
  await page.click("#addBtn");
  await page.waitForSelector("#add[open] #addTitleIn");
  await page.fill("#addTitleIn", "Ask the shop about gut strings for the bass");
  await page.selectOption("#addArea", "bass");
  await page.click('#addWho [data-who="claude"]');
  await page.click('#addWork [data-work="people"]');
  await shot("add");
  await sideways("adding"); await targets("adding");
  await page.click("#add [type=submit]");
  await page.waitForSelector("#add:not([open])", { state: "attached" });
  const added = await page.$('.cell[data-status="backlog"] .card[data-work="people"] .flag.unsynced');
  check(added, "the new card in Backlog, not synced");
  ch = await changes();
  const nw = ch[ch.length - 1].new || {};
  check(nw.title === "Ask the shop about gut strings for the bass" && nw.area === "bass" && nw.work === "people"
    && JSON.stringify(nw.who) === '["player","claude"]' && nw.status === "backlog" && nw.importance === 2
    && /^ask-the-shop-about-gut-strings-for-the-[0-9a-f]{4}$/.test(nw.id), `the new card in the log: ${JSON.stringify(nw)}`);
  check(await page.evaluate(() => document.activeElement && document.activeElement.classList.contains("card")), "the new card has the focus");
  await shot("added");

  // filters and lanes, kept on this device over a reload
  await page.click("#filtersBtn");
  await page.waitForSelector("#filters[open]");
  await page.click('#filters [data-f="importance"][data-v="1"]');
  await page.click('#filters [data-group="lanes"]');
  await shot("filters");
  await sideways("filters"); await targets("filters");
  await page.click("#filters .sh-foot [data-act=close]");
  const highs = await page.$$eval(".card", (cs) => cs.map((c) => c.classList.contains("imp1")));
  check(highs.length && highs.every(Boolean), "importance High: only high cards");
  check((await page.$$(LANE)).length >= 2, `area lanes (${LANE})`);
  await shot("lanes");
  await view("agent");
  await page.reload();
  await page.waitForSelector(LANE);
  check(await page.$('#views [data-view="agent"][aria-pressed="true"]'), "the view kept over a reload");
  check((await page.textContent("#filtersBtn")).includes("1"), "the filter kept over a reload");
  await page.click("#sub [data-act=clear]");
  await view("all");
  await page.click("#filtersBtn"); await page.click('#filters [data-group="columns"]'); await page.click("#filters .sh-foot [data-act=close]");

  // Done: the last two weeks, the older folded
  await jumpTo("done");
  const doneBefore = (await page.$$('.cell[data-status="done"] .card')).length;
  await page.click("[data-act=older]");
  const doneAfter = (await page.$$('.cell[data-status="done"] .card')).length;
  check(doneBefore === 6 && doneAfter === 9, `Done: ${doneBefore} in two weeks, ${doneAfter} with the older`);
  await page.click("[data-act=older]");

  // drag, with a mouse only
  if (!touch) {
    await page.dragAndDrop('.card[data-id="loop-section"]', '.cell[data-status="next"]');
    await page.waitForSelector('.cell[data-status="next"] .card[data-id="loop-section"]');
    ch = await changes();
    check(ch[ch.length - 1].card === "loop-section" && ch[ch.length - 1].set.status === "next", "dragged to Next");
  } else {
    check(!(await page.$(".card[draggable]")), "no drag on a touch screen");
  }

  await sideways("the end");
  check(!errors.length, `console errors: ${errors.join(" | ")}`);
  await browser.close();
  if (failures.length) { console.error(`${tag}: ${failures.length} failed\n  ` + failures.join("\n  ")); process.exit(1); }
  console.log(`${tag}: all passed`);
})().catch((e) => { console.error(`${tag}: ${e.stack || e}`); process.exit(2); });
