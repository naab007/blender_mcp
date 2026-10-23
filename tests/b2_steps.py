# Phase B2 (engine readiness, 2.4.0) headless steps. Executed inside tests/headless_handlers.py when it is run with
# `-- --b2`; uses that file's helpers (step, call, ok, err, close, clear_scene, make_cube, snapshot, WORK, addon, srv,
# HANDLERS, P, A, VER). Written from docs/PLAN.md section 13 (B2.2 b908da48: 13.2.1 fixes, 13.2.2-13.2.6 tools, H1-H22) and
# docs/TEST-PLAN-B2.md; expected numbers cite the B2CHECK facts in docs/apichecks/out_<ver>.jsonl.
# Fixtures are built with bpy.data / bpy.ops primitives, never with the handler under test; Blender truth is read
# with bpy after every mutating call. ASCII only (cp1252 console).
# Landing map (R-B2-13): T1 = steps for L1 (B2-0 debt #13 migration, 13.2.2 fixes, 13.2.3 validation);
# T2 = L2 (13.2.3 + 13.2.4); T3 = L3 (13.2.5 + 13.2.6). The harness exec-loads this file: never edit it while a run
# is in flight.
# A B2 handler that is not in HANDLERS yet (its landing has not happened) makes every step of that landing FAIL with
# "Unknown command type": that is the intended red-before-green, never a skip.
import json
import math
import os

import bmesh
import bpy
import mathutils

B2DIR = os.path.join(WORK, "b2")
os.makedirs(B2DIR, exist_ok=True)
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SECRET_KEYS = ("blendermcp_hyper3d_api_key", "blendermcp_sketchfab_api_key",
               "blendermcp_hunyuan3d_secret_id", "blendermcp_hunyuan3d_secret_key")


def _userpref_mtime():
    p = os.path.join(bpy.utils.user_resource("CONFIG"), "userpref.blend")
    return os.path.getmtime(p) if os.path.exists(p) else None


def _enc_ok(value, path="reply"):
    """No bpy structs anywhere in a reply (K5)."""
    if isinstance(value, dict):
        for k, v in value.items():
            _enc_ok(v, "%s.%s" % (path, k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _enc_ok(v, "%s[%d]" % (path, i))
    else:
        assert value is None or isinstance(value, (str, int, float, bool)), "%s carries %r" % (path, type(value))
        if isinstance(value, float):
            assert math.isfinite(value), "%s is not finite" % path


def _world_verts(ob):
    return [tuple(round(c, 5) for c in (ob.matrix_world @ v.co)) for v in ob.data.vertices]


def _eval_tris(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me = ob.evaluated_get(dg).data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def _prim(op, name, **kw):
    """bpy primitive -> object named `name` (fixture, not the handler under test)."""
    getattr(bpy.ops.mesh, op)(**kw)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.name = name
    return ob


def _open_cube(name="Open"):
    ob = make_cube(name, 2.0, (0, 0, 0))
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    return ob


def _dirty_cube(name="Dirty"):
    """cube + 1 coincident vertex pair (8 doubles 6, both on faces) + 1 loose vertex (9) + 1 zero-area collinear face."""
    h = 1.0
    verts = [(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h), (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h),
             (h + 1e-6, h, h),        # 8: double of 6
             (5.0, 5.0, 5.0),         # 9: loose
             (0.0, 0.0, 3.0), (1.0, 0.0, 3.0), (2.0, 0.0, 3.0)]   # 10-12: degenerate (collinear, zero-area) triangle
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 8, 5), (2, 3, 7, 6), (3, 0, 4, 7), (10, 11, 12)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob


def _flip_one_face(ob, index=0):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    bm.faces[index].normal_flip()
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def _check(r, obj, cid):
    checks = r["objects"][obj]["checks"]
    assert cid in checks, "check id %r missing for %s; present: %s" % (cid, obj, sorted(checks))
    c = checks[cid]
    for k in ("status", "detail", "count", "sample_indices"):
        assert k in c, "check %s lacks %r: %s" % (cid, k, A(json.dumps(c)))
    assert c["status"] in ("PASS", "WARN", "FAIL"), c
    assert isinstance(c["count"], int) and isinstance(c["sample_indices"], list) and len(c["sample_indices"]) <= 20, c
    return c


CHECK_IDS = """unapplied_transform negative_scale origin_bottom_center dimensions ngons non_manifold loose degenerate
doubles inconsistent_normals missing_uv uv_out_of_bounds missing_uv2 vertex_count_16bit triangle_budget material_slots
materials_without_nodes images_missing bone_influences illegal_names name_collisions hierarchy""".split()
assert len(CHECK_IDS) == 22


def _validate(objects, **kw):
    r = ok(call("validate_asset", objects=objects, **kw), must=["profile", "verdict", "summary", "objects",
                                                                 "fixes_available", "fixes_applied"])
    _enc_ok(r)
    assert r["verdict"] in ("PASS", "WARN", "FAIL"), r["verdict"]
    for k in ("pass", "warn", "fail"):
        assert isinstance(r["summary"][k], int), r["summary"]
    for name, entry in r["objects"].items():
        assert entry["verdict"] in ("PASS", "WARN", "FAIL"), (name, entry.get("verdict"))
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.type == "MESH":
            missing = [c for c in CHECK_IDS if c not in entry["checks"]]
            assert not missing, "%s lacks check ids %s" % (name, missing)
    return r


clear_scene()
bpy.context.scene.frame_set(1)


# =========================================================================
# B2-0 debt #13: legacy Scene secrets migration from real 1.6.0 / 5.x files (H15, R-B2-11, R-B2-18)
# =========================================================================
def _sys_group(sc, create=False):
    f = getattr(sc, "bl_system_properties_get", None)
    return f(do_create=create) if f else None


def _secret_prefs(prefs):
    return {k: getattr(prefs, k) for k in ("hyper3d_api_key", "sketchfab_api_key", "hunyuan3d_secret_id", "hunyuan3d_secret_key")}


def _clear_secret_prefs(prefs):
    for k in ("hyper3d_api_key", "sketchfab_api_key", "hunyuan3d_secret_id", "hunyuan3d_secret_key"):
        setattr(prefs, k, "")


EXPECTED_SECRETS = {"hyper3d_api_key": "FIXTURE-HYPER3D-KEY", "sketchfab_api_key": "FIXTURE-SKETCHFAB-KEY",
                    "hunyuan3d_secret_id": "FIXTURE-HUNYUAN-ID", "hunyuan3d_secret_key": "FIXTURE-HUNYUAN-KEY"}


def _assert_clean(sc, fname):
    group = _sys_group(sc)
    for k in SECRET_KEYS:
        assert sc.get(k) is None, "moved, not copied: scene.get(%s) still %r" % (k, sc.get(k))
        if group is not None:
            assert k not in group.keys(), "moved, not copied: %s still in the 5.x system group" % k
    if group is not None and fname.startswith("legacy_secrets_5x"):
        assert "cycles" in group.keys(), "migration cleared foreign keys from the system group"


def b2_0_fixture(fname):
    """Real-user path: open the fixture with prefs real -> load_post migrates; then the helper on hand-written idprops."""
    path = os.path.join(FIXTURES, fname)
    assert os.path.exists(path), "fixture missing: %s" % path
    prefs = addon._prefs()
    assert prefs is not None, "_prefs() None with the addons entry present"
    _clear_secret_prefs(prefs)
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    except RuntimeError as e:
        # R-B2-18: a 5.x file cannot be opened by 4.3.2 ("incomplete header, may be from a newer version"); the
        # gate is the exception, never bpy.app.version
        assert "newer" in str(e) or "header" in str(e), "unexpected open error: %s" % A(e)
        return "SKIPPED-BY-INSTALL: %s cannot be opened here (%s)" % (fname, A(str(e))[:80])
    sc = bpy.context.scene
    assert not any(k in sc.bl_rna.properties.keys() for k in SECRET_KEYS), \
        "H15: legacy Scene StringProperties still registered at 2.4.0"
    # (a) load_post already migrated (the 2.1 _on_load_post handler, now reading scene.get / the system group)
    got = _secret_prefs(prefs)
    assert got == EXPECTED_SECRETS, "load_post migration: prefs hold %s" % A(got)
    _assert_clean(sc, fname)
    assert not hasattr(prefs, "blendermcp_port") and not hasattr(prefs, "blendermcp_server_running"), "idprops leaked into prefs"
    assert prefs.port != 9999, "legacy Scene port overwrote the preference"
    # (b) prefs already filled: a second open must not overwrite them (2.1 rule: only where the prefs value is empty)
    for k in EXPECTED_SECRETS:
        setattr(prefs, k, "KEEP-" + k)
    bpy.ops.wm.open_mainfile(filepath=path)
    sc = bpy.context.scene
    assert all(v == "KEEP-" + k for k, v in _secret_prefs(prefs).items()), "second open overwrote filled prefs: %s" % A(_secret_prefs(prefs))
    _assert_clean(sc, fname)                      # the orphaned keys are deleted even when prefs win (not saved again)
    # (c) the helper itself on hand-written idprops in BOTH storages (what a mixed 4.x/5.x history leaves behind)
    _clear_secret_prefs(prefs)
    sc["blendermcp_hyper3d_api_key"] = "RAW-H"
    sc["blendermcp_port"] = 9999
    group = _sys_group(sc, create=True)
    if group is not None:
        group["blendermcp_sketchfab_api_key"] = "SYS-S"
        if "cycles" not in group.keys():
            group["cycles"] = {"keep": 1}
    migrated = addon._migrate_legacy_secrets(prefs, sc)
    assert "hyper3d_api_key" in migrated and prefs.hyper3d_api_key == "RAW-H", (migrated, prefs.hyper3d_api_key)
    if group is not None:
        assert "sketchfab_api_key" in migrated and prefs.sketchfab_api_key == "SYS-S", (migrated, prefs.sketchfab_api_key)
    assert sc.get("blendermcp_hyper3d_api_key") is None and sc.get("blendermcp_port") == 9999, "port idprop must be left alone / key deleted"
    group = _sys_group(sc)
    if group is not None:
        assert "blendermcp_sketchfab_api_key" not in group.keys() and "cycles" in group.keys(), sorted(group.keys())
    again = addon._migrate_legacy_secrets(prefs, sc)
    assert again == [], again
    _clear_secret_prefs(prefs)
    del sc["blendermcp_port"]
    return "%s: load_post moved 4 secrets into prefs (scene.get None + system group clean), prefs win on re-open, helper moves raw + system-group idprops, port never read" % fname


def b2_0():
    entry = bpy.context.preferences.addons.new()
    entry.module = "addon"
    mt = _userpref_mtime()
    det = []
    try:
        for fname in ("legacy_secrets_1.6.0.blend", "legacy_secrets_5x.blend"):
            det.append(b2_0_fixture(fname))
    finally:
        try:
            p = addon._prefs()
            if p is not None:
                _clear_secret_prefs(p)
        except Exception:
            pass
        bpy.context.preferences.addons.remove(entry)
        bpy.ops.wm.read_homefile(use_empty=True)
        clear_scene()
        bpy.context.scene.frame_set(1)
    assert _userpref_mtime() == mt, "userpref.blend written by the migration"
    return " | ".join(det)


step("B2-0 legacy secrets migrate from 1.6.0 and 5.x fixtures (H15)", b2_0, tag="B2")


# =========================================================================
# 13.2.1 fixes (L1): get_mesh_stats, get_scene_info, apply_modifier, set_origin, export_object, import_file
# =========================================================================
def b2_f3():
    clear_scene()
    cube = make_cube("Cube", 2.0, (0, 0, 0))
    r = ok(call("get_mesh_stats", name="Cube"), must=["triangles", "triangles_simple", "ngons", "quads", "tris", "loose_verts",
                                                       "loose_edges", "non_manifold_edges", "has_custom_normals", "uv_layers",
                                                       "color_attributes", "material_slots", "dimensions",
                                                       "unapplied_transform", "origin_offset"])
    _enc_ok(r)
    assert r["triangles"] == 12 and r["triangles_simple"] == 12 and r["quads"] == 6 and r["tris"] == 0 and r["ngons"] == 0, r
    assert r["loose_verts"] == 0 and r["loose_edges"] == 0 and r["non_manifold_edges"] == 0, r
    assert r["has_custom_normals"] is False and r["uv_layers"] == [] and r["color_attributes"] == [] and r["material_slots"] == [], r
    assert close(r["dimensions"], (2, 2, 2)), r["dimensions"]
    assert r["unapplied_transform"] == {"rotation": False, "scale": False, "negative_scale": False}, r["unapplied_transform"]
    assert close(r["origin_offset"]["to_bbox_center"], (0, 0, 0)) and abs(abs(r["origin_offset"]["to_bbox_bottom"][2]) - 1.0) < 1e-5, r["origin_offset"]
    # 2.3 keys kept
    assert r["vertices"] == 8 and r["polygons"] == 6 and r["edges"] == 12 and r["modifiers"] == [] and "bounding_box" in r, r
    # Prop: scaled + rotated + negative
    cube.scale = (2, 1, -1)
    cube.rotation_euler = (0, 0, math.radians(45))
    bpy.context.view_layer.update()
    r = ok(call("get_mesh_stats", name="Cube"))
    assert r["unapplied_transform"] == {"rotation": True, "scale": True, "negative_scale": True}, r["unapplied_transform"]
    cube.scale = (1, 1, 1); cube.rotation_euler = (0, 0, 0)
    # Sub: evaluated triangles (E13: 6 polys + subsurf 2 -> 192 tris on both)
    m = cube.modifiers.new("Sub", "SUBSURF"); m.levels = 2
    bpy.context.view_layer.update()
    r = ok(call("get_mesh_stats", name="Cube"))
    assert r["triangles"] == 192 and r["triangles_simple"] == 12 and r["polygons"] == 6, (r["triangles"], r["triangles_simple"])
    cube.modifiers.remove(m)
    # Cyl: ngon caps; Open: non-manifold; Dirty: loose + doubles
    _prim("primitive_cylinder_add", "Cyl", vertices=12, end_fill_type="NGON")
    r = ok(call("get_mesh_stats", name="Cyl"))
    assert r["ngons"] == 2 and r["quads"] == 12 and r["tris"] == 0 and r["triangles"] == 2 * 12 + 2 * 10, r
    assert r["uv_layers"] == ["UVMap"], r["uv_layers"]
    _open_cube("Open")
    r = ok(call("get_mesh_stats", name="Open"), must=["boundary_edges"])
    assert r["non_manifold_edges"] == 4 and r["boundary_edges"] == 4, (r["non_manifold_edges"], r["boundary_edges"])   # R-B2-29
    _dirty_cube("Dirty")
    r = ok(call("get_mesh_stats", name="Dirty"))
    assert r["loose_verts"] == 1, r["loose_verts"]
    # material slots + colour attribute
    mat = bpy.data.materials.new("M1")
    cube.data.materials.append(mat)
    cube.data.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    r = ok(call("get_mesh_stats", name="Cube"))
    assert r["material_slots"] == [{"index": 0, "material": "M1"}] and r["color_attributes"] == ["Col"], r
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    err(call("get_mesh_stats", name="Lamp"), "not a mesh")
    err(call("get_mesh_stats", name="Nope"), "not found")
    return "cube 12/12 tris, subsurf 192 evaluated vs 12 simple, Cyl 2 ngons, Open 4 non-manifold, Dirty 1 loose, slots/colours listed"


step("B2-F3 get_mesh_stats engine counts (13.2.1, E13)", b2_f3, tag="B2")


def b2_f4():
    if "Lamp" not in bpy.data.objects:
        bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    for n in ("Cube", "Cyl"):
        assert n in bpy.data.objects, "F3 fixture %s missing (F3 failed early)" % n
    r = ok(call("get_scene_info"), must=["total_triangles", "total_vertices", "objects"])
    _enc_ok(r)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert r["total_triangles"] == sum(_eval_tris(o) for o in meshes), (r["total_triangles"], [(_eval_tris(o), o.name) for o in meshes])
    assert r["total_vertices"] == sum(len(o.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices) for o in meshes), r["total_vertices"]
    per = {o["name"]: o for o in r["objects"]}
    assert per["Cube"]["triangles"] == 12 and per["Cyl"]["triangles"] == 44, (per["Cube"].get("triangles"), per["Cyl"].get("triangles"))
    assert "Lamp" in per and per["Lamp"].get("triangles", 0) in (0, None), per.get("Lamp")
    assert "hidden" in per["Cube"] and per["Cube"]["hidden"] is False, per["Cube"]      # Lead ruling m_1f6f58ae (7)
    assert "parent" in per["Cube"] and per["Cube"]["parent"] is None, per["Cube"]           # R-B2-28
    assert isinstance(per["Cube"].get("collections"), list) and per["Cube"]["collections"], per["Cube"]      # R-B2-30
    kit = bpy.data.collections.new("Kit"); bpy.context.scene.collection.children.link(kit)
    kit.objects.link(bpy.data.objects["Cyl"]); bpy.context.scene.collection.objects.unlink(bpy.data.objects["Cyl"])
    rc = {o["name"]: o for o in ok(call("get_scene_info"))["objects"]}
    assert rc["Cyl"]["collections"] == ["Kit"], rc["Cyl"]
    kit.objects.unlink(bpy.data.objects["Cyl"]); bpy.context.scene.collection.objects.link(bpy.data.objects["Cyl"])
    bpy.data.collections.remove(kit)
    bpy.data.objects["Cyl"].parent = bpy.data.objects["Cube"]; bpy.data.objects["Cyl"].hide_viewport = True
    rp = {o["name"]: o for o in ok(call("get_scene_info"))["objects"]}
    assert rp["Cyl"]["parent"] == "Cube" and rp["Cyl"]["hidden"] is True and rp["Cube"]["parent"] is None, (rp["Cyl"], rp["Cube"])
    bpy.data.objects["Cyl"].parent = None; bpy.data.objects["Cyl"].hide_viewport = False
    units = r["settings_summary"]["units"]
    assert set(units) >= {"system", "scale_length", "length_unit"}, units
    assert r["total_triangles"] == sum(o["triangles"] for o in r["objects"] if o["type"] == "MESH"), "totals != per-object sum"
    bpy.data.objects["Cube"].hide_viewport = True
    r2 = ok(call("get_scene_info"))
    assert {o["name"]: o for o in r2["objects"]}["Cube"]["hidden"] is True
    bpy.data.objects["Cube"].hide_viewport = False
    clear_scene()
    r0 = ok(call("get_scene_info"))
    assert r0["total_triangles"] == 0 and r0["total_vertices"] == 0 and r0["objects"] == [], r0
    return "totals == evaluated sums (%d tris), per-object triangles + hidden, lamp 0, empty scene 0" % r["total_triangles"]


step("B2-F4 get_scene_info budgets + hidden (13.2.1)", b2_f4, tag="B2", readonly=False)


def b2_rs1():
    # RS-1 / R-B2-32: the object list is capped at 10 by default; limit=0 lists every object (batch_export relies on it)
    clear_scene()
    for i in range(15):
        make_cube("R%02d" % i, 1.0, (i * 2.0, 0, 0))
    r = ok(call("get_scene_info"))
    assert r["object_count"] == 15 and len(r["objects"]) == 10, (r["object_count"], len(r["objects"]))
    r = ok(call("get_scene_info", limit=0))
    assert len(r["objects"]) == 15 and {o["name"] for o in r["objects"]} == {"R%02d" % i for i in range(15)}, len(r["objects"])
    r = ok(call("get_scene_info", limit=3))
    assert len(r["objects"]) == 3 and r["object_count"] == 15, len(r["objects"])
    err(call("get_scene_info", limit="x"))
    clear_scene()
    return "15 objects: default lists 10 of 15, limit=0 lists 15, limit=3 lists 3, non-int refused"


step("B2-RS1 get_scene_info limit (R-B2-32)", b2_rs1, tag="B2")


def b2_f5():
    clear_scene()
    cube = make_cube("Mod", 2.0, (0, 0, 0))
    bpy.ops.object.armature_add(); arm = bpy.context.active_object; arm.name = "Arm"
    cube.modifiers.new("Sub", "SUBSURF").levels = 1
    cube.modifiers.new("Bev", "BEVEL")
    am = cube.modifiers.new("Armature", "ARMATURE"); am.object = arm
    r = ok(call("apply_modifier", name="Mod", apply_all=True, keep="ARMATURE"), must=["applied", "kept", "failed"])
    _enc_ok(r)
    assert r["applied"] == ["Sub", "Bev"] and r["kept"] == ["Armature"] and r["failed"] == {}, r
    assert [m.name for m in cube.modifiers] == ["Armature"] and len(cube.data.polygons) > 6, ([m.name for m in cube.modifiers], len(cube.data.polygons))
    # keep by NAME, empty list, old shape, failure does not stop the rest
    cube.modifiers.new("Bev2", "BEVEL")
    r = ok(call("apply_modifier", name="Mod", apply_all=True, keep="Armature"))
    assert r["applied"] == ["Bev2"] and r["kept"] == ["Armature"], r
    r = ok(call("apply_modifier", name="Mod", apply_all=True, keep="Armature,NoSuch"))
    assert r["applied"] == [] and r["kept"] == ["Armature"], r
    cube.modifiers.new("Bev3", "BEVEL")
    r = ok(call("apply_modifier", name="Mod", modifier_name="Bev3"))
    assert r.get("applied_modifier") == "Bev3" and "Bev3" not in cube.modifiers, r
    err(call("apply_modifier", name="Mod"), "modifier_name")
    err(call("apply_modifier", name="Mod", modifier_name="Nope"), "not found")
    err(call("apply_modifier", name="Nope", apply_all=True), "not found")
    # a modifier that cannot apply (Armature with no bones is fine, so use a Boolean with no object -> applies as no-op;
    # use a non-mesh: apply_all on the armature object with no modifiers = success, applied [])
    r = ok(call("apply_modifier", name="Arm", apply_all=True))
    assert r["applied"] == [] and r["kept"] == [] and r["failed"] == {}, r
    return "apply_all kept ARMATURE by type and by name, 2.3 shape intact, empty = []"


step("B2-F5 apply_modifier apply_all + keep (13.2.1)", b2_f5, tag="B2")


def b2_f6():
    clear_scene()
    cube = make_cube("Org", 2.0, (0, 0, 0))
    wv = _world_verts(cube)
    r = ok(call("set_origin", name="Org", origin_type="BOTTOM_CENTER"), must=["offset_applied"])
    assert r.get("origin_type") == "BOTTOM_CENTER" and "offset_local" not in r, r          # F-B2-1: contract key wins
    bpy.context.view_layer.update()
    assert abs(min(v.co.z for v in cube.data.vertices)) < 1e-6 and abs(cube.location.z + 1.0) < 1e-6, (min(v.co.z for v in cube.data.vertices), tuple(cube.location))
    assert _world_verts(cube) == wv, "world geometry moved"
    ok(call("set_origin", name="Org", origin_type="TOP_CENTER"))
    bpy.context.view_layer.update()
    assert abs(max(v.co.z for v in cube.data.vertices)) < 1e-6 and abs(cube.location.z - 1.0) < 1e-6, tuple(cube.location)
    assert _world_verts(cube) == wv
    ok(call("set_origin", name="Org", origin_type="CUSTOM", location="1,2,3"))
    bpy.context.view_layer.update()
    assert close(cube.location, (1, 2, 3)) and _world_verts(cube) == wv, tuple(cube.location)
    # rotated + scaled object: the offset must go through the object matrix (world geometry fixed)
    cube.rotation_euler = (0, 0, math.radians(30)); cube.scale = (2, 1, 1)
    bpy.context.view_layer.update()
    wv2 = _world_verts(cube)
    ok(call("set_origin", name="Org", origin_type="BOTTOM_CENTER"))
    bpy.context.view_layer.update()
    assert _world_verts(cube) == wv2, "rotated/scaled: world geometry moved"
    assert abs(min(v.co.z for v in cube.data.vertices)) < 1e-6
    err(call("set_origin", name="Org", origin_type="CUSTOM"), "location")
    err(call("set_origin", name="Org", origin_type="CUSTOM", location="a,b"), "location")
    err(call("set_origin", name="Org", origin_type="SIDEWAYS"))
    # 2.3 shape intact
    ok(call("set_origin", name="Org", origin_type="ORIGIN_GEOMETRY"))
    cube.rotation_euler = (0, 0, 0); cube.scale = (1, 1, 1)
    return "BOTTOM/TOP/CUSTOM keep world geometry (also rotated+scaled), errors teach"


step("B2-F6 set_origin BOTTOM_CENTER / TOP_CENTER / CUSTOM (13.2.1, E13)", b2_f6, tag="B2")


def _glb_json(path):
    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:4] == b"glTF", "not a GLB"
    import struct
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + length].decode("utf-8"))


