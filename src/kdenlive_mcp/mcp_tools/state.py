"""In-process session state for the MCP server.

Multiple projects can be open at once (mirroring Kdenlive's own tabbed
documents); one is "active" and is what tools operate on by default. This
is process-local, in-memory state -- it is not itself persisted; that's
what save_project / snapshots are for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.timeline.model import Project
from kdenlive_mcp.core.timeline.serialize import project_from_dict, project_to_dict
from kdenlive_mcp.errors import InvalidOperationError, ProjectNotOpenError
from kdenlive_mcp.storage.snapshots.manager import SnapshotManager

UNDO_STACK_LIMIT = 50


@dataclass
class ProjectSession:
    project: Project
    media_index: MediaIndex


class ServerState:
    def __init__(self):
        self._sessions: dict[str, ProjectSession] = {}
        self._active_project_id: str | None = None
        self.snapshots = SnapshotManager()
        self._undo_stacks: dict[str, list[dict]] = {}
        self._redo_stacks: dict[str, list[dict]] = {}

    def add(self, session: ProjectSession, *, make_active: bool = True) -> None:
        self._sessions[session.project.id] = session
        if make_active or self._active_project_id is None:
            self._active_project_id = session.project.id

    def get(self, project_id: str | None = None) -> ProjectSession:
        pid = project_id or self._active_project_id
        if pid is None or pid not in self._sessions:
            raise ProjectNotOpenError(
                "No project is open" if pid is None else f"Project '{pid}' is not open",
                suggestion="Call create_project or open_project first.",
            )
        return self._sessions[pid]

    def set_active(self, project_id: str) -> None:
        if project_id not in self._sessions:
            raise ProjectNotOpenError(f"Project '{project_id}' is not open")
        self._active_project_id = project_id

    def close(self, project_id: str | None = None) -> None:
        pid = project_id or self._active_project_id
        if pid in self._sessions:
            del self._sessions[pid]
        if self._active_project_id == pid:
            self._active_project_id = next(iter(self._sessions), None)

    def list_open(self) -> list[Project]:
        return [s.project for s in self._sessions.values()]

    @property
    def active_project_id(self) -> str | None:
        return self._active_project_id

    # ---------------------------------------------------------- undo/redo -

    def checkpoint(self, project_id: str) -> None:
        """Push the project's current state onto its undo stack, e.g. right
        before a mutating tool applies an edit. Clears the redo stack, same
        as any editor: making a fresh edit invalidates the old redo branch.
        """
        session = self._sessions.get(project_id)
        if session is None:
            return
        stack = self._undo_stacks.setdefault(project_id, [])
        stack.append(project_to_dict(session.project))
        if len(stack) > UNDO_STACK_LIMIT:
            del stack[0]
        self._redo_stacks[project_id] = []

    def undo(self, project_id: str) -> Project:
        session = self._sessions.get(project_id)
        if session is None:
            raise ProjectNotOpenError(f"Project '{project_id}' is not open")
        stack = self._undo_stacks.get(project_id, [])
        if not stack:
            raise InvalidOperationError("Nothing to undo", suggestion="No checkpointed edits exist yet.")
        self._redo_stacks.setdefault(project_id, []).append(project_to_dict(session.project))
        restored = project_from_dict(stack.pop())
        session.project = restored
        return restored

    def redo(self, project_id: str) -> Project:
        session = self._sessions.get(project_id)
        if session is None:
            raise ProjectNotOpenError(f"Project '{project_id}' is not open")
        stack = self._redo_stacks.get(project_id, [])
        if not stack:
            raise InvalidOperationError("Nothing to redo")
        self._undo_stacks.setdefault(project_id, []).append(project_to_dict(session.project))
        restored = project_from_dict(stack.pop())
        session.project = restored
        return restored


_state = ServerState()


def get_state() -> ServerState:
    return _state
