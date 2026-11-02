// Project cards: every folder that has been scanned, with its counts and the
// Sorted/ folders it produced. Folders open as a slideshow or in Finder.

import { openSlideshow } from "./slideshow.js";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

async function openPath(path) {
  await fetch("/api/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

async function folderSlideshow(path, title) {
  const res = await fetch(`/api/folder-items?path=${encodeURIComponent(path)}`);
  const d = await res.json();
  if (!res.ok) return alert(d.error || "Could not open this folder");
  if (!d.items.length) return alert("No photos or videos in this folder.");
  openSlideshow({ title, items: d.items });
}

function projectCard(p, current, onSelect, refresh) {
  const card = el("div", `project${p.dir === current ? " current" : ""}${p.exists ? "" : " missing"}`);
  card.title = p.dir;

  const head = el("div", "project-head");
  head.append(el("span", "folder-ic"), el("span", "project-name", p.name));
  const state = !p.exists ? ["Missing", "missing"] : p.sortedAt ? ["Sorted", "sorted"] : p.analyzed ? ["Tagged", "tagged"] : ["Scanned", ""];
  head.append(el("span", `project-state ${state[1]}`, state[0]));
  const forget = el("button", "project-x", "×");
  forget.title = "Remove from projects (files are not touched)";
  forget.onclick = async (e) => {
    e.stopPropagation();
    await fetch(`/api/projects?dir=${encodeURIComponent(p.dir)}`, { method: "DELETE" });
    refresh();
  };
  head.append(forget);
  card.append(head, el("div", "project-path", p.dir.replace(/^\/Users\/[^/]+/, "~")));

  const stats = el("div", "project-stats");
  const stat = (n, label) => {
    const s = el("span");
    s.append(el("b", null, n ?? "–"), ` ${label}`);
    return s;
  };
  stats.append(stat(p.photos, "photos"), stat(p.videos, "videos"), stat(p.unique ? `${p.analyzed}/${p.unique}` : 0, "tagged"));
  if (p.tags) stats.append(stat(p.tags, "tags"));
  card.append(stats);

  if (p.topTags?.length) {
    const top = el("div", "chips project-tags");
    for (const t of p.topTags) top.append(el("span", "chip", t));
    card.append(top);
  }

  if (p.sortedFolders.length) {
    const total = p.sortedFolders.reduce((a, f) => a + f.count, 0);
    const label = el("div", "project-sub");
    label.textContent = `Sorted ${p.sortedBy ? `by ${p.sortedBy} ` : ""}· ${p.sortedFolders.length} folders · ${total} files${p.sortedAt ? ` · ${ago(p.sortedAt)}` : ""}`;
    const chips = el("div", "chips");
    for (const f of p.sortedFolders.slice(0, 8)) {
      const chip = el("button", "chip folder-chip");
      chip.append(el("span", null, f.label), el("span", "chip-count", f.count));
      chip.title = `View ${f.label} as a slideshow (⌥-click opens it in Finder)`;
      chip.onclick = (e) => {
        e.stopPropagation();
        e.altKey ? openPath(f.path) : folderSlideshow(f.path, `${f.label} · ${p.name}`);
      };
      chips.append(chip);
    }
    if (p.sortedFolders.length > 8) {
      const more = el("button", "chip folder-chip", `+${p.sortedFolders.length - 8} more`);
      more.onclick = (e) => {
        e.stopPropagation();
        openPath(`${p.dir}/Sorted`);
      };
      chips.append(more);
    }
    card.append(label, chips);
  } else {
    card.append(el("div", "project-sub", p.lastUsed ? `Not sorted yet · used ${ago(p.lastUsed)}` : "Not sorted yet"));
  }

  const actions = el("div", "project-actions");
  const openBtn = el("button", "link-btn", "Open in Finder");
  openBtn.onclick = (e) => {
    e.stopPropagation();
    openPath(p.dir);
  };
  const tagsLink = el("a", "link-btn", "Tags →");
  tagsLink.href = "tags.html";
  tagsLink.onclick = (e) => {
    e.stopPropagation();
    try { localStorage.setItem("pics-manager-dir", p.dir); } catch {}
  };
  const playBtn = el("button", "link-btn", "▶ Slideshow");
  playBtn.title = "View every photo and video in this project";
  playBtn.onclick = (e) => {
    e.stopPropagation();
    folderSlideshow(p.dir, p.name);
  };
  actions.append(playBtn, openBtn, tagsLink);
  card.append(actions);

  if (p.exists) card.onclick = () => onSelect(p.dir);
  return card;
}

// Renders into `container`; returns a refresh function to call after changes.
export function mountProjects(container, onSelect) {
  async function refresh() {
    const d = await (await fetch("/api/projects")).json();
    const cards = d.projects.map((p) => projectCard(p, d.current, onSelect, refresh));
    if (!cards.length) cards.push(el("div", "project-empty", "Folders you choose show up here as projects."));
    container.replaceChildren(...cards);
  }
  refresh();
  return refresh;
}