def b2_f1():
    clear_scene()
    _prim("primitive_monkey_add", "Suz")
    cube = make_cube("Prop", 2.0, (3, 0, 0))
    cube.scale = (2, 1, 1)
    bpy.ops.object.empty_add(); emp = bpy.context.active_object; emp.name = "SuzChild"; emp.parent = bpy.data.objects["Suz"]
    sub = make_cube("Sub", 2.0, (-3, 0, 0)); sub.modifiers.new("Sub", "SUBSURF").levels = 2
    bpy.context.view_layer.update()
    p = os.path.join(B2DIR, "f1.fbx")
    r = ok(call("export_object", objects="Suz,Prop", file_format="fbx", filepath=p),
           must=["size_bytes", "object_count", "vertices_written", "triangles_written", "warnings", "transform_baked"])
    _enc_ok(r)
    assert os.path.exists(p) and r["size_bytes"] == os.path.getsize(p) > 0, r
    assert r["object_count"] == 3, r["object_count"]            # Suz + its child empty (include_hierarchy default) + Prop
    assert r["triangles_written"] == _eval_tris(bpy.data.objects["Suz"]) + 12, (r["triangles_written"], _eval_tris(bpy.data.objects["Suz"]))
    assert r["vertices_written"] == len(bpy.data.objects["Suz"].data.vertices) + 8, r["vertices_written"]
    assert isinstance(r["warnings"], list) and any("Prop" in w and "scale" in w.lower() for w in r["warnings"]), r["warnings"]
    assert r["transform_baked"] is False, r
    # 2.3 keys kept
    assert r["filepath"] == p and r["format"] == "fbx" and isinstance(r["exported_objects"], list), r
    r = ok(call("export_object", objects="Suz,Prop", include_hierarchy=False, file_format="fbx", filepath=p))
    assert r["object_count"] == 2, r["object_count"]
    err(call("export_object", name="Suz", objects="Prop", file_format="fbx", filepath=p))
    # evaluated vs simple
    p2 = os.path.join(B2DIR, "f1_sub.glb")
    r = ok(call("export_object", name="Sub", file_format="glb", filepath=p2, apply_modifiers=False))
    assert r["triangles_written"] == 12, r["triangles_written"]
    r = ok(call("export_object", name="Sub", file_format="glb", filepath=p2, apply_modifiers=True))
    assert r["triangles_written"] == 192, r["triangles_written"]
    # warnings: ngons when use_triangles is off, missing UVs, material without nodes
    _prim("primitive_cylinder_add", "Cyl", vertices=12, end_fill_type="NGON")
    p3 = os.path.join(B2DIR, "f1_cyl.fbx")
    r = ok(call("export_object", name="Cyl", file_format="fbx", filepath=p3, use_triangles=False))
    assert any("ngon" in w.lower() for w in r["warnings"]), r["warnings"]
    r = ok(call("export_object", name="Cyl", file_format="fbx", filepath=p3, use_triangles=True))
    assert not any("ngon" in w.lower() for w in r["warnings"]), r["warnings"]
    r = ok(call("export_object", name="Prop", file_format="fbx", filepath=p3))           # make_cube has no UV layer
    assert any("uv" in w.lower() for w in r["warnings"]), r["warnings"]
    mat = bpy.data.materials.new("NoNodes"); mat.use_nodes = False
    bpy.data.objects["Prop"].data.materials.append(mat)
    r = ok(call("export_object", name="Prop", file_format="fbx", filepath=p3))
    nodeless = not (mat.use_nodes and mat.node_tree is not None and len(mat.node_tree.nodes) > 0)
    assert any("NoNodes" in w for w in r["warnings"]) is nodeless, (r["warnings"], nodeless)
    # enum validation against the live operator enum (D9 idiom), nothing written
    bad = os.path.join(B2DIR, "f1_bad.fbx")
    msg = err(call("export_object", name="Suz", file_format="fbx", filepath=bad, axis_up="Q"), "axis_up")
    assert not os.path.exists(bad), "file written despite a refused param"
    assert "Z" in msg and "-Z" in msg, "valid values not listed: %s" % A(msg)
    msg = err(call("export_object", name="Suz", file_format="glb", filepath=os.path.join(B2DIR, "f1_bad.glb"), export_vertex_color="BOGUS"), "export_vertex_color")
    assert "MATERIAL" in msg, A(msg)
    # E8: mesh_smooth_type SMOOTH_GROUP is 5.2.1-only; the branch is keyed on the live enum, never on bpy.app.version
    sg = os.path.join(B2DIR, "f1_sg.fbx")
    rr = call("export_object", name="Suz", file_format="fbx", filepath=sg, mesh_smooth_type="SMOOTH_GROUP")
    live = [i.identifier for i in bpy.ops.export_scene.fbx.get_rna_type().properties["mesh_smooth_type"].enum_items]
    if "SMOOTH_GROUP" in live:
        ok(rr); assert os.path.exists(sg)
        sg_note = "SMOOTH_GROUP exported (live enum has it)"
    else:
        m = err(rr, "mesh_smooth_type"); assert "FACE" in m and not os.path.exists(sg), A(m)
        sg_note = "SMOOTH_GROUP refused with the live enum (%s)" % live
    # glTF kwargs reach the exporter: tangents present in the GLB only when asked
    g1 = os.path.join(B2DIR, "f1_t.glb")
    ok(call("export_object", name="Suz", file_format="glb", filepath=g1, export_tangents=True))
    attrs = [set(pr["attributes"]) for m in _glb_json(g1)["meshes"] for pr in m["primitives"]]
    assert any("TANGENT" in a for a in attrs), attrs
    ok(call("export_object", name="Suz", file_format="glb", filepath=g1, export_tangents=False))
    attrs = [set(pr["attributes"]) for m in _glb_json(g1)["meshes"] for pr in m["primitives"]]
    assert not any("TANGENT" in a for a in attrs), attrs
    # E10: OBJ bakes the transform on 4.x (no apply_transform kwarg); keyed on the live operator, not the version
    o = os.path.join(B2DIR, "f1.obj")
    r = ok(call("export_object", name="Prop", file_format="obj", filepath=o))
    # truth = the file: Prop sits at x=3 with scale 2, so baked vertices have |x| > 1.5, local ones |x| <= 1
    xs = [abs(float(l.split()[1])) for l in open(o, encoding="utf-8") if l.startswith("v ")]
    baked_in_file = max(xs) > 1.5
    assert isinstance(r["transform_baked"], bool) and r["transform_baked"] is baked_in_file, (r["transform_baked"], max(xs))
    return "objects list + hierarchy counted, evaluated tris 192/12, 4 warning kinds, live-enum refusals with no file, %s, TANGENT on/off, obj transform_baked=%s" % (sg_note, r["transform_baked"])


step("B2-F1 export_object engine params + report (13.2.1, E8/E10)", b2_f1, tag="B2")


def b2_f2():
    # fixtures written with the bpy exporters directly (import_file is the tool under test, export_object is not)
    clear_scene()
    _prim("primitive_monkey_add", "Suz")
    cube = make_cube("Prop", 2.0, (3, 0, 0)); cube.scale = (2, 1, 1)
    bpy.context.view_layer.update()
    g1 = os.path.join(B2DIR, "f2_suz.glb")
    bpy.ops.object.select_all(action="DESELECT"); bpy.data.objects["Suz"].select_set(True)
    bpy.ops.export_scene.gltf(filepath=g1, export_format="GLB", use_selection=True)
    f1 = os.path.join(B2DIR, "f2_prop.fbx")
    bpy.ops.object.select_all(action="DESELECT"); cube.select_set(True)
    bpy.ops.export_scene.fbx(filepath=f1, use_selection=True)
    clear_scene()
    r = ok(call("import_file", filepath=g1, import_shading="FLAT"), must=["objects", "imported_objects", "armatures"])
    _enc_ok(r)
    assert len(r["objects"]) == 1, r["objects"]
    o = r["objects"][0]
    for k in ("name", "type", "vertices", "triangles", "has_custom_normals", "uv_layers", "materials"):
        assert k in o, (k, o)
    ob = bpy.data.objects[o["name"]]
    assert o["type"] == "MESH" and o["vertices"] == len(ob.data.vertices) and o["triangles"] == _eval_tris(ob), o
    assert o["uv_layers"] == [l.name for l in ob.data.uv_layers] and isinstance(o["has_custom_normals"], bool), o
    assert all(not p.use_smooth for p in ob.data.polygons), "import_shading=FLAT not applied"
    assert r["armatures"] == [], r["armatures"]
    clear_scene()
    r = ok(call("import_file", filepath=g1, import_shading="SMOOTH"))
    ob = bpy.data.objects[r["objects"][0]["name"]]
    assert all(p.use_smooth for p in ob.data.polygons), "import_shading=SMOOTH not applied"
    # FBX: global_scale + axes accepted, counts per object, armatures listed
    clear_scene()
    r = ok(call("import_file", filepath=f1, global_scale=2.0))
    names = {o["name"]: o for o in r["objects"]}
    prop = next(o for n, o in names.items() if n.startswith("Prop"))
    bpy.context.view_layer.update()
    dims = bpy.data.objects[prop["name"]].dimensions
    assert all(abs(d - e) < 1e-2 for d, e in zip(dims, (8, 4, 4))), tuple(dims)      # 2x of the scaled (4,2,2) cube
    assert prop["triangles"] == 12 and prop["vertices"] == 8, prop
    # armature: rigged glb -> armatures named, no Icosphere (E9/K8)
    clear_scene()
    bpy.ops.object.armature_add(); arm = bpy.context.active_object; arm.name = "Rig"
    cube = make_cube("Skin", 2.0, (0, 0, 0)); cube.modifiers.new("Armature", "ARMATURE").object = arm
    cube.vertex_groups.new(name="Bone"); cube.vertex_groups["Bone"].add(list(range(8)), 1.0, "REPLACE")
    rig = os.path.join(B2DIR, "f2_rig.glb")
    ok(call("export_object", objects="Skin,Rig", file_format="glb", filepath=rig))
    clear_scene()
    r = ok(call("import_file", filepath=rig))
    assert len(r["armatures"]) == 1, r["armatures"]
    assert not any(o.name.startswith("Icosphere") for o in bpy.data.objects), [o.name for o in bpy.data.objects]
    # refusals: bad enum lists the valid values and imports nothing
    clear_scene()
    msg = err(call("import_file", filepath=g1, import_shading="BOGUS"), "import_shading")
    assert "NORMALS" in msg and "FLAT" in msg, A(msg)
    assert len(bpy.data.objects) == 0, "objects imported despite a refused param"
    # a format-foreign kwarg never becomes a TypeError traceback
    rr = call("import_file", filepath=g1, ignore_leaf_bones=True)
    assert rr.get("status") == "success", A(rr)
    if isinstance(rr.get("result"), dict) and "error" in rr["result"]:
        assert "Traceback" not in rr["result"]["error"] and "TypeError" not in rr["result"]["error"], A(rr["result"]["error"])
    clear_scene()
    return "objects report (counts, uv_layers, custom normals), FLAT/SMOOTH shading applied, global_scale 2.0 -> dims x2, armatures listed, no Icosphere, enum refusal imports nothing"


step("B2-F2 import_file axis/scale/shading + report (13.2.1, E9)", b2_f2, tag="B2")


# =========================================================================
# 13.2.2 validation (L1): validate_asset, find_mesh_issues, compare_meshes, apply_transforms, fix_normals, cleanup_mesh
# =========================================================================
def b2_v1():
    clear_scene()
    prop = make_cube("Prop", 2.0, (0, 0, 0))
    prop.data.uv_layers.new(name="UVMap")
    prop.scale = (2, 1, 1); prop.rotation_euler = (0, 0, math.radians(45))
    bpy.context.view_layer.update()
    wv = _world_verts(prop)
    snap_before = snapshot()
    r = _validate("Prop")
    assert snapshot() == snap_before, "validate_asset(fix=False) changed state: %s" % A({k: (snap_before[k], snapshot()[k]) for k in snap_before if snap_before[k] != snapshot()[k]})
    assert r["profile"] == "GENERIC" and r["verdict"] == "FAIL" and r["objects"]["Prop"]["verdict"] == "FAIL", (r["profile"], r["verdict"])
    assert _check(r, "Prop", "unapplied_transform")["status"] == "FAIL"
    assert _check(r, "Prop", "origin_bottom_center")["status"] == "WARN"
    assert _check(r, "Prop", "negative_scale")["status"] == "PASS"
    assert r["summary"] == {"pass": 0, "warn": 0, "fail": 1}, r["summary"]           # counts objects (ruling m_1f6f58ae 6)
    assert any(f["object"] == "Prop" and f["check_id"] == "unapplied_transform" for f in r["fixes_available"]), r["fixes_available"]
    assert r["fixes_applied"] == [], r["fixes_applied"]
    r = _validate("Prop", fix=True)
    assert any(f["object"] == "Prop" and f["check_id"] == "unapplied_transform" for f in r["fixes_applied"]), r["fixes_applied"]
    bpy.context.view_layer.update()
    assert close(prop.scale, (1, 1, 1)) and close(prop.rotation_euler, (0, 0, 0)), (tuple(prop.scale), tuple(prop.rotation_euler))
    assert _world_verts(prop) == wv, "fix moved the world geometry"
    assert r["verdict"] in ("PASS", "WARN") and _check(r, "Prop", "unapplied_transform")["status"] == "PASS", r["verdict"]
    r = _validate("Prop")
    assert _check(r, "Prop", "unapplied_transform")["status"] == "PASS" and r["objects"]["Prop"]["verdict"] in ("PASS", "WARN")
    # negative scale: FAIL before apply, flip corrected after fix (normals outward)
    prop.scale = (-1, 1, 1); bpy.context.view_layer.update()
    r = _validate("Prop")
    assert _check(r, "Prop", "negative_scale")["status"] == "FAIL"
    r = _validate("Prop", fix=True)
    bpy.context.view_layer.update()
    assert close(prop.scale, (1, 1, 1))
    me = prop.data
    centre = sum((v.co for v in me.vertices), mathutils.Vector()) / len(me.vertices)
    inward = [p.index for p in me.polygons if (p.normal.dot(p.center - centre)) < 0]
    assert not inward, "faces still inward after the negative-scale fix: %s" % inward
    assert _check(r, "Prop", "inconsistent_normals")["status"] == "PASS"
    # dimensions plausibility
    prop.scale = (60, 60, 60); bpy.context.view_layer.update()
    assert _check(_validate("Prop"), "Prop", "dimensions")["status"] == "WARN"
    prop.scale = (0.002, 0.002, 0.002); bpy.context.view_layer.update()      # max extent 0.85 cm < 1 cm (max-extent rule)
    assert max(prop.dimensions) < 0.01
    assert _check(_validate("Prop"), "Prop", "dimensions")["status"] == "WARN"
    prop.scale = (1, 1, 1); bpy.context.view_layer.update()
    assert _check(_validate("Prop"), "Prop", "dimensions")["status"] == "PASS"
    return "doc step 1: FAIL transforms + WARN origin, fix applied with world geometry fixed, re-run PASS; negative scale FAIL -> fixed outward; dimensions WARN 120 m / 0.85 cm"


step("B2-V1 validate_asset doc step 1 + negative scale + dimensions (13.2.2)", b2_v1, tag="B2")


