# Feature request: generic driver (CDP-style RNA and operator protocol)

Status: BUILT as 2.3.0 on `blender-5.2` (team nosubl, 2026-10-16; contract = `PLAN.md` section 12 D1.4, which
wins where this doc differs). Shipped: 13 tools = `describe`, `rna_get`, `rna_set`, `data_new`, `data_remove`,
`find`, `context_info`, `call_operator`, `call_method`, `scene_snapshot`, `scene_diff`, `batch`, `transaction`
(169 -> 182) plus the section 3 fixes except the settings re-implementation (deferred, replies pinned
byte-identical by `tests/settings_baseline.py`). NOT shipped: `events_subscribe` / `events_poll` /
`events_unsubscribe` (Tier 2 events, ruled OUT of 2.3.0 = first item of 2.4.0: msgbus delivery is unprovable
headless, so the `property` kind would ship untested; depsgraph/frame events need @persistent handlers at
phase end); Tier 3. Design changes recorded in `PLAN.md` 12.1: no uid cache, transaction by Scene idprop marker,
static id_type table, pointers in the depth-0 default, radians default, caps 24000 / 200, unsafe structs
`ToolSettings` / `Paint` / `CurveMapping` never read (4.3.2 access violation).
Asked 2026-09-09 ("could we introduce a cdp style driver?"), primitives
verified headlessly the same day (`apichecks/cdp.py`, lines `CDP` in `out_4.3.2.jsonl` and
`out_5.2.1.jsonl`), written up 2026-09-12.
Target version: the next minor after the phase in flight (all three version sources move
together, `tests/test_version_sync.py`). Recommended slot: Tier 1 BEFORE engine-readiness
Tier 2, node graphs and simulation, because their recipe tiers are cheaper on top of this.
The Lead decides in `PLAN.md`.
Baseline: v2.2.0, 169 tools (`tests/baseline_tools_2.2.txt`), add-on `0eb7ec4c`, server
`2ddfc98a`, commit `3459566` on `blender-5.2`. `PROTOCOL = 1` stays: everything here is
additive (new handler keys, no existing payload or reply changes meaning).
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here. Team rules: `PLAN.md` section 0 (one file one
owner, hash posts, evidence = pasted output, nobody commits/deploys/launches without the
user).

## 0. Scope and why

The server has 169 hand-written tools and the seven open request docs ask for roughly
150 more. Each one is a Python handler in `addon.py` plus a wrapper in `server.py`, and
each exists because the agent otherwise has to fall back to `execute_blender_code`, which
gives it no schema, no stable handles, no atomicity and an error that is a traceback in
Blender's stdout. That is the "you are missing an abstraction" signal.

Chrome DevTools Protocol solved the same problem for browsers: a small generic surface
(introspectable schema, stable node ids, get/set/call by path, a DOM snapshot with diffs,
an event stream, batched commands) on top of which the convenience layers are thin. Blender
has every primitive the design needs, verified on 4.3.2 and 5.2.1:

| CDP concept | Blender primitive |
|---|---|
| Protocol schema, generated clients | RNA is fully introspectable: every property and operator exposes type, subtype, enum items, defaults, min/max, read-only flag and description through `bl_rna` and `get_rna_type()`. `describe` is generated, never hand-written. |
| Stable node ids (`backendNodeId`) | `ID.session_uid`, an integer stable across renames (verified) and unique per session. Names stop being the handle. |
| Domains and methods | `bpy.data` (new, remove, lookup), RNA get and set by `path_resolve`, `bpy.ops` with a `temp_override`, ID and struct methods (`evaluated_get`, `calc_tangents`, `from_pydata`). |
| Events pushed to the client | `bpy.app.handlers` (40+ hooks: depsgraph update, frame change, render, bake, save, load, undo, redo) and `bpy.msgbus.subscribe_rna` for individual property changes. |
| DOM snapshot and mutation records | A scene-graph snapshot keyed by uid and a diff between two snapshots, the same shape as the AgentBrowser snapshot. |
| Transactions | `ed.undo_push(message)` and `ed.undo` make a batch atomic from the agent's point of view. |

What changes for the other request docs once this lands:

- **Fixes to existing tools mostly disappear.** A missing parameter on export, capture or
  import is just a field the driver already exposes.
- **Convenience tools survive as recipes.** Unwrap, LOD chains, collision proxies, bind
  armature, export presets, quick effects become server-side compositions of driver calls
  (section 5). They stay as named tools because they encode judgement, but they no longer
  need an add-on handler each.
- **Perception tools stay first class.** Contact sheets, weight maps, UV layouts, sprite
  sheets, node-graph renders need Blender-side rendering and server-side compositing. The
  driver does not replace them.
- **Numeric checks stay add-on side** where they need BMesh or numpy (UV stretch, island
  detection, collision fit, manifold checks).

Build what is in this document. If something is wrong or impossible on either install, say
so in the report and continue.

## 1. Design rules (in addition to the architecture rules)

1. **One dispatcher, one address grammar.** Every driver command addresses data the same
   way (section 2.1). No driver tool takes an `object_name` parameter.
