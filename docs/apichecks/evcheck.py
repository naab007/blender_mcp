# D2-G0 facts for driver events (PLAN.md 13.6 items 1-7) with REAL calls on both installs, headless.
# Prints one line: EVCHECK {json}. ASCII only. Never imports addon.py.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\evcheck.py 2>&1 | findstr EVCHECK
import bpy
import collections
import json
import os
import sys
import tempfile
import traceback

from bpy.app.handlers import persistent

out = {"version": bpy.app.version_string, "python": sys.version.split()[0], "background": bpy.app.background}
TMP = tempfile.mkdtemp(prefix="evcheck_")


def tryf(key, fn):
    try:
        out[key] = fn()
    except Exception as e:
        out[key] = "Error: " + str(e).strip()[:200] + " / " + traceback.format_exc().strip().splitlines()[-1][:120]


def err(e):
    return "Error: " + str(e).strip()[:140]


NAMES = ["depsgraph_update_post", "frame_change_post", "render_pre", "render_post", "render_write", "render_complete",
         "render_cancel", "save_pre", "save_post", "load_post", "undo_post", "redo_post"]
FIRED = collections.Counter()
ERRORS = []
LAST_ARGS = {}
# handlers must be @persistent or Blender drops them on open_mainfile (the add-on's are persistent, H6)
_HANDLERS = {}


def _make(name):
    @persistent
    def _h(*args):
        try:
            FIRED[name] += 1
            LAST_ARGS[name] = [type(a).__name__ for a in args]
        except Exception as e:  # pragma: no cover
            ERRORS.append(name + ": " + str(e)[:80])
    _h.__name__ = "evcheck_" + name
    return _h


# --- 1a. which handler names exist; register one counter on each ------------------------------
def handlers_exist():
    res = {}
    for n in NAMES:
        res[n] = hasattr(bpy.app.handlers, n)
        if res[n]:
            fn = _make(n)
            _HANDLERS[n] = fn
            getattr(bpy.app.handlers, n).append(fn)
    res["_persistent_attr"] = all(getattr(f, "_bpy_persistent", None) is not None for f in _HANDLERS.values())
    return res


tryf("handlers_exist", handlers_exist)


# --- 3. depsgraph.updates ids: session_uid readable for every update type -----------------------
DG_SEEN = []
DG_ERR = []


@persistent
def _dg_probe(scene, depsgraph=None):
    try:
        dg = depsgraph if depsgraph is not None else bpy.context.evaluated_depsgraph_get()
        for u in dg.updates:
            rec = {"id_type": type(u.id).__name__}
            try:
                rec["uid"] = u.id.session_uid
            except Exception as e:
                rec["uid"] = err(e)
            try:
                # the update carries the EVALUATED id (uid 0); the real uid is on .original
                o = u.id.original
                rec["orig_uid"] = o.session_uid
                rec["orig_uid_matches_data"] = (getattr(bpy.data, {"Object": "objects", "Mesh": "meshes", "Scene": "scenes",
                                                                   "Collection": "collections"}.get(rec["id_type"], "objects")).get(o.name).session_uid == o.session_uid) if rec["id_type"] in ("Object", "Mesh", "Scene", "Collection") else None
            except Exception as e:
                rec["orig_uid"] = err(e)
            try:
                rec["name"] = u.id.name
                rec["is_evaluated"] = bool(getattr(u.id, "is_evaluated", False))
                rec["has_original"] = u.id.original is not None if hasattr(u.id, "original") else None
                rec["orig_same_uid"] = (u.id.original.session_uid == u.id.session_uid) if getattr(u.id, "original", None) is not None else None
                rec["t"] = u.is_updated_transform
                rec["g"] = u.is_updated_geometry
                rec["s"] = u.is_updated_shading
            except Exception as e:
                rec["fields"] = err(e)
            DG_SEEN.append(rec)
    except Exception as e:
        DG_ERR.append(err(e))


