"""Pics Manager server: scans a folder of photos/videos, analyzes and tags them
with the model picked in config.toml / the UI, lets you fix tags by hand, and
moves files into category or tag folders (with undo)."""

import asyncio
import hashlib
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import media, mover
from .analyzers import AnalyzerError, clean_tag, clean_tags, get_analyzer
from .config import ROOT, load
from .projects import ProjectStore
from .store import AnalysisStore

cfg = load()
store = AnalysisStore(media.CACHE / "analyses.json")
projects = ProjectStore(media.CACHE / "projects.json")
app = FastAPI(title="Pics Manager")

ID_RE = re.compile(r"^[a-f0-9]{40}$")


# ---------- scan state (one folder at a time, kept in memory) ----------

@dataclass
class Item:
    name: str
    kind: str
    path: Path
    id: str
    size: int
    mtime: float
    width: int | None = None
    height: int | None = None
    thumb: bool = False
    duplicate_of: str | None = None
    category: str | None = None

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "thumb": self.thumb, "category": self.category}


@dataclass
class ScanState:
    dir: Path | None = None
    items: list[Item] = field(default_factory=list)
    total_files: int = 0


state = ScanState()


def expand_dir(d: str | None) -> Path:
    return Path(d).expanduser().resolve() if d else cfg.sort_dir


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def event_stream(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def find_item(file_id: str) -> Item | None:
    return next((it for it in state.items if it.id == file_id), None)


# Files listed for a folder slideshow (they may belong to a project that isn't
# the current scan): id -> (path, kind).
folder_files: dict[str, tuple[Path, str]] = {}


def media_for(file_id: str) -> tuple[Path, str] | None:
    if it := find_item(file_id):
        return it.path, it.kind
    return folder_files.get(file_id)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_project(**fields) -> None:
    """Save the current folder's counts to the projects list."""
    if not state.dir:
        return
    unique = [it for it in state.items if not it.duplicate_of]
    counts: dict[str, int] = defaultdict(int)
    for it in unique:
        if a := store.get(it.id):
            for t in a["tags"]:
                counts[t] += 1
    projects.update(
        state.dir,
        files=state.total_files,
        tags=len(counts),
        topTags=sorted(counts, key=lambda t: (-counts[t], t))[:6],
        photos=sum(it.kind == "image" for it in state.items),
        videos=sum(it.kind == "video" for it in state.items),
        duplicates=len(state.items) - len(unique),
        analyzed=sum(store.get(it.id) is not None for it in unique),
        unique=len(unique),
        **fields,
    )


def model_or_400(model_id: str | None) -> str:
    model_id = model_id or cfg.default_model
    if model_id not in cfg.models:
        raise HTTPException(400, f"Unknown model {model_id!r}; pick one from config.toml")
    return model_id


# ---------- config ----------

@app.get("/api/config")
def get_config():
    models = []
    for m in cfg.models.values():
        ok, note = get_analyzer(m.id, cfg).status()
        models.append({"id": m.id, "label": m.label, "provider": m.provider, "available": ok, "note": note})
    return {
        "defaultDir": str(cfg.sort_dir),
        "defaultModel": cfg.default_model,
        "models": models,
        "categories": list(cfg.categories),
        "ai": any(m["available"] for m in models),
    }


class PickReq(BaseModel):
    current: str | None = None


# Shows the native macOS folder chooser over the browser (the frontmost app).
# The start folder is passed as an argument, never spliced into the script.
PICK_SCRIPT = """on run argv
  tell application (path to frontmost application as text)
    return POSIX path of (choose folder with prompt "Choose a folder of photos and videos" default location (POSIX file (item 1 of argv)))
  end tell
end run"""


@app.post("/api/pick-folder")
async def pick_folder(req: PickReq):
    start = expand_dir(req.current) if req.current else cfg.sort_dir
    if not start.is_dir():
        start = Path.home()
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", PICK_SCRIPT, str(start),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        if b"-128" in err:  # user pressed Cancel
            return {"cancelled": True}
        raise HTTPException(500, f"Could not open the folder picker: {err.decode().strip()}")
    return {"dir": out.decode().strip().rstrip("/") or "/"}


# ---------- scan ----------

def load_item(path: Path, kind: str, base: Path) -> Item | None:
    try:
        st = path.stat()
        # Files in Sorted/ are named by their relative path so names stay unique.
        item = Item(name=str(path.relative_to(base)), kind=kind, path=path, id=media.quick_hash(path, st.st_size), size=st.st_size, mtime=st.st_mtime)
        if kind == "image":
            item.width, item.height = media.image_size(path)
        item.thumb = media.make_thumb(item.id, path, kind)
        return item
    except OSError:
        return None  # unreadable file: skip


def list_files(d: Path) -> list[Path]:
    """Files directly in d, plus files already moved into d/Sorted/ so a sorted
    folder still shows everything. Hard links (multi-tag copies) count once."""
    files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    for root, dirs, names in os.walk(d / mover.SORTED_DIRNAME):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        files += [Path(root) / n for n in sorted(names) if not n.startswith(".")]
    seen, out = set(), []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        if (st.st_dev, st.st_ino) not in seen:
            seen.add((st.st_dev, st.st_ino))
            out.append(p)
    return out


def mark_duplicates(items: list[Item]) -> None:
    """Group by quick hash, confirm with a full hash; keep the oldest copy."""
    groups: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        groups[it.id].append(it)
    for group in groups.values():
        if len(group) < 2:
            continue
        by_full: dict[str, list[Item]] = defaultdict(list)
        for it in group:
            by_full[media.full_hash(it.path)].append(it)
        for same in by_full.values():
            same.sort(key=lambda x: x.mtime)
            for dup in same[1:]:
                dup.duplicate_of = same[0].name


@app.get("/api/scan")
async def scan(dir: str | None = None):
    async def gen():
        global state
        d = expand_dir(dir)
        try:
            files = list_files(d)
        except OSError as e:
            yield sse("error", {"message": str(e)})
            return
        found = [(p, k) for p in files if (k := media.kind_of(p))]
        yield sse("start", {"dir": str(d), "totalFiles": len(files), "media": len(found)})

        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(8)
        items: list[Item] = []
        try:
            for fut in asyncio.as_completed([loop.run_in_executor(pool, load_item, p, k, d) for p, k in found]):
                if item := await fut:
                    items.append(item)
                    yield sse("item", item.public())
            await loop.run_in_executor(pool, mark_duplicates, items)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        state = ScanState(d, items, len(files))
        dupes = [it.name for it in items if it.duplicate_of]
        record_project(scanned=True)
        yield sse("done", {
            "totalFiles": len(files),
            "photos": sum(it.kind == "image" for it in items),
            "videos": sum(it.kind == "video" for it in items),
            "duplicates": len(dupes),
            "dupeIds": dupes,
            "alreadySorted": sum(it.path.parent != d for it in items),
            # Every file has a saved result, so sorting needs no model calls.
            "allAnalyzed": all(store.get(it.id) is not None for it in items if not it.duplicate_of),
            "canUndo": (d / mover.UNDO_FILE).exists(),
        })

    return event_stream(gen())


# ---------- analysis ----------

def heuristic_category(item: Item) -> str:
    n = item.path.name.lower()
    if re.search(r"screen ?shot|screenshot|снимок экрана|скриншот|screen ?recording|запись экрана|simulator", n):
        return "Screenshots"
    if item.kind == "image" and item.path.suffix.lower() == ".png" and item.width and item.height:
        r = max(item.width, item.height) / min(item.width, item.height)
        # Typical phone (19.5:9 ≈ 2.16) and laptop (16:10) screen ratios.
        if abs(r - 2.16) < 0.06 or abs(r - 1.6) < 0.02:
            return "Screenshots"
    return "Other"


def analyze_media(model_id: str, path: Path, kind: str, name: str | None, file_id: str, force: bool, size=(None, None)) -> dict:
    if not force and (cached := store.get(file_id)):
        return {**cached, "cached": True}
    img = media.load_media(path, kind, cfg.max_edge)
    result = get_analyzer(model_id, cfg).analyze(media.jpeg_bytes(img), name, kind).model_dump()
    result["tags"] = clean_tags(result["tags"])
    w, h = size
    result |= {
        "width": w or img.width,
        "height": h or img.height,
        "dominantColor": media.dominant_color(img),
        "model": model_id,
        "analyzedAt": now_iso(),
    }
    return {**store.put_analysis(file_id, result), "cached": False}


def result_event(it: Item, a: dict | None, error: str | None = None) -> dict:
    out = {"name": it.name, "id": it.id, "category": it.category}
    if a:
        out |= {"tags": a["tags"], "description": a["description"], "model": a["model"], "cached": a.get("cached", True)}
    if error:
        out["error"] = error
    return out


async def analysis_events(model_id: str, force: bool):
    """Analyze every scanned file (once per unique id), streaming one "result"
    event per file. Falls back to cached results / filename rules when the
    model is unavailable or a file fails."""
    if not state.dir:
        yield sse("error", {"message": "Scan a folder first"})
        return
    analyzer = get_analyzer(model_id, cfg)
    ok, note = analyzer.status()
    groups: dict[str, list[Item]] = defaultdict(list)
    for it in state.items:
        groups[it.id].append(it)
    yield sse("progress", {"total": len(state.items), "unique": len(groups), "model": model_id, "ai": ok, "note": note})

    if not ok:
        for it in state.items:
            a = store.get(it.id)
            it.category = a["category"] if a else heuristic_category(it)
            yield sse("result", result_event(it, a))
        yield sse("done", {"ai": False, "note": note, "failed": 0, "failedBatches": 0})
        return

    def work(group: list[Item]):
        first = next((it for it in group if not it.duplicate_of), group[0])
        try:
            size = (first.width, first.height)
            return group, analyze_media(model_id, first.path, first.kind, first.path.name, first.id, force, size), None
        except (AnalyzerError, OSError, RuntimeError) as e:
            return group, None, str(e)
        except Exception as e:  # keep going on unexpected per-file errors
            return group, None, f"{type(e).__name__}: {e}"

    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(cfg.models[model_id].concurrency)
    failures, last_error = 0, None
    try:
        for fut in asyncio.as_completed([loop.run_in_executor(pool, work, g) for g in groups.values()]):
            group, a, err = await fut
            if err:
                failures += 1
                last_error = err
            for it in group:
                it.category = a["category"] if a else heuristic_category(it)
                yield sse("result", result_event(it, a, err))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    record_project(lastModel=model_id)
    yield sse("done", {"ai": True, "model": model_id, "failed": failures, "failedBatches": failures, "lastError": last_error})


@app.get("/api/classify")
async def classify(model: str | None = None):
    return event_stream(analysis_events(model_or_400(model), force=False))


@app.get("/api/tag")
async def tag_all(model: str | None = None, force: bool = False):
    return event_stream(analysis_events(model_or_400(model), force=force))


@app.post("/api/analyze")
async def analyze_one(request: Request, model: str | None = None, force: bool = False):
    """Analyze one image: send its bytes (Content-Type: image/*) or JSON {"path": ...}."""
    model_id = model_or_400(model)
    ok, note = get_analyzer(model_id, cfg).status()
    if not ok:
        raise HTTPException(503, note)
    ctype = request.headers.get("content-type", "")
    try:
        if ctype.startswith("image/"):
            data = await request.body()
            if not data:
                raise HTTPException(400, "Empty image body")
            file_id = hashlib.sha1(data).hexdigest()

            def run():
                if not force and (cached := store.get(file_id)):
                    return {"id": file_id, **cached, "cached": True}
                # Uploads have no path on disk: decode in memory, then reuse the pipeline.
                img = media.open_image_bytes(data)
                tmp = media.CACHE / f"upload-{file_id}.jpg"
                img.save(tmp, "JPEG", quality=92)
                try:
                    return {"id": file_id, **analyze_media(model_id, tmp, "image", request.headers.get("x-filename"), file_id, force, img.size)}
                finally:
                    tmp.unlink(missing_ok=True)

            return await asyncio.to_thread(run)

        body = await request.json()
        path = expand_dir(body.get("path"))
        kind = media.kind_of(path)
        if not body.get("path") or not kind:
            raise HTTPException(400, "Send image bytes (Content-Type: image/*) or JSON {\"path\": \"/file.jpg\"}")
        if not path.is_file():
            raise HTTPException(404, f"No such file: {path}")
        file_id = media.quick_hash(path, path.stat().st_size)
        size = media.image_size(path) if kind == "image" else (None, None)
        result = await asyncio.to_thread(analyze_media, model_id, path, kind, path.name, file_id, force, size)
        return {"id": file_id, **result}
    except AnalyzerError as e:
        raise HTTPException(502, str(e))
    except (OSError, RuntimeError) as e:
        raise HTTPException(422, f"Could not read image: {e}")


# ---------- tags ----------

@app.get("/api/tags")
def list_tags():
    """Every unique file in the scan with its (effective) analysis, plus tag counts."""
    counts: dict[str, int] = defaultdict(int)
    files = []
    for it in state.items:
        if it.duplicate_of:
            continue
        a = store.get(it.id)
        if a:
            for t in a["tags"]:
                counts[t] += 1
        files.append({
            **it.public(),
            "analyzed": bool(a),
            "category": a["category"] if a else it.category,
            "tags": a["tags"] if a else [],
            "description": a["description"] if a else "",
            "model": a["model"] if a else None,
            "edited": a["edited"] if a else False,
        })
    tags = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "dir": str(state.dir) if state.dir else None,
        "tags": [{"tag": t, "count": c} for t, c in tags],
        "files": files,
        "canUndo": bool(state.dir and (state.dir / mover.UNDO_FILE).exists()),
    }


