# drv_oracle: the exact handler calls and expected replies for the generic driver (PLAN.md 12.7)

Owner: Barbara (Planner). Written 2026-10-16 from PLAN.md section 12 at D1.2; revised for D1.4 (rulings O1-O4,
R32b, R33, R35, R37; see the O-notes inline). QA (Margaret) turns
these into `tests/drv_steps.py`; Linus runs them before posting a landing. Both start from the same
oracle, so a disagreement is a contract question for the Planner, not a negotiation between coders.
Everything below uses the harness helpers (`call`, `ok`, `err`, `make_cube`, `clear_scene`, `step`)
of `tests/headless_handlers.py`; `call(cmd, **params)` goes through `srv.execute_command`, so a
handler reply is `{"status": "success", "result": {...}}` and `ok(res)` returns the inner dict.
"error status" below = `ok()` fails because the result carries an `error` key. ASCII only.

Conventions the oracle relies on (PLAN 12.2):
- A batch item is `status: "error"` when its result dict has an `error` key OR `success: false`.
- Angles are radians unless `angles="DEGREES"` (unit ROTATION props only).
- `max_chars` is sent by the server; the harness passes it explicitly where the step needs it.
- Fixtures are built with bpy primitives (`make_cube`, `bpy.data.*.new`), never with the handler
  under test. `uid` readback for a fixture = `obj.session_uid`.

## Step 1: describe (L1)

```python
r = ok(call("drv_describe", what="Object"))                # O1 / R32b: the default cap (24000) ALWAYS truncates Object
assert r["kind"] == "struct" and r["identifier"] == "Object" and r["base"] == "ID"
assert r["total"] >= 130                                   # declared: 133 on 4.3.2, 141 on 5.2.1
assert r["count"] == len(r["properties"]) and r["count"] < r["total"]          # count = kept entries, never half-null
assert r["truncated"] and "description" in str(r["truncated"][0]).lower()      # descriptions dropped FIRST
assert all(v is not None for v in r["properties"].values())
r = ok(call("drv_describe", what="Object", max_chars=60000))                   # the full shape
assert r["count"] == r["total"] and r["truncated"] == []
loc = r["properties"]["location"]
assert loc["type"] == "FLOAT" and loc["unit"] == "LENGTH" and loc["array_length"] == 3 and loc["readonly"] is False
typ = r["properties"]["type"]
assert typ["type"] == "ENUM" and typ["readonly"] is True and ["MESH", "Mesh", ""] in typ["enum_items"]
assert r["properties"]["parent"]["type"] == "POINTER" and r["properties"]["parent"]["fixed_type"] == "Object"
assert r["properties"]["modifiers"]["type"] == "COLLECTION"
assert "functions" not in r                                # include_functions defaults to False
assert r["chars"] == len(json.dumps(r))                    # R34: chars counts the final JSON incl. truncated + chars

r = ok(call("drv_describe", what="Object", include_functions=True, fields="location,name"))
assert set(r["properties"]) == {"location", "name"}
assert "evaluated_get" in r["functions"]
assert r["functions"]["evaluated_get"]["params"][0][:2] == ["depsgraph", "POINTER"]

r = ok(call("drv_describe", what="mesh.bevel"))
assert r["kind"] == "operator" and r["idname"] == "mesh.bevel" and r["refused"] is False
assert r["properties"]["offset"]["type"] == "FLOAT" and r["properties"]["offset"]["soft_max"] == 100.0
assert r["poll_now"] is False                              # object mode
assert "bl_options" not in r                               # R19

r = ok(call("drv_describe", what="SCENE:Scene/render.engine"))      # a property ref
assert r["kind"] == "property" and r["type"] == "ENUM" and r["dynamic"] is True

r = ok(call("drv_describe", what="ops", search="bevel"))
assert r["kind"] == "ops" and any(row[0] == "mesh.bevel" for row in r["operators"])

msg = err(call("drv_describe", what="NoSuchStruct"))
res = call("drv_describe", what="Objekt")
assert "Object" in res["result"]["closest"]
msg = err(call("drv_describe", what="mesh.no_such_op"), contains="mesh.")
```