def b2_v2():
    clear_scene()
    cyl = _prim("primitive_cylinder_add", "Cyl", vertices=12, end_fill_type="NGON")
    for prof in ("UNITY", "UNREAL", "GODOT", "BEVY"):
        c = _check(_validate("Cyl", profile=prof), "Cyl", "ngons")
        assert c["status"] == "FAIL" and c["count"] == 2 and len(c["sample_indices"]) == 2, (prof, c)
    c = _check(_validate("Cyl", profile="GENERIC"), "Cyl", "ngons")
    assert c["status"] == "WARN" and c["count"] == 2, c
    ngon_faces = sorted(p.index for p in cyl.data.polygons if len(p.vertices) > 4)
    assert sorted(c["sample_indices"]) == ngon_faces, (c["sample_indices"], ngon_faces)
    _open_cube("Open")
    c = _check(_validate("Open"), "Open", "non_manifold")
    assert c["status"] == "PASS" and c["count"] == 0 and "boundary: 4" in c["detail"], c     # R-B2-29: boundary is not non-manifold
    _prim("primitive_plane_add", "Plane")
    c = _check(_validate("Plane"), "Plane", "non_manifold")
    assert c["status"] == "PASS" and "boundary: 4" in c["detail"], c
    wire = make_cube("Wire", 2.0, (0, 6, 0))
    bmw = bmesh.new(); bmw.from_mesh(wire.data); bmw.verts.ensure_lookup_table()
    v1 = bmw.verts.new((3.0, 0.0, 0.0)); v2 = bmw.verts.new((4.0, 0.0, 0.0)); bmw.edges.new((v1, v2))
    bmw.to_mesh(wire.data); bmw.free(); wire.data.update()
    c = _check(_validate("Wire"), "Wire", "non_manifold")
    assert c["status"] == "WARN" and c["count"] == 1 and len(c["sample_indices"]) == 1, c          # a wire edge IS non-manifold
    tri = make_cube("Tri", 2.0, (0, 10, 0))
    bmt = bmesh.new(); bmt.from_mesh(tri.data); bmt.verts.ensure_lookup_table(); bmt.edges.ensure_lookup_table()
    e = bmt.edges[0]; apex = bmt.verts.new((e.verts[0].co + e.verts[1].co) / 2 + mathutils.Vector((0, 0, 3)))
    bmt.faces.new((e.verts[0], e.verts[1], apex)); bmt.to_mesh(tri.data); bmt.free(); tri.data.update()
    c = _check(_validate("Tri"), "Tri", "non_manifold")
    assert c["status"] == "WARN" and c["count"] >= 1, c                                               # a 3-face edge IS non-manifold
    _dirty_cube("Dirty")
    r = _validate("Dirty")
    assert _check(r, "Dirty", "loose")["status"] == "WARN" and _check(r, "Dirty", "loose")["count"] >= 1
    assert _check(r, "Dirty", "doubles")["status"] == "WARN" and _check(r, "Dirty", "doubles")["count"] >= 1
    assert _check(r, "Dirty", "degenerate")["status"] == "FAIL" and _check(r, "Dirty", "degenerate")["count"] >= 1
    flip = make_cube("Flip", 2.0, (5, 0, 0))
    _flip_one_face(flip, 0)
    c = _check(_validate("Flip"), "Flip", "inconsistent_normals")
    assert c["status"] == "FAIL" and c["count"] == 1 and c["sample_indices"] == [0], c
    # dry-run: validate must not have touched the mesh (13.5 risk: compare against a COPY)
    assert flip.data.polygons[0].normal.dot(flip.data.polygons[0].center) < 0, "validate recalculated the live normals"
    # fix=True: doubles merged, loose gone, degenerate dissolved, normals outward; Cyl ngons triangulated ONLY when tangents matter
    nv = len(bpy.data.objects["Dirty"].data.vertices)
    r = _validate("Dirty,Flip", fix=True)
    d = bpy.data.objects["Dirty"].data
    assert len(d.vertices) < nv, "no vertex removed by the doubles/loose fixes"
    assert all((p.normal.dot(p.center)) > 0 for p in flip.data.polygons), "flip not fixed"
    applied = sorted(f["check_id"] for f in r["fixes_applied"])
    assert "doubles" in applied and "loose" in applied and "inconsistent_normals" in applied, applied
    assert not any(f["check_id"] == "degenerate" for f in r["fixes_applied"]), "degenerate is not a safe fix (13.2.2)"
    r2 = _validate("Dirty,Flip")
    for cid in ("loose", "doubles", "inconsistent_normals"):
        for n in ("Dirty", "Flip"):
            assert _check(r2, n, cid)["status"] == "PASS", (n, cid, _check(r2, n, cid))
    assert _check(r2, "Dirty", "degenerate")["status"] == "FAIL", "degenerate must still be reported after the safe fixes"
    polys = len(cyl.data.polygons)
    r = _validate("Cyl", profile="GENERIC", fix=True)
    assert len(cyl.data.polygons) > polys and not any(len(p.vertices) > 4 for p in cyl.data.polygons), "fix did not triangulate the ngons"
    assert any(f["check_id"] == "ngons" for f in r["fixes_applied"]), r["fixes_applied"]
    assert all(len(p.vertices) <= 4 for p in cyl.data.polygons) and sum(1 for p in cyl.data.polygons if len(p.vertices) == 4) == 12, "quads were triangulated too"
    assert _check(_validate("Cyl", profile="UNITY"), "Cyl", "ngons")["status"] == "PASS"
    return "ngons FAIL x4 profiles / WARN generic with %d indices, open cube boundary PASS, wire WARN, loose/doubles/degenerate, inconsistent 1 (dry-run), fixes clean, ngon fix leaves %d quads" % (len(ngon_faces), sum(1 for p in cyl.data.polygons if len(p.vertices) == 4))


step("B2-V2 validate_asset topology checks + safe fixes (13.2.2, E5)", b2_v2, tag="B2")


def b2_v3():
    clear_scene()
    cube = make_cube("UV", 2.0, (0, 0, 0))
    assert _check(_validate("UV"), "UV", "missing_uv")["status"] == "FAIL"
    assert _check(_validate("UV", require_uv=False), "UV", "missing_uv")["status"] == "PASS"
    cube.data.uv_layers.new(name="UVMap")
    r = _validate("UV", require_uv2=True)
    assert _check(r, "UV", "missing_uv")["status"] == "PASS" and _check(r, "UV", "missing_uv2")["status"] == "FAIL"
    cube.data.uv_layers.new(name="UVMap2")
    assert _check(_validate("UV", require_uv2=True), "UV", "missing_uv2")["status"] == "PASS"
    assert _check(_validate("UV"), "UV", "uv_out_of_bounds")["status"] == "PASS"
    uv = cube.data.uv_layers["UVMap"].data
    for li in cube.data.polygons[0].loop_indices:
        uv[li].uv = (uv[li].uv[0] + 2.0, uv[li].uv[1])
    c = _check(_validate("UV"), "UV", "uv_out_of_bounds")
    assert c["status"] == "WARN" and c["count"] >= 1, c
    # budgets
    c = _check(_validate("UV", max_triangles=10), "UV", "triangle_budget")
    assert c["status"] == "FAIL" and c["count"] == 12, c
    assert _check(_validate("UV", max_triangles=12), "UV", "triangle_budget")["status"] == "PASS"
    assert _check(_validate("UV"), "UV", "triangle_budget")["status"] == "PASS"
    sub = cube.modifiers.new("Sub", "SUBSURF"); sub.levels = 2
    c = _check(_validate("UV", max_triangles=100), "UV", "triangle_budget")
    assert c["status"] == "FAIL" and c["count"] == 192, c        # evaluated (E13)
    cube.modifiers.remove(sub)
    _prim("primitive_grid_add", "Big", x_subdivisions=300, y_subdivisions=300)
    c = _check(_validate("Big"), "Big", "vertex_count_16bit")
    assert c["status"] == "WARN" and c["count"] == 301 * 301, c
    assert _check(_validate("UV"), "UV", "vertex_count_16bit")["status"] == "PASS"
    bpy.data.objects.remove(bpy.data.objects["Big"], do_unlink=True)
    # materials and images
    cube.data.materials.append(None)
    c = _check(_validate("UV"), "UV", "material_slots")
    assert c["status"] == "WARN" and c["count"] >= 1, c
    cube.data.materials.clear()
    m1 = bpy.data.materials.new("Used"); m2 = bpy.data.materials.new("Unused")
    m1.use_nodes = True
    cube.data.materials.append(m1); cube.data.materials.append(m2)
    c = _check(_validate("UV"), "UV", "material_slots")
    assert c["status"] == "WARN" and c["count"] >= 1, c
    m2.use_nodes = False
    nodeless = not (m2.use_nodes and m2.node_tree is not None and len(m2.node_tree.nodes) > 0)
    c = _check(_validate("UV"), "UV", "materials_without_nodes")
    assert c["status"] == ("WARN" if nodeless else "PASS") and c["count"] == (1 if nodeless else 0), (c, nodeless)
    m2.use_nodes = True
    if m2.node_tree is not None and len(m2.node_tree.nodes) > 0:
        assert _check(_validate("UV"), "UV", "materials_without_nodes")["status"] == "PASS"
    m1.use_nodes = True
    img = bpy.data.images.new("Missing", 4, 4); img.filepath = os.path.join(B2DIR, "no_such_image.png"); img.source = "FILE"
    tex = m1.node_tree.nodes.new("ShaderNodeTexImage"); tex.image = img
    c = _check(_validate("UV"), "UV", "images_missing")
    assert c["status"] == "FAIL" and c["count"] >= 1, c
    m1.node_tree.nodes.remove(tex); bpy.data.images.remove(img)
    assert _check(_validate("UV"), "UV", "images_missing")["status"] == "PASS"
    # bone influences
    for i in range(5):
        cube.vertex_groups.new(name="B%d" % i).add([0], 0.2, "REPLACE")
    c = _check(_validate("UV"), "UV", "bone_influences")
    assert c["status"] == "WARN" and c["count"] == 1 and c["sample_indices"] == [0], c
    assert _check(_validate("UV", max_bone_influences=8), "UV", "bone_influences")["status"] == "PASS"
    cube.vertex_groups["B4"].add([0], 0.0, "REPLACE")
    assert _check(_validate("UV"), "UV", "bone_influences")["status"] == "PASS", "zero weight counted as an influence"
    return "uv/uv2/out-of-bounds, budget 12 vs 10 and evaluated 192, 16-bit WARN at 90601, slots/nodes/images, 5 influences WARN (zero weight ignored)"


step("B2-V3 validate_asset uv / budgets / materials / bones (13.2.2)", b2_v3, tag="B2")


def b2_v4():
    clear_scene()
    a = make_cube("My Prop.001", 2.0, (0, 0, 0)); a.data.uv_layers.new(name="UVMap")
    mat = bpy.data.materials.new("Wood Ok"); a.data.materials.append(mat)
    c = _check(_validate("My Prop.001", profile="UNREAL"), "My Prop.001", "illegal_names")
    assert c["status"] == "WARN" and "My Prop.001" in c["detail"] and "Wood Ok" in c["detail"], c
    assert _check(_validate("My Prop.001", profile="GENERIC"), "My Prop.001", "illegal_names")["status"] == "PASS"
    r = _validate("My Prop.001", profile="UNREAL", fix=True)
    assert any(f["check_id"] == "illegal_names" for f in r["fixes_applied"]), r["fixes_applied"]
    renamed = [o.name for o in bpy.data.objects if o.type == "MESH"]
    assert renamed == ["My_Prop_001"], renamed
    assert mat.name == "Wood_Ok", mat.name
    b = make_cube("Cube", 2.0, (4, 0, 0)); c2 = make_cube("Cube.001", 2.0, (8, 0, 0))
    r = _validate("Cube,Cube.001")
    assert _check(r, "Cube", "name_collisions")["status"] == "WARN" and _check(r, "Cube.001", "name_collisions")["status"] == "WARN"
    # E12: name limit is the live one (63 on 4.3.2, 255 on 5.2.1), never a constant
    long_name = "L" * 200
    c2.name = long_name
    live_len = len(c2.name)
    r = _validate(c2.name)
    assert c2.name in r["objects"], "object with a %d-char name not found by name" % live_len
    assert _check(r, c2.name, "illegal_names")["status"] == "PASS", "a name Blender itself accepts was flagged"
    # hierarchy per profile (ruling m_1f6f58ae 5: no children = PASS; collision-typed child = WIRE+hide_render mesh or a collision name)
    base = make_cube("Gun", 2.0, (0, 5, 0)); base.data.uv_layers.new(name="UVMap")
    assert _check(_validate("Gun", profile="UNREAL"), "Gun", "hierarchy")["status"] == "PASS"
    col = make_cube("Hull", 2.2, (0, 5, 0)); col.parent = base; col.display_type = "WIRE"; col.hide_render = True
    assert _check(_validate("Gun", profile="UNREAL"), "Gun", "hierarchy")["status"] == "FAIL"
    col.name = "UCX_Gun_00"
    assert _check(_validate("Gun", profile="UNREAL"), "Gun", "hierarchy")["status"] == "PASS"
    assert _check(_validate("Gun", profile="GODOT"), "Gun", "hierarchy")["status"] == "FAIL"
    col.name = "Gun-col"
    assert _check(_validate("Gun", profile="GODOT"), "Gun", "hierarchy")["status"] == "PASS"
    for prof in ("GENERIC", "UNITY", "BEVY"):
        assert _check(_validate("Gun", profile=prof), "Gun", "hierarchy")["status"] == "PASS", prof
    assert _check(_validate("Gun", profile="TIMBERMESH"), "Gun", "hierarchy")["status"] == "FAIL"
    bpy.ops.object.empty_add(); root = bpy.context.active_object; root.name = "#Gun"; base.parent = root
    assert _check(_validate("Gun", profile="TIMBERMESH"), "Gun", "hierarchy")["status"] == "PASS"
    # inputs (H11/H12)
    rr = call("validate_asset", objects="Gun", profile="PS5")
    m = err(rr, "PS5") + json.dumps(rr.get("result"))
    assert all(p in m for p in ("GENERIC", "UNITY", "UNREAL", "GODOT", "BEVY", "TIMBERMESH")), A(m)
    rr = call("validate_asset", objects="Gun,Nope")
    e = err(rr, "Nope")
    res = rr.get("result") if isinstance(rr, dict) else None
    assert isinstance(res, dict) and res.get("unknown") == ["Nope"] and "valid_count" in res, A(json.dumps(res)[:300])
    err(call("validate_asset", collection="NoColl"), "NoColl")
    err(call("validate_asset"))
    coll = bpy.data.collections.new("Kit"); bpy.context.scene.collection.children.link(coll); coll.objects.link(base)
    r = ok(call("validate_asset", collection="Kit"))
    assert list(r["objects"]) == ["Gun"], list(r["objects"])
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    r = ok(call("validate_asset", objects="Gun,Lamp"))
    assert r["objects"]["Lamp"]["verdict"] == "PASS" and r["objects"]["Lamp"]["checks"] == {} and "not a mesh" in r["objects"]["Lamp"]["detail"], r["objects"]["Lamp"]
    # fnmatch glob (H11)
    r = ok(call("validate_asset", objects="Gu*"))
    assert "Gun" in r["objects"] and "Gun-col" in r["objects"], list(r["objects"])
    return "illegal names WARN + rename fix, collisions WARN, live name limit (%d chars ok), hierarchy per 6 profiles incl. no-children PASS, H11/H12 refusals, collection/glob/light" % live_len


step("B2-V4 validate_asset names / hierarchy / inputs (13.2.2, E12, H11, H12)", b2_v4, tag="B2")


def b2_v8():
    clear_scene()
    op = _open_cube("Open")
    snap_before = snapshot()
    r = ok(call("find_mesh_issues", mesh="Open", issue="NON_MANIFOLD"), must=["mesh", "issue", "element", "count", "indices", "truncated"])
    _enc_ok(r)
    assert snapshot() == snap_before, "find_mesh_issues changed mode/selection"
    assert r["element"] == "EDGE" and r["count"] == 4 and len(r["indices"]) == 4 and r["truncated"] is False, r
    bm = bmesh.new(); bm.from_mesh(op.data); bm.edges.ensure_lookup_table()
    nm = sorted(e.index for e in bm.edges if not e.is_manifold); bm.free()
    assert sorted(r["indices"]) == nm, (r["indices"], nm)
    r = ok(call("find_mesh_issues", mesh="Open", issue="NON_MANIFOLD", max_indices=2))
    assert len(r["indices"]) == 2 and r["count"] == 4 and r["truncated"] is True, r
    r = ok(call("find_mesh_issues", mesh="Open", issue="BOUNDARY"))
    assert r["element"] == "EDGE" and r["count"] == 4, r
    cyl = _prim("primitive_cylinder_add", "Cyl", vertices=12, end_fill_type="NGON")
    r = ok(call("find_mesh_issues", mesh="Cyl", issue="NGONS"))
    assert r["element"] == "FACE" and sorted(r["indices"]) == sorted(p.index for p in cyl.data.polygons if len(p.vertices) > 4), r
    _dirty_cube("Dirty")
    r = ok(call("find_mesh_issues", mesh="Dirty", issue="LOOSE"))
    assert r["element"] == "VERT" and 9 in r["indices"], r
    r = ok(call("find_mesh_issues", mesh="Dirty", issue="DOUBLES"))
    assert r["element"] == "VERT" and r["count"] >= 1 and (6 in r["indices"] or 8 in r["indices"]), r
    r = ok(call("find_mesh_issues", mesh="Dirty", issue="DEGENERATE"))
    assert r["element"] in ("FACE", "EDGE", "VERT") and r["count"] >= 1, r
    flip = make_cube("Flip", 2.0, (5, 0, 0)); _flip_one_face(flip, 2)
    r = ok(call("find_mesh_issues", mesh="Flip", issue="FLIPPED"))
    assert r["element"] == "FACE" and r["indices"] == [2], r
    _prim("primitive_monkey_add", "Suz")
    r = ok(call("find_mesh_issues", mesh="Suz", issue="BOUNDARY"))
    assert r["count"] > 0 and r["element"] == "EDGE", r                                   # doc step 2
    suz_boundary = r["count"]
    # INTERIOR_FACES: two cubes sharing a face, joined
    a = make_cube("IA", 2.0, (0, 0, 10)); b = make_cube("IB", 2.0, (2, 0, 10))
    bpy.ops.object.select_all(action="DESELECT"); a.select_set(True); b.select_set(True)
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.join()
    with bpy.context.temp_override(active_object=a, selected_editable_objects=[a]):
        bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=1e-4); bpy.ops.object.mode_set(mode="OBJECT")
    r = ok(call("find_mesh_issues", mesh="IA", issue="INTERIOR_FACES"))
    assert r["element"] == "FACE" and r["count"] >= 1 and all(0 <= i < len(a.data.polygons) for i in r["indices"]), r
    shared = [p.index for p in a.data.polygons if abs(p.center.x - 1.0) < 1e-5]
    assert sorted(r["indices"]) == sorted(shared), (r["indices"], shared)
    interior = r["count"]
    # errors + mode restore on the error path (E7 finally)
    rr = call("find_mesh_issues", mesh="Open", issue="HOLES")
    m = err(rr, "HOLES") + json.dumps(rr.get("result"))
    assert all(k in m for k in ("NON_MANIFOLD", "NGONS", "LOOSE", "DEGENERATE", "DOUBLES", "FLIPPED", "INTERIOR_FACES", "BOUNDARY")), A(m)
    assert bpy.context.mode == "OBJECT"
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    err(call("find_mesh_issues", mesh="Lamp", issue="NGONS"), "not a mesh")
    err(call("find_mesh_issues", mesh="Nope", issue="NGONS"), "not found")
    err(call("find_mesh_issues", mesh="Open", issue="NON_MANIFOLD", max_indices=0), "max_indices")        # L1 deviation (f): < 1 refused
    # render=True headless -> R20 error keyed on bpy.app.background; mode/selection compared against the state right before the call
    snap_before = snapshot()
    rr = call("find_mesh_issues", mesh="Open", issue="NON_MANIFOLD", render=True)
    res = rr.get("result") if isinstance(rr, dict) else None
    assert isinstance(res, dict) and "error" in res and res.get("background") is True and "GUI" in res["error"], A(json.dumps(res)[:300])
    assert bpy.context.mode == "OBJECT", "render path left mode %s" % bpy.context.mode
    assert snapshot()["selected"] == snap_before["selected"], "render path changed the selection"
    return "8 issue classes with bmesh-verified indices, cap/truncated, Suz boundary %d, interior %d, errors list the enum, render headless R20" % (suz_boundary, interior)


step("B2-V8 find_mesh_issues 8 classes + cap + render headless (13.2.2, E7, H22)", b2_v8, tag="B2")


def b2_v9():
    clear_scene()
    _prim("primitive_monkey_add", "Suz")
    suz = bpy.data.objects["Suz"]
    cp = suz.copy(); cp.data = suz.data.copy(); cp.name = "SuzCopy"; bpy.context.scene.collection.objects.link(cp)
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="SuzCopy"), must=["mesh_a", "mesh_b", "same", "unit_test_compare", "vertices",
                                                                           "triangles", "positions", "order_match", "custom_normals",
                                                                           "uvs", "tangents", "materials", "vertex_groups"])
    _enc_ok(r)
    assert r["same"] is True and r["unit_test_compare"] == "Same", (r["same"], r["unit_test_compare"])
    assert r["vertices"]["delta"] == 0 and r["triangles"]["delta"] == 0 and r["positions"] == {"differing": 0, "max_delta": 0.0}, r
    assert r["order_match"] is True and r["custom_normals"]["a"] is False and r["custom_normals"]["b"] is False, r
    assert r["uvs"]["layers_a"] == r["uvs"]["layers_b"] == ["UVMap"] and r["materials"]["same"] is True and r["vertex_groups"]["same"] is True, r
    cp.data.vertices[3].co.x += 1e-3
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="SuzCopy"))
    assert r["same"] is False and r["positions"]["differing"] == 1 and abs(r["positions"]["max_delta"] - 1e-3) < 1e-6 and r["order_match"] is True, r["positions"]
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="SuzCopy", tolerance=1e-2))
    assert r["positions"]["differing"] == 0, r["positions"]
    cp.data.vertices[3].co.x -= 1e-3
    # permuted order: same positions set, order mismatch
    perm = suz.copy(); perm.data = suz.data.copy(); perm.name = "SuzPerm"; bpy.context.scene.collection.objects.link(perm)
    n = len(perm.data.vertices)
    co = [v.co.copy() for v in perm.data.vertices]
    perm.data.vertices[0].co, perm.data.vertices[1].co = co[1], co[0]
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="SuzPerm"))
    assert r["order_match"] is False and r["positions"]["differing"] == 2, r["positions"]
    # different counts: deltas, positions null, no exception
    cube = make_cube("Cube", 2.0, (5, 0, 0))
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="Cube"))
    assert r["vertices"]["delta"] == 8 - n and r["positions"] is None and r["order_match"] is None and r["same"] is False, r
    assert r["uvs"]["layers_b"] == [] and r["uvs"]["max_delta"] is None, r["uvs"]
    # custom normals + angle
    cp.data.normals_split_custom_set_from_vertices([v.normal for v in cp.data.vertices])
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="SuzCopy"))
    assert r["custom_normals"]["b"] is True and r["custom_normals"]["a"] is False, r["custom_normals"]
    # subset + errors
    r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="SuzCopy", compare="VERTS,ORDER"))
    assert "positions" in r and "order_match" in r and r["positions"]["differing"] == 0, sorted(r)
    assert all(k in r for k in ("mesh_a", "mesh_b", "same", "unit_test_compare", "vertices", "triangles")), sorted(r)
    assert not any(k in r for k in ("uvs", "materials", "vertex_groups", "tangents", "custom_normals")), sorted(r)   # R-B2-27
    rr = call("compare_meshes", mesh_a="Suz", mesh_b="SuzCopy", compare="BOGUS")
    m = err(rr, "BOGUS") + json.dumps(rr.get("result"))
    assert "TANGENTS" in m and "GROUPS" in m, A(m)
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    err(call("compare_meshes", mesh_a="Suz", mesh_b="Lamp"), "not a mesh")
    err(call("compare_meshes", mesh_a="Suz", mesh_b="Nope"), "not found")
    # numpy pass stays fast on a 10k-vert mesh (M12 float64 buffers)
    _prim("primitive_grid_add", "Grid", x_subdivisions=100, y_subdivisions=100)
    g = bpy.data.objects["Grid"]; g2 = g.copy(); g2.data = g.data.copy(); g2.name = "Grid2"; bpy.context.scene.collection.objects.link(g2)
    t0 = __import__("time").time()
    r = ok(call("compare_meshes", mesh_a="Grid", mesh_b="Grid2"))
    dt = __import__("time").time() - t0
    assert r["same"] is True and dt < 20.0, dt
    return "Same / moved 1e-3 / tolerance / permuted order / count mismatch null / custom normals / subset / errors; 10k verts in %.2fs" % dt


