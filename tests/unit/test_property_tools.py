"""Tests for the generic get_property/set_property/list_properties interface
(spec section 17) -- a forward-compatible escape hatch across object types,
driven through the actual registered MCP tools.
"""

from __future__ import annotations

import pytest

from kdenlive_mcp.mcp_tools.state import ServerState
from kdenlive_mcp.mcp_tools.tools import project_tools, property_tools, timeline_tools


class _ToolBag:
    def __init__(self):
        self.tools = {}

    def tool(self, *_args, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.fixture()
def tools(tmp_path, monkeypatch):
    from kdenlive_mcp import config as config_module
    from kdenlive_mcp.mcp_tools import state as state_module

    monkeypatch.setenv("KDENLIVE_MCP_WORKSPACE", str(tmp_path / "workspace"))
    config_module._config = None
    state_module._state = ServerState()

    bag = _ToolBag()
    for module in (project_tools, timeline_tools, property_tools):
        module.register(bag)
    try:
        yield bag.tools
    finally:
        config_module._config = None


def _setup(tools):
    created = tools["create_project"](name="Property Test")
    pid = created["project"]["id"]
    info = tools["get_project_info"](project_id=pid)
    track_id = info["project"]["sequences"][0]["video_tracks"][0]["id"]
    added = tools["add_clip"](track_id=track_id, position=0.0, source_in=0.0, source_out=2.0,
                               clip_type="color", name="orig", project_id=pid)
    return pid, track_id, added["clip"]["id"]


def test_list_properties_on_clip(tools):
    pid, _, clip_id = _setup(tools)
    result = tools["list_properties"](object_type="clip", object_id=clip_id, project_id=pid)
    assert result["success"], result
    assert result["properties"]["name"]["value"] == "orig"
    assert result["properties"]["name"]["writable"] is True
    assert result["properties"]["clip_type"]["writable"] is False


def test_get_and_set_property_on_clip(tools):
    pid, _, clip_id = _setup(tools)

    got = tools["get_property"](object_type="clip", object_id=clip_id, property_name="name", project_id=pid)
    assert got["value"] == "orig"

    updated = tools["set_property"](object_type="clip", object_id=clip_id, property_name="name",
                                     value="renamed", project_id=pid)
    assert updated["success"], updated

    got_again = tools["get_property"](object_type="clip", object_id=clip_id, property_name="name", project_id=pid)
    assert got_again["value"] == "renamed"


def test_set_property_rejects_read_only(tools):
    pid, _, clip_id = _setup(tools)
    result = tools["set_property"](object_type="clip", object_id=clip_id,
                                    property_name="clip_type", value="audio", project_id=pid)
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_OPERATION"


def test_set_property_rejects_unknown_property(tools):
    pid, _, clip_id = _setup(tools)
    result = tools["set_property"](object_type="clip", object_id=clip_id,
                                    property_name="not_a_real_property", value=1, project_id=pid)
    assert result["success"] is False


def test_properties_on_track_and_project(tools):
    pid, track_id, _ = _setup(tools)

    track_result = tools["set_property"](object_type="track", object_id=track_id,
                                          property_name="muted", value=True, project_id=pid)
    assert track_result["success"], track_result
    got = tools["get_property"](object_type="track", object_id=track_id, property_name="muted", project_id=pid)
    assert got["value"] is True

    project_result = tools["set_property"](object_type="project", object_id=pid,
                                            property_name="name", value="Renamed Project", project_id=pid)
    assert project_result["success"], project_result
    got_name = tools["get_property"](object_type="project", object_id=pid, property_name="name", project_id=pid)
    assert got_name["value"] == "Renamed Project"


def test_unknown_object_type_is_rejected(tools):
    pid, _, clip_id = _setup(tools)
    result = tools["get_property"](object_type="not_a_type", object_id=clip_id,
                                    property_name="name", project_id=pid)
    assert result["success"] is False
