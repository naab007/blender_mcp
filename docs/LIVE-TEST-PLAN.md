# Live test plan for the deployed Blender MCP 2.2.0 (Blender 5.2.1 GUI)

Written 2026-09-11 by Grace (Planner/API) for the agent that runs the live tests. You have:
the deployed MCP server (tools `mcp__blender__*`), the deployed add-on inside a running
Blender 5.2.1 GUI, and nothing else. You do not edit code, you do not deploy, you do not
open any file that is not created by these steps.

## Preconditions (the Lead confirms these before handing this file over)

- Deployed build = branch `blender-5.2`, version 2.2.0: `addon.py` git hash-object
  `f4dc37b10ee09f1d3277a0922da396c6b3b3e129` (A1.2, 14:25; the first live pass ran on
  `37d2211d` and fixed five capture/playblast/session defects, see PLAN.md D37 and
  `docs/LIVE-TEST-REPORT-2026-09-11.md`), `src/blender_mcp/server.py` `6f24904908b2593147ed2240e3ad96c22ff5f0ed`,
  `src/blender_mcp/__init__.py` `f76c47d1e90d7ad249d8ffa21341b7d59c129a0c`, `src/blender_mcp/settings.py`
  `2bbc200bed65028afbf2a79927ca2fd248320e09`, `src/blender_mcp/presets_default.json`
  `0a58e8c3218ee536badfc35d2e4d8ba4066c577a`, `pyproject.toml` `0197888a` (2.2.0). S1 below proves the
  deployed pair agrees (both 2.2.0, protocol 1).
- DEPLOYED 2026-09-11 14:01 (copy only, every target file re-hashed = source): the MCP
  server the Claude Code config launches lives at `B:\-AI-Stuff-\-=MCP-Servers=-\blender_mcp\`
  (venv imports blender_mcp 2.2.0, PROTOCOL 1, 169 tools); the add-on is at
  `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\addon.py` (and the 4.3 folder).
  You must be in a NEW Claude Code session (the MCP server process restarts to load 2.2.0)
  and Blender 5.2.1 must be a fresh launch after enabling "Blender MCP" once in
  Preferences; autostart then opens 127.0.0.1:9876. S1 fails with a version mismatch if
  either restart was skipped: report it, do not work around it.
- The MCP server was started from a session whose environment has
  `BLENDER_EXE=D:\blender-5.2.1-windows-x64\blender.exe` (or the settings file names it).
- Blender 5.2.1 GUI is running with the add-on installed in
  `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\addon.py` and ENABLED
  (Edit > Preferences > Add-ons > "Blender MCP"), and its socket server is started
  (autostart preference, default on since 2.1.0, or the N panel > BlenderMCP > Connect).
- Tier 2 rigging tools (Rigify, NLA, shape keys, drivers, weight ops) are NOT deployed in
  this build; L13 is skipped by design.

## What you must NOT do

- Do not edit any file in the repo or the deployed folders, do not run git (no commit,
  no checkout, no stash), do not deploy or copy files, do not change Blender preferences
  except through the tool calls written below (they restore what they change).
- Do not open, save over, or delete any `.blend` file that these steps did not create;
  everything you save goes under the temp directory with an `lt_` prefix.
- Do not call `recover_file` with `mode="LAST_SESSION"` (it opens the user's real last
  session file).

Rules:
- Run the steps in order. Every step names the exact tool call, its PASS condition, and
  what to paste back. Paste replies VERBATIM; never summarise a reply into "it worked".
- Report format, one block per step, posted to the Lead (Ada) as one message per section:
  `L<n>.<k>: PASS|FAIL` then the pasted reply (or the error text). An image reply is
  reported as `IMAGE: <one sentence naming what is visible>` plus the numeric check where
  one is given. FAIL is not a reason to stop; continue and report everything.
- Do not weaken a PASS condition to make a step pass. If a tool named here does not exist
  in your tool list, report `MISSING TOOL <name>` for that step and continue.
- Every Blender-side check goes through `execute_blender_code(code=...)`; paste its printed
  output. `bpy` is already imported there.
- Fixture names used below start with `lt_`; nothing else in the scene is touched.

Section map: S = setup, L1-L6 = Phase A migration (M14), L7-L9 = Phase B0 settings, L10-L13
= Phase B1 rigging and animation, R = rigging smoke through the tools.

## S. Setup and identity (run first)

S1. `get_version()`
PASS: prints a server line `BlenderMCP server 2.2.0 (protocol 1)`, an add-on line with
`2.2.0 (protocol 1)` and `Blender 5.2.1`, and `Compatibility: OK`. Paste all lines.
FAIL if the add-on line says `unreachable`, `pre-2.1`, or the Compatibility line says
`MISMATCH`: paste it and continue (many later steps will then fail; that is the finding).

S2. `get_blender_status()`
PASS: the process/socket report says reachable on 127.0.0.1:9876. Paste it.

S3. `get_scene_info()`
PASS: a scene reply that contains the keys `file` and `settings_summary`. Paste those two
keys' values only.

S4. `get_file_state()`
PASS: reply lists `filepath`, `is_saved`, `is_dirty`, `blender_version` (5.2.1). Paste it.

S5. Build the fixture scene (default startup scene is assumed: Cube, Camera, Light):
- `add_primitive(primitive_type="cylinder", location="3,0,1", name="lt_cyl")`
- `add_primitive(primitive_type="cube", location="0.5,0.5,0.5", size=1.0, name="lt_cutter")`
- `create_camera(name="lt_cam", location="6,-6,4", look_at="0,0,0")`
- `set_active_camera(name="lt_cam")`
PASS: four success replies. Paste the four replies.

## L1. Legacy add-on running on the 5.2.1 GUI

L1.1. `execute_blender_code(code="import bpy, addon_utils; print([m.__name__ for m in addon_utils.modules() if 'addon' in m.__name__.lower() or 'mcp' in m.__name__.lower()]); print(sorted(k for k in bpy.context.preferences.addons.keys()))")`
PASS: the printed enabled list contains the MCP add-on module. Paste both lines.

L1.2. `execute_blender_code(code="import bpy; s=getattr(bpy.types,'blendermcp_server',None); print('server object:', s is not None, 'running:', getattr(s,'running',None), 'host:', getattr(s,'host',None), 'port:', getattr(s,'port',None)); print('scene props:', [p for p in ('blendermcp_port','blendermcp_server_running') if hasattr(bpy.context.scene, p)])")`
PASS: `server object: True running: True host: 127.0.0.1 port: 9876` and `scene props: []`
(the old Scene properties must be gone). Paste both lines.

L1.3. `get_addon_settings()`
PASS: reply shows `port` 9876 and `autostart_server` True; API keys are never echoed
(shown as set/unset flags only). Paste it.

## L2. Still PNG without media_type, then FFMPEG with it (migration break #4)

L2.1. `execute_blender_code` with this code (paste exactly):
```
import bpy, os, tempfile
r = bpy.context.scene.render
ims = r.image_settings
print("media_type present:", hasattr(ims, "media_type"), "default:", getattr(ims, "media_type", None))
ims.file_format = 'PNG'
p = os.path.join(tempfile.gettempdir(), "lt_still.png")
r.filepath = p
bpy.ops.render.render(write_still=True)
print("still written:", os.path.exists(p), os.path.getsize(p) if os.path.exists(p) else 0)
try:
    ims.file_format = 'FFMPEG'
    print("FFMPEG without media_type: accepted ->", ims.file_format)
