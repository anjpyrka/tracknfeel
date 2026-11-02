/* tracknfeel viewer - single-file app, no build step.
   Data: site/data/manifest.json + encrypted gzip JSON bundles (see pipeline/build.py). */
(() => {
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const DATA = "data/";
const STYLE_LIGHT = "https://tiles.openfreemap.org/styles/positron";
const STYLE_DARK = "https://tiles.openfreemap.org/styles/dark";
const CITY_COLORS = ["#3B6FB6","#D2672A","#4F9D69","#B23A8D","#E0B51F","#6D4BB8","#1FA3A3","#C23B3B","#8C7A2A","#D98AB8","#4A8FD0","#7A5230"];
const OTHER_COLOR = "#A8A49B";
const MODE_LABEL = { car:"by car", walk:"on foot", run:"running", bike:"by bike", bus:"by bus", train:"by train", metro:"by metro", tram:"by tram", flight:"flying", moto:"by motorbike", ferry:"by ferry", boat:"by boat", ski:"skiing", taxi:"by taxi", hike:"hiking", "?":"moving", still:"still" };
const MODE_COLOR = { walk:"#4F9D69", run:"#4F9D69", hike:"#4F9D69", bike:"#1FA3A3", flight:"#B23A8D", train:"#6D4BB8", metro:"#6D4BB8", tram:"#6D4BB8", bus:"#6D4BB8", ferry:"#3B6FB6", boat:"#3B6FB6" };

const S = {
  key: null, manifest: null, idx: null, years: new Map(), loading: new Map(),
  date: null, view: "day", map: null, mapReady: false, playing: null, cityColor: new Map(),
  dates: [], dateSet: new Set(), dayMarkers: [], dark: matchMedia("(prefers-color-scheme: dark)").matches,
};

/* ---------------------------------------------------------------- crypto */
const enc = new TextEncoder();
async function deriveKey(pass, saltHex, iter) {
  const base = await crypto.subtle.importKey("raw", enc.encode(pass), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt: hex2buf(saltHex), iterations: iter, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
}
function hex2buf(h) { const a = new Uint8Array(h.length / 2); for (let i = 0; i < a.length; i++) a[i] = parseInt(h.substr(i * 2, 2), 16); return a; }
async function decryptBlob(buf) {
  const u = new Uint8Array(buf);
  if (String.fromCharCode(...u.slice(0, 4)) !== "TNF1") throw new Error("bad magic");
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: u.slice(4, 16) }, S.key, u.slice(16));
  return plain;
}
async function gunzipJSON(buf) {
  const ds = new DecompressionStream("gzip");
  const stream = new Blob([buf]).stream().pipeThrough(ds);
  return JSON.parse(await new Response(stream).text());
}
async function loadEncrypted(name) {
  const r = await fetch(`${DATA}${name}?v=${encodeURIComponent(S.manifest.built)}`, { cache: "force-cache" });
  if (!r.ok) throw new Error("fetch " + name);
  return gunzipJSON(await decryptBlob(await r.arrayBuffer()));
}
// key persistence (IndexedDB can store a non-extractable CryptoKey)
function idb() {
  return new Promise((res, rej) => {
    const o = indexedDB.open("tracknfeel", 1);
    o.onupgradeneeded = () => o.result.createObjectStore("kv");
    o.onsuccess = () => res(o.result); o.onerror = () => rej(o.error);
  });
}
async function kvSet(k, v) { try { const db = await idb(); const tx = db.transaction("kv", "readwrite"); tx.objectStore("kv").put(v, k); await new Promise(r => tx.oncomplete = r); } catch (e) {} }
async function kvGet(k) { try { const db = await idb(); return await new Promise(r => { const q = db.transaction("kv").objectStore("kv").get(k); q.onsuccess = () => r(q.result); q.onerror = () => r(undefined); }); } catch (e) { return undefined; } }
async function kvDel(k) { try { const db = await idb(); db.transaction("kv", "readwrite").objectStore("kv").delete(k); } catch (e) {} }

/* ---------------------------------------------------------------- boot */
async function boot() {
  S.manifest = await (await fetch(DATA + "manifest.json", { cache: "no-cache" })).json();
  const first = S.manifest.files[0]; if (first) $("#gateLede").textContent = `Where I was, every day since ${first.year}.`;
  const saved = await kvGet("key");
  if (saved) { S.key = saved; if (await verifyKey()) return start(); S.key = null; await kvDel("key"); }
  $("#gateForm").addEventListener("submit", async e => {
    e.preventDefault();
    const btn = $("#unlockBtn"); btn.disabled = true; btn.textContent = "Unlocking…"; $("#gateErr").hidden = true;
    try {
      S.key = await deriveKey($("#pass").value, S.manifest.salt, S.manifest.iter);
      if (!(await verifyKey())) throw new Error("wrong");
      if ($("#remember").checked) await kvSet("key", S.key);
      await start();
    } catch (err) { S.key = null; $("#gateErr").hidden = false; }
    btn.disabled = false; btn.textContent = "Unlock";
  });
}
async function verifyKey() {
  try { const p = await decryptBlob(hex2buf(S.manifest.check).buffer); return new TextDecoder().decode(p) === "tracknfeel-ok"; } catch (e) { return false; }
}
async function start() {
  $("#unlockBtn").textContent = "Opening the archive…";
  S.idx = await loadEncrypted("index.bin");
  S.dates = Object.keys(S.idx.days).sort(); S.dateSet = new Set(S.dates);
  assignCityColors();
  $("#gate").hidden = true; $("#app").hidden = false;
  initMap(); initRibbon(); initUI();
  const h = parseHash();
  S.view = h.view || "day";
  setView(S.view, false);
  goto(h.date || S.dates[S.dates.length - 1], false);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

/* ---------------------------------------------------------------- helpers */
const pad = n => String(n).padStart(2, "0");
const hm = m => `${pad(Math.floor(m / 60) % 24)}:${pad(Math.floor(m % 60))}`;
const dur = m => m < 60 ? `${Math.round(m)} min` : m < 1440 ? `${Math.floor(m / 60)} h ${pad(Math.round(m % 60))}` : `${(m / 1440).toFixed(1)} d`;
const fmtDate = (d, o = { weekday: "long", day: "numeric", month: "long", year: "numeric" }) => new Date(d + "T12:00:00").toLocaleDateString("en-GB", o);
const fmtShort = d => fmtDate(d, { day: "numeric", month: "short", year: "numeric" });
const fmtMonth = ym => new Date(ym + "-15T12:00:00").toLocaleDateString("en-GB", { month: "short", year: "numeric" });
const addDays = (d, n) => { const t = new Date(d + "T12:00:00"); t.setDate(t.getDate() + n); return t.toISOString().slice(0, 10); };
const km = n => n >= 100 ? Math.round(n).toLocaleString("en-GB") : n.toFixed(1);
const cityName = i => i >= 0 && S.idx.cities[i] ? S.idx.cities[i][0] : "unknown place";
const cityCC = i => i >= 0 && S.idx.cities[i] ? S.idx.cities[i][2] : "";
const countryName = cc => S.idx.countries[cc] || cc;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function decodePolyline(str) {
  let index = 0, lat = 0, lng = 0; const coords = [];
  while (index < str.length) {
    let b, shift = 0, result = 0;
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lng += (result & 1) ? ~(result >> 1) : (result >> 1);
    coords.push([lng / 1e5, lat / 1e5]);
  }
  return coords;
}
function assignCityColors() {
  const cnt = new Map();
  for (const d of S.dates) { const c = S.idx.days[d][0]; if (c >= 0) cnt.set(c, (cnt.get(c) || 0) + 1); }
  S.topCities = [...cnt.entries()].sort((a, b) => b[1] - a[1]);
  S.topCities.slice(0, CITY_COLORS.length).forEach(([c], i) => S.cityColor.set(c, CITY_COLORS[i]));
  S.cityCount = cnt;
}
const colorFor = c => S.cityColor.get(c) || OTHER_COLOR;
function dayColor(d) {
  const rec = S.idx.days[d]; if (!rec) return null;
  if (rec[2].includes("R") || rec[2].includes("P")) return getComputedStyle(document.documentElement).getPropertyValue("--recon").trim();
  return colorFor(rec[0]);
}
async function getYear(y) {
  y = String(y);
  if (S.years.has(y)) return S.years.get(y);
  if (!S.loading.has(y)) S.loading.set(y, loadEncrypted(`${y}.bin`).then(j => { S.years.set(y, j); return j; }));
  return S.loading.get(y);
}
function parseHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  return { date: p.get("d") && S.dateSet.has(p.get("d")) ? p.get("d") : null, view: p.get("v") };
}
function writeHash() { history.replaceState(null, "", `#d=${S.date}&v=${S.view}`); }

/* ---------------------------------------------------------------- map */
function initMap() {
  S.map = new maplibregl.Map({
    container: "map", style: S.dark ? STYLE_DARK : STYLE_LIGHT, center: [12.48, 41.9], zoom: 3,
    attributionControl: { compact: true }, hash: false,
  });
  S.map.on("load", () => { addLayers(); S.mapReady = true; if (S.pending) { const p = S.pending; S.pending = null; p(); } });
  S.map.on("error", e => { /* keep quiet on tile hiccups */ });
}
function addLayers() {
  const m = S.map;
  m.addSource("heat", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addSource("tracks", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addSource("stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  m.addLayer({ id: "heat", type: "heatmap", source: "heat", paint: {
    "heatmap-weight": ["interpolate", ["linear"], ["get", "w"], 1, 0.15, 50, 0.6, 500, 1],
    "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 2, 0.6, 9, 1.4, 14, 2.5],
    "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 2, 4, 8, 12, 14, 28],
    "heatmap-opacity": 0.85,
    "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(37,72,240,0)", 0.15, "rgba(37,72,240,0.35)", 0.4, "rgba(31,163,163,0.7)", 0.7, "rgba(224,181,31,0.9)", 1, "rgba(210,103,42,1)"],
  }});
  m.addLayer({ id: "tracks-casing", type: "line", source: "tracks", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 4, 14, 7], "line-opacity": 0.8 } });
  m.addLayer({ id: "tracks", type: "line", source: "tracks", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["get", "color"], "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2, 14, 3.5], "line-opacity": 0.95,
      "line-dasharray": ["case", ["==", ["get", "src"], "A"], ["literal", [1, 2]], ["literal", [1, 0]]] } });
  m.addLayer({ id: "stops", type: "circle", source: "stops", paint: {
    "circle-radius": ["interpolate", ["linear"], ["get", "mins"], 5, 4, 120, 8, 600, 12],
    "circle-color": ["case", ["in", ["get", "src"], ["literal", ["R", "P"]]], "#D08A1E", "#15212E"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
  m.on("click", "stops", e => {
    const p = e.features[0].properties;
    new maplibregl.Popup({ closeButton: false, offset: 10 }).setLngLat(e.lngLat).setHTML(`<b>${esc(p.name)}</b><br>${esc(p.meta)}`).addTo(m);
  });
  m.on("mouseenter", "stops", () => m.getCanvas().style.cursor = "pointer");
  m.on("mouseleave", "stops", () => m.getCanvas().style.cursor = "");
}
function whenMap(fn) { if (S.mapReady) fn(); else S.pending = fn; }
function setSource(id, features) { whenMap(() => S.map.getSource(id).setData({ type: "FeatureCollection", features })); }
function heatFromIndex() {
  return S.idx.heat.map(([lat, lng, w]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lng, lat] }, properties: { w } }));
}
function heatFromYear(yj) {
  const feats = [];
  for (const d of Object.keys(yj.days)) {
    const day = yj.days[d];
    for (const v of day.v) feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [v[3], v[2]] }, properties: { w: Math.max(1, (v[1] - v[0]) / 10) } });
    for (const t of day.t) { const c = decodePolyline(t[2]); for (let i = 0; i < c.length; i += 2) feats.push({ type: "Feature", geometry: { type: "Point", coordinates: c[i] }, properties: { w: 2 } }); }
  }
  return feats;
}
function showDayOnMap(day, fit = true) {
  const tracks = [], stops = [];
  for (const t of day.t) {
    const coords = decodePolyline(t[2]); if (coords.length < 2) continue;
    const mode = modeForTrack(day, t);
    tracks.push({ type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { color: MODE_COLOR[mode] || cssVar("--accent"), src: t[3] } });
  }
  for (const v of day.v) {
    stops.push({ type: "Feature", geometry: { type: "Point", coordinates: [v[3], v[2]] }, properties: { name: placeName(v, day), meta: `${hm(v[0])} to ${hm(v[1])}, ${dur(v[1] - v[0])}`, mins: v[1] - v[0], src: v[6] } });
  }
  setSource("tracks", tracks); setSource("stops", stops); setSource("heat", []);
  if (fit) whenMap(() => fitTo([...tracks, ...stops]));
}
function fitTo(features, pad) {
  const b = new maplibregl.LngLatBounds(); let n = 0;
  for (const f of features) { const g = f.geometry; (g.type === "Point" ? [g.coordinates] : g.coordinates).forEach(c => { b.extend(c); n++; }); }
  if (!n) return;
  const phone = innerWidth <= 760;
  const padding = pad || (phone ? { top: 110, bottom: innerHeight * 0.46 + 70, left: 24, right: 24 } : { top: 90, bottom: 120, left: 60, right: 420 });
  if (n === 1) S.map.easeTo({ center: b.getCenter(), zoom: 13, padding, duration: 600 });
  else S.map.fitBounds(b, { padding, maxZoom: 15, duration: 700 });
}
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function modeForTrack(day, t) {
  let best = "?", bo = 0;
  for (const mv of day.m) { const o = Math.min(mv[1], t[1]) - Math.max(mv[0], t[0]); if (o > bo) { bo = o; best = mv[2]; } }
  return best;
}
function placeName(v, day) {
  const p = S.idx.places[v[4]];
  if (p && p[5]) return p[5];
  const sem = v[5];
  if (sem === "HOME" || sem === "INFERRED_HOME") return "Home";
  if (sem === "WORK" || sem === "INFERRED_WORK") return "Work";
  if (sem === "PHOTO") return "Photo taken here";
  const city = p && p[6] >= 0 ? cityName(p[6]) : cityName(day.c);
  if (v[6] === "R") return `${city} (reconstructed)`;
  return `Stop in ${city}`;
}

