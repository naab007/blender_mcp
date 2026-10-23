# Lead rulings, Phase C (generic driver), team nosubl, 2026-10-16

Owner: Lead (Ada). Identities are `@x4rah` (room roster wins over the brief). Barbara copies
these verbatim into `docs/PLAN.md` section 12.0.

- **R1 SCOPE.** "Continue the work on blender mcp" = Phase C: generic driver Tier 1 as version
  2.3.0 on branch `blender-5.2`, per `docs/FEATURE-REQUEST-generic-driver.md` (docs/README.md row
  18, build order 2a; written 2026-09-12, never built; the doc and `drvcheck.py` are still
  uncommitted). Section 3 "fixes first" is IN (uids in get_scene_info / get_object_info /
  find_objects_by_type / get_armature_info; `execute_code` result_expr + structured error;
  `add_modifier` props through the coercer; get/set/describe_settings re-implemented on the
  driver with byte-identical replies). Tier 2 (events, find, context_info) only if Tier 1 lands
  by gate C-G3; Tier 3 never. `PROTOCOL` stays 1, additive only; every name in
  `tests/baseline_tools_2.2.txt` (169) keeps its schema.
- **R2 OWNERSHIP.** `addon.py` = Linus. `src/blender_mcp/{server.py,settings.py,__init__.py}`,
  `pyproject.toml`, `TOOLS.md`, `README.md`, `docs/release-notes/`,
  `~/.claude/commands/blender.md` = Grace. `tests/*` (`drv_steps.py`, `test_drv_server.py`, the
  `--drv` flag in `headless_handlers.py`, `baseline_tools_2.3.txt`, `docs/TEST-PLAN-C.md`) =
  Margaret. `docs/PLAN.md` (append section 12 = Phase C amendment C.1, C.2, ...),
  `docs/README.md`, the one-line status notes in the other request docs,
  `docs/apichecks/README.md` = Barbara. `docs/apichecks/drvcheck.py` + `out_*.jsonl` appends =
  Linus during recon. One file, one owner; cross-file needs come to the Lead.
- **R3 TOOLCHAIN.** Blender 4.3.2 = `D:\blender-4.3.2-windows-x64\blender.exe`; 5.2.1 =
  `D:\blender-5.2.1-windows-x64\blender.exe`. Venv = `D:\App Dev\blender_mcp\.venv\Scripts\python.exe`
  (3.13.2). NO new dependencies (`difflib`, `fnmatch`, `json` are stdlib). 4.x/5.x differences by
  try-assign / `hasattr`, never `bpy.app.version` compares.
- **R4 TEST COMMANDS (exact).** Harness, BOTH installs:
  `<install>\blender.exe --background --factory-startup --python tests\headless_handlers.py -- --b0 --b1 --drv --out tests\report_c_<ver>.json`
  (baseline today without `--drv`: 186/186 each). Venv: `set BLENDER_MCP_EXPECTED_VERSION=2.3.0`
  then `.venv\Scripts\python.exe -m pytest tests -q` (69 today at 2.2.0). Facts:
  `<install>\blender.exe --background --factory-startup --python docs\apichecks\drvcheck.py`,
  lines appended under `### drvcheck.py` in `docs/apichecks/out_<ver>.jsonl`, pre-existing lines
  untouched (M10 drift rule).
- **R5 EVIDENCE.** A landing = `git hash-object <file>` posted in the same minute + the pasted
  SUMMARY lines of the FULL harness on both installs + the pytest tail. Never a summary of a
  summary. QA re-runs from disk before any card moves to Verified.
- **R6 NO-GO.** Nobody commits, pushes, tags, deploys (B:, `D:\blender-*\MCP`, `%APPDATA%`), or
  launches a Blender GUI or the MCP server. `--background` runs are allowed (that is the
  harness). Live GUI steps are batched to the user at the end as one request.
- **R7 COMMS.** Inbox first on every wake; ACK every assignment / finding / ruling / gate signal
  by DM within one poll; no ACK in 5 min = re-assigned. Findings as finding / evidence / who it
  blocks. Room posts <= 2500 chars; long material goes on your kanban card (board `team-nosubl`,
  db `creative-suite`; card ids are lowercase: `c-recon-addon`, `c-recon-server`, `c-recon-qa`,
  slices `c-a-l1` ...) or on disk with the path posted.
- **R8 CONTRACT.** Coders build only from a PLAN.md version the Lead has ratified in the room.
  Changes only as versioned amendments through Barbara (C.1, C.2, ...), re-posted as diffs.
