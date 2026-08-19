"""Lossless JSON (de)serialization of the internal timeline model.

Used by the snapshot system, where round-tripping through `.kdenlive` XML
would be lossy (many of our own fields -- group ids, per-clip metadata,
easing curves before they get flattened to linear keyframes -- have no XML
representation). `dataclasses.asdict` isn't used directly because it loses
the distinction between tuples and lists and doesn't resolve Easing back
from its string value, both of which matter for exact round-tripping.
"""

from __future__ import annotations

from typing import Any

from kdenlive_mcp.core.timeline.model import (
    Clip, Easing, EffectInstance, Keyframe, KeyframeTrack, Marker, Project,
    ProjectSettings, Sequence, Track, TransitionInstance, new_id,
)


def _value_to_dict(value: Any) -> Any:
    if isinstance(value, tuple):
        return {"__tuple__": True, "items": [_value_to_dict(v) for v in value]}
    if isinstance(value, list):
        return [_value_to_dict(v) for v in value]
    return value


def _value_from_dict(value: Any) -> Any:
    if isinstance(value, dict) and value.get("__tuple__"):
        return tuple(_value_from_dict(v) for v in value["items"])
    if isinstance(value, list):
        return [_value_from_dict(v) for v in value]
    return value


def _keyframe_to_dict(kf: Keyframe) -> dict:
    return {
        "frame": kf.frame, "value": _value_to_dict(kf.value), "easing": kf.easing.value,
        "bezier_handles": list(kf.bezier_handles) if kf.bezier_handles else None,
    }


def _keyframe_from_dict(d: dict) -> Keyframe:
    return Keyframe(
        frame=d["frame"], value=_value_from_dict(d["value"]), easing=Easing(d["easing"]),
        bezier_handles=tuple(d["bezier_handles"]) if d.get("bezier_handles") else None,
    )


def _kf_track_to_dict(t: KeyframeTrack) -> dict:
    return {"param_name": t.param_name, "keyframes": [_keyframe_to_dict(k) for k in t.keyframes]}


def _kf_track_from_dict(d: dict) -> KeyframeTrack:
    return KeyframeTrack(param_name=d["param_name"], keyframes=[_keyframe_from_dict(k) for k in d["keyframes"]])


def _effect_to_dict(e: EffectInstance) -> dict:
    return {
        "id": e.id, "service": e.service, "display_name": e.display_name,
        "params": {k: _value_to_dict(v) for k, v in e.params.items()},
        "keyframed_params": {k: _kf_track_to_dict(v) for k, v in e.keyframed_params.items()},
        "enabled": e.enabled, "index": e.index,
    }


def _effect_from_dict(d: dict) -> EffectInstance:
    return EffectInstance(
        id=d["id"], service=d["service"], display_name=d["display_name"],
        params={k: _value_from_dict(v) for k, v in d.get("params", {}).items()},
        keyframed_params={k: _kf_track_from_dict(v) for k, v in d.get("keyframed_params", {}).items()},
        enabled=d.get("enabled", True), index=d.get("index", 0),
    )


def _transition_to_dict(t: TransitionInstance) -> dict:
    return {
        "id": t.id, "service": t.service, "position": t.position, "duration": t.duration,
        "a_track": t.a_track, "b_track": t.b_track, "clip_a_id": t.clip_a_id, "clip_b_id": t.clip_b_id,
        "params": {k: _value_to_dict(v) for k, v in t.params.items()}, "easing": t.easing.value,
    }


def _transition_from_dict(d: dict) -> TransitionInstance:
    return TransitionInstance(
        id=d["id"], service=d["service"], position=d["position"], duration=d["duration"],
        a_track=d.get("a_track"), b_track=d.get("b_track"),
        clip_a_id=d.get("clip_a_id"), clip_b_id=d.get("clip_b_id"),
        params={k: _value_from_dict(v) for k, v in d.get("params", {}).items()},
        easing=Easing(d.get("easing", "linear")),
    )


def _clip_to_dict(c: Clip) -> dict:
    return {
        "id": c.id, "track_id": c.track_id, "clip_type": c.clip_type, "position": c.position,
        "in_point": c.in_point, "out_point": c.out_point, "asset_id": c.asset_id, "speed": c.speed,
        "reversed": c.reversed, "name": c.name, "effects": [_effect_to_dict(e) for e in c.effects],
        "group_id": c.group_id, "text_content": c.text_content, "color": c.color, "locked": c.locked,
        "fade_in": c.fade_in, "fade_out": c.fade_out, "metadata": c.metadata,
    }


