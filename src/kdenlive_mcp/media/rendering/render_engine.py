"""Real rendering via melt -- the actual MLT rendering engine, not a
reimplementation. A render is a genuinely long-running background process:
start_render returns immediately with a job id, poll_render_status tails
its log for progress, and completion is only ever reported after verifying
the output file is actually valid media via ffprobe (a process exiting 0
does not by itself prove a playable file was produced).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from kdenlive_mcp.config import get_config
from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.timeline.model import Project, new_id
from kdenlive_mcp.core.timeline.timecode import seconds_to_frames
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter
from kdenlive_mcp.media.ffmpeg.runner import spawn_background
from kdenlive_mcp.media.ffprobe.probe import probe_media

_PROGRESS_RE = re.compile(r"Current Frame:\s*(\d+),\s*percentage:\s*(\d+)")

# codec -> real ffmpeg/avformat vcodec name, kept small and explicit rather
# than accepting an arbitrary passthrough string (spec: validate arguments,
# don't let external input drive an allowlisted tool's flags unchecked).
_VIDEO_CODECS = {"h264": "libx264", "h265": "libx265", "vp9": "libvpx-vp9", "prores": "prores_ks"}
_AUDIO_CODECS = {"aac": "aac", "mp3": "libmp3lame", "opus": "libopus"}


@dataclass
class RenderJob:
    id: str
    project_id: str
    output_path: str
    status: str = "running"  # running | completed | failed | cancelled
    progress_percent: float = 0.0
    current_frame: int | None = None
    total_frames: int | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    output_duration: float | None = None
    output_size_bytes: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "output_path": self.output_path,
            "status": self.status, "progress_percent": round(self.progress_percent, 1),
            "current_frame": self.current_frame, "total_frames": self.total_frames,
            "error": self.error, "started_at": self.started_at, "completed_at": self.completed_at,
            "output_duration": self.output_duration, "output_size_bytes": self.output_size_bytes,
        }


@dataclass
class _JobHandle:
    job: RenderJob
    process: subprocess.Popen
    log_path: Path
    scratch_project_path: Path


class RenderManager:
    def __init__(self):
        self._jobs: dict[str, _JobHandle] = {}

    def _validate_output_path(self, output_path: str, media_index: MediaIndex) -> Path:
        resolved = Path(output_path).expanduser()
        if not resolved.is_absolute():
            resolved = get_config().workspace_dir / resolved
        resolved = resolved.resolve() if resolved.parent.exists() else resolved

        for asset in media_index.list():
            if Path(asset.path).resolve() == resolved:
                raise InvalidOperationError(
                    f"Refusing to render over an imported source media file: {resolved}",
                    suggestion="Choose a different output path.",
                )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def start_render(
        self, project: Project, media_index: MediaIndex, *,
        output_path: str, video_codec: str = "h264", audio_codec: str = "aac",
        start_seconds: float | None = None, end_seconds: float | None = None,
        width: int | None = None, height: int | None = None,
    ) -> RenderJob:
        if video_codec not in _VIDEO_CODECS:
            raise InvalidOperationError(f"Unknown video_codec '{video_codec}'",
                                         suggestion=f"Use one of: {sorted(_VIDEO_CODECS)}")
        if audio_codec not in _AUDIO_CODECS:
            raise InvalidOperationError(f"Unknown audio_codec '{audio_codec}'",
                                         suggestion=f"Use one of: {sorted(_AUDIO_CODECS)}")

        out_path = self._validate_output_path(output_path, media_index)
        cfg = get_config()
        scratch_dir = cfg.workspace_dir / ".render_scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        job_id = new_id("render")
        scratch_project_path = scratch_dir / f"{job_id}.kdenlive"
        log_path = scratch_dir / f"{job_id}.log"

        writer = KdenliveXmlWriter(project, media_index)
        writer.write(scratch_project_path)

        fps = project.settings.fps
        args = [str(scratch_project_path), "-consumer", f"avformat:{out_path}",
                f"vcodec={_VIDEO_CODECS[video_codec]}", f"acodec={_AUDIO_CODECS[audio_codec]}"]
        if start_seconds is not None:
            args.append(f"in={seconds_to_frames(start_seconds, fps)}")
        if end_seconds is not None:
            args.append(f"out={seconds_to_frames(end_seconds, fps)}")
        if width and height:
            args.append(f"s={width}x{height}")

        process = spawn_background("melt", args, stdout_path=log_path)
        job = RenderJob(id=job_id, project_id=project.id, output_path=str(out_path))
        total_frames = project.active_sequence().duration() if project.active_sequence() else None
        job.total_frames = total_frames

        self._jobs[job_id] = _JobHandle(job=job, process=process, log_path=log_path,
                                         scratch_project_path=scratch_project_path)
        return job

    def poll(self, job_id: str) -> RenderJob:
        handle = self._jobs.get(job_id)
        if handle is None:
            raise InvalidOperationError(f"Render job not found: {job_id}")
        job = handle.job

        if job.status == "running":
            self._update_progress(handle)
            exit_code = handle.process.poll()
            if exit_code is not None:
                self._finalize(handle, exit_code)
        return job

    def _update_progress(self, handle: _JobHandle) -> None:
        try:
            text = handle.log_path.read_text(errors="ignore")
        except FileNotFoundError:
            return
        matches = _PROGRESS_RE.findall(text)
        if matches:
            frame, pct = matches[-1]
            handle.job.current_frame = int(frame)
            handle.job.progress_percent = float(pct)

    def _finalize(self, handle: _JobHandle, exit_code: int) -> None:
        job = handle.job
        job.completed_at = time.time()
        handle.scratch_project_path.unlink(missing_ok=True)

        if job.status == "cancelled":
            return
        if exit_code != 0:
            job.status = "failed"
            job.error = handle.log_path.read_text(errors="ignore")[-2000:] or f"melt exited with code {exit_code}"
            return

        out_path = Path(job.output_path)
        if not out_path.exists() or out_path.stat().st_size == 0:
            job.status = "failed"
            job.error = "melt exited successfully but produced no output file"
            return

        try:
            meta = probe_media(out_path)
        except Exception as exc:  # noqa: BLE001 - report as a failed render, not a crash
            job.status = "failed"
            job.error = f"Output file is not valid media: {exc}"
            return

        if meta.duration <= 0:
            job.status = "failed"
            job.error = "Output file has zero duration -- render likely failed silently"
            return

        job.status = "completed"
        job.progress_percent = 100.0
        job.output_duration = meta.duration
        job.output_size_bytes = meta.size_bytes

    def cancel(self, job_id: str) -> RenderJob:
        handle = self._jobs.get(job_id)
        if handle is None:
            raise InvalidOperationError(f"Render job not found: {job_id}")
        job = handle.job
        if job.status != "running":
            raise InvalidOperationError(f"Render job '{job_id}' is not running (status: {job.status})")

        job.status = "cancelled"
        try:
            os.killpg(os.getpgid(handle.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            handle.process.terminate()
        handle.process.wait(timeout=10)
        job.completed_at = time.time()
        handle.scratch_project_path.unlink(missing_ok=True)
        Path(job.output_path).unlink(missing_ok=True)
        return job

    def list_jobs(self, project_id: str | None = None) -> list[RenderJob]:
        jobs = [h.job for h in self._jobs.values()]
        if project_id:
            jobs = [j for j in jobs if j.project_id == project_id]
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)


_manager = RenderManager()


def get_render_manager() -> RenderManager:
    return _manager


def supported_video_codecs() -> list[str]:
    return sorted(_VIDEO_CODECS)


def supported_audio_codecs() -> list[str]:
    return sorted(_AUDIO_CODECS)
