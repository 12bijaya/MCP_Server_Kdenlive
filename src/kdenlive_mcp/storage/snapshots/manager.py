from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from kdenlive_mcp.config import get_config
from kdenlive_mcp.core.assets.model import MediaAsset, MediaIndex
from kdenlive_mcp.core.timeline.model import Project, new_id
from kdenlive_mcp.core.timeline.serialize import project_from_dict, project_to_dict
from kdenlive_mcp.errors import InvalidOperationError


@dataclass
class SnapshotInfo:
    id: str
    project_id: str
    label: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class SnapshotManager:
    def _dir(self, project_id: str):
        d = get_config().snapshots_dir / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load(self, project_id: str, snapshot_id: str) -> dict:
        path = self._dir(project_id) / f"{snapshot_id}.json"
        if not path.exists():
            raise InvalidOperationError(f"Snapshot '{snapshot_id}' not found for project '{project_id}'")
        return json.loads(path.read_text())

    def create_snapshot(self, project: Project, media_index: MediaIndex, label: str = "") -> SnapshotInfo:
        info = SnapshotInfo(new_id("snap"), project.id, label, datetime.now(timezone.utc).isoformat())
        payload = {
            "info": info.to_dict(),
            "project": project_to_dict(project),
            "media": [a.to_dict() for a in media_index.list()],
        }
        (self._dir(project.id) / f"{info.id}.json").write_text(json.dumps(payload))
        return info

    def list_snapshots(self, project_id: str) -> list[SnapshotInfo]:
        infos = [SnapshotInfo(**json.loads(p.read_text())["info"]) for p in self._dir(project_id).glob("*.json")]
        return sorted(infos, key=lambda i: i.created_at)

    def restore_snapshot(self, project_id: str, snapshot_id: str) -> tuple[Project, MediaIndex]:
        payload = self._load(project_id, snapshot_id)
        index = MediaIndex(index_path=None)
        for a in payload["media"]:
            index.upsert(MediaAsset.from_dict(a))
        return project_from_dict(payload["project"]), index

    def compare_snapshots(self, project_id: str, snapshot_id_a: str, snapshot_id_b: str) -> dict:
        a = self._load(project_id, snapshot_id_a)["project"]
        b = self._load(project_id, snapshot_id_b)["project"]

        def clips(p: dict) -> dict:
            return {c["id"]: c for s in p["sequences"] for t in s["tracks"] for c in t["clips"]}

        ca, cb = clips(a), clips(b)
        return {
            "clips_added": sorted(cb.keys() - ca.keys()),
            "clips_removed": sorted(ca.keys() - cb.keys()),
            "clips_changed": sorted(k for k in ca.keys() & cb.keys() if ca[k] != cb[k]),
            "settings_changed": a["settings"] != b["settings"],
        }
