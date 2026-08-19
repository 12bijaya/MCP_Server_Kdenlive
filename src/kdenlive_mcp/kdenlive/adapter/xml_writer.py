"""Internal timeline model -> `.kdenlive` (MLT XML) writer.

Ground truth for every structural choice here was taken from real Kdenlive
26.04.3 project files (not guessed), specifically:

  - Every timeline track is a `<tractor>` wrapping exactly two `<playlist>`
    children (one carries the clips, the other is kept empty) -- this is
    Kdenlive's current on-disk track representation and is present on
    every track in every real project inspected, used or not.
  - The compositing chain always uses a_track="0" (the master tractor's
    background/black `producer0`) paired against b_track=<position of this
    track in the master tractor's track list>, with service "qtblend" for
    video tracks and "mix" for audio tracks. Kdenlive stacks video/audio
    tracks by chaining each one against the background rather than against
    each other pairwise.
  - `<chain>` (not `<producer>`) is used specifically for avformat media
    (audio/video files); still-image and generated producers (color,
    qimage, text-via-dynamictext) use plain `<producer>`.
  - Keyframed params on Kdenlive's Transform effect ("qtblend") use an
    "animation string" of `frame=value;frame=value;...`, where the "rect"
    param's value is "x y w h opacity" space-separated.

We deliberately do NOT replicate Kdenlive's audio/video "linked clip"
split (where one bin clip becomes two separate producer instances, one
video-only on a video track and one audio-only on a paired audio track).
That's a decoding-performance optimization internal to Kdenlline's own
GUI, not a requirement of the file format: a single chain, referenced from
however many playlist entries need it, is valid MLT and keeps this adapter
far simpler. A video clip's own audio plays through its video track
normally as a result (no `hide="audio"` is set on video-track tracks).
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_mcp.core.assets.model import MediaAsset, MediaIndex
from kdenlive_mcp.core.keyframes.easing import sample_track
from kdenlive_mcp.core.timeline.model import (
    Clip, EffectInstance, KeyframeTrack, Project, Sequence, Track,
)
from kdenlive_mcp.core.timeline.timecode import frames_to_timecode
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.adapter.profiles import profile_description, profile_name_hint

MLT_VERSION = "7.9.0"


def _fmt_num(v: float | int) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if float(v).is_integer():
        return str(int(v))
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _fmt_value(value) -> str:
    if isinstance(value, (tuple, list)):
        return " ".join(_fmt_num(v) for v in value)
    if isinstance(value, str):
        # Already text: a plugin/service id, a color, a pre-built animation
        # string round-tripped from an existing project, etc. -- only
        # numeric Python values go through _fmt_num's number formatting.
        return value
    return _fmt_num(value)


def _color_to_mlt(color: str | None) -> str:
    """Accepts "#RRGGBB" or "#RRGGBBAA"; returns MLT's 0xRRGGBBAA form."""
    if not color:
        return "0x000000ff"
    c = color.lstrip("#")
    if len(c) == 6:
        c += "ff"
    if len(c) != 8:
        raise InvalidOperationError(f"Invalid color '{color}', expected #RRGGBB or #RRGGBBAA")
    return f"0x{c}"


class _IdAllocator:
    def __init__(self):
        self._counters: dict[str, int] = {}
        self._next_bin_id = 2

    def next(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0)
        self._counters[prefix] = n + 1
        return f"{prefix}{n}"

    def next_bin_id(self) -> int:
        val = self._next_bin_id
        self._next_bin_id += 1
        return val


def _sub(parent: ET.Element, tag: str, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag)
    for k, v in attrs.items():
        if v is None:
            continue
        el.set(k, str(v))
    return el


def _prop(parent: ET.Element, name: str, value=None) -> ET.Element:
    el = ET.SubElement(parent, "property", {"name": name})
    if value is not None:
        el.text = str(value)
    return el


