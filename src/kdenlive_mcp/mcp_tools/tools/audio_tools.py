"""AUDIO ENGINE + BEAT-SYNC EDITING tools (spec sections 12-14)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.assets.sfx import SFX_CATEGORIES, place_sfx, sfx_on_beat
from kdenlive_mcp.core.audio import beat_sync
from kdenlive_mcp.core.audio.beats import (
    detect_bpm_and_beats, detect_downbeats, detect_energy_sections,
    detect_music_sections, detect_silence,
)
from kdenlive_mcp.core.audio.waveform import analyze_waveform
from kdenlive_mcp.core.timeline.timecode import seconds_to_frames
from kdenlive_mcp.errors import ClipNotFoundError, InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, mutates, clip_summary, s2f, tool_result
from kdenlive_mcp.storage.workspace import resolve_source_path


def register(mcp: FastMCP) -> None:

    def _asset_path(session, asset_id: str):
        asset = session.media_index.get(asset_id)
        if asset is None:
            raise InvalidOperationError(f"Asset not found: {asset_id}")
        return asset, resolve_source_path(asset.path)

    def _seq(session, sequence_id):
        sid = sequence_id or session.project.active_sequence_id
        seq = session.project.get_sequence(sid) if sid else None
        if seq is None:
            raise InvalidOperationError("Project has no active sequence")
        return seq

    @mcp.tool()
    @catch_errors
    def analyze_waveform_tool(asset_id: str, project_id: str | None = None) -> dict:
        """Analyze an audio asset's waveform: RMS envelope, peak level, clipping, loudness."""
        session = get_state().get(project_id)
        asset, path = _asset_path(session, asset_id)
        return tool_result(waveform=analyze_waveform(path, asset=asset))

    @mcp.tool()
    @catch_errors
    def detect_beats(asset_id: str, project_id: str | None = None) -> dict:
        """Detect BPM and beat timestamps (seconds) in an audio asset."""
        session = get_state().get(project_id)
        asset, path = _asset_path(session, asset_id)
        return tool_result(**detect_bpm_and_beats(path, asset=asset))

    @mcp.tool()
    @catch_errors
    def detect_downbeats_tool(asset_id: str, project_id: str | None = None) -> dict:
        """Estimate downbeat (bar start) timestamps. Heuristic, not ground truth."""
        session = get_state().get(project_id)
        asset, path = _asset_path(session, asset_id)
        return tool_result(downbeat_times=detect_downbeats(path, asset=asset))

    @mcp.tool()
    @catch_errors
    def detect_energy_sections_tool(asset_id: str, section_seconds: float = 2.0, project_id: str | None = None) -> dict:
        """Segment an audio asset into low/medium/high energy windows."""
        session = get_state().get(project_id)
        asset, path = _asset_path(session, asset_id)
        return tool_result(sections=detect_energy_sections(path, section_seconds=section_seconds, asset=asset))

    @mcp.tool()
    @catch_errors
    def detect_music_sections_tool(asset_id: str, project_id: str | None = None) -> dict:
        """Best-effort structural segmentation into generically-labeled sections (not semantic verse/chorus detection)."""
        session = get_state().get(project_id)
        asset, path = _asset_path(session, asset_id)
        return tool_result(sections=detect_music_sections(path, asset=asset))

    @mcp.tool()
    @catch_errors
    def detect_silence_tool(asset_id: str, threshold_db: float = -40.0, min_duration: float = 0.3,
                             project_id: str | None = None) -> dict:
        """Detect silent spans in an audio asset via ffmpeg's silencedetect."""
        session = get_state().get(project_id)
        asset, path = _asset_path(session, asset_id)
        return tool_result(silence=detect_silence(path, threshold_db=threshold_db, min_duration=min_duration, asset=asset))

    @mcp.tool()
    @catch_errors
    @mutates
    def cut_on_beat(clip_ids: list[str], asset_id: str, intensity: str = "medium",
                    use_downbeats: bool = True, sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Re-time an ordered list of existing clips so their boundaries land on selected beats of a music asset."""
        session = get_state().get(project_id)
        project = session.project
        seq = _seq(session, sequence_id)
        _, path = _asset_path(session, asset_id)
        beat_data = detect_bpm_and_beats(path)
        downbeats = detect_downbeats(path) if use_downbeats else None

        clips = []
        for cid in clip_ids:
            found = seq.get_clip(cid)
            if found is None:
                raise ClipNotFoundError(f"Clip not found: {cid}")
            clips.append(found[1])

        beat_sync.cut_on_beat(clips, beat_data["beat_times"], project.settings.fps,
                               intensity=intensity, downbeat_times=downbeats)
        project.dirty = True
        return tool_result(clips=[clip_summary(c, project) for c in clips], bpm=beat_data.get("bpm"))

    def _beat_effect(tool_fn):
        def impl(clip_id: str, asset_id: str, intensity: str = "medium", use_downbeats: bool = True,
                  sequence_id: str | None = None, project_id: str | None = None) -> dict:
            session = get_state().get(project_id)
            project = session.project
            seq = _seq(session, sequence_id)
            found = seq.get_clip(clip_id)
            if found is None:
                raise ClipNotFoundError(f"Clip not found: {clip_id}")
            _, clip = found
            _, path = _asset_path(session, asset_id)
            beat_data = detect_bpm_and_beats(path)
            downbeats = detect_downbeats(path) if use_downbeats else None
            w, h = project.settings.width, project.settings.height
            effects = tool_fn(clip, w, h, beat_data["beat_times"], project.settings.fps,
                               intensity=intensity, downbeat_times=downbeats)
            project.dirty = True
            return tool_result(effect_count=len(effects), bpm=beat_data.get("bpm"))
        return impl

    @mcp.tool(name="zoom_on_beat")
    @catch_errors
    @mutates
    def zoom_on_beat_tool(clip_id: str, asset_id: str, intensity: str = "medium", use_downbeats: bool = True,
                           sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Apply a zoom punch at every selected beat of a music asset."""
        return _beat_effect(beat_sync.zoom_on_beat)(clip_id, asset_id, intensity, use_downbeats, sequence_id, project_id)

    @mcp.tool(name="shake_on_beat")
    @catch_errors
    @mutates
    def shake_on_beat_tool(clip_id: str, asset_id: str, intensity: str = "medium", use_downbeats: bool = True,
                            sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Apply a short camera shake at every selected beat of a music asset."""
        return _beat_effect(beat_sync.shake_on_beat)(clip_id, asset_id, intensity, use_downbeats, sequence_id, project_id)

    @mcp.tool(name="flash_on_beat")
    @catch_errors
    @mutates
    def flash_on_beat_tool(clip_id: str, asset_id: str, intensity: str = "medium", use_downbeats: bool = True,
                            sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Approximate a flash (opacity spike) at every selected beat of a music asset."""
        return _beat_effect(beat_sync.flash_on_beat)(clip_id, asset_id, intensity, use_downbeats, sequence_id, project_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def montage_on_beats(track_id: str, clips_with_sources: list[list], asset_id: str,
                          intensity: str = "medium", use_downbeats: bool = True,
                          sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Build a beat-synced montage. clips_with_sources: list of [asset_id, source_in_seconds, source_out_seconds]."""
        session = get_state().get(project_id)
        project = session.project
        sid = sequence_id or project.active_sequence_id
        _, path = _asset_path(session, asset_id)
        beat_data = detect_bpm_and_beats(path)
        downbeats = detect_downbeats(path) if use_downbeats else None

        fps = project.settings.fps
        sources = [(a_id, seconds_to_frames(src_in, fps), seconds_to_frames(src_out, fps))
                   for a_id, src_in, src_out in clips_with_sources]

        clips = beat_sync.montage_on_beats(project, sid, track_id, sources, beat_data["beat_times"], fps,
                                            intensity=intensity, downbeat_times=downbeats)
        project.dirty = True
        return tool_result(clips=[clip_summary(c, project) for c in clips], bpm=beat_data.get("bpm"))

    @mcp.tool()
    @catch_errors
    @mutates
    def add_sfx(track_id: str, asset_id: str, at_seconds: float, category: str,
                sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Place a short sound-effect asset at a specific time on an audio track."""
        session = get_state().get(project_id)
        project = session.project
        sid = sequence_id or project.active_sequence_id
        asset = session.media_index.get(asset_id)
        if asset is None:
            raise InvalidOperationError(f"Asset not found: {asset_id}")
        clip = place_sfx(project, sid, track_id, asset_id=asset_id,
                          at_frame=s2f(at_seconds, project), category=category, asset=asset)
        project.dirty = True
        return tool_result(clip=clip_summary(clip, project))

    @mcp.tool()
    @catch_errors
    @mutates
    def sfx_on_beat_tool(track_id: str, sfx_asset_id: str, music_asset_id: str, category: str = "hit",
                          intensity: str = "medium", use_downbeats: bool = True,
                          sequence_id: str | None = None, project_id: str | None = None) -> dict:
        """Place one SFX hit at every selected beat of a music asset."""
        session = get_state().get(project_id)
        project = session.project
        sid = sequence_id or project.active_sequence_id
        sfx_asset = session.media_index.get(sfx_asset_id)
        if sfx_asset is None:
            raise InvalidOperationError(f"SFX asset not found: {sfx_asset_id}")
        _, path = _asset_path(session, music_asset_id)
        beat_data = detect_bpm_and_beats(path)
        downbeats = detect_downbeats(path) if use_downbeats else None
        clips = sfx_on_beat(project, sid, track_id, sfx_asset, beat_data["beat_times"], project.settings.fps,
                             intensity=intensity, downbeat_times=downbeats, category=category)
        project.dirty = True
        return tool_result(clips=[clip_summary(c, project) for c in clips])

    @mcp.tool()
    @catch_errors
    def list_sfx_categories() -> dict:
        """List the supported SFX categories."""
        return tool_result(categories=SFX_CATEGORIES)
