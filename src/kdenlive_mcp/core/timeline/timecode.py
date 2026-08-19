"""Frame/second/timecode conversions.

The internal timeline model works exclusively in integer frames (matching
how Kdenlive/MLT actually store edits) so repeated edits never accumulate
float drift. Seconds are only used at the MCP tool boundary for
human-friendly input/output.
"""

from __future__ import annotations

from fractions import Fraction


def seconds_to_frames(seconds: float, fps: Fraction) -> int:
    return round(seconds * float(fps))


def frames_to_seconds(frames: int, fps: Fraction) -> float:
    return frames / float(fps)


def frames_to_timecode(frames: int, fps: Fraction) -> str:
    """Format as Kdenlive/MLT-style HH:MM:SS.mmm (frames converted to ms)."""
    total_ms = round(frames * 1000 / float(fps))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def timecode_to_frames(timecode: str, fps: Fraction) -> int:
    """Parse HH:MM:SS.mmm (or MM:SS.mmm / SS.mmm) into frames."""
    parts = timecode.strip().split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    total_seconds = h * 3600 + m * 60 + s
    return round(total_seconds * float(fps))
