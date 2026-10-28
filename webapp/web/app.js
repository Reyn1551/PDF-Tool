"use strict";

// ============================================================ state
const state = {
  id: null,
  name: "",
  pages: 0,
  version: 0,
  size: 0,
  page: 0,        // current page (0-based)
  selection: new Set(),
  zoom: 1.0,      // viewer zoom factor
  thumbW: 132,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ============================================================ api
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let body = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) body = await res.json();
  else if (ct.startsWith("image/")) body = await res.blob();
  else if (ct.startsWith("text/")) body = await res.text();
  if (!res.ok) {
    const msg = body && body.error ? body.error : res.statusText;
    throw new Error(msg);
  }
  return body;
}
const postJSON = (path, obj) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) });

// ============================================================ toast + busy
let toastTimer = null;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}
function busy(on) { document.body.classList.toggle("busy", on); }

// ============================================================ modal
function modal({ title, sub, fields, okText = "Apply", onOk, danger = false }) {
  const card = $("#modalCard");
  card.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = title; card.appendChild(h);
  if (sub) { const p = document.createElement("p"); p.className = "modal-sub"; p.innerHTML = sub; card.appendChild(p); }
  const inputs = {};
  for (const f of fields) {
    const wrap = document.createElement("div");
    if (f.row) wrap.className = "field-row";
    const items = f.row ? f.row : [f];
    for (const it of items) {
      const div = document.createElement("div"); div.className = "field";
      const lab = document.createElement("label"); lab.textContent = it.label; div.appendChild(lab);
      let el;
      if (it.type === "range") {
        el = document.createElement("input"); el.type = "range";
        el.min = it.min; el.max = it.max; el.step = it.step; el.value = it.value;
        const span = document.createElement("span"); span.className = "range-val"; span.textContent = it.value;
        el.addEventListener("input", () => span.textContent = el.value);
        div.appendChild(el); div.appendChild(span);
      } else if (it.type === "select") {
        el = document.createElement("select");
        for (const o of it.options) { const op = document.createElement("option"); op.value = o.value; op.textContent = o.label; el.appendChild(op); }
        el.value = it.value;
        div.appendChild(el);
      } else {
        el = document.createElement("input"); el.type = it.type || "text";
        if (it.value != null) el.value = it.value;
        if (it.placeholder) el.placeholder = it.placeholder;
        div.appendChild(el);
      }
      inputs[it.key] = el;
      wrap.appendChild(div);
    }
    card.appendChild(wrap);
  }
  const actions = document.createElement("div"); actions.className = "modal-actions";
  const cancel = document.createElement("button"); cancel.className = "btn"; cancel.textContent = "Cancel";
  const ok = document.createElement("button"); ok.className = "btn " + (danger ? "danger" : "primary"); ok.textContent = okText;
  actions.appendChild(cancel); actions.appendChild(ok); card.appendChild(actions);

  const close = () => $("#modal").classList.add("hidden");
  cancel.onclick = close;
  $(".modal-backdrop").onclick = close;
  ok.onclick = async () => {
    const vals = {};
    for (const k in inputs) vals[k] = inputs[k].value;
    ok.disabled = true; busy(true);
    try { await onOk(vals); close(); }
    catch (e) { toast(e.message, "err"); ok.disabled = false; busy(false); }
  };
  $("#modal").classList.remove("hidden");
  const first = card.querySelector("input,select");
  if (first) first.focus();
}

// ============================================================ upload / open
async function openFile(file) {
  busy(true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const s = await api("/api/upload", { method: "POST", body: fd });
    adoptSession(s);
    toast(`Opened ${s.name} (${s.pages} pages)`, "ok");
  } catch (e) {
    toast("Failed to open: " + e.message, "err");
  } finally { busy(false); }
}

function adoptSession(s) {
  state.id = s.id; state.name = s.name; state.pages = s.pages;
  state.version = s.version; state.size = s.size || 0; state.page = 0;
  state.selection.clear();
  $("#empty").classList.add("hidden");
  $("#app").classList.remove("hidden");
  renderMeta();
  renderThumbs();
  renderViewer();
  updatePageInfo();
}

