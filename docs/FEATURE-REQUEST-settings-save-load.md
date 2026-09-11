# Feature request: settings, save and load

Status: IN PROGRESS as Phase B0 of team nuzukm on branch `blender-5.2` (2026-09-11);
contract deltas C1-C15 and slices in `PLAN.md` section 9. Requested 2026-09-08.
Target version: 2.1.0 (PLAN.md C1; the doc's original 1.7.x is superseded)
Baseline: v2.0.0 (Blender 5.2 migration, branch `blender-5.2`), 92 tools
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here.

## 0. Scope and why

"Settings" here means everything an agent must read or change that is not geometry:
scene, render, colour management, output, units, preferences, add-ons, render devices,
workspaces, the add-on's own configuration and the MCP server's configuration. "Save and
load" means the whole file lifecycle: save with options, copies and numbered versions,
new files, revert, recovery, autosave, recent files, partial append and link, presets,
and session state that survives a Blender restart.

Today there are three tools in this area. `save_blend` always calls Save As (so it
silently changes the working file path), with no compression, relative-path or copy
option. `load_blend` discards unsaved work without checking and cannot control UI or
script loading. `set_render_settings` covers seven properties. Every other tool in the
other request docs that temporarily changes a setting (engine, samples, shading, mode)
re-implements its own save-and-restore, which is exactly the shared helper this request
adds.

Two problems found in the existing code that this request fixes:

- The add-on keeps API keys (Hyper3D, Hunyuan3D, Sketchfab) in `Scene` properties, so
  they are written into every `.blend` the user saves and shared with anyone who receives
  the file. They belong in `AddonPreferences` or the OS keyring, never in scene data.
- The add-on port is also a `Scene` property, so it changes per file and resets on
  `wm.read_homefile`. It belongs in `AddonPreferences`.

Build what is in this document. If something is wrong or impossible on 4.3.2, say so in
the report and continue.

## 0.1 BUG: the add-on never starts its socket server on a normal Blender launch

Symptom: after launching Blender (by hand or through `start_blender`), nothing listens on
port 9876 until the user opens the N sidebar and clicks "Connect to Claude".
`start_blender(wait_for_addon=True)` therefore polls for 30 s and reports "the MCP addon
did not respond", and every subsequent tool call fails to connect. Agents have repeatedly
misread this as "the add-on is not enabled".

Cause (addon.py, verified 2026-09-08):

- `register()` only starts the server when `_restart_flag_get()` is True, and that flag is
  set exclusively by `unregister()` while the server was running. It exists to survive an
  add-on reload. On a cold start the flag is False and nothing starts.
- The only other start path is `BLENDERMCP_OT_StartServer.execute` (the panel button).
- `scene.blendermcp_server_running` is a `Scene` property, so it is SAVED INTO `.blend`
  FILES. A file saved while connected reopens with the panel showing "running" while no
  server exists, and a file saved while disconnected can show "stopped" while one does.
  It is display state and must not live in the file.
- `start_blender` has a `python_expr` hook but never uses it to start the server.

Required fix (part of this request, Tier 1):

1. Add `autostart_server: BoolProperty(default=True)` to `BLENDERMCP_AddonPreferences`.
   In `register()`, when it is True (or when the reload flag is set), schedule the same
   deferred `_deferred_start` through `bpy.app.timers` (`bpy.context.scene` is not
   available inside `register()` at startup; the timer already handles that). Read the
   port from preferences, not the scene (see the fix table below).
2. Replace `scene.blendermcp_server_running` with a runtime check
   (`getattr(bpy.types, "blendermcp_server", None) is not None and server.running`) used by
   the panel, and drop the Scene property. Add a `load_post` handler that leaves the
   running server alone when another file is opened (the server object is process-global,
   the scenes are not).
3. `start_blender` gains `start_server=True`: when the launch is not headless it passes
   `--python-expr "import bpy; bpy.app.timers.register(lambda: (bpy.ops.blendermcp.start_server(), None)[1], first_interval=1.0)"`
   (or a module-level `blendermcp.ensure_server()` function exposed by the add-on, which
   is cleaner and does not need operator context) as a belt-and-braces path when the
   preference is off. Keep the 30 s poll, but on timeout the error must say the two real
   causes: the add-on is not enabled in Preferences, or autostart is off and nobody
   clicked "Connect to Claude".
4. `get_blender_status` must probe the port the same way `BlenderConnection` connects
   (the memory note records it reporting "not reachable" while `netstat` shows LISTENING;
   find and fix that discrepancy in the same pass, most likely a host mismatch between
   `localhost` and `127.0.0.1` or a probe that connects without sending a request).
5. Add `ensure_server_running()` as an add-on command that starts the server if needed and
   reports `{running, port, host, started_now}`; the panel button calls the same function.

Test (headless is NOT enough for this one, the timer must fire in a real session): launch
Blender through `start_blender` with the preference on and confirm the port answers a
`get_scene_info` within 10 s with no clicks; save a file while connected, reopen it with
the preference off, confirm the panel does not claim to be running.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `save_blend` | Always Save As; no options | Keep the name. Add `compress=None` (None = preference), `relative_remap=True`, `copy=False` (save a copy WITHOUT changing the working path, `wm.save_as_mainfile(copy=True)`), `incremental=False` (`wm.save_mainfile(incremental=True)`, Blender's own `name_001` numbering), `backup=True` (Blender's `.blend1` is governed by the `save_version` preference; report the preference value), `overwrite=True` (refuse when False and the target exists), `purge_orphans=False`. When `filepath` is omitted and the file has never been saved, save to the temp path as now but return `warning: unsaved file, saved to temp`. Reply: path, bytes, `is_dirty` after, elapsed. |
| `load_blend` | Loses unsaved work silently; no options | Add `force=False`: when `bpy.data.is_dirty` and not `force`, return an error naming the current file and the `force` flag (or `save_first=True` to save then load). Add `load_ui=None` (None = preference `use_load_ui`), `use_scripts=None` (None = preference), `revert_on_fail=True`. Reply adds `previous_file`, `blender_version_of_file` (`bpy.data.version`), `unsaved_changes_discarded`. |
| `set_render_settings` | Seven properties | Keep as the convenience wrapper; route it through the new `set_settings(scope="RENDER")` so both share validation, and extend it with `fps`, `fps_base`, `frame_start`, `frame_end`, `resolution_percentage`, `color_mode`, `color_depth`, `compression`, `denoise`, `device`, `use_persistent_data`, `use_simplify` + `simplify_subdivision`. |
| `get_scene_info` | No file state | Add `file: {filepath, is_saved, is_dirty, version}` and `settings_summary` (engine, resolution, fps, frame range, units, view transform). |
| Add-on preferences | Secrets and port in `Scene` props | Move `blendermcp_port` and every API key to `BLENDERMCP_AddonPreferences` (subtype `'PASSWORD'` for keys). Keep the Scene toggles (`blendermcp_use_polyhaven` etc.) since they are per-file choices. Migration: on `register()`, if a loaded scene carries a non-empty legacy key, copy it into preferences once and blank the scene property. Read the port from preferences in `register()` and the panel. Version bump required (wire protocol unchanged, but the panel and prefs change). |
| `_render_settings` (addon helper) | Only knows camera, resolution, samples | Superseded by the generic `_settings_scope` context manager below; refactor it to use it. |

## 2. New tools

### 2.1 Tier 1, required

**File lifecycle**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `get_file_state` | | `filepath, is_saved, is_dirty, file_version (bpy.data.version), blender_version, use_autopack, packed_images, missing_files (images/libraries whose path does not exist), libraries (linked .blend paths), autosave_dir (bpy.app.tempdir), autosave_files (newest first with mtime), recent_files (from preferences), backup_files (<name>.blend1..N beside the file)`. The first call an agent makes before any destructive step. |
| `new_file` | `template=None` (app template name), `empty=False` (`use_empty`), `load_ui=False`, `force=False` | `wm.read_homefile`. Same dirty guard as `load_blend`. Reply: object count after. |
| `revert_file` | `force=False`, `use_scripts=None` | `wm.revert_mainfile`. Refuses when the file was never saved. |
| `recover_file` | `mode="LAST_SESSION"` (LAST_SESSION / AUTOSAVE with `filepath` from `get_file_state.autosave_files`), `force=False` | `wm.recover_last_session` / `wm.recover_auto_save`. |
| `save_copy` | `filepath`, `compress=None`, `relative_remap=True`, `pack_images=False` | Shorthand for `save_blend(copy=True)` plus optional temporary `file.pack_all` that is undone after (so the working file is unchanged). Deliverable-safe exports of the working file. |
| `save_version` | `note=None`, `pattern="{stem}_v{n:03d}"`, `dir=None` (beside the file), `copy=True` | Numbered copies with a sidecar `<file>.versions.json` (n, timestamp, note, object count, tri count). `copy=True` keeps working on the original; `copy=False` switches to the new version. `list_versions(filepath=None)` reads the sidecar and the matching files. |
| `append_from_blend` / `link_from_blend` | `filepath`, `datablocks` JSON `{"objects": [...], "collections": [...], "materials": [...], "node_groups": [...]}` or `names` + `kind`, `link=False`, `collection=None`, `instance_collections=False`, `relative=True`, `list_only=False` | `bpy.data.libraries.load(filepath, link=, relative=)` context manager; `list_only=True` returns what the file contains per data type without loading. Replaces the whole-file append in `import_file`. Shared with the presentation request. |
| `set_autosave` | `enabled=None`, `interval_minutes=None`, `save_versions=None`, `temp_dir=None`, `persist=False` | Preferences `filepaths.use_auto_save_temporary_files`, `auto_save_time`, `save_version`, `temporary_directory`. `persist=True` calls `wm.save_userpref` (see consent rule). |
| `make_paths_relative` / `make_paths_absolute` / `find_missing_files` | `find_missing_files(directory, find_all=False)` | `bpy.ops.file.make_paths_relative/absolute`, `file.find_missing_files`. Reply: counts and the still-missing list. |
| `pack_all` / `unpack_all` | `unpack_method="USE_LOCAL"` | `file.pack_all`, `file.unpack_all(method=)`. |

**Settings, generic**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `describe_settings` | `scope` | Scopes: `SCENE`, `RENDER`, `OUTPUT` (image_settings + filepath), `CYCLES`, `EEVEE`, `COLOR` (view_settings + display_settings), `UNITS`, `VIEWPORT` (the shading and overlay of the first VIEW_3D), `PREFS_FILEPATHS`, `PREFS_VIEW`, `PREFS_EDIT`, `PREFS_SYSTEM`, `PREFS_INPUT`, `ADDON:<module>` (its AddonPreferences), `MCP` (the add-on's own prefs), `SERVER` (the Python server's settings file). Returns every property with type, current value, default, enum items, min/max, description, read-only flag. Auto-generated from `bl_rna`, never hand-written. |
| `get_settings` | `scope`, `keys=None` (comma list; all if None) | Values only. Enum values as strings, vectors as lists, pointers as names. |
| `set_settings` | `scope`, `values` JSON, `persist=False` | Validates against `bl_rna` before writing (enum membership, numeric range, type), writes, returns `set` and `unset {key: reason}` exactly like `add_modifier`. `persist=True` on a `PREFS_*`, `ADDON:` or `MCP` scope saves preferences (consent rule below). Refuses read-only properties with the reason. |
| `settings_snapshot` | `name`, `scopes="SCENE,RENDER,OUTPUT,CYCLES,EEVEE,COLOR,UNITS,VIEWPORT"` | Captures the values into `bpy.app.driver_namespace["blendermcp_settings_snapshots"][name]` (survives add-on reload, dies with the session) and optionally to a JSON file with `filepath`. Reply: key count per scope. |
| `settings_restore` | `name` or `filepath`, `scopes=None` | Restores; reports keys that no longer exist or failed. Every tool in the other requests that changes settings temporarily MUST use the addon-side helper `_settings_scope(scopes)` (a context manager that snapshots on enter and restores in `finally`), which is the same code path. |
| `list_settings_snapshots` / `delete_settings_snapshot` | | |

**Settings, typed conveniences** (all implemented on `set_settings`)

| Tool | Parameters | Behaviour |
|---|---|---|
| `set_output_settings` | `filepath=None`, `file_format=None` (PNG / JPEG / OPEN_EXR / TIFF / FFMPEG ...), `color_mode=None` (BW / RGB / RGBA), `color_depth=None`, `compression=None`, `quality=None`, `film_transparent=None`, `use_stamp=None`, `use_overwrite=None`, `use_placeholder=None`, `ffmpeg` JSON (`format`, `codec`, `constant_rate_factor`) | Output path, format and video settings in one call. |
| `set_color_management` | `view_transform=None` (Standard / Filmic / AgX / Khronos PBR Neutral / Raw), `look=None`, `exposure=None`, `gamma=None`, `display_device=None`, `sequencer_colorspace=None` | Game-asset renders usually want `Standard` so baked colours match the engine; the default is AgX. Enum items are NOT readable headless (see facts), so validate by try-assign. |
| `set_render_quality` | `preset="PREVIEW"` (PREVIEW / DRAFT / FINAL / custom), `engine=None` | PREVIEW: 25 % resolution, 16 samples, denoise on, simplify on. DRAFT: 50 %, 64. FINAL: 100 %, 256 or the engine default, persistent data on. Stores the previous values in a snapshot named `_before_quality` so `settings_restore` can undo it. |
| `set_render_device` | `device="GPU"` (GPU / CPU), `backend=None` (OPTIX / CUDA / HIP / ONEAPI / METAL / NONE), `persist=False` | Cycles add-on preferences `compute_device_type` + `get_devices()` + per-device `use` flags, then `scene.cycles.device`. Reply: devices found with their `use` state. `list_render_devices()` is the read side. |
| `set_simplify` | `enabled`, `subdivision=None`, `child_particles=None`, `texture_limit=None`, `volume_resolution=None` | |
| `set_frame_range` | shared with the rigging request | |
| `set_scene_units` | shared with the engine-readiness request | |
| `set_viewport_defaults` | `shading=None`, `light=None`, `color_type=None`, `show_overlays=None`, `show_floor=None`, `show_stats=None`, `clip_end=None`, `lens=None` | Applies to every VIEW_3D area; the persistent counterpart of the per-capture `shading`/`overlay` params. |

**Presets and profiles**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `save_preset` | `name`, `scopes="RENDER,OUTPUT,CYCLES,EEVEE,COLOR"`, `overwrite=False`, `description=None` | JSON under `<MCP settings dir>/presets/<name>.json` (server-side file, values pulled through `get_settings`). Portable across files and Blender versions, unlike Blender's Python presets. |
| `load_preset` | `name` or `filepath`, `scopes=None` | Through `set_settings`; reports unset keys. |
| `list_presets` / `delete_preset` / `export_preset` / `import_preset` | | Ship-with defaults: `game_bake` (Standard view transform, Cycles, 32 samples, no denoise, 16-bit PNG), `preview`, `final_eevee`, `final_cycles`, `sprite_sheet` (transparent, Standard, RGBA 8-bit, Filmic off), `turntable_video` (FFMPEG H.264). |
| `apply_blender_preset` | `category` (render / cycles/sampling / cycles/viewport / ...), `name` | Blender's own `.py` presets via `script.execute_preset(filepath, menu_idname)`; `list_blender_presets(category)` walks `bpy.utils.preset_paths(category)`. The built-in render presets (HDTV 1080p, 4K UHD, ...) come free. |
| `set_project_profile` | `engine_target` (UNITY / UNREAL / GODOT / BEVY / TIMBERMESH / NONE), `export_dir=None`, `texture_dir=None`, `render_dir=None`, `kit_unit=None`, `naming=None`, `max_triangles=None`, `texture_size=None`, `notes=None` | Stored as `scene["blendermcp_profile"]` (custom property, travels with the file) and mirrored to `<file>.mcp-profile.json`. The engine-readiness and texturing tools read their defaults from it (`export_for_engine` with `engine=None` uses the profile). `get_project_profile()` reads it back and applies `set_scene_units` for the target when `apply_units=True`. |

**Add-ons, workspaces, preferences**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `list_addons` | `enabled_only=False`, `filter=None` | `addon_utils.modules()` + `preferences.addons.keys()`: module, name, version, category, enabled, has_preferences, path. |
| `enable_addon` / `disable_addon` | `module`, `persist=False` | `addon_utils.enable(module, default_set=True)` ALWAYS (PLAN.md C13: `default_set=False` registers the module but never adds it to `preferences.addons`, so `list_addons` cannot see it and Rigify's own `register()` fails); `persist` only decides whether `wm.save_userpref` follows (consent rule). Returns the add-on's `bl_info`, `preferences.is_dirty`, and the error text if enabling fails; a failed enable is rolled back with `addon_utils.disable(module, default_set=True)` so a retry does not hit "already registered". Rigify, BVH, Node Wrangler and Cycles all go through here. |
| `get_addon_preferences` / `set_addon_preferences` | `module`, `values` JSON | Same machinery as `get_settings(scope="ADDON:<module>")`. |
| `save_preferences` | `confirm=False` | `wm.save_userpref`. Refuses without `confirm=True` and reports `preferences.is_dirty` and `use_preferences_save`. Consent rule: nothing in this request writes `userpref.blend` unless the caller passes `persist=True` or `confirm=True`; the reply always says whether preferences were persisted. |
| `list_workspaces` / `set_workspace` | `name` | `bpy.data.workspaces`, `context.window.workspace`. The capture tools need a VIEW_3D; `set_workspace("Layout")` guarantees one. Reply lists each workspace's area types. |
| `get_server_settings` / `set_server_settings` | `values` JSON | The Python server's own config (`~/.blender_mcp/settings.json`, created on first run): `host`, `port`, `connect_timeout`, `command_timeout`, `blender_exe`, `output_dir` (default for renders and exports when a tool's path is omitted), `presets_dir`, `image_max_pixels`, `log_level`, `img_to_3d_port`. Environment variables (`BLENDER_HOST`, `BLENDER_PORT`, `BLENDER_EXE`) override the file. Changes to host/port take effect on the next connection (`_drop_connection()`), others immediately. |
| `get_addon_settings` / `set_addon_settings` | `values` JSON | The MCP add-on's own `AddonPreferences`: `port`, `autostart_server`, `api keys` (write-only: never echoed back, reply shows `set: true/false` per key). |

**Session state**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `save_session_state` | `name="last"`, `include="FILE,FRAME,CAMERA,SELECTION,ACTIVE,MODE,VIEWPORT,WORKSPACE,SETTINGS_SNAPSHOTS"` | JSON in the server settings dir: open file path, dirty flag, frame, active camera, selected and active object names, mode, each VIEW_3D's view matrix/distance/shading, workspace, and the named settings snapshots. Pairs with `close_blender` / `start_blender`. |
| `restore_session_state` | `name="last"`, `load_file=True`, `force=False` | Reopens the file (dirty guard) and restores the rest; reports what could not be restored (a renamed object, a missing camera). `start_blender(restore_session="last")` is the shorthand. |

### 2.2 Tier 2

| Tool | Parameters | Behaviour |
|---|---|---|
| `run_on_files` | `files` (list or glob), `commands` JSON list of `{type, params}`, `save=False`, `output_dir=None`, `continue_on_error=True` | Opens each file, runs the commands through the normal dispatcher, optionally saves or saves-as into `output_dir`. Batch re-export, batch validate, batch rename across a library. Restores the original file afterwards. |
| `compare_blend_files` | `file_a`, `file_b` | Via `libraries.load(list_only)` on both: datablock names per type added, removed, common; sizes; versions. |
| `list_scenes` / `create_scene` / `delete_scene` / `set_active_scene` / `copy_settings_between_scenes` | | Scene management; `copy_settings_between_scenes(src, dst, scopes)` uses the settings machinery. |
| `list_view_layers` / `create_view_layer` / `set_active_view_layer` | | |
| `save_startup_file` | `confirm=False` | `wm.save_homefile`; refuses without `confirm=True`. |
| `load_factory_settings` | `confirm=False`, `keep_addons=True` | `wm.read_factory_settings`; re-enables the current add-on list afterwards when `keep_addons`. |
| `set_asset_libraries` | `add` JSON `[{name, path}]`, `remove=None`, `persist=False` | `preferences.filepaths.asset_libraries` + `preferences.asset_library_add`. |
| `set_external_tools` | `image_editor=None`, `animation_player=None`, `text_editor=None`, `persist=False` | `preferences.filepaths.*` so `image.external_edit` can open ImageTools-friendly editors. |
| `export_settings_report` | `filepath` | Markdown or JSON dump of every scope for a bug report or a MemPalace drawer. |
| `set_undo_settings` | `steps`, `memory_limit`, `persist=False` | `preferences.edit.undo_steps` / `undo_memory_limit`; the driver's batch transactions need enough steps. |

### 2.3 Tier 3, later (do NOT build unless asked)

Theme and keymap management, app-template creation, per-project preference overlays,
cloud sync of the presets dir.

## 3. Shared helper to add (addon)

```python
@contextmanager
def _settings_scope(self, scopes=("RENDER", "CYCLES", "EEVEE", "OUTPUT", "COLOR", "VIEWPORT")):
    """Snapshot the named scopes on enter, restore them in finally, whatever happens."""

def _scope_owner(self, scope):
    """Map a scope name to (rna_struct_instance, allowed_property_filter)."""
```

Every temporary change in the other requests (bake engine swap, playblast, weight-map
capture, material preview, sprite sheet, set_render_quality) uses `_settings_scope`.
`_render_settings` becomes a thin call to it.

## 4. Ripple points beyond the standard list

- `addon.py` preferences class (`BLENDERMCP_AddonPreferences`), panel (`BLENDERMCP_PT_Panel`)
  and `register()` port read; migration of legacy scene keys.
- `src/blender_mcp/server.py`: settings file loader (`~/.blender_mcp/settings.json`),
  presets dir, env override order, `_drop_connection()` on host/port change.
- `TOOLS.md` and `README.md`: sections "File Lifecycle", "Settings & Presets",
  "Add-ons & Preferences", "Session".
- `/blender` skill: a "Start of session" pattern (`get_file_state` → `set_workspace` →
  `load_preset` → work → `save_version`) and the consent rule for preferences.
- Auto-memory `project_blender_mcp.md`: settings file location, presets dir, the
  secrets-migration note.
- MemPalace `main` db: a decisions drawer for the secrets move and the consent rule.

## 5. Blender facts, verified headlessly on 4.3.2 (2026-09-08) and re-verified on 4.3.2 and 5.2.1 (2026-09-11) (do not re-derive)

Evidence for the 2026-09-11 re-verification: `apichecks/b0check.py` and `apichecks/scheck.py`
run on both installs, lines under `### b0check.py` in `apichecks/out_4.3.2.jsonl` and
`apichecks/out_5.2.1.jsonl` (Planner/API, PLAN.md section 9.5). Bullets below hold on both
versions unless annotated `[5.2.1: ...]`; section 5.1 lists every difference as a per-tool
switch.

- `wm.save_mainfile(filepath, compress, relative_remap, exit, incremental)`;
  `wm.save_as_mainfile(filepath, compress, relative_remap, copy)`; `compress` defaults
  to False (the preference `use_file_compression` is NOT applied by the operator when
  called from Python; pass it explicitly). [5.2.1: `wm.save_mainfile` gains
  `show_save_modified_images_dialog`, never pass it (S15); the preference
  `use_file_compression` defaults to True on 5.2.1 and False on 4.3.2 (S12); a compressed
  save is 92-96 KB against a 459-567 KB uncompressed copy of the factory file; `incremental`
  names `a.blend` -> `a1.blend` on both (C14).]
- `wm.open_mainfile(filepath, load_ui, use_scripts, display_file_selector, state)`;
  `wm.revert_mainfile(use_scripts)`; `wm.recover_last_session(use_scripts)`;
  `wm.recover_auto_save(filepath, use_scripts)`.
- `wm.read_homefile(filepath, load_ui, use_splash, use_factory_startup,
  use_factory_startup_app_template_only, app_template, use_empty)`; `wm.save_homefile()`;
  `wm.read_factory_settings(use_factory_startup_app_template_only, app_template,
  use_empty)`; `wm.save_userpref()`; `wm.read_userpref()`; `wm.read_factory_userpref()`.
- `wm.append(filepath, directory, filename, files, link, do_reuse_local_id,
  clear_asset_data, autoselect, active_collection, instance_collections,
  instance_object_data, set_fake, use_recursive)`; `wm.link(... relative_path ...)`.
  `bpy.data.libraries.load(filepath, link=False, relative=False, assets_only=False,
  create_liboverrides=False, reuse_liboverrides=False, create_liboverrides_runtime=False)`
  [5.2.1: adds `pack, set_fake, recursive, reuse_local_id, clear_asset_data`; every
  argument after `filepath` is keyword-only on BOTH versions (positional `link` raises),
  and loading from the currently open file is refused on both (S14, S25)].
- `script.execute_preset(filepath, menu_idname)`; `render.preset_add(name, remove_name,
  remove_active)`; `bpy.utils.preset_paths("render")` →
  `<install>\4.3\scripts\presets\render`, `preset_paths("cycles/sampling")` likewise.
- `bpy.data.is_dirty`, `is_saved`, `filepath`, `version` (tuple; the factory startup file
  reports 3.6.10 on 4.3.2 and (5, 1, 16) on 5.2.1, i.e. the version that SAVED the file,
  not the running Blender; never asserted, S19), `use_autopack`. [5.2.1: `is_dirty` is
  False at factory startup (4.3.2: True) and False after a headless `save_as_mainfile`
  (4.3.2: True): tools REPORT it, tests never assume it (C3, S11, S21).] `bpy.app.tempdir` is the autosave/temp dir for the session,
  `bpy.app.background` tells headless.
- `preferences.filepaths` props: `use_relative_paths, use_file_compression, use_load_ui,
  use_scripts_auto_execute, save_version, use_auto_save_temporary_files, auto_save_time,
  temporary_directory, render_output_directory, texture_directory, font_directory,
  sound_directory, render_cache_directory, script_directories, image_editor,
  text_editor, text_editor_args, animation_player, animation_player_preset,
  recent_files, file_preview_type, asset_libraries, active_asset_library, ...`.
  `preferences.asset_library_add` operator exists.
- `preferences.view`: `show_splash, show_tooltips, ui_scale, render_display_type,
  show_developer_ui, use_translate_interface, language`. `preferences.edit`:
  `undo_steps, undo_memory_limit, use_global_undo, object_align, use_enter_edit_mode,
  material_link`. `preferences.system`: `memory_cache_limit, scrollback,
  gl_texture_limit, anisotropic_filter, viewport_aa, use_gpu_subdivision,
  texture_time_out`. Top level: `is_dirty, use_preferences_save, active_section,
  autoexec_paths, addons, themes, keymap, apps, filepaths, view, edit, inputs, system,
  experimental, studio_lights`.
- Cycles add-on preferences: `compute_device_type` enum items read EMPTY headless in
  factory mode (dynamic enum), `get_devices()` exists. Validate by try-assign and report
  the devices `get_devices()` returns.
- `scene.view_settings`: `view_transform, look, exposure, gamma, use_curve_mapping,
  use_hdr_view` [5.2.1: `use_hdr_view` REMOVED (C4)]; the `view_transform` enum reads only `NONE` headless (OCIO config not
  fully loaded in background), so validate by try-assign, never by enum listing.
  `scene.display_settings.display_device`.
- `scene.render` props present: `engine, resolution_x, resolution_y,
  resolution_percentage, fps, fps_base, filepath, film_transparent, use_motion_blur,
  use_persistent_data, threads_mode, threads, use_border, use_crop_to_border,
  pixel_aspect_x, use_simplify, simplify_subdivision, simplify_child_particles,
  use_stamp, use_compositing, use_sequencer, dither_intensity, use_lock_interface`.
  `image_settings`: `file_format, color_mode, color_depth, compression, quality`.
- `scene.cycles`: `samples, preview_samples, use_denoising, denoiser, device,
  adaptive_threshold, use_adaptive_sampling, max_bounces, time_limit, use_light_tree,
  tile_size, use_auto_tile`. `scene.eevee` (EEVEE Next): `taa_render_samples,
  taa_samples, use_raytracing, use_shadows, shadow_ray_count, use_volumetric_shadows,
  use_gtao` [5.2.1: `use_gtao` REMOVED (C4); shipped presets must not carry it];
  `use_bloom`, `use_ssr`, `use_motion_blur` are GONE on both.
- `scene` props: `frame_start, frame_end, frame_current, frame_step, use_preview_range,
  camera, world, unit_settings, gravity, use_gravity, audio_volume, use_nodes,
  background_set, cursor`.
- `addon_utils`: `enable, disable, modules, check, module_bl_info, paths, reset_all`.
- Default workspaces: Animation, Compositing, Geometry Nodes, Layout, Modeling,
  Rendering, Scripting, Sculpting, Shading, Texture Paint, UV Editing.
- Config files: `%APPDATA%\Blender Foundation\Blender\4.3\config\startup.blend` and
  `userpref.blend` (`bpy.utils.user_resource("CONFIG", path=...)`). App templates dir
  `<install>\4.3\scripts\startup\bl_app_templates_system`.
- Existing add-on: `blendermcp_port` and every API key are `bpy.types.Scene` properties
  registered in `register()` (addon.py ~lines 4557-4676); the server reads `BLENDER_HOST`,
  `BLENDER_PORT`, `BLENDER_EXE`, `IMG_TO_3D_*` from the environment only.

### 5.1 4.x/5.x switches per tool (B0-G0, evidence keys in `out_*.jsonl`)

| Tool | Switch | Evidence |
|---|---|---|
| `get_file_state` | `is_dirty` False at 5.2.1 startup, True at 4.3.2; `file_version` is the saving version ((3, 6, 10) / (5, 1, 16)); report, never assert | `data_flags_startup` |
| `load_blend`, `new_file`, `revert_file` (dirty guard) | on 5.2.1 `--background` `bpy.data.is_dirty` STAYS False after edits (no undo pushes headless; `primitive_cube_add` leaves it False), on 4.3.2 headless it is True from startup. The guard therefore fires headless only on 4.3.2; doc test 3 (refusal when dirty) is 4.3.2-headless / 5.2.1-live, asserted as "guard iff `is_dirty` was True before the call" | Addon Dev L2 measurement, Lead verification 2026-09-11 (`load_blend` fires on 4.3.2, passes on 5.2.1) |
| `save_blend`, `save_copy` | compression preference default on (5.2.1) / off (4.3.2); pass `compress` explicitly and report the effective value; `is_dirty` after save differs per version | `save_roundtrip` |
| `save_blend` | 5.2 `wm.save_mainfile` has `show_save_modified_images_dialog`: never passed | scheck diff |
| `save_blend(incremental=True)` | Blender names `a.blend` -> `a1.blend` on both (not `a_001`) | `save_roundtrip.save_incremental` |
| `revert_file` | on 4.3.2 headless a long save-as / copy / incremental / save / revert sequence crashed Blender (`EXCEPTION_ACCESS_VIOLATION` re-reading `a1.blend`); 5.2.1 finishes. Tests run revert in a fresh process or before any incremental save (C15) | `a1.crash.txt`, REVCHK runs |
| `recover_file(mode="LAST_SESSION")` | identical on both and DANGEROUS headless: reopens Blender's real quit.blend from the user's last session (it loaded an unrelated B: file into the harness once). Tests assert only the BOGUS-mode error and the AUTOSAVE missing-file error; never call LAST_SESSION from a test or batch process | Addon Dev L3 fact, Lead harness safety rule 2026-09-11 |
| `describe/get/set_settings`, presets | `use_gtao`, `use_hdr_view` absent on 5.2; everything `bl_rna`-driven; `load_preset` reports unknown keys as `unset` | scheck diff |
| `describe_settings("PREFS_FILEPATHS")` | `save_modified_images`, `texture_cache_directory` exist only on 5.2; auto-listed | scheck diff |
| `append_from_blend`, `link_from_blend`, `list_only` | `libraries.load` keyword-only, 5.2 adds five kwargs; pass every argument by name | `libraries_load` |
| `set_color_management` | `view_transform` enum reads `["NONE"]` headless on both; try-assign accepts Standard / AgX / Filmic / Khronos PBR Neutral / Raw on both; the live tuple in the error differs (5.2.1 adds `ACES 1.3`, `ACES 2.0`; 4.3.2 has `Filmic Log`): quote the exception text | `view_transform` |
| `set_render_device`, `list_render_devices` | `compute_device_type` enum empty headless on both; try-assign accepts NONE/CUDA/OPTIX/HIP/ONEAPI, METAL refused on Windows; `get_devices()` returns None and fills `prefs.devices`; `scene.cycles.device` is `CPU / GPU` | `cycles_devices` |
| `enable_addon`, `disable_addon` | C13 (`default_set=True` always); `preferences.is_dirty` stays True after a disable on both | `addons` |
| `set_project_profile` | nested dict id-prop round-trips as `IDPropertyGroup` with `to_dict()` on both | `scene_idprop` |
| add-on `register()` (autostart, `exit_pre`) | `load_post/load_pre/save_pre/save_post` on both; `bpy.app.handlers.exit_pre` 5.2.1 only, `hasattr`-guarded | `handlers` |
| `get_addon_settings` (API keys) | `AddonPreferences` with `StringProperty(subtype='PASSWORD')` registers headless on both | `addon_prefs` |

### 5.2 Claims NOT confirmed headless (do not build on them)

- "5.2 adds `All Libraries` and `Essentials` at `asset_libraries` indices 0 and 1": on both
  installs headless only `User Library` is listed. Tier 2 (`set_asset_libraries`) re-checks
  this in a GUI session; no B0 code reads that index (C6).

## 6. Testing (required before you report done)

Headless, `--background --factory-startup`, handlers called directly, using a temp dir:

1. `get_file_state` on the unsaved default: `is_saved` False, `is_dirty` True, `filepath` "".
2. `save_blend(tmp/a.blend, compress=True)` then `get_file_state`: saved, not dirty, the
   file is smaller than an uncompressed `save_copy(tmp/a_raw.blend, compress=False)`.
3. `add_primitive` cube; `load_blend(tmp/a.blend)` WITHOUT `force` returns the dirty
   error; with `force=True` loads and reports `unsaved_changes_discarded: True`.
4. `save_version(note="first")` twice: `a_v001.blend`, `a_v002.blend` and a sidecar with
   two entries; the working file path is unchanged.
5. `save_blend(incremental=True)` produces Blender's `a_001.blend` naming (or the
   documented 4.3 pattern; report which).