/* ---------------------------------------------------------------- ribbon */
function initRibbon() {
  const cv = $("#ribbon");
  const draw = () => drawRibbon();
  new ResizeObserver(draw).observe(cv);
  let dragging = false;
  const pick = e => {
    const r = cv.getBoundingClientRect(); const x = Math.max(0, Math.min(r.width - 1, e.clientX - r.left));
    const i = Math.floor(x / r.width * S.span); return addDays(S.dates[0], i);
  };
  cv.addEventListener("pointerdown", e => { dragging = true; cv.setPointerCapture(e.pointerId); gotoNearest(pick(e)); });
  cv.addEventListener("pointermove", e => {
    const d = pick(e); const tip = $("#ribbonTip"); const r = cv.getBoundingClientRect();
    tip.hidden = false; tip.style.left = (e.clientX - r.left + 12) + "px";
    const rec = S.idx.days[d]; tip.textContent = rec ? `${fmtShort(d)}, ${cityName(rec[0])}` : `${fmtShort(d)}, no data`;
    if (dragging) gotoNearest(d);
  });
  cv.addEventListener("pointerup", () => dragging = false);
  cv.addEventListener("pointerleave", () => { $("#ribbonTip").hidden = true; dragging = false; });
  // legend
  $("#ribbonLegend").innerHTML = S.topCities.slice(0, 6).map(([c]) => `<span><i style="background:${colorFor(c)}"></i>${esc(cityName(c))}</span>`).join("")
    + `<span><i style="background:${cssVar("--recon")}"></i>reconstructed</span>`;
}
function drawRibbon() {
  const cv = $("#ribbon"); const r = cv.getBoundingClientRect(); if (!r.width) return;
  const dpr = devicePixelRatio || 1; cv.width = r.width * dpr; cv.height = r.height * dpr;
  const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
  const first = S.dates[0], last = S.dates[S.dates.length - 1];
  S.span = Math.round((new Date(last) - new Date(first)) / 864e5) + 1;
  const w = r.width / S.span, H = r.height, bandTop = 14;
  ctx.clearRect(0, 0, r.width, H);
  // gap base
  ctx.fillStyle = cssVar("--gap"); ctx.fillRect(0, bandTop, r.width, H - bandTop);
  // days (merge same-colour runs for speed)
  let runStart = 0, runColor = null, i = 0, d = first;
  const flush = () => { if (runColor) { ctx.fillStyle = runColor; ctx.fillRect(runStart * w, bandTop, (i - runStart) * w + 0.5, H - bandTop); } };
  for (; i < S.span; i++, d = addDays(d, 1)) {
    const c = dayColor(d);
    if (c !== runColor) { flush(); runStart = i; runColor = c; }
  }
  flush();
  // year ticks
  ctx.fillStyle = cssVar("--ink-3"); ctx.font = "11px Public Sans, system-ui"; ctx.textBaseline = "top";
  const y0 = +first.slice(0, 4), y1 = +last.slice(0, 4);
  for (let y = y0; y <= y1; y++) {
    const i = Math.round((new Date(`${y}-01-01`) - new Date(first)) / 864e5); if (i < 0) continue;
    const x = i * w; ctx.fillRect(x, bandTop - 4, 1, 4);
    if (w * 365 > 34 || (w * 365 > 17 && y % 2 === 0)) ctx.fillText(String(y), x + 3, 0);
  }
  // current marker
  if (S.date) {
    const i = Math.round((new Date(S.date) - new Date(first)) / 864e5); const x = i * w + w / 2;
    ctx.fillStyle = cssVar("--ink"); ctx.fillRect(x - 1, bandTop - 6, 2, H - bandTop + 6);
    ctx.beginPath(); ctx.moveTo(x - 5, bandTop - 8); ctx.lineTo(x + 5, bandTop - 8); ctx.lineTo(x, bandTop - 2); ctx.fill();
  }
}