2. **Describe before guessing.** The agent discovers parameters with `describe`; the
   server tool docstrings for the driver are short and point at `describe`.
3. **Context is solved once.** `call_operator` takes a declared context and applies it
   through the existing helpers (`_selection_scope`, `_mode_restore`, `_gui_window`, the
   area/region `temp_override` the capture tools use). Handlers never build overrides ad hoc.
4. **Replies are bounded.** Every reading command has field selection, depth and type
   filters and a hard character cap (settings key `driver_reply_max_chars`, default
   24000). A truncated reply says what it dropped and which filter narrows it.
5. **Errors teach.** Unknown property: the three closest identifiers (`difflib`) and the
   struct's property count. Bad enum: the valid identifiers. Operator poll failure: the
   poll message and the context keys that were applied. Never a bare traceback.
6. **Additive wire protocol.** New handler keys only. Batch, cursors and uids do not change
   any existing command. The 169 tools keep working unchanged during and after this phase.
7. **Same power, same boundary.** `rna_set` and `call_operator` are exactly as powerful as
   `execute_code`, which already exists. The loopback-only bind stays, nothing is gated,
   and the deny-list in 2.5 exists to keep the session alive, not for safety theatre.
8. **Raw RNA values, no unit conversion by default.** Angles are radians unless
   `angles="DEGREES"` is passed (applies to properties whose RNA `unit == 'ROTATION'`).
   This is different from the existing hand-written tools (degrees); document it in
   `TOOLS.md` and in the `/blender` skill.

## 2. New tools

### 2.1 Addressing (the `ref` grammar, used by every driver command)

| Form | Meaning | Example |
|---|---|---|
| `#<uid>` | An ID by `session_uid` | `#127` |
| `<IDType>:<name>` | An ID by `bpy.data` collection and name; `IDType` is the `ID.id_type` enum identifier (`OBJECT`, `MESH`, `MATERIAL`, `NODETREE`, `ACTION`, `SCENE`, ...) | `OBJECT:Cube` |
| `<ref>/<path>` | An RNA path under an ID, `path_resolve` syntax: dotted props, `[index]`, `["key"]` collection items, `["prop"]` ID properties | `OBJECT:Cube/modifiers["Bevel"].width`, `#127/location[2]`, `SCENE:Scene/render.resolution_x` |
| `context` | The current context as a struct root (`context/scene`, `context/active_object`, `context/view_layer`) | `context/scene.frame_current` |
| `data` | `bpy.data` as root, for collection questions | `data/objects`, `data/materials["Red"]` |
| `prefs` | `bpy.context.preferences` as root | `prefs/edit.undo_steps` |

Every reply that mentions an ID carries `{"uid": 127, "id_type": "OBJECT", "name": "Cube"}`.
Non-ID structs (modifiers, bones, constraints, nodes) have no uid and are addressed by
path under their owning ID. A `ref` that resolves to nothing returns `{"error": "...",
"tried": [...]}` naming the collection it searched. Uid lookup has no API in Blender: the
add-on keeps a uid → ID index rebuilt lazily on a miss (scan of the `bpy.data` collections)
and invalidated on `load_post` and `undo_post`/`redo_post` handlers; measure the scan
(`drvcheck.py` reports it) and keep the index if a full scan costs more than about 5 ms
on a 1000-object file.

### 2.2 Tier 1, the driver (required)

