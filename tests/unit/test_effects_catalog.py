"""Unit tests for the Kdenlive effect catalog and effect-domain helpers.

These run against the *real* Kdenlive effect XML shipped on this machine
(the snap install at /var/lib/snapd/snap/kdenlive/*/usr/share/kdenlive/effects),
not a synthetic fixture -- the whole point is to prove the parser survives
contact with real, messy, upstream XML (bare <effect> roots, <group>-wrapped
multi-version effects, missing `id` attributes, etc).
"""

from __future__ import annotations

import glob

import pytest

from kdenlive_mcp.core.effects.model import create_effect
from kdenlive_mcp.core.effects.presets import build_effect_stack, list_presets
from kdenlive_mcp.errors import EffectUnavailableError, InvalidOperationError
from kdenlive_mcp.kdenlive.effects.catalog import (
    EffectCatalog,
    get_default_catalog,
    validate_effect_available,
)

REAL_EFFECTS_DIRS = glob.glob("/var/lib/snapd/snap/kdenlive/*/usr/share/kdenlive/effects")

pytestmark = pytest.mark.skipif(
    not REAL_EFFECTS_DIRS,
    reason="Real Kdenlive effects directory not found on this machine",
)


@pytest.fixture(scope="module")
def real_catalog() -> EffectCatalog:
    return EffectCatalog.load(REAL_EFFECTS_DIRS[0])


# --------------------------------------------------------------- catalog --

def test_loads_nontrivial_number_of_effects(real_catalog: EffectCatalog):
    assert len(real_catalog.all()) > 50


def test_qtblend_present_with_expected_params(real_catalog: EffectCatalog):
    defn = real_catalog.get("qtblend")
    assert defn is not None
    assert defn.tag == "qtblend"
    assert defn.name == "Transform"
    param_names = {p["name"] for p in defn.parameters}
    assert "rect" in param_names
    assert "rotation" in param_names


def test_vignette_present_with_expected_params(real_catalog: EffectCatalog):
    defn = real_catalog.get("vignette")
    assert defn is not None
    assert defn.tag == "vignette"
    param_names = {p["name"] for p in defn.parameters}
    assert {"radius", "smooth", "opacity"} <= param_names


def test_effect_missing_id_falls_back_to_tag(real_catalog: EffectCatalog):
    # avfilter_eq.xml ships an <effect tag="avfilter.eq"> with no id="" attr.
    defn = real_catalog.get("avfilter.eq")
    assert defn is not None
    assert defn.tag == "avfilter.eq"


def test_group_wrapped_effects_are_parsed(real_catalog: EffectCatalog):
    # qtblend.xml wraps two <effect id="qtblend"> versions in a <group>; the
    # second (version="2") should win and include the "rect" param.
    defn = real_catalog.get("qtblend")
    assert defn is not None
    param_names = {p["name"] for p in defn.parameters}
    assert "rect" in param_names


def test_audio_effects_are_flagged(real_catalog: EffectCatalog):
    volume = real_catalog.get("volume")
    assert volume is not None
    assert volume.is_audio is True
    assert volume.category == "audio"


def test_video_effects_are_not_flagged_audio(real_catalog: EffectCatalog):
    vignette = real_catalog.get("vignette")
    assert vignette is not None
    assert vignette.is_audio is False


def test_search_finds_by_substring(real_catalog: EffectCatalog):
    results = real_catalog.search("blur")
    assert len(results) > 0
    assert all("blur" in f"{d.id} {d.tag} {d.name} {d.description}".lower() for d in results)


def test_is_available(real_catalog: EffectCatalog):
    assert real_catalog.is_available("vignette") is True
    assert real_catalog.is_available("this_effect_does_not_exist") is False


def test_load_missing_directory_returns_empty_catalog(tmp_path):
    catalog = EffectCatalog.load(tmp_path / "does-not-exist")
    assert catalog.all() == []
    assert catalog.is_available("vignette") is False


# ------------------------------------------------------- default catalog --

def test_get_default_catalog_discovers_real_effects():
    catalog = get_default_catalog()
    assert len(catalog.all()) > 50
    assert catalog.is_available("qtblend")


def test_validate_effect_available_returns_definition():
    defn = validate_effect_available("vignette")
    assert defn.id == "vignette"


def test_validate_effect_available_raises_with_suggestion():
    with pytest.raises(EffectUnavailableError) as exc_info:
        validate_effect_available("vigentte")  # typo
    err = exc_info.value
    assert err.code == "EFFECT_UNAVAILABLE"
    assert err.suggestion is not None
    assert "vignette" in err.suggestion


# ------------------------------------------------------------- create_effect

def test_create_effect_applies_catalog_defaults():
    effect = create_effect("vignette")
    assert effect.service == "vignette"
    assert effect.params["radius"] == pytest.approx(0.5)
    assert effect.params["smooth"] == pytest.approx(0.8)


def test_create_effect_overrides_defaults():
    effect = create_effect("vignette", params={"opacity": 42})
    assert effect.params["opacity"] == 42
    # untouched defaults remain
    assert effect.params["radius"] == pytest.approx(0.5)


def test_create_effect_raises_for_unknown_effect():
    with pytest.raises(EffectUnavailableError):
        create_effect("totally_not_a_real_effect")


# ------------------------------------------------------------------ presets

def test_list_presets_returns_all_ten():
    presets = list_presets()
    assert set(presets) == {
        "cinematic", "clean", "punchy", "vintage", "dreamy",
        "dark", "high_contrast", "music_video", "energetic", "minimal",
    }


@pytest.mark.parametrize("preset_name", list_presets())
def test_build_effect_stack_for_every_preset(preset_name):
    stack = build_effect_stack(preset_name)
    assert len(stack) >= 1
    for effect in stack:
        assert effect.service  # non-empty real MLT service name
        assert effect.display_name
        assert isinstance(effect.params, dict)
        assert len(effect.params) > 0  # presets set real param values, not just defaults


def test_build_effect_stack_unknown_preset_raises():
    with pytest.raises(InvalidOperationError):
        build_effect_stack("not_a_real_preset")


def test_preset_effect_ids_are_all_real_catalog_effects():
    catalog = get_default_catalog()
    for preset_name in list_presets():
        stack = build_effect_stack(preset_name)
        for effect in stack:
            assert catalog.is_available(effect.service), (
                f"preset '{preset_name}' uses service '{effect.service}' "
                "which is not a real effect in the Kdenlive catalog"
            )
