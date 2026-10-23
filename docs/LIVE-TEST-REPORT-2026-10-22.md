# Live test report: Blender MCP 2.4.0 (B2-G5) on Blender 5.2.1 LTS and 4.3.2 (2026-10-22, re-probe 2026-10-23)

Run by QA (team lohjud, Margaret) against `docs/TEST-PLAN-B2.md` section 5 and the Lead's GO
(m_46fe3afa). TWO add-on hashes appear in this report, and every check names the one it ran at:
- G5 list (section "Results"): the G3-frozen pair, re-hashed before each GUI start and after the last close:
  addon.py `ee9b9dc0d0ec202712d8685f398cad0215201e68`, src/blender_mcp/server.py `a2cdd77f46ac761d6bd402b4286ad00cd75d253e`,
  __init__ 9ce248f3, pyproject 45b5c465, settings.py f877cf66. Nothing was edited during that run.
- Re-probe (section "Re-freeze re-probe", 2026-10-23): the RE-FROZEN pair after the four live findings were fixed
  (R-B2-43/44/45): addon.py `2a97736c503ecca1c6428d70a772052788bee4f9`, src/blender_mcp/server.py `8e3ca441`,
  tests b2_steps ba354830 / drv_steps a45e37c4 / test_b2_server b70cbc20; headless 253/253 both installs, venv 131.
Helper files (new, QA-owned): `tests/live_register.py` b47a716f, `tests/live_client.py` 6211aa3c.

