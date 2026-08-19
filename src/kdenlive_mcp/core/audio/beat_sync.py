"""Beat-sync editing API: turn detected beats into actual timeline edits.

Everything here works in timeline frames (integer, at the sequence's fps),
converting from the seconds-based output of `core.audio.beats` via a
required `fps: Fraction` parameter on every function -- consistent with how
the rest of the timeline model stores positions/durations
(`timecode.seconds_to_frames`).
"""

from __future__ import annotations

from fractions import Fraction

from kdenlive_mcp.core.keyframes.motion import (
    animate_opacity,
    create_camera_shake,
    create_zoom_punch,
)
from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.core.timeline.model import Clip, EffectInstance, Project
from kdenlive_mcp.core.timeline.timecode import seconds_to_frames
from kdenlive_mcp.errors import InvalidOperationError

_INTENSITY_STEP = {"subtle": 4, "medium": 2, "aggressive": 1}

# how close (seconds) a beat must be to a downbeat to count as "on" it
_DOWNBEAT_SNAP_SECONDS = 0.1


def select_beats(
    beat_times: list[float], *, intensity: str = "medium", phrase_aware: bool = True,
    downbeat_times: list[float] | None = None,
) -> list[float]:
    """Pick a musically-varied subset of beats to edit on.

    Cutting on literally every single beat looks mechanical/robotic; the
    spec explicitly warns against that, so intensity controls the base
    density: "subtle" takes every 4th beat, "medium" every 2nd, "aggressive"
    every beat. When `phrase_aware` is True and `downbeat_times` is
    supplied, any beat within `_DOWNBEAT_SNAP_SECONDS` of a detected
    downbeat is always included as a high-priority phrase anchor -- even at
    "subtle" intensity -- so edits still land on bar starts and feel like
    they're following the music's phrasing rather than a fixed mechanical
    stride that happens to drift across bar lines.
    """
    if intensity not in _INTENSITY_STEP:
        raise InvalidOperationError(
            f"Unknown intensity '{intensity}'",
            suggestion=f"Use one of: {sorted(_INTENSITY_STEP)}",
        )
    if not beat_times:
        return []

    step = _INTENSITY_STEP[intensity]
    selected_indices = set(range(0, len(beat_times), step))

    if phrase_aware and downbeat_times:
        for i, t in enumerate(beat_times):
            if any(abs(t - db) <= _DOWNBEAT_SNAP_SECONDS for db in downbeat_times):
                selected_indices.add(i)

    return [beat_times[i] for i in sorted(selected_indices)]


# --------------------------------------------------------------------- cut -

def cut_on_beat(
    clips: list[Clip], beat_times: list[float], fps: Fraction, *,
    intensity: str = "medium", downbeat_times: list[float] | None = None,
) -> None:
    """Re-time an existing ordered sequence of clips onto the beat grid.

    This does not create or resize clips: it mutates `position` in place,
    keeping the same order and the same durations the clips already had. The
    first clip starts at the first selected beat; each following clip starts
    at the next selected beat. If there are more clips than selected beats,
    overflow clips fall back to the *full* `beat_times` list (not just the
    selected subset) so the sequence keeps advancing on-beat rather than
    piling up. If even the full beat list runs out, remaining clips are
    chained back-to-back after the last placed clip so this never crashes or
    silently drops clips.
    """
    if not clips:
        return

    selected = select_beats(beat_times, intensity=intensity, downbeat_times=downbeat_times)
    selected_frames = [seconds_to_frames(t, fps) for t in selected]
    full_frames = [seconds_to_frames(t, fps) for t in beat_times]

    for i, clip in enumerate(clips):
        if i < len(selected_frames):
            clip.position = selected_frames[i]
        elif i < len(full_frames):
            clip.position = full_frames[i]
        else:
            prev = clips[i - 1]
            clip.position = prev.position + prev.timeline_length


# ------------------------------------------------------------- beat effects

def zoom_on_beat(
    clip: Clip, frame_w: int, frame_h: int, beat_times: list[float], fps: Fraction, *,
    intensity: str = "medium", downbeat_times: list[float] | None = None,
) -> list[EffectInstance]:
    """Apply a zoom punch (`create_zoom_punch`) at every beat selected by `select_beats`."""
    selected = select_beats(beat_times, intensity=intensity, downbeat_times=downbeat_times)
    return [
        create_zoom_punch(clip, frame_w, frame_h, at_frame=seconds_to_frames(t, fps))
        for t in selected
    ]


def shake_on_beat(
    clip: Clip, frame_w: int, frame_h: int, beat_times: list[float], fps: Fraction, *,
    intensity: str = "medium", downbeat_times: list[float] | None = None,
    duration_frames: int = 8,
) -> list[EffectInstance]:
    """Apply a short camera shake (`create_camera_shake`) at every selected beat.

    `create_camera_shake` takes a start/end window rather than a single
    `at_frame`, so each hit is windowed as `[beat, beat + duration_frames]`.
    """
    selected = select_beats(beat_times, intensity=intensity, downbeat_times=downbeat_times)
    effects = []
    for t in selected:
        frame = seconds_to_frames(t, fps)
        effects.append(create_camera_shake(
            clip, frame_w, frame_h, start_frame=frame, end_frame=frame + duration_frames, intensity=intensity,
        ))
    return effects


