"""ffprobe wrapper producing structured, typed media metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from kdenlive_mcp.media.ffmpeg.runner import run
from kdenlive_mcp.errors import UnsupportedMediaError


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "N/A"):
        return None
    try:
        return float(Fraction(rate))
    except (ZeroDivisionError, ValueError):
        return None


@dataclass
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_aspect_ratio: str | None = None
    bit_rate: int | None = None
    channels: int | None = None
    sample_rate: int | None = None
    channel_layout: str | None = None
    rotation: int = 0


@dataclass
class MediaMetadata:
    path: Path
    format_name: str | None
    duration: float
    size_bytes: int | None
    bit_rate: int | None
    video_streams: list[StreamInfo] = field(default_factory=list)
    audio_streams: list[StreamInfo] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.video_streams and self._is_single_frame():
            return "image"
        if self.video_streams:
            return "video"
        if self.audio_streams:
            return "audio"
        return "unknown"

    def _is_single_frame(self) -> bool:
        vs = self.video_streams[0] if self.video_streams else None
        return bool(vs) and (self.duration in (0.0, None)) and not self.audio_streams

    @property
    def primary_video(self) -> StreamInfo | None:
        return self.video_streams[0] if self.video_streams else None

    @property
    def primary_audio(self) -> StreamInfo | None:
        return self.audio_streams[0] if self.audio_streams else None

    @property
    def width(self) -> int | None:
        return self.primary_video.width if self.primary_video else None

    @property
    def height(self) -> int | None:
        return self.primary_video.height if self.primary_video else None

    @property
    def fps(self) -> float | None:
        return self.primary_video.fps if self.primary_video else None

    @property
    def orientation(self) -> str:
        w, h = self.width, self.height
        if not w or not h:
            return "unknown"
        if self.primary_video and self.primary_video.rotation in (90, -90, 270):
            w, h = h, w
        if w > h:
            return "landscape"
        if h > w:
            return "portrait"
        return "square"

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "format": self.format_name,
            "duration": self.duration,
            "size_bytes": self.size_bytes,
            "bit_rate": self.bit_rate,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "orientation": self.orientation,
            "video_codec": self.primary_video.codec_name if self.primary_video else None,
            "audio_codec": self.primary_audio.codec_name if self.primary_audio else None,
            "audio_channels": self.primary_audio.channels if self.primary_audio else None,
            "sample_rate": self.primary_audio.sample_rate if self.primary_audio else None,
            "has_audio": bool(self.audio_streams),
            "has_video": bool(self.video_streams),
        }


def probe_media(path: Path) -> MediaMetadata:
    result = run(
        "ffprobe",
        [
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=False,
    )
    if not result.ok or not result.stdout.strip():
        raise UnsupportedMediaError(
            f"ffprobe could not read media: {path}",
            suggestion="Confirm the file is a valid, readable media file.",
            details={"stderr": result.stderr[-2000:]},
        )

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_streams: list[StreamInfo] = []
    audio_streams: list[StreamInfo] = []

    for s in streams:
        codec_type = s.get("codec_type")
        rotation = 0
        for side in s.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(side["rotation"])
        tags = s.get("tags", {}) or {}
        if "rotate" in tags:
            try:
                rotation = int(tags["rotate"])
            except ValueError:
                pass

        if codec_type == "video":
            video_streams.append(StreamInfo(
                index=s.get("index", 0),
                codec_type="video",
                codec_name=s.get("codec_name"),
                width=s.get("width"),
                height=s.get("height"),
                fps=_parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate")),
                sample_aspect_ratio=s.get("sample_aspect_ratio"),
                bit_rate=int(s["bit_rate"]) if s.get("bit_rate") else None,
                rotation=abs(rotation) % 360,
            ))
        elif codec_type == "audio":
            audio_streams.append(StreamInfo(
                index=s.get("index", 0),
                codec_type="audio",
                codec_name=s.get("codec_name"),
                bit_rate=int(s["bit_rate"]) if s.get("bit_rate") else None,
                channels=s.get("channels"),
                sample_rate=int(s["sample_rate"]) if s.get("sample_rate") else None,
                channel_layout=s.get("channel_layout"),
            ))

    duration = 0.0
    if fmt.get("duration"):
        duration = float(fmt["duration"])
    elif video_streams or audio_streams:
        for s in streams:
            if s.get("duration"):
                duration = max(duration, float(s["duration"]))

    return MediaMetadata(
        path=path,
        format_name=fmt.get("format_name"),
        duration=duration,
        size_bytes=int(fmt["size"]) if fmt.get("size") else None,
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        video_streams=video_streams,
        audio_streams=audio_streams,
        raw=data,
    )
