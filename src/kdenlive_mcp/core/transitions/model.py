"""Builders for the timeline transition primitives.

Each builder returns a `TransitionInstance` (see `core.timeline.model`) that
the caller attaches to a `Sequence`/pair of clips -- these functions never
take clip references, only `position`/`duration` (frames) and style knobs,
mirroring how `TransitionInstance` itself has no required clip linkage.

Real vs. simulated services
----------------------------
Kdenlive 26.04.3's transition XML (`/var/lib/snapd/snap/kdenlive/144/usr/share/kdenlive/transitions/*.xml`)
was inspected to ground every service name used here:

    id "dissolve"      -> tag "luma"      -- true audio/video crossfade
    id "qtblend"        -> tag "qtblend"    -- keyframed composite/transform (animatedrect "rect", "compositing" blend modes)
    id "frei0r.sleid0r_slide-{left,right,up,down}" -- dedicated frei0r slide transitions
    id "frei0r.sleid0r_push-{left,right,up,down}"  -- dedicated frei0r push transitions
    id "frei0r.sleid0r_wipe-{left,right,up,down,circle,rect,barn-door-h,barn-door-v}" -- dedicated directional wipes
    id "frei0r.uvmap"   -> tag "frei0r.uvmap" -- "Uses Input 1 as UV Map to distort Input 2" (real distortion compositor)

None of these are guesses -- they're transcribed straight from the shipped
XML `tag=`/`id=` attributes (see `tests/unit/test_transitions.py`, which
loads the real catalog and asserts on a subset of them).

Some of the requested primitives (whip, blur, glitch) have **no** dedicated
MLT/frei0r transition service -- Kdenlive achieves those looks by combining
a plain composite with per-clip motion/blur effects, not a single
"whip transition" plugin. Those builders are explicit about this: they
return a `qtblend`-based TransitionInstance approximating the geometry/look,
and their docstrings say what to pair it with
(`core.keyframes.motion` for camera motion, `core.effects.model.create_effect`
for a ramped blur/chroma-shift effect on the adjoining clips) to get the
full effect -- they do not pretend a matching MLT service exists.
"""

from __future__ import annotations

import logging
import random

from kdenlive_mcp.core.timeline.model import Easing, TransitionInstance, new_id
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.transitions.catalog import (
    get_default_transition_catalog,
    validate_transition_available,
)

logger = logging.getLogger("kdenlive_mcp.core.transitions.model")

_DIRECTIONS_4 = ("left", "right", "up", "down")
_WIPE_STYLES = ("left", "right", "up", "down", "circle", "rect", "barn-door-h", "barn-door-v")


def _resolve_service(transition_id: str, fallback_tag: str) -> str:
    """Look up `transition_id`'s real MLT service (tag) in the default
    transition catalog.

    - If the catalog loaded and has this id: use its real tag (handles cases
      like id "dissolve" whose tag is actually "luma").
    - If the catalog loaded but doesn't have this id: raise
      `TransitionUnavailableError` via `validate_transition_available` --
      this is a real environment where we can check, so a mismatch is a bug,
      not something to paper over.
    - If the catalog is empty (Kdenlive not found on this machine at all):
      degrade gracefully -- log a warning and use `fallback_tag`, since we
      can't validate anything anyway.
    """
    catalog = get_default_transition_catalog()
    if not catalog.all():
        logger.warning(
            "Transition catalog is empty/unavailable; assuming '%s' maps to "
            "service '%s' without validation", transition_id, fallback_tag,
        )
        return fallback_tag

    definition = validate_transition_available(transition_id)
    return definition.tag


def hard_cut() -> None:
    """A hard cut is simply the absence of a transition -- no overlap, no
    blending. There is no MLT transition service for "no transition"; the
    Kdenlive adapter should just not emit a <transition> element between two
    abutting clips. This function exists purely so callers have a uniform
    "build the transition for style X" dispatch table; it always returns
    None.
    """
    return None


def crossfade(*, position: int, duration: int, easing: Easing = Easing.LINEAR,
              softness: float = 0.0) -> TransitionInstance:
    """Standard audio/video crossfade. Real service: id "dissolve" -> MLT
    tag "luma" (Kdenlive's own "Dissolve" transition is a "luma" service
    instance with no luma map resource set, i.e. a plain linear crossfade).

    `softness` is 0-100, matching the real "softness" parameter's own scale.
    """
    service = _resolve_service("dissolve", "luma")
    return TransitionInstance(
        id=new_id("transition"),
        service=service,
        position=position,
        duration=duration,
        params={"reverse": 0, "alpha_over": 1, "fix_background_alpha": 1, "softness": softness},
        easing=easing,
    )


