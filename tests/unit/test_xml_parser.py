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


# Minimal but real MLT/Kdenlive XML: one video chain referenced by a
# relative `resource`, root pointing at a fake home dir. Mirrors what
# Kdenlive's own autosave (KAutoSaveFile-style) files look like -- they
# store resource paths relative to <mlt root="..."> and are never named
# "*.kdenlive", both of which real-world testing found unhandled.
_MINIMAL_PROJECT_XML = """<?xml version='1.0' encoding='utf-8'?>
<mlt LC_NUMERIC="C" root="/home/someuser" producer="main_bin" version="7.38.0">
 <profile colorspace="709" description="HD 1080p 30 fps" display_aspect_den="9"
          display_aspect_num="16" frame_rate_den="1" frame_rate_num="30" height="1080"
          progressive="1" sample_aspect_den="1" sample_aspect_num="1" width="1920"/>
 <chain id="chain0" out="00:00:04.999">
  <property name="length">150</property>
  <property name="resource">footage/clip.mp4</property>
  <property name="mlt_service">avformat-novalidate</property>
  <property name="video_index">0</property>
  <property name="audio_index">1</property>
 </chain>
 <playlist id="playlist0">
  <entry producer="chain0" in="00:00:00.000" out="00:00:04.999"/>
 </playlist>
 <playlist id="playlist1"/>
 <tractor id="tractor0" in="00:00:00.000">
  <track producer="playlist0"/>
  <track producer="playlist1"/>
 </tractor>
 <tractor id="{seq-uuid}" in="00:00:00.000" out="00:00:04.999">
  <property name="kdenlive:uuid">{seq-uuid}</property>
  <property name="kdenlive:clipname">Sequence 1</property>
  <track producer="tractor0"/>
 </tractor>
</mlt>
"""


def test_relative_resource_resolves_against_mlt_root():
    """resource="footage/clip.mp4" + root="/home/someuser" must resolve to
    the absolute path /home/someuser/footage/clip.mp4, not stay relative."""
    parser = KdenliveXmlParser(_MINIMAL_PROJECT_XML)
    project, media_index = parser.parse_project()

    assets = media_index.list()
    assert len(assets) == 1
    assert assets[0].path == "/home/someuser/footage/clip.mp4"


def test_open_project_does_not_require_kdenlive_extension(tmp_path):
    """Kdenlive's own autosave files are real project XML but never named
    "*.kdenlive" (e.g. "_untitled.kdenlivewtpfile_...") -- open_project must
    accept any file whose *content* is valid MLT XML."""
    from kdenlive_mcp.kdenlive.adapter.project import open_project

    autosave_style_path = tmp_path / "_untitled.kdenlivewtpfile_someRandomSuffix"
    autosave_style_path.write_text(_MINIMAL_PROJECT_XML)

    project, media_index = open_project(str(autosave_style_path))
    assert project.sequences
    assert media_index.list()


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
