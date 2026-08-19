"""PROFESSIONAL MOTION SYSTEM tools (spec section 7)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.keyframes import motion
from kdenlive_mcp.core.timeline.model import Clip, Project
from kdenlive_mcp.errors import ClipNotFoundError, InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, mutates, s2f, tool_result


def _find_clip(session, sequence_id: str | None, clip_id: str) -> tuple[Project, Clip]:
    project = session.project
    sid = sequence_id or project.active_sequence_id
    seq = project.get_sequence(sid) if sid else None
    if seq is None:
        raise InvalidOperationError("Project has no active sequence")
    found = seq.get_clip(clip_id)
    if found is None:
        raise ClipNotFoundError(f"Clip not found: {clip_id}")
    _, clip = found
    return project, clip


def _effect_result(effect) -> dict:
    return {
        "effect_id": effect.id, "service": effect.service,
        "keyframed_params": {k: len(v.keyframes) for k, v in effect.keyframed_params.items()},
    }


def register(mcp: FastMCP) -> None:

    def _frame_size(project: Project) -> tuple[int, int]:
        return project.settings.width, project.settings.height

    def _path_to_frames(path: list[list[float]], project: Project) -> list[tuple]:
        return [(s2f(pt[0], project), *pt[1:]) for pt in path]

    @mcp.tool()
    @catch_errors
    @mutates
    def animate_position(clip_id: str, path: list[list[float]], easing: str = "ease_in_out",
                          sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Animate a clip's position. path: list of [time_seconds, x, y] keyframes (pixels)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.animate_position(clip, w, h, _path_to_frames(path, project),
                                          easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def animate_scale(clip_id: str, path: list[list[float]], anchor: str = "center", easing: str = "ease_in_out",
                       sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Animate a clip's scale. path: list of [time_seconds, scale_factor] keyframes (1.0 = 100%)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.animate_scale(clip, w, h, _path_to_frames(path, project),
                                       anchor=anchor, easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def animate_rotation(clip_id: str, path: list[list[float]], easing: str = "ease_in_out",
                          sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Animate a clip's rotation. path: list of [time_seconds, degrees] keyframes."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.animate_rotation(clip, w, h, _path_to_frames(path, project),
                                          easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def animate_opacity(clip_id: str, path: list[list[float]], easing: str = "linear",
                         sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Animate a clip's opacity. path: list of [time_seconds, opacity_0_to_1] keyframes."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.animate_opacity(clip, w, h, _path_to_frames(path, project),
                                         easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def animate_anchor_point(clip_id: str, anchor: str, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Set a clip's transform anchor (center/top_left/top_right/bottom_left/bottom_right)."""
        session = get_state().get(project_id)
        _, clip = _find_clip(session, sequence_id, clip_id)
        motion.animate_anchor_point(clip, anchor)
        return tool_result(anchor=anchor)

    @mcp.tool()
    @catch_errors
    @mutates
    def animate_crop(clip_id: str, path: list[list[float]], easing: str = "linear",
                      sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Animate a crop. path: list of [time_seconds, left, right, top, bottom] as 0..1 fractions."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        frames_path = [(s2f(pt[0], project), pt[1], pt[2], pt[3], pt[4]) for pt in path]
        effect = motion.animate_crop(clip, frames_path, easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_motion_path(clip_id: str, points: list[list[float]], easing: str = "ease_in_out",
                            sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Create a multi-point motion path. points: list of [time_seconds, x, y]."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_motion_path(clip, w, h, _path_to_frames(points, project),
                                            easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_bezier_motion(clip_id: str, points: list[list[float]],
                              handles: list[float] | None = None,
                              sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Create bezier-eased motion. points: [time_seconds, x, y]; handles: [x1,y1,x2,y2] (default 0.25,0.1,0.25,1.0)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        h_tuple = tuple(handles) if handles else (0.25, 0.1, 0.25, 1.0)
        effect = motion.create_bezier_motion(clip, w, h, _path_to_frames(points, project), handles=h_tuple)
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_smooth_follow(clip_id: str, raw_path: list[list[float]], smoothing: float = 0.25,
                              sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Smooth a noisy tracked path (e.g. from object tracking) before keyframing it."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_smooth_follow(clip, w, h, _path_to_frames(raw_path, project), smoothing=smoothing)
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_camera_push(clip_id: str, start_seconds: float, end_seconds: float,
                            start_scale: float = 1.0, end_scale: float = 1.15, easing: str = "ease_in_out",
                            sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Slow zoom-in over the clip (or a portion of it)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_camera_push(clip, w, h, start_frame=s2f(start_seconds, project),
                                            end_frame=s2f(end_seconds, project), start_scale=start_scale,
                                            end_scale=end_scale, easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_camera_pull(clip_id: str, start_seconds: float, end_seconds: float,
                            start_scale: float = 1.15, end_scale: float = 1.0, easing: str = "ease_in_out",
                            sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Slow zoom-out over the clip (or a portion of it)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_camera_pull(clip, w, h, start_frame=s2f(start_seconds, project),
                                            end_frame=s2f(end_seconds, project), start_scale=start_scale,
                                            end_scale=end_scale, easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_pan(clip_id: str, start_seconds: float, end_seconds: float, direction: str = "right",
                   distance_px: float = 100.0, scale: float = 1.15, easing: str = "ease_in_out",
                   sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Horizontal pan (direction: left/right)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_pan(clip, w, h, start_frame=s2f(start_seconds, project),
                                    end_frame=s2f(end_seconds, project), direction=direction,
                                    distance_px=distance_px, scale=scale, easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_tilt(clip_id: str, start_seconds: float, end_seconds: float, direction: str = "down",
                    distance_px: float = 100.0, scale: float = 1.15, easing: str = "ease_in_out",
                    sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Vertical tilt (direction: up/down)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_tilt(clip, w, h, start_frame=s2f(start_seconds, project),
                                     end_frame=s2f(end_seconds, project), direction=direction,
                                     distance_px=distance_px, scale=scale, easing=motion.resolve_easing(easing))
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_orbit(clip_id: str, start_seconds: float, end_seconds: float, radius_px: float = 60.0,
                      revolutions: float = 1.0, scale: float = 1.2,
                      sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Circular orbit motion around the frame center."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_orbit(clip, w, h, start_frame=s2f(start_seconds, project),
                                      end_frame=s2f(end_seconds, project), radius_px=radius_px,
                                      revolutions=revolutions, scale=scale)
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_parallax(clip_ids_with_depth: list[list], start_seconds: float, end_seconds: float,
                         distance_px: float = 100.0, direction: str = "right", easing: str = "ease_in_out",
                         sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Multi-layer parallax pan. clip_ids_with_depth: list of [clip_id, depth_0_to_1] (closer = higher depth)."""
        session = get_state().get(project_id)
        project = session.project
        layers = []
        for clip_id, depth in clip_ids_with_depth:
            _, clip = _find_clip(session, sequence_id, clip_id)
            layers.append((clip, float(depth)))
        w, h = _frame_size(project)
        effects = motion.create_parallax(layers, w, h, start_frame=s2f(start_seconds, project),
                                          end_frame=s2f(end_seconds, project), distance_px=distance_px,
                                          direction=direction, easing=motion.resolve_easing(easing))
        return tool_result(effects=[_effect_result(e) for e in effects])

    @mcp.tool()
    @catch_errors
    @mutates
    def create_handheld_motion(clip_id: str, start_seconds: float, end_seconds: float,
                                intensity: str = "medium", scale: float = 1.08, seed: int | None = None,
                                sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Organic handheld camera jitter. intensity: subtle/medium/aggressive."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_handheld_motion(clip, w, h, start_frame=s2f(start_seconds, project),
                                                 end_frame=s2f(end_seconds, project), intensity=intensity,
                                                 scale=scale, seed=seed)
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_camera_shake(clip_id: str, start_seconds: float, end_seconds: float,
                             intensity: str = "medium", scale: float = 1.1, seed: int | None = None,
                             sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Punchy, high-frequency camera shake. intensity: subtle/medium/aggressive."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_camera_shake(clip, w, h, start_frame=s2f(start_seconds, project),
                                              end_frame=s2f(end_seconds, project), intensity=intensity,
                                              scale=scale, seed=seed)
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_zoom_punch(clip_id: str, at_seconds: float, intensity: float = 0.15,
                           attack_frames: int = 3, decay_frames: int = 10,
                           sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """A quick punch-zoom accent at a specific moment (e.g. on a beat)."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effect = motion.create_zoom_punch(clip, w, h, at_frame=s2f(at_seconds, project), intensity=intensity,
                                            attack_frames=attack_frames, decay_frames=decay_frames)
        return tool_result(effect=_effect_result(effect))

    @mcp.tool()
    @catch_errors
    @mutates
    def create_impact_motion(clip_id: str, at_seconds: float, punch_intensity: float = 0.1,
                              shake_intensity: str = "aggressive", duration_seconds: float = 0.5,
                              seed: int | None = None, sequence_id: str | None = None,
                              project_id: str | None = None) -> dict:
        """Zoom punch + violent shake combo for the strongest hits/cuts."""
        session = get_state().get(project_id)
        project, clip = _find_clip(session, sequence_id, clip_id)
        w, h = _frame_size(project)
        effects = motion.create_impact_motion(clip, w, h, at_frame=s2f(at_seconds, project),
                                                punch_intensity=punch_intensity, shake_intensity=shake_intensity,
                                                duration_frames=s2f(duration_seconds, project), seed=seed)
        return tool_result(effects=[_effect_result(e) for e in effects])
