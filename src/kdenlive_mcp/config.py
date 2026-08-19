"""Environment-driven configuration.

All paths the server is allowed to touch derive from here. Nothing outside
`workspace_dir` (and paths the caller explicitly passes for source media,
which are still validated) should ever be written to.
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _find_binary(name: str, env_var: str) -> str | None:
    override = os.environ.get(env_var)
    if override:
        return override
    return shutil.which(name)


def _find_melt() -> tuple[str | None, dict[str, str], bool]:
    """Locate the MLT `melt` renderer.

    Returns (binary_path, env_overrides, needs_snap_run).

    On a snap install of Kdenlive there is no `melt`/`melt-7` on PATH at
    all -- it lives inside the snap's private tree. Manually reconstructing
    LD_LIBRARY_PATH/MLT_REPOSITORY/MLT_DATA gets far enough for `-query`
    introspection, but real encoding needs libavcodec etc. from the
    separate `ffmpeg-2404` content-interface snap Kdenlive is plugged into
    -- correctly resolving *all* of those paths by hand is exactly what
    `snap run` already does. So for a snap install we don't reconstruct the
    environment at all; we shell out through `snap run --shell kdenlive -c
    '<command>'`, which was confirmed (by hand) to successfully decode
    *and* encode real media, unlike the manual env reconstruction.
    """
    override = os.environ.get("KDENLIVE_MCP_MELT")
    if override:
        return override, {}, False
    found = shutil.which("melt") or shutil.which("melt-7")
    if found:
        return found, {}, False
    candidates = sorted(glob.glob("/var/lib/snapd/snap/kdenlive/*/usr/bin/melt-7"), reverse=True)
    if candidates:
        return candidates[0], {}, True
    return None, {}, False


def _discover_kdenlive_effects_dir() -> Path | None:
    candidates = sorted(glob.glob("/var/lib/snapd/snap/kdenlive/*/usr/share/kdenlive/effects"), reverse=True)
    candidates += ["/usr/share/kdenlive/effects", "/usr/local/share/kdenlive/effects"]
    for c in candidates:
        p = Path(c)
        if p.is_dir():
            return p
    return None


@dataclass(frozen=True)
class Config:
    workspace_dir: Path = field(default_factory=lambda: _env_path(
        "KDENLIVE_MCP_WORKSPACE", "~/.kdenlive-mcp/workspace"
    ))
    cache_dir: Path = field(default_factory=lambda: _env_path(
        "KDENLIVE_MCP_CACHE", "~/.kdenlive-mcp/cache"
    ))
    snapshots_dir: Path = field(default_factory=lambda: _env_path(
        "KDENLIVE_MCP_SNAPSHOTS", "~/.kdenlive-mcp/snapshots"
    ))
    assets_dir: Path = field(default_factory=lambda: _env_path(
        "KDENLIVE_MCP_ASSETS", "~/.kdenlive-mcp/assets"
    ))
    log_dir: Path = field(default_factory=lambda: _env_path(
        "KDENLIVE_MCP_LOGS", "~/.kdenlive-mcp/logs"
    ))

    ffmpeg_bin: str | None = field(default_factory=lambda: _find_binary("ffmpeg", "KDENLIVE_MCP_FFMPEG"))
    ffprobe_bin: str | None = field(default_factory=lambda: _find_binary("ffprobe", "KDENLIVE_MCP_FFPROBE"))
    melt_bin: str | None = field(default_factory=lambda: _find_melt()[0])
    melt_env: dict[str, str] = field(default_factory=lambda: _find_melt()[1])
    melt_via_snap_run: bool = field(default_factory=lambda: _find_melt()[2])
    melt_snap_name: str = "kdenlive"
    kdenlive_bin: str | None = field(default_factory=lambda: _find_binary("kdenlive", "KDENLIVE_MCP_KDENLIVE"))
    kdenlive_effects_dir: Path | None = field(default_factory=lambda: (
        _env_path("KDENLIVE_MCP_EFFECTS_DIR", "") if os.environ.get("KDENLIVE_MCP_EFFECTS_DIR")
        else _discover_kdenlive_effects_dir()
    ))

    # Allowlisted external commands the server is permitted to exec. Never
    # extend this from model-supplied strings.
    allowed_binaries: tuple[str, ...] = ("ffmpeg", "ffprobe", "melt", "melt-7", "kdenlive")

    max_preview_resolution_height: int = 720

    def ensure_dirs(self) -> None:
        for d in (self.workspace_dir, self.cache_dir, self.snapshots_dir, self.assets_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
        _config.ensure_dirs()
    return _config
