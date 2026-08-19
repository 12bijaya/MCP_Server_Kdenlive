"""Timeline editing primitives.

Every function here takes a `Project` plus a `sequence_id` and mutates the
model in place, returning whatever is most useful to the caller (the
touched Clip/Track, or None). All functions validate invariants (no
overlapping clips on one track, valid in/out ranges, existing ids) and
raise errors.InvalidOperationError / ClipNotFoundError / TrackNotFoundError
with an actionable message on failure. Nothing here touches Kdenlive XML.
"""

from __future__ import annotations

from kdenlive_mcp.core.timeline.model import (
    Clip, ClipType, Marker, Project, Sequence, Track, TrackType, new_id,
)
from kdenlive_mcp.errors import ClipNotFoundError, InvalidOperationError, TrackNotFoundError


def _sequence(project: Project, sequence_id: str) -> Sequence:
    seq = project.get_sequence(sequence_id)
    if seq is None:
        raise InvalidOperationError(f"Sequence not found: {sequence_id}")
    return seq


def _track(seq: Sequence, track_id: str) -> Track:
    t = seq.get_track(track_id)
    if t is None:
        raise TrackNotFoundError(f"Track not found: {track_id}", suggestion="Call list_sequences or get_project_info to see valid track ids.")
    return t


def _clip(seq: Sequence, clip_id: str) -> tuple[Track, Clip]:
    found = seq.get_clip(clip_id)
    if found is None:
        raise ClipNotFoundError(f"Clip not found: {clip_id}")
    return found


def _check_overlap(track: Track, position: int, length: int, exclude_clip_id: str | None = None) -> None:
    if position < 0:
        raise InvalidOperationError("Clip position cannot be negative")
    if length <= 0:
        raise InvalidOperationError("Clip length must be positive")
    other = track.overlaps(position, length, exclude_clip_id=exclude_clip_id)
    if other is not None:
        raise InvalidOperationError(
            f"Clip would overlap existing clip '{other.id}' on track '{track.id}' "
            f"(existing: {other.position}-{other.end}, requested: {position}-{position + length})",
            suggestion="Choose a different position/track, or use ripple_insert to make room.",
        )


def _mark_dirty(project: Project) -> None:
    project.dirty = True


# ---------------------------------------------------------------- clips ---

def add_clip(
    project: Project, sequence_id: str, track_id: str, *,
    position: int, in_point: int, out_point: int,
    asset_id: str | None = None, clip_type: ClipType = "video",
    name: str = "", speed: float = 1.0, text_content: str | None = None,
    color: str | None = None,
) -> Clip:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    if out_point <= in_point:
        raise InvalidOperationError("out_point must be greater than in_point")

    clip = Clip(
        id=new_id("clip"), track_id=track.id, clip_type=clip_type,
        position=position, in_point=in_point, out_point=out_point,
        asset_id=asset_id, speed=speed, name=name,
        text_content=text_content, color=color,
    )
    _check_overlap(track, position, clip.timeline_length)
    track.clips.append(clip)
    _mark_dirty(project)
    return clip


def remove_clip(project: Project, sequence_id: str, clip_id: str) -> None:
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    track.clips = [c for c in track.clips if c.id != clip_id]
    _mark_dirty(project)


def ripple_delete(project: Project, sequence_id: str, clip_id: str, *, affect_all_tracks: bool = False) -> None:
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    gap_start = clip.position
    gap_len = clip.timeline_length
    track.clips = [c for c in track.clips if c.id != clip_id]

    targets = seq.tracks if affect_all_tracks else [track]
    for t in targets:
        for c in t.clips:
            if c.position >= gap_start:
                c.position -= gap_len
    _mark_dirty(project)