Mechanics (R-B2-25 / R-B2-31): a SEPARATE GUI instance per build, started by QA from the dev tree
(`<install>\blender.exe --factory-startup --python tests\live_register.py`), the dev add-on importlib-loaded, prefs
entry created, port 9877, autostart off, `ensure_server(9877)`; driven through the repo `.venv` server over MCP stdio
(`tests/live_client.py`, env `BLENDER_PORT=9877 BLENDER_HOST=127.0.0.1 BLENDER_EXE=<install> BLENDER_MCP_SETTINGS_DIR=<temp>`).
The user's deployed pair (port 9876, `%APPDATA%`, `B:`) and `~/.blender_mcp/settings.json` (mtime 2026-09-11, untouched)
were never used; the only other MCP process on the box (`B:\...\blender-mcp.exe`, the user's own) was left alone.
Registration lines: 5.2.1 `LIVE_REGISTER {"bl_info_version": [2, 4, 0], "blender": "5.2.1 LTS", "background": false,
"ensure_server": {"port": 9877, "running": true, "started_now": true}}` (pid 16436); 4.3.2 the same with
`"blender": "4.3.2"` (pid 28316). `get_version` on both: server 2.4.0 / add-on 2.4.0 / protocol 1 / Compatibility OK,
settings file = the temp dir. Startup note (user): a GUI Blender takes a while to settle and shows the splash until an
input event; QA dismissed it with one synthetic click posted to the QA window only (own pid, `PostMessage`).

Evidence: `<scratchpad>/live52/` and `<scratchpad>/live43/` (plan_*.json, plan_*.log with every reply, *.png
screenshots, blender_stdout.log with the undo-history dumps, exports/). Replies below are trimmed; the logs hold them
in full.

## Results (PASS unless noted; "both" = 5.2.1 and 4.3.2 identical)

| Step | Build | Result | Evidence (reply, trimmed) / screenshot |
|---|---|---|---|
| S1 get_version | both | PASS | `BlenderMCP server 2.4.0 (protocol 1) ... Blender add-on: 2.4.0 (protocol 1) on Blender 5.2.1 LTS, Python 3.13.13` / `4.3.2, Python 3.11.9`; `protocol_match: true`; `Compatibility: OK` |
| S2 fixtures | both | PASS | execute_blender_code: `lt_Gun` (UV sphere, scale 2,1,1), `lt_Open` (cube minus one face), `lt_Cyl`, `lt_Cone`, Camera; UVs on every mesh |
| L1 find_mesh_issues(lt_Open, NON_MANIFOLD, render=True) | both | PASS | image 800x452 stddev 46.19 (5.2.1) / 800x448 stddev 40.26 (4.3.2): the open cube with the 4 hole edges highlighted, `L1_render.png`; mode/active/selection after = `OBJECT / Camera / [Camera]` (unchanged) |
| L2 export_for_engine(TIMBERMESH) | 5.2.1 | PASS (teach) | with a `#` root: `Error: the Timbermesh add-on (timbermesh_blender_plugin) could not be enabled on this Blender: No module named 'timbermesh_blender_plugin'; install it from https://github.com/mechanistry/timbermesh and retry`; without a `#` root the S1b refusal fires first |
| L2 export_for_engine(TIMBERMESH) | 4.3.2 | PASS (file) | `Exported TIMBERMESH -> ...\_lt_Cyl.timbermesh (3211 bytes, 1 objects, 124 tris); manifest: ..._lt_Cyl.timbermesh.manifest.json; validation: skipped`; manifest `exporter: timbermesh`, kwargs merge_meshes/single_animation. Side effect = F-B2-7 (frame 0) |
| L3 ship-an-asset chain | both | PASS | validate_asset(lt_Gun, UNREAL, fix=True) `verdict WARN` (origin_bottom_center only) -> generate_lods ratios 0.5,0.25 `levels 960/480/240 tris`, lod0 `lt_Gun_LOD0` -> create_collision CONVEX `UCX_lt_Gun(_LOD0)_00 960 tris, volume_ratio 1.0` -> add_socket `SOCKET_Muzzle` parent lt_Gun_LOD0, `location_world [2,0,1]`, display 0.3 -> build_export_hierarchy `SM_lt_Gun` tree `[LOD0 {SOCKET_Muzzle, UCX}, LOD1, LOD2]`, `lod_group` props -> export_for_engine UNREAL `158668 bytes, 6 objects, 2640 tris`, manifest (6 objects, fbx kwargs), `validation: WARN` (scale_length 1.0 hint, UCX no UV). Viewport `L3g_shot.png` (1000x566/560) shows the sphere, LODs, the WIRE proxy and the socket; `L3h_sheet.png` 4012x1000 four labelled views; `L3i_angle.png` iso; scene listing: UCX `display_type WIRE`, socket `empty_display_size 0.3` |
| L4 reads with an object in EDIT mode (R-B2-36) | both | PASS | lt_Cone put in EDIT_MESH in the GUI; get_mesh_stats(lt_Open) `non_manifold_edges 4, boundary_edges 4`; get_mesh_stats(lt_Cone) `triangles 0, triangles_simple 62` (E20: the evaluated mesh of an EDIT-mode object is empty, GUI too); validate_asset(lt_Cone, fix=False) `WARN`; find_mesh_issues(lt_Open, NON_MANIFOLD) `count 4`; after all four: `("EDIT_MESH", "lt_Cone", ["lt_Cone"])`; `L4g_shot.png` (4.3.2 copy blank because of F-B2-6, not L4) |
| L5 transaction BEGIN -> set_pivot -> ROLLBACK (R-B2-42) | both | PASS | before `[[0,5,1.98], minz -1.0]` (4.3.2: `[[0,5,1], -1]`); set_pivot TOP_CENTER `new_location [0,5,2.98]`, after `minz -2.0`; STATUS `open true`; ROLLBACK `undo_calls 2, marker_present false`; restored `[[0,5,1.98], -1.0]`. Undo history (`print_undo_steps` + CRT fflush into blender_stdout.log): `mcp:lt_pivot2:pre` (active after the rollback), `mcp:lt_pivot2`, `mcp:lt_pivot2:rollback` (5.2.1 lines 264-267); 4.3.2 `mcp:lt_pivot:pre / mcp:lt_pivot / mcp:lt_pivot:rollback` (lines 9-11). Screenshots before/after identical (origin-only change) |
| L6 call_operator view3d.view_all, area/region context (D-G5) | both | PASS | `{"result": ["FINISHED"], "context_applied": {"area": "VIEW_3D", "region": "WINDOW"}, "elapsed_ms": 16.05 / 59.63}`; the viewport re-frames (`S4_shot.png`, `L6_shot.png`) |
| L7 transaction rollback visible in the GUI (D-G5) | both | PASS | BEGIN lt_move2 -> rna_set `OBJECT:lt_Open/location [5,0,6]` (`matrix_world.translation` 6; 5.2.1 `D4b_shot.png` shows the cube above its row) -> ROLLBACK `undo_calls 2` -> location back to the pre-BEGIN value (`[5,0,3]` on 5.2.1, `[5,0,1]` on 4.3.2), `D4d_shot.png` identical to the baseline; undo history `mcp:lt_move2:pre / mcp:lt_move2 / mcp:lt_move2:rollback`. 4.3.2 screenshots are blank for this row (F-B2-6, the view had been left at a capture framing); the values and the undo dump carry the check |
| L8 batch of 50 rna_set in one round trip (D-G5) | both | PASS | `batch: 50 commands, 50 ok, 0 error, 0 skipped; rolled_back=False, undo_available=True, elapsed_ms=6.16` (4.3.2: 3.87); lt_Cone z after = 1.98; undo history `mcp:batch:pre` + `mcp:batch`; GUI responsive (screenshots after) |
| L9 rna_set(publish=True) msgbus delivery (D-G5) | both | PASS (fact) | subscriber on `(bpy.types.Object, "location")` + the instance key: count 0 -> publish=False set -> `["hit","hit"]` -> publish=True set -> `["hit","hit","hit","hit"]`. Delivery confirmed; FACT: in a GUI session Blender's RNA update publishes on its own, so publish=False delivers too (headless it does not, R18). Docs nuance only |
| L10 batch_export of a 3-root scene | both | PASS | UNITY: `FAIL lt_Cyl ... ['ngons']`, `FAIL lt_Cone ... ['ngons']`, `PASS lt_Open -> ...\lt_Open.fbx (10 tris)`, `batch_manifest.json` rows PASS/FAIL with the error text; GENERIC_FBX: 3 PASS rows (124 / 62 / 10 tris), 3 fbx + 3 per-file manifests + batch_manifest.json on disk (`exports/batch`, `exports/batch2`) |
| Captures on a real window (2.2.0 L3 regression) | both | PASS | get_viewport_screenshot 800x452 / 1000x566 non-blank; capture_viewport_angle iso 800x452 non-blank; capture_contact_sheet 4012x1000 four views non-blank (both builds) |
| Close | both | PASS | `close_blender(force=False)` -> `Blender closed (gracefully).`, Blender stdout `Blender quit`, `BLENDER EXIT 0`; tasklist: no blender.exe; no `blender_mcp.server` python left; port 9877 not listening; temp settings dirs removed. `close_blender(force=True)` first = F-B2-5 |

## Findings (all filed on the owner's card with a repro; the frozen pair is NOT reopened by QA)

- **F-B2-6 (addon, Linus; 4.3.2 GUI only; medium): the capture tools leave the user's viewport at the capture
  framing.** `capture_viewport_angle` (addon.py:1429-1441) saves/restores only `view_matrix` + `view_perspective`;
  on 4.3.2 assigning `view_matrix` does not write back `view_location/view_rotation/view_distance`, so the next
  redraw rebuilds the matrix from the capture framing. Bisect (plan_r, 4.3.2): view_all -> `(3.5,-3.5,4.0) dist 32.928`;
  after find_mesh_issues(render=True) -> `(3.5,-3.5,33.462) dist 3.466` (viewport empty); after capture_contact_sheet
  -> `(0,5,-1.486) dist 3.466`; after capture_viewport_angle -> `(3.5,-3.5,33.462) dist 3.466`. All callers
  (contact sheet :1530, find_mesh_issues render :2859, find_unweighted_vertices render :8158/8180) route through it.
  5.2.1 restores correctly. Fix shape verified live (plan_t): restoring `view_location, view_rotation, view_distance,
  view_perspective` brings `(3.5,-3.5,4.0)/32.928` back and the scene is visible again (`T3_shot.png`).
- **F-B2-7 (addon, Linus; 4.3.2 with the Timbermesh plugin; low): `export_for_engine(TIMBERMESH)` leaves
  `scene.frame_current` at 0** (plan_s: frame 1 -> UNREAL export -> 1 -> TIMBERMESH export -> 0). The plugin's
  single_animation export rewinds the frame; the add-on's render scope (addon.py:4708-4748) already restores
  camera + frame for renders and should wrap the plugin call too. Headless guard: E4 asserts frame_current unchanged.
- **F-B2-5 (server, Grace; low, wording): `close_blender(force=True)` on a Blender the server did not start
  reports `Blender closed (process terminated).` while nothing was terminated** (graceful path skipped by `force`,
  `_blender_process` None, reply text chosen by `elif pid or force`). Measured: the 5.2.1 instance stayed alive and
  listening 8 s later; `force=False` closed it (`gracefully`, exit 0). Fix: still send quit_blender, or return an
  error; never claim a termination that did not happen. Venv guard named on the card.
- **F-B2-4 (addon, Linus; both; low): `execute_blender_code(result_expr=<dict>)` returns the dict's keys only**
  (`{'a': 1, 'b': [1, 2]}` -> `["a", "b"]`; `[{'a': 1}, dict(b=2)]` -> `[["a"], ["b"]]`): `_encode_value`
  (addon.py:9116) has no dict branch. One `isinstance(value, dict)` branch; headless guard = a round-trip step.
- Facts for the oracle/docs (not defects): E20 holds in the GUI (EDIT-mode object -> `triangles 0`,
  `triangles_simple 62`); `rna_set(publish=False)` already reaches msgbus subscribers in a GUI session.

## Re-freeze re-probe (2026-10-23, addon 2a97736c / server 8e3ca441, restarted GUIs from the dev tree, port 9877)

Each instance read its own add-on hash back in-process (`V0_addon_hash` = 2a97736c) before any check. The user's
sessions were untouched (no blender.exe before each start; 0 after each close). Evidence: `<scratchpad>/reprobe43/`
and `<scratchpad>/reprobe52/` (plan_v.log, plan_mut.log, *.png, blender_stdout.log, exports/).

| Check | 4.3.2 | 5.2.1 | Evidence |
|---|---|---|---|
| F-B2-6 captures restore the view: view fields read before and after find_mesh_issues(render=True), capture_contact_sheet, capture_viewport_angle | PASS: `[loc (0,2.5,1), dist 20.797, PERSP]` identical after all three | PASS: `[(0,2.5,1), 20.611, PERSP]` identical after all three | `V2_view`..`V5_view`; images non-blank (800x448 stddev 41.3, 2412x600, 400x224); `V5_shot.png` shows the scene |
| F-B2-6 live RED (mut_f6 11b306b1 = Linus's scratch copy with the three-field restore removed, loaded into a 4.3.2 GUI, hash read back in-process) | RED as expected: view `(2.5,2.5,1)/12.131` -> `(9.595,-0.634,4.863)/3.466` after capture_viewport_angle, unchanged after the render capture (framing sticks) | n/a (5.2.1 derives the fields from the matrix) | `reprobe43/plan_mut.log` |
| F-B2-7 Timbermesh keeps the frame | PASS: `Exported TIMBERMESH -> ..._lt_Cyl.timbermesh (3211 bytes, 1 objects, 124 tris)`, frame_current 7 before and after | PASS (teach error, plugin absent): frame_current 7 before and after | `V6_timber`, `V6_frame` |
| F-B2-4 dict result_expr | PASS: `{"a": 1, "b": [1, 2], "c": {"x": 2.5}}`; `[{"n": 1}, [{"k": "v"}, 7]]` | PASS (same values) | `V7_dict`, `V7_dictlist` |
| F-B2-5 close_blender(force=True) on an unmanaged instance | PASS: `Blender asked to quit (not a process this server started; force applies only to managed processes).`; stdout `Blender quit`, `BLENDER EXIT 0`; 0 blender.exe; 9877 free | PASS (same text, exit 0) | `Z_close_force`, blender_stdout.log tail |
| Headless guards at the re-frozen pair | DRV-12c (dict / list of dicts / dict in a tuple / non-str keys) + B2-E4 frame assert PASS; 253/253 | DRV-12c PASS; E4 teach path PASS; 253/253 | `tests/run_b2_qa6_<ver>.log`; red at a scratch ee9b9dc0 copy (`red_reopen_<ver>.log`: 4.3.2 199/201, 5.2.1 154/155, exactly those steps) |
| RS-16 FakeConn close_blender case | venv 131 passed, 0 skipped at server 8e3ca441; red at the scratch server mutant 856bb054 (`AssertionError: []`, no quit_blender send) | same | `tests/test_b2_server.py` b70cbc20 |

Status of the four live findings after the re-probe: F-B2-4, F-B2-5, F-B2-6, F-B2-7 all FIXED at the re-frozen
pair and verified live on both builds (F-B2-7's file path only on 4.3.2, where the plugin exists).

## Not run / not verified

- Export files opened in the user's engines (TEST-PLAN-B2 s5.2 item 3): the files are in `live52/exports` and
  `live43/exports` (UNREAL `SM_lt_Gun.fbx`, UNITY/GENERIC batch sets, 4.3.2 `_lt_Cyl.timbermesh`); the engine import
  check is the user's to report.
- Timbermesh teach error on 4.3.2 (plugin present there, so only the file path was exercised); 5.2.1 covered the error.
- The interleaved user undo inside an open transaction, transactions longer than undo_steps, the 60 s batch wall,
  INVOKE on a real window: not in the G5 list; still open from D-G5.
- (Superseded 2026-10-23: the F-B2-4..7 fixes ARE re-tested live at the re-frozen pair, see "Re-freeze re-probe".)
- The G5 list items L1-L10 were not re-run at 2a97736c live; the headless harness (253/253 both installs) covers
  their add-on behaviour and the three hunks touch only the encoder, the capture finally and the Timbermesh export.

## Evidence files

`<scratchpad>` = `C:\Users\Naabin\AppData\Local\Temp\claude\D--App-Dev\a32750cc-eaf4-42ea-b11b-2605fe1c8d34\scratchpad`.
5.2.1: `live52/plan_a.log`, `plan_b.log`, `plan_c1.log`, `plan_d.log`, `blender_stdout.log` (undo dumps at lines 264-293),
`S3_shot.png`, `L1_render.png`, `C0b_shot.png` (splash gone), `L3g_shot.png`, `L3h_sheet.png`, `L3i_angle.png`,
`L4g_shot.png`, `L5c_shot.png`/`L5f_shot.png`, `L7b_shot.png`/`L7c_shot.png`, `D3d_shot.png`/`D3f_shot.png`,
`D4b_shot.png`/`D4d_shot.png`, `D5a_shot.png`/`D5b_shot.png`, `exports/`.
4.3.2: `live43/plan_full.log`, `plan_p.log`.. `plan_t.log` (bisects), `blender_stdout.log` (undo dumps at lines 5-19),
`S4_shot.png`, `L1_render.png`, `L3g_shot.png`, `L3h_sheet.png`, `L3i_angle.png`, `L4g_shot.png` (blank = F-B2-6),
`P3_shot.png`, `Q6_shot.png`, `R1_render.png`, `R3_sheet.png`, `R3_shot.png`, `R5_angle.png`, `R5_shot.png`,
`T2_angle.png`, `T3_shot.png` (view restored by the fix shape), `exports/`.