function renderMeta() {
  const kb = state.size ? (state.size / 1024).toFixed(0) + " KB" : "";
  $("#docMeta").innerHTML = `<b>${escapeHtml(state.name)}</b><br>${state.pages} pages · ${kb}<br>v${state.version}`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ============================================================ thumbnails (virtualized)
const THUMB_H = 176; // approx rendered height per thumb incl. number bar
let thumbEls = new Map(); // index -> element
let thumbTop = 0;

function renderThumbs() {
  const strip = $("#thumbs");
  strip.innerHTML = "";
  thumbEls.clear();
  // total height spacer for scroll
  strip.style.minHeight = (state.pages * (THUMB_H + 10)) + "px";
  strip.onscroll = onThumbScroll;
  onThumbScroll();
}

function onThumbScroll() {
  const strip = $("#thumbs");
  const top = strip.scrollTop;
  const viewH = strip.clientHeight;
  const first = Math.max(0, Math.floor(top / (THUMB_H + 10)) - 2);
  const last = Math.min(state.pages - 1, Math.ceil((top + viewH) / (THUMB_H + 10)) + 2);
  // remove out-of-range
  for (const [idx, el] of thumbEls) {
    if (idx < first || idx > last) { el.remove(); thumbEls.delete(idx); }
  }
  // add in-range
  for (let i = first; i <= last; i++) {
    if (thumbEls.has(i)) continue;
    thumbEls.set(i, makeThumb(i));
    strip.appendChild(thumbEls.get(i));
  }
  // keep order
  const ordered = [];
  for (let i = first; i <= last; i++) if (thumbEls.has(i)) ordered.push(i);
  for (const i of ordered) strip.appendChild(thumbEls.get(i));
}

function makeThumb(i) {
  const el = document.createElement("div");
  el.className = "thumb" + (i === state.page ? " active" : "") + (state.selection.has(i) ? " selected" : "");
  el.style.position = "absolute";
  el.style.top = (i * (THUMB_H + 10)) + "px";
  el.style.left = "50%";
  el.style.transform = "translateX(-50%)";
  const spin = document.createElement("div"); spin.className = "thumb-spin";
  const img = new Image();
  img.alt = "page " + (i + 1);
  img.loading = "lazy";
  img.src = `/api/session/${state.id}/page/${i}/thumb?w=${state.thumbW}&v=${state.version}`;
  img.onload = () => spin.remove();
  img.onerror = () => spin.remove();
  const num = document.createElement("div"); num.className = "thumb-num"; num.textContent = i + 1;
  const check = document.createElement("div"); check.className = "thumb-check"; check.textContent = "✓";
  el.appendChild(spin); el.appendChild(img); el.appendChild(num); el.appendChild(check);
  el.onclick = () => { state.page = i; refreshThumbActive(); renderViewer(); updatePageInfo(); };
  el.oncontextmenu = (e) => { e.preventDefault(); toggleSelect(i); };
  return el;
}

function refreshThumbActive() {
  for (const [idx, el] of thumbEls) {
    el.classList.toggle("active", idx === state.page);
    el.classList.toggle("selected", state.selection.has(idx));
  }
}
function toggleSelect(i) {
  if (state.selection.has(i)) state.selection.delete(i); else state.selection.add(i);
  refreshThumbActive();
}

// ============================================================ viewer
function renderViewer() {
  const v = $("#viewer");
  v.innerHTML = "";
  if (!state.id) return;
  const width = Math.round(760 * state.zoom);
  const img = new Image();
  img.className = "page";
  img.src = `/api/session/${state.id}/page/${state.page}/full?w=${width}&v=${state.version}`;
  const wrap = document.createElement("div"); wrap.className = "page";
  wrap.appendChild(img);
  const num = document.createElement("div"); num.className = "page-num"; num.textContent = "Page " + (state.page + 1) + " / " + state.pages;
  v.appendChild(wrap); v.appendChild(num);
  $("#zoomLabel").textContent = Math.round(state.zoom * 100) + "%";
}

function updatePageInfo() {
  const sel = state.selection.size;
  $("#pageInfo").textContent = `Page ${state.page + 1} of ${state.pages}` + (sel ? ` · ${sel} selected` : "");
}

function zoomBy(d) {
  state.zoom = Math.min(3, Math.max(0.4, state.zoom + d));
  renderViewer();
}

// ============================================================ tools
const TOOLS = [
  { group: "Organize", items: [
    { icon: "🗑️", label: "Delete pages", act: deletePages },
    { icon: "🔄", label: "Rotate", act: rotateModal },
    { icon: "↔️", label: "Reverse order", act: reverse },
    { icon: "✂️", label: "Split (extract range)", act: splitModal },
    { icon: "🔀", label: "Reorder (move selected)", act: reorderSelected },
  ]},
  { group: "Security", items: [
    { icon: "🔒", label: "Protect (password)", act: protectModal },
    { icon: "🔓", label: "Unlock", act: unlockModal },
    { icon: "💧", label: "Watermark", act: watermarkModal },
    { icon: "⬛", label: "Redact", act: redactModal },
  ]},
  { group: "Optimize", items: [
    { icon: "📦", label: "Compress", act: compressModal },
  ]},
  { group: "Export", items: [
    { icon: "📝", label: "Extract text", act: exportText },
    { icon: "🖼️", label: "Export images", act: exportImagesModal },
  ]},
];

function renderTools() {
  const nav = $("#tools");
  nav.innerHTML = "";
  for (const g of TOOLS) {
    const grp = document.createElement("div"); grp.className = "tool-group";
    const t = document.createElement("div"); t.className = "tool-group-title"; t.textContent = g.group; grp.appendChild(t);
    for (const it of g.items) {
      const b = document.createElement("button"); b.className = "tool-item";
      b.innerHTML = `<span class="ti-icon">${it.icon}</span><span>${it.label}</span>`;
      b.onclick = () => { if (!state.id) return; busy(true); it.act().finally(() => busy(false)); };
      grp.appendChild(b);
    }
    nav.appendChild(grp);
  }
}

function requireSel() {
  if (state.selection.size === 0) { toast("Select pages first (right-click thumbnails)", "err"); throw new Error("no selection"); }
  return [...state.selection].sort((a, b) => a - b);
}

// ---- organize
async function deletePages() {
  const pages = requireSel();
  modal({
    title: "Delete pages",
    sub: `Remove ${pages.length} page(s): ${pages.map(p => p + 1).join(", ")}`,
    fields: [],
    okText: "Delete",
    danger: true,
    onOk: async () => {
      const s = await postJSON(`/api/session/${state.id}/delete`, { pages });
      applyEdit(s);
    },
  });
}

async function rotateModal() {
  const pages = requireSel();
  modal({
    title: "Rotate pages",
    sub: `Rotate ${pages.length} page(s)`,
    fields: [{ key: "deg", label: "Angle (clockwise)", type: "select", value: "90",
      options: [{ value: "90", label: "90°" }, { value: "180", label: "180°" }, { value: "270", label: "270°" }] }],
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/rotate`, { pages, deg: +v.deg });
      applyEdit(s);
    },
  });
}

async function reverse() {
  modal({
    title: "Reverse page order",
    sub: "Flip the whole document.",
    fields: [], okText: "Reverse",
    onOk: async () => { const s = await postJSON(`/api/session/${state.id}/reverse`, {}); applyEdit(s); },
  });
}

async function splitModal() {
  modal({
    title: "Split — extract a range",
    sub: "Creates a new document from the selected pages (1-based, inclusive).",
    fields: [
      { row: true, row: [
        { key: "start", label: "From page", type: "number", value: 1, min: 1 },
        { key: "end", label: "To page", type: "number", value: state.pages, min: 1 },
      ]},
    ],
    okText: "Extract",
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/split`, { start: +v.start, end: +v.end });
      adoptSession(s);
      toast(`Extracted ${s.pages} pages`, "ok");
    },
  });
}