def ripple_insert(
    project: Project, sequence_id: str, track_id: str, *,
    position: int, in_point: int, out_point: int,
    asset_id: str | None = None, clip_type: ClipType = "video",
    name: str = "", affect_all_tracks: bool = True,
) -> Clip:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    if out_point <= in_point:
        raise InvalidOperationError("out_point must be greater than in_point")
    length = out_point - in_point

    targets = seq.tracks if affect_all_tracks else [track]
    for t in targets:
        for c in t.clips:
            if c.position >= position:
                c.position += length

    clip = Clip(
        id=new_id("clip"), track_id=track.id, clip_type=clip_type,
        position=position, in_point=in_point, out_point=out_point,
        asset_id=asset_id, name=name,
    )
    track.clips.append(clip)
    _mark_dirty(project)
    return clip


def move_clip(
    project: Project, sequence_id: str, clip_id: str, *,
    new_position: int, new_track_id: str | None = None, move_group: bool = True,
) -> Clip:
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    dest_track = _track(seq, new_track_id) if new_track_id else track
    delta = new_position - clip.position

    _check_overlap(dest_track, new_position, clip.timeline_length, exclude_clip_id=clip.id)

    if move_group and clip.group_id:
        group_members = [(t, c) for t in seq.tracks for c in t.clips if c.group_id == clip.group_id and c.id != clip.id]
        for t, c in group_members:
            _check_overlap(t, c.position + delta, c.timeline_length, exclude_clip_id=c.id)
        for t, c in group_members:
            c.position += delta

    if dest_track is not track:
        track.clips.remove(clip)
        clip.track_id = dest_track.id
        dest_track.clips.append(clip)
    clip.position = new_position
    _mark_dirty(project)
    return clip


def trim_clip(project: Project, sequence_id: str, clip_id: str, *, edge: str, delta_frames: int) -> Clip:
    """Adjust the in/out point of one edge of a clip (non-ripple trim)."""
    if edge not in ("start", "end"):
        raise InvalidOperationError("edge must be 'start' or 'end'")
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    source_delta = round(delta_frames * clip.speed)

    if edge == "start":
        new_in = clip.in_point + source_delta
        new_position = clip.position + delta_frames
        if new_in < 0:
            raise InvalidOperationError("Trim would move in_point before 0 (no more source handle available)")
        if new_in >= clip.out_point:
            raise InvalidOperationError("Trim would collapse the clip to zero/negative length")
        _check_overlap(track, new_position, round((clip.out_point - new_in) / clip.speed), exclude_clip_id=clip.id)
        clip.in_point = new_in
        clip.position = new_position
    else:
        new_out = clip.out_point + source_delta
        if new_out <= clip.in_point:
            raise InvalidOperationError("Trim would collapse the clip to zero/negative length")
        new_length = round((new_out - clip.in_point) / clip.speed)
        _check_overlap(track, clip.position, new_length, exclude_clip_id=clip.id)
        clip.out_point = new_out

    _mark_dirty(project)
    return clip


def extend_clip(project: Project, sequence_id: str, clip_id: str, *, frames: int) -> Clip:
    return trim_clip(project, sequence_id, clip_id, edge="end", delta_frames=abs(frames))


def shorten_clip(project: Project, sequence_id: str, clip_id: str, *, frames: int) -> Clip:
    return trim_clip(project, sequence_id, clip_id, edge="end", delta_frames=-abs(frames))


def slip_clip(project: Project, sequence_id: str, clip_id: str, *, delta_frames: int) -> Clip:
    """Shift which portion of the source is shown, keeping position/length fixed."""
    seq = _sequence(project, sequence_id)
    _, clip = _clip(seq, clip_id)
    source_delta = round(delta_frames * clip.speed)
    new_in = clip.in_point + source_delta
    new_out = clip.out_point + source_delta
    if new_in < 0:
        raise InvalidOperationError("Slip would move in_point before source start")
    clip.in_point = new_in
    clip.out_point = new_out
    _mark_dirty(project)
    return clip


