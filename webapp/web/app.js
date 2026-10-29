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

// ============================================================ icons
/** Inline SVG sprite reference — crisp, themeable, zero network cost. */
function icon(name, cls = "icon") {
  return `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
}

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

/** Fetch that turns an error response into a thrown Error with the server message. */
async function fetchOk(path) {
  const res = await fetch(path);
  if (!res.ok) {
    let msg = res.statusText || "request failed";
    try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
    throw new Error(msg);
  }
  return res;
}

// ============================================================ toast + busy
let toastTimer = null;
function toast(msg, kind = "ok") {
  const t = $("#toast");
  t.querySelector(".toast-icon use").setAttribute("href", kind === "err" ? "#i-alert" : "#i-check");
  t.querySelector(".toast-msg").textContent = msg;
  t.className = "toast " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}
function busy(on) { document.body.classList.toggle("busy", on); }

/** "1 page" / "3 pages" — small grammar helper so messages read naturally. */
function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }
function kb(bytes) { return bytes ? Math.max(1, Math.round(bytes / 1024)) + " KB" : ""; }

// ============================================================ modal
function modal({ title, sub, fields, okText = "Apply", onOk, danger = false, iconName = "doc" }) {
  const card = $("#modalCard");
  card.innerHTML = "";

  const head = document.createElement("div");
  head.className = "modal-head";
  head.innerHTML = `<span class="m-tile${danger ? " danger" : ""}">${icon(iconName)}</span>`;
  const h = document.createElement("h3"); h.textContent = title;
  head.appendChild(h);
  card.appendChild(head);

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
  const ok = document.createElement("button"); ok.className = "btn " + (danger ? "danger" : "primary");
  ok.innerHTML = icon(iconName) + `<span>${okText}</span>`;
  actions.appendChild(cancel); actions.appendChild(ok); card.appendChild(actions);

  const close = () => $("#modal").classList.add("hidden");
  cancel.onclick = close;
  $(".modal-backdrop").onclick = close;
  ok.onclick = async () => {
    const vals = {};
    for (const k in inputs) vals[k] = inputs[k].value;
    ok.disabled = true; busy(true);
    try { await onOk(vals); close(); }
    catch (e) { toast(e.message, "err"); ok.disabled = false; }
    finally { busy(false); }
  };
  $("#modal").classList.remove("hidden");
  const first = card.querySelector("input,select") || ok;
  first.focus();
}

// ============================================================ upload / open
async function openFile(file) {
  busy(true);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const s = await api("/api/upload", { method: "POST", body: fd });
    adoptSession(s);
    toast(`${s.name} is ready — ${plural(s.pages, "page")}`);
  } catch (e) {
    toast("Couldn't open that file. " + e.message, "err");
  } finally { busy(false); }
}

function adoptSession(s) {
  state.id = s.id; state.name = s.name; state.pages = s.pages;
  state.version = s.version; state.size = s.size || 0; state.page = 0;
  state.selection.clear();
  $("#empty").classList.add("hidden");
  $("#app").classList.remove("hidden");
  document.body.classList.add("has-doc");
  renderMeta();
  renderThumbs();
  renderViewer();
  updatePageInfo();
}

function renderMeta() {
  const kb = state.size ? (state.size / 1024).toFixed(0) + " KB" : "";
  $("#docMeta").innerHTML = `
    <div class="doc-name">${icon("doc")}<span>${escapeHtml(state.name)}</span></div>
    <div class="doc-stats">
      <span class="pill">${state.pages} pages</span>
      ${kb ? `<span class="pill">${kb}</span>` : ""}
      <span class="pill">v${state.version}</span>
    </div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ============================================================ thumbnails (virtualized)
const THUMB_H = 186; // approx row height incl. gap
let thumbEls = new Map(); // index -> element

function renderThumbs() {
  const strip = $("#thumbs");
  strip.innerHTML = "";
  thumbEls.clear();
  strip.style.minHeight = (state.pages * THUMB_H + 12) + "px";
  strip.onscroll = onThumbScroll;
  onThumbScroll();
}

function onThumbScroll() {
  const strip = $("#thumbs");
  const top = strip.scrollTop;
  const viewH = strip.clientHeight || 600;
  const first = Math.max(0, Math.floor(top / THUMB_H) - 2);
  const last = Math.min(state.pages - 1, Math.ceil((top + viewH) / THUMB_H) + 2);

  for (const [idx, el] of thumbEls) {
    if (idx < first || idx > last) { el.remove(); thumbEls.delete(idx); }
  }
  for (let i = first; i <= last; i++) {
    if (thumbEls.has(i)) continue;
    const el = makeThumb(i);
    thumbEls.set(i, el);
    strip.appendChild(el);
  }
}

function makeThumb(i) {
  const el = document.createElement("div");
  el.className = "thumb" + (i === state.page ? " active" : "") + (state.selection.has(i) ? " selected" : "");
  el.style.top = (i * THUMB_H + 8) + "px";
  el.style.left = "50%";
  el.style.transform = "translateX(-50%)";
  el.setAttribute("role", "button");
  el.setAttribute("aria-label", `Page ${i + 1}`);

  const spin = document.createElement("div"); spin.className = "thumb-spin";
  const img = new Image();
  img.alt = "";
  img.loading = "lazy";
  img.src = `/api/session/${state.id}/page/${i}/thumb?w=${state.thumbW}&v=${state.version}`;
  img.onload = () => spin.remove();
  img.onerror = () => spin.remove();

  const num = document.createElement("div"); num.className = "thumb-num"; num.textContent = i + 1;
  const check = document.createElement("div"); check.className = "thumb-check"; check.innerHTML = icon("check");

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
  updatePageInfo();
}

// ============================================================ viewer
function renderViewer() {
  const v = $("#viewer");
  v.innerHTML = "";
  if (!state.id) return;
  const width = Math.round(760 * state.zoom);
  const img = new Image();
  img.alt = `Page ${state.page + 1}`;
  img.src = `/api/session/${state.id}/page/${state.page}/full?w=${width}&v=${state.version}`;
  const wrap = document.createElement("div"); wrap.className = "page";
  wrap.appendChild(img);
  v.appendChild(wrap);
  const label = document.createElement("div"); label.className = "page-num";
  label.textContent = `Page ${state.page + 1} / ${state.pages}`;
  v.appendChild(label);
  $("#zoomLabel").textContent = Math.round(state.zoom * 100) + "%";
}

function updatePageInfo() {
  const sel = state.selection.size;
  $("#pageInfo").textContent = `Page ${state.page + 1} of ${state.pages}`;
  const selInfo = $("#selInfo");
  const hint = $("#thumbsHint");
  if (sel) {
    selInfo.textContent = `${plural(sel, "page")} picked`;
    selInfo.classList.add("sel-active");
    $("#clearSel").classList.remove("hidden");
    if (hint) hint.classList.add("hidden");
  } else {
    selInfo.textContent = "Pages";
    selInfo.classList.remove("sel-active");
    $("#clearSel").classList.add("hidden");
    if (hint) hint.classList.remove("hidden");
  }
  $("#prevBtn").disabled = state.page <= 0;
  $("#nextBtn").disabled = state.page >= state.pages - 1;
}

function goPage(n) {
  state.page = Math.max(0, Math.min(state.pages - 1, n));
  renderViewer();
  updatePageInfo();
  scrollThumbTo(state.page);
  refreshThumbActive();
}

function zoomBy(d) {
  state.zoom = Math.min(3, Math.max(0.4, state.zoom + d));
  renderViewer();
}

// ============================================================ tools
const TOOLS = [
  { group: "Organize", items: [
    { icon: "trash",    label: "Delete pages",  desc: "Remove the pages you don't need",        act: deletePages,     needsSel: true },
    { icon: "rotate",   label: "Rotate",        desc: "Turn pages 90°, 180° or 270°",           act: rotateModal,     needsSel: true },
    { icon: "reverse",  label: "Reverse order", desc: "Flip the whole document end-to-end",     act: reverse },
    { icon: "split",    label: "Split",         desc: "Save a range of pages as a new file",    act: splitModal },
    { icon: "move",     label: "Move pages",    desc: "Put picked pages in a new position",     act: reorderSelected, needsSel: true },
  ]},
  { group: "Security", items: [
    { icon: "lock",      label: "Add password",    desc: "Ask for a password before opening",   act: protectModal },
    { icon: "lock-open", label: "Remove password", desc: "Unlock a protected file",             act: unlockModal },
    { icon: "watermark", label: "Watermark",       desc: "Stamp text across every page",        act: watermarkModal },
    { icon: "redact",    label: "Hide an area",    desc: "Cover up something permanently",      act: redactModal },
  ]},
  { group: "Optimize", items: [
    { icon: "compress", label: "Compress", desc: "Make the file smaller", act: compressModal },
  ]},
  { group: "Export", items: [
    { icon: "text",  label: "Extract text",  desc: "Save all the text as a .txt file",  act: exportText },
    { icon: "image", label: "Save as images", desc: "Turn each page into a PNG or JPG",  act: exportImagesModal },
  ]},
];

/** Run a tool, closing any open sheet first. */
function runTool(it) {
  closeSheet();
  if (!state.id) return;
  busy(true);
  Promise.resolve(it.act()).catch((e) => {
    if (e && e.message !== "no selection") toast(e.message || "That action failed.", "err");
  }).finally(() => busy(false));
}

function renderTools() {
  const nav = $("#tools");
  nav.innerHTML = "";
  for (const g of TOOLS) {
    const grp = document.createElement("div"); grp.className = "tool-group";
    const t = document.createElement("div"); t.className = "tool-group-title"; t.textContent = g.group;
    grp.appendChild(t);
    for (const it of g.items) {
      const b = document.createElement("button"); b.className = "tool-item";
      b.title = `${it.label} — ${it.desc}`;
      b.setAttribute("aria-label", `${it.label}. ${it.desc}`);
      b.innerHTML = `<span class="ti-tile">${icon(it.icon)}</span><span class="ti-label">${it.label}</span>`;
      b.onclick = () => runTool(it);
      grp.appendChild(b);
    }
    nav.appendChild(grp);
  }
}

function requireSel() {
  if (state.selection.size === 0) {
    const mobile = window.matchMedia("(max-width: 720px)").matches;
    toast(mobile
      ? "Pick pages first — tap Pages at the bottom, then Select."
      : "Pick a page first — right-click any thumbnail.", "err");
    throw new Error("no selection");
  }
  return [...state.selection].sort((a, b) => a - b);
}

// ---- organize
async function deletePages() {
  const pages = requireSel();
  modal({
    title: "Delete pages",
    sub: `Remove ${pages.length} page(s): ${pages.map(p => p + 1).join(", ")}. This cannot be undone.`,
    iconName: "trash", danger: true, okText: "Delete pages",
    fields: [],
    onOk: async () => {
      const s = await postJSON(`/api/session/${state.id}/delete`, { pages });
      applyEdit(s, `Removed ${plural(pages.length, "page")}`);
    },
  });
}

async function rotateModal() {
  const pages = requireSel();
  modal({
    title: "Rotate pages",
    sub: `Rotate ${pages.length} selected page(s) clockwise.`,
    iconName: "rotate",
    fields: [{ key: "deg", label: "Angle", type: "select", value: "90",
      options: [{ value: "90", label: "90°" }, { value: "180", label: "180°" }, { value: "270", label: "270°" }] }],
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/rotate`, { pages, deg: +v.deg });
      applyEdit(s, `Turned ${plural(pages.length, "page")} by ${v.deg}°`);
    },
  });
}

async function reverse() {
  modal({
    title: "Reverse page order",
    sub: "Flip the whole document end-to-end.",
    iconName: "reverse", okText: "Flip order",
    fields: [],
    onOk: async () => {
      const s = await postJSON(`/api/session/${state.id}/reverse`, {});
      applyEdit(s, "Page order flipped");
    },
  });
}

async function splitModal() {
  modal({
    title: "Split — extract a range",
    sub: "Creates a new document from the pages you choose (1-based, inclusive).",
    iconName: "split", okText: "Extract pages",
    fields: [
      { row: true, row: [
        { key: "start", label: "From page", type: "number", value: 1, min: 1 },
        { key: "end", label: "To page", type: "number", value: state.pages, min: 1 },
      ]},
    ],
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/split`, { start: +v.start, end: +v.end });
      adoptSession(s);
      toast(`Extracted ${s.pages} pages`);
    },
  });
}

