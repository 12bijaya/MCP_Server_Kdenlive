"""EFFECTS tools (spec section 10)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.effects.model import create_effect
from kdenlive_mcp.core.effects.presets import build_effect_stack, list_presets
from kdenlive_mcp.errors import ClipNotFoundError, InvalidOperationError
from kdenlive_mcp.kdenlive.effects.catalog import get_default_catalog
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, mutates, tool_result


def _find_clip(session, sequence_id):
    project = session.project
    sid = sequence_id or project.active_sequence_id
    seq = project.get_sequence(sid) if sid else None
    if seq is None:
        raise InvalidOperationError("Project has no active sequence")
    return project, seq


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
