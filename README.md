# BlenderMCP — Blender Model Context Protocol Integration

BlenderMCP connects Blender to AI assistants through the Model Context Protocol (MCP), letting Claude directly control Blender for prompt-assisted 3D modelling, scene creation, rendering, and automation.

This is an extended fork of [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) with **91 tools**, full Blender 4.x compatibility, and telemetry removed.

---

## What's new in this fork

- **91 tools** across 26 categories (up from the original ~20)
- **Blender 4.x compatibility** — fixes for BMesh layer API changes, EEVEE engine name, `temp_override` region requirement, compositor node ordering
- **No telemetry** — all analytics code removed
- **Auto-restart on addon reload** — server restarts automatically when the addon is cycled in Preferences; no manual click needed
- **`deploy.py`** — one-command script to copy `addon.py` to Blender's installed addons path
- **`img_to_3d_server.py`** — local image-to-3D inference via TripoSR (optional, loads/unloads on demand to free VRAM)
- **`TOOLS.md`** — full reference for all 91 tools with parameters

---

## Tool categories (91 tools)

| Category | Tools |
|---|---|
| Process management | `start_blender`, `close_blender`, `get_blender_status` |
| Scene & object info | `get_scene_info`, `get_object_info`, `find_objects_by_type`, `measure_distance` |
| Primitives & object mgmt | `add_primitive`, `delete_object`, `duplicate_object`, `rename_object`, `join_objects`, `separate_mesh`, `set_origin`, `snap_to_ground`, `set_smooth_shading`, `parent_object`, `select_objects`, `align_objects` |
| Transforms | `move_object`, `scale_object`, `rotate_object` |
| Mesh editing | `get_mesh_stats`, `subdivide_mesh`, `apply_modifier`, `set_vertex_position` |
| Vertex operations | `get_vertex_positions`, `set_vertex_positions` |
| Edge operations | `get_edges`, `mark_sharp_edges`, `set_edge_crease`, `set_edge_bevel_weight` |
| Face operations | `get_faces`, `extrude_faces`, `inset_faces`, `flip_normals`, `merge_vertices`, `triangulate_mesh`, `set_face_material_index` |
| Curve control points | `get_control_points`, `set_control_point` |
| Camera | `create_camera`, `set_active_camera` |
| Lighting | `add_light`, `set_world_background`, `add_3point_lighting` |
| Materials | `create_material`, `assign_material`, `set_object_material_color`, `load_texture`, `set_texture` |
| Modifiers | `add_modifier`, `boolean_operation` |
| Rendering | `set_render_settings`, `render_from_camera`, `render_all_cameras` |
| Viewport capture | `get_viewport_screenshot`, `capture_viewport_angle`, `capture_contact_sheet`, `render_depth_map` |
| Reference images | `store_reference_image`, `compare_reference_image` |
| Animation | `add_keyframe`, `set_frame` |
| Collections | `create_collection`, `move_to_collection` |
| Export / import | `export_object`, `import_file`, `save_blend`, `load_blend` |
| Scripting | `execute_blender_code` |
| PolyHaven | `get_polyhaven_status`, `get_polyhaven_categories`, `search_polyhaven_assets`, `download_polyhaven_asset` |
| Sketchfab | `get_sketchfab_status`, `search_sketchfab_models`, `get_sketchfab_model_preview`, `download_sketchfab_model` |
| Hyper3D / Rodin | `get_hyper3d_status`, `generate_hyper3d_model_via_text`, `generate_hyper3d_model_via_images`, `poll_rodin_job_status`, `import_generated_asset` |
| Hunyuan3D | `get_hunyuan3d_status`, `generate_hunyuan3d_model`, `poll_hunyuan_job_status`, `import_generated_asset_hunyuan` |
| Image-to-3D (TripoSR) | `load_img_to_3d_model`, `generate_3d_from_image`, `unload_img_to_3d_model` |

See [TOOLS.md](TOOLS.md) for full parameter documentation.

---

## Requirements

- Blender 4.0 or newer (Blender 4.3 recommended)
- Python 3.10+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager

---

## Installation

### 1. Install the MCP server

```bash
git clone https://github.com/naab007/blender_mcp
cd blender_mcp
uv venv .venv
uv pip install -e .
```

### 2. Install the Blender addon

1. Download `addon.py` from this repo
2. Open Blender → Edit → Preferences → Add-ons → Install...
3. Select `addon.py` and enable **Interface: Blender MCP**
4. In the 3D View sidebar (N), open the **BlenderMCP** tab and click **Start MCP Server**

> After the first install, use `deploy.py` to push addon updates in one command:
> ```bash
> .venv/Scripts/python.exe deploy.py
> ```
> Edit the destination path in `deploy.py` to match your Blender version.

### 3. Configure your AI client

**Claude Code** — create `.mcp.json` in your working directory:
```json
{
  "mcpServers": {
    "blender": {
      "type": "stdio",
      "command": "/path/to/blender_mcp/.venv/Scripts/blender-mcp.exe"
    }
  }
}
```

**Claude Desktop** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["blender-mcp"]
    }
  }
}
```

**Cursor** — add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["blender-mcp"]
    }
  }
}
```

---

## Usage

Once the addon is running and the MCP server is configured, ask Claude to:

- "Create a low-poly dungeon scene with a dragon guarding a pot of gold"
- "Set up 3-point studio lighting and render from the active camera"
- "Add a Subdivision Surface modifier to the selected object and apply a PBR material"
- "Capture viewport angles from front, side, and top and give me a contact sheet"
- "Boolean-subtract the sphere from the cube"
- "Download a rock model from PolyHaven and place it at the origin"
- "Generate a 3D model of a garden gnome via Hyper3D"
- "Render depth map of the current scene"
- "Keyframe this object moving from (0,0,0) to (5,0,0) over 60 frames"

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BLENDER_HOST` | `localhost` | Host for the Blender TCP socket |
| `BLENDER_PORT` | `9876` | Port for the Blender TCP socket |
| `IMG_TO_3D_PORT` | `7862` | Port for the local TripoSR server |
| `IMG_TO_3D_MODEL_DIR` | `stabilityai/TripoSR` | Local weights path or HuggingFace hub ID |
| `IMG_TO_3D_DEVICE` | auto | `cuda` or `cpu` |

---

## Architecture

```
Claude / AI client
      │  MCP (stdio)
      ▼
server.py  (FastMCP, @mcp.tool functions)
      │  JSON over TCP :9876
      ▼
addon.py   (Blender Python addon, bpy.app.timers dispatch)
      │
      ▼
Blender scene
```

- All addon handlers run on Blender's main thread via `bpy.app.timers.register()` — no threading issues
- Commands are JSON `{ "type": "...", "params": {...} }`, responses are `{ "status": "success"|"error", "result": ... }`

---

## Troubleshooting

- **"context is incorrect" on view3d operators** — requires Blender 4.x `temp_override(area=area, region=region)`. Already fixed in this fork.
- **BLENDER_EEVEE enum not found** — use `BLENDER_EEVEE_NEXT` in Blender 4.x. Already normalized in this fork.
- **Render writes nothing after depth map** — compositor state corruption from empty node tree. Fixed in this fork via full snapshot/restore in `render_depth_map`.
- **`execute_blender_code`** runs arbitrary Python in Blender — powerful but irreversible. Save your work first.
- **Connection issues** — ensure the addon server is running (green dot in the BlenderMCP sidebar tab) before sending commands.

---

## License

MIT — see [LICENSE](LICENSE).

Based on [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) by Siddharth Ahuja.