/* ---------------------------------------------------------------- navigation */
function gotoNearest(d) {
  if (S.dateSet.has(d)) return goto(d);
  // nearest recorded day
  let lo = 0, hi = S.dates.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (S.dates[mid] < d) lo = mid + 1; else hi = mid; }
  const a = S.dates[Math.max(0, lo - 1)], b = S.dates[lo];
  goto(Math.abs(new Date(a) - new Date(d)) < Math.abs(new Date(b) - new Date(d)) ? a : b);
}
async function goto(d, fit = true) {
  S.date = d; writeHash(); drawRibbon();
  const rec = S.idx.days[d];
  $("#ribbonLabel").textContent = `${fmtShort(d)}, ${cityName(rec[0])}`;
  if (S.view === "day") await renderDay(fit);
  else if (S.view === "year") await renderYear();
}
function step(n) { const i = S.dates.indexOf(S.date); const j = Math.max(0, Math.min(S.dates.length - 1, i + n)); if (j !== i) goto(S.dates[j], true); }
function setView(v, render = true) {
  S.view = v; $$(".views button").forEach(b => b.classList.toggle("on", b.dataset.view === v)); writeHash();
  if (!render) return;
  if (v === "day") renderDay(true); else if (v === "year") renderYear(); else if (v === "life") renderLife(); else renderPlaces();
}

