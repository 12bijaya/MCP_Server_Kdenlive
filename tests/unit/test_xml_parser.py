"""Tests for the Kdenlive XML parser against real project files.

Kdenlive doesn't always write clip/blank "length" as a plain frame count --
some producers on real projects (color/qimage-backed clips, in particular)
write it as a timecode string like "00:00:05.000" instead. This was found
by opening a real user project and hit a bare int() call; both the bin
producer length and the playlist <blank> length had the same bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_mcp.kdenlive.adapter.xml_parser import KdenliveXmlParser
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter

REAL_PROJECTS = [
    Path.home() / "Videos/day1.kdenlive",
    Path.home() / "Videos/day4.kdenlive",
    Path.home() / "Videos/day02.kdenlive",
]


def _require_project(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"real project not found on this machine: {path}")
    return path


@pytest.mark.parametrize("path", REAL_PROJECTS)
def test_parses_real_project_without_error(path: Path):
    path = _require_project(path)
    parser = KdenliveXmlParser(path.read_text(), source_path=path)
    project, media_index = parser.parse_project()

    assert project.sequences
    seq = project.sequences[0]
    assert seq.tracks
    total_clips = sum(len(t.clips) for t in seq.tracks)
    assert total_clips > 0


def test_timecode_style_length_values_parse_correctly():
    """Regression test for the specific bug: length="00:00:05.000" style
    values (both on bin producers and playlist <blank> gaps) must not
    raise ValueError from a bare int()."""
    path = _require_project(Path.home() / "Videos/day1.kdenlive")
    parser = KdenliveXmlParser(path.read_text(), source_path=path)
    project, media_index = parser.parse_project()

    seq = project.sequences[0]
    video_tracks = [t for t in seq.tracks if t.track_type == "video" and t.clips]
    assert video_tracks, "expected at least one video track with clips"
    for clip in video_tracks[0].clips:
        assert clip.position >= 0
        assert clip.out_point > clip.in_point


def test_parsed_project_round_trips_through_writer():
    path = _require_project(Path.home() / "Videos/day1.kdenlive")
    parser = KdenliveXmlParser(path.read_text(), source_path=path)
    project, media_index = parser.parse_project()

    writer = KdenliveXmlWriter(project, media_index)
    xml_text = writer.to_string()

    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    assert root.tag == "mlt"