async function reorderSelected() {
  const pages = requireSel();
  modal({
    title: "Move selected pages",
    sub: `Move ${pages.length} page(s) to a new position.`,
    iconName: "move", okText: "Move pages",
    fields: [{ key: "pos", label: "Insert before page", type: "number", value: 1, min: 1, max: state.pages + 1 }],
    onOk: async (v) => {
      const pos = Math.max(0, Math.min(state.pages, +v.pos - 1));
      const set = new Set(pages);
      const rest = [];
      for (let i = 0; i < state.pages; i++) if (!set.has(i)) rest.push(i);
      const order = rest.slice(0, pos).concat(pages).concat(rest.slice(pos));
      const s = await postJSON(`/api/session/${state.id}/reorder`, { order });
      applyEdit(s, `Moved ${plural(pages.length, "page")} to position ${pos + 1}`);
    },
  });
}

// ---- security
async function protectModal() {
  modal({
    title: "Protect with password",
    sub: "Encrypt the document. You will need this password to open it again.",
    iconName: "lock", okText: "Set password",
    fields: [
      { key: "user", label: "Open password", type: "password", placeholder: "required" },
      { key: "owner", label: "Owner password (optional)", type: "password", placeholder: "restricts editing & printing" },
    ],
    onOk: async (v) => {
      if (!v.user) throw new Error("open password required");
      const s = await postJSON(`/api/session/${state.id}/protect`, { user: v.user, owner: v.owner });
      applyEdit(s, "Locked — a password is now needed to open it");
    },
  });
}