/* ---------------------------------------------------------------- day view */
async function renderDay(fit) {
  const d = S.date, y = d.slice(0, 4), rec = S.idx.days[d];
  const body = $("#panelBody");
  body.innerHTML = `<div class="dow">${fmtDate(d, { weekday: "long" })}</div><h2>${fmtDate(d, { day: "numeric", month: "long", year: "numeric" })}</h2><p class="sub">Loading…</p>`;
  const yj = await getYear(y); if (S.date !== d) return;
  const day = yj.days[d]; if (!day) { body.innerHTML += "<p>No data for this day.</p>"; return; }
  const touched = (day.ct || []).map(cityName).filter((v, i, a) => a.indexOf(v) === i);
  const cc = [...new Set((day.ct || []).map(cityCC))].map(countryName);
  const recon = day.s.includes("R") || day.s.includes("P"), gps = day.s.includes("G");
  const items = [...day.v.map(v => ({ t: v[0], kind: "visit", v })), ...day.m.map(m => ({ t: m[0], kind: "move", m }))].sort((a, b) => a.t - b.t);
  let html = `<div class="dow">${fmtDate(d, { weekday: "long" })}</div>
    <h2>${fmtDate(d, { day: "numeric", month: "long", year: "numeric" })}${recon ? '<span class="chip">reconstructed</span>' : ""}</h2>
    <p class="sub"><b>${esc(touched.join(", ") || cityName(day.c))}</b>${cc.length ? ", " + esc(cc.join(", ")) : ""} · ${km(day.km)} km${gps ? " · GPS log" : ""}</p>`;
  if (day.n) html += `<p class="hint">${day.n.map(n => `${esc(n.label)} <small>(${esc(n.source)}, ${esc(n.confidence)} confidence)</small>`).join("<br>")}</p>`;
  html += `<ol class="tl">` + items.map(it => {
    if (it.kind === "visit") {
      const v = it.v; return `<li class="visit ${v[6] === "R" || v[6] === "P" ? "recon" : ""}" data-ll="${v[2]},${v[3]}"><time>${hm(v[0])}</time><span class="dot"></span><div class="tl-name">${esc(placeName(v, day))}</div><div class="tl-meta">${dur(v[1] - v[0])}${v[1] >= 1440 ? ", overnight" : ""}</div></li>`;
    }
    const m = it.m; return `<li class="move"><time>${hm(m[0])}</time><span class="dot"></span>${esc(MODE_LABEL[m[2]] || m[2])}, ${km(m[3] / 1000)} km, ${dur(m[1] - m[0])}</li>`;
  }).join("") + `</ol>`;
  if (!items.length) html += `<p class="hint">Only a track was recorded on this day.</p>`;
  body.innerHTML = html;
  $$(".tl .visit", body).forEach(li => li.addEventListener("click", () => { const [la, ln] = li.dataset.ll.split(",").map(Number); S.map.easeTo({ center: [ln, la], zoom: Math.max(S.map.getZoom(), 14) }); }));
  showDayOnMap(day, fit);
}

