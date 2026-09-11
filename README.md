# BlenderMCP — Blender Model Context Protocol Integration

BlenderMCP connects Blender to AI assistants through the Model Context Protocol (MCP), letting Claude directly control Blender for prompt-assisted 3D modelling, scene creation, rendering, and automation.

This is an extended fork of [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) with **169 tools**, Blender 4.3 – 5.2 support (tested on 4.3.2 and 5.2.1 LTS), and telemetry removed.

---

## What's new in this fork

- **v2.2.0: rigging and animation** — 21 new tools: armatures and bones (`create_armature`, `add_bones`, `get_armature_info`, `set_bone_properties`, `delete_bones`), skinning and weights (`bind_armature`, `get_vertex_groups`, `get_vertex_weights`, `set_vertex_weights`, `render_weight_map`, `find_unweighted_vertices`), pose and constraints (`set_pose`, `get_pose`, `reset_pose`, `add_constraint`, `get_constraints`, `remove_constraint`), animation (`set_keyframes`, `get_animation_info`, `playblast`, `bake_action`); every act tool has a perceive twin (`get_armature_info`, `get_pose`, `render_weight_map`, `playblast`). Bones are returned by name; on Blender 5.x actions are slotted and the tools bind the slot. Rig-aware changes to existing tools: `export_object` gains `include_hierarchy` (armature and children export with the mesh) and the FBX/glTF animation parameters (`bake_anim`, `add_leaf_bones`, `use_armature_deform_only`, `bake_anim_simplify_factor`, `mesh_smooth_type`, bone axes, `apply_scale_options`, `export_animations` / `export_skins` / `export_morph`; parameters that do not apply to the chosen format are reported as ignored); `add_keyframe` keys pose bones (`bone`) and reports a rotation-mode switch; `capture_viewport_angle` / `capture_contact_sheet` gain `overlay` (`bones_in_front`, `wireframe`, `weight_paint`); `parent_object` gains `parent_type=BONE` + `bone`; `get_project_profile(apply_units=True)`. Rigging and animation tools follow as they land.
- **v2.1.0 (in progress on `blender-5.2`): settings, save and load** — the add-on now starts its socket server automatically when Blender starts (preference, on by default), the port and every API key moved from Scene properties into the add-on Preferences (never written into `.blend` files again), the server binds `127.0.0.1`, and `get_version` / `ensure_server_running` commands report the add-on version and wire protocol (`PROTOCOL = 1`). 56 new tools: file lifecycle, generic and typed settings, presets and project profile, add-ons and preferences, session state; `save_blend` / `load_blend` / `set_render_settings` / `start_blender` gained parameters, none removed.
- **v2.0.0: Blender 5.2 LTS** — the four 5.0 API breaks are fixed (EEVEE engine id, `Scene.node_tree` removal, boolean solver `FAST`→`FLOAT`, `media_type` before `file_format`); 4.x/5.x differences are resolved by try-assign, never by version checks. `start_blender` also finds portable `blender-<ver>-windows-x64` folders. Details: `docs/MIGRATION-blender-5.2.md`
- **169 tools** across 34 categories (92 in 2.0.0, 56 added in 2.1.0, 21 added in 2.2.0; up from the original ~20)
- **Blender 4.x compatibility** (v1.6.0) — fixes for BMesh layer API changes, EEVEE engine name, `temp_override` region requirement, compositor node ordering
- **No telemetry** — all analytics code removed
- **Auto-restart on addon reload** — server restarts automatically when the addon is cycled in Preferences; no manual click needed
- **`img_to_3d_server.py`** — local image-to-3D inference via TripoSR (optional, loads/unloads on demand to free VRAM)
- **`TOOLS.md`** — full reference for all 169 tools with parameters

---