## Step 2: data_new x2 in one batch with `$` substitution, then find (L1 for drv_new/drv_find, L3 for drv_batch; until L3 lands run the two drv_new calls directly)

```python
r = ok(call("drv_batch", undo="NONE", commands=[
    {"id": "mesh", "type": "drv_new", "params": {"collection": "meshes", "args": ["DrvMesh"]}},
    {"id": "obj",  "type": "drv_new", "params": {"collection": "objects", "args": ["Drv", "$mesh.uid"]}},
]))
assert r["success"] is True and r["rolled_back"] is False and r["stopped_at"] is None
assert [x["status"] for x in r["results"]] == ["ok", "ok"]
mesh_uid = r["results"][0]["result"]["uid"]; obj_uid = r["results"][1]["result"]["uid"]
assert isinstance(obj_uid, int) and obj_uid != mesh_uid
ob = bpy.data.objects["Drv"]
assert ob.session_uid == obj_uid and ob.data.session_uid == mesh_uid
assert r["results"][1]["result"]["linked_to"] == bpy.context.scene.collection.name
assert r["results"][1]["result"]["id_type"] == "OBJECT" and r["results"][1]["result"]["name"] == "Drv"

r = ok(call("drv_find", name="Drv*"))
assert r["count"] == 1 and r["items"][0]["uid"] == obj_uid and r["items"][0]["name"] == "Drv"
r = ok(call("drv_find", id_type="MESH", name="DrvMesh"))
assert r["count"] == 1 and r["scope"] == "DATA" and r["items"][0]["uid"] == mesh_uid
r = ok(call("drv_find", where=[["type", "==", "MESH"], ["name", "contains", "Drv"]], fields="name,type,location"))
assert r["count"] == 1 and r["items"][0]["type"] == "MESH" and r["items"][0]["location"] == [0.0, 0.0, 0.0]
err(call("drv_find", where=[["type", "~", "MESH"]]), contains="valid")

msg = err(call("drv_new", collection="window_managers", args=["x"]))
assert "objects" in call("drv_new", collection="window_managers", args=["x"])["result"]["valid"]
```

## Step 3: rna_set / rna_get, coercion, angles, stop_on_error (L1)

```python
R = "#%d" % obj_uid
r = ok(call("drv_set", sets=[[R + "/location", [1, 2, 3]], [R + "/rotation_euler", [90, 0, 0]]], angles="DEGREES"))
assert r["applied"] == 2 and r["failed"] == 0 and r["stopped_at"] is None
assert r["results"][0]["set"] is True and r["results"][0]["value_after"] == [1.0, 2.0, 3.0]

r = ok(call("drv_get", refs=[R], fields="location,rotation_euler"))
v = r["results"][0]["value"]
assert v["location"] == [1.0, 2.0, 3.0] and abs(v["rotation_euler"][0] - 1.5708) < 1e-3     # radians by default
assert r["results"][0]["uid"] == obj_uid and r["errors"] == []
r = ok(call("drv_get", refs=[R + "/rotation_euler"], angles="DEGREES"))
assert abs(r["results"][0]["value"][0] - 90.0) < 1e-3                                       # no double conversion

r = ok(call("drv_get", refs=[R]))                       # default field set: scalars + pointers (R23), no collections
v = r["results"][0]["value"]
assert "location" in v and v["parent"] is None and "modifiers" not in v
assert v["data"] == {"uid": mesh_uid, "id_type": "MESH", "name": "DrvMesh"}

# dict form of sets is accepted too (server normalises, add-on tolerates)
r = ok(call("drv_set", sets={R + "/hide_render": 1}))
assert r["results"][0]["value_after"] is True

res = call("drv_set", sets=[[R + "/type", "EMPTY"], ["SCENE:Scene/render.engine", "NOPE"], [R + "/nope", 1], [R + "/scale", [2, 2, 2]]],
           stop_on_error=False)
r = res["result"]
assert r["success"] is False and r["applied"] == 1 and r["failed"] == 3 and r["stopped_at"] is None
assert r["results"][0]["set"] is False and "read-only" in r["results"][0]["rna_error"]          # R21 verbatim
assert "BLENDER_WORKBENCH" in r["results"][1]["valid"] and "CYCLES" in r["results"][1]["valid"]
assert r["results"][2]["closest"] and r["results"][2]["set"] is False
assert r["results"][3]["set"] is True and list(bpy.data.objects["Drv"].scale) == [2.0, 2.0, 2.0]

res = call("drv_set", sets=[[R + "/type", "EMPTY"], [R + "/scale", [3, 3, 3]]])                 # stop_on_error=True
assert res["result"]["stopped_at"] == 0 and res["result"]["applied"] == 0 and list(bpy.data.objects["Drv"].scale) == [2.0, 2.0, 2.0]

# pointer by ref + ID property path + side-effect setter
mat = bpy.data.materials.new("DrvMat"); bpy.data.objects["Drv"].data.materials.append(mat)
r = ok(call("drv_set", sets=[[R + "/active_material", "MATERIAL:DrvMat"], [R + '/["drv_custom"]', 7], ["SCENE:Scene/frame_current", 5]]))
assert r["applied"] == 3 and bpy.data.objects["Drv"].active_material is mat and bpy.data.objects["Drv"]["drv_custom"] == 7
assert bpy.context.scene.frame_current == 5
r = ok(call("drv_get", refs=[R], fields="custom"))
assert r["results"][0]["value"]["custom"]["drv_custom"] == 7
bpy.context.scene.frame_set(1)
```