def depsgraph_uids():
    res = {"handler_args": None}
    bpy.app.handlers.depsgraph_update_post.append(_dg_probe)
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
    # transform
    DG_SEEN.clear()
    cube.location.x += 1.0
    bpy.context.view_layer.update()
    res["after_move"] = list(DG_SEEN)
    # rename
    DG_SEEN.clear()
    cube.name = "evcheck_cube"
    bpy.context.view_layer.update()
    res["after_rename"] = list(DG_SEEN)
    # link a new collection
    DG_SEEN.clear()
    coll = bpy.data.collections.new("evcheck_coll")
    bpy.context.scene.collection.children.link(coll)
    bpy.context.view_layer.update()
    res["after_collection_link"] = list(DG_SEEN)
    # geometry edit
    DG_SEEN.clear()
    cube.data.vertices[0].co.z += 0.1
    cube.data.update()
    bpy.context.view_layer.update()
    res["after_geometry"] = list(DG_SEEN)
    # scene property
    DG_SEEN.clear()
    bpy.context.scene.frame_end += 1
    bpy.context.view_layer.update()
    res["after_scene_prop"] = list(DG_SEEN)
    # remove an object: is the freed id in updates? handler must not crash
    DG_SEEN.clear()
    bpy.ops.mesh.primitive_cube_add()
    tmp = bpy.context.active_object
    bpy.context.view_layer.update()
    DG_SEEN.clear()
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.context.view_layer.update()
    res["after_remove"] = list(DG_SEEN)
    res["handler_args"] = LAST_ARGS.get("depsgraph_update_post")
    res["handler_errors"] = list(DG_ERR)
    res["no_update_without_change"] = None
    DG_SEEN.clear()
    before = FIRED["depsgraph_update_post"]
    bpy.context.view_layer.update()
    res["no_update_without_change"] = {"fired_delta": FIRED["depsgraph_update_post"] - before, "updates": len(DG_SEEN)}
    bpy.app.handlers.depsgraph_update_post.remove(_dg_probe)
    return res


tryf("depsgraph_uids", depsgraph_uids)


# --- 1b. frame_change_post on frame_set ---------------------------------------------------------
def frame_fires():
    before = FIRED["frame_change_post"]
    bpy.context.scene.frame_set(5)
    bpy.context.scene.frame_set(6)
    return {"fired": FIRED["frame_change_post"] - before, "args": LAST_ARGS.get("frame_change_post"),
            "frame_in_handler_is_new": True}


tryf("frame_fires", frame_fires)


# --- 1c + 4. render handlers on an 8x8 WORKBENCH still; frame_path() inside render_write ----------
RW = {}


@persistent
def _rw_probe(scene, *args):
    try:
        RW["frame_path_in_write"] = scene.render.frame_path()
        RW["filepath_in_write"] = scene.render.filepath
        RW["frame_current"] = scene.frame_current
    except Exception as e:
        RW["frame_path_in_write"] = err(e)


def render_fires():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = 8
    sc.render.resolution_y = 8
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = os.path.join(TMP, "still_")
    bpy.app.handlers.render_write.append(_rw_probe)
    before = {n: FIRED[n] for n in ("render_pre", "render_post", "render_write", "render_complete", "render_cancel")}
    try:
        r = bpy.ops.render.render(write_still=True)
    except Exception as e:
        r = err(e)
    bpy.app.handlers.render_write.remove(_rw_probe)
    fired = {n: FIRED[n] - before[n] for n in before}
    fp = RW.get("frame_path_in_write")
    still = {"result": sorted(r) if not isinstance(r, str) else r, "fired": fired,
             "frame_path_in_write": fp, "frame_path_exists": bool(fp) and os.path.exists(fp) if isinstance(fp, str) else None,
             "ensure_ext_filepath": bpy.path.ensure_ext(sc.render.filepath, sc.render.file_extension),
             "ensure_ext_exists": os.path.exists(bpy.path.ensure_ext(sc.render.filepath, sc.render.file_extension)),
             "files_in_tmp": sorted(os.listdir(TMP)), "render_write_args": LAST_ARGS.get("render_write")}
    # animation of one frame: here frame_path() is the written file
    RW.clear()
    sc.frame_start = sc.frame_end = 6
    sc.render.filepath = os.path.join(TMP, "anim_")
    bpy.app.handlers.render_write.append(_rw_probe)
    before = {n: FIRED[n] for n in ("render_pre", "render_post", "render_write", "render_complete")}
    try:
        r2 = bpy.ops.render.render(animation=True)
    except Exception as e:
        r2 = err(e)
    bpy.app.handlers.render_write.remove(_rw_probe)
    fp2 = RW.get("frame_path_in_write")
    anim = {"result": sorted(r2) if not isinstance(r2, str) else r2, "fired": {n: FIRED[n] - before[n] for n in before},
            "frame_path_in_write": fp2, "frame_path_exists": os.path.exists(fp2) if isinstance(fp2, str) else None,
            "files_in_tmp": sorted(os.listdir(TMP))}
    return {"still": still, "animation": anim}


tryf("render_fires", render_fires)