async function reorderSelected() {
  const pages = requireSel();
  // move selected block to a target position
  modal({
    title: "Move selected pages",
    sub: `Move ${pages.length} page(s) to a new position.`,
    fields: [{ key: "pos", label: "Insert before page (1-based)", type: "number", value: 1, min: 1, max: state.pages + 1 }],
    okText: "Move",
    onOk: async (v) => {
      const pos = Math.max(0, Math.min(state.pages, +v.pos - 1));
      const order = buildMoveOrder(pages, pos);
      const s = await postJSON(`/api/session/${state.id}/reorder`, { order });
      applyEdit(s);
    },
  });
}

// produce a full permutation that moves `pages` to just before index `pos`
function buildMoveOrder(pages, pos) {
  const set = new Set(pages);
  const rest = [];
  for (let i = 0; i < state.pages; i++) if (!set.has(i)) rest.push(i);
  const out = rest.slice(0, pos).concat(pages).concat(rest.slice(pos));
  return out;
}

// ---- security
async function protectModal() {
  modal({
    title: "Protect with password",
    sub: "Encrypt the document. You'll need this password to open it again.",
    fields: [
      { key: "user", label: "Open password", type: "password", placeholder: "required" },
      { key: "owner", label: "Owner password (optional)", type: "password", placeholder: "restricts editing/printing" },
    ],
    okText: "Protect",
    onOk: async (v) => {
      if (!v.user) throw new Error("open password required");
      const s = await postJSON(`/api/session/${state.id}/protect`, { user: v.user, owner: v.owner });
      applyEdit(s);
    },
  });
}

