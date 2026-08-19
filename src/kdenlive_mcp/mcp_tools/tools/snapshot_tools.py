"""VERSION CONTROL / SNAPSHOTS tools (spec section 21) and batch execution
with automatic rollback (spec sections 19/20)."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.errors import InvalidOperationError
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

    @mcp.tool()
    @catch_errors
    async def execute_batch(operations: list[dict], project_id: str | None = None) -> dict:
        """Execute multiple tool calls as one atomic unit: if any operation
        fails, every operation that already succeeded in this batch is
        rolled back (via the same undo mechanism as undo_operation), and
        the project ends up exactly as it started -- no partial edits.

        operations: list of {"tool": "<tool_name>", "args": {...}}. Each
        op's args are given this call's project_id automatically unless the
        op specifies its own. Works with any registered tool, mutating or
        not; only mutating tools actually contribute anything to roll back.
        """
        state = get_state()
        session = state.get(project_id)
        pid = session.project.id
        # @mutates checkpoints *before* a mutating call runs, whether or
        # not it ends up succeeding -- so a failing operation that is
        # itself a mutating tool still pushes a (now-orphaned) checkpoint.
        # Roll back by undoing until the stack returns to its depth here,
        # not by counting how many operations reported success.
        start_depth = state.undo_stack_depth(pid)

        results = []
        for i, op in enumerate(operations):
            tool_name = op.get("tool")
            if not tool_name:
                _rollback_to_depth(state, pid, start_depth)
                return {
                    "success": False, "failed_at_index": i, "error": f"Operation {i} is missing a 'tool' name",
                    "rolled_back": True, "completed_before_failure": len(results),
                }

            args = dict(op.get("args", {}))
            args.setdefault("project_id", pid)

            try:
                raw = await mcp.call_tool(tool_name, args)
            except Exception as exc:  # noqa: BLE001 - unknown tool name, bad args, etc.
                _rollback_to_depth(state, pid, start_depth)
                return {
                    "success": False, "failed_at_index": i, "tool": tool_name,
                    "error": f"Could not call tool '{tool_name}': {exc}",
                    "rolled_back": True, "completed_before_failure": len(results),
                }

            result = json.loads(raw[0].text) if raw and hasattr(raw[0], "text") else raw
            if not isinstance(result, dict) or not result.get("success", False):
                _rollback_to_depth(state, pid, start_depth)
                return {
                    "success": False, "failed_at_index": i, "tool": tool_name, "result": result,
                    "rolled_back": True, "completed_before_failure": len(results),
                }
            results.append({"tool": tool_name, "result": result})

        return tool_result(results=results, count=len(results))


def _rollback_to_depth(state, project_id: str, target_depth: int) -> None:
    while state.undo_stack_depth(project_id) > target_depth:
        try:
            state.undo(project_id)
        except InvalidOperationError:
            break
