"""Regression test for the real root cause of clips vanishing on open:
Kdenlive's ProjectItemModel::loadBinPlaylist doesn't find main_bin by
scanning the XML tree -- it fetches it via MLT's xml_retain mechanism off
what it calls the "document tractor": one more outer
<tractor kdenlive:projectTractor="1"> wrapping the sequence tractor,
always the last top-level element in the file. Without both main_bin's own
xml_retain=1 property and this wrapper tractor, the bin is never
populated, so every timeline clip fails control_uuid resolution and gets
silently stripped by Kdenlive's own loader (melt never complained, which
is why this went unnoticed for so long -- confirmed against a real
project of ours, then against Kdenlive's real source and a real file's
structure to pin down exactly what was missing).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.timeline.model import new_project
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter


def _write(tmp_path) -> ET.Element:
    project = new_project("Project Tractor Test")
    media_index = MediaIndex(index_path=None)
    out_path = tmp_path / "project.kdenlive"
    KdenliveXmlWriter(project, media_index).write(out_path)
    return ET.fromstring(out_path.read_text())


def test_main_bin_has_xml_retain_property(tmp_path):
    root = _write(tmp_path)
    main_bin = root.find("playlist[@id='main_bin']")
    assert main_bin is not None
    props = {p.get("name"): p.text for p in main_bin.findall("property")}
    assert props.get("xml_retain") == "1"


def test_final_top_level_element_is_the_project_tractor(tmp_path):
    root = _write(tmp_path)
    children = list(root)
    last = children[-1]

    assert last.tag == "tractor", "the project tractor must be the last top-level element (MLT's default-producer convention)"
    props = {p.get("name"): p.text for p in last.findall("property")}
    assert props.get("kdenlive:projectTractor") == "1"

    tracks = last.findall("track")
    assert len(tracks) == 1, "the project tractor must have exactly one track, pointing at the sequence"

    # The sequence tractor is the second-to-last top-level element (written
    # right before the project tractor that wraps it).
    sequence_tractor = children[-2]
    assert tracks[0].get("producer") == sequence_tractor.get("id")


def test_project_tractor_spans_the_full_sequence_duration(tmp_path):
    from kdenlive_mcp.core.timeline import ops

    project = new_project("Duration Test")
    seq = project.active_sequence()
    v1 = seq.video_tracks()[0]
    clip = ops.add_clip(project, seq.id, v1.id, position=0, in_point=0, out_point=150,
                         clip_type="color")
    clip.color = "#000000ff"

    media_index = MediaIndex(index_path=None)
    out_path = tmp_path / "project.kdenlive"
    KdenliveXmlWriter(project, media_index).write(out_path)
    root = ET.fromstring(out_path.read_text())

    project_tractor = list(root)[-1]
    assert project_tractor.get("out") != "00:00:00.000"