@app.get("/api/items/{file_id}")
def get_item(file_id: str):
    it = find_item(file_id)
    a = store.get(file_id)
    if not it and not a:
        raise HTTPException(404, "Unknown file")
    return {**(it.public() if it else {"id": file_id}), "analysis": a}


class TagEdit(BaseModel):
    add: list[str] = []
    remove: list[str] = []
    category: str | None = None


@app.patch("/api/items/{file_id}")
def edit_item(file_id: str, edit: TagEdit):
    if edit.category is not None and edit.category not in cfg.categories:
        raise HTTPException(400, f"Unknown category {edit.category!r}")
    try:
        a = store.edit(file_id, edit.add, edit.remove, edit.category)
    except KeyError:
        raise HTTPException(404, "Analyze this file before editing its tags")
    for it in state.items:
        if it.id == file_id:
            it.category = a["category"]
    record_project()
    return a


# ---------- moving files ----------

@app.post("/api/apply")
def apply_categories():
    """Move files into <dir>/Sorted/{Photos,Videos}/<category>/ and duplicates
    into <dir>/Sorted/Duplicates/."""
    if not state.dir:
        raise HTTPException(400, "Scan a folder first")
    if any(not it.category and not it.duplicate_of for it in state.items):
        raise HTTPException(400, "Sort before moving files")
    base = state.dir / mover.SORTED_DIRNAME
    log, folders = [], set()
    for it in state.items:
        folder = base / "Duplicates" if it.duplicate_of else base / ("Videos" if it.kind == "video" else "Photos") / it.category
        if it.path.parent == folder:
            continue  # already there from an earlier sort
        try:
            target = mover.move(it.path, folder)
        except OSError as e:
            print(f"move failed {it.path}: {e}")
            continue
        log.append({"from": str(it.path), "to": str(target)})
        it.path = target
        folders.add(folder)
    mover.append_undo(state.dir, log)
    record_project(sortedAt=now_iso(), sortedBy="category", lastMoved=len(log))
    return {"moved": len(log), "folders": len(folders), "base": str(base)}


