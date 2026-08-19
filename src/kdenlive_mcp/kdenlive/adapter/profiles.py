"""Resolution / frame-rate profile resolution.

Ground truth for the `<profile>` element's attribute names and values was
taken directly from real Kdenlive 26.04.3 projects and MLT's own profile
files (/usr/share/mlt-7/profiles/*) on this machine, e.g.:

    <profile colorspace="709" description="Vertical HD 60 fps"
             display_aspect_den="16" display_aspect_num="9"
             frame_rate_den="1" frame_rate_num="60"
             height="1920" progressive="1"
             sample_aspect_den="1" sample_aspect_num="1" width="1080"/>

We never silently change these once a project exists -- callers must go
through set_project_resolution / set_project_fps explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from kdenlive_mcp.core.timeline.model import ProjectSettings
from kdenlive_mcp.errors import InvalidOperationError

# name -> (width, height) in landscape orientation
RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
}

# label -> Fraction fps
FPS_PRESETS: dict[str, Fraction] = {
    "23.976": Fraction(24000, 1001),
    "24": Fraction(24, 1),
    "25": Fraction(25, 1),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30, 1),
    "50": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60, 1),
}

# Best-effort mapping to real named MLT/Kdenlive profiles, purely cosmetic
# (used for the kdenlive:docproperties.profile hint). Unmapped combinations
# fall back to a synthesized "custom_<w>x<h>_<fps>" label; Kdenlive doesn't
# require this to match an on-disk profile file since the .kdenlive project
# always embeds the full <profile> attributes inline.
_NAMED_PROFILES: dict[tuple[int, int, str], str] = {
    (1280, 720, "25"): "hdv_720_25p", (1280, 720, "30"): "hdv_720_30p",
    (1280, 720, "50"): "hdv_720_50p", (1280, 720, "60"): "hdv_720_60p",
    (1920, 1080, "25"): "atsc_1080p_25", (1920, 1080, "30"): "atsc_1080p_30",
    (1920, 1080, "50"): "atsc_1080p_50", (1920, 1080, "60"): "atsc_1080p_60",
    (2560, 1440, "25"): "qhd_1440p_25", (2560, 1440, "30"): "qhd_1440p_30",
    (2560, 1440, "50"): "qhd_1440p_50", (2560, 1440, "60"): "qhd_1440p_60",
    (3840, 2160, "25"): "uhd_2160p_25", (3840, 2160, "30"): "uhd_2160p_30",
    (3840, 2160, "50"): "uhd_2160p_50", (3840, 2160, "60"): "uhd_2160p_60",
    (1080, 1920, "30"): "vertical_hd_30", (1080, 1920, "60"): "vertical_hd_60",
    (1080, 1080, "30"): "square_1080p_30", (1080, 1080, "60"): "square_1080p_60",
}


@dataclass(frozen=True)
class Orientation:
    LANDSCAPE = "landscape"
    VERTICAL = "vertical"
    SQUARE = "square"


def resolve_profile(resolution: str, fps: str | float, *, orientation: str = "landscape") -> ProjectSettings:
    res_key = resolution.lower().strip()
    if res_key not in RESOLUTIONS:
        raise InvalidOperationError(
            f"Unknown resolution '{resolution}'",
            suggestion=f"Use one of: {sorted(RESOLUTIONS)}",
        )
    w, h = RESOLUTIONS[res_key]

    fps_key = str(fps).strip()
    if fps_key not in FPS_PRESETS:
        try:
            fps_frac = Fraction(str(fps)).limit_denominator(1001)
        except (ValueError, ZeroDivisionError):
            raise InvalidOperationError(
                f"Unknown fps '{fps}'",
                suggestion=f"Use one of: {sorted(FPS_PRESETS)}",
            )
    else:
        fps_frac = FPS_PRESETS[fps_key]

    if orientation == Orientation.VERTICAL:
        w, h = h, w
    elif orientation == Orientation.SQUARE:
        w = h = min(w, h)
    elif orientation != Orientation.LANDSCAPE:
        raise InvalidOperationError(
            f"Unknown orientation '{orientation}'",
            suggestion="Use 'landscape', 'vertical', or 'square'",
        )

    dar_num, dar_den = _reduce_ratio(w, h)

    return ProjectSettings(
        width=w, height=h,
        fps_num=fps_frac.numerator, fps_den=fps_frac.denominator,
        display_aspect_num=dar_num, display_aspect_den=dar_den,
        sample_aspect_num=1, sample_aspect_den=1,
        colorspace="709", progressive=True,
    )


def _reduce_ratio(w: int, h: int) -> tuple[int, int]:
    from math import gcd
    g = gcd(w, h) or 1
    return w // g, h // g


def profile_name_hint(settings: ProjectSettings) -> str:
    fps_label = _fps_label(settings.fps)
    key = (settings.width, settings.height, fps_label)
    if key in _NAMED_PROFILES:
        return _NAMED_PROFILES[key]
    return f"custom_{settings.width}x{settings.height}_{fps_label}"


def _fps_label(fps: Fraction) -> str:
    for label, value in FPS_PRESETS.items():
        if value == fps:
            return label
    return f"{float(fps):.3f}".rstrip("0").rstrip(".")


def profile_description(settings: ProjectSettings) -> str:
    fps_label = _fps_label(settings.fps)
    return f"{settings.width}x{settings.height} {fps_label}fps"