async function unlockModal() {
  modal({
    title: "Unlock",
    sub: "Remove the password from this document.",
    iconName: "lock-open", okText: "Remove password",
    fields: [{ key: "password", label: "Password", type: "password" }],
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/unlock`, { password: v.password });
      applyEdit(s, "Unlocked — no password needed anymore");
    },
  });
}

async function watermarkModal() {
  modal({
    title: "Watermark",
    sub: "Stamp text on every page.",
    iconName: "watermark",
    fields: [
      { key: "text", label: "Text", type: "text", value: "CONFIDENTIAL" },
      { key: "wRatio", label: "Width (% of page)", type: "range", min: 10, max: 100, step: 5, value: 50 },
      { key: "hRatio", label: "Height (% of page)", type: "range", min: 2, max: 40, step: 2, value: 10 },
      { key: "pos", label: "Position", type: "select", value: "center",
        options: [{ value: "center", label: "Center" }, { value: "tl", label: "Top-left" }, { value: "tr", label: "Top-right" }, { value: "bl", label: "Bottom-left" }, { value: "br", label: "Bottom-right" }] },
    ],
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/watermark`, {
        text: v.text, wRatio: +v.wRatio / 100, hRatio: +v.hRatio / 100, pos: v.pos,
      });
      applyEdit(s, "Watermark added to every page");
    },
  });
}

