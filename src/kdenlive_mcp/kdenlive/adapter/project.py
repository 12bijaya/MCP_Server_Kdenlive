"""Project-level orchestration: create / open / save / backup / duplicate.

This is the only place that touches the filesystem for `.kdenlive` files.
Every path comes in through storage.workspace's validators. Source project
files (open_project's input) are read-only inputs; anything this module
writes goes through resolve_workspace_path or a path the caller explicitly
confirmed is theirs to overwrite (save_project on an already-open project's
own path).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.timeline.model import Project, new_project as _new_project
from kdenlive_mcp.errors import ProjectNotFoundError, ValidationError
from kdenlive_mcp.kdenlive.adapter.profiles import resolve_profile
from kdenlive_mcp.kdenlive.adapter.xml_parser import KdenliveXmlParser
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter
from kdenlive_mcp.storage.workspace import resolve_source_path, resolve_workspace_path


def create_project(name: str, *, resolution: str = "1080p", fps: str | float = "30",
                    orientation: str = "landscape") -> Project:
    settings = resolve_profile(resolution, fps, orientation=orientation)
    return _new_project(name, settings)


def open_project(path: str) -> tuple[Project, MediaIndex]:
    resolved = resolve_source_path(path)
    if resolved.suffix != ".kdenlive":
        raise ValidationError(f"Not a .kdenlive project file: {path}")
    xml_text = resolved.read_text(encoding="utf-8")
    parser = KdenliveXmlParser(xml_text, source_path=resolved)
    return parser.parse_project(project_name=resolved.stem)


def save_project(project: Project, media_index: MediaIndex, *, path: str | None = None) -> Path:
    """Writes the project to `path`, or to `project.path` if already saved."""
    target = path or project.path
    if not target:
        raise ValidationError(
            "Project has never been saved and no path was given",
            suggestion="Call save_project_as with a destination path first.",
        )
    resolved = resolve_workspace_path(target) if not Path(target).is_absolute() else Path(target)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.suffix != ".kdenlive":
        resolved = resolved.with_suffix(".kdenlive")

    writer = KdenliveXmlWriter(project, media_index)
    writer.write(resolved)
    project.path = str(resolved)
    project.dirty = False
    return resolved


def save_project_as(project: Project, media_index: MediaIndex, new_path: str) -> Path:
    return save_project(project, media_index, path=new_path)


def backup_project(project_path: str) -> Path:
    resolved = resolve_source_path(project_path)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = resolved.with_name(f"{resolved.stem}.backup_{timestamp}{resolved.suffix}")
    shutil.copy2(resolved, backup_path)
    return backup_path


def restore_project(backup_path: str, *, restore_to: str) -> Path:
    resolved_backup = resolve_source_path(backup_path)
    resolved_target = resolve_workspace_path(restore_to) if not Path(restore_to).is_absolute() else Path(restore_to)
    shutil.copy2(resolved_backup, resolved_target)
    return resolved_target


def duplicate_project(project: Project) -> Project:
    from kdenlive_mcp.core.timeline.serialize import project_from_dict, project_to_dict
    from kdenlive_mcp.core.timeline.model import new_id

    data = project_to_dict(project)
    duplicate = project_from_dict(data)
    duplicate.id = new_id("project")
    duplicate.name = f"{project.name} (copy)"
    duplicate.path = None
    duplicate.dirty = True
    return duplicate
