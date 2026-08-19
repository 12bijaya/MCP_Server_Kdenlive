"""MCP server entrypoint.

Wires every tool module's `register(mcp)` into one FastMCP app and runs it
over stdio (the standard transport for a locally-launched MCP server, e.g.
from Claude Desktop / Claude Code's MCP config).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.mcp_tools.tools import (
    audio_tools,
    capability_tools,
    effects_tools,
    media_tools,
    motion_tools,
    project_tools,
    render_tools,
    snapshot_tools,
    subtitle_tools,
    timeline_tools,
    transitions_tools,
)

INSTRUCTIONS = """\
You are driving Kdenlive through a structured timeline model, not raw XML.
Typical flow: create_project or open_project, import_video/import_folder to
populate the media bin, add_clip/split_clip/trim_clip etc. to build the
timeline, motion/effects/transitions tools for polish, then
validate_project before save_project. Call get_kdenlive_capabilities before
relying on any specific effect/transition existing -- nothing here is
faked; unavailable capabilities are reported, not silently substituted.
Most tools take an optional project_id/sequence_id; omit them to operate on
whichever project/sequence is currently active."""


def build_server() -> FastMCP:
    mcp = FastMCP("kdenlive-mcp", instructions=INSTRUCTIONS)
    for module in (
        project_tools, media_tools, timeline_tools, motion_tools,
        effects_tools, transitions_tools, audio_tools, capability_tools,
        snapshot_tools, render_tools, subtitle_tools,
    ):
        module.register(mcp)
    return mcp


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
