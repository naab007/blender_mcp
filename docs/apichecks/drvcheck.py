"""Facts for FEATURE-REQUEST-generic-driver.md (the CDP-style RNA/operator driver).

    "<blender>\\blender.exe" --background --factory-startup --python docs\\apichecks\\drvcheck.py 2>&1 | findstr DRVCHECK

One DRVCHECK {json} line. Real calls, never hasattr-only, on the facts the driver rests on:
stable ids and their lookup, RNA property/function/operator introspection shape, path
resolution and coercion, operator poll + context override, undo push/undo headless,
msgbus + app handlers firing headless, bpy.data collection enumeration, reply-size numbers.
"""
import bpy, json, time, sys

out = {"blender": "%d.%d.%d" % bpy.app.version, "python": sys.version.split()[0], "background": bpy.app.background}
ERR = []


def step(name, fn):
    try:
        out[name] = fn()
    except Exception as e:  # noqa
        out[name] = None
        ERR.append("%s: %s: %s" % (name, type(e).__name__, str(e)[:160]))


# ------------------------------------------------------------------ ids
def ids():
    cube = bpy.data.objects.get("Cube")
    ob = bpy.data.objects.new("drv_o", bpy.data.meshes.new("drv_m"))
    bpy.context.scene.collection.objects.link(ob)
    u = ob.session_uid
    ob.name = "drv_renamed"
    r = {"session_uid": u, "stable_after_rename": ob.session_uid == u, "int": isinstance(u, int)}
    cols = [p.identifier for p in bpy.data.bl_rna.properties if p.type == "COLLECTION"]
    r["data_collections"] = cols
    r["has_all_ids"] = "all_ids" in cols  # 5.x: union view of every ID, double-counts a scan
    cols = [c for c in cols if c != "all_ids"]
    t0 = time.perf_counter()
    found = None
    for c in cols:
        for idb in getattr(bpy.data, c):
            if getattr(idb, "session_uid", None) == u:
                found = (c, idb.name)
                break
        if found:
            break
    r["scan_lookup"] = found
    r["scan_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    all_uids = [i.session_uid for c in cols for i in getattr(bpy.data, c) if hasattr(i, "session_uid")]
    r["uids_total"] = len(all_uids)
    r["uids_unique"] = len(set(all_uids)) == len(all_uids)
    r["modifier_has_uid"] = hasattr(cube.modifiers.new("m", "SUBSURF"), "session_uid") if cube else None
    r["mesh_uid"] = hasattr(ob.data, "session_uid")
    r["as_pointer_int"] = isinstance(ob.as_pointer(), int)
    r["id_type"] = ob.id_type
    r["bl_rna_identifier"] = ob.bl_rna.identifier
    r["id_type_enum_sample"] = [i.identifier for i in bpy.types.ID.bl_rna.properties["id_type"].enum_items][:8]
    # is the uid reused after remove + new?
    old = ob.session_uid
    bpy.data.objects.remove(ob)
    ob2 = bpy.data.objects.new("drv_o2", None)
    r["uid_reused_after_remove"] = ob2.session_uid == old
    r["uid_monotonic"] = ob2.session_uid > old
    bpy.data.objects.remove(ob2)
    # scan cost at scale: 2000 empties, look up the LAST one (worst case) by full scan
    objs = [bpy.data.objects.new("drv_bulk_%d" % i, None) for i in range(2000)]
    target = objs[-1].session_uid
    t0 = time.perf_counter()
    hit = None
    for c in cols:
        for idb in getattr(bpy.data, c):
            if getattr(idb, "session_uid", None) == target:
                hit = idb.name
                break
        if hit:
            break
    r["scan_2000_worst_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    t0 = time.perf_counter()
    index = {i.session_uid: (c, i.name) for c in cols for i in getattr(bpy.data, c) if hasattr(i, "session_uid")}
    r["index_build_2000_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    r["index_hit"] = index.get(target, (None, None))[1] == hit
    for o in objs:
        bpy.data.objects.remove(o)
    return r


step("ids", ids)


# ------------------------------------------------------------------ rna introspection
def rna_props():
    props = bpy.types.Object.bl_rna.properties

    def describe(p):
        d = {"identifier": p.identifier, "type": p.type, "subtype": p.subtype, "readonly": p.is_readonly,
             "description": p.description[:40], "is_runtime": p.is_runtime, "is_hidden": p.is_hidden,
             "is_animatable": p.is_animatable, "unit": getattr(p, "unit", None)}
        if p.type in ("INT", "FLOAT", "BOOLEAN"):
            d["array_length"] = p.array_length
            if p.type != "BOOLEAN":
                d["hard_min"] = p.hard_min
                d["hard_max"] = p.hard_max
                d["soft_min"] = p.soft_min
                d["soft_max"] = p.soft_max
            d["default"] = list(p.default_array) if p.array_length else p.default
        if p.type == "ENUM":
            d["enum_items"] = [(i.identifier, i.name, i.description[:20]) for i in p.enum_items][:4]
            d["is_enum_flag"] = p.is_enum_flag
            d["default"] = p.default
        if p.type == "POINTER":
            d["fixed_type"] = p.fixed_type.identifier
        if p.type == "COLLECTION":
            d["fixed_type"] = p.fixed_type.identifier
            d["srna"] = p.srna.identifier if p.srna else None
        if p.type == "STRING":
            d["length_max"] = p.length_max
            d["default"] = p.default
        return d

    r = {"count": len(props), "location": describe(props["location"]), "type": describe(props["type"]),
         "parent": describe(props["parent"]), "modifiers": describe(props["modifiers"]), "name": describe(props["name"])}
    eng = bpy.context.scene.render.bl_rna.properties["engine"]
    r["engine_static_items"] = [i.identifier for i in eng.enum_items]
    r["Object_base"] = bpy.types.Object.bl_rna.base.identifier if bpy.types.Object.bl_rna.base else None
    r["bpy_types_dir_len"] = len(dir(bpy.types))
    f = bpy.types.Object.bl_rna.functions["evaluated_get"]
    r["func_evaluated_get"] = {"desc": f.description[:40],
                               "params": [(p.identifier, p.type, p.is_output, p.is_required) for p in f.parameters],
                               "use_self": f.use_self}
    f2 = bpy.types.Mesh.bl_rna.functions["calc_tangents"]
    r["func_calc_tangents_params"] = [(p.identifier, p.type, p.is_output) for p in f2.parameters]
    ob = bpy.data.objects["Cube"]
    dg = bpy.context.evaluated_depsgraph_get()
    r["getattr_call_evaluated_get"] = getattr(ob, "evaluated_get")(dg).name
    r["modifiers_new_params"] = [(p.identifier, p.type) for p in bpy.types.ObjectModifiers.bl_rna.functions["new"].parameters]
    # pointer prop with fixed_type lets the driver resolve names -> IDs: which bpy.data collection?
    fx = props["parent"].fixed_type
    r["parent_fixed_type_is_ID"] = fx.identifier
    # struct lookup by identifier for describe(type=...)
    r["types_lookup"] = getattr(bpy.types, "BooleanModifier").bl_rna.properties["solver"].enum_items.keys()
    r["types_lookup_bad"] = hasattr(bpy.types, "NoSuchStruct")
    # subclasses of Modifier are registered lazily? count now
    r["modifier_subclasses_now"] = len(bpy.types.Modifier.__subclasses__())
    r["modifier_type_enum_count"] = len(bpy.types.ObjectModifiers.bl_rna.functions["new"].parameters["type"].enum_items)
    return r


step("rna_props", rna_props)


def rna_ops():
    op = bpy.ops.mesh.bevel
    rt = op.get_rna_type()
    r = {"identifier": rt.identifier, "name": rt.name, "description": rt.description[:40],
         "props": [(p.identifier, p.type, p.is_readonly) for p in rt.properties][:6],
         "offset_soft": (rt.properties["offset"].soft_min, rt.properties["offset"].soft_max)}
    r["idname_py"] = op.idname_py() if hasattr(op, "idname_py") else None
    r["idname"] = op.idname()
    r["poll_edit_op_in_object_mode"] = op.poll()
    r["poll_object_op"] = bpy.ops.object.select_all.poll()
    r["modules"] = len([m for m in dir(bpy.ops) if not m.startswith("_")])
    r["mesh_ops"] = len(dir(bpy.ops.mesh))
    t0 = time.perf_counter()
    n = 0
    for m in dir(bpy.ops):
        if m.startswith("_"):
            continue
        mod = getattr(bpy.ops, m)
        for o in dir(mod):
            try:
                getattr(mod, o).get_rna_type()
                n += 1
            except Exception:
                pass
    r["all_ops"] = n
    r["enumerate_ms"] = round((time.perf_counter() - t0) * 1000)
    try:
        r["bl_options"] = sorted(getattr(bpy.types, rt.identifier).bl_options) if hasattr(bpy.types, rt.identifier) else "no bpy.types class"
    except Exception as e:
        r["bl_options"] = "err %s" % e
    cube = bpy.data.objects["Cube"]
    with bpy.context.temp_override(object=cube, active_object=cube, selected_objects=[cube], selected_editable_objects=[cube]):
        res = bpy.ops.object.shade_smooth()
    r["op_return"] = sorted(res)
    try:
        res2 = bpy.ops.object.select_all("EXEC_DEFAULT", action="DESELECT")
        r["exec_context_ok"] = sorted(res2)
    except Exception as e:
        r["exec_context_ok"] = "err %s" % str(e)[:80]
    try:
        bpy.ops.object.select_all(bogus=1)
    except Exception as e:
        r["bad_kwarg_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    try:
        bpy.ops.mesh.bevel(offset=0.1)
    except Exception as e:
        r["wrong_context_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    try:
        bpy.ops.object.select_all(action="NOPE")
    except Exception as e:
        r["bad_enum_kwarg_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    ok = {}
    for k, v in (("area", None), ("region", None), ("window", None), ("screen", None), ("object", cube),
                 ("active_object", cube), ("selected_objects", [cube]), ("edit_object", None), ("mode", "OBJECT"),
                 ("scene", bpy.context.scene), ("view_layer", bpy.context.view_layer), ("bogus_key", 1)):
        try:
            with bpy.context.temp_override(**{k: v}):
                pass
            ok[k] = True
        except Exception as e:
            ok[k] = str(e)[:60]
    r["temp_override_keys"] = ok
    with bpy.context.temp_override(object=cube, active_object=cube, selected_objects=[cube]):
        bpy.ops.object.mode_set(mode="EDIT")
        r["poll_bevel_in_edit"] = bpy.ops.mesh.bevel.poll()
        bpy.ops.object.mode_set(mode="OBJECT")
    # operator reports capture: WindowManager has no report list headless; test print capture of a warning op
    return r


step("rna_ops", rna_ops)


# ------------------------------------------------------------------ path get/set/coerce
def paths():
    cube = bpy.data.objects["Cube"]
    sc = bpy.context.scene
    r = {}
    r["resolve_vector"] = type(cube.path_resolve("location")).__name__
    r["resolve_nested"] = sc.path_resolve("render.resolution_x")
    r["resolve_collection_item"] = cube.path_resolve('modifiers["m"].levels') if cube.modifiers.get("m") else "no mod"
    r["resolve_index"] = cube.path_resolve("location[2]")
    r["resolve_coerce_false"] = type(sc.path_resolve("render", False)).__name__
    try:
        cube.path_resolve("nope.nada")
    except Exception as e:
        r["resolve_bad_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
    parent = sc.path_resolve("render", False)
    setattr(parent, "resolution_x", 640)
    r["set_via_parent"] = sc.render.resolution_x
    cube.location = [1, 2, 3]
    r["set_list_to_vector"] = list(cube.location)
    cube.location[0] = 5
    r["set_index"] = cube.location[0]
    try:
        sc.render.engine = "NOPE"
    except Exception as e:
        r["bad_enum_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    try:
        cube.location = "abc"
    except Exception as e:
        r["bad_type_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
    try:
        cube.type = "MESH"
    except Exception as e:
        r["readonly_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
    mat = bpy.data.materials.new("drv_mat")
    cube.data.materials.append(mat)
    cube.active_material = bpy.data.materials["drv_mat"]
    r["pointer_set"] = cube.active_material.name
    r["path_from_id"] = sc.render.path_from_id("resolution_x")
    r["path_from_id_struct"] = sc.render.path_from_id()
    r["id_data"] = sc.render.id_data.name
    r["modifiers_get_none"] = cube.modifiers.get("zzz")
    big = {p.identifier: str(getattr(cube, p.identifier, None))[:40] for p in cube.bl_rna.properties if not p.type == "COLLECTION"}
    r["object_flat_dump_chars"] = len(json.dumps(big))
    r["matrix_rows"] = [list(v) for v in cube.matrix_world][:1]
    cube["drv_custom"] = 3
    r["idprop_path"] = cube.path_resolve('["drv_custom"]')
    r["idprop_keys"] = list(cube.keys())
    # int -> float coercion, bool from int
    sc.render.resolution_percentage = 50
    cube.hide_render = 1
    r["bool_from_int"] = cube.hide_render
    # setting a float prop with int
    cube.scale = (2, 2, 2)
    r["float_from_int_tuple"] = list(cube.scale)
    return r


step("paths", paths)


# ------------------------------------------------------------------ undo headless
def undo():
    r = {}
    r["use_global_undo"] = bpy.context.preferences.edit.use_global_undo
    r["undo_steps"] = bpy.context.preferences.edit.undo_steps
    cube = bpy.data.objects["Cube"]
    before = tuple(cube.location)
    uid_before = cube.session_uid
    res = bpy.ops.ed.undo_push(message="drv test")
    r["undo_push"] = sorted(res)
    cube.location.x += 10
    res = bpy.ops.ed.undo_push(message="drv moved")
    r["undo_push2"] = sorted(res)
    r["undo_poll"] = bpy.ops.ed.undo.poll()
    try:
        res = bpy.ops.ed.undo()
        r["undo"] = sorted(res)
    except Exception as e:
        r["undo"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    cube = bpy.data.objects.get("Cube")
    r["location_restored"] = (tuple(cube.location) == before) if cube else "cube gone"
    r["uid_survives_undo"] = (cube.session_uid == uid_before) if cube else "cube gone"
    r["undo_history_poll"] = bpy.ops.ed.undo_history.poll()
    r["redo_poll"] = bpy.ops.ed.redo.poll()
    return r


step("undo", undo)


# ------------------------------------------------------------------ handlers + msgbus headless
def events():
    r = {}
    fired = []

    def on_dg(scene, depsgraph):
        fired.append(("depsgraph", [(u.id.name, u.is_updated_transform, u.is_updated_geometry) for u in depsgraph.updates][:3]))

    bpy.app.handlers.depsgraph_update_post.append(on_dg)
    cube = bpy.data.objects["Cube"]
    cube.location.y += 1
    bpy.context.view_layer.update()
    r["depsgraph_after_view_layer_update"] = [f for f in fired if f[0] == "depsgraph"][:1]
    fired.clear()
    cube.location.y += 1
    bpy.context.evaluated_depsgraph_get()
    r["depsgraph_after_evaluated_get"] = len(fired)
    bpy.app.handlers.depsgraph_update_post.remove(on_dg)
    r["Depsgraph_updates_attr"] = hasattr(bpy.types.Depsgraph, "updates")
    r["DepsgraphUpdate_props"] = [p.identifier for p in bpy.types.DepsgraphUpdate.bl_rna.properties]
    fc = []
    h = lambda scene, *a: fc.append(scene.frame_current)  # noqa
    bpy.app.handlers.frame_change_post.append(h)
    bpy.context.scene.frame_set(7)
    bpy.app.handlers.frame_change_post.remove(h)
    r["frame_change_post_fired"] = fc
    mb = []
    owner = object()
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "location"), owner=owner, args=("loc",), notify=lambda *a: mb.append(a))
    cube.location.z += 1
    r["msgbus_after_py_set"] = len(mb)
    bpy.msgbus.publish_rna(key=(bpy.types.Object, "location"))
    r["msgbus_after_publish"] = len(mb)
    bpy.context.view_layer.update()
    r["msgbus_after_vl_update"] = len(mb)
    bpy.msgbus.clear_by_owner(owner)
    r["handlers"] = sorted(h for h in dir(bpy.app.handlers) if not h.startswith("_") and h not in ("persistent",)
                           and isinstance(getattr(bpy.app.handlers, h), list))
    r["persistent_available"] = hasattr(bpy.app.handlers, "persistent")
    t = []
    bpy.app.timers.register(lambda: t.append(1) or None, first_interval=0.0)
    time.sleep(0.05)
    r["timer_fired_during_sleep"] = len(t)
    return r


step("events", events)


# ------------------------------------------------------------------ data lifecycle
def data():
    r = {}
    cols = [p.identifier for p in bpy.data.bl_rna.properties if p.type == "COLLECTION"]
    sigs = {}
    no_new = []
    for c in cols:
        srna = bpy.data.bl_rna.properties[c].srna
        if srna and "new" in srna.functions:
            sigs[c] = [(p.identifier, p.type) for p in srna.functions["new"].parameters]
        else:
            no_new.append(c)
    r["new_sigs_sample"] = {k: sigs[k] for k in ("objects", "meshes", "materials", "lights", "cameras", "collections", "armatures", "images", "node_groups", "actions", "curves") if k in sigs}
    r["new_sigs_count"] = len(sigs)
    r["no_new"] = no_new
    r["remove_sig"] = [(p.identifier, p.type) for p in bpy.types.BlendDataObjects.bl_rna.functions["remove"].parameters]
    m = bpy.data.meshes.new("drv_from_py")
    m.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    r["mesh_from_pydata"] = (m.validate(), len(m.polygons))
    r["users_and_orphan"] = (m.users, m.use_fake_user)
    bpy.data.meshes.remove(m)
    r["removed"] = bpy.data.meshes.get("drv_from_py") is None
    r["orphans_purge_sig"] = [(p.identifier, p.type) for p in bpy.ops.outliner.orphans_purge.get_rna_type().properties][1:]
    r["user_map"] = type(bpy.data.user_map(subset=[bpy.data.objects["Cube"]])).__name__
    r["batch_remove"] = hasattr(bpy.data, "batch_remove")
    return r


step("data", data)


# ------------------------------------------------------------------ context introspection
def ctx():
    r = {}
    r["mode"] = bpy.context.mode
    r["window_none_headless"] = bpy.context.window is None
    r["screen_none_headless"] = bpy.context.screen is None
    r["window_manager_windows"] = len(bpy.context.window_manager.windows)
    r["areas_headless"] = sum(len(w.screen.areas) for w in bpy.context.window_manager.windows)
    r["copy_keys_sample"] = sorted(bpy.context.copy().keys())[:10]
    return r


step("ctx", ctx)


# ------------------------------------------------------------------ id_type -> bpy.data collection map (PLAN 12.1 G5)
def idtype_map():
    enum_ids = [i.identifier for i in bpy.types.ID.bl_rna.properties["id_type"].enum_items]
    cols = {}
    for p in bpy.data.bl_rna.properties:
        if p.type == "COLLECTION" and p.fixed_type is not None:
            cols.setdefault(p.fixed_type.identifier.upper().replace("_", ""), []).append(p.identifier)
    mapping, unmapped, ambiguous = {}, [], []
    for e in enum_ids:
        hits = cols.get(e.replace("_", ""), [])
        if len(hits) == 1:
            mapping[e] = hits[0]
        elif not hits:
            unmapped.append(e)
        else:
            ambiguous.append((e, hits))
    return {"enum_count": len(enum_ids), "mapped": len(mapping), "unmapped": unmapped, "ambiguous": ambiguous,
            "sample": {k: mapping[k] for k in ("OBJECT", "MESH", "MATERIAL", "NODETREE", "ACTION", "SCENE") if k in mapping},
            "unit_attr_on_rotation": getattr(bpy.types.Object.bl_rna.properties["rotation_euler"], "unit", None)}


step("idtype_map", idtype_map)

out["errors"] = ERR
print("DRVCHECK", json.dumps(out, default=str))
