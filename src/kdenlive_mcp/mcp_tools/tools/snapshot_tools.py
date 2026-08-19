"""VERSION CONTROL / SNAPSHOTS tools (spec section 21)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.mcp_tools.state import ProjectSession, get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, project_summary, tool_result


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def create_snapshot(label: str = "", project_id: str | None = None) -> dict:
        """Snapshot the project's current state so it can be restored later."""
        session = get_state().get(project_id)
        info = get_state().snapshots.create_snapshot(session.project, session.media_index, label=label)
        return tool_result(snapshot=info.to_dict())

    @mcp.tool()
    @catch_errors
    def list_snapshots(project_id: str | None = None) -> dict:
        """List every snapshot taken of a project."""
        session = get_state().get(project_id)
        infos = get_state().snapshots.list_snapshots(session.project.id)
        return tool_result(snapshots=[i.to_dict() for i in infos])

    @mcp.tool()
    @catch_errors
    def restore_snapshot(snapshot_id: str, project_id: str | None = None) -> dict:
        """Restore a project to a previous snapshot. Replaces the in-memory project state."""
        state = get_state()
        session = state.get(project_id)
        restored_project, restored_index = state.snapshots.restore_snapshot(session.project.id, snapshot_id)
        state.add(ProjectSession(restored_project, restored_index), make_active=True)
        return tool_result(project=project_summary(restored_project))

    @mcp.tool()
    @catch_errors
    def compare_snapshots(snapshot_id_a: str, snapshot_id_b: str, project_id: str | None = None) -> dict:
        """Compare two snapshots of the same project."""
        session = get_state().get(project_id)
        diff = get_state().snapshots.compare_snapshots(session.project.id, snapshot_id_a, snapshot_id_b)
        return tool_result(diff=diff)

    @mcp.tool()
    @catch_errors
    def undo_operation(project_id: str | None = None) -> dict:
        """Undo the most recent edit. Every mutating timeline/motion/effects/
        transitions/audio tool call automatically checkpoints before it runs,
        so this steps back through actual tool calls, not just snapshots."""
        state = get_state()
        session = state.get(project_id)
        restored = state.undo(session.project.id)
        return tool_result(project=project_summary(restored))

    @mcp.tool()
    @catch_errors
    def redo_operation(project_id: str | None = None) -> dict:
        """Redo the last undone edit."""
        state = get_state()
        session = state.get(project_id)
        restored = state.redo(session.project.id)
        return tool_result(project=project_summary(restored))