6. `new_file(empty=True)`: 0 objects; `revert_file` refuses (never saved); `load_blend`
   the temp file back.
7. `append_from_blend(tmp/a.blend, list_only=True)` lists the cube; appending it into a
   `new_file(empty=True)` scene yields 1 object.
8. `describe_settings("RENDER")` returns `resolution_x` with type INT and a range;
   `set_settings("RENDER", {"resolution_x": 1280, "engine": "BOGUS"})` sets the first and
   reports the second as unset with the valid engines listed.
9. `settings_snapshot("t")`, `set_render_quality("PREVIEW")`, `settings_restore("t")`:
   resolution percentage and samples are back to the snapshot values.
10. `save_preset("p1", scopes="RENDER,OUTPUT")`, change resolution, `load_preset("p1")`
    restores it; `list_presets` contains `p1` and the shipped defaults.
11. `set_color_management(view_transform="Standard")` succeeds by assignment even though
    the enum reads empty headless; `get_settings("COLOR")` reads `Standard` back.
12. `set_project_profile(engine_target="UNREAL", apply_units=True)`: `scene["blendermcp_profile"]`
    exists and `unit_settings.scale_length == 0.01`; `get_project_profile` reads it back
    after save + load.
13. `enable_addon("rigify")` returns bl_info; `list_addons(enabled_only=True)` includes it;
    `disable_addon("rigify")` removes it. Preferences NOT persisted (assert
    `preferences.is_dirty` is still whatever it was and `userpref.blend` mtime unchanged).