- **R9 VERSION.** 2.3.0 in all three sources (`pyproject.toml`, `bl_info`, `__init__.py`), bumped
  by Grace at signal C-S0, not before (`tests/test_version_sync.py` guards it).
- **R10 UNITS.** Raw RNA values, radians by default (doc rule 8) stands; the hand-written tools
  keep degrees.

## Rulings R11-R29 (issued in #team-nosubl and by DM during recon and L1; this file is the record)

- **R11** `tests/test_f6_connection.py` tools/list test is repointed at the bump: names == `tests/baseline_tools_2.3.txt`, schema == `tests/baselines/tools_list_2.3.json` (both cut from the real list at D-G3; SKIP with reason until then), plus the 2.2 superset assertion (see R29). The 2.2 baseline files stay untouched.
- **R12** Server JSON params: dict params -> `_parse_json_object`; list-or-dict -> `_parse_json_any`; "comma list or JSON list" -> new `_parse_refs(text, label)` (JSON list when `text.lstrip()` starts with `[`, else `_split_csv`; empty -> `[]`; non-string item -> `Error: <label> must be a JSON list of strings, e.g. '["#127", "OBJECT:Cube"]'`). No new `_parse_json`. All params stay typed `str`.
- **R13** `settings.py` DEFAULTS += `driver_reply_max_chars` 24000, `driver_batch_max` 200; ENV_KEYS += `BLENDER_DRIVER_REPLY_MAX`, `BLENDER_DRIVER_BATCH_MAX`; read at use time via `int(_settings.get(...))`.
- **R14** `tests/test_drv_server.py` copies the `FakeConn` + `server_in` block from `tests/test_b0_server.py:25-66` (house idiom: no conftest, no cross-test import).
- **R15** `tests/settings_baseline.py -- --check` (fixtures `tests/baselines/settings_replies_<ver>.json`, captured at addon `0eb7ec4c`, frozen) = the regression gate for get/set/describe_settings replies at D-G3.
- **R16** No uid index or cache: `_uid_lookup(uid)` is a full `bpy.data` scan per call (measured 0.27 ms worst on 2000 objects), skipping `all_ids` on 5.x.
- **R17** Undo works in `--background` on both installs: `transaction` ROLLBACK and `batch(undo=AUTO)` are asserted for real in the harness.
- **R18** msgbus delivers nothing headless: `rna_set(publish=True)` stays but no harness step asserts msgbus delivery; Tier 2 `property` events are live-only; depsgraph/frame events are the testable path.
- **R19** `_apply_context` whitelists `temp_override` keys: window, screen, area, region, scene, view_layer, object, active_object, selected_objects, selected_editable_objects, edit_object, pose_object, active_bone, active_pose_bone, modifier, point_cache; unknown -> `{error, valid:[whitelist]}`. `bl_options` is dropped from `describe(operator)`.
- **R20** Headless has a window, a screen and 4 areas under `--factory-startup`: the "needs the GUI" error is keyed on `bpy.app.background`; `context_info` reports `window: bool` and `background: bool`.
- **R21** Caps stand (24000 chars, 200 commands). RNA error texts are passed through verbatim under `rna_error` inside the teaching error.
- **R22** `transaction` by Scene marker: BEGIN = `undo_push("mcp:<name>:pre")`, `scene["_mcp_txn"] = seq`, `undo_push("mcp:<name>")`; ROLLBACK = `ed.undo` while `scene.get("_mcp_txn") == seq` (cap = undo_steps); COMMIT deletes the idprop. Reply keys `undo_calls`, `marker_present`, `undo_available` replace `pushes`. Declared side effects: one Scene idprop, `is_dirty`.
- **R23** `rna_get` default at depth 0 = every non-COLLECTION property INCLUDING pointers (`{uid,id_type,name}` for IDs, `{struct,path}` otherwise); collections `{count}`; depth >= 1 expands.
- **R24** Settings tools keep their bodies; the driver builds on `_rna_get` / `_rna_set_validated` / `_prop_default` / `_scope_owner`.
- **R25** `execute_code` returns `{error: "<type>: <msg>", traceback: [last 3 frames], line}` instead of raising; stdout capture kept; the wrapper prints the dict.
- **R26** `IDTYPE:` map = static table with ORDERED `hasattr` candidates per id_type (GreasePencil struct moved between 4.3 and 5.2); unknown -> `{error, valid}`. Supersedes G5's derived map.
- **R27** The 60 s batch wall cap is checked between commands (declared deviation); `elapsed_ms` reports the truth.
- **R28** `add_modifier` keeps accepting a plain object NAME for pointer props beside refs.
- **R29** 2.2 superset assertion = every 2.2 tool keeps every 2.2 inputSchema property with the same type and unchanged `required`; additive properties and description changes are allowed. Byte-identity only against the 2.3 baselines.
- **Inconsistency rulings I1-I9** (TEST-PLAN-C.md section 5): find/context_info are in scope (13 tools, 182; 185 with L4); canonical wire shape = JSON list (dict accepted for `sets`, normalised server-side); server always sends `max_chars`, add-on default 24000; caps are addon module constants `_DRV_BATCH_MAX` / `_DRV_BATCH_WALL_S` read at call time; a rename appears ONLY in `renamed`; events step 10 = T4 only.

