# AI Media Manager

A local web app that analyzes and tags a folder of photos and videos (your Downloads by default), lets you fix tags by hand, and sorts files into category or tag folders — with undo.

## Run

```bash
python3 -m venv .venv          # or: uv venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m mediamanager
```

Put your Claude key in `.env` in the project folder (`ANTHROPIC_API_KEY=sk-ant-...`). It is git-ignored.

Open http://localhost:8769. Needs Python 3.11+ and `ffmpeg` on your PATH for videos (`brew install ffmpeg`).

## Models — `config.toml`

Every model in the UI's model picker comes from the `[[models]]` list in [config.toml](config.toml), and `default_model` picks the starting one.

| Model | Provider | Notes |
|---|---|---|
| `claude-opus-5-5` | Claude API | Best quality (default) |
| `claude-sonnet-5-5` | Claude API | Balanced |
| `claude-haiku-5-5` | Claude API | Cheapest and fastest — roughly $0.30 per 1,000 photos |
| `qwen2.5vl:7b` | Ollama (local) | Free and private; slower on a laptop |

To use Qwen2.5-VL locally:

```bash
brew install ollama        # or download from ollama.com
ollama serve               # leave running
ollama pull qwen2.5vl:7b   # ~6 GB
```

Any other Ollama vision model works too — add a `[[models]]` entry with `provider = "ollama"`. `max_edge` sets the image size sent to the model (smaller is cheaper and faster). `PORT`, `SORT_DIR` and `PICS_MODEL` env vars override the config.

## Pages

**Sort** (`/`) — the animated "messy pile → folders" view. *Sort* analyzes every file with the selected model and files it under a category; *Move files* moves them into `Sorted/Photos/<category>/`, `Sorted/Videos/<category>/` and `Sorted/Duplicates/`.

**Tags** (`/tags.html`)
- *Tag photos* analyzes files that haven't been analyzed yet; *Re-analyze all* runs everything again with the selected model.
- Click a tag in the sidebar to see every image with it. Hover a card and press **× Not "tag"** to remove a wrong tag in one click.
- Click an image to open a large preview where you can remove tags (×), add tags (type and press Enter), and change its category. ← / → step through the current tag.
- Manual edits are kept separately from the model's tags, so re-analyzing never undoes them.
- Tick the tags you want as folders (or use *Tick tags with ≥ N files*), then **Move into tag folders**. Each file moves to `Sorted/Tags/<tag>/` for its most relevant ticked tag; if it has several ticked tags it also appears in the others as a hard link (no extra disk space; a copy if on another volume). Files without a ticked tag stay put.

Nothing is ever deleted. Every move is logged in `<folder>/.pics-manager-undo.json`; *Undo move* puts everything back, removes the extra links and the empty `Sorted/` folders.

Analyses are cached by file content in `.cache/analyses.json`, so each file is only sent to a model once.

## API

| Endpoint | |
|---|---|
| `GET /api/config` | Models (with availability), categories, default folder |
| `GET /api/scan?dir=` | Scan a folder (server-sent events) |
| `GET /api/tag?model=&force=true` | Analyze all scanned files (server-sent events) |
| `GET /api/tags` | Every file with its tags, category and description |
| `PATCH /api/items/{id}` | `{"add": [...], "remove": [...], "category": "..."}` |
| `POST /api/analyze?model=` | One image: raw bytes with `Content-Type: image/*`, or `{"path": "..."}` |
| `POST /api/apply` | Move into category folders |
| `POST /api/apply-tags` | `{"tags": [...]}` — move into tag folders |
| `POST /api/undo` | Undo all moves in the folder |

```bash
curl -X POST 'localhost:8769/api/analyze?model=claude-haiku-5-5' \
  -H 'Content-Type: image/jpeg' -H 'X-Filename: beach.jpg' --data-binary @beach.jpg
```
