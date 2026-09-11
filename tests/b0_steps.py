# Phase B0 (settings, save and load) headless steps. Executed inside tests/headless_handlers.py
# when it is run with `-- --b0`; uses that file's helpers (step, call, ok, err, make_cube, clear_scene,
# WORK, addon, srv, HANDLERS, P, A). Written from docs/FEATURE-REQUEST-settings-save-load.md section 6
# with docs/PLAN.md section 9 deltas C3 (is_dirty recorded, never asserted), C5 (compress explicit both
# ways), C13 (default_set=True, persist only decides save_userpref), C14 (incremental -> a1.blend),
# C15 (no wm.revert after an incremental save on 4.3.2: only the refusal path is exercised here).
# Server-side steps 10 (presets files), 16 (session JSON), 17 (output_dir) live in tests/test_b0_server.py.
import os
import json
import time
import socket

import bpy

B0DIR = os.path.join(WORK, "b0")
os.makedirs(B0DIR, exist_ok=True)
A_BLEND = os.path.join(B0DIR, "a.blend")
os.environ.setdefault("BLENDER_MCP_SETTINGS_DIR", os.path.join(WORK, "settings_dir"))


def _prefs_path():
    cfg = bpy.utils.user_resource("CONFIG")
    return os.path.join(cfg, "userpref.blend") if cfg else None


def _mtime(p):
    return os.path.getmtime(p) if p and os.path.exists(p) else None


USERPREF_MTIME_AT_START = _mtime(_prefs_path())

# fixture: a never-saved, empty file (the Phase A section left a saved file open); bpy-level, not a handler under test
bpy.ops.wm.read_homefile(use_empty=True)
assert bpy.data.filepath == "" and not bpy.data.is_saved
clear_scene()
make_cube("B0Cube")
bpy.context.scene.frame_set(1)
RAW_BLEND = os.path.join(B0DIR, "a_raw.blend")


# ---- B0 step 1: get_file_state on the unsaved default (C3)
def b1():
    r = ok(call("get_file_state"), must=["filepath", "is_saved", "is_dirty", "file_version", "blender_version"])
    assert r["is_saved"] is False and r["filepath"] == "", (r["is_saved"], r["filepath"])
    for k in ("use_autopack", "packed_images", "missing_files", "libraries", "autosave_dir", "autosave_files", "recent_files", "backup_files"):
        assert k in r, "get_file_state lacks %s" % k
    return "is_saved=False filepath='' is_dirty=%s (recorded, C3) file_version=%s" % (r["is_dirty"], r["file_version"])


step("B0-1 get_file_state unsaved default", b1, readonly=True, tag="B0")


# ---- B0 step 2: save compressed, copy uncompressed (C5)
def b2():
    # reply key is "filepath" (Ton's landed shape, accepted by Ada 11:58; the doc said "path")
    r = ok(call("save_blend", filepath=A_BLEND, compress=True), must=["filepath", "bytes", "is_dirty", "elapsed", "compress"])
    assert r["compress"] is True and os.path.exists(A_BLEND) and r["bytes"] == os.path.getsize(A_BLEND), r
    st = ok(call("get_file_state"))
    assert st["is_saved"] is True and os.path.normcase(st["filepath"]) == os.path.normcase(A_BLEND), st
    raw = os.path.join(B0DIR, "a_raw.blend")
    r2 = ok(call("save_copy", filepath=raw, compress=False))
    assert os.path.normcase(bpy.data.filepath) == os.path.normcase(A_BLEND), "save_copy changed the working path to %s" % bpy.data.filepath
    assert os.path.getsize(raw) > os.path.getsize(A_BLEND), "compressed %d >= raw %d" % (os.path.getsize(A_BLEND), os.path.getsize(raw))
    return "a.blend %dB compressed < a_raw.blend %dB; is_dirty after save=%s (S21 recorded)" % (os.path.getsize(A_BLEND), os.path.getsize(raw), r["is_dirty"])


step("B0-2 save_blend compress=True vs save_copy compress=False", b2, tag="B0")


# ---- B0 step 3: load_blend dirty guard
def b3():
    # S-new (Ton, 11:57): 5.2.1 --background never sets bpy.data.is_dirty (no undo pushes headless); the guard
    # fires iff is_dirty was True before the call, so assert exactly that and report which branch ran
    ok(call("add_primitive", primitive_type="cube", name="Dirty"))
    dirty_before = bpy.data.is_dirty
    res = call("load_blend", filepath=A_BLEND)
    if dirty_before:
        msg = err(res, "force")
        assert "a.blend" in msg.lower() or "current" in msg.lower(), A(msg)
        branch = "is_dirty=True -> refused without force (%s)" % A(msg)[:60]
        assert "Dirty" in bpy.data.objects, "refusal must not have loaded anything"
    else:
        r0 = ok(res, must=["previous_file", "blender_version_of_file", "unsaved_changes_discarded"])
        assert r0["unsaved_changes_discarded"] is False, r0
        branch = "is_dirty=False headless -> loaded without force, unsaved_changes_discarded=False"
        ok(call("add_primitive", primitive_type="cube", name="Dirty"))
    r = ok(call("load_blend", filepath=A_BLEND, force=True), must=["previous_file", "blender_version_of_file", "unsaved_changes_discarded"])
    assert r["unsaved_changes_discarded"] is dirty_before and "Dirty" not in bpy.data.objects, (r, dirty_before)
    assert os.path.normcase(r["previous_file"] or A_BLEND) == os.path.normcase(A_BLEND), r
    return "%s; force=True reloaded (discarded=%s), file version %s" % (branch, r["unsaved_changes_discarded"], r["blender_version_of_file"])


