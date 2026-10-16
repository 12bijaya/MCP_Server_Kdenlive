from __future__ import annotations

import json

import pytest

from kdenlive_mcp.server import build_server


@pytest.fixture()
def mcp(tmp_path, monkeypatch):
    from kdenlive_mcp import config as config_module
    from kdenlive_mcp.mcp_tools import state as state_module

    monkeypatch.setenv("KDENLIVE_MCP_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("KDENLIVE_MCP_SNAPSHOTS", str(tmp_path / "snapshots"))
    config_module._config = None
    state_module._state = state_module.ServerState()
    try:
        yield build_server()
    finally:
        config_module._config = None


async def _call(mcp, tool_name, **args):
    raw = await mcp.call_tool(tool_name, args)
    return json.loads(raw[0].text)


@pytest.mark.asyncio
async def test_snapshot_create_list_compare_restore(mcp):
    pid = (await _call(mcp, "create_project", name="Snap Test"))["project"]["id"]
    info = await _call(mcp, "get_project_info", project_id=pid)
    track_id = info["project"]["sequences"][0]["video_tracks"][0]["id"]

    empty = await _call(mcp, "create_snapshot", label="empty", project_id=pid)
    assert empty["success"], empty

    added = await _call(mcp, "add_clip", project_id=pid, track_id=track_id, position=0.0,
                        source_in=0.0, source_out=2.0, clip_type="color")
    assert added["success"], added
    with_clip = await _call(mcp, "create_snapshot", label="with clip", project_id=pid)

    listed = await _call(mcp, "list_snapshots", project_id=pid)
    assert [s["label"] for s in listed["snapshots"]] == ["empty", "with clip"]

    diff = await _call(mcp, "compare_snapshots", project_id=pid,
                       snapshot_id_a=empty["snapshot"]["id"], snapshot_id_b=with_clip["snapshot"]["id"])
    assert len(diff["diff"]["clips_added"]) == 1
    assert diff["diff"]["clips_removed"] == []

    restored = await _call(mcp, "restore_snapshot", project_id=pid, snapshot_id=empty["snapshot"]["id"])
    assert restored["success"], restored
    info_after = await _call(mcp, "get_project_info", project_id=pid)
    assert info_after["project"]["sequences"][0]["video_tracks"][0]["clips"] == []


@pytest.mark.asyncio
async def test_restore_unknown_snapshot_fails_cleanly(mcp):
    pid = (await _call(mcp, "create_project", name="Snap Test"))["project"]["id"]
    result = await _call(mcp, "restore_snapshot", project_id=pid, snapshot_id="snap_missing")
    assert result["success"] is False
