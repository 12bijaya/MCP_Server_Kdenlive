"""Thin effect-domain helpers on top of `core.timeline.model.EffectInstance`.

Deliberately decoupled from any specific `Clip` -- this module only builds
`EffectInstance` objects; the caller is responsible for appending them to a
clip's `effects` list (and picking a sane `index` via
`Clip.next_effect_index()` if ordering matters).
"""

from __future__ import annotations

from typing import Any

from kdenlive_mcp.core.timeline.model import EffectInstance, new_id
from kdenlive_mcp.kdenlive.effects.catalog import validate_effect_available


def _coerce_default(raw: Any) -> Any:
    """Best-effort conversion of an XML-sourced default string to a native type.

    Kdenlive effect XML defaults are plain strings (e.g. "0.5", "40", "0").
    Some contain profile-relative tokens like "%width" or multi-keyframe
    animation strings (e.g. "0=0;%out=1") which cannot be meaningfully
    converted to a single number -- those are left as-is.
    """
    if not isinstance(raw, str):
        return raw
    if "%" in raw or "=" in raw or ";" in raw:
        return raw
    try:
        if raw.lstrip("-").isdigit():
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def create_effect(effect_id: str, *, params: dict[str, Any] | None = None) -> EffectInstance:
    """Build an `EffectInstance` for `effect_id`, seeded with the catalog's
    default parameter values and overridden by `params`.

    Raises `EffectUnavailableError` (via `validate_effect_available`) if
    `effect_id` isn't in the default catalog.
    """
    definition = validate_effect_available(effect_id)

    defaults: dict[str, Any] = {}
    for param in definition.parameters:
        name = param.get("name")
        if not name or "default" not in param:
            continue
        defaults[name] = _coerce_default(param["default"])

    final_params = {**defaults, **(params or {})}

    return EffectInstance(
        id=new_id("effect"),
        service=definition.tag,
        display_name=definition.name,
        params=final_params,
    )