_FLASH_PRESETS = {
    # (base_opacity, peak_opacity, half_width_frames)
    "subtle": (0.85, 1.0, 2),
    "medium": (0.6, 1.0, 3),
    "aggressive": (0.3, 1.0, 4),
}


def flash_on_beat(
    clip: Clip, frame_w: int, frame_h: int, beat_times: list[float], fps: Fraction, *,
    intensity: str = "medium", downbeat_times: list[float] | None = None,
) -> list[EffectInstance]:
    """Approximate a "flash on beat" using an opacity spike.

    There is no dedicated flash / white-overlay effect confirmed available
    in this codebase's effects catalog, so this reuses
    `core.keyframes.motion.animate_opacity` to spike the clip's own opacity
    up to `peak_opacity` and back down to `base_opacity` around each
    selected beat. This dims/brightens the existing footage rather than
    flashing white over it -- a real white-flash transition would need a
    color-overlay effect that a different part of this codebase's effects
    catalog may or may not expose, and this module does not invent one.
    """
    base_opacity, peak_opacity, half_width = _FLASH_PRESETS.get(intensity, _FLASH_PRESETS["medium"])
    selected = select_beats(beat_times, intensity=intensity, downbeat_times=downbeat_times)
    effects = []
    for t in selected:
        frame = seconds_to_frames(t, fps)
        path = [
            (max(0, frame - half_width), base_opacity),
            (frame, peak_opacity),
            (frame + half_width, base_opacity),
        ]
        effects.append(animate_opacity(clip, frame_w, frame_h, path))
    return effects


def speed_ramp_on_beat(
    clip: Clip, beat_times: list[float], fps: Fraction, *, ramp_type: str = "into",
) -> None:
    """Not implemented: speed-ramping requires the speed/time-effects module.

    That's Section 8 of the beat-sync spec and hasn't been built in this
    codebase yet (no clip-speed/time-remap primitives exist to ramp between),
    so this raises rather than faking a ramp with something that isn't
    actually a speed change.
    """
    raise NotImplementedError(
        "speed_ramp_on_beat requires the speed/time-effects module (spec Section 8), "
        "which is not implemented in this codebase yet."
    )


# ------------------------------------------------------------------ montage

def montage_on_beats(
    project: Project, sequence_id: str, track_id: str,
    clips_with_sources: list[tuple[str, int, int]], beat_times: list[float], fps: Fraction, *,
    intensity: str = "medium", downbeat_times: list[float] | None = None,
) -> list[Clip]:
    """Build a beat-synced montage: place sources sequentially, cutting on selected beats.

    `clips_with_sources` is a list of `(asset_id, in_point, out_point)`
    source references. Clips are placed back-to-back on `track_id` starting
    at frame 0 so that each clip's *end* lands on a selected beat: the gap
    between the previous clip's end and the next selected beat becomes that
    clip's timeline duration, and `out_point` is trimmed down to
    `in_point + gap` to match. If the requested `out_point` doesn't have
    enough source material to fill the gap (i.e. the source is shorter than
    the beat-to-beat gap), the clip simply uses all the source it has and
    the montage falls slightly behind the beat grid for that one cut rather
    than fabricating footage that doesn't exist.

    Uses `ops.add_clip` for placement, so its own overlap/validation checks
    apply -- this function does not duplicate that logic.
    """
    if not clips_with_sources:
        return []

    seq = project.get_sequence(sequence_id)
    if seq is None:
        raise InvalidOperationError(f"Sequence not found: {sequence_id}")
    track = seq.get_track(track_id)
    if track is None:
        raise InvalidOperationError(f"Track not found: {track_id}")
    clip_type = track.track_type  # "video" or "audio" both valid ClipType values

    selected = select_beats(beat_times, intensity=intensity, downbeat_times=downbeat_times)
    selected_frames = [seconds_to_frames(t, fps) for t in selected]
    full_frames = [seconds_to_frames(t, fps) for t in beat_times]

    placed: list[Clip] = []
    cursor = 0
    for i, (asset_id, in_point, out_point) in enumerate(clips_with_sources):
        if i < len(selected_frames) and selected_frames[i] > cursor:
            target_end = selected_frames[i]
        elif i < len(full_frames) and full_frames[i] > cursor:
            target_end = full_frames[i]
        else:
            # ran out of usable beats: fall back to the clip's own full length
            target_end = cursor + max(1, out_point - in_point)

        gap = max(1, target_end - cursor)
        trimmed_out = min(out_point, in_point + gap)

        clip = ops.add_clip(
            project, sequence_id, track_id,
            position=cursor, in_point=in_point, out_point=trimmed_out,
            asset_id=asset_id, clip_type=clip_type,
        )
        placed.append(clip)
        cursor = clip.end

    return placed
