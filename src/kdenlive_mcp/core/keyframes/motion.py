"""High-level professional motion engine.

These builders let a caller ask for "camera push", "handheld", "zoom punch"
etc. without hand-computing every keyframe. Everything ultimately writes
into a clip's qtblend "rect" (x, y, w, h, opacity) and "rotation" animated
parameters -- the same params Kdenlive's own Transform effect uses -- so
the Kdenlive adapter needs no special-casing per motion type.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from kdenlive_mcp.core.timeline.model import Clip, Easing, EffectInstance
from kdenlive_mcp.errors import InvalidOperationError

QTBLEND_SERVICE = "qtblend"


@dataclass
class TransformKeyframe:
    frame: int
    x: float
    y: float
    w: float
    h: float
    opacity: float = 1.0
    rotation: float = 0.0
    easing: Easing = Easing.LINEAR
    bezier_handles: tuple[float, float, float, float] | None = None


def _get_or_create_transform(clip: Clip) -> EffectInstance:
    for effect in clip.effects:
        if effect.service == QTBLEND_SERVICE:
            return effect
    effect = EffectInstance(
        id=f"effect_{clip.id}_transform", service=QTBLEND_SERVICE,
        display_name="Transform", index=clip.next_effect_index(),
    )
    clip.effects.append(effect)
    return effect


def apply_transform_keyframes(clip: Clip, frames: list[TransformKeyframe]) -> EffectInstance:
    if not frames:
        raise InvalidOperationError("At least one keyframe is required")
    effect = _get_or_create_transform(clip)
    for tk in sorted(frames, key=lambda f: f.frame):
        effect.set_keyframe("rect", tk.frame, (tk.x, tk.y, tk.w, tk.h, tk.opacity), tk.easing, tk.bezier_handles)
        effect.set_keyframe("rotation", tk.frame, tk.rotation, tk.easing, tk.bezier_handles)
    return effect


def _center_rect(frame_w: int, frame_h: int, w: float, h: float, anchor: str = "center") -> tuple[float, float]:
    if anchor == "center":
        return (frame_w - w) / 2, (frame_h - h) / 2
    if anchor == "top_left":
        return 0.0, 0.0
    if anchor == "top_right":
        return frame_w - w, 0.0
    if anchor == "bottom_left":
        return 0.0, frame_h - h
    if anchor == "bottom_right":
        return frame_w - w, frame_h - h
    raise InvalidOperationError(f"Unknown anchor: {anchor}")


# ------------------------------------------------------------- primitives -

def animate_position(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float, float]],
                      *, easing: Easing = Easing.EASE_IN_OUT,
                      bezier_handles: tuple[float, float, float, float] | None = None) -> EffectInstance:
    frames = [TransformKeyframe(f, x, y, frame_w, frame_h, easing=easing, bezier_handles=bezier_handles)
              for f, x, y in path]
    return apply_transform_keyframes(clip, frames)


def animate_scale(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float]],
                   *, anchor: str = "center", easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    frames = []
    for f, scale in path:
        w, h = frame_w * scale, frame_h * scale
        x, y = _center_rect(frame_w, frame_h, w, h, anchor)
        frames.append(TransformKeyframe(f, x, y, w, h, easing=easing))
    return apply_transform_keyframes(clip, frames)


def animate_rotation(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float]],
                      *, easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    x, y = _center_rect(frame_w, frame_h, frame_w, frame_h)
    frames = [TransformKeyframe(f, x, y, frame_w, frame_h, rotation=r, easing=easing) for f, r in path]
    return apply_transform_keyframes(clip, frames)


def animate_opacity(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float]],
                     *, easing: Easing = Easing.LINEAR) -> EffectInstance:
    x, y = _center_rect(frame_w, frame_h, frame_w, frame_h)
    frames = [TransformKeyframe(f, x, y, frame_w, frame_h, opacity=o, easing=easing) for f, o in path]
    return apply_transform_keyframes(clip, frames)


def animate_anchor_point(clip: Clip, anchor: str) -> None:
    valid = {"center", "top_left", "top_right", "bottom_left", "bottom_right"}
    if anchor not in valid:
        raise InvalidOperationError(f"anchor must be one of {sorted(valid)}")
    clip.metadata["anchor"] = anchor


def animate_crop(clip: Clip, path: list[tuple[int, float, float, float, float]],
                  *, easing: Easing = Easing.LINEAR) -> EffectInstance:
    """path items: (frame, left, right, top, bottom) as 0..1 fractions of frame size."""
    effect = None
    for e in clip.effects:
        if e.service == "crop":
            effect = e
            break
    if effect is None:
        effect = EffectInstance(id=f"effect_{clip.id}_crop", service="crop", display_name="Crop",
                                 index=clip.next_effect_index())
        clip.effects.append(effect)
    for f, left, right, top, bottom in path:
        effect.set_keyframe("crop", f, (left, right, top, bottom), easing)
    return effect


def create_motion_path(clip: Clip, frame_w: int, frame_h: int, points: list[tuple[int, float, float]],
                        *, easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    return animate_position(clip, frame_w, frame_h, points, easing=easing)


def create_bezier_motion(clip: Clip, frame_w: int, frame_h: int, points: list[tuple[int, float, float]],
                          *, handles: tuple[float, float, float, float] = (0.25, 0.1, 0.25, 1.0)) -> EffectInstance:
    return animate_position(clip, frame_w, frame_h, points, easing=Easing.BEZIER, bezier_handles=handles)


def create_smooth_follow(clip: Clip, frame_w: int, frame_h: int, raw_path: list[tuple[int, float, float]],
                          *, smoothing: float = 0.25) -> EffectInstance:
    """Exponential-moving-average smoothing over a noisy tracked path before keyframing it."""
    if not raw_path:
        raise InvalidOperationError("raw_path must not be empty")
    smoothing = max(0.0, min(1.0, smoothing))
    smoothed: list[tuple[int, float, float]] = [raw_path[0]]
    sx, sy = raw_path[0][1], raw_path[0][2]
    for f, x, y in raw_path[1:]:
        sx = sx + smoothing * (x - sx)
        sy = sy + smoothing * (y - sy)
        smoothed.append((f, sx, sy))
    return animate_position(clip, frame_w, frame_h, smoothed, easing=Easing.LINEAR)


# ------------------------------------------------------------ easing presets

EASING_ALIASES = {
    "linear": Easing.LINEAR,
    "ease_in": Easing.EASE_IN, "ease-in": Easing.EASE_IN,
    "ease_out": Easing.EASE_OUT, "ease-out": Easing.EASE_OUT,
    "ease_in_out": Easing.EASE_IN_OUT, "ease-in-out": Easing.EASE_IN_OUT, "smooth": Easing.EASE_IN_OUT,
    "cubic": Easing.CUBIC,
    "bezier": Easing.BEZIER,
    "bounce": Easing.BOUNCE,
    "elastic": Easing.ELASTIC, "spring": Easing.ELASTIC,
    "overshoot": Easing.OVERSHOOT,
    "hold": Easing.HOLD, "step": Easing.HOLD,
}


def resolve_easing(name: str) -> Easing:
    key = (name or "").strip().lower()
    if key not in EASING_ALIASES:
        raise InvalidOperationError(
            f"Unknown easing '{name}'", suggestion=f"Use one of: {sorted(set(EASING_ALIASES))}",
        )
    return EASING_ALIASES[key]


def create_easing(name: str) -> Easing:
    return resolve_easing(name)


def create_overshoot(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float, float]]) -> EffectInstance:
    return animate_position(clip, frame_w, frame_h, path, easing=Easing.OVERSHOOT)


def create_bounce(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float, float]]) -> EffectInstance:
    return animate_position(clip, frame_w, frame_h, path, easing=Easing.BOUNCE)


def create_spring_motion(clip: Clip, frame_w: int, frame_h: int, path: list[tuple[int, float, float]]) -> EffectInstance:
    return animate_position(clip, frame_w, frame_h, path, easing=Easing.ELASTIC)


# ------------------------------------------------------------ camera moves

def create_camera_push(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                        start_scale: float = 1.0, end_scale: float = 1.15,
                        easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    return animate_scale(clip, frame_w, frame_h, [(start_frame, start_scale), (end_frame, end_scale)], easing=easing)


def create_camera_pull(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                        start_scale: float = 1.15, end_scale: float = 1.0,
                        easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    return animate_scale(clip, frame_w, frame_h, [(start_frame, start_scale), (end_frame, end_scale)], easing=easing)


def create_pan(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                direction: str = "right", distance_px: float = 100.0, scale: float = 1.15,
                easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    """Pans by translating a slightly-oversized frame across `distance_px`."""
    sign = -1 if direction == "left" else 1
    w, h = frame_w * scale, frame_h * scale
    base_x, base_y = _center_rect(frame_w, frame_h, w, h)
    x0, x1 = base_x - sign * distance_px / 2, base_x + sign * distance_px / 2
    return apply_transform_keyframes(clip, [
        TransformKeyframe(start_frame, x0, base_y, w, h, easing=easing),
        TransformKeyframe(end_frame, x1, base_y, w, h, easing=easing),
    ])


def create_tilt(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                 direction: str = "down", distance_px: float = 100.0, scale: float = 1.15,
                 easing: Easing = Easing.EASE_IN_OUT) -> EffectInstance:
    sign = -1 if direction == "up" else 1
    w, h = frame_w * scale, frame_h * scale
    base_x, base_y = _center_rect(frame_w, frame_h, w, h)
    y0, y1 = base_y - sign * distance_px / 2, base_y + sign * distance_px / 2
    return apply_transform_keyframes(clip, [
        TransformKeyframe(start_frame, base_x, y0, w, h, easing=easing),
        TransformKeyframe(end_frame, base_x, y1, w, h, easing=easing),
    ])


def create_orbit(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                  radius_px: float = 60.0, revolutions: float = 1.0, scale: float = 1.2,
                  samples_per_revolution: int = 24) -> EffectInstance:
    w, h = frame_w * scale, frame_h * scale
    base_x, base_y = _center_rect(frame_w, frame_h, w, h)
    total_frames = max(1, end_frame - start_frame)
    n_samples = max(4, round(samples_per_revolution * revolutions))
    frames: list[TransformKeyframe] = []
    for i in range(n_samples + 1):
        t = i / n_samples
        angle = 2 * math.pi * revolutions * t
        frame = start_frame + round(total_frames * t)
        x = base_x + radius_px * math.cos(angle)
        y = base_y + radius_px * math.sin(angle)
        frames.append(TransformKeyframe(frame, x, y, w, h, easing=Easing.LINEAR))
    return apply_transform_keyframes(clip, frames)


def create_parallax(layers: list[tuple[Clip, float]], frame_w: int, frame_h: int, *,
                     start_frame: int, end_frame: int, distance_px: float = 100.0,
                     direction: str = "right", easing: Easing = Easing.EASE_IN_OUT) -> list[EffectInstance]:
    """layers: [(clip, depth), ...] where depth in (0,1]; smaller depth = further away = moves less."""
    effects = []
    for clip, depth in layers:
        d = distance_px * depth
        effects.append(create_pan(clip, frame_w, frame_h, start_frame=start_frame, end_frame=end_frame,
                                   direction=direction, distance_px=d, scale=1.0 + 0.25 * depth, easing=easing))
    return effects


def _jitter_path(start_frame: int, end_frame: int, *, sample_every: int, amplitude_px: float,
                  amplitude_deg: float, seed: int | None) -> list[tuple[int, float, float, float]]:
    rng = random.Random(seed)
    frames = list(range(start_frame, end_frame + 1, max(1, sample_every)))
    if frames[-1] != end_frame:
        frames.append(end_frame)
    path = []
    for f in frames:
        dx = rng.uniform(-amplitude_px, amplitude_px)
        dy = rng.uniform(-amplitude_px, amplitude_px)
        dr = rng.uniform(-amplitude_deg, amplitude_deg)
        path.append((f, dx, dy, dr))
    # taper first/last sample to zero so the shake blends cleanly with neighbours
    f0, *_ = path[0]
    path[0] = (f0, 0.0, 0.0, 0.0)
    fN, *_ = path[-1]
    path[-1] = (fN, 0.0, 0.0, 0.0)
    return path


_INTENSITY_PRESETS = {
    "subtle": (2.0, 0.4),
    "medium": (6.0, 1.0),
    "aggressive": (14.0, 2.2),
}


def create_handheld_motion(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                            intensity: str = "medium", scale: float = 1.08, seed: int | None = None) -> EffectInstance:
    amp_px, amp_deg = _INTENSITY_PRESETS.get(intensity, _INTENSITY_PRESETS["medium"])
    w, h = frame_w * scale, frame_h * scale
    base_x, base_y = _center_rect(frame_w, frame_h, w, h)
    jitter = _jitter_path(start_frame, end_frame, sample_every=8, amplitude_px=amp_px,
                           amplitude_deg=amp_deg, seed=seed)
    frames = [TransformKeyframe(f, base_x + dx, base_y + dy, w, h, rotation=dr, easing=Easing.EASE_IN_OUT)
              for f, dx, dy, dr in jitter]
    return apply_transform_keyframes(clip, frames)


def create_camera_shake(clip: Clip, frame_w: int, frame_h: int, *, start_frame: int, end_frame: int,
                         intensity: str = "medium", scale: float = 1.1, seed: int | None = None) -> EffectInstance:
    amp_px, amp_deg = _INTENSITY_PRESETS.get(intensity, _INTENSITY_PRESETS["medium"])
    amp_px, amp_deg = amp_px * 1.8, amp_deg * 1.8
    w, h = frame_w * scale, frame_h * scale
    base_x, base_y = _center_rect(frame_w, frame_h, w, h)
    jitter = _jitter_path(start_frame, end_frame, sample_every=2, amplitude_px=amp_px,
                           amplitude_deg=amp_deg, seed=seed)
    frames = [TransformKeyframe(f, base_x + dx, base_y + dy, w, h, rotation=dr, easing=Easing.LINEAR)
              for f, dx, dy, dr in jitter]
    return apply_transform_keyframes(clip, frames)


def create_zoom_punch(clip: Clip, frame_w: int, frame_h: int, *, at_frame: int, intensity: float = 0.15,
                       attack_frames: int = 3, decay_frames: int = 10) -> EffectInstance:
    base_x, base_y = _center_rect(frame_w, frame_h, frame_w, frame_h)
    peak_w, peak_h = frame_w * (1 + intensity), frame_h * (1 + intensity)
    peak_x, peak_y = _center_rect(frame_w, frame_h, peak_w, peak_h)
    return apply_transform_keyframes(clip, [
        TransformKeyframe(max(0, at_frame - attack_frames), base_x, base_y, frame_w, frame_h, easing=Easing.EASE_OUT),
        TransformKeyframe(at_frame, peak_x, peak_y, peak_w, peak_h, easing=Easing.EASE_IN_OUT),
        TransformKeyframe(at_frame + decay_frames, base_x, base_y, frame_w, frame_h, easing=Easing.EASE_OUT),
    ])


def create_impact_motion(clip: Clip, frame_w: int, frame_h: int, *, at_frame: int,
                          punch_intensity: float = 0.1, shake_intensity: str = "aggressive",
                          duration_frames: int = 14, seed: int | None = None) -> list[EffectInstance]:
    """Zoom punch plus a short violent shake -- for the strongest hits/cuts.

    Shake is applied first, punch second: both write keyframes into the same
    qtblend "rect" track and shake's start/end samples land on the exact
    same frames as punch's peak/decay (both anchor to at_frame /
    at_frame+duration_frames), so whichever runs last wins at those frames.
    Punch must win there or its peak/decay would be silently overwritten by
    shake's neutral taper.
    """
    shake = create_camera_shake(clip, frame_w, frame_h, start_frame=at_frame,
                                 end_frame=at_frame + duration_frames, intensity=shake_intensity, seed=seed)
    punch = create_zoom_punch(clip, frame_w, frame_h, at_frame=at_frame, intensity=punch_intensity,
                               attack_frames=2, decay_frames=duration_frames)
    return [punch, shake]