step("B0-3 load_blend dirty guard + force", b3, tag="B0")


# ---- B0 step 4: save_version twice + sidecar
def b4():
    r1 = ok(call("save_version", note="first"))
    r2 = ok(call("save_version", note="second"))
    v1, v2 = os.path.join(B0DIR, "a_v001.blend"), os.path.join(B0DIR, "a_v002.blend")
    assert os.path.exists(v1) and os.path.exists(v2), (r1, r2)
    side = os.path.join(B0DIR, "a.blend.versions.json")
    assert os.path.exists(side), "sidecar missing"
    entries = json.load(open(side, encoding="utf-8"))
    entries = entries.get("versions", entries) if isinstance(entries, dict) else entries
    assert len(entries) == 2 and entries[0].get("note") == "first", entries
    assert os.path.normcase(bpy.data.filepath) == os.path.normcase(A_BLEND), "working path changed"
    lv = ok(call("list_versions"))
    lst = lv.get("versions", lv)
    assert len(lst) == 2, lv
    return "a_v001/a_v002 + sidecar with 2 entries, working path unchanged, list_versions=2"


step("B0-4 save_version x2 + sidecar + list_versions", b4, tag="B0")


# ---- B0 step 5: incremental save (C14)
def b5():
    r = ok(call("save_blend", incremental=True))
    inc = os.path.join(B0DIR, "a1.blend")
    assert os.path.exists(inc), "expected a1.blend (C14), reply %s" % A(r)
    assert os.path.normcase(bpy.data.filepath) == os.path.normcase(inc), bpy.data.filepath
    ok(call("load_blend", filepath=A_BLEND, force=True))
    return "a.blend -> a1.blend (Blender's own naming), reloaded a.blend"


step("B0-5 save_blend incremental -> a1.blend (C14)", b5, tag="B0")


# ---- B0 step 6: new_file empty, revert refuses when never saved (C15: refusal path only)
def b6():
    r = ok(call("new_file", empty=True, force=True))
    assert len(bpy.context.scene.objects) == 0 and r.get("object_count", 0) == 0, (len(bpy.context.scene.objects), r)
    msg = err(call("revert_file"))
    assert "never" in msg.lower() or "not saved" in msg.lower() or "unsaved" in msg.lower(), A(msg)
    r2 = ok(call("load_blend", filepath=A_BLEND, force=True))
    assert "B0Cube" in bpy.data.objects
    return "empty file 0 objects; revert refused (%s); a.blend back with B0Cube" % A(msg)[:50]


step("B0-6 new_file(empty) + revert_file refusal + load back", b6, tag="B0")


# ---- B0 step 7: append_from_blend list_only + append into empty file
def b7():
    # S25: Blender refuses libraries.load on the currently open file, so list from the save_copy (a_raw.blend)
    err(call("append_from_blend", filepath=A_BLEND, list_only=True), "currently open")
    r = ok(call("append_from_blend", filepath=RAW_BLEND, list_only=True))
    contents = r.get("contents", r)
    objs = contents.get("objects")
    assert objs and "B0Cube" in objs and "B0Cube" in contents.get("meshes", []), r
    ok(call("new_file", empty=True, force=True))
    r2 = ok(call("append_from_blend", filepath=A_BLEND, datablocks={"objects": ["B0Cube"]}))
    assert len(bpy.context.scene.objects) == 1 and "B0Cube" in bpy.data.objects, r2
    # an unknown name: landed shape reports it under "missing" with nothing appended (not an error envelope)
    r3 = ok(call("append_from_blend", filepath=A_BLEND, datablocks={"objects": ["Nope"]}))
    assert r3.get("new_objects") == [] and (r3.get("missing") or {}).get("objects") == ["Nope"], r3
    assert len(bpy.context.scene.objects) == 1, "an all-missing append must add nothing"
    ok(call("load_blend", filepath=A_BLEND, force=True))
    return "open-file refusal surfaced; list_only(a_raw) shows B0Cube; appended into empty file -> 1 object; unknown name reported under missing, nothing added"


def b7b():
    ok(call("new_file", empty=True, force=True))
    r = ok(call("link_from_blend", filepath=RAW_BLEND, datablocks={"objects": ["B0Cube"]}))
    linked = [o for o in bpy.data.objects if o.library is not None or (o.name == "B0Cube" and o.data and o.data.library)]
    assert linked or any(l.filepath and "a_raw" in l.filepath for l in bpy.data.libraries), "no linked library after link_from_blend: %s" % A(r)
    ok(call("load_blend", filepath=A_BLEND, force=True))
    return "link_from_blend linked B0Cube from a_raw.blend (library present), reloaded a.blend"


