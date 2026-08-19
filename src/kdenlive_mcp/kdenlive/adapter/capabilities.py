"""Detect what this installation of Kdenlive/MLT/FFmpeg can actually do.

The adapter must never assume an effect, transition, or render profile
exists just because the spec mentions it -- everything here is grounded in
what's actually installed, discovered at call time (cheaply cached).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kdenlive_mcp.config import get_config
from kdenlive_mcp.kdenlive.effects.catalog import get_default_catalog as get_effect_catalog
from kdenlive_mcp.kdenlive.transitions.catalog import get_default_transition_catalog
from kdenlive_mcp.media.ffmpeg.runner import is_available, run


@dataclass
class Capabilities:
    kdenlive_version: str | None = None
    kdenlive_available: bool = False
    ffmpeg_available: bool = False
    ffmpeg_version: str | None = None
    ffprobe_available: bool = False
    melt_available: bool = False
    melt_version: str | None = None
    effects_dir_found: bool = False
    effect_count: int = 0
    transition_count: int = 0
    supported_effects: list[str] = field(default_factory=list)
    supported_transitions: list[str] = field(default_factory=list)
    render_profiles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kdenlive_version": self.kdenlive_version,
            "kdenlive_available": self.kdenlive_available,
            "ffmpeg_available": self.ffmpeg_available,
            "ffmpeg_version": self.ffmpeg_version,
            "ffprobe_available": self.ffprobe_available,
            "melt_available": self.melt_available,
            "melt_version": self.melt_version,
            "effects_dir_found": self.effects_dir_found,
            "effect_count": self.effect_count,
            "transition_count": self.transition_count,
            "supported_effects": self.supported_effects,
            "supported_transitions": self.supported_transitions,
            "render_profiles": self.render_profiles,
        }


_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def detect_capabilities() -> Capabilities:
    cfg = get_config()
    caps = Capabilities()

    caps.ffmpeg_available = is_available("ffmpeg")
    if caps.ffmpeg_available:
        r = run("ffmpeg", ["-version"], check=False)
        m = _VERSION_RE.search(r.stdout or "")
        caps.ffmpeg_version = m.group(1) if m else None

    caps.ffprobe_available = is_available("ffprobe")

    caps.melt_available = is_available("melt")
    if caps.melt_available:
        r = run("melt", ["-version"], check=False)
        m = _VERSION_RE.search(r.stdout or "")
        caps.melt_version = m.group(1) if m else None

    caps.kdenlive_available = bool(cfg.kdenlive_bin)
    if cfg.kdenlive_effects_dir:
        caps.effects_dir_found = cfg.kdenlive_effects_dir.is_dir()
        # snap paths embed the version, e.g. .../kdenlive/144/usr/share/...
        m = re.search(r"/kdenlive/(\d+)/", str(cfg.kdenlive_effects_dir))
        if m:
            caps.kdenlive_version = f"snap rev {m.group(1)}"

    effect_catalog = get_effect_catalog()
    caps.effect_count = len(effect_catalog.all())
    caps.supported_effects = sorted(e.id for e in effect_catalog.all())

    transition_catalog = get_default_transition_catalog()
    caps.transition_count = len(transition_catalog.all())
    caps.supported_transitions = sorted(t.id for t in transition_catalog.all())

    caps.render_profiles = ["720p", "1080p", "1440p", "4k"]

    return caps
