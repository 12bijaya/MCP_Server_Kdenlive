"""MEDIA INGESTION tools (spec section 3)."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from kdenlive_mcp.core.analysis.media_analysis import analyze_media_file, scan_folder
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.mcp_tools.state import get_state
from kdenlive_mcp.mcp_tools.tools._common import catch_errors, tool_result
from kdenlive_mcp.storage.workspace import resolve_source_path


def _asset_dict(asset) -> dict:
    return {
        "id": asset.id, "path": asset.path, "kind": asset.kind, "duration": asset.duration,
        "width": asset.width, "height": asset.height, "fps": asset.fps, "orientation": asset.orientation,
        "has_audio": asset.has_audio, "has_video": asset.has_video, "video_codec": asset.video_codec,
        "audio_codec": asset.audio_codec, "sample_rate": asset.sample_rate, "size_bytes": asset.size_bytes,
        "thumbnail_path": asset.thumbnail_path, "tags": asset.tags,
    }


def register(mcp: FastMCP) -> None:

    def _import_one(path: str, project_id: str | None) -> dict:
        resolved = resolve_source_path(path)
        session = get_state().get(project_id)
        existing = session.media_index.find_by_path(resolved)
        if existing:
            return _asset_dict(existing)
        asset = analyze_media_file(resolved)
        session.media_index.upsert(asset)
        return _asset_dict(asset)

    @mcp.tool()
    @catch_errors
    def import_video(path: str, project_id: str | None = None) -> dict:
        """Import a video file into the project's media index."""
        return tool_result(asset=_import_one(path, project_id))

    @mcp.tool()
    @catch_errors
    def import_image(path: str, project_id: str | None = None) -> dict:
        """Import an image file into the project's media index."""
        return tool_result(asset=_import_one(path, project_id))

    @mcp.tool()
    @catch_errors
    def import_audio(path: str, project_id: str | None = None) -> dict:
        """Import an audio file into the project's media index."""
        return tool_result(asset=_import_one(path, project_id))

    @mcp.tool()
    @catch_errors
    def import_folder(folder: str, recursive: bool = True, project_id: str | None = None) -> dict:
        """Import every recognized media file in a folder."""
        resolved = resolve_source_path(folder)
        if not resolved.is_dir():
            raise InvalidOperationError(f"Not a directory: {folder}")
        files = scan_folder(resolved, recursive=recursive)
        imported = []
        errors = []
        for f in files:
            try:
                imported.append(_import_one(str(f), project_id))
            except Exception as exc:  # noqa: BLE001 - one bad file shouldn't abort the whole batch
                errors.append({"path": str(f), "error": str(exc)})
        return tool_result(imported=imported, count=len(imported), errors=errors)

    @mcp.tool()
    @catch_errors
    def scan_media_folder(folder: str, recursive: bool = True) -> dict:
        """List recognized media files in a folder without importing them."""
        resolved = resolve_source_path(folder)
        files = scan_folder(resolved, recursive=recursive)
        return tool_result(files=[str(f) for f in files], count=len(files))

    @mcp.tool()
    @catch_errors
    def analyze_media(path: str, project_id: str | None = None) -> dict:
        """Analyze a media file (ffprobe metadata + thumbnail) without adding it to the timeline."""
        return tool_result(asset=_import_one(path, project_id))

    @mcp.tool()
    @catch_errors
    def get_media_metadata(asset_id: str, project_id: str | None = None) -> dict:
        """Get full stored metadata for a previously imported asset."""
        session = get_state().get(project_id)
        asset = session.media_index.get(asset_id)
        if asset is None:
            raise InvalidOperationError(f"Asset not found: {asset_id}")
        return tool_result(asset=_asset_dict(asset))

    @mcp.tool()
    @catch_errors
    def detect_duplicate_media(project_id: str | None = None) -> dict:
        """Find groups of imported assets that look like duplicates (same size + duration)."""
        session = get_state().get(project_id)
        groups = session.media_index.duplicates()
        return tool_result(duplicate_groups=[[a.id for a in g] for g in groups], group_count=len(groups))

    @mcp.tool()
    @catch_errors
    def organize_media(project_id: str | None = None) -> dict:
        """Summarize the media bin grouped by kind, for the AI to reason about what's available."""
        session = get_state().get(project_id)
        by_kind: dict[str, list[str]] = {}
        for asset in session.media_index.list():
            by_kind.setdefault(asset.kind, []).append(asset.id)
        return tool_result(by_kind=by_kind, total=len(session.media_index.list()))
