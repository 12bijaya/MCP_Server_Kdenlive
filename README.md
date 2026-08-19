# MCP Server: Kdenlive

A Python [MCP](https://modelcontextprotocol.io) server that lets Claude edit videos in [Kdenlive](https://kdenlive.org/) through natural-language instructions.

## How it connects to Kdenlive

This does **not** remote-control a running Kdenlive window. There's no live connection to the app.

Instead, the server directly reads and writes `.kdenlive` project files (Kdenlive's native XML project format). Claude calls MCP tools (`add_clip`, `create_camera_push`, `save_project`, ...), which edit an internal project model in memory; `save_project`/`save_project_as` writes that out as a real `.kdenlive` file. You then open that file in Kdenlive normally to view, tweak by hand, or render it.

It also uses two other programs, both installed alongside Kdenlive:
- `ffmpeg` / `ffprobe` — reads video/audio file info, makes thumbnails, extracts audio for beat detection.
- `melt` — Kdenlive's own rendering engine. The server can use it to double-check a generated project actually loads correctly, without opening the Kdenlive GUI.

Kdenlive itself doesn't need to be running at all while the server works. You only open Kdenlive when you want to look at or render the result.

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

**Claude Code:** open (or create) `.mcp.json` in your project folder.

Either way, add this (using the path from step 4):

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "/full/path/to/MCP_Server_Kdenlive/.venv/bin/kdenlive-mcp"
    }
  }
}
```

Restart Claude Desktop (or Claude Code) after saving. Kdenlive tools should now show up as available.

### 6. (Optional) Point it at Kdenlive/melt manually

The server auto-detects a snap-installed Kdenlive on Linux. If yours is installed a different way and isn't found automatically, set these environment variables before launching Claude, or add them to the `mcpServers` entry above under an `"env"` key:

| Variable | What it's for |
|---|---|
| `KDENLIVE_MCP_KDENLIVE` | Path to the `kdenlive` binary |
| `KDENLIVE_MCP_MELT` | Path to the `melt` binary |
| `KDENLIVE_MCP_EFFECTS_DIR` | Path to Kdenlive's `effects` folder |

Without Kdenlive/melt installed at all, the server still works for building/saving `.kdenlive` files — you just lose the effects/transitions catalog (what effects actually exist) and the real-engine validation check.

## Try it

Ask Claude something like:

> Create a new 1080p project, import `/home/me/footage/clip1.mp4`, put it on the timeline, add a slow zoom-in over the first 2 seconds, then save it as `my_edit.kdenlive`.

Then open `my_edit.kdenlive` in Kdenlive to see the result.

## What's implemented

98 MCP tools covering: project management, media import, timeline editing (add/trim/split/ripple/group clips), camera motion (push/pull/pan/tilt/orbit/shake/zoom-punch), effects (from Kdenlive's real installed effect list, plus 10 named presets like "cinematic"/"vintage"), transitions (crossfade/zoom/whip/slide/glitch/wipes), audio analysis and beat-sync editing, sound-effect placement, snapshots, and undo/redo.

Not yet implemented: reference-video style analysis, automatic stock-footage/music sourcing, speed ramping, render/preview tools, and a fully autonomous one-shot "make me a video" tool.

## Project layout

```
src/kdenlive_mcp/
  core/           # internal model: timeline, keyframes, effects, transitions, audio, assets
  kdenlive/       # reads/writes .kdenlive files; effect & transition catalogs; capability detection
  media/          # ffmpeg/ffprobe wrappers, thumbnail generation
  storage/        # workspace path safety, caching, snapshots
  validation/     # project validation
  mcp_tools/      # the 98 MCP tools, grouped by category
  server.py       # entrypoint
tests/
```
