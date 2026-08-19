"""Shared helpers for MCP tool implementations: error handling and the
frame<->second translation at the tool boundary (the internal model is
frame-exact; tool inputs/outputs are seconds, which is what an AI agent
and a human both think in).
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from kdenlive_mcp.core.timeline.model import Clip, Project, Sequence, Track
from kdenlive_mcp.core.timeline.timecode import frames_to_seconds, seconds_to_frames
from kdenlive_mcp.errors import KdenliveMcpError


def tool_result(**kwargs) -> dict[str, Any]:
    return {"success": True, **kwargs}


def _error_dict(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, KdenliveMcpError):
        return exc.to_dict()
    return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}


def catch_errors(fn: Callable) -> Callable:
    """Wraps a tool function so it never raises: KdenliveMcpError and any
    other exception both become a structured {"success": False, "error"}
    dict instead. Handles both sync and async (e.g. execute_batch, which
    awaits mcp.call_tool) tool functions -- wrapping an async def with a
    plain sync wrapper would just hand back an un-awaited coroutine object
    instead of running it.
    """
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - last-resort guard so tools never crash the server
                return _error_dict(exc)
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - last-resort guard so tools never crash the server
            return _error_dict(exc)
    return wrapper


def mutates(fn: Callable) -> Callable:
    """Marks a tool as state-changing: pushes an undo checkpoint of the
    target project before the wrapped function runs. Apply this to every
    tool that edits a Project in place (timeline/motion/effects/
    transitions/audio edits), never to read-only tools. Order matters:
    place it *inside* @catch_errors (i.e. `@mcp.tool() @catch_errors
    @mutates def ...`) so a failed op still leaves a harmless checkpoint
    rather than crashing before catch_errors can convert the error.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from kdenlive_mcp.mcp_tools.state import get_state
        state = get_state()
        pid = kwargs.get("project_id") or state.active_project_id
        if pid:
            state.checkpoint(pid)
        return fn(*args, **kwargs)
    return wrapper


def s2f(seconds: float, project: Project) -> int:
    return seconds_to_frames(seconds, project.settings.fps)


def f2s(frames: int, project: Project) -> float:
    return round(frames_to_seconds(frames, project.settings.fps), 4)


def clip_summary(clip: Clip, project: Project) -> dict[str, Any]:
    return {
        "id": clip.id, "track_id": clip.track_id, "clip_type": clip.clip_type,
        "name": clip.name, "asset_id": clip.asset_id,
        "start": f2s(clip.position, project), "end": f2s(clip.end, project),
        "duration": f2s(clip.timeline_length, project),
        "source_in": f2s(clip.in_point, project), "source_out": f2s(clip.out_point, project),
        "speed": clip.speed, "reversed": clip.reversed,
        "group_id": clip.group_id, "locked": clip.locked,
        "effect_count": len(clip.effects),
        "effects": [{"id": e.id, "service": e.service, "enabled": e.enabled} for e in clip.effects],
    }


def track_summary(track: Track, project: Project) -> dict[str, Any]:
    return {
        "id": track.id, "index": track.index, "track_type": track.track_type, "name": track.name,
        "muted": track.muted, "locked": track.locked, "solo": track.solo,
        "clip_count": len(track.clips), "duration": f2s(track.duration(), project),
        "clips": [clip_summary(c, project) for c in track.sorted_clips()],
    }


def sequence_summary(seq: Sequence, project: Project) -> dict[str, Any]:
    return {
        "id": seq.id, "name": seq.name, "duration": f2s(seq.duration(), project),
        "video_tracks": [track_summary(t, project) for t in seq.video_tracks()],
        "audio_tracks": [track_summary(t, project) for t in seq.audio_tracks()],
        "marker_count": len(seq.markers),
    }


def project_summary(project: Project) -> dict[str, Any]:
    return {
        "id": project.id, "name": project.name, "path": project.path, "dirty": project.dirty,
        "resolution": f"{project.settings.width}x{project.settings.height}",
        "fps": project.settings.fps_float,
        "sequences": [{"id": s.id, "name": s.name, "duration": f2s(s.duration(), project)} for s in project.sequences],
        "active_sequence_id": project.active_sequence_id,
    }
