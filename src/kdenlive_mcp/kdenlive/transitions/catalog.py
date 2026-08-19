"""Catalog of real Kdenlive/MLT transition definitions, parsed from the
transition XML files Kdenlive ships (typically under
``<kdenlive-install>/share/kdenlive/transitions/*.xml``).

Mirrors `kdenlive_mcp.kdenlive.effects.catalog` -- same load/get/search/all
shape -- so callers building transitions can detect what's really available
and degrade gracefully instead of assuming an MLT service exists.
"""

from __future__ import annotations

import difflib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kdenlive_mcp.config import get_config
from kdenlive_mcp.errors import TransitionUnavailableError

logger = logging.getLogger("kdenlive_mcp.kdenlive.transitions")

_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("dissolve", ("dissolve", "fade", "mix", "cross")),
    ("wipe", ("wipe", "barn door", "circle", "rect")),
    ("slide", ("slide", "push")),
    ("blend", ("blend", "composite", "compositing", "overlay", "screen", "multiply", "darken", "lighten")),
    ("distort", ("distort", "uv map", "affine", "transform")),
    ("audio", ("audio", "mix")),
]


def _guess_category(name: str, description: str, tag: str) -> str:
    haystack = f"{name} {description} {tag}".lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return category
    return "other"


def _local(tag: str) -> str:
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
class TransitionDefinition:
    id: str
    tag: str  # MLT service name
    name: str
    description: str
    category: str
    parameters: list[dict[str, Any]] = field(default_factory=list)
    is_audio: bool = False
    source_file: str | None = None


def _parse_transition_element(elem: ET.Element, source_file: str) -> TransitionDefinition:
    tag = elem.get("tag", "")
    tid = elem.get("id") or tag
    ttype = (elem.get("type") or "").lower()

    name = _text(_child(elem, "name")) or tid
    description = _text(_child(elem, "description"))

    parameters = [
        _parse_parameter(child)
        for child in elem
        if _local(child.tag) == "parameter"
    ]

    is_audio = "audio" in ttype or "audio" in tag.lower()
    category = "audio" if is_audio else _guess_category(name, description, tag)

    return TransitionDefinition(
        id=tid,
        tag=tag,
        name=name,
        description=description,
        category=category,
        parameters=parameters,
        is_audio=is_audio,
        source_file=source_file,
    )


class TransitionCatalog:
    def __init__(self) -> None:
        self._by_id: dict[str, TransitionDefinition] = {}

    @classmethod
    def load(cls, transitions_dir: Path) -> "TransitionCatalog":
        catalog = cls()
        transitions_dir = Path(transitions_dir)
        if not transitions_dir.is_dir():
            logger.warning("Transitions directory does not exist: %s", transitions_dir)
            return catalog

        for xml_path in sorted(transitions_dir.glob("*.xml")):
            try:
                root = ET.parse(xml_path).getroot()
            except ET.ParseError as exc:
                logger.warning("Skipping unparseable transition XML %s: %s", xml_path, exc)
                continue

            root_local = _local(root.tag)
            if root_local == "transition":
                trans_elems = [root]
            elif root_local == "group":
                trans_elems = [c for c in root if _local(c.tag) == "transition"]
            else:
                continue

            for elem in trans_elems:
                try:
                    definition = _parse_transition_element(elem, str(xml_path))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to parse transition in %s: %s", xml_path, exc)
                    continue
                if not definition.id:
                    continue
                catalog._by_id[definition.id] = definition

        logger.info("Loaded %d transition definitions from %s", len(catalog._by_id), transitions_dir)
        return catalog

    def get(self, transition_id: str) -> TransitionDefinition | None:
        return self._by_id.get(transition_id)

    def search(self, query: str) -> list[TransitionDefinition]:
        q = (query or "").strip().lower()
        if not q:
            return []
        results = []
        for defn in self._by_id.values():
            haystack = f"{defn.id} {defn.tag} {defn.name} {defn.description}".lower()
            if q in haystack:
                results.append(defn)
        return results

    def all(self) -> list[TransitionDefinition]:
        return list(self._by_id.values())

    def is_available(self, transition_id: str) -> bool:
        return transition_id in self._by_id


# --------------------------------------------------------------- discovery -

def _discover_transitions_dir() -> Path | None:
    cfg = get_config()

    candidates: list[Path] = []

    if cfg.kdenlive_bin:
        bin_path = Path(cfg.kdenlive_bin)
        candidates.append(Path("/snap/kdenlive/current/usr/share/kdenlive/transitions"))
        candidates.append(Path("/var/lib/snapd/snap/kdenlive/current/usr/share/kdenlive/transitions"))
        try:
            resolved = bin_path.resolve()
            candidates.append(resolved.parent.parent / "share" / "kdenlive" / "transitions")
        except OSError:
            pass

    candidates.extend([
        Path("/usr/share/kdenlive/transitions"),
        Path("/usr/local/share/kdenlive/transitions"),
        Path.home() / ".local/share/kdenlive/transitions",
    ])

    snap_root = Path("/var/lib/snapd/snap/kdenlive")
    if snap_root.is_dir():
        candidates.extend(sorted(snap_root.glob("*/usr/share/kdenlive/transitions")))

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


_default_catalog: TransitionCatalog | None = None


def get_default_transition_catalog() -> TransitionCatalog:
    global _default_catalog
    if _default_catalog is None:
        transitions_dir = _discover_transitions_dir()
        if transitions_dir is None:
            logger.warning(
                "Could not locate a Kdenlive transitions directory; transition catalog will be empty."
            )
            _default_catalog = TransitionCatalog()
        else:
            _default_catalog = TransitionCatalog.load(transitions_dir)
    return _default_catalog


def reset_default_transition_catalog() -> None:
    """Clear the cached singleton (mainly useful for tests)."""
    global _default_catalog
    _default_catalog = None


def validate_transition_available(transition_id: str) -> TransitionDefinition:
    catalog = get_default_transition_catalog()
    definition = catalog.get(transition_id)
    if definition is not None:
        return definition

    known_ids = [d.id for d in catalog.all()]
    close = difflib.get_close_matches(transition_id, known_ids, n=5, cutoff=0.5)
    if not close:
        close = [d.id for d in catalog.search(transition_id)][:5]

    if close:
        suggestion = f"closest matches: {', '.join(close)}"
    elif not known_ids:
        suggestion = (
            "no transitions are loaded at all -- is Kdenlive installed, or is "
            "the transitions directory discoverable?"
        )
    else:
        suggestion = f"no close match found among {len(known_ids)} known transitions"
    raise TransitionUnavailableError(
        f"Transition '{transition_id}' is not available in the Kdenlive transition catalog",
        suggestion=suggestion,
        details={"requested": transition_id, "catalog_size": len(known_ids)},
    )
