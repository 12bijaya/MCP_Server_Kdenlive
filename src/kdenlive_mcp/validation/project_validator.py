"""Project validation.

Three layers, cheapest first:
  1. Structural: every clip/track/asset reference resolves, no overlaps,
     in/out ranges are sane. Pure Python, no subprocess.
  2. XML: the adapter can actually serialize the project and the result is
     well-formed. Still no subprocess.
  3. Optional real-engine check: if `melt` is available, write the project
     to a scratch file *inside the workspace* (never /tmp -- Kdenlive's
     snap sandbox cannot see /tmp, only $HOME-rooted paths) and ask melt to
     load+decode a small bounded frame range. Bounded explicitly via
     `out=N` and a hard subprocess timeout so this can never turn into an
     open-ended render.

melt unavailable/failing to even start is reported as "skipped", distinct
from the project actually being invalid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kdenlive_mcp.config import get_config
from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.timeline.model import Project
from kdenlive_mcp.errors import ExternalToolError
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter
from kdenlive_mcp.media.ffmpeg.runner import is_available, run

MELT_VALIDATE_FRAMES = 5
MELT_VALIDATE_TIMEOUT = 25.0


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    melt_checked: bool = False
    melt_skipped_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "melt_checked": self.melt_checked,
            "melt_skipped_reason": self.melt_skipped_reason,
        }


def validate_project(project: Project, media_index: MediaIndex, *, use_melt: bool = True) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not project.sequences:
        errors.append("Project has no sequences")

    for sequence in project.sequences:
        seen_clip_ids: set[str] = set()
        for track in sequence.tracks:
            sorted_clips = track.sorted_clips()
            for i, clip in enumerate(sorted_clips):
                if clip.id in seen_clip_ids:
                    errors.append(f"Duplicate clip id '{clip.id}' in sequence '{sequence.id}'")
                seen_clip_ids.add(clip.id)

                if clip.out_point <= clip.in_point:
                    errors.append(f"Clip '{clip.id}' has out_point <= in_point")
                if clip.position < 0:
                    errors.append(f"Clip '{clip.id}' has negative position")

                if i > 0 and clip.position < sorted_clips[i - 1].end:
                    errors.append(
                        f"Overlapping clips on track '{track.id}': "
                        f"'{sorted_clips[i - 1].id}' ends at {sorted_clips[i - 1].end}, "
                        f"'{clip.id}' starts at {clip.position}"
                    )

                if clip.asset_id:
                    asset = media_index.get(clip.asset_id)
                    if asset is None:
                        errors.append(f"Clip '{clip.id}' references unknown asset '{clip.asset_id}'")
                    else:
                        if not Path(asset.path).exists():
                            warnings.append(f"Missing media for asset '{asset.id}': {asset.path}")
                elif clip.clip_type not in ("color", "text"):
                    errors.append(f"Clip '{clip.id}' has no asset_id and is not a color/text clip")

                for effect in clip.effects:
                    if not effect.service:
                        errors.append(f"Effect on clip '{clip.id}' has no service name")

    melt_checked = False
    melt_skip_reason = None
    if use_melt and not errors:
        melt_checked, melt_skip_reason = _try_melt_validate(project, media_index)
        if melt_checked is False and melt_skip_reason is None:
            errors.append("melt reported the generated project as invalid (failed to load)")

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        melt_checked=melt_checked is True,
        melt_skipped_reason=melt_skip_reason,
    )


def _try_melt_validate(project: Project, media_index: MediaIndex) -> tuple[bool | None, str | None]:
    if not is_available("melt"):
        return None, "melt is not installed/configured on this system"

    cfg = get_config()
    scratch_dir = cfg.workspace_dir / ".validation_scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch_path = scratch_dir / f"validate_{project.id}.kdenlive"

    try:
        writer = KdenliveXmlWriter(project, media_index)
        writer.write(scratch_path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a validation error, not a crash
        return None, f"could not serialize project for melt validation: {exc}"

    try:
        result = run(
            "melt",
            [str(scratch_path), "-consumer", "null", f"out={MELT_VALIDATE_FRAMES}"],
            timeout=MELT_VALIDATE_TIMEOUT,
            check=False,
        )
    except ExternalToolError as exc:
        return None, f"melt could not run in this environment: {exc.message}"
    finally:
        scratch_path.unlink(missing_ok=True)

    if not result.ok or "Failed to load" in result.stderr:
        return False, None
    return True, None