step("B0-7 append_from_blend list_only + append", b7, tag="B0")
step("B0-7b link_from_blend", b7b, tag="B0")


# ---- B0 step 8: describe_settings / set_settings
def b8():
    d = ok(call("describe_settings", scope="RENDER"))
    props = d.get("properties", d)
    rx = props["resolution_x"]
    assert rx["type"] == "INT" and "min" in rx and "max" in rx, rx
    r = ok(call("set_settings", scope="RENDER", values={"resolution_x": 1280, "engine": "BOGUS"}), must=["set", "unset"])
    assert "resolution_x" in r["set"] and bpy.context.scene.render.resolution_x == 1280
    assert "engine" in r["unset"] and ("CYCLES" in str(r["unset"]["engine"]) or "EEVEE" in str(r["unset"]["engine"])), r["unset"]
    ro = [k for k, v in props.items() if v.get("read_only")]
    if ro:
        r3 = ok(call("set_settings", scope="RENDER", values={ro[0]: 1}))
        assert ro[0] in r3.get("unset", {}), "read-only %s accepted" % ro[0]
    g = ok(call("get_settings", scope="RENDER", keys="resolution_x,engine"))
    g = g.get("values", g)
    assert g.get("resolution_x") == 1280 and isinstance(g.get("engine"), str), g
    assert set(g) == {"resolution_x", "engine"}, "keys filter not honoured: %s" % list(g)
    return "resolution_x INT with range; set 1280 + engine BOGUS unset listing engines; read-only refused (%s); get_settings keys filter" % (ro[:1] or "none found")


step("B0-8 describe/get/set_settings RENDER", b8, tag="B0")


# ---- B0 step 9: snapshot / set_render_quality / restore
def b9():
    sc = bpy.context.scene
    sc.render.resolution_percentage = 77
    sc.cycles.samples = 123
    r = ok(call("settings_snapshot", name="t"))
    ok(call("set_render_quality", preset="PREVIEW"))
    assert sc.render.resolution_percentage == 25 and sc.cycles.samples != 123, (sc.render.resolution_percentage, sc.cycles.samples)
    ok(call("settings_restore", name="t"))
    assert sc.render.resolution_percentage == 77 and sc.cycles.samples == 123, (sc.render.resolution_percentage, sc.cycles.samples)
    ls = ok(call("list_settings_snapshots"))
    names = ls.get("snapshots", ls)
    assert "t" in names and "_before_quality" in names, names
    ok(call("delete_settings_snapshot", name="t"))
    assert "t" not in (ok(call("list_settings_snapshots")).get("snapshots") or []), "snapshot t not deleted"
    return "snapshot/preview(25%%)/restore -> 77%% and 123 samples; _before_quality listed; delete ok"


step("B0-9 settings_snapshot + set_render_quality + settings_restore", b9, tag="B0")


# ---- B0 step 10 (addon side): Blender's own presets
def b10():
    r = ok(call("list_blender_presets", category="render"))
    names = [p["name"] if isinstance(p, dict) else p for p in r.get("presets", r)]
    assert any("HDTV_1080p" == n for n in names), names
    hd = "HDTV_1080p"
    ok(call("apply_blender_preset", category="render", name=hd))
    sc = bpy.context.scene
    assert (sc.render.resolution_x, sc.render.resolution_y) == (1920, 1080), (sc.render.resolution_x, sc.render.resolution_y)
    err(call("apply_blender_preset", category="render", name="No Such Preset"))
    return "render preset %r applied -> 1920x1080; unknown preset rejected (server-side save/load/list_preset in test_b0_server.py)" % hd


step("B0-10 list/apply_blender_preset", b10, tag="B0")


# ---- B0 step 11: set_color_management by try-assign (S17/S22)
def b11():
    sc = bpy.context.scene
    ok(call("set_color_management", view_transform="Standard"))
    assert sc.view_settings.view_transform == "Standard", sc.view_settings.view_transform
    g = ok(call("get_settings", scope="COLOR"))
    g = g.get("values", g)
    assert g.get("view_transform") == "Standard", g
    # a bogus value comes back in the set_settings envelope: set=[] and unset.view_transform quoting the live tuple
    rb = ok(call("set_color_management", view_transform="NoSuchTransform"))
    msg = str((rb.get("unset") or {}).get("view_transform", ""))
    assert rb.get("set") == [] and "Standard" in msg and "AgX" in msg, "unset must quote the live tuple: %s" % A(rb)
    assert sc.view_settings.view_transform == "Standard", "a rejected value must not change the setting"
    ok(call("set_color_management", view_transform="AgX", exposure=0.5, gamma=1.1))
    assert sc.view_settings.view_transform == "AgX" and abs(sc.view_settings.exposure - 0.5) < 1e-6
    return "Standard by assignment, read back via get_settings(COLOR); bogus lists live tuple; AgX+exposure+gamma"


step("B0-11 set_color_management try-assign", b11, tag="B0")


