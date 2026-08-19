"""Tests for real rendering via melt. Skipped if melt isn't available on
this machine; otherwise renders real, small clips from real project files
and verifies the output with ffprobe -- not mocked.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest

from kdenlive_mcp.kdenlive.adapter.xml_parser import KdenliveXmlParser
from kdenlive_mcp.media.ffmpeg.runner import is_available
from kdenlive_mcp.media.rendering.render_engine import RenderManager

REAL_PROJECT = Path.home() / "Videos/day4.kdenlive"

pytestmark = pytest.mark.skipif(not is_available("melt"), reason="melt not available on this machine")


@pytest.fixture()
def tmp_path(tmp_path):  # noqa: ARG001 - shadows pytest's own tmp_path fixture on purpose
    """melt (via `snap run`, see config._find_melt) runs inside Kdenlive's
    snap sandbox, which can only see paths under $HOME -- not /tmp, which
    is what pytest's built-in tmp_path fixture uses. Render tests need a
    real writable location melt can actually reach, so this shadows the
    stdlib fixture with a $HOME-rooted equivalent, cleaned up afterward."""
    home_tmp = Path.home() / ".kdenlive-mcp" / "test_scratch" / uuid.uuid4().hex
    home_tmp.mkdir(parents=True)
    try:
        yield home_tmp
    finally:
        shutil.rmtree(home_tmp, ignore_errors=True)


def _load_real_project():
    if not REAL_PROJECT.exists():
        pytest.skip(f"real project not found: {REAL_PROJECT}")
    parser = KdenliveXmlParser(REAL_PROJECT.read_text(), source_path=REAL_PROJECT)
    return parser.parse_project()


def _wait_for_completion(mgr: RenderManager, job_id: str, *, timeout: float = 60.0):
    deadline = time.time() + timeout
    job = mgr.poll(job_id)
    while job.status == "running" and time.time() < deadline:
        time.sleep(0.3)
        job = mgr.poll(job_id)
    return job


def test_render_produces_verified_output(tmp_path):
    project, media_index = _load_real_project()
    mgr = RenderManager()
    out_path = tmp_path / "rendered.mp4"

    job = mgr.start_render(project, media_index, output_path=str(out_path), end_seconds=2.0)
    assert job.status == "running"

    job = _wait_for_completion(mgr, job.id)
    assert job.status == "completed", job.error
    assert job.progress_percent == 100.0
    assert out_path.exists()
    assert job.output_duration is not None and job.output_duration > 0


def test_cancel_stops_process_and_removes_partial_output(tmp_path):
    project, media_index = _load_real_project()
    mgr = RenderManager()
    out_path = tmp_path / "cancelled.mp4"

    job = mgr.start_render(project, media_index, output_path=str(out_path))
    time.sleep(0.3)
    cancelled = mgr.cancel(job.id)

    assert cancelled.status == "cancelled"
    assert not out_path.exists()


def test_refuses_to_render_over_source_media():
    from kdenlive_mcp.errors import InvalidOperationError

    project, media_index = _load_real_project()
    mgr = RenderManager()
    source_asset = media_index.list()[0]

    with pytest.raises(InvalidOperationError):
        mgr.start_render(project, media_index, output_path=source_asset.path)


def test_rejects_unknown_codec(tmp_path):
    from kdenlive_mcp.errors import InvalidOperationError

    project, media_index = _load_real_project()
    mgr = RenderManager()

    with pytest.raises(InvalidOperationError):
        mgr.start_render(project, media_index, output_path=str(tmp_path / "x.mp4"),
                          video_codec="not_a_real_codec")
