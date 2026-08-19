"""Tests for the actual MCP tools (not just underlying model primitives)
covering effect parameter get/set, generic keyframes on arbitrary effects,
and audio volume/normalize -- the acceptance-test gaps ("modify effect
parameters", "create keyframes", "modify audio levels") that were missing
before this pass.
"""

from __future__ import annotations

import pytest

from kdenlive_mcp.kdenlive.effects.catalog import get_default_catalog
from kdenlive_mcp.mcp_tools.tools import audio_tools, effects_tools, project_tools, timeline_tools

pytestmark = pytest.mark.skipif(
    not get_default_catalog().all(), reason="real Kdenlive effects catalog not available on this machine"
)


class _ToolBag:
    def __init__(self):
        self.tools = {}

    def tool(self, *_args, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.fixture()
def tools(tmp_path_home_workspace):
    from kdenlive_mcp.mcp_tools import state as state_module
    state_module._state = state_module.ServerState()

    bag = _ToolBag()
    for module in (project_tools, timeline_tools, effects_tools, audio_tools):
        module.register(bag)
    return bag.tools


@pytest.fixture()
def tmp_path_home_workspace(tmp_path, monkeypatch):
    from kdenlive_mcp import config as config_module
    monkeypatch.setenv("KDENLIVE_MCP_WORKSPACE", str(tmp_path / "workspace"))
    config_module._config = None
    try:
        yield tmp_path
    finally:
        config_module._config = None


def _project_with_color_clip(tools):
    created = tools["create_project"](name="Effect Tool Test")
    pid = created["project"]["id"]
    info = tools["get_project_info"](project_id=pid)
    track_id = info["project"]["sequences"][0]["video_tracks"][0]["id"]

    added = tools["add_clip"](track_id=track_id, position=0.0, source_in=0.0, source_out=3.0,
                               clip_type="color", name="test", project_id=pid)
    assert added["success"], added
    return pid, added["clip"]["id"]


def test_apply_effect_then_get_and_set_parameter(tools):
    pid, clip_id = _project_with_color_clip(tools)

    applied = tools["apply_effect"](clip_id=clip_id, effect_id="volume", params={"level": -10}, project_id=pid)
    assert applied["success"], applied
    effect_id = applied["effect_id"]

    got = tools["get_effect_parameter"](clip_id=clip_id, effect_id=effect_id, param_name="level", project_id=pid)
    assert got["success"], got
    assert got["value"] == -10

    updated = tools["set_effect_parameter"](clip_id=clip_id, effect_id=effect_id,
                                             param_name="level", value=-5, project_id=pid)
    assert updated["success"], updated

    got_again = tools["get_effect_parameter"](clip_id=clip_id, effect_id=effect_id,
                                               param_name="level", project_id=pid)
    assert got_again["value"] == -5


def test_generic_keyframe_tools_on_arbitrary_effect(tools):
    pid, clip_id = _project_with_color_clip(tools)

    applied = tools["apply_effect"](clip_id=clip_id, effect_id="volume", project_id=pid)
    effect_id = applied["effect_id"]

    added = tools["add_keyframe"](clip_id=clip_id, effect_id=effect_id, param_name="level",
                                   at_seconds=0.0, value=-20, easing="linear", project_id=pid)
    assert added["success"], added
    tools["add_keyframe"](clip_id=clip_id, effect_id=effect_id, param_name="level",
                           at_seconds=1.0, value=0, easing="ease_in_out", project_id=pid)

    listed = tools["list_keyframes"](clip_id=clip_id, effect_id=effect_id, param_name="level", project_id=pid)
    assert listed["success"]
    assert len(listed["keyframes"]) == 2
    assert listed["keyframes"][0]["value"] == -20
    assert listed["keyframes"][1]["easing"] == "ease_in_out"

    removed = tools["remove_keyframe"](clip_id=clip_id, effect_id=effect_id, param_name="level",
                                        at_seconds=0.0, project_id=pid)
    assert removed["success"], removed

    listed_after = tools["list_keyframes"](clip_id=clip_id, effect_id=effect_id, param_name="level", project_id=pid)
    assert len(listed_after["keyframes"]) == 1


def _volume_effect_ids(tools, clip_id, pid):
    info = tools["get_project_info"](project_id=pid)
    for seq in info["project"]["sequences"]:
        for track in seq["video_tracks"]:
            for clip in track["clips"]:
                if clip["id"] == clip_id:
                    return [e["id"] for e in clip["effects"] if e["service"] == "volume"]
    return []


def test_set_clip_volume_reuses_existing_effect(tools):
    pid, clip_id = _project_with_color_clip(tools)

    first = tools["set_clip_volume"](clip_id=clip_id, level_db=-6.0, project_id=pid)
    assert first["success"], first
    second = tools["set_clip_volume"](clip_id=clip_id, level_db=-12.0, project_id=pid)
    assert second["success"], second

    effect_ids = _volume_effect_ids(tools, clip_id, pid)
    assert len(effect_ids) == 1, "calling set_clip_volume twice should reuse the same effect, not add a second"

    got = tools["get_effect_parameter"](clip_id=clip_id, effect_id=effect_ids[0], param_name="level", project_id=pid)
    assert got["value"] == -12.0


def test_set_clip_volume_rejects_out_of_range(tools):
    pid, clip_id = _project_with_color_clip(tools)
    result = tools["set_clip_volume"](clip_id=clip_id, level_db=999.0, project_id=pid)
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_OPERATION"


def test_normalize_clip_audio(tools):
    pid, clip_id = _project_with_color_clip(tools)
    result = tools["normalize_clip_audio"](clip_id=clip_id, target_lufs=-23.0, project_id=pid)
    assert result["success"], result

    got = tools["get_effect_parameter"](clip_id=clip_id, effect_id=result["effect_id"],
                                         param_name="target_loudness", project_id=pid)
    assert got["value"] == -23.0