# ---- B0 step 12: project profile + units + sidecar + survives save/load
def b12():
    sc = bpy.context.scene
    ok(call("set_project_profile", engine_target="UNREAL", apply_units=True, max_triangles=5000))
    assert "blendermcp_profile" in sc.keys(), list(sc.keys())
    assert abs(sc.unit_settings.scale_length - 0.01) < 1e-9, sc.unit_settings.scale_length
    ok(call("save_blend", filepath=A_BLEND, compress=True))
    side = os.path.join(B0DIR, "a.blend.mcp-profile.json")
    assert os.path.exists(side), "profile sidecar missing (C10: written by the addon)"
    ok(call("load_blend", filepath=A_BLEND, force=True))
    r = ok(call("get_project_profile"))
    prof = r.get("profile", r)
    assert prof.get("engine_target") == "UNREAL" and prof.get("max_triangles") == 5000, r
    err(call("set_project_profile", engine_target="SOURCE2"))
    return "UNREAL profile stored, scale_length 0.01, sidecar written, read back after save+load; bad target rejected"


step("B0-12 set/get_project_profile", b12, tag="B0")


# ---- B0 step 13: enable/disable rigify, preferences not persisted (C13)
def b13():
    pref_p = _prefs_path()
    before_dirty = bpy.context.preferences.is_dirty
    r = ok(call("enable_addon", module="rigify"), must=["bl_info"])
    assert "is_dirty" in r or "preferences_dirty" in r, "reply must report preferences.is_dirty (C13): %s" % A(r)
    lst = ok(call("list_addons", enabled_only=True))
    mods = [a.get("module") for a in lst.get("addons", lst)]
    assert "rigify" in mods, mods
    r2 = ok(call("disable_addon", module="rigify"))
    mods2 = [a.get("module") for a in ok(call("list_addons", enabled_only=True)).get("addons", [])]
    assert "rigify" not in mods2, mods2
    assert _mtime(pref_p) == USERPREF_MTIME_AT_START, "userpref.blend was written (mtime changed)"
    assert r.get("persisted", False) is False and r2.get("persisted", False) is False, "persisted must be False without persist=True"
    msg = err(call("enable_addon", module="no_such_addon_xyz"))
    return "rigify enabled (is_dirty %s -> reported %s), listed, disabled; userpref.blend untouched; unknown module: %s" % (before_dirty, r.get("is_dirty", r.get("preferences_dirty")), A(msg)[:50])


step("B0-13 enable/disable_addon rigify without persisting (C13)", b13, tag="B0")


# ---- B0 step 14: save_preferences refuses without confirm (never call with confirm=True)
def b14():
    res = call("save_preferences")
    msg = err(res)
    r = res.get("result") if isinstance(res.get("result"), dict) else {}
    assert "confirm" in msg.lower(), A(msg)
    dirty_key = "preferences_dirty" if "preferences_dirty" in r else "is_dirty"
    assert dirty_key in r and "use_preferences_save" in r, "refusal must report preferences.is_dirty and use_preferences_save: %s" % A(r)
    assert _mtime(_prefs_path()) == USERPREF_MTIME_AT_START, "userpref.blend written by a refusal"
    return "refused: %s; %s=%s use_preferences_save=%s" % (A(msg)[:60], dirty_key, r[dirty_key], r["use_preferences_save"])


step("B0-14 save_preferences refuses without confirm", b14, readonly=True, tag="B0")


# ---- B0 step 15: addon settings + secrets migration
def b15():
    scene_props = bpy.types.Scene.bl_rna.properties.keys()
    leaked = [p for p in scene_props if p in ("blendermcp_port", "blendermcp_server_running")]
    assert not leaked, "Scene still carries port/running props: %s" % leaked
    # Lead ruling (11:45): the four legacy secret props stay REGISTERED through 2.1.x but are NOT drawn by the panel
    import inspect
    legacy_expected = ["blendermcp_hyper3d_api_key", "blendermcp_sketchfab_api_key",
                       "blendermcp_hunyuan3d_secret_id", "blendermcp_hunyuan3d_secret_key"]
    missing_legacy = [p for p in legacy_expected if p not in scene_props]
    assert not missing_legacy, "legacy secret props must stay registered through 2.1.x, missing: %s" % missing_legacy
    draw_src = inspect.getsource(addon.BLENDERMCP_PT_Panel.draw)
    drawn = [p for p in legacy_expected if p in draw_src]
    assert not drawn, "panel draw() still references legacy secret props: %s" % drawn
    legacy = {p: getattr(bpy.context.scene, p, None) for p in legacy_expected}
    # Ton fact (1): a preferences.addons entry named after the module makes _prefs() real headless.
    # Removed in finally; wm.save_userpref is never called (userpref.blend mtime checked at the end).
    entry = bpy.context.preferences.addons.new()
    entry.module = "addon"
    try:
        assert addon._prefs() is not None, "_prefs() still None with a preferences.addons entry for 'addon'"
        r = ok(call("set_addon_settings", values={"port": 9877, "hyper3d_api_key": "k-abc"}))
        g = ok(call("get_addon_settings"))
        assert g.get("port") == 9877, g
        assert addon._prefs().port == 9877
        keys = [k for k in g if "key" in k.lower() or "secret" in k.lower()]
        for k in keys:
            assert g[k] in (True, False, None) or isinstance(g[k], dict), "secret %s echoed back: %r" % (k, g[k])
        assert "k-abc" not in json.dumps(g) and "k-abc" not in json.dumps(r), "secret value echoed in a reply"
        assert addon._secret("hyper3d_api_key") == "k-abc", "consumer must read the pref first"
        addon._prefs().port = 9876
        addon._prefs().hyper3d_api_key = ""
    finally:
        bpy.context.preferences.addons.remove(entry)
    assert _mtime(_prefs_path()) == USERPREF_MTIME_AT_START, "userpref.blend written"
    return "no Scene port/running props (legacy secret props registered, values %s); prefs real via addons entry: port 9877 set+read, secret set but never echoed, _secret reads prefs first" % legacy


