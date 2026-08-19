"""SubRip (.srt) format read/write.

Kdenlive stores a sequence's subtitles as a plain sibling .srt file (see
kdenlive/adapter/xml_writer.py's subtitle handling), not inline in the
.kdenlive XML. SRT timecodes use a comma for milliseconds
(HH:MM:SS,mmm) -- distinct from MLT's own dot-separated
HH:MM:SS.mmm used everywhere else in this codebase (core.timeline.timecode)
-- so this module has its own small timecode formatter/parser rather than
reusing that one.
"""

from __future__ import annotations

import re
from fractions import Fraction

from kdenlive_mcp.core.timeline.model import Subtitle, new_id
from kdenlive_mcp.core.timeline.timecode import frames_to_seconds, seconds_to_frames
from kdenlive_mcp.errors import ValidationError

_BLOCK_RE = re.compile(
    r"(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n"
    r"(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
    re.DOTALL,
)


def srt_timecode(frames: int, fps: Fraction) -> str:
    total_ms = round(frames_to_seconds(frames, fps) * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_timecode(tc: str, fps: Fraction) -> int:
    m = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", tc.strip())
    if not m:
        raise ValidationError(f"Invalid SRT timecode: {tc!r}")
    h, mi, s, ms = (int(g) for g in m.groups())
    total_seconds = h * 3600 + mi * 60 + s + ms / 1000
    return seconds_to_frames(total_seconds, fps)


def format_srt(subtitles: list[Subtitle], fps: Fraction) -> str:
    blocks = []
    for i, sub in enumerate(sorted(subtitles, key=lambda s: s.start_frame), start=1):
        start = srt_timecode(sub.start_frame, fps)
        end = srt_timecode(sub.end_frame, fps)
        blocks.append(f"{i}\n{start} --> {end}\n{sub.text}\n")
    return "\n".join(blocks)


def parse_srt(text: str, fps: Fraction) -> list[Subtitle]:
    subtitles = []
    normalized = text.replace("\r\n", "\n").strip() + "\n"
    for match in _BLOCK_RE.finditer(normalized):
        _, start_tc, end_tc, body = match.groups()
        subtitles.append(Subtitle(
            id=new_id("subtitle"),
            start_frame=parse_srt_timecode(start_tc, fps),
            end_frame=parse_srt_timecode(end_tc, fps),
            text=body.strip("\n"),
        ))
    return subtitles
