"""Lightweight SFX placement/categorization scaffold.

This is NOT a live SFX library client -- no network calls happen here and
no API keys are available in this environment. Real search/download
(`search_sfx`, `download_sfx` from spec Section 14) is left as a TODO for
when a configured asset provider actually exists.
"""

from __future__ import annotations

from fractions import Fraction

from kdenlive_mcp.core.assets.model import MediaAsset
from kdenlive_mcp.core.audio.beat_sync import select_beats
from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.core.timeline.model import Clip, Project
from kdenlive_mcp.core.timeline.timecode import seconds_to_frames
from kdenlive_mcp.errors import InvalidOperationError

SFX_CATEGORIES = [
    "whoosh", "impact", "riser", "hit", "transition", "click",
    "camera", "glitch", "bass", "ambience", "cinematic", "ui",
]


def place_sfx(
    project: Project, sequence_id: str, track_id: str, *,
    asset_id: str, at_frame: int, category: str, asset: MediaAsset,
) -> Clip:
    """Place a short audio asset at `at_frame` on an audio track, tagged with its SFX category."""
    if category not in SFX_CATEGORIES:
        raise InvalidOperationError(
            f"Unknown SFX category '{category}'",
            suggestion=f"Use one of: {SFX_CATEGORIES}",
        )

    fps: Fraction = project.settings.fps
    duration_frames = seconds_to_frames(asset.duration, fps) if asset.duration else 0
    duration_frames = max(1, duration_frames)

    clip = ops.add_clip(
        project, sequence_id, track_id,
        position=at_frame, in_point=0, out_point=duration_frames,
        asset_id=asset_id, clip_type="audio", name=f"sfx:{category}",
    )
    clip.metadata["sfx_category"] = category
    return clip


def sfx_on_beat(
    project: Project, sequence_id: str, track_id: str, asset: MediaAsset,
    beat_times: list[float], fps: Fraction, *,
    intensity: str = "medium", downbeat_times: list[float] | None = None,
    category: str = "hit",
) -> list[Clip]:
    """Place one SFX hit at every beat selected by `beat_sync.select_beats`."""
    selected = select_beats(beat_times, intensity=intensity, downbeat_times=downbeat_times)
    clips = []
    for t in selected:
        frame = seconds_to_frames(t, fps)
        clips.append(place_sfx(
            project, sequence_id, track_id,
            asset_id=asset.id, at_frame=frame, category=category, asset=asset,
        ))
    return clips


# TODO(phase5): wire to a configured asset provider once one exists
# (search_sfx / download_sfx from spec Section 14) -- no network provider is
# configured or available in this environment, so it's not built here.