step("B2-V9 compare_meshes (13.2.2, E13)", b2_v9, tag="B2")


def b2_v10():
    clear_scene()
    a = make_cube("A", 2.0, (0, 0, 0)); b = bpy.data.objects.new("B", a.data); bpy.context.scene.collection.objects.link(b)
    b.location = (5, 0, 0)
    a.scale = (2, 1, 1); b.scale = (1, 1, 3)
    bpy.context.view_layer.update()
    wa = _world_verts(a); dims_b = tuple(round(x, 5) for x in b.dimensions)
    r = ok(call("apply_transforms", objects="A"), must=["objects", "applied", "isolated", "flip_corrected", "negative_scale"])
    _enc_ok(r)
    assert r["objects"] == ["A"] and r["applied"] == {"location": False, "rotation": True, "scale": True}, r
    assert r["isolated"] == ["A"] and r["negative_scale"] == [] and isinstance(r["flip_corrected"], list), r
    bpy.context.view_layer.update()
    assert a.data.users == 1 and a.data is not b.data and close(a.scale, (1, 1, 1)) and _world_verts(a) == wa, (a.data.users, tuple(a.scale))
    assert tuple(round(x, 5) for x in b.dimensions) == dims_b and close(b.scale, (1, 1, 3)), "the sharing object was touched"
    # keep_children_world: child world matrix unchanged after the parent's rotation is applied
    p = make_cube("P", 2.0, (0, 5, 0)); p.rotation_euler = (0, 0, math.radians(45))
    c = make_cube("C", 1.0, (0, 0, 0)); c.parent = p; c.location = (2, 0, 0)
    bpy.context.view_layer.update()
    cw = [round(x, 5) for row in c.matrix_world for x in row]
    ok(call("apply_transforms", objects="P", rotation=True, scale=False))
    bpy.context.view_layer.update()
    assert close(p.rotation_euler, (0, 0, 0)) and [round(x, 5) for row in c.matrix_world for x in row] == cw, "child moved in world space"
    # location
    wl = _world_verts(p)
    r = ok(call("apply_transforms", objects="P", location=True, rotation=False, scale=False))
    bpy.context.view_layer.update()
    assert r["applied"]["location"] is True and close(p.location, (0, 0, 0)) and _world_verts(p) == wl, tuple(p.location)
    # negative scale: applied anyway, reported; flip_corrected keyed on the live kwarg (E6), never bpy.app.version
    n = make_cube("N", 2.0, (0, -5, 0)); n.scale = (-1, 1, 1); bpy.context.view_layer.update()
    r = ok(call("apply_transforms", objects="N"))
    has_kw = "corrective_flip_normals" in bpy.ops.object.transform_apply.get_rna_type().properties.keys()
    assert r["negative_scale"] == ["N"] and (r["flip_corrected"] == ["N"]) is has_kw, (r, has_kw)
    assert close(n.scale, (1, 1, 1))
    # H11: one unknown name -> nothing applied
    q = make_cube("Q", 2.0, (0, 8, 0)); q.scale = (2, 2, 2); bpy.context.view_layer.update()
    rr = call("apply_transforms", objects="Q,Nope")
    err(rr, "Nope")
    assert (rr.get("result") or {}).get("unknown") == ["Nope"] and close(q.scale, (2, 2, 2)), "applied despite an unknown name"
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    bpy.data.objects["Lamp"].scale = (2, 2, 2)
    r = ok(call("apply_transforms", objects="Lamp,Q"))
    assert close(q.scale, (1, 1, 1)), "mesh in a mixed list not applied"
    return "isolate_users on a shared mesh, child world kept, location applied, negative scale reported (flip_corrected=%s per live kwarg), H11 refusal applies nothing" % has_kw


step("B2-V10 apply_transforms (13.2.2, E6, H11)", b2_v10, tag="B2")


def b2_v11():
    clear_scene()
    cube = make_cube("Nrm", 2.0, (0, 0, 0))
    cube.data.flip_normals()
    centre = mathutils.Vector((0, 0, 0))
    inward = lambda ob: [p.index for p in ob.data.polygons if p.normal.dot(p.center - centre) < 0]
    assert len(inward(cube)) == 6
    r = ok(call("fix_normals", objects="Nrm"), must=["objects", "mode", "faces_flipped", "custom_normals_cleared", "sharp_edges_marked"])
    _enc_ok(r)
    assert r["mode"] == "OUTSIDE" and r["faces_flipped"] == {"Nrm": 6} and inward(cube) == [], r
    r = ok(call("fix_normals", objects="Nrm", mode="INSIDE"))
    assert len(inward(cube)) == 6 and r["faces_flipped"]["Nrm"] == 6, r
    r = ok(call("fix_normals", objects="Nrm", mode="OUTSIDE"))
    assert inward(cube) == [] and r["faces_flipped"]["Nrm"] == 6, r
    r = ok(call("fix_normals", objects="Nrm", mode="OUTSIDE"))
    assert r["faces_flipped"]["Nrm"] == 0, r
    before = [tuple(round(c, 5) for c in p.normal) for p in cube.data.polygons]
    r = ok(call("fix_normals", objects="Nrm", mode="FLIP"))
    after = [tuple(round(c, 5) for c in p.normal) for p in cube.data.polygons]
    assert all(abs(a + b) < 1e-4 for na, nb in zip(before, after) for a, b in zip(na, nb)) and r["faces_flipped"]["Nrm"] == 6, r
    ok(call("fix_normals", objects="Nrm", mode="OUTSIDE"))
    cube.data.normals_split_custom_set_from_vertices([v.normal for v in cube.data.vertices])
    assert cube.data.has_custom_normals
    r = ok(call("fix_normals", objects="Nrm", mode="CLEAR_CUSTOM"))
    assert r["custom_normals_cleared"] == ["Nrm"] and cube.data.has_custom_normals is False, r
    r = ok(call("fix_normals", objects="Nrm", also_sharp_by_angle=30))
    assert r["sharp_edges_marked"] == {"Nrm": 12} and sum(1 for e in cube.data.edges if e.use_edge_sharp) == 12, r
    r = ok(call("fix_normals", objects="Nrm"))
    assert r["sharp_edges_marked"] == {}, r
    rr = call("fix_normals", objects="Nrm", mode="UP")
    m = err(rr, "UP") + json.dumps(rr.get("result"))
    assert "CLEAR_CUSTOM" in m and "FLIP" in m, A(m)
    err(call("fix_normals", objects="Nope"), "Nope")
    assert bpy.context.mode == "OBJECT"
    return "OUTSIDE/INSIDE/FLIP counts by bpy, CLEAR_CUSTOM, 12 sharp edges at 30 deg, enum teach"


step("B2-V11 fix_normals (13.2.2, E4)", b2_v11, tag="B2")


def b2_v12():
    clear_scene()
    d = _dirty_cube("Dirty")
    nv, nf = len(d.data.vertices), len(d.data.polygons)
    r = ok(call("cleanup_mesh", objects="Dirty"), must=["objects", "per_object"])
    _enc_ok(r)
    po = r["per_object"]["Dirty"]
    for k in ("doubles_removed", "loose_verts_removed", "loose_edges_removed", "degenerate_dissolved", "holes_filled", "faces_before", "faces_after"):
        assert k in po and isinstance(po[k], int), (k, po)
    assert po["doubles_removed"] == 1 and po["loose_verts_removed"] >= 1 and po["degenerate_dissolved"] >= 1 and po["holes_filled"] == 0, po
    assert po["faces_before"] == nf and po["faces_after"] == len(d.data.polygons) == 6, (po, len(d.data.polygons))
    assert 8 <= len(d.data.vertices) <= 10, len(d.data.vertices)      # the collinear remnant may survive as an edge
    clean = make_cube("Clean", 2.0, (5, 0, 0))
    r = ok(call("cleanup_mesh", objects="Clean"))
    po = r["per_object"]["Clean"]
    assert all(po[k] == 0 for k in ("doubles_removed", "loose_verts_removed", "loose_edges_removed", "degenerate_dissolved", "holes_filled")), po
    assert len(clean.data.vertices) == 8 and len(clean.data.polygons) == 6
    op = _open_cube("Open")
    r = ok(call("cleanup_mesh", objects="Open", fill_holes=4))
    assert r["per_object"]["Open"]["holes_filled"] == 1 and len(op.data.polygons) == 6, r["per_object"]["Open"]
    _prim("primitive_grid_add", "Grid", x_subdivisions=4, y_subdivisions=4)
    r = ok(call("cleanup_mesh", objects="Grid", limited_dissolve=5))
    assert len(bpy.data.objects["Grid"].data.polygons) == 1, len(bpy.data.objects["Grid"].data.polygons)
    d2 = _dirty_cube("Dirty2")
    r = ok(call("cleanup_mesh", objects="Dirty2", remove_doubles=0.0, delete_loose=False, dissolve_degenerate=False))
    po = r["per_object"]["Dirty2"]
    assert po["doubles_removed"] == 0 and po["loose_verts_removed"] == 0 and len(d2.data.vertices) == 13, (po, len(d2.data.vertices))
    assert po["degenerate_dissolved"] == 0, po
    r = ok(call("cleanup_mesh", objects="Dirty2,Clean"))
    assert set(r["per_object"]) == {"Dirty2", "Clean"}, r["per_object"].keys()
    err(call("cleanup_mesh", objects="Nope"), "Nope")
    assert bpy.context.mode == "OBJECT"
    return "doubles 1 / loose / degenerate counts, clean no-op, fill_holes 1, limited_dissolve 25 -> 1 face, thresholds honoured"


step("B2-V12 cleanup_mesh (13.2.2)", b2_v12, tag="B2")

clear_scene()

# =========================================================================
# 13.2.3 optimisation and LODs (L2): decimate_mesh, generate_lods, set_normals, triangulate_for_export, get_triangle_budget
# =========================================================================
def _valid_text(rr):
    """error text + the reply's teaching keys (valid/closest/unknown) as one searchable string."""
    return err(rr) + json.dumps(rr.get("result") if isinstance(rr, dict) else None)


def b2_o1():
    clear_scene()
    sph = _prim("primitive_uv_sphere_add", "Sph", segments=32, ring_count=16)
    before = len(sph.data.polygons); tris0 = _eval_tris(sph)
    r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5, new_object="Sph_half"),
           must=["mesh", "result_object", "mode", "ratio_used", "triangles_before", "triangles_after", "passes", "applied", "modifier"])
    _enc_ok(r)
    assert r["mesh"] == "Sph" and r["result_object"] == "Sph_half" and r["mode"] == "COLLAPSE" and r["applied"] is True and r["modifier"] is None, r
    assert r["triangles_before"] == tris0 and 0.4 * tris0 <= r["triangles_after"] <= 0.6 * tris0 and abs(r["ratio_used"] - 0.5) < 1e-9, r
    half = bpy.data.objects["Sph_half"]
    assert len(sph.data.polygons) == before and half.data is not sph.data and _eval_tris(half) == r["triangles_after"], (len(sph.data.polygons), _eval_tris(half))
    assert not half.modifiers, [m.name for m in half.modifiers]
    r = ok(call("decimate_mesh", mesh="Sph", target_triangles=200, new_object="Sph_200"))
    assert r["passes"] <= 6 and abs(r["triangles_after"] - 200) <= 10 and 0 < r["ratio_used"] < 1 and len(sph.data.polygons) == before, r
    assert _eval_tris(bpy.data.objects["Sph_200"]) == r["triangles_after"]
    # O1c (Ada m_2c988757): a target that needs >= 2 passes; the source datablock stays untouched between passes
    # (an "apply per pass" implementation destroys the mesh and cannot re-read face_count from the same source)
    src_before = (len(sph.data.vertices), len(sph.data.polygons))
    r = ok(call("decimate_mesh", mesh="Sph", target_triangles=40, new_object="Sph_40"))
    assert 1 <= r["passes"] <= 6 and abs(r["triangles_after"] - 40) <= 2 and 0 < r["ratio_used"] < 0.1, r
    assert (len(sph.data.vertices), len(sph.data.polygons)) == src_before and _eval_tris(bpy.data.objects["Sph_40"]) == r["triangles_after"], (src_before, r)
    r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5, mode="PLANAR", apply=False, new_object="Sph_live", symmetry_axis="X"))
    assert r["mode"] == "DISSOLVE" and r["applied"] is False and r["modifier"] is not None, r
    live = bpy.data.objects["Sph_live"]; m = live.modifiers[r["modifier"]]
    assert m.type == "DECIMATE" and m.decimate_type == "DISSOLVE" and abs(m.angle_limit - math.radians(5.0)) < 1e-6, (m.decimate_type, m.angle_limit)
    r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5, apply=False, new_object="Sph_live2"))
    m = bpy.data.objects["Sph_live2"].modifiers[r["modifier"]]
    assert m.decimate_type == "COLLAPSE" and m.delimit == {"UV", "SEAM"} and m.use_collapse_triangulate is True and abs(m.ratio - 0.5) < 1e-9, (m.delimit, m.use_collapse_triangulate)
    bpy.context.view_layer.update()
    assert m.face_count > 0 and r["triangles_after"] > 0, (m.face_count, r["triangles_after"])          # E3 face_count readback
    r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5, apply=False, new_object="Sph_live3", keep_uv_seams=False, triangulate=False, symmetry_axis="Y"))
    m = bpy.data.objects["Sph_live3"].modifiers[r["modifier"]]
    assert m.delimit == set() and m.use_collapse_triangulate is False and m.use_symmetry is True and m.symmetry_axis == "Y", (m.delimit, m.use_symmetry, m.symmetry_axis)
    grid = _prim("primitive_grid_add", "Grid", x_subdivisions=8, y_subdivisions=8)
    gf = len(grid.data.polygons)
    r = ok(call("decimate_mesh", mesh="Grid", ratio=0.5, mode="UNSUBDIVIDE", iterations=2))
    assert r["mode"] == "UNSUBDIV" and r["result_object"] == "Grid" and len(grid.data.polygons) < gf, (r["mode"], len(grid.data.polygons), gf)
    r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5))                                   # in place
    assert r["result_object"] == "Sph" and len(sph.data.polygons) < before and not sph.modifiers
    err(call("decimate_mesh", mesh="Sph_half"))
    err(call("decimate_mesh", mesh="Sph_half", ratio=0.5, target_triangles=100))
    assert "COLLAPSE" in _valid_text(call("decimate_mesh", mesh="Sph_half", ratio=0.5, mode="NOPE"))
    err(call("decimate_mesh", mesh="Sph_half", ratio=0.0)); err(call("decimate_mesh", mesh="Sph_half", ratio=1.5))
    err(call("decimate_mesh", mesh="Sph_half", ratio=0.5, vertex_group="NoSuch"), "NoSuch")
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    err(call("decimate_mesh", mesh="Lamp", ratio=0.5), "not a mesh"); err(call("decimate_mesh", mesh="Nope", ratio=0.5), "not found")
    assert bpy.context.mode == "OBJECT"
    return "ratio 0.5 (%d -> %d) with the source untouched, target 200 in <= %d passes, PLANAR->DISSOLVE live modifier, delimit/triangulate/symmetry read back, UNSUBDIV, in place, 8 refusals" % (tris0, _eval_tris(half), r.get("passes", 0) or 0)


step("B2-O1 decimate_mesh (13.2.3, E3)", b2_o1, tag="B2")


def b2_o2():
    clear_scene()
    suz = _prim("primitive_monkey_add", "Suz")
    mat = bpy.data.materials.new("SuzMat"); suz.data.materials.append(mat)
    src_mesh = suz.data; n0 = len(src_mesh.polygons); t0 = _eval_tris(suz)
    r = ok(call("generate_lods", mesh="Suz", ratios="0.5,0.25"), must=["source", "lod0", "levels", "collection", "custom_normals_transferred"])
    _enc_ok(r)
    assert r["source"] == "Suz" and r["lod0"] == "Suz_LOD0" and r["custom_normals_transferred"] is False, r
    names = [l["name"] for l in r["levels"]]; ratios = [l["ratio"] for l in r["levels"]]; levels = [l["level"] for l in r["levels"]]
    assert names == ["Suz_LOD0", "Suz_LOD1", "Suz_LOD2"] and levels == [0, 1, 2] and ratios == [1.0, 0.5, 0.25], (names, levels, ratios)
    t = [l["triangles"] for l in r["levels"]]
    assert t[0] == t0 and t[0] > t[1] > t[2] and abs(t[1] / t[0] - 0.5) < 0.1 and abs(t[2] / t[0] - 0.25) < 0.1, t
    assert "Suz" not in bpy.data.objects and bpy.data.objects["Suz_LOD0"].data is src_mesh and len(src_mesh.polygons) == n0, "source mesh touched or not renamed"
    for n in ("Suz_LOD1", "Suz_LOD2"):
        o = bpy.data.objects[n]
        assert o.data is not src_mesh and o.data.materials[0] is mat and _eval_tris(o) == t[names.index(n)], (n, _eval_tris(o))
    # collision up front, scene untouched (R-B2-17 9)
    snap = sorted((o.name, len(o.data.polygons)) for o in bpy.data.objects if o.type == "MESH")
    rr = call("generate_lods", mesh="Suz_LOD0", ratios="0.5")
    err(rr, "Suz_LOD1"); assert "Suz_LOD1" in (rr.get("result") or {}).get("existing", []), rr.get("result")
    assert sorted((o.name, len(o.data.polygons)) for o in bpy.data.objects if o.type == "MESH") == snap, "collision refusal changed the scene"
    for n in ("Suz_LOD1", "Suz_LOD2"):
        bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
    r = ok(call("generate_lods", mesh="Suz_LOD0", ratios="0.5"))                        # existing suffix stripped
    assert r["source"] == "Suz" and r["lod0"] == "Suz_LOD0" and [l["name"] for l in r["levels"]] == ["Suz_LOD0", "Suz_LOD1"], r["levels"]
    # default form (R-B2-26): no ratios, no targets -> 0.5,0.25,0.1 applied by the add-on = 4 entries incl. LOD0
    clear_scene(); _prim("primitive_monkey_add", "Suz")
    r = ok(call("generate_lods", mesh="Suz"))
    assert [l["ratio"] for l in r["levels"]] == [1.0, 0.5, 0.25, 0.1] and [l["name"] for l in r["levels"]] == ["Suz_LOD%d" % i for i in range(4)], r["levels"]
    # targets form + collection + parent + naming + normals
    clear_scene(); s2 = _prim("primitive_monkey_add", "Suz")
    r = ok(call("generate_lods", mesh="Suz", triangle_targets="400,200", collection="LODs", parent_to_lod0=True, naming="{name}_lod{level}", transfer_normals=True))
    names = [l["name"] for l in r["levels"]]
    assert names == ["Suz_lod0", "Suz_lod1", "Suz_lod2"] and r["collection"] == "LODs" and r["custom_normals_transferred"] is True, r
    assert abs(r["levels"][1]["triangles"] - 400) <= 40 and abs(r["levels"][2]["triangles"] - 200) <= 20, [l["triangles"] for l in r["levels"]]
    coll = bpy.data.collections.get("LODs"); assert coll is not None and all(n in coll.objects for n in names), [o.name for o in coll.objects] if coll else None
    assert bpy.data.objects["Suz_lod1"].parent is bpy.data.objects["Suz_lod0"] and bpy.data.objects["Suz_lod1"].data.has_custom_normals
    # failures leave the source as it was
    clear_scene(); s3 = _prim("primitive_monkey_add", "Suz"); n = len(s3.data.polygons)
    err(call("generate_lods", mesh="Suz", ratios="2,abc")); assert s3.name == "Suz" and len(s3.data.polygons) == n
    err(call("generate_lods", mesh="Suz", ratios="")); err(call("generate_lods", mesh="Suz", ratios="1.5"))
    err(call("generate_lods", mesh="Suz", ratios="0.5", triangle_targets="100"))        # both -> error at the add-on too
    assert s3.name == "Suz" and len(bpy.data.objects) == 1, [o.name for o in bpy.data.objects]
    err(call("generate_lods", mesh="Nope"), "not found")
    return "doc step 3: Suz_LOD0..2 ratios 1/0.5/0.25 within 10 %, materials shared, source mesh untouched; collision refused with existing; suffix stripped; default form 4 levels; targets/collection/parent/naming/normals; bad ratios leave the source"


