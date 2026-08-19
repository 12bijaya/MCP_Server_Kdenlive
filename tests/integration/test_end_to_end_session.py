"""End-to-end integration test: drive the MCP tool layer like an AI agent
would -- create a project, import real media, build a timeline, add
motion/effects/a transition, undo/redo, validate, save, and confirm the
saved .kdenlive file is well-formed and (if melt/kdenlive are installed)
actually loads in the real engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools import (
    audio_tools, capability_tools, effects_tools, media_tools, motion_tools,
    project_tools, snapshot_tools, timeline_tools, transitions_tools,
)

pytestmark = pytest.mark.integration

REAL_CLIP_CANDIDATES = [
    Path.home() / "Documents/kaam/day4/videos/clip1.mp4",
    Path.home() / "Documents/kaam/day4/videos/clip2.mp4",
]


class _ToolBag:
    """Collects every @mcp.tool()-registered function from a module's
    register(mcp) call so the test can invoke them directly without
    spinning up a real MCP transport."""

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
    monkeypatch.setenv("KDENLIVE_MCP_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("KDENLIVE_MCP_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("KDENLIVE_MCP_SNAPSHOTS", str(tmp_path / "snapshots"))
    monkeypatch.setenv("KDENLIVE_MCP_ASSETS", str(tmp_path / "assets"))
    monkeypatch.setenv("KDENLIVE_MCP_LOGS", str(tmp_path / "logs"))
    config_module._config = None

    from kdenlive_mcp.mcp_tools import state as state_module
    state_module._state = state_module.ServerState()

    bag = _ToolBag()
    for module in (project_tools, media_tools, timeline_tools, motion_tools,
                   effects_tools, transitions_tools, audio_tools, capability_tools,
                   snapshot_tools):
        module.register(bag)
    try:
        yield bag.tools
    finally:
        # Config is a cached global singleton computed once from env vars;
        # monkeypatch reverts the env vars at teardown but that alone
        # leaves the *already-built* Config object (with this test's
        # /tmp-based workspace baked in) cached for whatever test runs
        # next. Reset it so the next test rebuilds Config from the real
        # environment instead of inheriting this one's paths.
        config_module._config = None


def _require_real_clip():
    for c in REAL_CLIP_CANDIDATES:
        if c.exists():
            return c
    pytest.skip("no real sample clip found on this machine")


def test_full_editing_session(tools):
    clip_path = _require_real_clip()

    created = tools["create_project"](name="E2E Test", resolution="1080p", fps="30")
    assert created["success"], created
    project_id = created["project"]["id"]

    imported = tools["import_video"](path=str(clip_path), project_id=project_id)
    assert imported["success"], imported
    asset_id = imported["asset"]["id"]
    duration = imported["asset"]["duration"]
    assert duration > 0

    info = tools["get_project_info"](project_id=project_id)
    video_track_id = info["project"]["sequences"][0]["video_tracks"][0]["id"]
    sequence_id = info["project"]["sequences"][0]["id"]

    added = tools["add_clip"](track_id=video_track_id, position=0.0, source_in=0.0,
                               source_out=min(duration, 5.0), asset_id=asset_id,
                               clip_type="video", name="clip1", project_id=project_id)
    assert added["success"], added
    clip_id = added["clip"]["id"]

    duplicated = tools["duplicate_clip"](clip_id=clip_id, project_id=project_id)
    assert duplicated["success"], duplicated
    clip2_id = duplicated["clip"]["id"]

    pushed = tools["create_camera_push"](clip_id=clip_id, start_seconds=0.0, end_seconds=2.0,
                                          project_id=project_id)
    assert pushed["success"], pushed

    preset = tools["apply_effect_preset"](clip_id=clip2_id, preset_name="cinematic", project_id=project_id)
    assert preset["success"], preset
    assert preset["effect_count"] > 0

    split = tools["split_clip"](clip_id=clip2_id, at_seconds=added["clip"]["end"] + 1.0, project_id=project_id)
    assert split["success"], split

    validated = tools["validate_project"](project_id=project_id, use_melt=False)
    assert validated["success"], validated
    assert validated["validation"]["valid"], validated["validation"]["errors"]

    undone = tools["undo_operation"](project_id=project_id)
    assert undone["success"], undone

    redone = tools["redo_operation"](project_id=project_id)
    assert redone["success"], redone

    snap = tools["create_snapshot"](label="checkpoint", project_id=project_id)
    assert snap["success"]

    saved = tools["save_project_as"](new_path="e2e_test.kdenlive", project_id=project_id)
    assert saved["success"], saved
    saved_path = Path(saved["path"])
    assert saved_path.exists()

    import xml.etree.ElementTree as ET
    root = ET.fromstring(saved_path.read_text())
    assert root.tag == "mlt"

    caps = tools["get_kdenlive_capabilities"]()
    assert caps["success"]
