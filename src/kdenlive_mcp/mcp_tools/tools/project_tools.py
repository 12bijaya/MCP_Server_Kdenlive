"""PROJECT MANAGEMENT tools (spec section 2)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.timeline.model import Sequence, new_id
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.adapter import project as adapter_project
from kdenlive_mcp.kdenlive.adapter.profiles import FPS_PRESETS
from kdenlive_mcp.mcp_tools.state import ProjectSession, get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, mutates, project_summary, sequence_summary, tool_result
from kdenlive_mcp.validation.project_validator import validate_project as run_project_validation


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    @catch_errors
    def create_project(name: str, resolution: str = "1080p", fps: str = "30",
                        orientation: str = "landscape") -> dict:
        """Create a new, empty Kdenlive project (not yet saved to disk).

        resolution: one of 720p, 1080p, 1440p, 4k.
        fps: one of 23.976, 24, 25, 29.97, 30, 50, 59.94, 60.
        orientation: landscape, vertical, or square.
        """
        project = adapter_project.create_project(name, resolution=resolution, fps=fps, orientation=orientation)
        media_index = MediaIndex(index_path=None)
        get_state().add(ProjectSession(project, media_index))
        return tool_result(project=project_summary(project))

    @mcp.tool()
    @catch_errors
    def open_project(path: str) -> dict:
        """Open an existing .kdenlive project file, parsing it into the internal model."""
        project, media_index = adapter_project.open_project(path)
        get_state().add(ProjectSession(project, media_index))
        return tool_result(project=project_summary(project))

    @mcp.tool()
    @catch_errors
    def save_project(project_id: str | None = None) -> dict:
        """Save the active (or given) project to its existing path."""
        session = get_state().get(project_id)
        path = adapter_project.save_project(session.project, session.media_index)
        return tool_result(path=str(path))

    @mcp.tool()
    @catch_errors
    def save_project_as(new_path: str, project_id: str | None = None) -> dict:
        """Save the active (or given) project to a new path."""
        session = get_state().get(project_id)
        path = adapter_project.save_project_as(session.project, session.media_index, new_path)
        return tool_result(path=str(path))

    @mcp.tool()
    @catch_errors
    def close_project(project_id: str | None = None, save_first: bool = False) -> dict:
        """Close a project. Never deletes the file on disk."""
        state = get_state()
        session = state.get(project_id)
        if save_first:
            adapter_project.save_project(session.project, session.media_index)
        state.close(session.project.id)
        return tool_result(closed=session.project.id)

    @mcp.tool()
    @catch_errors
    def get_project_info(project_id: str | None = None) -> dict:
        """Return a summary of the active (or given) project: settings, sequences, tracks."""
        session = get_state().get(project_id)
        summary = project_summary(session.project)
        summary["sequences"] = [sequence_summary(s, session.project) for s in session.project.sequences]
        return tool_result(project=summary)

    @mcp.tool()
    @catch_errors
    def validate_project(project_id: str | None = None, use_melt: bool = True) -> dict:
        """Validate the project's structural integrity (and, if melt is available, that it actually loads)."""
        session = get_state().get(project_id)
        result = run_project_validation(session.project, session.media_index, use_melt=use_melt)
        return tool_result(validation=result.to_dict())

    @mcp.tool()
    @catch_errors
    def backup_project(project_id: str | None = None) -> dict:
        """Copy the project's saved file to a timestamped backup next to it."""
        session = get_state().get(project_id)
        if not session.project.path:
            raise InvalidOperationError("Project has never been saved; nothing to back up",
                                         suggestion="Call save_project first.")
        backup_path = adapter_project.backup_project(session.project.path)
        return tool_result(backup_path=str(backup_path))

    @mcp.tool()
    @catch_errors
    def restore_project(backup_path: str, restore_to: str) -> dict:
        """Restore a previously created backup file to a destination path."""
        restored = adapter_project.restore_project(backup_path, restore_to=restore_to)
        return tool_result(restored_path=str(restored))

    @mcp.tool()
    @catch_errors
    def duplicate_project(project_id: str | None = None) -> dict:
        """Create an in-memory duplicate of a project (not yet saved to disk)."""
        session = get_state().get(project_id)
        duplicate = adapter_project.duplicate_project(session.project)
        new_index = MediaIndex(index_path=None)
        for asset in session.media_index.list():
            new_index.upsert(asset)
        get_state().add(ProjectSession(duplicate, new_index))
        return tool_result(project=project_summary(duplicate))

    @mcp.tool()
    @catch_errors
    def list_sequences(project_id: str | None = None) -> dict:
        """List every sequence (timeline) in the project."""
        session = get_state().get(project_id)
        return tool_result(sequences=[sequence_summary(s, session.project) for s in session.project.sequences])

    @mcp.tool()
    @catch_errors
    @mutates
    def create_sequence(name: str = "Sequence", project_id: str | None = None) -> dict:
        """Create a new, empty sequence (timeline) with 2 video + 2 audio tracks."""
        session = get_state().get(project_id)
        from kdenlive_mcp.core.timeline.model import Track
        seq = Sequence(id=new_id("seq"), name=name)
        seq.tracks.append(Track(id=new_id("track"), index=0, track_type="video", name="V1"))
        seq.tracks.append(Track(id=new_id("track"), index=1, track_type="video", name="V2"))
        seq.tracks.append(Track(id=new_id("track"), index=0, track_type="audio", name="A1"))
        seq.tracks.append(Track(id=new_id("track"), index=1, track_type="audio", name="A2"))
        session.project.sequences.append(seq)
        session.project.dirty = True
        return tool_result(sequence=sequence_summary(seq, session.project))

    @mcp.tool()
    @catch_errors
    @mutates
    def delete_sequence(sequence_id: str, project_id: str | None = None) -> dict:
        """Delete a sequence from the project. Refuses to delete the last remaining sequence."""
        session = get_state().get(project_id)
        project = session.project
        if len(project.sequences) <= 1:
            raise InvalidOperationError("Cannot delete the only sequence in a project")
        if project.get_sequence(sequence_id) is None:
            raise InvalidOperationError(f"Sequence not found: {sequence_id}")
        project.sequences = [s for s in project.sequences if s.id != sequence_id]
        if project.active_sequence_id == sequence_id:
            project.active_sequence_id = project.sequences[0].id
        project.dirty = True
        return tool_result(deleted=sequence_id)

    @mcp.tool()
    @catch_errors
    @mutates
    def set_project_resolution(resolution: str, orientation: str = "landscape", project_id: str | None = None) -> dict:
        """Explicitly change the project's resolution. Never happens implicitly elsewhere."""
        session = get_state().get(project_id)
        from kdenlive_mcp.kdenlive.adapter.profiles import resolve_profile
        new_settings = resolve_profile(resolution, str(session.project.settings.fps_float), orientation=orientation)
        session.project.settings.width = new_settings.width
        session.project.settings.height = new_settings.height
        session.project.settings.display_aspect_num = new_settings.display_aspect_num
        session.project.settings.display_aspect_den = new_settings.display_aspect_den
        session.project.dirty = True
        return tool_result(project=project_summary(session.project))

    @mcp.tool()
    @catch_errors
    @mutates
    def set_project_fps(fps: str, project_id: str | None = None) -> dict:
        """Explicitly change the project's frame rate. Never happens implicitly elsewhere."""
        if fps not in FPS_PRESETS:
            raise InvalidOperationError(f"Unknown fps '{fps}'", suggestion=f"Use one of: {sorted(FPS_PRESETS)}")
        session = get_state().get(project_id)
        frac = FPS_PRESETS[fps]
        session.project.settings.fps_num = frac.numerator
        session.project.settings.fps_den = frac.denominator
        session.project.dirty = True
        return tool_result(project=project_summary(session.project))

    @mcp.tool()
    @catch_errors
    @mutates
    def set_project_audio_settings(sample_rate: int | None = None, audio_channels: int | None = None,
                                    project_id: str | None = None) -> dict:
        """Set project-wide audio sample rate / channel count."""
        session = get_state().get(project_id)
        if sample_rate is not None:
            session.project.settings.sample_rate = sample_rate
        if audio_channels is not None:
            session.project.settings.audio_channels = audio_channels
        session.project.dirty = True
        return tool_result(project=project_summary(session.project))

    @mcp.tool()
    @catch_errors
    @mutates
    def set_project_metadata(key: str, value: str, project_id: str | None = None) -> dict:
        """Set an arbitrary project metadata key/value (author, description, tags, ...)."""
        session = get_state().get(project_id)
        session.project.metadata[key] = value
        session.project.dirty = True
        return tool_result(metadata=session.project.metadata)