async function unlockModal() {
  modal({
    title: "Unlock",
    sub: "Remove the password.",
    fields: [{ key: "password", label: "Password", type: "password" }],
    okText: "Unlock",
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/unlock`, { password: v.password });
      applyEdit(s);
    },
  });
}

async function watermarkModal() {
  modal({
    title: "Watermark",
    sub: "Stamp text on every page.",
    fields: [
      { key: "text", label: "Text", type: "text", value: "CONFIDENTIAL" },
      { key: "wRatio", label: "Width (% of page)", type: "range", min: 10, max: 100, step: 5, value: 50 },
      { key: "hRatio", label: "Height (% of page)", type: "range", min: 2, max: 40, step: 2, value: 10 },
      { key: "pos", label: "Position", type: "select", value: "center",
        options: [{ value: "center", label: "Center" }, { value: "tl", label: "Top-left" }, { value: "tr", label: "Top-right" }, { value: "bl", label: "Bottom-left" }, { value: "br", label: "Bottom-right" }] },
    ],
    okText: "Apply",
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/watermark`, {
        text: v.text, wRatio: +v.wRatio / 100, hRatio: +v.hRatio / 100, pos: v.pos,
      });
      applyEdit(s);
    },
  });
}

async function redactModal() {
  const page = state.page;
  modal({
    title: "Redact (white-out)",
    sub: `Draw a box on page ${page + 1} to permanently white-out. Coordinates are fractions of the page (0–1, origin top-left).`,
    fields: [
      { row: true, row: [
        { key: "x", label: "X (left)", type: "number", value: 0.1, step: 0.05 },
        { key: "y", label: "Y (top)", type: "number", value: 0.1, step: 0.05 },
      ]},
      { row: true, row: [
        { key: "w", label: "Width", type: "number", value: 0.3, step: 0.05 },
        { key: "h", label: "Height", type: "number", value: 0.1, step: 0.05 },
      ]},
    ],
    okText: "Redact",
    danger: true,
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/redact`, {
        rects: [{ page, x: +v.x, y: +v.y, w: +v.w, h: +v.h }],
      });
      applyEdit(s);
    },
  });
}

// ---- optimize
async function compressModal() {
  modal({
    title: "Compress",
    sub: "Re-encode with Ghostscript. Lower quality = smaller file.",
    fields: [{ key: "level", label: "Quality", type: "select", value: "1",
      options: [{ value: "0", label: "Smallest (72 dpi)" }, { value: "1", label: "Balanced (150 dpi)" }, { value: "2", label: "High (300 dpi)" }] }],
    okText: "Compress",
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/compress`, { level: +v.level });
      applyEdit(s);
    },
  });
}