async function redactModal() {
  const page = state.page;
  modal({
    title: "Redact (white-out)",
    sub: `Draw a box on page ${page + 1}. Coordinates are fractions of the page (0–1, origin top-left).`,
    iconName: "redact", danger: true, okText: "Hide area",
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
    onOk: async (v) => {
      const s = await postJSON(`/api/session/${state.id}/redact`, {
        rects: [{ page, x: +v.x, y: +v.y, w: +v.w, h: +v.h }],
      });
      applyEdit(s, `Area on page ${page + 1} is now hidden`);
    },
  });
}

// ---- optimize
async function compressModal() {
  modal({
    title: "Compress",
    sub: "Re-encode with Ghostscript. Lower quality means a smaller file.",
    iconName: "compress", okText: "Shrink file",
    fields: [{ key: "level", label: "Quality", type: "select", value: "1",
      options: [{ value: "0", label: "Smallest (72 dpi)" }, { value: "1", label: "Balanced (150 dpi)" }, { value: "2", label: "High (300 dpi)" }] }],
    onOk: async (v) => {
      const before = state.size;
      const s = await postJSON(`/api/session/${state.id}/compress`, { level: +v.level });
      const saved = before && s.size < before
        ? ` — ${kb(before)} down to ${kb(s.size)}`
        : "";
      applyEdit(s, "Compressed" + saved);
    },
  });
}

// ---- export
async function exportText() {
  const res = await fetchOk(`/api/session/${state.id}/export/text`);
  const text = await res.text();
  downloadBlob(new Blob([text], { type: "text/plain" }), state.name.replace(/\.pdf$/i, "") + ".txt");
  toast("Text file is ready to save");
}

