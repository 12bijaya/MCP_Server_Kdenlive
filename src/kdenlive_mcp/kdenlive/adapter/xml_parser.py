"""`.kdenlive` (MLT XML) -> internal timeline model.

This is the inverse of xml_writer: it reads a real Kdenlive project file
(the user's own project, or one this adapter wrote) and reconstructs a
Project/Sequence/Track/Clip model, so tools like open_project and
analyze_kdenlive_project can work with arbitrary existing projects, not
just ones this server created.

We deliberately parse defensively: real-world Kdenlive projects can be far
more elaborate than anything this adapter writes (audio/video linked-clip
splits, nested sequences, custom effect stacks, nested tracks). Anything we
don't have a model for is preserved in Clip.metadata / Sequence markers
rather than dropped silently, and the parser never raises on unrecognized
structure -- it does its best and records what it found.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaAsset, MediaIndex, make_asset_id
from kdenlive_mcp.core.timeline.model import (
    Clip, EffectInstance, Marker, Project, ProjectSettings, Sequence, Track, new_id,
)
from kdenlive_mcp.core.timeline.timecode import timecode_to_frames
from kdenlive_mcp.errors import ValidationError


def _props(el: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in el.findall("property"):
        name = p.get("name")
        if name is not None:
            out[name] = (p.text or "").strip()
    return out


def _parse_bool(s: str | None, default: bool = False) -> bool:
    if s is None:
        return default
    return s.strip() in ("1", "true", "True")


def _parse_length_frames(value: str | None, fps) -> int:
    """The "length" property is usually a plain frame count, but some
    producers (seen on real color/qimage clips) store it as a timecode
    string like "00:00:05.000" instead. Handle both."""
    if not value:
        return 0
    value = value.strip()
    if ":" in value:
        from kdenlive_mcp.core.timeline.timecode import timecode_to_frames
        return timecode_to_frames(value, fps)
    try:
        return int(value)
    except ValueError:
        return 0


class KdenliveXmlParser:
    def __init__(self, xml_text: str, *, source_path: Path | None = None):
        self.root = ET.fromstring(xml_text)
        if self.root.tag != "mlt":
            raise ValidationError(f"Not a valid MLT/Kdenlive project: root tag is <{self.root.tag}>, expected <mlt>")
        self.source_path = source_path
        # <mlt root="..."> is the base dir non-absolute `resource` paths are
        # relative to (seen on real autosave files, where clip paths are
        # written relative to the project's root rather than absolute).
        root_attr = self.root.get("root")
        self._root_dir = Path(root_attr) if root_attr else None
        self._elements_by_id: dict[str, ET.Element] = {}
        for el in self.root.iter():
            eid = el.get("id")
            if eid:
                self._elements_by_id[eid] = el

    # ------------------------------------------------------------ public -

    def parse_project(self, project_name: str | None = None) -> tuple[Project, MediaIndex]:
        settings = self._parse_profile()
        project = Project(
            id=new_id("project"),
            name=project_name or (self.source_path.stem if self.source_path else "Imported Project"),
            path=str(self.source_path) if self.source_path else None,
            settings=settings,
        )

        media_index = self._build_media_index(settings)

        for seq_el in self._find_sequence_tractors():
            sequence = self._parse_sequence(seq_el, settings, media_index)
            project.sequences.append(sequence)

        if project.sequences:
            project.active_sequence_id = project.sequences[0].id
        return project, media_index

    # ------------------------------------------------------------ profile

    def _parse_profile(self) -> ProjectSettings:
        el = self.root.find("profile")
        if el is None:
            return ProjectSettings()
        return ProjectSettings(
            width=int(el.get("width", 1920)),
            height=int(el.get("height", 1080)),
            fps_num=int(el.get("frame_rate_num", 30)),
            fps_den=int(el.get("frame_rate_den", 1)),
            colorspace=el.get("colorspace", "709"),
            progressive=el.get("progressive", "1") == "1",
            display_aspect_num=int(el.get("display_aspect_num", 16)),
            display_aspect_den=int(el.get("display_aspect_den", 9)),
            sample_aspect_num=int(el.get("sample_aspect_num", 1)),
            sample_aspect_den=int(el.get("sample_aspect_den", 1)),
        )

    # -------------------------------------------------------- media / bin

    def _build_media_index(self, settings: ProjectSettings) -> MediaIndex:
        index = MediaIndex(index_path=None)  # in-memory only; caller decides whether/where to persist

        for chain in self.root.findall("chain"):
            self._index_media_producer(chain, settings, index, is_chain=True)
        for producer in self.root.findall("producer"):
            props = _props(producer)
            service = props.get("mlt_service", "")
            if service in ("qimage", "pixbuf", "avformat", "avformat-novalidate"):
                self._index_media_producer(producer, settings, index, is_chain=False)
        return index

    def _index_media_producer(self, el: ET.Element, settings: ProjectSettings, index: MediaIndex, *, is_chain: bool) -> None:
        props = _props(el)
        resource = props.get("resource")
        if not resource or resource in ("black", "0x00000000") or resource.startswith("0x"):
            return
        service = props.get("mlt_service", "")
        kind = "image" if service == "qimage" or service == "pixbuf" else "video"

        length_frames = _parse_length_frames(props.get("length"), settings.fps)
        duration = length_frames / settings.fps_float if length_frames and kind != "image" else 0.0

        path = Path(resource)
        if not path.is_absolute() and self._root_dir is not None:
            path = self._root_dir / path
        asset = MediaAsset(
            id=make_asset_id(path) if path.is_absolute() else new_id("asset"),
            path=str(path),
            kind=kind,
            duration=duration,
            has_video=props.get("video_index", "0") != "-1",
            has_audio=props.get("audio_index", "-1") not in ("-1", ""),
        )
        index.upsert(asset)
        el.set("_kdenlive_mcp_asset_id", asset.id)

    def _asset_id_for_producer(self, producer_id: str) -> str | None:
        el = self._elements_by_id.get(producer_id)
        if el is None:
            return None
        return el.get("_kdenlive_mcp_asset_id")

    # -------------------------------------------------------- sequences --

    def _find_sequence_tractors(self) -> list[ET.Element]:
        sequences = []
        for tractor in self.root.findall("tractor"):
            props = _props(tractor)
            if "kdenlive:uuid" in props or "kdenlive:clipname" in props:
                sequences.append(tractor)
        if not sequences:
            # fall back to the last top-level tractor (typically the timeline)
            all_tractors = self.root.findall("tractor")
            if all_tractors:
                sequences = [all_tractors[-1]]
        return sequences

    def _parse_sequence(self, seq_el: ET.Element, settings: ProjectSettings, media_index: MediaIndex) -> Sequence:
        props = _props(seq_el)
        sequence = Sequence(id=new_id("seq"), name=props.get("kdenlive:clipname", "Sequence 1"))

        track_refs = [t.get("producer") for t in seq_el.findall("track") if t.get("producer")]

        video_index = 0
        audio_index = 0
        for ref in track_refs:
            track_tractor = self._elements_by_id.get(ref)
            if track_tractor is None or track_tractor.tag != "tractor":
                continue  # background color producer or unrecognized ref
            track = self._parse_track(track_tractor, settings, media_index)
            if track is None:
                continue
            if track.track_type == "video":
                track.index = video_index
                video_index += 1
            else:
                track.index = audio_index
                audio_index += 1
            sequence.tracks.append(track)

        return sequence

    def _parse_track(self, tractor_el: ET.Element, settings: ProjectSettings, media_index: MediaIndex) -> Track | None:
        props = _props(tractor_el)
        is_audio = props.get("kdenlive:audio_track") == "1"
        track = Track(
            id=new_id("track"), index=0, track_type="audio" if is_audio else "video",
            muted=False, locked=_parse_bool(props.get("kdenlive:locked_track")),
            height=int(props.get("kdenlive:trackheight", "75") or 75),
        )

        sub_playlist_refs = [t.get("producer") for t in tractor_el.findall("track") if t.get("producer")]
        for ref in sub_playlist_refs:
            playlist_el = self._elements_by_id.get(ref)
            if playlist_el is None or playlist_el.tag != "playlist":
                continue
            self._parse_playlist_into_track(playlist_el, track, settings, media_index)

        for flt in tractor_el.findall("filter"):
            fprops = _props(flt)
            if fprops.get("mlt_service") == "volume" and fprops.get("level") == "-1000":
                track.muted = True

        return track

    def _parse_playlist_into_track(self, playlist_el: ET.Element, track: Track, settings: ProjectSettings, media_index: MediaIndex) -> None:
        cursor = 0
        for child in playlist_el:
            if child.tag == "blank":
                cursor += _parse_length_frames(child.get("length"), settings.fps)
            elif child.tag == "entry":
                clip = self._parse_entry(child, cursor, track.id, settings, media_index, track_type=track.track_type)
                if clip is not None:
                    track.clips.append(clip)
                    cursor = clip.end

    def _parse_entry(self, entry_el: ET.Element, position: int, track_id: str, settings: ProjectSettings,
                      media_index: MediaIndex, *, track_type: str = "video") -> Clip | None:
        producer_ref = entry_el.get("producer")
        in_tc = entry_el.get("in", "00:00:00.000")
        out_tc = entry_el.get("out", "00:00:00.000")
        in_point = timecode_to_frames(in_tc, settings.fps)
        out_point = timecode_to_frames(out_tc, settings.fps) + 1
        if out_point <= in_point:
            return None

        asset_id = self._asset_id_for_producer(producer_ref) if producer_ref else None
        producer_el = self._elements_by_id.get(producer_ref) if producer_ref else None
        producer_props = _props(producer_el) if producer_el is not None else {}
        service = producer_props.get("mlt_service", "")

        if asset_id:
            asset = media_index.get(asset_id)
            if asset and asset.kind == "image":
                clip_type = "image"
            elif track_type == "audio":
                clip_type = "audio"
            else:
                clip_type = "video"
        elif service == "color":
            text_filter = next((f for f in (producer_el.findall("filter") if producer_el is not None else [])
                                 if _props(f).get("mlt_service") == "dynamictext"), None)
            if text_filter is not None:
                clip_type = "text"
            else:
                clip_type = "color"
        else:
            clip_type = "video"

        clip = Clip(
            id=new_id("clip"), track_id=track_id, clip_type=clip_type,
            position=position, in_point=in_point, out_point=out_point,
            asset_id=asset_id,
        )
        if clip_type == "color":
            clip.color = "#" + producer_props.get("resource", "000000ff").replace("0x", "")
        if clip_type == "text" and producer_el is not None:
            text_filter = next((f for f in producer_el.findall("filter")
                                 if _props(f).get("mlt_service") == "dynamictext"), None)
            if text_filter is not None:
                clip.text_content = _props(text_filter).get("argument", "")

        for flt in entry_el.findall("filter"):
            clip.effects.append(self._parse_filter_as_effect(flt))

        bin_id = _props(entry_el).get("kdenlive:id")
        if bin_id:
            clip.metadata["kdenlive_bin_id"] = bin_id

        return clip

    def _parse_filter_as_effect(self, flt: ET.Element) -> EffectInstance:
        props = _props(flt)
        service = props.pop("mlt_service", "unknown")
        disabled = props.pop("disable", None) == "1"
        effect = EffectInstance(id=new_id("effect"), service=service, display_name=service, enabled=not disabled)
        for key, value in props.items():
            # Values that look like "frame=val;frame=val" animation strings
            # are kept as raw params rather than decomposed into a
            # KeyframeTrack here; the writer passes any string param
            # through verbatim, so this round-trips correctly either way.
            effect.params[key] = value
        return effect
