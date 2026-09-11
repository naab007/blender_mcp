# Live test report: Blender MCP 2.2.0 on Blender 5.2.1 (2026-09-11)

Run by the live-testing agent against `docs/LIVE-TEST-PLAN.md`. Deployed build under test at the
start: addon.py `37d2211d`, server.py `6f249049`, __init__ `f76c47d1`, settings `2bbc200b`,
presets `0a58e8c3`, pyproject 2.2.0. Blender 5.2.1 LTS GUI launched through `start_blender`
(settings file `blender_exe` = `D:\blender-5.2.1-windows-x64\blender.exe`).

Five add-on defects were found live, fixed in the same session (A1.2, below), verified headless
185/185 on 4.3.2 AND 5.2.1, redeployed to the four targets, and re-tested live. Final add-on:
**`f4dc37b10ee09f1d3277a0922da396c6b3b3e129`** (server unchanged at `6f249049`). Nothing committed.

## Registration (done this session)

- MCPManager `server_reload("blender")`: manifest 92 -> 169 tools, endpoint
  `http://127.0.0.1:7796/mcp/blender` (already bridged through `~/.claude.json`).
- Add-on enabled in Blender 5.2 headlessly (`preferences.addon_enable(module='addon')` +
  `save_userpref`); `%APPDATA%\Blender Foundation\Blender\5.2\config\userpref.blend` created;
  `autostart_server` True.
- `~/.blender_mcp/settings.json` created with `blender_exe` = the 5.2.1 portable install.

## Results (PASS unless noted; every FAIL below was fixed and re-tested PASS)

| Step | Result | Evidence |
|---|---|---|
| S1 get_version | PASS | server 2.2.0 / add-on 2.2.0 on Blender 5.2.1 LTS, Python 3.13.13, protocol_match true, Compatibility OK |
| S2 get_blender_status | PASS | reachable on 127.0.0.1:9876 (addon responding), exe = 5.2.1 |
| S3 get_scene_info | PASS | `file` {filepath "", is_saved false, is_dirty false, version [5,1,16]}; `settings_summary` engine BLENDER_EEVEE, 1920x1080, fps 24, frames 1-250, METRIC, AgX |
| S4 get_file_state | PASS | filepath "", is_saved false, is_dirty false, blender_version "5.2.1 LTS", autosave_dir listed |
| S5 fixtures | PASS | lt_cyl, lt_cutter, lt_cam created, lt_cam active (first add_primitive of a 5-call parallel batch failed once with WinError 10054, see F6) |
| L1.1 add-on enabled | PASS | modules ['addon']; enabled list contains 'addon' |
| L1.2 server object | PASS | `server object: True running: True host: 127.0.0.1 port: 9876`, `scene props: []` |
| L1.3 get_addon_settings | PASS | port 9876, autostart_server True, keys all false, server_running True |
| L2.1 media_type | PASS | media_type present, default IMAGE; still written 1227788 bytes; FFMPEG without media_type REJECTED (`enum "FFMPEG" not found in ('AVIF', 'JPEG', ...)`); with VIDEO -> FFMPEG MKV; restored IMAGE PNG. **Docs: media_type is mandatory for video in the GUI too.** |
| L3.1 get_viewport_screenshot | PASS | 800x452 image of cube, cylinder, cameras |
| L3.2 capture_viewport_angle iso | **FAIL -> fixed (F1)** | 1x1 PNG before the fix; full-size iso view after |
| L3.3 capture_contact_sheet | **FAIL -> fixed (F1)** | four blank tiles before; four labelled views after |
| L3.4/L3.6 counts | PASS | `scenes 1 node_groups 0 use_pass_z False` before and after render_depth_map |
| L3.5 render_depth_map(25) | PASS | cube and cylinder lighter than the black background |
| L3.7 render_from_camera | PASS | 640x360 EEVEE render from lt_cam |
| L3.8 engine BLENDER_EEVEE | PASS | reply engine=BLENDER_EEVEE; scene prints BLENDER_EEVEE |
| L3.9 eevee / BOGUS | PASS | `eevee` -> BLENDER_EEVEE; BOGUS -> `valid: ['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES']` |
| L3.10 boolean FAST apply | PASS | `solver=FLOAT`, applied, lt_cutter gone |
| L3.11 boolean BOGUS | PASS | `valid: ['FLOAT', 'EXACT', 'MANIFOLD'] (FAST and FLOAT are accepted as aliases...)`; Cube modifiers `[]` |
| L5.2 start_blender GUI start_server=True | PASS | pid returned, addon ready |
| L5.3 background + python_expr | PASS | log line `expr ok 5.2.1 LTS`, process exited 0 |
| L5.4 GUI back | PASS | Compatibility OK |
| L6 one-way format | PASS (fact) | 4.3.2: `Error: Cannot read blend file '...lt_52_copy.blend', incomplete header, may be from a newer version of Blender` |
| L7.1 autostart off persist | PASS | autostart_server=False, persisted |
| L7.2 cold launch, no hook | PASS | 30 s timeout text names both causes and 127.0.0.1:9876 |
| L7.3 click Connect | NOT RUN | needs a human click; covered by L8 |
| L7.4/L7.5 autostart on, cold launch | PASS | socket reachable with no click |
| L7.6 no server flag in file | PASS | `scene has server flag: False` |
| L8.1 start_server=True with pref off | PASS | addon ready; get_version Compatibility OK |
| L8.2 restore pref | PASS | autostart_server=True, persisted |
| L9.1 save_session_state | PASS | file, frame 42, selection [Cube, lt_cyl], workspace Shading (a workspace switch applies on the next event-loop turn) |
| L9.2 start_blender(restore_session) | **FAIL -> fixed (F4)** | before: `Could not restore: ['workspace: no window in this session (headless)...']` in a GUI session; after: nothing under could-not-restore, workspace Shading restored |
| L9.3 restore with missing object | PASS | `Could not restore: ['selection: lt_cyl_renamed missing', 'active: lt_cyl_renamed missing']` |
| R1-R2 create_armature / info | PASS | 3 bones, fore and hand connected with the right parents |
| R3 bind_armature AUTO | PASS | modifier Armature, 3 groups (960-vert subdivided cylinder) |
| R4 groups / unweighted | PASS | upper 948, fore 960, hand 320 verts weighted; unweighted 0 |
| R5 set_pose / get_pose / reset | PASS | QUATERNION->XYZ reported; 45 deg on X read back; reset OK |
| R6 IK constraint | PASS | chain_count 2 read back |
| R7 set_keyframes / animation info | PASS | 3 rotation fcurves x 2 keys, action lt_rigAction, action_slot OBlt_rig |
| R8 bake_action 1-10 | PASS | action 'Action', 29 fcurves on upper/fore/hand |
| R9 FBX export | PASS | exported_objects lt_rig, lt_skin; 105404 bytes |
| R10 glb export + import | PASS | armature lt_rig.001, actions Action.001 + lt_rigAction.001, no Icosphere |
| L10.1 render_weight_map | **FAIL -> fixed (F1)** | heat map of `fore` after the fix; mode OBJECT + active restored |
| L10.2 find_unweighted render=True | **FAIL -> fixed (F1, F5)** | 1x1 before; after F1 it showed stale face highlights with 0 unweighted (F5); after F5 a plain edit-mode view; mode restored |
| L11.1 playblast camera=lt_cam | **FAIL -> fixed (F3)** | before: frames showed the viewport's own view; after: f1/f4/f7/f10 through lt_cam with the arm bending; frame restored |
| L11.2 playblast video max_size=400 | **FAIL -> fixed (F2)** | before: `height not divisible by 2 (400x225)`; after: MP4 written (`lt_playblast20001-0010.mp4`), settings restored IMAGE PNG 1920x1080 |
| L12.1 bones_in_front | **FAIL -> fixed (F1)** | bones drawn through the cylinder; show_in_front / show_bones / shading restored |
| L12.2 wireframe sheet | **FAIL -> fixed (F1)** | four wireframe tiles |
| L13 Rigify | SKIPPED | Tier 2 not deployed |
| C1 cleanup | PASS | objects left: Camera, Cube, Light |
| 4.3.2 GUI regression of F1 | PASS | captures 3173x1780 on 4.3.2, "Top Orthographic" label visible; front vs iso 24.8% pixels differ, non-blank |

