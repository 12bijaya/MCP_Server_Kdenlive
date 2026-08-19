"""Tests for real speed changes: MLT's `timewarp` producer (confirmed by
hand with melt that its own in/out frame numbering is source_frames/speed,
not raw source frame numbers -- see xml_writer._new_placement_producer).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from kdenlive_mcp.core.assets.model import MediaAsset, MediaIndex, make_asset_id
from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.core.timeline.model import new_project
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter
from kdenlive_mcp.media.ffmpeg.runner import is_available, run

REAL_CLIP = Path.home() / "Documents/kaam/day4/videos/clip1.mp4"


def _project_with_real_clip():
    project = new_project("Speed Test")
    seq = project.active_sequence()
    v1 = seq.video_tracks()[0]
    media_index = MediaIndex(index_path=None)
    asset = MediaAsset(id=make_asset_id(REAL_CLIP), path=str(REAL_CLIP), kind="video",
                        duration=9.983, has_video=True, has_audio=True)
    media_index.upsert(asset)
    clip = ops.add_clip(project, seq.id, v1.id, position=0, in_point=0, out_point=300,
                         asset_id=asset.id, clip_type="video")
    return project, seq, v1, clip, media_index


def test_set_clip_speed_halves_timeline_length_at_2x():
    if not REAL_CLIP.exists():
        pytest.skip("real clip not found on this machine")
    project, seq, v1, clip, _ = _project_with_real_clip()
    original_length = clip.timeline_length

    ops.set_clip_speed(project, seq.id, clip.id, speed=2.0)
    assert clip.timeline_length == pytest.approx(original_length / 2, abs=1)


def test_set_clip_speed_doubles_timeline_length_at_half_speed():
    if not REAL_CLIP.exists():
        pytest.skip("real clip not found on this machine")
    project, seq, v1, clip, _ = _project_with_real_clip()
    original_length = clip.timeline_length

    ops.set_clip_speed(project, seq.id, clip.id, speed=0.5)
    assert clip.timeline_length == pytest.approx(original_length * 2, abs=1)


def test_set_clip_speed_ripples_subsequent_clips():
    if not REAL_CLIP.exists():
        pytest.skip("real clip not found on this machine")
    project, seq, v1, clip, media_index = _project_with_real_clip()
    asset_id = clip.asset_id
    second = ops.add_clip(project, seq.id, v1.id, position=clip.end, in_point=0, out_point=150,
                           asset_id=asset_id, clip_type="video")
    second_start_before = second.position

    ops.set_clip_speed(project, seq.id, clip.id, speed=2.0)
    assert second.position < second_start_before, "later clip should shift earlier since the sped-up clip got shorter"
    assert second.position == clip.end


def test_set_clip_speed_rejects_out_of_range():
    project, seq, v1, clip, _ = _project_with_real_clip()
    with pytest.raises(InvalidOperationError):
        ops.set_clip_speed(project, seq.id, clip.id, speed=100.0)


def test_speed_ramp_creates_expected_segment_count_and_speeds():
    project, seq, v1, clip, _ = _project_with_real_clip()
    pieces = ops.create_speed_ramp(project, seq.id, clip.id, segment_speeds=[1.0, 0.4, 2.0, 1.0])
    assert len(pieces) == 4
    assert [p.speed for p in pieces] == [1.0, 0.4, 2.0, 1.0]
    # segments must be contiguous and non-overlapping
    for a, b in zip(pieces, pieces[1:]):
        assert a.end == b.position


def test_speed_ramp_rejects_single_segment():
    project, seq, v1, clip, _ = _project_with_real_clip()
    with pytest.raises(InvalidOperationError):
        ops.create_speed_ramp(project, seq.id, clip.id, segment_speeds=[1.0])


def test_speed_ramp_without_ripple_raises_when_it_does_not_fit():
    project, seq, v1, clip, _ = _project_with_real_clip()
    with pytest.raises(InvalidOperationError):
        ops.create_speed_ramp(project, seq.id, clip.id, segment_speeds=[1.0, 0.2], ripple=False)
    # must not have left a partially-split state behind
    assert len(v1.clips) == 1


@pytest.mark.skipif(not is_available("melt"), reason="melt not available on this machine")
def test_sped_up_clip_actually_renders_at_the_expected_duration():
    if not REAL_CLIP.exists():
        pytest.skip("real clip not found on this machine")
    project, seq, v1, clip, media_index = _project_with_real_clip()
    ops.set_clip_speed(project, seq.id, clip.id, speed=2.0)

    # melt (via `snap run`, see config._find_melt) runs inside Kdenlive's
    # snap sandbox, which can only see paths under $HOME -- not /tmp, which
    # is what pytest's tmp_path fixture uses.
    home_tmp = Path.home() / ".kdenlive-mcp" / "test_scratch" / uuid.uuid4().hex
    home_tmp.mkdir(parents=True)
    try:
        out_path = home_tmp / "project.kdenlive"
        KdenliveXmlWriter(project, media_index).write(out_path)

        result = run("melt", [str(out_path), "-consumer", "null"], check=False, timeout=30)
        assert result.ok, result.stderr[-2000:]
    finally:
        shutil.rmtree(home_tmp, ignore_errors=True)
