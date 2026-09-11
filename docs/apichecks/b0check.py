# Headless facts for Phase B0 (settings, save and load) that scheck.py does not record.
# Runs on 4.x and 5.x. Prints one line: B0CHECK {json}. ASCII only. Never imports addon.py.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\b0check.py 2>&1 | findstr B0CHECK
import bpy
import json
import os
import sys
import tempfile

out = {"version": bpy.app.version_string, "python": sys.version.split()[0]}


def tryf(key, fn):
    try:
        out[key] = fn()
    except Exception as e:
        out[key] = "Error: " + str(e).strip()[:200]


def err(e):
    return "Error: " + str(e).strip()[:120]


def op_kwargs(op):
    try:
        return [p.identifier for p in op.get_rna_type().properties if p.identifier != "rna_type"]
    except Exception as e:
        return err(e)


def op_enum(op, prop):
    try:
        return [i.identifier for i in op.get_rna_type().properties[prop].enum_items]
    except Exception as e:
        return err(e)


# --- startup file flags (S11, S19) --------------------------------------------
out["data_flags_startup"] = {"is_dirty": bpy.data.is_dirty, "is_saved": bpy.data.is_saved,
                             "filepath": bpy.data.filepath, "version": list(bpy.data.version)}

# --- file operators not covered by scheck.py ------------------------------------
out["file.pack_all"] = op_kwargs(bpy.ops.file.pack_all)
out["file.unpack_all"] = op_kwargs(bpy.ops.file.unpack_all)
out["file.unpack_all.method"] = op_enum(bpy.ops.file.unpack_all, "method")
out["file.find_missing_files"] = op_kwargs(bpy.ops.file.find_missing_files)
out["file.make_paths_relative"] = op_kwargs(bpy.ops.file.make_paths_relative)
out["file.make_paths_absolute"] = op_kwargs(bpy.ops.file.make_paths_absolute)
out["file.pack_libraries"] = op_kwargs(bpy.ops.file.pack_libraries)
out["wm.save_mainfile"] = op_kwargs(bpy.ops.wm.save_mainfile)
out["outliner.orphans_purge"] = op_kwargs(bpy.ops.outliner.orphans_purge)


# --- save with compress / copy / incremental in a temp dir (S12, C5) -----------
def save_roundtrip():
    d = tempfile.mkdtemp(prefix="b0check_")
    res = {"tempdir": d,
           "use_file_compression_pref": bpy.context.preferences.filepaths.use_file_compression}
    a = os.path.join(d, "a.blend")
    r = bpy.ops.wm.save_as_mainfile(filepath=a, compress=True, relative_remap=True)
    res["save_as_compress"] = {"result": sorted(r), "bytes": os.path.getsize(a) if os.path.exists(a) else 0,
                               "is_saved": bpy.data.is_saved, "is_dirty": bpy.data.is_dirty,
                               "filepath": bpy.data.filepath}
    raw = os.path.join(d, "a_raw.blend")
    r = bpy.ops.wm.save_as_mainfile(filepath=raw, compress=False, copy=True)
    res["save_copy_uncompressed"] = {"result": sorted(r), "bytes": os.path.getsize(raw) if os.path.exists(raw) else 0,
                                     "filepath_after_copy": bpy.data.filepath}
    res["copy_smaller_than_raw"] = res["save_as_compress"]["bytes"] < res["save_copy_uncompressed"]["bytes"]
    try:
        r = bpy.ops.wm.save_mainfile(incremental=True)
        res["save_incremental"] = {"result": sorted(r), "filepath": bpy.data.filepath,
                                   "files": sorted(os.listdir(d))}
    except Exception as e:
        res["save_incremental"] = err(e)
    try:
        r = bpy.ops.wm.save_mainfile()
        res["save_plain"] = {"result": sorted(r), "files": sorted(os.listdir(d))}
    except Exception as e:
        res["save_plain"] = err(e)
    res["file_version_after_save"] = list(bpy.data.version)
    # wm.revert_mainfile() headless crashed Blender 4.3.2 with EXCEPTION_ACCESS_VIOLATION
    # (2026-09-11, a1.crash.txt); opt-in so the script finishes on every version.
    if os.environ.get("B0CHECK_REVERT") == "1":
        try:
            r = bpy.ops.wm.revert_mainfile()
            res["revert"] = sorted(r)
        except Exception as e:
            res["revert"] = err(e)
    else:
        res["revert"] = "skipped (set B0CHECK_REVERT=1; crashes 4.3.2 headless)"
    return res