class TagFolders(BaseModel):
    tags: list[str]


@app.post("/api/apply-tags")
def apply_tag_folders(req: TagFolders):
    """Make <dir>/Sorted/Tags/<tag>/ for each chosen tag. A file goes to its
    first (most relevant) chosen tag; every other chosen tag it has gets a hard
    link (a copy across volumes), so it shows up in all of its folders."""
    if not state.dir:
        raise HTTPException(400, "Scan a folder first")
    chosen = {clean_tag(t) for t in req.tags} - {""}
    if not chosen:
        raise HTTPException(400, "Pick at least one tag")
    base = state.dir / mover.SORTED_DIRNAME / "Tags"
    log, folders, moved, linked, errors = [], set(), 0, 0, []
    for it in state.items:
        a = store.get(it.id)
        if it.duplicate_of or not a:
            continue
        targets: list[Path] = []
        for t in a["tags"]:
            folder = base / mover.safe_folder_name(t)
            if t in chosen and folder not in targets:
                targets.append(folder)
        if not targets or it.path.parent == targets[0]:
            continue  # untagged, or already moved there earlier
        try:
            new_path = mover.move(it.path, targets[0])
            log.append({"from": str(it.path), "to": str(new_path)})
            it.path = new_path
            moved += 1
            for folder in targets[1:]:
                log.append({"link": str(mover.link_or_copy(new_path, folder))})
                linked += 1
            folders.update(targets)
        except OSError as e:
            errors.append(f"{it.name}: {e}")
    mover.append_undo(state.dir, log)
    record_project(sortedAt=now_iso(), sortedBy="tags", lastMoved=moved)
    return {"moved": moved, "extraCopies": linked, "folders": len(folders), "base": str(base), "errors": errors[:20]}