## Step 4: rename, snapshot, diff (L1 for the rename part, L2 for snapshot/diff)

```python
ok(call("drv_set", sets=[[R + "/name", "DrvRenamed"]]))
assert ok(call("drv_find", name="Drv"))["count"] == 0                   # exact glob: old name gone
assert ok(call("drv_get", refs=[R], fields="name"))["results"][0]["value"]["name"] == "DrvRenamed"
err(call("drv_get", refs=["OBJECT:Drv"]))  # -> errors[] not an error status: check the list instead
res = call("drv_get", refs=["OBJECT:Drv"])
assert res["result"]["errors"][0]["tried"] and res["result"]["results"] == []

r = ok(call("drv_snapshot", name="a"))
assert r["name"] == "a" and r["scope"] == "SCENE" and str(obj_uid) in r["ids"] or obj_uid in r["ids"]
entry = r["ids"][str(obj_uid)] if str(obj_uid) in r["ids"] else r["ids"][obj_uid]
assert entry["name"] == "DrvRenamed" and entry["location"] == [1.0, 2.0, 3.0]
r2 = ok(call("drv_snapshot", name="b", return_tree=False))
assert set(r2) >= {"success", "name", "count", "chars"} and "ids" not in r2

ok(call("drv_set", sets=[[R + "/name", "DrvTwice"]]))
r = ok(call("drv_diff", a="a"))
assert r["b"] == "NOW" and r["added"] == [] and r["removed"] == []
assert r["renamed"] == [{"uid": obj_uid, "before": "DrvRenamed", "after": "DrvTwice"}]
assert "name" not in r["changed"].get(str(obj_uid), r["changed"].get(obj_uid, {}))      # G19: renamed only
make_cube("Extra")
r = ok(call("drv_diff", a="a"))
assert len(r["added"]) == 1 and r["added"][0]["name"] == "Extra"
err(call("drv_diff", a="nope"), contains="valid")
```

## Step 5: call_operator, context, poll, mode restore (L2)