## Tool categories (169 tools)

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
| Reference images | `store_reference_image`, `compare_reference_image`, `diff_images` |
| Animation | `add_keyframe`, `set_frame`, `set_keyframes`, `get_animation_info`, `playblast`, `bake_action` |
| Collections | `create_collection`, `move_to_collection` |
| Export / import | `export_object`, `import_file`, `save_blend`, `load_blend` |
| Scripting | `execute_blender_code` |
| Version & server settings (2.1.0) | `get_version`, `get_server_settings`, `set_server_settings` |
| File lifecycle (2.1.0) | `get_file_state`, `new_file`, `revert_file`, `recover_file`, `save_copy`, `save_version`, `list_versions`, `append_from_blend`, `link_from_blend`, `set_autosave`, `make_paths_relative`, `make_paths_absolute`, `find_missing_files`, `pack_all`, `unpack_all` |
| Settings & presets (2.1.0) | `describe_settings`, `get_settings`, `set_settings`, `settings_snapshot`, `settings_restore`, `list_settings_snapshots`, `delete_settings_snapshot`, `set_output_settings`, `set_color_management`, `set_render_quality`, `set_render_device`, `list_render_devices`, `set_simplify`, `set_frame_range`, `set_scene_units`, `set_viewport_defaults`, `save_preset`, `load_preset`, `list_presets`, `delete_preset`, `export_preset`, `import_preset`, `apply_blender_preset`, `list_blender_presets`, `set_project_profile`, `get_project_profile` |
| Add-ons & preferences (2.1.0) | `list_addons`, `enable_addon`, `disable_addon`, `get_addon_preferences`, `set_addon_preferences`, `save_preferences`, `list_workspaces`, `set_workspace`, `get_addon_settings`, `set_addon_settings` |
| Session (2.1.0) | `save_session_state`, `restore_session_state` |
| Rigging: armatures & bones (2.2.0) | `create_armature`, `add_bones`, `get_armature_info`, `set_bone_properties`, `delete_bones` |
| Rigging: skinning & weights (2.2.0) | `bind_armature`, `get_vertex_groups`, `get_vertex_weights`, `set_vertex_weights`, `render_weight_map`, `find_unweighted_vertices` (numbers, or an image with `render=True`) |
| Rigging: pose & constraints (2.2.0) | `set_pose`, `get_pose`, `reset_pose`, `add_constraint`, `get_constraints`, `remove_constraint` |
| PolyHaven | `get_polyhaven_status`, `get_polyhaven_categories`, `search_polyhaven_assets`, `download_polyhaven_asset` |
| Sketchfab | `get_sketchfab_status`, `search_sketchfab_models`, `get_sketchfab_model_preview`, `download_sketchfab_model` |
| Hyper3D / Rodin | `get_hyper3d_status`, `generate_hyper3d_model_via_text`, `generate_hyper3d_model_via_images`, `poll_rodin_job_status`, `import_generated_asset` |
| Hunyuan3D | `get_hunyuan3d_status`, `generate_hunyuan3d_model`, `poll_hunyuan_job_status`, `import_generated_asset_hunyuan` |
| Image-to-3D (TripoSR) | `load_img_to_3d_model`, `generate_3d_from_image`, `unload_img_to_3d_model` |

See [TOOLS.md](TOOLS.md) for full parameter documentation.

---

## Requirements

