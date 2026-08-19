"""Media asset model and the on-disk asset index (semantic media database).

The AI should be able to refer to imported media by a stable short id
("asset_a1b2c3") instead of re-scanning the filesystem or repeating full
paths on every tool call. MediaIndex is the persisted store backing that.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

AssetKind = Literal["video", "image", "audio", "unknown"]


@dataclass
class ProvenanceInfo:
    """Where an asset came from, required for anything not directly imported."""
    source: str  # e.g. "import", "stock:pexels", "sfx:freesound"
    url: str | None = None
    license: str | None = None
    downloaded_at: float | None = None
    attribution: str | None = None


@dataclass
class MediaAsset:
    id: str
    path: str
    kind: AssetKind
    duration: float = 0.0
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    orientation: str = "unknown"
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool = False
    has_video: bool = False
    audio_channels: int | None = None
    sample_rate: int | None = None
    size_bytes: int | None = None
    bit_rate: int | None = None
    thumbnail_path: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None
    imported_at: float = field(default_factory=time.time)
    provenance: ProvenanceInfo | None = None
    analysis: dict[str, Any] = field(default_factory=dict)  # scenes, beats, etc. once computed

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MediaAsset":
        prov = d.get("provenance")
        d = dict(d)
        d["provenance"] = ProvenanceInfo(**prov) if prov else None
        return MediaAsset(**d)


def make_asset_id(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()
    return f"asset_{digest[:10]}"


class MediaIndex:
    """JSON-backed semantic index of imported media, keyed by asset id.

    Pass `index_path=None` for an in-memory-only index (e.g. while parsing
    an existing Kdenlive project into the internal model, before the
    caller has decided whether/where to persist it) -- upsert() then just
    updates the in-memory dict and skips the disk write.
    """

    def __init__(self, index_path: Path | None):
        self.index_path = index_path
        self._assets: dict[str, MediaAsset] = {}
        self._load()

    def _load(self) -> None:
        if self.index_path is not None and self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text())
                self._assets = {k: MediaAsset.from_dict(v) for k, v in raw.items()}
            except (json.JSONDecodeError, TypeError, KeyError):
                self._assets = {}

    def _save(self) -> None:
        if self.index_path is None:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v.to_dict() for k, v in self._assets.items()}
        self.index_path.write_text(json.dumps(payload, indent=2))

    def upsert(self, asset: MediaAsset) -> MediaAsset:
        self._assets[asset.id] = asset
        self._save()
        return asset

    def get(self, asset_id: str) -> MediaAsset | None:
        return self._assets.get(asset_id)

    def find_by_path(self, path: Path) -> MediaAsset | None:
        resolved = str(path.resolve())
        for asset in self._assets.values():
            if asset.path == resolved:
                return asset
        return None

    def list(self) -> list[MediaAsset]:
        return list(self._assets.values())

    def search(self, query: str, kind: AssetKind | None = None) -> list[MediaAsset]:
        q = query.lower().strip()
        results = []
        for asset in self._assets.values():
            if kind and asset.kind != kind:
                continue
            haystack = " ".join([asset.path, asset.notes or "", *asset.tags]).lower()
            if not q or q in haystack:
                results.append(asset)
        return results

    def remove(self, asset_id: str) -> bool:
        if asset_id in self._assets:
            del self._assets[asset_id]
            self._save()
            return True
        return False

    def duplicates(self) -> list[list[MediaAsset]]:
        by_size_dur: dict[tuple, list[MediaAsset]] = {}
        for asset in self._assets.values():
            key = (asset.size_bytes, round(asset.duration, 2))
            by_size_dur.setdefault(key, []).append(asset)
        return [group for group in by_size_dur.values() if len(group) > 1]
