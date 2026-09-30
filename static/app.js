// Lighting timeline UI: editing, SVG timeline, playback, exact readouts.
import { evaluate, fromNumber, frac, toFixed, toNumber, roundHalfUp } from "./engine.js";

const PROJECT_ID = "demo";
const AXIS_H = 28;
const LANE_H = 78;
const PLOT_PAD = 8;
const PLOT_H = 42;
const MIN_PX_PER_MS = 0.005;
const MAX_PX_PER_MS = 2;

const $ = (sel) => document.querySelector(sel);

const state = {
  project: { name: "", cues: [] },
  timeline: null,          // last successfully compiled timeline
  errors: [],              // last validation errors (banner-worthy)
  quietErrors: [],         // errors from debounced background validation
  dirty: false,
  selectedId: null,
  playhead: frac(0),
  playing: false,
  rafId: null,
  wallStart: 0,
  playStart: null,
  pxPerMs: 0.12,
  parity: {},              // laneKey -> 'ok' | 'bad' | null
  lastParityAt: 0,
};

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = res.status !== 204 ? await res.json() : null;
  if (!res.ok) throw { status: res.status, data };
  return data;
}

async function loadProject() {
  const data = await api("GET", `/api/projects/${PROJECT_ID}?include_timeline=true`);
  state.project = data.project;
  state.timeline = data.timeline;
  state.dirty = false;
  $("#project-name").value = state.project.name;
  renderAll();
  setSaveState("已加载（保存于服务器）", false);
}

function payload() {
  return { name: state.project.name || "未命名工程",
           cues: state.project.cues.map((c) => ({ ...c, start: { ...c.start } })) };
}

async function compileRemote({ loud }) {
  try {
    const data = await api("POST", `/api/projects/${PROJECT_ID}/validate`, payload());
    state.quietErrors = [];
    if (loud) state.errors = [];
    state.timeline = data.timeline;
    renderTimeline();
    renderReadouts();
    renderActualCells();
    paintRowErrors(loud ? [] : []);
    if (loud) showOk(`校验通过：${data.timeline.cues.length} 个提示项`);
    return true;
  } catch (err) {
    const errors = err.data?.detail?.errors || [{ message: "校验请求失败" }];
    if (loud) { state.errors = errors; showErrors(errors); }
    state.quietErrors = errors;
    paintRowErrors(errors);
    setSaveState("校验未通过", true);
    return false;
  }
}

let revalidateTimer = null;
function scheduleRevalidate() {
  clearTimeout(revalidateTimer);
  revalidateTimer = setTimeout(() => compileRemote({ loud: false }), 350);
}

async function saveProject() {
  try {
    const data = await api("PUT", `/api/projects/${PROJECT_ID}`, payload());
    state.timeline = data.timeline;
    state.project = data.project;
    state.dirty = false;
    state.errors = [];
    state.quietErrors = [];
    showOk("已保存：失败的保存不会改变原工程，本次为整体原子提交");
    setSaveState("已保存", false);
    renderAll();
    checkParity();
  } catch (err) {
    const errors = err.data?.detail?.errors || [{ message: "保存失败" }];
    state.errors = errors;
    showErrors(errors);
    paintRowErrors(errors);
    setSaveState("保存被拒绝（工程未改动）", true);
  }
}

// ---------------------------------------------------------------------------
// Editing
// ---------------------------------------------------------------------------

function cueById(id) { return state.project.cues.find((c) => c.id === id); }

