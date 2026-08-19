"""Named effect-stack presets built from real Kdenlive/MLT effects.

Every effect id referenced below was verified against the real Kdenlive
26.04.3 effect XML shipped on this machine
(/var/lib/snapd/snap/kdenlive/144/usr/share/kdenlive/effects/*.xml):

    avfilter.eq          -- avfilter_eq.xml       (contrast/brightness/saturation/gamma)
    vignette              -- vignette.xml          (native MLT vignette)
    sepia                 -- sepia.xml              (chrominance-shift sepia tone)
    grain                 -- grain.xml              (film grain)
    oldfilm               -- oldfilm.xml            (old film flicker/jitter)
    frei0r.softglow        -- frei0r_softglow.xml    (highlight glow/bloom)
    frei0r.IIRblur          -- frei0r_iirblur.xml     (gaussian/lowpass blur)
    frei0r.contrast0r       -- frei0r_contrast0r.xml  (contrast)
    frei0r.glow             -- frei0r_glow.xml        (glamour glow)
    avfilter.chromashift    -- avfilter_chromashift.xml (RGB channel shift / chromatic aberration)

For each, the `id` equals the MLT `tag` in the real XML, so the params dict
keys below are the real parameter names from those files (e.g. avfilter.eq's
params are prefixed "av." because that's how libavfilter-backed effects are
named in Kdenlive's own XML).

`build_effect_stack()` only includes an effect if the default catalog
(`kdenlive_mcp.kdenlive.effects.catalog.get_default_catalog`) reports it as
available -- if the catalog can't be loaded at all (e.g. Kdenlive isn't
installed on this machine), we can't validate against it, so we degrade
gracefully: still build the requested EffectInstance objects (using the ids
above, which are real service names either way) but log a warning instead of
silently pretending everything was verified.
"""

from __future__ import annotations

import logging
from typing import Any

from kdenlive_mcp.core.timeline.model import EffectInstance, new_id
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.effects.catalog import get_default_catalog

logger = logging.getLogger("kdenlive_mcp.core.effects.presets")

# Fallback display names, used only when the catalog is unavailable and we
# can't look the real <name> up from the XML.
_DISPLAY_NAMES: dict[str, str] = {
    "avfilter.eq": "Video Equalizer",
    "vignette": "Vignette Effect",
    "sepia": "Sepia",
    "grain": "Grain",
    "oldfilm": "Old Film Simulator",
    "frei0r.softglow": "Soft Glow",
    "frei0r.IIRblur": "Blur",
    "frei0r.contrast0r": "Contrast",
    "frei0r.glow": "Glow",
    "avfilter.chromashift": "Chroma Shift",
}

# preset name -> ordered list of (effect_id, params)
_PRESETS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "cinematic": [
        ("avfilter.eq", {"av.contrast": 1.15, "av.saturation": 0.85, "av.brightness": -0.02, "av.gamma": 1.0}),
        ("vignette", {"mode": 0, "radius": 0.6, "smooth": 0.4, "opacity": 35}),
    ],
    "clean": [
        ("avfilter.eq", {"av.contrast": 1.05, "av.saturation": 1.03, "av.brightness": 0.01, "av.gamma": 1.0}),
    ],
    "punchy": [
        ("avfilter.eq", {"av.contrast": 1.3, "av.saturation": 1.4, "av.brightness": 0.03}),
        ("vignette", {"mode": 0, "radius": 0.65, "smooth": 0.45, "opacity": 15}),
    ],
    "vintage": [
        ("sepia", {"u": 90, "v": 140}),
        ("grain", {"noise": 25, "contrast": 140, "brightness": 80}),
        ("oldfilm", {
            "delta": 10, "every": 15,
            "brightnessdelta_up": 15, "brightnessdelta_down": 20, "brightnessdelta_every": 60,
        }),
        ("vignette", {"mode": 0, "radius": 0.5, "smooth": 0.5, "opacity": 25}),
    ],
    "dreamy": [
        ("frei0r.softglow", {"blurblend": 1.0, "blur": 0.6, "brightness": 0.55, "sharpness": 0.3}),
        ("frei0r.IIRblur", {"Amount": 0.02, "Type": 0.5, "Edge": 1}),
        ("avfilter.eq", {"av.saturation": 0.9, "av.brightness": 0.05, "av.contrast": 0.95}),
    ],
    "dark": [
        ("avfilter.eq", {"av.brightness": -0.15, "av.contrast": 1.15, "av.saturation": 0.85, "av.gamma": 0.9}),
        ("vignette", {"mode": 0, "radius": 0.4, "smooth": 0.3, "opacity": 45}),
    ],
    "high_contrast": [
        ("avfilter.eq", {"av.contrast": 1.6, "av.saturation": 1.1, "av.gamma": 0.95}),
        ("frei0r.contrast0r", {"Contrast": 0.65}),
    ],
    "music_video": [
        ("avfilter.eq", {"av.saturation": 1.3, "av.contrast": 1.2}),
        ("frei0r.glow", {"Blur": 0.05}),
        ("vignette", {"mode": 0, "radius": 0.65, "smooth": 0.4, "opacity": 20}),
    ],
    "energetic": [
        ("avfilter.eq", {"av.contrast": 1.25, "av.saturation": 1.35, "av.brightness": 0.02}),
        ("avfilter.chromashift", {"av.crh": 3, "av.cbh": -3}),
        ("frei0r.glow", {"Blur": 0.03}),
    ],
    "minimal": [
        ("vignette", {"mode": 0, "radius": 0.75, "smooth": 0.5, "opacity": 10}),
    ],
}


def list_presets() -> list[str]:
    return sorted(_PRESETS)


def build_effect_stack(preset_name: str) -> list[EffectInstance]:
    """Return a plain, uncommitted list of `EffectInstance` for a named preset.

    The caller appends these to a clip's `effects` list. Effects are checked
    against the default effect catalog and silently skipped (with a warning
    logged) if unavailable -- unless the catalog itself is empty/unavailable,
    in which case every effect is still built (see module docstring).
    """
    spec = _PRESETS.get(preset_name)
    if spec is None:
        raise InvalidOperationError(
            f"Unknown effect preset '{preset_name}'",
            suggestion=f"Use one of: {list_presets()}",
        )

    catalog = get_default_catalog()
    catalog_known = bool(catalog.all())
    if not catalog_known:
        logger.warning(
            "Effect catalog is empty/unavailable; building preset '%s' without "
            "availability validation", preset_name,
        )

    instances: list[EffectInstance] = []
    index = 0
    for effect_id, params in spec:
        definition = catalog.get(effect_id)
        if catalog_known and definition is None:
            logger.warning(
                "Effect '%s' not found in catalog; skipping for preset '%s'",
                effect_id, preset_name,
            )
            continue

        tag = definition.tag if definition is not None else effect_id
        display_name = definition.name if definition is not None else _DISPLAY_NAMES.get(effect_id, effect_id)

        instances.append(EffectInstance(
            id=new_id("effect"),
            service=tag,
            display_name=display_name,
            params=dict(params),
            index=index,
        ))
        index += 1

    return instances
