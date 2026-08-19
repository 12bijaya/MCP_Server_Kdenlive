"""Path safety helpers.

Every filesystem path that originates from an MCP tool call (i.e. from the
model) must go through `resolve_source_path` (read-only inputs like source
media / reference projects) or `resolve_workspace_path` (paths the server
itself will write to). Both reject traversal tricks and symlink escapes.
"""

from __future__ import annotations

from pathlib import Path

from kdenlive_mcp.config import get_config
from kdenlive_mcp.errors import InvalidPathError


def resolve_source_path(raw_path: str, *, must_exist: bool = True) -> Path:
    """Resolve a path the model supplied to reference existing user files.

    Symlinks are resolved so an escape via a symlinked entry can't hide a
    traversal outside anywhere sane; we still allow arbitrary absolute
    locations here since source media legitimately lives all over the
    filesystem, but we refuse to touch non-existent parents or obvious
    device/proc paths.
    """
    if not raw_path or "\x00" in raw_path:
        raise InvalidPathError(f"Invalid path: {raw_path!r}", suggestion="Provide a non-empty filesystem path.")

    path = Path(raw_path).expanduser()
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise InvalidPathError(f"Path does not exist: {raw_path}", suggestion="Check the path and try again.") from exc
    except (OSError, RuntimeError) as exc:
        raise InvalidPathError(f"Could not resolve path: {raw_path} ({exc})") from exc

    for part in ("/proc", "/sys", "/dev"):
        if str(resolved).startswith(part + "/") or str(resolved) == part:
            raise InvalidPathError(f"Refusing to access system path: {resolved}")

    if must_exist and not resolved.exists():
        raise InvalidPathError(f"Path does not exist: {raw_path}")

    return resolved


def resolve_workspace_path(relative_or_absolute: str, *, subdir: Path | None = None) -> Path:
    """Resolve a path the server will write to, constrained to the workspace.

    Accepts either a path relative to the workspace (or given subdir) or an
    absolute path that must already live inside the workspace tree.
    """
    cfg = get_config()
    root = (subdir or cfg.workspace_dir).resolve()
    candidate = Path(relative_or_absolute).expanduser()
    combined = candidate if candidate.is_absolute() else root / candidate

    resolved_parent = combined.parent.resolve() if combined.parent.exists() else combined.parent
    resolved = resolved_parent / combined.name

    try:
        resolved_check = resolved.resolve() if resolved.exists() else resolved
    except (OSError, RuntimeError) as exc:
        raise InvalidPathError(f"Could not resolve workspace path: {relative_or_absolute} ({exc})") from exc

    if root not in resolved_check.parents and resolved_check != root:
        raise InvalidPathError(
            f"Path escapes workspace: {relative_or_absolute}",
            suggestion=f"Use a path inside {root}",
        )
    return resolved_check
