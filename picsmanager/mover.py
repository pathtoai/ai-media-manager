"""Moving files into folders, with an undo log. Nothing is ever deleted except
the extra hard links/copies this app itself created for multi-tag files."""

import json
import os
import shutil
from pathlib import Path

SORTED_DIRNAME = "Sorted"
UNDO_FILE = ".pics-manager-undo.json"


def unique_target(folder: Path, name: str) -> Path:
    target = folder / name
    stem, ext = os.path.splitext(name)
    n = 2
    while target.exists():
        target = folder / f"{stem} ({n}){ext}"
        n += 1
    return target


def safe_folder_name(tag: str) -> str:
    name = tag.replace("/", "-").replace(":", "-").strip().lstrip(".")
    return name or "untagged"


def append_undo(base: Path, entries: list[dict]) -> None:
    path = base / UNDO_FILE
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = []
    path.write_text(json.dumps(previous + entries, indent=1))


def move(src: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_target(folder, src.name)
    os.rename(src, target)
    return target


def link_or_copy(src: Path, folder: Path) -> Path:
    """A second entry for the same file: a hard link (no extra disk space) when
    possible, a copy otherwise (e.g. across volumes)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_target(folder, src.name)
    try:
        os.link(src, target)
    except OSError:
        shutil.copy2(src, target)
    return target


def undo(base: Path) -> dict:
    path = base / UNDO_FILE
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        raise FileNotFoundError("Nothing to undo")

    restored, failed = 0, []
    for e in reversed(entries):
        try:
            if "link" in e:
                Path(e["link"]).unlink(missing_ok=True)
                continue
            if os.path.exists(e["from"]):
                raise FileExistsError("a file already exists at the original path")
            os.rename(e["to"], e["from"])
            restored += 1
        except OSError as err:
            failed.append({**e, "error": str(err)})

    if failed:
        path.write_text(json.dumps([{k: v for k, v in f.items() if k != "error"} for f in reversed(failed)], indent=1))
    else:
        path.unlink(missing_ok=True)
    _prune_empty(base / SORTED_DIRNAME)
    return {"restored": restored, "failed": len(failed)}


def _prune_empty(folder: Path) -> None:
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_dir():
            _prune_empty(child)
    try:
        folder.rmdir()  # only succeeds when empty
    except OSError:
        pass
