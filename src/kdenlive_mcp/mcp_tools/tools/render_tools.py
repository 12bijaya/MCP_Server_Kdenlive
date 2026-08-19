"""RENDERING tools (spec section 14 / 20).

Rendering is genuinely long-running, so start_render returns immediately
with a job id; poll_render_status must be called repeatedly to track
progress and learn when it's done. A job is only ever reported "completed"
after the output file has been verified as real, playable media via
ffprobe -- a process exiting 0 does not by itself prove that.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.media.rendering.render_engine import (
    get_render_manager, supported_audio_codecs, supported_video_codecs,
)
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, tool_result


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def start_render(output_path: str, video_codec: str = "h264", audio_codec: str = "aac",
                      start_seconds: float | None = None, end_seconds: float | None = None,
                      width: int | None = None, height: int | None = None,
                      project_id: str | None = None) -> dict:
        """Start rendering the active sequence to a real video file via melt.
        Returns immediately with a job id -- call poll_render_status to track it.

        video_codec: one of h264, h265, vp9, prores. audio_codec: aac, mp3, opus.
        Omit start_seconds/end_seconds to render the whole sequence.
        width/height together scale the output; omit to render at project resolution.
        """
        session = get_state().get(project_id)
        job = get_render_manager().start_render(
            session.project, session.media_index, output_path=output_path,
            video_codec=video_codec, audio_codec=audio_codec,
            start_seconds=start_seconds, end_seconds=end_seconds, width=width, height=height,
        )
        return tool_result(job=job.to_dict())

    @mcp.tool()
    @catch_errors
    def poll_render_status(job_id: str) -> dict:
        """Check a render job's progress. Call this repeatedly until status
        is "completed", "failed", or "cancelled"."""
        job = get_render_manager().poll(job_id)
        return tool_result(job=job.to_dict())

    @mcp.tool()
    @catch_errors
    def cancel_render(job_id: str) -> dict:
        """Cancel a running render job and delete its partial output."""
        job = get_render_manager().cancel(job_id)
        return tool_result(job=job.to_dict())

    @mcp.tool()
    @catch_errors
    def list_render_jobs(project_id: str | None = None) -> dict:
        """List render jobs, optionally filtered to one project. Most recent first."""
        jobs = get_render_manager().list_jobs(project_id)
        return tool_result(jobs=[j.to_dict() for j in jobs])

    @mcp.tool()
    @catch_errors
    def list_render_codecs() -> dict:
        """List supported video/audio codecs for start_render."""
        return tool_result(video_codecs=supported_video_codecs(), audio_codecs=supported_audio_codecs())
