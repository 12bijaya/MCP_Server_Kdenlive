"""Catalog of real Kdenlive/MLT effect definitions, parsed from the effect
XML files Kdenlive itself ships (typically under
``<kdenlive-install>/share/kdenlive/effects/*.xml``).

This is how the rest of the system detects which effects are actually
available on the current machine and degrades gracefully when they are not
(e.g. running on a box without Kdenlive installed): callers ask the
`EffectCatalog` rather than hard-coding assumptions about what MLT/frei0r
plugins exist.
"""

from __future__ import annotations

import difflib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kdenlive_mcp.config import get_config
from kdenlive_mcp.errors import EffectUnavailableError

logger = logging.getLogger("kdenlive_mcp.kdenlive.effects")

# Best-effort keyword -> category mapping. Checked in order; first match wins.
# This is intentionally simple (substring match over name+description+tag) --
# Kdenlive's own XML doesn't carry a canonical "category" field, so this is a
# heuristic, not ground truth.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("blur", ("blur",)),
    ("keying", ("chroma key", "key ", "keyer", "bluescreen", "greenscreen", "mask", "alpha")),
    ("distort", ("distort", "wave", "wobble", "lens", "mirror", "fisheye", "glitch", "displace", "uvmap")),
    ("transform", ("transform", "position", "scale", "rotate", "rotation", "crop", "pan ")),
    ("stylize", ("glow", "charcoal", "emboss", "sketch", "cartoon", "old film", "grain",
                 "posterize", "pixelize", "mosaic", "sepia", "vignette", "noise")),
    ("color", ("color", "colour", "saturation", "hue", "contrast", "brightness", "gamma",
               "white balance", "lift", "gain", "curves", "equalizer", "tint", "lut")),
    ("generator", ("generator", "title", "text", "color clip", "noise generator")),
    ("audio", ("audio", "volume", "gain", "pitch", "pan ", "equaliz", "reverb", "compressor",
               "limiter", "noise gate", "vocoder")),
]


def _guess_category(name: str, description: str, tag: str) -> str:
    haystack = f"{name} {description} {tag}".lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return category
    return "other"