| Tool (server) | Handler (add-on) | Parameters | Behaviour and reply |
|---|---|---|---|
| `describe` | `drv_describe` | `what`: a `ref` (describes the struct the ref resolves to, or the single property when the path ends on one), a struct name (`"Object"`, `"BooleanModifier"`, `"RenderSettings"`), or an operator id (`"mesh.bevel"`, `"object.modifier_apply"`); `fields="all"` or a comma list of property identifiers; `include_functions=False`; `search=None` (substring over identifier, name and description of the struct's props or, with `what="ops"`, over every operator id and description) | Struct: `{identifier, base, description, properties: {id: {type, subtype, unit, readonly, array_length, default, min, max (hard), soft_min, soft_max, enum_items: [[id, name, description]], fixed_type (POINTER/COLLECTION), is_animatable}}, functions: {name: {description, params: [[id, type, is_output, is_required]]}}}`. Operator: `{idname, name, description, properties (same shape), poll_now (bool, evaluated with the current context), bl_options}`. Dynamic enums that read empty headless (render engine, view transforms, Cycles devices) are marked `dynamic: true` and the reply says to validate by assignment. Catalogue cached per struct name for the session. |
| `rna_get` | `drv_get` | `refs`: comma list or JSON list of refs; `fields=None` (comma list of property identifiers; default = every non-collection, non-pointer property for a struct ref, the value for a property ref); `depth=0` (expand POINTER props to this depth; collections report `{count}` unless named in `fields` as `modifiers[]`, which lists items to `depth`); `angles="RADIANS"`; `max_chars=None` (overrides the settings cap) | `{results: [{ref, uid?, value}], truncated: [what was dropped], chars}`. Value encoding: numbers, bools, strings, enums as identifiers, enum flags as lists, vectors/colors as lists, matrices as row lists, pointers as `{uid, id_type, name}` (or `null`), collections as `{count, items?}`, ID properties under `custom: {}`. One round trip for many refs. |
| `rna_set` | `drv_set` | `sets`: JSON list of `[ref, value]` (also accepted: JSON object `{ref: value}`); `angles="RADIANS"`; `stop_on_error=True`; `publish=True` (call `msgbus.publish_rna` for each set so subscribers, including the driver's own event stream, see it) | Coercion by the property's RNA type: list → vector/color/matrix, string → enum (validated, error lists the valid identifiers), `{uid}` / `"#uid"` / `"OBJECT:Name"` → pointer (resolved through the pointer's `fixed_type`), int → float, 0/1 → bool. Read-only properties are refused with the RNA message. Reply per item: `{ref, set: bool, value_after, error?}`. `frame_current` and other props with side effects are set through the API that triggers the update (`scene.frame_set` for `frame_current`); keep a small map. |
| `call_operator` | `drv_op` | `op` (`"mesh.bevel"`), `params` JSON (validated against the operator's RNA before the call: unknown key → error naming the closest keys; bad enum → valid values), `context` JSON (section 2.4), `exec_context="EXEC_DEFAULT"` (INVOKE_* is refused headless and allowed live only when `context.area` is given), `undo=True` (wraps in `undo_push` so `transaction`/`batch` rollback covers it) | Polls first under the override: on poll failure returns `{error, poll_message, context_applied}` without calling. On success `{result: ["FINISHED"], reports: [...captured print/report lines], context_applied, elapsed_ms}`. `RUNNING_MODAL` / `PASS_THROUGH` are returned verbatim with a warning that the operator is modal and nothing waits for it. Mode, selection and active object are restored in `finally` unless `context.keep=True`. |
| `call_method` | `drv_call` | `ref` (an ID or struct), `method`, `args` JSON list, `kwargs` JSON; `depsgraph=True` supplies `context.evaluated_depsgraph_get()` for methods whose first parameter is a `Depsgraph` (`evaluated_get`, `to_mesh`) | Validated against `bl_rna.functions[method].parameters` (closest names on a miss). Return value encoded like `rna_get`; an ID return carries its uid; a struct return is reported as `{struct, path}` when it has a `path_from_id`. `from_pydata`, `validate`, `calc_tangents`, `transform_apply` (via `call_operator`), `evaluated_get`, `to_mesh`/`to_mesh_clear`, `new_from_object` (a `data` method) are the ones the recipes use; test them. |
| `data_new` | `drv_new` | `collection` (`objects`, `meshes`, `materials`, `lights`, `cameras`, `collections`, `armatures`, `images`, `node_groups`, `actions`, `curves`, `texts`, ...: any `bpy.data` collection with a `new`), `args` JSON list / `kwargs` JSON matched to that collection's `new` signature from RNA (`objects.new(name, object_data)` takes a ref for `object_data`), `link_to=None` (`"scene"`, `"COLLECTION:Name"`, `"#uid"`: links a new object into that collection, default the active scene's root collection), `set` JSON (initial `rna_set` list on the new ID) | `{uid, id_type, name (final, after Blender's dedupe), linked_to}`. Collections without `new` are listed in the error. |
| `data_remove` | `drv_remove` | `refs`, `do_unlink=True`, `purge_orphans=False` (runs `orphans_purge` with `do_recursive=True` after) | Per ref: `{ref, removed, users_before}`; a ref with users and `do_unlink=False` is refused with the user count (`user_map`). |
| `scene_snapshot` | `drv_snapshot` | `name` (stored in `driver_namespace["blendermcp_driver_snapshots"]`, same pattern as reference images and settings snapshots, dies with the session), `scope="SCENE"` (objects reachable from the active scene) or `"DATA"` (every ID collection), `types=None` (comma list of `id_type` filters), `fields="name,type,parent,location,rotation_euler,scale,hide_viewport,hide_render,users"` (per-ID properties captured), `include=None` (comma list of collection expansions captured per object: `modifiers`, `constraints`, `material_slots`, `vertex_groups`, `children`), `depth=1` (parent/child tree depth), `return_tree=True`, `max_chars` | Tree keyed by uid: `{captured_at, scope, count, ids: {uid: {id_type, name, fields..., include...}}, tree: [{uid, children: [...]}]}`. With `return_tree=False` only `{name, count, chars}` comes back (capture for a later diff without paying for the reply). |
| `scene_diff` | `drv_diff` | `a` (snapshot name), `b="NOW"` (snapshot name or the live scene captured with `a`'s parameters), `max_chars` | `{added: [{uid, id_type, name}], removed: [...], changed: {uid: {field: [before, after]}}, renamed: [{uid, before, after}]}`. Renames are detected by uid, which is the point of uids. Same shape as `diff_node_trees` in the node-graph request; share code where possible. |
| `batch` | `drv_batch` | `commands`: JSON list of `{id?, type, params}` where `type` is ANY handler key (driver or existing), `stop_on_error=True`, `undo="AUTO"` (`undo_push` before the batch and `ed.undo` back to it when a command fails and `stop_on_error` is set; `"NONE"` for read-only batches), `refs=True` (string values of the form `"$<id>.<json.path>"` in later params are replaced by that earlier result, e.g. `"$new_cube.uid"`) | One round trip, one main-thread timer slot (the whole batch runs inside one `execute_wrapper` call, so no other client command interleaves). Reply `{results: [{id, status, result|error, elapsed_ms}], rolled_back: bool, stopped_at}`. The server-side tool accepts the same JSON and returns it pretty-printed and capped. Cap the batch at 200 commands and 60 s wall time (then stop, report, roll back if `undo=AUTO`). |
| `transaction` | `drv_transaction` | `action="BEGIN"|"COMMIT"|"ROLLBACK"|"STATUS"`, `name=None` | `BEGIN`: `undo_push(message="mcp:<name>")`, remembers the undo step; `ROLLBACK`: `ed.undo` until the step is popped (verify headless behaviour with `drvcheck.py`: `use_global_undo`, poll of `ed.undo` in `--background`; if undo is unavailable headless the handler must say so, and the harness asserts the message rather than the rollback); `COMMIT`: drops the marker; nested `BEGIN` is refused with the open transaction's name. `STATUS`: open transaction, undo steps available. |

Eleven tools. `find_objects_by_type`, `get_object_info`, `get_scene_info` are NOT
replaced; they are the friendly views and gain uids (section 3).

### 2.3 Tier 2, events and discovery (build in the same pass if Tier 1 lands by the gate)

| Tool | Handler | Parameters | Behaviour and reply |
|---|---|---|---|
| `events_subscribe` | `drv_sub` | `kinds` comma list from `depsgraph`, `frame`, `render`, `bake`, `file` (save/load), `undo`, `property`; `watch=None` (for `property`: JSON list of `[struct_name, prop_id]` pairs for `msgbus.subscribe_rna`, e.g. `["Object","location"]`), `buffer=1000` | Registers the `@persistent` app handlers once (in `register()`, always on but cheap: they only append when a subscription exists) and the msgbus keys under a driver owner. Reply `{subscription, cursor}`. Events are queued on Blender's main thread into a ring buffer (cap `buffer`, oldest dropped, drop count reported). |
| `events_poll` | `drv_poll` | `cursor`, `limit=200`, `kinds=None`, `wait_ms=0` (the server side may sleep and retry up to `wait_ms` in ≤ 250 ms steps; the add-on never blocks) | `{events: [{seq, t, kind, data}], cursor, dropped}`. `depsgraph` events carry `[{uid, id_type, name, is_updated_transform, is_updated_geometry, is_updated_shading}]` from `depsgraph.updates` (cap 50 per event, count reported); `frame` carries `frame_current`; `render` carries the handler name and the output path when known; `property` carries the msgbus key. |
| `events_unsubscribe` | `drv_unsub` | `subscription=None` (all if None) | Clears the msgbus owner (`clear_by_owner`) and the buffer. `unregister()` does the same. |
| `find` | `drv_find` | `id_type="OBJECT"`, `name=None` (glob), `where=None` (JSON list of `[path, op, value]` with `op` in `==, !=, <, <=, >, >=, in, contains, exists`; `path` relative to the ID, e.g. `["type","==","MESH"]`, `["modifiers[].type","contains","ARMATURE"]`), `scope="SCENE"|"DATA"`, `fields="name,type"`, `limit=100` | `{count, items: [{uid, id_type, name, fields...}]}`. This is the query tool that removes the need for one `find_*` tool per question. |
| `context_info` | `drv_context` | `fields=None` | `{mode, active_object (uid), selected (uids), scene, view_layer, window: bool, areas: [{type, regions}], frame_current, edit_object, pose_object}`. What `call_operator`'s context declaration will be applied against; headless reports `window: false` so the agent knows viewport ops cannot run. |

### 2.4 The `context` declaration for `call_operator`

```json
{"mode": "EDIT", "active": "#127", "selected": ["#127", "OBJECT:Cutter"],
 "area": "VIEW_3D", "region": "WINDOW", "scene": "SCENE:Scene", "keep": false,
 "extra": {"edit_object": "#127"}}
```

- `active` / `selected` go through `_selection_scope` (restored in `finally`).
- `mode` uses `object.mode_set` on the active object inside `_mode_restore`; leaving edit
  mode on an armature or mesh is restored the same way the rigging handlers do it.
- `area` / `region` resolve to the first matching area of `_gui_window()` and enter the
  same `temp_override(window, area, region)` as `capture_viewport_angle`; without a window
  the reply is the teaching error the capture tools already give ("needs the GUI").
- `extra` is passed to `temp_override` verbatim for the rare keys (`edit_object`,
  `pose_object`, `selected_editable_objects`, `point_cache`, `modifier`). Unknown keys are
  refused by Blender and the error is passed through with the key name.
- `keep=true` leaves the mode/selection as the operator left them (for a batch that
  continues in edit mode); the batch's end restores everything.

### 2.5 Refused operators (kept small, listed in `describe(what="ops")` replies)

`wm.quit_blender` (use `quit_blender`), `wm.console_toggle`, `wm.window_new`,
`wm.window_fullscreen_toggle`, `screen.animation_play` (modal, never returns headless;
use `set_frame`/`playblast`), `wm.read_factory_settings` (kills the socket server),
`preferences.addon_disable` on `blendermcp` (the panel does it), `script.reload`. Anything
else runs. `wm.open_mainfile` / `wm.save_mainfile` run but the reply says the file-lifecycle
tools (`load_blend`, `save_blend`) carry the safety checks.

### 2.6 Tier 3, later (do NOT build unless asked)

Schema export (`describe` dump of every struct and operator to JSON for offline clients),
generated typed client for the server side, a Python-code-to-driver-calls translator for
the existing handlers, live viewer-node readback, event push over a second socket instead
of polling.

## 3. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `get_scene_info`, `get_object_info`, `find_objects_by_type`, `get_armature_info`, `list_node_trees` (when built) | Names are the only handle | Every object entry gains `uid` (`session_uid`). Additive reply keys, PROTOCOL stays 1. |
| `execute_blender_code` / `execute_code` | Returns stdout only; an exception is a one-line string; no value return | Add `result_expr=None`: an expression evaluated after `exec` and returned JSON-encoded with the `rna_get` value rules. On exception return `{error, traceback (last 3 frames), line}` instead of raising. Keep the stdout capture. |
| `add_modifier` | Flat `props` cannot reach nested settings (`domain_settings.resolution_max`) | Route `props` through the `rna_set` coercer: dotted keys and pointer-by-ref values work everywhere, and the `set`/`unset` reply stays. The simulation request's fix becomes this one. |
| `set_settings` / `get_settings` / `describe_settings` (2.1.0) | A second, settings-only path resolver and describer | Re-implement on `rna_get` / `rna_set` / `describe` with the `_scope_owner` roots as the ref prefixes (`SCENE:Scene/render.*`). Replies byte-identical for the same inputs (harness asserts against the 2.1.0 baseline replies). |
| `store_reference_image`, `settings_snapshot`, `save_session_state` | Three private `driver_namespace` registries | Leave them, but the new driver snapshots and event buffers follow the same `driver_namespace` key convention (`blendermcp_driver_*`) and are listed by a single `list_session_registries` addition to `get_version`'s reply (`registries: {name: count}`). |

## 4. Ripple points beyond the standard list

- `addon.py`: a `# ─── Generic driver ───` section; new helpers `_resolve_ref(ref) ->
  (owner, prop_id | None, error)`, `_encode_value(value, depth, fields, cap)`,
  `_coerce_value(prop, value)`, `_describe_struct(srna)`, `_describe_op(idname)`,
  `_apply_context(decl)` (context manager built from the existing scopes), `_uid_index`.
  Handlers registered in `extended_handlers` under the `drv_` keys above. App handlers for
  events registered in `register()` with `@persistent`, removed in `unregister()`.
- `src/blender_mcp/server.py`: the 16 `@mcp.tool()` wrappers; `_settings` gains
  `driver_reply_max_chars` (default 24000) and `driver_batch_max` (default 200); the
  server-side `events_poll` wait loop; `batch` result pretty-printer.
- `src/blender_mcp/settings.py`: the two keys, with env names `BLENDER_DRIVER_REPLY_MAX`
  and `BLENDER_DRIVER_BATCH_MAX`.
- `TOOLS.md`: section "Generic Driver" with the ref grammar, the value encoding table,
  the context declaration and the refused-operator list; header count; TOC.
- `README.md`: category row, "What's new" paragraph, the `execute_blender_code`
  paragraph in Architecture points at the driver.
- `/blender` skill (`C:\Users\Naabin\.claude\commands\blender.md`): a "Generic driver"
  workflow pattern (`describe` → `find` → `rna_get` → `batch` of `rna_set` /
  `call_operator` inside a `transaction` → `scene_diff`), quick-reference rows, and the
  Decision Rules change: "prefer `describe` + driver calls over `execute_blender_code`
  for anything without a dedicated tool; keep `execute_blender_code` for BMesh/numpy
  loops". Note the radians default.
- `docs/apichecks/README.md`: the `drvcheck.py` row; its `DRVCHECK` lines appended to
  `out_4.3.2.jsonl` and `out_5.2.1.jsonl` under `### drvcheck.py` (pre-existing lines
  untouched, M10 drift rule).
- `tests/headless_handlers.py`: `--drv` flag running `tests/drv_steps.py` (section 6);
  `tests/test_drv_server.py` for the server-side parsing (ref grammar, `$id.path`
  substitution, cap/truncation text, coercion of the JSON parameter styles) with a mocked
  connection like `test_b0_server.py`.
- `docs/release-notes/v<version>.md`, `PLAN.md` slice amendment, auto-memory
  `project_blender_mcp.md`, MemPalace `blender-mcp` db decisions drawer.
- The other request docs: each gets a one-line status note naming which of its tools are
  now recipes (section 5) so nobody builds an add-on handler for them.

## 5. Recipe mapping (what the other docs' tools become)

Recipes are server-side functions that issue driver calls (usually one `batch` inside a
`transaction`). They keep their tool name and docstring; they lose their add-on handler.
The Lead decides per tool at that doc's amendment; the default is this table.

| Request doc | Becomes a recipe on the driver | Stays add-on side (needs BMesh/numpy/render) |
|---|---|---|
| Engine readiness | `apply_transforms`, `set_origin_*`, LOD chain (Decimate modifiers + `object.modifier_apply`), `add_collision_proxy` (convex hull via `call_operator("mesh.convex_hull")`), naming/renaming, export presets (`call_operator("export_scene.fbx", ...)` with per-engine kwargs). OUTCOME 2.4.0 (team lohjud, Lead ruling D5/H23, PLAN.md 13.2.0): ALL 22 engine-readiness tools shipped as add-on handlers; no recipe was built and the `_drv_batch` seam (R-B2-9) stays unbuilt. Candidates for a later lift onto the driver: `apply_transforms`, `set_pivot`, `snap_to_grid`, `list_sockets`, `build_export_hierarchy`. | `validate_asset` numeric checks, manifold/ngon/normal analysis, budgets |
| Asset construction | bisect, mirror, symmetrize, remesh, dissolve, bridge, solidify, arrays on curves (all operators or modifiers), grid snapping, greybox primitives, instancing | kitbash placement heuristics, terrain from heightmap (numpy) |
| UV and texturing | seams by angle, unwrap, smart project, pack islands, image new/save/reload, node wiring (via node-graph driver) | UV stretch/island metrics, bake orchestration with file checks, UV layout render |
| Presentation and library | asset marking, metadata/custom props, batch rename, import hygiene renames | every capture and sheet tool |
| Rigging Tier 2 | shape keys, drivers, NLA, action management, mirror/rename bones, Rigify generate | weight tools with numpy |
| Simulation and VFX | fluid domain/flow/effector, particles, force fields, cloth/soft body/rigid body, quick effects, bake triggers | VAT baking, flipbook compositing, fluid stats |
| Node graphs | `add_node`, `link_nodes`, `set_node_input`, `remove_node`, zones, groups: the node-graph driver is a specialisation of this one (same ref grammar with `NODETREE:` roots and `nodes["Name"].inputs[0].default_value` paths) | `render_node_graph`, `evaluate_geometry`, catalogue building |
| Settings, save, load | `get_settings` / `set_settings` / `describe_settings` (section 3) | file lifecycle checks, presets, session state |

## 6. Facts verified headlessly (do not re-derive) and facts to verify first

Verified 2026-09-09 with `apichecks/cdp.py` on 4.3.2 and 5.2.1 (identical except the uid
values):

- `ID.session_uid` is an int and unchanged after `ob.name = ...` (127 on the 4.3.2 run,
  136 on 5.2.1: uids are per session and per file load, never persist them).
- `ob.as_pointer()` exists; do not use it as a handle (reused after free).
- `bpy.msgbus` has `subscribe_rna`, `publish_rna`, `clear_by_owner`.
- `bpy.app.handlers` lists 40+ hooks including `depsgraph_update_pre/post`,
  `frame_change_pre/post`, `render_init/pre/post/complete/cancel/write/stats`,
  `object_bake_pre/complete/cancel`, `save_pre/post`, `load_pre/post`, `undo_pre/post`,
  `redo_pre/post`, `blend_import_pre/post`, `animation_playback_pre/post`.
- `ed.undo_push` has one property, `message`; `ed.undo` is `ED_OT_undo`.
- `bpy.types.Object.bl_rna.properties` has 133 entries; `bl_rna.functions` includes
  `rename`, `evaluated_get`, `copy`, `asset_mark`, `asset_clear`, `asset_generate_preview`.
- `bpy.ops.mesh.bevel.get_rna_type()` gives `description` and typed `properties`
  (`offset_type` ENUM, `offset` FLOAT, `profile_type` ENUM, `offset_pct` FLOAT, ...).
- `ob.path_resolve("location")` returns a `Vector`; `scene.render.path_from_id
  ("resolution_x")` returns `"render.resolution_x"`.
- `hasattr(bpy.types.Depsgraph, "updates")` is False on the TYPE; the instance attribute
  `depsgraph.updates` is what handlers read. Test on an instance.
- `bpy.app.timers.register` exists; per `PLAN.md` C19 timers do NOT fire in `--background`
  while a script sleeps, so `events_poll(wait_ms)` waiting happens on the SERVER side.

Facts already established elsewhere in this repo that the driver must respect:

- `hasattr(bpy.ops.x, "y")` is always True: use `_op_exists` / `get_rna_type()` in a
  try (v1.6.0).
- Viewport operators need `region` in the `temp_override`, and on 5.2 `wm.redraw_timer`
  must run outside the area/region override (A1.2).
- `bpy.context.window` is None inside a timer right after `open_mainfile`; use
  `_gui_window()` (A1.2).
- Dynamic enums (`render.engine`, view transforms, Cycles devices, fluid cache formats)
  read empty from `bl_rna` headless: `describe` marks them `dynamic` (cyc.py, B0 facts).
- 4.x/5.x differences by try-assign, never `bpy.app.version` compares (M7 grep must stay 0).

VERIFIED 2026-10-16 with `apichecks/drvcheck.py` on 4.3.2 and 5.2.1 (`DRVCHECK` lines in
`out_4.3.2.jsonl` L40-41 and `out_5.2.1.jsonl` L32-33; the full table with consequences is
`PLAN.md` 12.6). The nine questions above, answered:

1. Uid lookup: a full `bpy.data` scan costs 0.24 / 0.27 ms worst case on 2000 objects, so NO
   index is kept (PLAN R16); skip `all_ids` on 5.x (union view). Uids are monotonic, never
   reused after remove, survive rename and undo. Modifiers, bones, nodes have no uid.
2. Undo WORKS in `--background` on both installs (`undo_push` / `undo` FINISHED,
   `ed.undo.poll()` True, the location reverts): `transaction` and `batch(undo=AUTO)` are
   asserted for real in the harness. Rollback is by a Scene idprop marker, not by counting
   (R22), because the undo step index has no API and Scene idprops do not survive undo.
3. msgbus delivers NOTHING while a `--background` script runs, even after `publish_rna`:
   `rna_set(publish=True)` stays but no harness step asserts msgbus; `property` events are
   live-only (R18).
4. `depsgraph_update_post` fires after `view_layer.update()` and after
   `evaluated_depsgraph_get()`, with `DepsgraphUpdate.{id, is_updated_transform,
   is_updated_geometry, is_updated_shading}`; `frame_change_post` fires on `frame_set`.
   Timers do NOT fire during a sleeping script: `events_poll(wait_ms)` waits server-side.
5. Every descriptor field is readable on both: `hard_min/max`, `soft_min/max`,
   `default_array`, `fixed_type`, `srna`, `length_max` (64 on 4.3.2, 256 on 5.2.1: report,
   never assert), `is_enum_flag`, `unit` (`LENGTH` on location, `ROTATION` on
   rotation_euler), `bl_rna.base` (`ID`), function `parameters` with `is_output` /
   `is_required`; `ObjectModifiers.new(type)` has 83 enum items.
6. Operators: `poll()` False for `mesh.bevel` in object mode, True in edit mode; the
   positional `"EXEC_DEFAULT"` form works; `temp_override` accepts ANY key silently (so the
   driver whitelists 16 keys, R19); `bl_options` is unreachable for C operators (dropped from
   `describe`); 2321 / 2499 operators enumerate via `get_rna_type` in about 100 ms. Error
   texts are identical on both installs and are passed through verbatim as `rna_error`.
7. Coercion: RNA itself coerces list -> Vector, int -> float, int -> bool; the driver adds
   refs -> pointers, degrees and enum validation with closest names. `["key"]` ID-property
   paths resolve. A flat Object dump is 4454 / 4761 chars (24000 cap stands).
8. 29 `bpy.data` collections have `new` (none: libraries, screens, window_managers, fonts,
   shape_keys, sounds, movieclips, cache_files, paint_curves, workspaces, + all_ids on 5.x);
   `objects.new(name, object_data)`; `remove(object, do_unlink, do_id_user, do_ui_user)`;
   `orphans_purge(do_local_ids, do_linked_ids, do_recursive)`; `user_map` returns a dict;
   `batch_remove` exists.
9. WRONG as written above: under `--factory-startup` a headless session HAS a window, a screen
   and 4 areas. The "needs the GUI" error is keyed on `bpy.app.background` (R20).
10. (added) The `IDTYPE:` -> collection map cannot be derived from `fixed_type.identifier`
    (FONT, LINESTYLE, META, PARTICLE unmapped on 4.3.2, GREASEPENCIL_V3 on 5.2.1): a static
    table with ordered `hasattr` candidates is used (R26).

## 7. Testing (required before you report done)

Headless, `--background --factory-startup`, handlers called through `execute_command`
exactly as the harness does (`tests/drv_steps.py`, run on BOTH installs):

1. `describe("Object")` returns 130+ properties with `location` FLOAT array 3 unit LENGTH,
   `type` ENUM read-only with the object-type identifiers, `parent` POINTER fixed_type
   Object, `modifiers` COLLECTION. `describe("mesh.bevel")` lists `offset` FLOAT with
   soft range and `poll_now` False in object mode. `describe("NoSuchStruct")` and
   `describe("mesh.no_such_op")` fail with the closest names.
2. `data_new("objects", ["Drv", "$mesh.uid"])` after `data_new("meshes", ["DrvMesh"])` in
   one `batch` with `$` substitution: two uids, the object linked to the scene collection,
   `find(name="Drv*")` returns it with the same uid.
3. `rna_set([["#<uid>/location", [1,2,3]], ["#<uid>/rotation_euler", [90,0,0]]],
   angles="DEGREES")` then `rna_get("#<uid>", fields="location,rotation_euler")` reads
   `[1,2,3]` and `[1.5708,0,0]` (radians by default). A read-only set (`/type`), a bad
   enum (`SCENE:Scene/render.engine` = `NOPE`) and an unknown property each return the
   teaching error and, with `stop_on_error=False`, the other items still apply.
4. Rename the object through `rna_set(".../name")`; `find` by the OLD name returns nothing,
   `rna_get("#<uid>")` still resolves. Then `scene_snapshot("a")`, rename again,
   `scene_diff("a")` reports one `renamed` entry keyed by uid and nothing `added`.
5. `call_operator("object.modifier_add", {"type":"BEVEL"}, context={"active":"#<uid>",
   "selected":["#<uid>"]})` returns `["FINISHED"]` and `rna_get(".../modifiers[]",
   depth=1)` lists the Bevel; `call_operator("mesh.bevel", ...)` WITHOUT `mode:"EDIT"`
   returns the poll error naming the context; WITH `mode:"EDIT"` and `context.keep=false`
   the object is back in OBJECT mode afterwards (`context_info().mode == "OBJECT"`).
6. `call_method("#<uid>", "evaluated_get", depsgraph=True)` returns the evaluated object's
   uid; `call_method("MESH:DrvMesh", "from_pydata", [[[0,0,0],[1,0,0],[0,1,0]], [],
   [[0,1,2]]])` then `rna_get("MESH:DrvMesh", fields="polygons")` reports `count 1`.
7. `transaction(BEGIN, "t1")`, move the object, `transaction(ROLLBACK)`: location restored
   OR the reply says undo is unavailable headless (per fact 2 in section 6; the live test
   then proves the rollback). Nested `BEGIN` is refused.
8. `batch` of 5 commands with `stop_on_error=True` where the 3rd fails: `stopped_at == 2`,
   `rolled_back` reflects fact 2, the 4th and 5th are `skipped`. A 201-command batch is
   refused before running anything.
9. `rna_get("SCENE:Scene", fields="all", depth=2, max_chars=2000)` is truncated with the
   `truncated` list naming the dropped fields and the reply length ≤ 2000 + the envelope.
10. `events_subscribe("depsgraph,frame")`, move the object, `view_layer.update()`,
    `frame_set(5)`, `events_poll(cursor)` returns a `depsgraph` event carrying the uid
    with `is_updated_transform` True and a `frame` event with 5; `events_unsubscribe()`
    then `events_poll` returns nothing and `dropped == 0`.
11. `data_remove("#<uid>", purge_orphans=True)` removes object and mesh;
    `rna_get("#<uid>")` returns the not-found error with `tried`.
12. Regression: every existing harness step still passes (Phase A, B0, B1) and the
    `get_settings` / `set_settings` replies match the 2.1.0 baseline byte for byte.
13. `tests/test_version_sync.py` and the venv unit tests green; `tools/list` count =
    169 + 16 and `tests/baseline_tools_<version>.txt` written from the real list.

Live (Blender 5.2.1 GUI, by the live-test agent, after deploy on the user's word):

- `call_operator("view3d.view_all", context={"area":"VIEW_3D","region":"WINDOW"})`
  returns FINISHED; the same call headless returns the "needs the GUI" error.
- `transaction` rollback really reverts (Ctrl-Z equivalent visible in the viewport), and
  Blender's own undo history shows one `mcp:<name>` step.
- `events_poll(wait_ms=2000)` returns a `frame` event while the user scrubs the timeline.
- One `batch` of 50 `rna_set` calls completes in one round trip and Blender stays
  responsive (no per-command timer starvation).

Report PASS / FAIL per step with the pasted output. Do not weaken an assertion to make it
pass.

## 8. Risks to design around

- **Token cost.** A flat Object dump is a few thousand characters; a naive `depth=2` scene
  read is megabytes. The cap is not optional, and the default `fields` for a struct ref
  exclude pointers and collections for that reason.
- **Error rate.** Generic calls lose the guidance a docstring gives. `describe` and the
  teaching errors close most of the gap; the recipes cover the frequent paths; the
  `/blender` skill tells the agent to `describe` first.
- **Undo scope.** `ed.undo` reverts everything since the push, including changes the user
  made in the GUI in between. `transaction` replies carry the undo step count so the
  agent can see when the user has interleaved work, and the skill says to keep
  transactions short.
- **Modal operators.** Nothing waits for them. The refused list plus the
  `RUNNING_MODAL` warning is the whole mitigation.
- **Main-thread time.** A 200-command batch runs inside one timer slot and freezes the
  GUI for its duration; the 60 s wall cap and the elapsed report exist for that. Baking
  and rendering stay on their dedicated tools.
- **Uid index staleness.** Undo, redo and file load invalidate uids; the handlers listed
  in 2.1 rebuild the index, and a miss falls back to a scan, never to a stale hit.