def _clip_from_dict(d: dict) -> Clip:
    return Clip(
        id=d["id"], track_id=d["track_id"], clip_type=d["clip_type"], position=d["position"],
        in_point=d["in_point"], out_point=d["out_point"], asset_id=d.get("asset_id"),
        speed=d.get("speed", 1.0), reversed=d.get("reversed", False), name=d.get("name", ""),
        effects=[_effect_from_dict(e) for e in d.get("effects", [])],
        group_id=d.get("group_id"), text_content=d.get("text_content"), color=d.get("color"),
        locked=d.get("locked", False), fade_in=d.get("fade_in", 0), fade_out=d.get("fade_out", 0),
        metadata=d.get("metadata", {}),
    )


def _track_to_dict(t: Track) -> dict:
    return {
        "id": t.id, "index": t.index, "track_type": t.track_type, "name": t.name,
        "clips": [_clip_to_dict(c) for c in t.clips], "muted": t.muted, "locked": t.locked,
        "solo": t.solo, "hidden": t.hidden, "height": t.height,
    }


def _track_from_dict(d: dict) -> Track:
    return Track(
        id=d["id"], index=d["index"], track_type=d["track_type"], name=d.get("name", ""),
        clips=[_clip_from_dict(c) for c in d.get("clips", [])], muted=d.get("muted", False),
        locked=d.get("locked", False), solo=d.get("solo", False), hidden=d.get("hidden", False),
        height=d.get("height", 75),
    )


def _marker_to_dict(m: Marker) -> dict:
    return {"id": m.id, "frame": m.frame, "name": m.name, "color": m.color, "category": m.category}


def _marker_from_dict(d: dict) -> Marker:
    return Marker(id=d.get("id") or new_id("marker"), frame=d["frame"], name=d.get("name", ""),
                  color=d.get("color", "#00ff00"), category=d.get("category", "default"))


def _sequence_to_dict(s: Sequence) -> dict:
    return {
        "id": s.id, "name": s.name, "tracks": [_track_to_dict(t) for t in s.tracks],
        "transitions": [_transition_to_dict(t) for t in s.transitions],
        "markers": [_marker_to_dict(m) for m in s.markers],
    }


def _sequence_from_dict(d: dict) -> Sequence:
    return Sequence(
        id=d["id"], name=d.get("name", "Sequence 1"), tracks=[_track_from_dict(t) for t in d.get("tracks", [])],
        transitions=[_transition_from_dict(t) for t in d.get("transitions", [])],
        markers=[_marker_from_dict(m) for m in d.get("markers", [])],
    )


def _settings_to_dict(s: ProjectSettings) -> dict:
    return {
        "width": s.width, "height": s.height, "fps_num": s.fps_num, "fps_den": s.fps_den,
        "sample_rate": s.sample_rate, "audio_channels": s.audio_channels, "colorspace": s.colorspace,
        "progressive": s.progressive, "display_aspect_num": s.display_aspect_num,
        "display_aspect_den": s.display_aspect_den, "sample_aspect_num": s.sample_aspect_num,
        "sample_aspect_den": s.sample_aspect_den,
    }


def _settings_from_dict(d: dict) -> ProjectSettings:
    return ProjectSettings(**d)


def project_to_dict(p: Project) -> dict:
    return {
        "id": p.id, "name": p.name, "path": p.path, "settings": _settings_to_dict(p.settings),
        "sequences": [_sequence_to_dict(s) for s in p.sequences], "active_sequence_id": p.active_sequence_id,
        "metadata": p.metadata, "dirty": p.dirty,
    }


def project_from_dict(d: dict) -> Project:
    return Project(
        id=d["id"], name=d["name"], path=d.get("path"), settings=_settings_from_dict(d["settings"]),
        sequences=[_sequence_from_dict(s) for s in d.get("sequences", [])],
        active_sequence_id=d.get("active_sequence_id"), metadata=d.get("metadata", {}),
        dirty=d.get("dirty", False),
    )
