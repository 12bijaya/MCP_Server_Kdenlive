"""TRANSITIONS tools (spec section 9).

Transitions here bridge two clips that already overlap in time on two
different tracks (the standard, well-supported MLT pattern) -- arrange the
overlap first with the timeline tools (move_clip/trim_clip), then call
add_transition. Same-track "mix" transitions (Kdenlive's newer overlapping-
clips-on-one-track feature) are not implemented; that's a much more
involved on-disk representation and out of scope for this pass.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.timeline.model import Easing
from kdenlive_mcp.core.transitions import model as transitions
from kdenlive_mcp.errors import ClipNotFoundError, InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, mutates, s2f, tool_result


def _resolve_easing(name: str) -> Easing:
    from kdenlive_mcp.core.keyframes.motion import resolve_easing
    return resolve_easing(name)


_BUILDERS = {
    "crossfade": transitions.crossfade,
    "zoom": transitions.zoom_transition,
    "whip": transitions.whip_transition,
    "slide": transitions.slide_transition,
    "push": transitions.push_transition,
    "blur": transitions.blur_transition,
    "flash": transitions.flash_transition,
    "glitch": transitions.glitch_transition,
    "distortion": transitions.distortion_transition,
    "directional": transitions.directional_transition,
}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def list_transition_types() -> dict:
        """List the built-in transition style names usable with add_transition."""
        return tool_result(types=sorted(_BUILDERS) + ["dip_to_black", "dip_to_white", "hard_cut"])

    @mcp.tool()
    @catch_errors
    @mutates
    def add_transition(clip_a_id: str, clip_b_id: str, transition_type: str,
                        position_seconds: float, duration_seconds: float,
                        direction: str = "left", intensity: str = "medium", easing: str = "linear",
                        sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Add a transition spanning [position_seconds, position_seconds+duration_seconds)
        between two clips that already overlap there on two different tracks.

        transition_type: crossfade, zoom, whip, slide, push, blur, flash, glitch,
        distortion, directional, dip_to_black, dip_to_white, or hard_cut (no-op).
        """
        session = get_state().get(project_id)
        project = session.project
        sid = sequence_id or project.active_sequence_id
        seq = project.get_sequence(sid) if sid else None
        if seq is None:
            raise InvalidOperationError("Project has no active sequence")
        if seq.get_clip(clip_a_id) is None:
            raise ClipNotFoundError(f"Clip not found: {clip_a_id}")
        if seq.get_clip(clip_b_id) is None:
            raise ClipNotFoundError(f"Clip not found: {clip_b_id}")

        position = s2f(position_seconds, project)
        duration = s2f(duration_seconds, project)

        if transition_type == "hard_cut":
            return tool_result(created=[])

        if transition_type in ("dip_to_black", "dip_to_white"):
            fn = transitions.dip_to_black if transition_type == "dip_to_black" else transitions.dip_to_white
            built = fn(position=position, duration=duration, easing=_resolve_easing(easing))
        elif transition_type in ("slide", "push"):
            built = [_BUILDERS[transition_type](position=position, duration=duration, direction=direction)]
        elif transition_type == "directional":
            built = [transitions.directional_transition(direction, position=position, duration=duration)]
        elif transition_type == "whip":
            built = [transitions.whip_transition(position=position, duration=duration, direction=direction,
                                                   easing=_resolve_easing(easing))]
        elif transition_type == "zoom":
            direction_in_out = direction if direction in ("in", "out") else "in"
            built = [transitions.zoom_transition(position=position, duration=duration, direction=direction_in_out,
                                                   easing=_resolve_easing(easing))]
        elif transition_type == "glitch":
            built = [transitions.glitch_transition(position=position, duration=duration, intensity=intensity)]
        elif transition_type in _BUILDERS:
            built = [_BUILDERS[transition_type](position=position, duration=duration, easing=_resolve_easing(easing))]
        else:
            raise InvalidOperationError(f"Unknown transition_type: {transition_type}",
                                         suggestion=f"Use one of: {sorted(_BUILDERS)} or dip_to_black/dip_to_white/hard_cut")

        for t in built:
            t.clip_a_id = clip_a_id
            t.clip_b_id = clip_b_id
            seq.transitions.append(t)
        project.dirty = True
        return tool_result(created=[t.id for t in built], service=[t.service for t in built])