step("B2-O2 generate_lods (13.2.3, R-B2-17 9, R-B2-26)", b2_o2, tag="B2")


def b2_o3():
    clear_scene()
    cube = make_cube("Cube", 2.0, (0, 0, 0))
    r = ok(call("set_normals", objects="Cube", mode="AUTO_SMOOTH", angle=30.0), must=["objects", "mode", "method", "modifier_used", "has_custom_normals", "sharp_edges"])
    _enc_ok(r)
    assert r["method"] == "shade_smooth_by_angle" and r["modifier_used"] is None and r["sharp_edges"]["Cube"] == 12 and r["has_custom_normals"]["Cube"] is False, r
    assert sum(1 for e in cube.data.edges if e.use_edge_sharp) == 12 and not cube.modifiers and all(p.use_smooth for p in cube.data.polygons)
    r = ok(call("set_normals", objects="Cube", mode="WEIGHTED"))
    assert r["method"] == "weighted_normal" and r["has_custom_normals"]["Cube"] is True and cube.data.has_custom_normals and not cube.modifiers, r
    r = ok(call("set_normals", objects="Cube", mode="CLEAR_CUSTOM"))
    assert r["method"] == "clear_custom" and r["has_custom_normals"]["Cube"] is False and not cube.data.has_custom_normals, r
    r = ok(call("set_normals", objects="Cube", mode="WEIGHTED", apply=False, keep_sharp=True))
    m = cube.modifiers[r["modifier_used"]]
    assert m.type == "WEIGHTED_NORMAL" and m.mode == "FACE_AREA_WITH_ANGLE" and m.keep_sharp is True, (m.type, m.mode, m.keep_sharp)
    cube.modifiers.remove(m)
    r = ok(call("set_normals", objects="Cube", mode="FLAT"))
    assert r["method"] == "shade_flat" and not any(p.use_smooth for p in cube.data.polygons), r
    r = ok(call("set_normals", objects="Cube", mode="SMOOTH"))
    assert r["method"] == "shade_smooth" and all(p.use_smooth for p in cube.data.polygons), r
    # AUTO_SMOOTH_MODIFIER (E4): 5.x adds the "Smooth by Angle" NODES modifier; 4.3.2 CANCELLED -> teach error naming AUTO_SMOOTH
    rr = call("set_normals", objects="Cube", mode="AUTO_SMOOTH_MODIFIER", apply=False)
    res = rr.get("result")
    if isinstance(res, dict) and "error" in res:
        assert "AUTO_SMOOTH" in res["error"] and not cube.modifiers, (res["error"], [m.name for m in cube.modifiers])
        asm_note = "AUTO_SMOOTH_MODIFIER refused (shade_auto_smooth CANCELLED here)"
    else:
        r = ok(rr)
        assert r["method"] == "smooth_by_angle_modifier" and r["modifier_used"] and cube.modifiers[r["modifier_used"]].type == "NODES", r
        cube.modifiers.remove(cube.modifiers[r["modifier_used"]])
        asm_note = "AUTO_SMOOTH_MODIFIER added a NODES modifier"
    _prim("primitive_monkey_add", "Suz")
    r = ok(call("set_normals", objects="Cube", mode="FROM_OBJECT", source_object="Suz"))
    assert r["method"] == "data_transfer" and r["has_custom_normals"]["Cube"] is True and cube.data.has_custom_normals and not cube.modifiers, r
    err(call("set_normals", objects="Cube", mode="FROM_OBJECT"))
    err(call("set_normals", objects="Cube", mode="FROM_OBJECT", source_object="Nope"), "Nope")
    assert "WEIGHTED" in _valid_text(call("set_normals", objects="Cube", mode="BUMPY"))
    err(call("set_normals", objects="Nope"), "Nope")
    assert bpy.context.mode == "OBJECT"
    return "AUTO_SMOOTH 12 sharp edges no modifier, WEIGHTED custom normals (live modifier read back), CLEAR/FLAT/SMOOTH, %s, FROM_OBJECT, refusals" % asm_note


step("B2-O3 set_normals (13.2.3, E4)", b2_o3, tag="B2")


def b2_o4():
    clear_scene()
    cube = make_cube("Cube", 2.0, (0, 0, 0))
    r = ok(call("triangulate_for_export", objects="Cube"), must=["objects", "as_modifier", "per_object"])
    _enc_ok(r)
    assert r["as_modifier"] is False and r["per_object"]["Cube"] == {"triangles_before": 12, "triangles_after": 12, "modifier": None}, r
    assert len(cube.data.polygons) == 12 and all(len(p.vertices) == 3 for p in cube.data.polygons) and not cube.modifiers
    c2 = make_cube("Keep", 2.0, (4, 0, 0))
    c2.data.normals_split_custom_set_from_vertices([v.normal for v in c2.data.vertices]); assert c2.data.has_custom_normals
    ok(call("triangulate_for_export", objects="Keep"))
    assert c2.data.has_custom_normals and len(c2.data.polygons) == 12, "custom normals lost by the triangulation (modifier path required)"
    live = make_cube("Live", 2.0, (8, 0, 0))
    r = ok(call("triangulate_for_export", objects="Live", as_modifier=True, quad_method="BEAUTY", ngon_method="CLIP", keep_custom_normals=False))
    m = live.modifiers[r["per_object"]["Live"]["modifier"]]
    assert r["as_modifier"] is True and m.type == "TRIANGULATE" and m.quad_method == "BEAUTY" and m.ngon_method == "CLIP" and m.keep_custom_normals is False and len(live.data.polygons) == 6, (m.quad_method, m.ngon_method)
    assert r["per_object"]["Live"]["triangles_after"] == 12
    q = make_cube("Quads", 2.0, (12, 0, 0))
    ok(call("triangulate_for_export", objects="Quads", min_vertices=5))
    assert len(q.data.polygons) == 6, "min_vertices=5 must leave quads alone"
    cyl = _prim("primitive_cylinder_add", "Cyl", vertices=12, end_fill_type="NGON")
    r = ok(call("triangulate_for_export", objects="Cyl,Quads"))
    assert not any(len(p.vertices) > 4 for p in cyl.data.polygons) and set(r["per_object"]) == {"Cyl", "Quads"} and r["per_object"]["Cyl"]["triangles_after"] == 44, r["per_object"]
    assert "BEAUTY" in _valid_text(call("triangulate_for_export", objects="Cube", quad_method="DIAGONAL"))
    err(call("triangulate_for_export", objects="Nope"), "Nope")
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    err(call("triangulate_for_export", objects="Lamp"), "not a mesh")
    assert bpy.context.mode == "OBJECT"
    return "cube -> %d tris, custom normals kept, live modifier with options read back, min_vertices 5 leaves %d quads, Cyl -> %d tris, refusals" % (len(cube.data.polygons), len(q.data.polygons), r["per_object"]["Cyl"]["triangles_after"])


step("B2-O4 triangulate_for_export (13.2.3)", b2_o4, tag="B2")


def b2_o5():
    clear_scene()
    cube = make_cube("Cube", 2.0, (0, 0, 0))
    sub = make_cube("Sub", 2.0, (4, 0, 0)); sub.modifiers.new("Sub", "SUBSURF").levels = 2
    m1 = bpy.data.materials.new("M1"); m2 = bpy.data.materials.new("M2")
    cube.data.materials.append(m1); sub.data.materials.append(m2)
    coll = bpy.data.collections.new("Kit"); bpy.context.scene.collection.children.link(coll); coll.objects.link(sub)
    bpy.context.scene.collection.objects.unlink(sub)
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    bpy.context.view_layer.update()
    snap = snapshot()
    r = ok(call("get_triangle_budget", budget=100), must=["group_by", "budget", "total", "items", "over_budget"])
    _enc_ok(r)
    assert snapshot() == snap
    assert r["group_by"] == "OBJECT" and r["budget"] == 100 and r["total"] == 204 and r["over_budget"] is True, r
    assert [i["name"] for i in r["items"]] == ["Sub", "Cube"] and r["items"][0]["triangles"] == 192 and r["items"][1]["triangles"] == 12, r["items"]
    assert abs(sum(i["share"] for i in r["items"]) - 1.0) < 1e-6 and abs(r["items"][0]["share"] - 192 / 204) < 1e-6, [i["share"] for i in r["items"]]
    r = ok(call("get_triangle_budget"))
    assert r["budget"] is None and r["over_budget"] is None and r["total"] == 204, r
    r = ok(call("get_triangle_budget", budget=300))
    assert r["over_budget"] is False
    r = ok(call("get_triangle_budget", objects="Cube", budget=12))
    assert [i["name"] for i in r["items"]] == ["Cube"] and r["total"] == 12 and r["over_budget"] is False, r
    r = ok(call("get_triangle_budget", group_by="COLLECTION"))
    by = {i["name"]: i["triangles"] for i in r["items"]}
    assert by.get("Kit") == 192 and sum(by.values()) == 204, by
    r = ok(call("get_triangle_budget", group_by="MATERIAL"))
    by = {i["name"]: i["triangles"] for i in r["items"]}
    assert by.get("M2") == 192 and by.get("M1") == 12, by
    assert "COLLECTION" in _valid_text(call("get_triangle_budget", group_by="OWNER"))
    err(call("get_triangle_budget", objects="Nope"), "Nope")
    clear_scene()
    r = ok(call("get_triangle_budget", budget=10))
    assert r["total"] == 0 and r["items"] == [] and r["over_budget"] is False, r
    return "evaluated 192 + 12 = 204 sorted desc, shares sum 1, budget None/100/300, objects subset, by collection/material, empty scene 0"


step("B2-O5 get_triangle_budget (13.2.3, E13)", b2_o5, tag="B2", readonly=False)


# =========================================================================
# 13.2.4 collision (L2): create_collision, get_collision_info, check_collision_fit
# =========================================================================
def _mesh_state(ob):
    return (len(ob.data.vertices), len(ob.data.polygons), [m.name for m in ob.modifiers], tuple(round(x, 6) for x in ob.location))


def _assert_closed_hull(ob):
    """A collision proxy is a closed manifold shell with every face pointing away from its centroid (R-B2-34)."""
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table(); bm.edges.ensure_lookup_table()
    try:
        bad_edges = [e.index for e in bm.edges if not e.is_manifold or e.is_boundary]
        assert not bad_edges, "%s is not a closed manifold shell: %d bad edges" % (ob.name, len(bad_edges))
        centroid = sum((v.co for v in bm.verts), mathutils.Vector()) / max(1, len(bm.verts))
        inward = [f.index for f in bm.faces if f.normal.dot(f.calc_center_median() - centroid) < -1e-6]
        assert not inward, "%s has %d inward faces" % (ob.name, len(inward))
        degenerate = [f.index for f in bm.faces if f.calc_area() < 1e-9]
        assert not degenerate, "%s has %d degenerate faces" % (ob.name, len(degenerate))
    finally:
        bm.free()


def b2_c1():
    clear_scene()
    tor = _prim("primitive_torus_add", "Tor", major_segments=24, minor_segments=12)
    tor.data.materials.append(bpy.data.materials.new("TorMat"))
    st = _mesh_state(tor); src_tris = _eval_tris(tor)
    r = ok(call("create_collision", mesh="Tor", shape="CONVEX", profile="UNREAL"), must=["mesh", "shape", "profile", "proxies", "parent", "collection"])
    _enc_ok(r)
    p0 = r["proxies"][0]
    assert r["mesh"] == "Tor" and r["shape"] == "CONVEX" and r["profile"] == "UNREAL" and r["parent"] == "Tor" and len(r["proxies"]) == 1, r
    assert p0["name"] == "UCX_Tor_00" and p0["triangles"] > 0 and 1.7 <= p0["volume_ratio"] <= 2.3, p0        # R-B2-34: analytic 1.99 (b2check's 3.87 summed leftover torus faces)
    px = bpy.data.objects["UCX_Tor_00"]
    _assert_closed_hull(px)
    assert px.type == "MESH" and px.display_type == "WIRE" and px.hide_render is True and px.parent is tor and len(px.data.materials) == 0, (px.display_type, px.hide_render, px.parent)
    assert _eval_tris(px) == p0["triangles"], (_eval_tris(px), p0["triangles"])
    assert all(len(f.vertices) == 3 for f in px.data.polygons), "hull is not triangulated"
    assert _mesh_state(tor) == st, "source changed"
    # convexity: every torus vertex inside or on the hull (bvh nearest normal test)
    from mathutils.bvhtree import BVHTree
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(px, dg)
    inv = px.matrix_world.inverted()
    outside = 0
    for v in tor.data.vertices:
        pt = inv @ (tor.matrix_world @ v.co)
        loc, nor, idx, dist = bvh.find_nearest(pt)
        if nor.dot(pt - loc) > 1e-4:
            outside += 1
    assert outside == 0, "%d torus vertices outside the hull" % outside
    r = ok(call("create_collision", mesh="Tor", shape="CONVEX", profile="UNREAL"))
    assert r["proxies"][0]["name"] == "UCX_Tor_01", r["proxies"]
    r = ok(call("create_collision", mesh="Tor", shape="SLICED_CONVEX", count=3, axis="Z", profile="UNREAL"))
    assert [q["name"] for q in r["proxies"]] == ["UCX_Tor_02", "UCX_Tor_03", "UCX_Tor_04"], r["proxies"]
    zs = []
    for q in r["proxies"]:
        o = bpy.data.objects[q["name"]]; assert o.parent is tor and o.display_type == "WIRE"
        _assert_closed_hull(o)
        zs.append((min((o.matrix_world @ v.co).z for v in o.data.vertices), max((o.matrix_world @ v.co).z for v in o.data.vertices)))
    assert min(z[0] for z in zs) <= min((tor.matrix_world @ v.co).z for v in tor.data.vertices) + 1e-3 and max(z[1] for z in zs) >= max((tor.matrix_world @ v.co).z for v in tor.data.vertices) - 1e-3, zs
    r = ok(call("create_collision", mesh="Tor", shape="BOX", profile="GODOT"))
    assert r["proxies"][0]["name"] == "Tor-col" and r["proxies"][0]["triangles"] == 12, r["proxies"]
    box = bpy.data.objects["Tor-col"]; bpy.context.view_layer.update()
    assert all(abs(a - b) < 1e-3 for a, b in zip(box.dimensions, tor.dimensions)), (tuple(box.dimensions), tuple(tor.dimensions))
    r = ok(call("create_collision", mesh="Tor", shape="BOX", profile="GENERIC", padding=0.1))
    pb = bpy.data.objects[r["proxies"][0]["name"]]; bpy.context.view_layer.update()
    assert r["proxies"][0]["name"].startswith("Tor_COL") and all(abs(a - (b + 0.2)) < 1e-3 for a, b in zip(pb.dimensions, tor.dimensions)), (r["proxies"][0]["name"], tuple(pb.dimensions))
    assert ok(call("create_collision", mesh="Tor", shape="CONVEX", profile="GODOT"))["proxies"][0]["name"] == "Tor-convcol"
    assert ok(call("create_collision", mesh="Tor", shape="SPHERE", profile="UNITY"))["proxies"][0]["name"] == "Tor_Collider"
    r = ok(call("create_collision", mesh="Tor", shape="CAPSULE", profile="BEVY"))
    assert r["proxies"][0]["name"].startswith("Tor_COL"), r["proxies"]
    r = ok(call("create_collision", mesh="Tor", shape="CYLINDER", profile="TIMBERMESH", parent=False))
    assert r["proxies"][0]["name"].startswith("Tor_COL") and r["parent"] is None and bpy.data.objects[r["proxies"][0]["name"]].parent is None, r
    for nm in ("Tor_Collider",):
        o = bpy.data.objects[nm]; bpy.context.view_layer.update()
        assert all(o.dimensions[i] >= tor.dimensions[i] - 1e-3 for i in range(3)), (nm, tuple(o.dimensions), tuple(tor.dimensions))
    r = ok(call("create_collision", mesh="Tor", shape="MESH_DECIMATED", decimate_ratio=0.1, profile="UNREAL", collection="Col"))
    q = r["proxies"][0]
    assert q["name"] == "UCX_Tor_05" and 0.04 * src_tris <= q["triangles"] <= 0.25 * src_tris and r["collection"] == "Col", (q, src_tris)
    assert bpy.data.collections.get("Col") is not None and q["name"] in bpy.data.collections["Col"].objects
    assert _mesh_state(tor) == st, "source changed"
    assert "CONVEX" in _valid_text(call("create_collision", mesh="Tor", shape="NOPE"))
    assert "GODOT" in _valid_text(call("create_collision", mesh="Tor", profile="PS5"))
    err(call("create_collision", mesh="Tor", shape="SLICED_CONVEX", count=0)); err(call("create_collision", mesh="Tor", shape="SLICED_CONVEX", count=33))
    err(call("create_collision", mesh="Tor", shape="SLICED_CONVEX", axis="W"))
    bpy.ops.object.light_add(type="POINT"); bpy.context.active_object.name = "Lamp"
    err(call("create_collision", mesh="Lamp"), "not a mesh"); err(call("create_collision", mesh="Nope"), "not found")
    assert bpy.context.mode == "OBJECT"
    return "UCX_Tor_00 hull ratio %.2f (all verts inside), _01, sliced _02.._04 cover z, BOX dims (+padding), GODOT/UNITY/GENERIC/BEVY/TIMBERMESH names, decimated, source untouched, refusals" % p0["volume_ratio"]


step("B2-C1 create_collision (13.2.4, E11)", b2_c1, tag="B2")


def b2_c2():
    snap = snapshot()
    r = ok(call("get_collision_info", mesh="Tor"), must=["mesh", "proxies"])
    _enc_ok(r)
    assert snapshot() == snap
    names = {q["name"] for q in r["proxies"]}
    assert names >= {"UCX_Tor_00", "UCX_Tor_01", "UCX_Tor_02", "Tor-col", "Tor-convcol", "Tor_Collider", "Tor_COL"}, sorted(names)
    for q in r["proxies"]:
        for k in ("name", "profile", "triangles", "is_child"):
            assert k in q, q
        assert q["is_child"] is (bpy.data.objects[q["name"]].parent is bpy.data.objects["Tor"]), q
        assert q["triangles"] == _eval_tris(bpy.data.objects[q["name"]]), q
    by = {q["name"]: q["profile"] for q in r["proxies"]}
    assert by["UCX_Tor_00"] == "UNREAL" and by["Tor-col"] == "GODOT" and by["Tor_Collider"] == "UNITY" and by["Tor_COL"] == "GENERIC", by
    make_cube("Lonely", 2.0, (20, 0, 0))
    r = ok(call("get_collision_info", mesh="Lonely"))
    assert r["proxies"] == [], r
    err(call("get_collision_info", mesh="Nope"), "not found")
    return "%d proxies listed with profile/triangles/is_child; none -> []" % len(names)


step("B2-C2 get_collision_info (13.2.4)", b2_c2, tag="B2")


def b2_c3():
    clear_scene()
    box = make_cube("Box", 2.0, (0, 0, 0))
    ok(call("create_collision", mesh="Box", shape="BOX", profile="GENERIC"))
    r = ok(call("check_collision_fit", mesh="Box"), must=["mesh", "samples", "proxies", "covered_fraction", "max_outside_distance", "inside_samples"])
    _enc_ok(r)
    assert r["samples"] == 2000 and r["proxies"] == ["Box_COL"] and r["covered_fraction"] > 0.95 and r["max_outside_distance"] < 1e-3, r
    assert abs(r["inside_samples"] / r["samples"] - r["covered_fraction"]) < 1e-9, r
    r2 = ok(call("check_collision_fit", mesh="Box"))
    assert r2["covered_fraction"] == r["covered_fraction"] and r2["inside_samples"] == r["inside_samples"], "not deterministic (seed 0)"
    bpy.data.objects["Box_COL"].scale = (0.5, 0.5, 0.5); bpy.context.view_layer.update()
    r = ok(call("check_collision_fit", mesh="Box"))
    assert r["covered_fraction"] < 0.6 and r["max_outside_distance"] > 0.1, r               # discriminating: an undersized proxy must score low
    r = ok(call("check_collision_fit", mesh="Box", samples=10))
    assert r["samples"] == 10 and 0 <= r["inside_samples"] <= 10
    err(call("check_collision_fit", mesh="Box", samples=0)); err(call("check_collision_fit", mesh="Box", samples=20001))
    _prim("primitive_monkey_add", "Suz")
    ok(call("create_collision", mesh="Suz", shape="CONVEX", profile="UNREAL"))
    r = ok(call("check_collision_fit", mesh="Suz"))
    assert r["covered_fraction"] > 0.95 and r["proxies"] == ["UCX_Suz_00"], r                   # doc step 4
    make_cube("Lonely", 2.0, (20, 0, 0))
    err(call("check_collision_fit", mesh="Lonely"), "no collision proxies")
    err(call("check_collision_fit", mesh="Nope"), "not found")
    assert bpy.context.mode == "OBJECT"
    return "box proxy > 0.95 (deterministic), undersized proxy < 0.6, samples bounds, Suz hull > 0.95, no-proxy error"


