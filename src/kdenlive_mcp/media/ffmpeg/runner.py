"""Allowlisted subprocess runner for ffmpeg / ffprobe / melt / kdenlive.

The AI never gets to construct a raw shell command. Callers build a plain
list[str] of arguments for one of the known binaries; this module resolves
the binary via config (never via a caller-supplied path) and execs it with
no shell involved.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from kdenlive_mcp.config import get_config
from kdenlive_mcp.errors import ExternalToolError

logger = logging.getLogger("kdenlive_mcp.external")

_BIN_ATTR = {
    "ffmpeg": "ffmpeg_bin",
    "ffprobe": "ffprobe_bin",
    "melt": "melt_bin",
    "kdenlive": "kdenlive_bin",
}


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(tool: str, args: list[str], *, timeout: float = 300.0, check: bool = True) -> RunResult:
    if tool not in _BIN_ATTR:
        raise ExternalToolError(f"Tool '{tool}' is not allowlisted", code="TOOL_NOT_ALLOWLISTED")

    cfg = get_config()
    binary = getattr(cfg, _BIN_ATTR[tool])
    if not binary:
        raise ExternalToolError(
            f"'{tool}' binary not found on this system",
            code="TOOL_NOT_INSTALLED",
            suggestion=f"Install {tool} or set the corresponding KDENLIVE_MCP_* env var to its path.",
        )

    cmd = [binary, *args]
    env = None
    if tool == "melt" and cfg.melt_env:
        import os
        env = {**os.environ, **cfg.melt_env}

    logger.info("exec: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(f"{tool} timed out after {timeout}s", code="TOOL_TIMEOUT") from exc
    except OSError as exc:
        raise ExternalToolError(f"Failed to execute {tool}: {exc}") from exc

    result = RunResult(proc.returncode, proc.stdout, proc.stderr)
    if check and not result.ok:
        raise ExternalToolError(
            f"{tool} exited with code {proc.returncode}",
            code="TOOL_FAILED",
            details={"stderr": proc.stderr[-4000:], "cmd": cmd},
        )
    return result


def is_available(tool: str) -> bool:
    cfg = get_config()
    return bool(getattr(cfg, _BIN_ATTR.get(tool, ""), None))