- Blender 4.3 – 5.2 (tested on 4.3.2 and 5.2.1 LTS). 5.2 LTS is the development target since v2.0.0; 4.0+ is best-effort through try-assign fallbacks (`bl_info` minimum 4.0)
- `.blend` files saved by Blender 5.x open only in Blender 4.5 or newer (4.3.2 refuses them with "incomplete header, may be from a newer version of Blender"). Keep a 4.x copy of any project you still need there; never re-save a 4.x project from 5.2 without a copy.
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
3. Select `addon.py` and enable **Interface: Blender MCP** (it installs into `<Blender config>/scripts/addons/`, e.g. `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\` on Windows; each Blender version has its own folder, so a 5.2 install starts empty even when 4.3 already has the add-on)
4. Since 2.1.0 the socket server starts by itself when Blender starts (Preferences > Add-ons > Blender MCP > **Start server automatically**, on by default; never in `--background`). The 3D View sidebar (N) > **BlenderMCP** tab shows `Running on 127.0.0.1:<port>`, or a **Connect to MCP server** button if autostart is off. On 2.0.0 and earlier, click that button once per session.
5. Port and API keys live in Preferences > Add-ons > Blender MCP (see below); the per-file integration toggles (PolyHaven, Hyper3D, Sketchfab, Hunyuan3D) stay in the sidebar tab.

> To update after the first install, copy the new `addon.py` over the installed one
> (`<Blender config>/scripts/addons/addon.py`) and cycle the add-on off/on in
> Preferences — the MCP server restarts automatically.

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
- "Compare these two renders and show me exactly what changed" → `diff_images(path_a, path_b)`

---

## Add-on preferences and API keys (2.1.0)

Edit > Preferences > Add-ons > Blender MCP holds:

| Preference | Default | Notes |
|---|---|---|
| Port | `9876` | 1024–65535; the server listens on `127.0.0.1` only |
| Start server automatically | on | Starts the socket server 0.5 s after Blender starts; ignored in `--background` |
| Hyper3D API key, Sketchfab API key, Hunyuan 3D SecretId / SecretKey | empty | Password fields, stored in `userpref.blend`, never in a `.blend` file |

Before 2.1.0 the keys and the port were Scene properties and travelled inside every saved `.blend`. On the first load of such a file the add-on moves any key it finds into the Preferences (only where the preference is still empty) and blanks the Scene copy, so the next save no longer carries it. The legacy Scene properties stay registered but hidden (through 2.1.x and 2.2.0) so a file coming straight from 1.6.0 still migrates; they are removed in 2.3.0. Because preferences are per Blender installation (`%APPDATA%\Blender Foundation\Blender\<ver>\config\userpref.blend`), you enter each key once per installation, for example once in 4.3 and once in 5.2. The keys are write-only over the wire: no tool ever echoes them back.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BLENDER_EXE` | auto-detect | Full path to `blender.exe` for `start_blender`. Auto-detection picks the highest-version copy it finds (Program Files installs and portable `blender-<ver>-windows-x64` folders on D:, in your home folder and on any fixed drive root); set this to pin one version, e.g. `D:\blender-5.2.1-windows-x64\blender.exe`. A path that does not exist is ignored. |
| `BLENDER_HOST` | `127.0.0.1` | Host of the Blender add-on socket (the add-on binds `127.0.0.1` only since 2.1.0; was `localhost` before) |
| `BLENDER_PORT` | `9876` | Port of the Blender add-on socket |
| `BLENDER_MCP_SETTINGS_DIR` | `~/.blender_mcp` | Directory of the server's `settings.json` and `presets/` (2.1.0). Tests and harness runs always set it so they never touch your real file |
| `IMG_TO_3D_PORT` | `7862` | Port of the local TripoSR server. Since 2.1.0 the MCP server honours it (env > settings file `img_to_3d_port` > 7862); in 2.0.0 `load_img_to_3d_model` always used 7862 |
| `IMG_TO_3D_MODEL_DIR` | `stabilityai/TripoSR` | Local weights path or HuggingFace hub ID |
| `IMG_TO_3D_DEVICE` | auto | `cuda` or `cpu` |

## Server settings file (2.1.0)

The MCP server keeps its own configuration in `<settings dir>/settings.json`, where the
settings dir is `~/.blender_mcp` or `BLENDER_MCP_SETTINGS_DIR`. The file is created on the
first `set_server_settings` call; until then every key is at its default. Precedence,
highest first: environment variable, settings file, code default. A missing or corrupt file
logs one warning and falls back to the defaults; unknown keys in the file are preserved.

| Key | Default | Env override | Meaning |
|---|---|---|---|
| `host` | `127.0.0.1` | `BLENDER_HOST` | Add-on socket host |
| `port` | `9876` | `BLENDER_PORT` | Add-on socket port |
| `connect_timeout` | `5.0` | | Seconds to wait for the socket connect |
| `command_timeout` | `180.0` | | Seconds to wait for a command reply |
| `blender_exe` | none | `BLENDER_EXE` | Blender executable for `start_blender` |
| `output_dir` | none | | Default directory for renders and exports when a tool's path is omitted |
| `presets_dir` | `<settings dir>/presets` | | Where `save_preset` / `load_preset` keep their JSON |
| `image_max_pixels` | `8000000` | | Pixel ceiling before image replies are downscaled |
| `log_level` | `INFO` | | Server log level |
| `img_to_3d_port` | `7862` | `IMG_TO_3D_PORT` | Local TripoSR server port |

`get_server_settings` shows every key with its source (`env`, `file` or `default`);
`set_server_settings` writes the file atomically (a `null` value resets a key to its
default, unknown keys and wrong types are rejected, a key pinned by an environment variable
keeps the environment value and is reported as overridden). Changing `host` or `port` drops
the current Blender connection so the next call reconnects.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `mcp[cli]` | ≥1.3.0 | MCP server framework |
| `pillow` | ≥10.0.0 | Image processing (viewport capture, contact sheets, diff) |
| `numpy` | ≥1.24.0 | Pixel math for `diff_images` |
| `flask` | ≥3.0.0 | Local TripoSR inference server |
| `requests` | ≥2.31.0 | PolyHaven / Sketchfab / Hyper3D API calls |

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

- **"context is incorrect" on view3d operators** — Blender 4.x and 5.x require `temp_override(area=area, region=region)`. Already fixed in this fork.
- **EEVEE engine id** — it is `BLENDER_EEVEE_NEXT` on Blender 4.2–4.5 and `BLENDER_EEVEE` again from 5.0. The add-on resolves `BLENDER_EEVEE` (or plain `EEVEE`) on 4.x and 5.x by try-assign; the `set_render_settings` reply reports the id actually used.
- **`start_blender` launches the wrong Blender** — auto-detection takes the highest version it finds across Program Files and portable folders. Set `BLENDER_EXE` (see Environment variables) or pass `blender_exe=` to pin one.
- **Boolean solver `FAST` rejected** — Blender 5.0 renamed it `FLOAT`. `boolean_operation` accepts either spelling and maps it to the live enum; `MANIFOLD` is passed through on 5.2+. The reply names the solver used.
- **Render writes nothing after depth map** — compositor state corruption from empty node tree. Fixed in this fork via full snapshot/restore in `render_depth_map`.
- **`execute_blender_code`** runs arbitrary Python in Blender — powerful but irreversible. Save your work first.
- **`[blender-mcp mismatch]` at the top of a reply** — the server and the add-on differ in version or wire protocol (shown once per connection; a pre-2.1 add-on is reported as `pre-2.1 / protocol none`). Deploy the matching `addon.py`, cycle the add-on, and check with `get_version`.
- **Connection issues** — the sidebar tab must show `Running on 127.0.0.1:<port>`. If it shows the **Connect to MCP server** button, autostart is off (Preferences > Add-ons > Blender MCP) or the add-on was enabled after launch: click the button once. Headless (`--background`) Blender never opens the port by itself; the server's `start_blender` handles that path. On 2.0.0 and earlier the server never started on a normal launch (fixed in 2.1.0).

---

## License

MIT — see [LICENSE](LICENSE).

Based on [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) by Siddharth Ahuja.
