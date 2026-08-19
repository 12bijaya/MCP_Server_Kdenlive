"""Internal timeline model.

This is the single source of truth the AI-facing MCP tools mutate. The
Kdenlive adapter translates it to/from `.kdenlive` XML; nothing else in the
system should read or write Kdenlive XML directly. All positions/durations
are integer frames at the sequence's fps.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from typing import Any, Literal

ClipType = Literal["video", "audio", "image", "text", "color"]
TrackType = Literal["video", "audio"]

_id_counter = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Easing(str, Enum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    CUBIC = "cubic"
    BEZIER = "bezier"
    BOUNCE = "bounce"
    ELASTIC = "elastic"
    OVERSHOOT = "overshoot"
    HOLD = "hold"  # step function, no interpolation


@dataclass
class Keyframe:
    frame: int
    value: Any  # float, or tuple[float, ...] for multi-component params (x,y,w,h)
    easing: Easing = Easing.LINEAR
    # bezier control handles as (in_x, in_y, out_x, out_y) fractions, only used when easing == BEZIER
    bezier_handles: tuple[float, float, float, float] | None = None


@dataclass
class KeyframeTrack:
    """One animated parameter (e.g. "position", "scale", "opacity") on a clip/effect."""
    param_name: str
    keyframes: list[Keyframe] = field(default_factory=list)

    def add(self, frame: int, value: Any, easing: Easing = Easing.LINEAR,
            bezier_handles: tuple[float, float, float, float] | None = None) -> Keyframe:
        kf = Keyframe(frame=frame, value=value, easing=easing, bezier_handles=bezier_handles)
        self.keyframes = [k for k in self.keyframes if k.frame != frame] + [kf]
        self.keyframes.sort(key=lambda k: k.frame)
        return kf

    def remove(self, frame: int) -> bool:
        before = len(self.keyframes)
        self.keyframes = [k for k in self.keyframes if k.frame != frame]
        return len(self.keyframes) != before

    @property
    def is_animated(self) -> bool:
        return len(self.keyframes) > 1


@dataclass
class EffectInstance:
    id: str
    service: str  # MLT/Kdenlive service name, e.g. "qtblend", "frei0r.cartoon", "avfilter.eq"
    display_name: str
    params: dict[str, Any] = field(default_factory=dict)
    keyframed_params: dict[str, KeyframeTrack] = field(default_factory=dict)
    enabled: bool = True
    index: int = 0  # stacking order within the clip/track effect stack

    def set_keyframe(self, param: str, frame: int, value: Any, easing: Easing = Easing.LINEAR,
                      bezier_handles: tuple[float, float, float, float] | None = None) -> None:
        track = self.keyframed_params.setdefault(param, KeyframeTrack(param_name=param))
        track.add(frame, value, easing, bezier_handles)


@dataclass
class TransitionInstance:
    id: str
    service: str  # e.g. "luma", "qtblend", "mix", "wipe"
    position: int  # frame, timeline-absolute start
    duration: int  # frames
    a_track: str | None = None
    b_track: str | None = None
    clip_a_id: str | None = None
    clip_b_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    easing: Easing = Easing.LINEAR


@dataclass
class Clip:
    id: str
    track_id: str
    clip_type: ClipType
    position: int  # timeline frame where the clip starts
    in_point: int  # source frame, inclusive
    out_point: int  # source frame, exclusive (length = out_point - in_point)
    asset_id: str | None = None  # None for text/color clips
    speed: float = 1.0
    reversed: bool = False
    name: str = ""
    effects: list[EffectInstance] = field(default_factory=list)
    group_id: str | None = None
    text_content: str | None = None
    color: str | None = None  # "#RRGGBBAA" for color clips
    locked: bool = False
    fade_in: int = 0  # frames
    fade_out: int = 0  # frames
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_length(self) -> int:
        return self.out_point - self.in_point

    @property
    def timeline_length(self) -> int:
        return max(1, round(self.source_length / self.speed))

    @property
    def end(self) -> int:
        return self.position + self.timeline_length

    def next_effect_index(self) -> int:
        return max((e.index for e in self.effects), default=-1) + 1


@dataclass
class Marker:
    frame: int
    name: str = ""
    color: str = "#00ff00"
    category: str = "default"


@dataclass
class Subtitle:
    """One subtitle entry. Kdenlive stores the whole subtitle track as a
    sibling .srt file (not inline in the .kdenlive XML) referenced by an
    `avfilter.subtitles` filter on the sequence -- see kdenlive/adapter.
    """
    id: str
    start_frame: int
    end_frame: int
    text: str


@dataclass
class Track:
    id: str
    index: int  # 0 = bottom-most / lowest z-order within its type, matching MLT track order
    track_type: TrackType
    name: str = ""
    clips: list[Clip] = field(default_factory=list)
    muted: bool = False
    locked: bool = False
    solo: bool = False
    hidden: bool = False
    height: int = 75

    def sorted_clips(self) -> list[Clip]:
        return sorted(self.clips, key=lambda c: c.position)

    def clip_at(self, clip_id: str) -> Clip | None:
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    def overlaps(self, position: int, length: int, *, exclude_clip_id: str | None = None) -> Clip | None:
        end = position + length
        for c in self.clips:
            if c.id == exclude_clip_id:
                continue
            if position < c.end and end > c.position:
                return c
        return None

    def duration(self) -> int:
        return max((c.end for c in self.clips), default=0)


@dataclass
class ProjectSettings:
    width: int = 1920
    height: int = 1080
    fps_num: int = 30
    fps_den: int = 1
    sample_rate: int = 48000
    audio_channels: int = 2
    colorspace: str = "709"
    progressive: bool = True
    display_aspect_num: int = 16
    display_aspect_den: int = 9
    sample_aspect_num: int = 1
    sample_aspect_den: int = 1

    @property
    def fps(self) -> Fraction:
        return Fraction(self.fps_num, self.fps_den)

    @property
    def fps_float(self) -> float:
        return float(self.fps)


@dataclass
class Sequence:
    id: str
    name: str = "Sequence 1"
    tracks: list[Track] = field(default_factory=list)
    transitions: list[TransitionInstance] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    subtitles: list[Subtitle] = field(default_factory=list)

    def video_tracks(self) -> list[Track]:
        return sorted((t for t in self.tracks if t.track_type == "video"), key=lambda t: t.index)

    def audio_tracks(self) -> list[Track]:
        return sorted((t for t in self.tracks if t.track_type == "audio"), key=lambda t: t.index)

    def get_track(self, track_id: str) -> Track | None:
        for t in self.tracks:
            if t.id == track_id:
                return t
        return None

    def get_clip(self, clip_id: str) -> tuple[Track, Clip] | None:
        for t in self.tracks:
            c = t.clip_at(clip_id)
            if c:
                return t, c
        return None

    def get_subtitle(self, subtitle_id: str) -> Subtitle | None:
        for s in self.subtitles:
            if s.id == subtitle_id:
                return s
        return None

    def sorted_subtitles(self) -> list[Subtitle]:
        return sorted(self.subtitles, key=lambda s: s.start_frame)

    def duration(self) -> int:
        return max((t.duration() for t in self.tracks), default=0)

    def next_track_index(self, track_type: TrackType) -> int:
        existing = [t.index for t in self.tracks if t.track_type == track_type]
        return (max(existing) + 1) if existing else 0


@dataclass
class Project:
    id: str
    name: str
    path: str | None  # filesystem path once saved, None for unsaved
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    sequences: list[Sequence] = field(default_factory=list)
    active_sequence_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False

    def active_sequence(self) -> Sequence | None:
        for seq in self.sequences:
            if seq.id == self.active_sequence_id:
                return seq
        return self.sequences[0] if self.sequences else None

    def get_sequence(self, sequence_id: str) -> Sequence | None:
        for seq in self.sequences:
            if seq.id == sequence_id:
                return seq
        return None


def new_project(name: str, settings: ProjectSettings | None = None) -> Project:
    settings = settings or ProjectSettings()
    project = Project(id=new_id("project"), name=name, path=None, settings=settings)
    seq = Sequence(id=new_id("seq"), name="Sequence 1")
    # Standard default layout: 2 video tracks over 2 audio tracks, matching
    # what Kdenlive's "new project" wizard gives you.
    seq.tracks.append(Track(id=new_id("track"), index=0, track_type="video", name="V1"))
    seq.tracks.append(Track(id=new_id("track"), index=1, track_type="video", name="V2"))
    seq.tracks.append(Track(id=new_id("track"), index=0, track_type="audio", name="A1"))
    seq.tracks.append(Track(id=new_id("track"), index=1, track_type="audio", name="A2"))
    project.sequences.append(seq)
    project.active_sequence_id = seq.id
    return project