class KdenliveXmlWriter:
    """Serializes a Project (+ its MediaIndex) into `.kdenlive` XML text."""

    def __init__(self, project: Project, media_index: MediaIndex):
        self.project = project
        self.media_index = media_index
        self.ids = _IdAllocator()
        self._asset_chain_id: dict[str, str] = {}  # asset_id -> the BIN's own dedicated chain (main_bin only)
        self._asset_bin_id: dict[str, int] = {}
        # asset_id -> a UUID shared by every producer/chain instance of that
        # asset (bin chain + every timeline placement). This, not
        # kdenlive:id, is what Kdenlive's own project loader actually keys
        # a timeline clip's "bin reference" validation on (meltBuilder.cpp:
        # reads kdenlive:control_uuid off the timeline clip's producer and
        # drops the clip if it's missing/invalid) -- confirmed against
        # Kdenlive's real source after a real project of ours got its
        # clips silently stripped on open without this property.
        self._asset_control_uuid: dict[str, str] = {}
        self._clip_placement_id: dict[str, str] = {}  # clip.id -> its own dedicated timeline producer/chain
        self._color_producer_id: dict[str, str] = {}
        self._target_path: Path | None = None

    # ------------------------------------------------------------ public -

    def to_string(self, *, target_path: Path | None = None) -> str:
        """`target_path` (the eventual .kdenlive location, if known) is
        needed to place the sibling <name>.kdenlive.srt subtitle file at
        the right path; without it (e.g. building an in-memory-only XML
        string) subtitles are silently omitted rather than guessing a path
        -- see _write_subtitles."""
        self._target_path = target_path
        root = self._build()
        ET.indent(root, space=" ")
        return ET.tostring(root, encoding="unicode", xml_declaration=False)

    def write(self, path: Path) -> None:
        xml_text = "<?xml version='1.0' encoding='utf-8'?>\n" + self.to_string(target_path=path) + "\n"
        path.write_text(xml_text, encoding="utf-8")

    # ----------------------------------------------------------- builder -

    def _build(self) -> ET.Element:
        project = self.project
        settings = project.settings

        project_dir = str(Path(project.path).resolve().parent) if project.path else str(Path.cwd())
        mlt = ET.Element("mlt", {
            "LC_NUMERIC": "C", "producer": "main_bin", "root": project_dir, "version": MLT_VERSION,
        })

        _sub(mlt, "profile",
             colorspace=settings.colorspace,
             description=profile_description(settings),
             display_aspect_den=settings.display_aspect_den,
             display_aspect_num=settings.display_aspect_num,
             frame_rate_den=settings.fps_den,
             frame_rate_num=settings.fps_num,
             height=settings.height,
             progressive="1" if settings.progressive else "0",
             sample_aspect_den=settings.sample_aspect_den,
             sample_aspect_num=settings.sample_aspect_num,
             width=settings.width)

        producer0 = _sub(mlt, "producer", id="producer0", **{"in": "00:00:00.000"}, out=frames_to_timecode(21600 * int(settings.fps), settings.fps))
        _prop(producer0, "length", "2147483647")
        _prop(producer0, "eof", "continue")
        _prop(producer0, "resource", "black")
        _prop(producer0, "aspect_ratio", "1")
        _prop(producer0, "mlt_service", "color")
        _prop(producer0, "kdenlive:playlistid", "black_track")
        _prop(producer0, "mlt_image_format", "rgba")

        sequence = project.active_sequence()
        if sequence is None:
            raise InvalidOperationError("Project has no active sequence to write")

        used_assets = self._collect_used_assets()
        for asset in used_assets:
            self._write_bin_producer(mlt, asset)

        self._write_text_and_color_producers(mlt)
        self._write_placement_producers(mlt, sequence)

        main_bin = _sub(mlt, "playlist", id="main_bin")
        self._write_doc_properties(main_bin)
        for asset in used_assets:
            chain_id = self._asset_chain_id[asset.id]
            dur_frames = max(1, round(asset.duration * settings.fps_float)) if asset.duration else 1
            entry = _sub(main_bin, "entry", producer=chain_id,
                         **{"in": "00:00:00.000"}, out=frames_to_timecode(dur_frames - 1, settings.fps))

        track_tractor_id: dict[str, str] = {}

        for track in [*sequence.audio_tracks(), *sequence.video_tracks()]:
            playlist_main = _sub(mlt, "playlist", id=self.ids.next("playlist"))
            self._write_track_clips(playlist_main, track, settings)
            playlist_companion = _sub(mlt, "playlist", id=self.ids.next("playlist"))

            tractor_id = self.ids.next("tractor")
            tractor = _sub(mlt, "tractor", id=tractor_id, **{"in": "00:00:00.000"})
            if track.track_type == "audio":
                _prop(tractor, "kdenlive:audio_track", "1")
            _prop(tractor, "kdenlive:trackheight", str(track.height))
            _prop(tractor, "kdenlive:timeline_active", "1")
            _prop(tractor, "kdenlive:collapsed", "0")
            _prop(tractor, "kdenlive:thumbs_format")
            _prop(tractor, "kdenlive:audio_rec")
            if track.locked:
                _prop(tractor, "kdenlive:locked_track", "1")
            hide = "video" if track.track_type == "audio" else None
            _sub(tractor, "track", hide=hide, producer=playlist_main.get("id"))
            _sub(tractor, "track", hide=hide, producer=playlist_companion.get("id"))
            if track.track_type == "audio":
                self._write_audio_mix_filters(tractor, muted=track.muted)

            track_tractor_id[track.id] = tractor_id

        master_track_order = ["producer0"] + [track_tractor_id[t.id] for t in sequence.audio_tracks()] \
            + [track_tractor_id[t.id] for t in sequence.video_tracks()]
        track_position: dict[str, int] = {"producer0": 0}
        for pos, t in enumerate(sequence.audio_tracks(), start=1):
            track_position[t.id] = pos
        for pos, t in enumerate(sequence.video_tracks(), start=1 + len(sequence.audio_tracks())):
            track_position[t.id] = pos

        master = _sub(mlt, "tractor", id="{%s}" % _pad_uuid(project.id),
                       **{"in": "00:00:00.000"}, out=frames_to_timecode(max(1, sequence.duration()), settings.fps))
        seq_uuid = master.get("id")
        _prop(master, "kdenlive:uuid", seq_uuid)
        _prop(master, "kdenlive:clipname", sequence.name)
        _prop(master, "kdenlive:sequenceproperties.hasAudio", "1" if sequence.audio_tracks() else "0")
        _prop(master, "kdenlive:sequenceproperties.hasVideo", "1" if sequence.video_tracks() else "0")
        _prop(master, "kdenlive:sequenceproperties.tracksCount", str(len(sequence.tracks)))
        _prop(master, "kdenlive:sequenceproperties.documentuuid", seq_uuid)
        _prop(master, "kdenlive:duration", frames_to_timecode(sequence.duration(), settings.fps))

        for pos, producer_ref in enumerate(master_track_order):
            _sub(master, "track", producer=producer_ref)

        for pos, track in enumerate(sequence.audio_tracks(), start=1):
            t = _sub(master, "transition", id=self.ids.next("transition"))
            _prop(t, "a_track", "0")
            _prop(t, "b_track", str(pos))
            _prop(t, "mlt_service", "mix")
            _prop(t, "kdenlive_id", "mix")
            _prop(t, "always_active", "1")
            _prop(t, "accepts_blanks", "1")
            _prop(t, "sum", "1")

        video_start = 1 + len(sequence.audio_tracks())
        for pos, track in enumerate(sequence.video_tracks(), start=video_start):
            t = _sub(master, "transition", id=self.ids.next("transition"))
            _prop(t, "a_track", "0")
            _prop(t, "b_track", str(pos))
            _prop(t, "compositing", "0")
            _prop(t, "distort", "0")
            _prop(t, "rotate_center", "0")
            _prop(t, "mlt_service", "qtblend")
            _prop(t, "kdenlive_id", "qtblend")
            _prop(t, "always_active", "1")

        self._write_creative_transitions(master, sequence, track_position, settings)
        self._write_subtitles(master, sequence, settings)

        return mlt

    def _write_subtitles(self, master: ET.Element, sequence: Sequence, settings) -> None:
        """Kdenlive stores a sequence's subtitles as a sibling
        <name>.kdenlive.srt file (never inline in the XML), referenced by
        an `avfilter.subtitles` filter on the sequence tractor -- ground
        truth taken from KDE's own dev-docs/fileformat.md, not guessed.
        Silently skipped if there's no known target path to place the
        sibling file next to (see to_string's target_path parameter)."""
        if not sequence.subtitles:
            return
        if self._target_path is not None:
            srt_path = Path(str(self._target_path) + ".srt")
        elif self.project.path:
            srt_path = Path(self.project.path + ".srt")
        else:
            return

        from kdenlive_mcp.core.subtitles.srt import format_srt
        srt_path.write_text(format_srt(sequence.subtitles, settings.fps), encoding="utf-8")

        flt = _sub(master, "filter", id=self.ids.next("filter"))
        _prop(flt, "mlt_service", "avfilter.subtitles")
        _prop(flt, "internal_added", "237")
        _prop(flt, "av.filename", str(srt_path))
        _prop(flt, "kdenlive:locked", "0")

    def _write_creative_transitions(self, master: ET.Element, sequence: Sequence,
                                     track_position: dict[str, int], settings) -> None:
        """Emits transitions added via sequence.transitions (crossfade, wipe,
        etc. from core.transitions.model) -- distinct from the always-on
        background compositing transitions written above. Each one is
        bounded to its [position, position+duration) span via in/out, unlike
        the always_active background transitions."""
        for t in sequence.transitions:
            a_track = track_position.get(t.a_track) if t.a_track else None
            b_track = track_position.get(t.b_track) if t.b_track else None
            if a_track is None and t.clip_a_id:
                found = sequence.get_clip(t.clip_a_id)
                if found:
                    a_track = track_position.get(found[0].id)
            if b_track is None and t.clip_b_id:
                found = sequence.get_clip(t.clip_b_id)
                if found:
                    b_track = track_position.get(found[0].id)
            if a_track is None or b_track is None:
                raise InvalidOperationError(
                    f"Transition '{t.id}' could not resolve both tracks "
                    f"(a_track={t.a_track!r}, b_track={t.b_track!r}, "
                    f"clip_a_id={t.clip_a_id!r}, clip_b_id={t.clip_b_id!r})",
                )

            el = _sub(master, "transition", id=self.ids.next("transition"),
                      **{"in": frames_to_timecode(t.position, settings.fps)},
                      out=frames_to_timecode(max(t.position, t.position + t.duration - 1), settings.fps))
            _prop(el, "a_track", str(a_track))
            _prop(el, "b_track", str(b_track))
            _prop(el, "mlt_service", t.service)
            _prop(el, "kdenlive_id", t.service)
            for key, value in t.params.items():
                _prop(el, key, str(value))

    # ------------------------------------------------------------- bins --

    def _collect_used_assets(self) -> list[MediaAsset]:
        used_ids: set[str] = set()
        sequence = self.project.active_sequence()
        if sequence:
            for track in sequence.tracks:
                for clip in track.clips:
                    if clip.asset_id:
                        used_ids.add(clip.asset_id)
        assets = []
        for aid in used_ids:
            asset = self.media_index.get(aid)
            if asset is None:
                raise InvalidOperationError(f"Clip references unknown asset '{aid}'; it is not in the media index")
            assets.append(asset)
        return assets

    def _write_doc_properties(self, main_bin: ET.Element) -> None:
        settings = self.project.settings
        project = self.project
        _prop(main_bin, "xml", "was here")
        _prop(main_bin, "kdenlive:docproperties.audioChannels", str(settings.audio_channels))
        _prop(main_bin, "kdenlive:docproperties.documentid", project.id.replace("project_", ""))
        _prop(main_bin, "kdenlive:docproperties.kdenliveversion", "26.04.3")
        _prop(main_bin, "kdenlive:docproperties.profile", profile_name_hint(settings))
        _prop(main_bin, "kdenlive:docproperties.version", "1.1")
        _prop(main_bin, "kdenlive:docproperties.uuid", "{%s}" % _pad_uuid(project.id))
        sequence = project.active_sequence()
        if sequence:
            seq_uuid = "{%s}" % _pad_uuid(project.id)
            _prop(main_bin, "kdenlive:docproperties.opensequences", seq_uuid)
            _prop(main_bin, "kdenlive:docproperties.activetimeline", seq_uuid)
        for key, value in project.metadata.items():
            _prop(main_bin, f"kdenlive:docproperties.{key}", str(value))

    def _write_bin_producer(self, mlt: ET.Element, asset: MediaAsset) -> None:
        settings = self.project.settings
        bin_id = self.ids.next_bin_id()
        control_uuid = "{%s}" % uuid.uuid4()
        self._asset_control_uuid[asset.id] = control_uuid

        if asset.kind == "image":
            producer_id = self.ids.next("producer")
            el = _sub(mlt, "producer", id=producer_id, **{"in": "00:00:00.000"},
                      out=frames_to_timecode(14999, settings.fps))
            _prop(el, "length", "15000")
            _prop(el, "eof", "pause")
            _prop(el, "resource", asset.path)
            _prop(el, "mlt_service", "qimage")
            _prop(el, "kdenlive:id", str(bin_id))
            _prop(el, "kdenlive:control_uuid", control_uuid)
            _prop(el, "kdenlive:clip_type", "5")
            self._asset_chain_id[asset.id] = producer_id
            self._asset_bin_id[asset.id] = bin_id
            return

        dur_frames = max(1, round(asset.duration * settings.fps_float)) if asset.duration else 1
        chain_id = self.ids.next("chain")
        el = _sub(mlt, "chain", id=chain_id, out=frames_to_timecode(dur_frames - 1, settings.fps))
        _prop(el, "length", str(dur_frames))
        _prop(el, "eof", "pause")
        _prop(el, "resource", asset.path)
        _prop(el, "mlt_service", "avformat-novalidate")
        _prop(el, "seekable", "1")
        if asset.has_video:
            _prop(el, "video_index", "0")
        else:
            _prop(el, "video_index", "-1")
        if asset.has_audio:
            _prop(el, "audio_index", "1" if asset.has_video else "0")
        else:
            _prop(el, "audio_index", "-1")
        _prop(el, "kdenlive:id", str(bin_id))
        _prop(el, "kdenlive:control_uuid", control_uuid)
        _prop(el, "kdenlive:folderid", "-1")
        if asset.size_bytes:
            _prop(el, "kdenlive:file_size", str(asset.size_bytes))
        self._asset_chain_id[asset.id] = chain_id
        self._asset_bin_id[asset.id] = bin_id

    def _write_placement_producers(self, mlt: ET.Element, sequence: Sequence) -> None:
        """Mints a dedicated chain/producer instance for every individual
        timeline clip placement that references a real asset, duplicating
        the bin chain's properties but as its own XML element.

        This mirrors what real Kdenlive projects always do: main_bin's
        clip entries reference their own producer instances, entirely
        separate from the ones actual timeline playlists reference for the
        same underlying asset (verified directly against a real project:
        a 6-clip project has 3 distinct chain elements per clip -- one for
        the bin, one for its video-track placement, one for its audio-track
        placement). Reusing a single shared chain everywhere (the original,
        simpler approach here) is valid MLT and melt loads it fine, but
        Kdenlive's own GUI project loader does an additional "does this
        timeline clip have a resolvable bin reference" check that a shared
        chain silently fails -- confirmed by hand: Kdenlive strips every
        clip from the timeline on load, with no error, when a chain is
        referenced from more than one playlist. So every clip placement
        gets its own dedicated element instead.
        """
        for track in sequence.tracks:
            for clip in track.clips:
                if not clip.asset_id or clip.clip_type not in ("video", "audio", "image"):
                    continue
                asset = self.media_index.get(clip.asset_id)
                if asset is None:
                    continue
                self._clip_placement_id[clip.id] = self._new_placement_producer(mlt, asset)

    def _new_placement_producer(self, mlt: ET.Element, asset: MediaAsset) -> str:
        settings = self.project.settings
        bin_id = self._asset_bin_id[asset.id]
        control_uuid = self._asset_control_uuid[asset.id]

        if asset.kind == "image":
            producer_id = self.ids.next("producer")
            el = _sub(mlt, "producer", id=producer_id, **{"in": "00:00:00.000"},
                      out=frames_to_timecode(14999, settings.fps))
            _prop(el, "length", "15000")
            _prop(el, "eof", "pause")
            _prop(el, "resource", asset.path)
            _prop(el, "mlt_service", "qimage")
            _prop(el, "kdenlive:id", str(bin_id))
            _prop(el, "kdenlive:control_uuid", control_uuid)
            _prop(el, "kdenlive:clip_type", "5")
            return producer_id

        dur_frames = max(1, round(asset.duration * settings.fps_float)) if asset.duration else 1
        chain_id = self.ids.next("chain")
        el = _sub(mlt, "chain", id=chain_id, out=frames_to_timecode(dur_frames - 1, settings.fps))
        _prop(el, "length", str(dur_frames))
        _prop(el, "eof", "pause")
        _prop(el, "resource", asset.path)
        _prop(el, "mlt_service", "avformat-novalidate")
        _prop(el, "seekable", "1")
        _prop(el, "video_index", "0" if asset.has_video else "-1")
        _prop(el, "audio_index", ("1" if asset.has_video else "0") if asset.has_audio else "-1")
        _prop(el, "kdenlive:control_uuid", control_uuid)
        _prop(el, "kdenlive:id", str(bin_id))
        _prop(el, "kdenlive:folderid", "-1")
        if asset.size_bytes:
            _prop(el, "kdenlive:file_size", str(asset.size_bytes))
        return chain_id

    def _write_text_and_color_producers(self, mlt: ET.Element) -> None:
        settings = self.project.settings
        sequence = self.project.active_sequence()
        if not sequence:
            return
        for track in sequence.tracks:
            for clip in track.clips:
                if clip.clip_type == "color" and clip.color:
                    # A dedicated producer per clip, not shared by color
                    # value -- same "one instance per placement" rule as
                    # _write_placement_producers; a shared producer
                    # referenced from multiple playlists is what makes
                    # Kdenlive's own loader silently strip clips, even
                    # though melt tolerates it fine.
                    pid = self.ids.next("producer")
                    el = _sub(mlt, "producer", id=pid, **{"in": "00:00:00.000"},
                              out=frames_to_timecode(14999, settings.fps))
                    _prop(el, "length", "15000")
                    _prop(el, "eof", "pause")
                    _prop(el, "resource", _color_to_mlt(clip.color))
                    _prop(el, "mlt_service", "color")
                    self._color_producer_id[f"__color__{clip.id}"] = pid
                elif clip.clip_type == "text":
                    pid = self.ids.next("producer")
                    el = _sub(mlt, "producer", id=pid, **{"in": "00:00:00.000"},
                              out=frames_to_timecode(14999, settings.fps))
                    _prop(el, "length", "15000")
                    _prop(el, "eof", "pause")
                    _prop(el, "resource", "0x00000000")
                    _prop(el, "mlt_service", "color")
                    flt = _sub(el, "filter", id=self.ids.next("filter"))
                    _prop(flt, "argument", clip.text_content or "")
                    _prop(flt, "geometry", "0%,0%:100%,100%:100")
                    _prop(flt, "family", "Sans")
                    _prop(flt, "size", str(round(settings.height * 0.08)))
                    _prop(flt, "weight", "500")
                    _prop(flt, "fgcolour", "0xffffffff")
                    _prop(flt, "olcolour", "0x000000ff")
                    _prop(flt, "outline", "2")
                    _prop(flt, "halign", "center")
                    _prop(flt, "valign", "middle")
                    _prop(flt, "mlt_service", "dynamictext")
                    self._color_producer_id[f"__text__{clip.id}"] = pid

    def _producer_ref(self, clip: Clip) -> str:
        if clip.clip_type == "color":
            return self._color_producer_id[f"__color__{clip.id}"]
        if clip.clip_type == "text":
            return self._color_producer_id[f"__text__{clip.id}"]
        if clip.asset_id:
            return self._clip_placement_id[clip.id]
        raise InvalidOperationError(f"Clip '{clip.id}' has no asset and is not a color/text clip")

    # --------------------------------------------------------- playlists -

    def _write_track_clips(self, playlist: ET.Element, track: Track, settings) -> None:
        cursor = 0
        for clip in track.sorted_clips():
            gap = clip.position - cursor
            if gap > 0:
                _sub(playlist, "blank", length=str(gap))
            elif gap < 0:
                raise InvalidOperationError(
                    f"Overlapping clips on track '{track.id}' at frame {clip.position}",
                )
            producer_ref = self._producer_ref(clip)
            entry = _sub(playlist, "entry", producer=producer_ref,
                         **{"in": frames_to_timecode(clip.in_point, settings.fps)},
                         out=frames_to_timecode(max(clip.in_point, clip.out_point - 1), settings.fps))
            bin_id = self._asset_bin_id.get(clip.asset_id) if clip.asset_id else None
            if bin_id is not None:
                _prop(entry, "kdenlive:id", str(bin_id))
            for effect in clip.effects:
                if not effect.enabled and not effect.keyframed_params and not effect.params:
                    continue
                flt = _sub(entry, "filter", id=self.ids.next("filter"))
                self._keyframe_track_to_filter_props(flt, effect, settings)
            cursor = clip.end

    def _write_audio_mix_filters(self, tractor: ET.Element, *, muted: bool) -> None:
        f0 = _sub(tractor, "filter", id=self.ids.next("filter"))
        _prop(f0, "window", "75")
        _prop(f0, "max_gain", "20dB")
        _prop(f0, "channel_mask", "-1")
        _prop(f0, "mlt_service", "volume")
        _prop(f0, "internal_added", "237")
        _prop(f0, "disable", "0" if muted else "1")
        if muted:
            _prop(f0, "level", "-1000")

        f1 = _sub(tractor, "filter", id=self.ids.next("filter"))
        _prop(f1, "channel", "-1")
        _prop(f1, "mlt_service", "panner")
        _prop(f1, "internal_added", "237")
        _prop(f1, "start", "0.5")
        _prop(f1, "disable", "1")

        f2 = _sub(tractor, "filter", id=self.ids.next("filter"))
        _prop(f2, "iec_scale", "0")
        _prop(f2, "mlt_service", "audiolevel")
        _prop(f2, "internal_added", "237")
        _prop(f2, "dbpeak", "1")
        _prop(f2, "disable", "1")

    # ------------------------------------------------------------ effects

    def _keyframe_track_to_filter_props(self, flt: ET.Element, effect: EffectInstance, settings) -> None:
        for param_name, kf_track in effect.keyframed_params.items():
            sampled_track = KeyframeTrack(param_name=param_name, keyframes=sample_track(kf_track))
            anim = ";".join(f"{kf.frame}={_fmt_value(kf.value)}" for kf in sampled_track.keyframes)
            _prop(flt, param_name, anim)
        for param_name, value in effect.params.items():
            if param_name in effect.keyframed_params:
                continue
            _prop(flt, param_name, _fmt_value(value))
        _prop(flt, "mlt_service", effect.service)
        if not effect.enabled:
            _prop(flt, "disable", "1")


def _pad_uuid(project_id: str) -> str:
    """Kdenlive sequence tractor ids are `{uuid}`-formatted; we don't need
    real UUID entropy here (new_id already used uuid4 hex), just the
    canonical 8-4-4-4-12 grouping so it looks/parses like one."""
    hexpart = (project_id.replace("project_", "").replace("seq_", "") + "0" * 32)[:32]
    return f"{hexpart[0:8]}-{hexpart[8:12]}-{hexpart[12:16]}-{hexpart[16:20]}-{hexpart[20:32]}"
