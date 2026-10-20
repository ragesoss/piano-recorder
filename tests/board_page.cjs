// The board's page in headless Chrome (Playwright), at one screen size, muted (the board plays nothing).
//   plain: cards pushed with no goals, as before them: the views, a card opened, moved with the sheet's buttons, a
//     note, "Claude: do this next", a card added, filters and lanes kept over a reload, the older Done folded, a drag
//     (with a mouse only), and nothing of the goals anywhere.
//   goals: cards with their goals: the cards' stripes and chips (one goal, two, none), the Goals view and its charts, a
//     dot's date, stage and note, a goal's sheet, a goal moved a stage, a note on it, a card's goals chosen, the goal
//     filter kept over a reload, the stages' labels from the JSON.
// Both: no console errors, no sideways scroll, 44 px targets.
//   node tests/board_page.cjs URL WIDTHxHEIGHT SHOTS_DIR [plain|goals]      (tests/test_board_page.py runs it)
const { chromium } = require("playwright");

const [url, size, shots, mode = "plain"] = process.argv.slice(2);
const [W, H] = size.split("x").map(Number);
const touch = W < 1100;  // the iPad and the phone: no drag, the sheet's buttons
const failures = [];
const check = (ok, what) => { if (!ok) failures.push(what); };
const tag = mode === "goals" ? `${W}x${H}-goals` : `${W}x${H}`;
const LANE = W <= 640 ? ".cell h3" : ".lane";  // lanes across the columns; on a phone (one column at a time), headings in it

