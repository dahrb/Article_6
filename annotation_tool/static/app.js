"use strict";
// Set annotator: one judgment's sets of proceedings, start acts and decisions,
// written by the server as annotation_guide.txt section 8 YAML.

const $ = (s, el = document) => el.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};

// Short glosses from the guide, shown as tooltips.
const GLOSS = {
  Investigative: "a prosecutor, police or investigating judge",
  AdministrativeReview: "a non-judicial public body",
  Cassation: "a supreme court on points of law",
  CaseReview: "supervisory or extraordinary review",
  MeritsDecided: "the substance was decided", Inadmissible: "rejected without reaching the substance",
  Remitted: "quashed and sent back", SettledOrWithdrawn: "discontinued, settled, withdrawn",
  FavourableToApplicant: "they got what they wanted", UnfavourableToApplicant: "they did not",
  Mixed: "the same decision partly granted and partly refused the same claim or charge",
  Initiating: "moved the body to act here (claimant, prosecution, appellant)",
  Responding: "it was brought against them (defendant, accused, respondent on appeal)",
  ThirdParty: "joined on neither side",
  OpposingParty: "the other litigant, the prosecution or the State",
  Arrest: "being apprehended or taken into custody", Detention: "remand / pre-trial detention",
  Bail: "release pending trial", Charge: "being charged or indicted",
  Conviction: "the trial or an appeal on the charge", Investigation: "a criminal investigation or a refusal to open one",
  Damages: "compensation claimed or awarded", Custody: "custody of or guardianship over a person",
  Employment: "dismissal, reinstatement, wages, work terms", Pension: "pensions, social security, welfare",
  Property: "a dispute over the asset itself", Extradition: "extradition, deportation, removal",
  LegalAid: "legal aid or legal-aid counsel", Vetting: "security clearance or similar",
  Disciplinary: "professional or employment discipline",
  Requests: "applications about the conduct of the proceedings themselves",
  Health: "medical treatment or care", Immigration: "visas, residence, asylum",
  Enforcement: "enforcing a judgment", Other: "something specific not listed",
  Unknown: "the text does not say",
};

const S = {
  vocab: null, cases: [], annotator: "",
  id: null, text: "", data: null, done: false, seconds: 0,
  activeSet: null, open: null, // open = event key: "P1.s" or a decision id
  problems: [], dirty: false, saving: false, lastActivity: Date.now(),
  norm: null, selection: null, hideBefore: 0,
};

// ---------------------------------------------------------------- text search
function normWithMap(text) {
  const out = [], idx = [];
  let prevSpace = false;
  const swap = { "’": "'", "‘": "'", "“": '"', "”": '"' };
  for (let i = 0; i < text.length; i++) {
    let ch = swap[text[i]] || text[i];
    if (/\s/.test(ch)) { if (prevSpace) continue; ch = " "; prevSpace = true; } else prevSpace = false;
    out.push(ch.toLowerCase()); idx.push(i);
  }
  return { s: out.join(""), idx };
}
function locate(quote) {
  if (!quote || !quote.trim()) return null;
  const n = normWithMap(quote.trim()).s.trim();
  const j = S.norm.s.indexOf(n);
  if (j < 0) return null;
  return [S.norm.idx[j], S.norm.idx[j + n.length - 1] + 1];
}

// ---------------------------------------------------------------- dates
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december"];
const MRE = MONTHS.join("|");
function parseDate(t) {
  if (!t) return null;
  let m = t.match(new RegExp(`\\b(\\d{1,2})\\s+(${MRE})\\s+(\\d{4})\\b`, "i"));
  const p = (x) => String(x).padStart(2, "0");
  if (m) return `${m[3]}-${p(MONTHS.indexOf(m[2].toLowerCase()) + 1)}-${p(m[1])}`;
  m = t.match(new RegExp(`\\b(${MRE})\\s+(\\d{4})\\b`, "i"));
  if (m) return `${m[2]}-${p(MONTHS.indexOf(m[1].toLowerCase()) + 1)}`;
  m = t.match(/\b(1[89]\d\d|20\d\d)\b/);
  return m ? m[1] : null;
}
const ISO = /^\d{4}(-\d{2}(-\d{2})?)?$/;
const dateKey = (d) => (d || "9999").padEnd(10, "9");