class UndoReq(BaseModel):
    dir: str | None = None


@app.post("/api/undo")
def undo(req: UndoReq | None = None):
    d = expand_dir(req.dir) if req and req.dir else state.dir
    if not d:
        raise HTTPException(400, "Nothing to undo")
    try:
        result = mover.undo(d)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    if state.dir == d:
        state.items = []  # paths changed; the UI rescans
    if not (d / mover.UNDO_FILE).exists():
        projects.update(d, sortedAt=None, sortedBy=None, lastMoved=0)
    return result


# ---------- projects ----------

@app.get("/api/projects")
def list_projects():
    return {"projects": projects.list(), "current": str(state.dir) if state.dir else None}


@app.delete("/api/projects")
def forget_project(dir: str):
    """Removes a folder from the projects list only; nothing on disk changes."""
    if not projects.remove(dir):
        raise HTTPException(404, "Not a project")
    return {"ok": True}


class OpenReq(BaseModel):
    path: str


@app.post("/api/open")
async def open_in_finder(req: OpenReq):
    path = Path(req.path).expanduser().resolve()
    if not projects.contains(path) or not path.exists():
        raise HTTPException(404, "Only project folders can be opened")
    proc = await asyncio.create_subprocess_exec("open", str(path))
    await proc.wait()
    return {"ok": proc.returncode == 0}