# --- 1d. undo_post / redo_post after ed.undo / ed.redo ---------------------------------------------
def undo_fires():
    res = {"undo_push_poll": None}
    cube = bpy.data.objects.get("evcheck_cube")
    bpy.ops.ed.undo_push(message="evcheck a")
    cube.location.y = 3.0
    bpy.ops.ed.undo_push(message="evcheck b")
    before_u, before_r = FIRED["undo_post"], FIRED["redo_post"]
    try:
        ru = bpy.ops.ed.undo()
        res["undo"] = {"result": sorted(ru), "y_after": round(bpy.data.objects["evcheck_cube"].location.y, 3)}
    except Exception as e:
        res["undo"] = err(e)
    try:
        rr = bpy.ops.ed.redo()
        res["redo"] = {"result": sorted(rr), "y_after": round(bpy.data.objects["evcheck_cube"].location.y, 3)}
    except Exception as e:
        res["redo"] = err(e)
    res["fired"] = {"undo_post": FIRED["undo_post"] - before_u, "redo_post": FIRED["redo_post"] - before_r}
    res["args"] = {"undo_post": LAST_ARGS.get("undo_post"), "redo_post": LAST_ARGS.get("redo_post")}
    return res


tryf("undo_fires", undo_fires)


# --- 5. msgbus subscribe + publish headless: delivers nothing? ----------------------------------------
MB = {"notified": 0}


def _mb_notify(*args):
    MB["notified"] += 1


def msgbus():
    owner = object()
    key = (bpy.types.Object, "location")
    bpy.msgbus.subscribe_rna(key=key, owner=owner, args=(), notify=_mb_notify, options={"PERSISTENT"})
    cube = bpy.data.objects["evcheck_cube"]
    cube.location.x += 1.0
    bpy.context.view_layer.update()
    after_set = MB["notified"]
    bpy.msgbus.publish_rna(key=key)
    bpy.context.view_layer.update()
    after_publish = MB["notified"]
    # a timer cannot tick in --background while the script runs; record whether registering one is even allowed
    try:
        bpy.app.timers.register(lambda: None, first_interval=0.0)
        timer_ok = True
    except Exception as e:
        timer_ok = err(e)
    bpy.msgbus.clear_by_owner(owner)
    return {"after_direct_set": after_set, "after_publish_rna": after_publish, "timer_register": timer_ok,
            "note": "no timer tick runs during a --background --python script; counts are what the add-on would see per command"}


tryf("msgbus", msgbus)


# --- 6. deque resize keeps order (stdlib) -----------------------------------------------------------
def deque_resize():
    d = collections.deque(range(10), maxlen=5)
    kept = list(d)
    d2 = collections.deque(d, maxlen=8)
    d2.extend([10, 11, 12, 13])
    d3 = collections.deque(d2, maxlen=3)
    return {"after_overflow": kept, "after_grow_and_extend": list(d2), "after_shrink": list(d3),
            "ok": kept == [5, 6, 7, 8, 9] and list(d2) == [6, 7, 8, 9, 10, 11, 12, 13] and list(d3) == [11, 12, 13]}


tryf("deque_resize", deque_resize)


# --- 7. timbermesh plugin check tuple ------------------------------------------------------------------
def timbermesh():
    import addon_utils
    try:
        return {"check": list(addon_utils.check("timbermesh_blender_plugin")),
                "module_listed": any(m.__name__ == "timbermesh_blender_plugin" for m in addon_utils.modules())}
    except Exception as e:
        return err(e)


tryf("timbermesh", timbermesh)


