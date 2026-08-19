"""Allowlisted subprocess runner for ffmpeg / ffprobe / melt / kdenlive.

The AI never gets to construct a raw shell command. Callers build a plain
list[str] of arguments for one of the known binaries; this module resolves
the binary via config (never via a caller-supplied path) and execs it with
no shell involved.
"""

from __future__ import annotations

import logging
import os
import shlex
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


def build_command(tool: str, args: list[str]) -> tuple[list[str], dict[str, str] | None]:
    """Resolves `tool` to an actual argv + env, applying the same
    snap-run-wrapping / env-override logic `run()` uses. Shared by `run()`
    (blocking) and `spawn_background()` (long-lived process, e.g. a render)
    so the two never drift out of sync on how a tool is actually invoked.
    """
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

    env = None
    if tool == "melt" and cfg.melt_via_snap_run:
        # Reconstructing the snap's library environment by hand only gets
        # far enough for -query introspection; real encoding needs libs
        # from a separate content-interface snap that `snap run` resolves
        # correctly on its own. Route the whole invocation through it.
        inner_cmd = shlex.join([binary, *args])
        cmd = ["snap", "run", "--shell", cfg.melt_snap_name, "-c", inner_cmd]
    else:
        cmd = [binary, *args]
        if tool == "melt" and cfg.melt_env:
            env = {**os.environ, **cfg.melt_env}
    return cmd, env


def spawn_background(tool: str, args: list[str], *, stdout_path) -> subprocess.Popen:
    """Launches `tool` as a long-lived background process (e.g. a render),
    with combined stdout+stderr redirected to `stdout_path` so a separate
    poll can tail it for progress without blocking on a live pipe.

    Runs in its own process group (start_new_session=True) so a caller that
    needs to cancel it can os.killpg() the whole subtree -- e.g. the `snap
    run` wrapper plus the actual melt-7 child it launches -- without any
    risk of that signal reaching our own server process, which shares the
    parent's process group otherwise.
    """
    cmd, env = build_command(tool, args)
    logger.info("spawn: %s", " ".join(cmd))
    with open(stdout_path, "w") as log_file:
        return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env,
                                 start_new_session=True)


def run(tool: str, args: list[str], *, timeout: float = 300.0, check: bool = True) -> RunResult:
    cmd, env = build_command(tool, args)
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