```python
ctx = {"active": R, "selected": [R]}
r = ok(call("drv_op", op="object.modifier_add", params={"type": "BEVEL"}, context=ctx))
assert r["result"] == ["FINISHED"] and r["modal"] is False and r["elapsed_ms"] >= 0
assert r["context_applied"]["active"] == obj_uid and r["context_applied"]["selected"] == [obj_uid]
r = ok(call("drv_get", refs=[R], fields="modifiers[]", depth=1))
mods = r["results"][0]["value"]["modifiers"]
assert mods["count"] == 1 and mods["items"][0]["type"] == "BEVEL"

res = call("drv_op", op="mesh.bevel", params={"offset": 0.1}, context=ctx)            # no mode: poll fails
assert "poll_message" in res["result"] and "context is incorrect" in res["result"]["rna_error"]
assert res["result"]["context_applied"]["mode"] is None

r = ok(call("drv_op", op="mesh.bevel", params={"offset": 0.1}, context=dict(ctx, mode="EDIT")))
assert r["result"] == ["FINISHED"]
assert ok(call("drv_context"))["mode"] == "OBJECT"                                      # restored (keep=False)
assert bpy.data.objects["DrvTwice"].mode == "OBJECT"

res = call("drv_op", op="object.select_all", params={"bogus": 1})
assert "closest" in res["result"] and 'keyword "bogus" unrecognized' in res["result"]["rna_error"]
res = call("drv_op", op="mesh.bevel", params={"offsett": 0.1})                 # R37: params validated BEFORE poll
assert "offset" in res["result"]["closest"] and "poll_message" not in res["result"]   # even in OBJECT mode
res = call("drv_op", op="object.select_all", params={"action": "NOPE"})
assert "DESELECT" in res["result"]["valid"]
res = call("drv_op", op="wm.quit_blender")
assert res["result"]["refused"] is True

# 5b: INVOKE refused headless, before the call
t0 = time.time(); res = call("drv_op", op="transform.translate", exec_context="INVOKE_DEFAULT", context=ctx)
assert "EXEC_DEFAULT" in res["result"]["error"] and (time.time() - t0) < 0.05
# 5c: extra whitelist (R19)
loc_before = list(bpy.data.objects["DrvTwice"].location)
res = call("drv_op", op="object.modifier_add", params={"type": "SUBSURF"}, context=dict(ctx, extra={"edit_objekt": R}))
assert "edit_object" in res["result"]["valid"] and len(res["result"]["valid"]) == 16
assert len(bpy.data.objects["DrvTwice"].modifiers) == 1                                 # operator NOT called
# GUI-only (R20): keyed on bpy.app.background
res = call("drv_op", op="view3d.view_all", context={"area": "VIEW_3D", "region": "WINDOW"})
assert res["result"]["background"] is True and "GUI" in res["result"]["error"]
```

## Step 6: call_method (L2)

```python
r = ok(call("drv_call", ref=R, method="evaluated_get", depsgraph=True))
assert r["value"]["uid"] == obj_uid or isinstance(r["value"]["uid"], int)             # evaluated copy reports an ID
r = ok(call("drv_call", ref="MESH:DrvMesh", method="from_pydata", args=[[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [], [[0, 1, 2]]]))
assert ok(call("drv_get", refs=["MESH:DrvMesh"], fields="polygons"))["results"][0]["value"]["polygons"] == {"count": 1}
res = call("drv_call", ref=R, method="evaluated_gett")
assert "evaluated_get" in res["result"]["closest"]
r = ok(call("drv_call", ref="SCENE:Scene/render", method="path_from_id", args=["resolution_x"]))
assert r["value"] == "render.resolution_x"
```

## Step 7: transaction (L3; R22 marker semantics, R17 real rollback)

```python
pre = make_cube("PreTxn")                                   # exists BEFORE the transaction
before = list(bpy.data.objects["DrvTwice"].location)
r = ok(call("drv_transaction", action="BEGIN", name="t1"))
assert r["open"] is True and r["marker_present"] is True and r["undo_available"] is True and r["undo_calls"] == 0
assert "_mcp_txn" in bpy.context.scene                      # holds seq (an int), not the name (R22)
res = call("drv_transaction", action="BEGIN", name="t2")
assert "t1" in res["result"]["error"] and res["result"]["open"] is True
ok(call("drv_set", sets=[[R + "/location", [9, 9, 9]]]))
r = ok(call("drv_transaction", action="STATUS"))
assert r["open"] is True and r["name"] == "t1"
r = ok(call("drv_transaction", action="ROLLBACK"))
assert r["undo_calls"] >= 1 and r["marker_present"] is False and r["open"] is False
assert list(bpy.data.objects["DrvTwice"].location) == before                       # restored for real
assert bpy.data.objects.get("PreTxn") is not None                                  # not one step too far
assert "_mcp_txn" not in bpy.context.scene
r = ok(call("drv_transaction", action="BEGIN", name="t3")); r = ok(call("drv_transaction", action="COMMIT"))
assert r["open"] is False and "_mcp_txn" not in bpy.context.scene
res = call("drv_transaction", action="ROLLBACK")
assert "no transaction" in res["result"]["error"].lower()
```