async function exportImagesModal() {
  modal({
    title: "Export images",
    sub: "Render every page to an image.",
    iconName: "image", okText: "Save images",
    fields: [
      { key: "fmt", label: "Format", type: "select", value: "png", options: [{ value: "png", label: "PNG" }, { value: "jpg", label: "JPG" }] },
      { key: "w", label: "Width (px)", type: "number", value: 1100, min: 100 },
    ],
    onOk: async (v) => {
      const q = v.fmt === "jpg" ? `&fmt=jpg&q=85` : "";
      const data = await api(`/api/session/${state.id}/export/images?w=${v.w}${q}`);
      for (const [name, dataUrl] of Object.entries(data)) {
        const a = document.createElement("a");
        a.href = dataUrl; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
      }
      toast(`${plural(Object.keys(data).length, "image")} saved`);
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
function applyEdit(s, message) {
  state.pages = s.pages; state.version = s.version; state.size = s.size;
  state.name = s.name;
  if (state.page >= state.pages) state.page = state.pages - 1;
  for (const p of [...state.selection]) if (p >= state.pages) state.selection.delete(p);
  renderMeta();
  renderThumbs();
  renderViewer();
  updatePageInfo();
  toast(message || "All set.");
}

// ============================================================ download
async function download() {
  busy(true);
  try {
    const res = await fetchOk(`/api/session/${state.id}/download`);
    const blob = await res.blob();
    downloadBlob(blob, state.name.replace(/\.pdf$/i, "") + "-edited.pdf");
    toast("Saved to your downloads");
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
    if (e.key === "Escape" && !$("#sheet").classList.contains("hidden")) {
      closeSheet();
      return;
    }
    if (!$("#modal").classList.contains("hidden")) {
      if (e.key === "Escape") $("#modal").classList.add("hidden");
      return;
    }
    if (!state.id) return;
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
    if (typing) return;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); goPage(state.page + 1); }
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); goPage(state.page - 1); }
    else if (e.key === "Delete" && state.selection.size) { e.preventDefault(); Promise.resolve(deletePages()).catch(() => {}); }
    else if (e.key === "+" || e.key === "=") { zoomBy(0.1); }
    else if (e.key === "-") { zoomBy(-0.1); }
    else if (e.key === "Home") { e.preventDefault(); goPage(0); }
    else if (e.key === "End") { e.preventDefault(); goPage(state.pages - 1); }
  });
}
function scrollThumbTo(i) {
  const strip = $("#thumbs");
  const target = i * THUMB_H - strip.clientHeight / 2 + THUMB_H / 2;
  strip.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
}

// ============================================================ bottom sheet (mobile)
let sheetReturnFocus = null;

function openSheet(title, build) {
  sheetReturnFocus = document.activeElement;
  $("#sheetTitle").textContent = title;
  const body = $("#sheetBody");
  body.innerHTML = "";
  build(body);
  $("#sheet").classList.remove("hidden");
  requestAnimationFrame(() => $("#sheetBody").scrollTop = 0);
}

function closeSheet() {
  const el = $("#sheet");
  if (!el || el.classList.contains("hidden")) return;
  el.classList.add("hidden");
  $("#sheetBody").innerHTML = "";
  if (sheetReturnFocus && sheetReturnFocus.focus) sheetReturnFocus.focus();
  sheetReturnFocus = null;
}

