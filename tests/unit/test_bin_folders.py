"""Tests for bin/folder organization -- an idea adopted from studying
D-Ogi/mcp-kdenlive's tool list (create_bin_folder / get_media_pool),
implemented here as flat folders written to the real
kdenlive:folder.-1.<id> / kdenlive:folderid properties Kdenlive itself
uses, so a project this adapter writes shows real folders in Kdenlive's
own bin panel, not just an internal-only concept.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaAsset, MediaIndex, make_asset_id
from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.core.timeline.model import new_project
from kdenlive_mcp.kdenlive.adapter.xml_parser import KdenliveXmlParser
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter


def _props(el: ET.Element) -> dict[str, str]:
    return {p.get("name"): (p.text or "") for p in el.findall("property")}


def test_media_index_groups_by_folder():
    index = MediaIndex(index_path=None)
    a = MediaAsset(id="asset_a", path="/x/a.mp4", kind="video", folder="B-Roll")
    b = MediaAsset(id="asset_b", path="/x/b.mp4", kind="video", folder="B-Roll")
    c = MediaAsset(id="asset_c", path="/x/c.mp4", kind="video", folder=None)
    for asset in (a, b, c):
        index.upsert(asset)

    assert index.list_folders() == ["B-Roll"]
    assert {a.id for a in index.list_by_folder("B-Roll")} == {"asset_a", "asset_b"}
    assert {a.id for a in index.list_by_folder(None)} == {"asset_c"}


def test_folder_round_trips_through_writer_and_parser(tmp_path):
    project = new_project("Folder Test")
    seq = project.active_sequence()
    v1 = seq.video_tracks()[0]

    src = Path.home() / "Documents/kaam/day4/videos/clip1.mp4"
    media_index = MediaIndex(index_path=None)
    asset = MediaAsset(id=make_asset_id(src), path=str(src), kind="video", duration=10.0,
                        has_video=True, has_audio=True, folder="Interviews")
    media_index.upsert(asset)
    ops.add_clip(project, seq.id, v1.id, position=0, in_point=0, out_point=300,
                 asset_id=asset.id, clip_type="video")

    out_path = tmp_path / "project.kdenlive"
    KdenliveXmlWriter(project, media_index).write(out_path)
    root = ET.fromstring(out_path.read_text())

    main_bin = root.find("playlist[@id='main_bin']")
    main_bin_props = _props(main_bin)
    folder_keys = [k for k in main_bin_props if k.startswith("kdenlive:folder.-1.")]
    assert len(folder_keys) == 1
    assert main_bin_props[folder_keys[0]] == "Interviews"
    folder_id = folder_keys[0].rsplit(".", 1)[-1]

    chains_with_folder = [c for c in root.findall("chain") if _props(c).get("kdenlive:folderid") == folder_id]
    assert len(chains_with_folder) >= 1

    parser = KdenliveXmlParser(out_path.read_text(), source_path=out_path)
    reparsed_project, reparsed_index = parser.parse_project()
    reparsed_asset = reparsed_index.get(asset.id) or next(iter(reparsed_index.list()))
    assert reparsed_asset.folder == "Interviews"
