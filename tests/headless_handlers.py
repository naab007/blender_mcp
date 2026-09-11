"""Headless handler harness for the Blender MCP add-on.

Runs unchanged on every Blender install:

    "<blender>\\blender.exe" --background --factory-startup --python tests\\headless_handlers.py -- [--addon PATH] [--out REPORT.json]

It imports addon.py from the repo root (or --addon), registers it, instantiates
BlenderMCPServer and drives every handler through execute_command() exactly as the
socket path does (gate check, kwargs, error wrapping), without any socket.

Output: one "STEP n PASS|FAIL <name> :: <detail>" line per step and a final
"SUMMARY {json}" line. Everything printed is ASCII (cp1252 consoles).

Step -> request-doc mapping (docs/MIGRATION-blender-5.2.md):
  BREAK1  set_render_settings / depth-map engine fallback (BLENDER_EEVEE id)
  BREAK2  render_depth_map compositor tree (Scene.node_tree removed in 5.0)
  BREAK3  boolean_operation solver FAST -> FLOAT
  BREAK4  image_settings.media_type before file_format
  PLAN4   "headless handler tests for all 92 tools" (85 addon handlers)
  LIVE    viewport-only handlers: headless can only prove graceful failure
"""
import sys
import os
import json
import math
import time
import tempfile
import traceback
import importlib.util

import bpy
import mathutils
import numpy as np

# ---------------------------------------------------------------- arguments
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
_opts = {"--addon": None, "--out": None}
_i = 0
while _i < len(_argv):
    if _argv[_i] in _opts and _i + 1 < len(_argv):
        _opts[_argv[_i]] = _argv[_i + 1]
        _i += 2
    else:
        _i += 1

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PATH = _opts["--addon"] or os.environ.get("BLENDERMCP_ADDON") or os.path.join(REPO, "addon.py")
OUT_PATH = _opts["--out"]
WORK = tempfile.mkdtemp(prefix="mcp_harness_")
VER = "%d.%d.%d" % bpy.app.version


def A(s):
    """ASCII-only text for stdout (cp1252 consoles choke on non-ASCII)."""
    return str(s).encode("ascii", "replace").decode("ascii")


def P(msg):
    print(A(msg), flush=True)


# ---------------------------------------------------------------- load addon
import hashlib
ADDON_SRC = open(ADDON_PATH, "rb").read()
ADDON_SHA = hashlib.sha256(ADDON_SRC).hexdigest()
# `git hash-object addon.py` (git applies its CRLF clean filter, so ask git; fall back to the raw blob sha1)
try:
    import subprocess
    ADDON_GIT = subprocess.run(["git", "-C", os.path.dirname(ADDON_PATH), "hash-object", ADDON_PATH],
                               capture_output=True, text=True, timeout=20).stdout.strip() or "git-failed"
except Exception:
    ADDON_GIT = "raw:" + hashlib.sha1(b"blob %d\0" % len(ADDON_SRC) + ADDON_SRC).hexdigest()
P("HARNESS blender=%s python=%s numpy=%s background=%s" % (VER, sys.version.split()[0], np.__version__, bpy.app.background))
P("HARNESS addon=%s git_hash_object=%s sha256=%s" % (ADDON_PATH, ADDON_GIT, ADDON_SHA))
RESULTS = []
_STEP = [0]
try:
    import requests  # bundled in Blender's python; the add-on needs it
    _spec = importlib.util.spec_from_file_location("addon", ADDON_PATH)
    addon = importlib.util.module_from_spec(_spec)
    sys.modules["addon"] = addon
    _spec.loader.exec_module(addon)
    addon.register()
    srv = addon.BlenderMCPServer()
    HANDLERS = srv._build_handlers()
    _STEP[0] += 1
    RESULTS.append({"step": 1, "name": "M1 import + register()", "status": "PASS", "tag": "M1",
                    "detail": "bl_info.version=%s requests=%s handlers=%d" % (addon.bl_info["version"], requests.__version__, len(HANDLERS)), "seconds": 0})
    P("STEP 1 PASS [M1] import + register() :: bl_info.version=%s requests=%s handlers=%d" % (addon.bl_info["version"], requests.__version__, len(HANDLERS)))
except Exception:
    tb = A(traceback.format_exc())
    P("STEP 1 FAIL [M1] import + register() :: %s" % tb.replace("\n", " / "))
    P("SUMMARY " + json.dumps({"blender": VER, "addon": ADDON_PATH, "addon_sha256": ADDON_SHA, "steps": 1, "passed": 0, "failed": 1,
                               "failed_steps": [{"step": 1, "name": "M1 import + register()", "tag": "M1", "detail": tb[-800:]}]}))
    sys.exit(0)

CALLED = set()


def call(cmd, **params):
    # One depsgraph tick before each command, as the real main loop gives between timer callbacks.
    bpy.context.view_layer.update()
    CALLED.add(cmd)
    return srv.execute_command({"type": cmd, "params": params})


def ok(res, must=None):
    """Assert a success reply without an error key; return the result dict."""
    assert isinstance(res, dict), "reply is not a dict: %r" % (res,)
    assert res.get("status") == "success", "status=%s message=%s" % (res.get("status"), A(res.get("message")))
    r = res.get("result")
    assert not (isinstance(r, dict) and "error" in r), "result carries error: %s" % A(r.get("error"))
    if must:
        for k in must:
            assert isinstance(r, dict) and k in r, "result lacks key %r: %s" % (k, A(json.dumps(r)[:200]))
    return r


def err(res, contains=None):
    """Assert an error reply (status error OR result.error); return the message."""
    assert isinstance(res, dict), "reply is not a dict: %r" % (res,)
    if res.get("status") == "error":
        msg = str(res.get("message"))
    else:
        r = res.get("result")
        assert isinstance(r, dict) and "error" in r, "expected an error, got %s" % A(json.dumps(res)[:300])
        msg = str(r["error"])
    if contains:
        assert contains.lower() in msg.lower(), "error message %r lacks %r" % (A(msg), contains)
    return msg


def close(a, b, tol=1e-4):
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b)) and len(list(a)) == len(list(b))


def snapshot():
    sc = bpy.context.scene
    try:
        mode = bpy.context.mode
    except Exception:
        mode = "?"
    act = bpy.context.view_layer.objects.active
    r = sc.render
    return {
        "mode": mode,
        "active": act.name if act else None,
        "selected": sorted(o.name for o in bpy.context.view_layer.objects if o.select_get()),
        "engine": r.engine,
        "res": (r.resolution_x, r.resolution_y, r.resolution_percentage),
        "file_format": r.image_settings.file_format,
        "media_type": getattr(r.image_settings, "media_type", None),
        "camera": sc.camera.name if sc.camera else None,
        "frame": sc.frame_current,
        "scenes": sorted(s.name for s in bpy.data.scenes),
        "node_groups": sorted(g.name for g in bpy.data.node_groups),
        "filepath": r.filepath,
    }


def step(name, fn, readonly=False, tag="PLAN4"):
    """Run one test step. fn returns a detail string or raises."""
    _STEP[0] += 1
    n = _STEP[0]
    before = snapshot()
    t0 = time.time()
    status, detail = "PASS", ""
    try:
        detail = fn() or ""
        after = snapshot()
        assert after["mode"] == "OBJECT", "left Blender in mode %s" % after["mode"]
        if readonly:
            diff = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
            assert not diff, "read-only handler changed state: %s" % A(diff)
        else:
            # invariants for every handler: no leaked temp scenes, engine untouched unless the
            # handler is the one that sets it
            assert after["scenes"] == before["scenes"], "scene list changed %s -> %s" % (before["scenes"], after["scenes"])
    except Exception as e:
        status = "FAIL"
        tb = traceback.format_exc().strip().splitlines()
        detail = "%s: %s | %s" % (type(e).__name__, A(e), A(" / ".join(tb[-4:-1])))
    dt = time.time() - t0
    RESULTS.append({"step": n, "name": name, "status": status, "detail": A(detail), "tag": tag, "seconds": round(dt, 2)})
    P("STEP %d %s [%s] %s :: %s" % (n, status, tag, name, detail))


# ---------------------------------------------------------------- fixtures
def clear_scene():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights,
                 bpy.data.materials, bpy.data.images, bpy.data.collections, bpy.data.node_groups,
                 bpy.data.actions, bpy.data.worlds):
        for d in list(coll):
            if coll is bpy.data.worlds and d == bpy.context.scene.world:
                continue
            if d.users == 0 or coll is not bpy.data.worlds:
                try:
                    coll.remove(d)
                except Exception:
                    pass
    for sc in list(bpy.data.scenes):
        if sc != bpy.context.scene:
            bpy.data.scenes.remove(sc)
    bpy.context.scene.camera = None
    bpy.context.scene.frame_set(1)