def slide_clip(project: Project, sequence_id: str, clip_id: str, *, delta_frames: int) -> Clip:
    """Move a clip while trimming its immediate neighbors to absorb the shift."""
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    ordered = track.sorted_clips()
    idx = next(i for i, c in enumerate(ordered) if c.id == clip.id)
    prev_clip = ordered[idx - 1] if idx > 0 else None
    next_clip = ordered[idx + 1] if idx < len(ordered) - 1 else None

    if delta_frames > 0 and next_clip and clip.end + delta_frames > next_clip.end:
        raise InvalidOperationError("Slide would consume the entire next clip")
    if delta_frames < 0 and prev_clip and clip.position + delta_frames < prev_clip.position:
        raise InvalidOperationError("Slide would consume the entire previous clip")

    if prev_clip is not None and prev_clip.end == clip.position:
        trim_clip(project, sequence_id, prev_clip.id, edge="end", delta_frames=delta_frames)
    if next_clip is not None and next_clip.position == clip.end:
        trim_clip(project, sequence_id, next_clip.id, edge="start", delta_frames=delta_frames)

    clip.position += delta_frames
    _mark_dirty(project)
    return clip


def split_clip(project: Project, sequence_id: str, clip_id: str, *, at_frame: int) -> tuple[Clip, Clip]:
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    if not (clip.position < at_frame < clip.end):
        raise InvalidOperationError(f"Split point {at_frame} is not inside clip range {clip.position}-{clip.end}")

    offset_frames = at_frame - clip.position
    source_offset = round(offset_frames * clip.speed)
    split_source_point = clip.in_point + source_offset

    left = clip
    right = Clip(
        id=new_id("clip"), track_id=track.id, clip_type=clip.clip_type,
        position=at_frame, in_point=split_source_point, out_point=clip.out_point,
        asset_id=clip.asset_id, speed=clip.speed, name=clip.name,
        text_content=clip.text_content, color=clip.color,
        fade_out=clip.fade_out,
    )
    left.out_point = split_source_point
    left.fade_out = 0
    track.clips.append(right)
    _mark_dirty(project)
    return left, right


def duplicate_clip(
    project: Project, sequence_id: str, clip_id: str, *,
    new_position: int | None = None, new_track_id: str | None = None,
) -> Clip:
    seq = _sequence(project, sequence_id)
    track, clip = _clip(seq, clip_id)
    dest_track = _track(seq, new_track_id) if new_track_id else track
    position = new_position if new_position is not None else clip.end

    new_clip = Clip(
        id=new_id("clip"), track_id=dest_track.id, clip_type=clip.clip_type,
        position=position, in_point=clip.in_point, out_point=clip.out_point,
        asset_id=clip.asset_id, speed=clip.speed, name=clip.name,
        effects=[e for e in clip.effects],  # shallow copy of effect stack
        text_content=clip.text_content, color=clip.color,
        fade_in=clip.fade_in, fade_out=clip.fade_out,
    )
    _check_overlap(dest_track, position, new_clip.timeline_length)
    dest_track.clips.append(new_clip)
    _mark_dirty(project)
    return new_clip


def replace_clip(project: Project, sequence_id: str, clip_id: str, *, new_asset_id: str) -> Clip:
    seq = _sequence(project, sequence_id)
    _, clip = _clip(seq, clip_id)
    clip.asset_id = new_asset_id
    _mark_dirty(project)
    return clip


def reorder_clips(project: Project, sequence_id: str, track_id: str, *, ordered_clip_ids: list[str]) -> Track:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    by_id = {c.id: c for c in track.clips}
    if set(ordered_clip_ids) != set(by_id.keys()):
        raise InvalidOperationError("ordered_clip_ids must contain exactly the clip ids currently on this track")

    cursor = 0
    for cid in ordered_clip_ids:
        c = by_id[cid]
        c.position = cursor
        cursor += c.timeline_length
    _mark_dirty(project)
    return track


# ---------------------------------------------------------------- tracks --

def create_track(project: Project, sequence_id: str, *, track_type: TrackType, name: str = "") -> Track:
    seq = _sequence(project, sequence_id)
    index = seq.next_track_index(track_type)
    track = Track(id=new_id("track"), index=index, track_type=track_type,
                   name=name or f"{'V' if track_type == 'video' else 'A'}{index + 1}")
    seq.tracks.append(track)
    _mark_dirty(project)
    return track