except Exception as e:
    print("FFMPEG without media_type: REJECTED:", str(e)[:120])
if hasattr(ims, "media_type"):
    ims.media_type = 'VIDEO'
    ims.file_format = 'FFMPEG'
    print("FFMPEG with media_type=VIDEO ->", ims.file_format, r.ffmpeg.format)
    ims.media_type = 'IMAGE'
    ims.file_format = 'PNG'
    print("restored ->", ims.media_type, ims.file_format)
```
PASS: `still written: True <n>` with n > 0; the with-media_type line ends in `FFMPEG`;
the restored line prints `IMAGE PNG`. The without-media_type line is recorded either way
(headless it is REJECTED; the GUI result decides the docs wording). Paste all lines.

## L3. Viewport and render tools through the server (GUI only)

L3.1. `get_viewport_screenshot()`
PASS: an image showing the default cube, `lt_cyl` and the camera. Report `IMAGE: ...`.

L3.2. `capture_viewport_angle(angle="iso_front_right")`
PASS: image of the scene from an isometric angle. Report `IMAGE: ...`.

L3.3. `capture_contact_sheet()`
PASS: a tiled image with four labelled views. Report `IMAGE: ...` naming the labels.

L3.4. Before-counts: `execute_blender_code(code="import bpy; print('scenes', len(bpy.data.scenes), 'node_groups', len(bpy.data.node_groups), 'use_pass_z', bpy.context.view_layer.use_pass_z)")`
Paste the line.

L3.5. `render_depth_map(max_depth=25.0)`
PASS: an image where nearer objects are lighter than the background. Report `IMAGE: ...`.

L3.6. After-counts: repeat the L3.4 call.
PASS: `scenes` and `node_groups` counts equal the L3.4 values and `use_pass_z` is
unchanged. Paste the line.

L3.7. `render_from_camera(camera_name="lt_cam", width=640, height=360, samples=8)`
PASS: an image of the scene from `lt_cam`. Report `IMAGE: ...`.

L3.8. `set_render_settings(engine="BLENDER_EEVEE")`
PASS: the reply's `engine` is `BLENDER_EEVEE`. Paste it. Then
`execute_blender_code(code="import bpy; print(bpy.context.scene.render.engine)")` must
print `BLENDER_EEVEE`.

L3.9. `set_render_settings(engine="eevee")` then `set_render_settings(engine="BOGUS")`
PASS: the first resolves to `BLENDER_EEVEE`; the second is an error whose text lists
`BLENDER_WORKBENCH` and `CYCLES` among the valid ids. Paste both replies.

L3.10. `boolean_operation(target_name="Cube", cutter_name="lt_cutter", operation="DIFFERENCE", solver="FAST", apply=True)`
PASS: the reply says the solver used is `FLOAT` and the operation was applied
(`lt_cutter` is gone). Paste it.

L3.11. `boolean_operation(target_name="Cube", cutter_name="lt_cyl", solver="BOGUS", apply=False)`
PASS: an error listing `FLOAT`, `EXACT`, `MANIFOLD`; then
`execute_blender_code(code="import bpy; print([m.type for m in bpy.data.objects['Cube'].modifiers])")`
prints a list WITHOUT a BOOLEAN entry (no modifier left behind). Paste both.

## L5. start_blender launch paths (the managed instance replaces the GUI you are in)

Read first: L5 closes and relaunches Blender. Run L5 AFTER every other section, or
skip it if the Lead told you the GUI must stay up. Steps L5.x assume the server's
settings resolve to `D:\blender-5.2.1-windows-x64\blender.exe` (S1 already proved the
add-on runs on 5.2.1).

L5.1. `close_blender()`
PASS: reply says the instance closed. Paste it.

L5.2. `start_blender(background=False, wait_for_addon=True, start_server=True)`
PASS: a PID is returned and the socket becomes reachable within the timeout (the add-on
autostarts on a normal launch since 2.1.0). Paste the reply. Then S1 again: PASS is the
same `Compatibility: OK`.

L5.3. `close_blender()`, then
`start_blender(background=True, python_expr="import bpy; print('expr ok', bpy.app.version_string)", wait_for_addon=False)`
PASS: the reply names the log file; then
`execute_blender_code` cannot reach a background instance, so instead paste the reply
and, if the log path is on your machine, the log line containing `expr ok 5.2.1`.

L5.4. `close_blender()`, then `start_blender(background=False, wait_for_addon=True)`
to get a GUI back for the remaining sections. PASS: S1 shows `Compatibility: OK`.

## L6. One-way file format (documentation fact, optional)

Only if a 4.3.2 install is available to you. `save_blend(filepath="<tempdir>/lt_52.blend")`,
then open that file in 4.3.2 by hand. PASS: 4.3.2 refuses or warns; paste the message.
Otherwise report `L6: SKIPPED (no 4.3.2)`.

## L7. Autostart on a cold launch (B0, settings section 0.1)

L7.1. `set_addon_settings(values='{"autostart_server": false}', persist=True)`
PASS: reply shows `autostart_server` false and `persisted` true. Paste it.

L7.2. `close_blender()`, then `start_blender(background=False, wait_for_addon=True, start_server=False)`
PASS: the reply is a timeout whose text names BOTH real causes (the add-on is not enabled
in Preferences, or the autostart preference is off and nobody clicked Connect) and the
host:port. Paste the whole text. Blender stays open with no server.

L7.3. In the Blender window that just opened: sidebar (N) > BlenderMCP tab > click
Connect/Start. Then `get_version()`.
PASS: `Compatibility: OK`. Paste it.

L7.4. `set_addon_settings(values='{"autostart_server": true}', persist=True)`
PASS: `autostart_server` true, `persisted` true. Paste it.

L7.5. `close_blender()`, then `start_blender(background=False, wait_for_addon=True, start_server=False)`
PASS: socket reachable within 10 s WITHOUT any click (the preference alone started it).
Paste the reply and the elapsed time it reports.

L7.6. `save_blend(filepath="<tempdir>/lt_session.blend")` while connected, then
`execute_blender_code(code="import bpy; print('scene has server flag:', 'blendermcp_server_running' in bpy.context.scene.keys() or hasattr(bpy.context.scene,'blendermcp_server_running'))")`
PASS: `False` (the running state is no longer saved into the file). Paste both.

## L8. start_blender(start_server=True) path with the preference OFF

L8.1. `set_addon_settings(values='{"autostart_server": false}', persist=True)`, then
`close_blender()`, then `start_blender(background=False, wait_for_addon=True, start_server=True)`
PASS: socket reachable within the timeout (the `--python-expr` hook started it, not the
preference); `get_version()` shows `Compatibility: OK`. Paste both replies.

L8.2. `set_addon_settings(values='{"autostart_server": true}', persist=True)` to restore.
Paste the reply.

## L9. Session state across a restart

L9.1. `load_blend(filepath="<tempdir>/lt_session.blend", force=True)`, then
`set_frame_range(current=42)`, then
`select_objects(names="Cube,lt_cyl")` (if that tool is present; otherwise
`execute_blender_code(code="import bpy; [o.select_set(o.name in ('Cube','lt_cyl')) for o in bpy.context.view_layer.objects]; print('selected')")`),
then `execute_blender_code(code="import bpy; bpy.context.window.workspace = bpy.data.workspaces['Shading']; print(bpy.context.window.workspace.name)")`,
then `save_session_state(name="lt")`.
PASS: the session reply lists file, frame 42, selection, workspace Shading. Paste it.

L9.2. `close_blender()`, then `start_blender(background=False, wait_for_addon=True, restore_session="lt")`
PASS: the reply says the session was restored with `file_loaded` true and nothing under
"could not restore"; then `execute_blender_code(code="import bpy; print(bpy.data.filepath, bpy.context.scene.frame_current, sorted(o.name for o in bpy.context.selected_objects), bpy.context.window.workspace.name)")`
prints the lt_session path, `42`, `['Cube', 'lt_cyl']`, `Shading`. Paste both.

L9.3. `rename_object(old_name="lt_cyl", new_name="lt_cyl_renamed")`, `save_blend()`, then
`save_session_state(name="lt2")`, then `execute_blender_code` to rename it back
(`bpy.data.objects['lt_cyl_renamed'].name='lt_cyl'`) and `save_blend()`; then
`restore_session_state(name="lt2", load_file=True, force=True)`
PASS: the reply names `lt_cyl_renamed` under "could not restore" (or `not_restored`) and
restores everything else. Paste it.

## R. Rigging and animation smoke through the tools (B1, headless-equivalent, run before L10)

R1. `create_armature(name="lt_rig", location="0,0,0", bones='[{"name":"upper","head":[0,0,0],"tail":[0,0,1]},{"name":"fore","head":[0,0,1],"tail":[0,0,2],"parent":"upper","connected":true},{"name":"hand","head":[0,0,2],"tail":[0,0,2.5],"parent":"fore","connected":true}]')`
PASS: reply names `lt_rig` with 3 bones. Paste it.

R2. `get_armature_info(armature="lt_rig")`
PASS: 3 bones, `fore` and `hand` have `connected` true and the right parents. Paste the
bones list.

R3. `add_primitive(primitive_type="cylinder", location="0,0,1.25", name="lt_skin")`, then
`execute_blender_code(code="import bpy; o=bpy.data.objects['lt_skin']; bpy.context.view_layer.objects.active=o; bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT'); bpy.ops.mesh.subdivide(number_cuts=4); bpy.ops.object.mode_set(mode='OBJECT'); print(len(o.data.vertices))")`
(a default cylinder has only two cap rings; the middle bone would get no vertices), then
`bind_armature(mesh="lt_skin", armature="lt_rig", method="AUTO")`
PASS: reply lists 3 vertex groups with non-zero counts and an Armature modifier. Paste it.

R4. `get_vertex_groups(mesh="lt_skin")` then `find_unweighted_vertices(mesh="lt_skin")`
PASS: three groups with counts > 0; unweighted count 0. Paste both.

R5. `set_pose(armature="lt_rig", bones='{"upper": {"rotation": [45, 0, 0]}}', rotation_mode="XYZ")` then `get_pose(armature="lt_rig", bones="upper")`
PASS: the set reply reports the rotation_mode change; get_pose reads 45 degrees on X.
Paste both. Then `reset_pose(armature="lt_rig")`: PASS reply success; paste.

R6. `add_constraint(owner="lt_rig", bone="hand", constraint_type="IK", params='{"chain_count": 2}')` then `get_constraints(owner="lt_rig", bone="hand")`
PASS: one IK constraint with chain_count 2. Paste both.

R7. `set_keyframes(target="lt_rig", bone="upper", data_path="rotation_euler", keys='[[1,[0,0,0]],[10,[45,0,0]]]')` then `get_animation_info(target="lt_rig")`
PASS: the info lists 3 rotation F-curves with 2 keys each and an action; on 5.2.1 the
reply also names the action slot (`OBlt_rig`). Paste both.

R8. `bake_action(armature="lt_rig", start=1, end=10)` then `get_animation_info(target="lt_rig")`
PASS: F-curves exist on all three bones after the bake. Paste the second reply.

R9. `export_object(name="lt_skin", file_format="fbx", include_hierarchy=True)` then
`execute_blender_code(code="import os; p=r'<filepath from the reply>'; print(os.path.exists(p), os.path.getsize(p))")`
PASS: the reply's `exported_objects` includes `lt_rig` and `lt_skin`; the file exists
with size > 0. Paste both.

R10. `export_object(name="lt_skin", file_format="glb", include_hierarchy=True)` then
`import_file(filepath="<that .glb>")`
PASS: the import reply lists an armature under `armatures`, an action under `actions`,
and `bone_shape_objects` is EMPTY (the importer's Icosphere is suppressed). Paste it.

## L10. Weight map capture (B1)

L10.1. `render_weight_map(mesh="lt_skin", group="fore", angle="front")`
PASS: an image in weight-paint colours (blue to red) with the middle band warm. Report
`IMAGE: ...`. Then `execute_blender_code(code="import bpy; print(bpy.context.mode, bpy.context.view_layer.objects.active.name)")`
must print `OBJECT` and the object that was active before the call. Paste it.

L10.2. `find_unweighted_vertices(mesh="lt_skin", render=True, angle="front")`
PASS: an image (the same tool returns numbers with `render=False`, an image with
`render=True`) showing the unweighted selection highlighted, or a plain edit-mode view
when the count is 0, and the mode restored as in L10.1 (run the same
`execute_blender_code` mode line). Report `IMAGE: ...` and paste the mode line.

## L11. Playblast: OpenGL viewport path and video (B1)

L11.1. `playblast(start=1, end=10, step=3, camera="lt_cam", max_size=400)`
PASS: a labelled contact sheet of 4 frames showing the rig moving (the R7 keys). Report
`IMAGE: ...` naming the frame labels; then
`execute_blender_code(code="import bpy; print(bpy.context.scene.frame_current)")` prints
the frame that was current before the call. Paste it.

L11.2. `playblast(start=1, end=10, video_path="<tempdir>/lt_playblast.mp4", max_size=400)`
PASS: the reply names the MP4 and `execute_blender_code(code="import os; p=r'<tempdir>/lt_playblast.mp4'; print(os.path.exists(p), os.path.getsize(p))")` prints `True <n>` with n > 0; then the L2-style check
`execute_blender_code(code="import bpy; ims=bpy.context.scene.render.image_settings; print(ims.media_type, ims.file_format)")`
prints `IMAGE PNG` (settings restored). Paste all three.

## L12. Bones-in-front overlay on captures (B1)

L12.1. `capture_viewport_angle(angle="front", overlay="bones_in_front")`
PASS: an image where the three bones are drawn through the cylinder. Report `IMAGE: ...`.
Then `execute_blender_code(code="import bpy; o=bpy.data.objects['lt_rig']; print(o.show_in_front, [ (a.spaces[0].overlay.show_bones) for a in bpy.context.screen.areas if a.type=='VIEW_3D'])")`
prints the values that were in effect BEFORE the call (the tool restores them; on a
fresh scene: `False [True]`). Paste it.

L12.2. `capture_contact_sheet(overlay="wireframe")`
PASS: a tiled wireframe image. Report `IMAGE: ...`.

## L13. Rigify generation (B1 Tier 2): SKIPPED in this build

Tier 2 was ruled out of this run (PLAN.md 10.6, 13:56). Report `L13: SKIPPED (Tier 2 not
deployed)`. If `add_rigify_metarig` unexpectedly appears in your tool list, report that
as a finding and still skip.

## Cleanup and report

C1. `execute_blender_code(code="import bpy; [bpy.data.objects.remove(o, do_unlink=True) for o in list(bpy.data.objects) if o.name.startswith('lt_')]; print(sorted(o.name for o in bpy.data.objects))")`
Paste the remaining object list.

C2. Post the report to the Lead: sections S, L1, L2, L3, L5, L6, L7, L8, L9, R, L10,
L11, L12, L13, C in that order, each step as `L<n>.<k>: PASS|FAIL` + pasted output.
The Lead turns PASS/FAIL into the "verified live on 5.2.1" tables; nothing here is
edited by you.
