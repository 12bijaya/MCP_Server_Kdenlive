"""Unit tests for the Kdenlive transition catalog and transition builders.

Runs against the real Kdenlive transition XML shipped on this machine, same
as test_effects_catalog.py.
"""

from __future__ import annotations

import glob

import pytest

from kdenlive_mcp.core.timeline.model import Easing, TransitionInstance
from kdenlive_mcp.core.transitions import model as tm
from kdenlive_mcp.errors import InvalidOperationError, TransitionUnavailableError
from kdenlive_mcp.kdenlive.transitions.catalog import (
    TransitionCatalog,
    get_default_transition_catalog,
    validate_transition_available,
)

REAL_TRANSITIONS_DIRS = glob.glob("/var/lib/snapd/snap/kdenlive/*/usr/share/kdenlive/transitions")

pytestmark = pytest.mark.skipif(
    not REAL_TRANSITIONS_DIRS,
    reason="Real Kdenlive transitions directory not found on this machine",
)


@pytest.fixture(scope="module")
def real_catalog() -> TransitionCatalog:
    return TransitionCatalog.load(REAL_TRANSITIONS_DIRS[0])


# --------------------------------------------------------------- catalog --

def test_loads_nontrivial_number_of_transitions(real_catalog: TransitionCatalog):
    assert len(real_catalog.all()) > 20


def test_dissolve_maps_to_luma_service(real_catalog: TransitionCatalog):
    defn = real_catalog.get("dissolve")
    assert defn is not None
    assert defn.tag == "luma"


def test_qtblend_transition_present(real_catalog: TransitionCatalog):
    defn = real_catalog.get("qtblend")
    assert defn is not None
    assert defn.tag == "qtblend"
    param_names = {p["name"] for p in defn.parameters}
    assert "rect" in param_names


def test_dedicated_slide_and_push_services_exist(real_catalog: TransitionCatalog):
    for direction in ("left", "right", "up", "down"):
        assert real_catalog.is_available(f"frei0r.sleid0r_slide-{direction}")
        assert real_catalog.is_available(f"frei0r.sleid0r_push-{direction}")


def test_uvmap_distortion_service_exists(real_catalog: TransitionCatalog):
    defn = real_catalog.get("frei0r.uvmap")
    assert defn is not None
    assert "distort" in defn.description.lower()


def test_audio_mix_transition_flagged_audio(real_catalog: TransitionCatalog):
    defn = real_catalog.get("mix")
    assert defn is not None
    assert defn.is_audio is True


def test_search_and_is_available(real_catalog: TransitionCatalog):
    results = real_catalog.search("wipe")
    assert len(results) > 0
    assert real_catalog.is_available("luma") is True
    assert real_catalog.is_available("not_a_real_transition") is False


def test_load_missing_directory_returns_empty_catalog(tmp_path):
    catalog = TransitionCatalog.load(tmp_path / "does-not-exist")
    assert catalog.all() == []


# ------------------------------------------------------- default catalog --

def test_get_default_transition_catalog_discovers_real_transitions():
    catalog = get_default_transition_catalog()
    assert len(catalog.all()) > 20
    assert catalog.is_available("dissolve")


def test_validate_transition_available_raises_with_suggestion():
    with pytest.raises(TransitionUnavailableError) as exc_info:
        validate_transition_available("dissolv")  # typo
    err = exc_info.value
    assert err.code == "TRANSITION_UNAVAILABLE"
    assert err.suggestion is not None


# ------------------------------------------------------------- builders --

def test_hard_cut_returns_none():
    assert tm.hard_cut() is None


def test_crossfade_uses_luma_service():
    t = tm.crossfade(position=10, duration=20)
    assert isinstance(t, TransitionInstance)
    assert t.service == "luma"
    assert t.position == 10
    assert t.duration == 20


def test_crossfade_respects_easing():
    t = tm.crossfade(position=0, duration=10, easing=Easing.EASE_IN_OUT)
    assert t.easing == Easing.EASE_IN_OUT


def test_dip_to_black_returns_two_transitions_through_a_gap():
    parts = tm.dip_to_black(position=100, duration=40, hold_frames=5)
    assert len(parts) == 2
    first, second = parts
    assert first.service == "luma"
    assert second.service == "luma"
    assert first.position == 100
    # second starts after first's half-duration plus the hold gap
    assert second.position == 100 + first.duration + 5
    assert first.params["dip_color_hint"] == "#000000"


def test_dip_to_white_hints_white():
    parts = tm.dip_to_white(position=0, duration=20)
    assert all(p.params["dip_color_hint"] == "#ffffff" for p in parts)


def test_zoom_transition_uses_qtblend_animated_rect():
    t = tm.zoom_transition(position=0, duration=30, direction="in")
    assert t.service == "qtblend"
    assert "rect" in t.params
    assert "0=" in t.params["rect"] and "30=" in t.params["rect"]


def test_zoom_transition_rejects_bad_direction():
    with pytest.raises(InvalidOperationError):
        tm.zoom_transition(position=0, duration=10, direction="sideways")


def test_whip_transition_builds_directional_rect():
    t = tm.whip_transition(position=0, duration=8, direction="right")
    assert t.service == "qtblend"
    assert "rect" in t.params


def test_slide_transition_uses_dedicated_service_per_direction():
    for direction in ("left", "right", "up", "down"):
        t = tm.slide_transition(position=0, duration=10, direction=direction)
        assert t.service == f"frei0r.sleid0r_slide-{direction}"


def test_push_transition_uses_dedicated_service_per_direction():
    for direction in ("left", "right", "up", "down"):
        t = tm.push_transition(position=0, duration=10, direction=direction)
        assert t.service == f"frei0r.sleid0r_push-{direction}"


def test_blur_transition_returns_qtblend_placeholder():
    t = tm.blur_transition(position=0, duration=10)
    assert t.service == "qtblend"


def test_flash_transition_uses_additive_compositing_mode():
    t = tm.flash_transition(position=0, duration=5)
    assert t.service == "qtblend"
    assert t.params["compositing"] == "12"


def test_glitch_transition_produces_jittered_keyframes():
    t = tm.glitch_transition(position=0, duration=8, seed=1)
    assert t.service == "qtblend"
    assert t.params["rect"].count(";") >= 1


def test_distortion_transition_uses_uvmap():
    t = tm.distortion_transition(position=0, duration=10)
    assert t.service == "frei0r.uvmap"


def test_directional_transition_dispatches_to_wipe_services():
    for direction in ("left", "right", "up", "down", "circle", "rect", "barn-door-h", "barn-door-v"):
        t = tm.directional_transition(direction, position=0, duration=10)
        assert t.service == f"frei0r.sleid0r_wipe-{direction}"


def test_directional_transition_rejects_bad_direction():
    with pytest.raises(InvalidOperationError):
        tm.directional_transition("diagonal", position=0, duration=10)


def test_custom_transition_validates_and_uses_real_tag():
    t = tm.custom_transition("wipe", position=0, duration=10, params={"softness": 20})
    assert t.service == "composite"  # wipe's real MLT tag
    assert t.params == {"softness": 20}


def test_custom_transition_raises_for_unknown_service():
    with pytest.raises(TransitionUnavailableError):
        tm.custom_transition("definitely_not_real", position=0, duration=10, params={})