def make_cube(name="Cube", size=2.0, loc=(0, 0, 0)):
    h = size / 2.0
    verts = [(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
             (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)]
    # consistent outward winding: -Z, +Z, -Y, +X, +Y, -X
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    bpy.context.view_layer.update()
    return ob


def make_camera(name="Cam", loc=(0, -6, 0), look=(0, 0, 0)):
    cd = bpy.data.cameras.new(name)
    ob = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    d = mathutils.Vector(look) - mathutils.Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    return ob


def make_light(name="Lamp", kind="POINT", loc=(2, -2, 4)):
    ld = bpy.data.lights.new(name, kind)
    ob = bpy.data.objects.new(name, ld)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    return ob


def make_bezier(name="Bez"):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(2)
    for i, p in enumerate(sp.bezier_points):
        p.co = (i * 1.0, 0, 0)
        p.handle_left = (i * 1.0 - 0.3, 0, 0)
        p.handle_right = (i * 1.0 + 0.3, 0, 0)
        p.handle_left_type = p.handle_right_type = "FREE"
    sp2 = cu.splines.new("POLY")
    sp2.points.add(2)
    for i, p in enumerate(sp2.points):
        p.co = (0, i * 1.0, 0, 1.0)
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def read_png(path):
    img = bpy.data.images.load(path)
    try:
        w, h = img.size
        arr = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(arr)
        return arr.reshape(h, w, 4)
    finally:
        bpy.data.images.remove(img)


def assert_not_blank(path, min_std=0.02):
    assert os.path.exists(path), "no file at %s" % path
    assert os.path.getsize(path) > 0, "empty file %s" % path
    px = read_png(path)
    rgb = px[..., :3]
    std = float(rgb.std())
    # distinct colour clusters: quantise to 16 levels, count unique colours
    q = np.unique((rgb * 15).round().astype(np.int16).reshape(-1, 3), axis=0)
    assert std > min_std and len(q) >= 2, "image looks blank: std=%.4f clusters=%d" % (std, len(q))
    return "%dx%d std=%.3f clusters=%d alpha_cov=%.2f" % (px.shape[1], px.shape[0], std, len(q), float((px[..., 3] > 0.5).mean()))


def write_small_png(path, colour=(1.0, 0.2, 0.2, 1.0)):
    img = bpy.data.images.new("harness_tex", 8, 8, alpha=True)
    px = np.tile(np.array(colour, dtype=np.float32), 64)
    img.pixels.foreach_set(px)
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return path


def engine_ids():
    return [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]


def solver_ids():
    return [e.identifier for e in bpy.types.BooleanModifier.bl_rna.properties["solver"].enum_items]


# =========================================================================
# S0 preflight
# =========================================================================
PHASE_A_HANDLERS = 85   # the 2.0.0 table; B0 adds commands, none may disappear (C12 spirit on the addon side)
PHASE_A_TABLE = set("""get_scene_info get_object_info get_viewport_screenshot execute_code get_polyhaven_status
get_hyper3d_status get_sketchfab_status get_hunyuan3d_status get_polyhaven_categories search_polyhaven_assets
download_polyhaven_asset set_texture create_rodin_job poll_rodin_job_status import_generated_asset
search_sketchfab_models get_sketchfab_model_preview download_sketchfab_model create_hunyuan_job
poll_hunyuan_job_status import_generated_asset_hunyuan capture_viewport_angle capture_contact_sheet
render_depth_map store_reference_image get_reference_image move_object scale_object rotate_object
set_object_material_color get_vertex_positions set_vertex_position set_vertex_positions get_control_points
set_control_point quit_blender get_edges mark_sharp_edges set_edge_crease set_edge_bevel_weight get_faces
set_face_material_index extrude_faces inset_faces flip_normals merge_vertices triangulate_mesh subdivide_mesh
apply_modifier get_mesh_stats create_camera set_active_camera render_from_camera render_all_cameras
find_objects_by_type measure_distance add_light set_world_background add_3point_lighting export_object
import_file save_blend load_blend add_primitive delete_object duplicate_object join_objects separate_mesh
rename_object set_origin snap_to_ground set_smooth_shading parent_object select_objects align_objects
create_material assign_material load_texture add_modifier boolean_operation set_render_settings add_keyframe
set_frame create_collection move_to_collection""".split())
assert len(PHASE_A_TABLE) == PHASE_A_HANDLERS


def s0_handlers():
    assert len(HANDLERS) >= PHASE_A_HANDLERS, "expected at least %d handlers, got %d" % (PHASE_A_HANDLERS, len(HANDLERS))
    missing = [c for c in srv._GATED_COMMANDS if c not in HANDLERS]
    assert not missing, "gated commands without handler: %s" % missing
    return "%d handlers (%d in 2.0.0), %d gated" % (len(HANDLERS), PHASE_A_HANDLERS, len(srv._GATED_COMMANDS))


step("handler table complete", s0_handlers, readonly=True)


def s0_m7():
    # Lead ruling 11:49: only the comparison form counts; bpy.app.version_string reads are allowed (get_version)
    import re as _re
    src = ADDON_SRC.decode("utf-8", "replace")
    pat = _re.compile(r"bpy\.app\.version\s*(>=|<=|==|!=|<|>)")
    hits = [i + 1 for i, line in enumerate(src.splitlines()) if pat.search(line) and not line.strip().startswith("#")]
    reads = sum(1 for line in src.splitlines() if "bpy.app.version" in line and not pat.search(line))
    assert not hits, "bpy.app.version compared at lines %s" % hits
    return "0 version comparisons in addon.py (%d plain bpy.app.version/version_string reads, allowed)" % reads


step("M7 no bpy.app.version comparisons", s0_m7, readonly=True, tag="M7")


def s0_m12():
    bad, seen = [], []
    for label, path in (("addon.py", ADDON_PATH), ("server.py", os.path.join(REPO, "src", "blender_mcp", "server.py"))):
        if not os.path.exists(path):
            continue
        for i, line in enumerate(open(path, encoding="utf-8", errors="replace").read().splitlines()):
            if "np.array(" in line or "numpy.array(" in line or "np.asarray(" in line:
                m = [w for w in ("float64", "float32", "uint8") if w in line]
                seen.append("%s:%d dtype=%s" % (label, i + 1, "/".join(m) if m else "MISSING"))
                if "dtype" not in line:
                    bad.append("%s:%d %s" % (label, i + 1, line.strip()[:80]))
    assert not bad, "numpy.array without dtype: %s" % bad
    return "%d numpy.array sites, all carry dtype (float64 required only where the source is mathutils): %s" % (len(seen), seen or "none")


step("M12 numpy.array sites carry dtype", s0_m12, readonly=True, tag="M12")


def s0_m7b():
    lines = ADDON_SRC.decode("utf-8", "replace").splitlines()
    sites = [i for i, l in enumerate(lines) if "import_scene.obj" in l or "export_scene.obj" in l or "import_mesh.stl" in l or "export_mesh.stl" in l]
    unguarded = [i + 1 for i in sites if not any("_op_exists" in lines[j] for j in range(max(0, i - 4), i))]
    assert not unguarded, "legacy OBJ/STL operator without _op_exists guard at lines %s" % unguarded
    return "%d legacy obj/stl operator sites, all under an _op_exists guard" % len(sites)


step("M7b legacy obj/stl operators guarded by _op_exists", s0_m7b, readonly=True, tag="M7")


def s0_f8():
    lines = ADDON_SRC.decode("utf-8", "replace").splitlines()
    hits = [i + 1 for i, l in enumerate(lines) if "tempfile._cleanup" in l]
    assert not hits, "tempfile._cleanup (private, wipes every temp file of the process) used at lines %s" % hits
    return "0 hits"


step("F8 no tempfile._cleanup in addon.py", s0_f8, readonly=True, tag="F8")


def s0_f7_static():
    lines = ADDON_SRC.decode("utf-8", "replace").splitlines()
    hits = [(i + 1, l.strip()) for i, l in enumerate(lines) if "ShaderNodeSeparateRGB" in l]
    code = [(n, l) for n, l in hits if not l.startswith("#")]
    comments = [n for n, l in hits if l.startswith("#")]
    bad = [(n, l[:100]) for n, l in code if "_new_node" not in l or "ShaderNodeSeparateColor" not in l]
    assert not bad, "ShaderNodeSeparateRGB in code outside a _new_node(...ShaderNodeSeparateColor...) try-list at %s" % bad
    assert code, "ShaderNodeSeparateRGB fallback missing: no _new_node try-list names it"
    return "%d code mention(s) all inside a _new_node try-list with ShaderNodeSeparateColor (comment-only lines: %s)" % (len(code), comments)


step("F7 static: SeparateRGB only as _new_node fallback of SeparateColor", s0_f7_static, readonly=True, tag="F7")
step("scene props registered", lambda: ok(call("get_polyhaven_status"), must=["enabled"]) and "enabled=%s" % ok(call("get_polyhaven_status"))["enabled"], readonly=True)

# =========================================================================
# S1 dispatch
# =========================================================================
step("unknown command -> error", lambda: err(call("no_such_tool"), "Unknown command type"), readonly=True)
step("bad kwargs -> error not crash", lambda: err(call("move_object", bogus=1)), readonly=True)


def s1_gates():
    for cmd in srv._GATED_COMMANDS:
        err(call(cmd), "integration is disabled")
    return "%d gated commands refuse while toggles are off" % len(srv._GATED_COMMANDS)


step("integration gates closed by default", s1_gates, readonly=True)

# =========================================================================
# S2 scene / object info
# =========================================================================
clear_scene()
step("get_scene_info empty scene", lambda: (lambda r: (
    (r["object_count"] == 0 and r["objects"] == []) or (_ for _ in ()).throw(AssertionError(json.dumps(r))),
    "objects=%d materials=%d" % (r["object_count"], r["materials_count"]))[1])(ok(call("get_scene_info"))), readonly=True)

cube = make_cube("Cube")
make_light("Lamp")


def s2_info():
    r = ok(call("get_object_info", name="Cube"), must=["mesh", "world_bounding_box"])
    assert r["mesh"] == {"vertices": 8, "edges": 12, "polygons": 6}, r["mesh"]
    bb = r["world_bounding_box"]
    assert close(bb[0], (-1, -1, -1)) and close(bb[1], (1, 1, 1)), bb
    r2 = ok(call("get_object_info", name="Lamp"))
    assert "mesh" not in r2 and r2["type"] == "LIGHT"
    return "cube 8/12/6 bbox ok, light has no mesh key"


step("get_object_info mesh + light", s2_info, readonly=True)


def s2_find():
    r = ok(call("find_objects_by_type", obj_type="mesh"))
    assert r["count"] == 1 and r["objects"][0]["name"] == "Cube" and r["type"] == "MESH", r
    r2 = ok(call("find_objects_by_type", obj_type="LIGHT"))
    assert r2["count"] == 1 and r2["objects"][0]["name"] == "Lamp"
    r3 = ok(call("find_objects_by_type", obj_type="ARMATURE"))
    assert r3["count"] == 0 and r3["objects"] == []
    return "mesh=1 light=1 armature=0"


step("find_objects_by_type", s2_find, readonly=True)


def s2_measure():
    bpy.data.objects["Lamp"].location = (3, 4, 0)
    r = ok(call("measure_distance", name_a="Cube", name_b="Lamp"))
    assert abs(r["distance"] - 5.0) < 1e-6, r
    err(call("measure_distance", name_a="Cube", name_b="Nope"), "not found")
    bpy.data.objects["Lamp"].location = (2, -2, 4)
    return "3-4-5 triangle -> 5.0; missing object rejected"


step("measure_distance", s2_measure)
step("get_object_info missing -> error", lambda: err(call("get_object_info", name="Nope"), "not found"), readonly=True)


def s2_scene_cap():
    for i in range(12):
        make_cube("Filler%02d" % i, 0.1, (i, 0, 0))
    r = ok(call("get_scene_info"))
    assert r["object_count"] == 14 and len(r["objects"]) == 10, (r["object_count"], len(r["objects"]))
    for i in range(12):
        bpy.data.objects.remove(bpy.data.objects["Filler%02d" % i], do_unlink=True)
    return "object_count=14 listed=10 (cap honoured)"


step("get_scene_info caps list at 10", s2_scene_cap)

# =========================================================================
# S3 transforms
# =========================================================================
def s3_move():
    ok(call("move_object", name="Cube", x=1, y=2, z=3))
    assert close(cube.location, (1, 2, 3)), tuple(cube.location)
    ok(call("scale_object", name="Cube", x=2, y=1, z=0.5))
    assert close(cube.scale, (2, 1, 0.5))
    ok(call("rotate_object", name="Cube", x=90, y=0, z=45, mode="ZXY"))
    assert cube.rotation_mode == "ZXY" and close(cube.rotation_euler, (math.radians(90), 0, math.radians(45)))
    ok(call("rotate_object", name="Cube")); ok(call("scale_object", name="Cube")); ok(call("move_object", name="Cube"))
    assert close(cube.location, (0, 0, 0)) and close(cube.scale, (1, 1, 1)) and close(cube.rotation_euler, (0, 0, 0))
    return "move/scale/rotate applied and reset"


step("move/scale/rotate", s3_move)
step("move_object missing -> error", lambda: err(call("move_object", name="Nope"), "not found"), readonly=True)
step("move_object wrong type -> error", lambda: err(call("move_object", name="Cube", x="abc")), readonly=True)
step("rotate_object bad mode -> error", lambda: err(call("rotate_object", name="Cube", mode="NOPE")))

# =========================================================================
# S4 vertices
# =========================================================================
def s4_get():
    cube.location = (1, 2, 3)
    bpy.context.view_layer.update()
    w = ok(call("get_vertex_positions", name="Cube"))
    l = ok(call("get_vertex_positions", name="Cube", world_space=False))
    assert w["total_vertices"] == 8 and w["returned"] == 8
    for a, b in zip(w["vertices"], l["vertices"]):
        assert close(a["co"], [b["co"][0] + 1, b["co"][1] + 2, b["co"][2] + 3]), (a, b)
    sub = ok(call("get_vertex_positions", name="Cube", indices=[7, 0]))
    assert [v["index"] for v in sub["vertices"]] == [7, 0]
    cube.location = (0, 0, 0)
    return "world = local + offset for 8 verts; subset order kept"


step("get_vertex_positions world/local/subset", s4_get)
step("get_vertex_positions index 99 -> error", lambda: err(call("get_vertex_positions", name="Cube", indices=[99]), "out of range"), readonly=True)
step("get_vertex_positions index -1 -> error", lambda: err(call("get_vertex_positions", name="Cube", indices=[-1]), "out of range"), readonly=True)
step("get_vertex_positions non-int index -> error", lambda: err(call("get_vertex_positions", name="Cube", indices=["a"]), "integers"), readonly=True)
step("get_vertex_positions max_verts cap -> error", lambda: err(call("get_vertex_positions", name="Cube", max_verts=4), "max_verts"), readonly=True)
step("get_vertex_positions on light -> error", lambda: err(call("get_vertex_positions", name="Lamp"), "not a mesh"), readonly=True)


def s4_set_one():
    cube.location = (5, 0, 0)
    bpy.context.view_layer.update()
    ok(call("set_vertex_position", name="Cube", vertex_index=0, x=5.5, y=0, z=0))
    assert close(cube.data.vertices[0].co, (0.5, 0, 0)), tuple(cube.data.vertices[0].co)
    cube.location = (0, 0, 0)
    cube.data.vertices[0].co = (-1, -1, -1)
    return "world (5.5,0,0) stored as local (0.5,0,0)"


step("set_vertex_position world->local", s4_set_one)
step("set_vertex_position index 8 -> error", lambda: err(call("set_vertex_position", name="Cube", vertex_index=8, x=0, y=0, z=0), "out of range"), readonly=True)


def s4_batch():
    r = ok(call("set_vertex_positions", name="Cube", world_space=False,
                vertices=[{"index": 1, "co": [3, -1, -1]}, {"index": 42, "co": [0, 0, 0]}, {"co": [1, 1, 1]}]))
    assert r["updated_count"] == 1 and r["updated"] == [1] and len(r.get("errors", [])) == 2, r
    assert close(cube.data.vertices[1].co, (3, -1, -1))
    cube.data.vertices[1].co = (1, -1, -1)
    return "1 updated, 2 rejected (index 42, missing index)"


step("set_vertex_positions batch with bad entries", s4_batch)

# =========================================================================
# S5 curve control points
# =========================================================================
bez = make_bezier("Bez")


def s5_get():
    r = ok(call("get_control_points", name="Bez"))
    assert r["spline_type"] == "BEZIER" and r["point_count"] == 3 and r["spline_count"] == 2, r
    p1 = r["points"][1]
    assert close(p1["co"], (1, 0, 0)) and close(p1["handle_left"], (0.7, 0, 0)) and p1["handle_left_type"] == "FREE", p1
    r2 = ok(call("get_control_points", name="Bez", spline_index=1))
    assert r2["spline_type"] == "POLY" and close(r2["points"][2]["co"], (0, 2, 0)), r2
    return "bezier 3 pts with handles; poly spline index 1"


step("get_control_points bezier + poly", s5_get, readonly=True)
step("get_control_points spline 5 -> error", lambda: err(call("get_control_points", name="Bez", spline_index=5), "out of range"), readonly=True)
step("get_control_points on mesh -> error", lambda: err(call("get_control_points", name="Cube"), "not a curve"), readonly=True)


def s5_set():
    ok(call("set_control_point", name="Bez", point_index=1, co=[1, 1, 0], handle_left=[0.5, 1, 0],
            handle_right=[1.5, 1, 0], handle_left_type="ALIGNED", handle_right_type="ALIGNED"))
    p = bez.data.splines[0].bezier_points[1]
    assert close(p.co, (1, 1, 0)) and close(p.handle_right, (1.5, 1, 0)) and p.handle_left_type == "ALIGNED", (tuple(p.co), p.handle_left_type)
    ok(call("set_control_point", name="Bez", point_index=0, co=[9, 9, 9], spline_index=1))
    q = bez.data.splines[1].points[0]
    assert close(q.co, (9, 9, 9, 1.0)), tuple(q.co)
    return "bezier co/handles/types and poly co written"


step("set_control_point bezier + poly", s5_set)
step("set_control_point index 3 -> error", lambda: err(call("set_control_point", name="Bez", point_index=3, co=[0, 0, 0]), "out of range"), readonly=True)
step("set_control_point bad handle type -> error", lambda: err(call("set_control_point", name="Bez", point_index=0, co=[0, 0, 0], handle_left_type="WOBBLY")))

# =========================================================================
# S6 edges
# =========================================================================
def s6_edges():
    r = ok(call("get_edges", name="Cube"))
    assert r["total_edges"] == 12 and r["returned"] == 12 and all(not e["sharp"] for e in r["edges"])
    ok(call("mark_sharp_edges", name="Cube", edge_indices=[0, 5], sharp=True))
    r = ok(call("get_edges", name="Cube"))
    sharp = sorted(e["index"] for e in r["edges"] if e["sharp"])
    assert sharp == [0, 5], sharp
    ok(call("mark_sharp_edges", name="Cube", edge_indices="all", sharp=False))
    assert not any(e["sharp"] for e in ok(call("get_edges", name="Cube"))["edges"])
    ok(call("set_edge_crease", name="Cube", edge_indices=[3], crease=0.7))
    r = ok(call("set_edge_crease", name="Cube", edge_indices=[4], crease=1.5))
    assert r["crease"] == 1.0
    r = ok(call("get_edges", name="Cube", indices=[3, 4, 0]))
    cr = {e["index"]: e["crease"] for e in r["edges"]}
    assert abs(cr[3] - 0.7) < 1e-5 and cr[4] == 1.0 and cr[0] == 0.0, cr
    ok(call("set_edge_bevel_weight", name="Cube", edge_indices=[2], weight=0.25))
    bw = {e["index"]: e["bevel_weight"] for e in ok(call("get_edges", name="Cube"))["edges"]}
    assert abs(bw[2] - 0.25) < 1e-5 and bw[3] == 0.0, bw
    return "sharp [0,5], crease 0.7/clamped 1.0, bevel 0.25 all read back"


step("edge sharp/crease/bevel round trip", s6_edges)
step("get_edges index 12 -> error", lambda: err(call("get_edges", name="Cube", indices=[12]), "out of range"), readonly=True)
step("mark_sharp_edges index -3 -> error", lambda: err(call("mark_sharp_edges", name="Cube", edge_indices=[-3]), "out of range"), readonly=True)
step("set_edge_crease on curve -> error", lambda: err(call("set_edge_crease", name="Bez", edge_indices=[0], crease=1), "not a mesh"), readonly=True)

# =========================================================================
# S7 faces
# =========================================================================
def s7_get():
    cube.location = (0, 0, 2)
    bpy.context.view_layer.update()
    r = ok(call("get_faces", name="Cube"))
    assert r["total_faces"] == 6 and all(abs(mathutils.Vector(f["normal"]).length - 1) < 1e-4 for f in r["faces"])
    assert all(f["loop_total"] == 4 for f in r["faces"])
    top = [f for f in r["faces"] if close(f["normal"], (0, 0, 1))]
    assert len(top) == 1 and close(top[0]["center"], (0, 0, 3)), top
    l = ok(call("get_faces", name="Cube", world_space=False, indices=[top[0]["index"]]))
    assert close(l["faces"][0]["center"], (0, 0, 1))
    cube.location = (0, 0, 0)
    return "6 quads, unit normals, world/local centers differ by offset"


step("get_faces world/local", s7_get)
step("get_faces index 6 -> error", lambda: err(call("get_faces", name="Cube", indices=[6]), "out of range"), readonly=True)
step("set_face_material_index without slots -> error", lambda: err(call("set_face_material_index", name="Cube", face_indices=[0], material_index=0), "slot"), readonly=True)


def s7_matidx():
    m1 = bpy.data.materials.new("HarnessA"); m2 = bpy.data.materials.new("HarnessB")
    cube.data.materials.append(m1); cube.data.materials.append(m2)
    ok(call("set_face_material_index", name="Cube", face_indices=[0, 2], material_index=1))
    idx = [f["material_index"] for f in ok(call("get_faces", name="Cube"))["faces"]]
    assert idx == [1, 0, 1, 0, 0, 0], idx
    ok(call("set_face_material_index", name="Cube", face_indices="all", material_index=0))
    assert all(p.material_index == 0 for p in cube.data.polygons)
    err(call("set_face_material_index", name="Cube", face_indices=[9], material_index=1), "out of range")
    return "faces 0,2 -> slot 1; all -> 0; index 9 rejected"


step("set_face_material_index", s7_matidx)


def s7_flip():
    before = ok(call("get_faces", name="Cube", indices=[0]))["faces"][0]["normal"]
    r = ok(call("flip_normals", name="Cube", face_indices=[0]))
    after = ok(call("get_faces", name="Cube", indices=[0]))["faces"][0]["normal"]
    assert r["flipped_faces"] == 1 and close(after, [-v for v in before]), (before, after)
    other = ok(call("get_faces", name="Cube", indices=[1]))["faces"][0]["normal"]
    ok(call("flip_normals", name="Cube"))
    all_after = ok(call("get_faces", name="Cube"))["faces"]
    assert close(all_after[0]["normal"], before) and close(all_after[1]["normal"], [-v for v in other])
    ok(call("flip_normals", name="Cube", face_indices=[1, 2, 3, 4, 5]))
    return "face 0 normal negated then restored by flip-all; others negated"


step("flip_normals discriminating (hand-flipped face)", s7_flip)


def s7_extrude():
    top = [f for f in ok(call("get_faces", name="Cube"))["faces"] if close(f["normal"], (0, 0, 1))][0]
    r = ok(call("extrude_faces", name="Cube", face_indices=[top["index"]], amount=0.5))
    zmax = max(v.co.z for v in cube.data.vertices)
    assert abs(zmax - 1.5) < 1e-4, zmax
    # the original cap must be gone: no face may still lie flat at z=1 (interior face = non-manifold)
    interior = [p.index for p in cube.data.polygons
                if all(abs(cube.data.vertices[i].co.z - 1.0) < 1e-5 for i in p.vertices)]
    assert len(cube.data.vertices) == 12 and len(cube.data.polygons) == 10 and not interior, \
        "verts=%d faces=%d (want 12/10), faces left at z=1 inside the mesh: %s" % (len(cube.data.vertices), len(cube.data.polygons), interior)
    return "8->12 verts, 6->10 faces, top moved to z=1.5, no interior face"


step("extrude_faces", s7_extrude)
step("extrude_faces empty list -> error", lambda: err(call("extrude_faces", name="Cube", face_indices=[]), "No face"), readonly=True)


def s7_inset():
    nf = len(cube.data.polygons)
    ok(call("inset_faces", name="Cube", face_indices=[0], thickness=0.2))
    assert len(cube.data.polygons) == nf + 4, (nf, len(cube.data.polygons))
    nf2 = len(cube.data.polygons)
    ok(call("inset_faces", name="Cube", face_indices=[1, 2], thickness=0.1, use_individual=False))
    assert len(cube.data.polygons) > nf2
    return "individual inset +4 faces; region inset adds faces"


step("inset_faces individual + region", s7_inset)
step("inset_faces index 999 -> error", lambda: err(call("inset_faces", name="Cube", face_indices=[999]), "out of range"), readonly=True)


def s7_merge():
    me = bpy.data.meshes.new("Dup")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)], [], [(0, 1, 2), (3, 5, 4)])
    ob = bpy.data.objects.new("Dup", me); bpy.context.scene.collection.objects.link(ob)
    r = ok(call("merge_vertices", name="Dup", distance=0.001))
    assert r["removed"] == 2 and r["vertices_after"] == 4, r
    bpy.data.objects.remove(ob, do_unlink=True)
    return "6 verts with 2 duplicates -> 4"