## Rulings R30-R39 (build phase; issued in #team-nosubl and by DM; this file is the record)

- **R30** A batch item is "error" when its result dict has an `error` key OR `success: false` (an rna_set whose items all failed trips stop_on_error).
- **R31** `_parse_json_any` refuses any decoded scalar (str/int/float/bool/null) with `Error: <label> must be a JSON object or list`; the fix is at the chokepoint so all 11 callers (incl. the 2.2 rigging wrappers, which echoed a JSON string value as the reply) inherit it; QA proves the class closed with a case at a 2.2 call site that is red against the committed 2.2.0 server.
- **R32 / R32b** `drv_describe` truncation order at the cap: (1) blank property `description` strings and the description element of `enum_items` (3-element shape kept), (2) only then drop whole properties from the END, named compactly. The default cap stays 24000 (measured: describe("Object") = 38965 chars full, 30201 with descriptions blanked, so ~30 trailing properties still drop); `count` = kept entries, `total` = declared; the "130+ properties" assertion lives on the `max_chars=60000` variant.
- **R33** The depth walk (rna_get, scene_snapshot, find expansion) never dereferences pointers on UNSAFE structs: `_DRV_UNSAFE_STRUCTS = {ToolSettings, Paint (and any struct whose bl_rna.base chain reaches Paint), CurveMapping}`; such pointers are reported at depth-0 shape only and the skip is named once in `truncated`. Cause: `scene.tool_settings.uv_sculpt` is an uninitialised Paint struct in the 4.3.2 factory file; a plain getattr faults the process (frames rna_Paint_eraser_brush_get, brush_asset_reference.asset_library_type, CurveMapping_tone_get). A run without a SUMMARY on one build is a landing FAIL, never evidence.
- **R34** `truncated` entries are compact ("properties: 30 of 132 dropped, e.g. ...; narrow with fields= or raise max_chars="); the envelope stays <= 400 chars and the cap check counts the final JSON including `truncated`.
- **R35** A ref ending in "/" with an empty path is refused for every root (`context/` like `OBJECT:Cube/`); bare `context`, `data`, `prefs` resolve to the root struct.
- **R36** The planned "coercer read-only skip" mutant cannot discriminate (RNA refuses the assignment itself); the add-on discriminators are mutant C (default cap off -> DRV-1/DRV-9 FAIL), D (DEGREES conversion off -> DRV-3 FAIL), E (context whitelist off -> DRV-5 FAIL), F (R30 item rule off -> DRV-8 FAIL), G (`_DRV_BATCH_MAX` off -> 201 refusal FAIL); QA ratifies survival independently on scratch copies.
- **R37** `call_operator` validates `params` against the operator's RNA BEFORE poll (refused-op check first, params second, poll third), so a misspelt param returns `{error, closest}` even in the wrong context.
- **R38** L4 events (`events_subscribe` / `events_poll` / `events_unsubscribe`) are OUT of 2.3.0: 13 driver tools, 182 total. msgbus delivery is unprovable headless; depsgraph/frame events need `@persistent` handlers in `register()`; nothing in Tier 1 depends on them. First item of 2.4.0 with the R18 facts attached.
- **R39** (F12) A wall-capped batch counts the wall stop as an error for the rollback decision: reply `success: false`, `rolled_back` per the undo mode (AUTO + stop_on_error rolls back the completed commands), `stopped_at` = first command not run, plus `error` = `wall cap <N> s reached after command <i>; <k> skipped`.
- **Oracle rulings O1-O4**: O1 = R32b supersedes "truncated == []" at the default cap; O2 = the R33 skip note is present even uncapped; O3 = `get_version.registries` lists only the `blendermcp_*` driver_namespace registries that exist (no pre-creation); O4 = the INVOKE refusal < 50 ms stands as written.
