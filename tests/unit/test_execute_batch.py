"""Tests for execute_batch: atomic multi-tool-call execution with automatic
rollback on failure (spec sections 19/20), driven through the real MCP
server (mcp.call_tool), not mocked.
"""

from __future__ import annotations

import pytest

from kdenlive_mcp.server import build_server


@pytest.fixture()
def mcp(tmp_path, monkeypatch):
    from kdenlive_mcp import config as config_module
    from kdenlive_mcp.mcp_tools import state as state_module

    monkeypatch.setenv("KDENLIVE_MCP_WORKSPACE", str(tmp_path / "workspace"))
    config_module._config = None
    state_module._state = state_module.ServerState()
    try:
        yield build_server()
    finally:
        config_module._config = None


async def _call(mcp, tool_name, **args):
    import json
    raw = await mcp.call_tool(tool_name, args)
    return json.loads(raw[0].text)


@pytest.mark.asyncio
async def test_successful_batch_applies_all_operations(mcp):
    created = await _call(mcp, "create_project", name="Batch Test")
    pid = created["project"]["id"]
    info = await _call(mcp, "get_project_info", project_id=pid)
    track_id = info["project"]["sequences"][0]["video_tracks"][0]["id"]

    result = await _call(
        mcp, "execute_batch", project_id=pid,
        operations=[
            {"tool": "add_clip", "args": {"track_id": track_id, "position": 0.0, "source_in": 0.0,
                                           "source_out": 2.0, "clip_type": "color"}},
            {"tool": "add_marker", "args": {"frame_seconds": 1.0, "name": "mid"}},
        ],
    )
    assert result["success"], result
    assert result["count"] == 2

    info_after = await _call(mcp, "get_project_info", project_id=pid)
    clips = info_after["project"]["sequences"][0]["video_tracks"][0]["clips"]
    assert len(clips) == 1


@pytest.mark.asyncio
async def test_failed_batch_rolls_back_every_prior_operation(mcp):
    created = await _call(mcp, "create_project", name="Rollback Test")
    pid = created["project"]["id"]
    info = await _call(mcp, "get_project_info", project_id=pid)
    track_id = info["project"]["sequences"][0]["video_tracks"][0]["id"]

    result = await _call(
        mcp, "execute_batch", project_id=pid,
        operations=[
            {"tool": "add_clip", "args": {"track_id": track_id, "position": 0.0, "source_in": 0.0,
                                           "source_out": 2.0, "clip_type": "color"}},
            {"tool": "add_clip", "args": {"track_id": "not_a_real_track_id", "position": 5.0,
                                           "source_in": 0.0, "source_out": 2.0, "clip_type": "color"}},
        ],
    )
    assert result["success"] is False
    assert result["failed_at_index"] == 1
    assert result["rolled_back"] is True
    assert result["completed_before_failure"] == 1

    info_after = await _call(mcp, "get_project_info", project_id=pid)
    clips = info_after["project"]["sequences"][0]["video_tracks"][0]["clips"]
    assert clips == [], "the successful first add_clip should have been rolled back too"


@pytest.mark.asyncio
async def test_batch_rejects_operation_missing_tool_name(mcp):
    created = await _call(mcp, "create_project", name="Bad Op Test")
    pid = created["project"]["id"]

    result = await _call(mcp, "execute_batch", project_id=pid, operations=[{"args": {}}])
    assert result["success"] is False
    assert "missing a 'tool' name" in result["error"]
