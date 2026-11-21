// Runs a built page's script in node against a tiny stub DOM and drives every filter.
// Usage: node page_harness.js PAGE.html [--block-storage] [--no-leaflet]
// Prints one JSON object with what it observed; the Python tests assert on it. Exits non-zero on a
// script error or a failed check.
"use strict";
const fs = require("fs");
const vm = require("vm");

const file = process.argv[2];
const blockStorage = process.argv.includes("--block-storage");
const noLeaflet = process.argv.includes("--no-leaflet");
const html = fs.readFileSync(file, "utf8");

// ---------- stub DOM ----------
const registry = new Map(); // id -> element
const all = new Set();
function parseAttrs(s) {
  const a = {};
  for (const m of s.matchAll(/([\w:-]+)(?:\s*=\s*"([^"]*)")?/g)) a[m[1]] = m[2] === undefined ? "" : m[2];
  return a;
}
const unescape = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
class El {
  constructor(tag, attrs, inner) {
    this.tagName = tag.toUpperCase();
    this.attrs = {};
    for (const [k, v] of Object.entries(attrs)) this.attrs[k] = unescape(v);
    this.id = this.attrs.id || "";
    this.type = this.attrs.type || "";
    this.checked = "checked" in this.attrs;
    this.hidden = "hidden" in this.attrs;
    this.handlers = {};
    this.kids = [];
    this._inner = "";
    this.textContent = "";
    this.classList = new ClassList(this);
    this.dataset = {};
    for (const [k, v] of Object.entries(this.attrs)) if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
    this.parentNode = {appendChild() {}};
    if (this.tagName === "SELECT") {
      this.options = [...(inner || "").matchAll(/<option\b([^>]*)>/g)].map(m => ({value: unescape(parseAttrs(m[1]).value), selected: "selected" in parseAttrs(m[1])}));
      this._value = (this.options.find(o => o.selected) || this.options[0] || {value: ""}).value;
    } else this._value = this.attrs.value ?? "";
  }
  get value() { return this._value; }
  set value(v) {
    v = String(v);
    if (this.tagName === "SELECT" && !this.options.some(o => o.value === v)) v = "";
    if (this.type === "range") v = String(Math.min(+this.attrs.max, Math.max(+this.attrs.min, +v)));
    this._value = v;
  }
  get innerHTML() { return this._inner; }
  set innerHTML(h) {
    for (const k of this.kids) { all.delete(k); if (k.id && registry.get(k.id) === k) registry.delete(k.id); }
    this._inner = h;
    this.kids = parseElements(h);
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.classList = new ClassList(this); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(t, fn) { (this.handlers[t] ||= []).push(fn); }
  fire(t, ev = {}) { for (const fn of this.handlers[t] || []) fn({target: this, ...ev}); if (t === "click" && this.onclick) this.onclick(); }
  querySelectorAll(sel) { return this.kids.filter(k => matches(k, sel)); }
  closest(sel) { return matches(this, sel) ? this : null; }
  contains() { return false; }
  scrollIntoView() {}
  focus() {}
}
class ClassList {
  constructor(el) { this.el = el; this.set = new Set((el.attrs.class || "").split(/\s+/).filter(Boolean)); }
  add(c) { this.set.add(c); }
  remove(c) { this.set.delete(c); }
  contains(c) { return this.set.has(c); }
}
function matches(el, sel) {
  const m = sel.match(/^(\w*)((?:\.[\w-]+)*)(?:\[([\w-]+)\])?$/);
  if (!m) throw new Error("stub DOM cannot match selector " + sel);
  if (m[1] && el.tagName !== m[1].toUpperCase()) return false;
  for (const c of m[2].split(".").filter(Boolean)) if (!el.classList.contains(c)) return false;
  if (m[3] && !(m[3] in el.attrs)) return false;
  return true;
}
function parseElements(h) {
  const out = [];
  const re = /<(\w+)\b([^>]*)>/g;
  let m;
  while ((m = re.exec(h))) {
    let inner = "";
    if (m[1] === "select") { const end = h.indexOf("</select>", re.lastIndex); inner = h.slice(re.lastIndex, end); }
    const el = new El(m[1], parseAttrs(m[2]), inner);
    out.push(el); all.add(el);
    if (el.id) registry.set(el.id, el);
  }
  return out;
}
// static elements of the page body (outside <script>)
const body = html.slice(html.indexOf("<body>"), html.indexOf("<script>"));
parseElements(body);

const docHandlers = {};
const store = new Map();
const localStorage = {
  getItem(k) { if (blockStorage) throw new Error("blocked"); return store.has(k) ? store.get(k) : null; },
  setItem(k, v) { if (blockStorage) throw new Error("blocked"); store.set(k, String(v)); },
};
const errors = [];

// ---------- minimal Leaflet stub: records what the page draws ----------
const leaflet = {fits: 0, polygons: [], tooltips: []};
class Layer {
  constructor(latlng, options) { this.latlng = latlng; this.options = {...options}; this.handlers = {}; this.el = {classList: new ClassList({attrs: {class: options && options.className || ""}})}; }
  addTo(target) { if (target.addLayer) target.addLayer(this); return this; }
  on(t, fn) { (this.handlers[t] ||= []).push(fn); return this; }
  fire(t) { for (const fn of this.handlers[t] || []) fn({target: this}); return this; }
  bindTooltip(content) { this.tooltip = content; return this; }
  setStyle(s) { Object.assign(this.options, s); return this; }
  setRadius(r) { this.options.radius = r; return this; }
  bringToFront() { return this; }
  getElement() { return this.el; }
  setLatLng(ll) { this.latlng = ll; return this; }
  setContent(c) { this.content = c; return this; }
}
class Group { constructor() { this.layers = []; } addTo() { return this; } addLayer(l) { this.layers.push(l); return this; } clearLayers() { this.layers = []; return this; } }
const L = {
  map: id => {
    if (!registry.get(id)) throw new Error("L.map: no element #" + id);
    return {attributionControl: {setPrefix(p) { leaflet.prefix = p; }}, addLayer() { return this; },
      fitBounds(pts) { if (!pts.length || pts.some(p => !Number.isFinite(p[0]) || !Number.isFinite(p[1]))) throw new Error("fitBounds: bad points"); leaflet.fits++; }};
  },
  tileLayer: (url, o) => { const l = new Layer(null, o); leaflet.tiles = {url, attribution: o.attribution, layer: l}; return l; },
  polygon: (rings, o) => { const p = new Layer(rings, o); leaflet.polygons.push(p); return p; },
  circleMarker: (ll, o) => new Layer(ll, o),
  tooltip: o => { const t = new Layer(null, o); leaflet.tooltips.push(t); return t; },
  layerGroup: () => (leaflet.dots = new Group()),
};
const ctx = {
  document: {
    getElementById: id => registry.get(id) || null,
    querySelectorAll: sel => [...all].filter(e => matches(e, sel)),
    addEventListener: (t, fn) => { (docHandlers[t] ||= []).push(fn); },
  },
  localStorage, matchMedia: () => ({matches: false}), Image: class { set src(v) {} },
  console: {log() {}, error: (...a) => errors.push(a.map(String).join(" "))},
  Intl, Date, JSON, Math, Object, Array, Set, String, Number,
};
if (!noLeaflet) ctx.L = L;
vm.createContext(ctx);
const script = html.slice(html.indexOf("<script>") + 8, html.lastIndexOf("</script>"));
if (noLeaflet) {
  // the page must stop with a visible message, not run without its map
  let thrown = null;
  try { vm.runInContext(script, ctx, {filename: file}); } catch (e) { thrown = String(e.message); }
  console.log(JSON.stringify({thrown, mapErr: registry.get("mapErr").hidden ? null : registry.get("mapErr").textContent}));
  process.exit(thrown ? 0 : 1);
}
vm.runInContext(script, ctx, {filename: file});

// ---------- drive it ----------
const $ = id => registry.get(id);
const APP = vm.runInContext("APP", ctx);
const DATA = APP.listings;
const shownIds = () => [...$("cards").innerHTML.matchAll(/<article [^>]*data-id="([^"]+)"/g)].map(m => m[1]);
const MK = () => vm.runInContext("MK", ctx);
const dots = () => Object.keys(MK());
const fails = [];
function check(ok, what) { if (!ok) fails.push(what); }
function set(id, v) { const e = $(id); if (e.type === "checkbox") e.checked = v; else e.value = v; e.fire("input"); }
const byId = Object.fromEntries(DATA.map(d => [d.id, d]));
const result = {listings: DATA.length, storeWarnVisible: !$("storeWarn").hidden, steps: {},
  header: {lede: $("lede").textContent, stats: $("stats").innerHTML.replace(/<[^>]+>/g, ""), notes: $("notes").innerHTML.replace(/<li>/g, "\n- ").replace(/<[^>]+>/g, "")}};

let ids = shownIds();
result.steps.initial = ids.length;
const live = DATA.filter(d => !d.exclude.length);
const verdict = d => d.photo ? d.photo.verdict : "unchecked";
// default photo filter (only on judged pages) keeps everything but "no"
const liveDefault = APP.judged ? live.filter(d => verdict(d) !== "no") : live;
check(ids.length === liveDefault.length, `initial: ${ids.length} shown, expected ${liveDefault.length} (all not excluded${APP.judged ? ", photos not 'no'" : ""})`);
check(JSON.stringify([...dots()].sort()) === JSON.stringify([...ids].sort()), "initial: map dots differ from cards");
check(!!$("fPhoto") === !!APP.judged, "photo filter present without judgements, or missing with them");
check(/Photos: /.test($("cards").innerHTML) === !!APP.judged, "photo chips present without judgements, or missing with them");
// the rest of the filter checks count against every listing not excluded, so show every photo verdict
if (APP.judged) set("fPhoto", "all");
check(/match/.test($("count").textContent), "count text missing");
check(leaflet.prefix === false, "attribution prefix not removed");
// a page opened as a local file sends no Referer; OpenStreetMap's tile servers refuse that, Esri's do not
check(/^https:\/\/server\.arcgisonline\.com\//.test(leaflet.tiles.url) && /Esri/.test(leaflet.tiles.attribution) && /OpenStreetMap/.test(leaflet.tiles.attribution), "Esri tiles or attribution missing");
check($("tileErr").hidden, "tile error shown before any tile failed");
leaflet.tiles.layer.fire("tileerror");
check(!$("tileErr").hidden && /did not load/.test($("tileErr").textContent), "a failed tile shows no message");
const tileLog = errors.findIndex(e => e.startsWith("Map tile failed"));
check(tileLog >= 0, "a failed tile is not logged");
if (tileLog >= 0) errors.splice(tileLog, 1);
check(leaflet.polygons.length === Math.max(1, APP.areas.length), "area outlines missing");
check(leaflet.tooltips.length === APP.labels.length, "area labels missing");
check(leaflet.dots.layers.filter(l => /\bpt\b/.test(l.options.className)).length === shownIds().length, "marker count differs from cards");
check(Object.values(MK()).every(m => typeof m.tooltip === "string" && m.tooltip.includes(" · ")), "marker tooltip missing");
result.fits = {initial: leaflet.fits};

// price
if (live.length > 1) {
  const prices = live.map(d => d.total).sort((a, b) => a - b);
  const mid = prices[Math.floor(prices.length / 2)];
  set("fPrice", mid);
  ids = shownIds();
  result.steps.price = {max: +$("fPrice").value, shown: ids.length};
  check(ids.every(i => byId[i].total <= +$("fPrice").value), "price: a listing over the slider is shown");
  check(ids.length === live.filter(d => d.total <= +$("fPrice").value).length, "price: count wrong");
  set("fPrice", $("fPrice").attrs.max);
}
// sort: same listings, so the map keeps its view
let fits = leaflet.fits;
set("fSort", "rating");
check(leaflet.fits === fits, "changing the sort re-fitted the map");
set("fSort", "price");
check(leaflet.fits === fits, "changing the sort re-fitted the map");
ids = shownIds();
check(ids.every((id, i) => i === 0 || byId[ids[i - 1]].total <= byId[id].total), "sort by price is not ascending");
// areas
APP.areas.forEach((a, i) => {
  set("fArea" + i, false);
  ids = shownIds();
  result.steps["area_off_" + a.name] = ids.length;
  check(!ids.some(id => byId[id].area === a.name), `area ${a.name}: still shown when unchecked`);
  check(ids.length === live.filter(d => d.area !== a.name).length, `area ${a.name}: count wrong`);
  set("fArea" + i, true);
});
// rating
set("fRating", "4.7");
ids = shownIds();
check(ids.every(id => byId[id].rating === null || byId[id].rating >= 4.7), "rating filter lets a lower rating through");
set("fUnrated", false);
check(shownIds().every(id => byId[id].rating !== null), "unrated still shown");
set("fRating", "0"); set("fUnrated", true);
// studio
if ($("fStudio")) {
  set("fStudio", true);
  ids = shownIds();
  result.steps.no_studios = ids.length;
  check(!ids.some(id => byId[id].studio), "studio still shown");
  check(ids.length === live.filter(d => !d.studio).length, "studio: count wrong");
  set("fStudio", false);
}
APP.flags.forEach((l, i) => {
  set("fFlag" + i, true);
  ids = shownIds();
  result.steps["hide_" + l] = ids.length;
  check(!ids.some(id => byId[id].flag.some(h => h.label === l)), `flag ${l}: still shown`);
  check(ids.length === live.filter(d => !d.flag.some(h => h.label === l)).length, `flag ${l}: count wrong`);
  set("fFlag" + i, false);
});
APP.highlights.forEach((l, i) => {
  set("fHl" + i, true);
  ids = shownIds();
  result.steps["must_" + l] = ids.length;
  check(ids.length === live.filter(d => d.highlight.some(h => h.label === l)).length, `highlight ${l}: count wrong`);
  set("fHl" + i, false);
});
APP.amenity_filters.forEach((a, i) => {
  set("fAm" + i, true);
  ids = shownIds();
  result.steps["amenity_" + a] = ids.length;
  check(ids.length === live.filter(d => d.am[a]).length, `amenity ${a}: count wrong`);
  set("fAm" + i, false);
});
// excluded view
set("fShow", "excluded");
ids = shownIds();
result.steps.excluded = ids.length;
check(ids.length === DATA.filter(d => d.exclude.length).length, "excluded view count wrong");
set("fShow", "active");

// photo check
if (APP.judged) {
  const want = {all: live, ok: liveDefault, great: live.filter(d => verdict(d) === "great")};
  for (const v of ["ok", "great", "all"]) {
    set("fPhoto", v);
    ids = shownIds();
    result.steps["photo_" + v] = ids.length;
    check(ids.length === want[v].length, `photo filter ${v}: ${ids.length} shown, expected ${want[v].length}`);
  }
  set("fSort", "photos");
  ids = shownIds();
  result.steps.photo_order = ids;
  const rank = {great: 0, ok: 1, unchecked: 2, no: 3};
  check(ids.every((id, i) => i === 0 || rank[verdict(byId[ids[i - 1]])] <= rank[verdict(byId[id])]), "sort by photos is not best first");
  for (const [id, m] of Object.entries(MK())) check(m.options.className.split(" ").includes("c-" + verdict(byId[id])), `dot ${id}: class ${m.options.className} does not match its photo verdict`);
  for (const d of live.filter(d => d.photo)) check($("cards").innerHTML.includes(`Photos: ${verdict(d)}`) && $("cards").innerHTML.includes(d.photo.note.replace(/&/g, "&amp;")), `card ${d.id}: verdict or note missing`);
  check($("cards").innerHTML.includes("Photos: not checked") === live.some(d => !d.photo), "'not checked' chip wrong");
  result.legend = $("legend").innerHTML.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  set("fSort", "price");
  set("fPhoto", "all");
}

// favourite + delete through the card buttons
ids = shownIds();
if (ids.length >= 2) {
  const btn = (kind, id) => $("cards").querySelectorAll("button[data-mark]").find(b => b.dataset.mark === kind && b.dataset.id === id);
  const click = el => $("cards").fire("click", {target: el});
  click(btn("fav", ids[0]));
  click(btn("del", ids[1]));
  check(!shownIds().includes(ids[1]), "deleted listing still shown");
  set("fShow", "fav");
  result.steps.favourites = shownIds();
  check(JSON.stringify(shownIds()) === JSON.stringify([ids[0]]), "favourites view wrong");
  set("fShow", "deleted");
  check(JSON.stringify(shownIds()) === JSON.stringify([ids[1]]), "deleted view wrong");
  set("fShow", "active");
  if (!blockStorage) {
    const saved = JSON.parse(store.get(APP.key + ":marks"));
    result.savedMarks = saved;
    check(saved.fav[0] === ids[0] && saved.del[0] === ids[1], "marks not saved to localStorage");
    check(JSON.parse(store.get(APP.key + ":filters")).fShow === "active", "filters not saved");
  }
  // photo viewer
  const photo = $("cards").querySelectorAll("button[data-g]")[0];
  click(photo);
  result.lightbox = {open: !$("lb").hidden, src: $("lbImg").src};
  check(!$("lb").hidden && /^https?:\/\//.test($("lbImg").src), "photo viewer did not open");
  $("lbNext").fire("click");
  for (const fn of docHandlers.keydown || []) fn({key: "Escape"});
  check($("lb").hidden, "Escape did not close the viewer");
  // map dot click highlights its card; hovering a card enlarges its dot
  const [dotId, dot] = Object.entries(MK())[0];
  dot.fire("click");
  check($("c" + dotId).classList.contains("hl"), "clicking a dot did not highlight its card");
  const card = $("c" + dotId);
  $("cards").fire("mouseover", {target: card, relatedTarget: null});
  check(dot.options.radius === 10 && dot.getElement().classList.contains("hov"), "hovering a card did not enlarge its dot");
  $("cards").fire("mouseout", {target: card, relatedTarget: null});
  check(dot.options.radius === 6 && !dot.getElement().classList.contains("hov"), "leaving a card did not shrink its dot");
}
result.errors = errors;
result.fails = fails;
console.log(JSON.stringify(result));
process.exit(fails.length || (errors.length && !blockStorage) ? 1 : 0);