step("merge_vertices removes duplicates", s7_merge)


def s7_tri():
    c = make_cube("Tri")
    r = ok(call("triangulate_mesh", name="Tri", method="BEAUTY"))
    assert r["triangles"] == 12 and all(p.loop_total == 3 for p in c.data.polygons), r
    err(call("triangulate_mesh", name="Tri", method="WORST"), "Unknown method")
    bpy.data.objects.remove(c, do_unlink=True)
    return "6 quads -> 12 tris; bad method rejected"


step("triangulate_mesh", s7_tri)


def s7_subdiv():
    c = make_cube("Sub")
    r = ok(call("subdivide_mesh", name="Sub", cuts=1))
    assert r["vertices"] == 26 and r["faces"] == 24, r
    bpy.data.objects.remove(c, do_unlink=True)
    return "cube cuts=1 -> 26 verts 24 faces"


step("subdivide_mesh", s7_subdiv)


def s7_apply():
    c = make_cube("Mod")
    m = c.modifiers.new("Subd", "SUBSURF"); m.levels = 1
    err(call("apply_modifier", name="Mod", modifier_name="Nope"), "not found")
    ok(call("apply_modifier", name="Mod", modifier_name="Subd"))
    assert len(c.modifiers) == 0 and len(c.data.polygons) == 24, (len(c.modifiers), len(c.data.polygons))
    bpy.data.objects.remove(c, do_unlink=True)
    return "SUBSURF applied -> 24 faces, modifier list empty"


