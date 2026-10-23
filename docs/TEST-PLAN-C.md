# TEST-PLAN-C: generic driver Tier 1 (2.3.0), QA acceptance

Owner: QA (Margaret), team nosubl. Written 2026-10-16 from `FEATURE-REQUEST-generic-driver.md`
sections 2, 3, 7 and `LEAD-RULINGS-C.md` R1-R10, BEFORE PLAN.md section 12 (C.1) landed. Where C.1
changes a name, key or number, C.1 wins and this file is amended in the same minute.
Harness vocabulary = `tests/headless_handlers.py`: `call(cmd, **params)` goes through
`srv.execute_command({"type", "params"})`; `ok(reply, must=[keys])` asserts `status == "success"`
and no `result.error`; `err(reply, "substr")` asserts an error reply whose text contains the
substring; every `step()` additionally asserts `bpy.context.mode == "OBJECT"` afterwards and that
the scene list did not change (`readonly=True` steps: the whole snapshot is unchanged).
Fixtures are built with `bpy.data` / `bpy.ops`, never with the handler under test.

## 0. Baseline (taken from disk 2026-10-16 01:20Z, addon `0eb7ec4c`, server `2ddfc98a`)

| Gate | Command | Baseline |
|---|---|---|
| Harness 4.3.2 | `D:\blender-4.3.2-windows-x64\blender.exe --background --factory-startup --python tests\headless_handlers.py -- --b0 --b1 --out tests\report_c0_4.3.2.json` | steps 186, passed 186, failed 0, handlers 158 |
| Harness 5.2.1 | same with `D:\blender-5.2.1-windows-x64` and `report_c0_5.2.1.json` | steps 186, passed 186, failed 0, handlers 158 |
| Venv | `set BLENDER_MCP_EXPECTED_VERSION=2.2.0` + `.venv\Scripts\python.exe -m pytest tests -q` | 69 passed |
| Settings replies | `<install>\blender.exe --background --factory-startup --python tests\settings_baseline.py -- --check` | `match: true`, 64 calls, fixtures `tests/baselines/settings_replies_{4.3.2,5.2.1}.json` |
| tools/list | `.venv\Scripts\python.exe tests\dump_tools_list.py names` | 169 names = `tests/baseline_tools_2.2.txt` |

## 1. Headless steps (both installs, `-- --b0 --b1 --drv`, file `tests/drv_steps.py`)

Numbering follows doc section 7. Each step lists the exact PASS conditions; a step passes only
when every condition holds on BOTH installs. "T2" marks a condition that needs a Tier 2 tool and
is replaced by the Tier-1 substitute in brackets until Tier 2 lands (see section 5, I1).

### D1 describe
1. `ok(call("describe", what="Object"))`: `identifier == "Object"`, `len(properties) >= 130`,
   `properties.location == {type: "FLOAT", array_length: 3, unit: "LENGTH", readonly: False, ...}`,
   `properties.type.type == "ENUM"`, `properties.type.readonly is True`, `"MESH"` and `"ARMATURE"`
   in `[i[0] for i in properties.type.enum_items]`, `properties.parent.type == "POINTER"`,
   `properties.parent.fixed_type == "Object"`, `properties.modifiers.type == "COLLECTION"`,
   `"functions" not in reply` (default `include_functions=False`).
2. `ok(call("describe", what="Object", include_functions=True))`: `functions.evaluated_get.params`
   is a list of 4-lists `[id, type, is_output, is_required]` and contains `"depsgraph"` as a
   required input.
3. `ok(call("describe", what="Object", fields="location,name"))`: `set(properties) == {"location","name"}`.
4. `ok(call("describe", what="Object", search="rotation"))`: every returned property identifier,
   name or description contains `rotation` (case-insensitive); `rotation_euler` present,
   `location` absent.
5. `ok(call("describe", what="mesh.bevel"))`: `idname == "mesh.bevel"`, `properties.offset.type ==
   "FLOAT"`, `properties.offset.soft_min` and `soft_max` present and numeric, `poll_now is False`
   (object mode, nothing in edit mode), `bl_options` is a list.
6. `ok(call("describe", what="OBJECT:Cube/location"))` (a property ref): the single-property shape
   `{identifier: "location", type: "FLOAT", array_length: 3, ...}` and NOT a struct dump.
7. `ok(call("describe", what="SCENE:Scene/render"))`: `identifier == "RenderSettings"`;
   `properties.engine.dynamic is True` on 5.2.1 if `enum_items` reads empty headless, and the reply
   text says to validate by assignment (B0 fact: `render.engine` is dynamic; if items are non-empty
   on 4.3.2, `dynamic` may be False there, assert `dynamic in (True, False)` and items consistent).