def _local(tag: str) -> str:
    """Strip an XML namespace off an element tag, e.g. '{ns}effect' -> 'effect'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _parse_parameter(param_elem: ET.Element) -> dict[str, Any]:
    entry: dict[str, Any] = dict(param_elem.attrib)
    name_child = _child(param_elem, "name")
    if name_child is not None and _text(name_child):
        entry["label"] = _text(name_child)
    display = _child(param_elem, "paramlistdisplay")
    if display is not None and _text(display):
        entry["options"] = [o.strip() for o in _text(display).split(",") if o.strip()]
    if "paramlist" in entry and isinstance(entry["paramlist"], str):
        entry["paramlist"] = [v for v in entry["paramlist"].split(";") if v]
    entry.setdefault("type", "unknown")
    return entry


@dataclass
class EffectDefinition:
    id: str
    tag: str  # MLT service name
    name: str
    description: str
    category: str
    parameters: list[dict[str, Any]] = field(default_factory=list)
    is_audio: bool = False
    source_file: str | None = None


def _parse_effect_element(effect_elem: ET.Element, source_file: str) -> EffectDefinition:
    tag = effect_elem.get("tag", "")
    eid = effect_elem.get("id") or tag
    effect_type = (effect_elem.get("type") or "").lower()

    name = _text(_child(effect_elem, "name")) or eid
    description = _text(_child(effect_elem, "description"))

    parameters = [
        _parse_parameter(child)
        for child in effect_elem
        if _local(child.tag) == "parameter"
    ]

    is_audio = effect_type == "audio" or "audio" in tag.lower()
    category = "audio" if is_audio else _guess_category(name, description, tag)

    return EffectDefinition(
        id=eid,
        tag=tag,
        name=name,
        description=description,
        category=category,
        parameters=parameters,
        is_audio=is_audio,
        source_file=source_file,
    )


class EffectCatalog:
    def __init__(self) -> None:
        self._by_id: dict[str, EffectDefinition] = {}

    @classmethod
    def load(cls, effects_dir: Path) -> "EffectCatalog":
        catalog = cls()
        effects_dir = Path(effects_dir)
        if not effects_dir.is_dir():
            logger.warning("Effects directory does not exist: %s", effects_dir)
            return catalog

        for xml_path in sorted(effects_dir.glob("*.xml")):
            try:
                root = ET.parse(xml_path).getroot()
            except ET.ParseError as exc:
                logger.warning("Skipping unparseable effect XML %s: %s", xml_path, exc)
                continue

            root_local = _local(root.tag)
            if root_local == "effect":
                effect_elems = [root]
            elif root_local == "group":
                effect_elems = [c for c in root if _local(c.tag) == "effect"]
            else:
                continue

            for elem in effect_elems:
                try:
                    definition = _parse_effect_element(elem, str(xml_path))
                except Exception as exc:  # pragma: no cover - defensive, keep loading resilient
                    logger.warning("Failed to parse effect in %s: %s", xml_path, exc)
                    continue
                if not definition.id:
                    continue
                catalog._by_id[definition.id] = definition

        logger.info("Loaded %d effect definitions from %s", len(catalog._by_id), effects_dir)
        return catalog

    def get(self, effect_id: str) -> EffectDefinition | None:
        return self._by_id.get(effect_id)

    def search(self, query: str) -> list[EffectDefinition]:
        q = (query or "").strip().lower()
        if not q:
            return []
        results = []
        for defn in self._by_id.values():
            haystack = f"{defn.id} {defn.tag} {defn.name} {defn.description}".lower()
            if q in haystack:
                results.append(defn)
        return results

    def all(self) -> list[EffectDefinition]:
        return list(self._by_id.values())

    def is_available(self, effect_id: str) -> bool:
        return effect_id in self._by_id


# --------------------------------------------------------------- discovery -

def _discover_effects_dir() -> Path | None:
    cfg = get_config()
    if cfg.kdenlive_effects_dir:
        return cfg.kdenlive_effects_dir

    candidates: list[Path] = []

    if cfg.kdenlive_bin:
        bin_path = Path(cfg.kdenlive_bin)
        # Snap installs: the `kdenlive` binary on PATH is a wrapper/symlink
        # into snapd's private mount tree (e.g. /var/lib/snapd/snap/bin/kdenlive
        # -> /usr/bin/snap), so there is no simple "../share" relative to it.
        # `/snap/<name>/current/...` is the stable, revision-independent path
        # snapd maintains for any installed snap.
        candidates.append(Path("/snap/kdenlive/current/usr/share/kdenlive/effects"))
        candidates.append(Path("/var/lib/snapd/snap/kdenlive/current/usr/share/kdenlive/effects"))
        try:
            resolved = bin_path.resolve()
            candidates.append(resolved.parent.parent / "share" / "kdenlive" / "effects")
        except OSError:
            pass

    candidates.extend([
        Path("/usr/share/kdenlive/effects"),
        Path("/usr/local/share/kdenlive/effects"),
        Path.home() / ".local/share/kdenlive/effects",
    ])

    # Fall back to scanning snap revision directories directly in case the
    # "current" symlink is missing/broken.
    snap_root = Path("/var/lib/snapd/snap/kdenlive")
    if snap_root.is_dir():
        candidates.extend(sorted(snap_root.glob("*/usr/share/kdenlive/effects")))

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


_default_catalog: EffectCatalog | None = None


def get_default_catalog() -> EffectCatalog:
    global _default_catalog
    if _default_catalog is None:
        effects_dir = _discover_effects_dir()
        if effects_dir is None:
            logger.warning(
                "Could not locate a Kdenlive effects directory; effect catalog will be empty. "
                "Set KDENLIVE_MCP_EFFECTS_DIR to override."
            )
            _default_catalog = EffectCatalog()
        else:
            _default_catalog = EffectCatalog.load(effects_dir)
    return _default_catalog


def reset_default_catalog() -> None:
    """Clear the cached singleton (mainly useful for tests)."""
    global _default_catalog
    _default_catalog = None


def validate_effect_available(effect_id: str) -> EffectDefinition:
    catalog = get_default_catalog()
    definition = catalog.get(effect_id)
    if definition is not None:
        return definition

    known_ids = [d.id for d in catalog.all()]
    close = difflib.get_close_matches(effect_id, known_ids, n=5, cutoff=0.5)
    if not close:
        # Fall back to substring search, which catches things like asking
        # for "saturation" when the real id is "frei0r.saturat0r".
        close = [d.id for d in catalog.search(effect_id)][:5]

    if close:
        suggestion = f"closest matches: {', '.join(close)}"
    elif not known_ids:
        suggestion = (
            "no effects are loaded at all -- is Kdenlive installed, or is "
            "KDENLIVE_MCP_EFFECTS_DIR set correctly?"
        )
    else:
        suggestion = f"no close match found among {len(known_ids)} known effects"
    raise EffectUnavailableError(
        f"Effect '{effect_id}' is not available in the Kdenlive effect catalog",
        suggestion=suggestion,
        details={"requested": effect_id, "catalog_size": len(known_ids)},
    )
