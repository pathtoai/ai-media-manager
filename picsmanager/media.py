"""File-level helpers: hashing, decoding photos/video frames, thumbnails, previews."""

import hashlib
import io
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import ROOT

CACHE = ROOT / ".cache"
THUMBS = CACHE / "thumbs"
PREVIEWS = CACHE / "previews"
THUMBS.mkdir(parents=True, exist_ok=True)
PREVIEWS.mkdir(parents=True, exist_ok=True)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif", ".tif", ".tiff", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}

THUMB_SIZE = 320
PREVIEW_EDGE = 1200
CHUNK = 64 * 1024


def kind_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None


def quick_hash(path: Path, size: int) -> str:
    """Fast fingerprint: size + first/last 64KB. Used as the file id and cache key,
    and as the first pass of duplicate detection (confirmed with full_hash)."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > CHUNK * 2:
            f.seek(size - CHUNK)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def full_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


# ---------- decoding ----------

def _flatten(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def open_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as img:
            return _flatten(img)
    except (UnidentifiedImageError, OSError):
        # HEIC etc. that Pillow can't read: let macOS `sips` convert it first.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "converted.jpg"
            subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(out)], check=True, capture_output=True)
            with Image.open(out) as img:
                return _flatten(img)


def open_image_bytes(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return _flatten(img)
    except (UnidentifiedImageError, OSError):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "upload"
            src.write_bytes(data)
            return open_image(src)


def video_frame(path: Path, max_edge: int) -> Image.Image:
    """A frame ~1s in; very short clips fall back to the first frame."""
    scale = f"scale={max_edge}:{max_edge}:force_original_aspect_ratio=decrease"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "frame.jpg"
        for ss in ("1", "0"):
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", ss, "-i", str(path), "-frames:v", "1", "-vf", scale, str(out)],
                capture_output=True,
            )
            if out.exists():
                with Image.open(out) as img:
                    return img.convert("RGB")
    raise RuntimeError(f"could not read a frame from {path.name} (is ffmpeg installed?)")


def load_media(path: Path, kind: str, max_edge: int) -> Image.Image:
    """Decoded, upright RGB image (or video frame) no larger than max_edge."""
    if kind == "video":
        return video_frame(path, max_edge)
    img = open_image(path)
    img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return img


def jpeg_bytes(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def image_size(path: Path) -> tuple[int | None, int | None]:
    """Displayed width/height without decoding pixels (EXIF rotations swap them)."""
    try:
        with Image.open(path) as img:
            w, h = img.size
            return (h, w) if img.getexif().get(0x0112, 1) >= 5 else (w, h)
    except (UnidentifiedImageError, OSError):
        return None, None


def dominant_color(img: Image.Image) -> str:
    small = img.copy()
    small.thumbnail((64, 64))
    q = small.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    _, idx = max(q.getcolors())
    r, g, b = q.getpalette()[idx * 3 : idx * 3 + 3]
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------- thumbnails & previews ----------

# Identical files share an id, so share one lock per id while rendering.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _render_cached(final: Path, key: str, render) -> bool:
    if final.exists():
        return True
    with _lock_for(key):
        if final.exists():
            return True
        # Write to a temp file and rename, so a half-written image is never served.
        part = final.with_suffix(".part.jpg")
        try:
            render().save(part, "JPEG", quality=80)
            part.replace(final)
            return True
        except Exception:
            part.unlink(missing_ok=True)
            return False


def make_thumb(file_id: str, path: Path, kind: str) -> bool:
    def render():
        img = load_media(path, kind, THUMB_SIZE * 2)
        return ImageOps.fit(img, (THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)

    return _render_cached(THUMBS / f"{file_id}.jpg", f"t{file_id}", render)


def make_preview(file_id: str, path: Path, kind: str) -> Path | None:
    final = PREVIEWS / f"{file_id}.jpg"
    ok = _render_cached(final, f"p{file_id}", lambda: load_media(path, kind, PREVIEW_EDGE))
    return final if ok else None