8. `err(call("describe", what="NoSuchStruct"), "Object")` is NOT required; required: the error
   names at least one closest identifier (`difflib`) and contains `NoSuchStruct`.
   `err(call("describe", what="mesh.no_such_op"), "mesh.")`: the error lists closest operator ids.
9. `err(call("describe", what=""))` and `err(call("describe"))` (missing param): an error, never a
   traceback string (`"Traceback" not in msg`).
10. Second `describe(what="Object")` in the same session returns byte-identical `properties`
    (catalogue cache): `json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)`.

### D2 data_new + batch + $ substitution
1. `ok(call("batch", commands=[{"id":"mesh","type":"data_new","params":{"collection":"meshes","args":["DrvMesh"]}},
   {"id":"ob","type":"data_new","params":{"collection":"objects","args":["Drv","$mesh.uid"],"link_to":"scene"}}]))`:
   `len(results) == 2`, both `status == "ok"` (or the C.1 name), `rolled_back is False`,
   `stopped_at is None`; `results[0].result.uid` and `results[1].result.uid` are distinct ints;
   `results[1].result.id_type == "OBJECT"`, `.name == "Drv"`, `.linked_to` names the scene's root
   collection.
2. Blender truth: `bpy.data.objects["Drv"].data.name == "DrvMesh"`,
   `bpy.data.objects["Drv"].session_uid == results[1].result.uid`, `"Drv" in bpy.context.scene.collection.objects`.
3. T2 `find(name="Drv*")` returns one item with the same uid [substitute:
   `ok(call("get_scene_info"))` object entry `Drv` carries `uid == <uid>` (section 3 fix), and
   `ok(call("rna_get", refs="data/objects[\"Drv\"]", fields="name"))` carries `results[0].uid == <uid>`].
4. `err(call("data_new", collection="no_such_collection"), "collection")`: the error lists the
   collections that have `new`. `err(call("data_new", collection="screens"))` (no `new`): same list.
