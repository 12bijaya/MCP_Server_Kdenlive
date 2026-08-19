"""Thumbnail / contact-sheet generation via ffmpeg."""

from __future__ import annotations

import hashlib
from pathlib import Path

from kdenlive_mcp.config import get_config
from kdenlive_mcp.media.ffmpeg.runner import run


def _cache_key(path: Path, timestamp: float, width: int) -> str:
    h = hashlib.sha1(f"{path}|{timestamp}|{width}|{path.stat().st_mtime_ns}".encode()).hexdigest()
    return h[:24]


def generate_thumbnail(path: Path, *, timestamp: float = 0.0, width: int = 320) -> Path:
    """Extract a single JPEG frame, cached by (path, timestamp, width, mtime)."""
    cfg = get_config()
    thumb_dir = cfg.cache_dir / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    out_path = thumb_dir / f"{_cache_key(path, timestamp, width)}.jpg"
    if out_path.exists():
        return out_path

    run("ffmpeg", [
        "-y",
        "-ss", str(max(timestamp, 0.0)),
        "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "3",
        str(out_path),
    ])
    return out_path


def generate_contact_sheet(path: Path, *, duration: float, columns: int = 4, rows: int = 4, tile_width: int = 240) -> Path:
    """Generate a grid contact sheet covering the clip evenly."""
    cfg = get_config()
    sheet_dir = cfg.cache_dir / "contact_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    count = columns * rows
    key = _cache_key(path, duration, tile_width * 1000 + count)
    out_path = sheet_dir / f"{key}.jpg"
    if out_path.exists():
        return out_path

    interval = max(duration / max(count, 1), 0.1)
    run("ffmpeg", [
        "-y",
        "-i", str(path),
        "-vf",
        f"select='not(mod(t,{interval:.3f}))',scale={tile_width}:-2,tile={columns}x{rows}",
        "-frames:v", "1",
        "-vsync", "vfr",
        str(out_path),
    ])
    return out_path
