# PLAN.md - team nuzukm, Blender MCP

File of record. Owner: Grace (Planner/API). Contract changes are versioned amendments
in section 8 and are re-posted in #team-nuzukm. Builders build to the Lead's stated
reading until a re-post lands.

Plan version: A.4 / B0.3 (2026-09-11). Branch: `blender-5.2` (cut from `main` at `ef0ff7d`).
Phase A = migration to Blender 5.2.1 LTS, version 2.0.0. Phase B = feature requests,
planned after Phase A slices are out (section 6).

## 0. House rules (Lead's kickoff post, verbatim)

1. Inbox first on every wake. ACK every assignment / finding / ruling / gate by DM within one poll. No ACK in 5 min = re-assigned.
2. One file = one owner. addon.py = Ton. server.py + __init__.py = Campbell. tests/ = Bastien. TOOLS.md, README.md, docs/*.md (not PLAN.md), pyproject.toml, /blender skill, memory note = Sybren. docs/PLAN.md = Grace. Cross-file needs go through me.
3. Contract changes only as versioned amendments in PLAN.md, re-posted here. Build to my stated reading until the re-post lands.
4. 4.x/5.x differences are resolved by try-assign or hasattr, NEVER by comparing bpy.app.version. Grep found TWO existing version comparisons in addon.py (lines 2439 and 4414): both go.
5. Evidence = pasted headless output, PASS/FAIL per test-plan step, never a summary. Assertions are never weakened. Image results pass a non-blank pixel test (stddev > 0), not eyeballing.
6. Report "done" only when the FULL harness ran on BOTH installs and the output is pasted. I re-run it myself before accepting.

Lead rulings that PLAN.md restates and does not reopen: branch `blender-5.2`, nobody
commits, deploys, launches a Blender GUI or the MCP server; live copies
(`B:\...`, `D:\blender-4.3.2-windows-x64\MCP\`, `%APPDATA%` add-ons) are never edited;
headless command is `<install>\blender.exe --background --factory-startup --python <script>`
with `<install>` = `D:\blender-4.3.2-windows-x64` and `D:\blender-5.2.1-windows-x64`;
`bl_info["blender"]` stays `(4, 0, 0)`; `material.use_nodes` / `world.use_nodes` stay.

Shared-worktree rule (Lead, 11:21): NO `git stash` / `checkout` / `reset` by anyone,
Lead included, while a builder is mid-slice; compare against `main` with
`git show main:<file>` into the scratchpad.

8. (Lead, 11:55) NO write to a shared-tree file without a room post carrying the new `git hash-object` in the SAME minute; a landing that is not posted does not exist for verification. The Verifier pins whichever hash is on disk when a run STARTS and quotes it.
7. (Lead, 11:27) Nobody re-checks-out, stashes or resets a file under another owner; review diffs with `git diff --ignore-cr-at-eol`; any file hash used as evidence is `git hash-object <file>` (normalised), never a raw sha256 of the working copy (autocrlf rewrote server.py/__init__.py to CRLF at 11:20, content intact). Builders quote both when they quote a sha; the harness header uses git hash-object.

## 1. Decisions table

| # | Decision | Ruled by | Evidence / pointer |
|---|---|---|---|
| D1 | Phase A contract = `docs/MIGRATION-blender-5.2.md`: hard-breaks table, "Verified on the installed 5.2.1", migration plan steps 2-4. Architecture = `FEATURE-REQUEST-rigging-and-animation.md` sections 0, 1, 4, 7. | Lead | DM m_f3fc1ddbf7a8c0ff0ac0 |
| D2 | Every 4.x/5.x difference is a try-assign or `hasattr`; the two `bpy.app.version` compares in addon.py (2439, 4414) are removed. | Lead, house rule 4 | `grep -n "bpy.app.version" addon.py` |
| D3 | Version 2.0.0 in `pyproject.toml`, `addon.py` `bl_info["version"]`, `src/blender_mcp/__init__.py`. Both server-side files already read 2.0.0 on the branch (Campbell); addon.py still reads (1, 6, 0). | Lead | migcheck `bl_info_version: [1, 6, 0]` |
| D4 | Boolean solver alias mapping lives in the addon; server passes the string through and echoes the reply. | Lead | DM m_01ec1c277a565d563f87 |
| D5 | Break #4 is real only for FFMPEG output: PNG stills render on 5.2.1 with `media_type` untouched. The fix is still applied everywhere (one helper) so video output and `_render_settings` restore are correct. | Grace, evidence | migcheck 5.2.1 `png_no_media: ok`, `ffmpeg_no_media: Error`, `render_from_camera: success` |
| D6 | The 5.2.1 `media_type` enum is `IMAGE / MULTI_LAYER_IMAGE / VIDEO` (the migration doc's `MULTI_LAYER` is wrong; A4 corrects it). | Grace, evidence | migcheck 5.2.1 `media_type_enum` |
| D7 | Addon handler table has 85 entries at runtime; the 92 count is MCP tool names and the mapping is not 1:1 (8 tools are server-only: `diff_images`, `compare_reference_image`, `start_blender`, `close_blender`, `get_blender_status`, `load_img_to_3d_model`, `unload_img_to_3d_model`, `generate_3d_from_image`; some handlers back two tools, e.g. `create_rodin_job`; some have no tool, e.g. `get_reference_image`). Test plan M2 counts handlers (85, every entry of `_build_handlers()`), M9 counts tool names (92, Campbell's baseline). | Grace, evidence | migcheck `handler_count: 85` both versions; `grep -c "def " server.py` tool defs |
| D8 | Depth-map tests use `max_depth=25`: the factory camera is ~11.2 units from the cube, so `max_depth=10` clamps nearly every pixel (4.3.2 pixel range 0.000-0.035 at 64x64). | Grace, evidence | migcheck `depth_map.pixel_max` 0.035 (4.3.2) / 0.208 (5.2.1) at max_depth 10 |
| D9 | The `RenderSettings.engine` enum is a headless blind spot on BOTH versions whether read from the type or from a scene instance (lists only the one EEVEE id, not WORKBENCH or CYCLES). Engine validity is decided by assignment; an error text that wants the live list quotes the assignment exception, which carries the live tuple. | Grace, evidence | migcheck `engine_enum_from_instance` = `engine_enum_from_type` = `["BLENDER_EEVEE"]` (5.2.1) / `["BLENDER_EEVEE_NEXT"]` (4.3.2); cyc `dynamic_enum` same; `engine_assign` errors list `('BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES')` |
| D10 | `ImageFormatSettings.file_format` `enum_items` is NOT filtered by `media_type` (always the full 16-entry list on 5.2.1) while assignment IS filtered: under `IMAGE` only stills, under `MULTI_LAYER_IMAGE` only `OPEN_EXR_MULTILAYER`, under `VIDEO` only `FFMPEG`. Same rule as D9: the exception text is the live list. | Grace, evidence | migcheck 5.2.1 `media_matrix` |
| D11 | Helper names and signatures in section 2 are Ton's (working tree 2026-09-11): `_new_node(tree, *ids)`, `_engine_ids(render)`, `_set_render_engine(render, engine) -> error dict or None`, `_set_file_format(image_settings, file_format) -> error dict or None`. Reply keys: `engine` and `solver` carry the resolved live id; no `*_requested` keys. | Lead (A1 assigned), Grace aligns | `git diff addon.py` on `blender-5.2` |
| D12 | Post-fix evidence: with Ton's working tree, migcheck `handler_smoke` (opt-in mode `MIGCHECK_ADDON=1`) returns four success dicts on both installs (`engine` BLENDER_EEVEE / BLENDER_EEVEE_NEXT, `solver` FLOAT / FAST). The pre-fix failures are quoted in 2.1-2.3 from the first run (11:2x UTC). The SAVED MIGCHECK lines are the default (bpy-only) mode, deterministic for M10; handler-mode output is posted, never saved. | Grace, evidence; Lead condition m_d012ad3a87ba11052e60 | room post 11:4x; `out_*.jsonl` tag MIGCHECK |
| D13 | (Q1) `_set_file_format` aliases `EXR -> OPEN_EXR`, `JPG -> JPEG`, `TIF -> TIFF` before the try-assign; in A1 now. | Lead | m_82366714634ed035ba03 |
| D14 | (Q2) `tests/test_version_sync.py` (the three version sources must agree) is written in A3 and survives every Phase B bump. | Lead | same |
| D15 | (Q3) M2 = 85 addon handlers via `execute_command`; M9 = 92 tool names + inputSchema vs baseline. Server-only tools, by name: `start_blender`, `close_blender`, `get_blender_status`, `compare_reference_image`, `diff_images`, `load_img_to_3d_model`, `unload_img_to_3d_model`, `generate_3d_from_image`. Handler-backed under other names: `execute_blender_code -> execute_code`, both `generate_hyper3d_*` -> `create_rodin_job`, `generate_hunyuan3d_model -> create_hunyuan_job`. | Lead | same |
| D16 | (Q4) `operation` validation and the MESH-target check in `boolean_operation` are in A1 scope, built and verified by the Lead. | Lead | same |
| D17 | `engine=None` on the wire means "unchanged" (the server sends every key); `engine=""` is the malformed case and errors. Same reading for `file_format`. | Lead | same |
| D18 | F6: the unguarded `import_scene.obj` in `import_generated_asset` (Hyper3D) and the OBJ branch of `download_polyhaven_asset` get the `_op_exists(bpy.ops.wm.obj_import)` guard, inside A1. | Lead; Ton done | m_b49facb8cd82efec220a |
| D19 | Boolean solver enum on 5.2.1 is `FLOAT / EXACT / MANIFOLD`; `MANIFOLD` is passed through where the live enum has it; the migration doc gets the MANIFOLD line (A4). | Lead 11:17 | Ton's baseline; migcheck `boolean_solver.enum` |
| D20 | A2 ACCEPTED 11:20 (tools/list identical to baseline, inputSchema hash identical to main, resolver verified). `server.py` is frozen until Phase B except a 2-minute unfreeze to apply the canonical docstrings: boolean solver = section 2.3 wording, render engine = section 3 A2 row wording. Sybren copies the same wording into TOOLS.md and the /blender skill. | Lead 11:20, 11:24 | room |
| D21 | `gpu.init()` exists on 5.2.1 but does NOT enable `render.opengl`, `screen.screenshot_area` or `Window.screenshot()` headless (Sybren's F5 probe, accepted). Dropped from M14 and from the B4 hot-spot list; `Window.screenshot()` is a live-session tool only. | Lead | m_fe7b0cc160b41dca4b10 |
| D22 | Ton's error-list choices under R1/R2 (accepted): `_engine_ids` probes by assignment and parses the live tuple from the `TypeError` (fallback static), so engine errors list WORKBENCH and CYCLES; `_set_file_format` lists the static (complete, 16-entry) `file_format` enum. Measured: the `file_format` `TypeError` tuple IS filtered by the current media type, the static list is complete; the engine static list is incomplete. | Lead; Ton evidence | m_b49facb8cd82efec220a; migcheck `media_matrix`, cyc `dynamic_enum` |
| D24 | A1 ACCEPTED 11:28 on `addon.py` git hash-object `3c726b58e66101a2bc37d18f775c3e73ad8af20e` (Lead 4.3.2 20/20, 5.2.1 21/21, alias probe exr/.jpg/TIF/BOGUS; Ton 19/19 both). addon.py FROZEN except A1.1. | Lead | room 11:28 |
| D25 | G3 RESULT on that hash (Bastien, `tests/report_4.3.2.json`, `report_5.2.1.json`): 131 steps, 129 PASS / 2 FAIL on BOTH installs, identical fail set (F1 `extrude_faces` interior cap, F2 active-object restore); M1-M13 all PASS; venv `test_server_units.py` + `test_version_sync.py` 30 passed; M9 names + inputSchema identical, descriptions changed only for `boolean_operation`, `set_render_settings`, `start_blender`; M10 PASS (noise: `app.tempdir`, pre-existing nodecheck ShaderNode lines). | Lead, Bastien | room 11:35, 11:37 |
| D26 | A1.1 (owner Ton, gate = Bastien re-run on the A1.1 hash, then Lead): F1 `extrude_faces` leaves the source cap face inside the mesh (12 verts / 11 faces on a single-face extrude, want 10 faces); F2 active object not restored by `subdivide_mesh`, `apply_modifier`, `export_object`, `set_origin`, `set_smooth_shading`; break #5 `ShaderNodeSeparateRGB` in `set_texture` (ARM branch) undefined on 5.2.1 (section 2.8); F8 `tempfile._cleanup()` does not exist on either bundled Python, HDRI temp file never deleted. `add_primitive`, `import_file`, `join_objects` legitimately make the result active; `separate_mesh` keeps the SOURCE object active by design (both pieces are results), tests assert that. | Lead | m_a7814710ebb639808f61, m_2fa7191aa9bd9a0e44ec, room 11:35 |
| D27 | Tool arithmetic (supersedes the split in D7/D15): 92 tools = 84 own-key wrappers (the two Rodin wrappers share `create_rodin_job`; `execute_blender_code -> execute_code` and `generate_hunyuan3d_model -> create_hunyuan_job` are renames) + 5 addon-free (`start_blender`, `diff_images`, `load_img_to_3d_model`, `unload_img_to_3d_model`, `generate_3d_from_image`) + 3 aliasing (`close_blender -> quit_blender`, `get_blender_status -> get_polyhaven_status` probe, `compare_reference_image -> capture_viewport_angle`). Live handler table 85; the one handler without a wrapper is `get_reference_image` (reached only through the server helper `_resolve_reference`). | Lead, Bastien | m_b1b821fcec569f9dda00, room 11:37 |
| D28 | Sequencing (supersedes the "after G5" start in section 9): B0 starts after A1.1 is ACCEPTED (headless-green Phase A). The M14 live pass runs on the user's INSTALLED copy of the accepted hash (deploy = copy into the 5.2 add-ons folder), so the working tree moves on; a defect found live becomes A1.2 on the same owner's tree. G5 stays the Phase A completion gate and the v2.0.0 tag gate. | Lead | room 11:34 |
| D29 | A1.1 LANDED 11:39 as one write: `addon.py` git hash-object `e0329ced83ca1b74e12d0c5b2caaab65c872b94e` (raw sha256 `6f208e07...`), diff vs `ef0ff7d` +310/-108; new helper `_selection_scope` (next to `_select_only`) used by `extrude_faces`, `subdivide_mesh`, `apply_modifier`, `export_object`, `set_origin`, `set_smooth_shading` and the `boolean_operation` apply block; `extrude_faces` reply gains `new_faces`; F2 handlers leave active/selection as found; HDRI temp file unlinked; `set_texture` ARM branch via `_new_node("ShaderNodeSeparateRGB", "ShaderNodeSeparateColor")` with outputs by name `R|Red` etc. (F7, the ruled form; also covers the AO mix link). Lead's pre-acceptance on the staged file: 13/13 A1.1 cases + 19/19 A1 regression, both installs. Gate G3' = Bastien's acceptance run on this hash. | Ton, Lead | room 11:39 |
| D30 | G3' evidence (Bastien 11:45) on `addon.py` `e0329ced`, `server.py` `c080dfeb`, `__init__.py` `01a8fd68`: full harness 134/134 PASS on 4.3.2 AND 134/134 on 5.2.1, venv 30/30; `extrude_faces` 12 verts / 10 faces, no interior face; all 47 mutating handlers restore the active object (`separate_mesh` = source, creators = result); F8 zero `tempfile._cleanup`; F7 runtime ARM wiring gives a 3-output `ShaderNodeSeparateColor` on 5.2.1 / `ShaderNodeSeparateRGB` on 4.3.2, G -> Roughness, B -> Metallic. Logs `tests/run_*.log`. B0-T cases pre-written: `tests/b0_steps.py` (behind `-- --b0`), `tests/test_b0_server.py`. Awaiting the Lead's "A1.1 accepted" post (= B0 start + 2.1.0 bump signal, C24). | Bastien | room 11:45 |
| D31 | A1.1 ACCEPTED 11:45 = PHASE A HEADLESS COMPLETE at `addon.py` `e0329ced`, `server.py` `c080dfeb`, `__init__.py` `01a8fd68` (harness 134/134 both installs, venv 30/30, Lead 20/20 + 21/21, migcheck handler mode 4/4 both). Remaining Phase A item: M14 live pass by the USER on the installed copy of this hash (checklist sent by the Lead); G5 stays the v2.0.0 tag gate. The same post is the 2.1.0 bump signal (C24) and B0 GO for every landing; the working tree moves to 2.1.0 while the user tests the deployed 2.0.0 copy. Bastien's B0-15a ruling: assert `blendermcp_port` and `blendermcp_server_running` ABSENT from `Scene`, the four legacy secret props PRESENT and not drawn (through 2.1.x). | Lead | room 11:45 |
| D32 | USER DIRECTIVE 12:25 (relayed by the Lead): PAUSE upon completion of this feature. Reading: B1 (rigging and animation, 2.2.0) is the LAST slice of this run; B1 finishes through B1-G3 (headless, both installs) and B1-G4 (docs), then everyone freezes: no B2 landings, no new slices. Section 11 is a plan only (start condition B1-G3 + user resume). Sybren's v2.2.0 release notes + deploy checklist become the hand-off document. After B1-G4 the Lead posts the completion report, collects an after-action report from every member by DM, and updates the leading playbook. Grace's remaining task in this run = the AAR when asked. | User, Lead 12:25 |
| D33 | B0-G4 ACCEPTED 12:22 (Lead review from disk): TOOLS.md 148 entries = `tests/baseline_tools_2.1.txt`; README consistent; /blender skill pattern 9 + consent rule + FLOAT wording; settings doc L7-L9; memory note with the B0-G3 block + six-file deploy list; pyproject 2.2.0; files are LF. Phase B0 complete except the live L7-L9 in the user's batch. | Lead 12:22 |
| D34 | Hand-off rulings (Lead 12:27): (1) Release shape = ONE commit on `blender-5.2`, the branch switch from the MIGRATION doc, ONE tag `v2.2.0` whose release body carries the 2.0.0 / 2.1.0 / 2.2.0 sections; `v1.6.0` stays on the archive branch (supersedes "one release per version": no intermediate commits exist, so per-version tags are not reproducible; the user decides at "push"). (2) Legacy Scene secret properties are KEPT hidden in 2.2.0; removal moves to 2.3.0 (a user going 1.6.0 -> 2.2.0 in one deploy needs the migration to read them once); amends C19's "removed in 2.2.0". (3) `TERMS_AND_CONDITIONS.md` carries a pre-existing user change; the hand-off tells the user to decide before committing. | Lead 12:27 |
| D35 | USER DIRECTIVE 13:52 (supersedes the pause D32): "complete the rigging and animation request and deploy for another agent to do live testing". State at 13:53: addon.py `01d0e4c6` (B1-A L1-L3, K8 one-liner missing), server.py `44a80bc6` (B1-S L1 only), no `--b1` harness report; B1 NOT complete (22 rigging/animation wrappers not on the server). Orders: Campbell L1.1 + L2 + L3 now (reply keys per Ton's 12:34 post); Ton L3.1 then freeze then AAR; Bastien full `--b0 --b1` on both installs now, venv suites at Campbell's L3 hash (= B1-G3); Sybren docs then `docs/HANDOFF-2026-09-11.md`; Grace `docs/LIVE-TEST-PLAN.md` for the live-testing agent (numbered tool calls: M14 L1-L3/L5/L6, B0 L7-L9, B1 L10-L13, each with the exact call, PASS condition, what to paste back; that agent has only the deployed server + add-on + a 5.2.1 GUI); Lead deploys after B1-G3. | User, Lead 13:53 |
| D36 | DEPLOYED 14:01 (copy only, every target file re-hashed = source), nothing committed: (1) `B:\-AI-Stuff-\-=MCP-Servers=-\blender_mcp\` (the server the Claude Code MCP config launches): addon.py `37d2211d`, pyproject `0197888a`, server.py `6f249049`, `__init__.py` `f76c47d1`, settings.py `2bbc200b`, presets_default.json `0a58e8c3`, TOOLS.md, README.md; venv imports 2.2.0 / PROTOCOL 1 / 169 tools. (2) `D:\blender-4.3.2-windows-x64\MCP\`: same eight files, venv 2.2.0 / 169 tools. (3) 4.3 add-ons folder addon.py `37d2211d`, `__pycache__` cleared. (4) 5.2 add-ons folder addon.py `37d2211d` (folder created). The live-testing agent needs a NEW Claude Code session and a fresh Blender 5.2.1 launch (enable "Blender MCP" once; autostart opens 127.0.0.1:9876). PAUSE in effect once Sybren's HANDOFF refresh, Bastien's `baseline_tools_2.2.txt` and Grace's preconditions line are posted; AARs are in the playbook. | Lead 14:01 |
| D37 | LIVE PASS DONE 14:25 on the 5.2.1 GUI by the live-test agent (`claude@stratum`); report `docs/LIVE-TEST-REPORT-2026-09-11.md`. PASS: S, L1, L2, L3.1 / L3.4-L3.11, L5, L6, L7 (L7.3 not run: needs a human click), L8, L9.1 / L9.3, R1-R10, C1; every step that failed first was re-run PASS after A1.2. FIVE add-on defects found live and FIXED by that agent as A1.2, `addon.py` `f4dc37b10ee09f1d3277a0922da396c6b3b3e129` (diff vs `37d2211d` about +60/-8: `capture_viewport_angle` / `_capture_angle_inner`, `playblast`, `find_unweighted_vertices`, new `_gui_window` staticmethod), deployed to all four targets, headless `--b0 --b1` 185/185 on BOTH installs (`tests/report_a12_*.json`), venv 64/64, 4.3.2 GUI capture regression-checked: F1 `capture_viewport_angle` wrote a 1x1 PNG on 5.2.1 (`wm.redraw_timer` DRAW_WIN_SWAP inside the area/region `temp_override` breaks the following `screenshot_area`; redraw moved outside the override + 1x1 guard/retry; also hit `capture_contact_sheet`, `render_weight_map`, `find_unweighted_vertices(render=True)`, overlay captures); F2 `playblast(video_path=)` failed "height not divisible by 2" (400x225) -> even dims; F3 `playblast(camera=)` on the OpenGL path drew the viewport's own view -> `region_3d.view_perspective='CAMERA'` during capture, view restored; F4 `start_blender(restore_session=)` reported "no window in this session (headless)" in a GUI session (`bpy.context.window` is None in the timer right after `open_mainfile`) -> `_gui_window()` fallback to `window_manager.windows[0]` in `set_workspace` / `list_workspaces`; F5 `find_unweighted_vertices(render=True)` showed stale face selection -> edge/polygon flags cleared too. NOT fixed (server, Campbell): F6 one call of a 5-call parallel batch failed "Connection to Blender lost: WinError 10054" once (shared `_blender_connection` socket, no lock around `send_command`; recommend a lock or one reconnect-retry). Doc facts (Sybren): `media_type` is mandatory for FFMPEG in the GUI too (REJECTED without it); 4.3.2 refuses a 5.2 file with "incomplete header, may be from a newer version of Blender"; `set_workspace`'s reply reads the old workspace (switch applies next event-loop turn); `nla.bake` names the action "Action". Process note for the Lead: the live agent edited and deployed Ton's file (house rules 2 and 8); Ton asked to review as A1.2. Nothing committed. VERIFIED by Bastien 14:26 (own re-run, hash pinned start = end, four targets re-hashed = `f4dc37b1`): headless 185/185 on both installs, venv 64/64; caveat: headless proves no regression only, the five GUI fixes rest on the live agent's re-run. Ton's diff review in progress (read-only). LEAD RULING 14:26: verified from disk (dev tree and all four targets at `f4dc37b1`, server `6f249049`, two independent 185/185 runs, migration script and rigging probe green); the live agent acted outside the one-owner rule, the result is verified and STANDS as A1.2. Pause-compatible follow-ups: Ton reviews the diff against the handler rules (mode/view restored in `finally`, no version compares) and reports "clean" or findings; Sybren adds the four live facts and updates HANDOFF / release notes / memory to addon `f4dc37b1` and "live pass PASS (L7.3 needs a human click; F6 open)"; Campbell STAGES an F6 fix in scratchpad (lock around `send_command` or one reconnect-retry) with a mocked concurrency test, NOT landed: the user decides at resume. A1.2 ACCEPTED 14:26: no headless regression (Lead + Bastien's independent 185/185 both installs + 64/64); the five GUI-only fixes are recorded as "verified live by the live-test agent, not reproducible headless"; Ton's OWNER REVIEW 14:26 = ACCEPT (diff vs `37d2211d` +53/-9 in exactly the five claimed spots, no handler keys changed, PROTOCOL 1, bl_info 2.2.0, M7 grep 0, compiles on 3.11.9 + 3.13.13; his harnesses 13/13 + 31/31 + 14/14 + 10/10 on both installs, 0 FAIL): A1.2 is FINAL, `f4dc37b1` is the deployed and staged base. Doc fact: playblast tiles are now always even-sized (an odd `max_size` rounds down by 1). F6 RULING: stays STAGED under the pause; the APPROVED DESIGN for the next run = an `RLock` around `send_command` plus ONE reconnect-resend only when the failure happens before the request reached the add-on, never on receive. Campbell's measured assessment for the hand-off: the single server process serialises tool calls on one event loop, so a 10054 is the add-on closing the socket (likely the MCPManager bridge fanning a batch across processes, or the add-on's single-client accept loop); one occurrence in five. | live-test agent 14:25; Bastien 14:26; Lead 14:26 |
| D38 | F6 LANDING SCOPE (resume 14:31, R-F6): Campbell lands the staged fix on `server.py`: an `RLock` around the `send_command` call plus ONE reconnect-resend only when the connection reset happens BEFORE the request reached the add-on; never resend after the request was sent (a reset on receive raises, no retry). tools/list must stay byte-identical (169 names, diff vs `tests/baseline_tools_2.2.txt`), venv suite green, one copy, same-minute hash post naming the resend rule in one line. Bastien adds the mocked concurrency test: two threads through `send_command`; a reset injected before send -> exactly one resend; a reset injected after send -> `BlenderCommandError` / no resend. LANDED 14:32: `server.py` `2ddfc98abd72ebe5db4bf6430b895360978882ca` (`__init__` `f76c47d1`, settings `2bbc200b`, presets `0a58e8c3` unchanged). Resend rule as landed: `send_command` runs under a class-level `RLock`; a transport failure on connect / `sendall` is retried ONCE by reconnect-and-resend (the request never reached the add-on); a failure during receive is never retried. `_send_lock`, `_send_request(payload)`, `send_command` = lock + `_send_command_locked`; +42/-4 in the connection class, no tool / param / docstring / wrapper touched. Evidence: tools/list canonical dump md5 `2078b743...` pre = post (byte-identical), 169 = baseline 2.2; venv 64/64 at 2.2.0; mocked sockets: reset before send -> one reconnect + one resend, exactly one request delivered; reset during receive -> sent once, no reconnect, socket dropped, transport error. | Lead 14:31; Campbell 14:32 |
| D39 | A1.3 (resume 14:31, R-A1.3, from R9): `find_unweighted_vertices(render=True)` must restore the vertex, edge and polygon `select` flags in `finally` (`foreach_get` / `foreach_set`); pre-existing since `37d2211d`, fixed now because the branch is being committed. Ton lands it from `f4dc37b1` as one copy: both bundled interpreters compile, harness both installs staged, same-minute hash post, reply keys unchanged. Bastien adds a harness step: mixed vert/edge/face selection, call the headless-safe path, assert flags restored. LANDED 14:33: `addon.py` `0eb7ec4c23e8fb6d1c39e83ba237b0e23804c971` (diff vs `f4dc37b1` +23/-1): new helper `_mesh_select_scope(mesh)` next to `_selection_scope` (`foreach_get 'select'` on vertices / edges / polygons, `foreach_set` in `finally`), entered OUTSIDE `_mode_restore` so the flags are written after the mode is back; reply keys unchanged, PROTOCOL 1, bl_info 2.2.0, M7 grep 0, compiles on both interpreters; harness both installs L2 15/15 (new SFU2: mixed 320v/7e/3f selection restored) + L3 10/10 + A1.1 13/13 + L1 19/19, 0 FAIL. NOT deployed (deploy pause holds). FINAL PAIR for the commit: addon `0eb7ec4c` / server `2ddfc98a`. LEAD VERIFICATION 14:35 (discriminating, hash pinned start = end): A1.3 `lead_a13_check.py` (mixed 9v/14e/7f selection + 2 selected objects, render True then False): `f4dc37b1` 4/7 on both installs (flags FAIL), `0eb7ec4c` 7/7 on both. F6 `lead_f6_check.py` (scripted sockets: resend-before-delivery, two failures, receive failure, status error, 8 threads, tools/list): old `6f249049` 4/6 (no delivery on a pre-send reset; 8 in flight, 3 callers closed), new `2ddfc98a` 6/6 (one resend, one delivery, max in flight 1, 169 tools identical). Migration check 20/20 + 21/21 at the pair. Campbell's seam check on the pair: 158 handler keys, 162 send sites, 0 problems. Commit waits on Bastien's full run at the pair, Sybren's docs move + `docs/COMMIT-MESSAGE-v2.2.0.md`, and Grace's zero-diff line. | Lead 14:31, 14:35; Ton 14:33; Campbell 14:34 |
| D40 | GIT RULING (user directive, verbatim: "when the issues for this feature is complete upload it to git under the new branch, it is not yet released so main will stay the same"): after F6 and A1.3 land and the full run is green at the final pair, ONE commit on `blender-5.2` pushed to `origin/blender-5.2`. NO tag, NO branch switch, `main` untouched, NO deploy (live copies stay at addon `f4dc37b1` / server `6f249049` until the user says deploy). Version stays 2.2.0 (nothing released). Rigging Tier 2 stays deferred (10.4), not an "issue". Sybren writes `docs/COMMIT-MESSAGE-v2.2.0.md` (subject + body covering 2.0.0 / 2.1.0 / 2.2.0 in one commit, files list, evidence line; UTF-8/LF). The Lead re-runs `lead_check.py` + `b1_probe` on both installs at the final pair, diffs tools/list, then commits and pushes. Stray `pb_1..4.png` (96 B each, repo root) were Campbell's (20x12 mock PNGs from his 13:57 test run before the wrapper deleted its inputs); removed, untracked, not part of the commit. Grace's last task: compare the release-notes claims against the landed code at the final hashes and post the diff (expected zero). DONE 14:37: 18/18 claims resolve at addon `0eb7ec4c` / server `2ddfc98a` (re-hashed in the run), tools/list 169 = baseline 2.2; `docs/COMMIT-MESSAGE-v2.2.0.md` cites both hashes and all three versions; only Sybren's pending-evidence placeholders (COMMIT-MESSAGE 3, HANDOFF 3, rigging doc 2) remained until Bastien's final-pair numbers; filled 14:38, none left (`docs/COMMIT-MESSAGE-v2.2.0.md` final hash `ef7f88ba`). Release notes vs code diff = 0. FINAL PAIR VERIFIED by Bastien 14:38 (hash pinned start = end): headless `--b0 --b1` 186/186 on 4.3.2 AND 186/186 on 5.2.1 (new step B1-4b select-flag restore included; discriminating: the same harness on a copy of `f4dc37b1` = 185/186 on both, the only FAIL is B1-4b); venv 69/69 at `2ddfc98a` (64 + 5 new in `tests/test_f6_connection.py`: resend-once, give-up-after-one-resend, no-resend-after-send, two-threads-serialised, tools/list == 2.2 baseline in order and per-tool schema; discriminating: 3 of the 5 FAIL on the old `6f249049` by design). Evidence `tests/run_final_<ver>.log`, `tests/report_final_<ver>.json`, `tests/run_oldaddon_<ver>.log`. Numbers for the notes' pending markers: 186/186 + 186/186 + venv 69/69 at `0eb7ec4c` / `2ddfc98a`. | User, Lead 14:31; Grace 14:37; Bastien 14:38 |
| D23 | Phase B0 rulings (settings-save-load): settings file `~/.blender_mcp/settings.json`; precedence env (`BLENDER_HOST/PORT/EXE`) ABOVE the file, file above code defaults; `run_on_files` stays synchronous per file; the freeze on `get_blender_status` / `start_blender` lifts at the B0 assignment; the addon writes the `<file>.versions.json` and `<file>.mcp-profile.json` sidecars; shared tools `set_frame_range`, `set_scene_units`, `append_from_blend` land in B0 with one owner; `get_version` (addon command + server tool + `PROTOCOL` constant) from the update-notification doc section 2.1 is built INSIDE B0, the rest of that doc stays at B8+. | Lead | m_64ccea9045de7c7ae709, m_fe7b0cc160b41dca4b10 |

## 2. Phase A contract (per hard break)

Evidence lines: `docs/apichecks/out_4.3.2.jsonl` and `out_5.2.1.jsonl`, tag `MIGCHECK`
(script `docs/apichecks/migcheck.py`, run 2026-09-11 on both installs; it loads the
repo's addon.py, registers it headless and calls the four affected handlers). Every
fact below is "fact / evidence key / versions".

### 2.0 A1 rulings R1-R6 (Lead to Ton, m_a86d329a86a6062e2d7b, verbatim)

R1 boolean_operation: the live solver enum is only knowable inside Blender, so the mapping lives in the ADDON; the server passes the uppercased string through. Accept EXACT/FAST/FLOAT case-insensitively. If the enum lacks the requested id, map FAST<->FLOAT; reply includes "solver": <id actually set>; unknown value -> {"error": "solver 'X' not valid; valid: [live enum items]"}. Read the enum from mod.bl_rna.properties['solver'].enum_items.
R2 set_render_settings: try-assign the user's engine id, then the alias (BLENDER_EEVEE <-> BLENDER_EEVEE_NEXT), never bpy.app.version. Reply includes "engine": scene.render.engine (the resolved id). On failure list scene.render.bl_rna.properties['engine'].enum_items ids in the error and note that add-on engines (Cycles) may be missing from that list. Lowercase input accepted.
R3 render_depth_map: `if hasattr(tmp, "compositing_node_group")` -> node group path (bpy.data.node_groups.new, NodeGroupOutput + interface Image socket) else scene.node_tree path. Node ids: try Compositor id then Shader id via a small helper _new_node(tree, *ids) that lists what it tried in the error. Remove the node group AND the temp scene in finally; the Verifier asserts node_groups and scenes counts unchanged after the call. Line 761 loop: reorder to BLENDER_EEVEE first, keep enum resolution.
R4 media_type: set image_settings.media_type = 'IMAGE' before file_format at 805, 1571, 2449, each guarded by hasattr; _render_settings saves/restores media_type in the same tuple (guarded).
R5 line 4414: _op_exists(bpy.ops.wm.obj_import) else import_scene.obj. Final grep for bpy.app.version outside bl_info must be 0.
R6 use_nodes lines: untouched.

Also from the same DM: bl_info["version"] = (2, 0, 0), bl_info["blender"] stays (4, 0, 0).

Later deltas accepted by the Lead: R2 error list built from the parsed live tuple (D22);
R3 Workbench fallback is `_set_render_engine(tmp.render, 'BLENDER_EEVEE')` try-assign;
R4 media type is `VIDEO` for FFMPEG and `MULTI_LAYER_IMAGE` for OPEN_EXR_MULTILAYER, not
always `IMAGE` (2.4 matrix); EXR/JPG/TIF aliases (D13); F6 guards (D18); MANIFOLD (D19).

### 2.1 Break #1: render engine identifier (addon.py `set_render_settings` ~2437-2441, `render_depth_map` ~757-765)

Facts:
- `BLENDER_EEVEE` assigns on 5.2.1 and raises `TypeError` on 4.3.2; `BLENDER_EEVEE_NEXT` the reverse. `BLENDER_WORKBENCH` and `CYCLES` assign on both. / `engine_assign` / 4.3.2 + 5.2.1.
- The exception text is `bpy_struct: item.attr = val: enum "X" not found in ('BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES')` (5.2.1) and `... ('BLENDER_EEVEE_NEXT', 'BLENDER_WORKBENCH', 'CYCLES')` (4.3.2): the tuple in the message IS the live enum. / `engine_assign` / both.
- Live `set_render_settings(engine="BLENDER_EEVEE")` on 5.2.1: `Exception: enum "BLENDER_EEVEE_NEXT" not found`. On 4.3.2 it succeeds and reports `BLENDER_EEVEE_NEXT`. / `addon_register.handler_smoke.set_render_settings_eevee` / both.

Idiom (addon helpers, as implemented by Ton; D11):

```python
_ENGINE_ALIASES = {'BLENDER_EEVEE': ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'),
                   'BLENDER_EEVEE_NEXT': ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE')}

@classmethod
def _set_render_engine(cls, render, engine):
    """Try-assign, case-insensitive, accepts bare EEVEE / CYCLES / WORKBENCH (BLENDER_ prefix
    added) and the EEVEE alias pair. Returns an error dict on failure, else None.
    Never inspects bpy.app.version."""
```

Contract:
- `set_render_settings(engine=...)`: `if engine is not None:` call the helper and return
  its error dict as the reply on failure (covers M3 `engine="BOGUS"`, M13 `engine=""`,
  lower-case `eevee`). Reply keeps `engine` = `scene.render.engine`, the resolved live id.
  The `samples` branch keeps its string membership test on both EEVEE ids.
- Error text (D22, accepted): `_engine_ids()` probes by assignment and parses the live
  tuple out of the `TypeError` (static `enum_items` as fallback), so the `valid:` list
  names WORKBENCH and CYCLES on both versions despite the D9 blind spot.
- `render_depth_map`: Workbench fallback is `_set_render_engine(tmp.render, 'BLENDER_EEVEE')`
  (try-assign; the alias table reaches BLENDER_EEVEE_NEXT on 4.x).
- `engine=None` = unchanged, `engine=""` = error (D17).

### 2.2 Break #2: compositor tree in `render_depth_map` (addon.py ~774-800)

Facts:
- 5.2.1: `Scene.node_tree` absent, `Scene.compositing_node_group` present and `None` after `scene.copy()` on a factory scene; the source scene's group stays `None` after the temp scene got its own tree. 4.3.2: `node_tree` present, no `compositing_node_group`. / `depth_map.src_has_*`, `tmp_comp_group_after_copy`, `src_comp_group_untouched` / both.
- Live handler on 5.2.1: `Exception: 'Scene' object has no attribute 'node_tree'`. / `handler_smoke.render_depth_map` / 5.2.1.
- `CompositorNodeMapRange` undefined on 5.2.1; `ShaderNodeMapRange` accepted in a `CompositorNodeTree`. On 4.3.2 `CompositorNodeMapRange` exists and `ShaderNode*` ids are rejected in the compositor (nodecheck A errors). / `depth_map.map_range_id`, nodecheck / both.
- `ShaderNodeMapRange` (5.2.1) inputs `["Value", "From Min", "From Max", "To Min", "To Max", "Steps", "Vector", "From Min", ...]` (float set first, then vector set; `inputs["From Min"]` resolves to the float one), outputs `["Result", "Vector"]`, clamp property is `clamp`. `CompositorNodeMapRange` (4.3.2) inputs `["Value", "From Min", "From Max", "To Min", "To Max"]`, outputs `["Value"]`, property `use_clamp`. / `depth_map.map_range_*` / both.
- `CompositorNodeInvert` exists on both. Inputs 4.3.2 `["Fac", "Color"]`, 5.2.1 `["Color", "Factor", "Invert Color", "Invert Alpha"]`; output `["Color"]` on both. Link by name `"Color"`. / `depth_map.invert_*` / both.
- `CompositorNodeComposite` gone on 5.2.1; `NodeGroupOutput` after `tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")` exposes input `"Image"`. / `depth_map.output_*` / 5.2.1.
- `CompositorNodeRLayers.outputs` on 5.2.1 lists only enabled passes: `["Image", "Alpha", "Depth"]` after `use_pass_z = True` (set it BEFORE creating the node, as the code already does). 4.3.2 lists all 31. / `depth_map.rl_outputs` / both.
- The rebuilt graph renders on both: `render: ["FINISHED"]`, file written, pixels vary on 5.2.1 (max 0.208); after cleanup `bpy.data.node_groups` is empty. / `depth_map.render`, `pixel_varies`, `node_groups_left` / both.

Idiom (inside `render_depth_map`, as implemented; D11):

```python
group = None
try:
    ...                                   # use_pass_z on every view layer FIRST
    if hasattr(tmp, "compositing_node_group"):
        group = bpy.data.node_groups.new(f"{tmp.name}_tree", "CompositorNodeTree")
        group.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')
        tmp.compositing_node_group = group
        tree = group
    else:
        tmp.use_nodes = True
        tree = tmp.node_tree
    rl = self._new_node(tree, "CompositorNodeRLayers")
    map_node = self._new_node(tree, "CompositorNodeMapRange", "ShaderNodeMapRange")
    map_node.inputs["From Min"/"From Max"/"To Min"/"To Max"].default_value = ...   # first match = float socket
    for clamp_attr in ("use_clamp", "clamp"): setattr if hasattr
    map_out = map_node.outputs.get("Value") or map_node.outputs.get("Result")
    invert = self._new_node(tree, "CompositorNodeInvert", "ShaderNodeInvert")
    out_node = self._new_node(tree, "NodeGroupOutput" if group is not None else "CompositorNodeComposite")
    depth_out = rl.outputs.get("Depth") or rl.outputs.get("Z")
    links: depth_out -> map_node.inputs["Value"]; map_out -> invert.inputs["Color"]; invert.outputs["Color"] -> out_node.inputs["Image"]
    err = self._set_file_format(tmp.render.image_settings, 'PNG'); if err: return err
finally:
    remove tmp scene; if group is not None: bpy.data.node_groups.remove(group, do_unlink=True)
```

with the helper `_new_node(tree, *ids)`: `tree.nodes.new()` with the first id this Blender
accepts (compositor id first, shader twin second); raises `RuntimeError` naming every id
tried when none exists.

Contract: reply shape unchanged (`success, filepath, max_depth`). Cleanup guarantees for
M4: scene count and node-group count equal their before-values after the call, on both
versions, including the error paths (no camera, render failure, missing node id).

### 2.3 Break #3: boolean solver (addon.py ~2397-2415; server.py `boolean_operation` ~3297)

Facts:
- Solver enum is static and complete (not a dynamic blind spot): 4.3.2 `["FAST", "EXACT"]`, 5.2.1 `["FLOAT", "EXACT", "MANIFOLD"]`. Assigning a missing id raises `TypeError` with the live tuple. / `boolean_solver` / both.
- Live `boolean_operation(solver="FAST")` on 5.2.1: `Exception: enum "FAST" not found in ('FLOAT', 'EXACT', 'MANIFOLD')`; the modifier is left on the target. / `handler_smoke.boolean_fast` / 5.2.1.

Idiom (addon, as implemented; D11): `solver_id = solver.strip().upper()`; `solvers_valid`
from `mod.bl_rna.properties['solver'].enum_items` (static and complete here, unlike D9);
try `solver_id` then its alias (`FAST <-> FLOAT`); first hit is assigned and recorded as
`resolved_solver`; no hit removes the modifier and returns
`{"error": "solver '<x>' not valid; valid: [...] (FAST and FLOAT are accepted as aliases of each other)"}`.
`operation` is validated the same way against its enum; a non-MESH target is an error;
apply restores the previous selection and active object in `finally`.

Contract:
- Reply ADDS `"solver": <live id>` (read before apply). `operation` is echoed upper-cased,
  `applied` is a bool. Existing keys unchanged.
- Server (`boolean_operation` wrapper, already on the branch): docstring
  `solver: EXACT, FLOAT (FAST accepted as alias), MANIFOLD (Blender 5.2+)`; passes the
  string through unchanged; confirmation string appends the reply's `solver`. Error path
  unchanged (`Error: <text>`), which carries the enum.

### 2.4 Break #4: `ImageFormatSettings.media_type` (addon.py 805, 1571, 2449; `_render_settings` 1558-1583)

Facts:
- `media_type` exists on 5.2.1 (enum `["IMAGE", "MULTI_LAYER_IMAGE", "VIDEO"]`, default `IMAGE`), absent on 4.3.2. / `media_type.has_media_type`, `media_type_enum` / both.
- 5.2.1 assignment matrix: under `IMAGE`: `PNG`, `OPEN_EXR` assign; `OPEN_EXR_MULTILAYER` and `FFMPEG` raise. Under `MULTI_LAYER_IMAGE`: only `OPEN_EXR_MULTILAYER` assigns (`not found in ('OPEN_EXR_MULTILAYER')`). Under `VIDEO`: only `FFMPEG` assigns (`not found in ('FFMPEG')`). `file_format` `enum_items` reads the full 16-entry list under every media type (D10). 4.3.2: every format assigns directly. / `media_matrix`, `media_type.*` / both.
- `Image.file_format` (datablock, used by the resize helper at 530 and 709) assigns `PNG` on both; not affected. / `media_type.image_datablock_file_format` / both.
- Still renders (Workbench and EEVEE) write PNG headless on both versions; the live `render_from_camera` succeeds on 5.2.1 without any change. / `render_still`, `handler_smoke.render_from_camera` / both.

Idiom (addon helper `_set_file_format(image_settings, file_format)`, as implemented;
D11; replaces the three direct assignments): `fmt = file_format.strip().upper()`; when
`hasattr(image_settings, "media_type")`, try-assign the media type from
`{'FFMPEG': ('VIDEO',), 'OPEN_EXR_MULTILAYER': ('MULTI_LAYER_IMAGE', 'MULTI_LAYER')}.get(fmt, ('IMAGE',))`
(the matrix above proves all three mappings); aliases `EXR -> OPEN_EXR`, `JPG -> JPEG`,
`TIF -> TIFF` are applied first (D13); then assign `file_format`; on failure return
`{"error": "file_format '<x>' not valid; valid: [...]"}` with the static, complete
16-entry enum (D22; the assignment `TypeError` would name only the formats the current
media type accepts, D10).

`_render_settings` saves `getattr(ims, "media_type", None)` alongside `file_format`
and restores in the order media_type THEN file_format (restoring `PNG` while
`media_type` is still `VIDEO` raises); a helper error inside the context manager is
raised as `ValueError` so the caller's `except` reports it. `set_render_settings(file_format=...)`
returns the helper's error dict.

### 2.5 Extra version compare (addon.py 4414, Hunyuan OBJ import)

`if bpy.app.version >= (4, 0, 0): wm.obj_import else import_scene.obj` becomes
`if self._op_exists(bpy.ops.wm.obj_import): ... else: ...`. `_op_exists` already exists
(line 618). After this, M7 (`grep "bpy.app.version" addon.py` = 0 hits) holds.

### 2.6 Non-breaks confirmed (no code change, tests must not assert the 4.3 value)

- `material.use_nodes = True` / `world.use_nodes = True` run without error on 5.2.1; on 5.2.1 the node tree already exists at creation (`mat_node_tree_on_create: true`, `use_nodes` default `True`), on 4.3.2 it does not. Keep the assignments (house rule). / `material_nodes` / both.
- addon.py imports, `register()` and `unregister()` run headless on 5.2.1 with `--factory-startup`; the panel class registers, `scene.blendermcp_port` / `blendermcp_server_running` exist. / `addon_register.panel_registered`, `scene_props` / both.
- `requests` 2.32.3 and numpy 2.3.4 bundled on 5.2.1 (2.27.1 / 1.24.3 on 4.3.2). / `misc` / both.
- `numpy.array(mathutils.Vector)` is `float32` on 5.2.1, `float64` on 4.3.2; `dtype=numpy.float64` gives float64 on both. Today neither addon.py nor server.py converts a mathutils value through numpy (server's numpy use is PIL-image based in `diff_images`); M12 is a grep with an expected 0 hits, kept as a guard for Phase B. / `misc.numpy_vector_dtype` / both.
- 5.2.1 only: `gpu.init`, `Window.screenshot`, `bpy.app.handlers.exit_pre`, `render.render(frame_start=, frame_end=)` exist. Not used in Phase A. `gpu.init()` does NOT make viewport captures work headless (D21); `Window.screenshot()` is live-session only. / `misc`; Sybren F5 / 5.2.1.
- `bpy.data.is_dirty` is `False` at 5.2.1 startup and `True` on 4.3.2 (scheck `data_flags`). Tests must not assume either.

### 2.7 4.x/5.x switch list (the Verifier covers each on BOTH installs)

| # | Where | Switch | Test |
|---|---|---|---|
| S1 | `set_render_settings`, `render_depth_map` | engine id by try-assign, aliases EEVEE / BLENDER_EEVEE / BLENDER_EEVEE_NEXT | M3 |
| S2 | `render_depth_map` | `compositing_node_group` + own node group (5.x) vs `use_nodes` + `node_tree` (4.x) | M4 |
| S3 | `render_depth_map` | `CompositorNodeMapRange` (4.x) vs `ShaderNodeMapRange` (5.x); `use_clamp` vs `clamp`; output `[0]` | M4 |
| S4 | `render_depth_map` | `CompositorNodeComposite` (4.x) vs interface socket + `NodeGroupOutput` (5.x) | M4 |
| S5 | `render_depth_map` | RLayers lists only enabled passes on 5.x: `use_pass_z` before node creation | M4 with `use_pass_z` previously False |
| S6 | `boolean_operation` | `FAST` (4.x) vs `FLOAT` (5.x), `MANIFOLD` 5.2 only, alias both ways | M5 |
| S7 | `_set_file_format`, `_render_settings` | `media_type` present (5.x) vs absent (4.x); restore order | M6 |
| S8 | Hunyuan import (4414) | `_op_exists(bpy.ops.wm.obj_import)` | M7 grep |
| S9 | any material/world handler | node tree pre-exists on 5.x | M2 (materials created and wired on both) |
| S10 | `render_depth_map` cleanup | node group removed on 5.x only | M4 counts |
| S26 | `set_texture` ARM branch | `ShaderNodeSeparateRGB` (4.x only, outputs R/G/B) vs `ShaderNodeSeparateColor` (both, outputs Red/Green/Blue) | A1.1 re-run |

### 2.8 Break #5 (A1.1): `ShaderNodeSeparateRGB` in `set_texture` (addon.py ~3302), and F8

Facts (SEPCHK `--python-expr` on both installs, material node tree, 2026-09-11; not saved to the
out files because the M10 baseline is frozen):
- 4.3.2: `ShaderNodeSeparateRGB` inputs `["Image"]`, outputs `["R", "G", "B"]`; `ShaderNodeSeparateColor` inputs `["Color"]`, outputs `["Red", "Green", "Blue"]`, `mode = 'RGB'`; `CombineRGB` / `CombineColor` mirror them.
- 5.2.1: `ShaderNodeSeparateRGB` and `ShaderNodeCombineRGB` are `Node type undefined`; `ShaderNodeSeparateColor` / `CombineColor` present with the same Red/Green/Blue sockets and `mode`. `ShaderNodeMixRGB` still present on 5.2.1 (deprecated; first input is `Factor` there, `Fac` on 4.3.2).
- `tempfile._cleanup` is absent on both bundled Pythons (F8).

Contract (ruled, Ton's F7 form, landed in D29): `self._new_node(tree, "ShaderNodeSeparateRGB", "ShaderNodeSeparateColor")`
and read each channel by name with a per-version fallback (`outputs.get("R") or outputs.get("Red")`,
same for G/Green, B/Blue), including the AO mix link. F8: `tempfile._cleanup()` replaced by an
explicit unlink of the HDRI temp file in `finally` (guarded).

### 2.9 A1.1 pre-existing defects F1, F2 (from G3)

- F1 `extrude_faces`: after extruding a single face the original cap face remains inside
  the mesh (12 verts / 11 faces; a correct single-face extrude of a 6-face cube gives 10
  faces). Fix in the BMesh path: delete the source faces (`bmesh.ops.delete(bm, geom=faces, context='FACES')`
  after `extrude_face_region`, or use `extrude_discrete_faces` and remove the originals), on
  both versions. Test: face count 10, vert count 12, manifold.
- F2 active/selection restore: `subdivide_mesh`, `apply_modifier`, `export_object`, `set_origin`,
  `set_smooth_shading` must restore `view_layer.objects.active` (and selection) in `finally`,
  the pattern Ton already used in `boolean_operation(apply=True)`. `add_primitive`,
  `import_file`, `join_objects` keep the result active; `separate_mesh` keeps the source
  active (D26).

## 3. Slices (file ownership)

| Slice | Owner | Files (exact) | Content | Gate | ETA |
|---|---|---|---|---|---|
| A1 addon | Ton | `addon.py` | Assigned by the Lead 11:15 with rulings R1-R6 (Ton's DM; restated in section 8 once the text is in PLAN.md). Scope = sections 2.1-2.5: helpers `_new_node`, `_engine_ids`, `_set_render_engine`, `_set_file_format`; the four fixes; the OBJ-import op check; `bl_info["version"] = (2, 0, 0)`. ACCEPTED 11:28 on hash `3c726b58` (D24); addon.py frozen. | G1 done | done |
| A1.1 addon | Ton | `addon.py` | F1 `extrude_faces` cap (2.9), F2 active restore on five handlers (2.9), break #5 `ShaderNodeSeparateRGB` (2.8), F8 `tempfile._cleanup` (2.8). Delivered as ONE write; post raw sha256 + git hash-object + harness output on both installs. | A1.1 gate: Bastien full harness + venv tests on that hash, both summaries + every changed STEP line to the Lead; Lead accepts | Ton's |
| A2 server | Campbell | `src/blender_mcp/server.py`, `src/blender_mcp/__init__.py` | ACCEPTED 11:20 (D20). Delivered: `boolean_operation` docstring + solver echo (2.3); `set_render_settings` docstring `engine: CYCLES, BLENDER_EEVEE (alias EEVEE; the 4.x id BLENDER_EEVEE_NEXT is accepted), BLENDER_WORKBENCH` (canonical wording); `_find_blender_exe` portable-folder discovery (`D:\blender-*-windows-x64` + fixed-drive roots, highest version wins) and `start_blender` error text naming `BLENDER_EXE`; `__version__ = "2.0.0"`; `tests/baseline_tools_4.3.txt` handed to Bastien. server.py FROZEN until Phase B except the 2-minute docstring unfreeze. | G2 done | done |
| A3 tests | Bastien | `tests/headless_handlers.py` (119 steps, `execute_command` dispatch, addon sha256 in the header), `tests/test_server_units.py`, `tests/test_version_sync.py` (D14), `tests/baseline_tools_4.3.txt` (from Campbell), `tests/README.md` | Harness per the Lead's spec: import addon.py from the repo path, `register()`, `BlenderMCPServer()` without a socket, every entry of `_build_handlers()` (85) with a happy-path payload, one `PASS|FAIL <name> <reply>` line each; M1-M13 as named cases; identical command on both installs; run against Ton's FINAL sha only. Gated integration handlers (`_GATED_COMMANDS`) count PASS on a "not configured / not enabled" reply. | G3 | Bastien's |
| A4 docs | Sybren | `docs/MIGRATION-blender-5.2.md` (+ new section "Live checklist (M14)"), `docs/apichecks/README.md` (add the `migcheck.py` row), `docs/README.md`, `TOOLS.md`, `README.md`, `pyproject.toml` (2.0.0 done), `C:\Users\Naabin\.claude\commands\blender.md`, auto-memory `project_blender_mcp.md`, MemPalace `main` drawer, release notes + deploy checklist (scratchpad until release) | PARTIALLY OPEN since 11:20 (D20): everything that follows from final rulings and measured facts goes in now: version lines; README Blender/requirements/troubleshooting/`BLENDER_EXE` row/5.2 add-ons path/one-way `.blend` warning; TOOLS.md boolean + engine + depth-map rows in the canonical wording; skill boolean line; MIGRATION status, MANIFOLD line (D19), break #4 note (D5), media_type enum spelling (D6), 85 handlers (D7), engine enum blind spot (D9), depth-map `max_depth` note (D8), `gpu.init()` correction (D21); apichecks README entry for `migcheck.py`; memory session block + F1 repair. HOLD ONLY the "verified on 5.2.1" claims for the 4 fixes and the tool-count/pass tables until the Lead posts "A1 accepted" with the harness evidence. | G4 (partial) | Sybren's |
| A0 plan + API | Grace | `docs/PLAN.md`, `docs/apichecks/migcheck.py`, `docs/apichecks/out_4.3.2.jsonl`, `docs/apichecks/out_5.2.1.jsonl` | This file; headless evidence on request (answer = section number + evidence key + versions). | - | live |

## 4. Sequencing and gates

```
T0  A1 helpers (_set_render_engine, _new_node, _set_image_format) -> A1 fixes 2.1-2.5
    A2 (independent of A1: docstrings, pass-through, discovery, __version__)     } parallel
    A3 harness skeleton + M1/M2/M7/M9/M11/M12 (run against the UNFIXED addon first:
       expected FAIL set on 5.2.1 = set_render_settings, boolean_operation FAST,
       render_depth_map; that run is the "before" evidence)
    A4 drafts in scratchpad only
G1  Ton reports A1 done with pasted `migcheck.py` handler_smoke from 5.2.1 showing four
    success dicts, and `grep -c "bpy.app.version" addon.py` = 0. Grace re-runs migcheck
    on both installs and posts the lines.
G2  DONE 11:20 (D20): tools/list identical (92 names, inputSchema hash identical to main),
    `__version__` 2.0.0, resolver: unset -> 5.2.1 exe, BLENDER_EXE=4.3.2 -> that path,
    BLENDER_EXE missing file -> ignored.
G1' "A1 accepted" DONE 11:28 on hash 3c726b58 (D24).
G3  DONE 11:35-11:37 (D25): 129/131 both installs; the two FAILs are F1/F2 -> A1.1.
G3' A1.1 gate: Ton posts the A1.1 hash; Bastien re-runs the full harness + venv tests on
    it on both installs (expected 131/131) and DMs the Lead both summaries + every changed
    STEP line; nobody edits addon.py during that run; Lead posts "A1.1 accepted".
    B0 starts here (D28). Grace re-runs migcheck in handler mode against the A1.1 hash and
    posts the four success dicts (not saved).
G4  SUPERSEDED (A.3): A4 is partially open since 11:20 for everything that follows from
    final rulings and measured facts; only the "verified on 5.2.1" claims for the four
    fixes and the tool-count/pass tables wait for "A1 accepted" + Bastien's table.
G5  Lead batches M14 (live pass) for the USER, using Sybren's checklist blocks
    L1 legacy add-on load + socket on the 5.2 GUI; L2 PNG still without media_type then
    FFMPEG with media_type; L3 viewport tools through the MCP server (scene/node_group
    count check after render_depth_map, EEVEE id, boolean FAST alias); L5 start_blender
    launch paths (python_expr / factory / blend_file); L6 one-way .blend format.
    L4 (gpu.init() headless probe) is removed: settled headless by Sybren (D21).
    Final home: MIGRATION-blender-5.2.md section "Live checklist (M14)". M14 runs on the
    user's INSTALLED copy of the accepted hash (D28); a live defect becomes A1.2 on Ton's
    tree. Phase A done and v2.0.0 tag gate = M14 passes. Nothing is committed until the
    user says so.
```

Gate rule: a gate is a DM to the Lead with pasted headless output; the Lead posts the
gate result in the room. Grace answers design or API questions at any time with a
section number and, for API questions, a migcheck/apicheck evidence key.

## 5. Risks and pre-existing issues (not Phase A breaks; Lead decides if they ride along)

| # | Risk | Where | Recommendation |
|---|---|---|---|
| R1 | `set_render_settings(file_format="EXR")` raised on both versions: the enum id is `OPEN_EXR` and the docstring said `EXR`. | addon `_set_file_format` | RESOLVED by D13 (aliases in A1). |
| R2 | Handlers raise raw exceptions (not error dicts) on enum failures; the dispatcher turns them into `{"error"}` but the traceback goes to Blender's stdout. | all four sites | Covered by 2.1-2.4 (explicit error dicts). |
| R3 | Depth map contrast depends on `max_depth` vs camera distance; a "non-blank" test at `max_depth=10` is borderline on 4.3.2 (range 0.035). | tests M4 | D8: use `max_depth=25` in tests; document the parameter in TOOLS.md. |
| R4 | All six agents share one working tree and one checked-out branch; a file edited by two owners is lost work, and a stash/pop flips file contents under a running test (happened 11:20). | repo | House rule 2; NO `git stash` / `checkout` / `reset` by ANYONE, Lead included, while a builder is mid-slice; compare against `main` with `git show main:<file>` into the scratchpad. |
| R5 | 5.x `.blend` files do not open in 4.3.2. | tests that save | Tests save only under the temp dir with a version-suffixed name. |
| R6 | The 2026-09-02 headless handler tests were never committed (MemPalace has the PASS summary only). | tests/ | A3 builds them fresh; this time they live in `tests/`. |
| R7 | `Scene.use_nodes` still exists on 5.2.1 as a no-op; a test asserting "scene.use_nodes False after depth map" is meaningless there. | tests M4 | Assert counts (scenes, node_groups) instead. |
| R9 | `find_unweighted_vertices(render=True)` leaves the mesh element selection set to the unweighted verts (present since `37d2211d`; A1.2's F5 extends it to edges/faces). Not a blocker; Ton's review 14:27 offers it as a cheap A1.3 (restore the prior selection in `finally`) for the next run. | addon `f4dc37b1` | Candidate A1.3 at resume; the live plan's L10.2 already tolerates it. |
| R8 | `recover_file(mode="LAST_SESSION")` reads the real user `quit.blend` even under `--factory-startup`; a harness step can load a private file into the test process (happened once in Ton's run). | tests, B0 | Never call LAST_SESSION in any harness; the tool itself is live-only by nature (doc). Same care for `recover_file(AUTOSAVE)` with a real path: tests use a missing path. |

## 6. Phase B first cut (ordering only; each slice gets its own amendment before it starts)

Order = `docs/README.md` "Build order recommendation". Each doc is its own contract
(tools, parameters, reply shapes, tiers, ripple points, test plan). Before each slice
Grace re-verifies that doc's "verified facts" against `out_5.2.1.jsonl` plus a targeted
apicheck for anything the MIGRATION doc's "needs re-verification" table names, and
Sybren updates the doc's facts section; builders never build against a 4.3-only fact.

| Slice | Doc | Tier | Version | 5.2 re-verification hot spots (from MIGRATION doc) |
|---|---|---|---|---|
| B0 | `FEATURE-REQUEST-settings-save-load.md` (section 0.1 autostart bug, section 1 fixes, Tier 1) + `get_version` / `PROTOCOL` from the update-notification doc section 2.1 (D23). Slice amendment: section 9. | T1 | 2.1.0 | `active_asset_library` index shift (Tier 2 only), compression default on, `is_dirty` False at startup, `eevee.use_gtao` and `view_settings.use_hdr_view` removed |
| B1 | `FEATURE-REQUEST-rigging-and-animation.md` | T1 | 2.2.0 | slotted actions / channelbags, `pose.bones[].select`, `symmetrize(copy_bone_colors)` |
| B2 | `FEATURE-REQUEST-engine-readiness.md` | T1 | 2.3.0 | boolean FLOAT, FBX `SMOOTH_GROUP`, glTF `NAME`, `obj_export(apply_transform)` |
| B3 | `FEATURE-REQUEST-uv-and-texturing.md` | T1 | 2.4.0 | UV selection attrs gone, `pin_ensure()`, bake kwargs, `Thin Wall` |
| B4 | `FEATURE-REQUEST-presentation-and-library.md` | T1 | 2.5.0 | `Window.screenshot()` live only; headless captures stay impossible (D21) |
| B5 | `FEATURE-REQUEST-asset-construction.md` | all | 2.6.0 | `object.convert` kwargs, `orphans_purge(do_recursive)` |
| B6 | `FEATURE-REQUEST-node-graphs.md` | T1 | 2.7.0 | `modifier.properties.inputs[id].value`, File Output node fields, compositor ids |
| B7 | `FEATURE-REQUEST-simulation-and-vfx.md` | T1 | 2.8.0 | `PointCache.compression`, Alembic/USD kwargs, `Depth` pass name, `frame_start/frame_end` |
| B8+ | remaining tiers, `FEATURE-REQUEST-update-notification.md` minus its section 2.1 (built in B0, D23; the version-sync test is A3, D14) | - | - | none |

## 7. Q&A (open questions to the Lead; answered ones move to section 1)

- Q1-Q4: answered 11:23, recorded as D13-D16.
- Q5 (B0): resolved; Campbell's recon (B0-1..B0-5, GV-1..GV-3) and Ton's recon (parts 1-2) arrived 11:31-11:34 and are folded into section 9.
- Q6: RESOLVED 11:38 (m_fa6cefad61baa979e62a): `PROTOCOL = 1`; every "protocol 2" string in Campbell's GV-1/GV-2 draft is SUPERSEDED and read as 1 (C16, C22). Campbell and Sybren build and write 1.

## 8. Amendments log

| Version | Date | Change | Posted |
|---|---|---|---|
| A.1 | 2026-09-11 | Initial Phase A contract, slices, gates, Phase B ordering. | #team-nuzukm |
| A.2 | 2026-09-11 | Section 2 aligned to Ton's helper names and signatures (D11); `*_requested` reply keys dropped; 2.4 corrected: `OPEN_EXR_MULTILAYER` needs `MULTI_LAYER_IMAGE` (D10, `media_matrix`); D9 extended to the instance-level enum; D12 post-fix evidence. | #team-nuzukm |
| A.3 | 2026-09-11 | R1-R6 verbatim as 2.0 with accepted deltas; D13-D23 (Q1-Q4 answers, engine None/"" rule, F6, MANIFOLD, A2 accepted + freeze + canonical docstrings, gpu.init() correction, error-list choices, B0 rulings); shared-worktree rule; A3 file list; A4 partially open (G4 superseded); M14 = L1/L2/L3/L5/L6; migcheck addon section opt-in (D12). | #team-nuzukm |
| B0.1 | 2026-09-11 | Section 9: B0 slice amendment (settings-save-load), contract deltas C1-C12, ownership, gates, switch list, fact-check list. | #team-nuzukm |
| B0.2 | 2026-09-11 | b0check.py evidence on both installs: C13 (`default_set=True` always; doc wrong), C14 (incremental naming), C15 (4.3.2 revert crash in a long sequence), S20-S25; 9.5 rewritten as verified facts. `out_4.3.2.jsonl` 30 -> 32 lines, `out_5.2.1.jsonl` 22 -> 24 lines (header + one B0CHECK line each). | #team-nuzukm |
| A.4 | 2026-09-11 | House rule 7; D24-D28 (A1 accepted, G3 result, A1.1 = F1/F2/break #5/F8, tool arithmetic, B0 starts after A1.1); 2.8 break #5 + F8 evidence, 2.9 F1/F2 contract; S26; A1.1 slice + G3' gate. | #team-nuzukm |
| B0.4 | 2026-09-11 | C24 version-bump rule, C25 M7 definition, 9.7 landing log (B0-A L1 accepted on `39162cbf`), C2 seam authority note. | #team-nuzukm |
| B0.5 | 2026-09-11 | House rule 8 (hash discipline); C26 six-file deploy list; C27 server seam rulings (load_blend 5 params, no ensure_server_running wrapper, incremental filepath rule, F10, ffmpeg seam, Campbell L4 reconciliation, payload map authority); C28 5.2.1 headless never dirty; landing log rows B0-S L1/L2/L3 and B0-A L2. | #team-nuzukm |
| B1.2 | 2026-09-11 | 10.5 = verified facts K17-K23 (b1check2.py, out files 34->36 / 26->28); 10.4 ruled (L4 set incl. NLA trio + Rigify pair, only setup_ik_chain held); K17 into the Tier 1 contract; K5 EditBone undefined on both. | #team-nuzukm |
| B1.3 | 2026-09-11 | D32 user pause directive (B1 = last slice of this run), D33 B0-G4 accepted; 10.2 L3: no `set_scene_frame_range` (ruling 12:23), K8 amended (`disable_bone_shape=True`, L2); 10.6 B1-S L1 `44a80bc6` / `f76c47d1` verified. | #team-nuzukm |
| B2.2 | 2026-09-11 | Section 11 marked PLANNED, NOT STARTED (start = B1-G3 + user resume); 11.4 Tier 2 split ruled for the record. | #team-nuzukm |
| B2.1 | 2026-09-11 | Section 11: B2 slice amendment (engine-readiness) from B2-G0 evidence (b2check.py, out files 36->38 / 28->30): deltas E1-E14, ownership, landings, gates, Tier 2 judgement. | #team-nuzukm |
| B1.1 | 2026-09-11 | Section 10: B1 slice amendment (rigging-and-animation) from B1-G0 evidence: deltas K1-K15, ownership, three landings, gates, Tier 2 judgement, facts still to verify (B1-G0b). | #team-nuzukm |
| B0.3 | 2026-09-11 | B0-Q1..Q3 rulings (C16-C18), Ton's recon D-a..D-g + measured facts (C19), Campbell's settings module, defect fix, get_version and mismatch mechanism (C20-C22), Sybren's cleanup list, three-landing delivery, start after A1.1; 9.6 emptied (PROTOCOL value is Q6). | #team-nuzukm |

## 9. Phase B0 slice amendment (B0.1): settings, save and load

Starts after the Lead posts "A1.1 accepted" (D28; assignments pre-issued to all four builders with that start condition). Contract = `docs/FEATURE-REQUEST-settings-save-load.md`
sections 0.1 (five required fixes), 1 (fixes table), 2.1 (Tier 1 tables: file lifecycle,
settings generic, typed conveniences, presets and profiles, add-ons/workspaces/preferences,
session state), 3 (shared helper), 4 (ripple points), 6 (tests 1-18), plus
`docs/FEATURE-REQUEST-update-notification.md` section 2.1 items 2-3 (`get_version`
command + tool, `PROTOCOL`). Tier 2 and 3 are OUT. Architecture rules unchanged
(rigging doc sections 0, 1, 4, 7). Version 2.1.0 in the three sources.

### 9.1 Contract deltas (versioned; the doc text stands where not listed)

| # | Delta | Ruled by |
|---|---|---|
| C1 | Target version 2.1.0 (doc says 1.7.x). | Lead |
| C2 | SEAM AUTHORITY (Lead 11:52): the LANDED `get_version` reply keys are exactly `{addon, blender, python, protocol, addon_file, background}`; Ton's seam DM naming `addon_version` / `blender_version` is superseded, Campbell builds the server tool on these keys. `get_version`: addon command `get_version -> {"addon": "2.1.0", "blender": bpy.app.version_string, "python": ..., "protocol": PROTOCOL}`; addon module constant `PROTOCOL = 1` (first numbered protocol; bumped by any later wire change); server constant `PROTOCOL = 1`; server tool `get_version` returns server version (from `__version__`), addon version or `"unreachable"`, Blender version, both protocol numbers with a `protocol_match` bool, the loaded `server.py` path, the settings file path, and `last_update_check: null` (the check itself is B8+). | Lead D23; Grace numbers |
| C3 | Doc test 1 asserts `is_dirty True` at factory startup; 5.2.1 measures False (4.3.2 True). Assert `is_saved False` and `filepath ""`, record `is_dirty`. `get_file_state` reports it, never assumes it. | Lead |
| C4 | `scene.eevee.use_gtao` and `view_settings.use_hdr_view` are removed on 5.2 (scheck diff). `describe/get/set_settings` are `bl_rna`-driven so they list only what exists; shipped presets (`game_bake`, `preview`, `final_eevee`, `final_cycles`, `sprite_sheet`, `turntable_video`) must not contain either key; `load_preset` reports unknown keys as `unset` and still applies the rest. | Lead |
| C5 | File compression default is ON in 5.0. `save_blend(compress=None)` = preference `use_file_compression` (as the doc says) and the reply reports the effective `compress`; doc test 2 passes `compress=True` and `compress=False` explicitly on both files. | Lead |
| C6 | `active_asset_library` index shift (5.2 adds "All Libraries" and "Essentials" at 0 and 1): Tier 2 (`set_asset_libraries`) only; no B0 code reads that index. | Lead |
| C7 | Settings precedence for the server: environment (`BLENDER_HOST`, `BLENDER_PORT`, `BLENDER_EXE`, `IMG_TO_3D_*`) ABOVE `~/.blender_mcp/settings.json`, file above code defaults. `_find_blender_exe` order becomes hint > env > settings file > PATH > pool (A2 order amended at B0 start). | Lead |
| C8 | Deploy table (docs only): 5.2 add-ons path `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\`; there is no `D:\blender-5.2.1-windows-x64\MCP\` yet. Which live folders receive 2.x is a user decision recorded by Sybren, not built in B0. | Lead |
| C9 | The server-side cross-cutting change lands ONCE in one edit: a settings module that backs `DEFAULT_HOST`, `DEFAULT_PORT`, `_SAFE_IMAGE_MAX_PIXELS`, `_IMG_TO_3D_PORT`, the two 180 s socket timeouts, and a new `connect_timeout` (absent today) from the settings file with env override. | Lead |
| C10 | `run_on_files` is Tier 2 (out of B0); when built it stays synchronous per file. Sidecars `<file>.versions.json` and `<file>.mcp-profile.json` are written by the ADDON (it owns the file path); the server writes only under `~/.blender_mcp/` (settings, presets, session states). | Lead |
| C11 | Shared tools `set_frame_range` (rigging doc), `set_scene_units` (engine-readiness doc), `append_from_blend` (presentation doc) are built in B0, one owner each side (Ton handler, Campbell wrapper); the later docs consume them. | Lead |
| C12 | M9 rule for B0: no pre-existing tool name or parameter removed or retyped; adding params and tools is expected (`save_blend` +7, `load_blend` +4, `set_render_settings` +13, `start_blender` +2 per Campbell's recon, second-hand). Baseline re-cut as `tests/baseline_tools_2.1.txt` after B0-G3. The freeze on `get_blender_status` / `start_blender` lifts at the B0 assignment. | Lead |
| C13 | `enable_addon` / `disable_addon`: the doc's `addon_utils.enable(module, default_set=persist)` is wrong. On BOTH versions `default_set=False` registers the module but never adds it to `preferences.addons` (so `list_addons(enabled_only)` cannot see it and Rigify's own `register()` fails with `KeyError: key "rigify" not found`); `default_set=True` adds it and marks `preferences.is_dirty`. Contract: always `default_set=True` (and `disable(..., default_set=True)`); `persist` only decides whether `wm.save_userpref` is called afterwards (consent rule unchanged); reply reports `preferences.is_dirty`. A failed enable leaves classes half-registered (a retry raises `already registered as a subclass`): on failure call `addon_utils.disable(module, default_set=True)` inside `suppress` before returning the error. | Grace, evidence b0check `addons.enable_disabled_modules`, ENCHK runs |
| C14 | `save_blend(incremental=True)`: Blender's own naming is `a.blend -> a1.blend` on both versions (not `a_001`); a plain save afterwards leaves `a1.blend1` (the `save_version` preference default 1). Doc test 5 asserts `a1.blend`. | Grace, evidence b0check `save_roundtrip.save_incremental` |
| C15 | `revert_file` on 4.3.2 headless: a bare save/open/revert, save/incremental/revert and save/copy/save/revert all FINISH, but the full b0check sequence (save-as compressed, save-as copy uncompressed, incremental, plain save, revert) crashed Blender 4.3.2 with `EXCEPTION_ACCESS_VIOLATION` while re-reading `a1.blend`; 5.2.1 finishes. Not pinned further. The harness runs its revert step in a fresh process or before any incremental save, never as the last file op of a long sequence on 4.3.2; `revert_file` keeps the doc's "refuse when never saved" rule. ACCEPTED 11:37. | Grace, evidence a1.crash.txt, REVCHK/REVCHK2/REVCHK3 |
| C16 | (B0-Q1) `PROTOCOL = 1` for 2.1.0: legacy add-ons are detected by the unknown-command reply, so 2.1.0 is simply the first declared protocol. Where Campbell's GV-1/GV-2 text reads "protocol 2", read 1 (Q6 in section 7 flags the conflict for confirmation). Bump rule (GV-1): PROTOCOL bumps when a handler key is renamed or removed, or an existing payload/reply key changes meaning or type; purely additive keys with addon-side defaults do not bump. | Lead 11:34 |
| C17 | (B0-Q2, amended 12:15 by C31) Session state: the server persists the JSON under `<settings dir>/sessions/<name>.json` and never reopens a file; the add-on applies the state and, on `restore_session_state`, reopens the file named in it through `load_blend`'s dirty guard (`force`) in ONE call. `get_session_state` / `apply_session_state` stay as the no-reopen primitives for callers that already hold the file. | Lead 11:34, 12:15 |
| C18 | (B0-Q3) Live steps L7-L9 go in the settings doc's own test plan; the MIGRATION doc gets a one-line pointer. | Lead 11:34 |
| C19 | Ton's measured facts and design (B0PROBE, both installs; accepted through D-e and the 11:34 ruling): (a) `bpy.app.timers` do NOT fire in `--background` while a script sleeps, so the autostart timer path is untestable headless; tests call `ensure_server()` directly, the timer path is live (L7). (b) `load_post/load_pre/save_pre/save_post` + `@persistent` on both; `exit_pre` 5.2.1 only, `hasattr`-guarded. (c) A `sys.path`-imported addon.py is NOT in `preferences.addons` after `register()` (KeyError on both): `_prefs()` returns None headless and every consumer falls back to defaults; the harness never gets prefs; `_migrate_legacy_secrets(prefs)` takes the prefs object as a parameter so tests can pass a `SimpleNamespace`. (d) `StringProperty(subtype='PASSWORD')` OK on both. (e) `'localhost'` resolves to `['127.0.0.1', '::1']`, the addon binds AF_INET: the addon binds `127.0.0.1` explicitly, the server default host becomes `127.0.0.1`; L3 step 1 verifies. Design D-a..D-g: `BLENDERMCP_AddonPreferences` gains `port` (1024-65535, default 9876), `autostart_server` (default True), four `PASSWORD` keys; module helpers `_prefs()`, `_port()`, `_secret(name)` (prefs value, else legacy scene value read-only); module-level `ensure_server(port=None) -> {running, port, host, started_now}` used by the deferred timer, the panel operator, the new handler `ensure_server_running` (wire `{}` -> `{"success": True, "running", "port", "host", "started_now"}`, "# Lifecycle" section next to `quit_blender`) and exposed as `bpy.app.driver_namespace["blendermcp_ensure_server"]` for `start_blender`'s `--python-expr`; `register()` schedules the deferred timer when `(_restart_flag_get() or prefs.autostart_server) and not bpy.app.background` (autostart never opens a port headless), the timer also runs the one-time legacy-secret migration; `Scene.blendermcp_server_running` dropped, `_server_running()` reads `bpy.types.blendermcp_server`; legacy Scene secret props stay REGISTERED but undrawn through 2.2.x, removed in 2.3.0 (D34 amended the earlier 2.2.0 date); `@persistent` `load_post` re-runs the migration and leaves the server alone; `exit_pre` stops the server (4.x: `unregister()` path). Headless `ensure_server()` tests use `port=0` (ephemeral) and stop the server in `finally`; the default port is never opened by a test. `_render_settings` becomes `with self._settings_scope(("RENDER","OUTPUT","CYCLES","EEVEE"))`; `_scope_owner`: RENDER=`scene.render`, OUTPUT=`scene.render.image_settings`, CYCLES=`scene.cycles`, EEVEE=`scene.eevee`, COLOR=`scene.view_settings` + `scene.display_settings`, VIEWPORT=VIEW_3D shading/overlay when a window exists, extras `scene.camera`, `scene.frame_current`; snapshot every writable non-pointer non-collection RNA property; restore order media_type before file_format, engine before engine-specific props; failed restores collected into the reply, not raised. Doc corrections: `use_nodes` exists on 5.2.1 as a no-op; the "All Libraries / Essentials at indices 0/1" claim is UNCONFIRMED headless (`asset_libraries == ['User Library']` on both), do not build on it. | Lead, Ton m_a72ca7a040ade852ca9a, m_05e75642f115c39de58f |
| C20 | Campbell's server design (B0-1..B0-5, GV-3; accepted): new module `src/blender_mcp/settings.py` with zero imports from `server.py` or `mcp`; API `load() -> dict` (defaults < `~/.blender_mcp/settings.json` < environment), `get(key)`, `update(values)` (writes the file, refreshes memory), `path()`; read once at import, a missing or corrupt file logs ONE warning and falls back, never raises; env overrides `BLENDER_HOST`, `BLENDER_PORT`, `BLENDER_EXE`, `IMG_TO_3D_PORT`, `BLENDER_MCP_NO_UPDATE_CHECK`, `BLENDER_MCP_UPDATE_REPO`, plus NEW `BLENDER_MCP_SETTINGS_DIR` so tests never touch the user's real file. Import-time consumers: `log_level` (basicConfig), `image_max_pixels` (getter), `img_to_3d_port` (the `_IMG_TO_3D_URL` f-string becomes `_img_to_3d_url()`; precedence env > file > 7862). Call-time consumers: host/port (defaults 127.0.0.1 / 9876), `command_timeout` (the two 180 s literals), NEW `connect_timeout` (connect has none today), `blender_exe` (resolver order hint > env > settings file > PATH > pool > Steam > macOS), `output_dir`, `presets_dir`. `set_server_settings` calls `update()` then `_drop_connection()` iff host or port changed. The addon never reads this file. Param styles unchanged: comma strings for lists, JSON strings for structured data with the `add_modifier` `set/unset` reply shape, tri-state `bool = None` for "= preference". | Lead, Campbell m_a2efbc13be3022a7aff7, m_d79c715ce2cc8b07d777 |
| C21 | Pre-existing server defect riding with B0 (accepted): `send_command`'s catch-all drops the socket on every handler status error. Fix: `BlenderCommandError` for `status == "error"` replies, excluded from the `sock = None` branch; wrappers then read `Error: <message>` (no "Communication error" prefix). Acceptance: mocked-transport unit test, socket object unchanged after a status error, dropped after a transport error. Also: `get_blender_status` probes `127.0.0.1` exactly like `BlenderConnection` (AF_INET); the dual-stack `create_connection(('localhost', ...))` walk is the suspected cause of "not reachable while LISTENING" and is verified in L3. | Lead, Campbell |
| C22 | `get_version` and the mismatch prefix (GV-1, GV-2; accepted; PROTOCOL value per C16): addon command `get_version` -> `{"addon": "2.1.0", "protocol": 1, "blender": bpy.app.version_string, "python": platform.python_version(), "addon_file": <addon.__file__>, "background": bpy.app.background}`. Legacy detection: a 2.0.0 add-on answers `Unknown command type: get_version`; the server maps that substring to "pre-2.1 add-on, protocol assumed"; any other exception = `unreachable (<reason>)`. Server tool `get_version` (no params, str, never touches GitHub): lines `BlenderMCP server <ver> (protocol <n>)`, `Server module: <path>`, `Settings file: <path> (loaded | not created yet)`, `Blender add-on: <ver> (protocol <n>) on Blender <ver>, Python <ver>, file <addon_file> | unreachable (<reason>) | pre-2.1 (no get_version handler)`, `Compatibility: OK | MISMATCH. <prefix text>` (always present), `Update check: not run yet | disabled (BLENDER_MCP_NO_UPDATE_CHECK) | ...` (the GitHub check itself is B8+). Placement: `PROTOCOL` module constant directly under `bl_info` in addon.py; `__version__` then `PROTOCOL` in `src/blender_mcp/__init__.py` ABOVE the `from .server import ...` line; `server.py` does `from . import __version__, PROTOCOL`, no literal copies; `tests/test_version_sync.py` also asserts the two PROTOCOLs equal. Mismatch mechanism: `_probe_addon_version(conn)` in `get_blender_connection()` after `connect()`, beside the polyhaven probe, stores `_addon_info`; on version or protocol mismatch sets `_pending_notices["mismatch"]` = `[blender-mcp mismatch] Server <ver> / protocol <n> but the Blender add-on reports <ver> / protocol <n>. Wire formats differ; deploy addon.py and cycle the add-on before continuing.`; re-runs on every reconnect. Emission: one wrapper around `mcp.tool` installed after `mcp = FastMCP(...)` and before the first `@mcp.tool()` (`functools.wraps`, sync and async branches); `_attach_notice(result)` prepends `<notices>\n---\n` to the next `str` reply only, images untouched; once per session via `_mismatch_notified`, re-armed only after the versions are seen agreeing again; no lock needed (single event loop, no await in `_attach_notice`). Acceptance: tools/list dump byte-identical before/after the wrapper. | Lead, Campbell m_f5e22bf111b5482c9f10, m_c4aa054c8d3b592a27c9 |
| C26 | Deploy list for 2.1.0 = SIX files: `addon.py`, `src/blender_mcp/server.py`, `__init__.py`, `settings.py`, `presets_default.json` (NEW data file, 6 shipped presets), `pyproject.toml` (+ `img_to_3d_server.py` unchanged). Sybren records it. | Lead 11:56 |
| C27 | Server seam rulings during landings: `load_blend` gained 5 params (`save_first` is in the doc's dirty-guard text), accepted; NO `ensure_server_running` wrapper on the server (over the socket it is trivially running; `get_blender_status` covers the report); `save_blend` wrapper must not forward `filepath` when `incremental=True` (send it only when given); F10: `get_version` must print `Compatibility: unknown (add-on unreachable)` when the add-on is unreachable (not `OK`); the OUTPUT scope accepting a nested `ffmpeg` object in `set_settings` (turntable_video preset) is an open seam Ton lands and Campbell reconciles. Campbell's L4 = one reconciliation landing of every wrapper against Ton's landed reply keys + F10 + F12 + F13 + the ffmpeg seam (closed addon-side in L3.1) + the three doc parameters the wrappers do not send yet (`set_scene_units(preset=, rescale_objects=)`, `get_project_profile(apply_units=)`), after Ton's L3.1 (`81038008`). Campbell's AST payload map (60 entries, handler key + payload keys per wrapper) is the seam authority for Ton's L2/L3 signatures: `<scratchpad 5041fb5c>/b0s_payload_map.txt`. | Lead 11:56, 11:58 |
| C28 | MEASURED (Ton, L2): on 5.2.1 `--background`, `bpy.data.is_dirty` stays False after edits (`primitive_cube_add` leaves it False; no undo pushes headless), while 4.3.2 headless reports True from startup. `load_blend`'s dirty guard therefore fires headless only on 4.3.2; doc test 3 (refusal when dirty) is 4.3.2-headless / 5.2.1-live. Harness rule: assert the guard iff `bpy.data.is_dirty` was True before the call. Extends S11/S21. | Lead 11:58 |
| C29 | (Lead 11:59, cited as "C25" in the room; C25 is the M7 row) `output_dir` RULING: image-RETURNING tools that read and delete their temp PNG (`render_from_camera`, `capture_viewport_angle`, `capture_contact_sheet`, `render_depth_map`, `diff_images`, `compare_reference_image`) are EXEMPT: the image is the product, no file is kept. `output_dir` is the default for tools that LEAVE files when their path is omitted: `render_all_cameras` (its `output_dir` param default), `export_object` (pathless), `save_blend` / `save_copy` on a never-saved file (currently "saved to temp"), and any future `playblast` `video_path`. Decisive fact: a default directory only means something for a file the user gets to keep. Doc test 17 is re-pointed at `render_all_cameras` + `save_blend`-unsaved, assertion strength unchanged. L4 findings for Campbell: F12 `_find_blender_exe` never reads the settings-file `blender_exe` (C7 order hint > env > settings file > PATH > pool; repro: settings.json `blender_exe` set, `BLENDER_EXE` unset -> pool wins); F13 `output_dir` has no consumer. B0-G2: `2123e09d` green on everything else (units 24/24, version sync 6/6 at 2.1.0, test_b0_server 16/18 with F12/F13 the two fails); Sybren writes server rows from the docstrings at that hash, L4 gets a delta pass. F14 (Ton, L3.1, 12:09): the add-on registered `save_session_state` / `restore_session_state` with add-on-side persistence, but the contract (C17, B0-Q2) is `get_session_state(include) -> {success, state}` and `apply_session_state(state, load_file, force)` with the SERVER persisting `<settings dir>/sessions/<name>.json`; Campbell's wrappers already send those keys (`Unknown command type: get_session_state` on disk at `cdfdc5bb`). Fix: add the two C17 handlers (state dict in/out, no file I/O in the add-on), drop the add-on-side persistence or keep the old two as thin aliases; one landing, hash post. Sequencing: Campbell L4 GO on `cdfdc5bb` (F10, F12, F13, reconciliation, session wrappers keep the C17 keys); Bastien runs the full `--b0` harness on `cdfdc5bb` + `2123e09d` as the pre-G3 baseline, then again after L3.1 + L4 for B0-G3. | Lead 11:59, 12:09 |
| C30 | Harness facts from Ton's L3 (both installs): (1) `preferences.addons.new()` + `entry.module = "addon"` makes `_prefs()` real headless, so `set/get_addon_settings` (port 9877, keys never echoed) and the secret migration into prefs are testable; the harness removes the entry in `finally` and never calls `save_userpref`. (2) DANGER: `recover_file(mode="LAST_SESSION")` headless OPENS THE USER'S REAL `quit.blend` (it loaded `B:\Item_Retriever_Console.blend` into a harness run once); the harness asserts only the BOGUS-mode error and the AUTOSAVE missing-file error, never calls LAST_SESSION. Recorded as risk R8. | Ton 12:07 |
| C31 | Session seam RULED by hash (Lead 12:15): the L4 wrappers send `save_session_state {name, include}` and `restore_session_state {name, state, load_file, force}`, both registered on Ton's frozen `81038008` (the C17 `get_session_state` / `apply_session_state` keys also exist there as the add-on-side pair); the server persists `<settings dir>/sessions/<name>.json`; restore is ONE call in which the add-on reopens the file (`force`) and applies the state, reply `file_loaded` + `not_restored`. Decisive fact: the file path and the dirty guard live in the add-on, so one add-on call gives one guard check and one reply instead of a `load_blend` + apply round trip that can half-apply. C17 amended accordingly. Bastien adds one headless step to the server-half report: `restore_session_state` with a saved file path -> `file_loaded` True on both installs, and the dirty-guard refusal on 4.3.2 without `force`. Campbell re-runs the AST reconciliation against `81038008` for the record (no code change). | Lead 12:15 |
| C25 | M7 definition from B0 on (Lead 11:49): the harness step greps the COMPARISON form `bpy\.app\.version\s*(>=|<=|==|!=|<|>)` and requires 0 hits; plain reads of `bpy.app.version_string` (and `bpy.app.version` for reporting) are allowed, `get_version` needs them. House rule 4 unchanged in substance. | Lead |
| C24 | Version-bump rule for B0 (Lead 11:42): the "A1.1 accepted" post is the 2.1.0 bump signal. All three sources move in the FIRST landing after it, within the same few minutes: Campbell `__init__.py` `__version__ = "2.1.0"` (L1), Ton `bl_info = (2, 1, 0)` (L1), Sybren `pyproject.toml` 2.1.0 immediately at the signal. Bastien runs `test_version_sync` at 2.1.0 only after all three have reported; before that it is expected to be transiently red. Campbell's L1 staged evidence (40/40 incl. C12 superset, BlenderCommandError, precedence, mismatch-once) accepted pending the repo landing. | Lead |
| C23 | Comment-audit cleanup items batched into B0 (Sybren's `b0-docs-notes.md` section 1, line numbers on hash 3c726b58): addon F9, A3-A8 incl. the `set_texture` duplicate-node second pass (Ton); server C1-C6 with C6 = read `IMG_TO_3D_PORT` env > settings file > 7862 (Campbell). Cosmetic, no wire change. | Lead, Sybren m_c62c6078049a54e54cc5 |

### 9.2 Slices (file ownership)

| Slice | Owner | Files | Content | Gate |
|---|---|---|---|---|
| B0-A addon | Ton | `addon.py` | Three landings: (1) helpers `_settings_scope` / `_scope_owner` + preferences move D-a..D-e + `ensure_server` + `get_version` + `PROTOCOL` + cleanup C23; (2) section 1 fixes; (3) Tier 1 handlers table by table. Design per C19. Section 0.1 items 1, 2, 5: `autostart_server` preference + deferred start from `register()` via `bpy.app.timers`; runtime running check replaces the `blendermcp_server_running` Scene property; `load_post` handler leaves the server alone; addon command `ensure_server_running -> {running, port, host, started_now}`, panel button calls the same function. Section 1 fixes: `save_blend` (+`compress, relative_remap, copy, incremental, backup, overwrite, purge_orphans`, unsaved-file warning, reply `path, bytes, is_dirty, elapsed, compress`), `load_blend` (+`force, save_first, load_ui, use_scripts, revert_on_fail`, dirty guard, reply `previous_file, blender_version_of_file, unsaved_changes_discarded`), `set_render_settings` routed through `set_settings(scope="RENDER")` with the 13 extra params, `get_scene_info` `file` + `settings_summary`, port and API keys moved to `BLENDERMCP_AddonPreferences` (keys `subtype='PASSWORD'`) with the one-time scene-to-prefs migration in `register()`, `_render_settings` refactored onto `_settings_scope`. Section 3 helpers `_settings_scope(scopes)`, `_scope_owner(scope)`. Every Tier 1 handler in section 2.1 that touches `bpy` (file lifecycle, settings generic, typed conveniences, `apply_blender_preset`, `list_blender_presets`, project profile + sidecar, add-ons, workspaces, `get/set_addon_settings`, session-state capture/apply). `get_version` command, `PROTOCOL = 1`, `bl_info` 2.1.0. | B0-G1 |
| B0-S server | Campbell | `src/blender_mcp/server.py`, `src/blender_mcp/__init__.py`, NEW `src/blender_mcp/settings.py` | Three landings: (1) `settings.py` + the C9/C20 module-level change + `BlenderCommandError` (C21) + `PROTOCOL`/`__version__` placement (C22); (2) 0.1 items 3-4 + `get_version` + mismatch wrapper (C22) + `get/set_server_settings`; (3) the Tier 1 wrappers, presets, session files. The C9 settings module (`~/.blender_mcp/settings.json`, keys per doc: `host, port, connect_timeout, command_timeout, blender_exe, output_dir, presets_dir, image_max_pixels, log_level, img_to_3d_port`; env override C7; `_drop_connection()` on host/port change). `get/set_server_settings`. Section 0.1 items 3-4: `start_blender(start_server=True, restore_session=None)` with the `--python-expr` belt-and-braces path and the two-cause timeout text; `get_blender_status` probes exactly like `BlenderConnection` (fix the localhost/127.0.0.1 or no-request discrepancy). One `@mcp.tool()` wrapper per Tier 1 tool (Campbell's count: ~55) in the doc's reply shapes; server-side files for `save/load/list/delete/export/import_preset` (values through `get/set_settings`) and `save/restore_session_state` JSON; the six shipped presets; `get_version` tool; `PROTOCOL = 1`; `__version__ = "2.1.0"`. | B0-G2 |
| B0-T tests | Bastien | `tests/` (extend `headless_handlers.py`; `tests/test_server_units.py`; `tests/baseline_tools_2.1.txt` after G3) | Doc section 6 steps 1-18 with C3 and C5 applied, on both installs; step 14 asserts the refusal only; step 13 asserts `userpref.blend` mtime unchanged. Plus: C12 superset check; `test_version_sync` at 2.1.0; settings precedence unit test (env > file > defaults, C7) and `connect_timeout` honoured; `ensure_server_running` reply shape (headless: `started_now` may be False, `running` reflects the real socket); secrets migration (step 15) with a scene that carries a legacy key. Autostart on a cold GUI launch is live (L7, user). | B0-G3 |
| B0-D docs | Sybren | `TOOLS.md`, `README.md`, `docs/FEATURE-REQUEST-settings-save-load.md` (status + C3-C8 into its facts section), `docs/FEATURE-REQUEST-update-notification.md` (section 2.1 marked built in 2.1.0), `docs/README.md` (known-bugs list), `/blender` skill, `pyproject.toml` 2.1.0, memory note, MemPalace drawer | TOOLS.md sections "File Lifecycle", "Settings & Presets", "Add-ons & Preferences", "Session"; README tool count, category table, settings-file and consent rule, "re-enter API keys once" note; skill "Start of session" pattern (`get_file_state -> set_workspace -> load_preset -> work -> save_version`) and the consent rule; memory: settings file location, presets dir, secrets migration, `PROTOCOL`; MemPalace: decisions drawer for the secrets move and the consent rule; deploy table C8. | B0-G4 |
| B0-P plan/API | Grace | `docs/PLAN.md`, `docs/apichecks/b0check.py` + 2 appended lines per `out_*.jsonl` | Section 9.5 facts on both installs before B0-A starts on the affected handlers; answers by section number + evidence key. | B0-G0 |

### 9.3 Sequencing and gates

```
B0-G0  DONE 11:37 (b0check on both installs; C13-C15 accepted). Sybren corrects the doc's
       facts section from 9.5 and C19 (Lead gate: partially open, as in A4).
Start  "A1.1 accepted" (D28). Ton and Campbell each deliver in three landings (9.2); the T1
       order below holds inside each landing.
T1     Ton: helpers first (_settings_scope, _scope_owner), then preferences move + migration
       + autostart (0.1), then section 1 fixes, then Tier 1 handlers table by table in the
       doc's order.                                                      } parallel
       Campbell: C9 settings module in ONE edit, then 0.1 items 3-4, then wrappers against
       the doc's reply shapes (no addon needed to write them), presets + session files.
       Bastien: doc section 6 steps as harness cases from the doc, C3/C5 applied.
       Sybren: drafts in scratchpad; doc facts corrections from B0-G0.
B0-G1  Ton: every new handler smoke-run headless on both installs, pasted; grep
       "bpy.app.version" = 0; scene has no blendermcp_port / key props after register().
B0-G2  Campbell: tools/list superset vs tests/baseline_tools_4.3.txt (C12), import OK,
       settings precedence unit test PASS, get_version tool output pasted with addon
       unreachable and (headless harness) reachable.
B0-G3  Bastien: full harness (Phase A cases + section 6 steps) on both installs, FAIL 0,
       pasted. Lead re-runs. Baseline re-cut to tests/baseline_tools_2.1.txt.
       HEADLESS HALF DONE 12:13: addon.py 81038008 + server.py 2123e09d = 164/164 PASS on
       4.3.2 AND 5.2.1 (Phase A 133 steps + M2 over all 136 handlers + settings doc
       steps 1-16, 18 addon side with C3/C5/C13/C14/C15/C17/C28 applied, get_version,
       ensure_server_running port=0, secrets migration, link_from_blend, addon prefs);
       logs tests/run_b0_*.log.
       SERVER HALF DONE 12:19 on server 598a765d / addon 81038008 / __init__ 3410b362 /
       settings 2bbc200b (pinned, unchanged through the run): venv 49/49 (units 24,
       version sync 6 at 2.1.0, test_b0_server 19 incl. F10/F12/F13 + the C31 one-call
       restore). Headless re-run with the C31 step: 165/165 on 4.3.2 AND 5.2.1
       (restore_session_state reopens the saved file, file_loaded True both; dirty guard
       refuses without force on 4.3.2). Baseline re-cut: tests/baseline_tools_2.1.txt
       (148 names, source order) + tests/baselines/tools_list_2.1.json.
       Both halves green from the Verifier.
       B0-G3 PASSED 12:19 = PHASE B0 HEADLESS COMPLETE. Frozen: addon.py 81038008,
       server.py 598a765d, __init__.py 3410b362, settings.py 2bbc200b,
       presets_default.json 0a58e8c3. Remaining B0: B0-G4 docs review by the Lead
       (Sybren posts when the header re-cut, MemPalace drawer and docs/README build-order
       row are in); B0-G5 live L7-L9 joins the user's batch with M14.
       The same post = 2.2.0 bump signal (C24 pattern) and B1 GO for every slice (section 10).
B0-G4  Sybren: docs; Lead review.
B0-G5  Live (user, Lead's gate): L7 cold launch with autostart on -> get_scene_info within
       10 s, no clicks; file saved while connected reopened with the preference off shows
       the panel not running; L8 start_blender(start_server=True) path; L9 save/restore
       session state across close_blender/start_blender.
```

### 9.4 4.x/5.x switches for B0 (Verifier covers each on both installs)

| # | Where | Switch | Evidence |
|---|---|---|---|
| S11 | `get_file_state`, tests | `bpy.data.is_dirty` False at 5.2.1 startup, True at 4.3.2 | scheck `data_flags` |
| S12 | `save_blend`, `save_copy` | compression default on (5.0); pass `compress` explicitly, report it | C5 |
| S13 | presets, `describe_settings("EEVEE"/"COLOR")` | `use_gtao`, `use_hdr_view` absent on 5.2; everything `bl_rna`-driven, presets omit them | scheck diff |
| S14 | `append_from_blend`, `link_from_blend`, `list_only` | `libraries.load` is keyword-only on 5.2 (`pack, set_fake, recursive, reuse_local_id, clear_asset_data` added); pass every arg by name | scheck `libs_load_sig` |
| S15 | `save_blend` | `wm.save_mainfile` gained `show_save_modified_images_dialog` (5.2); never pass it | scheck diff |
| S16 | `describe_settings("PREFS_FILEPATHS")` | `save_modified_images`, `texture_cache_directory` exist only on 5.2; auto-listed | scheck diff |
| S17 | `set_color_management` | `view_transform` enum reads `NONE`/empty headless on both; try-assign only | doc facts; b0check |
| S18 | `set_render_device` | `compute_device_type` enum empty headless; try-assign, report `get_devices()` | doc facts; b0check |
| S19 | `get_file_state.file_version` | factory file reports `(3, 6, 10)` on 4.3.2 and `(5, 1, 16)` on 5.2.1: the saving version, never asserted | scheck `data_flags` |
| S20 | `set_project_profile` | `scene["blendermcp_profile"]` nested dict id-prop round-trips as `IDPropertyGroup` with `to_dict()` on both versions | b0check `scene_idprop` |
| S21 | `save_blend` reply `is_dirty` | after `save_as_mainfile` headless, `bpy.data.is_dirty` is True on 4.3.2 and False on 5.2.1: report it, never assert it | b0check `save_roundtrip.save_as_compress.is_dirty` |
| S22 | `set_color_management` | `view_transform` enum reads `["NONE"]` headless on both; try-assign accepts Standard / AgX / Filmic / Khronos PBR Neutral / Raw on both; the live tuple in the error differs (5.2.1 adds `ACES 1.3`, `ACES 2.0`; 4.3.2 has `Filmic Log`): quote the exception text, same as the engine idiom | b0check `view_transform` |
| S23 | `set_render_device`, `list_render_devices` | `compute_device_type` enum empty headless on both; try-assign accepts NONE/CUDA/OPTIX/HIP/ONEAPI, METAL refused on Windows; `get_devices()` returns None and fills `prefs.devices` (RTX 4090 CUDA use=True, CPU use=False, RTX 4090 OPTIX use=True); `scene.cycles.device` enum `CPU / GPU` | b0check `cycles_devices` |
| S24 | `enable_addon` | see C13; `preferences.is_dirty` stays True after a disable on both | b0check `addons` |
| S25 | `append_from_blend` | `libraries.load` refuses the currently open file ("Cannot load from the current blend file") and the `link` argument is keyword-only on BOTH versions (positional raises) | b0check `libraries_load` |

### 9.5 Facts verified on both installs (b0check.py, 2026-09-11; lines under `### b0check.py` in `out_4.3.2.jsonl` (32 lines now) and `out_5.2.1.jsonl` (24 lines now); scheck.py already covers the wm.* lifecycle kwargs, preset paths, `script.execute_preset`, prefs sections)

- `file.pack_all`, `file.make_paths_relative/absolute`, `file.pack_libraries`: no kwargs. `file.unpack_all.method` = `USE_LOCAL / WRITE_LOCAL / USE_ORIGINAL / WRITE_ORIGINAL / KEEP / REMOVE`. `file.find_missing_files(find_all, directory, ...)`. `outliner.orphans_purge(do_local_ids, do_linked_ids, do_recursive)`. Same on both. / `file.*`, `outliner.orphans_purge`.
- `wm.save_mainfile` has `compress, relative_remap, exit, incremental` on both (+`show_save_modified_images_dialog` on 5.2.1, S15). Compressed save 92-96 KB vs 459-567 KB uncompressed copy; `save_as_mainfile(copy=True)` leaves `bpy.data.filepath` unchanged; incremental -> `a1.blend` (C14). `use_file_compression` preference default: False on 4.3.2, True on 5.2.1 (S12). / `save_roundtrip`.
- Startup flags: 4.3.2 `is_dirty True`, 5.2.1 `False`; `is_saved False`, `filepath ""` on both (C3, S11); `bpy.data.version` = saving version (S19). / `data_flags_startup`.
- `libraries.load(fp, link=False, relative=False)` lists `objects` / `materials` and appends `Cube` as `Cube.001` on both; positional `link` rejected on both; cannot load from the open file (S25). / `libraries_load`.
- Scene id-prop nested dict round-trips on both (S20). / `scene_idprop`.
- `AddonPreferences` with `IntProperty`, `BoolProperty`, `StringProperty(subtype='PASSWORD')` registers and unregisters headless on both; `subtype` reads `PASSWORD`. / `addon_prefs`.
- `bpy.app.handlers.load_post/load_pre/save_pre/save_post` on both, `exit_pre` 5.2.1 only; `bpy.app.timers.register` works from a script. `preferences.is_dirty False`, `use_preferences_save True` at startup. Config dir `%APPDATA%\Blender Foundation\Blender\<ver>\config`. / `handlers`, `timers`, `prefs_flags`, `config_dir`.
- `unit_settings` props `system, system_rotation, scale_length, use_separate, length_unit, mass_unit, time_unit, temperature_unit` on both; workspaces list identical on both; `bpy.context.window` is not None headless. / `unit_settings`, `workspaces`, `window_available`.
- `image_settings` props on 5.2.1 include `media_type, file_format, color_mode, color_depth, quality, compression, exr_codec, ...` (D6). / `image_settings`.
- View transform, Cycles devices, add-on enable: S22, S23, S24/C13. / `view_transform`, `cycles_devices`, `addons`.
- `addon_utils.enable(module_name, *, default_set=False, persistent=False, refresh_handled=False, handle_error=None)` on both; `disable(module_name, *, default_set=False, refresh_handled=False, handle_error=None)`; `check(module_name)`. `rigify` and `io_anim_bvh` present on both; `bpy.ops.preferences.addon_enable(module="rigify")` FINISHED on 5.2.1 in a fresh process. / `addons`.

### 9.7 Landing log

| Landing | Hash (git hash-object) | Accepted | Evidence |
|---|---|---|---|
| B0-A L1 (Ton) | `addon.py` `39162cbfa8580b4575aab954a50741d76d8947df` | 11:52 | bl_info (2,1,0), PROTOCOL = 1; compiles on 3.11.9 and 3.13.13; version-compare grep 0; `_prefs/_port/_secret/_migrate_legacy_secrets(prefs)/_server_running/ensure_server(port)`, `load_post` + `exit_pre` handlers, `driver_namespace["blendermcp_ensure_server"]`, server binds 127.0.0.1 and learns its bound port (`port=0` works), commands `get_version` (keys `addon, blender, python, protocol, addon_file, background` = C2/C22, the seam authority) and `ensure_server_running`, `_scope_owner`/`_settings_scope` with `_render_settings` on top, AddonPreferences port/autostart/4 PASSWORD keys, `Scene.blendermcp_port` + `blendermcp_server_running` removed, 4 legacy secret props hidden until 2.2.0, 17 secret consumers read prefs first; 87 handlers. Lead: migration 20/20 + 21/21, no server in `--background`, `ensure_server_running(port=0)` real TCP connect. Bastien: Phase A 133/133 both, B0-15b/19/20 PASS both; 24 remaining FAILs are unlanded L2/L3 commands. |
| B0-S L1 (Campbell) | `server.py` `e08196685da11764aff7360e5f31f7202757c486`, `__init__.py` `3410b3629a4bd92d3eb91880eb60a0bfb6bc688d`, NEW `settings.py` `2bbc200bed65028afbf2a79927ca2fd248320e09` | 11:54 (F10 to L4) | settings loader (env > file > defaults, `BLENDER_MCP_SETTINGS_DIR`, never raises); one-edit rewiring (DEFAULT_HOST 127.0.0.1, image_max_pixels, log_level, both 180 s timeouts, NEW connect_timeout, img_to_3d port/url getters); `BlenderCommandError`; FastMCP instructions; notice wrapper before the first tool; `_probe_addon_version` + legacy detection; tools `get_version` / `get_server_settings` / `set_server_settings`; `__version__` 2.1.0 + PROTOCOL 1 above the server import. Campbell test_l1 40/40, settings unit tests 15/15; Lead: 95 tools, 92 baseline present, 0 removed/retyped, precedence measured, user's `~/.blender_mcp` untouched. All three version sources at 2.1.0 (C24 satisfied). |
| B0-S L2 (Campbell) | `server.py` `7a035061fa6f3dea5b20778271e4f1b5cb7de937` | 11:56 (F11: landed before its post; house rule 8 born here) | `start_blender(+start_server=True, +restore_session=None)`: GUI launch appends `--python-expr` with a 1 s timer hook calling `driver_namespace['blendermcp_ensure_server']`, background launch calls it directly; two-cause timeout text; `_probe_port` AF_INET + connect_timeout, identical to `BlenderConnection.connect` (dual-stack discrepancy closed); `save_session_state` / `restore_session_state` (addon `get_session_state` / `apply_session_state`, server persists `<settings dir>/sessions/<name>.json`); `save_blend` +7, `load_blend` +5, `set_render_settings` +13. test_l2 17/17; 97 tools, C12 violations 0. |
| B0-S L3 (Campbell) | `server.py` `2123e09d80f9f041b874fe7aa94dae1a3796eff1`, NEW `presets_default.json` `0a58e8c3218ee536badfc35d2e4d8ba4066c577a` | 11:56 = B0-S COMPLETE (server side); frozen until Ton's L2+L3, then L4 reconciliation | Every Tier 1 table: file lifecycle 15, settings generic 7 (scope SERVER answered without Blender), typed conveniences 9 (incl. C11 shared tools), presets/profiles 10, add-ons/workspaces 10. Six shipped presets, none naming `use_gtao` / `use_hdr_view` (C4); a user preset shadows a shipped one. 148 tools (92 + 56), C12 violations 0, no duplicate names, every new tool has docstring + Parameters. B0-G2 = Bastien's green `test_b0_server.py` run on this hash. |
| B0-S L4 (Campbell) | `server.py` `598a765dc9a09670e0baebb4b0c25fa757dec7c2` (other three files unchanged) | 12:15 VERIFIED by the Lead (F10/F12/F13 measured, 148 tools, C12 0, user's `~/.blender_mcp` untouched); server.py FROZEN at this hash; seam check CLOSED by C31; F14/C29 closed | F10 `Compatibility: unknown (add-on unreachable)` / `MISMATCH. ...` / `OK (versions and protocols match)` only after a real read; F12 resolver order hint > `BLENDER_EXE` > settings-file `blender_exe` > PATH > pool > Steam > macOS (measured: settings 4.3.2 wins with env unset); F13/C29 `_default_output_path`: `render_all_cameras` under `output_dir` when its param is omitted, pathless `export_object` -> `<output_dir>/<name>.<fmt>`, `save_blend` on a never-saved file -> `<output_dir>/untitled_<stamp>.blend` after a `get_file_state` pre-check (temp fallback for a pre-2.1 add-on), reply ends with a Note saying where and why; `save_copy` exempt; `save_blend` sends `filepath` only when given. Reconciliation by AST over 139 `send_command` sites, 0 problems, BUT against `cdfdc5bb` (L3) not the frozen `81038008` (L3.1); session wrappers described as `save_session_state {name, include}` + `restore_session_state {name, state, load_file, force}` with the add-on reopening the file, which contradicts C17 / F14 (get/apply, server reopens) and Ton's L3.1 text. Open as the seam check posted 12:1x; outcome -> C31. 148 tools, C12 violations 0; settings tests 15/15; test_l2 17/17 after the mock fix. Reconciliation RE-RUN against `81038008` (12:18): 136 addon keys, 141 `send_command` sites, 0 problems; addon keys no wrapper sends = `get_session_state`, `apply_session_state` (primitives), `ensure_server_running` (ruled out). Leftover RULED 12:18: `get_project_profile(apply_units=)` rides with B1-S L1 (a doc param no B0 test calls; not worth a hash churn before the gate). Campbell frozen at this hash; B0-G3 waits only on Bastien's server-half venv report + the `restore_session_state` reopen step. |
| B0-A L3.1 (Ton) | `addon.py` `81038008f474d3b371a1844705d9f3cddd13d4db` | 12:12 VERIFIED by the Lead both installs (session state `{active, blender, camera, file, frame, mode, name, saved_at}` round-trips, nested ffmpeg sets `ffmpeg.format` / `ffmpeg.codec`, 136 dispatch keys); B0-A COMPLETE, addon.py FROZEN at this hash until B1; Bastien's full `--b0` run = headless half of B0-G3 | Seam reconciliation against Campbell's payload map, 138 handler keys: F14 fixed with new handler keys `get_session_state {include}` and `apply_session_state {state}` (state applied without reopening the file, the server does that; old two kept as aliases); OUTPUT scope accepts the nested `{"ffmpeg": {format, codec, constant_rate_factor, ...}}` object and `get_settings("OUTPUT")` returns it (preset round-trip, C27 seam closed); `set_frame_range` accepts `frame_start/frame_end/frame_step/current_frame` beside `start/end/current`; `set_scene_units` accepts `mass_unit / time_unit / rotation_unit` beside `preset / rescale_objects`. L3 harness 31/31 both; regressions unchanged both. Every payload-map key honoured. Open for Campbell L4: the wrappers do not yet send `set_scene_units(preset=, rescale_objects=)` and `get_project_profile(apply_units=)` (doc parameters). |
| B0-A L3 (Ton) | `addon.py` `cdfdc5bba4e181b1c987081b5f9c3ce35d2009db` | 12:09 VERIFIED by the Lead except F14 (session seam, L3.1 pending) | 49 new handlers under "# Settings, save and load (B0 Tier 1)": every Tier 1 table; 134 handler keys counted from the dict on disk (Ton's post said 136); 6667 lines; compiles on 3.11.9 / 3.13.13; version-compare grep 0. C13 built in (`default_set=True`, unwind on failure). Validation change: enum values whose static list is bogus headless (`view_transform` `['NONE']`, `length_unit` `['DEFAULT']`) fall through to assignment and Blender's live tuple is the error reason (D9 idiom). L3 harness 28/28 both (doc steps 1-16, 18 addon side + extras); regressions L2 16/16, L1 12/12, A1 19/19, A1.1 13/13, M1, all both. Harness facts (C30). |
| B0-A L2 (Ton) | `addon.py` `e0d7966cf58f8f24d826cc3ebb3ca361c1ddd37b` | 11:58 (pending Bastien) | Section 1 fixes (`save_blend` +7 with reply `{success, filepath, bytes, is_dirty, elapsed, compress, copy, incremental, save_versions, purged}`; `incremental=True` with a filepath -> teaching error, without -> `a1.blend` beside the open file; `load_blend` +5 with dirty guard/revert, reply `previous_file`, `blender_version_of_file`; `set_render_settings` +13 through new `_apply_settings` / `_rna_set_validated` RNA validation with set/unset reply; `get_scene_info` `file` + `settings_summary`) + C23 cleanup + 7 non-ASCII error strings normalised. L2 harness 16/16 both; regressions L1 12/12, A1 19/19, A1.1 13/13 both. C28 measured here. |

### 9.6 Open for the Lead

None. B0-Q1..Q3 are ruled (C16-C18). The PROTOCOL value conflict between the ruling and
Campbell's accepted draft is Q6 in section 7 (resolved: 1).

## 10. Phase B1 slice amendment (B1.1): rigging and animation

Start condition: B0-G3 passed (Lead's post is also the 2.2.0 bump signal, rule C24).
Contract = `docs/FEATURE-REQUEST-rigging-and-animation.md` sections 1 (architecture, 1.4
helpers), 2 (six fixes to existing tools), 3.1 (Tier 1, every row), 4 (ripple points),
5 (gotchas, corrected by K-rows below), 6 (tests 1-11). Tier 2: see 10.4. Tier 3 OUT.
Version 2.2.0 in the three sources. Evidence: `B1CHECK` lines in `out_4.3.2.jsonl` /
`out_5.2.1.jsonl` (script `docs/apichecks/b1check.py`, 2026-09-11) plus the older
`APICHECK` / `APICHECK2` lines; keys cited as `b1check.<section>.<key>`.

### 10.1 Contract deltas (versioned; the doc text stands where not listed)

| # | Delta | Evidence / ruled by |
|---|---|---|
| K1 | Target version 2.2.0 (doc says 1.7.0; doc section 4 item 5 reads 2.2.0). | Lead |
| K2 | Action data access switch, one addon helper used by every animation reader/writer: `_action_channels(id_obj, ensure=False)` returns `(fcurves, groups)`: if `hasattr(action, "fcurves")` -> `action.fcurves`, `action.groups` (4.x); else `bpy_extras.anim_utils.action_get_channelbag_for_slot(action, ad.action_slot)` (5.x; `None` = no channels yet = empty; `action_ensure_channelbag_for_slot` when `ensure=True` for writes) -> `channelbag.fcurves`, `channelbag.groups`. Both collections support `.find(data_path, index=)`. `keyframe_insert` itself is unchanged on both. Affected: `get_animation_info`, `set_keyframes`, `add_keyframe` reply, `delete_keyframes`, `set_keyframe_interpolation`, `list_actions` (fcurve count), `bake_action` reply (fcurve count). `bake_action` behaviour unchanged. | b1check `keyframes.action_attrs`, `legacy_fcurves`, `channelbag`, `anim_utils_funcs` (4.3.2 lacks the channelbag functions) |
| K3 | `assign_action(target, action)`: after `ad.action = action`, on 5.x `ad.action_slot` is `None` while `ad.action_suitable_slots` lists the matching slot; the tool sets `ad.action_slot = ad.action_suitable_slots[0]` (guarded by `hasattr(ad, "action_slot")` and a non-empty list) and reports `slot`; with no suitable slot it reports `slot: null` and a warning that nothing animates until a key is inserted. 4.x: no slots, reply `slot: null`. `create_action` on 5.x: see 10.5 (slot creation path to verify). | b1check `keyframes.assign_action_slot_auto` (null) vs `assign_action_suitable_slots` (["OBb1_arm"]) on 5.2.1 |
| K4 | Bone selection idiom, helper `_select_bones(arm_obj, names=None, select=True)`: per pose bone `if hasattr(pb, "select"): pb.select = v else: pb.bone.select = v` (Bone.select / select_head / select_tail exist on 4.x only, PoseBone.select on 5.x only); same for hide (`PoseBone.hide` 5.x, `Bone.hide` both). Used by `bake_action`, `mirror_bones`, `delete_bones`, `render_weight_map`, and any op that reads bone selection. | b1check `select_attrs`, `nla_bake.selected_via` |
| K5 | EditBone references are UNDEFINED after leaving EDIT mode on BOTH versions: Grace's run read garbage on 4.3.2 and "child.L" on 5.2.1, Bastien's run read garbage on 5.2.1 too (M10 re-run). `_armature_edit` yields `edit_bones`, every caller returns NAMES. | b1check `build_rig.editbone_after_exit`; Bastien 11:54 |
| K6 | `armature.symmetrize`: `copy_bone_colors` exists on 5.2.1 only; call with it inside `try/except TypeError` then retry with `direction` only. Reply lists bones created (name diff before/after); note the op mirrors only selected bones with L/R style names. | b1check `symmetrize.kwargs`, `call_with_copy_bone_colors` (4.3.2 "keyword unrecognized") |
| K7 | `export_object` FBX `mesh_smooth_type` validated against the live enum (`OFF/FACE/EDGE` on 4.3.2, `+ SMOOTH_GROUP` on 5.2.1; error lists the live enum, D9 idiom). glTF `export_format` enum reads EMPTY headless on both: pass the value through and let the operator's error speak (try-assign idiom). All FBX/glTF kwargs the doc names exist on both and a real rigged export FINISHES on both. | b1check `exporters.fbx_mesh_smooth_type`, `fbx_smooth_group`, `gltf_export_format`, `fbx_export`, `gltf_export`; APICHECK `fbx`, `gltf` |
| K8 | `import_file` reply: `armatures`, `actions`, `new_actions` as the doc says, plus `bone_shape_objects`: the glTF importer adds an `Icosphere` MESH used as bone custom shape on BOTH versions; it is reported there and excluded from the user-geometry count. | b1check `exporters.gltf_reimport.new_objects` |
| K9 | `playblast` / turntable: `render.opengl` raises "Cannot use OpenGL render in background mode" on BOTH versions (poll is True, do not trust it) and `screen.screenshot_area.poll()` is False headless; the opengl path is taken only when a VIEW_3D area exists (live), otherwise an EEVEE camera render per frame through `_render_settings`. `video_path` writes FFMPEG through `_set_file_format(ims, "FFMPEG")` (media_type VIDEO on 5.x, D6) inside `_settings_scope`. Tests: headless = camera fallback path; opengl = live L-step. | b1check `render_opengl`; D21 |
| K10 | BVH: `io_anim_bvh` enabled in factory prefs on both; `import_anim.bvh(filepath, target, global_scale, frame_start, use_fps_scale, ...)` and `export_anim.bvh` present on both. `import_file` accepts `.bvh` when `_addon_enabled("io_anim_bvh")`, teach-style error otherwise. | b1check `exporters.bvh_ops`; APICHECK `addons` |
| K11 | Rigify (Tier 2 only): enable with `addon_utils.enable("rigify", default_set=True)` (C13: `default_set=True` is what registers it in `preferences.addons` on both; with False Rigify's own `register()` fails); generator called directly per doc section 5. Live check required before shipping (10.3 G5). | b0check `addons`; C13 |
| K12 | `bake_action`: `bpy.ops.nla.bake(frame_start, frame_end, step, only_selected, visual_keying, clear_constraints, use_current_action, bake_types={"POSE"})` FINISHES headless in POSE mode on both once bones are selected via K4 and the armature is active. `bake_types` POSE/OBJECT, `channel_types` LOCATION/ROTATION/SCALE/BBONE/PROPS on both. | b1check `nla_bake` |
| K13 | Bone collections: `armature.collections` and `collections_all` both exist on both versions; `collections.new(name)` + `assign(bone)` works; read `bone.collections` names. Use `collections_all` via `getattr` fallback as the doc says. | b1check `select_attrs.bone_collection_assign`; APICHECK |
| K14 | M9 for B1: superset rule (no pre-existing name/param removed or retyped); param additions to existing tools: `export_object` (+`include_hierarchy` + FBX/glTF params), `add_keyframe` (+`bone`), `capture_viewport_angle` / `capture_contact_sheet` (+`overlay`), `parent_object` (+`parent_type`, `bone`); reply-only changes: `get_object_info`, `import_file`. Baseline re-cut as `tests/baseline_tools_2.2.txt` after B1-G3. | Lead |
| K15 | `export_object(include_hierarchy=True)` builds its selection inside `_selection_scope` (the A1.1 helper) and restores it; `parent_set(type='ARMATURE_AUTO')` for `bind_armature` runs with the mesh selected and the armature active in OBJECT mode and leaves the armature active (evidence), so `bind_armature` restores the previous active object in `finally`. Vertex groups after AUTO: one per deform bone with real weights (8/8 verts on the test cube). | b1check `parent_set` |
| K16 | Already-verified constants (no re-derivation): pose bones default to `rotation_mode == 'QUATERNION'` on both (the `add_keyframe` / `set_pose` mode switch stays); `VertexGroup.add(index, weight, type)`; `pose.armature_apply(selected)`; pose ops `transforms_clear, select_all, copy, paste, loc/rot/scale_clear, user_transforms_clear, visual_transform_apply` exist on both; `object.vertex_group_normalize_all/_clean/_smooth/_limit_total/_mirror` exist, `vertex_group_remove_unused` does NOT; `Armature.display_type` OCTAHEDRAL/STICK/BBONE/ENVELOPE/WIRE; `pose_position` POSE/REST; IK constraint attrs `target, subtarget, pole_target, pole_subtarget, chain_count, use_tail, iterations, pole_angle`; shape keys and drivers via data API work on both. | b1check `misc`, `keyframes.rotation_mode_default` |

### 10.2 Slices (file ownership as B0)

| Slice | Owner | Files | Landings | Gate |
|---|---|---|---|---|
| B1-A addon | Ton | `addon.py` | L1: helpers `_armature_edit`, `_pose_mode`, `_mode_restore`, `_get_armature`, `_get_mesh`, `_addon_enabled`, `_action_channels` (K2), `_select_bones` (K4) + the six section 2 fixes (`get_object_info`, `export_object`, `add_keyframe`, `import_file`, `capture_*` overlay, `parent_object`) + `bl_info` (2, 2, 0). L2: Tier 1 armature, skinning, pose, constraint handlers (`create_armature` .. `remove_constraint`). L3: `set_keyframes`, `get_animation_info`, `playblast` (camera fallback path), `bake_action`. RULED 12:23: `set_scene_frame_range` is NOT built anywhere; `set_frame_range` (B0, C11; both spellings accepted on `81038008`) is the one name on both sides, no alias. RULED 12:24 (K8 amended): in L2 `import_file` passes `disable_bone_shape=True` on glTF import; `bone_shape_objects` stays in the reply for anything that still slips through. | B1-G1 per landing |
| B1-S server | Campbell | `src/blender_mcp/server.py`, `src/blender_mcp/__init__.py` | L1: wrappers for the six fixes, banners `# --- Rigging ---` and extended `# --- Animation ---`, `__version__ = "2.2.0"`, plus the B0 leftover `get_project_profile(apply_units=)` param (Lead 12:18). L2: Tier 1 wrappers for the L2 handlers (degrees -> radians, comma lists, JSON `bones` / `weights` / `params`). L3: animation wrappers, `playblast` grid via `_compose_grid` + `_safe_image_return`, `render_weight_map` / `find_unweighted_vertices(render=True)` image returns. | B1-G2 per landing |
| B1-T tests | Bastien | `tests/` (extend harness; `tests/b1_steps.py`; `tests/baseline_tools_2.2.txt` after G3) | Doc section 6 steps 1-10 on both installs (11 is live), plus: K2 readback on both paths (legacy fcurves 4.3.2 / channelbag 5.2.1, identical data_path/index/count); K3 slot set after `assign_action` on 5.2.1; K4 selection via the version-correct attribute; K5 names-only replies; K6 symmetrize on both; K7 `SMOOTH_GROUP` accepted on 5.2.1 and rejected with the live enum on 4.3.2; K8 Icosphere excluded; K9 headless playblast = camera fallback, non-blank PNG; K14 superset; version sync 2.2.0. | B1-G3 |
| B1-D docs | Sybren | `TOOLS.md` (sections "Rigging: Armatures & Bones", "Rigging: Skinning & Weights", "Rigging: Pose & Constraints", "Shape Keys & Drivers", extended "Animation"; count + TOC), `README.md`, `/blender` skill (Rigging + Animation patterns, quick-reference rows, REMOVE "armature rigging" and "shape keys" from the `execute_blender_code` fallback line), `pyproject.toml` 2.2.0, rigging doc (status, section 5 corrections from K2-K9, version lines), memory note, MemPalace drawer | per doc section 4 | B1-G4 |
| B1-P plan/API | Grace | `docs/PLAN.md`, `docs/apichecks/b1check.py` (+ extension for 10.5, 2 lines per out file) | 10.5 facts before B1-A L2 (B1-G0b). | B1-G0b |

### 10.3 Sequencing and gates

```
B1-G0   DONE 11:5x (b1check, 12-row table in the room).
B1-G0b  Grace: 10.5 facts on both installs, posted before B1-A L2 starts.
Start   B0-G3 passed = 2.2.0 bump signal (C24: all three sources in L1).
L1..L3  as in B0: Ton and Campbell each three landings, Bastien per landing, Sybren
        behaviour edits per landed docstrings; harness header = git hash-object.
B1-G3   full harness (Phase A + B0 + B1 cases) on both installs, FAIL 0; baseline re-cut.
        Definition 14:00 (supersedes 13:56): FROZEN PAIR for B1-G3 and the deploy =
        addon.py 37d2211d, server.py 6f249049, __init__.py f76c47d1, settings.py 2bbc200b,
        presets_default.json 0a58e8c3, pyproject.toml 0197888a (2.2.0). Bastien's --b0 --b1
        headless on 37d2211d (both installs) + test_b1_server on 6f249049 (F19 mock fixed,
        F20 pinned to 2.2.0) = B1-G3; baseline re-cut tests/baseline_tools_2.2.txt (169 names).
        On green the Lead deploys to the four live targets and posts the hashes;
        docs/LIVE-TEST-PLAN.md (cites this pair) is the other agent's script.
        B1-G3 PASSED 14:01 = RIGGING AND ANIMATION TIER 1 COMPLETE HEADLESS: harness
        185/185 on 4.3.2 AND 5.2.1 at addon 37d2211d (Phase A 133 + B0 31 + B1 21 steps,
        158/158 handlers exercised; tests/report_b1_*.json); venv 64/64
        (test_b1_server + test_b0_server + units + version sync at 2.2.0) on server
        6f249049 (all 22 Tier 1 rigging wrappers, F15-F19 shapes). Baseline cut:
        tests/baseline_tools_2.2.txt = 169 names (148 of 2.1 present, 21 new). Bastien's two
        behaviour notes (contact-sheet headless envelope, create_armature restores active)
        are in the Lead's DM. Lead deploying to the four live targets by copy (user directive 13:52);
        everyone freezes after their last ordered edit; the pause directive (D32) resumes
        once the deploy post is up. Live steps for the other agent: docs/LIVE-TEST-PLAN.md.
B1-G4   docs; Lead review.
B1-G5   live (user, Lead's gate): L10 render_weight_map non-blank; L11 playblast opengl path
        + video_path MP4; L12 capture overlay bones_in_front; (Tier 2) L13 rigify generate.
```

### 10.4 Tier 2 judgement (Lead decides; recommendation below)

Cheap after Tier 1 because the facts are verified and the helpers exist: `normalize_weights`,
`clean_weights`, `smooth_weights`, `limit_weights` (ops exist on both, K16), `get/add_shape_key`,
`set_shape_key_value`, `get/set_vertex_positions(shape_key=)` (shape-key API verified),
`add/get/remove_driver` (driver API verified), `delete_keyframes`, `set_keyframe_interpolation`,
`list_actions`, `create/assign/duplicate/rename/delete_action` (K2/K3 helpers), `mirror_bones`
(K6), `rename_bones`, `get_bone_trajectory`, `apply_pose_as_rest` (`pose.armature_apply(selected)`
verified), `transfer_weights` (Data Transfer modifier verified in gdcheck). Recommendation:
these ride in 2.2.0 as a fourth landing (L4) after B1-G3 passes on Tier 1.
Held back to a B1.5 follow-up (2.2.x): `setup_ik_chain` (pole-angle math needs its own test),
`add_rigify_metarig` / `generate_rigify_rig` (needs the live L13 check), `add_nla_strip` /
`get_nla_tracks` / `push_down_action` (NLA strip API unverified: 10.5).

### 10.6 Landing log (B1)

Start: B0-G3 passed 12:19 (= 2.2.0 bump signal: Sybren `pyproject.toml` now, Ton `bl_info`
(2, 2, 0) in L1, Campbell `__version__` 2.2.0 in L1, Bastien version sync after all three).
Rules restated by the Lead: staged file, one write, hash post in the same minute, harness per
landing, K14 superset vs `tests/baseline_tools_2.1.txt`, never keep EditBone refs, never open
the user's real files. Bastien: `tests/b1_steps.py` behind `--b1`.

| Landing | Hash (git hash-object) | Accepted | Evidence |
|---|---|---|---|
| B1-A L1 (Ton) | `addon.py` `c51a5b68462dea495fb6091f6f126b6c2d8a848c` | 12:34, verified 13:43 | bl_info (2, 2, 0), PROTOCOL 1; rigging helpers `_mode_restore`, `_armature_edit`, `_pose_mode`, `_get_armature` / `_get_mesh`, `_addon_enabled`, `_bone_collections`, `_action_channels` (K2/K3/K17/K18), `_select_bones` (K4) + the six section 2 fixes. EXACT reply keys (seam authority for Campbell L1.1): `export_object` + `exported_objects` ([names] or "all"), `include_hierarchy`, `fbx_options{}` or `gltf_options{}`, `ignored[]`; `add_keyframe` + `bone?`, `rotation_mode_changed?{from, to}`, `action?`, `fcurve_count?`; `import_file` -> `{success, filepath, imported_objects[], armatures[], actions[], new_actions[], bone_shape_objects[]}`; `parent_object` -> `{success, child, parent, parent_type, bone?}`; capture tools + `overlay?` (headless: `{error: "Viewport capture needs a GUI session: ..."}`); `get_object_info` + `parent, parent_type, parent_bone?, vertex_groups[], shape_keys[], armature` or `bones, pose_position, display_type, action, bone_collections[]`. 19/19 both. |
| B1-A L2 (Ton) | `addon.py` `339e66f37c491e4dec4dd76b6252b4271a7696fd` | 12:34, verified 13:43 | 17 Tier 1 rigging handlers (`create_armature` .. `remove_constraint`). Doc steps 1-7 harness 14/14 both. |
| B1-A L3 (Ton) | `addon.py` `01d0e4c68a926fc6fb7689f028bef73914b01f63` | 12:34 = FINAL B1-A state, verified 13:43 by the Lead on both installs (158 handlers; K2/K17 slot `OBarm` on 5.2.1 / None on 4.3.2; playblast headless camera path 8 non-blank frames; bake_action 29 fcurves) | `set_keyframes`, `get_animation_info`, `set_scene_frame_range` (add-on-side alias, ruling 12:33), `playblast` (camera fallback headless, opengl live, `video_path` FFMPEG), `bake_action` (`nla.bake` in POSE mode, `anim_utils` fallback). 158 handler keys; diff vs `ef0ff7d` +3925/-371. ONE GAP: K8 `disable_bone_shape=True` not in the file -> L3.1 one-liner in `import_file`'s glTF branch, then freeze. |
| B1-A L3.1 (Ton) | `addon.py` `37d2211dacd1adb7aed3b93e502d62fc2863e4e6` | 13:54, VERIFIED 13:55 = FINAL B1-A state, addon.py FROZEN = the add-on that gets deployed | K8: `import_file` passes `disable_bone_shape=True` to `import_scene.gltf` (try-kwarg, plain call on TypeError); `bone_shape_objects` stays. H9 asserts no Icosphere after a glTF import on both installs; all B1/B0/A harnesses green on this file; 158 handlers. |
| B1-S L1.1 + L2 + L3 (Campbell) | `server.py` `a3b579aa56a055103f4043ba02fb53a6bc2dc365` (`__init__.py` stays `f76c47d1` = 2.2.0) | 13:55, VERIFIED 13:56, server.py FROZEN = the server that gets deployed | ONE write. AST reconciliation vs `37d2211d`: 158 addon keys, 163 send_command sites, 0 problems (unsent: the C17 primitives, `ensure_server_running`, `set_scene_frame_range` alias). L1.1: six wrappers read Ton's landed keys. L2: 16 rigging wrappers under "--- Rigging ---" (JSON `bones` / `weights` / `params` parsed server-side, `x,y,z` vectors, degrees on the wire). L3: 6 under "--- Animation ---": `set_keyframes`, `get_animation_info`, `bake_action` (`bones` and `bake_types` as strings), `playblast` (sheet from `images[{frame, filepath}]` via `_compose_grid` + `_safe_image_return`, PNGs deleted), `render_weight_map` and `find_unweighted_vertices_image` (Image tools; `find_unweighted_vertices` stays numeric: one MCP tool has one return type; same add-on handler `render=False/True`). 170 tools = 148 baseline + 22 new, K14 violations 0. L4 (Tier 2, 28 wrappers, 198 tools) STAGED and dry-run green, NOT landed. |
| B1-S L3.1 (Campbell) | `server.py` `6f24904908b2593147ed2240e3ad96c22ff5f0ed` (13:59; `__init__` `f76c47d1`, settings, presets unchanged; addon `37d2211d`) = the server that gets deployed; 169 tools = 148 baseline + 21 new, K14 violations 0; Campbell's mocked suites 19/19 + 7/7; Bastien's suites on it 40 passed / 5 failed = four stale 2.0.0 version pins (F20) + one stale mock shape (F19-type) | RULED 13:58 from Campbell's pre-emptive venv run (36/45): F15 `set_keyframes` reads Ton's `keyed` list + `keyed_count`; F16 MERGE back to ONE tool `find_unweighted_vertices(mesh, tolerance, render=True, angle)` returning an Image when `render=True` (doc contract; a tool without a return annotation returns text or Image with the same inputSchema on mcp 1.27.0), `find_unweighted_vertices_image` removed (in no baseline); F17 `playblast` accepts `images` as list or dict; F18 `export_object` reads `ignored` or `ignored_params`. One write, hash post + K14 line, then freeze; tool count returns to 169. Test-side: F19 Bastien's mock returns the filepath (`render_weight_map` takes no filepath); F20 version sync with `BLENDER_MCP_EXPECTED_VERSION=2.2.0`. B1-G3 venv half runs on the L3.1 hash; the headless half on `37d2211d` stands. LIVE-TEST-PLAN L10.2 is re-patched to `find_unweighted_vertices(mesh, render=True, angle=)` and the server hash in its preconditions updated once L3.1 is posted. | - |
| B1 L4 (Tier 2) | not landed | RULED 13:56: NOT in this run (the rigging doc makes Tier 2 "if time allows"; the user wants the request complete and deployed now; Tier 2 needs Ton's handlers + a full cycle). Campbell's staged L4 stays staged for the next run; the report says so. | - |
| B1-S L1 (Campbell) | `server.py` `44a80bc6791b8dcdd704e94ed1d6c2f64fd6bc1f`, `__init__.py` `f76c47d1e90d7ad249d8ffa21341b7d59c129a0c` | 12:23 VERIFIED by the Lead, pending reconciliation against Ton's L1 (reply keys `exported_objects` / `ignored` per Ton vs Campbell's `exported` / `ignored_params`: reconcile to Ton's) | `__version__` 2.2.0, PROTOCOL stays 1 (additive keys only); `export_object` +12 params (payload 15 keys) with reply `exported[]` + `ignored_params[]`; `add_keyframe` +`bone` (reply `rotation_mode_changed` / `rotation_mode`); `capture_viewport_angle` / `capture_contact_sheet` +`overlay` (bones_in_front / wireframe / weight_paint, validated server-side); `parent_object` +`parent_type` (OBJECT/BONE) +`bone` (BONE without a bone refused server-side); `get_project_profile` +`apply_units`; banner "--- Rigging ---". 148 tools, all of `baseline_tools_2.1.txt` present, K14 violations 0, descriptions changed for those six tools only. Bastien venv: K14 superset + four L1 wrapper shapes PASS; 11 remaining FAILs = unlanded L2/L3. L2 (16 rigging wrappers) + L3 (6 animation/image wrappers) staged, chain dry-run 170 tools K14 clean. `set_scene_frame_range` deliberately NOT added (ruling 12:23). |

### 10.5 B1-G0b facts, verified on both installs (b1check2.py, 2026-09-11; `B1CHECK2` lines: `out_4.3.2.jsonl` 34 -> 36, `out_5.2.1.jsonl` 26 -> 28)

| # | Fact (4.3.2 | 5.2.1) | Consequence | Evidence key |
|---|---|---|---|
| K17 | A FRESH action assigned to an object on 5.2.1 has NO slots and `action_suitable_slots` is EMPTY; `action.slots.new(id_type='OBJECT', name=...)` (params `id_type` ENUM, `name` STRING, both required) creates slot `OB<name>`; even then `action_slot` stays `None` until set explicitly (`ad.action_slot = ad.action_suitable_slots[0]`); a `keyframe_insert` afterwards lands in that slot. 4.3.2: no slots API at all. | `create_action(target=...)` on 5.x: `slots.new(id_type=<target id type>, name=target.name)` then assign the slot (hasattr-guarded); `assign_action` per K3 and, when the list is empty, creates the slot the same way. Reply `slot`. | `slots` |
| K18 | Write path: 4.3.2 `action.fcurves.new(data_path, index=, action_group=)`; 5.2.1 `channelbag.fcurves.new(data_path, index=, group_name=)` (the `action_group` keyword is rejected: "expected (data_path, index, group_name)"). `keyframe_points.insert(frame, value)` works on both; `fc.evaluate(frame)` and `fc.update()` exist on both; `.find(data_path, index=)` on both collections. `Keyframe.interpolation` enum `CONSTANT/LINEAR/BEZIER/SINE/QUAD/CUBIC/QUART/QUINT/EXPO/CIRC/BACK/BOUNCE/ELASTIC`, `easing` `AUTO/EASE_IN/EASE_OUT/EASE_IN_OUT`, identical on both. | `_action_channels(..., ensure=True)` also exposes a `new_fcurve(data_path, index, group)` wrapper that passes `action_group=` on 4.x and `group_name=` on 5.x (try the 5.x keyword first, `TypeError` -> 4.x). `set_keyframes` validates interpolation/easing against these enums. | `write_path` |
| K19 | NLA: `nla_tracks.new(prev=None)`, `track.strips.new(name, start, action)` (name STRING, start INT, action POINTER, all required) on both; strip props `frame_start/end, extrapolation (NOTHING/HOLD/HOLD_FORWARD), blend_in/out, repeat, blend_type (REPLACE/COMBINE/ADD/SUBTRACT/MULTIPLY)` on both. 4.3.2 IGNORES the `name` argument (strip is named after the action): set `strip.name` after creation. 5.2.1 strips carry `action_slot` (auto-filled `OBb1b_arm`). `nla.action_pushdown(track_index)` exists on both. | The NLA trio (`add_nla_strip`, `get_nla_tracks`, `push_down_action`) is now verified: move it from the B1.5 hold list into the 10.4 L4 set. `add_nla_strip` sets `strip.name` explicitly and reports `action_slot` when present. | `nla` |
| K20 | Pose math: `pb.head` / `pb.tail` are pose-space (armature-local); world = `arm_obj.matrix_world @ pb.head`; `(matrix_world @ pb.matrix).translation` equals the world head; `pb.matrix`, `matrix_basis`, `matrix_channel`, `x/y/z_axis`, `length`, `custom_shape` exist on both. A pose value written while the bone is ANIMATED is overwritten by the action on the next depsgraph update (the rotation set to 90 deg read back 0 because a key existed). | `get_pose(space="WORLD")` uses `matrix_world @ head/tail`; `set_pose(keyframe=False)` on an animated bone reports `warning: bone is animated, value will not persist without keyframe=True`. | `pose_math` |
| K21 | `object.mode_set(mode='WEIGHT_PAINT')` works headless with the mesh active (`bpy.context.mode == 'PAINT_WEIGHT'`); `vertex_groups.active_index` sets the displayed group; `vertex_group_smooth` requires WEIGHT_PAINT mode (poll fails in OBJECT mode), `normalize_all` / `clean` / `limit_total` run in either mode; `group_select_mode` enum reads EMPTY headless (dynamic) but `"ALL"` is accepted on both. `VertexGroup.weight(i)` raises for a vertex not in the group. Data Transfer modifier (`use_vert_data`, `data_types_verts={'VGROUP_WEIGHTS'}`, `vert_mapping` TOPOLOGY/NEAREST/EDGE_NEAREST/EDGEINTERP_NEAREST/POLY_NEAREST/POLYINTERP_NEAREST/POLYINTERP_VNORPROJ) + `object.datalayout_transfer(modifier=)` + `modifier_apply` copies the groups on both. | `smooth_weights` enters WEIGHT_PAINT via `_mode_restore`; `transfer_weights` uses the modifier path with `datalayout_transfer` before apply; `group_select_mode` is passed as a string, not validated by enum. | `weight_paint` |
| K22 | Rigify: `addon_utils.enable("rigify", default_set=True)` succeeds on both; `rigify.generate.generate_rig(context, metarig)` and `rigify.utils.rig.get_rigify_target_rig` importable on both; the 8 metarig operators exist on both (`armature_human/basic_human/basic_quadruped/bird/cat/horse/shark/wolf_metarig_add`); `armature_human_metarig_add` FINISHED headless (159 bones) and `generate_rig` produced `rig` with 706 bones headless on BOTH, mode OBJECT afterwards; disable removes it from prefs. | `add_rigify_metarig` / `generate_rigify_rig` are verified headless: move them from B1.5 into the L4 set; the live L13 check becomes a confirmation, not a gate. | `rigify` |
| K23 | `bpy_extras.anim_utils.bake_action(obj, *, action, frames, bake_options)` with `BakeOptions(only_selected, do_pose, do_object, do_visual_keying, do_constraint_clear, do_parents_clear, do_clean, do_location, do_rotation, do_scale, do_bbone, do_custom_props)` runs headless on both and returns an action. | `bake_action` may use this data-API path instead of `nla.bake` when no POSE-mode context is available; same reply. | `bake_action_api` |

| K24 | `Bone.roll` does not exist on `bpy.types.Bone` (only on `EditBone`); recover it outside edit mode with `Bone.AxisRollFromMatrix(bone.matrix_local.to_3x3(), axis=<bone y axis>)`; a Euler decomposition of the matrix reads 0. Both versions. | `get_armature_info(space=ARMATURE)` reports `roll` from `AxisRollFromMatrix`, never enters edit mode for a read. | Ton L2 staging, Lead m_f66079d5e09251f3d109 |
| K25 | A default primitive cylinder has only two cap rings, so `parent_set(ARMATURE_AUTO)` on a 3-bone chain leaves the middle bone's vertex group with 0 verts on both versions. | Doc test 3 fixture corrected: subdivide the cylinder (or use enough rings) before `bind_armature`; `get_vertex_groups` reporting a zero-vert group is correct behaviour, not a bind failure. | same |

Note for builders: `bpy_prop_collection` types have no `bl_rna`, so collection method
signatures cannot be introspected; the tuples above come from the operator error text and
from successful calls.

10.4 RULED (Lead 11:58, B1.2): the L4 Tier 2 set for 2.2.0 = weights (`normalize/clean/smooth/limit_weights`,
`transfer_weights`), shape keys (`get/add_shape_key`, `set_shape_key_value`, `get/set_vertex_positions(shape_key=)`),
drivers (`add/get/remove_driver`), action management (`delete_keyframes`, `set_keyframe_interpolation`,
`list_actions`, `create/assign/duplicate/rename/delete_action`), `mirror_bones`, `rename_bones`,
`get_bone_trajectory`, `apply_pose_as_rest`, PLUS the NLA trio (`add_nla_strip`, `get_nla_tracks`,
`push_down_action`; K19: set `strip.name` after creation on 4.3.2, report `action_slot` on 5.x) and the
two Rigify tools (`add_rigify_metarig`, `generate_rigify_rig`; K22). Only `setup_ik_chain` is held for
B1.5 (own amendment; its pole-angle math needs its own evidence).
K17 is a TIER 1 contract change: `create_action` and `assign_action` on 5.x create the slot
(`action.slots.new(id_type='OBJECT', name=target.name)`) and assign `action_slot` explicitly
(K3 + K17); reply `slot`.

## 11. Phase B2 slice amendment (B2.1): engine readiness

STATUS: PLANNED, NOT STARTED (user pause directive D32, 12:25). Start condition: B1-G3 passed
AND the user resumes the run; the Lead's resume post is then the 2.3.0 bump signal (rule C24).
Accepted as a plan only 12:26; the 11.4 Tier 2 split is RULED for the record (see 11.4).
Contract =
`docs/FEATURE-REQUEST-engine-readiness.md` sections 1 (six fixes), 2.1 (Tier 1: validation,
optimisation and LODs, collision, sockets/pivots/hierarchy, engine export presets), 3 (ripple),
4 (facts, corrected by E-rows), 5 (tests). Tier 2: see 11.4. Tier 3 OUT. Version 2.3.0.
Evidence: `B2CHECK` lines (script `docs/apichecks/b2check.py`, 2026-09-11) plus `GDCHECK`;
keys cited as `b2check.<section>.<key>`. B0 already landed `set_scene_units` (C11) and B1
lands `export_object`'s hierarchy/animation params (K14): B2 extends both, never re-adds.

### 11.1 Contract deltas

| # | Delta | Evidence / ruled by |
|---|---|---|
| E1 | Target version 2.3.0 (doc says 1.7.x). | Lead |
| E2 | Boolean solver names on both the modifier and the edit-mode operator follow D19/2.3: modifier `FAST/EXACT` (4.x) vs `FLOAT/EXACT/MANIFOLD` (5.x); `mesh.intersect_boolean` has no `MANIFOLD` on either. Every solver produces identical geometry on the test cube; `validate_asset(fix=True)` and `cleanup_mesh` never call the operator, only `bmesh` and the modifier path. | `boolean` |
| E3 | `decimate_mesh` mode names map to the enum `COLLAPSE / UNSUBDIV / DISSOLVE` (the doc's `PLANAR` = `DISSOLVE`, `UNSUBDIVIDE` = `UNSUBDIV`); accept both spellings, report the enum id. `keep_uv_seams` = `delimit = {'UV', 'SEAM'}` (assignable on both). `face_count` is readable BEFORE apply after `view_layer.update()`: the `target_triangles` iteration reads it instead of applying per pass. `triangulate=True` = `use_collapse_triangulate`. | `modifiers` |
| E4 | `set_normals`: AUTO_SMOOTH uses `object.shade_smooth_by_angle(angle, keep_sharp_edges)` on BOTH versions (marks sharp edges, no modifier, FINISHED headless both). `object.shade_auto_smooth` is CANCELLED headless on 4.3.2 and on 5.2.1 adds the "Smooth by Angle" NODES modifier (inputs `Input_0 / Input_1 / Socket_1`; applying it leaves sharp edges and no custom normals): offered only as `mode="AUTO_SMOOTH_MODIFIER"`, meaningful with `apply=False` on 5.x, teach-style error on 4.x. WEIGHTED = Weighted Normal modifier (`FACE_AREA_WITH_ANGLE`, `keep_sharp`) applied -> `has_custom_normals` True on both. FROM_OBJECT = Data Transfer `data_types_loops={'CUSTOM_NORMAL'}`, `loop_mapping='POLYINTERP_NEAREST'` applied -> custom normals on both. CLEAR_CUSTOM = `mesh.customdata_custom_splitnormals_clear()` (object mode, both). | `normals` |
| E5 | `calc_tangents` ABORTS on meshes with ngons ("Tangent space can only be computed for tris/quads") on both: `validate_asset` reports ngons as FAIL whenever the profile exports tangents (`use_tspace` / `export_tangents`), and `export_for_engine` with `triangulate=True` triangulates first (Triangulate modifier, `keep_custom_normals=True`). | `normals.calc_tangents` |
| E6 | `apply_transforms`: `transform_apply(location, rotation, scale, isolate_users=True)` on both; `corrective_flip_normals` exists on 5.2.1 only (pass it inside `try/except TypeError`, report `flip_corrected`). A shared mesh with `isolate_users=True` gets its own data (users 1) on both. Negative scale is reported as a FAIL check before apply (doc rule). | `ops.transform_apply_*` |
| E7 | `mesh.tris_convert_to_quads`: `topology_influence` and `deselect_joined` are 5.2.1-only kwargs (try/except TypeError). `mesh.decimate(ratio)` works in edit mode headless on both. `select_non_manifold` / `select_face_by_sides(number=4, type='GREATER')` / `select_interior_faces` run headless in edit mode after `mesh.select_mode(type=...)`; `find_mesh_issues` reads the counts from `bmesh.from_edit_mesh` and restores OBJECT mode in `finally`. | `ops.tris_convert_to_quads`, `ops.select_ops` |
| E8 | FBX round trip is faithful on both: a root EMPTY, a `_LOD0` mesh, a `UCX_<name>_00` child mesh and a `SOCKET_<name>` child empty come back with names, hierarchy, custom props (`use_custom_props` both ways), the socket's location and custom normals (`use_custom_normals`). `mesh_smooth_type='SMOOTH_GROUP'` exports on 5.2.1 only: `export_for_engine` validates the value against the live enum (D9 idiom). Import kwargs on both: `use_manual_orientation, global_scale, bake_space_transform, use_custom_normals, colors_type, use_image_search, use_anim, use_custom_props, ignore_leaf_bones, force_connect_children, automatic_bone_orientation, primary/secondary_bone_axis, axis_forward, axis_up` (+ `mtl_name_collision_mode` on 5.2.1). | `fbx_roundtrip`, `ops.fbx_import_kwargs` |
| E9 | glTF: `export_vertex_color='NAME'` is 5.2.1-only and requires `export_vertex_color_name=<attribute>`; on 4.3.2 the preset uses `MATERIAL` (or `ACTIVE`). `export_all_vertex_colors` and `export_active_vertex_color_when_no_material` exist on both. Round trip preserves extras (custom props) on both but RENAMES colour attributes to `Color` / `Color.001` on both (names are not part of glTF): `validate_asset(profile=GODOT/BEVY)` warns when a colour attribute name matters. 5.2.1 import adds a `custom_normal` attribute. `import_file` glTF kwargs on both: `import_shading` (NORMALS/FLAT/SMOOTH), `merge_vertices`, `bone_heuristic`, `disable_bone_shape` (pass `True` by default: removes the importer's Icosphere, closes K8 at the source), `import_pack_images`; 5.2.1 adds `import_scene_extras`, `import_select_created_objects`, `import_merge_material_slots`, `import_unused_materials`. `export_image_format` AUTO/JPEG/WEBP/NONE on both. | `gltf_roundtrip`, `ops.gltf_import_kwargs` |
| E10 | OBJ: `wm.obj_export(apply_transform=)` is 5.2.1-only; 4.3.2 ALWAYS bakes the object transform into the vertices. `export_smooth_groups` writes `s` lines on both. `export_for_engine` never offers OBJ for a shipping round trip (doc rule); `export_object(obj)` reports `transform_baked: true` on 4.x. | `obj_roundtrip` |
| E11 | `create_collision`: CONVEX = `bmesh.ops.convex_hull(bm, input=bm.verts)` then delete `geom_interior` + `geom_unused` verts; SLICED_CONVEX = `bmesh.ops.bisect_plane(..., clear_inner/clear_outer)` per slab then hull; volume ratio via `bm.calc_volume(signed=False)` (hull 3.87 vs torus 1.16 on the test); proxy gets `display_type='WIRE'`, `hide_render=True`, `parent=<mesh>`, no material. All on both. Names per profile as the doc says. | `collision` |
| E12 | Names: data-block name limit is 63 bytes on 4.3.2 and 255 on 5.2.1; collisions suffix `.001/.002` on both. `validate_asset` name checks use the live limit (`len(obj.name)` after assignment, never a constant). | `misc.max_name_len` |
| E13 | Numbers: `get_triangle_budget` / `get_mesh_stats.triangles` use `evaluated_get(depsgraph).data.calc_loop_triangles()` (6 polys + subsurf 2 -> 192 tris on both). `compare_meshes` first pass = `mesh.unit_test_compare(mesh=, threshold=)` ("Same" or a difference string whose wording differs per version: match on `== "Same"` only); numpy pass uses `foreach_get` into preallocated `float64` buffers (M12; `numpy.array(Vector)` is float32 on 5.2.1). `set_origin(BOTTOM_CENTER)` = `mesh.transform(Matrix.Translation)` + `location` offset (verified). | `misc` |
| E14 | Timbermesh: the plugin module is present on 4.3.2 (user add-ons folder) and ABSENT on 5.2.1 (not installed). `export_for_engine(TIMBERMESH)` errors with a teach-style message when `addon_utils` cannot find the module; its exporter path stays as the doc describes and is verified live on 4.3.2 only until the user installs it on 5.2 (L-step). C13 applies to its enable call (`default_set=True`). | `misc.timbermesh_module`; gdcheck `addons_all` |

### 11.2 Slices (file ownership as B0/B1)

| Slice | Owner | Files | Landings | Gate |
|---|---|---|---|---|
| B2-A addon | Ton | `addon.py` | L1: section 1 fixes (`export_object` engine params on top of B1's, `import_file` axis/scale/report, `get_mesh_stats` counts, `get_scene_info` budgets + units, `apply_modifier(apply_all, keep)`, `set_origin` BOTTOM/TOP/custom) + helpers `_eval_tri_count`, `_mesh_issue_indices`, `_hull_bmesh`, `_profile_names`; `bl_info` (2, 3, 0). L2: validation + optimisation (`validate_asset`, `find_mesh_issues`, `compare_meshes`, `apply_transforms`, `fix_normals`, `cleanup_mesh`, `decimate_mesh`, `generate_lods`, `set_normals`, `triangulate_for_export`, `get_triangle_budget`). L3: collision + sockets/pivots/hierarchy + export presets (`create_collision`, `get_collision_info`, `check_collision_fit`, `add_socket`, `list_sockets`, `set_pivot`, `snap_to_grid`, `build_export_hierarchy`, `export_for_engine`, `batch_export`, `get_export_preset`; `set_scene_units` extended, not re-added). | B2-G1 per landing |
| B2-S server | Campbell | `src/blender_mcp/server.py`, `src/blender_mcp/__init__.py` | L1: fix wrappers + `__version__ = "2.3.0"` + banner `# --- Engine readiness ---`. L2/L3: Tier 1 wrappers in the doc's reply shapes; `export_for_engine` writes the manifest server-side from the addon reply; `batch_export` loops on the server; `get_export_preset` is server-only (the preset table lives in the server). | B2-G2 per landing |
| B2-T tests | Bastien | `tests/b2_steps.py` (`--b2`), `tests/baseline_tools_2.3.txt` after G3 | Doc section 5 steps on both installs, plus E-cases: E2 all solvers; E3 mode spellings + `face_count` readback; E4 AUTO_SMOOTH sharp-edge count on both and the 5.x modifier option; E5 ngon -> tangent FAIL; E6 `corrective_flip_normals` guarded; E8 FBX round trip names/props/socket; E9 NAME colour export 5.x only, Icosphere absent with `disable_bone_shape`; E10 OBJ transform baked on 4.x; E11 hull volume ratio; E12 name limit per version; E13 tri counts; superset vs `baseline_tools_2.2.txt`; version sync 2.3.0. | B2-G3 |
| B2-D docs | Sybren | `TOOLS.md` (sections "Validation", "Optimisation & LODs", "Collision", "Sockets, Pivots & Hierarchy", "Engine Export"), `README.md`, `/blender` skill (engine-export pattern), `pyproject.toml` 2.3.0, engine doc (status, section 4 corrections from E4/E5/E9/E10/E12/E14), memory, MemPalace | per doc section 3 | B2-G4 |
| B2-P plan/API | Grace | `docs/PLAN.md`, `docs/apichecks/b2check.py` (+ extension for 11.5 if needed) | answers by section number + evidence key | - |

### 11.3 Sequencing and gates

```
B2-G0   DONE (b2check, 9-row table in the room).
Start   B1-G3 passed = 2.3.0 bump signal (C24 pattern: all three sources in L1).
L1..L3  as B0/B1; harness per landing; K14-style superset vs tests/baseline_tools_2.2.txt.
B2-G3   full harness (Phase A + B0 + B1 + B2 cases) on both installs, FAIL 0; baseline re-cut 2.3.
B2-G4   docs; Lead review.
B2-G5   live (user, Lead's gate): L14 find_mesh_issues(render=True) non-blank; L15 Timbermesh
        export on 4.3.2 (and on 5.2.1 once the plugin is installed there); L16 an Unreal
        FBX import check of UCX_/SOCKET_ (outside Blender, user's engine).
```

### 11.4 Tier 2 judgement (RULED by the Lead 12:26, for the record so the next run does not re-litigate)

L4 set for 2.3.0 = `reduce_bone_influences`, `check_vertex_count_split`, `make_single_user`,
`set_custom_properties` / `get_custom_properties`, `merge_for_export`, `remesh_object`.
HOLD = `generate_lightmap_uv` (needs B3) and `export_timbermesh` (plugin absent on 5.2.1 until
the user installs it; Sybren's deploy checklist says the user copies it before any Timbermesh
test on 5.2.1). Also ruled 12:24 for B2: `shade_smooth_by_angle` is the AUTO_SMOOTH path on both
versions, `shade_auto_smooth` (5.x modifier) only as an explicit option (= E4).

Recommendation as written before the ruling (facts verified or pure data API): `reduce_bone_influences` (B1's
`limit_weights` + normalize), `check_vertex_count_split`, `make_single_user`,
`set_custom_properties` / `get_custom_properties` (extras round-trip verified, E9),
`merge_for_export` (join + collection), `remesh_object` (op kwargs in gdcheck; real call not yet
exercised: 11.5). Recommendation: these ride in 2.3.0 as L4 after B2-G3. Held for a B2.5:
`generate_lightmap_uv` (depends on B3's `add_uv_layer` / `pack_uv_islands`),
`export_timbermesh` (plugin absent on 5.2.1, E14).

### 11.5 Facts still to verify if the Lead pulls Tier 2 in (b2check extension)

- `object.quadriflow_remesh(target_faces=, use_preserve_sharp=, ...)` and `object.voxel_remesh()` real calls headless on both.
- `mesh.separate(type='LOOSE')` result object naming for `check_vertex_count_split(split=True)`.
- `object.make_single_user(object=True, obdata=True)` kwargs vs `obj.data = obj.data.copy()`.