function guessBody(q) {
  const m = (q || "").match(/\bthe\s+(?:[A-Z][\w’'.-]*\s+(?:of\s+(?:the\s+)?)?){0,5}(?:Court(?:\s+of\s+[A-Z]\w+(?:\s+[A-Z]\w+)?)?|Tribunal|Prosecutor(?:’s|'s)?(?:\s+Office)?|Police|Ministry(?:\s+of\s+[A-Z]\w+)?|Inspectorate|Ombudsman|Commission|Board|Council|Agency|Service|Office)\b|\bthe\s+(?:investigating\s+judge|prosecutor(?:’s|'s)?\s+office|public\s+prosecutor|prosecutor)\b/);
  return m ? m[0] : null;
}

// ---------------------------------------------------------------- model helpers
const sets = () => S.data.sets;
const setOf = (key) => sets().find((s) => (s.start && key === `${s.id}.s`) || (s.decisions || []).some((d) => d.id === key));
function eventOf(key) {
  const s = setOf(key);
  if (!s) return null;
  return key === `${s.id}.s` ? s.start : s.decisions.find((d) => d.id === key);
}
const isStart = (key) => key && key.endsWith(".s");
function nextId(prefix, ids) {
  let n = 0;
  for (const i of ids) { const m = String(i).match(new RegExp(`^${prefix}(\\d+)$`)); if (m) n = Math.max(n, +m[1]); }
  return `${prefix}${n + 1}`;
}
const allDecisions = () => sets().flatMap((s) => (s.decisions || []).map((d) => ({ set: s, d })));
function ordered(s) {
  const decs = (s.decisions || []).map((d, i) => ({ d, i }));
  const ids = new Set(decs.map((x) => x.d.id));
  const depth = (d, seen = new Set()) => {
    if (d.reviews && ids.has(d.reviews) && !seen.has(d.reviews)) {
      seen.add(d.id);
      return 1 + depth(s.decisions.find((x) => x.id === d.reviews), seen);
    }
    return 0;
  };
  decs.sort((a, b) => dateKey(a.d.date).localeCompare(dateKey(b.d.date)) || depth(a.d) - depth(b.d) || a.i - b.i);
  return decs.map((x) => x.d);
}
const setIndex = (sid) => Math.max(0, sets().findIndex((s) => s.id === sid)) % 8;

// ---------------------------------------------------------------- server
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}
let saveTimer = null;
function changed(rerenderText = true) {
  S.dirty = true;
  $("#saved").textContent = "unsaved";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 700);
  if (rerenderText) scheduleText();
}
async function save() {
  if (!S.id || S.saving) { if (S.saving) saveTimer = setTimeout(save, 400); return; }
  S.saving = true;
  const id = S.id;
  try {
    for (const s of sets()) s.decisions = ordered(s); // the file lists decisions in derived order
    const r = await api(`/api/case?id=${encodeURIComponent(id)}`, { data: S.data, seconds: Math.round(S.seconds), done: S.done });
    if (id === S.id) {
      S.problems = r.problems; S.dirty = false;
      $("#saved").textContent = `saved ${r.saved}`;
      renderProblems(); renderFlags();
    }
    const c = S.cases.find((c) => c.id === id);
    if (c) { c.started = true; c.done = S.done; }
    renderCaseList();
  } catch (e) {
    $("#saved").textContent = `SAVE FAILED: ${e.message}`;
  } finally { S.saving = false; }
}

// ---------------------------------------------------------------- loading
async function loadState() {
  const st = await api("/api/state");
  Object.assign(S, { vocab: st.vocab, cases: st.cases, annotator: st.annotator });
  $("#who").textContent = `${st.annotator} · guide v${st.guide_version}`;
  renderCaseList();
  const first = st.cases.find((c) => !c.done) || st.cases[0];
  const want = decodeURIComponent(location.hash.slice(1));
  await openCase(st.cases.some((c) => c.id === want) ? want : first.id);
}
function renderCaseList() {
  const sel = $("#cases");
  sel.replaceChildren(...S.cases.map((c) => el("option", { value: c.id, selected: c.id === S.id },
    `${c.done ? "✓" : c.started ? "●" : "○"} ${c.id}`)));
  const done = S.cases.filter((c) => c.done).length;
  sel.title = `${done} of ${S.cases.length} done`;
}
async function openCase(id) {
  if (S.dirty) await save();
  const r = await api(`/api/case?id=${encodeURIComponent(id)}`);
  Object.assign(S, { id, text: r.text, data: r.data, done: r.done, seconds: r.seconds || 0, problems: r.problems || [],
    open: null, dirty: false });
  S.data.sets = S.data.sets || [];
  for (const s of S.data.sets) { s.decisions = s.decisions || []; }
  S.activeSet = S.data.sets[0]?.id || null;
  S.norm = normWithMap(S.text);
  const f = S.text.search(/^#*\s*THE FACTS\s*$/m);
  S.hideBefore = f > 0 ? f : 0;
  location.hash = id;
  $("#done").checked = S.done;
  $("#saved").textContent = S.data.sets.length ? "loaded" : "new";
  renderCaseList(); renderText(); renderSets(); renderProblems(); tick();
  $("#text").scrollTop = 0;
  if ($("#facts-only").checked && S.hideBefore) {
    const mk = $("#text .facts-anchor"); if (mk) mk.scrollIntoView({ block: "start" });
  }
}

// ---------------------------------------------------------------- text pane
let textTimer = null;
const scheduleText = () => { clearTimeout(textTimer); textTimer = setTimeout(renderText, 150); };
function renderText() {
  const box = $("#text"), top = box.scrollTop;
  const keep = currentSelection(); // a redraw must not eat the annotator's selection
  const ranges = [];
  for (const s of sets()) {
    const evs = (s.start ? [[`${s.id}.s`, s.start]] : []).concat((s.decisions || []).map((d) => [d.id, d]));
    for (const [key, e] of evs) { const r = locate(e.quote); if (r) ranges.push({ key, set: s.id, a: r[0], b: r[1] }); }
  }
  const cuts = new Set([0, S.text.length, S.hideBefore]);
  for (const r of ranges) { cuts.add(r.a); cuts.add(r.b); }
  const pts = [...cuts].sort((x, y) => x - y);
  const frag = document.createDocumentFragment();
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1], t = S.text.slice(a, b);
    if (a === S.hideBefore && S.hideBefore) frag.append(el("span", { class: "facts-anchor" }));
    const cover = ranges.filter((r) => r.a <= a && r.b >= b);
    let node;
    if (cover.length) {
      const keys = cover.map((r) => r.key);
      node = el("mark", { class: `s${setIndex(cover[0].set)}${cover.length > 1 ? " multi" : ""}${keys.includes(S.open) ? " current" : ""}`,
        "data-evs": keys.join(" "), title: keys.join(", ") }, t);
    } else if (a < S.hideBefore && $("#facts-only").checked) node = el("span", { class: "hidden-pre" }, t);
    else node = document.createTextNode(t);
    frag.append(node);
  }
  box.replaceChildren(frag);
  box.scrollTop = top;
  if (keep) selectRange(keep.a, keep.b);
}
// global character offsets of a DOM position inside #text
function offsetOf(node, off) {
  const box = $("#text");
  const w = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
  let n, total = 0;
  while ((n = w.nextNode())) {
    if (n === node) return total + off;
    total += n.length;
  }
  if (node === box) { // an element-level position: count the text of the first `off` children
    let t = 0; for (let i = 0; i < off; i++) t += box.childNodes[i].textContent.length; return t;
  }
  return null;
}
function nodeAt(offset) {
  const w = document.createTreeWalker($("#text"), NodeFilter.SHOW_TEXT);
  let n, total = 0;
  while ((n = w.nextNode())) { if (offset <= total + n.length) return [n, offset - total]; total += n.length; }
  return null;
}
function currentSelection() {
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const r = sel.getRangeAt(0);
  if (!$("#text").contains(r.commonAncestorContainer)) return null;
  let a = offsetOf(r.startContainer, r.startOffset), b = offsetOf(r.endContainer, r.endOffset);
  if (a == null || b == null) return null;
  while (a < b && /\s/.test(S.text[a])) a++;
  while (b > a && /\s/.test(S.text[b - 1])) b--;
  return a < b ? { a, b, text: S.text.slice(a, b).replace(/\s+/g, " ") } : null;
}
function sentenceAround(pos) {
  const t = S.text;
  let a = pos, b = pos;
  const isEnd = (i) => /[.!?;]/.test(t[i]) && (i + 1 >= t.length || /[\s"”’)]/.test(t[i + 1])) &&
    !/\b(?:no|nos|Mr|Mrs|Ms|Dr|Art|para|paras|cf|i\.e|e\.g|v)$/i.test(t.slice(Math.max(0, i - 5), i));
  while (a > 0 && !(isEnd(a - 1) && /\s/.test(t[a])) && !(t[a - 1] === "\n" && t[a - 2] === "\n")) a--;
  const para = t.slice(a, pos).match(/^\s*\*\*\d+\.\*\*\s*/);
  if (para) a += para[0].length;
  while (a < pos && /\s/.test(t[a])) a++;
  while (b < t.length && !isEnd(b) && !(t[b] === "\n" && t[b + 1] === "\n")) b++;
  if (b < t.length && isEnd(b)) b++;
  return [a, b];
}
function selectRange(a, b) {
  const s = nodeAt(a), e = nodeAt(b);
  if (!s || !e) return;
  const r = document.createRange();
  r.setStart(s[0], s[1]); r.setEnd(e[0], e[1]);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
}

// ---------------------------------------------------------------- selection bar
function showPop() {
  const sel = currentSelection();
  S.selection = sel;
  const pop = $("#pop");
  if (!sel) { pop.hidden = true; return; }
  const rect = window.getSelection().getRangeAt(0).getBoundingClientRect();
  const e = S.open && eventOf(S.open);
  $("#pop-target").textContent = e ? `→ ${S.open}:` : "(open an event to fill it)";
  for (const b of pop.querySelectorAll("[data-act=quote],[data-act=body],[data-act=party],[data-act=date]")) b.disabled = !e;
  for (const b of pop.querySelectorAll(".dec-only")) b.hidden = !e || isStart(S.open);
  pop.hidden = false;
  const w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, rect.left))}px`;
  const below = rect.bottom + 8, above = rect.top - h - 8;
  const top = below + h <= window.innerHeight ? below : above >= 8 ? above : window.innerHeight - h - 8;
  pop.style.top = `${Math.max(8, Math.min(window.innerHeight - h - 8, top))}px`;
}
function act(kind) {
  const sel = S.selection || currentSelection();
  if (!sel) return;
  if (kind === "start" || kind === "decision") return newEvent(kind, sel.text);
  const e = S.open && eventOf(S.open);
  if (!e) return;
  if (kind === "quote") { e.quote = sel.text; if (!e.date) e.date = parseDate(sel.text) || ""; }
  else if (kind === "body") e.body = sel.text;
  else if (kind === "party") { e.parties = e.parties || []; e.parties.push({ name: sel.text, side: "Unknown" }); }
  else { const d = parseDate(sel.text) || sel.text; e[kind] = d; }
  hidePop(); changed(); renderSets();
}
function hidePop() { $("#pop").hidden = true; S.selection = null; }

// ---------------------------------------------------------------- editing
function addSet() {
  const id = nextId("P", sets().map((s) => s.id));
  sets().push({ id, kind: "Unknown", label: "", pending: false, enforces: null, start: null, decisions: [] });
  S.activeSet = id; changed(false); renderSets();
  setTimeout(() => { const i = document.querySelector(`[data-set="${id}"] .label-input`); if (i) i.focus(); }, 0);
}
function newEvent(kind, quote) {
  if (!sets().length) addSet();
  const s = sets().find((x) => x.id === S.activeSet) || sets()[sets().length - 1];
  S.activeSet = s.id;
  const base = { date: parseDate(quote) || "", body: "", authority_kind: "Unknown", parties: [], matter: [], quote };
  if (kind === "start") {
    if (s.start) { flash(`Set ${s.id} already has a start act: at most one per set. Use Q to replace its quote.`); return; }
    s.start = base; S.open = `${s.id}.s`;
  } else {
    const id = nextId("D", allDecisions().map((x) => x.d.id));
    s.decisions.push({ id, ...base, level: "Unknown", outcome: "Unknown", direction: "Unknown", reviews: null });
    S.open = id;
  }
  hidePop(); window.getSelection().removeAllRanges(); changed(); renderSets();
  scrollToOpen();
}
function deleteEvent(key) {
  const s = setOf(key);
  if (isStart(key)) s.start = null;
  else {
    s.decisions = s.decisions.filter((d) => d.id !== key);
    for (const x of allDecisions()) if (x.d.reviews === key) { x.d.reviews = null; delete x.d.appealed_by; delete x.d.appeal_date; }
    for (const t of sets()) if (t.enforces === key) t.enforces = null;
  }
  if (S.open === key) S.open = null;
  changed(); renderSets();
}
function moveEvent(key, target) {
  const s = setOf(key), t = sets().find((x) => x.id === target);
  if (!t || t === s) return;
  if (isStart(key)) {
    if (t.start) { flash(`Set ${t.id} already has a start act.`); renderSets(); return; }
    t.start = s.start; s.start = null; S.open = `${t.id}.s`;
  } else {
    const d = s.decisions.find((x) => x.id === key);
    s.decisions = s.decisions.filter((x) => x !== d);
    d.reviews = null; delete d.appealed_by; delete d.appeal_date;
    for (const x of s.decisions) if (x.reviews === key) { x.reviews = null; delete x.appealed_by; delete x.appeal_date; }
    t.decisions.push(d);
  }
  S.activeSet = t.id; changed(); renderSets();
}
function deleteSet(id) {
  const s = sets().find((x) => x.id === id);
  for (const d of s.decisions) for (const t of sets()) if (t.enforces === d.id) t.enforces = null;
  S.data.sets = sets().filter((x) => x !== s);
  if (S.activeSet === id) S.activeSet = sets()[0]?.id || null;
  changed(); renderSets();
}
let flashTimer = null;
function flash(msg) {
  const p = $("#saved"); const old = p.textContent; p.textContent = msg; p.style.color = "var(--bad)";
  clearTimeout(flashTimer); flashTimer = setTimeout(() => { p.textContent = old; p.style.color = ""; }, 4000);
}

// ---------------------------------------------------------------- side panel
function select(values, value, onchange, { blank = false, title = "" } = {}) {
  const s = el("select", { onchange: (ev) => onchange(ev.target.value || null), title });
  if (blank) s.append(el("option", { value: "" }, "—"));
  for (const v of values) s.append(el("option", { value: v, selected: v === value, title: GLOSS[v] || "" }, v));
  if (value && !values.includes(value)) s.append(el("option", { value, selected: true }, `${value} (not in list)`));
  return s;
}
function input(value, onchange, attrs = {}) {
  return el("input", { type: "text", value: value ?? "", ...attrs, oninput: (ev) => onchange(ev.target.value) });
}
const confirmBtn = (label, fn) => {
  const b = el("button", { class: "small danger", title: "click twice", onclick: () => {
    if (b.dataset.armed) return fn();
    b.dataset.armed = "1"; b.textContent = "sure?"; setTimeout(() => { delete b.dataset.armed; b.textContent = label; }, 2500);
  } }, label);
  return b;
};

function renderSets() {
  const box = $("#sets"), top = box.scrollTop;
  box.replaceChildren(...sets().map(renderSet));
  if (!sets().length) box.append(el("p", { class: "muted" }, "No sets yet. Press + Set (N), or select a sentence and press S or D."));
  box.scrollTop = top;
  renderFlags(); scheduleText();
}
function renderSet(s) {
  const active = s.id === S.activeSet;
  const enforceable = allDecisions().filter((x) => x.set !== s).map((x) => x.d.id);
  const card = el("div", { class: `set s${setIndex(s.id)}${active ? " active" : ""}`, "data-set": s.id },
    el("div", { class: "set-head", onclick: (ev) => { if (ev.target.closest("input,select,button")) return; S.activeSet = s.id; renderSets(); } },
      el("span", { class: "set-id" }, s.id),
      input(s.label, (v) => { s.label = v; changed(false); }, { placeholder: "label: a few words naming it", class: "label-input" }),
      confirmBtn("delete set", () => deleteSet(s.id))),
    el("div", { class: "set-fields" },
      select(S.vocab.kind, s.kind, (v) => { s.kind = v; changed(false); }, { title: "kind" }),
      el("label", { title: "true only if the text says it is still pending" },
        el("input", { type: "checkbox", checked: !!s.pending, onchange: (ev) => { s.pending = ev.target.checked; changed(false); } }), " pending"),
      el("label", { class: "muted", title: "enforcement sets only: the decision being enforced" }, "enforces ",
        select(enforceable, s.enforces, (v) => { s.enforces = v; changed(false); }, { blank: true })),
      input(s.note, (v) => { s.note = v; changed(false); }, { placeholder: "note (when unsure)" })),
  );
  const evs = el("div", { class: "events" });
  if (s.start) evs.append(renderEvent(s, `${s.id}.s`, s.start, "S"));
  else evs.append(el("div", { class: "muted", style: "font-size:12px;padding:3px 0" }, "no start act (select its sentence and press S)"));
  ordered(s).forEach((d, i) => evs.append(renderEvent(s, d.id, d, String(i + 1))));
  card.append(evs);
  return card;
}
function eventTitle(key, e) {
  const bits = [e.date || "no date", isStart(key) ? "start" : e.level, e.body || ""];
  return `${key.endsWith(".s") ? "start" : key} · ${bits.filter(Boolean).join(" · ")}`;
}
function renderEvent(s, key, e, ord) {
  const open = S.open === key;
  const box = el("div", { class: `event${open ? " open" : ""}`, "data-ev": key },
    el("div", { class: "ev-head", onclick: () => { S.open = open ? null : key; S.activeSet = s.id; renderSets(); if (!open) scrollQuote(key); } },
      el("span", { class: "ord" }, ord),
      el("span", { class: "what", title: e.quote || "" }, eventTitle(key, e)),
      el("span", { class: "flag" })));
  if (!open) return box;
  const body = el("div", { class: "ev-body" });
  const row = (label, ...kids) => body.append(el("label", {}, label), el("div", { class: "row" }, ...kids));
  const dec = !isStart(key);
  const set = (k) => (v) => { e[k] = v; changed(k === "quote"); };

  const dateIn = input(e.date, (v) => { e.date = v.trim(); dateIn.classList.toggle("bad-field", !!e.date && !ISO.test(e.date)); changed(false); },
    { placeholder: "YYYY-MM-DD / YYYY-MM / YYYY", size: 12 });
  dateIn.classList.toggle("bad-field", !e.date || !ISO.test(e.date));
  row("date", dateIn);
  const sug = guessBody(e.quote);
  row("body", input(e.body, set("body"), { placeholder: "the court or authority, as the text names it", style: "flex:1" }),
    sug && sug !== e.body ? el("span", { class: "suggest", title: "use this", onclick: () => { e.body = sug; changed(false); renderSets(); } }, sug) : null);
  row("authority", select(S.vocab.authority_kind, e.authority_kind, set("authority_kind")));
  if (dec) {
    row("level", select(S.vocab.level, e.level, set("level")));
    row("outcome", select(S.vocab.outcome, e.outcome, set("outcome")));
    row("direction", select(S.vocab.direction, e.direction, set("direction"), { title: "how THIS decision came out for the applicant" }));
    const earlier = (s.decisions || []).filter((d) => d.id !== key);
    const rev = el("select", { onchange: (ev) => {
      e.reviews = ev.target.value || null;
      if (!e.reviews) { delete e.appealed_by; delete e.appeal_date; } else if (!e.appealed_by) e.appealed_by = "Unknown";
      changed(false); renderSets();
    } }, el("option", { value: "" }, "— (first decision, or it merely comes next)"));
    for (const d of ordered({ ...s, decisions: earlier }))
      rev.append(el("option", { value: d.id, selected: d.id === e.reviews }, `${d.id} · ${d.date || "?"} · ${d.level} · ${d.body || ""}`));
    row("reviews", rev);
    if (e.reviews) {
      row("appealed by", select(S.vocab.appealed_by, e.appealed_by, set("appealed_by")));
      row("appeal date", input(e.appeal_date, (v) => { e.appeal_date = v.trim(); changed(false); }, { placeholder: "if the text says", size: 12 }));
    }
    row("served", input(e.communication_date, (v) => { e.communication_date = v.trim(); changed(false); },
      { placeholder: "communication date, if the text says", size: 12 }));
    row("costs", select(S.vocab.costs, e.costs, set("costs"), { blank: true, title: "who this decision orders to bear the costs; — if the text says nothing" }));
  }
  // parties
  const plist = el("div", { class: "full" });
  const drawParties = () => {
    plist.replaceChildren(...(e.parties || []).map((p, i) => el("div", { class: "party" },
      input(p.name, (v) => { p.name = v; changed(false); }, { placeholder: "name as the text gives it" }),
      select(S.vocab.side, p.side || "Unknown", (v) => { p.side = v; changed(false); }),
      el("button", { class: "small", title: "remove", onclick: () => { e.parties.splice(i, 1); drawParties(); changed(false); } }, "×"))));
  };
  drawParties();
  const addP = (name, side) => () => { e.parties = e.parties || []; e.parties.push({ name, side }); drawParties(); changed(false); };
  body.append(el("label", {}, "parties"), el("div", { class: "add-row" },
    el("button", { class: "small", onclick: addP("the applicant", "Initiating") }, "+ applicant · Initiating"),
    el("button", { class: "small", onclick: addP("the applicant", "Responding") }, "+ applicant · Responding"),
    el("button", { class: "small", onclick: addP("", "Unknown") }, "+ other"),
    el("span", { class: "muted", style: "font-size:12px" }, "or select a name, P")), plist);
  // matter
  const chips = el("div", { class: "chips" });
  for (const m of S.vocab.matter) {
    const on = (e.matter || []).includes(m);
    chips.append(el("span", { class: `chip${on ? " on" : ""}`, title: GLOSS[m] || "", onclick: (ev) => {
      e.matter = e.matter || [];
      if (e.matter.includes(m)) e.matter = e.matter.filter((x) => x !== m); else e.matter.push(m);
      ev.target.classList.toggle("on"); changed(false);
    } }, m));
  }
  body.append(el("label", {}, "matter"), chips);
  const q = el("textarea", { rows: 3, oninput: (ev) => { e.quote = ev.target.value; q.classList.toggle("bad-field", !locate(e.quote)); changed(); } });
  q.value = e.quote || "";
  q.classList.toggle("bad-field", !locate(e.quote));
  body.append(el("label", {}, "quote"), q);
  body.append(el("label", {}, "note"), input(e.note, set("note"), { placeholder: "when torn between two readings, say why" }));
  const move = select(sets().map((x) => x.id), s.id, (v) => moveEvent(key, v), { title: "move to another set" });
  body.append(el("label", {}, ""), el("div", { class: "row" }, el("span", { class: "muted" }, "set"), move,
    el("span", { style: "flex:1" }), confirmBtn("delete event", () => deleteEvent(key))));
  box.append(body);
  return box;
}
function scrollToOpen() {
  setTimeout(() => { const b = document.querySelector(`[data-ev="${S.open}"]`); if (b) b.scrollIntoView({ block: "nearest" }); }, 0);
}
function scrollQuote(key) {
  setTimeout(() => {
    const m = [...document.querySelectorAll("#text mark")].find((x) => x.dataset.evs.split(" ").includes(key));
    if (m) m.scrollIntoView({ block: "center", behavior: "smooth" });
  }, 200);
}

// ---------------------------------------------------------------- checks
function problemKey(p) {
  let m = p.match(/^set (\S+) decision (\S+):/); if (m) return m[2];
  m = p.match(/^set (\S+) start:/); if (m) return `${m[1]}.s`;
  m = p.match(/^(\S+): quote not found/); if (m) return m[1];
  return null;
}
function renderProblems() {
  const ul = $("#problems");
  $("#problem-count").textContent = S.problems.length ? `(${S.problems.length})` : "";
  if (!S.problems.length) { ul.replaceChildren(el("li", { class: "ok" }, sets().length ? "no problems found" : "nothing yet")); return; }
  ul.replaceChildren(...S.problems.map((p) => el("li", { onclick: () => {
    const k = problemKey(p); if (!k || !eventOf(k)) return;
    S.open = k; S.activeSet = setOf(k).id; renderSets(); scrollToOpen(); scrollQuote(k);
  } }, p)));
}
function renderFlags() {
  const bad = new Set(S.problems.map(problemKey).filter(Boolean));
  for (const b of document.querySelectorAll(".event")) {
    b.classList.toggle("bad", bad.has(b.dataset.ev));
    const f = b.querySelector(".flag"); if (f) f.textContent = bad.has(b.dataset.ev) ? "!" : "";
  }
}

// ---------------------------------------------------------------- drawer
let guideText = null;
async function drawer(kind) {
  const d = $("#drawer");
  if (!d.hidden && d.dataset.kind === kind) { d.hidden = true; return; }
  d.dataset.kind = kind; d.hidden = false;
  if (kind === "guide") {
    $("#drawer-title").textContent = "Annotation guide";
    guideText = guideText || (await api("/api/guide")).text;
    $("#drawer-body").replaceChildren(el("pre", {}, guideText));
  } else {
    $("#drawer-title").textContent = "Keys and workflow";
    $("#drawer-body").replaceChildren($("#help-tpl").content.cloneNode(true));
  }
}

// ---------------------------------------------------------------- timer
let lastTick = Date.now(), sinceSave = 0;
function tick() {
  const now = Date.now(), dt = (now - lastTick) / 1000;
  lastTick = now;
  if (S.id && document.visibilityState === "visible" && now - S.lastActivity < 120000 && dt < 5) {
    S.seconds += dt; sinceSave += dt;
  }
  const s = Math.round(S.seconds);
  $("#timer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  if (sinceSave > 60 && sets().length && !S.saving) { sinceSave = 0; save(); }
}

// ---------------------------------------------------------------- wiring
function wire() {
  for (const ev of ["mousemove", "keydown", "scroll", "click"]) document.addEventListener(ev, () => { S.lastActivity = Date.now(); }, { passive: true, capture: true });
  setInterval(tick, 1000);
  $("#cases").addEventListener("change", (ev) => openCase(ev.target.value));
  const step = (k) => { const i = S.cases.findIndex((c) => c.id === S.id) + k; if (S.cases[i]) openCase(S.cases[i].id); };
  $("#prev").onclick = () => step(-1);
  $("#next").onclick = () => step(1);
  $("#done").onchange = (ev) => { S.done = ev.target.checked; changed(false); };
  $("#add-set").onclick = addSet;
  $("#guide-btn").onclick = () => drawer("guide");
  $("#help").onclick = () => drawer("help");
  $("#drawer-close").onclick = () => { $("#drawer").hidden = true; };
  $("#facts-only").onchange = renderText;
  $("#pop").addEventListener("mousedown", (ev) => ev.preventDefault()); // keep the text selection
  $("#pop").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b) act(b.dataset.act); });

  const text = $("#text");
  text.addEventListener("mouseup", (ev) => {
    if (ev.altKey) {
      let pos = null;
      if (document.caretPositionFromPoint) { const c = document.caretPositionFromPoint(ev.clientX, ev.clientY); if (c) pos = offsetOf(c.offsetNode, c.offset); }
      else if (document.caretRangeFromPoint) { const c = document.caretRangeFromPoint(ev.clientX, ev.clientY); if (c) pos = offsetOf(c.startContainer, c.startOffset); }
      if (pos != null) { const [a, b] = sentenceAround(pos); selectRange(a, b); }
    }
    setTimeout(() => {
      const sel = currentSelection();
      if (!sel) {
        hidePop();
        const m = ev.target.closest("mark");
        if (m) { // click on a quote: open its event (cycle when quotes are shared)
          const keys = m.dataset.evs.split(" ");
          const k = keys[(keys.indexOf(S.open) + 1) % keys.length];
          S.open = k; S.activeSet = setOf(k)?.id || S.activeSet; renderSets(); scrollToOpen();
        }
        return;
      }
      showPop();
    }, 0);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.altKey && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) { ev.preventDefault(); $(ev.key === "ArrowLeft" ? "#prev" : "#next").click(); return; }
    if (ev.key === "Escape") { hidePop(); $("#drawer").hidden = true; return; }
    if (ev.target.closest("input, textarea, select") || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const k = ev.key.toLowerCase();
    if (k === "n") { ev.preventDefault(); addSet(); return; }
    if (k === "?") { drawer("help"); return; }
    const acts = { s: "start", d: "decision", q: "quote", b: "body", p: "party", t: "date" };
    if (acts[k] && currentSelection()) { ev.preventDefault(); S.selection = currentSelection(); act(acts[k]); }
  });
  window.addEventListener("beforeunload", (ev) => { if (S.dirty) { save(); ev.preventDefault(); } });
}

wire();
loadState().catch((e) => { document.body.prepend(el("p", { style: "color:var(--bad);padding:8px" }, `Could not load: ${e.message}`)); });