// ---- export
async function exportText() {
  const res = await fetch(`/api/session/${state.id}/export/text`);
  const text = await res.text();
  const blob = new Blob([text], { type: "text/plain" });
  downloadBlob(blob, state.name.replace(/\.pdf$/i, "") + ".txt");
  toast("Text exported", "ok");
}

async function exportImagesModal() {
  modal({
    title: "Export images",
    sub: "Render every page to an image.",
    fields: [
      { key: "fmt", label: "Format", type: "select", value: "png", options: [{ value: "png", label: "PNG" }, { value: "jpg", label: "JPG" }] },
      { key: "w", label: "Width (px)", type: "number", value: 1100, min: 100 },
    ],
    okText: "Export",
    onOk: async (v) => {
      const q = v.fmt === "jpg" ? `&fmt=jpg&q=85` : "";
      const data = await api(`/api/session/${state.id}/export/images?w=${v.w}${q}`);
      // download each
      for (const [name, dataUrl] of Object.entries(data)) {
        const a = document.createElement("a");
        a.href = dataUrl; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
      }
      toast(`Exported ${Object.keys(data).length} images`, "ok");
    },
  });
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// ============================================================ apply edit
function applyEdit(s) {
  state.pages = s.pages; state.version = s.version; state.size = s.size;
  state.name = s.name;
  if (state.page >= state.pages) state.page = state.pages - 1;
  // prune selection
  for (const p of [...state.selection]) if (p >= state.pages) state.selection.delete(p);
  renderMeta();
  renderThumbs();
  renderViewer();
  updatePageInfo();
  toast("Done", "ok");
}

// ============================================================ download
async function download() {
  busy(true);
  try {
    const res = await fetch(`/api/session/${state.id}/download`);
    const blob = await res.blob();
    downloadBlob(blob, state.name.replace(/\.pdf$/i, "") + "-edited.pdf");
  } catch (e) { toast("Download failed: " + e.message, "err"); }
  finally { busy(false); }
}

// ============================================================ drag & drop
let dragDepth = 0;
function setupDnD() {
  window.addEventListener("dragenter", (e) => { e.preventDefault(); if (++dragDepth === 1) $("#dropzone").classList.remove("hidden"); });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", (e) => { e.preventDefault(); if (--dragDepth === 0) $("#dropzone").classList.add("hidden"); });
  window.addEventListener("drop", (e) => {
    e.preventDefault(); dragDepth = 0; $("#dropzone").classList.add("hidden");
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) openFile(f);
  });
}

// ============================================================ keyboard
function setupKeys() {
  window.addEventListener("keydown", (e) => {
    if ($("#modal").classList.contains("hidden") === false) {
      if (e.key === "Escape") $("#modal").classList.add("hidden");
      return;
    }
    if (!state.id) return;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); if (state.page < state.pages - 1) { state.page++; renderViewer(); updatePageInfo(); scrollThumbTo(state.page); } }
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); if (state.page > 0) { state.page--; renderViewer(); updatePageInfo(); scrollThumbTo(state.page); } }
    else if (e.key === "Delete" && state.selection.size) { e.preventDefault(); deletePages().catch(() => {}); }
    else if (e.key === "+" || e.key === "=") { zoomBy(0.1); }
    else if (e.key === "-") { zoomBy(-0.1); }
  });
}
function scrollThumbTo(i) {
  const strip = $("#thumbs");
  const target = i * (THUMB_H + 10) - strip.clientHeight / 2 + THUMB_H / 2;
  strip.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
}

// ============================================================ init
function init() {
  renderTools();
  setupDnD();
  setupKeys();
  $("#file").onchange = (e) => { if (e.target.files[0]) openFile(e.target.files[0]); e.target.value = ""; };
  $("#newBtn").onclick = () => $("#file").click();
  $("#downloadBtn").onclick = download;
  $("#zoomIn").onclick = () => zoomBy(0.1);
  $("#zoomOut").onclick = () => zoomBy(-0.1);
  $("#zoomFit").onclick = () => { state.zoom = 1.0; renderViewer(); };
}
init();