function openToolsSheet() {
  openSheet("What would you like to do?", (body) => {
    const note = document.createElement("div");
    note.className = "sheet-note";
    note.style.padding = "2px 8px 6px";
    note.textContent = state.selection.size
      ? `${plural(state.selection.size, "page")} picked — these tools will use them.`
      : "Some tools ask you to pick pages first.";
    body.appendChild(note);

    // File actions first — the desktop "+" button has no mobile equivalent.
    const fileTitle = document.createElement("div");
    fileTitle.className = "sheet-group-title"; fileTitle.textContent = "File";
    body.appendChild(fileTitle);
    const openRow = document.createElement("button");
    openRow.className = "sheet-tool";
    openRow.innerHTML = `
      <span class="ti-tile">${icon("plus")}</span>
      <span class="sheet-tool-text">
        <span class="sheet-tool-label">Open another PDF</span>
        <span class="sheet-tool-desc">Pick a different file from your phone</span>
      </span>
      ${icon("next", "icon chev")}`;
    openRow.onclick = () => { closeSheet(); setTimeout(() => $("#file").click(), 180); };
    body.appendChild(openRow);

    for (const g of TOOLS) {
      const t = document.createElement("div");
      t.className = "sheet-group-title"; t.textContent = g.group;
      body.appendChild(t);
      for (const it of g.items) {
        const b = document.createElement("button");
        b.className = "sheet-tool";
        b.setAttribute("aria-label", `${it.label}. ${it.desc}`);
        b.innerHTML = `
          <span class="ti-tile">${icon(it.icon)}</span>
          <span class="sheet-tool-text">
            <span class="sheet-tool-label">${it.label}</span>
            <span class="sheet-tool-desc">${it.desc}</span>
          </span>
          ${icon("next", "icon chev")}`;
        b.onclick = () => runTool(it);
        body.appendChild(b);
      }
    }
  });
}

// ---- pages sheet: tap to jump, toggle to pick ----
let sheetSelecting = false;

function openPagesSheet() {
  sheetSelecting = state.selection.size > 0;
  openSheet(state.name ? `Pages — ${state.name}` : "Pages", (body) => {
    const bar = document.createElement("div");
    bar.className = "sheet-toolbar";
    bar.innerHTML = `
      <span class="sheet-note" id="pagesNote"></span>
      <button class="sheet-toggle" id="pagesToggle" aria-pressed="${sheetSelecting}">
        ${icon("check")}<span>Select</span>
      </button>`;
    body.appendChild(bar);

    const grid = document.createElement("div");
    grid.className = "page-grid" + (sheetSelecting ? " selecting" : "");
    grid.id = "pagesGrid";

    for (let i = 0; i < state.pages; i++) {
      const cell = document.createElement("button");
      cell.className = "page-cell"
        + (i === state.page ? " current" : "")
        + (state.selection.has(i) ? " picked" : "");
      cell.dataset.page = i;
      cell.setAttribute("aria-label", `Page ${i + 1}`);
      cell.innerHTML = `
        <img loading="lazy" src="/api/session/${state.id}/page/${i}/thumb?w=150&v=${state.version}" alt="">
        <span class="pc-num">${i + 1}</span>
        <span class="pc-check">${icon("check")}</span>`;
      cell.onclick = () => onPageCell(i, cell, grid);
      grid.appendChild(cell);
    }
    body.appendChild(grid);

    const toggle = bar.querySelector("#pagesToggle");
    toggle.onclick = () => {
      sheetSelecting = !sheetSelecting;
      toggle.setAttribute("aria-pressed", String(sheetSelecting));
      grid.classList.toggle("selecting", sheetSelecting);
      refreshPagesNote();
    };
    refreshPagesNote();
  });
}

function refreshPagesNote() {
  const note = document.getElementById("pagesNote");
  if (!note) return;
  const n = state.selection.size;
  note.textContent = sheetSelecting
    ? (n ? `${plural(n, "page")} picked — tap more, or close and open Tools.` : "Tap the pages you want to work on.")
    : "Tap a page to view it.";
}

function onPageCell(i, cell, grid) {
  if (sheetSelecting) {
    if (state.selection.has(i)) state.selection.delete(i); else state.selection.add(i);
    cell.classList.toggle("picked", state.selection.has(i));
    refreshThumbActive();
    updatePageInfo();
    refreshPagesNote();
    return;
  }
  goPage(i);
  for (const c of grid.children) c.classList.toggle("current", +c.dataset.page === i);
  closeSheet();
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
  $("#prevBtn").onclick = () => goPage(state.page - 1);
  $("#nextBtn").onclick = () => goPage(state.page + 1);
  $("#clearSel").onclick = () => { state.selection.clear(); refreshThumbActive(); updatePageInfo(); };

  // mobile bottom bar
  $("#mPages").onclick = openPagesSheet;
  $("#mTools").onclick = openToolsSheet;
  $("#mSave").onclick = download;
  $("#sheetClose").onclick = closeSheet;
  $("#sheet").querySelector("[data-close]").onclick = closeSheet;
}
init();