step("apply_modifier", s7_apply)


def s7_stats():
    c = make_cube("Stat", 2.0, (1, 1, 1))
    c.modifiers.new("Bev", "BEVEL")
    r = ok(call("get_mesh_stats", name="Stat"))
    assert r["triangles"] == 12 and r["polygons"] == 6 and r["modifiers"] == ["Bev"], r
    assert close(r["bounding_box"][0], (0, 0, 0)) and close(r["bounding_box"][1], (2, 2, 2)), r["bounding_box"]
    bpy.data.objects.remove(c, do_unlink=True)
    return "12 tris, bbox (0..2), modifier listed"


step("get_mesh_stats", s7_stats)

# =========================================================================
# S8 edit-mode restore
# =========================================================================
def s8_editmode():
    c = make_cube("Edit")
    bpy.context.view_layer.objects.active = c
    c.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    assert bpy.context.mode == "EDIT_MESH", bpy.context.mode
    r = ok(call("get_vertex_positions", name="Edit", indices=[0]))
    assert bpy.context.mode == "OBJECT"
    bpy.ops.object.mode_set(mode="EDIT")
    ok(call("set_edge_crease", name="Edit", edge_indices=[0], crease=0.5))
    assert bpy.context.mode == "OBJECT"
    bpy.data.objects.remove(c, do_unlink=True)
    return "handlers left EDIT_MESH -> OBJECT twice"


step("handlers leave edit mode", s8_editmode)

# =========================================================================
# S9 camera + render settings (BREAK1, BREAK4)
# =========================================================================
def s9_create_cam():
    r = ok(call("create_camera", name="CamA", location=[0, -6, 3], look_at=[0, 0, 0], lens=35, cam_type="PERSP"))
    cam = bpy.data.objects[r["name"]]
    bpy.context.view_layer.update()
    fwd = (cam.matrix_world.to_3x3() @ mathutils.Vector((0, 0, -1))).normalized()
    want = (mathutils.Vector((0, 0, 0)) - mathutils.Vector((0, -6, 3))).normalized()
    assert fwd.dot(want) > 0.999, (tuple(fwd), tuple(want))
    assert cam.data.lens == 35
    ok(call("set_active_camera", name="CamA"))
    assert bpy.context.scene.camera == cam
    return "camera aims at look_at, lens 35, active"


step("create_camera + set_active_camera", s9_create_cam)
step("set_active_camera on mesh -> error", lambda: err(call("set_active_camera", name="Cube"), "not a camera"), readonly=True)
step("create_camera bad type -> error", lambda: err(call("create_camera", name="Bad", cam_type="FISHEYE")))


def s9_settings_basic():
    r = ok(call("set_render_settings", engine="BLENDER_WORKBENCH", width=64, height=48, file_format="JPEG", transparent_background=True))
    sc = bpy.context.scene
    assert sc.render.engine == "BLENDER_WORKBENCH" and sc.render.resolution_x == 64 and sc.render.resolution_y == 48
    assert sc.render.image_settings.file_format == "JPEG" and sc.render.film_transparent is True
    assert r["engine"] == "BLENDER_WORKBENCH" and r["resolution"] == [64, 48]
    return "workbench 64x48 JPEG transparent"


step("set_render_settings workbench/JPEG", s9_settings_basic)


def _eevee(spelling):
    bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
    r = ok(call("set_render_settings", engine=spelling, samples=3))
    eng = bpy.context.scene.render.engine
    assert eng in engine_ids(), "engine %s not in RNA enum %s" % (eng, engine_ids())
    assert eng.startswith("BLENDER_EEVEE"), eng
    assert r.get("engine") == eng, "reply engine %r does not name the resolved id %r" % (r.get("engine"), eng)
    assert bpy.context.scene.eevee.taa_render_samples == 3, bpy.context.scene.eevee.taa_render_samples
    return "%r -> engine=%s reply=%s taa_render_samples=3 (enum: %s)" % (spelling, eng, r["engine"], ",".join(engine_ids()))


step("M3 set_render_settings engine=BLENDER_EEVEE resolves on this build", lambda: _eevee("BLENDER_EEVEE"), tag="M3")
step("M3 set_render_settings engine=blender_eevee lower-case", lambda: _eevee("blender_eevee"), tag="M3")
step("M3 set_render_settings engine=eevee short alias", lambda: _eevee("eevee"), tag="M3")
step("M13 set_render_settings engine='' -> error", lambda: err(call("set_render_settings", engine="")), tag="M13")


def s9_cycles():
    r = ok(call("set_render_settings", engine="cycles", samples=4))
    assert bpy.context.scene.render.engine == "CYCLES" and bpy.context.scene.cycles.samples == 4
    assert r.get("engine") == "CYCLES", r
    r2 = ok(call("set_render_settings", engine="workbench"))
    assert r2.get("engine") == "BLENDER_WORKBENCH" == bpy.context.scene.render.engine, r2
    return "'cycles' -> CYCLES samples=4 reply engine=CYCLES; 'workbench' -> BLENDER_WORKBENCH"


step("M3 set_render_settings lowercase cycles + workbench alias, reply engine key", s9_cycles, tag="M3")
def s9_bogus():
    msg = err(call("set_render_settings", engine="BLENDER_BOGUS"))
    assert all(e in msg for e in engine_ids()), "error must list the live enum %s: %s" % (engine_ids(), A(msg))
    return "error lists live enum: %s" % A(msg)[:160]


step("M3 set_render_settings bogus engine -> error listing enum", s9_bogus, tag="M3")


def s9_formats():
    out = []
    for fmt in ("PNG", "JPEG", "OPEN_EXR", "TIFF"):
        ok(call("set_render_settings", file_format=fmt))
        assert bpy.context.scene.render.image_settings.file_format == fmt
        out.append(fmt)
    return "still formats " + ",".join(out)


step("M6 set_render_settings still formats", s9_formats, tag="M6")


def s9_video():
    ok(call("set_render_settings", file_format="FFMPEG"))
    ims = bpy.context.scene.render.image_settings
    assert ims.file_format == "FFMPEG", ims.file_format
    mt = getattr(ims, "media_type", None)
    ok(call("set_render_settings", file_format="PNG"))
    assert ims.file_format == "PNG"
    mt2 = getattr(ims, "media_type", None)
    return "FFMPEG accepted (media_type=%s) then PNG (media_type=%s)" % (mt, mt2)


step("M6 set_render_settings FFMPEG video format", s9_video, tag="M6")

# =========================================================================
# S9b renders
# =========================================================================
def render_state():
    sc = bpy.context.scene; r = sc.render
    return (sc.camera.name if sc.camera else None, r.resolution_x, r.resolution_y, r.resolution_percentage,
            r.filepath, r.image_settings.file_format, getattr(r.image_settings, "media_type", None),
            sc.cycles.samples, sc.eevee.taa_render_samples, r.engine)


def s9_render():
    ok(call("set_render_settings", engine="BLENDER_WORKBENCH", width=640, height=480, file_format="JPEG"))
    bpy.context.scene.render.resolution_percentage = 50
    st = render_state()
    path = os.path.join(WORK, "render_a.png")
    r = ok(call("render_from_camera", camera_name="CamA", filepath=path, width=48, height=32, samples=1))
    assert r["filepath"] == path
    detail = assert_not_blank(path)
    px = read_png(path)
    assert px.shape[1] == 48 and px.shape[0] == 32, px.shape
    assert render_state() == st, "render settings not restored: %s vs %s" % (render_state(), st)
    return detail + "; settings restored (file_format=%s media_type=%s)" % (st[5], st[6])


step("M6 render_from_camera 48x32 PNG + settings restored", s9_render, tag="M6")
step("render_from_camera missing camera -> error", lambda: err(call("render_from_camera", camera_name="Nope", filepath=os.path.join(WORK, "x.png")), "not found"))
step("render_from_camera mesh as camera -> error", lambda: err(call("render_from_camera", camera_name="Cube", filepath=os.path.join(WORK, "x.png")), "not a camera"))


def s9_no_cam():
    saved = bpy.context.scene.camera
    bpy.context.scene.camera = None
    try:
        return err(call("render_from_camera", filepath=os.path.join(WORK, "x.png")), "no active camera")
    finally:
        bpy.context.scene.camera = saved


step("render_from_camera no active camera -> error", s9_no_cam)


def s9_all():
    make_camera("CamB", (6, 0, 2))
    st = render_state()
    r = ok(call("render_all_cameras", width=40, height=30, samples=1, output_dir=WORK))
    assert r["total_cameras"] == 2 and r["rendered"] == 2, r
    det = [assert_not_blank(e["filepath"]) for e in r["renders"]]
    assert render_state() == st
    return "2 cameras: " + " | ".join(det)


step("render_all_cameras 2 cams", s9_all)
step("render_all_cameras bad output_dir -> error", lambda: err(call("render_all_cameras", output_dir=os.path.join(WORK, "nope_dir")), "does not exist"), readonly=True)

