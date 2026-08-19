"""Tests for replace_scene -- a composite op idea adopted from studying
D-Ogi/mcp-kdenlive's tool list, built here on top of the existing
add_clip/trim_clip/split_clip primitives rather than duplicating their
overlap-validation logic.
"""

from __future__ import annotations

import pytest

from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.core.timeline.model import new_project
from kdenlive_mcp.errors import InvalidOperationError


def _project_with_track():
    project = new_project("Replace Scene Test")
    seq = project.active_sequence()
    track = seq.video_tracks()[0]
    return project, seq, track


def test_replace_fully_contained_clip():
    project, seq, track = _project_with_track()
    ops.add_clip(project, seq.id, track.id, position=0, in_point=0, out_point=100, clip_type="color")

    new_clip, removed = ops.replace_scene(
        project, seq.id, track.id, start_frame=0, end_frame=100,
        in_point=0, out_point=100, clip_type="color",
    )
    assert len(removed) == 1
    assert len(track.clips) == 1
    assert track.clips[0].id == new_clip.id


def test_replace_trims_partially_overlapping_clips():
    project, seq, track = _project_with_track()
    left = ops.add_clip(project, seq.id, track.id, position=0, in_point=0, out_point=60, clip_type="color")
    right = ops.add_clip(project, seq.id, track.id, position=80, in_point=0, out_point=60, clip_type="color")

    new_clip, removed = ops.replace_scene(
        project, seq.id, track.id, start_frame=50, end_frame=90,
        in_point=0, out_point=40, clip_type="color",
    )
    assert removed == []
    assert left.end == 50, "left clip should be trimmed back to the replacement start"
    assert right.position == 90, "right clip should be trimmed forward to the replacement end"
    assert new_clip.position == 50
    assert new_clip.end == 90


def test_replace_splits_a_clip_that_fully_contains_the_range():
    project, seq, track = _project_with_track()
    ops.add_clip(project, seq.id, track.id, position=0, in_point=0, out_point=200, clip_type="color")

    new_clip, removed = ops.replace_scene(
        project, seq.id, track.id, start_frame=50, end_frame=100,
        in_point=0, out_point=50, clip_type="color",
    )
    assert len(removed) == 1
    remaining = sorted(track.clips, key=lambda c: c.position)
    assert len(remaining) == 3  # left remainder, new clip, right remainder
    assert remaining[0].end == 50
    assert remaining[1].id == new_clip.id
    assert remaining[2].position == 100
    assert remaining[2].end == 200


def test_replace_scene_rejects_invalid_range():
    project, seq, track = _project_with_track()
    with pytest.raises(InvalidOperationError):
        ops.replace_scene(project, seq.id, track.id, start_frame=50, end_frame=50,
                           in_point=0, out_point=10, clip_type="color")
