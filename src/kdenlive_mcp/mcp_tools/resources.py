"""MCP Resources: read-heavy project state exposed as resources rather than
tool calls, for context-efficient inspection (spec section 26/27). All
operate on the currently active project/sequence -- there's no
project_id/sequence_id param on a resource URI, unlike the tools.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.kdenlive.adapter.capabilities import detect_capabilities
from kdenlive_mcp.kdenlive.effects.catalog import get_default_catalog
from kdenlive_mcp.kdenlive.transitions.catalog import get_default_transition_catalog
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import project_summary, sequence_summary, track_summary


def _active_or_error() -> dict | None:
    state = get_state()
    if state.active_project_id is None:
        return {"error": "No project is open. Call create_project or open_project first."}
    return None


def register(mcp: FastMCP) -> None:

    @mcp.resource("kdenlive://project")
    def project_resource() -> str:
        """Summary of the active project: settings, sequences, dirty state."""
        err = _active_or_error()
        if err:
            return json.dumps(err)
        session = get_state().get(None)
        return json.dumps(project_summary(session.project), indent=2)

    @mcp.resource("kdenlive://timeline")
    def timeline_resource() -> str:
        """Full timeline of the active sequence: every track and clip."""
        err = _active_or_error()
        if err:
            return json.dumps(err)
        session = get_state().get(None)
        seq = session.project.active_sequence()
        if seq is None:
            return json.dumps({"error": "Project has no active sequence"})
        return json.dumps(sequence_summary(seq, session.project), indent=2)

    @mcp.resource("kdenlive://tracks")
    def tracks_resource() -> str:
        """Just the track list of the active sequence (no clip contents) --
        cheaper than kdenlive://timeline when you only need track ids/names."""
        err = _active_or_error()
        if err:
            return json.dumps(err)
        session = get_state().get(None)
        seq = session.project.active_sequence()
        if seq is None:
            return json.dumps({"error": "Project has no active sequence"})
        tracks = [
            {"id": t.id, "index": t.index, "track_type": t.track_type, "name": t.name,
             "muted": t.muted, "locked": t.locked, "solo": t.solo, "clip_count": len(t.clips)}
            for t in [*seq.video_tracks(), *seq.audio_tracks()]
        ]
        return json.dumps({"tracks": tracks}, indent=2)

    @mcp.resource("kdenlive://media")
    def media_resource() -> str:
        """The active project's media bin (every imported asset)."""
        err = _active_or_error()
        if err:
            return json.dumps(err)
        session = get_state().get(None)
        assets = [
            {"id": a.id, "path": a.path, "kind": a.kind, "duration": a.duration,
             "has_audio": a.has_audio, "has_video": a.has_video}
            for a in session.media_index.list()
        ]
        return json.dumps({"assets": assets}, indent=2)

    @mcp.resource("kdenlive://effects")
    def effects_resource() -> str:
        """Every effect actually available in this Kdenlive installation."""
        catalog = get_default_catalog()
        effects = [{"id": e.id, "tag": e.tag, "name": e.name, "category": e.category} for e in catalog.all()]
        return json.dumps({"effects": effects, "count": len(effects)}, indent=2)

    @mcp.resource("kdenlive://transitions")
    def transitions_resource() -> str:
        """Every transition actually available in this Kdenlive installation."""
        catalog = get_default_transition_catalog()
        transitions = [{"id": t.id, "tag": t.tag, "name": t.name} for t in catalog.all()]
        return json.dumps({"transitions": transitions, "count": len(transitions)}, indent=2)

    @mcp.resource("kdenlive://capabilities")
    def capabilities_resource() -> str:
        """What this Kdenlive/FFmpeg/melt install can actually do."""
        return json.dumps(detect_capabilities().to_dict(), indent=2)