tryf("save_roundtrip", save_roundtrip)


# --- libraries.load list_only / append with keyword args (S14) -----------------
def libraries_load():
    # Blender refuses to load from the currently open file; use the uncompressed copy.
    fp = os.path.join(os.path.dirname(bpy.data.filepath), "a_raw.blend") if bpy.data.filepath else ""
    res = {"filepath": fp}
    if not fp or not os.path.exists(fp):
        return "no saved copy to load from"
    with bpy.data.libraries.load(fp, link=False, relative=False) as (src, dst):
        res["objects"] = list(src.objects)
        res["materials"] = list(src.materials)
        dst.objects = [n for n in src.objects if n == "Cube"]
    res["appended"] = [o.name for o in dst.objects if o is not None]
    res["object_count_after"] = len(bpy.data.objects)
    try:
        with bpy.data.libraries.load(fp, False) as (src, dst):
            pass
        res["positional_link_arg"] = "accepted"
    except Exception as e:
        res["positional_link_arg"] = err(e)
    return res


tryf("libraries_load", libraries_load)


# --- id-prop dict on the scene (S20) ---------------------------------------------
def scene_idprop():
    sc = bpy.context.scene
    sc["blendermcp_profile"] = {"engine_target": "UNREAL", "max_triangles": 5000,
                                "dirs": {"export": "x", "tex": "y"}, "list": [1, 2, 3]}
    v = sc["blendermcp_profile"]
    res = {"type": type(v).__name__}
    try:
        res["to_dict"] = v.to_dict()
    except Exception as e:
        res["to_dict"] = err(e)
    res["keys_has"] = "blendermcp_profile" in sc.keys()
    del sc["blendermcp_profile"]
    res["deleted"] = "blendermcp_profile" not in sc.keys()
    return res


tryf("scene_idprop", scene_idprop)


# --- AddonPreferences with PASSWORD + Bool + Int registers headless --------------
def addon_prefs():
    import bpy.props as P

    class B0CHECK_AddonPreferences(bpy.types.AddonPreferences):
        bl_idname = "b0check_fake_addon"
        port: P.IntProperty(name="Port", default=9876, min=1024, max=65535)
        autostart_server: P.BoolProperty(name="Autostart", default=True)
        api_key: P.StringProperty(name="Key", subtype='PASSWORD', default="")

        def draw(self, context):
            pass

    res = {}
    bpy.utils.register_class(B0CHECK_AddonPreferences)
    res["registered"] = True
    props = B0CHECK_AddonPreferences.bl_rna.properties
    res["api_key_subtype"] = props["api_key"].subtype
    res["port_default"] = props["port"].default
    res["prefs_addons_has_fake"] = "b0check_fake_addon" in bpy.context.preferences.addons.keys()
    bpy.utils.unregister_class(B0CHECK_AddonPreferences)
    res["unregistered"] = True
    return res


tryf("addon_prefs", addon_prefs)

# --- handlers, timers, prefs flags, config paths ---------------------------------
out["handlers"] = {h: hasattr(bpy.app.handlers, h) for h in
                   ("load_post", "load_pre", "save_pre", "save_post", "exit_pre")}


def timers():
    res = {"timers_module": hasattr(bpy.app, "timers")}
    res["register_returns"] = str(bpy.app.timers.register(lambda: None, first_interval=0.1))
    return res


tryf("timers", timers)
prefs = bpy.context.preferences
out["prefs_flags"] = {"is_dirty": prefs.is_dirty, "use_preferences_save": prefs.use_preferences_save}
out["config_dir"] = bpy.utils.user_resource('CONFIG')
out["unit_settings"] = [p.identifier for p in bpy.context.scene.unit_settings.bl_rna.properties
                        if p.identifier != "rna_type"]
out["image_settings"] = [p.identifier for p in bpy.context.scene.render.image_settings.bl_rna.properties
                         if p.identifier != "rna_type"]
