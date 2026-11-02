import { fillModelSelect } from "./models.js";
import { pickFolder, rememberDir, savedDir } from "./folder.js";
import { mountProjects } from "./projects.js";
import { openSlideshow } from "./slideshow.js";

const $ = (id) => document.getElementById(id);

const MAX_BLOB_TILES = 700;
const els = {
  blob: $("blob"),
  layer: $("flightLayer"),
  photoCards: $("photoCards"),
  videoCards: $("videoCards"),
  dupCard: $("dupCard"),
  status: $("status"),
  statusText: $("statusText"),
  sortBtn: $("sortBtn"),
  sortLabel: $("sortLabel"),
  undoBtn: $("undoBtn"),
  dirInput: $("dirInput"),
  dirName: $("dirName"),
  footnote: $("footnote"),
  modelSelect: $("modelSelect"),
};

let config = null;
let phase = "idle"; // idle | scanning | ready | sorting | sorted | moved
let items = new Map(); // name -> { id, name, kind, thumb, tile }
let mediaCount = 0;
let tileIndex = 0;
let dupeNames = new Set();
let cards = { image: {}, video: {} };
let counts = new Map(); // card element -> count
let landed = 0;
let queue = [];
let inFlight = 0;
let classifyDone = false;
let lastClassify = null;
let refreshProjects = () => {};

function setStatus(text, mode = "busy") {
  els.statusText.textContent = text;
  els.status.className = `status ${mode}`;
}

function setStat(id, v) {
  $(id).textContent = v;
}

// ---------- cards ----------

function makeCard(name, kind) {
  const card = document.createElement("div");
  card.className = "card empty";
  card.innerHTML = `<div class="card-head"><span class="folder-ic"></span><span class="card-name"></span><span class="card-play" title="Slideshow">▶</span><span class="card-count">0</span></div><div class="thumbs"></div>`;
  card.querySelector(".card-name").textContent = name;
  card.title = "Click a photo to preview it, or the folder to play all";
  card.onclick = (e) => folderSlideshow(`${kind === "video" ? "Videos" : "Photos"} / ${name}`,
    (it) => !dupeNames.has(it.name) && it.kind === kind && it.category === name, e.target.dataset.name);
  return card;
}

// Slideshow of the files sorted into one card, with their tags; starts at the
// clicked thumbnail when there is one.
async function folderSlideshow(title, match, startName) {
  const list = [...items.values()].filter(match);
  if (!list.length) return;
  let info = new Map();
  try {
    const d = await (await fetch("/api/tags")).json();
    info = new Map(d.files.map((f) => [f.id, f]));
  } catch {}
  openSlideshow({
    title: `${title} · ${els.dirName.textContent}`,
    items: list.map((it) => ({ ...it, tags: info.get(it.id)?.tags || [], description: info.get(it.id)?.description || "" })),
    start: Math.max(0, list.findIndex((it) => it.name === startName)),
  });
}

function buildCards() {
  els.photoCards.innerHTML = "";
  els.videoCards.innerHTML = "";
  cards = { image: {}, video: {} };
  counts = new Map();
  for (const cat of config.categories) {
    cards.image[cat] = els.photoCards.appendChild(makeCard(cat, "image"));
    cards.video[cat] = els.videoCards.appendChild(makeCard(cat, "video"));
  }
  els.dupCard.querySelector(".thumbs").innerHTML = "";
  els.dupCard.querySelector(".card-count").textContent = "0";
  els.dupCard.classList.add("empty");
  setStat("sFolders", 0);
}

function bumpCard(card) {
  const n = (counts.get(card) || 0) + 1;
  counts.set(card, n);
  card.querySelector(".card-count").textContent = n;
  if (n === 1) {
    card.classList.remove("empty");
    setStat("sFolders", [...counts.values()].filter(Boolean).length);
  }
  card.classList.remove("bump");
  void card.offsetWidth;
  card.classList.add("bump");
}

