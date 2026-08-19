"""Regression test for a real bug: Kdenlive's own project loader (not
melt) validates each timeline clip via its producer's kdenlive:control_uuid
property and silently strips any clip whose producer lacks it or whose
chain is shared across multiple playlists (confirmed against Kdenlive's
real source, src/timeline2/model/builders/meltBuilder.cpp). melt itself
never complained, which is exactly why this went unnoticed until testing
against the real running app. Every asset used in a project must get: one
dedicated bin chain, one dedicated chain per timeline placement, and every
one of those instances must share the same kdenlive:control_uuid.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaAsset, MediaIndex, make_asset_id
from kdenlive_mcp.core.timeline import ops
from kdenlive_mcp.core.timeline.model import new_project
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter


def _props(el: ET.Element) -> dict[str, str]:
    return {p.get("name"): (p.text or "") for p in el.findall("property")}


def test_video_clip_gets_dedicated_bin_and_placement_chains(tmp_path):
    project = new_project("Dedicated Chain Test")
    seq = project.active_sequence()
    v1 = seq.video_tracks()[0]

    src = Path.home() / "Documents/kaam/day4/videos/clip1.mp4"
    media_index = MediaIndex(index_path=None)
    asset = MediaAsset(id=make_asset_id(src), path=str(src), kind="video", duration=10.0,
                        has_video=True, has_audio=True)
    media_index.upsert(asset)

    clip = ops.add_clip(project, seq.id, v1.id, position=0, in_point=0, out_point=300,
                         asset_id=asset.id, clip_type="video", name="clip1")

    out_path = tmp_path / "project.kdenlive"
    KdenliveXmlWriter(project, media_index).write(out_path)
    root = ET.fromstring(out_path.read_text())

    chains = root.findall("chain")
    matching = [c for c in chains if _props(c).get("resource") == str(src)]
    # One dedicated bin chain + one dedicated timeline-placement chain --
    # never a single chain referenced from both main_bin and a playlist.
    assert len(matching) == 2, f"expected 2 distinct chain elements for one asset, got {len(matching)}"
    assert len({c.get("id") for c in matching}) == 2, "the two chains must be distinct elements"

    control_uuids = {_props(c).get("kdenlive:control_uuid") for c in matching}
    assert len(control_uuids) == 1, "every instance of the same asset must share one control_uuid"
    uuid_value = next(iter(control_uuids))
    assert uuid_value and uuid_value.startswith("{") and uuid_value.endswith("}"), \
        f"control_uuid must be a real UUID string, got {uuid_value!r}"

    for c in matching:
        assert _props(c).get("kdenlive:control_uuid"), "every chain instance must carry kdenlive:control_uuid"

    main_bin = root.find("playlist[@id='main_bin']")
    bin_entry = main_bin.find("entry")
    assert bin_entry.get("producer") in {c.get("id") for c in matching}


def test_two_clips_of_different_assets_never_share_a_chain(tmp_path):
    project = new_project("Multi Asset Test")
    seq = project.active_sequence()
    v1 = seq.video_tracks()[0]

    media_index = MediaIndex(index_path=None)
    assets = []
    for name in ("clip1.mp4", "clip2.mp4"):
        src = Path.home() / "Documents/kaam/day4/videos" / name
        asset = MediaAsset(id=make_asset_id(src), path=str(src), kind="video", duration=10.0,
                            has_video=True, has_audio=True)
        media_index.upsert(asset)
        assets.append(asset)

    ops.add_clip(project, seq.id, v1.id, position=0, in_point=0, out_point=300,
                 asset_id=assets[0].id, clip_type="video", name="clip1")
    ops.add_clip(project, seq.id, v1.id, position=300, in_point=0, out_point=300,
                 asset_id=assets[1].id, clip_type="video", name="clip2")

    out_path = tmp_path / "project.kdenlive"
    KdenliveXmlWriter(project, media_index).write(out_path)
    root = ET.fromstring(out_path.read_text())

    chains = root.findall("chain")
    chain_ids = [c.get("id") for c in chains]
    assert len(chain_ids) == len(set(chain_ids)), "chain element ids must all be unique"

    control_uuid_by_asset: dict[str, set[str]] = {}
    for c in chains:
        props = _props(c)
        control_uuid_by_asset.setdefault(props.get("resource"), set()).add(props.get("kdenlive:control_uuid"))
    for resource, uuids in control_uuid_by_asset.items():
        assert len(uuids) == 1, f"asset {resource} has inconsistent control_uuid across its chain instances: {uuids}"
    assert len(control_uuid_by_asset) == 2
    all_uuids = {next(iter(v)) for v in control_uuid_by_asset.values()}
    assert len(all_uuids) == 2, "different assets must never share a control_uuid"