step("B0-15a addon settings, no Scene secrets", b15, tag="B0")


def b15b():
    """Secrets migration: headless _prefs() is None -> migration skips and the fallback read sees the scene key."""
    sc = bpy.context.scene
    legacy_prop = "blendermcp_hyper3d_api_key"
    if legacy_prop in sc.bl_rna.properties.keys():
        setattr(sc, legacy_prop, "legacy123")          # registered (hidden) legacy StringProperty
    else:
        sc[legacy_prop] = "legacy123"                   # id-prop as an old file would carry it
    assert (sc.get(legacy_prop, getattr(sc, legacy_prop, None))) == "legacy123", "fixture write failed"
    # Ton seam fact (e): _migrate_legacy_secrets(prefs) takes the prefs object, so a stand-in works headless
    import types
    helper = getattr(addon, "_migrate_legacy_secrets", None) or getattr(srv, "_migrate_legacy_secrets", None)
    assert helper, "_migrate_legacy_secrets not found on the module or server"
    # 1) headless skip path: no prefs -> nothing migrated, scene value untouched
    res_none = helper(None)
    still = sc.get("blendermcp_hyper3d_api_key", getattr(sc, "blendermcp_hyper3d_api_key", None))
    assert still == "legacy123", "skip path must leave the scene key alone, got %r" % (still,)
    # 2) with a prefs stand-in: key moves to prefs and the scene prop is blanked
    fake_prefs = types.SimpleNamespace(hyper3d_api_key="", sketchfab_api_key="", hunyuan3d_secret_id="", hunyuan3d_secret_key="")
    res = helper(fake_prefs)
    assert fake_prefs.hyper3d_api_key == "legacy123", "prefs did not receive the legacy key: %s" % A(res)
    after = sc.get("blendermcp_hyper3d_api_key", getattr(sc, "blendermcp_hyper3d_api_key", None))
    assert not after, "scene prop not blanked after migration: %r" % (after,)
    return "helper(None) skipped (%s); helper(prefs) moved legacy123 into prefs and blanked the scene prop (%s)" % (A(res_none)[:40], A(res)[:40])


step("B0-15b secrets migration headless skip + fallback read", b15b, tag="B0")


# ---- B0 step 16 (addon side): session state dict round trip
def b16():
    sc = bpy.context.scene
    c = bpy.data.objects["B0Cube"]
    sc.frame_set(7)
    c.select_set(True); bpy.context.view_layer.objects.active = c
    # C17 names; reply {success, name, state{file{filepath,is_dirty}, frame{current,start,end}, selection[], active, mode, ...}}
    st = ok(call("get_session_state"))
    st = st.get("state", st)
    assert st.get("frame", {}).get("current") == 7, st.get("frame")
    assert "B0Cube" in (st.get("selection") or []) and st.get("active") == "B0Cube", (st.get("selection"), st.get("active"))
    assert set(st.get("file", {})) >= {"filepath", "is_dirty"} and st.get("mode") == "OBJECT", st
    sc.frame_set(1); c.select_set(False); bpy.context.view_layer.objects.active = None
    r = ok(call("apply_session_state", state=st))
    assert sc.frame_current == 7 and c.select_get() and bpy.context.view_layer.objects.active == c, (sc.frame_current, c.select_get())
    st2 = json.loads(json.dumps(st)); st2["active"] = "Renamed_Away"; st2["selection"] = ["Renamed_Away"]
    r2 = ok(call("apply_session_state", state=st2))
    assert r2.get("not_restored") or r2.get("failed"), "missing object must be reported: %s" % A(r2)
    assert "Renamed_Away" in json.dumps(r2), "the report must name the missing object: %s" % A(r2)
    sc.frame_set(1)
    return "C17 names: frame/selection/active captured and re-applied; missing object reported by name"


step("B0-16 get/apply_session_state", b16, tag="B0")