def _dip_through_color(*, position: int, duration: int, hold_frames: int,
                        easing: Easing, color_label: str) -> list[TransitionInstance]:
    """Shared implementation for dip_to_black/dip_to_white.

    MLT has no single 2-clip "dip to color" transition service -- a dip is
    really clip_a fading out, a beat of solid color, then clip_b fading in.
    So unlike the other primitives here, this one returns a *list* of two
    `TransitionInstance` (both real "dissolve"/luma crossfades): the first
    from clip_a into a solid-color clip, the second from that color clip
    into clip_b. The caller is responsible for inserting a `Clip` of type
    "color" (set to black/white, `hold_frames` long) covering the gap
    between the two returned transitions' spans -- this module has no clip
    references to do that itself.
    """
    half = max(1, duration // 2)
    first = crossfade(position=position, duration=half, easing=easing)
    second = crossfade(position=position + half + max(0, hold_frames), duration=half, easing=easing)
    first.params["dip_color_hint"] = color_label
    second.params["dip_color_hint"] = color_label
    return [first, second]


def dip_to_black(*, position: int, duration: int, hold_frames: int = 0,
                  easing: Easing = Easing.LINEAR) -> list[TransitionInstance]:
    """See `_dip_through_color`. Caller inserts a black color Clip between
    the two returned transitions."""
    return _dip_through_color(position=position, duration=duration, hold_frames=hold_frames,
                               easing=easing, color_label="#000000")


def dip_to_white(*, position: int, duration: int, hold_frames: int = 0,
                  easing: Easing = Easing.LINEAR) -> list[TransitionInstance]:
    """See `_dip_through_color`. Caller inserts a white color Clip between
    the two returned transitions."""
    return _dip_through_color(position=position, duration=duration, hold_frames=hold_frames,
                               easing=easing, color_label="#ffffff")


def zoom_transition(*, position: int, duration: int, direction: str = "in",
                     easing: Easing = Easing.EASE_IN_OUT) -> TransitionInstance:
    """Zoom crossfade built on the real "qtblend" transition service, whose
    "rect" parameter is a genuine MLT `animatedrect` (percentage rects are
    accepted -- see the `<preview>` blocks in qtblend.xml/composite.xml,
    which use exactly this "X% Y% W% H% opacity" syntax).

    direction="in":  B starts small & transparent at center, grows to full frame.
    direction="out": B starts full frame, shrinks to center while fading in reverse.
    """
    if direction not in ("in", "out"):
        raise InvalidOperationError("direction must be 'in' or 'out'", suggestion="Use 'in' or 'out'")
    service = _resolve_service("qtblend", "qtblend")
    small = "25% 25% 50% 50% 0"
    full = "0% 0% 100% 100% 100"
    rect = f"0={small};{duration}={full}" if direction == "in" else f"0={full};{duration}={small}"
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"rect": rect, "compositing": "0", "distort": 0},
        easing=easing,
    )


def whip_transition(*, position: int, duration: int, direction: str = "left",
                     easing: Easing = Easing.EASE_IN) -> TransitionInstance:
    """Fast directional pan-through, approximated on "qtblend" (no dedicated
    "whip pan" MLT service exists). This only carries the geometry: for a
    convincing whip-pan look, pair it with a short, heavy directional blur
    ramped up/down on the two adjoining clips via
    `core.keyframes.motion.create_pan` + a blur effect from
    `core.effects.model.create_effect("frei0r.IIRblur", ...)` keyframed to
    peak mid-transition -- that per-clip motion blur is what actually sells
    "whip", and it's out of scope for a single TransitionInstance.
    """
    if direction not in _DIRECTIONS_4:
        raise InvalidOperationError(f"direction must be one of {_DIRECTIONS_4}")
    service = _resolve_service("qtblend", "qtblend")
    offsets = {"left": ("100%", "0%"), "right": ("-100%", "0%"), "up": ("0%", "100%"), "down": ("0%", "-100%")}
    ox, oy = offsets[direction]
    start = f"{ox} {oy} 100% 100% 0"
    end = "0% 0% 100% 100% 100"
    rect = f"0={start};{duration}={end}"
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"rect": rect, "compositing": "0", "distort": 0},
        easing=easing,
    )


def slide_transition(*, position: int, duration: int, direction: str = "left") -> TransitionInstance:
    """Real dedicated MLT/frei0r service: id/tag
    "frei0r.sleid0r_slide-{left,right,up,down}" (the "sleid0r" spelling is
    the actual frei0r plugin name, not a typo)."""
    if direction not in _DIRECTIONS_4:
        raise InvalidOperationError(f"direction must be one of {_DIRECTIONS_4}")
    transition_id = f"frei0r.sleid0r_slide-{direction}"
    service = _resolve_service(transition_id, transition_id)
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"position": "0=0;%out=1"},
    )


def push_transition(*, position: int, duration: int, direction: str = "left") -> TransitionInstance:
    """Real dedicated MLT/frei0r service: id/tag
    "frei0r.sleid0r_push-{left,right,up,down}"."""
    if direction not in _DIRECTIONS_4:
        raise InvalidOperationError(f"direction must be one of {_DIRECTIONS_4}")
    transition_id = f"frei0r.sleid0r_push-{direction}"
    service = _resolve_service(transition_id, transition_id)
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"position": "0=0;%out=1"},
    )