/* ---------------------------------------------------------------- year view */
async function renderYear() {
  const y = S.date.slice(0, 4);
  const body = $("#panelBody");
  const days = S.dates.filter(d => d.startsWith(y));
  const cnt = new Map(), ccs = new Map(); let kmT = 0, recon = 0;
  for (const d of days) { const r = S.idx.days[d]; cnt.set(r[0], (cnt.get(r[0]) || 0) + 1); const cc = cityCC(r[0]); ccs.set(cc, (ccs.get(cc) || 0) + 1); kmT += r[1]; if (r[2].includes("R") || r[2].includes("P")) recon++; }
  const top = [...cnt.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  const years = [...new Set(S.dates.map(d => d.slice(0, 4)))];
  const yi = years.indexOf(y);
  let html = `<div class="year-nav"><button class="icon-btn" id="yPrev" ${yi === 0 ? "disabled" : ""}>‹</button><h2>${y}</h2><button class="icon-btn" id="yNext" ${yi === years.length - 1 ? "disabled" : ""}>›</button></div>
    <div class="stats"><div class="stat"><b>${days.length}</b><span>days recorded</span></div><div class="stat"><b>${km(kmT)}</b><span>km</span></div><div class="stat"><b>${ccs.size}</b><span>countries</span></div></div>`;
  html += `<div class="cal">`;
  for (let m = 1; m <= 12; m++) {
    html += `<span class="mo">${new Date(`${y}-${pad(m)}-15`).toLocaleDateString("en-GB", { month: "short" })}</span>`;
    const dim = new Date(+y, m, 0).getDate();
    for (let dd = 1; dd <= 31; dd++) {
      if (dd > dim) { html += `<span class="d none"></span>`; continue; }
      const date = `${y}-${pad(m)}-${pad(dd)}`; const r = S.idx.days[date];
      const col = r ? colorFor(r[0]) : "";
      html += `<span class="d ${r && (r[2].includes("R") || r[2].includes("P")) ? "recon" : ""} ${date === S.date ? "sel" : ""}" data-d="${date}" title="${fmtShort(date)}${r ? ", " + esc(cityName(r[0])) : ", no data"}" style="${col ? "background:" + col : ""}"></span>`;
    }
  }
  html += `</div>`;
  html += `<h3>Where the year went</h3><div class="bars">` + top.map(([c, n]) => `<div class="bar" data-c="${c}"><span class="name"><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${colorFor(c)};margin-right:6px"></i>${esc(cityName(c))}, ${esc(countryName(cityCC(c)))}</span><span class="num">${n} days</span><div class="track"><div class="fill" style="width:${n / days.length * 100}%;background:${colorFor(c)}"></div></div></div>`).join("") + `</div>`;
  const gaps = S.idx.gaps.filter(g => g[0].startsWith(y) || g[1].startsWith(y));
  if (gaps.length) html += `<h3>Missing</h3><p class="hint">${gaps.map(g => `${fmtShort(g[0])} to ${fmtShort(g[1])} (${g[2]} days)`).join("<br>")}</p>`;
  if (recon) html += `<p class="hint">${recon} days in this year are reconstructed from other sources (hatched).</p>`;
  body.innerHTML = html;
  $("#yPrev").onclick = () => { const t = S.dates.filter(d => d.startsWith(years[yi - 1])); goto(t[t.length - 1], false); };
  $("#yNext").onclick = () => { goto(S.dates.find(d => d.startsWith(years[yi + 1])), false); };
  $$(".cal .d[data-d]", body).forEach(el => el.addEventListener("click", () => { const dd = el.dataset.d; if (S.dateSet.has(dd)) { setView("day", false); goto(dd); } }));
  $$(".bar", body).forEach(el => el.addEventListener("click", () => searchCity(+el.dataset.c)));
  const yj = await getYear(y); if (!S.date.startsWith(y) || S.view !== "year") return;
  setSource("tracks", []); setSource("stops", []);
  const feats = heatFromYear(yj); setSource("heat", feats);
  whenMap(() => fitTo(feats.filter((f, i) => i % 7 === 0)));
}

/* ---------------------------------------------------------------- life view */
function chapters() {
  // dominant city per month, merged into runs
  const months = new Map();
  for (const d of S.dates) { const ym = d.slice(0, 7); const c = S.idx.days[d][0]; if (!months.has(ym)) months.set(ym, new Map()); const m = months.get(ym); m.set(c, (m.get(c) || 0) + 1); }
  const runs = [];
  for (const [ym, m] of [...months.entries()].sort()) {
    const [c] = [...m.entries()].sort((a, b) => b[1] - a[1])[0];
    const last = runs[runs.length - 1];
    if (last && last.c === c) last.to = ym; else runs.push({ c, from: ym, to: ym });
  }
  // absorb short interruptions (1 month) into the surrounding run
  const out = [];
  for (const r of runs) {
    const prev = out[out.length - 1];
    const len = (a, b) => (+b.slice(0, 4) - +a.slice(0, 4)) * 12 + (+b.slice(5) - +a.slice(5)) + 1;
    if (prev && len(r.from, r.to) <= 1 && out.length >= 1) { prev.to = r.to; prev.pending = true; continue; }
    if (prev && prev.c === r.c) { prev.to = r.to; continue; }
    out.push({ ...r });
  }
  return out.filter(r => (+r.to.slice(0, 4) - +r.from.slice(0, 4)) * 12 + (+r.to.slice(5) - +r.from.slice(5)) >= 1);
}
function renderLife() {
  const body = $("#panelBody");
  const ccs = new Map(); let kmT = 0;
  for (const d of S.dates) { const r = S.idx.days[d]; const cc = cityCC(r[0]); ccs.set(cc, (ccs.get(cc) || 0) + 1); kmT += r[1]; }
  const topC = [...ccs.entries()].sort((a, b) => b[1] - a[1]);
  const first = S.dates[0], last = S.dates[S.dates.length - 1];
  const yearsN = ((new Date(last) - new Date(first)) / 864e5 / 365.25).toFixed(1);
  let html = `<h2>${yearsN} years on record</h2><p class="sub">${fmtShort(first)} to ${fmtShort(last)}</p>
    <div class="stats"><div class="stat"><b>${S.dates.length.toLocaleString("en-GB")}</b><span>days</span></div><div class="stat"><b>${ccs.size}</b><span>countries</span></div><div class="stat"><b>${S.cityCount.size}</b><span>cities</span></div></div>
    <div class="stats"><div class="stat"><b>${km(kmT)}</b><span>km moved</span></div><div class="stat"><b>${(kmT / 40075).toFixed(1)}</b><span>times around the earth</span></div><div class="stat"><b>${S.idx.gaps.reduce((a, g) => a + g[2], 0)}</b><span>days missing</span></div></div>`;
  html += `<h3>Chapters</h3><ul class="chapters">` + chapters().map(r => `<li data-from="${r.from}"><span class="sw" style="background:${colorFor(r.c)}"></span><span>${esc(cityName(r.c))}</span><span class="when">${fmtMonth(r.from)} to ${fmtMonth(r.to)}</span></li>`).join("") + `</ul>`;
  html += `<h3>Countries</h3><div class="bars">` + topC.slice(0, 14).map(([cc, n]) => `<div class="bar" data-cc="${cc}"><span class="name">${esc(countryName(cc))}</span><span class="num">${n} days</span><div class="track"><div class="fill" style="width:${n / topC[0][1] * 100}%"></div></div></div>`).join("") + `</div>`;
  html += `<h3>Gaps in the record</h3><p class="hint">${S.idx.gaps.filter(g => g[2] >= 3).map(g => `${fmtShort(g[0])} to ${fmtShort(g[1])}, ${g[2]} days`).join("<br>")}</p>`;
  body.innerHTML = html;
  $$(".chapters li", body).forEach(li => li.addEventListener("click", () => { const d = S.dates.find(x => x >= li.dataset.from); setView("day", false); goto(d); }));
  $$(".bar", body).forEach(el => el.addEventListener("click", () => searchCountry(el.dataset.cc)));
  setSource("tracks", []); setSource("stops", []); setSource("heat", heatFromIndex());
  whenMap(() => S.map.easeTo({ center: [30, 35], zoom: innerWidth <= 760 ? 0.8 : 1.6, duration: 800 }));
}

/* ---------------------------------------------------------------- places view */
function renderPlaces() {
  const body = $("#panelBody");
  const list = Object.entries(S.idx.places).sort((a, b) => b[1][2] - a[1][2]).slice(0, 120);
  let html = `<h2>Places</h2><p class="sub">Ranked by time spent. Names come from <span class="hint">inbox/labels.json</span>; unnamed stops show their city.</p><ul class="place-list">`;
  html += list.map(([k, p]) => {
    const name = p[5] || (p[4] === "HOME" || p[4] === "INFERRED_HOME" ? "Home" : p[4] === "WORK" ? "Work" : `Stop in ${cityName(p[6])}`);
    return `<li data-ll="${p[0]},${p[1]}"><span><span class="pn">${esc(name)}</span><br><span class="pm">${esc(cityName(p[6]))}, ${esc(countryName(cityCC(p[6])))}</span></span><span class="pm">${dur(p[2])}<br>${p[3]} visits</span></li>`;
  }).join("") + `</ul>`;
  body.innerHTML = html;
  const feats = list.map(([k, p]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p[1], p[0]] }, properties: { name: p[5] || p[4] || "Stop", meta: `${dur(p[2])}, ${p[3]} visits`, mins: Math.min(600, p[2] / 60), src: "" } }));
  setSource("tracks", []); setSource("heat", []); setSource("stops", feats);
  whenMap(() => fitTo(feats.slice(0, 40)));
  $$(".place-list li", body).forEach(li => li.addEventListener("click", () => { const [la, ln] = li.dataset.ll.split(",").map(Number); S.map.easeTo({ center: [ln, la], zoom: 15 }); }));
}