@app.get("/api/folder-items")
async def folder_items(path: str):
    """Photos and videos in a project folder (and its subfolders), for a slideshow."""
    folder = Path(path).expanduser().resolve()
    if not projects.contains(folder) or not folder.is_dir():
        raise HTTPException(404, "Only project folders can be previewed")

    def collect() -> list[dict]:
        out = []
        for root, dirs, names in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(names):
                p = Path(root) / n
                if n.startswith(".") or not (kind := media.kind_of(p)):
                    continue
                try:
                    file_id = media.quick_hash(p, p.stat().st_size)
                except OSError:
                    continue
                folder_files[file_id] = (p, kind)
                a = store.get(file_id)
                out.append({
                    "id": file_id,
                    "name": str(p.relative_to(folder)),
                    "kind": kind,
                    "tags": a["tags"] if a else [],
                    "description": a["description"] if a else "",
                })
        return out

    return {"dir": str(folder), "name": folder.name, "items": await asyncio.to_thread(collect)}


# ---------- images ----------

@app.get("/thumb/{file_id}.jpg")
def thumb(file_id: str):
    path = media.THUMBS / f"{file_id}.jpg"
    if not ID_RE.match(file_id) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/preview/{file_id}.jpg")
async def preview(file_id: str):
    if not ID_RE.match(file_id):
        raise HTTPException(404)
    path = media.PREVIEWS / f"{file_id}.jpg"
    if not path.exists():
        found = media_for(file_id)
        if not found:
            raise HTTPException(404)
        path = await asyncio.to_thread(media.make_preview, file_id, *found)
        if not path:
            raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/media/{file_id}")
def original(file_id: str):
    """The original file (supports range requests, so videos can seek)."""
    found = media_for(file_id) if ID_RE.match(file_id) else None
    if not found or not found[0].exists():
        raise HTTPException(404)
    return FileResponse(found[0])


@app.exception_handler(HTTPException)
async def http_error(_req, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


class PageFiles(StaticFiles):
    """The UI's own files: always revalidate, so a reload picks up new code."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", PageFiles(directory=ROOT / "public", html=True), name="public")
