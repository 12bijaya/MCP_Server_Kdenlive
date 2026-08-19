"""SUBTITLE tools (spec section 11).

Kdenlive stores subtitles as a sibling <project>.kdenlive.srt file, not
inline in the project XML -- see kdenlive/adapter/xml_writer.py's
_write_subtitles for the ground truth (KDE's own dev-docs/fileformat.md).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.subtitles import ops as sub_ops
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, f2s, mutates, s2f, tool_result


def _subtitle_summary(sub, project) -> dict:
    return {
        "id": sub.id, "start": f2s(sub.start_frame, project), "end": f2s(sub.end_frame, project),
        "text": sub.text,
    }


def register(mcp: FastMCP) -> None:

    def _seq_id(session, sequence_id: str | None) -> str:
        sid = sequence_id or session.project.active_sequence_id
        if sid is None:
            raise InvalidOperationError("Project has no active sequence")
        return sid

    @mcp.tool()
    @catch_errors
    def list_subtitles(sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """List every subtitle entry in a sequence, ordered by start time."""
        session = get_state().get(project_id)
        p = session.project
        seq = p.get_sequence(_seq_id(session, sequence_id))
        return tool_result(subtitles=[_subtitle_summary(s, p) for s in seq.sorted_subtitles()])

    @mcp.tool()
    @catch_errors
    @mutates
    def add_subtitle(start_seconds: float, end_seconds: float, text: str,
                      sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Add a subtitle entry spanning [start_seconds, end_seconds)."""
        session = get_state().get(project_id)
        p = session.project
        sub = sub_ops.add_subtitle(p, _seq_id(session, sequence_id),
                                    start_frame=s2f(start_seconds, p), end_frame=s2f(end_seconds, p), text=text)
        return tool_result(subtitle=_subtitle_summary(sub, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def remove_subtitle(subtitle_id: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Remove a subtitle entry."""
        session = get_state().get(project_id)
        sub_ops.remove_subtitle(session.project, _seq_id(session, sequence_id), subtitle_id)
        return tool_result(removed=subtitle_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def edit_subtitle_text(subtitle_id: str, text: str, sequence_id: str | None = None,
                            project_id: str | None = None) -> dict:
        """Change a subtitle entry's text."""
        session = get_state().get(project_id)
        p = session.project
        sub = sub_ops.edit_subtitle_text(p, _seq_id(session, sequence_id), subtitle_id, text=text)
        return tool_result(subtitle=_subtitle_summary(sub, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def move_subtitle(subtitle_id: str, start_seconds: float, end_seconds: float,
                       sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Retime a subtitle entry to a new [start_seconds, end_seconds) span."""
        session = get_state().get(project_id)
        p = session.project
        sub = sub_ops.move_subtitle(p, _seq_id(session, sequence_id), subtitle_id,
                                     start_frame=s2f(start_seconds, p), end_frame=s2f(end_seconds, p))
        return tool_result(subtitle=_subtitle_summary(sub, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def split_subtitle(subtitle_id: str, at_seconds: float, first_text: str | None = None,
                        second_text: str | None = None, sequence_id: str | None = None,
                        project_id: str | None = None) -> dict:
        """Split one subtitle entry into two at at_seconds. Optionally set each half's text."""
        session = get_state().get(project_id)
        p = session.project
        first, second = sub_ops.split_subtitle(p, _seq_id(session, sequence_id), subtitle_id,
                                                at_frame=s2f(at_seconds, p),
                                                first_text=first_text, second_text=second_text)
        return tool_result(first=_subtitle_summary(first, p), second=_subtitle_summary(second, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def merge_subtitles(subtitle_ids: list[str], separator: str = " ",
                         sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Merge multiple subtitle entries into one spanning their full range."""
        session = get_state().get(project_id)
        p = session.project
        merged = sub_ops.merge_subtitles(p, _seq_id(session, sequence_id), subtitle_ids, separator=separator)
        return tool_result(subtitle=_subtitle_summary(merged, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def import_subtitles(srt_path: str, replace_existing: bool = False,
                          sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Import an existing .srt file's entries into the sequence."""
        session = get_state().get(project_id)
        p = session.project
        imported = sub_ops.import_srt(p, _seq_id(session, sequence_id), srt_path, replace_existing=replace_existing)
        return tool_result(imported_count=len(imported), subtitles=[_subtitle_summary(s, p) for s in imported])

    @mcp.tool()
    @catch_errors
    def export_subtitles(output_path: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Export the sequence's subtitles to a standalone .srt file."""
        session = get_state().get(project_id)
        path = sub_ops.export_srt(session.project, _seq_id(session, sequence_id), output_path)
        return tool_result(path=str(path))
