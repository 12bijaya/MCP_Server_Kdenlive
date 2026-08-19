"""Turns a raw file path into an indexed MediaAsset via ffprobe."""

from __future__ import annotations

from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaAsset, make_asset_id
from kdenlive_mcp.media.ffprobe.probe import probe_media
from kdenlive_mcp.media.thumbnails.generator import generate_thumbnail

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


def analyze_media_file(path: Path, *, generate_thumb: bool = True) -> MediaAsset:
    ext = path.suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        meta = probe_media(path)
        asset = MediaAsset(
            id=make_asset_id(path),
            path=str(path.resolve()),
            kind="image",
            width=meta.width,
            height=meta.height,
            orientation=meta.orientation,
            size_bytes=meta.size_bytes or path.stat().st_size,
        )
    else:
        meta = probe_media(path)
        kind = meta.kind if meta.kind != "unknown" else ("audio" if ext in AUDIO_EXTENSIONS else "video")
        asset = MediaAsset(
            id=make_asset_id(path),
            path=str(path.resolve()),
            kind=kind,
            duration=meta.duration,
            width=meta.width,
            height=meta.height,
            fps=meta.fps,
            orientation=meta.orientation,
            video_codec=meta.primary_video.codec_name if meta.primary_video else None,
            audio_codec=meta.primary_audio.codec_name if meta.primary_audio else None,
            has_audio=bool(meta.audio_streams),
            has_video=bool(meta.video_streams),
            audio_channels=meta.primary_audio.channels if meta.primary_audio else None,
            sample_rate=meta.primary_audio.sample_rate if meta.primary_audio else None,
            size_bytes=meta.size_bytes,
            bit_rate=meta.bit_rate,
        )

    if generate_thumb and asset.kind in ("video", "image"):
        try:
            ts = min(1.0, asset.duration / 2) if asset.duration else 0.0
            asset.thumbnail_path = str(generate_thumbnail(path, timestamp=ts))
        except Exception:
            asset.thumbnail_path = None

    return asset


def scan_folder(folder: Path, *, recursive: bool = True) -> list[Path]:
    known = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | {
        ".mp4", ".mov", ".mkv", ".avi", ".webm", ".mxf", ".m4v", ".mts", ".m2ts",
    }
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() in known)