def b16b():
    """Ton landed the pair as save_session_state / restore_session_state (C17 names it get/apply_session_state; the
    server sends the C17 names). This step verifies the dict round trip on the LANDED names so the behaviour is
    covered whichever name the Lead rules; B0-16/B0-27 keep failing until the names agree."""
    sc = bpy.context.scene
    c = bpy.data.objects["B0Cube"]
    sc.frame_set(7)
    c.select_set(True); bpy.context.view_layer.objects.active = c
    st = ok(call("save_session_state", name="t"))
    st = st.get("state", st)
    # landed layout: frame {current,start,end}, selection [names], active name, file {filepath,is_dirty}, mode
    assert st.get("frame", {}).get("current") == 7, st.get("frame")
    assert "B0Cube" in (st.get("selection") or []) and st.get("active") == "B0Cube", (st.get("selection"), st.get("active"))
    assert set(st.get("file", {})) >= {"filepath", "is_dirty"} and st.get("mode") == "OBJECT", st
    sc.frame_set(1); c.select_set(False); bpy.context.view_layer.objects.active = None
    r = ok(call("restore_session_state", name="t", state=st, load_file=False))
    assert sc.frame_current == 7 and c.select_get() and bpy.context.view_layer.objects.active == c, (sc.frame_current, c.select_get())
    st2 = json.loads(json.dumps(st))
    st2["active"] = "Renamed_Away"
    st2["selection"] = ["Renamed_Away"]
    r2 = ok(call("restore_session_state", name="t2", state=st2, load_file=False))
    assert r2.get("not_restored") or r2.get("failed"), "missing object must be reported: %s" % A(r2)
    assert "Renamed_Away" in json.dumps(r2), "the report must name the missing object: %s" % A(r2)
    sc.frame_set(1)
    return "landed names: state captured (frame 7, B0Cube selected), re-applied, missing object reported"


step("B0-16b session dict round trip on Ton's landed names (naming seam open)", b16b, tag="B0")


def b16c():
    r = ok(call("get_addon_preferences", module="cycles"))
    vals = r.get("values", r)
    assert isinstance(vals, dict) and "compute_device_type" in vals, r
    r2 = ok(call("set_addon_preferences", module="cycles", values={"compute_device_type": "NONE", "no_such_pref": 1}))
    assert "compute_device_type" in r2.get("set", []) and "no_such_pref" in r2.get("unset", {}), r2
    assert r2.get("persisted", False) is False
    err(call("get_addon_preferences", module="no_such_addon_xyz"))
    assert _mtime(_prefs_path()) == USERPREF_MTIME_AT_START
    return "cycles prefs read; compute_device_type set, unknown key unset, not persisted; unknown module rejected"


step("B0-16c get/set_addon_preferences (cycles)", b16c, tag="B0")


def b16d():
    """C31: restore_session_state with a saved file path reopens the file in the add-on (file_loaded True) and
    honours load_blend's dirty guard (refusal without force while dirty; 4.3.2 headless is always dirty)."""
    sc = bpy.context.scene
    ok(call("save_blend", filepath=A_BLEND, compress=True))
    sc.frame_set(9)
    st = ok(call("save_session_state", name="c31"))
    st = st.get("state", st)
    assert os.path.normcase(st["file"]["filepath"]) == os.path.normcase(A_BLEND) and st["frame"]["current"] == 9, st
    # the add-on reopens only when the session's path differs from the open file: move to a fresh, dirty file first
    bpy.ops.wm.read_homefile(use_empty=True)           # fixture: a different (never-saved) file is now open
    make_cube("DirtyC31")
    dirty_before = bpy.data.is_dirty
    res = call("restore_session_state", name="c31", state=st, load_file=True)
    if dirty_before:
        msg = err(res, "force")
        guard = "refused while dirty without force (%s)" % A(msg)[:50]
        assert "DirtyC31" in bpy.data.objects and bpy.data.filepath == "", "refusal must not have reopened the file"
    else:
        r0 = ok(res)
        assert r0.get("file_loaded") is True, r0
        guard = "not dirty headless -> reopened without force"
        bpy.ops.wm.read_homefile(use_empty=True)
        make_cube("DirtyC31")
    r = ok(call("restore_session_state", name="c31", state=st, load_file=True, force=True), must=["file_loaded"])
    assert r["file_loaded"] is True, r
    assert os.path.normcase(bpy.data.filepath) == os.path.normcase(A_BLEND) and "DirtyC31" not in bpy.data.objects and "B0Cube" in bpy.data.objects
    assert bpy.context.scene.frame_current == 9, bpy.context.scene.frame_current
    r2 = ok(call("restore_session_state", name="c31", state=st, load_file=False))
    assert r2.get("file_loaded") is False, r2
    bpy.context.scene.frame_set(1)
    return "%s; force=True reopened a.blend (file_loaded=True, DirtyC31 gone, frame 9 restored); load_file=False -> file_loaded False" % guard


step("B0-16d restore_session_state reopens the file with the dirty guard (C31)", b16d, tag="B0")


# ---- B0 step 18: render devices
def b18():
    r = ok(call("list_render_devices"))
    devs = r.get("devices", r)
    assert isinstance(devs, list), r
    r2 = ok(call("set_render_device", device="CPU"))
    assert bpy.context.scene.cycles.device == "CPU"
    msg = err(call("set_render_device", device="GPU", backend="METAL"))
    return "%d devices listed; CPU set; METAL refused on Windows (%s)" % (len(devs), A(msg)[:40])


step("B0-18 list_render_devices + set_render_device", b18, tag="B0")