/* ---------------------------------------------------------------- search */
const MONTHS = ["january","february","march","april","may","june","july","august","september","october","november","december"];
function runsFor(pred) {
  const runs = []; let cur = null;
  for (const d of S.dates) {
    if (pred(S.idx.days[d])) { if (cur && addDays(cur.to, 1) >= d && (new Date(d) - new Date(cur.to)) / 864e5 <= 2) cur.to = d; else { cur = { from: d, to: d }; runs.push(cur); } }
  }
  return runs.map(r => ({ ...r, n: Math.round((new Date(r.to) - new Date(r.from)) / 864e5) + 1 })).reverse();
}
function searchCity(c) { showResults(runsFor(r => r[0] === c || (r[3] || []).includes(c)).map(r => ({ label: `${cityName(c)}, ${fmtShort(r.from)}${r.n > 1 ? " to " + fmtShort(r.to) : ""}`, small: `${r.n} day${r.n > 1 ? "s" : ""}`, date: r.from })), `${cityName(c)}`); }
function searchCountry(cc) { showResults(runsFor(r => cityCC(r[0]) === cc).map(r => ({ label: `${countryName(cc)}, ${fmtShort(r.from)}${r.n > 1 ? " to " + fmtShort(r.to) : ""}`, small: `${r.n} day${r.n > 1 ? "s" : ""}`, date: r.from })), countryName(cc)); }
function showResults(items, label) {
  const ul = $("#qResults"); $("#q").value = label || "";
  ul.innerHTML = items.slice(0, 60).map(it => `<li data-d="${it.date}"><span>${esc(it.label)}</span><small>${esc(it.small || "")}</small></li>`).join("") || `<li><span>Nothing found</span></li>`;
  ul.hidden = false;
  $$("li[data-d]", ul).forEach(li => li.addEventListener("click", () => { ul.hidden = true; setView("day", false); gotoNearest(li.dataset.d); }));
}
function search(q) {
  q = q.trim().toLowerCase(); const ul = $("#qResults");
  if (!q) { ul.hidden = true; return; }
  let m;
  if ((m = q.match(/^(\d{4})-(\d{2})-(\d{2})$/))) return showResults([{ label: fmtDate(q), date: q }]);
  if ((m = q.match(/^(\d{1,2})[.\/ ]+(\d{1,2})[.\/ ]+(\d{4})$/))) { const d = `${m[3]}-${pad(m[2])}-${pad(m[1])}`; return showResults([{ label: fmtDate(d), date: d }]); }
  if ((m = q.match(/^(\d{1,2})\.?\s+([a-z]+)\.?\s+(\d{4})$/)) || (m = q.match(/^([a-z]+)\s+(\d{1,2}),?\s+(\d{4})$/))) {
    const dayN = isNaN(m[1]) ? m[2] : m[1], mon = isNaN(m[1]) ? m[1] : m[2];
    const mi = MONTHS.findIndex(x => x.startsWith(mon.slice(0, 3)));
    if (mi >= 0) { const d = `${m[3]}-${pad(mi + 1)}-${pad(dayN)}`; return showResults([{ label: fmtDate(d), date: d }]); }
  }
  if ((m = q.match(/^([a-z]+)\s+(\d{4})$/)) || (m = q.match(/^(\d{4})-(\d{2})$/))) {
    let ym; if (/^\d{4}$/.test(m[1])) ym = `${m[1]}-${m[2]}`; else { const mi = MONTHS.findIndex(x => x.startsWith(m[1].slice(0, 3))); if (mi >= 0) ym = `${m[2]}-${pad(mi + 1)}`; }
    if (ym) { const ds = S.dates.filter(d => d.startsWith(ym)); const cnt = new Map(); ds.forEach(d => { const c = S.idx.days[d][0]; cnt.set(c, (cnt.get(c) || 0) + 1); });
      return showResults([...cnt.entries()].sort((a, b) => b[1] - a[1]).map(([c, n]) => ({ label: `${fmtMonth(ym)}, ${cityName(c)}`, small: `${n} days`, date: ds.find(d => S.idx.days[d][0] === c) })), fmtMonth(ym)); }
  }
  if ((m = q.match(/^(\d{4})$/))) { const ds = S.dates.filter(d => d.startsWith(m[1])); if (ds.length) { S.date = ds[0]; setView("year"); ul.hidden = true; return; } }
  // cities & countries
  const res = [];
  for (const [c, n] of S.topCities) if (cityName(c).toLowerCase().includes(q)) res.push({ label: `${cityName(c)}, ${countryName(cityCC(c))}`, small: `${n} days`, city: c });
  for (const [cc, name] of Object.entries(S.idx.countries)) if (name.toLowerCase().startsWith(q) && [...S.cityCount.keys()].some(c => cityCC(c) === cc)) res.push({ label: name, small: "country", cc });
  for (const [k, p] of Object.entries(S.idx.places)) if (p[5] && p[5].toLowerCase().includes(q)) res.push({ label: p[5], small: cityName(p[6]), ll: [p[0], p[1]] });
  ul.innerHTML = res.slice(0, 20).map((it, i) => `<li data-i="${i}"><span>${esc(it.label)}</span><small>${esc(it.small)}</small></li>`).join("") || `<li><span>Nothing found. Try a date like 7 March 2025, a month, a city or a country.</span></li>`;
  ul.hidden = false;
  $$("li[data-i]", ul).forEach(li => li.addEventListener("click", () => {
    const it = res[+li.dataset.i];
    if (it.city !== undefined) searchCity(it.city); else if (it.cc) searchCountry(it.cc); else { ul.hidden = true; S.map.easeTo({ center: [it.ll[1], it.ll[0]], zoom: 15 }); }
  }));
}