## Findings and fixes (A1.2, addon.py only, hash f4dc37b1)

- **F1 (5.2.1 GUI): every named-angle capture wrote a 1x1 PNG.** `capture_viewport_angle` ran
  `wm.redraw_timer(type='DRAW_WIN_SWAP')` inside the area/region `temp_override`; on 5.2 the
  following `screen.screenshot_area` then writes a 1x1 image. Measured: plain and view-change
  captures are full size; the redraw inside the override alone breaks it; the same redraw
  outside the override is fine and the view is fresh (pixel-identical to a DRAW-redrawn
  capture). Fix: redraw outside the override, plus a 1x1 guard that retries once without any
  redraw and errors clearly if still tiny. Affected every tool on that path:
  capture_viewport_angle, capture_contact_sheet, render_weight_map,
  find_unweighted_vertices(render=True), the overlay captures.
- **F2: playblast video_path fails on odd frame sizes** (`height not divisible by 2` for
  max_size=400 -> 400x225). Fix: width and height rounded down to even (min 2).
- **F3: playblast(camera=...) on the OpenGL path drew the viewport's own view**, not the
  camera (`render.opengl(view_context=True)`; `scene.camera` alone changes nothing). Fix: when
  a camera is given, `region_3d.view_perspective = 'CAMERA'` for the capture; view matrix,
  distance and perspective restored afterwards.
- **F4: session restore at launch reported "no window in this session (headless)" in a GUI
  session.** `bpy.context.window` is None inside the timer right after `wm.open_mainfile`.
  Fix: `_gui_window()` helper falls back to `window_manager.windows[0]` when not in
  background; used by set_workspace and list_workspaces.
- **F5: find_unweighted_vertices(render=True) highlighted stale face selection** (vertex flags
  cleared, edge and face flags not; entering edit mode flushed the faces back). Fix: clear edge
  and polygon flags too.
- **F6 (not fixed, server side): one call in a 5-call parallel batch failed with
  `Connection to Blender lost: [WinError 10054]`** right after start_blender; two later
  deliberate 5-call parallel batches passed. `_blender_connection` is one shared socket with
  no lock around `send_command` in server.py; recommend a lock, or one reconnect-and-retry
  on 10054.
- **Minor, not fixed:** set_workspace's reply reports the workspace read immediately, which
  is still the old one (the switch applies on the next event-loop turn); the mesh's own
  edit-mode selection flags are not restored by the render=True capture (edit-mode scratch
  state); `get_animation_info` after bake names the action `Action` (nla.bake creates a new
  action; document it).

## Evidence files

`tests/report_a12_4.3.2.json`, `tests/report_a12_5.2.1.json`, `tests/run_a12_*.log`
(harness 185/185 both installs, hash f4dc37b1); venv `BLENDER_MCP_EXPECTED_VERSION=2.2.0`
64/64. The four venv failures without that variable are the stale 2.0.0 pins in
`tests/test_version_sync.py` and `test_server_units.py::test_versions_all_agree`.