def delete_track(project: Project, sequence_id: str, track_id: str) -> None:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    if track.clips:
        raise InvalidOperationError(
            f"Track '{track_id}' still has {len(track.clips)} clip(s)",
            suggestion="Remove or move its clips first, or pass force via a higher-level tool.",
        )
    seq.tracks = [t for t in seq.tracks if t.id != track_id]
    _mark_dirty(project)


def mute_track(project: Project, sequence_id: str, track_id: str, *, muted: bool = True) -> Track:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    track.muted = muted
    _mark_dirty(project)
    return track


def solo_track(project: Project, sequence_id: str, track_id: str, *, solo: bool = True) -> Track:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    track.solo = solo
    _mark_dirty(project)
    return track


def lock_track(project: Project, sequence_id: str, track_id: str, *, locked: bool = True) -> Track:
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)
    track.locked = locked
    _mark_dirty(project)
    return track


# ---------------------------------------------------------------- groups --

def group_clips(project: Project, sequence_id: str, clip_ids: list[str]) -> str:
    seq = _sequence(project, sequence_id)
    group_id = new_id("group")
    for cid in clip_ids:
        _, clip = _clip(seq, cid)
        clip.group_id = group_id
    _mark_dirty(project)
    return group_id


def ungroup_clips(project: Project, sequence_id: str, clip_ids: list[str]) -> None:
    seq = _sequence(project, sequence_id)
    for cid in clip_ids:
        _, clip = _clip(seq, cid)
        clip.group_id = None
    _mark_dirty(project)


def align_clips(project: Project, sequence_id: str, clip_ids: list[str], *, mode: str = "start") -> list[Clip]:
    if mode not in ("start", "end"):
        raise InvalidOperationError("mode must be 'start' or 'end'")
    seq = _sequence(project, sequence_id)
    pairs = [_clip(seq, cid) for cid in clip_ids]
    if not pairs:
        return []
    if mode == "start":
        target = min(c.position for _, c in pairs)
        for t, c in pairs:
            _check_overlap(t, target, c.timeline_length, exclude_clip_id=c.id)
        for _, c in pairs:
            c.position = target
    else:
        target_end = max(c.end for _, c in pairs)
        for t, c in pairs:
            new_pos = target_end - c.timeline_length
            _check_overlap(t, new_pos, c.timeline_length, exclude_clip_id=c.id)
        for _, c in pairs:
            c.position = target_end - c.timeline_length
    _mark_dirty(project)
    return [c for _, c in pairs]


# --------------------------------------------------------------- snapping-

def snap_to_marker(project: Project, sequence_id: str, clip_id: str, *, edge: str = "start", tolerance_frames: int = 15) -> Clip | None:
    seq = _sequence(project, sequence_id)
    _, clip = _clip(seq, clip_id)
    anchor = clip.position if edge == "start" else clip.end
    nearest = min(seq.markers, key=lambda m: abs(m.frame - anchor), default=None)
    if nearest is None or abs(nearest.frame - anchor) > tolerance_frames:
        return None
    delta = nearest.frame - anchor
    return move_clip(project, sequence_id, clip_id, new_position=clip.position + delta)


def snap_to_beat(project: Project, sequence_id: str, clip_id: str, *, beat_frames: list[int], edge: str = "start", tolerance_frames: int = 15) -> Clip | None:
    seq = _sequence(project, sequence_id)
    _, clip = _clip(seq, clip_id)
    if not beat_frames:
        return None
    anchor = clip.position if edge == "start" else clip.end
    nearest = min(beat_frames, key=lambda f: abs(f - anchor))
    if abs(nearest - anchor) > tolerance_frames:
        return None
    delta = nearest - anchor
    return move_clip(project, sequence_id, clip_id, new_position=clip.position + delta)


# --------------------------------------------------------------- markers --

