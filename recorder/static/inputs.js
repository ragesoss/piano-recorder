/* The room's inputs on every app's page (recorder/inputs.py; each app serves this at /inputs.js): which of the
   interface's inputs are recorded and what is on each, their live levels, and a sheet to name a mic that has just
   been plugged in, or to say whether an input is recorded. One file for the room, so a mic named on one app's
   page is named on every app's.

   A page calls RoomInputs.init({ monitor }) once (monitor: what keeps the levels coming while the sheet is open,
   e.g. a POST to /api/monitor), RoomInputs.update(status) with every status it polls (its input_levels,
   input_peaks, inputs_version, recording and monitor_error), and puts RoomInputs.strip() where it shows what a
   take records (each recorded input's name and meter; an input not recorded that hears something, with
   "Record it"). Anything with data-inp-open opens the sheet (data-inp-open="3": at input 3's form).
   RoomInputs.onChange(fn) is called when the inputs change (here or in another app). Uses the page's colours
   (--surf, --line, --purple, ...): the exercise app's look. */
(function () {
  "use strict";
  const STYLE = `
.ri-strip { display: flex; flex-direction: column; gap: 8px; }
.ri-row { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(60px, 1fr) 56px; align-items: center; gap: 10px; min-height: 32px; }
.ri-row .ri-name { font-size: 16px; color: var(--ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ri-row .ri-name b { color: var(--ink); font-weight: 700; }
.ri-db { font-size: 15px; color: var(--ink3); text-align: right; font-variant-numeric: tabular-nums; }
.ri-meter { height: 10px; border-radius: 5px; background: var(--line); position: relative; overflow: hidden; }
.ri-meter > i { position: absolute; left: 0; top: 0; bottom: 0; width: 0; border-radius: 5px; background: var(--ink2); transition: width .12s linear; }
.ri-meter > b { position: absolute; top: 0; bottom: 0; width: 3px; background: var(--ink); opacity: .8; display: none; }
.ri-hears { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 15px; color: var(--amber); }
.ri-hears span { flex: 1; min-width: 160px; }
.ri-none { font-size: 16px; color: var(--red-ink); }
.ri-btn { min-height: 44px; padding: 0 16px; border-radius: 14px; border: 1.5px solid var(--purple); background: transparent; color: var(--purple-ink);
  font: inherit; font-size: 16px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; gap: 8px; cursor: pointer; white-space: nowrap; }
.ri-btn.fill { background: var(--purple-fill); border-color: var(--purple-fill); color: #fff; }
.ri-btn.plain { border-color: var(--line); color: var(--ink); }
.ri-btn:disabled { opacity: .5; cursor: default; }
.ri-open { align-self: flex-start; }
.ri-sheet { position: fixed; inset: 0; z-index: 50; background: rgba(0, 0, 0, .6); display: flex; align-items: center; justify-content: center; padding: 16px; }
.ri-box { background: var(--surf2); border: 1.5px solid var(--line); border-radius: 22px; width: min(720px, 100%); max-height: min(92dvh, 940px);
  display: flex; flex-direction: column; overflow: hidden; color: var(--ink); font-family: var(--text); }
.ri-head { display: flex; align-items: center; gap: 12px; padding: 14px 16px 12px 18px; border-bottom: 1px solid var(--line); }
.ri-head h2 { flex: 1; margin: 0; font-family: var(--display); font-size: 26px; font-weight: 700; }
.ri-close { width: 48px; height: 48px; border-radius: 24px; border: 1.5px solid var(--line); background: transparent; color: var(--ink); font-size: 26px; line-height: 1; cursor: pointer; flex: none; }
.ri-body { overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 12px 18px 18px; display: flex; flex-direction: column; gap: 10px; }
.ri-intro { font-size: 15px; color: var(--ink3); margin: 0; }
.ri-note { font-size: 15px; color: var(--ink2); margin: 0; }
.ri-err { font-size: 15px; color: var(--red-ink); margin: 0; }
.ri-item { background: var(--surf); border: 1.5px solid var(--line); border-radius: 16px; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.ri-item.off { background: transparent; border-color: var(--line-soft); }
.ri-item.hears-it { border-color: var(--amber); }
.ri-top { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.ri-num { font-size: 14px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--ink3); flex: none; }
.ri-title { font-size: 18px; font-weight: 700; min-width: 0; overflow-wrap: anywhere; }
.ri-item.off .ri-title { color: var(--ink2); font-weight: 600; }
.ri-socket { font-size: 14px; color: var(--faint); margin-left: auto; }
.ri-details { font-size: 15px; color: var(--ink3); overflow-wrap: anywhere; }
.ri-level { display: grid; grid-template-columns: minmax(0, 1fr) 64px; align-items: center; gap: 10px; }
.ri-acts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.ri-seg { display: inline-flex; border: 1.5px solid var(--line); border-radius: 14px; overflow: hidden; }
.ri-seg button { min-height: 44px; padding: 0 14px; border: 0; background: transparent; color: var(--ink2); font: inherit; font-size: 15px; font-weight: 600; cursor: pointer; }
.ri-seg button[aria-pressed="true"] { background: var(--purple-fill); color: #fff; font-weight: 700; }
.ri-form { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--line-soft); padding-top: 10px; }
.ri-form label { font-size: 14px; font-weight: 700; color: var(--ink3); display: flex; flex-direction: column; gap: 6px; }
.ri-form input[type=text] { min-height: 48px; border-radius: 12px; border: 1.5px solid var(--line); background: var(--bg); color: var(--ink); padding: 0 12px;
  font: inherit; font-size: 17px; font-weight: 400; width: 100%; box-sizing: border-box; }
.ri-form input[type=text]:focus { outline: 2px solid var(--purple); outline-offset: 1px; }
.ri-form .ri-acts { justify-content: flex-end; }
`;
  const R = { data: null, version: null, levels: null, peaks: null, recording: false, monError: null, monitor: null,
              listeners: [], open: false, editing: null, form: null, error: "", busy: false, timer: null, stripKey: "" };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (db) => Math.max(0, Math.min(100, (db + 60) / 60 * 100));
  const dbText = (v) => v == null ? "–" : `${v < -0.5 ? "−" : ""}${Math.abs(Math.round(v))} dB`;

  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* the status alone */ }
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return r.json();
  }

  function inputs() { return R.data ? R.data.inputs : []; }
  function recorded() { return inputs().filter((i) => i.record).sort((a, b) => a.channel - b.channel); }
  function level(n) { return R.levels && R.levels[n - 1] != null ? R.levels[n - 1] : null; }
  function hears(n) { const v = level(n); return v != null && R.data && v > R.data.signal_dbfs; }
  function label(i) { return `In ${i.input} · ${i.name}`; }
  // an input not recorded that hears something: worth recording, unless the player named it and said not to (records)
  function unheard() { return inputs().filter((i) => !i.record && !i.named && hears(i.input)); }

  function changed() { R.listeners.forEach((fn) => { try { fn(R.data); } catch (e) { console.error(e); } }); }

  async function load() {
    try {
      R.data = await api("GET", "/api/inputs");
      R.version = R.data.version;
    } catch (e) {
      R.error = `Could not read the inputs: ${e.message}`;
    }
    R.stripKey = "";
    changed();
    if (R.open) renderSheet();
    paint();
    return R.data;
  }

  function update(status) {
    R.levels = status ? status.input_levels || null : null;
    R.peaks = status ? status.input_peaks || null : null;
    R.recording = !!(status && status.recording);
    R.monError = status ? status.monitor_error || status.error_inputs || null : null;
    const v = status ? status.inputs_version : null;
    if (v != null && R.version != null && v !== R.version) { R.version = v; load(); return; }
    paint();
  }

  function meter(n, big) {
    return `<span class="ri-meter"${big ? ' style="height:14px;border-radius:7px"' : ""} data-inp-meter="${n}"><i></i><b></b></span>`;
  }

  // mode "": each recorded input with its meter, then the rest; "extras": the rest alone (a page that lists the
  // recorded inputs itself): an input not recorded that hears something, and the Inputs button
  function stripInner(mode) {
    const rec = recorded(), extra = unheard();
    const rows = mode === "extras" ? "" : rec.map((i) => `<div class="ri-row"><span class="ri-name">In ${i.input} · <b>${esc(i.name)}</b></span>${meter(i.input)}<span class="ri-db" data-inp-db="${i.input}">–</span></div>`).join("");
    const hearing = extra.map((i) => `<div class="ri-hears"><span>In ${i.input} hears something and is not recorded.</span><button type="button" class="ri-btn" data-inp-open="${i.input}">Record it</button></div>`).join("");
    const none = !R.data || rec.length ? "" : '<div class="ri-none">No input is recorded: choose one under Inputs.</div>';
    return `${none}${rows}${hearing}<button type="button" class="ri-btn ri-open" data-inp-open>Inputs…</button>`;
  }
  function strip() { return `<div class="ri-strip" data-inp-strip="">${stripInner("")}</div>`; }
  function extras() { return `<div class="ri-strip" data-inp-strip="extras">${stripInner("extras")}</div>`; }

  function paint() {
    const key = `${R.version}|${unheard().map((i) => i.input).join(",")}`;
    if (key !== R.stripKey) {
      R.stripKey = key;
      document.querySelectorAll("[data-inp-strip]").forEach((el) => { el.innerHTML = stripInner(el.dataset.inpStrip); });
    }
    document.querySelectorAll("[data-inp-meter]").forEach((el) => {
      const n = Number(el.dataset.inpMeter), v = level(n), bar = el.firstElementChild, mark = el.lastElementChild;
      bar.style.width = v == null ? "0" : pct(v) + "%";
      bar.style.background = v > -6 ? "var(--red-dot, #FF5147)" : v > -12 ? "var(--amber)" : "var(--ink2)";
      const p = R.peaks && R.peaks[n - 1];
      if (R.recording && p != null) { mark.style.display = "block"; mark.style.left = `calc(${pct(p)}% - 2px)`; } else mark.style.display = "none";
    });
    document.querySelectorAll("[data-inp-db]").forEach((el) => { el.textContent = dbText(level(Number(el.dataset.inpDb))); });
    if (R.open) {
      document.querySelectorAll(".ri-item[data-n]").forEach((el) => {
        const i = inputs().find((x) => x.input === Number(el.dataset.n));
        el.classList.toggle("hears-it", !!(i && !i.record && !i.named && hears(i.input)));
      });
      const note = document.getElementById("riListen");
      if (note) note.textContent = listenNote();
    }
  }

  function listenNote() {
    if (R.recording) return "A take is being recorded: its levels are live. What is recorded changes from the next take; a name, in this take too.";
    if (R.monError) return `Not listening: ${R.monError}.`;
    return R.levels ? "Listening to every input." : "Listening…";
  }

  // --- the sheet: every input, named or not, recorded or not

  function itemHtml(i) {
    const editing = R.editing === i.input, f = R.form || {};
    const kindRow = `<div class="ri-seg" role="radiogroup" aria-label="What it hears">
        <button type="button" role="radio" aria-pressed="${!f.forOne}" data-ri="for-room">The room</button>
        <button type="button" role="radio" aria-pressed="${!!f.forOne}" data-ri="for-one">One instrument</button></div>`;
    const form = !editing ? "" : `<div class="ri-form">
        <label>Name<input type="text" id="riName" maxlength="40" value="${esc(f.name)}" placeholder="Bluebird SL" autocomplete="off"></label>
        <label>What it is, where it is<input type="text" id="riDetails" maxlength="200" value="${esc(f.details)}" placeholder="Blue Bluebird SL, large-diaphragm condenser, beside the MXL" autocomplete="off"></label>
        <label>What it hears ${kindRow}</label>
        ${f.forOne ? `<label>Which instrument<input type="text" id="riKind" maxlength="30" value="${esc(f.kind)}" placeholder="bass" autocomplete="off"></label>` : ""}
        <div class="ri-acts"><button type="button" class="ri-btn plain" data-ri="cancel">Cancel</button><button type="button" class="ri-btn fill" data-ri="save"${R.busy ? " disabled" : ""}>Save</button></div></div>`;
    const acts = editing ? "" : `<div class="ri-acts">
        <div class="ri-seg" role="radiogroup" aria-label="${esc(label(i))}: recorded or not">
          <button type="button" role="radio" aria-pressed="${i.record}" data-ri="record" data-n="${i.input}" data-v="1"${R.busy ? " disabled" : ""}>Recorded</button>
          <button type="button" role="radio" aria-pressed="${!i.record}" data-ri="record" data-n="${i.input}" data-v="0"${R.busy ? " disabled" : ""}>Not recorded</button></div>
        ${i.named ? `<button type="button" class="ri-btn" data-ri="edit" data-n="${i.input}">Edit</button>
          <button type="button" class="ri-btn plain" data-ri="new" data-n="${i.input}">A new mic here</button>`
        : `<button type="button" class="ri-btn" data-ri="new" data-n="${i.input}">Name it</button>`}</div>`;
    const what = i.for ? `on the ${esc(i.for)}` : i.named ? "the room" : "";
    return `<div class="ri-item${i.record ? "" : " off"}" data-n="${i.input}">
      <div class="ri-top"><span class="ri-num">In ${i.input}</span><span class="ri-title">${i.named ? esc(i.name) : "Nothing named"}</span>${i.socket ? `<span class="ri-socket">${esc(i.socket)}</span>` : ""}</div>
      ${i.details || what ? `<div class="ri-details">${esc(i.details)}${i.details && what ? " · " : ""}${what}${i.record ? "" : " · not recorded"}</div>` : ""}
      <div class="ri-level">${meter(i.input)}<span class="ri-db" data-inp-db="${i.input}">–</span></div>
      ${acts}${form}</div>`;
  }

  function renderSheet() {
    let el = document.getElementById("riSheet");
    if (!el) {
      el = document.createElement("div");
      el.id = "riSheet";
      el.className = "ri-sheet";
      el.innerHTML = `<div class="ri-box" role="dialog" aria-modal="true" aria-labelledby="riTitle">
        <div class="ri-head"><h2 id="riTitle">Inputs</h2><button type="button" class="ri-close" data-ri="close" aria-label="Close">×</button></div>
        <div class="ri-body" id="riBody"></div></div>`;
      document.body.appendChild(el);
    }
    const focus = document.activeElement && document.activeElement.id;
    const keep = focus && ["riName", "riDetails", "riKind"].includes(focus) ? focus : null;
    readForm();
    const probs = (R.data && R.data.problems) || [];
    document.getElementById("riBody").innerHTML = `
      <p class="ri-intro">What is plugged into each of the interface's inputs, for every app in the room. A take records the ones marked Recorded, each named in its sidecar.</p>
      <p class="ri-note" id="riListen">${esc(listenNote())}</p>
      ${R.error ? `<p class="ri-err" role="alert">${esc(R.error)}</p>` : ""}
      ${probs.map((p) => `<p class="ri-err">${esc(p)}</p>`).join("")}
      ${inputs().map(itemHtml).join("") || '<p class="ri-note">No inputs.</p>'}`;
    if (keep) { const f = document.getElementById(keep); if (f) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); } }
    paint();
  }

  function readForm() {
    if (R.editing == null || !R.form) return;
    const v = (id) => { const f = document.getElementById(id); return f ? f.value : null; };
    if (v("riName") != null) R.form.name = v("riName");
    if (v("riDetails") != null) R.form.details = v("riDetails");
    if (v("riKind") != null) R.form.kind = v("riKind");
  }

  function startEdit(n, fresh) {
    const i = inputs().find((x) => x.input === n);
    if (!i) return;
    R.editing = n;
    R.error = "";
    R.form = fresh ? { name: "", details: "", forOne: false, kind: "" }
      : { name: i.named ? i.name : "", details: i.details, forOne: !!i.for, kind: i.for };
    renderSheet();
    const item = document.querySelector(`.ri-item[data-n="${n}"]`);
    if (item) item.scrollIntoView({ block: "nearest" });
    const f = document.getElementById("riName");
    if (f) f.focus({ preventScroll: true });
  }

  async function put(n, body) {
    R.busy = true;
    R.error = "";
    renderSheet();
    try {
      R.data = await api("PUT", `/api/inputs/${n}`, body);
      R.version = R.data.version;
      R.editing = null;
      R.form = null;
      R.stripKey = "";
      changed();
    } catch (e) {
      R.error = e.message;
    }
    R.busy = false;
    renderSheet();
  }

  function save() {
    readForm();
    const f = R.form, i = inputs().find((x) => x.input === R.editing);
    if (!f || !i) return;
    if (!f.name.trim()) { R.error = "A name is needed: what the takes will call it."; renderSheet(); return; }
    const body = { name: f.name.trim(), details: f.details.trim(), for: f.forOne ? f.kind.trim().toLowerCase() : "" };
    if (f.forOne && !body.for) { R.error = "Which instrument does it hear? (bass, dobro, guitar...)"; renderSheet(); return; }
    if (!i.named) body.record = true;  // naming a mic plugged in: it is recorded from the next take
    put(i.input, body);
  }

  function tick() {
    if (!R.open) return;
    if (R.monitor) { try { R.monitor(); } catch (e) { /* the page's own status says */ } }
    R.timer = setTimeout(tick, 2000);
  }

  function open(n) {
    R.open = true;
    R.error = "";
    if (!R.data) load();
    renderSheet();
    clearTimeout(R.timer);
    tick();
    if (n) startEdit(Number(n), !(inputs().find((x) => x.input === Number(n)) || {}).named);
  }

  function close() {
    R.open = false;
    R.editing = null;
    R.form = null;
    clearTimeout(R.timer);
    const el = document.getElementById("riSheet");
    if (el) el.remove();
  }

  document.addEventListener("click", (ev) => {
    const opener = ev.target.closest("[data-inp-open]");
    if (opener) { ev.preventDefault(); open(opener.dataset.inpOpen || null); return; }
    const sheet = document.getElementById("riSheet");
    if (!sheet || !sheet.contains(ev.target)) return;
    if (ev.target === sheet) { close(); return; }
    const el = ev.target.closest("[data-ri]");
    if (!el) return;
    const a = el.dataset.ri, n = Number(el.dataset.n);
    if (a === "close") close();
    else if (a === "edit") startEdit(n, false);
    else if (a === "new") startEdit(n, true);
    else if (a === "cancel") { R.editing = null; R.form = null; R.error = ""; renderSheet(); }
    else if (a === "save") save();
    else if (a === "for-room" || a === "for-one") { readForm(); R.form.forOne = a === "for-one"; renderSheet(); }
    else if (a === "record") {
      const i = inputs().find((x) => x.input === n), want = el.dataset.v === "1";
      if (!i || i.record === want) return;
      if (want && !i.named) { startEdit(n, true); return; }  // a mic is named before it is recorded
      put(n, { record: want });
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (!R.open) return;
    if (ev.key === "Escape") { if (R.editing != null) { R.editing = null; R.form = null; renderSheet(); } else close(); }
    else if (ev.key === "Enter" && ev.target && ["riName", "riDetails", "riKind"].includes(ev.target.id)) { ev.preventDefault(); save(); }
  });

  function init(opts) {
    opts = opts || {};
    R.monitor = opts.monitor || null;
    if (!document.getElementById("riStyle")) {
      const st = document.createElement("style");
      st.id = "riStyle";
      st.textContent = STYLE;
      document.head.appendChild(st);
    }
    return load();
  }

  window.RoomInputs = {
    init, load, update, strip, extras, open, close, inputs, recorded, hears, label, paint, meter,
    onChange(fn) { R.listeners.push(fn); },
    get data() { return R.data; },
    get isOpen() { return R.open; },
  };
})();
