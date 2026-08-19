"""EFFECTS tools (spec section 10)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.effects.model import create_effect
from kdenlive_mcp.core.effects.presets import build_effect_stack, list_presets
from kdenlive_mcp.core.keyframes.motion import resolve_easing
from kdenlive_mcp.errors import ClipNotFoundError, InvalidOperationError
from kdenlive_mcp.kdenlive.effects.catalog import get_default_catalog
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, f2s, mutates, s2f, tool_result


def _find_clip(session, sequence_id):
    project = session.project
    sid = sequence_id or project.active_sequence_id
    seq = project.get_sequence(sid) if sid else None
    if seq is None:
        raise InvalidOperationError("Project has no active sequence")
    return project, seq


def _get_clip_and_effect(session, sequence_id, clip_id, effect_id):
    project, seq = _find_clip(session, sequence_id)
    found = seq.get_clip(clip_id)
    if found is None:
        raise ClipNotFoundError(f"Clip not found: {clip_id}")
    _, clip = found
    effect = next((e for e in clip.effects if e.id == effect_id), None)
    if effect is None:
        raise InvalidOperationError(f"Effect not found on clip '{clip_id}': {effect_id}")
    return project, clip, effect


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def list_available_effects(query: str = "") -> dict:
        """List effects actually available in this Kdenlive installation, optionally filtered by a search term."""
        catalog = get_default_catalog()
        effects = catalog.search(query) if query else catalog.all()
        return tool_result(count=len(effects), effects=[
            {"id": e.id, "tag": e.tag, "name": e.name, "category": e.category, "is_audio": e.is_audio}
            for e in effects
        ])

    @mcp.tool()
    @catch_errors
    def list_effect_presets() -> dict:
        """List the named professional effect-stack presets (cinematic, punchy, vintage, ...)."""
        return tool_result(presets=list_presets())

    @mcp.tool()
    @catch_errors
    @mutates
    def apply_effect(clip_id: str, effect_id: str, params: dict | None = None,
                      sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Apply a single effect (by its Kdenlive effect id) to a clip, with optional param overrides."""
        session = get_state().get(project_id)
        project, seq = _find_clip(session, sequence_id)
        found = seq.get_clip(clip_id)
        if found is None:
            raise ClipNotFoundError(f"Clip not found: {clip_id}")
        _, clip = found
        effect = create_effect(effect_id, params=params)
        effect.index = clip.next_effect_index()
        clip.effects.append(effect)
        project.dirty = True
        return tool_result(effect_id=effect.id, service=effect.service)

    @mcp.tool()
    @catch_errors
    @mutates
    def apply_effect_preset(clip_id: str, preset_name: str,
                             sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Apply a named effect-stack preset (cinematic, punchy, vintage, ...) to a clip."""
        session = get_state().get(project_id)
        project, seq = _find_clip(session, sequence_id)
        found = seq.get_clip(clip_id)
        if found is None:
            raise ClipNotFoundError(f"Clip not found: {clip_id}")
        _, clip = found
        stack = build_effect_stack(preset_name)
        for effect in stack:
            effect.index = clip.next_effect_index()
            clip.effects.append(effect)
        project.dirty = True
        return tool_result(preset=preset_name, effect_count=len(stack),
                            effect_ids=[e.id for e in stack])

    @mcp.tool()
    @catch_errors
    @mutates
    def remove_effect(clip_id: str, effect_id: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Remove one effect from a clip by its effect id."""
        session = get_state().get(project_id)
        project, seq = _find_clip(session, sequence_id)
        found = seq.get_clip(clip_id)
        if found is None:
            raise ClipNotFoundError(f"Clip not found: {clip_id}")
        _, clip = found
        before = len(clip.effects)
        clip.effects = [e for e in clip.effects if e.id != effect_id]
        if len(clip.effects) == before:
            raise InvalidOperationError(f"Effect not found on clip: {effect_id}")
        project.dirty = True
        return tool_result(removed=effect_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def set_effect_enabled(clip_id: str, effect_id: str, enabled: bool = True,
                            sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Enable/disable one effect on a clip without removing it."""
        session = get_state().get(project_id)
        project, seq = _find_clip(session, sequence_id)
        found = seq.get_clip(clip_id)
        if found is None:
            raise ClipNotFoundError(f"Clip not found: {clip_id}")
        _, clip = found
        for e in clip.effects:
            if e.id == effect_id:
                e.enabled = enabled
                project.dirty = True
                return tool_result(effect_id=effect_id, enabled=enabled)
        raise InvalidOperationError(f"Effect not found on clip: {effect_id}")

    @mcp.tool()
    @catch_errors
    def get_effect_parameters(clip_id: str, effect_id: str, sequence_id: str | None = None,
                               project_id: str | None = None) -> dict:
        """Get every parameter (static and keyframed) currently set on an applied effect."""
        session = get_state().get(project_id)
        project, clip, effect = _get_clip_and_effect(session, sequence_id, clip_id, effect_id)
        return tool_result(
            service=effect.service, enabled=effect.enabled,
            static_params=dict(effect.params),
            keyframed_params={
                name: [
                    {"time": f2s(kf.frame, project), "value": kf.value, "easing": kf.easing.value}
                    for kf in track.keyframes
                ]
                for name, track in effect.keyframed_params.items()
            },
        )

    @mcp.tool()
    @catch_errors
    def get_effect_parameter(clip_id: str, effect_id: str, param_name: str,
                              sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Get a single static parameter's current value from an applied effect."""
        session = get_state().get(project_id)
        _, _, effect = _get_clip_and_effect(session, sequence_id, clip_id, effect_id)
        if param_name in effect.keyframed_params:
            raise InvalidOperationError(
                f"'{param_name}' is keyframed, not a static value",
                suggestion="Use list_keyframes to inspect it instead.",
            )
        if param_name not in effect.params:
            raise InvalidOperationError(f"Parameter '{param_name}' is not set on this effect")
        return tool_result(param_name=param_name, value=effect.params[param_name])

    @mcp.tool()
    @catch_errors
    @mutates
    def set_effect_parameter(clip_id: str, effect_id: str, param_name: str, value,
                              sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Set a single static parameter on an already-applied effect. For an
        animated parameter, use add_keyframe instead."""
        session = get_state().get(project_id)
        project, clip, effect = _get_clip_and_effect(session, sequence_id, clip_id, effect_id)
        if param_name in effect.keyframed_params:
            raise InvalidOperationError(
                f"'{param_name}' is keyframed on this effect",
                suggestion="Use add_keyframe to change an animated parameter's values.",
            )
        effect.params[param_name] = value
        project.dirty = True
        return tool_result(param_name=param_name, value=value)

    @mcp.tool()
    @catch_errors
    @mutates
    def add_keyframe(clip_id: str, effect_id: str, param_name: str, at_seconds: float, value,
                      easing: str = "linear", sequence_id: str | None = None,
                      project_id: str | None = None) -> dict:
        """Add (or overwrite) a keyframe on any effect parameter, not just motion/transform.
        `value` may be a number or a list of numbers (e.g. for a multi-component param)."""
        session = get_state().get(project_id)
        project, clip, effect = _get_clip_and_effect(session, sequence_id, clip_id, effect_id)
        frame_value = tuple(value) if isinstance(value, list) else value
        effect.set_keyframe(param_name, s2f(at_seconds, project), frame_value, resolve_easing(easing))
        project.dirty = True
        return tool_result(param_name=param_name, time=at_seconds, value=value)

    @mcp.tool()
    @catch_errors
    @mutates
    def remove_keyframe(clip_id: str, effect_id: str, param_name: str, at_seconds: float,
                         sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Remove the keyframe at exactly at_seconds on an effect parameter."""
        session = get_state().get(project_id)
        project, clip, effect = _get_clip_and_effect(session, sequence_id, clip_id, effect_id)
        track = effect.keyframed_params.get(param_name)
        if track is None:
            raise InvalidOperationError(f"Parameter '{param_name}' has no keyframes")
        removed = track.remove(s2f(at_seconds, project))
        if not removed:
            raise InvalidOperationError(f"No keyframe at {at_seconds}s on '{param_name}'")
        project.dirty = True
        return tool_result(removed_at=at_seconds)

    @mcp.tool()
    @catch_errors
    def list_keyframes(clip_id: str, effect_id: str, param_name: str,
                        sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """List every keyframe on one effect parameter, in time order."""
        session = get_state().get(project_id)
        project, clip, effect = _get_clip_and_effect(session, sequence_id, clip_id, effect_id)
        track = effect.keyframed_params.get(param_name)
        if track is None:
            return tool_result(keyframes=[])
        return tool_result(keyframes=[
            {"time": f2s(kf.frame, project), "value": kf.value, "easing": kf.easing.value}
            for kf in sorted(track.keyframes, key=lambda k: k.frame)
        ])
