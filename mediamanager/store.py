"""Persistent analyses, keyed by file id, in .cache/analyses.json.

The model's tags are kept apart from manual edits ("added" / "removed" /
"category_override"), so re-analyzing a file never undoes a correction."""

import json
import os
import threading
from pathlib import Path

from .analyzers import clean_tag


class AnalysisStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data: dict[str, dict] = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        os.replace(tmp, self.path)

    @staticmethod
    def _effective(raw: dict) -> dict:
        removed = set(raw.get("removed", []))
        tags = [t for t in raw["ai_tags"] if t not in removed]
        tags += [t for t in raw.get("added", []) if t not in tags]
        out = {k: v for k, v in raw.items() if k not in ("added", "removed", "category_override")}
        out["tags"] = tags
        out["category"] = raw.get("category_override") or raw["category"]
        out["edited"] = bool(raw.get("added") or raw.get("removed") or raw.get("category_override"))
        return out

    def get(self, file_id: str) -> dict | None:
        with self.lock:
            raw = self.data.get(file_id)
            return self._effective(raw) if raw else None

    def put_analysis(self, file_id: str, analysis: dict) -> dict:
        """Store a fresh model result, keeping any manual edits on top of it."""
        with self.lock:
            old = self.data.get(file_id, {})
            raw = {k: v for k, v in analysis.items() if k != "tags"}
            raw["ai_tags"] = analysis["tags"]
            for k in ("added", "removed", "category_override"):
                if old.get(k):
                    raw[k] = old[k]
            self.data[file_id] = raw
            self._save()
            return self._effective(raw)

    def edit(self, file_id: str, add: list[str], remove: list[str], category: str | None) -> dict:
        with self.lock:
            raw = self.data.get(file_id)
            if raw is None:
                raise KeyError(file_id)
            added = list(raw.get("added", []))
            removed = set(raw.get("removed", []))
            for t in filter(None, map(clean_tag, remove)):
                if t in added:
                    added.remove(t)
                if t in raw["ai_tags"]:
                    removed.add(t)
            for t in filter(None, map(clean_tag, add)):
                removed.discard(t)
                if t not in raw["ai_tags"] and t not in added:
                    added.append(t)
            raw["added"] = added
            raw["removed"] = sorted(removed)
            if category is not None:
                raw["category_override"] = None if category == raw["category"] else category
            self._save()
            return self._effective(raw)
