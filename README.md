# MCP Server: Kdenlive

A Python [MCP](https://modelcontextprotocol.io) server that lets an AI agent perform professional video editing in [Kdenlive](https://kdenlive.org/) through natural-language instructions.

This is not a thin wrapper around a handful of Kdenlive commands. It's a layered system:

```
AI Agent (Claude)
  -> MCP tools (project/media/timeline/motion/effects/transitions/audio/...)
  -> Internal timeline model (Project / Sequence / Track / Clip / Keyframe)
  -> Kdenlive adapter (translates the model to/from real .kdenlive XML)
  -> .kdenlive project file
  -> Kdenlive / FFmpeg / MLT (melt) rendering
```

The AI never edits `.kdenlive` XML directly. It calls typed MCP tools that mutate a clean, testable internal timeline model; a dedicated adapter translates that model to a real Kdenlive project on save, and back again when opening one.

## Status

Phases 1-3 of the build (project management, media ingestion, timeline editing, the Kdenlive adapter, keyframes/motion/effects/transitions, and the audio/beat-sync engine) are implemented, tested, and wired into 98 MCP tools. Reference-video analysis, automatic asset sourcing, and the fully autonomous `create_professional_edit` workflow (spec phases 4-7) are not built yet.

## Why this is grounded in the real file format

The Kdenlive project XML structure used by the adapter (`src/kdenlive_mcp/kdenlive/adapter/`) was reverse-engineered from real `.kdenlive` projects and cross-checked against a locally installed Kdenlive 26.04.3 + MLT 7.38 (`melt`), rather than guessed from documentation. The effects and transitions catalogs are parsed directly from Kdenlive's own shipped effect/transition XML definitions, so the AI can never reference an effect that doesn't actually exist in the installed Kdenlive. Projects built entirely through the MCP tool layer have been round-tripped through the real `melt` renderer to confirm they actually load.

## Project layout

```
src/kdenlive_mcp/
  core/           # internal model: timeline, keyframes, effects, transitions, audio, assets
  kdenlive/       # the adapter: XML read/write, effect & transition catalogs, capability detection
  media/          # ffmpeg/ffprobe subprocess wrappers, thumbnail generation
  storage/        # workspace path safety, caching, snapshots
  validation/     # project validation (structural + optional real-melt check)
  mcp_tools/      # the 98 MCP-facing tools, grouped by category, plus session state
  server.py       # FastMCP entrypoint
tests/
  unit/           # per-module tests, including against the real installed Kdenlive catalog
  integration/    # end-to-end: drives the tool layer like an agent would, against real media
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Requires `ffmpeg`/`ffprobe` on PATH. Kdenlive/`melt` are optional but unlock the effects/transitions catalogs, capability detection, and real-engine project validation. If Kdenlive is installed as a snap, the server auto-discovers its bundled `melt` binary and effects directory; set `KDENLIVE_MCP_MELT` / `KDENLIVE_MCP_EFFECTS_DIR` to override.

All server state (workspace projects, thumbnail/analysis cache, snapshots) lives under `~/.kdenlive-mcp/` by default; override with `KDENLIVE_MCP_WORKSPACE`, `KDENLIVE_MCP_CACHE`, `KDENLIVE_MCP_SNAPSHOTS`.

## Connecting to Claude

Add to your MCP client config (e.g. Claude Desktop's `claude_desktop_config.json`, or Claude Code's `.mcp.json`):

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "/path/to/.venv/bin/kdenlive-mcp"
    }
  }
}
```

Or, without installing the console script:

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "kdenlive_mcp.server"]
    }
  }
}
```

## Example: a minimal edit, end to end

What an agent's tool calls look like for "put this clip on the timeline with a slow zoom-in":

```
create_project(name="My Edit", resolution="1080p", fps="30")
import_video(path="/home/me/footage/clip1.mp4")
add_clip(track_id="<video track id>", position=0.0, source_in=0.0, source_out=5.0,
         asset_id="<asset id from import_video>", clip_type="video")
create_camera_push(clip_id="<clip id>", start_seconds=0.0, end_seconds=2.0, end_scale=1.15)
validate_project()
save_project_as(new_path="my_edit.kdenlive")
```

`get_project_info()` after `create_project`/`open_project` returns every track/clip/sequence id needed for the calls above. Every tool that would fail (unknown effect, overlapping clips, missing media) returns a structured `{"success": false, "error": {"code", "message", "suggestion"}}` instead of raising, so an agent can react to it.

## Tool categories (98 tools)

Project management &middot; media ingestion &middot; timeline editing (add/trim/split/ripple/group/...) &middot; motion (camera push/pull/pan/tilt/orbit/shake/zoom-punch/impact, full keyframe/easing primitives) &middot; effects (catalog-backed, plus 10 named presets) &middot; transitions (crossfade, zoom, whip, slide, push, glitch, directional wipes, ...) &middot; audio analysis and beat-sync editing &middot; SFX placement &middot; snapshots and undo/redo &middot; capability detection.

Run `get_kdenlive_capabilities()` before relying on any specific effect or transition — it reports what's actually installed rather than assuming.

## Development notes

- Never hand-edit `.kdenlive` XML outside `kdenlive/adapter/`; every other layer works through the internal model in `core/timeline/model.py`.
- The Kdenlive snap's `melt` needs `LD_LIBRARY_PATH`/`MLT_REPOSITORY`/`MLT_DATA` pointed at the snap's private lib tree to run at all outside `snap run`; `config.py` handles this automatically. Its sandbox can only see `$HOME`-rooted paths, not `/tmp` — validation scratch files are written under the workspace dir for this reason.
- Mutating tools are decorated with `@mutates` (see `mcp_tools/tools/_common.py`), which checkpoints the project before the edit runs; `undo_operation`/`redo_operation` walk that stack.
