"""Easing curves and keyframe-track interpolation.

Kdenlive/MLT keyframes are natively linear-only in the file format; smooth
motion is achieved by pre-sampling an eased curve into many linear
keyframes at write time (see kdenlive.adapter.xml_writer). This module is
the math behind that: given a KeyframeTrack, evaluate its value at any
frame.
"""

from __future__ import annotations

import math

from kdenlive_mcp.core.timeline.model import Easing, Keyframe, KeyframeTrack


def ease(t: float, easing: Easing, bezier_handles: tuple[float, float, float, float] | None = None) -> float:
    """Map t in [0,1] -> eased t in [0,1] (clamped)."""
    t = max(0.0, min(1.0, t))
    if easing == Easing.LINEAR:
        return t
    if easing == Easing.HOLD:
        return 0.0
    if easing == Easing.EASE_IN:
        return t * t
    if easing == Easing.EASE_OUT:
        return 1 - (1 - t) * (1 - t)
    if easing == Easing.EASE_IN_OUT:
        return t * t * (3 - 2 * t)  # smoothstep
    if easing == Easing.CUBIC:
        return t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2
    if easing == Easing.BEZIER:
        handles = bezier_handles or (0.42, 0.0, 0.58, 1.0)
        return _cubic_bezier_y(t, *handles)
    if easing == Easing.BOUNCE:
        return _bounce_out(t)
    if easing == Easing.ELASTIC:
        return _elastic_out(t)
    if easing == Easing.OVERSHOOT:
        return _overshoot(t)
    return t


def _cubic_bezier_y(t: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Standard CSS-style cubic bezier easing: control points (x1,y1) (x2,y2),
    endpoints fixed at (0,0)/(1,1). Solves for y given t via x(u)=t."""
    def bez(u: float, p1: float, p2: float) -> float:
        return 3 * (1 - u) ** 2 * u * p1 + 3 * (1 - u) * u ** 2 * p2 + u ** 3

    lo, hi = 0.0, 1.0
    u = t
    for _ in range(20):
        x = bez(u, x1, x2)
        if abs(x - t) < 1e-5:
            break
        if x < t:
            lo = u
        else:
            hi = u
        u = (lo + hi) / 2
    return bez(u, y1, y2)


def _bounce_out(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def _elastic_out(t: float) -> float:
    if t in (0.0, 1.0):
        return t
    c4 = (2 * math.pi) / 3
    return pow(2, -10 * t) * math.sin((t * 10 - 0.75) * c4) + 1


def _overshoot(t: float, amount: float = 1.70158) -> float:
    t -= 1
    return t * t * ((amount + 1) * t + amount) + 1


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _interp_value(a, b, t: float):
    if isinstance(a, (tuple, list)):
        return tuple(_lerp(av, bv, t) for av, bv in zip(a, b))
    return _lerp(a, b, t)


def interpolate(track: KeyframeTrack, frame: int):
    """Evaluate a keyframe track's value at an arbitrary frame."""
    kfs = sorted(track.keyframes, key=lambda k: k.frame)
    if not kfs:
        return None
    if frame <= kfs[0].frame:
        return kfs[0].value
    if frame >= kfs[-1].frame:
        return kfs[-1].value

    for left, right in zip(kfs, kfs[1:]):
        if left.frame <= frame <= right.frame:
            span = right.frame - left.frame
            t = 0.0 if span == 0 else (frame - left.frame) / span
            eased_t = ease(t, left.easing, left.bezier_handles)
            return _interp_value(left.value, right.value, eased_t)
    return kfs[-1].value


def sample_track(track: KeyframeTrack, *, step: int = 1) -> list[Keyframe]:
    """Densely sample a keyframe track into linear-only keyframes.

    Used by the Kdenlive adapter, since MLT's on-disk keyframe format only
    supports linear/discrete/smooth segments, not arbitrary easing curves.
    """
    if len(track.keyframes) < 2:
        return list(track.keyframes)
    kfs = sorted(track.keyframes, key=lambda k: k.frame)
    out: list[Keyframe] = [kfs[0]]
    for left, right in zip(kfs, kfs[1:]):
        if left.easing == Easing.LINEAR:
            out.append(right)
            continue
        span = right.frame - left.frame
        if span <= 1:
            out.append(right)
            continue
        for f in range(left.frame + step, right.frame, step):
            t = (f - left.frame) / span
            value = _interp_value(left.value, right.value, ease(t, left.easing, left.bezier_handles))
            out.append(Keyframe(frame=f, value=value, easing=Easing.LINEAR))
        out.append(Keyframe(frame=right.frame, value=right.value, easing=Easing.LINEAR))
    return out