5. `ok(call("data_new", collection="objects", args=["Drv"], kwargs={"object_data": None}))` (dedupe):
   `name == "Drv.001"` (Blender's suffix is reported, not the requested name).
6. `ok(call("data_new", collection="materials", args=["DrvMat"], set=[["/diffuse_color",[1,0,0,1]]]))`:
   `bpy.data.materials["DrvMat"].diffuse_color[:] == (1,0,0,1)` (initial `set` applied on the new ID).

### D3 rna_set / rna_get, coercion, angles, stop_on_error
1. `ok(call("rna_set", sets=[["#<uid>/location",[1,2,3]],["#<uid>/rotation_euler",[90,0,0]]], angles="DEGREES"))`:
   every item `set is True`, `value_after` present; Blender truth `tuple(ob.location) == (1,2,3)`,
   `abs(ob.rotation_euler.x - 1.5708) < 1e-3`.
2. `ok(call("rna_get", refs="#<uid>", fields="location,rotation_euler"))`: `results[0].ref == "#<uid>"`,
   `results[0].uid == <uid>`, `results[0].value.location == [1,2,3]`,
   `abs(results[0].value.rotation_euler[0] - 1.5708) < 1e-3` (radians by default, R10),
   `truncated == []`, `chars` is an int `>= len(json.dumps(value))`.
3. Same read with `angles="DEGREES"`: `abs(rotation_euler[0] - 90) < 1e-3`.
4. `rna_set(stop_on_error=False)` with 4 items: `[ref/type, "EMPTY"]` (read-only),
   `["SCENE:Scene/render.engine","NOPE"]` (bad enum), `["#<uid>/no_such_prop", 1]` (unknown),
   `["#<uid>/location[2]", 9]` (valid, last): reply `status == "success"`, items 0-2 carry
   `set is False` and `error`; item 0 error contains `read-only` (or Blender's text, case-insensitive
   `readonly|read-only`); item 1 error contains `CYCLES` (valid identifiers listed); item 2 error
   contains `location` or another closest identifier AND the property count; item 3 `set is True`;
   Blender truth `ob.location.z == 9` and `scene.render.engine` unchanged.
5. Same 4 items with `stop_on_error=True` (default): the reply stops at item 0; item 3 is reported
   `skipped` (or absent, per C.1) and `ob.location.z` is NOT changed from the previous value.
6. Coercion table, one `rna_set` each, assert Blender truth after:
   `int -> float` (`/scale[0]`, 2 -> 2.0), `0/1 -> bool` (`/hide_render`, 1 -> True),
   `string -> enum` (`/rotation_mode`, "XYZ"), `list -> color` (`MATERIAL:DrvMat/diffuse_color`),
   `pointer by "#uid"` (`/parent` = `"#<other uid>"`), `pointer by "OBJECT:Name"`,
   `pointer by {"uid": n}`, `null -> pointer None` (`/parent` = `None`),
   `custom ID prop` (`/["drv_tag"]` = `"x"` then `rna_get(fields='["drv_tag"]')` or `custom.drv_tag == "x"`),
   `frame_current` via `SCENE:Scene/frame_current` = 5 -> `scene.frame_current == 5` AND a
   `frame_change_post` handler registered by the test fired (side-effect map, doc 2.2).
7. Value encoding in `rna_get` (one call, `fields="matrix_world,parent,modifiers,type,hide_render,name"`,
   `depth=0`): `matrix_world` is 4 lists of 4 floats; `parent` is `None` or `{uid, id_type, name}`;
   `modifiers == {"count": n}`; `type == "MESH"` (enum as identifier); `hide_render` is bool.
   With `fields="modifiers[]", depth=1`: `modifiers.items` is a list whose entries carry `name` and `type`.
8. `rna_get` with many refs: `refs='["#<uid>","OBJECT:Cube","MESH:DrvMesh"]'` (JSON string) and
   `refs="#<uid>,OBJECT:Cube,MESH:DrvMesh"` (comma string) return equal `results` (seam I2).
9. `err(call("rna_get", refs="OBJECT:NoSuch"), "NoSuch")` carries `tried` listing `objects`;
   `err(call("rna_get", refs="#999999"))` carries `tried`; `err(call("rna_get", refs="BOGUS:Cube"))`
   names the valid id_type identifiers; `err(call("rna_get", refs="#<uid>/location[7]"))` is an
   error, not a traceback. `err(call("rna_set", sets="not json"))` is the JSON teaching error.

### D4 rename, snapshot, diff
1. `ok(call("rna_set", sets=[["#<uid>/name","DrvRenamed"]]))`: `bpy.data.objects.get("Drv") is None`,
   `bpy.data.objects["DrvRenamed"].session_uid == <uid>`.
2. T2 `find(name="Drv")` returns `count == 0` [substitute: `err(call("rna_get", refs="OBJECT:Drv"))`
   with `tried`]; `ok(call("rna_get", refs="#<uid>", fields="name")).results[0].value.name == "DrvRenamed"`.
3. `ok(call("scene_snapshot", name="a"))`: `count >= 1`, `scope == "SCENE"`, `ids` keyed by str(uid)
   with `id_type`, `name` and the default fields (`type, parent, location, rotation_euler, scale,
   hide_viewport, hide_render, users`); `tree` is a list of `{uid, children}`; `captured_at` present.
   `ok(call("scene_snapshot", name="b0", return_tree=False))` returns only `{name, count, chars}`.
4. Rename again (`DrvTwice`), `ok(call("scene_diff", a="a"))`: `renamed == [{"uid": <uid>, "before":
   "DrvRenamed", "after": "DrvTwice"}]`, `added == []`, `removed == []`, `changed` has no entry for
   `<uid>` other than possibly `name` (C.1 decides whether a rename also appears in `changed`;
   assert exactly one of the two, never both).
5. Move the object + add a cube with bpy, `scene_diff("a")`: `changed[<uid>].location == [before, after]`,
   `added` has one entry with the new cube's uid, `removed == []`.
6. Remove the bpy cube, `scene_diff("a","b0")` (two names): the new cube is absent from both;
   `err(call("scene_diff", a="nope"), "nope")`.
7. Snapshot registry: `ok(call("get_version")).registries` is a dict and
   `registries["blendermcp_driver_snapshots"] >= 2` (section 3, `list_session_registries`).

### D5 call_operator, context, poll, mode restore
1. `ok(call("call_operator", op="object.modifier_add", params={"type":"BEVEL"}, context={"active":"#<uid>","selected":["#<uid>"]}))`:
   `result == ["FINISHED"]`, `context_applied` names `active` and `selected`, `elapsed_ms` numeric,
   `reports` is a list. Blender truth: `ob.modifiers[0].type == "BEVEL"`;
   `ok(call("rna_get", refs="#<uid>", fields="modifiers[]", depth=1)).results[0].value.modifiers.items[0].type == "BEVEL"`.
2. `err(call("call_operator", op="mesh.bevel", params={"offset":0.1}, context={"active":"#<uid>","selected":["#<uid>"]}))`
   (no `mode`): the reply carries `poll_message` and `context_applied`; the operator was NOT run
   (`len(ob.data.polygons)` unchanged); `bpy.context.mode == "OBJECT"`.
3. `ok(call("call_operator", op="mesh.bevel", params={"offset":0.1,"segments":2}, context={"active":"#<uid>","selected":["#<uid>"],"mode":"EDIT"}))`:
   `result == ["FINISHED"]`; after the call `bpy.context.mode == "OBJECT"` and
   T2 `context_info().mode == "OBJECT"` [substitute: `ok(call("rna_get", refs="context/mode"))`
   value `"OBJECT"`]; `len(ob.data.polygons) > before` (bevel took effect); the active object and
   selection are what they were before the call (`_selection_scope` restored).
4. `context.keep=True` variant: after the call `bpy.context.mode == "EDIT_MESH"`; the step itself
   then calls `bpy.ops.object.mode_set(mode="OBJECT")` so `step()`'s mode invariant holds.
5. Validation before the call: `err(call("call_operator", op="mesh.bevel", params={"offest":0.1}), "offset")`
   (closest key), `err(call("call_operator", op="mesh.bevel", params={"offset_type":"NOPE"}), "OFFSET")`
   (valid enum ids), `err(call("call_operator", op="mesh.no_such"), "mesh.")` (closest ids),
   `err(call("call_operator", op="object.modifier_add", params={"type":"BEVEL"}, context={"nope":1}), "nope")`.
6. Refused list (doc 2.5), each `err(..., "refused")` or the C.1 wording, and nothing happened:
   `wm.quit_blender`, `wm.read_factory_settings`, `screen.animation_play`, `script.reload`,
   `wm.console_toggle`, `wm.window_new`, `wm.window_fullscreen_toggle`,
   `preferences.addon_disable` with `module="blendermcp"` (and the addon's actual module name).
   Blender still answers the next `call("get_version")`.
7. `exec_context="INVOKE_DEFAULT"` headless: `err(..., "INVOKE")` (refused without a GUI).
   `call_operator(op="view3d.view_all", context={"area":"VIEW_3D","region":"WINDOW"})` headless:
   `err(..., "GUI")` (the capture tools' teaching text). Live counterparts in section 4.
8. `undo=True` default: `call_operator` inside `transaction(BEGIN)` is covered by D7's rollback
   assertion (same step reads fact 2).
9. `wm.save_mainfile` with `filepath` under WORK: `result == ["FINISHED"]` AND the reply carries a
   note naming `save_blend` (doc 2.5 last sentence); `bpy.data.filepath` ends with that file.
   Restore: `bpy.ops.wm.read_homefile(app_template="")` is NOT used (it would kill the session);
   the step runs last-but-one before D11 and the harness `clear_scene()` resets data.

### D6 call_method
1. `ok(call("call_method", ref="#<uid>", method="evaluated_get", depsgraph=True))`: `result.uid` is
   an int (the evaluated object's uid), `result.id_type == "OBJECT"`.
2. `ok(call("call_method", ref="MESH:DrvMesh", method="from_pydata", args=[[[0,0,0],[1,0,0],[0,1,0]], [], [[0,1,2]]]))`
   then `ok(call("rna_get", refs="MESH:DrvMesh", fields="polygons,vertices")).results[0].value ==
   {"polygons": {"count": 1}, "vertices": {"count": 3}}`; `ok(call("call_method", ref="MESH:DrvMesh",
   method="validate")).result is False` (no errors fixed) and `bpy.data.meshes["DrvMesh"].polygons[0].area` is close to 0.5.
3. `ok(call("call_method", ref="MESH:DrvMesh", method="calc_tangents"))` returns success on both
   installs OR the Blender error text verbatim (no UV layer): assert `"Traceback" not in reply`.
4. `ok(call("call_method", ref="#<uid>", method="to_mesh", depsgraph=True))`: a struct return
   reported as `{struct: "Mesh", ...}` or an ID with uid; then `call_method(method="to_mesh_clear")` ok.
5. `ok(call("call_method", ref="data/meshes", method="new_from_object", args=["#<uid>"]))`: returns a
   new mesh uid; `bpy.data.meshes` grew by one (collection method on `data`).
6. `err(call("call_method", ref="#<uid>", method="evaluated_gett"), "evaluated_get")` (closest
   names); `err(call("call_method", ref="#<uid>", method="evaluated_get"))` without `depsgraph=True`
   and without args: error names the missing `depsgraph` parameter (`is_required`).
   `err(call("call_method", ref="#<uid>", method="from_pydata"))` (method of Mesh, not Object).

### D7 transaction
Fact 2 (drvcheck.py, Linus) decides the branch. Both branches assert; neither is weakened.
1. `ok(call("transaction", action="BEGIN", name="t1"))`: `open == "t1"` (or C.1 key), `undo_steps`
   int. `ok(call("transaction", action="STATUS")).open == "t1"`.
2. `rna_set` location to `[5,5,5]`; `r = call("transaction", action="ROLLBACK")`:
   - undo works headless: `ok(r)`, `tuple(ob.location) == <location before BEGIN>`, `STATUS.open is None`;
   - undo unavailable headless: `err(r, "headless")` (or the C.1 wording naming `--background`),
     `ob.location == (5,5,5)` untouched, and `STATUS.open is None` (the marker is dropped either way).
3. Nested: `BEGIN t2` then `err(call("transaction", action="BEGIN", name="t3"), "t2")`.
   `COMMIT` -> `STATUS.open is None`; `err(call("transaction", action="ROLLBACK"), "no open")`;
   `err(call("transaction", action="NOPE"))` lists the four actions.
4. The undo step message: when undo works, `bpy.ops.ed.undo_history` is not callable headless, so
   the message `mcp:t1` is asserted live only (section 4).

### D8 batch limits and rollback
1. 5 commands, `stop_on_error=True`, 3rd is `rna_set` on `OBJECT:NoSuch`: `len(results) == 5`,
   `results[0..1].status == "ok"`, `results[2].status == "error"` with `error` text,
   `results[3..4].status == "skipped"`, `stopped_at == 2`, `rolled_back` equals fact 2 (True when
   undo works headless and the first two commands' effects are reverted: assert Blender truth of
   command 1's `rna_set`; False with the headless note otherwise).
2. 201 commands: `err(call("batch", commands=[...201 get_version...]), "200")` and NOT ONE ran: a
   counter on `get_version` calls (wrap `srv` handler in the test, or a `rna_set` as command 1 whose
   effect must be absent).
3. `undo="NONE"` with a failing command: `rolled_back is False` and earlier effects persist.
4. `stop_on_error=False`: all 5 run, `stopped_at is None`, the error item is still `error`.
5. `$` substitution: `"$ob.uid"` resolved (D2); `"$nope.uid"` -> that command errors with
   `nope` in the text and the batch stops (stop_on_error default); `refs=False` -> the literal
   string `"$ob.uid"` reaches the handler and errors as a bad ref. A nested path `"$get.results[0].value.name"`
   resolves (json.path over lists).
6. Per-item `elapsed_ms` numeric; `type` of a non-driver handler (`get_version`) is accepted inside
   a batch; `err(call("batch", commands=[{"type":"no_such_handler"}]))` names the handler.
7. Wall cap: C.1 must expose the cap as a module constant the harness can lower
   (`addon._DRV_BATCH_WALL_S`); with it set to 0.5 a batch of `execute_code` sleeps 0.3 s x 3 stops
   after the second with `stopped_at == 1` and the reply text names the wall cap. Without such a
   constant the cap is untestable headless and is reported as NOT VERIFIED (section 5, I5).

### D9 reply cap
1. `r = ok(call("rna_get", refs="SCENE:Scene", fields="all", depth=2, max_chars=2000))`:
   `len(json.dumps(r["results"])) <= 2000`, `r["truncated"]` is a non-empty list naming dropped
   fields (strings), `r["chars"] <= 2000`; the reply text tells which filter narrows it (`fields`,
   `depth` or `max_chars` named in a `hint`/`note` key per C.1).
2. Without `max_chars` the cap is the server setting (24000): the server wrapper sends
   `max_chars = settings.driver_reply_max_chars` on every reading call (seam I3); add-on side with
   no `max_chars` key the default 24000 applies (assert `chars <= 24000` on a `depth=3` scene read).
3. `max_chars=50`: still a valid reply (envelope intact, `results` may be `[]`, `truncated` non-empty).
4. `scene_snapshot(max_chars=500, return_tree=True)` and `scene_diff(max_chars=200)` obey the same
   cap (`chars <= cap`, `truncated` non-empty).

### D10 events (Tier 2, conditional on gate C-G3)
Runs only when `drv_sub` is in `HANDLERS`; otherwise the step reports `SKIP tier2` and is not a
failure. When it runs: `ok(call("events_subscribe", kinds="depsgraph,frame"))` gives `subscription`
and `cursor`; move the object via `rna_set`, `bpy.context.view_layer.update()`, `scene.frame_set(5)`;
`ok(call("events_poll", cursor=cursor))`: one `depsgraph` event whose `data` list contains
`{uid: <uid>, is_updated_transform: True}` and one `frame` event with `data.frame_current == 5`;
`dropped == 0`; the new `cursor` advanced. `events_unsubscribe()` then `events_poll(cursor)`:
`events == []`, `dropped == 0`. Buffer: `buffer=3`, 5 frame changes, poll: `len(events) == 3`,
`dropped == 2`. Timer caveat (C19): the add-on never blocks; `wait_ms` is a server-side loop and is
unit-tested with a FakeConn that returns empty twice then one event (test_drv_server).

### D11 data_remove
1. `ok(call("data_remove", refs="#<uid>", purge_orphans=True))`: `results[0] == {ref: "#<uid>",
   removed: True, users_before: 1}`; `bpy.data.objects.get("DrvTwice") is None`,
   `bpy.data.meshes.get("DrvMesh") is None` (orphan purged).
2. `err(call("rna_get", refs="#<uid>"))` carries `tried` (uid index invalidated, no stale hit).
3. A mesh with users and `do_unlink=False`: `results[0].removed is False`, `users_before >= 1`,
   error names the user count; the mesh still exists.
4. `err(call("data_remove", refs="SCENE:Scene"))` or a success that leaves `bpy.data.scenes`
   unchanged: either way `after["scenes"] == before["scenes"]` (step invariant).

### D12 regression gate (every existing step + settings bytes)
1. Full harness `-- --b0 --b1 --drv` on both installs: every Phase A, B0, B1 step name present in
   `report_c0_<ver>.json` is present with `status == "PASS"` in `report_c_<ver>.json`; `failed == 0`;
   `steps == 186 + <drv steps>`; M2 coverage: every `drv_*` handler key appears in `CALLED`
   (add `DRV_TABLE` to `s21_coverage`, gated on `--drv`).
2. R15 (Lead, 2026-10-16): `settings_baseline.py -- --check` on both installs is THE C-G2 command:
   `match: true`, `differing: []`, after the section-3 re-implementation of `describe_settings` /
   `get_settings` / `set_settings` on the driver. Fixtures `tests/baselines/settings_replies_<ver>.json`
   are frozen at addon `0eb7ec4c`; nobody regenerates them without the Lead's word. Any DIFF line
   is a finding against addon.py, never a fixture update.
3. Uids in friendly views (section 3): `get_scene_info` objects carry `uid`; `get_object_info`
   carries `uid`; `find_objects_by_type` entries carry `uid`; `get_armature_info` carries `uid`;
   all equal to `session_uid`. Existing keys unchanged (the b0/b1 steps already assert them).
4. `execute_code` with `result_expr="bpy.context.scene.frame_current"`: reply carries `result == <int>`
   and `stdout`; with code `raise ValueError("x")`: `error` contains `ValueError: x`, `traceback` is a
   list of <= 3 frames, `line` is an int; the old `stdout`-only call shape still works unchanged.
5. `add_modifier` with nested props `{"domain_settings.resolution_max": 48}` on a FLUID modifier
   (or `{"strength": 2}` flat on DISPLACE as the control): `set` lists the dotted key and Blender
   truth matches; `unset` shape unchanged.
6. `get_version().registries` lists `blendermcp_settings_snapshots`, `blendermcp_reference_images`
   (names per the existing driver_namespace keys) and the driver's, each with an int count; every
   other `get_version` key from 2.2.0 unchanged (C2/C22 seam authority).

### D13 venv and tools/list
1. `set BLENDER_MCP_EXPECTED_VERSION=2.3.0` + `.venv\Scripts\python.exe -m pytest tests -q`:
   `0 failed`, count = 69 + `tests/test_drv_server.py` tests (number stated at landing).
   `tests/test_version_sync.py` green at 2.3.0 (three sources).
2. `tests/dump_tools_list.py names` = `tests/baseline_tools_2.3.txt` exactly (written from the real
   list at C-S0); count = 169 + N where N = the tool count C.1 ratifies (11 for Tier 1 only, 16 with
   Tier 2; the doc's `169 + 16` assumes Tier 2, see I4). Every name in `baseline_tools_2.2.txt` is
   present with an identical `inputSchema` in `baselines/tools_list_2.2.json` (superset, no retype).
   `test_f6_connection.py:190` repointed at the 2.3 baseline by QA at C-S0.
3. `grep -c "bpy.app.version" addon.py` compares = 0 (M7).

## 2. Seam checks (server.py <-> addon.py, `tests/test_drv_server.py`, FakeConn)

For each of the driver tools the test asserts the exact `(command_type, params)` the wrapper puts
on the wire, because the add-on steps above only prove the handler and the wrapper is where
"works in the harness, fails from Claude" lives.

| # | Check | PASS condition |
|---|---|---|
| S1 | handler keys | `describe -> drv_describe`, `rna_get -> drv_get`, `rna_set -> drv_set`, `call_operator -> drv_op`, `call_method -> drv_call`, `data_new -> drv_new`, `data_remove -> drv_remove`, `scene_snapshot -> drv_snapshot`, `scene_diff -> drv_diff`, `batch -> drv_batch`, `transaction -> drv_transaction` (`fake.calls[-1][0]`) |
| S2 | JSON param styles | `refs="a,b"`, `refs='["a","b"]'`, `sets='[["r",1]]'`, `sets='{"r":1}'`, `commands='[...]'`, `params='{}'`, `context='{}'` each arrive on the wire as the ONE canonical shape C.1 names (lists / dicts, never strings); malformed JSON returns the `_parse_json_object` "Error:" text WITHOUT a wire call (`fake.calls` unchanged) |
| S3 | max_chars | every reading wrapper (`describe`, `rna_get`, `scene_snapshot`, `scene_diff`, `batch`) sends `max_chars == settings.driver_reply_max_chars` when the caller omits it; `BLENDER_DRIVER_REPLY_MAX=100` env makes it 100; `driver_batch_max` likewise (`BLENDER_DRIVER_BATCH_MAX`); both keys appear in `get_server_settings` with `[default]`/`[env]` sources; `set_server_settings({"driver_reply_max_chars": "x"})` is rejected |
| S4 | `$id.path` | substitution happens add-on side (one round trip); the wrapper passes `refs=True` through untouched and does NOT try to resolve `$` strings (a `"$x.y"` literal reaches `fake.calls`) |
| S5 | reply rendering | `batch` reply pretty-printed and capped at `driver_reply_max_chars` with a trailing "truncated" note; `rna_get` reply with `truncated` non-empty renders the hint; an add-on `{"error": ...}` becomes the `Error: ...` text the other 169 tools use |
| S6 | `events_poll(wait_ms)` (Tier 2) | FakeConn returns `events: []` twice then one event: 3 wire calls, elapsed >= 2 x step; `wait_ms=0` -> exactly 1 call |
| S7 | schema freeze | `tools/list` for the 169 2.2.0 names byte-identical to `baselines/tools_list_2.2.json` (name, description, inputSchema); new tools have `angles` enum `RADIANS|DEGREES`, `exec_context` default `EXEC_DEFAULT`, `undo` default per C.1 |
| S8 | liveness | a wrapper whose add-on reply is a socket timeout returns the two-cause timeout text (existing `BlenderConnection` path); tested against the throwaway stub server in `test_f6_connection.py`, not a mock |

## 3. Adversarial extras (headless, folded into the D-steps above where marked)

- Malformed refs: `""`, `"#"`, `"#abc"`, `"OBJECT:"`, `"OBJECT:Cube/"`, `"/location"`, `"#1/..//x"`,
  `"context/"`, `"data/nope"`, `"prefs/nope"`, 10 kB ref string: each an error reply containing
  the ref, never a traceback, and `bpy.context.mode == "OBJECT"` after.
- Boundary values: `depth=-1`, `depth=99`, `max_chars=0`, `max_chars=-1`, `limit=0`, `buffer=0`,
  `fields=","`, `angles="GRADIANS"`: each a teaching error or a clamped value stated in the reply.
- Concurrency: one `batch` of 50 `rna_set` through `execute_command` interleaved with a direct
  `get_version` from a second thread blocked on the same `_lock` (the harness has no socket): the
  second call returns after the batch, never mid-batch (ordering asserted via a shared list).
- Path checks: `wm.save_mainfile` / `wm.open_mainfile` through `call_operator` accept only the same
  `filepath` rules `save_blend` enforces OR the reply names `save_blend`; a relative path does not
  escape WORK.
- Security posture (doc rule 7): nothing new is gated; the test only asserts the deny-list and that
  the bind stays loopback (`get_version().host == "127.0.0.1"` via the B0 step already in place).

## 4. Live steps (Blender 5.2.1 GUI, ONE batched request to the user at the end, R6)

1. `call_operator("view3d.view_all", context={"area":"VIEW_3D","region":"WINDOW"})` -> `["FINISHED"]`.
2. `transaction(BEGIN "live1")`, move the cube, `ROLLBACK` -> the cube is back; Edit > Undo History
   shows one `mcp:live1` entry.
3. Tier 2 only: `events_poll(wait_ms=2000)` returns a `frame` event while the user scrubs.
4. `batch` of 50 `rna_set` -> one reply, `elapsed_ms` total under 1000, the viewport stays
   responsive (user confirms no freeze beyond the batch).
Evidence: pasted tool replies plus one viewport screenshot per step, stored under `test_output/live_c/`.

## 5. Inconsistencies found while writing this plan (for the Lead, before C.1)

- I1. Doc section 7 steps 2, 5 and 10 use Tier 2 tools (`find`, `context_info`, `events_*`) but
  Tier 2 is conditional (R1). Section 1 gives Tier-1 substitutes; C.1 should say which assertion
  is binding for the Tier 1 gate.
- I2. `refs` / `sets` / `commands` accept "comma list or JSON list" (doc 2.2) but the wire shape is
  unspecified; C.1 must fix one canonical shape (list / dict) and put normalisation in the wrapper.
- I3. `driver_reply_max_chars` lives in server settings (doc 4) but the cap is applied in the add-on;
  the wrapper must send `max_chars` on every reading call, and the add-on needs its own default
  (24000) for callers that bypass the server. Both are seam checks S3 / D9.2.
- I4. Doc step 13 says `169 + 16`; Tier 1 alone is 11 tools (180). The number must trace to the
  ratified tool list.
- I5. The batch wall cap (60 s) and command cap (200) are untestable headless unless exposed as
  module constants the harness can lower (`addon._DRV_BATCH_WALL_S`, `addon._DRV_BATCH_MAX`) or the
  command cap is read from the params the server sends (`driver_batch_max`). Ask for the constant.
- I6. `scene_diff` for a rename: doc 2.2 says `renamed` entries; whether `name` also appears in
  `changed` is unspecified (D4.4 asserts exactly one of the two).
- I7. Fact 2 (undo headless) is unknown until `drvcheck.py` runs (never run: 0 DRVCHECK lines in
  `out_*.jsonl`); D7 / D8 carry both branches and cannot be finalised before Linus reports.
- I8. The "2.1.0 baseline" for byte-identical settings replies never existed on disk; the fixture
  captured today from addon `0eb7ec4c` (the implementation that shipped those replies in 2.1.0 and
  2.2.0) is the baseline of record: `tests/baselines/settings_replies_{4.3.2,5.2.1}.json`.
- I9. `get_version` gains `registries` (doc section 3); C2/C22 call `get_version` the seam
  authority. Checked: `test_b0_server.py` asserts substrings, not key sets, so the additive key
  breaks nothing there; the server's `get_version` text rendering must still show the new key or
  the agent cannot see it (seam S5 covers the rendering).

## 6. Reporting

Per step: `STEP n PASS|FAIL [DRV] <name> :: <detail>` lines from both installs pasted on card
`c-recon-qa` (recon) / the slice card (build); SUMMARY lines by DM to the Lead; findings as
finding / evidence / who it blocks. Results land in `Tests.md` (Test / Method / Input / Expected /
Actual / Result, baseline and after numbers) at sign-off. No sign-off while any D-step or seam
check fails on either install.

## 7. Rulings applied after section 5 (2026-10-16, Lead DMs m_6e46100d, m_9046c3f1, R29, R31)

- I1: `find` and `context_info` are IN (13 tools); DRV-2 uses `find`, DRV-5 uses `context_info`. Step 10 (events)
  is written as T4 and not wired into `--drv` until L4 is pulled in.
- I2: wire shape = JSON lists (`refs: list[str]`, `sets: list[[ref, value]]`, dict normalised to the list
  server-side, `commands: list[dict]`); seam S2 asserts it; the harness calls handlers with Python lists.
- I3 = G7; I4 = 182 tools (185 with L4); I5: `addon._DRV_BATCH_MAX`, `addon._DRV_BATCH_WALL_S`,
  `addon._DRV_REPLY_MAX_DEFAULT` are module constants read at call time (DRV-8 lowers the wall cap and
  restores it in `finally`; the 201 refusal is tested at the real value); I6: a rename appears ONLY in
  `renamed`; I7: undo works headless (R17), DRV-7/DRV-8 assert the real revert; I8/I9 agreed.
- R16: NO uid cache; DRV-4 asserts `driver_namespace["blendermcp_driver_uids"]` is absent and that the
  IDTYPE disambiguates a same-named object/mesh. R22: transaction keys are `undo_calls`, `marker_present`,
  `undo_available`, `open`; the Scene idprop `_mcp_txn` is absent after COMMIT and ROLLBACK. R23: `rna_get`
  depth=0 includes pointer props as `{uid,id_type,name}` / `{struct,path}` / null. R25: `execute_code`
  returns `{error, traceback, line}`; the Phase A step S19 asserts "division by zero" (holds for both).
  R26: static IDTYPE table, unknown IDTYPE -> `{error, valid}`. R27: the wall cap is checked between
  commands. R29: the 2.2 superset test allows additive params and description changes (only
  `execute_blender_code.result_expr` may be added). R31: a decoded JSON scalar is refused with
  "Error: <label> must be a JSON object or list" (F4).
- Contract shapes learnt from the first end-to-end run (both installs, addon cb7112e7): `context_info.scene`
  is an ID dict per 12.2.1; `data_remove` reports `success: false` when any item is refused;
  `find_objects_by_type` takes `obj_type`; `get_version.registries` lists only registries that exist.
- Open findings at the time of writing: F4 (JSON scalar echoed, server), F5 (data_new docstring, server),
  F6 (`describe("Object")` properties half null, add-on), F7 (dynamic-enum `valid` lists static items,
  add-on). Each has a red test that closes it.
