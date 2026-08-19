"""KDENLIVE COMPATIBILITY tools (spec section 23)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.kdenlive.adapter.capabilities import detect_capabilities
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, tool_result


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def get_kdenlive_capabilities() -> dict:
        """Report what this installation can actually do: Kdenlive/FFmpeg/melt versions,
        available effects/transitions, render profiles. Never assume a capability exists."""
        caps = detect_capabilities()
        return tool_result(capabilities=caps.to_dict())