# ---- B0 extra: get_version + PROTOCOL
def b19():
    # PLAN.md C2 wire shape: {"addon", "blender", "python", "protocol"} (Ton's DM said addon_version/blender_version; the landed code and the contract agree on the short keys)
    r = ok(call("get_version"), must=["addon", "blender", "python", "protocol"])
    assert r["addon"] == ".".join(str(v) for v in addon.bl_info["version"]) and r["protocol"] == 1, r
    assert getattr(addon, "PROTOCOL", None) == 1, "addon.PROTOCOL must be 1"
    assert r["blender"] == bpy.app.version_string
    return "addon %s protocol %s blender %s python %s" % (r["addon"], r["protocol"], r["blender"], r["python"])


step("B0-19 get_version + PROTOCOL=1", b19, readonly=True, tag="B0")


# ---- B0 extra: ensure_server_running with port 0, stopped in finally
def b20():
    started = None
    try:
        r = ok(call("ensure_server_running", port=0), must=["running", "port", "host", "started_now"])
        assert r["running"] is True and r["host"] == "127.0.0.1" and r["port"] > 0, r
        started = r
        with socket.create_connection((r["host"], r["port"]), timeout=3):
            pass
        r2 = ok(call("ensure_server_running", port=0))
        assert r2["started_now"] is False and r2["port"] == r["port"], r2
    finally:
        s = getattr(bpy.types, "blendermcp_server", None)
        if s is not None:
            s.stop()
            try:
                del bpy.types.blendermcp_server
            except Exception:
                pass
    assert "blendermcp_server_running" not in bpy.types.Scene.bl_rna.properties.keys()
    return "started on 127.0.0.1:%d (started_now=%s), socket accepted, second call idempotent, stopped" % (started["port"], started["started_now"])


step("B0-20 ensure_server_running port=0", b20, tag="B0")


# ---- B0 extra: get_scene_info file + settings_summary; set_render_settings extensions
def b21():
    r = ok(call("get_scene_info"), must=["file", "settings_summary"])
    assert set(r["file"]) >= {"filepath", "is_saved", "is_dirty", "version"}, r["file"]
    assert "engine" in r["settings_summary"] and "fps" in r["settings_summary"], r["settings_summary"]
    sc = bpy.context.scene
    ok(call("set_render_settings", file_format="PNG"))       # RGBA is only in the live color_mode enum for PNG/EXR/TIFF, not JPEG
    r2 = ok(call("set_render_settings", fps=30, frame_start=5, frame_end=50, resolution_percentage=40, use_simplify=True, simplify_subdivision=1, color_mode="RGBA"))
    assert sc.render.fps == 30 and (sc.frame_start, sc.frame_end) == (5, 50) and sc.render.resolution_percentage == 40, r2
    got = {"use_simplify": sc.render.use_simplify, "simplify_subdivision": sc.render.simplify_subdivision, "color_mode": sc.render.image_settings.color_mode}
    want = {"use_simplify": True, "simplify_subdivision": 1, "color_mode": "RGBA"}
    wrong = {k: got[k] for k in want if got[k] != want[k]}
    assert not wrong, "not applied: %s; reply set=%s unset=%s" % (wrong, r2.get("set"), A(r2.get("unset")))
    # a value outside the live enum must come back under unset with the live tuple, never raise
    r3 = ok(call("set_render_settings", file_format="JPEG", color_mode="RGBA"))
    assert "color_mode" in (r3.get("unset") or {}) and "RGB" in str(r3["unset"]["color_mode"]), r3
    ok(call("set_render_settings", file_format="PNG"))
    ok(call("set_frame_range", start=1, end=24))
    assert (sc.frame_start, sc.frame_end) == (1, 24)
    err(call("set_frame_range", start=30, end=10), "must not exceed")
    ok(call("set_scene_units", preset="UNREAL"))
    assert abs(sc.unit_settings.scale_length - 0.01) < 1e-9, sc.unit_settings.scale_length
    ok(call("set_scene_units", system="METRIC", scale_length=1.0))
    assert sc.unit_settings.system == "METRIC" and abs(sc.unit_settings.scale_length - 1.0) < 1e-9
    err(call("set_scene_units", preset="SOURCE2"), "not valid")
    return "file + settings_summary present; 7 extended render params applied; set_frame_range / set_scene_units (C11)"


step("B0-21 get_scene_info file block + extended set_render_settings + C11 tools", b21, tag="B0")


# ---- B0 extra: output settings incl. FFMPEG media_type (S12/D10)
def b22():
    ims = bpy.context.scene.render.image_settings
    ok(call("set_output_settings", file_format="FFMPEG", ffmpeg={"format": "MPEG4", "codec": "H264"}))
    assert ims.file_format == "FFMPEG" and bpy.context.scene.render.ffmpeg.codec == "H264"
    mt = getattr(ims, "media_type", None)
    ok(call("set_output_settings", file_format="PNG", color_mode="RGB", color_depth="16", compression=50))
    assert ims.file_format == "PNG" and ims.color_mode == "RGB" and ims.color_depth == "16" and ims.compression == 50
    return "FFMPEG/H264 (media_type=%s) then PNG RGB 16-bit compression 50 (media_type=%s)" % (mt, getattr(ims, "media_type", None))