(async () => {
  const browser = await chromium.launch({ args: ["--mute-audio"] });
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
  if (mode === "goals") { await goalsFlow(); return; }

  // pushed before the goals: nothing of them, and the areas' colours as they were
  check(!(await page.$('#views [data-view="goals"]')) && !(await page.$("#views.has-goals")), "no Goals view without goals");
  check(!(await page.$(".gstripe, .gchip, .card.has-goals")), "no goal stripes or chips without goals");
  const hue = await page.$eval('.card[data-id="melodic-line-deploy"] .area i', (e) => getComputedStyle(e).backgroundColor);
  check(hue === "rgb(185, 160, 255)", `the area's colour as before: ${hue}`);

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
      check(on.includes("Live") && on.includes("6 questions to answer") && on.includes("Pipeline"), `the review a card links to, on that card: ${on}`);
      // an archived review: no card of its own; on the card that links to it, "Archived" and no live count
      const arch = await page.$eval('.card[data-id="free-bluegrass-review"] .summary', (e) => e.textContent).catch(() => "");
      check(arch.includes("Archived") && arch.includes("9 of 33 answered") && !arch.includes("Live") && !arch.includes("to answer")
        && arch.includes("Agent-written"), `the archived review on the card that links to it: ${arch}`);
      const lone = await page.$eval(".card.live .meta", (e) => e.textContent).catch(() => "");
      check(lone.includes("Pipeline"), `the live review card says who made it: ${lone}`);
      check(!(await page.$('.card[data-id="live:review:2026-10-20-arco"]')), "an archived review has no live card");
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
  check(!(await page.$("#sheet #cardGoals")), "no goals in a card's sheet without goals");
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
  check(!(await page.$('#filters [data-f="goal"]')), "no goal filter without goals");
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

  await finish();

  async function finish() {
    await sideways("the end");
    check(!errors.length, `console errors: ${errors.join(" | ")}`);
    await browser.close();
    if (failures.length) { console.error(`${tag}: ${failures.length} failed\n  ` + failures.join("\n  ")); process.exit(1); }
    console.log(`${tag}: all passed`);
  }

  async function goalsFlow() {
    const rgb = (v) => page.evaluate((v) => { const e = document.createElement("i"); e.style.color = `var(--g-${v})`; document.body.append(e);
      const c = getComputedStyle(e).color; e.remove(); return c; }, v);
    const GOLD = await rgb("gold"), ORANGE = await rgb("orange"), VIOLET = await rgb("violet"), TEAL = await rgb("teal");
    const stripe = (id) => page.$$eval(`.card[data-id="${id}"] .gstripe i`, (is) => is.map((i) => getComputedStyle(i).backgroundColor)).catch(() => []);
    const chips = (id) => page.$$eval(`.card[data-id="${id}"] .gchip`, (cs) => cs.map((c) => c.textContent.trim()));

    // seven views, the Goals last; the areas no longer coloured (colour is the goals')
    const views = await page.$$eval("#views button", (bs) => bs.map((b) => b.dataset.view));
    check(JSON.stringify(views) === JSON.stringify(["listen", "play", "agent", "people", "hardware", "all", "goals"]), `the views: ${views}`);
    check(await page.$$eval("#views button svg", (s) => s.length) === 7, "each view has its glyph");
    check((await page.getAttribute('#views [data-view="goals"]', "aria-label")) === "Goals, 6 goals", "the Goals view says how many");
    const area = await page.$eval('.card[data-id="melodic-line-deploy"] .area i', (e) => getComputedStyle(e).backgroundColor);
    check(area === "rgb(167, 159, 180)", `with goals an area's dot is grey: ${area}`);

    // the cards wear their goals: one, two (in order), none
    check(JSON.stringify(await stripe("guitalele-key")) === JSON.stringify([ORANGE]), `one goal, one segment: ${await stripe("guitalele-key")}`);
    check(JSON.stringify(await chips("guitalele-key")) === JSON.stringify(["Solo acoustic instrument transcription"]), "its chip names it");
    check(JSON.stringify(await stripe("bass-line-octaves")) === JSON.stringify([GOLD, ORANGE]), `two goals, two segments in order: ${await stripe("bass-line-octaves")}`);
    check(JSON.stringify(await chips("bass-line-octaves")) === JSON.stringify(["UD as a high-quality band-in-a-box", "Solo acoustic instrument transcription"]), "two chips, in order");
    const halves = await page.$$eval('.card[data-id="bass-line-octaves"] .gstripe i', (is) => is.map((i) => Math.round(i.getBoundingClientRect().width)));
    check(halves.length === 2 && Math.abs(halves[0] - halves[1]) <= 1 && halves[0] > 20, `equal segments: ${halves}`);
    check(!(await page.$('.card[data-id="records-loudness"] .gstripe')) && !(await page.$('.card[data-id="records-loudness"].has-goals')), "no goal, no stripe");
    check((await page.textContent('.card[data-id="bass-line-octaves"]')).includes("Goals:"), "the chips are named as goals to a screen reader");
    await shot("cards");
    await sideways("the cards with goals"); await targets("the cards with goals");

    // the Goals view: a tile for each, its chart
    await view("goals");
    await page.waitForSelector(".gtile .gchart svg");
    check(await page.$eval("#filtersBtn", (b) => b.hidden) && await page.$eval("#colnav", (n) => getComputedStyle(n).display === "none"), "no filters or columns in the Goals view");
    const tiles = await page.$$eval(".gtile", (ts) => ts.map((t) => ({ id: t.dataset.goal, stage: t.querySelector(".gstage").textContent.trim(),
      counts: t.querySelector(".gcounts").innerText.replace(/\s+/g, " ").trim(), dots: t.querySelectorAll("svg .dot").length,
      line: !!t.querySelector("svg path.line"), alt: t.querySelector("svg").getAttribute("aria-label"), top: getComputedStyle(t).borderTopColor })));
    check(tiles.length === 6, `six tiles: ${tiles.length}`);
    const band = tiles.find((t) => t.id === "band-in-a-box") || {};
    check(band.stage === "6 · Decent" && band.dots === 6 && band.line && band.top === GOLD, `the band's tile: ${JSON.stringify(band)}`);
    check(band.counts === "8 open (1 doing) 0 done", `its cards: ${band.counts}`);
    check(/UD as a high-quality band-in-a-box, its progress on a scale of 1 to 10: 1 · Idea on Sep 25; .*6 · Decent on Oct 17\. Now 6 · Decent\./.test(band.alt || ""), `the chart's text: ${band.alt}`);
    const axis = await page.$$eval('.gtile[data-goal="band-in-a-box"] svg text', (ts) => ts.map((t) => t.textContent));
    check(JSON.stringify(axis.slice(0, 5)) === JSON.stringify(["1", "3", "5", "7", "9"]) && axis.includes("Sep 25") && axis.includes("Today"), `the axis, lightly: ${axis}`);
    const room = await page.$eval('.gtile[data-goal="practice-room"]', (t) => ({ dots: t.querySelectorAll("svg .dot").length, btns: t.querySelectorAll(".gdot").length }));
    check(room.dots === 7 && room.btns < 7, `three changes on one day share a button: ${JSON.stringify(room)}`);
    await shot("goals");
    await sideways("the Goals view"); await targets("the Goals view");
    await page.click("#addBtn");
    await page.waitForSelector("#add[open]");
    check(await page.$('#addWork [aria-pressed="true"]'), "adding a card from the Goals view: a kind of work chosen");
    await page.click("#add [data-act=close]");
    await page.waitForSelector("#add:not([open])", { state: "attached" });

    // a dot: its date, stage and note; the steps through the changes
    const firstDot = '.gtile[data-goal="band-in-a-box"] .gdot[data-entry="0"]';
    await page.click(firstDot);
    await page.waitForSelector('.gtile[data-goal="band-in-a-box"] .gread');
    let said = await page.textContent('.gtile[data-goal="band-in-a-box"] .gread .txt');
    check(said.includes("Sep 25") && said.includes("1 · Idea") && said.includes("A band that follows you"), `the dot's readout: ${said}`);
    check(await page.$('.gtile[data-goal="band-in-a-box"] svg circle.sel') && await page.getAttribute(firstDot, "aria-pressed") === "true", "the dot marked");
    check(await page.$eval('.gtile[data-goal="band-in-a-box"] [data-gstep="-1"]', (b) => b.disabled), "nothing before the first");
    await page.click('.gtile[data-goal="band-in-a-box"] [data-gstep="1"]');
    said = await page.textContent('.gtile[data-goal="band-in-a-box"] .gread .txt');
    check(said.includes("Sep 30") && said.includes("2 · Idea with a plan") && said.includes("Strum Machine as the benchmark"), `a step on: ${said}`);
    check(await page.evaluate(() => document.activeElement && document.activeElement.dataset.gstep === "1"), "the focus kept on the step");
    await shot("dot");
    await targets("a dot's readout");

    // a goal's sheet
    await page.click('.gtile[data-goal="band-in-a-box"] .gopen');
    await page.waitForSelector("#goal[open] .gchart svg");
    const sheet = await page.textContent("#goal");
    check((await page.textContent("#goalTitle")) === "UD as a high-quality band-in-a-box", "the goal's title");
    check(sheet.includes("Release-ready (9) means") && sheet.includes("as well as Strum Machine does") && sheet.includes("Released (10) means")
      && sheet.includes("set up from the repo by anyone"), "the release lines");
    const cards = await page.$$eval("#goal .gcards .ggroup h3", (hs) => hs.map((h) => h.textContent.replace(/\s+/g, " ").trim()));
    check(JSON.stringify(cards) === JSON.stringify(["Doing 1", "Next 3", "Waiting 1", "Backlog 3"]), `its cards by status: ${cards}`);
    check(await page.$$eval("#goal .ghist li", (l) => l.length) === 6, "its history");
    const notes = await page.$$eval("#goal .notes li", (l) => l.map((x) => x.textContent));
    check(notes.length === 2 && notes[0].includes("You") && notes[1].includes("Claude") && notes[1].includes("The voices next"), `its notes, newest first, who wrote each: ${notes}`);
    const named = await page.$$eval("#goal svg text", (ts) => ts.map((t) => t.textContent));
    check(W >= 700 ? named.includes("6 Decent") && named.includes("10 Released") : named.includes("9") && !named.includes("6 Decent"), `the sheet's axis: ${named.slice(0, 10)}`);
    const ladder = await page.$$eval("#goal .stages button", (bs) => bs.map((b) => b.textContent.replace(/\s+/g, " ").trim()));
    check(ladder.length === 10 && ladder[0] === "1 · Idea" && ladder[5] === "6 · Decent (now)" && ladder[9] === "10 · Released", `move to stage, the ten: ${ladder}`);
    await shot("goal");
    await sideways("the goal's sheet"); await targets("the goal's sheet");

    // moved a stage, with a note
    await page.click('#goal [data-stage="7"]');
    await page.waitForSelector("#gmoveNote");
    await page.fill("#gmoveNote", "The voices are in");
    await shot("move");
    await targets("moving a goal");
    await page.click("#goal [data-act=gmove]");
    await page.waitForFunction(() => document.querySelector("#goal .sh-head .gstage").textContent.includes("7 · Getting pretty good"));
    let ch = await changes();
    const mv = ch[ch.length - 1];
    check(JSON.stringify(Object.keys(mv).sort()) === '["at","by","goal","id","note","stage"]' && mv.goal === "band-in-a-box" && mv.stage === 7
      && mv.note === "The voices are in" && mv.by === "player", `the move in the log: ${JSON.stringify(mv)}`);
    check((await page.textContent("#goal .ghist li")).includes("not synced yet") && (await page.textContent("#goal .sh-head")).includes("Not synced yet"), "the move shown, not synced");
    check((await page.textContent("#goal .gread .txt")).includes("7 · Getting pretty good: The voices are in"), "the new dot's readout");
    check(!(await page.$("#gmoveNote")), "the note's field gone with the move");
    await shot("moved");

    // a note
    await page.fill("#gnoteText", "Ask the band about the harmony line.");
    await page.click("#goal [data-act=gnote]");
    await page.waitForFunction(() => document.querySelector("#goal .notes li").textContent.includes("harmony line"));
    const first = await page.textContent("#goal .notes li");
    check(first.includes("You") && first.includes("not synced yet"), `the note shown: ${first}`);
    check(await page.inputValue("#gnoteText") === "", "the note's field emptied");
    ch = await changes();
    const nt = ch[ch.length - 1];
    check(JSON.stringify(Object.keys(nt).sort()) === '["at","by","goal","id","note"]' && nt.note === "Ask the band about the harmony line.", `the note in the log: ${JSON.stringify(nt)}`);
    await shot("note");

    // from the goal to a card, and from the card back to the goal
    await page.click('#goal [data-open="bass-line-octaves"]');
    await page.waitForSelector("#sheet[open] #cardGoals");
    check(!(await page.$("#goal[open]")), "one sheet at a time");
    const listed = await page.$$eval("#sheet #cardGoals .rel button", (bs) => bs.map((b) => b.textContent.replace(/\s+/g, " ").trim()));
    check(listed.length === 2 && listed[0].startsWith("UD as a high-quality band-in-a-box") && listed[0].includes("7 · Getting pretty good"), `the card's goals: ${listed}`);
    await page.click('#sheet #cardGoals [data-goal-open="solo-acoustic"]');
    await page.waitForSelector("#goal[open]");
    check((await page.textContent("#goalTitle")) === "Solo acoustic instrument transcription", "the card's goal opened");
    await page.click("#goal [data-act=close]");
    await page.waitForSelector("#goal:not([open])", { state: "attached" });
    check((await page.textContent('.gtile[data-goal="band-in-a-box"] .gstage')).includes("7 · Getting pretty good")
      && (await page.textContent('.gtile[data-goal="band-in-a-box"] .gcounts')).includes("Not synced"), "the tile moved too, not synced");

    // a card's goals chosen: Goals…, two in order
    await view("all");
    await openCard("retro-record");
    check((await page.textContent("#sheet #cardGoals")).includes("Not part of a goal yet"), "a card with no goal says so");
    await page.click('#sheet [data-act="goals-edit"]');
    await page.click('#sheet [data-goalpick="practice-room"]');
    await page.click('#sheet [data-goalpick="solo-dobro"]');
    check((await page.textContent('#sheet [data-goalpick="solo-dobro"] .gord')) === "2", "the second chosen, numbered");
    await shot("card-goals");
    await sideways("choosing a card's goals"); await targets("choosing a card's goals");
    await page.click('#sheet [data-act="goals-save"]');
    await page.waitForSelector('#sheet #cardGoals [data-goal-open="solo-dobro"]');
    ch = await changes();
    const set = ch[ch.length - 1];
    check(set.card === "retro-record" && JSON.stringify(set.set) === '{"goals":["practice-room","solo-dobro"]}', `the card's goals in the log: ${JSON.stringify(set)}`);
    await closeSheet();
    check(JSON.stringify(await stripe("retro-record")) === JSON.stringify([VIOLET, TEAL]), `its stripe, in order: ${await stripe("retro-record")}`);

    // the goal filter, kept over a reload
    await page.click("#filtersBtn");
    await page.waitForSelector("#filters[open]");
    const choices = await page.$$eval('#filters [data-f="goal"]', (bs) => bs.map((b) => b.dataset.v));
    check(choices.length === 7 && choices[6] === "none", `the goal filter: ${choices}`);
    await page.click('#filters [data-f="goal"][data-v="ai-detector"]');
    await shot("goal-filter");
    await sideways("the goal filter"); await targets("the goal filter");
    await page.click("#filters .sh-foot [data-act=close]");
    let shown = await page.$$eval(".card", (cs) => cs.map((c) => c.dataset.id));
    check(JSON.stringify(shown) === '["free-bluegrass-review"]', `the AI detector's cards: ${shown}`);
    await page.reload();
    await page.waitForSelector(".card");
    shown = await page.$$eval(".card", (cs) => cs.map((c) => c.dataset.id));
    check(JSON.stringify(shown) === '["free-bluegrass-review"]' && (await page.textContent("#filtersBtn")).includes("1"), `the goal filter kept: ${shown}`);
    await page.click("#filtersBtn"); await page.click('#filters [data-f="goal"][data-v="none"]'); await page.click("#filters .sh-foot [data-act=close]");
    shown = await page.$$eval(".card", (cs) => cs.map((c) => c.dataset.id));
    check(shown.includes("free-bluegrass-review") && shown.includes("records-loudness") && shown.includes("live:exercises") && !shown.includes("guitalele-key"), "No goal: the cards with none, and the live ones");
    await page.click("#sub [data-act=clear]");

    // the stages' labels: the JSON's own when it has all ten, the board's otherwise
    const labels = await page.evaluate(() => {
      const keep = S.data.stages, out = [];
      S.data.stages = keep.map((x, i) => i === 5 ? "Fine" : x); out.push(stageText(6));
      S.data.stages = keep.slice(0, 9); out.push(stageText(6));
      S.data.stages = null; out.push(stageText(10));
      S.data.stages = keep; return out;
    });
    check(JSON.stringify(labels) === '["6 · Fine","6 · Decent","10 · Released"]', `the stages' labels: ${labels}`);
    await finish();
  }
})().catch((e) => { console.error(`${tag}: ${e.stack || e}`); process.exit(2); });