step("B2-C3 check_collision_fit (13.2.4)", b2_c3, tag="B2")

clear_scene()

# =========================================================================
# 13.2.5 sockets, pivots and hierarchy (L3): add_socket, list_sockets, set_pivot, snap_to_grid, build_export_hierarchy
# =========================================================================
def _wm(ob):
    bpy.context.view_layer.update()
    return [round(x, 5) for row in ob.matrix_world for x in row]


def b2_s1():
    clear_scene()
    suz = _prim("primitive_monkey_add", "Suz")
    r = ok(call("add_socket", parent="Suz", name="Muzzle", location="0,1,0", profile="UNREAL"),
           must=["name", "parent", "bone", "profile", "location_world", "rotation_world", "matrix_world"])
    _enc_ok(r)
    sk = bpy.data.objects["SOCKET_Muzzle"]
    assert r["name"] == "SOCKET_Muzzle" and r["parent"] == "Suz" and r["bone"] is None and r["profile"] == "UNREAL", r
    assert sk.type == "EMPTY" and sk.parent is suz and abs(sk.empty_display_size - 0.1) < 1e-6 and close(sk.location, (0, 1, 0)), (sk.parent, sk.empty_display_size, tuple(sk.location))
    assert close(r["location_world"], (0, 1, 0)) and len(r["matrix_world"]) == 4 and all(len(row) == 4 for row in r["matrix_world"]), r
    suz.location = (5, 0, 0); bpy.context.view_layer.update()
    assert close(sk.matrix_world.translation, (5, 1, 0)), tuple(sk.matrix_world.translation)
    r = ok(call("add_socket", parent="Suz", name="Grip", location="5,1,0", space="WORLD", rotation="0,0,90", display_size=0.25))
    g = bpy.data.objects["SOCKET_Grip"]; bpy.context.view_layer.update()
    assert close(g.location, (0, 1, 0)) and close(r["location_world"], (5, 1, 0)) and abs(g.empty_display_size - 0.25) < 1e-6, (tuple(g.location), r["location_world"])
    assert abs(r["rotation_world"][2] - 90.0) < 1e-3, r["rotation_world"]                      # degrees (H20)
    suz.location = (0, 0, 0)
    # TIMBERMESH: needs a # root in the parent chain (doc gotcha)
    err(call("add_socket", parent="Suz", name="Slot", profile="TIMBERMESH"), "#")
    assert "#Slot" not in bpy.data.objects
    bpy.ops.object.empty_add(); root = bpy.context.active_object; root.name = "#Prop"; suz.parent = root
    r = ok(call("add_socket", parent="Suz", name="Slot", profile="TIMBERMESH"))
    assert r["name"] == "#Slot" and bpy.data.objects["#Slot"].parent is suz, r
    assert ok(call("add_socket", parent="Suz", name="Plain", profile="UNITY"))["name"] == "Plain"
    assert ok(call("add_socket", parent="Suz", name="Gen", profile="GENERIC"))["name"] == "Gen"
    # duplicate name: Blender's suffix is reported, not the requested name (E12)
    r = ok(call("add_socket", parent="Suz", name="Muzzle", profile="UNREAL"))
    assert r["name"] == "SOCKET_Muzzle.001" and r["name"] in bpy.data.objects, r["name"]
    # snap_to VERTEX / SURFACE
    r = ok(call("add_socket", parent="Suz", name="V3", snap_to="VERTEX", vertex_index=3))
    bpy.context.view_layer.update()
    expect = suz.matrix_world @ suz.data.vertices[3].co
    assert close(r["location_world"], expect, 1e-4) and close(bpy.data.objects["SOCKET_V3"].matrix_world.translation, expect, 1e-4), (r["location_world"], tuple(expect))
    r = ok(call("add_socket", parent="Suz", name="Surf", location="0,-3,0", snap_to="SURFACE"))
    hit, loc, nor, idx = suz.closest_point_on_mesh(suz.matrix_world.inverted() @ mathutils.Vector(r["location_world"]))
    assert hit and (loc - (suz.matrix_world.inverted() @ mathutils.Vector(r["location_world"]))).length < 1e-3, r["location_world"]
    err(call("add_socket", parent="Suz", name="V9", snap_to="VERTEX", vertex_index=999999), "range")
    err(call("add_socket", parent="Suz", name="V9", snap_to="VERTEX"))
    # bone
    bpy.ops.object.armature_add(); arm = bpy.context.active_object; arm.name = "Arm"
    bone_name = arm.data.bones[0].name
    r = ok(call("add_socket", parent="Arm", name="OnBone", bone=bone_name))
    ob = bpy.data.objects["SOCKET_OnBone"]
    assert r["bone"] == bone_name and ob.parent is arm and ob.parent_type == "BONE" and ob.parent_bone == bone_name, (r["bone"], ob.parent_type, ob.parent_bone)
    err(call("add_socket", parent="Arm", name="X", bone="NoBone"), "NoBone")
    err(call("add_socket", parent="Suz", name="X", bone=bone_name))                               # bone on a non-armature parent
    assert "WORLD" in _valid_text(call("add_socket", parent="Suz", name="X", space="SIDEWAYS"))
    assert "TIMBERMESH" in _valid_text(call("add_socket", parent="Suz", name="X", profile="PS5"))
    err(call("add_socket", parent="Nope", name="X"), "not found")
    err(call("add_socket", parent="Suz", name="X", location="a,b"))
    assert bpy.context.mode == "OBJECT"
    return "doc step 5: SOCKET_Muzzle parented, world matrix follows the parent; WORLD space + degrees; TIMBERMESH root rule (#Slot); UNITY/GENERIC plain; .001 suffix; VERTEX/SURFACE snap; bone parent; refusals"


step("B2-S1 add_socket (13.2.5, E12, H20)", b2_s1, tag="B2")


def b2_s2():
    bpy.ops.object.empty_add(); helper = bpy.context.active_object; helper.name = "Helper"; helper.parent = bpy.data.objects["Suz"]
    snap = snapshot()
    r = ok(call("list_sockets"), must=["parent", "count", "sockets"])
    _enc_ok(r)
    assert snapshot() == snap
    names = {sk["name"] for sk in r["sockets"]}
    assert {"SOCKET_Muzzle", "SOCKET_Grip", "SOCKET_Muzzle.001", "SOCKET_V3", "SOCKET_Surf", "SOCKET_OnBone", "#Slot"} <= names, sorted(names)
    assert "Helper" not in names and "Plain" not in names and "#Prop" in names, sorted(names)      # # prefix matches, plain names do not, without a parent filter
    assert r["count"] == len(r["sockets"]) and r["parent"] is None
    for sk in r["sockets"]:
        for k in ("name", "profile", "parent", "bone", "location_local", "location_world", "rotation_local"):
            assert k in sk, (k, sk)
        assert sk["profile"] in ("UNREAL", "TIMBERMESH", "GENERIC"), sk
    by = {sk["name"]: sk for sk in r["sockets"]}
    assert by["SOCKET_Muzzle"]["profile"] == "UNREAL" and by["#Slot"]["profile"] == "TIMBERMESH" and by["SOCKET_OnBone"]["bone"] == bpy.data.objects["Arm"].data.bones[0].name, (by["SOCKET_Muzzle"], by["#Slot"], by["SOCKET_OnBone"])
    assert close(by["SOCKET_Muzzle"]["location_local"], (0, 1, 0)), by["SOCKET_Muzzle"]
    r = ok(call("list_sockets", parent="Suz"))
    names = {sk["name"] for sk in r["sockets"]}
    assert "Helper" in names and "Plain" in names and "SOCKET_OnBone" not in names and r["parent"] == "Suz", sorted(names)   # parent given: every EMPTY child
    assert all(sk["parent"] == "Suz" for sk in r["sockets"])
    r = ok(call("list_sockets", parent="Arm"))
    assert {sk["name"] for sk in r["sockets"]} == {"SOCKET_OnBone"}, r["sockets"]
    err(call("list_sockets", parent="Nope"), "not found")
    return "%d sockets listed (SOCKET_/# prefixes, profiles, bone), parent filter lists every empty child, unknown parent refused" % len(by)


step("B2-S2 list_sockets (13.2.5)", b2_s2, tag="B2", readonly=False)


def b2_s3():
    clear_scene()
    a = make_cube("A", 2.0, (0, 0, 0)); a.scale = (2, 1, 1); a.rotation_euler = (0, 0, math.radians(45))
    b = make_cube("B", 2.0, (5, 0, 3))
    bpy.context.view_layer.update()
    wa, wb = _world_verts(a), _world_verts(b)
    r = ok(call("set_pivot", objects="A,B", mode="BOTTOM_CENTER"), must=["objects", "mode", "per_object"])
    _enc_ok(r)
    bpy.context.view_layer.update()
    assert set(r["per_object"]) == {"A", "B"} and all("new_location" in v for v in r["per_object"].values()), r["per_object"]
    assert _world_verts(a) == wa and _world_verts(b) == wb, "world geometry moved"
    assert abs(min(v.co.z for v in a.data.vertices)) < 1e-6 and abs(min(v.co.z for v in b.data.vertices)) < 1e-6
    assert close(r["per_object"]["B"]["new_location"], b.location, 1e-5), (r["per_object"]["B"], tuple(b.location))
    ok(call("set_pivot", objects="B", mode="TOP_CENTER")); bpy.context.view_layer.update()
    assert abs(max(v.co.z for v in b.data.vertices)) < 1e-6 and _world_verts(b) == wb
    ok(call("set_pivot", objects="B", mode="CENTER")); bpy.context.view_layer.update()
    assert all(abs(sum(v.co[i] for v in b.data.vertices)) < 1e-5 for i in range(3)) and _world_verts(b) == wb
    ok(call("set_pivot", objects="B", mode="WORLD_ORIGIN")); bpy.context.view_layer.update()
    assert close(b.location, (0, 0, 0)) and _world_verts(b) == wb
    bpy.context.scene.cursor.location = (1, 1, 1)
    ok(call("set_pivot", objects="B", mode="CURSOR")); bpy.context.view_layer.update()
    assert close(b.location, (1, 1, 1)) and _world_verts(b) == wb
    bpy.context.scene.cursor.location = (0, 0, 0)
    ok(call("set_pivot", objects="B", mode="CUSTOM", location="2,0,0")); bpy.context.view_layer.update()
    assert close(b.location, (2, 0, 0)) and _world_verts(b) == wb
    err(call("set_pivot", objects="B", mode="CUSTOM"))
    assert "BOTTOM_CENTER" in _valid_text(call("set_pivot", objects="B", mode="LEFT"))
    rr = call("set_pivot", objects="B,Nope"); err(rr, "Nope"); assert (rr.get("result") or {}).get("unknown") == ["Nope"] and close(b.location, (2, 0, 0)), "applied despite an unknown name"
    assert bpy.context.mode == "OBJECT"
    return "batch BOTTOM_CENTER on a rotated+scaled object keeps world geometry; TOP/CENTER/WORLD_ORIGIN/CURSOR/CUSTOM; refusals"


step("B2-S3 set_pivot (13.2.5, H11)", b2_s3, tag="B2")


def b2_s4():
    clear_scene()
    c = make_cube("G", 2.0, (0.4, 1.6, 0.2))
    r = ok(call("snap_to_grid", objects="G", grid=1.0), must=["objects", "grid", "axes", "what", "per_object"])
    _enc_ok(r)
    bpy.context.view_layer.update()
    assert close(c.location, (0, 2, 0), 1e-6) and close(r["per_object"]["G"]["before"], (0.4, 1.6, 0.2), 1e-6) and close(r["per_object"]["G"]["after"], (0, 2, 0), 1e-6), (tuple(c.location), r["per_object"])
    assert r["grid"] == 1.0 and r["axes"] == "XYZ" and r["what"] == "ORIGIN"
    c.location = (0.4, 1.6, 0.2)
    ok(call("snap_to_grid", objects="G", grid=1.0, axes="XY")); bpy.context.view_layer.update()
    assert close(c.location, (0, 2, 0.2), 1e-6), tuple(c.location)
    c.location = (0.4, 1.6, 0.2)
    ok(call("snap_to_grid", objects="G", grid=0.5)); bpy.context.view_layer.update()
    assert close(c.location, (0.5, 1.5, 0.0), 1e-6), tuple(c.location)
    c.location = (0.3, 0.3, 0.3)
    ok(call("snap_to_grid", objects="G", grid=1.0, what="BOUNDS_MIN")); bpy.context.view_layer.update()
    mn = [min((c.matrix_world @ v.co)[i] for v in c.data.vertices) for i in range(3)]
    assert all(abs(x - round(x)) < 1e-6 for x in mn), mn
    c.location = (0.3, 0.3, 0.3)
    ok(call("snap_to_grid", objects="G", grid=1.0, what="BOUNDS_CENTER")); bpy.context.view_layer.update()
    ce = [(min((c.matrix_world @ v.co)[i] for v in c.data.vertices) + max((c.matrix_world @ v.co)[i] for v in c.data.vertices)) / 2 for i in range(3)]
    assert all(abs(x - round(x)) < 1e-6 for x in ce), ce
    err(call("snap_to_grid", objects="G", grid=0)); err(call("snap_to_grid", objects="G", grid=-1))
    err(call("snap_to_grid", objects="G", axes="W"))
    assert "BOUNDS_MIN" in _valid_text(call("snap_to_grid", objects="G", what="EDGE"))
    err(call("snap_to_grid", objects="Nope"), "Nope")
    return "origin (0.4,1.6,0.2) -> (0,2,0); axes XY; grid 0.5; BOUNDS_MIN / BOUNDS_CENTER on the grid; refusals"


step("B2-S4 snap_to_grid (13.2.5)", b2_s4, tag="B2")


def b2_s5():
    clear_scene()
    _prim("primitive_monkey_add", "Suz")
    ok(call("generate_lods", mesh="Suz", ratios="0.5"))
    ok(call("create_collision", mesh="Suz_LOD0", shape="CONVEX", profile="UNREAL"))
    ok(call("add_socket", parent="Suz_LOD0", name="Muzzle", profile="UNREAL"))
    before = {o.name: _wm(o) for o in bpy.data.objects}
    r = ok(call("build_export_hierarchy", root_name="PropRoot", meshes="Suz_LOD0", lods="Suz_LOD1", collision="UCX_Suz_LOD0_00", sockets="SOCKET_Muzzle", profile="UNREAL"),
           must=["root", "profile", "created_root", "tree", "custom_props_set"])
    _enc_ok(r)
    root = bpy.data.objects["PropRoot"]
    assert r["root"] == "PropRoot" and r["created_root"] is True and root.type == "EMPTY" and r["profile"] == "UNREAL", r
    assert bpy.data.objects["Suz_LOD0"].parent is root and bpy.data.objects["Suz_LOD1"].parent is root, "LODs not root siblings"
    assert bpy.data.objects["UCX_Suz_LOD0_00"].parent is bpy.data.objects["Suz_LOD0"] and bpy.data.objects["SOCKET_Muzzle"].parent is bpy.data.objects["Suz_LOD0"]
    assert bpy.data.objects["Suz_LOD1"]["lod_group"] == 1 and bpy.data.objects["Suz_LOD0"]["lod_group"] == 0, (bpy.data.objects["Suz_LOD1"].get("lod_group"), bpy.data.objects["Suz_LOD0"].get("lod_group"))
    assert r["custom_props_set"].get("Suz_LOD1", {}).get("lod_group") == 1, r["custom_props_set"]
    tree = r["tree"]; assert tree["name"] == "PropRoot" and {c["name"] for c in tree["children"]} >= {"Suz_LOD0", "Suz_LOD1"}, tree
    lod0 = next(c for c in tree["children"] if c["name"] == "Suz_LOD0")
    assert {c["name"] for c in lod0["children"]} >= {"UCX_Suz_LOD0_00", "SOCKET_Muzzle"}, lod0
    for n, m in before.items():
        assert _wm(bpy.data.objects[n]) == m, "%s moved in world space" % n
    r = ok(call("build_export_hierarchy", root_name="PropRoot", meshes="Suz_LOD0", profile="UNREAL"))
    assert r["created_root"] is False and "PropRoot.001" not in bpy.data.objects, "root re-created"
    ok(call("create_collision", mesh="Suz_LOD0", shape="BOX", profile="GODOT"))
    r = ok(call("build_export_hierarchy", root_name="GodotRoot", meshes="Suz_LOD0", collision="Suz_LOD0-col", profile="GODOT"))
    assert bpy.data.objects["Suz_LOD0-col"].parent is bpy.data.objects["GodotRoot"], "GODOT collision must be a root sibling"
    rr = call("build_export_hierarchy", root_name="R2", meshes="Suz_LOD0,Nope")
    err(rr, "Nope"); assert (rr.get("result") or {}).get("unknown") == ["Nope"] and "R2" not in bpy.data.objects, rr.get("result")
    assert "GODOT" in _valid_text(call("build_export_hierarchy", root_name="R3", meshes="Suz_LOD0", profile="PS5"))
    assert bpy.context.mode == "OBJECT"
    return "UNREAL root + LOD siblings with lod_group, UCX + socket under the mesh, tree matches bpy, world transforms kept, root reused, GODOT -col sibling, unknown name refused"


step("B2-S5 build_export_hierarchy (13.2.5)", b2_s5, tag="B2")


# =========================================================================
# 13.2.6 engine export (L3): get_export_preset, export_for_engine (+ set_scene_units doc step 8)
# =========================================================================
PRESETS = {
    "UNITY": ("fbx", {"axis_forward": "-Z", "axis_up": "Y", "apply_scale_options": "FBX_SCALE_ALL", "global_scale": 1.0, "use_tspace": True,
                      "mesh_smooth_type": "FACE", "add_leaf_bones": False, "bake_space_transform": False, "use_custom_props": True}),
    "UNREAL": ("fbx", {"axis_forward": "-Y", "axis_up": "Z", "global_scale": 1.0, "apply_scale_options": "FBX_SCALE_NONE", "use_tspace": True,
                       "mesh_smooth_type": "FACE", "add_leaf_bones": False, "use_custom_props": True}),
    "GODOT": ("glb", {"export_yup": True, "export_apply": True, "export_tangents": True, "export_extras": True, "export_attributes": True,
                      "export_vertex_color": "MATERIAL", "export_image_format": "AUTO"}),
    "BEVY": ("glb", {"export_yup": True, "export_apply": True, "export_tangents": True, "export_extras": True, "export_attributes": True,
                     "export_vertex_color": "MATERIAL", "export_image_format": "AUTO"}),
    "TIMBERMESH": ("timbermesh", {"merge_meshes": True, "single_animation": True, "use_vertex_animations": False}),
    "GENERIC_FBX": ("fbx", {}),
    "GENERIC_GLTF": ("glb", {}),
}


def b2_e1():
    snap = snapshot()
    for eng, (fmt, kw) in PRESETS.items():
        r = ok(call("get_export_preset", engine=eng), must=["engine", "format", "exporter", "kwargs", "axis", "units", "naming", "notes"])
        _enc_ok(r)
        assert r["engine"] == eng and r["format"] == fmt and r["kwargs"] == kw, (eng, r["format"], r["kwargs"])
        assert r["exporter"] == {"fbx": "fbx", "glb": "gltf", "timbermesh": "timbermesh"}[fmt], r["exporter"]
        assert set(r["naming"]) >= {"collision", "socket", "lod"} and isinstance(r["notes"], list), r
    assert snapshot() == snap
    u = ok(call("get_export_preset", engine="UNREAL"))
    assert u["units"] == {"scale_length": 0.01, "length_unit": "CENTIMETERS"} and u["axis"] == {"forward": "-Y", "up": "Z"} and any("set_scene_units" in n for n in u["notes"]), u
    assert ok(call("get_export_preset", engine="UNITY"))["units"]["scale_length"] == 1.0
    assert ok(call("get_export_preset", engine="UNREAL"))["naming"]["collision"].startswith("UCX_")
    assert "GENERIC_GLTF" in _valid_text(call("get_export_preset", engine="PS5"))
    err(call("get_export_preset"))
    return "7 preset entries verbatim (format, exporter, kwargs, units, axis, naming), unknown engine lists the 7"


step("B2-E1 get_export_preset (13.2.6, H6)", b2_e1, tag="B2", readonly=True)