## Step 8: batch limits and rollback (L3)

```python
before = list(bpy.data.objects["DrvTwice"].location)
cmds = [{"type": "drv_set", "params": {"sets": [[R + "/location", [1, 1, 1]]]}},
        {"type": "drv_set", "params": {"sets": [[R + "/location", [2, 2, 2]]]}},
        {"type": "drv_set", "params": {"sets": [[R + "/nope", 1]]}},                 # fails (success False)
        {"type": "drv_set", "params": {"sets": [[R + "/location", [3, 3, 3]]]}},
        {"type": "drv_get", "params": {"refs": [R]}}]
res = call("drv_batch", commands=cmds)                      # stop_on_error=True, undo="AUTO"
r = res["result"]
assert r["success"] is False and r["stopped_at"] == 2 and r["rolled_back"] is True and r["undo_available"] is True
assert [x["status"] for x in r["results"]] == ["ok", "ok", "error", "skipped", "skipped"]
assert list(bpy.data.objects["DrvTwice"].location) == before                       # effects of 1 and 2 undone
assert bpy.data.objects.get("PreTxn") is not None
assert "_mcp_batch" not in bpy.context.scene

r = ok(call("drv_batch", commands=cmds, stop_on_error=False, undo="NONE"))
assert r["success"] is False and r["stopped_at"] is None and r["rolled_back"] is False
assert [x["status"] for x in r["results"]] == ["ok", "ok", "error", "ok", "ok"]
assert list(bpy.data.objects["DrvTwice"].location) == [3.0, 3.0, 3.0]

res = call("drv_batch", commands=[cmds[0]] * 201)
assert "201" in res["result"]["error"] and list(bpy.data.objects["DrvTwice"].location) == [3.0, 3.0, 3.0]   # nothing ran
res = call("drv_batch", commands=[{"type": "drv_batch", "params": {"commands": []}}])
assert res["result"]["results"][0]["status"] == "error" and "nested" in res["result"]["results"][0]["error"]
# legacy key keeps its gate (G9)
res = call("drv_batch", commands=[{"type": "get_polyhaven_categories", "params": {"asset_type": "hdris"}}, cmds[0]], stop_on_error=False, undo="NONE")
assert res["result"]["results"][0]["status"] == "error" and res["result"]["results"][1]["status"] == "ok"
# `$` into a missing id is that command's error
res = call("drv_batch", commands=[{"id": "a", "type": "drv_get", "params": {"refs": [R]}},
                                  {"type": "drv_set", "params": {"sets": [[R + "/location", "$zzz.value"]]}}], undo="NONE")
assert res["result"]["results"][1]["status"] == "error" and "zzz" in res["result"]["results"][1]["error"]
# 8b: wall cap checked between commands (R27); a wall stop is an error (R39, F12)
saved = addon._DRV_BATCH_WALL_S
before = list(bpy.data.objects["DrvTwice"].location)
try:
    addon._DRV_BATCH_WALL_S = 0.0
    res = call("drv_batch", commands=cmds[:3])                  # undo="AUTO", stop_on_error=True
    r = res["result"]
    assert r["stopped_at"] == 1 and [x["status"] for x in r["results"]] == ["ok", "skipped", "skipped"]
    assert r["success"] is False and "wall cap" in r["error"] and r["rolled_back"] is True
    assert list(bpy.data.objects["DrvTwice"].location) == before            # the 1st command's effect undone
    res = call("drv_batch", commands=cmds[:3], undo="NONE")
    assert res["result"]["success"] is False and res["result"]["rolled_back"] is False and res["result"]["stopped_at"] == 1
    assert list(bpy.data.objects["DrvTwice"].location) == [1.0, 1.0, 1.0]
finally:
    addon._DRV_BATCH_WALL_S = saved
ok(call("drv_set", sets=[[R + "/location", [1, 2, 3]]]))
```

