"""Projects: every folder the user has scanned, with its latest stats, kept in
.cache/projects.json. Sorted/ subfolders are read live from disk."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .mover import SORTED_DIRNAME, UNDO_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data: dict[str, dict] = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, self.path)

    def update(self, folder: Path, **fields) -> None:
        with self.lock:
            key = str(folder)
            p = self.data.setdefault(key, {"dir": key, "name": folder.name or key, "addedAt": _now()})
            p.update(fields, lastUsed=_now())
            self._save()

    def remove(self, folder: str) -> bool:
        with self.lock:
            found = self.data.pop(folder, None) is not None
            if found:
                self._save()
            return found

    def contains(self, path: Path) -> bool:
        """True if path is a project folder or inside one."""
        with self.lock:
            roots = [Path(d) for d in self.data]
        return any(path == r or r in path.parents for r in roots)

    def list(self) -> list[dict]:
        with self.lock:
            projects = [dict(p) for p in self.data.values()]
        for p in projects:
            folder = Path(p["dir"])
            p["exists"] = folder.is_dir()
            p["canUndo"] = (folder / UNDO_FILE).exists()
            p["sortedFolders"] = sorted_folders(folder)
        return sorted(projects, key=lambda p: p.get("lastUsed", ""), reverse=True)


def sorted_folders(folder: Path) -> list[dict]:
    """Leaf folders under <folder>/Sorted with how many files each holds."""
    base = folder / SORTED_DIRNAME
    if not base.is_dir():
        return []
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        shown = [f for f in files if not f.startswith(".")]
        if shown and not dirs:
            out.append({"path": root, "label": os.path.relpath(root, base), "count": len(shown)})
    return sorted(out, key=lambda f: -f["count"])