step("B0-22 set_output_settings FFMPEG then PNG", b22, tag="B0")


# ---- B0 extra: paths, packing, autosave, workspaces, viewport defaults, simplify
def b23():
    r = ok(call("pack_all"))
    r2 = ok(call("find_missing_files", directory=B0DIR))
    assert isinstance(r2.get("missing", r2.get("still_missing", [])), list)
    ok(call("make_paths_relative")); ok(call("make_paths_absolute"))
    ok(call("unpack_all", unpack_method="USE_LOCAL"))
    err(call("unpack_all", unpack_method="BOGUS"))
    return "pack/find_missing/relative/absolute/unpack ok; bad unpack method rejected"


step("B0-23 pack/unpack/paths/find_missing_files", b23, tag="B0")


def b24():
    p = bpy.context.preferences.filepaths
    saved = (p.use_auto_save_temporary_files, p.auto_save_time, p.save_version)
    try:
        r = ok(call("set_autosave", enabled=True, interval_minutes=3, save_versions=2))
        assert p.use_auto_save_temporary_files is True and p.auto_save_time == 3 and p.save_version == 2
        assert r.get("persisted", False) is False
        assert _mtime(_prefs_path()) == USERPREF_MTIME_AT_START
    finally:
        p.use_auto_save_temporary_files, p.auto_save_time, p.save_version = saved
    return "autosave prefs applied in memory only, userpref.blend untouched"


step("B0-24 set_autosave without persist", b24, tag="B0")


def b25():
    r = ok(call("list_workspaces"))
    ws = r.get("workspaces", r)
    names = [w.get("name") if isinstance(w, dict) else w for w in ws]
    assert "Layout" in names, names
    ok(call("set_workspace", name="Layout"))
    err(call("set_workspace", name="NoSuchWorkspace"), "not found")
    r2 = ok(call("set_viewport_defaults", shading="WIREFRAME", show_overlays=False))
    ok(call("set_simplify", enabled=True, subdivision=2))
    sc = bpy.context.scene
    assert sc.render.use_simplify and sc.render.simplify_subdivision == 2
    ok(call("set_simplify", enabled=False))
    return "%d workspaces, Layout set, bad name rejected; viewport defaults reply %s; simplify on/off" % (len(names), A(json.dumps(r2))[:60])


step("B0-25 workspaces, viewport defaults, simplify", b25, tag="B0")


def b26():
    ok(call("add_primitive", primitive_type="cube", name="Dirty2"))
    if bpy.data.is_dirty:
        err(call("new_file", empty=True), "force")
    else:
        P("B0-26 note: is_dirty False headless on this build, new_file guard cannot fire (S-new)")
    r = ok(call("new_file", empty=True, force=True))
    assert len(bpy.context.scene.objects) == 0
    # NEVER call recover_file(mode="LAST_SESSION") headless: it opens the user's real quit.blend (Ton, 12:07)
    msg1 = err(call("recover_file", mode="BOGUS"))
    assert "LAST_SESSION" in msg1 or "AUTOSAVE" in msg1, "bogus mode error must list the modes: %s" % A(msg1)
    msg2 = err(call("recover_file", mode="AUTOSAVE", filepath=os.path.join(B0DIR, "no_such_autosave.blend"), force=True))
    assert "not found" in msg2.lower() or "missing" in msg2.lower() or "exist" in msg2.lower(), A(msg2)
    assert len(bpy.context.scene.objects) == 0, "a refused recover must not change the file"
    ok(call("load_blend", filepath=A_BLEND, force=True))
    return "new_file dirty guard; recover_file BOGUS -> %s; AUTOSAVE missing file -> %s" % (A(msg1)[:50], A(msg2)[:50])


step("B0-26 new_file guard + recover_file graceful", b26, tag="B0")


def b27():
    # every B0 addon command the doc names must be in the dispatch table
    wanted = ["get_file_state", "new_file", "revert_file", "recover_file", "save_copy", "save_version", "list_versions",
              "append_from_blend", "link_from_blend", "set_autosave", "make_paths_relative", "make_paths_absolute",
              "find_missing_files", "pack_all", "unpack_all", "describe_settings", "get_settings", "set_settings",
              "settings_snapshot", "settings_restore", "list_settings_snapshots", "delete_settings_snapshot",
              "set_output_settings", "set_color_management", "set_render_quality", "set_render_device", "list_render_devices",
              "set_simplify", "set_frame_range", "set_scene_units", "set_viewport_defaults", "apply_blender_preset",
              "list_blender_presets", "set_project_profile", "get_project_profile", "list_addons", "enable_addon",
              "disable_addon", "get_addon_preferences", "set_addon_preferences", "save_preferences", "list_workspaces",
              "set_workspace", "get_addon_settings", "set_addon_settings", "get_version", "ensure_server_running",
              "get_session_state", "apply_session_state"]
    table = srv._build_handlers()
    missing = [w for w in wanted if w not in table]
    assert not missing, "B0 handlers missing from the dispatch table: %s" % missing
    return "%d B0 handlers present" % len(wanted)


step("B0-27 every B0 addon command is dispatchable", b27, readonly=True, tag="B0")