14. `save_preferences()` without `confirm` refuses. Do NOT call it with `confirm=True`
    in the test suite (it would overwrite the user's real preferences); assert the
    refusal only.
15. `set_addon_settings({"port": 9877})` then `get_addon_settings` shows 9877 and the
    Scene has no `blendermcp_port` property; a scene with a legacy API key property is
    migrated on `register()` and the scene property is blanked.
16. `save_session_state("t")`, change frame and selection, `restore_session_state("t",
    load_file=False)` restores both.
17. `set_server_settings({"output_dir": tmp})` (server-side test): a subsequent
    `render_from_camera` with no path writes under `tmp`.
18. `list_render_devices` returns a list (may be CPU-only headless) without error.

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.

## 7. Deploy

Identical to section 7 of the rigging request. The preferences migration means the user
must re-enter API keys once if the migration finds none in the open scene; say so in the
report. Do not launch Blender or deploy until told.

## 8. Live checklist (B0): Blender 5.2.1 GUI session, run by the user at the Lead's B0-G5 gate

Same rules as the Phase A checklist in `MIGRATION-blender-5.2.md`: every step has a PASS
condition, output is pasted back verbatim, agents run nothing here. Preconditions: B0-G3
green on both installs; the accepted add-on copied into
`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\addon.py`; the MCP server
reinstalled (`uv pip install -e .`) and the Claude Code session restarted.

### L7. Autostart on a cold launch (section 0.1)
1. Preferences > Add-ons > Blender MCP: tick `autostart_server`; Preferences are saved
   automatically or via `save_preferences(confirm=True)`. Quit Blender.
2. Launch `D:\blender-5.2.1-windows-x64\blender.exe` with no flags. Do not click
   anything.
3. Within 10 s, from the MCP client: `get_scene_info()`.
PASS: a scene reply without any click in Blender; the sidebar panel shows the server
running. Paste the reply's `file` and `settings_summary` keys.
4. Save the file while connected (`save_blend`), untick `autostart_server`, quit, relaunch
   and open that file.
PASS: the panel shows NOT running (the old `blendermcp_server_running` Scene property no
longer drives the display).

### L8. `start_blender(start_server=True)` path
1. `close_blender()` if a managed instance is running.
2. `start_blender(start_server=True)` (autostart preference OFF, so the `--python-expr`
   belt-and-braces path is what starts the server).
PASS: the reply reports the socket reachable within the timeout; `get_version()` returns
matching `addon` and `server` versions and `protocol_match: true`.
3. `start_blender(start_server=False)` with the preference OFF.
PASS: the timeout text names the two causes (preference off / add-on not enabled), not a
bare timeout.

### L9. Session state across a restart
1. Open a saved file, set frame 42, select two objects, switch to the Shading workspace,
   orbit the viewport, then `save_session_state()`.
2. `close_blender()`, then `start_blender(restore_session="last")`.
PASS: the same file is open, frame 42, the two objects selected, workspace Shading, the
viewport view restored; the reply lists nothing under "could not restore". Rename one of
the two objects, save, repeat: the reply names the missing object under "could not
restore" and everything else is restored.