/* ---------------------------------------------------------------- UI wiring */
function initUI() {
  $$(".views button").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#prevDay").onclick = () => step(-1); $("#nextDay").onclick = () => step(1);
  $("#play").onclick = togglePlay;
  $("#homeLink").onclick = e => { e.preventDefault(); setView("life"); };
  $("#lockBtn").onclick = async () => { await kvDel("key"); location.reload(); };
  const q = $("#q"); let t;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => search(q.value), 120); });
  q.addEventListener("keydown", e => { if (e.key === "Enter") { const li = $("#qResults li"); if (li) li.click(); } if (e.key === "Escape") { $("#qResults").hidden = true; q.blur(); } });
  document.addEventListener("click", e => { if (!e.composedPath().some(el => el.classList && el.classList.contains("search"))) $("#qResults").hidden = true; });
  document.addEventListener("keydown", e => {
    if (e.target.matches("input")) return;
    if (e.key === "ArrowLeft") step(e.shiftKey ? -7 : -1); else if (e.key === "ArrowRight") step(e.shiftKey ? 7 : 1);
    else if (e.key === " ") { e.preventDefault(); togglePlay(); } else if (e.key === "/") { e.preventDefault(); q.focus(); }
    else if (e.key === "y") setView("year"); else if (e.key === "l") setView("life"); else if (e.key === "d") setView("day");
  });
  // phone bottom sheet
  const panel = $("#panel"); let sy = null;
  $("#sheetHandle").addEventListener("pointerdown", e => sy = e.clientY);
  document.addEventListener("pointerup", e => { if (sy === null) return; const dy = e.clientY - sy; sy = null; if (dy < -30) panel.classList.add("expanded"); else if (dy > 30) panel.classList.remove("expanded"); });
  $("#sheetHandle").addEventListener("click", () => panel.classList.toggle("expanded"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", e => { S.dark = e.matches; S.map.setStyle(S.dark ? STYLE_DARK : STYLE_LIGHT); S.mapReady = false; S.map.once("style.load", () => { addLayers(); S.mapReady = true; goto(S.date, false); }); drawRibbon(); });
  window.addEventListener("hashchange", () => { const h = parseHash(); if (h.date && h.date !== S.date) goto(h.date); });
  window.addEventListener("resize", drawRibbon);
}
function togglePlay() {
  const b = $("#play");
  if (S.playing) { clearInterval(S.playing); S.playing = null; b.textContent = "▶"; return; }
  if (S.view !== "day") setView("day", false);
  b.textContent = "❚❚";
  S.playing = setInterval(() => { const i = S.dates.indexOf(S.date); if (i >= S.dates.length - 1) return togglePlay(); goto(S.dates[i + 1], true); }, 1400);
}

boot().catch(err => { console.error(err); $("#gateLede").textContent = "The archive could not be loaded: " + err.message; });
})();