function newCueId() {
  return "c" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

function markDirty(msg = "有未保存修改") {
  state.dirty = true;
  for (const k of Object.keys(state.parity)) state.parity[k] = null;
  setSaveState(msg, true);
  hideBanners();
}

function addCue() {
  const ref = state.project.cues.find((c) => c.id === state.selectedId);
  const cue = {
    id: newCueId(),
    fixture: ref ? ref.fixture : "L1",
    channel: ref ? ref.channel : "dimmer",
    target: 0,
    duration: 1000,
    start: { mode: "absolute", at: 0 },
  };
  state.project.cues.push(cue);
  state.selectedId = cue.id;
  markDirty();
  renderRows();
  renderTimeline();
  scheduleRevalidate();
  const el = document.querySelector(`tr[data-cue-id="${cssEscape(cue.id)}"] input.fixture`);
  if (el) el.focus();
}

function deleteCue(id) {
  state.project.cues = state.project.cues.filter((c) => c.id !== id);
  if (state.selectedId === id) state.selectedId = null;
  markDirty();
  renderRows();
  renderTimeline();
  renderReadouts();
  scheduleRevalidate();
}

function cssEscape(s) { return s.replace(/[^a-zA-Z0-9_-]/g, (m) => `\\${m}`); }

function bindRow(row, cue) {
  const set = (field, value) => {
    cue[field] = value;
    markDirty();
    scheduleRevalidate();
  };
  row.querySelector(".fixture").addEventListener("input", (e) => set("fixture", e.target.value));
  row.querySelector(".channel").addEventListener("input", (e) => set("channel", e.target.value));
  row.querySelector(".target").addEventListener("input", (e) =>
    set("target", toNonnegInt(e.target.value)));
  row.querySelector(".duration").addEventListener("input", (e) =>
    set("duration", toNonnegInt(e.target.value)));

  const modeSel = row.querySelector(".start-mode");
  modeSel.addEventListener("input", (e) => {
    if (e.target.value === "absolute") {
      cue.start = { mode: "absolute", at: cue.start.at ?? 0 };
    } else {
      cue.start = { mode: "reference", refId: firstRefId(cue),
        anchor: "end", offset: cue.start.offset ?? 0 };
    }
    markDirty();
    renderRows();
    scheduleRevalidate();
  });
  const refSel = row.querySelector(".ref-id");
  if (refSel) refSel.addEventListener("input", (e) => {
    cue.start.refId = e.target.value;
    markDirty(); scheduleRevalidate(); renderActualCells();
  });
  const anchor = row.querySelector(".ref-anchor");
  if (anchor) anchor.addEventListener("input", (e) => {
    cue.start.anchor = e.target.value; markDirty(); scheduleRevalidate();
  });
  const offset = row.querySelector(".ref-offset");
  if (offset) offset.addEventListener("input", (e) => {
    cue.start.offset = toNonnegInt(e.target.value); markDirty(); scheduleRevalidate();
  });
  const at = row.querySelector(".abs-at");
  if (at) at.addEventListener("input", (e) => {
    cue.start.at = toNonnegInt(e.target.value); markDirty(); scheduleRevalidate();
  });
  row.querySelector(".del").addEventListener("click", (e) => {
    e.stopPropagation();
    const dependents = (state.timeline?.depends[cue.id] || []);
    const msg = dependents.length
      ? `删除 ${cue.fixture}/${cue.channel} 后，${dependents.length} 项将引用失效（保存会被拒绝，直到改好它们）。仍要删除？`
      : `删除提示项 ${cue.fixture}/${cue.channel}？`;
    if (window.confirm && !window.confirm(msg)) return;
    deleteCue(cue.id);
  });
  row.addEventListener("click", () => { state.selectedId = cue.id; renderSelection(); });
}

function firstRefId(self) {
  return (state.project.cues.find((c) => c.id !== self.id) || {}).id || "";
}

function toNonnegInt(v) {
  if (v === "" || v === null) return 0;
  const n = Number(v);
  return Number.isInteger(n) && n >= 0 ? n : 0;
}

function compiledCueMap() {
  const m = new Map();
  if (state.timeline) for (const c of state.timeline.cues) m.set(c.id, c);
  return m;
}

// ---------------------------------------------------------------------------
// Rows table
// ---------------------------------------------------------------------------

function renderRows() {
  const tbody = $("#cue-rows");
  tbody.innerHTML = "";
  const compiled = compiledCueMap();
  state.project.cues.forEach((cue, i) => {
    const tr = document.createElement("tr");
    tr.className = "cue-row";
    tr.dataset.cueId = cue.id;
    const refOptions = state.project.cues
      .filter((c) => c.id !== cue.id)
      .map((c) => {
        const cc = compiled.get(c.id);
        const label = `${c.fixture}/${c.channel}${cc ? ` @${cc.actualStart}` : ""} (${c.id.slice(0, 6)})`;
        return `<option value="${c.id}" ${cue.start.refId === c.id ? "selected" : ""}>${esc(label)}</option>`;
      }).join("");
    const isRef = cue.start.mode === "reference";
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td><input class="fixture wide" value="${esc(cue.fixture)}" maxlength="100"></td>
      <td><input class="channel wide" value="${esc(cue.channel)}" maxlength="100"></td>
      <td><input class="target" type="number" min="0" max="255" step="1" value="${cue.target}"></td>
      <td><input class="duration" type="number" min="0" step="1" value="${cue.duration}"></td>
      <td><select class="start-mode">
        <option value="absolute" ${!isRef ? "selected" : ""}>绝对时间</option>
        <option value="reference" ${isRef ? "selected" : ""}>引用</option>
      </select></td>
      <td><input class="abs-at" type="number" min="0" step="1"
            value="${cue.start.at ?? 0}" ${isRef ? "disabled" : ""}></td>
      <td><select class="ref-id" ${isRef ? "" : "disabled"}>${refOptions}</select></td>
      <td><select class="ref-anchor" ${isRef ? "" : "disabled"}>
        <option value="start" ${isRef && cue.start.anchor === "start" ? "selected" : ""}>开始</option>
        <option value="end" ${(!isRef || cue.start.anchor !== "start") ? "selected" : ""}>结束</option>
      </select></td>
      <td><input class="ref-offset" type="number" min="0" step="1"
            value="${cue.start.offset ?? 0}" ${isRef ? "" : "disabled"}></td>
      <td class="actual-col"></td>
      <td><button class="del" type="button" title="删除">✕</button></td>`;
    tbody.appendChild(tr);
    bindRow(tr, cue);
  });
  renderActualCells();
  paintRowErrors(state.errors.length ? state.errors : state.quietErrors);
  renderSelection();
}

function renderActualCells() {
  const compiled = compiledCueMap();
  for (const tr of document.querySelectorAll("tr.cue-row")) {
    const cue = cueById(tr.dataset.cueId);
    if (!cue) continue;
    const cell = tr.querySelector(".actual-col");
    const isRef = cue.start.mode === "reference";
    const absAt = tr.querySelector(".abs-at");
    if (absAt) absAt.disabled = isRef;
    const cc = compiled.get(cue.id);
    if (cc) {
      cell.innerHTML = `<span class="actual ${cc.interrupted ? "cut" : ""}">${cc.actualStart}
        <span title="起步时从上一渐变接续的未取整值" class="raw">≈${toFixed(frac(BigInt(cc.fromValue.n), BigInt(cc.fromValue.d)), 3)}</span>
        ${cc.interrupted ? '<span class="badge cut">渐变被打断</span>' : ""}
        ${cc.duration === 0 ? '<span class="badge zero">立即</span>' : ""}</span>`;
    } else {
      cell.innerHTML = `<span class="actual">—</span>`;
    }
  }
}

function paintRowErrors(errors) {
  for (const tr of document.querySelectorAll("tr.cue-row")) tr.classList.remove("bad");
  for (const e of errors || []) {
    if (typeof e.index === "number" && e.index >= 0) {
      const tr = document.querySelectorAll("tr.cue-row")[e.index];
      if (tr) tr.classList.add("bad");
    }
  }
}

function renderSelection() {
  const sel = state.selectedId;
  const ancestors = new Set();
  const descendants = new Set();
  if (sel && state.timeline) {
    const cc = state.timeline.cues.find((c) => c.id === sel);
    if (cc) (cc.refAncestors || []).forEach((id) => ancestors.add(id));
    const walk = (id) => {
      for (const d of state.timeline.depends[id] || []) {
        if (!descendants.has(d)) { descendants.add(d); walk(d); }
      }
    };
    walk(sel);
  }
  for (const tr of document.querySelectorAll("tr.cue-row")) {
    tr.classList.toggle("selected", tr.dataset.cueId === sel);
    tr.classList.toggle("chain-in", ancestors.has(tr.dataset.cueId));
    tr.classList.toggle("chain-out", descendants.has(tr.dataset.cueId));
  }
  renderTimeline();
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
}

// ---------------------------------------------------------------------------
// Banners / save state
// ---------------------------------------------------------------------------

function showErrors(errors) {
  const lines = errors.map((e) => {
    const where = typeof e.index === "number" && e.index >= 0 ? `第 ${e.index + 1} 项 · ` : "";
    return `• ${where}${e.field ? e.field + " · " : ""}${e.message}`;
  });
  const banner = $("#error-banner");
  banner.textContent = "保存被拒绝，原工程未改变：\n" + lines.join("\n");
  banner.classList.remove("hidden");
  $("#ok-banner").classList.add("hidden");
}
function showOk(msg) {
  const b = $("#ok-banner");
  b.textContent = msg;
  b.classList.remove("hidden");
  $("#error-banner").classList.add("hidden");
}
function hideBanners() {
  $("#error-banner").classList.add("hidden");
  $("#ok-banner").classList.add("hidden");
}
function setSaveState(text, dirty) {
  const el = $("#save-state");
  el.textContent = text;
  el.classList.toggle("dirty", !!dirty);
}

// ---------------------------------------------------------------------------
// SVG timeline
// ---------------------------------------------------------------------------

const svgEl = () => document.getElementById("timeline-svg");
const NS = "http://www.w3.org/2000/svg";

function extent() {
  let end = 2000;
  if (state.timeline) {
    for (const c of state.timeline.cues) {
      end = Math.max(end, c.actualStart + c.duration);
    }
    const ph = toNumber(state.playhead);
    if (Number.isFinite(ph)) end = Math.max(end, ph);
  }
  return end;
}

function laneOf(cue) {
  return state.timeline.lanes.findIndex((l) => l.key === `${cue.fixture}|${cue.channel}`);
}

function renderTimeline() {
  const svg = svgEl();
  svg.innerHTML = "";
  const t = state.timeline;
  const lanes = t ? t.lanes : [];
  const end = extent();
  const width = Math.max(600, Math.ceil(end * state.pxPerMs) + 120);
  const height = AXIS_H + lanes.length * LANE_H + 24;
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  ensureDefs(svg);

  drawAxis(svg, width, end);
  lanes.forEach((lane, i) => drawLane(svg, lane, i, width));
  drawRefArrows(svg, lanes);
  drawPlayhead(svg, height);
  syncGutter(lanes);
}

function xOf(ms) { return 8 + ms * state.pxPerMs; }
function yOf(lane) { return AXIS_H + lane * LANE_H; }

function niceStep(targetPx) {
  const raw = targetPx / state.pxPerMs;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const candidates = [pow, 2 * pow, 5 * pow, 10 * pow];
  return candidates.find((c) => c * state.pxPerMs >= targetPx * 0.8) || candidates[candidates.length - 1];
}

function drawAxis(svg, width, end) {
  const step = niceStep(90);
  const g = el("g", {});
  for (let t = 0; t <= end + step; t += step) {
    const x = xOf(t);
    g.appendChild(el("line", { x1: x, y1: AXIS_H, x2: x, y2: 20000,
      stroke: "#2a3040", "stroke-width": 1 }));
    g.appendChild(el("text", { x: x + 3, y: 16, fill: "#8b94a5",
      "font-size": 11 }, t >= 1000 ? `${t / 1000}s` : `${t}`));
  }
  g.appendChild(el("line", { x1: 0, y1: AXIS_H, x2: width, y2: AXIS_H,
    stroke: "#444b5c" }));
  svg.appendChild(g);
}

function drawLane(svg, lane, i, width) {
  const y = yOf(i);
  const g = el("g", { "data-lane": i });
  g.appendChild(el("rect", { x: 0, y, width, height: LANE_H,
    fill: i % 2 ? "#1a1d24" : "#1d2027" }));
  g.appendChild(el("line", { x1: 0, y1: y + LANE_H, x2: width, y2: y + LANE_H,
    stroke: "#2a3040" }));

  const top = y + PLOT_PAD;
  const bottom = y + PLOT_PAD + PLOT_H;
  // value guides 0 / 128 / 255
  for (const v of [0, 128, 255]) {
    const yy = bottom - (v / 255) * PLOT_H;
    g.appendChild(el("line", { x1: 0, y1: yy, x2: width, y2: yy,
      stroke: "#242a36", "stroke-dasharray": "2 4" }));
  }

  lane.cues.forEach((cue, j) => {
    const next = lane.cues[j + 1];
    const xs = xOf(cue.actualStart);
    const xe = xOf(cue.actualStart + cue.duration);
    const fromFr = frac(BigInt(cue.fromValue.n), BigInt(cue.fromValue.d));
    const yv = (v) => bottom - (v / 255) * PLOT_H;
    const ys = yv(toNumber(fromFr));
    const yt = yv(cue.target);

    if (cue.duration === 0) {
      // instant switch: gold vertical line from current value to target
      g.appendChild(el("line", { x1: xs, y1: ys, x2: xs, y2: yt,
        stroke: "#ffd166", "stroke-width": 2.5 }));
      g.appendChild(el("path", { d: `M${xs - 6},${yt + 7} L${xs + 6},${yt + 7} L${xs},${yt} Z`,
        fill: "#ffd166" }));
    } else {
      // solid ramp up to the point a later cue actually interrupts it
      const cutMs = next && cue.interrupted ? next.actualStart : cue.actualStart + cue.duration;
      const xc = xOf(cutMs);
      const fromAtCut = next && cue.interrupted
        ? toNumber(frac(BigInt(next.fromValue.n), BigInt(next.fromValue.d)))
        : cue.target;
      const yc = yv(fromAtCut);
      g.appendChild(el("polygon", {
        points: `${xs},${bottom} ${xs},${ys} ${xc},${yc} ${xc},${bottom}`,
        fill: "rgba(90,169,255,0.22)", stroke: "none" }));
      g.appendChild(el("line", { x1: xs, y1: ys, x2: xc, y2: yc,
        stroke: "#5aa9ff", "stroke-width": 2 }));
      // start triangle
      g.appendChild(el("circle", { cx: xs, cy: ys, r: 3.5, fill: "#7ddc8a" }));
      if (cue.interrupted) {
        // ghost of the gradient that never played
        g.appendChild(el("polygon", {
          points: `${xc},${bottom} ${xc},${yc} ${xe},${yt} ${xe},${bottom}`,
          fill: "url(#hatch)", stroke: "#ff7a45", "stroke-dasharray": "4 3",
          "stroke-width": 1.2 }));
        g.appendChild(el("text", { x: xc + 3, y: yc - 4, fill: "#ff7a45",
          "font-size": 10 }, "✕ 被打断"));
      } else {
        g.appendChild(el("circle", { cx: xe, cy: yt, r: 3, fill: "#5aa9ff" }));
      }
    }

    // hit area + label
    const hitEnd = next ? xOf(next.actualStart) : width;
    const hit = el("rect", { x: xs, y: y + 1, width: Math.max(8, hitEnd - xs),
      height: LANE_H - 2, fill: "transparent", "data-cue-id": cue.id,
      style: "cursor:pointer" });
    hit.addEventListener("click", () => {
      if (!state.scrubMoved) { state.selectedId = cue.id; renderSelection(); }
    });
    g.appendChild(hit);
    g.appendChild(el("text", {
      x: xs + 4, y: y + LANE_H - 6, fill: cue.interrupted ? "#ff9d6e" : "#9aa3b2",
      "font-size": 10, "pointer-events": "none" },
      `${cue.id.slice(0, 6)}→${cue.target}${cue.duration === 0 ? " ⚡" : ""}`));
  });
  svg.appendChild(g);
}

function drawRefArrows(svg, lanes) {
  if (!state.timeline || !state.selectedId) return;
  const byId = new Map(state.timeline.cues.map((c) => [c.id, c]));
  const sel = byId.get(state.selectedId);
  if (!sel) return;
  const chain = new Set([sel.id, ...(sel.refAncestors || [])]);
  const walk = (id) => {
    for (const d of state.timeline.depends[id] || []) {
      if (!chain.has(d)) { chain.add(d); walk(d); }
    }
  };
  walk(sel.id);
  ensureDefs(svg);

  const g = el("g", {});
  for (const c of state.timeline.cues) {
    if (!c.refParent || !chain.has(c.id)) continue;
    const parent = byId.get(c.refParent);
    if (!parent) continue;
    const x1 = xOf(c.anchorActual);
    const x2 = xOf(c.actualStart);
    const y1 = yOf(laneOf(parent)) + LANE_H - 14;
    const y2 = yOf(laneOf(c)) + PLOT_PAD - 2;
    const midY = (y1 + y2) / 2;
    const path = el("path", {
      d: `M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`,
      fill: "none", stroke: "#b78cff", "stroke-width": 1.6,
      "stroke-dasharray": "5 3", "marker-end": "url(#arrow-ref)",
      opacity: c.id === sel.id || c.refParent === sel.id ? 1 : 0.55 });
    g.appendChild(path);
    g.appendChild(el("circle", { cx: x1, cy: y1, r: 3, fill: "#b78cff" }));
  }
  svg.appendChild(g);
}

function drawPlayhead(svg, height) {
  const x = xOf(toNumber(state.playhead));
  const g = el("g", { "data-playhead": 1 });
  g.appendChild(el("line", { x1: x, y1: AXIS_H, x2: x, y2: height - 4,
    stroke: "#ff6b6b", "stroke-width": 1.5 }));
  g.appendChild(el("path", { d: `M${x - 6},${AXIS_H} L${x + 6},${AXIS_H} L${x},${AXIS_H - 8} Z`,
    fill: "#ff6b6b" }));
  svg.appendChild(g);
}

function ensureDefs(svg) {
  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = el("defs", {});
    svg.insertBefore(defs, svg.firstChild);
    const pat = el("pattern", { id: "hatch", width: 8, height: 8,
      patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    pat.appendChild(el("rect", { width: 8, height: 8, fill: "rgba(255,122,69,0.08)" }));
    pat.appendChild(el("line", { x1: 0, y1: 0, x2: 0, y2: 8,
      stroke: "rgba(255,122,69,0.6)", "stroke-width": 2 }));
    defs.appendChild(pat);
    const m = el("marker", { id: "arrow-ref", viewBox: "0 0 10 10",
      refX: 9, refY: 5, markerWidth: 7, markerHeight: 7,
      orient: "auto-start-reverse" });
    m.appendChild(el("path", { d: "M0,0 L10,5 L0,10 z", fill: "#b78cff" }));
    defs.appendChild(m);
  }
  return defs;
}

function syncGutter(lanes) {
  let gutter = document.getElementById("lane-gutter");
  if (!gutter) {
    gutter = document.createElement("div");
    gutter.id = "lane-gutter";
    document.querySelector(".timeline-inner").prepend(gutter);
  }
  gutter.style.flex = "0 0 auto";
  gutter.innerHTML = "";
  const corner = document.createElement("div");
  corner.className = "gutter-corner";
  corner.textContent = "灯具 / 通道";
  gutter.appendChild(corner);
  lanes.forEach((lane, i) => {
    const d = document.createElement("div");
    d.className = "gutter-lane";
    d.innerHTML = `<b>${esc(lane.fixture)}</b><span>${esc(lane.channel)}</span>`;
    d.addEventListener("click", () => {
      // select first cue of this lane
      const first = lane.cues[0];
      if (first) { state.selectedId = first.id; renderSelection(); }
    });
    gutter.appendChild(d);
  });
}

function el(name, attrs, text) {
  const node = document.createElementNS(NS, name);
  if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------------------------------------------------------------------------
// Readouts
// ---------------------------------------------------------------------------

function renderReadouts() {
  const box = $("#readouts");
  box.innerHTML = "";
  if (!state.timeline) return;
  const result = evaluate(state.timeline, state.playhead);
  for (const lane of state.timeline.lanes) {
    const r = result.lanes[lane.key];
    const div = document.createElement("div");
    div.className = "readout" + (r.activeCueId ? " active" : "");
    const parity = state.parity[lane.key];
    const parityLabel = state.dirty ? "（未保存草稿）"
      : parity === "ok" ? "✓ 与服务端一致"
      : parity === "bad" ? "✗ 不一致" : "";
    div.innerHTML = `
      <div class="lane">${esc(lane.fixture)} / ${esc(lane.channel)}</div>
      <div><span class="val">${r.value.toString()}</span>
        <span class="parity ${state.dirty ? "" : parity === "ok" ? "ok" : parity === "bad" ? "bad" : ""}">
          ${parityLabel}</span></div>
      <div class="raw">精确值 ${toFixed(r.raw, 3)} · 活动项 ${r.activeCueId ? r.activeCueId.slice(0, 6) : "—"}</div>`;
    box.appendChild(div);
  }
  const t = toNumber(state.playhead);
  $("#live-time").textContent = Number.isInteger(t) ? `${t} ms` : `${t.toFixed(2)} ms`;
  if (!state.playing) $("#seek-input").value = Math.floor(t);
}

// Ask the server to evaluate the same instant and compare (jump parity).
// Only meaningful when the draft equals what the server has stored; while
// dirty the readouts are draft-only (validate, don't claim server parity).
let parityTimer = null;
function checkParity() {
  if (!state.timeline) return;
  const t = state.playhead;
  if (state.dirty) {
    for (const key of Object.keys(state.parity)) state.parity[key] = null;
    return;
  }
  api("POST", `/api/projects/${PROJECT_ID}/evaluate`,
    { time: { n: t.n.toString(), d: t.d.toString() } })
    .then((srv) => {
      const local = evaluate(state.timeline, t);
      let allOk = true;
      for (const key of Object.keys(local.lanes)) {
        const a = local.lanes[key];
        const b = srv.lanes[key];
        const ok = b && a.value === BigInt(b.value)
          && a.raw.n === BigInt(b.raw.n) && a.raw.d === BigInt(b.raw.d);
        state.parity[key] = ok ? "ok" : "bad";
        if (!ok) allOk = false;
      }
      renderReadouts();
      if (!allOk) console.warn("parity mismatch", { local, srv });
    })
    .catch(() => {});
}
function debouncedParity() {
  clearTimeout(parityTimer);
  parityTimer = setTimeout(checkParity, 250);
}

// ---------------------------------------------------------------------------
// Scrubbing + playback
// ---------------------------------------------------------------------------

function timeFromEvent(e) {
  const svg = svgEl();
  const rect = svg.getBoundingClientRect();
  const x = e.clientX - rect.left - 8; // axis offset inside svg (gutter is outside)
  const ms = Math.max(0, x / state.pxPerMs);
  return fromNumber(ms);
}

function setupScrub() {
  const scroll = $("#timeline-scroll");
  scroll.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, input, select")) return;
    state.scrubbing = true;
    state.scrubMoved = false;
    pause();
    state.playhead = timeFromEvent(e) || frac(0);
    renderReadouts();
    movePlayhead();
    scroll.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  });
  scroll.addEventListener("pointermove", (e) => {
    if (!state.scrubbing) return;
    state.scrubMoved = true;
    scroll.classList.add("dragging");
    state.playhead = timeFromEvent(e) || frac(0);
    renderReadouts();
    movePlayhead();
  });
  const end = (e) => {
    if (!state.scrubbing) return;
    state.scrubbing = false;
    scroll.classList.remove("dragging");
    setTimeout(() => { state.scrubMoved = false; }, 0);
    debouncedParity();
  };
  scroll.addEventListener("pointerup", end);
  scroll.addEventListener("pointercancel", end);
}

function play() {
  if (state.playing) return;
  state.playing = true;
  state.wallStart = performance.now();
  state.playStart = state.playhead;
  $("#play-btn").textContent = "▶ 播放中";
  const tick = (now) => {
    if (!state.playing) return;
    const elapsedMs = fromNumber(now - state.wallStart);
    state.playhead = frac(state.playStart.n * elapsedMs.d + elapsedMs.n * state.playStart.d,
                          state.playStart.d * elapsedMs.d);
    renderReadouts();
    movePlayhead();
    autoScrollPlayhead();
    if (now - state.lastParityAt > 2500) {
      state.lastParityAt = now;
      checkParity();
    }
    state.rafId = requestAnimationFrame(tick);
  };
  state.rafId = requestAnimationFrame(tick);
}

function movePlayhead() {
  const svg = svgEl();
  const g = svg.querySelector("g[data-playhead]");
  if (!g) { renderTimeline(); return; }
  const x = xOf(toNumber(state.playhead));
  const line = g.children[0];
  const tri = g.children[1];
  const height = svg.attrs.height || 400;
  if (line) {
    line.setAttribute("x1", x);
    line.setAttribute("x2", x);
    line.setAttribute("y2", height - 4);
  }
  if (tri) tri.setAttribute("d",
    `M${x - 6},${AXIS_H} L${x + 6},${AXIS_H} L${x},${AXIS_H - 8} Z`);
}

function autoScrollPlayhead() {
  const scroll = $("#timeline-scroll");
  const x = xOf(toNumber(state.playhead));
  if (x > scroll.scrollLeft + scroll.clientWidth - 60) {
    scroll.scrollLeft = x - scroll.clientWidth + 80;
  }
}

function pause() {
  state.playing = false;
  if (state.rafId) cancelAnimationFrame(state.rafId);
  state.rafId = null;
  $("#play-btn").textContent = "▶ 播放";
  if (state.timeline) debouncedParity();
}

function seekTo(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return;
  pause();
  state.playhead = frac(BigInt(Math.trunc(n)));
  renderReadouts();
  renderTimeline();
  debouncedParity();
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function wire() {
  $("#add-cue").addEventListener("click", addCue);
  $("#save-btn").addEventListener("click", saveProject);
  $("#validate-btn").addEventListener("click", () => compileRemote({ loud: true }));
  $("#project-name").addEventListener("input", (e) => {
    state.project.name = e.target.value;
    markDirty();
  });
  $("#play-btn").addEventListener("click", play);
  $("#pause-btn").addEventListener("click", pause);
  $("#stop-btn").addEventListener("click", () => seekTo(0));
  $("#seek-btn").addEventListener("click", () => seekTo($("#seek-input").value));
  $("#seek-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") seekTo(e.target.value);
  });
  $("#zoom-in").addEventListener("click", () => setZoom(state.pxPerMs * 1.5));
  $("#zoom-out").addEventListener("click", () => setZoom(state.pxPerMs / 1.5));
  $("#zoom-fit").addEventListener("click", () => {
    const w = $("#timeline-scroll").clientWidth - 170;
    setZoom(Math.min(MAX_PX_PER_MS, Math.max(MIN_PX_PER_MS, w / Math.max(1, extent()))));
  });
  setupScrub();
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });
}

function setZoom(v) {
  state.pxPerMs = Math.min(MAX_PX_PER_MS, Math.max(MIN_PX_PER_MS, v));
  $("#zoom-label").textContent = `${Math.round(state.pxPerMs * 1000)} px/s`;
  renderTimeline();
}

function renderAll() {
  renderRows();
  renderReadouts();
  renderTimeline();
  setZoom(state.pxPerMs);
}

wire();
loadProject().catch((err) => {
  setSaveState("加载失败", true);
  console.error(err);
});