## Step 9: reply cap (L1)

```python
res = call("drv_get", refs=["SCENE:Scene"], fields="all", depth=2, max_chars=2000)
r = res["result"]
assert len(json.dumps(r)) <= 2000 + 400 and r["truncated"] and r["chars"] <= 2400
assert any("ToolSettings" in str(x) for x in r["truncated"])                   # O2 / R33: the unsafe-struct skip is named
r = ok(call("drv_get", refs=["SCENE:Scene"], fields="all", depth=3, max_chars=4000))   # DRV-9b: no crash on 4.3.2
r = ok(call("drv_get", refs=["SCENE:Scene"], fields="all", depth=2, max_chars=200000))
assert r["truncated"] == []
res = call("drv_get", refs=[R], max_chars=0)
assert "max_chars" in res["result"]["error"]                                        # boundary: teaching error
```

## Step 11: data_remove (L1)

```python
r = ok(call("drv_remove", refs=[R], purge_orphans=True))
assert r["results"][0]["removed"] is True and r["results"][0]["users_before"] >= 1 and r["purged"] >= 1
assert bpy.data.objects.get("DrvTwice") is None and bpy.data.meshes.get("DrvMesh") is None
res = call("drv_get", refs=[R])
assert res["result"]["errors"][0]["tried"] and res["result"]["results"] == []
m = bpy.data.meshes.new("Shared"); o1 = bpy.data.objects.new("S1", m); bpy.context.scene.collection.objects.link(o1)
res = call("drv_remove", refs=["MESH:Shared"], do_unlink=False)
assert res["result"]["results"][0]["removed"] is False and res["result"]["results"][0]["users_before"] == 1
```

## Steps 12-13: regression and venv (D-G3)

```text
Every pre-existing harness step PASS on both installs (186) with -- --b0 --b1 --drv.
<install>\blender.exe --background --factory-startup --python tests\settings_baseline.py -- --check  -> match:true (R15)
.venv\Scripts\python.exe -m pytest tests -q  -> green with the 2.3.0 default (no env var)
.venv\Scripts\python.exe tests\dump_tools_list.py names | count = 182 (185 with L4); diff vs tests\baselines\tools_list_2.2.json = only "+" lines
```

## G11 rides-along (L1)

```python
r = ok(call("get_scene_info"))
assert all(isinstance(o.get("uid"), int) for o in r["objects"])
r = ok(call("execute_code", code="x = 2 + 2", result_expr="x * 10"))
assert r["value"] == 40 and r["executed"] is True
res = call("execute_code", code="raise ValueError('boom')")
assert res["status"] == "success" and res["result"]["error"].startswith("ValueError") and len(res["result"]["traceback"]) <= 3   # R25: never raises
r = ok(call("get_version"))                                # O3: registries lists only the blendermcp_* registries that EXIST
assert isinstance(r["registries"], dict) and all(k.startswith("blendermcp_") for k in r["registries"])
ok(call("drv_snapshot", name="reg", return_tree=False))
assert ok(call("get_version"))["registries"]["blendermcp_driver_snapshots"] >= 1
```

## Malformed refs (every one an error reply, never a traceback, mode OBJECT after; R35: a trailing slash IS malformed, so `OBJECT:Cube/` and `context/` stay in this list)

```python
for bad in ["", "#", "#abc", "OBJECT:", "OBJECT:Cube/", "/location", "#1/..//x", "context/", "data/nope", "prefs/nope", "x" * 10000]:
    res = call("drv_get", refs=[bad])
    assert res["status"] == "success" and res["result"]["errors"] and res["result"]["errors"][0]["ref"] == bad
    assert bpy.context.mode == "OBJECT"
```
