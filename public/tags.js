import { fillModelSelect, selectedModel } from "./models.js";
import { pickFolder, rememberDir, savedDir } from "./folder.js";
import { openSlideshow } from "./slideshow.js";

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  statusText: $("statusText"),
  dirInput: $("dirInput"),
  dirName: $("dirName"),
  modelSelect: $("modelSelect"),
  tagBtn: $("tagBtn"),
  retagBtn: $("retagBtn"),
  tagSearch: $("tagSearch"),
  sideViews: $("sideViews"),
  tagList: $("tagList"),
  minCount: $("minCount"),
  moveBtn: $("moveBtn"),
  moveLabel: $("moveLabel"),
  undoBtn: $("undoBtn"),
  viewTitle: $("viewTitle"),
  viewSub: $("viewSub"),
  grid: $("grid"),
  emptyNote: $("emptyNote"),
  footnote: $("footnote"),
  editDlg: $("editDlg"),
};

let config = null;
let files = []; // [{ id, name, kind, thumb, analyzed, category, tags, description, model, edited }]
let view = { type: "all", tag: null }; // all | todo | edited | tag
let chosen = new Set(); // tags that become folders
let busy = false;
let editing = null; // id of the file open in the editor
let renderQueued = false;
let firstLoad = true;

function setStatus(text, mode = "busy") {
  els.statusText.textContent = text;
  els.status.className = `status ${mode}`;
}

function setBusy(b) {
  busy = b;
  els.tagBtn.disabled = b || !files.length;
  els.retagBtn.disabled = b || !files.length;
  els.moveBtn.disabled = b || !chosen.size;
  els.undoBtn.disabled = b;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function tagCounts() {
  const counts = new Map();
  for (const f of files) for (const t of f.tags) counts.set(t, (counts.get(t) || 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function visibleFiles() {
  if (view.type === "todo") return files.filter((f) => !f.analyzed);
  if (view.type === "edited") return files.filter((f) => f.edited);
  if (view.type === "tag") return files.filter((f) => f.tags.includes(view.tag));
  return files;
}

function viewTitle(v) {
  const titles = { all: "All files", todo: "Not analyzed yet", edited: "Edited by hand" };
  return v.type === "tag" ? `#${v.tag}` : titles[v.type];
}

// Slideshow of a tag (or the current view), optionally starting at a file.
function slideshow(v = view, startId = null) {
  const prev = view;
  view = v;
  const list = visibleFiles();
  view = prev;
  openSlideshow({
    title: `${viewTitle(v)} · ${els.dirName.textContent}`,
    items: list,
    start: Math.max(0, list.findIndex((f) => f.id === startId)),
    onTag: (tag) => {
      setView({ type: "tag", tag });
      slideshow({ type: "tag", tag });
    },
  });
}

function queueRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    render();
  });
}

// ---------- sidebar ----------

function viewRow(label, count, type) {
  const row = el("div", `row${view.type === type ? " active" : ""}`);
  row.append(el("span", "name", label), el("span", "count", count));
  row.onclick = () => setView({ type, tag: null });
  return row;
}

function renderSide() {
  els.sideViews.replaceChildren(
    viewRow("All files", files.length, "all"),
    viewRow("Not analyzed yet", files.filter((f) => !f.analyzed).length, "todo"),
    viewRow("Edited by hand", files.filter((f) => f.edited).length, "edited")
  );

  const q = els.tagSearch.value.trim().toLowerCase();
  const rows = [];
  for (const [tag, count] of tagCounts()) {
    if (q && !tag.includes(q)) continue;
    const row = el("div", `row${view.type === "tag" && view.tag === tag ? " active" : ""}`);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = chosen.has(tag);
    box.title = "Make a folder for this tag";
    box.onclick = (e) => {
      e.stopPropagation();
      box.checked ? chosen.add(tag) : chosen.delete(tag);
      updateMoveBtn();
    };
    const play = el("button", "row-play", "▶");
    play.title = `Slideshow of “${tag}”`;
    play.onclick = (e) => {
      e.stopPropagation();
      setView({ type: "tag", tag });
      slideshow({ type: "tag", tag });
    };
    row.append(box, el("span", "name", tag), play, el("span", "count", count));
    row.onclick = () => setView({ type: "tag", tag });
    rows.push(row);
  }
  if (!rows.length) rows.push(el("div", "side-hint", files.some((f) => f.analyzed) ? "No matching tags" : "No tags yet — press Tag photos"));
  els.tagList.replaceChildren(...rows);
  updateMoveBtn();
}

function updateMoveBtn() {
  // Drop ticks for tags that no longer exist (e.g. removed from every file).
  const live = new Set(tagCounts().map(([t]) => t));
  for (const t of chosen) if (!live.has(t)) chosen.delete(t);
  els.moveLabel.textContent = chosen.size ? `Move into ${chosen.size} tag folder${chosen.size > 1 ? "s" : ""}` : "Move into tag folders";
  els.moveBtn.disabled = busy || !chosen.size;
}

// ---------- grid ----------

function card(f) {
  const c = el("div", "tcard");
  c.title = f.description || f.name;
  const pic = el("div", "pic");
  if (f.thumb) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `/thumb/${f.id}.jpg`;
    img.alt = "";
    pic.append(img);
  }
  if (f.kind === "video") pic.append(el("span", "badge", "video"));
  if (f.edited) pic.append(el("span", "badge edited", "edited"));
  const big = el("button", "pic-play", "⤢");
  big.title = "View as slideshow from here";
  big.onclick = (e) => {
    e.stopPropagation();
    slideshow(view, f.id);
  };
  pic.append(big);
  c.append(pic, el("div", "fname", f.name));

  const chips = el("div", "chips");
  if (!f.analyzed) chips.append(el("span", "chip", "not analyzed"));
  for (const t of f.tags.slice(0, 6)) {
    const chip = el("span", "chip", t);
    chip.onclick = (e) => {
      e.stopPropagation();
      setView({ type: "tag", tag: t });
    };
    chips.append(chip);
  }
  if (f.tags.length > 6) chips.append(el("span", "chip", `+${f.tags.length - 6}`));
  c.append(chips);

  if (view.type === "tag") {
    const rm = el("button", "unremove", `× Not “${view.tag}”`);
    rm.title = `Remove the tag “${view.tag}” from this file`;
    rm.onclick = async (e) => {
      e.stopPropagation();
      await editTags(f.id, { remove: [view.tag] });
    };
    c.append(rm);
  }
  c.onclick = () => openEditor(f.id);
  return c;
}

