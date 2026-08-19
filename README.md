# MCP Server: Kdenlive

A Python [MCP](https://modelcontextprotocol.io) server that lets an AI agent perform professional video editing in [Kdenlive](https://kdenlive.org/) through natural-language instructions.

This is not a thin wrapper around a handful of Kdenlive commands. It's a layered system:

```
AI Agent (Claude)
  -> MCP tools (project/media/timeline/motion/effects/audio/...)
  -> Video analysis layer (ffprobe, librosa)
  -> Internal timeline model (Project / Sequence / Track / Clip / Keyframe)
  -> Kdenlive adapter (translates the model to/from real .kdenlive XML)
  -> .kdenlive project file
  -> Kdenlive / FFmpeg / MLT (melt) rendering
```

The AI never edits `.kdenlive` XML directly. It calls typed MCP tools that mutate a clean, testable internal timeline model; a dedicated adapter translates that model to a real Kdenlive project on save.

## Status

Under active development, built in phases (see `PHASES` below). The core timeline model, the Kdenlive XML adapter, the effects/transitions catalog, and the audio/beat-sync engine are implemented and tested against a real Kdenlive 26.04.3 + MLT install. The MCP tool-facing layer that exposes all of this to an AI agent is being wired up next.

## Why this is grounded in the real file format

The Kdenlive project XML structure used by the adapter (`src/kdenlive_mcp/kdenlive/adapter/`) was reverse-engineered from real `.kdenlive` projects and cross-checked against a locally installed Kdenlive 26.04.3 + MLT 7.38 (`melt`), rather than guessed from documentation. The effects and transitions catalogs are parsed directly from Kdenlive's own shipped effect/transition XML definitions, so the AI can never reference an effect that doesn't actually exist in the installed Kdenlive.

## Project layout

```
src/kdenlive_mcp/
  core/           # internal model: timeline, keyframes, effects, transitions, audio, assets
  kdenlive/       # the adapter: XML read/write, effect & transition catalogs, capability detection
  media/          # ffmpeg/ffprobe subprocess wrappers, thumbnail generation
  storage/        # workspace path safety, caching, snapshots
  validation/     # project validation
  mcp_tools/      # MCP-facing tool definitions (in progress)
tests/
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Requires `ffmpeg`/`ffprobe` on PATH. Kdenlive/`melt` are optional but unlock the effects/transitions catalogs and structural project validation against the real installation.
