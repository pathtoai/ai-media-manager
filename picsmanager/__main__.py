import sys

import uvicorn

from .analyzers import get_analyzer
from .app import app, cfg


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    print(f"Pics Manager → http://localhost:{cfg.port}")
    print(f"Folder: {cfg.sort_dir}")
    for m in cfg.models.values():
        ok, note = get_analyzer(m.id, cfg).status()
        mark = "*" if m.id == cfg.default_model else " "
        print(f" {mark} {m.label:<45} {'ready' if ok else note}")
    uvicorn.run(app, host="127.0.0.1", port=cfg.port, log_level="warning")


main()