def _manifest(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _gun_fixture():
    clear_scene()
    # R-B2-35 (clean primitive): a pure-Blender FBX round trip of Suzanne drops ONE triangle (968 -> 967) even after
    # remove_doubles + dissolve_degenerate and even pre-triangulated (QA probe 2026-10-21, both installs); a UV sphere
    # round-trips exactly (960 -> 960). The fixture keeps the doc-step names on a 32x16 UV sphere.
    _prim("primitive_uv_sphere_add", "Suz", segments=32, ring_count=16)
    ok(call("generate_lods", mesh="Suz", ratios="0.5,0.25"))
    ok(call("create_collision", mesh="Suz_LOD0", shape="SLICED_CONVEX", count=3, profile="UNREAL"))
    ok(call("add_socket", parent="Suz_LOD0", name="Muzzle", profile="UNREAL"))
    ok(call("build_export_hierarchy", root_name="Gun", meshes="Suz_LOD0", lods="Suz_LOD1,Suz_LOD2",
            collision="UCX_Suz_LOD0_00,UCX_Suz_LOD0_01,UCX_Suz_LOD0_02", sockets="SOCKET_Muzzle", profile="UNREAL"))
    bpy.context.scene.unit_settings.scale_length = 1.0


EXPORT_KEYS = ["filepath", "engine", "format", "exporter", "size_bytes", "manifest_path", "objects", "triangles", "materials",
               "textures_copied", "validation", "warnings", "exporter_kwargs", "timbermesh_root"]
GUN_NAMES = {"Gun", "Suz_LOD0", "Suz_LOD1", "Suz_LOD2", "UCX_Suz_LOD0_00", "UCX_Suz_LOD0_01", "UCX_Suz_LOD0_02", "SOCKET_Muzzle"}


def b2_e2():
    _gun_fixture()
    lod0 = bpy.data.objects["Suz_LOD0"]
    polys = {o.name: len(o.data.polygons) for o in bpy.data.objects if o.type == "MESH"}
    mods = {o.name: [m.name for m in o.modifiers] for o in bpy.data.objects}
    snap = snapshot()
    p = os.path.join(B2DIR, "gun.fbx")
    r = ok(call("export_for_engine", objects="Gun", engine="UNREAL", filepath=p), must=EXPORT_KEYS)
    _enc_ok(r)
    assert snapshot()["selected"] == snap["selected"] and snapshot()["active"] == snap["active"], "selection not restored"
    assert r["filepath"] == p and os.path.exists(p) and r["size_bytes"] == os.path.getsize(p) > 0, (r["filepath"], r["size_bytes"])
    assert r["engine"] == "UNREAL" and r["format"] == "fbx" and r["exporter"] == "fbx" and r["timbermesh_root"] is None, r
    assert r["manifest_path"] == p + ".manifest.json" and os.path.exists(r["manifest_path"]), r["manifest_path"]
    m = _manifest(r["manifest_path"])
    assert m["manifest_version"] == 1 and m["preset"] == "UNREAL" and m["blender"] and m["written_at"] and "manifest_path" not in m, sorted(m)
    for k in EXPORT_KEYS:
        if k != "manifest_path":
            assert m[k] == r[k], ("manifest differs from the reply at", k)
    assert set(r["objects"]) >= GUN_NAMES, sorted(r["objects"])
    assert r["triangles"] == sum(_eval_tris(bpy.data.objects[n]) for n in r["objects"] if bpy.data.objects[n].type == "MESH"), r["triangles"]
    assert r["exporter_kwargs"]["axis_up"] == "Z" and r["exporter_kwargs"]["apply_scale_options"] == "FBX_SCALE_NONE", r["exporter_kwargs"]
    assert ok(call("get_export_preset", engine="UNREAL"))["kwargs"].items() <= r["exporter_kwargs"].items(), "preset not applied verbatim"
    assert any("set_scene_units" in w for w in r["warnings"]), r["warnings"]                   # scale_length 1.0 (doc 8)
    assert r["validation"] is not None and r["validation"]["verdict"] in ("PASS", "WARN") and {"verdict", "fail", "warn"} <= set(r["validation"]), r["validation"]
    # D6 non-destructive triangulation: scene meshes untouched, no modifier left
    assert {o.name: len(o.data.polygons) for o in bpy.data.objects if o.type == "MESH"} == polys and {o.name: [mm.name for mm in o.modifiers] for o in bpy.data.objects} == mods, "export changed the scene"
    # re-import in a fresh scene: names, hierarchy, props, socket, triangulated file
    clear_scene()
    ok(call("import_file", filepath=p))
    names = set(bpy.data.objects.keys())
    assert GUN_NAMES <= names, sorted(names)
    assert bpy.data.objects["Suz_LOD1"]["lod_group"] == 1 and bpy.data.objects["SOCKET_Muzzle"].parent.name == "Suz_LOD0", (bpy.data.objects["Suz_LOD1"].get("lod_group"), bpy.data.objects["SOCKET_Muzzle"].parent)
    imported_tris = sum(_eval_tris(bpy.data.objects[n]) for n in names if bpy.data.objects[n].type == "MESH")
    assert imported_tris == m["triangles"], (imported_tris, m["triangles"])
    assert all(len(pg.vertices) == 3 for n in names if bpy.data.objects[n].type == "MESH" for pg in bpy.data.objects[n].data.polygons), "file not triangulated"
    # include_* switches
    _gun_fixture()
    r = ok(call("export_for_engine", objects="Gun", engine="UNREAL", filepath=os.path.join(B2DIR, "gun_nocol.fbx"), include_collision=False, include_sockets=False, include_lods=False))
    assert not any(n.startswith("UCX_") or n.startswith("SOCKET_") or n in ("Suz_LOD1", "Suz_LOD2") for n in r["objects"]) and "Suz_LOD0" in r["objects"], r["objects"]
    # E2b: validation FAIL -> no file, no manifest; force -> file + FAIL recorded
    clear_scene(); prop = make_cube("Prop", 2.0, (0, 0, 0)); prop.scale = (2, 1, 1); bpy.context.view_layer.update()
    bad = os.path.join(B2DIR, "e2b.fbx")
    rr = call("export_for_engine", objects="Prop", engine="UNREAL", filepath=bad)
    msg = err(rr, "validation FAIL")
    assert "force" in msg and (rr.get("result") or {}).get("validation", {}).get("verdict") == "FAIL", rr.get("result")
    assert not os.path.exists(bad) and not os.path.exists(bad + ".manifest.json"), "refused export wrote a file"
    r = ok(call("export_for_engine", objects="Prop", engine="UNREAL", filepath=bad, overrides='{"force": true}'))
    assert os.path.exists(bad) and _manifest(r["manifest_path"])["validation"]["verdict"] == "FAIL", r["validation"]
    assert close(prop.scale, (2, 1, 1)), "export applied the transform on the scene object"
    r = ok(call("export_for_engine", objects="Prop", engine="UNREAL", filepath=bad, validate=False))
    assert r["validation"] is None, r["validation"]
    # overrides: live-enum validation (E8), JSON shapes (F4 class), unknown kwarg
    sg = os.path.join(B2DIR, "e2_sg.fbx")
    rr = call("export_for_engine", objects="Prop", engine="UNREAL", filepath=sg, validate=False, overrides='{"mesh_smooth_type": "SMOOTH_GROUP"}')
    live = [i.identifier for i in bpy.ops.export_scene.fbx.get_rna_type().properties["mesh_smooth_type"].enum_items]
    if "SMOOTH_GROUP" in live:
        assert ok(rr)["exporter_kwargs"]["mesh_smooth_type"] == "SMOOTH_GROUP" and os.path.exists(sg)
    else:
        mm = err(rr, "mesh_smooth_type"); assert "FACE" in mm and not os.path.exists(sg), A(mm)
    err(call("export_for_engine", objects="Prop", engine="UNREAL", filepath=sg, validate=False, overrides="not json"))
    rr = call("export_for_engine", objects="Prop", engine="UNREAL", filepath=os.path.join(B2DIR, "e2_scalar.fbx"), validate=False, overrides="7")
    assert rr.get("status") == "error" or ("error" in (rr.get("result") or {}) and rr.get("result") != 7), A(json.dumps(rr)[:200])
    err(call("export_for_engine", objects="Prop", engine="UNREAL", filepath=os.path.join(B2DIR, "nope_dir", "x.fbx"), validate=False), "does not exist")
    assert "GENERIC_GLTF" in _valid_text(call("export_for_engine", objects="Prop", engine="PS5", filepath=bad))
    err(call("export_for_engine", engine="UNREAL", filepath=bad))
    rr = call("export_for_engine", objects="Prop,Nope", engine="UNREAL", filepath=os.path.join(B2DIR, "e2_unknown.fbx"), validate=False)
    err(rr, "Nope"); assert not os.path.exists(os.path.join(B2DIR, "e2_unknown.fbx"))
    # textures COPY / NONE
    clear_scene(); tc = make_cube("Tex", 2.0, (0, 0, 0)); tc.data.uv_layers.new(name="UVMap")
    img_path = os.path.join(B2DIR, "tex_src.png")
    img = bpy.data.images.new("TexImg", 8, 8); img.filepath_raw = img_path; img.file_format = "PNG"; img.save()
    mat = bpy.data.materials.new("TexMat"); mat.use_nodes = True
    texn = mat.node_tree.nodes.new("ShaderNodeTexImage"); texn.image = img
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        mat.node_tree.links.new(texn.outputs["Color"], bsdf.inputs["Base Color"])
    tc.data.materials.append(mat)
    out = os.path.join(B2DIR, "texcopy", "tex.fbx"); os.makedirs(os.path.dirname(out), exist_ok=True)
    r = ok(call("export_for_engine", objects="Tex", engine="GENERIC_FBX", filepath=out, validate=False, textures="COPY"))
    assert r["textures_copied"] and all(os.path.exists(t) for t in r["textures_copied"]) and r["materials"] == ["TexMat"], r
    r = ok(call("export_for_engine", objects="Tex", engine="GENERIC_FBX", filepath=os.path.join(B2DIR, "tex_none.fbx"), validate=False, textures="NONE"))
    assert r["textures_copied"] == [], r["textures_copied"]
    assert "EMBED" in _valid_text(call("export_for_engine", objects="Tex", engine="GENERIC_FBX", filepath=out, textures="ZIP"))
    assert bpy.context.mode == "OBJECT"
    return "doc step 6: UNREAL fbx + manifest (= reply + version/written_at/blender/preset), 8 names + lod_group + socket survive re-import, file triangulated while the scene is untouched (D6), include_* switches, E2b refusal writes nothing / force records FAIL, live-enum + JSON + dir + unknown-name refusals, textures COPY/NONE"


step("B2-E2 export_for_engine UNREAL doc step 6 + refusals (13.2.6, D6, E8, F4)", b2_e2, tag="B2")


def b2_e3():
    clear_scene()
    # unsplit fixture (R-B2-15): smooth shaded, ONE seam-free UV island (tangents are derived from the UV map), no custom normals
    sph = _prim("primitive_grid_add", "Ico", x_subdivisions=8, y_subdivisions=8, calc_uvs=True)
    bpy.ops.object.shade_smooth()
    assert len(sph.data.uv_layers) == 1 and not sph.data.has_custom_normals
    src = sph.copy(); src.data = sph.data.copy(); src.name = "IcoSrc"; bpy.context.scene.collection.objects.link(src)
    p = os.path.join(B2DIR, "ico.glb")
    r = ok(call("export_for_engine", objects="Ico", engine="BEVY", filepath=p, validate=False, triangulate=False), must=EXPORT_KEYS)
    assert r["format"] == "glb" and r["exporter"] == "gltf" and r["exporter_kwargs"].get("export_tangents") is True and r["exporter_kwargs"].get("export_yup") is True, r["exporter_kwargs"]
    g = _glb_json(p)
    attrs = [set(pr["attributes"]) for mesh in g["meshes"] for pr in mesh["primitives"]]
    assert all("TANGENT" in a for a in attrs), attrs
    bpy.data.objects.remove(sph, do_unlink=True)
    ok(call("import_file", filepath=p))
    imp = next(n for n in bpy.data.objects.keys() if n.startswith("Ico") and n != "IcoSrc")
    c = ok(call("compare_meshes", mesh_a="IcoSrc", mesh_b=imp))
    assert c["vertices"]["delta"] == 0 and c["order_match"] is True and c["positions"]["max_delta"] < 1e-5, (c["vertices"], c["order_match"], c["positions"])
    # split mesh (UV seams): order is NOT a contract; export must still succeed with tangents
    clear_scene(); _prim("primitive_monkey_add", "Suz")
    p2 = os.path.join(B2DIR, "suz.glb")
    r = ok(call("export_for_engine", objects="Suz", engine="GODOT", filepath=p2, validate=False))
    attrs = [set(pr["attributes"]) for mesh in _glb_json(p2)["meshes"] for pr in mesh["primitives"]]
    assert all("TANGENT" in a for a in attrs) and os.path.exists(r["manifest_path"]), attrs
    ok(call("import_file", filepath=p2))
    imp = next(n for n in bpy.data.objects.keys() if n.startswith("Suz") and n != "Suz")
    c = ok(call("compare_meshes", mesh_a="Suz", mesh_b=imp))
    note = "Suz split export: vertices %d -> %d, order_match %s (measured, not asserted)" % (c["vertices"]["a"], c["vertices"]["b"], c["order_match"])
    # E9: colour attribute names are lost in glTF -> validate WARN / manifest warning under GODOT/BEVY
    clear_scene(); cc = make_cube("ColCube", 2.0, (0, 0, 0)); cc.data.uv_layers.new(name="UVMap")
    cc.data.color_attributes.new("TintMask", "BYTE_COLOR", "CORNER")
    r = ok(call("export_for_engine", objects="ColCube", engine="GODOT", filepath=os.path.join(B2DIR, "col.glb"), validate=False))
    assert any("TintMask" in w or "colour" in w.lower() or "color" in w.lower() for w in r["warnings"]), r["warnings"]
    # export_vertex_color NAME is 5.2-only: refused with the live enum where absent, accepted where present (E9)
    live = [i.identifier for i in bpy.ops.export_scene.gltf.get_rna_type().properties["export_vertex_color"].enum_items]
    rr = call("export_for_engine", objects="ColCube", engine="GODOT", filepath=os.path.join(B2DIR, "col_name.glb"), validate=False, overrides='{"export_vertex_color": "NAME", "export_vertex_color_name": "TintMask"}')
    if "NAME" in live:
        assert ok(rr)["exporter_kwargs"]["export_vertex_color"] == "NAME"
    else:
        mm = err(rr, "export_vertex_color"); assert "MATERIAL" in mm, A(mm)
    assert bpy.context.mode == "OBJECT"
    return "doc step 7 on the unsplit fixture: TANGENT in the GLB, round trip vertices delta 0 + order_match True + max_delta < 1e-5; %s; colour-name warning; NAME keyed on the live enum" % note


step("B2-E3 export_for_engine BEVY/GODOT glb doc step 7 (13.2.6, R-B2-15, E9)", b2_e3, tag="B2")


def b2_e4():
    clear_scene()
    bpy.ops.object.empty_add(); root = bpy.context.active_object; root.name = "#Prop"
    body = make_cube("Body", 2.0, (0, 0, 0)); body.parent = root
    bpy.ops.object.empty_add(); sock = bpy.context.active_object; sock.name = "#Socket"; sock.parent = root
    colls_before = sorted(bpy.data.collections.keys())
    import addon_utils
    try:
        present = addon_utils.enable("timbermesh_blender_plugin", default_set=False) is not None     # None = module missing
    except Exception:
        present = False
    p = os.path.join(B2DIR, "prop.timbermesh")
    bpy.context.scene.frame_set(7)      # R-B2-44 (F-B2-7): the plugin's export rewinds the frame; the add-on must restore it
    rr = call("export_for_engine", objects="#Prop", engine="TIMBERMESH", filepath=p, validate=False)
    assert bpy.context.scene.frame_current == 7, "TIMBERMESH export changed frame_current 7 -> %d (F-B2-7)" % bpy.context.scene.frame_current
    try:
        if present:
            r = ok(rr, must=EXPORT_KEYS)
            assert r["format"] == "timbermesh" and r["exporter"] == "timbermesh" and r["timbermesh_root"] == "#Prop", r
            assert os.path.exists(p) and os.path.getsize(p) > 100 and r["size_bytes"] == os.path.getsize(p), r["size_bytes"]
            import zlib
            raw = open(p, "rb").read()
            blob = None
            for off in (0, 4, 8):
                try:
                    blob = zlib.decompress(raw[off:]); break
                except zlib.error:
                    continue
            assert blob is not None and b"Prop" in blob and b"Socket" in blob, "decompressed payload lacks Prop/Socket"
            assert os.path.exists(r["manifest_path"])
            note = "4.3.2-style: .timbermesh %d B written, zlib payload names Prop + Socket" % os.path.getsize(p)
        else:
            mm = err(rr, "timbermesh_blender_plugin")
            assert not os.path.exists(p), "file written although the plugin is absent"
            note = "plugin absent: teach error names timbermesh_blender_plugin, no file"
    finally:
        if present:
            try:
                addon_utils.disable("timbermesh_blender_plugin", default_set=False)
            except Exception:
                pass
    assert sorted(bpy.data.collections.keys()) == colls_before, "temporary collection leaked: %s" % sorted(bpy.data.collections.keys())
    # no # root -> error on BOTH installs, nothing written, no collection leaked
    clear_scene(); make_cube("Loose", 2.0, (0, 0, 0))
    rr = call("export_for_engine", objects="Loose", engine="TIMBERMESH", filepath=os.path.join(B2DIR, "loose.timbermesh"), validate=False)
    err(rr)
    assert not os.path.exists(os.path.join(B2DIR, "loose.timbermesh")) and sorted(bpy.data.collections.keys()) == colls_before, "no-root path wrote a file or leaked a collection"
    return note + "; no-# root refused without a file; collections unchanged"


step("B2-E4 export_for_engine TIMBERMESH doc step 10 (13.2.6, H17, E14)", b2_e4, tag="B2")


def b2_e6():
    clear_scene()
    make_cube("U", 2.0, (0, 0, 0))
    r = ok(call("set_scene_units", preset="UNREAL"))
    us = bpy.context.scene.unit_settings
    assert abs(us.scale_length - 0.01) < 1e-9 and us.length_unit == "CENTIMETERS" and us.system == "METRIC", (us.scale_length, us.length_unit)
    r = ok(call("export_for_engine", objects="U", engine="UNREAL", filepath=os.path.join(B2DIR, "u.fbx"), validate=False))
    assert not any("set_scene_units" in w for w in r["warnings"]), r["warnings"]        # doc step 8
    ok(call("set_scene_units", preset="UNITY"))
    assert abs(us.scale_length - 1.0) < 1e-9 and us.length_unit == "METERS"
    r = ok(call("export_for_engine", objects="U", engine="UNREAL", filepath=os.path.join(B2DIR, "u2.fbx"), validate=False))
    assert any("set_scene_units" in w for w in r["warnings"]), r["warnings"]
    assert "set_scene_units" in HANDLERS
    return "UNREAL units 0.01 cm -> no scale warning; UNITY 1.0 m -> warning back; one set_scene_units handler"


step("B2-E6 set_scene_units + export warning doc step 8 (13.2.6, H17)", b2_e6, tag="B2")


# =========================================================================
# B2-G3.5 add-on review (docs/REVIEW-B2-addon.md RA-1..RA-17, R-B2-36..39): discriminating steps
# =========================================================================
def _world_key_coords(ob, key_name):
    kb = ob.data.shape_keys.key_blocks[key_name]
    return [tuple(round(c, 5) for c in (ob.matrix_world @ d.co)) for d in kb.data]


def b2_ra1():
    clear_scene()
    cube = make_cube("Keyed", 2.0, (0, 0, 0))
    cube.shape_key_add(name="Basis"); k1 = cube.shape_key_add(name="Key1")
    k1.data[0].co.z += 1.5
    bpy.context.view_layer.update()
    basis, key1 = _world_key_coords(cube, "Basis"), _world_key_coords(cube, "Key1")
    ok(call("set_origin", name="Keyed", origin_type="BOTTOM_CENTER"))
    bpy.context.view_layer.update()
    assert _world_key_coords(cube, "Basis") == basis, "Basis moved in world space (RA-1: shape_keys=True)"
    assert _world_key_coords(cube, "Key1") == key1, "Key1 moved in world space (RA-1: shape_keys=True)"
    assert abs(min(d.co.z for d in cube.data.shape_keys.key_blocks["Basis"].data)) < 1e-6
    return "set_origin BOTTOM_CENTER on a 2-key mesh: both key blocks keep their world coordinates"


step("B2-RA1 set_origin moves every shape key (RA-1)", b2_ra1, tag="B2")


def b2_ra2():
    clear_scene()
    a = make_cube("A", 2.0, (0, 0, 0)); b = make_cube("B", 2.0, (4, 0, 0)); b.data.uv_layers.new(name="UVMap")
    # A carries a UV layer with every loop shifted outside 0..1: the EDIT-mode read must still evaluate it (RNA uv data reads length 0)
    a.data.uv_layers.new(name="UVMap")
    for l in a.data.uv_layers[0].data:
        l.uv = (l.uv[0] + 2.0, l.uv[1])
    bpy.ops.object.select_all(action="DESELECT"); a.select_set(True); bpy.context.view_layer.objects.active = a
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        assert bpy.context.mode == "EDIT_MESH"
        r = ok(call("validate_asset", objects="A"))
        c = _check(r, "A", "uv_out_of_bounds")
        assert c["status"] == "WARN" and c["count"] == 24, "uv_out_of_bounds not evaluated for an EDIT-mode object: %s" % A(json.dumps(c)[:200])
        assert bpy.context.mode == "EDIT_MESH" and bpy.context.view_layer.objects.active is a
        # a vertex moved in the live edit-mesh must be seen by a read (flush, no mode change): A vs B VERTS max_delta 3.0
        ebm = bmesh.from_edit_mesh(a.data); ebm.verts.ensure_lookup_table(); ebm.verts[0].co.x -= 3.0; bmesh.update_edit_mesh(a.data)
        r = ok(call("compare_meshes", mesh_a="A", mesh_b="B", compare="VERTS"))
        assert abs(r["positions"]["max_delta"] - 3.0) < 1e-5, "edit-mesh change not flushed into the read: %s" % A(json.dumps(r.get("positions"))[:200])
        assert bpy.context.mode == "EDIT_MESH" and bpy.context.view_layer.objects.active is a
        r = ok(call("get_mesh_stats", name="B"))
        assert r["triangles"] == 12 and bpy.context.mode == "EDIT_MESH" and bpy.context.view_layer.objects.active is a, \
            "get_mesh_stats changed mode/active: %s / %s" % (bpy.context.mode, bpy.context.view_layer.objects.active)
        r = ok(call("validate_asset", objects="B"))
        assert bpy.context.mode == "EDIT_MESH" and bpy.context.view_layer.objects.active is a and r["verdict"] in ("PASS", "WARN"), \
            "validate_asset(fix=False) changed mode/active: %s / %s" % (bpy.context.mode, bpy.context.view_layer.objects.active)
        for issue in ("NGONS", "NON_MANIFOLD", "INTERIOR_FACES", "BOUNDARY"):
            r = ok(call("find_mesh_issues", mesh="B", issue=issue))
            assert bpy.context.mode == "EDIT_MESH" and bpy.context.view_layer.objects.active is a, \
                "find_mesh_issues(%s) left mode %s / active %s" % (issue, bpy.context.mode, bpy.context.view_layer.objects.active)
            assert r["count"] == 0, (issue, r)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    return "A in EDIT mode: uv_out_of_bounds on A itself WARN 24 loops + a live edit-mesh vertex move seen by compare_meshes (max_delta 3.0); get_mesh_stats / validate_asset(fix=False) / find_mesh_issues x4 issue kinds on B leave mode EDIT_MESH and active A (R-B2-36)"


step("B2-RA2 read paths never change mode or active (RA-2, R-B2-36)", b2_ra2, tag="B2")


def b2_ra3():
    clear_scene()
    a = make_cube("ShA", 2.0, (0, 0, 0)); b = bpy.data.objects.new("ShB", a.data); bpy.context.scene.collection.objects.link(b)
    b.location = (6, 0, 3); bpy.context.view_layer.update()
    wb = _world_verts(b); wa = _world_verts(a)
    r = ok(call("set_origin", name="ShA", origin_type="BOTTOM_CENTER"), must=["isolated"])
    bpy.context.view_layer.update()
    assert r["isolated"] is True and a.data is not b.data and a.data.users == 1 and b.data.users == 1, (r.get("isolated"), a.data.users)
    assert _world_verts(b) == wb, "the sharing object jumped (RA-3 / R-B2-37)"
    assert _world_verts(a) == wa and abs(min(v.co.z for v in a.data.vertices)) < 1e-6
    r = ok(call("set_origin", name="ShA", origin_type="TOP_CENTER"))
    assert r["isolated"] is False, r
    return "shared mesh: set_origin isolates the target (users 1, isolated true), the other object keeps its world vertices"


step("B2-RA3 set_origin isolates multi-user data (RA-3, R-B2-37)", b2_ra3, tag="B2")


def b2_ra4():
    clear_scene()
    odd = bpy.data.collections.new("Odd"); bpy.context.scene.collection.children.link(odd)
    names = ["Panel[1]", "Wall, left", "Star*Prop", "Q?Mark"]
    for i, n in enumerate(names):
        o = make_cube(n, 2.0, (i * 4.0, 0, 0)); o.scale = (2, 1, 1); o.data.uv_layers.new(name="UVMap")
        odd.objects.link(o); bpy.context.scene.collection.objects.unlink(o)
    bpy.context.view_layer.update()
    r = ok(call("validate_asset", collection="Odd", fix=True))
    assert set(r["objects"]) == set(names), sorted(r["objects"])
    for n in names:
        o = bpy.data.objects[n]
        assert close(o.scale, (1, 1, 1)), "%s not fixed through the collection path (RA-4)" % n
        assert _check(r, n, "unapplied_transform")["status"] == "PASS", n
    p = os.path.join(B2DIR, "odd.fbx")
    r = ok(call("export_for_engine", collection="Odd", engine="GENERIC_FBX", filepath=p, validate=False))
    assert set(r["objects"]) >= set(names) and os.path.exists(p), r["objects"]
    # R-B2-41: a literal name is always addressable by objects= (exact match first, fnmatch only when nothing matches exactly)
    for n in ("Panel[1]", "Star*Prop", "Q?Mark"):
        rr = call("validate_asset", objects=n)
        res = rr.get("result") or {}
        assert rr.get("status") == "success" and "error" not in res and list(res.get("objects") or {}) == [n], \
            "objects=%r not addressable exactly (R-B2-41): %s" % (n, A(json.dumps(rr)[:200]))
    # a name containing "," stays a documented limitation (objects= is a comma list): measured only
    rr = call("validate_asset", objects="Wall, left")
    comma = "ok" if rr.get("status") == "success" and "error" not in (rr.get("result") or {}) else "miss"
    return "names with , [ * ? survive the validate fix path and the export path (RA-4); exact objects= lookup of Panel[1] Star*Prop Q?Mark asserted (R-B2-41); comma name: %s" % comma


step("B2-RA4 special-character names never hit the glob parser internally (RA-4)", b2_ra4, tag="B2")


def b2_ra5():
    clear_scene()
    o = make_cube("Axis", 2.0, (0, 0, 0)); o.data.uv_layers.new(name="UVMap")
    o.rotation_mode = "AXIS_ANGLE"; o.rotation_axis_angle = (1.57, 0.0, 0.0, 1.0)
    bpy.context.view_layer.update()
    assert _check(_validate("Axis"), "Axis", "unapplied_transform")["status"] == "FAIL", "axis-angle rotation passed as applied (RA-5)"
    assert ok(call("get_mesh_stats", name="Axis"))["unapplied_transform"]["rotation"] is True
    o.rotation_axis_angle = (0.0, 0.0, 0.0, 1.0); bpy.context.view_layer.update()
    assert _check(_validate("Axis"), "Axis", "unapplied_transform")["status"] == "PASS"
    o.rotation_mode = "QUATERNION"; o.rotation_quaternion = (0.7071, 0.7071, 0.0, 0.0); bpy.context.view_layer.update()
    assert _check(_validate("Axis"), "Axis", "unapplied_transform")["status"] == "FAIL"
    o.rotation_quaternion = (-1.0, 0.0, 0.0, 0.0); bpy.context.view_layer.update()
    assert _check(_validate("Axis"), "Axis", "unapplied_transform")["status"] == "PASS", "w = -1 is the identity too"
    return "AXIS_ANGLE 1.57 -> FAIL, 0 -> PASS; QUATERNION 90 deg -> FAIL, w -1 -> PASS (RA-5)"


step("B2-RA5 rotation identity keyed on rotation_mode (RA-5)", b2_ra5, tag="B2")


def b2_ra6():
    clear_scene()
    body = make_cube("Body", 2.0, (0, 0, 0)); body.data.flip_normals()
    bpy.ops.object.armature_add(); bpy.context.active_object.name = "Rig"
    before = [tuple(round(c, 5) for c in p.normal) for p in body.data.polygons]
    rr = call("fix_normals", objects="Body,Rig", mode="FLIP"); err(rr)
    assert [tuple(round(c, 5) for c in p.normal) for p in body.data.polygons] == before, "fix_normals touched Body before refusing Rig (RA-6)"
    d = _dirty_cube("Dirty"); nv = len(d.data.vertices)
    rr = call("cleanup_mesh", objects="Dirty,Rig"); err(rr)
    assert len(d.data.vertices) == nv, "cleanup_mesh touched Dirty before refusing Rig (RA-6)"
    rr = call("set_normals", objects="Body,Rig", mode="FLAT"); err(rr)
    assert bpy.context.mode == "OBJECT"
    return "fix_normals / cleanup_mesh / set_normals refuse a list with a non-mesh and change nothing (RA-6, H11)"


step("B2-RA6 whole-list validation before any mutation (RA-6)", b2_ra6, tag="B2")


def _scene_names():
    return (sorted(o.name for o in bpy.data.objects), sorted(m.name for m in bpy.data.meshes))


def b2_ra7():
    clear_scene()
    _prim("primitive_uv_sphere_add", "Sph", segments=16, ring_count=8)
    _prim("primitive_torus_add", "Tor", major_segments=12, minor_segments=6)
    snap = _scene_names()
    for kw in (dict(mesh="Sph", ratio=0.5, new_object="Sph", ),                       # name collides with the source
               dict(mesh="Sph", ratio=0.5, new_object="Tor"),                         # name collides with another object
               dict(mesh="Sph", ratio=0.5, new_object="X1", vertex_group="NoSuch"),
               dict(mesh="Sph", ratio=1.5, new_object="X2"),
               dict(mesh="Sph", ratio=0.5, new_object="X3", mode="NOPE")):
        rr = call("decimate_mesh", **kw)
        if rr.get("status") == "success" and "error" not in rr["result"]:
            # a collision the add-on resolves by suffixing is allowed; the point is no ORPHAN after an error
            for n in (rr["result"]["result_object"],):
                bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
            for m in list(bpy.data.meshes):
                if m.users == 0: bpy.data.meshes.remove(m)
            snap = _scene_names(); continue
        assert _scene_names() == snap, "decimate_mesh error left an orphan: %s" % (kw,)
    for kw in (dict(mesh="Tor", shape="MESH_DECIMATED", decimate_ratio=1.5),
               dict(mesh="Tor", shape="SLICED_CONVEX", count=33),
               dict(mesh="Tor", shape="NOPE"), dict(mesh="Tor", profile="PS5"),
               dict(mesh="Tor", shape="MESH_DECIMATED", collection="")):
        rr = call("create_collision", **kw)
        if rr.get("status") == "success" and "error" not in rr["result"]:
            for q in rr["result"]["proxies"]:
                bpy.data.objects.remove(bpy.data.objects[q["name"]], do_unlink=True)
            for m in list(bpy.data.meshes):
                if m.users == 0: bpy.data.meshes.remove(m)
            snap = _scene_names(); continue
        assert _scene_names() == snap, "create_collision error left an orphan: %s" % (kw,)
    assert bpy.context.mode == "OBJECT"
    return "decimate_mesh(new_object) and create_collision error paths leave the object and mesh lists untouched (RA-7)"


step("B2-RA7 no orphans on error (RA-7)", b2_ra7, tag="B2")


def b2_ra8():
    clear_scene()
    c = make_cube("T", 2.0, (0, 0, 0)); c.data.uv_layers.new(name="UVMap")
    p = os.path.join(B2DIR, "ra8.fbx")
    mods = [m.name for m in c.modifiers]; polys = len(c.data.polygons)
    msg = err(call("export_for_engine", objects="T", engine="GENERIC_FBX", filepath=p, triangulate=True, apply_modifiers=False), "apply_modifiers")
    assert not os.path.exists(p) and not os.path.exists(p + ".manifest.json"), "refused export wrote a file (RA-8)"
    assert [m.name for m in c.modifiers] == mods and len(c.data.polygons) == polys, "refusal mutated the object"
    ok(call("export_for_engine", objects="T", engine="GENERIC_FBX", filepath=p, triangulate=False, apply_modifiers=False, validate=False))
    assert os.path.exists(p)
    return "triangulate=True + apply_modifiers=False refused before any mutation, no file; triangulate=False exports (R-B2-38)"


step("B2-RA8 triangulate requires apply_modifiers (RA-8, R-B2-38)", b2_ra8, tag="B2")


def b2_ra13():
    clear_scene()
    c = make_cube("Piv", 2.0, (1, 2, 3)); bpy.context.view_layer.update()
    loc = tuple(c.location); wv = _world_verts(c)
    r = ok(call("set_pivot", objects="Piv", mode="BOTTOM_CENTER", keep_world=False))
    bpy.context.view_layer.update()
    assert close(c.location, loc), "keep_world=False must not move the origin (RA-13 / R-B2-39)"
    assert abs(min(v.co.z for v in c.data.vertices)) < 1e-6, "geometry not re-based"
    assert _world_verts(c) != wv and abs(min((c.matrix_world @ v.co).z for v in c.data.vertices) - loc[2]) < 1e-6, "world geometry must move so the bottom sits at the (unchanged) origin"
    assert r.get("keep_world") is False, r
    # keep_world=True (default) on the same object: world geometry fixed, origin moves
    wv2 = _world_verts(c)
    ok(call("set_pivot", objects="Piv", mode="TOP_CENTER"))
    bpy.context.view_layer.update()
    assert _world_verts(c) == wv2 and not close(c.location, loc)
    # apply_transforms keep_children_world both ways
    p = make_cube("P", 2.0, (0, 6, 0)); p.rotation_euler = (0, 0, math.radians(30)); p.scale = (2, 1, 1)
    ch = make_cube("C", 1.0, (0, 0, 0)); ch.parent = p; ch.location = (2, 0, 0); bpy.context.view_layer.update()
    cw = _wm(ch)
    ok(call("apply_transforms", objects="P"))
    assert _wm(ch) == cw, "child world matrix changed with keep_children_world=True (R-B2-39)"
    p.rotation_euler = (0, 0, math.radians(45)); bpy.context.view_layer.update()
    cw = _wm(ch)
    r = ok(call("apply_transforms", objects="P", keep_children_world=False))
    bpy.context.view_layer.update()
    assert r["applied"]["rotation"] is True and close(p.rotation_euler, (0, 0, 0))
    return "set_pivot keep_world=False re-bases the geometry with the origin fixed; keep_world default keeps world geometry; apply_transforms keeps the child's world matrix (R-B2-39)"


step("B2-RA13 keep_world / keep_children_world implemented (RA-13, R-B2-39)", b2_ra13, tag="B2")


def b2_ra17():
    clear_scene()
    tc = make_cube("TexTwo", 2.0, (0, 0, 0)); tc.data.uv_layers.new(name="UVMap")
    d1 = os.path.join(B2DIR, "texA"); d2 = os.path.join(B2DIR, "texB"); os.makedirs(d1, exist_ok=True); os.makedirs(d2, exist_ok=True)
    imgs = []
    for i, d in enumerate((d1, d2)):
        im = bpy.data.images.new("albedo_%d" % i, 8, 8); im.filepath_raw = os.path.join(d, "albedo.png"); im.file_format = "PNG"; im.save()
        imgs.append(im)
    mat = bpy.data.materials.new("TwoTex"); mat.use_nodes = True
    for im in imgs:
        n = mat.node_tree.nodes.new("ShaderNodeTexImage"); n.image = im
    tc.data.materials.append(mat)
    out = os.path.join(B2DIR, "twotex", "two.fbx"); os.makedirs(os.path.dirname(out), exist_ok=True)
    r = ok(call("export_for_engine", objects="TexTwo", engine="GENERIC_FBX", filepath=out, validate=False, textures="COPY"))
    copied = r["textures_copied"]
    assert len(copied) == 2 and len(set(copied)) == 2 and all(os.path.exists(t) for t in copied), copied
    assert len({os.path.basename(t) for t in copied}) == 2, "basename collision clobbered one texture (RA-17): %s" % copied
    m = _manifest(r["manifest_path"]); assert sorted(m["textures_copied"]) == sorted(copied)
    return "two albedo.png from different folders -> two distinct copied files, both in the manifest (RA-17)"


step("B2-RA17 texture COPY basename collisions (RA-17)", b2_ra17, tag="B2")


# =========================================================================
# B2-G8 (R-B2-40): the generic driver composes with every 2.4.0 tool (batch + $id, undo=AUTO rollback, transaction, rna_get)
# =========================================================================
def _drv_ok(cmd, **params):
    rr = call(cmd, **params)
    assert isinstance(rr, dict) and rr.get("status") == "success", "driver handler failed: %s" % A(json.dumps(rr)[:300])
    res = rr.get("result")
    assert isinstance(res, dict) and "error" not in res, "driver reply error: %s" % A(json.dumps(res)[:300])
    return res


def b2_g8():
    clear_scene()
    sph = _prim("primitive_uv_sphere_add", "Sph", segments=16, ring_count=8); sph.data.uv_layers.new(name="UVMap")
    bpy.context.view_layer.update()
    # (a) batch with $id.path substitution across three B2 tools
    r = _drv_ok("drv_batch", commands=[
        {"id": "v", "type": "validate_asset", "params": {"objects": "Sph"}},
        {"id": "d", "type": "decimate_mesh", "params": {"mesh": "Sph", "ratio": 0.5, "new_object": "Sph_half"}},
        {"id": "c", "type": "create_collision", "params": {"mesh": "$d.result_object", "shape": "CONVEX", "profile": "UNREAL"}},
    ], stop_on_error=True, undo="NONE")
    assert [x["status"] for x in r["results"]] == ["ok", "ok", "ok"], r["results"]
    assert r["results"][1]["result"]["result_object"] == "Sph_half" and r["results"][2]["result"]["proxies"][0]["name"] == "UCX_Sph_half_00", r["results"]
    assert "Sph_half" in bpy.data.objects and "UCX_Sph_half_00" in bpy.data.objects and bpy.data.objects["UCX_Sph_half_00"].parent is bpy.data.objects["Sph_half"]
    assert r["results"][0]["result"]["verdict"] in ("PASS", "WARN"), r["results"][0]
    # (b) undo=AUTO + stop_on_error: item 2 fails -> rolled back, the decimate copy from item 1 is gone
    names_before = sorted(o.name for o in bpy.data.objects)
    rr = call("drv_batch", commands=[
        {"id": "d1", "type": "decimate_mesh", "params": {"mesh": "Sph", "ratio": 0.5, "new_object": "Sph_tmp"}},
        {"id": "d2", "type": "decimate_mesh", "params": {"mesh": "NoSuchMesh", "ratio": 0.5}},
    ], stop_on_error=True, undo="AUTO")
    res = rr.get("result") or {}
    assert res.get("rolled_back") is True and res.get("stopped_at") == 1, A(json.dumps(res)[:300])
    assert [x["status"] for x in res["results"]][:2] == ["ok", "error"], res["results"]
    assert "Sph_tmp" not in bpy.data.objects and sorted(o.name for o in bpy.data.objects) == names_before, "undo=AUTO did not revert the decimate copy"
    # (c) transaction over set_pivot + add_socket, then ROLLBACK (re-fetch: the undo in (b) invalidated the Python reference)
    sph = bpy.data.objects["Sph"]
    loc0 = tuple(round(c, 5) for c in sph.location); nverts = len(sph.data.vertices)
    _drv_ok("drv_transaction", action="BEGIN", name="g8")
    _drv_ok("set_pivot", objects="Sph", mode="BOTTOM_CENTER")
    _drv_ok("add_socket", parent="Sph", name="G8", profile="UNREAL")
    assert "SOCKET_G8" in bpy.data.objects and tuple(round(c, 5) for c in sph.location) != loc0
    rb = _drv_ok("drv_transaction", action="ROLLBACK")
    assert rb.get("undo_calls", 0) >= 1 and "_mcp_txn" not in bpy.context.scene, rb
    sph = bpy.data.objects["Sph"]; bpy.context.view_layer.update()
    assert "SOCKET_G8" not in bpy.data.objects, "ROLLBACK left the socket"
    assert tuple(round(c, 5) for c in sph.location) == loc0 and len(sph.data.vertices) == nverts, "ROLLBACK did not restore the pivot"
    # (d) rna_get on the UCX proxy by OBJECT:Name and by #uid
    uid = bpy.data.objects["UCX_Sph_half_00"].session_uid
    g = _drv_ok("drv_get", refs=["OBJECT:UCX_Sph_half_00", "#%d" % uid], fields="display_type,hide_render,parent")
    for item in g["results"]:
        assert item["value"]["display_type"] == "WIRE" and item["value"]["hide_render"] is True and item["value"]["parent"]["name"] == "Sph_half", item
    assert bpy.context.mode == "OBJECT"
    return "batch $id: validate -> decimate(new_object) -> create_collision ok; undo=AUTO rollback removed the copy; transaction ROLLBACK removed the socket and restored the pivot; rna_get WIRE + hide_render on the UCX proxy (R-B2-40)"


step("B2-G8 driver composes with the 2.4.0 tools (R-B2-40)", b2_g8, tag="B2")

clear_scene()