function slotCapacity(thumbs) {
  const tile = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tile")) || 18;
  return Math.max(1, Math.floor((thumbs.clientWidth + 3) / (tile + 3))) * 3;
}

// ---------- blob ----------

function placeTile(item) {
  if (!item.thumb || tileIndex >= MAX_BLOB_TILES) return;
  const n = Math.max(1, Math.min(mediaCount, MAX_BLOB_TILES));
  const i = tileIndex++;
  // Sunflower (phyllotaxis) packing inside a circle, with a little jitter.
  const R = 44;
  const r = R * Math.sqrt((i + 0.5) / n);
  const theta = i * 2.39996323;
  const size = Math.min(14, R * Math.sqrt(Math.PI / n) * 1.12) * (0.85 + Math.random() * 0.3);
  const x = 50 + r * Math.cos(theta) + (Math.random() - 0.5) * 1.2;
  const y = 50 + r * Math.sin(theta) + (Math.random() - 0.5) * 1.2;
  const img = document.createElement("img");
  img.className = "tile";
  img.src = `/thumb/${item.id}.jpg`;
  img.alt = "";
  img.title = item.name;
  img.style.cssText = `width:${size}%;height:${size}%;left:${x - size / 2}%;top:${y - size / 2}%;--r:${((Math.random() - 0.5) * 30).toFixed(1)}deg;animation-delay:${Math.min(i * 4, 900)}ms;z-index:${n - i}`;
  els.blob.appendChild(img);
  item.tile = img;
}

// ---------- flights ----------

function enqueue(name, category) {
  const item = items.get(name);
  if (!item || item.category) return;
  item.category = category;
  queue.push(item);
}

function targetFor(item) {
  const card = dupeNames.has(item.name) ? els.dupCard : cards[item.kind][item.category] || cards[item.kind].Other;
  const thumbs = card.querySelector(".thumbs");
  let slot = null;
  if (item.thumb && thumbs.children.length < slotCapacity(thumbs)) {
    slot = document.createElement("img");
    slot.className = "pending";
    slot.src = `/thumb/${item.id}.jpg`;
    slot.alt = "";
    slot.title = item.name;
    slot.dataset.name = item.name;
    thumbs.appendChild(slot);
  }
  return { card, slot };
}

function fly(item) {
  const { card, slot } = targetFor(item);
  const blobRect = els.blob.getBoundingClientRect();
  const from = item.tile
    ? item.tile.getBoundingClientRect()
    : { left: blobRect.left + blobRect.width / 2 - 8, top: blobRect.top + blobRect.height / 2 - 8, width: 16, height: 16 };
  const to = slot ? slot.getBoundingClientRect() : (() => {
    const c = card.getBoundingClientRect();
    return { left: c.left + c.width / 2 - 6, top: c.top + c.height / 2 - 6, width: 12, height: 12 };
  })();

  if (item.tile) item.tile.classList.add("gone");
  if (!item.thumb) {
    // Nothing to show in flight: just count it.
    bumpCard(card);
    landed++;
    return;
  }

  const ghost = document.createElement("img");
  ghost.src = `/thumb/${item.id}.jpg`;
  ghost.style.width = `${from.width}px`;
  ghost.style.height = `${from.height}px`;
  els.layer.appendChild(ghost);
  inFlight++;

  const sx = to.width / from.width;
  const sy = to.height / from.height;
  const dx = to.left - from.left;
  const dy = to.top - from.top;
  // Arc: lift the midpoint so tiles swoop rather than slide.
  const mx = from.left + dx * 0.5;
  const my = from.top + dy * 0.5 - 60 - Math.random() * 80;
  const rot = (Math.random() - 0.5) * 50;
  const anim = ghost.animate(
    [
      { transform: `translate(${from.left}px, ${from.top}px) scale(1) rotate(0deg)`, offset: 0 },
      { transform: `translate(${mx}px, ${my}px) scale(${(1 + sx) / 1.6}) rotate(${rot}deg)`, offset: 0.45 },
      { transform: `translate(${to.left}px, ${to.top}px) scale(${sx}, ${sy}) rotate(0deg)`, offset: 1, opacity: slot ? 1 : 0 },
    ],
    { duration: 650 + Math.random() * 350, easing: "cubic-bezier(.45,.05,.25,1)", fill: "forwards" }
  );
  ghost.style.transformOrigin = "0 0";
  anim.onfinish = () => {
    if (slot) slot.classList.remove("pending");
    ghost.remove();
    inFlight--;
    landed++;
    bumpCard(card);
    updateSortingStatus();
  };
}