# =========================================================================
# S10 depth map (BREAK2)
# =========================================================================
def s10_depth():
    ok(call("set_render_settings", engine="BLENDER_WORKBENCH", width=32, height=32))
    cam = make_camera("CamD", (0, -6, 0), (0, 0, 0))
    bpy.context.scene.camera = cam
    bpy.context.view_layer.use_pass_z = False
    st = render_state()
    n_scenes = len(bpy.data.scenes); n_groups = len(bpy.data.node_groups)
    comp_before = getattr(bpy.context.scene, "compositing_node_group", "n/a") if hasattr(bpy.context.scene, "compositing_node_group") else getattr(bpy.context.scene, "use_nodes", "n/a")
    path = os.path.join(WORK, "depth.png")
    r = ok(call("render_depth_map", filepath=path, max_depth=10.0))
    assert os.path.exists(path) and os.path.getsize(path) > 0
    px = read_png(path)
    h, w = px.shape[:2]
    center = float(px[h // 2, w // 2, 0]); corner = float(px[1, 1, 0])
    assert center > corner + 0.2, "center %.3f should be brighter (nearer) than corner %.3f" % (center, corner)
    assert len(bpy.data.scenes) == n_scenes and not [s for s in bpy.data.scenes if "mcp_depth" in s.name], "temp scene leaked"
    assert not [g for g in bpy.data.node_groups if "mcp_depth" in g.name.lower()], "temp node group leaked: %s" % [g.name for g in bpy.data.node_groups]
    assert len(bpy.data.node_groups) == n_groups, "node_groups count changed %d -> %d" % (n_groups, len(bpy.data.node_groups))
    assert render_state() == st, "user scene render settings changed"
    comp_after = getattr(bpy.context.scene, "compositing_node_group", "n/a") if hasattr(bpy.context.scene, "compositing_node_group") else getattr(bpy.context.scene, "use_nodes", "n/a")
    assert comp_before == comp_after, "user compositor touched: %s -> %s" % (comp_before, comp_after)
    assert bpy.context.view_layer.use_pass_z is False, "user view layer use_pass_z was switched on"
    return "%dx%d center=%.3f corner=%.3f, no leaks, user scene + use_pass_z untouched" % (w, h, center, corner)


step("M4 render_depth_map near>far, no leaks", s10_depth, tag="M4")


def s10_nocam():
    saved = bpy.context.scene.camera
    bpy.context.scene.camera = None
    try:
        return err(call("render_depth_map", filepath=os.path.join(WORK, "d2.png")), "no active camera")
    finally:
        bpy.context.scene.camera = saved


step("M13 render_depth_map no camera -> error", s10_nocam, tag="M13")

# =========================================================================
# S11 lights + world
# =========================================================================
def s11_lights():
    for kind in ("POINT", "SUN", "SPOT", "AREA"):
        r = ok(call("add_light", light_type=kind, name="L_" + kind, location=[1, 2, 3], energy=42, color=[1, 0.5, 0.25], radius=0.3))
        o = bpy.data.objects[r["name"]]
        assert o.data.type == kind and abs(o.data.energy - 42) < 1e-6 and close(o.data.color, (1, 0.5, 0.25)), kind
        if hasattr(o.data, "shadow_soft_size"):
            assert abs(o.data.shadow_soft_size - 0.3) < 1e-6
    return "POINT/SUN/SPOT/AREA with energy, colour, radius"


step("add_light four types", s11_lights)
step("add_light bad type -> error", lambda: err(call("add_light", light_type="LASER")))


def s11_world():
    r = ok(call("set_world_background", color=[0.1, 0.2, 0.3], strength=2.0))
    tree = bpy.context.scene.world.node_tree
    bg = [n for n in tree.nodes if n.type == "BACKGROUND"]
    assert len(bg) == 1 and close(bg[0].inputs["Color"].default_value, (0.1, 0.2, 0.3, 1)) and bg[0].inputs["Strength"].default_value == 2.0
    err(call("set_world_background", hdri_path=os.path.join(WORK, "missing.hdr")), "not found")
    exr = os.path.join(WORK, "env.exr")
    img = bpy.data.images.new("env", 8, 4, float_buffer=True)
    img.filepath_raw = exr; img.file_format = "OPEN_EXR"; img.save(); bpy.data.images.remove(img)
    r = ok(call("set_world_background", hdri_path=exr, strength=0.5))
    env = [n for n in bpy.context.scene.world.node_tree.nodes if n.type == "TEX_ENVIRONMENT"]
    assert len(env) == 1 and env[0].image and env[0].image.filepath == exr, [n.type for n in bpy.context.scene.world.node_tree.nodes]
    return "solid colour node wired; missing HDRI rejected; EXR env wired"


step("set_world_background colour + hdri", s11_world)


def s11_3pt():
    r = ok(call("add_3point_lighting", subject_name="Cube", key_energy=10, fill_energy=20, back_energy=30))
    assert r["lights"] == ["Key_Light", "Fill_Light", "Back_Light"], r
    e = [bpy.data.objects[n].data.energy for n in r["lights"]]
    assert close(e, (10, 20, 30)), e
    return "3 lights with energies 10/20/30"


step("add_3point_lighting", s11_3pt)

# =========================================================================
# S12 export / import
# =========================================================================
def s12_roundtrip():
    src = make_cube("RT", 2.0, (0, 0, 0))
    det = []
    for fmt in ("glb", "gltf", "fbx", "obj", "stl", "ply"):
        path = os.path.join(WORK, "rt_%s.%s" % (fmt, fmt))
        r = ok(call("export_object", name="RT", filepath=path, file_format=fmt))
        assert os.path.exists(path) and os.path.getsize(path) > 0, path
        before = set(bpy.data.objects.keys())
        r2 = ok(call("import_file", filepath=path))
        new = [n for n in r2["imported_objects"] if n not in before]
        meshes = [bpy.data.objects[n] for n in new if bpy.data.objects[n].type == "MESH"]
        assert meshes, "no mesh imported from %s: %s" % (fmt, r2)
        bpy.context.view_layer.update()
        dims = meshes[0].dimensions
        assert all(abs(d - 2.0) < 1e-2 for d in dims), "%s dims %s" % (fmt, tuple(dims))
        det.append("%s:%dB" % (fmt, os.path.getsize(path)))
        for n in new:
            bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
    return "round trip ok, imported dims 2.0: " + " ".join(det)


step("export_object/import_file 6 formats round trip", s12_roundtrip)
step("export_object bad format -> error", lambda: err(call("export_object", name="Cube", file_format="dae"), "Unsupported"), readonly=True)
step("export_object missing object -> error", lambda: err(call("export_object", name="Nope", file_format="glb"), "not found"), readonly=True)
step("import_file missing -> error", lambda: err(call("import_file", filepath=os.path.join(WORK, "nope.glb")), "not found"), readonly=True)


def s12_badext():
    p = os.path.join(WORK, "x.txt"); open(p, "w").write("hi")
    return err(call("import_file", filepath=p), "Unsupported")


step("import_file .txt -> error", s12_badext, readonly=True)


def s12_blend():
    lib = os.path.join(WORK, "lib.blend")
    ob = bpy.data.objects["RT"]
    bpy.data.libraries.write(lib, {ob}, fake_user=True)
    r = ok(call("import_file", filepath=lib))
    assert r["imported_objects"], r
    n = r["imported_objects"][0]
    assert bpy.data.objects[n].type == "MESH"
    bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
    return "appended %s from lib.blend" % n


step("import_file .blend append", s12_blend)

# =========================================================================
# S13 save / load
# =========================================================================
def s13_save():
    p = os.path.join(WORK, "saved")
    r = ok(call("save_blend", filepath=p))
    assert r["filepath"].endswith(".blend") and os.path.exists(r["filepath"]), r
    return "saved to %s (%d bytes)" % (os.path.basename(r["filepath"]), os.path.getsize(r["filepath"]))


step("save_blend adds .blend", s13_save)
step("load_blend missing -> error", lambda: err(call("load_blend", filepath=os.path.join(WORK, "nope.blend")), "not found"), readonly=True)
step("load_blend non-blend -> error", lambda: err(call("load_blend", filepath=os.path.join(WORK, "x.txt")), "only accepts"), readonly=True)


def s13_load():
    n_before = len(bpy.context.scene.objects)
    make_cube("Extra")
    assert len(bpy.context.scene.objects) == n_before + 1
    # 2.1.0 dirty guard (B0 section 1): without force the load is refused while bpy.data.is_dirty is True
    # (4.3.2 headless is always dirty; 5.2.1 headless never is, per S-new)
    res = call("load_blend", filepath=os.path.join(WORK, "saved.blend"))
    r = res.get("result") if isinstance(res.get("result"), dict) else {}
    if bpy.data.is_dirty and "error" in r and "force" in r["error"]:
        guard = "refused while dirty"
        r = ok(call("load_blend", filepath=os.path.join(WORK, "saved.blend"), force=True))
    else:
        r = ok(res)
        guard = "not dirty (or pre-2.1 addon), loaded directly"
    assert r["object_count"] == n_before, (r, n_before)
    assert "Extra" not in bpy.data.objects
    ok(call("get_polyhaven_status"))
    return "reloaded: %d objects, Extra gone, props alive (guard: %s)" % (r["object_count"], guard)


step("load_blend restores saved state", s13_load)

# =========================================================================
# S14 primitives + object management
# =========================================================================
clear_scene()


def s14_prims():
    det = []
    for p in ("cube", "plane", "circle", "sphere", "ico_sphere", "cylinder", "cone", "torus", "monkey"):
        r = ok(call("add_primitive", primitive_type=p, size=2.0, location=[1, 0, 0], name="P_" + p))
        o = bpy.data.objects.get(r["name"])
        assert o and o.type == "MESH" and o.name == "P_" + p, r
        bpy.context.view_layer.update()
        det.append("%s:%dv" % (p, len(o.data.vertices)))
        if p in ("cube", "sphere", "cylinder"):
            assert all(abs(d - 2.0) < 1e-3 for d in o.dimensions), (p, tuple(o.dimensions))
        assert close(o.location, (1, 0, 0))
    return " ".join(det)


step("add_primitive 9 types", s14_prims)
step("add_primitive unknown -> error", lambda: err(call("add_primitive", primitive_type="dodecahedron"), "Unknown primitive"))


def s14_delete():
    mesh_name = bpy.data.objects["P_monkey"].data.name
    r = ok(call("delete_object", name="P_monkey"))
    assert "P_monkey" not in bpy.data.objects
    assert mesh_name not in bpy.data.meshes or bpy.data.meshes[mesh_name].users > 0, "orphan mesh %s not purged" % mesh_name
    return "object and orphan mesh gone"


step("delete_object purges", s14_delete)
step("delete_object missing -> error", lambda: err(call("delete_object", name="Nope"), "not found"), readonly=True)


def s14_dup():
    r = ok(call("duplicate_object", name="P_cube", new_name="Dup1", offset=[2, 0, 0]))
    d = bpy.data.objects[r["duplicate"]]
    assert d.data != bpy.data.objects["P_cube"].data and close(d.location, (3, 0, 0))
    r2 = ok(call("duplicate_object", name="P_cube", new_name="Dup2", linked=True))
    assert bpy.data.objects[r2["duplicate"]].data == bpy.data.objects["P_cube"].data
    return "full copy at +2 with own mesh; linked copy shares mesh"


step("duplicate_object full + linked", s14_dup)


def s14_join():
    r = ok(call("join_objects", names=["Dup1", "Dup2"], result_name="Joined"))
    j = bpy.data.objects[r["result"]]
    assert r["merged_count"] == 2 and len(j.data.vertices) == 16 and "Dup2" not in bpy.data.objects, r
    return "16 verts in Joined"


step("join_objects", s14_join)
step("join_objects missing -> error", lambda: err(call("join_objects", names=["Joined", "Nope"]), "not found"), readonly=True)


def s14_sep():
    r = ok(call("separate_mesh", name="Joined", method="LOOSE"))
    assert len(r["new_objects"]) == 1 and len(bpy.data.objects["Joined"].data.vertices) == 8, r
    err(call("separate_mesh", name="Joined", method="BOGUS"), "Unknown method")
    return "LOOSE split into 2 x 8 verts"


step("separate_mesh LOOSE", s14_sep)


def s14_rename():
    r = ok(call("rename_object", old_name="P_plane", new_name="Ground.001"))
    assert r["new_name"] == "Ground.001" and bpy.data.objects["Ground.001"].data.name == "Ground.001"
    r2 = ok(call("rename_object", old_name="P_circle", new_name="Ground.001"))
    assert r2["new_name"] == bpy.data.objects[r2["new_name"]].name and r2["new_name"] != "Ground.001", r2
    return "dotted name kept; clash reported as actual name %s" % r2["new_name"]


step("rename_object dotted + clash", s14_rename)


def s14_origin():
    c = make_cube("Org")
    for v in c.data.vertices:
        v.co.x += 5
    ok(call("set_origin", name="Org", origin_type="ORIGIN_GEOMETRY"))
    assert close(c.location, (5, 0, 0)) and abs(sum(v.co.x for v in c.data.vertices)) < 1e-4, tuple(c.location)
    err(call("set_origin", name="Org", origin_type="ORIGIN_NOPE"))
    return "origin moved to geometry centre (5,0,0); bad type rejected"


step("set_origin", s14_origin)


def s14_snap():
    c = bpy.data.objects["Org"]; c.location.z = 7
    r = ok(call("snap_to_ground", name="Org", ground_z=0.5))
    bpy.context.view_layer.update()
    minz = min((c.matrix_world @ mathutils.Vector(b)).z for b in c.bound_box)
    assert abs(minz - 0.5) < 1e-4, minz
    return "lowest bbox point at z=0.5"


step("snap_to_ground", s14_snap)


def s14_smooth():
    c = bpy.data.objects["Org"]
    r = ok(call("set_smooth_shading", name="Org", smooth=True, auto_smooth=True, angle=40))
    assert all(p.use_smooth for p in c.data.polygons) and r["auto_smooth"] and r["auto_smooth_method"], r
    r2 = ok(call("set_smooth_shading", name="Org", smooth=False))
    assert not any(p.use_smooth for p in c.data.polygons) and not r2["auto_smooth"]
    return "smooth via %s, then flat" % r["auto_smooth_method"]


step("set_smooth_shading", s14_smooth)


def s14_parent():
    p = bpy.data.objects["P_cube"]; c = bpy.data.objects["Org"]
    p.location = (1, 1, 1); bpy.context.view_layer.update()
    mw = c.matrix_world.copy()
    ok(call("parent_object", child_name="Org", parent_name="P_cube", keep_transform=True))
    bpy.context.view_layer.update()
    assert c.parent == p and all(close(a, b) for a, b in zip(c.matrix_world, mw))
    err(call("parent_object", child_name="Org", parent_name="Nope"), "not found")
    return "parented, world matrix kept"


step("parent_object keep transform", s14_parent)


def s14_select():
    ok(call("select_objects", action="DESELECT"))
    assert not bpy.context.selected_objects
    ok(call("select_objects", names=["Org", "P_cube"], action="SELECT"))
    assert sorted(o.name for o in bpy.context.selected_objects) == ["Org", "P_cube"]
    ok(call("select_objects", names=["Org"], action="TOGGLE"))
    assert [o.name for o in bpy.context.selected_objects] == ["P_cube"]
    ok(call("select_objects", action="DESELECT"))
    r = ok(call("select_objects", obj_type="mesh"))
    assert set(r["names"]) == {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
    return "deselect/select/toggle/by-type"


step("select_objects", s14_select)


def s14_align():
    a = make_cube("Al1", 1, (0, 1, 0)); b = make_cube("Al2", 1, (0, 3, 0)); c = make_cube("Al3", 1, (0, 8, 0))
    ok(call("align_objects", names=["Al1", "Al2", "Al3"], axis="Y", align_to="AVERAGE"))
    assert all(abs(o.location.y - 4) < 1e-6 for o in (a, b, c))
    b.location.y = 9
    ok(call("align_objects", names=["Al1", "Al2", "Al3"], axis="y", align_to="max"))
    assert all(abs(o.location.y - 9) < 1e-6 for o in (a, b, c))
    err(call("align_objects", names=["Al1"], align_to="MIDDLE"), "Unknown align_to")
    return "AVERAGE=4, MAX=9, bad mode rejected"


step("align_objects", s14_align)

# =========================================================================
# S14b  M8 sweep: active object + mode after every mutating handler
# =========================================================================
def s14_m8():
    anchor = make_cube("M8Anchor", 1.0, (0, 0, -5))
    victim = make_cube("M8Victim", 2.0, (0, 0, 0))
    make_camera("M8Cam", (0, -6, 2))
    png = write_small_png(os.path.join(WORK, "m8.png"))
    bpy.data.materials.new("M8Mat")
    payloads = [
        ("move_object", dict(name="M8Victim", x=1)), ("scale_object", dict(name="M8Victim")), ("rotate_object", dict(name="M8Victim")),
        ("set_object_material_color", dict(name="M8Victim", r=0.5)),
        ("set_vertex_position", dict(name="M8Victim", vertex_index=0, x=-1, y=-1, z=-1)),
        ("set_vertex_positions", dict(name="M8Victim", vertices=[{"index": 0, "co": [-1, -1, -1]}])),
        ("mark_sharp_edges", dict(name="M8Victim", edge_indices=[0])), ("set_edge_crease", dict(name="M8Victim", edge_indices=[0], crease=0.5)),
        ("set_edge_bevel_weight", dict(name="M8Victim", edge_indices=[0], weight=0.5)),
        ("set_face_material_index", dict(name="M8Victim", face_indices=[0], material_index=0)),
        ("extrude_faces", dict(name="M8Victim", face_indices=[1], amount=0.1)), ("inset_faces", dict(name="M8Victim", face_indices=[0])),
        ("flip_normals", dict(name="M8Victim")), ("merge_vertices", dict(name="M8Victim")), ("triangulate_mesh", dict(name="M8Victim")),
        ("subdivide_mesh", dict(name="M8Victim", cuts=1)), ("add_modifier", dict(name="M8Victim", modifier_type="SUBSURF", modifier_name="M8S", props={"levels": 1})),
        ("apply_modifier", dict(name="M8Victim", modifier_name="M8S")),
        ("create_camera", dict(name="M8Cam2")), ("set_active_camera", dict(name="M8Cam")),
        ("set_render_settings", dict(engine="BLENDER_WORKBENCH", width=16, height=16)),
        ("render_from_camera", dict(camera_name="M8Cam", filepath=os.path.join(WORK, "m8r.png"), width=16, height=16, samples=1)),
        ("add_light", dict(light_type="POINT", name="M8L")), ("set_world_background", dict(color=[0.1, 0.1, 0.1])), ("add_3point_lighting", dict()),
        ("export_object", dict(name="M8Victim", filepath=os.path.join(WORK, "m8.obj"), file_format="obj")),
        ("import_file", dict(filepath=os.path.join(WORK, "m8.obj"))),
        ("add_primitive", dict(primitive_type="cube", name="M8Prim")), ("duplicate_object", dict(name="M8Prim", new_name="M8Dup")),
        ("join_objects", dict(names=["M8Dup", "M8Prim"])), ("separate_mesh", dict(name="M8Dup", method="LOOSE")),
        ("rename_object", dict(old_name="M8Dup", new_name="M8Dup")), ("set_origin", dict(name="M8Victim")), ("snap_to_ground", dict(name="M8Victim")),
        ("set_smooth_shading", dict(name="M8Victim")), ("parent_object", dict(child_name="M8Victim", parent_name="M8Cam")),
        ("select_objects", dict(names=["M8Victim"], action="SELECT")), ("align_objects", dict(names=["M8Victim", "M8Dup"], axis="X")),
        ("create_material", dict(name="M8Mat2", assign_to="M8Victim")), ("assign_material", dict(object_name="M8Victim", material_name="M8Mat")),
        ("load_texture", dict(material_name="M8Mat2", image_path=png)),
        ("add_keyframe", dict(name="M8Victim", data_path="location", frame=3)), ("set_frame", dict(frame=1)),
        ("create_collection", dict(name="M8Col")), ("move_to_collection", dict(object_names=["M8Dup"], collection_name="M8Col")),
        ("delete_object", dict(name="M8L")),
        ("render_depth_map", dict(filepath=os.path.join(WORK, "m8d.png"))),
    ]
    # Lead rulings (F2 split): for these three the RESULT object named in the reply must be active;
    # separate_mesh keeps the SOURCE object active by design (m_fe729ba3); every other handler restores.
    result_active = {"add_primitive": "name", "import_file": "imported_objects", "join_objects": "result"}
    source_active = {"separate_mesh": "name"}
    changed, failed = [], []
    for cmd, params in payloads:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        anchor.select_set(True)
        bpy.context.view_layer.objects.active = anchor
        res = call(cmd, **params)
        after = bpy.context.view_layer.objects.active
        after_name = after.name if after else None
        mode = bpy.context.mode
        r = res.get("result") if isinstance(res.get("result"), dict) else {}
        if res.get("status") != "success" or "error" in r:
            failed.append("%s: %s" % (cmd, A(res.get("message") or r.get("error"))[:80]))
        if cmd in result_active:
            want = r.get(result_active[cmd])
            want = want[0] if isinstance(want, list) and want else want
            good = after_name == want and want is not None
            expect = "result %r" % want
        elif cmd in source_active:
            want = params[source_active[cmd]]
            good = after_name == want
            expect = "source %r" % want
        else:
            good = after_name == "M8Anchor"
            expect = "M8Anchor"
        good = good and mode == "OBJECT"
        if not good:
            changed.append("%s active->%s (expected %s) mode=%s" % (cmd, after_name, expect, mode))
        P("M8 %s %s active=%s expected=%s mode=%s" % (cmd, "ok" if good else "CHANGED", after_name, expect, mode))
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    assert not failed, "handlers errored during sweep: %s" % failed
    assert not changed, "%d handlers changed active/mode: %s" % (len(changed), changed)
    return "%d handlers: active restored (or = result object for the 4 creators) and OBJECT mode" % len(payloads)


step("M8 active object + mode kept after every mutating handler", s14_m8, tag="M8")

# =========================================================================
# S15 materials
# =========================================================================
def s15_create():
    r = ok(call("create_material", name="MatA", base_color=[0.2, 0.4, 0.6], metallic=0.7, roughness=0.3,
                emission_color=[1, 0, 0], emission_strength=2.5, alpha=0.5, assign_to="P_cube"))
    m = bpy.data.materials["MatA"]
    b = [n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"][0]
    assert close(b.inputs["Base Color"].default_value, (0.2, 0.4, 0.6, 1)) and abs(b.inputs["Metallic"].default_value - 0.7) < 1e-6
    assert abs(b.inputs["Alpha"].default_value - 0.5) < 1e-6 and abs(b.inputs["Emission Strength"].default_value - 2.5) < 1e-6
    if hasattr(m, "surface_render_method"):
        assert m.surface_render_method == "BLENDED"
    assert bpy.data.objects["P_cube"].data.materials[0] == m
    return "PBR inputs set, BLENDED alpha, assigned to P_cube"


step("create_material full PBR", s15_create)


def s15_assign():
    bpy.data.materials.new("MatB")
    r = ok(call("assign_material", object_name="P_sphere", material_name="MatB", slot=2))
    mats = bpy.data.objects["P_sphere"].data.materials
    assert len(mats) == 3 and mats[2].name == "MatB" and mats[0] is None
    err(call("assign_material", object_name="P_sphere", material_name="Nope"), "not found")
    return "slot 2 padded with 2 empty slots"


step("assign_material slot padding", s15_assign)


def s15_color():
    r = ok(call("set_object_material_color", name="P_cone", r=0.9, g=0.1, b=0.2, a=1.0))
    m = bpy.data.materials[r["material"]]
    b = m.node_tree.nodes.get("Principled BSDF")
    assert b and close(b.inputs["Base Color"].default_value, (0.9, 0.1, 0.2, 1))
    return "material created and coloured"


step("set_object_material_color creates material", s15_color)


def s15_tex():
    png = write_small_png(os.path.join(WORK, "tex.png"))
    ok(call("create_material", name="MatT"))
    r = ok(call("load_texture", material_name="MatT", image_path=png, texture_slot="Base Color", uv_scale=2.0))
    m = bpy.data.materials["MatT"]
    b = [n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"][0]
    assert b.inputs["Base Color"].is_linked
    r = ok(call("load_texture", material_name="MatT", image_path=png, texture_slot="Normal"))
    assert b.inputs["Normal"].is_linked and b.inputs["Normal"].links[0].from_node.type == "NORMAL_MAP"
    img = bpy.data.images["tex.png"]
    assert img.colorspace_settings.name == "Non-Color", img.colorspace_settings.name
    err(call("load_texture", material_name="MatT", image_path=png, texture_slot="Shininess"), "not found")
    err(call("load_texture", material_name="MatT", image_path=os.path.join(WORK, "nope.png")), "not found")
    return "Base Color linked, Normal via NormalMap Non-Color, bad slot/path rejected"


step("load_texture base + normal", s15_tex)


def s15_f7_runtime():
    """set_texture ARM branch without network: images named <id>_<map>.png in bpy.data, PolyHaven toggle on."""
    sc = bpy.context.scene
    arm = write_small_png(os.path.join(WORK, "harntex_arm.png"), (0.5, 0.25, 0.75, 1.0))
    col = write_small_png(os.path.join(WORK, "harntex_color.png"), (0.9, 0.3, 0.1, 1.0))
    for p in (arm, col):
        if os.path.basename(p) not in bpy.data.images:
            bpy.data.images.load(p)
    c = make_cube("F7Cube")
    sc.blendermcp_use_polyhaven = True
    try:
        r = ok(call("set_texture", object_name="F7Cube", texture_id="harntex"))
    finally:
        sc.blendermcp_use_polyhaven = False
    mat = c.data.materials[0] if c.data.materials else None
    assert mat and mat.node_tree, "no material assigned by set_texture: %s" % A(r)
    seps = [n for n in mat.node_tree.nodes if n.type in ("SEPARATE_COLOR", "SEPRGB")]
    assert len(seps) == 1, "expected one separate node, found types %s" % [n.type for n in mat.node_tree.nodes]
    sep = seps[0]
    assert len(sep.outputs) == 3, "separate node has %d outputs" % len(sep.outputs)
    bsdf = [n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"][0]
    for sock in ("Roughness", "Metallic", "Base Color"):
        assert bsdf.inputs[sock].is_linked, "%s not linked after ARM wiring" % sock
    assert bsdf.inputs["Roughness"].links[0].from_node == sep and bsdf.inputs["Metallic"].links[0].from_node == sep
    ao_path = bsdf.inputs["Base Color"].links[0].from_node
    return "separate node %s (%s) with 3 outputs; G->Roughness, B->Metallic, Base Color via %s" % (sep.bl_idname, sep.type, ao_path.bl_idname)


step("F7 runtime: set_texture ARM branch builds a 3-output separate node", s15_f7_runtime, tag="F7")

# =========================================================================
# S16 modifiers + boolean (BREAK3)
# =========================================================================
def s16_mod():
    r = ok(call("add_modifier", name="P_cube", modifier_type="bevel", modifier_name="B1", props={"width": 0.2, "segments": 3, "nonsense": 1}))
    m = bpy.data.objects["P_cube"].modifiers["B1"]
    assert abs(m.width - 0.2) < 1e-6 and m.segments == 3 and r["unset"] == {"nonsense": "no such property"}, r
    r2 = ok(call("add_modifier", name="P_cube", modifier_type="BOOLEAN", props={"object": "P_sphere", "operation": "UNION"}))
    m2 = bpy.data.objects["P_cube"].modifiers[r2["modifier"]]
    assert m2.object == bpy.data.objects["P_sphere"] and m2.operation == "UNION"
    r3 = ok(call("add_modifier", name="P_cube", modifier_type="BOOLEAN", props={"object": "Nope"}))
    assert "object" in r3.get("unset", {}), r3
    err(call("add_modifier", name="P_cube", modifier_type="WARP_DRIVE"), "Valid types")
    return "bevel props set + unset reported; boolean object by name; bad type lists enum"


step("add_modifier props/unset/pointer", s16_mod)


def _bool(solver, apply=True):
    # cutter is a smaller cube poking into the +x face: no coplanar faces, so every solver gets a fair cut
    tn, cn = "BT_" + solver, "BC_" + solver
    make_cube(tn, 2.0, (0, 0, 0)); make_cube(cn, 1.0, (1, 0, 0))
    res = call("boolean_operation", target_name=tn, cutter_name=cn, operation="DIFFERENCE", solver=solver, apply=apply)
    return tn, cn, res


def _bool_ok(solver):
    """Solver accepted: DIFFERENCE carved a notch (inner wall at x=0.5), cutter removed, reply names a live solver."""
    tn, cn, res = _bool(solver)
    r = ok(res)
    verts = bpy.data.objects[tn].data.vertices
    xmax = max(v.co.x for v in verts); xmin = min(v.co.x for v in verts)
    inner = sum(1 for v in verts if abs(v.co.x - 0.5) < 1e-3)
    assert len(verts) > 8 and inner >= 4, "target not cut: verts=%d inner-wall verts=%d" % (len(verts), inner)
    assert xmax < 1.01 and xmin > -1.01, "target grew beyond its box: x %.2f..%.2f (cutter geometry merged in?)" % (xmin, xmax)
    assert cn not in bpy.data.objects, "cutter still present"
    assert r.get("solver") in solver_ids(), "reply solver %r not in live enum %s" % (r.get("solver"), solver_ids())
    return "%s -> reply solver=%s (enum %s), notch wall verts=%d total verts=%d" % (solver, r["solver"], "/".join(solver_ids()), inner, len(verts))


step("boolean_operation EXACT apply", lambda: _bool_ok("EXACT"), tag="M5")
step("boolean_operation FAST accepted, reply names live solver", lambda: _bool_ok("FAST"), tag="M5")
step("boolean_operation FLOAT accepted, reply names live solver", lambda: _bool_ok("FLOAT"), tag="M5")
step("boolean_operation lowercase 'fast' accepted", lambda: _bool_ok("fast"), tag="M13")


def s16_manifold():
    tn, cn, res = _bool("MANIFOLD")
    if "MANIFOLD" in solver_ids():
        r = ok(res)
        assert r.get("solver") == "MANIFOLD", r
        return "MANIFOLD passed through on this build, verts=%d" % len(bpy.data.objects[tn].data.vertices)
    msg = err(res)
    assert "EXACT" in msg, "error must list the live enum: %s" % A(msg)
    return "MANIFOLD absent on this build, error lists enum"


step("boolean_operation MANIFOLD per ruling", s16_manifold, tag="M5")


def s16_bogus():
    tn, cn, res = _bool("BOGUS")
    msg = err(res)
    assert "EXACT" in msg, A(msg)
    left = [m.name for m in bpy.data.objects[tn].modifiers]
    assert not left, "BOGUS solver left a modifier on the target: %s" % left
    assert cn in bpy.data.objects, "cutter deleted although the operation failed"
    return "error lists live enum, no modifier left, cutter kept: %s" % A(msg)[:100]


step("boolean_operation bogus solver -> error listing enum", s16_bogus, tag="M5")


def s16_noapply():
    tn, cn, res = _bool("FAST", apply=False)
    r = ok(res)
    t = bpy.data.objects[tn]
    assert len(t.modifiers) == 1 and t.modifiers[0].type == "BOOLEAN" and cn in bpy.data.objects
    assert t.modifiers[0].solver == r.get("solver"), "modifier solver %s != reply %s" % (t.modifiers[0].solver, r.get("solver"))
    assert t.modifiers[0].object == bpy.data.objects[cn]
    return "modifier kept with solver=%s (== reply), cutter kept" % t.modifiers[0].solver


step("boolean_operation apply=False", s16_noapply, tag="BREAK3")
step("boolean_operation missing target -> error", lambda: err(call("boolean_operation", target_name="Nope", cutter_name="P_cube"), "not found"), readonly=True)

# =========================================================================
# S17 animation
# =========================================================================
def s17_keys():
    c = make_cube("Anim")
    ok(call("add_keyframe", name="Anim", data_path="location", frame=10, value=[1, 2, 3]))
    ok(call("add_keyframe", name="Anim", data_path="location", frame=20, value=[3, 2, 1]))
    assert c.animation_data and c.animation_data.action
    bpy.context.scene.frame_set(15)
    assert close(c.location, (2, 2, 2), 1e-3), tuple(c.location)
    ok(call("add_keyframe", name="Anim", data_path="rotation_euler", frame=10, value=[90, 0, 0]))
    bpy.context.scene.frame_set(10)
    assert abs(c.rotation_euler.x - math.radians(90)) < 1e-4
    lamp = make_light("KLamp")
    r = ok(call("add_keyframe", name="KLamp", data_path="data.energy", frame=5, value=500))
    assert r["keyed_on"] == lamp.data.name and lamp.data.animation_data.action, r
    bpy.context.scene.frame_set(1)
    return "location interpolates to (2,2,2) at 15; rotation degrees; data.energy keyed on light data"


step("add_keyframe vector/scalar/dotted", s17_keys)
step("add_keyframe wrong arity -> error", lambda: err(call("add_keyframe", name="Anim", data_path="location", value=5), "expects"), readonly=True)
step("add_keyframe bad path -> error", lambda: err(call("add_keyframe", name="Anim", data_path="no.such.path"), "Cannot resolve"), readonly=True)
step("add_keyframe missing object -> error", lambda: err(call("add_keyframe", name="Nope"), "not found"), readonly=True)


def s17_frame():
    r = ok(call("set_frame", frame=33))
    assert r["frame"] == 33 and bpy.context.scene.frame_current == 33
    bpy.context.scene.frame_set(1)
    return "frame 33"


step("set_frame", s17_frame)

# =========================================================================
# S18 collections
# =========================================================================
def s18_coll():
    ok(call("create_collection", name="Kit"))
    ok(call("create_collection", name="KitChild", parent_collection="Kit"))
    assert "KitChild" in bpy.data.collections["Kit"].children and "Kit" in bpy.context.scene.collection.children
    err(call("create_collection", name="Orphan", parent_collection="Nope"), "not found")
    r = ok(call("move_to_collection", object_names=["Anim", "Org"], collection_name="KitChild"))
    for n in ("Anim", "Org"):
        assert [c.name for c in bpy.data.objects[n].users_collection] == ["KitChild"]
    ok(call("move_to_collection", object_names="Al1", collection_name="Kit"))
    assert [c.name for c in bpy.data.objects["Al1"].users_collection] == ["Kit"]
    err(call("move_to_collection", object_names=["Anim"], collection_name="Nope"), "not found")
    err(call("move_to_collection", object_names=["Nope"], collection_name="Kit"), "not found")
    return "nested collections; objects moved exclusively; string name accepted"


step("create_collection + move_to_collection", s18_coll)

# =========================================================================
# S19 execute_code, reference images, status
# =========================================================================
def s19_code():
    r = ok(call("execute_code", code="print('hello', len(bpy.data.objects))"))
    assert r["result"].startswith("hello "), r
    err(call("execute_code", code="1/0"), "Code execution error")
    return "stdout captured; exception surfaced"


step("execute_code", s19_code, readonly=True)


def s19_refs():
    err(call("store_reference_image", name="r1", filepath=os.path.join(WORK, "nope.png")), "not found")
    p = write_small_png(os.path.join(WORK, "ref.png"))
    ok(call("store_reference_image", name="r1", filepath=p))
    r = ok(call("get_reference_image", name="r1"))
    assert r["filepath"] == p and r["exists"] is True
    err(call("get_reference_image", name="zzz"), "not found")
    return "store/get round trip, misses rejected"


step("store/get_reference_image", s19_refs, readonly=True)


def s19_status():
    sc = bpy.context.scene
    assert ok(call("get_hyper3d_status"))["enabled"] is False
    assert ok(call("get_sketchfab_status"))["enabled"] is False
    assert ok(call("get_hunyuan3d_status"))["enabled"] is False
    sc.blendermcp_use_hyper3d = True
    assert ok(call("get_hyper3d_status"))["enabled"] is False  # no key
    sc.blendermcp_hyper3d_api_key = "x"
    assert ok(call("get_hyper3d_status"))["enabled"] is True
    sc.blendermcp_use_hunyuan3d = True; sc.blendermcp_hunyuan3d_mode = "OFFICIAL_API"
    assert ok(call("get_hunyuan3d_status"))["enabled"] is False
    sc.blendermcp_hunyuan3d_mode = "LOCAL_API"
    assert ok(call("get_hunyuan3d_status"))["enabled"] is True
    sc.blendermcp_use_polyhaven = True
    assert ok(call("get_polyhaven_status"))["enabled"] is True
    err(call("get_polyhaven_categories", asset_type="junk"), "Invalid asset type")
    for k in ("blendermcp_use_hyper3d", "blendermcp_use_hunyuan3d", "blendermcp_use_polyhaven"):
        setattr(sc, k, False)
    sc.blendermcp_hyper3d_api_key = ""
    return "status handlers follow toggles/keys; polyhaven bad asset_type rejected without network"


step("integration status handlers", s19_status)

# =========================================================================
# S20 viewport-only handlers (LIVE PASS REQUIRED)
# =========================================================================
def s20_shot():
    r = call("get_viewport_screenshot", filepath=os.path.join(WORK, "shot.png"), max_size=100)
    msg = err(r)
    assert not os.path.exists(os.path.join(WORK, "shot.png"))
    return "graceful headless error: %s" % A(msg)[:80]


step("get_viewport_screenshot headless graceful", s20_shot, readonly=True, tag="LIVE")
step("get_viewport_screenshot no filepath -> error", lambda: err(call("get_viewport_screenshot"), "No filepath"), readonly=True, tag="LIVE")


def s20_angle():
    msg = err(call("capture_viewport_angle", angle="front", filepath=os.path.join(WORK, "ang.png")))
    msg2 = err(call("capture_viewport_angle", angle="sideways", filepath=os.path.join(WORK, "ang2.png")))
    # contact sheet: an error envelope (2.0.0) or, since B1-A L1, a success envelope whose every image carries an
    # error and no file exists; either way nothing may be captured headless
    res3 = call("capture_contact_sheet", filepath=os.path.join(WORK, "sheet.png"))
    r3 = res3.get("result") if isinstance(res3.get("result"), dict) else {}
    if res3.get("status") == "error" or "error" in r3:
        msg3 = err(res3)
        form = "error envelope"
    else:
        imgs = r3.get("images") or {}
        assert imgs and all(isinstance(v, dict) and "error" in v for v in imgs.values()), "headless contact sheet must not capture anything: %s" % A(r3)
        assert not any(os.path.exists(v.get("filepath", "")) for v in imgs.values() if isinstance(v, dict) and v.get("filepath"))
        msg3 = next(iter(imgs.values()))["error"]
        form = "success envelope with per-angle errors (B1-A L1 shape)"
    return "front: %s | bad angle: %s | sheet (%s): %s" % (A(msg)[:50], A(msg2)[:50], form, A(msg3)[:50])


step("capture_viewport_angle/contact_sheet headless graceful", s20_angle, readonly=True, tag="LIVE")


def s20_quit():
    captured = []
    real = bpy.app.timers.register
    prefs = bpy.context.preferences
    saved = (prefs.view.use_save_prompt, prefs.use_preferences_save)
    bpy.app.timers.register = lambda fn, **kw: captured.append((fn, kw))
    try:
        r = ok(call("quit_blender", save_prompt=False, save=False))
        assert r["quitting"] is True and r["saved"] is False
        assert len(captured) == 1 and prefs.view.use_save_prompt is False and prefs.use_preferences_save is False
    finally:
        bpy.app.timers.register = real
        prefs.view.use_save_prompt, prefs.use_preferences_save = saved
    return "quit deferred via timer (intercepted), prefs toggled and restored"


step("quit_blender defers via timer", s20_quit, readonly=True)

# =========================================================================
# S21 final invariants
# =========================================================================
def s21_final():
    assert bpy.context.mode == "OBJECT"
    assert len(bpy.data.scenes) == 1, [s.name for s in bpy.data.scenes]
    leaks = [g.name for g in bpy.data.node_groups if "mcp" in g.name.lower()]
    assert not leaks, leaks
    return "1 scene, OBJECT mode, no mcp node groups"


step("final invariants", s21_final, readonly=True)

# ---------------------------------------------------------------- Phase B0 (settings, save and load) steps
# Opt-in with `-- --b0` so the Phase A acceptance run stays comparable across addon hashes.
if "--b0" in _argv:
    _b0_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b0_steps.py")
    P("HARNESS b0 steps from %s" % _b0_path)
    exec(compile(open(_b0_path, encoding="utf-8").read(), _b0_path, "exec"))

# Phase B1 (rigging and animation) steps, opt-in with `-- --b1` (usually together with --b0)
if "--b1" in _argv:
    _b1_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b1_steps.py")
    P("HARNESS b1 steps from %s" % _b1_path)
    exec(compile(open(_b1_path, encoding="utf-8").read(), _b1_path, "exec"))

B0_TABLE = set("""get_file_state new_file revert_file recover_file save_copy save_version list_versions append_from_blend
link_from_blend set_autosave make_paths_relative make_paths_absolute find_missing_files pack_all unpack_all describe_settings
get_settings set_settings settings_snapshot settings_restore list_settings_snapshots delete_settings_snapshot set_output_settings
set_color_management set_render_quality set_render_device list_render_devices set_simplify set_frame_range set_scene_units
set_viewport_defaults list_blender_presets apply_blender_preset set_project_profile get_project_profile list_addons enable_addon
disable_addon get_addon_preferences set_addon_preferences save_preferences list_workspaces set_workspace get_addon_settings
set_addon_settings save_session_state restore_session_state get_session_state apply_session_state get_version
ensure_server_running""".split())
B1_TABLE = set("""create_armature add_bones get_armature_info set_bone_properties delete_bones bind_armature get_vertex_groups
get_vertex_weights set_vertex_weights render_weight_map find_unweighted_vertices set_pose get_pose reset_pose add_constraint
get_constraints remove_constraint set_keyframes get_animation_info playblast bake_action mirror_bones""".split())


def s21_coverage():
    # every handler of an enabled phase must have gone through execute_command; handlers of a phase whose
    # flag is off are reported, not failed (their own flag covers them)
    expected = set(PHASE_A_TABLE)
    if "--b0" in _argv:
        expected |= B0_TABLE
    if "--b1" in _argv:
        expected |= B1_TABLE
    missing = sorted((set(HANDLERS) - CALLED) & expected)
    unflagged = sorted(set(HANDLERS) - CALLED - expected)
    assert not missing, "handlers never exercised: %s" % missing
    return "%d of %d handlers exercised through execute_command%s" % (
        len(CALLED & set(HANDLERS)), len(HANDLERS), ("; not covered by an enabled phase flag: %s" % unflagged) if unflagged else "")


step("M2 every addon handler exercised", s21_coverage, readonly=True, tag="M2")

# ---------------------------------------------------------------- server-only tools (no addon handler)
SKIPS = [
    ("start_blender", "process management; nobody launches Blender from the harness"),
    ("close_blender", "process management; nobody launches Blender from the harness"),
    ("get_blender_status", "process management; unit-tested in tests/test_server_units.py with the port probe mocked"),
    ("compare_reference_image", "PIL-only server tool; unit-tested in tests/test_server_units.py with generated PNGs"),
    ("diff_images", "PIL-only server tool; unit-tested in tests/test_server_units.py with generated PNGs"),
    ("load_img_to_3d_model", "TripoSR subprocess; out of scope for the migration"),
    ("unload_img_to_3d_model", "TripoSR subprocess; out of scope for the migration"),
    ("generate_3d_from_image", "TripoSR subprocess; out of scope for the migration"),
]
for name, why in SKIPS:
    P("SKIP %s :: %s" % (name, why))
for name in ("get_viewport_screenshot", "capture_viewport_angle", "capture_contact_sheet"):
    P("LIVE %s :: LIVE PASS REQUIRED, user runs in a 5.2.1 GUI session; headless proves graceful error only" % name)

# ---------------------------------------------------------------- summary
fails = [r for r in RESULTS if r["status"] == "FAIL"]
summary = {
    "blender": VER,
    "addon": ADDON_PATH,
    "addon_sha256": ADDON_SHA,
    "addon_git_hash_object": ADDON_GIT,
    "bl_info_version": list(addon.bl_info["version"]),
    "handlers": len(HANDLERS),
    "steps": len(RESULTS),
    "passed": len(RESULTS) - len(fails),
    "failed": len(fails),
    "failed_steps": [{"step": r["step"], "name": r["name"], "tag": r["tag"], "detail": r["detail"]} for r in fails],
    "live_pass_required": ["get_viewport_screenshot", "capture_viewport_angle", "capture_contact_sheet"],
    "network_not_run": sorted(srv._GATED_COMMANDS),
    "workdir": WORK,
}
P("SUMMARY " + json.dumps(summary))
if OUT_PATH:
    with open(OUT_PATH, "w", encoding="ascii", errors="replace") as f:
        json.dump({"summary": summary, "results": RESULTS}, f, indent=1)
    P("HARNESS report written to %s" % OUT_PATH)