# --- 2 + 1e. legacy Scene prop after unregister; save_pre/save_post/load_post --------------------------
def legacy_prop_and_file_handlers():
    res = {}
    bpy.types.Scene.blendermcp_hyper3d_api_key = bpy.props.StringProperty(subtype="PASSWORD")
    bpy.context.scene.blendermcp_hyper3d_api_key = "k"
    fp = os.path.join(TMP, "legacy.blend")
    before = {n: FIRED[n] for n in ("save_pre", "save_post", "load_post")}
    bpy.ops.wm.save_as_mainfile(filepath=fp, copy=True)
    res["save_fired"] = {n: FIRED[n] - before[n] for n in ("save_pre", "save_post")}
    res["save_args"] = {n: LAST_ARGS.get(n) for n in ("save_pre", "save_post")}
    # where does a registered prop's value live on this install? (5.x keeps RNA-defined props in a separate
    # "system" group that id.keys()/id.get() do not see)
    sc0 = bpy.context.scene
    res["keys_while_registered"] = [k for k in sc0.keys() if k.startswith("blendermcp_")]
    res["get_while_registered"] = sc0.get("blendermcp_hyper3d_api_key")
    res["system_props_attr"] = [a for a in dir(sc0) if "system" in a.lower() and "prop" in a.lower()]
    try:
        sp = sc0.system_properties
        res["system_properties_keys_while_registered"] = [k for k in sp.keys() if k.startswith("blendermcp_")]
    except Exception as e:
        res["system_properties_keys_while_registered"] = err(e)
    del bpy.types.Scene.blendermcp_hyper3d_api_key
    res["rna_registered_after_del"] = hasattr(bpy.types.Scene, "blendermcp_hyper3d_api_key")
    # still in memory: the idprop survives the del?
    res["in_memory_get_after_del"] = bpy.context.scene.get("blendermcp_hyper3d_api_key")
    try:
        sp = bpy.context.scene.system_properties
        res["system_properties_get_after_del"] = sp.get("blendermcp_hyper3d_api_key")
    except Exception as e:
        res["system_properties_get_after_del"] = err(e)
    handlers_before = {n: len(getattr(bpy.app.handlers, n)) for n in NAMES if hasattr(bpy.app.handlers, n)}
    bpy.ops.wm.open_mainfile(filepath=fp, load_ui=False)
    sc = bpy.context.scene
    res["load_fired"] = {"load_post": FIRED["load_post"] - before["load_post"]}
    res["load_args"] = LAST_ARGS.get("load_post")
    res["scene_get_after_open"] = sc.get("blendermcp_hyper3d_api_key")
    res["scene_keys_after_open"] = [k for k in sc.keys() if k.startswith("blendermcp_")]
    res["getattr_after_open"] = getattr(sc, "blendermcp_hyper3d_api_key", "<AttributeError>")
    # 5.x: ID.bl_system_properties_get() is the accessor for the system group (do_create kwarg)
    try:
        if hasattr(sc, "bl_system_properties_get"):
            grp = sc.bl_system_properties_get(do_create=False)
            res["bl_system_properties_after_open"] = {"type": type(grp).__name__,
                                                      "keys": [k for k in grp.keys() if k.startswith("blendermcp_")] if grp is not None else None,
                                                      "get": grp.get("blendermcp_hyper3d_api_key") if grp is not None else None}
            if grp is not None and "blendermcp_hyper3d_api_key" in grp.keys():
                del grp["blendermcp_hyper3d_api_key"]
                res["bl_system_properties_del_then_get"] = grp.get("blendermcp_hyper3d_api_key", "<missing>")
        else:
            res["bl_system_properties_after_open"] = "absent on this install"
    except Exception as e:
        res["bl_system_properties_after_open"] = err(e)
    try:
        sp = sc.system_properties
        res["system_properties_after_open"] = {"get": sp.get("blendermcp_hyper3d_api_key"),
                                               "keys": [k for k in sp.keys() if k.startswith("blendermcp_")]}
        # can the orphaned system prop be deleted through that group?
        try:
            del sp["blendermcp_hyper3d_api_key"]
            res["system_properties_del_then_get"] = sp.get("blendermcp_hyper3d_api_key", "<missing>")
        except Exception as e:
            res["system_properties_del_then_get"] = err(e)
    except Exception as e:
        res["system_properties_after_open"] = err(e)
    # a raw user-group idprop written WITHOUT registration (what a 4.3.2 / 1.6.0 file carries) round-trips?
    sc["blendermcp_sketchfab_api_key"] = "raw"
    fp2 = os.path.join(TMP, "raw.blend")
    bpy.ops.wm.save_as_mainfile(filepath=fp2, copy=True)
    bpy.ops.wm.open_mainfile(filepath=fp2, load_ui=False)
    sc = bpy.context.scene
    res["raw_idprop_after_open"] = sc.get("blendermcp_sketchfab_api_key")
    # delete the orphan idprop and prove it is gone
    try:
        del sc["blendermcp_hyper3d_api_key"]
        res["del_idprop_then_get"] = sc.get("blendermcp_hyper3d_api_key", "<missing>")
    except Exception as e:
        res["del_idprop_then_get"] = err(e)
    handlers_after = {n: len(getattr(bpy.app.handlers, n)) for n in NAMES if hasattr(bpy.app.handlers, n)}
    res["persistent_handlers_survive_open"] = handlers_before == handlers_after
    res["handler_counts_after_open"] = handlers_after
    # depsgraph + frame still fire after the file load (new scene)
    b = FIRED["frame_change_post"]
    sc.frame_set(3)
    res["frame_fires_after_open"] = FIRED["frame_change_post"] - b
    return res


tryf("legacy_prop_and_file_handlers", legacy_prop_and_file_handlers)


# --- cleanup: remove our handlers, prove the counts return to the start -----------------------------------
def cleanup():
    for n, fn in _HANDLERS.items():
        lst = getattr(bpy.app.handlers, n)
        if fn in lst:
            lst.remove(fn)
    return {"remaining_ours": sum(1 for n, fn in _HANDLERS.items() if fn in getattr(bpy.app.handlers, n)),
            "fired_total": dict(FIRED), "handler_errors": ERRORS}


tryf("cleanup", cleanup)

print("EVCHECK " + json.dumps(out))
