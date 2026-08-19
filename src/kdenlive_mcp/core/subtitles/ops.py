"""Subtitle editing operations, mirroring core.timeline.ops's style: pure
functions taking (project, sequence_id, ...), mutating the model in place,
raising InvalidOperationError with an actionable message on failure.
"""

from __future__ import annotations

from pathlib import Path

from kdenlive_mcp.core.subtitles.srt import format_srt, parse_srt
from kdenlive_mcp.core.timeline.model import Project, Sequence, Subtitle, new_id
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.storage.workspace import resolve_source_path, resolve_workspace_path


def _sequence(project: Project, sequence_id: str) -> Sequence:
    seq = project.get_sequence(sequence_id)
    if seq is None:
        raise InvalidOperationError(f"Sequence not found: {sequence_id}")
    return seq


def _subtitle(seq: Sequence, subtitle_id: str) -> Subtitle:
    sub = seq.get_subtitle(subtitle_id)
    if sub is None:
        raise InvalidOperationError(f"Subtitle not found: {subtitle_id}")
    return sub


def add_subtitle(project: Project, sequence_id: str, *, start_frame: int, end_frame: int, text: str) -> Subtitle:
    if end_frame <= start_frame:
        raise InvalidOperationError("end_frame must be greater than start_frame")
    if not text.strip():
        raise InvalidOperationError("Subtitle text cannot be empty")
    seq = _sequence(project, sequence_id)
    sub = Subtitle(id=new_id("subtitle"), start_frame=start_frame, end_frame=end_frame, text=text)
    seq.subtitles.append(sub)
    project.dirty = True
    return sub


def remove_subtitle(project: Project, sequence_id: str, subtitle_id: str) -> None:
    seq = _sequence(project, sequence_id)
    _subtitle(seq, subtitle_id)
    seq.subtitles = [s for s in seq.subtitles if s.id != subtitle_id]
    project.dirty = True


def edit_subtitle_text(project: Project, sequence_id: str, subtitle_id: str, *, text: str) -> Subtitle:
    if not text.strip():
        raise InvalidOperationError("Subtitle text cannot be empty")
    seq = _sequence(project, sequence_id)
    sub = _subtitle(seq, subtitle_id)
    sub.text = text
    project.dirty = True
    return sub


def move_subtitle(project: Project, sequence_id: str, subtitle_id: str, *,
                   start_frame: int, end_frame: int) -> Subtitle:
    if end_frame <= start_frame:
        raise InvalidOperationError("end_frame must be greater than start_frame")
    seq = _sequence(project, sequence_id)
    sub = _subtitle(seq, subtitle_id)
    sub.start_frame = start_frame
    sub.end_frame = end_frame
    project.dirty = True
    return sub


def split_subtitle(project: Project, sequence_id: str, subtitle_id: str, *, at_frame: int,
                    first_text: str | None = None, second_text: str | None = None) -> tuple[Subtitle, Subtitle]:
    seq = _sequence(project, sequence_id)
    sub = _subtitle(seq, subtitle_id)
    if not (sub.start_frame < at_frame < sub.end_frame):
        raise InvalidOperationError(
            f"Split point {at_frame} is not inside subtitle range {sub.start_frame}-{sub.end_frame}"
        )
    original_end = sub.end_frame
    sub.end_frame = at_frame
    if first_text is not None:
        sub.text = first_text

    second = Subtitle(id=new_id("subtitle"), start_frame=at_frame, end_frame=original_end,
                       text=second_text if second_text is not None else sub.text)
    seq.subtitles.append(second)
    project.dirty = True
    return sub, second


def merge_subtitles(project: Project, sequence_id: str, subtitle_ids: list[str], *, separator: str = " ") -> Subtitle:
    if len(subtitle_ids) < 2:
        raise InvalidOperationError("merge_subtitles needs at least 2 subtitle ids")
    seq = _sequence(project, sequence_id)
    subs = [_subtitle(seq, sid) for sid in subtitle_ids]
    subs.sort(key=lambda s: s.start_frame)

    merged = Subtitle(
        id=new_id("subtitle"),
        start_frame=subs[0].start_frame,
        end_frame=subs[-1].end_frame,
        text=separator.join(s.text for s in subs),
    )
    ids = set(subtitle_ids)
    seq.subtitles = [s for s in seq.subtitles if s.id not in ids] + [merged]
    project.dirty = True
    return merged


def import_srt(project: Project, sequence_id: str, srt_path: str, *, replace_existing: bool = False) -> list[Subtitle]:
    seq = _sequence(project, sequence_id)
    resolved = resolve_source_path(srt_path)
    text = resolved.read_text(encoding="utf-8")
    imported = parse_srt(text, project.settings.fps)
    if not imported:
        raise InvalidOperationError(f"No subtitle entries found in: {srt_path}")

    if replace_existing:
        seq.subtitles = imported
    else:
        seq.subtitles.extend(imported)
    project.dirty = True
    return imported


def export_srt(project: Project, sequence_id: str, output_path: str) -> Path:
    seq = _sequence(project, sequence_id)
    if not seq.subtitles:
        raise InvalidOperationError("Sequence has no subtitles to export")
    resolved = resolve_workspace_path(output_path) if not Path(output_path).is_absolute() else Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(format_srt(seq.subtitles, project.settings.fps), encoding="utf-8")
    return resolved
