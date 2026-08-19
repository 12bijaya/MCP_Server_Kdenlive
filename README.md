# MCP Server: Kdenlive

A Python [MCP](https://modelcontextprotocol.io) server that lets Claude edit videos in [Kdenlive](https://kdenlive.org/) through natural-language instructions.

**124 tools, 7 resources, 130 automated tests** — all tested against a real Kdenlive install and real `melt` renders, not mocked.

## How it connects to Kdenlive

This does **not** remote-control a running Kdenlive window. There's no live connection to the app. (This was tested directly against Kdenlive's DBus interface, including the one method with no unmet preconditions — confirmed via Kdenlive's own source — and it still doesn't do anything when called externally on a stock install. Real live scripting exists as prior art but needs a custom-patched Kdenlive fork, which is its own separate project.)

Instead, the server directly reads and writes `.kdenlive` project files (Kdenlive's native XML project format) and renders through `melt`, Kdenlive's own real rendering engine — the same one Kdenlive itself uses. Claude calls MCP tools (`add_clip`, `create_camera_push`, `start_render`, ...), which edit an internal project model in memory; `save_project`/`save_project_as` writes that out as a real `.kdenlive` file. You then open that file in Kdenlive normally to view or tweak it by hand, or just render straight from the tools.

It also uses:
- `ffmpeg` / `ffprobe` — reads video/audio file info, makes thumbnails, extracts audio for beat detection.
- `melt` — actually renders finished video files, and can double-check a generated project loads correctly before you save.

Kdenlive itself doesn't need to be running at all while the server works. You only open Kdenlive when you want to look at a project or edit it by hand.

## Features

### Project management
Create/open/save/save-as/close/duplicate projects. Backup and restore. Multiple sequences per project (create/delete/list). Set resolution (720p/1080p/1440p/4K, landscape/vertical/square), frame rate, audio settings, and arbitrary metadata — never changed implicitly.

### Media
Import video/image/audio files or whole folders. Every asset gets ffprobe'd (duration, resolution, fps, codecs, audio channels) and thumbnailed automatically, then tracked in a media index so Claude can refer to clips by id instead of re-scanning the filesystem. Duplicate detection.

### Timeline editing
Add, remove, move, trim, split, duplicate, replace, slip, and slide clips. Ripple insert/delete. Reorder, group/ungroup, align, snap to markers or beats. Create/delete/mute/solo/lock tracks. Markers.

### Motion & keyframes
High-level camera moves — push, pull, pan, tilt, orbit, handheld, shake, zoom-punch, impact hits — built on real easing curves (linear, ease-in/out, cubic, bezier, bounce, elastic, overshoot), not linear-only mechanical motion. Also exposes the low-level primitives directly: `animate_position`/`scale`/`rotation`/`opacity`/`crop`, and **generic keyframing on any parameter of any effect**, not just motion.

### Effects
Full introspection of every effect actually installed in your Kdenlive (parsed live from its own effect definitions — nothing invented), plus 10 named presets (cinematic, punchy, vintage, dreamy, dark, high-contrast, music-video, energetic, minimal, clean). Apply, remove, enable/disable, and — critically — **get/set parameters on an already-applied effect**, not just at creation time.

### Transitions
Crossfade, zoom, whip, slide, push, blur, flash, glitch, distortion, directional wipes, dip-to-black/white, hard cut. Each backed by a real MLT/frei0r service where one exists; where Kdenlive has no dedicated service (whip, blur, glitch), built as a documented, honest approximation rather than a fake one.

### Audio & beat-sync
Waveform analysis (RMS envelope, peak, clipping), BPM/beat/downbeat detection, energy-section and structural segmentation, silence detection — all real signal analysis via librosa/ffmpeg. Beat-synced editing: cut clips on the beat, zoom/shake/flash on the beat, beat-synced montages, all with musically-varied intensity (not every beat identical). Sound-effect placement. Clip volume/gain and loudness normalization using Kdenlive's real audio effects.

### Subtitles
Add/remove/edit/move/split/merge subtitle entries, import/export `.srt`. Stored exactly the way Kdenlive itself stores them (a sibling `.srt` file referenced by a filter), so a project this server edits opens identically in Kdenlive's own subtitle editor.

### Rendering
Real rendering through `melt` — the actual engine, not a reimplementation. Runs as a background job (`start_render` returns immediately), with live progress polling, cancellation, and codec selection (h264/h265/vp9/prores, aac/mp3/opus). A job is never reported "completed" without `ffprobe` verifying the output is real, playable media.

### Safety, undo, and batch operations
Every mutating tool automatically checkpoints before it runs; `undo_operation`/`redo_operation` walk that history. Named snapshots for longer-lived checkpoints. `execute_batch` runs a list of tool calls atomically — any failure rolls back every operation that already succeeded, leaving the project exactly as it started. All filesystem paths are validated; the server never overwrites source media or renders over an imported asset.

### Generic property interface & capability detection
`get_property`/`set_property`/`list_properties` work uniformly across projects, sequences, tracks, clips, and effects — a forward-compatible escape hatch alongside the typed tools. `get_kdenlive_capabilities` reports exactly what's installed (versions, available effects/transitions) rather than assuming.

### MCP Resources
Read-only, context-efficient project state exposed as resources rather than tool calls: `kdenlive://project`, `timeline`, `tracks`, `media`, `effects`, `transitions`, `capabilities`.

### Not implemented
Media bin folders/organization, proxy media, nested sequences, reference-video style analysis, automatic stock-footage/music sourcing (would need provider API keys), a fully autonomous one-shot "make me a video" tool, and anything requiring live GUI state (current tool, selected items, playhead position) — Kdenlive has no working external control surface for that on a stock install.

## Installation

**Requirements:** Python 3.10+, `ffmpeg`/`ffprobe`, and Kdenlive (recommended, not strictly required — see below).

### 1. Get the code

```bash
git clone https://github.com/12bijaya/MCP_Server_Kdenlive.git
cd MCP_Server_Kdenlive
```

### 2. Create a virtual environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. Check it's working

```bash
pytest
```

You should see all tests pass. If `ffmpeg`/`ffprobe` aren't installed, install them first (`sudo apt install ffmpeg` on Ubuntu, `brew install ffmpeg` on macOS).

### 4. Find the path to the installed server

```bash
which kdenlive-mcp
```

Copy that full path — you'll need it in the next step.

### 5. Connect it to Claude

**Claude Desktop:** open its config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

**Claude Code:** run `claude mcp add -s user kdenlive /full/path/to/kdenlive-mcp`, or open (or create) `.mcp.json` in your project folder.

Either way, for a manual config file add this (using the path from step 4):

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "/full/path/to/MCP_Server_Kdenlive/.venv/bin/kdenlive-mcp"
    }
  }
}
```

Restart Claude Desktop (or start a new Claude Code session) after saving. Kdenlive tools should now show up as available.

### 6. (Optional) Point it at Kdenlive/melt manually

The server auto-detects a snap-installed Kdenlive on Linux. If yours is installed a different way and isn't found automatically, set these environment variables before launching Claude, or add them to the `mcpServers` entry above under an `"env"` key:

| Variable | What it's for |
|---|---|
| `KDENLIVE_MCP_KDENLIVE` | Path to the `kdenlive` binary |
| `KDENLIVE_MCP_MELT` | Path to the `melt` binary |
| `KDENLIVE_MCP_EFFECTS_DIR` | Path to Kdenlive's `effects` folder |

Without Kdenlive/melt installed at all, the server still works for building/saving `.kdenlive` files — you just lose the effects/transitions catalog, real-engine validation, and rendering.

## Try it

Ask Claude something like:

> Create a new 1080p project, import `/home/me/footage/clip1.mp4`, put it on the timeline, add a slow zoom-in over the first 2 seconds, then render it to `~/Videos/output.mp4`.

Or open the saved `.kdenlive` project in Kdenlive directly to see/tweak the result.

## Project layout

```
src/kdenlive_mcp/
  core/           # internal model: timeline, keyframes, effects, transitions, audio, subtitles, assets
  kdenlive/       # reads/writes .kdenlive files; effect & transition catalogs; capability detection
  media/          # ffmpeg/ffprobe wrappers, thumbnail generation, real rendering via melt
  storage/        # workspace path safety, caching, snapshots
  validation/     # project validation (structural + real-melt load check)
  mcp_tools/      # all 124 MCP tools (by category) + 7 resources + session/undo state
  server.py       # entrypoint
tests/
```
