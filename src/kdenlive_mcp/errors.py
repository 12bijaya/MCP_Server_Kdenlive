"""Structured error types shared across the MCP tool layer.

Every tool-facing failure should raise (or be converted to) a KdenliveMcpError
so the AI agent always receives {success, error: {code, message, suggestion}}
instead of a bare exception string.
"""

from __future__ import annotations

from typing import Any


class KdenliveMcpError(Exception):
    """Base class for all errors surfaced to MCP tool callers."""

    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, suggestion: str | None = None, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.details = details or {}
        if code:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.suggestion:
            error["suggestion"] = self.suggestion
        if self.details:
            error["details"] = self.details
        return {"success": False, "error": error}


class ProjectNotOpenError(KdenliveMcpError):
    code = "PROJECT_NOT_OPEN"


class ProjectNotFoundError(KdenliveMcpError):
    code = "PROJECT_NOT_FOUND"


class InvalidPathError(KdenliveMcpError):
    code = "INVALID_PATH"


class MediaNotFoundError(KdenliveMcpError):
    code = "MEDIA_NOT_FOUND"


class UnsupportedMediaError(KdenliveMcpError):
    code = "UNSUPPORTED_MEDIA"


class ClipNotFoundError(KdenliveMcpError):
    code = "CLIP_NOT_FOUND"


class TrackNotFoundError(KdenliveMcpError):
    code = "TRACK_NOT_FOUND"


class EffectUnavailableError(KdenliveMcpError):
    code = "EFFECT_UNAVAILABLE"


class TransitionUnavailableError(KdenliveMcpError):
    code = "TRANSITION_UNAVAILABLE"


class ValidationError(KdenliveMcpError):
    code = "VALIDATION_FAILED"


class ExternalToolError(KdenliveMcpError):
    code = "EXTERNAL_TOOL_FAILED"


class InvalidOperationError(KdenliveMcpError):
    code = "INVALID_OPERATION"


class SnapshotNotFoundError(KdenliveMcpError):
    code = "SNAPSHOT_NOT_FOUND"
