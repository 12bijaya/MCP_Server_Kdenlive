"""Generic property interface (spec section 17): get_property/set_property/
list_properties work uniformly across object types, as a forward-compatible
escape hatch alongside the strongly-typed tools -- so the AI isn't blocked
when a new capability needs a property this codebase hasn't grown a
dedicated tool for yet. Each object type exposes an explicit allowlist of
safe scalar properties, not free-form attribute access: this is a
convenience layer over the real model, not a way to reach into internal
fields (ids, effect stacks, keyframe tracks -- those have their own typed
tools already).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, f2s, mutates, s2f, tool_result

# object_type -> {property_name: (getter, setter_or_None)}
# setter is None for read-only properties (e.g. derived/computed values).
_OBJECT_TYPES = ("project", "sequence", "track", "clip", "effect")


def _resolve_object(session, object_type: str, object_id: str, sequence_id: str | None):
    project = session.project
    if object_type == "project":
        return project
    if object_type == "sequence":
        seq = project.get_sequence(object_id)
        if seq is None:
            raise InvalidOperationError(f"Sequence not found: {object_id}")
        return seq

    # track/clip/effect all live inside some sequence -- default to the
    # active one, or the caller can target a specific sequence_id.
    sid = sequence_id or project.active_sequence_id
    seq = project.get_sequence(sid) if sid else None
    if seq is None:
        raise InvalidOperationError("Project has no active sequence")
    if object_type == "track":
        track = seq.get_track(object_id)
        if track is None:
            raise InvalidOperationError(f"Track not found: {object_id}")
        return track
    if object_type == "clip":
        found = seq.get_clip(object_id)
        if found is None:
            raise InvalidOperationError(f"Clip not found: {object_id}")
        return found[1]
    if object_type == "effect":
        for track in seq.tracks:
            for clip in track.clips:
                for effect in clip.effects:
                    if effect.id == object_id:
                        return effect
        raise InvalidOperationError(f"Effect not found: {object_id}")
    raise InvalidOperationError(f"Unknown object_type '{object_type}'", suggestion=f"Use one of: {_OBJECT_TYPES}")


def _properties_for(obj, project) -> dict[str, tuple]:
    """Returns {name: (value, is_writable)} for whatever `obj` is."""
    from kdenlive_mcp.core.timeline.model import Clip, EffectInstance, Project, Sequence, Track

    if isinstance(obj, Project):
        return {"name": (obj.name, True), "path": (obj.path, False), "dirty": (obj.dirty, False)}
    if isinstance(obj, Sequence):
        return {"name": (obj.name, True), "duration": (f2s(obj.duration(), project), False)}
    if isinstance(obj, Track):
        return {
            "name": (obj.name, True), "muted": (obj.muted, True), "locked": (obj.locked, True),
            "solo": (obj.solo, True), "height": (obj.height, True), "track_type": (obj.track_type, False),
        }
    if isinstance(obj, Clip):
        return {
            "name": (obj.name, True), "speed": (obj.speed, True), "reversed": (obj.reversed, True),
            "locked": (obj.locked, True), "color": (obj.color, True), "text_content": (obj.text_content, True),
            "fade_in": (f2s(obj.fade_in, project), True), "fade_out": (f2s(obj.fade_out, project), True),
            "position": (f2s(obj.position, project), False), "duration": (f2s(obj.timeline_length, project), False),
            "clip_type": (obj.clip_type, False), "group_id": (obj.group_id, False),
        }
    if isinstance(obj, EffectInstance):
        return {"enabled": (obj.enabled, True), "index": (obj.index, True), "service": (obj.service, False)}
    raise InvalidOperationError(f"No properties defined for object of type {type(obj).__name__}")


def _set_property(obj, name: str, value, project) -> None:
    from kdenlive_mcp.core.timeline.model import Clip

    if isinstance(obj, Clip) and name in ("fade_in", "fade_out"):
        setattr(obj, name, s2f(value, project))
    else:
        setattr(obj, name, value)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def list_properties(object_type: str, object_id: str, sequence_id: str | None = None,
                         project_id: str | None = None) -> dict:
        """List every readable property (and whether it's writable) on a project/sequence/track/clip/effect."""
        session = get_state().get(project_id)
        obj = _resolve_object(session, object_type, object_id, sequence_id)
        props = _properties_for(obj, session.project)
        return tool_result(properties={
            name: {"value": value, "writable": writable} for name, (value, writable) in props.items()
        })

    @mcp.tool()
    @catch_errors
    def get_property(object_type: str, object_id: str, property_name: str, sequence_id: str | None = None,
                      project_id: str | None = None) -> dict:
        """Get one property's value from any project/sequence/track/clip/effect by id."""
        session = get_state().get(project_id)
        obj = _resolve_object(session, object_type, object_id, sequence_id)
        props = _properties_for(obj, session.project)
        if property_name not in props:
            raise InvalidOperationError(
                f"'{object_type}' has no property '{property_name}'",
                suggestion=f"Available: {sorted(props)}",
            )
        value, _ = props[property_name]
        return tool_result(value=value)

    @mcp.tool()
    @catch_errors
    @mutates
    def set_property(object_type: str, object_id: str, property_name: str, value,
                      sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Set one property's value on any project/sequence/track/clip/effect by id."""
        session = get_state().get(project_id)
        obj = _resolve_object(session, object_type, object_id, sequence_id)
        props = _properties_for(obj, session.project)
        if property_name not in props:
            raise InvalidOperationError(
                f"'{object_type}' has no property '{property_name}'",
                suggestion=f"Available: {sorted(props)}",
            )
        _, writable = props[property_name]
        if not writable:
            raise InvalidOperationError(f"Property '{property_name}' on '{object_type}' is read-only")
        _set_property(obj, property_name, value, session.project)
        session.project.dirty = True
        return tool_result(object_type=object_type, object_id=object_id, property_name=property_name, value=value)