function pump() {
  if (queue.length && inFlight < 140) {
    const n = Math.min(queue.length, Math.max(1, Math.ceil(queue.length / 45)));
    for (let i = 0; i < n; i++) fly(queue.shift());
  }
  updateSortingStatus();
  requestAnimationFrame(pump);
}

function updateSortingStatus() {
  if (phase !== "sorting") return;
  if (classifyDone && !queue.length && !inFlight) return finishSorting();
  setStatus(`Sorting into folders… ${landed}/${items.size}`);
}

function finishSorting() {
  phase = "sorted";
  setStatus("Sorted — review, then move", "done");
  els.blob.style.opacity = "0.35";
  $("blobCaption").textContent = "all tidy";
  els.sortLabel.textContent = "Move files";
  els.sortBtn.disabled = false;
  const notes = [];
  if (lastClassify && !lastClassify.ai) notes.push(`The selected model isn't available, so only cached results and filename/size rules were used. ${lastClassify.note || ""}`);
  if (lastClassify?.failed) notes.push(`${lastClassify.failed} file(s) fell back to filename rules (${lastClassify.lastError}).`);
  els.footnote.textContent = notes.join(" ");
}

// ---------- scan / classify / move ----------

function scan() {
  phase = "scanning";
  items = new Map();
  tileIndex = 0;
  landed = 0;
  queue = [];
  dupeNames = new Set();
  classifyDone = false;
  els.blob.innerHTML = "";
  els.blob.style.opacity = "";
  $("blobCaption").textContent = "a small mess";
  els.footnote.textContent = "";
  els.sortLabel.textContent = "Sort";
  els.sortBtn.disabled = true;
  buildCards();
  ["sTotal", "sVideos", "sPhotos", "sDupes"].forEach((id) => setStat(id, "–"));
  setStatus("Scanning…");

  const dir = els.dirInput.value.trim();
  rememberDir(dir);
  const es = new EventSource(`/api/scan?dir=${encodeURIComponent(dir)}`);
  let photos = 0, videos = 0;
  es.addEventListener("start", (e) => {
    const d = JSON.parse(e.data);
    mediaCount = d.media;
    setStat("sTotal", d.totalFiles);
    els.dirName.textContent = d.dir.split("/").filter(Boolean).pop() || d.dir;
  });
  es.addEventListener("item", (e) => {
    const it = JSON.parse(e.data);
    it.category = null; // re-assigned (from cache or AI) when sorting
    items.set(it.name, it);
    it.kind === "video" ? videos++ : photos++;
    setStat("sVideos", videos);
    setStat("sPhotos", photos);
    placeTile(it);
    setStatus(`Scanning… ${items.size}/${mediaCount}`);
  });
  es.addEventListener("done", (e) => {
    es.close();
    const d = JSON.parse(e.data);
    dupeNames = new Set(d.dupeIds);
    setStat("sDupes", d.duplicates);
    phase = "ready";
    setStatus(
      !items.size ? "No photos or videos here"
        : d.alreadySorted ? `${items.size} photos & videos (${d.alreadySorted} already in Sorted/) — press Sort to see them by folder`
        : `${items.size} photos & videos found`,
      "idle",
    );
    els.sortBtn.disabled = !items.size;
    els.undoBtn.classList.toggle("hidden", !d.canUndo);
    refreshProjects();
    // Already tagged (e.g. a sorted project): show the groups straight away from saved results.
    if (items.size && d.allAnalyzed) sort();
  });
  es.addEventListener("error", (e) => {
    es.close();
    phase = "idle";
    let msg = "Scan failed";
    try { msg = JSON.parse(e.data).message; } catch {}
    setStatus(msg, "idle");
  });
}

