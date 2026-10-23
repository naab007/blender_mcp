# Tests.md: Blender MCP 2.4.0, Phase B2 engine readiness (team lohjud, QA Margaret, 2026-10-21 / 2026-10-22)

STATUS: B2-G3 FROZEN, B2-G5 LIVE DONE on both builds, pair RE-FROZEN 2026-10-23 after four live findings were fixed
(F-B2-4..7, R-B2-43/44/45). RE-FROZEN PAIR: addon.py `2a97736c503ecca1c6428d70a772052788bee4f9`, server.py `8e3ca441`,
tests b2_steps ba354830 (E4 frame assert) / drv_steps a45e37c4 (DRV-12c) / test_b2_server b70cbc20 (RS-16, 36 tests);
harness 253 steps, 253 PASS, 0 FAIL, EXIT 0 on BOTH installs (`tests/run_b2_qa6_<ver>.log`, head "run-time hashes:
addon 2a97736c b2_steps ba354830 drv_steps a45e37c4"); venv 131 passed, 0 skipped; tools/list IDENTICAL to the 2.4
json (204); settings --check match:true both (`tests/run_settings_check_g5_<ver>.log`). Live report (G5 list at the G3
pair + re-probe at the re-frozen pair): `docs/LIVE-TEST-REPORT-2026-10-22.md`. The G3 freeze rows below stay as the
record of the first freeze.
Frozen pair under test: `addon.py` `ee9b9dc0d0ec202712d8685f398cad0215201e68`, `src/blender_mcp/server.py`
`a2cdd77f46ac761d6bd402b4286ad00cd75d253e` (`__init__.py` 9ce248f3, `pyproject.toml` 45b5c465, `settings.py` f877cf66).
Tests at the freeze: `tests/b2_steps.py` 0362c7af (46 steps), `tests/drv_steps.py` 60f2bdec (20), `tests/headless_handlers.py` 416a9ad0,
`tests/test_b2_server.py` 7d84e42b (35 tests), `tests/test_f6_connection.py` ebb3e9ae (7, incl. the 2.4 identity test),
`tests/baseline_tools_2.4.txt` e24a34cd (204 names), `tests/baselines/tools_list_2.4.json` 3c6026b3,
settings fixtures `settings_replies_4.3.2.json` d7df3d0f / `_5.2.1.json` df292342 (re-cut at a611e0e3, R-B2-24).
Baseline = the 2.3.0 pair (addon `f2c680bb`, server `349c6a9e`), re-measured from disk 2026-10-21 by QA (`tests/run_b2_0_<ver>.log`).
Everything below was run from disk by QA on both portable installs (`D:\blender-4.3.2-windows-x64`, `D:\blender-5.2.1-windows-x64`)
and the repo venv (Python 3.13.2), no env vars. Plan and per-step assertions: `docs/TEST-PLAN-B2.md`; oracle: `docs/apichecks/b2_oracle.md`;
contract: `docs/PLAN.md` section 13 (B2.20); rulings R-B2-1..42: `docs/LEAD-RULINGS-B2.md`; reviews: `docs/REVIEW-B2-{server,addon,tests}.md`.

## B2.1 Baseline vs after

| Measure | Command | Baseline (2.3.0, f2c680bb / 349c6a9e) | After (2.4.0, frozen pair ee9b9dc0 / a2cdd77f) |
|---|---|---|---|
| Headless harness 4.3.2 | `blender.exe --background --factory-startup --python tests\headless_handlers.py -- --b0 --b1 --drv --b2` | 204 steps, 204 PASS, 0 FAIL, 171 handlers, EXIT 0 (`tests/run_b2_0_4.3.2.log`) | 252 steps, 252 PASS, 0 FAIL, 192 handlers, EXIT 0 (`tests/run_b2_qa5_4.3.2.log`, head line "run-time hashes: addon ee9b9dc0 b2_steps 0362c7af drv_steps 60f2bdec") |
| Headless harness 5.2.1 | same with `D:\blender-5.2.1-windows-x64` | 204 / 204 / 0, 171 handlers, EXIT 0 (`tests/run_b2_0_5.2.1.log`) | 252 / 252 / 0, 192 handlers, EXIT 0 (`tests/run_b2_qa5_5.2.1.log`, same head line) |
| Venv suite | `.venv\Scripts\python.exe -m pytest tests -q` (no env vars) | 94 passed, 0 skipped | 130 passed, 4 warnings, 0 skipped (94 + 35 `test_b2_server.py` + the 2.4 identity test); the one `skipif` in `test_server_units.py:85` predates B2 and its guard file exists, so nothing is skipped |
| tools/list | `tests\dump_tools_list.py names` / `json` / `diff` | 182 names (`baseline_tools_2.3.txt`) | 204 names (`baseline_tools_2.4.txt`); `diff tests/baselines/tools_list_2.4.json` = IDENTICAL; `diff tests/baselines/tools_list_2.3.json` = 22 tools added, 0 removed, 0 reordered, 1071 "+" lines; the "-" lines are confined to 7 ruled tools: descriptions (apply_modifier, batch, export_object, get_scene_info, import_file, set_origin, set_server_settings), the PLAN 13.2.2 additive params (export_object 16, import_file 8, apply_modifier apply_all/keep, set_origin location, get_scene_info limit = R-B2-32) and `apply_modifier.modifier_name` default null + dropped from `required` (R-B2-20) |
| Settings replies | `blender.exe --background --factory-startup --python tests\settings_baseline.py -- --check` | match:true at the 2.3 fixtures | `{"match": true, "calls": 64, "differing": []}` EXIT 0 on BOTH installs at ee9b9dc0 (`tests/run_settings_check_g3_<ver>.log`) |
| Legacy secret fixtures | `tests/fixtures/legacy_secrets_1.6.0.blend` (4.3.2, 459876 B, 743d93ba), `legacy_secrets_5x.blend` (5.2.1, 568349 B, 48a1de54) | n/a (new) | B2-0 PASS both installs (H15 migration from both idprop groups, load_post + helper paths, "cycles" kept) |

## B2.2 Headless steps (`tests/b2_steps.py`, `-- --b2`), both installs, at the frozen pair

46 B2 steps + 20 DRV steps, all PASS on 4.3.2 and 5.2.1 (`tests/run_b2_qa5_<ver>.log`). Per-step assertions: `docs/TEST-PLAN-B2.md` sections 1 and 7.

| Group | Steps | Contract | Result (both installs) |
|---|---|---|---|
| Migration | B2-0 | H15 | PASS: legacy secrets moved out of both idprop groups on both fixtures |
| 13.2.1 fixes | F1-F6, RS1 | R-B2-28/30/32, F-B2-1 | PASS: get_scene_info entries carry parent/hidden/collections, limit 10 default / 0 = all, set_origin `offset_applied`, boundary edges |
| 13.2.2 validation | V1-V4, V8-V12 | 22 fixed check ids, R-B2-27/29 | PASS: dims 0.002 fixture, wire/3-face-edge WARN, non_manifold excludes boundary, compare subset, valid lists |
| 13.2.3 LODs | O1-O5 (+O1c passes 1..6) | R-B2-26 | PASS: ratios XOR targets, target 40 in <= 6 passes |
| 13.2.4 collision | C1-C3 | R-B2-34 | PASS: closed hull, volume ratio in [1.7, 2.3], undersized proxy < 0.6 |
| 13.2.5 sockets | S1-S5 | 13.2.5 | PASS: S1b TIMBERMESH no-root error; world coords unchanged |
| 13.2.6 export | E1-E4, E6 | R-B2-33/35/38 | PASS: E2 UV-sphere fixture round trip, E2b refusal writes nothing, E3 TANGENT in GLB on the unsplit fixture, E4 keyed on `enable()` |
| Review steps | RA1-RA8, RA13, RA17 | docs/REVIEW-B2-addon.md, R-B2-36/37/38/39/41 | PASS: incl. RA2 edit-mode reads (uv_out_of_bounds WARN 24 on the EDIT-mode object; edit-mesh vertex move seen by compare_meshes max_delta 3.0), RA4 exact lookup of Panel[1] / Star*Prop / Q?Mark |
| Driver composition | B2-G8 | R-B2-40/42 | PASS: batch $id across 3 B2 tools, undo=AUTO rollback, transaction ROLLBACK restores set_pivot + removes add_socket, rna_get on the UCX proxy |
| Driver review | DRV-RA9, DRV-RA12 (end of the driver block) | RA-9, RA-12 | PASS: ROLLBACK with marker gone = error / undo_calls 0; drv_call pointer args |

## B2.3 Server-side tests (`tests/test_b2_server.py`, FakeConn), at server a2cdd77f

35 cases, 35 passed: S6 204 tools in 2.3 order; exact wire dicts for the 21 wrappers (S1); zero-send refusals (S2); server never writes the per-file manifest (S3); get_export_preset = one send (S4); batch_export loop with `get_scene_info {"limit": 0}` and `one_file_per OBJECT include_hierarchy False` (S5, R-B2-33); RS-1/4/8/12/13/14/15. Red proofs on record: 26 cases red against the committed 2.2.0 server (`git show HEAD:src`); RS-13 "requires apply_modifiers=True" and RS-14 red before Grace's RS landing (a3100d96); RS-15 `test_rs15_batch_export_exact_name_before_glob` red at f15808b3 ("'Panel1' != 'Panel[1]'"), green at a2cdd77f.

## B2.4 Mutation evidence (scratch copies only, the tree never mutated)

| Mutant (scratch copy hash) | What was broken | Expected to fail | Result |
|---|---|---|---|
| Coders' mutants (cards b2-a-l1/l2/l3, b2-s-l1/l2/l3) | F3 loop_total, V2 ngon rule, O1 apply per pass, C1 interior kept, E2b refusal, E2 temp modifier, S5 continue_on_error, S2 refusal | the named step / test | red as pasted on each card, verified by QA at the landing hash |
| mut9 d8d889b4 (Linus, from ee9b9dc0) | closing undo_push removed from `_drv_unwind` | B2-G8 (c) | FAIL "ROLLBACK did not restore the pivot" (199/200) |
| mut10 dc660ab4 | exact-first name match removed | B2-RA4 | FAIL "objects='Panel[1]' not addressable exactly ... objects not found" (199/200) |
| mut12 6f713dae | NON_MANIFOLD read touches selection outside the scopes | B2-V8 + B2-RA2 | FAIL "changed mode/selection" / "left mode OBJECT" (198/200) |
| mut11 8f05bb64 | `_sync_editmesh` no-op | B2-RA2 (flush case) | QA run on 4.3.2 `--b2`: 180 steps, 179 PASS, 1 FAIL = B2-RA2 "edit-mesh change not flushed into the read: {differing 0, max_delta 0.0}" |
| mut13 aca3ad2d | BMesh UV read dropped (`uvl = None`) | B2-RA2 (UV case) | QA run on 4.3.2 `--b2`: 180 steps, 178 PASS, 2 FAIL = B2-RA2 "uv_out_of_bounds not evaluated for an EDIT-mode object" + B2-V3 (uv_out_of_bounds PASS instead of WARN) |
| F-B2-3 pre-fix (addon 94cb47dc, the tree before R-B2-42) | real defect, not a mutant | B2-G8 (c) | FAIL on BOTH installs (`tests/run_b2_rt4_<ver>.log`, 251/252); green at ee9b9dc0 |

## B2.5 Findings log

| # | Finding | Owner | Status |
|---|---|---|---|
| F-B2-1 | set_origin reply key `offset_local` vs contract `offset_applied` (server printed nothing) | Linus | closed in L1.1 (F6 asserts `offset_applied`) |
| F-B2-2 | ADDITIVE_PARAMS_24 lacked `get_scene_info.limit` after the RS landing (2 superset reds, test-side) | Margaret | closed (R-B2-32 whitelist entry) |
| F-B2-3 | Driver `drv_transaction` ROLLBACK did not revert Python-only mutations when no undo step followed them (found by the new B2-G8 interop step; 2.3.0 defect) | Linus | closed by R-B2-42: closing `undo_push("mcp:<name>:rollback")` inside `_drv_unwind`, both callers; undo_calls for a plain rollback = 2; B2-G8 (c) is the regression guard |
| R-B2-41 | Literal object names carrying glob metacharacters (Panel[1]) were not addressable by `objects=` (fnmatch class) | Linus (add-on) + Grace (batch_export RS-15) | closed: exact-first match on both sides; RA4 + test_rs15 guard it; a name containing "," stays a documented limitation (whole-string exact match works, "A,Wall, left" splits) |
| Review sets | RS-1..15 (server), RA-1..17 (addon), RT-1..14 (tests) | Grace / Linus / Margaret | all fixed at the root, all three review cards Verified (Lead re-review lists empty); RA test-side shape error on my first RA4 edit (dict vs list) caught by Linus, corrected at b47476ad |
| EDIT-mode read (E20) | `Mesh.uv_layers[].data` reads length 0 while an edit-mesh exists; co-edited object evaluates to 0 polygons headless | Linus (declared, self-found) | closed: `_sync_editmesh` flush + UVs from the BMesh copy; RA2 cases guard it (mut11 / mut13) |
| F-B2-4 (live) | `execute_blender_code(result_expr=<dict>)` returned the dict's keys only (`_encode_value` had no dict branch) | Linus | closed at 2a97736c (R-B2-43); guard DRV-12c (red at ee9b9dc0 both installs); live dict round-trip verified on both builds |
| F-B2-5 (live) | `close_blender(force=True)` with no managed process claimed "process terminated" while nothing was killed | Grace | closed at server 8e3ca441 (R-B2-45): sends quit_blender, replies "asked to quit"; guard RS-16 FakeConn (red at scratch mutant 856bb054); live: the unmanaged instance exits on both builds |
| F-B2-6 (live, 4.3.2 GUI) | capture_viewport_angle restored only view_matrix, so on 4.3.2 the user's viewport stayed at the capture framing (all capture callers) | Linus | closed at 2a97736c (R-B2-44): view_location/rotation/distance restored; no headless guard (no window); live: fields identical before/after the three captures on 4.3.2 and 5.2.1, mut_f6 11b306b1 live red |
| F-B2-7 (live, 4.3.2 + plugin) | TIMBERMESH export left scene.frame_current at 0 | Linus | closed at 2a97736c (R-B2-44): frame recorded + frame_set in the finally; guard E4 frame assert (red at ee9b9dc0 on 4.3.2); live frame 7 -> 7 |

## B2.6 Not verified headless (B2-G5 live batch, R-B2-25/31)

Needs a SEPARATE Blender GUI instance started from the dev tree on port 9877 (never the user's session, never a copy to %APPDATA% or B:): the three `live_pass_required` handlers (get_viewport_screenshot, capture_viewport_angle, capture_contact_sheet), mode restore in a real GUI session, the four 2.3.0 D-G5 driver checks, and the `network_not_run` list (13 network handlers). List and mechanics: `docs/TEST-PLAN-B2.md` section 5. Also not verified (declared by the add-on owner): RA-17 EMBED/KEEP_PATHS beyond kwargs, the edit-mode flush under SCULPT mode (only EDIT is flushed), undo_calls on a batch(undo=AUTO) rollback beyond DRV-8.

---

# Tests.md: Blender MCP 2.3.0, Phase D1 generic driver (team nosubl, QA Margaret, 2026-10-16)

Frozen pair under test: `addon.py` `f2c680bb74a009766b07d129256d66c1d1e24999` (L3.1, F12/R39; supersedes
`bb5c6ef3`, the D-G3 candidate), `src/blender_mcp/server.py` `349c6a9ec3828f0472cee73623031ed8db0d7e93`
(settings.py `f877cf66`, `__init__.py` `b4aa8558`, pyproject `4a5c2421`). Baseline = 2.2.0 at `addon.py`
`0eb7ec4c`, server `2ddfc98a` (commit `3459566`).
Everything below was run from disk by QA on both portable installs
(`D:\blender-4.3.2-windows-x64`, `D:\blender-5.2.1-windows-x64`) and the repo venv (Python 3.13.2).
Plan and per-step assertions: `docs/TEST-PLAN-C.md`; oracle: `docs/apichecks/drv_oracle.md`.

## 1. Baseline vs after

| Measure | Command | Baseline (2.2.0, 0eb7ec4c / 2ddfc98a) | After (2.3.0, bb5c6ef3 / 349c6a9e) |
|---|---|---|---|
| Headless harness 4.3.2 | `blender.exe --background --factory-startup --python tests\headless_handlers.py -- --b0 --b1 [--drv]` | 186 steps, 186 PASS, 0 FAIL, 158 handlers | 204 steps, 204 PASS, 0 FAIL, 171 handlers, EXIT 0 (`tests/report_dg3b_4.3.2.json` at f2c680bb; `report_dg3_4.3.2.json` at bb5c6ef3 identical counts) |
| Headless harness 5.2.1 | same | 186 / 186 / 0, 158 handlers | 204 / 204 / 0, 171 handlers, EXIT 0 (`tests/report_dg3b_5.2.1.json`) |
| Venv suite | `.venv\Scripts\python.exe -m pytest tests -q` (no env vars) | 69 passed (needed `BLENDER_MCP_EXPECTED_VERSION=2.2.0`) | 94 passed, 0 skipped, 0 failed |
| tools/list | `tests\dump_tools_list.py names` | 169 names (`baseline_tools_2.2.txt`) | 182 names (`baseline_tools_2.3.txt`), diff vs 2.2 = 461 "+" lines, 3 "-" lines (descriptions only: R29, R13) |
| Settings replies | `tests\settings_baseline.py -- --check` | fixtures captured at 0eb7ec4c (64 calls, both installs, deterministic) | match:true, differing:[] on both installs |
| drvcheck facts | `docs\apichecks\drvcheck.py` | never run | lines appended to `out_4.3.2.jsonl` (41) and `out_5.2.1.jsonl` (33), prefix byte-identical |

## 2. Headless driver steps (`tests/drv_steps.py`, `-- --drv`), both installs, at the frozen pair

| Test | Method | Input | Expected | Actual | Result |
|---|---|---|---|---|---|
| DRV-1 describe(Object) | drv_describe at the default cap and at max_chars=60000 | what="Object"; fields="location,name"; search="rotation"; include_functions | R32b: >=100 kept props == count, no nulls, descriptions dropped first (truncated names it), reply <= 24400; at 60000 >= 130 props with descriptions; property ref -> kind property, dynamic True; "Objekt" -> closest Object | as expected on both (132 total / 102 kept on 4.3.2, 140 / 103 on 5.2.1) | PASS |
| DRV-1b describe(mesh.bevel) + ops | drv_describe operator / ops search / misses | "mesh.bevel", "wm.quit_blender", what="ops" search="bevel", "NoSuchStruct", "mesh.no_such_op", "" | offset FLOAT soft 0..100, poll_now False, refused False, no bl_options; quit_blender refused True; ops lists mesh.bevel; closest on misses; no traceback | as expected | PASS |
| DRV-2 data_new + find + uids | drv_new meshes/objects (object_data by "#uid"), drv_find, friendly views | args ["DrvMesh"], ["Drv","#<mesh uid>"], link_to="scene"; find glob/where; collection="no_such"/"screens"/"window_managers"; dedupe "Drv" twice; set on a new material | distinct uids, linked to the scene root, find count 1 with the same uid, scope DATA for MESH, uid on get_scene_info/get_object_info/find_objects_by_type, valid-list errors, "Drv.001", material colour applied | as expected | PASS |
| DRV-3 rna_set / rna_get | 2 sets with angles=DEGREES; reads radians/degrees; 4-item set with stop_on_error both ways; 9 coercions; encoding; 8 refs incl. 5 bad | see `docs/TEST-PLAN-C.md` D3 | applied 2, rotation 1.5708 rad / 90 deg, read-only + bad enum (live tuple incl. CYCLES, BLENDER_WORKBENCH) + unknown prop teach, stopped_at 0 on stop; pointer by #uid/{uid}/IDTYPE/null, idprop, frame_set side effect; matrix/pointer/collection encoding; errors[] with tried/valid, RNA text verbatim | as expected | PASS |
| DRV-3b malformed refs | drv_get with 11 bad refs + max_chars=0 | "", "#", "#abc", "OBJECT:", "OBJECT:Cube/", "/location", "#1/..//x", "context/", "data/nope", "prefs/nope", 10 kB string | every one an errors[] entry echoing the ref, no traceback, mode OBJECT; max_chars=0 teaches | as expected (R35: "context/" refused) | PASS |
| DRV-4 rename by uid | rna_set name, find, rna_get by #uid and OBJECT:, bpy renames | "DrvRenamed", then same-named object and mesh | old name gone, uid resolves, no uid cache (R16), IDTYPE disambiguates | as expected | PASS |
| DRV-4b snapshot + diff | drv_snapshot a / b0(return_tree=False), rename, bpy move + cube, drv_diff a / a vs b0 / unknown; caps | name="a", max_chars 400 / 200 | default fields present, tree with children; renamed only in `renamed`; changed.location before/after; added cube; a vs b0 clean; unknown -> valid; caps honoured | as expected | PASS |
| DRV-4c context_info | drv_context | fields None / "active_object,selected" | mode OBJECT, background True, window True (R20), 4 areas, scene as ID dict, active/selected as uids | as expected | PASS |
| DRV-2b batch + $ | drv_batch with $mesh.uid, nested $get.results[0].value.name, $nope, refs=False, nested batch, unknown handler, gated legacy | see D2b | 3 ok with the object linked to the batch-created mesh, nested path substituted, missing id stops, literal reaches drv_get, nested/unknown/gated errors | as expected | PASS |
| DRV-5 call_operator | modifier_add with context; bevel without/with mode EDIT; keep=True; typo params (R37 before poll); bad enum; unknown op; extra whitelist; 8 refused ops; INVOKE headless; view3d GUI-only | see D5 | FINISHED + context echo; poll error with rna_error "context is incorrect" and mode None; bevel grows polygons and restores mode/selection; closest/valid; 16-key whitelist, op not run; refused True; INVOKE refused < 50 ms; background True | as expected | PASS |
| DRV-6 call_method | evaluated_get(depsgraph), from_pydata, validate, calc_tangents, to_mesh/clear, data/meshes.new_from_object, path_from_id, misses | see D6 | uid returned; 3 verts 1 poly; validate False; no traceback; new mesh; "render.resolution_x"; closest; missing depsgraph named; NoSuch + tried | as expected | PASS |
| DRV-7 transaction | BEGIN t1 (pre-BEGIN object + unpushed scale), STATUS, set + op inside, ROLLBACK, nested BEGIN, COMMIT, ROLLBACK without, NOPE | see D7 | R22 keys; _mcp_txn int while open; location restored, modifier gone, pre-BEGIN object and unpushed scale kept, marker gone, undo_calls >= 1; nested refused naming t1; COMMIT clears; "no transaction"; BEGIN listed | as expected (undo_calls 2) | PASS |
| DRV-8 batch limits | oracle's 5 commands (R30 error at 2) AUTO / NONE / stop_on_error=False; 201; 200; nested; gated; $zzz; wall 0.0 with undo=NONE | see D8 | statuses ok/ok/error/skipped/skipped, stopped_at 2, rolled_back True + location restored, pre-batch object kept, _mcp_batch gone; NONE persists; all run; 201 refused w/o running; 200 run; wall -> stopped_at 1 | as expected | PASS |
| DRV-8 wall cap + AUTO rollback (added after D-G3) | wall 0.0 with undo=AUTO + stop_on_error | 3 commands | rolled_back True, location restored, success False, reply names the wall cap | at bb5c6ef3: success True, rolled_back False, command 0 persisted (F12); at f2c680bb (L3.1, R39): rolled_back True, location restored, success False, error "wall cap 0.0 s reached after command 0; 2 skipped" | PASS at f2c680bb (F12 closed) |
| DRV-9 reply cap | drv_get SCENE:Scene fields=all depth=2 at 2000 / default / 50; context/scene depth=2 uncapped; describe 800; find 300 | see D9 | chars <= cap, truncated non-empty and compact, R33 skipped walk named; default 24000 holds | as expected (404 chars at 2000) | PASS |
| DRV-9b deep read depth=3 | drv_get SCENE:Scene fields=all depth=3 max_chars=4000 (the 4.3.2 crash site) | | SCENE result, chars <= 4000, truncated names the skipped walk; process reaches SUMMARY and exits 0 on 4.3.2 | as expected on both, EXIT 0 | PASS |
| DRV-11 data_remove | drv_remove #uid purge_orphans; mesh with users do_unlink=False; multi-remove incl. an already-purged material; active scene | see D11 | object + orphan mesh + orphan material purged (purged 8), uids not found with tried, refused with users, success False with the not-found item, scene kept | as expected | PASS |
| Batch undo granularity (probe, 5.2.1, bb5c6ef3) | drv_batch(undo=AUTO) of one rna_set, then one `bpy.ops.ed.undo()`; transaction BEGIN/set/COMMIT, then one undo | Cube moved to [7,7,7] / [8,8,8] | one user undo reverts the WHOLE batch as a single step (documented intent); no `_mcp_batch` / `_mcp_txn` Scene property remains after the undo | batch: location back to [0,0,0], scene keys []; after COMMIT: no marker, scene keys [] | PASS (verified behaviour) |
| DRV-12b execute_code | execute_code old shape / result_expr / exception | print; x+2; ID expr; raise ValueError | stdout shape kept; value 42; ID as ref dict; error dict with traceback <= 3 and line 2 | as expected | PASS |
| DRV-12d get_version.registries | get_version before/after settings_snapshot | | registries dict of ints, only blendermcp_* keys, snapshot count +1, no uid-cache key | as expected | PASS |
| Legacy Phase A / B0 / B1 | every pre-existing step with --drv on | | 186 PASS unchanged | 186 PASS both installs | PASS |

## 3. Server-side tests (`tests/test_drv_server.py`, FakeConn), at server.py 349c6a9e

24 cases: exact wire kwargs for the 13 wrappers (L1/L3), refusals with zero sends and the exact error
texts (R12/R31), `$` strings pass through, cap default/env/file/bad values (always sent), settings keys +
rejection, teaching-key rendering, batch 200 ok / 201 refused with zero sends / head line + JSON,
transaction wire, execute_blender_code result_expr, the F4 class closed at a 2.2 site (add_bones). Result:
24 passed. Red proofs: 22/23 FAIL against the committed 2.2.0 server (`git show HEAD:src` copy via
`BLENDER_MCP_SRC`); the F4-class case FAILS there with the reply `x`.

## 4. Mutation evidence (scratch copies only, the tree never mutated)

| Mutant | Where | Expected to fail | Result |
|---|---|---|---|
| Server cap default dropped (`_driver_cap` -> `return max_chars`) | server.py 718a5066 | 4 cap tests | 4 failed = discriminating |
| A: coercer read-only guard skipped (addon.py:6788) | addon efc9123c | DRV-3 | SURVIVES 197/203 (RNA refuses read-only itself; R36 replaced it) |
| C: add-on default cap disabled (`_DRV_REPLY_MAX_DEFAULT` -> 10**9) | addon efc9123c | DRV-1, DRV-9 | DRV-1 FAIL (39029), DRV-9 FAIL (38530) = discriminating |
| D: DEGREES-in conversion removed (addon.py:6808) | addon efc9123c | DRV-3 | DRV-3 FAIL ((1,2,3), 90.0) + DRV-11 cascade = discriminating |
| E: R19 whitelist check disabled (addon.py:7471) | addon 7f356e45 | DRV-5 | DRV-5 FAIL (operator ran with the bogus extra key) = discriminating |
| G: add-on batch cap dropped (`_DRV_BATCH_MAX` -> 10**6) | addon bb5c6ef3 and again at f2c680bb | DRV-8 | DRV-8 FAIL (201 commands ran) = discriminating, both times (203/204) |
| H (Linus): F12 reintroduced (wall stop without `errored`) | addon f2c680bb | DRV-8 | DRV-8 FAIL at the wall-cap AUTO assertion = discriminating |

## 5. Findings log

| # | Finding | Owner | Status |
|---|---|---|---|
| F1 | "2.1.0 baseline" settings replies had no fixture on disk | QA | closed: `tests/settings_baseline.py` + fixtures (R15) |
| F2 | drvcheck.py never run | Linus | closed |
| F3 | tools/list exact test goes red at 2.3.0 | QA | closed: R11/R29 repoint |
| F4 | JSON scalar echoed as the reply at 6 driver + 5 legacy `_parse_json_any` sites | Grace | closed at the chokepoint (R31), proven at a 2.2 site |
| F5 | data_new docstring did not point at describe | Grace | closed |
| F6 | describe(Object) nulled half its properties at the default cap | Linus | closed (R32/R32b/R34) |
| F7 | dynamic-enum `valid` listed static items, not the live tuple | Linus | closed |
| F8 | reply cap overshoot (2908 chars at max_chars 2000) | Linus | closed (R34) |
| F9 | Blender 4.3.2 crash (EXCEPTION_ACCESS_VIOLATION) in a depth-2 walk: Paint.eraser_brush; second site CurveMapping.tone at depth 3 | Linus | closed (R33 struct deny-set), covered by DRV-9/9b |
| F10 | "context/" resolved to the Context struct instead of an error | Linus | closed (R35) |
| F12 | wall-capped batch with undo=AUTO + stop_on_error did not roll back and reported success | Linus | closed at f2c680bb (R39, L3.1); DRV-8 asserts it |

QA SIGN-OFF (2026-10-16, Margaret): every Phase D1 acceptance criterion in `docs/TEST-PLAN-C.md` sections 1-3
is verified with the evidence above at the frozen pair addon `f2c680bb` / server `349c6a9e`; no finding is
open at that pair (F1-F12 closed); section 6 lists what headless cannot verify and waits for the user's GUI
session (D-G5). The settings fixtures (R15) and the 2.3 tools/list baselines are the regression pins for the
next phase.

## 6. Not verified (needs the user's Blender 5.2.1 GUI session, D-G5; one batched request)

1. `call_operator("view3d.view_all", context={"area":"VIEW_3D","region":"WINDOW"})` returns FINISHED (headless: the GUI-only error, verified).
2. `transaction` ROLLBACK visibly reverts in the viewport and Edit > Undo History shows `mcp:<name>` (+ `:pre`).
3. One `batch` of 50 `rna_set` calls completes in one round trip with the viewport responsive.
4. `rna_set(publish=True)` msgbus delivery (R18: unprovable headless). Events (L4) are out of 2.3.0 (R38).
Also not verified headless: INVOKE_DEFAULT on a real window; undo_steps overflow inside a long transaction;
a user undo interleaved between BEGIN and ROLLBACK; scan cost above 2000 IDs.
