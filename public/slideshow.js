// Full-screen slideshow: ← / → to move, Space to play/pause, Esc to close.
// items: [{ id, name, kind, tags?, description? }]

const SLIDE_MS = 3500;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

let ui = null;

function build() {
  const root = el("div", "ss hidden");
  root.innerHTML = `
    <div class="ss-top">
      <div class="ss-title"></div>
      <div class="ss-count"></div>
      <button class="ss-btn ss-play" title="Play / pause (Space)">▶ Play</button>
      <button class="ss-btn ss-close" title="Close (Esc)">✕</button>
    </div>
    <div class="ss-stage">
      <button class="ss-nav ss-prev" title="Previous (←)">‹</button>
      <div class="ss-media"></div>
      <button class="ss-nav ss-next" title="Next (→)">›</button>
    </div>
    <div class="ss-info">
      <div class="ss-name"></div>
      <div class="ss-desc"></div>
      <div class="ss-tags"></div>
    </div>
    <div class="ss-strip"></div>
    <div class="ss-bar"><span></span></div>`;
  document.body.append(root);
  const q = (s) => root.querySelector(s);
  ui = {
    root, title: q(".ss-title"), count: q(".ss-count"), play: q(".ss-play"), media: q(".ss-media"),
    name: q(".ss-name"), desc: q(".ss-desc"), tags: q(".ss-tags"), strip: q(".ss-strip"), bar: q(".ss-bar span"),
    items: [], i: 0, playing: false, timer: null, onTag: null,
  };
  q(".ss-prev").onclick = () => go(-1);
  q(".ss-next").onclick = () => go(1);
  q(".ss-close").onclick = close;
  ui.play.onclick = () => setPlaying(!ui.playing);
  // Click the dark background (not the photo) to close.
  q(".ss-stage").onclick = (e) => { if (e.target.classList.contains("ss-stage")) close(); };
  document.addEventListener("keydown", (e) => {
    if (ui.root.classList.contains("hidden")) return;
    if (e.key === "ArrowLeft") go(-1);
    else if (e.key === "ArrowRight") go(1);
    else if (e.key === "Escape") close();
    else if (e.key === " ") setPlaying(!ui.playing);
    else return;
    e.preventDefault();
    e.stopPropagation();
  }, true);
  // Swipe on touch screens.
  let x0 = null;
  root.addEventListener("touchstart", (e) => (x0 = e.touches[0].clientX), { passive: true });
  root.addEventListener("touchend", (e) => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
    x0 = null;
  });
}

function preload(i) {
  const it = ui.items[(i + ui.items.length) % ui.items.length];
  if (it) new Image().src = `/preview/${it.id}.jpg`;
}

function show() {
  const it = ui.items[ui.i];
  clearTimeout(ui.timer);
  ui.count.textContent = `${ui.i + 1} / ${ui.items.length}`;
  ui.name.textContent = it.name;
  ui.desc.textContent = it.description || "";

  let node;
  if (it.kind === "video") {
    node = document.createElement("video");
    node.src = `/media/${it.id}`;
    node.poster = `/preview/${it.id}.jpg`;
    node.controls = true;
    node.autoplay = true;
    node.muted = ui.playing; // autoplay with sound is blocked by browsers
    node.playsInline = true;
    node.onended = () => ui.playing && go(1);
  } else {
    node = document.createElement("img");
    node.src = `/preview/${it.id}.jpg`;
    node.alt = it.name;
  }
  ui.media.replaceChildren(node);

  ui.tags.replaceChildren(...(it.tags || []).map((t) => {
    const chip = el(ui.onTag ? "button" : "span", "ss-tag", t);
    if (ui.onTag) {
      chip.title = `Slideshow of “${t}”`;
      chip.onclick = () => ui.onTag(t);
    }
    return chip;
  }));

  [...ui.strip.children].forEach((c, j) => c.classList.toggle("on", j === ui.i));
  ui.strip.children[ui.i]?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  preload(ui.i + 1);
  preload(ui.i - 1);
  restartTimer();
}

function restartTimer() {
  clearTimeout(ui.timer);
  ui.bar.style.transition = "none";
  ui.bar.style.width = "0";
  const it = ui.items[ui.i];
  if (!ui.playing || it.kind === "video") return; // videos advance when they end
  void ui.bar.offsetWidth;
  ui.bar.style.transition = `width ${SLIDE_MS}ms linear`;
  ui.bar.style.width = "100%";
  ui.timer = setTimeout(() => go(1), SLIDE_MS);
}

function setPlaying(on) {
  ui.playing = on;
  ui.play.textContent = on ? "❚❚ Pause" : "▶ Play";
  const v = ui.media.querySelector("video");
  if (v && on) v.play().catch(() => {});
  restartTimer();
}

function go(step) {
  if (!ui.items.length) return;
  ui.i = (ui.i + step + ui.items.length) % ui.items.length;
  show();
}

function close() {
  clearTimeout(ui.timer);
  ui.media.replaceChildren(); // stops a playing video
  ui.root.classList.add("hidden");
  document.body.style.overflow = "";
}

// onTag(tag): optional — clicking a tag chip calls it (e.g. to switch the show to that tag).
export function openSlideshow({ title, items, start = 0, onTag = null }) {
  if (!items.length) return;
  if (!ui) build();
  ui.items = items;
  ui.i = Math.max(0, Math.min(start, items.length - 1));
  ui.onTag = onTag;
  ui.title.textContent = title;
  ui.strip.replaceChildren(...items.map((it, j) => {
    const t = el("button", "ss-thumb");
    t.style.backgroundImage = `url(/thumb/${it.id}.jpg)`;
    if (it.kind === "video") t.classList.add("video");
    t.title = it.name;
    t.onclick = () => { ui.i = j; show(); };
    return t;
  }));
  ui.root.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  setPlaying(false);
  show();
}