function sort() {
  phase = "sorting";
  els.sortBtn.disabled = true;
  setStatus("Sorting into folders…");
  const es = new EventSource(`/api/classify?model=${encodeURIComponent(els.modelSelect.value)}`);
  es.addEventListener("result", (e) => {
    const r = JSON.parse(e.data);
    enqueue(r.name, r.category);
  });
  es.addEventListener("done", (e) => {
    es.close();
    lastClassify = JSON.parse(e.data);
    classifyDone = true;
    refreshProjects();
  });
  es.onerror = () => {
    es.close();
    classifyDone = true;
  };
}

async function move() {
  const photoCats = Object.entries(cards.image).filter(([, c]) => counts.get(c)).length;
  const videoCats = Object.entries(cards.video).filter(([, c]) => counts.get(c)).length;
  $("confirmText").textContent =
    `${items.size} files will be moved into:\n` +
    `${els.dirInput.value.replace(/\/$/, "")}/Sorted/Photos/… (${photoCats} folders)\n` +
    `${els.dirInput.value.replace(/\/$/, "")}/Sorted/Videos/… (${videoCats} folders)` +
    (dupeNames.size ? `\n${dupeNames.size} duplicate copies → Sorted/Duplicates` : "") +
    `\n\nNothing is deleted, and you can undo.`;
  const dlg = $("confirmDlg");
  dlg.showModal();
  const ok = await new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
  if (!ok) return;
  els.sortBtn.disabled = true;
  setStatus("Moving files…");
  const res = await fetch("/api/apply", { method: "POST" });
  const d = await res.json();
  if (!res.ok) return setStatus(d.error || "Move failed", "idle");
  phase = "moved";
  setStatus(`Moved ${d.moved} files into Sorted/`, "done");
  els.sortLabel.textContent = "Sorted";
  els.undoBtn.classList.remove("hidden");
  refreshProjects();
}

async function undo() {
  els.undoBtn.disabled = true;
  setStatus("Putting files back…");
  const res = await fetch("/api/undo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir: els.dirInput.value.trim() }),
  });
  const d = await res.json();
  els.undoBtn.disabled = false;
  if (!res.ok) return setStatus(d.error || "Undo failed", "idle");
  els.undoBtn.classList.add("hidden");
  scan();
  if (d.failed) els.footnote.textContent = `${d.failed} file(s) could not be restored (see .pics-manager-undo.json).`;
}

els.sortBtn.addEventListener("click", () => {
  if (phase === "ready") sort();
  else if (phase === "sorted") move();
});
els.undoBtn.addEventListener("click", undo);
els.dirInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && phase !== "scanning" && phase !== "sorting") scan();
});
$("pickDirBtn").addEventListener("click", async () => {
  if (phase === "scanning" || phase === "sorting") return;
  try {
    const dir = await pickFolder(els.dirInput.value.trim());
    if (dir) {
      els.dirInput.value = dir;
      scan();
    }
  } catch (err) {
    els.footnote.textContent = err.message;
  }
});

els.dupCard.title = "Click to view as a slideshow";
els.dupCard.onclick = (e) => folderSlideshow("Duplicates", (it) => dupeNames.has(it.name), e.target.dataset.name);

config = await (await fetch("/api/config")).json();
els.dirInput.value = savedDir(config.defaultDir);
fillModelSelect(els.modelSelect, config);
refreshProjects = mountProjects($("projects"), (dir) => {
  if (phase === "scanning" || phase === "sorting") return;
  els.dirInput.value = dir;
  scan();
});
requestAnimationFrame(pump);
scan();