function renderGrid() {
  const list = visibleFiles();
  els.viewTitle.textContent = viewTitle(view);
  $("slideshowBtn").disabled = !list.length;
  els.viewSub.textContent = `${list.length} file${list.length === 1 ? "" : "s"}`;
  els.grid.replaceChildren(...list.map(card));
  els.emptyNote.classList.toggle("hidden", list.length > 0);
  els.emptyNote.textContent = files.length ? "Nothing here." : "No photos or videos in this folder.";
}

function render() {
  renderSide();
  renderGrid();
}

function setView(v) {
  view = v;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---------- data ----------

async function load() {
  const res = await fetch("/api/tags");
  const d = await res.json();
  // Opened from a project card: switch the server to that folder first.
  const wanted = firstLoad ? savedDir(null) : null;
  firstLoad = false;
  if (!d.dir || (wanted && wanted !== d.dir)) return scan();
  files = d.files;
  els.dirInput.value = d.dir;
  fillProjects(d.dir);
  els.dirName.textContent = d.dir.split("/").filter(Boolean).pop() || d.dir;
  els.undoBtn.classList.toggle("hidden", !d.canUndo);
  if (view.type === "tag" && !files.some((f) => f.tags.includes(view.tag))) view = { type: "all", tag: null };
  render();
  setBusy(false);
  const todo = files.filter((f) => !f.analyzed).length;
  setStatus(!files.length ? "No photos or videos here" : todo ? `${todo} file(s) not analyzed yet` : `${files.length} files tagged`, todo ? "idle" : "done");
}

// Project switcher: every scanned folder, each with its own tags.
async function fillProjects(current) {
  const { projects } = await (await fetch("/api/projects")).json();
  const sel = $("projectSelect");
  sel.replaceChildren(
    ...projects.filter((p) => p.exists).map((p) => {
      const tags = p.tags != null ? ` · ${p.tags} tags` : "";
      return new Option(`${p.name} — ${p.unique ?? 0} files${tags}`, p.dir, false, p.dir === current);
    }),
  );
  if (![...sel.options].some((o) => o.selected)) sel.prepend(new Option("Projects…", "", true, true));
}

function switchTo(dir) {
  els.dirInput.value = dir;
  view = { type: "all", tag: null };
  chosen.clear();
  els.tagSearch.value = "";
  scan();
}

function scan() {
  setBusy(true);
  setStatus("Scanning…");
  const dir = els.dirInput.value.trim() || config.defaultDir;
  rememberDir(dir);
  const es = new EventSource(`/api/scan?dir=${encodeURIComponent(dir)}`);
  let n = 0;
  let total = 0;
  es.addEventListener("start", (e) => (total = JSON.parse(e.data).media));
  es.addEventListener("item", () => setStatus(`Scanning… ${++n}/${total}`));
  es.addEventListener("done", () => {
    es.close();
    load();
  });
  es.addEventListener("error", (e) => {
    es.close();
    setBusy(false);
    let msg = "Scan failed";
    try { msg = JSON.parse(e.data).message; } catch {}
    setStatus(msg, "idle");
  });
}

function tagAll(force) {
  const model = selectedModel(els.modelSelect, config);
  if (!model.available) {
    els.footnote.textContent = `${model.label} isn't ready. ${model.note}`;
    return;
  }
  setBusy(true);
  els.footnote.textContent = "";
  const es = new EventSource(`/api/tag?model=${encodeURIComponent(model.id)}${force ? "&force=true" : ""}`);
  let done = 0;
  let total = 0;
  let failed = 0;
  let lastError = "";
  const byId = new Map(files.map((f) => [f.id, f]));
  es.addEventListener("progress", (e) => {
    total = JSON.parse(e.data).total;
    setStatus(`Tagging with ${model.label.split("·")[0].trim()}… 0/${total}`);
  });
  es.addEventListener("result", (e) => {
    const r = JSON.parse(e.data);
    done++;
    if (r.error) {
      failed++;
      lastError = r.error;
    }
    const f = byId.get(r.id);
    if (f && r.tags) Object.assign(f, { analyzed: true, tags: r.tags, description: r.description, category: r.category, model: r.model });
    setStatus(`Tagging… ${done}/${total}`);
    queueRender();
  });
  es.addEventListener("done", () => {
    es.close();
    if (failed) els.footnote.textContent = `${failed} file(s) could not be analyzed: ${lastError}`;
    load();
  });
  es.addEventListener("error", (e) => {
    es.close();
    let msg = "Tagging stopped";
    try { msg = JSON.parse(e.data).message; } catch {}
    setStatus(msg, "idle");
    load();
  });
}

async function editTags(id, body) {
  const res = await fetch(`/api/items/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await res.json();
  if (!res.ok) {
    els.footnote.textContent = d.error || "Could not save the change";
    return null;
  }
  const f = files.find((x) => x.id === id);
  Object.assign(f, { tags: d.tags, category: d.category, edited: d.edited });
  render();
  if (editing === id) fillEditor(f);
  return d;
}

// ---------- editor ----------

function openEditor(id) {
  const f = files.find((x) => x.id === id);
  if (!f) return;
  editing = id;
  fillEditor(f);
  $("editImg").src = `/preview/${f.id}.jpg`;
  if (!els.editDlg.open) els.editDlg.showModal();
  $("addTag").focus();
}

function fillEditor(f) {
  $("editName").textContent = f.name;
  $("editDesc").textContent = f.analyzed ? f.description : "Not analyzed yet — press Tag photos first.";
  const sel = $("editCategory");
  sel.replaceChildren(...config.categories.map((c) => Object.assign(document.createElement("option"), { value: c, textContent: c })));
  sel.value = f.category || "Other";
  sel.disabled = !f.analyzed;
  $("addTag").disabled = !f.analyzed;

  const chips = f.tags.map((t) => {
    const chip = el("span", "chip", t);
    const x = el("button", "x", "×");
    x.title = `Remove “${t}”`;
    x.onclick = () => editTags(f.id, { remove: [t] });
    chip.append(x);
    return chip;
  });
  if (!chips.length) chips.push(el("span", "side-hint", f.analyzed ? "No tags" : ""));
  $("editTags").replaceChildren(...chips);
  const model = config.models.find((m) => m.id === f.model);
  $("editMeta").textContent = f.analyzed ? `Analyzed by ${model ? model.label : f.model}${f.edited ? " · edited by hand" : ""}` : "";
}

function step(dir) {
  const list = visibleFiles();
  const i = list.findIndex((f) => f.id === editing);
  const next = list[(i + dir + list.length) % list.length];
  if (next) openEditor(next.id);
}

$("addTag").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const add = e.target.value.split(",").map((t) => t.trim()).filter(Boolean);
  if (!add.length || !editing) return;
  if (await editTags(editing, { add })) e.target.value = "";
});
$("editCategory").addEventListener("change", (e) => editing && editTags(editing, { category: e.target.value }));
$("doneBtn").onclick = () => els.editDlg.close();
$("prevBtn").onclick = () => step(-1);
$("nextBtn").onclick = () => step(1);
els.editDlg.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "ArrowLeft") step(-1);
  if (e.key === "ArrowRight") step(1);
});
els.editDlg.addEventListener("close", () => (editing = null));
els.editDlg.addEventListener("click", (e) => e.target === els.editDlg && els.editDlg.close());

// ---------- moving ----------

async function moveToFolders() {
  const tags = [...chosen];
  let affected = 0;
  let extra = 0;
  for (const f of files) {
    const n = f.tags.filter((t) => chosen.has(t)).length;
    if (n) affected++;
    if (n > 1) extra += n - 1;
  }
  const base = `${els.dirInput.value.replace(/\/$/, "")}/Sorted/Tags/`;
  $("moveText").textContent =
    `${affected} file(s) will be moved into ${tags.length} folder(s) in\n${base}\n\n` +
    `${tags.slice(0, 12).join(", ")}${tags.length > 12 ? `, +${tags.length - 12} more` : ""}\n\n` +
    (extra ? `Files with several of these tags go into each of those folders (${extra} extra entries, as hard links — no extra disk space).\n` : "") +
    `Files without any of these tags stay where they are. Nothing is deleted, and you can undo.`;
  const dlg = $("moveDlg");
  dlg.showModal();
  const ok = await new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
  if (!ok) return;
  setBusy(true);
  setStatus("Moving files…");
  const res = await fetch("/api/apply-tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
  const d = await res.json();
  if (!res.ok) {
    setBusy(false);
    return setStatus(d.error || "Move failed", "idle");
  }
  chosen.clear();
  els.footnote.textContent =
    `Moved ${d.moved} file(s) into ${d.folders} folder(s) in ${d.base}` +
    (d.extraCopies ? `, plus ${d.extraCopies} extra entr${d.extraCopies === 1 ? "y" : "ies"} for multi-tag files.` : ".") +
    (d.errors.length ? ` ${d.errors.length} failed: ${d.errors[0]}` : "");
  scan();
}

async function undo() {
  setBusy(true);
  setStatus("Putting files back…");
  const res = await fetch("/api/undo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir: els.dirInput.value.trim() }),
  });
  const d = await res.json();
  if (!res.ok) {
    setBusy(false);
    return setStatus(d.error || "Undo failed", "idle");
  }
  els.footnote.textContent = `Restored ${d.restored} file(s).` + (d.failed ? ` ${d.failed} could not be restored (see .pics-manager-undo.json).` : "");
  scan();
}

// ---------- wiring ----------

els.tagBtn.onclick = () => tagAll(false);
els.retagBtn.onclick = () => tagAll(true);
els.moveBtn.onclick = moveToFolders;
els.undoBtn.onclick = undo;
els.tagSearch.oninput = renderSide;
$("pickBtn").onclick = () => {
  const min = Math.max(1, Number(els.minCount.value) || 1);
  for (const [t, n] of tagCounts()) if (n >= min) chosen.add(t);
  renderSide();
};
$("clearBtn").onclick = () => {
  chosen.clear();
  renderSide();
};
els.dirInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !busy) scan();
});
$("pickDirBtn").addEventListener("click", async () => {
  if (busy) return;
  try {
    const dir = await pickFolder(els.dirInput.value.trim());
    if (dir) switchTo(dir);
  } catch (err) {
    els.footnote.textContent = err.message;
  }
});

$("slideshowBtn").onclick = () => slideshow();
$("projectSelect").addEventListener("change", (e) => {
  if (busy || !e.target.value) return;
  switchTo(e.target.value);
});

config = await (await fetch("/api/config")).json();
els.dirInput.value = savedDir(config.defaultDir);
fillModelSelect(els.modelSelect, config);
load();