out["workspaces"] = sorted(w.name for w in bpy.data.workspaces)
out["window_available"] = bpy.context.window is not None


# --- try-assign view transform (S17) ----------------------------------------------
def view_transform():
    vs = bpy.context.scene.view_settings
    res = {"initial": vs.view_transform,
           "enum_headless": [i.identifier for i in vs.bl_rna.properties["view_transform"].enum_items]}
    for name in ("Standard", "AgX", "Filmic", "Khronos PBR Neutral", "Raw", "BOGUS"):
        try:
            vs.view_transform = name
            res[name] = vs.view_transform
        except Exception as e:
            res[name] = err(e)
    res["look_enum_headless"] = [i.identifier for i in vs.bl_rna.properties["look"].enum_items]
    res["display_device_enum"] = [i.identifier for i in
                                  bpy.context.scene.display_settings.bl_rna.properties["display_device"].enum_items]
    return res


tryf("view_transform", view_transform)


# --- Cycles device preferences (S18) ---------------------------------------------
def cycles_devices():
    addon = bpy.context.preferences.addons.get("cycles")
    if addon is None:
        return "cycles addon not in preferences.addons"
    cp = addon.preferences
    res = {"compute_device_type_initial": cp.compute_device_type,
           "enum_headless": [i.identifier for i in cp.bl_rna.properties["compute_device_type"].enum_items]}
    for name in ("NONE", "CUDA", "OPTIX", "HIP", "ONEAPI", "METAL", "BOGUS"):
        try:
            cp.compute_device_type = name
            res[name] = cp.compute_device_type
        except Exception as e:
            res[name] = err(e)
    try:
        cp.compute_device_type = "NONE"
    except Exception:
        pass
    try:
        devs = cp.get_devices()
        res["get_devices_type"] = type(devs).__name__
        res["devices"] = [(d.name, d.type, d.use) for d in cp.devices][:8]
    except Exception as e:
        res["get_devices"] = err(e)
    res["scene_cycles_device_enum"] = [i.identifier for i in
                                       bpy.context.scene.cycles.bl_rna.properties["device"].enum_items]
    return res


tryf("cycles_devices", cycles_devices)


# --- addon_utils signatures and a real enable/disable round trip -------------------
def addons():
    import addon_utils
    import inspect
    res = {"enable_sig": str(inspect.signature(addon_utils.enable)),
           "disable_sig": str(inspect.signature(addon_utils.disable)),
           "check_sig": str(inspect.signature(addon_utils.check))}
    res["enabled_now"] = sorted(bpy.context.preferences.addons.keys())
    names = [m.__name__ for m in addon_utils.modules()]
    res["module_count"] = len(names)
    res["has_rigify"] = "rigify" in names
    res["has_io_anim_bvh"] = "io_anim_bvh" in names
    enabled = set(res["enabled_now"])
    per = {}
    for n in names:
        if n in enabled:
            continue
        errs = []
        try:
            # default_set=True is what adds the module to preferences.addons (rigify reads
            # its own prefs there during register); persistence is a separate save_userpref.
            mod = addon_utils.enable(n, default_set=True,
                                     handle_error=lambda e: errs.append(str(e)[:160]))
            per[n] = {"enable": mod.__name__ if mod else None, "err": errs,
                      "in_prefs": n in bpy.context.preferences.addons.keys(),
                      "prefs_dirty": bpy.context.preferences.is_dirty}
            if mod:
                addon_utils.disable(n, default_set=True)
                per[n]["disabled_after"] = n not in bpy.context.preferences.addons.keys()
        except Exception as e:
            per[n] = err(e)
    res["enable_disabled_modules"] = per
    try:
        r = bpy.ops.preferences.addon_enable(module="rigify")
        res["op_addon_enable_rigify"] = {"result": sorted(r),
                                        "in_prefs": "rigify" in bpy.context.preferences.addons.keys()}
        if "rigify" in bpy.context.preferences.addons.keys():
            bpy.ops.preferences.addon_disable(module="rigify")
    except Exception as e:
        res["op_addon_enable_rigify"] = err(e)
    res["prefs_is_dirty_after"] = bpy.context.preferences.is_dirty
    return res


tryf("addons", addons)

print("B0CHECK " + json.dumps(out))