def blur_transition(*, position: int, duration: int, easing: Easing = Easing.LINEAR) -> TransitionInstance:
    """No dedicated "blur transition" MLT service exists. This returns a
    plain "qtblend" crossfade for the geometry/opacity ramp; for the actual
    blur-through look, ramp a blur effect
    (`core.effects.model.create_effect("frei0r.IIRblur", ...)` or
    "avfilter.gblur") up on the outgoing clip and down on the incoming clip
    across the same [position, position+duration) span, via
    `EffectInstance.set_keyframe`.
    """
    service = _resolve_service("qtblend", "qtblend")
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"rect": "0 0 %width %height 1", "compositing": "0", "distort": 0},
        easing=easing,
    )


def flash_transition(*, position: int, duration: int, flash_color: str = "#ffffff") -> TransitionInstance:
    """Brief additive-blend flash, built on the real "qtblend" transition
    using its "compositing" parameter set to mode 12 ("Plus" / additive
    blend, per qtblend.xml's own paramlistdisplay) -- overlapping two frames
    additively blows highlights toward white, giving a flash/burst look.
    This is a real, documented qtblend blend mode, just used for a purpose
    Kdenlive's UI doesn't dedicate a named transition to.
    """
    service = _resolve_service("qtblend", "qtblend")
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"rect": "0 0 %width %height 1", "compositing": "12", "distort": 0,
                "flash_color_hint": flash_color},
    )


def glitch_transition(*, position: int, duration: int, intensity: str = "medium",
                       seed: int | None = None) -> TransitionInstance:
    """No dedicated "glitch transition" MLT service exists. This builds a
    "qtblend" transition with a jittered rect (small random x/y offsets per
    keyframe) to fake a brief digital-glitch displacement during the cut.
    For a convincing glitch, pair it with
    `core.effects.model.create_effect("avfilter.chromashift", ...)` (RGB
    channel split) and/or the "wave" effect keyframed to spike across the
    same span on the adjoining clips -- that per-clip signal corruption is
    what actually sells "glitch"; this transition alone only wobbles the
    frame geometry.
    """
    amplitude = {"subtle": 1.5, "medium": 4.0, "aggressive": 9.0}.get(intensity, 4.0)
    rng = random.Random(seed)
    n_steps = max(2, min(8, duration))
    parts = []
    for i in range(n_steps + 1):
        frame = round(duration * i / n_steps)
        dx = 0.0 if i in (0, n_steps) else rng.uniform(-amplitude, amplitude)
        dy = 0.0 if i in (0, n_steps) else rng.uniform(-amplitude, amplitude)
        opacity = 0 if i == 0 else 100
        parts.append(f"{frame}={dx:.2f}% {dy:.2f}% 100% 100% {opacity}")
    rect = ";".join(parts)
    service = _resolve_service("qtblend", "qtblend")
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"rect": rect, "compositing": "0", "distort": 0},
    )


def distortion_transition(*, position: int, duration: int) -> TransitionInstance:
    """Real dedicated service: id/tag "frei0r.uvmap" -- "Uses Input 1 as UV
    Map to distort Input 2" per its own XML description. Note this is a
    genuine displacement compositor, but it's only useful paired with a
    clip_a that actually *is* a UV-displacement-map asset (a generated
    gradient/noise texture); with an ordinary video clip_a it will still
    composite, just without a meaningful "distortion" look. Documenting that
    constraint rather than hiding it.
    """
    service = _resolve_service("frei0r.uvmap", "frei0r.uvmap")
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={},
    )


def directional_transition(direction: str, *, position: int, duration: int,
                            style: str = "wipe") -> TransitionInstance:
    """Dispatches to a real dedicated frei0r "sleid0r" wipe service:
    id/tag "frei0r.sleid0r_wipe-{left,right,up,down,circle,rect,barn-door-h,barn-door-v}".

    `direction` selects the wipe style directly (it doubles as the style
    name for non-4-way wipes like "circle"/"rect"/"barn-door-h"); `style` is
    accepted for API symmetry with the other primitives but currently only
    "wipe" is implemented (there's no separate real service per "style").
    """
    if style != "wipe":
        raise InvalidOperationError(f"Unknown directional style '{style}'", suggestion="Use style='wipe'")
    if direction not in _WIPE_STYLES:
        raise InvalidOperationError(f"direction must be one of {_WIPE_STYLES}")
    transition_id = f"frei0r.sleid0r_wipe-{direction}"
    service = _resolve_service(transition_id, transition_id)
    return TransitionInstance(
        id=new_id("transition"), service=service, position=position, duration=duration,
        params={"position": "0=0;%out=1"},
    )


def custom_transition(service: str, *, position: int, duration: int,
                       params: dict) -> TransitionInstance:
    """Low-level escape hatch: build a TransitionInstance for any transition
    id known to the catalog. Validates `service` (interpreted as a
    transition *id*, matching `validate_transition_available`) actually
    exists before building, and uses its real MLT tag."""
    definition = validate_transition_available(service)
    return TransitionInstance(
        id=new_id("transition"), service=definition.tag, position=position, duration=duration,
        params=dict(params),
    )