def add_marker(project: Project, sequence_id: str, *, frame: int, name: str = "", color: str = "#00ff00", category: str = "default") -> Marker:
    seq = _sequence(project, sequence_id)
    marker = Marker(id=new_id("marker"), frame=frame, name=name, color=color, category=category)
    seq.markers.append(marker)
    seq.markers.sort(key=lambda m: m.frame)
    _mark_dirty(project)
    return marker


def _marker(seq: Sequence, marker_id: str) -> Marker:
    for m in seq.markers:
        if m.id == marker_id:
            return m
    raise InvalidOperationError(f"Marker not found: {marker_id}")


def remove_marker(project: Project, sequence_id: str, marker_id: str) -> None:
    seq = _sequence(project, sequence_id)
    _marker(seq, marker_id)
    seq.markers = [m for m in seq.markers if m.id != marker_id]
    _mark_dirty(project)


def remove_markers_by_category(project: Project, sequence_id: str, category: str) -> int:
    seq = _sequence(project, sequence_id)
    before = len(seq.markers)
    seq.markers = [m for m in seq.markers if m.category != category]
    removed = before - len(seq.markers)
    if removed:
        _mark_dirty(project)
    return removed


def move_marker(project: Project, sequence_id: str, marker_id: str, *, frame: int) -> Marker:
    seq = _sequence(project, sequence_id)
    marker = _marker(seq, marker_id)
    marker.frame = frame
    seq.markers.sort(key=lambda m: m.frame)
    _mark_dirty(project)
    return marker


def edit_marker(project: Project, sequence_id: str, marker_id: str, *,
                 name: str | None = None, color: str | None = None, category: str | None = None) -> Marker:
    seq = _sequence(project, sequence_id)
    marker = _marker(seq, marker_id)
    if name is not None:
        marker.name = name
    if color is not None:
        marker.color = color
    if category is not None:
        marker.category = category
    _mark_dirty(project)
    return marker


# ---------------------------------------------------------- composite ops -

def replace_scene(
    project: Project, sequence_id: str, track_id: str, *,
    start_frame: int, end_frame: int, in_point: int, out_point: int,
    asset_id: str | None = None, clip_type: ClipType = "video", name: str = "",
) -> tuple[Clip, list[str]]:
    """Replaces whatever occupies [start_frame, end_frame) on a track with
    a new clip: clips fully inside the range are removed, clips only
    partially overlapping are trimmed at the boundary, and a clip that
    fully contains the range gets split around it. Returns the new clip
    and the ids of every clip removed in the process (trims/splits keep
    their original ids, so they're not included).
    """
    if end_frame <= start_frame:
        raise InvalidOperationError("end_frame must be greater than start_frame")
    seq = _sequence(project, sequence_id)
    track = _track(seq, track_id)

    removed_ids: list[str] = []
    for c in list(track.clips):
        if c.end <= start_frame or c.position >= end_frame:
            continue  # no overlap with the range being replaced
        if c.position >= start_frame and c.end <= end_frame:
            track.clips.remove(c)
            removed_ids.append(c.id)
        elif c.position < start_frame and c.end <= end_frame:
            trim_clip(project, sequence_id, c.id, edge="end", delta_frames=start_frame - c.end)
        elif c.position >= start_frame and c.end > end_frame:
            trim_clip(project, sequence_id, c.id, edge="start", delta_frames=end_frame - c.position)
        else:
            # the replaced range is a hole entirely inside this one clip
            _left, right = split_clip(project, sequence_id, c.id, at_frame=start_frame)
            if end_frame < right.end:
                middle, _right2 = split_clip(project, sequence_id, right.id, at_frame=end_frame)
                track.clips.remove(middle)
                removed_ids.append(middle.id)
            else:
                track.clips.remove(right)
                removed_ids.append(right.id)

    new_clip = add_clip(
        project, sequence_id, track_id,
        position=start_frame, in_point=in_point, out_point=out_point,
        asset_id=asset_id, clip_type=clip_type, name=name,
    )
    return new_clip, removed_ids
