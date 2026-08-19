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


def _find_melt() -> tuple[str | None, dict[str, str]]:
    """Locate the MLT `melt` renderer.

    On a snap install of Kdenlive there is no `melt`/`melt-7` on PATH at
    all -- it lives inside the snap's private tree and needs
    LD_LIBRARY_PATH/MLT_REPOSITORY/MLT_DATA pointed at that same tree or it
    fails with "cannot open shared object file". We fall back to
    discovering the newest snap revision's melt-7 and computing those.
    """
    override = os.environ.get("KDENLIVE_MCP_MELT")
    if override:
        return override, {}
    found = shutil.which("melt") or shutil.which("melt-7")
    if found:
        return found, {}
    candidates = sorted(glob.glob("/var/lib/snapd/snap/kdenlive/*/usr/bin/melt-7"), reverse=True)
    if candidates:
        melt_path = candidates[0]
        snap_root = melt_path.rsplit("/usr/bin/", 1)[0]
        env = {
            "LD_LIBRARY_PATH": f"{snap_root}/usr/lib/x86_64-linux-gnu:{snap_root}/usr/lib",
            "MLT_REPOSITORY": f"{snap_root}/usr/lib/x86_64-linux-gnu/mlt-7",
            "MLT_DATA": f"{snap_root}/usr/share/mlt-7",
        }
        return melt_path, env
    return None, {}


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
