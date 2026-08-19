"""TIMELINE EDITING tools (spec section 6)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, mutates, clip_summary, f2s, s2f, tool_result, track_summary


def register(mcp: FastMCP) -> None:

    def _seq_id(session, sequence_id: str | None) -> str:
        sid = sequence_id or session.project.active_sequence_id
        if sid is None:
            raise InvalidOperationError("Project has no active sequence")
        return sid

    @mcp.tool()
    @catch_errors
    @mutates
    def add_clip(track_id: str, position: float, source_in: float, source_out: float,
                 asset_id: str | None = None, clip_type: str = "video", name: str = "",
                 sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Place a clip on a track. Times are in seconds; source_in/source_out address the asset's own timeline."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.add_clip(
            p, _seq_id(session, sequence_id), track_id,
            position=s2f(position, p), in_point=s2f(source_in, p), out_point=s2f(source_out, p),
            asset_id=asset_id, clip_type=clip_type, name=name,
        )
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def remove_clip(clip_id: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Remove a clip, leaving a gap behind."""
        session = get_state().get(project_id)
        ops.remove_clip(session.project, _seq_id(session, sequence_id), clip_id)
        return tool_result(removed=clip_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def ripple_delete(clip_id: str, affect_all_tracks: bool = False,
                       sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Remove a clip and shift subsequent clips left to close the gap."""
        session = get_state().get(project_id)
        ops.ripple_delete(session.project, _seq_id(session, sequence_id), clip_id, affect_all_tracks=affect_all_tracks)
        return tool_result(removed=clip_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def ripple_insert(track_id: str, position: float, source_in: float, source_out: float,
                       asset_id: str | None = None, clip_type: str = "video", name: str = "",
                       affect_all_tracks: bool = True, sequence_id: str | None = None,
                       project_id: str | None = None) -> dict:
        """Insert a clip at a position, pushing everything after it to the right."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.ripple_insert(
            p, _seq_id(session, sequence_id), track_id, position=s2f(position, p),
            in_point=s2f(source_in, p), out_point=s2f(source_out, p),
            asset_id=asset_id, clip_type=clip_type, name=name, affect_all_tracks=affect_all_tracks,
        )
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def move_clip(clip_id: str, new_position: float, new_track_id: str | None = None,
                  move_group: bool = True, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Move a clip to a new position and/or track."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.move_clip(p, _seq_id(session, sequence_id), clip_id,
                              new_position=s2f(new_position, p), new_track_id=new_track_id, move_group=move_group)
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def trim_clip(clip_id: str, edge: str, delta_seconds: float,
                  sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Trim one edge ('start' or 'end') of a clip by delta_seconds (negative shortens)."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.trim_clip(p, _seq_id(session, sequence_id), clip_id, edge=edge, delta_frames=s2f(delta_seconds, p))
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def extend_clip(clip_id: str, seconds: float, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Extend a clip's end by `seconds`."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.extend_clip(p, _seq_id(session, sequence_id), clip_id, frames=s2f(seconds, p))
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def shorten_clip(clip_id: str, seconds: float, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Shorten a clip's end by `seconds`."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.shorten_clip(p, _seq_id(session, sequence_id), clip_id, frames=s2f(seconds, p))
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def slip_clip(clip_id: str, delta_seconds: float, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Shift which portion of the source is shown, keeping position/length fixed."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.slip_clip(p, _seq_id(session, sequence_id), clip_id, delta_frames=s2f(delta_seconds, p))
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def slide_clip(clip_id: str, delta_seconds: float, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Move a clip while trimming its immediate neighbors to absorb the shift."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.slide_clip(p, _seq_id(session, sequence_id), clip_id, delta_frames=s2f(delta_seconds, p))
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def split_clip(clip_id: str, at_seconds: float, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Split a clip into two at an absolute timeline position (seconds)."""
        session = get_state().get(project_id)
        p = session.project
        left, right = ops.split_clip(p, _seq_id(session, sequence_id), clip_id, at_frame=s2f(at_seconds, p))
        return tool_result(left=clip_summary(left, p), right=clip_summary(right, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def duplicate_clip(clip_id: str, new_position: float | None = None, new_track_id: str | None = None,
                        sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Duplicate a clip (including its effects), by default placed right after the original."""
        session = get_state().get(project_id)
        p = session.project
        new_pos_frames = s2f(new_position, p) if new_position is not None else None
        clip = ops.duplicate_clip(p, _seq_id(session, sequence_id), clip_id,
                                   new_position=new_pos_frames, new_track_id=new_track_id)
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def replace_clip(clip_id: str, new_asset_id: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Swap a clip's underlying media asset, keeping its position/length/effects."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.replace_clip(p, _seq_id(session, sequence_id), clip_id, new_asset_id=new_asset_id)
        return tool_result(clip=clip_summary(clip, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def reorder_clips(track_id: str, ordered_clip_ids: list[str],
                       sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Reorder every clip on a track back-to-back in the given order."""
        session = get_state().get(project_id)
        p = session.project
        track = ops.reorder_clips(p, _seq_id(session, sequence_id), track_id, ordered_clip_ids=ordered_clip_ids)
        return tool_result(track=track_summary(track, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_track(track_type: str, name: str = "", sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Create a new video or audio track."""
        session = get_state().get(project_id)
        p = session.project
        track = ops.create_track(p, _seq_id(session, sequence_id), track_type=track_type, name=name)
        return tool_result(track=track_summary(track, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def delete_track(track_id: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Delete an empty track. Refuses if the track still has clips."""
        session = get_state().get(project_id)
        ops.delete_track(session.project, _seq_id(session, sequence_id), track_id)
        return tool_result(deleted=track_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def mute_track(track_id: str, muted: bool = True, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Mute/unmute a track."""
        session = get_state().get(project_id)
        p = session.project
        track = ops.mute_track(p, _seq_id(session, sequence_id), track_id, muted=muted)
        return tool_result(track=track_summary(track, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def solo_track(track_id: str, solo: bool = True, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Solo/unsolo a track."""
        session = get_state().get(project_id)
        p = session.project
        track = ops.solo_track(p, _seq_id(session, sequence_id), track_id, solo=solo)
        return tool_result(track=track_summary(track, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def lock_track(track_id: str, locked: bool = True, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Lock/unlock a track against edits."""
        session = get_state().get(project_id)
        p = session.project
        track = ops.lock_track(p, _seq_id(session, sequence_id), track_id, locked=locked)
        return tool_result(track=track_summary(track, p))

    @mcp.tool()
    @catch_errors
    @mutates
    def group_clips(clip_ids: list[str], sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Group clips so they move together."""
        session = get_state().get(project_id)
        group_id = ops.group_clips(session.project, _seq_id(session, sequence_id), clip_ids)
        return tool_result(group_id=group_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def ungroup_clips(clip_ids: list[str], sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Remove clips from their group."""
        session = get_state().get(project_id)
        ops.ungroup_clips(session.project, _seq_id(session, sequence_id), clip_ids)
        return tool_result(ungrouped=clip_ids)

    @mcp.tool()
    @catch_errors
    @mutates
    def align_clips(clip_ids: list[str], mode: str = "start",
                     sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Align clips' start or end points to match the earliest/latest among them."""
        session = get_state().get(project_id)
        p = session.project
        clips = ops.align_clips(p, _seq_id(session, sequence_id), clip_ids, mode=mode)
        return tool_result(clips=[clip_summary(c, p) for c in clips])

    @mcp.tool()
    @catch_errors
    @mutates
    def snap_to_marker(clip_id: str, edge: str = "start", tolerance_seconds: float = 0.5,
                        sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Snap a clip edge to the nearest marker within tolerance."""
        session = get_state().get(project_id)
        p = session.project
        clip = ops.snap_to_marker(p, _seq_id(session, sequence_id), clip_id, edge=edge,
                                   tolerance_frames=s2f(tolerance_seconds, p))
        return tool_result(clip=clip_summary(clip, p) if clip else None, snapped=clip is not None)

    @mcp.tool()
    @catch_errors
    @mutates
    def add_marker(frame_seconds: float, name: str = "", color: str = "#00ff00", category: str = "default",
                   sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Add a timeline marker at a given time."""
        session = get_state().get(project_id)
        p = session.project
        marker = ops.add_marker(p, _seq_id(session, sequence_id), frame=s2f(frame_seconds, p),
                                 name=name, color=color, category=category)
        return tool_result(marker={"frame": f2s(marker.frame, p), "name": marker.name, "category": marker.category})
