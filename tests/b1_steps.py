# Phase B1 (rigging and animation) headless steps. Executed inside tests/headless_handlers.py when it is run
# with `-- --b1`; uses that file's helpers (step, call, ok, err, make_cube, make_camera, clear_scene, WORK,
# addon, srv, HANDLERS, P, A, read_png, assert_not_blank, snapshot).
# Written from docs/FEATURE-REQUEST-rigging-and-animation.md sections 2, 3.1, 6 (steps 1-10; 11 is live) and
# docs/PLAN.md section 10 deltas K2-K17. Fixtures are built with bpy.data / bpy.ops primitives, never with the
# handler under test. Every reply that names bones must carry NAMES only (K5).
import os
import json
import math

import bpy
import mathutils

B1DIR = os.path.join(WORK, "b1")
os.makedirs(B1DIR, exist_ok=True)
# legacy action.fcurves exists on 4.x only (RNA props live on bl_rna, not as attributes of the type object)
IS_5X = "fcurves" not in bpy.types.Action.bl_rna.properties.keys()


def _cylinder(name="Cyl"):
    """Fixture: a cylinder with rings along its length (a default one has only the two cap rings, so ARMATURE_AUTO
    gives the middle bone zero vertices; Ada m_ff8f4d11). bpy-level, not a handler under test."""
    bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=2.5, location=(0, 0, 1.25), vertices=16)
    cyl = bpy.context.active_object
    cyl.name = name
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=4)
    bpy.ops.object.mode_set(mode="OBJECT")
    return cyl


def _arm(name):
    o = bpy.data.objects.get(name)
    assert o is not None and o.type == "ARMATURE", "armature %s missing" % name
    return o


def _fcurves_of(id_obj):
    """K2 readback for the test's own eyes: legacy action.fcurves (4.x) or the slot channelbag (5.x)."""
    ad = id_obj.animation_data
    if ad is None or ad.action is None:
        return []
    act = ad.action
    if hasattr(act, "fcurves"):
        return list(act.fcurves)
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(act, ad.action_slot) if getattr(ad, "action_slot", None) else None
    return list(bag.fcurves) if bag else []


def _only_names(value, path="reply"):
    """K5: no bpy structs / non-JSON values anywhere in a reply."""
    if isinstance(value, dict):
        for k, v in value.items():
            _only_names(v, "%s.%s" % (path, k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _only_names(v, "%s[%d]" % (path, i))
    else:
        assert value is None or isinstance(value, (str, int, float, bool)), "%s carries a non-JSON value %r" % (path, type(value))


clear_scene()
bpy.context.scene.frame_set(1)

BONES = [
    {"name": "upper", "head": [0, 0, 0], "tail": [0, 0, 1]},
    {"name": "fore", "head": [0, 0, 1], "tail": [0, 0, 2], "parent": "upper", "connected": True},
    {"name": "hand", "head": [0, 0, 2], "tail": [0, 0, 2.5], "parent": "fore", "connected": True},
]


# ---- doc step 1: create_armature with a 3-bone chain
def r1():
    r = ok(call("create_armature", name="Arm", bones=BONES), must=["name", "bone_count"])
    a = _arm(r["name"])
    assert r["bone_count"] == 3 and len(a.data.bones) == 3, (r, len(a.data.bones))
    assert a.data.bones["fore"].parent.name == "upper" and a.data.bones["hand"].use_connect, "parent/connected not applied"
    assert bpy.context.mode == "OBJECT"
    _only_names(r)
    return "Arm with upper/fore/hand, fore+hand connected, reply names only"


step("B1-1 create_armature 3-bone chain", r1, tag="B1")


def r1b():
    # collision entry (a second "hand") and an unambiguous connected child of "upper" (no batch-name ambiguity)
    r = ok(call("add_bones", armature="Arm", bones=[{"name": "hand", "head": [1, 0, 0], "tail": [1, 0, 1]},
                                                    {"name": "thumb", "head": [5, 5, 5], "tail": [0, 0, 1.5], "parent": "upper", "connected": True}]))
    created = r.get("created") or r.get("names")
    assert created and any(n.startswith("hand.") for n in created), "name collision must yield Blender's .001 suffix, reported: %s" % A(r)
    assert "thumb" in created and r.get("snapped") and "thumb" in r["snapped"], "connected=True must snap the head and report it: %s" % A(r)
    a = _arm("Arm")
    assert (a.data.bones["thumb"].head_local - a.data.bones["upper"].tail_local).length < 1e-5, "thumb head not snapped to upper tail: %s" % tuple(a.data.bones["thumb"].head_local)
    err(call("add_bones", armature="Arm", bones=[{"name": "x", "head": [0, 0, 0], "tail": [0, 0, 1], "parent": "nope"}]), "parent")
    r2 = ok(call("delete_bones", armature="Arm", bones="thumb,hand.001", reparent_children=True))
    assert set(r2.get("deleted", [])) == {"thumb", "hand.001"} and len(a.data.bones) == 3, (r2, len(a.data.bones))
    return "collision -> hand.001 reported, connected snap reported, bad parent rejected, delete_bones back to 3"


step("B1-1b add_bones collision/snap + delete_bones", r1b, tag="B1")


# ---- doc step 2: get_armature_info
def r2():
    r = ok(call("get_armature_info", armature="Arm"), must=["bones", "pose_position", "display_type"])
    names = [b["name"] for b in r["bones"]]
    assert names == ["upper", "fore", "hand"], "hierarchy order expected, got %s" % names
    by = {b["name"]: b for b in r["bones"]}
    assert by["fore"]["parent"] == "upper" and by["hand"]["parent"] == "fore" and by["upper"]["parent"] is None
    assert by["fore"]["connected"] is True and by["hand"]["connected"] is True and by["upper"]["connected"] is False
    assert abs(by["upper"]["length"] - 1.0) < 1e-5 and by["upper"]["children"] == ["fore"]
    for k in ("head", "tail", "roll", "deform", "bone_collections", "constraints"):
        assert k in by["upper"], "bone entry lacks %s" % k
    _only_names(r)
    rp = ok(call("get_armature_info", armature="Arm", include_pose=True, bone_filter="h*"))
    assert [b["name"] for b in rp["bones"]] == ["hand"] and "pose" in rp["bones"][0] and "matrix_world_head" in rp["bones"][0]["pose"]
    err(call("get_armature_info", armature="Nope"), "not found")
    return "3 bones in order, parents/connected/length/children, pose block on filter, names only"


step("B1-2 get_armature_info", r2, readonly=True, tag="B1")


def r2b():
    a = _arm("Arm")
    r = ok(call("set_bone_properties", armature="Arm", bone="hand", roll=30.0, new_name="hand_r"), must=["set"])
    try:
        assert "roll" in r["set"] and "new_name" in r["set"] and not r.get("unset"), r      # unset present only when non-empty (add_modifier shape)
        assert "hand_r" in a.data.bones and "hand" not in a.data.bones
        # Bone.roll is not an attribute of bpy.types.Bone: read the roll back through get_armature_info (Ada m_ff8f4d11)
        info = ok(call("get_armature_info", armature="Arm", bone_filter="hand_r"))
        roll_deg = info["bones"][0]["roll"]
        assert abs(roll_deg - 30.0) < 0.5, "roll set to 30 deg reads back %s" % roll_deg
        # a value Blender refuses lands in unset with the reason (a bone cannot be its own parent)
        res_bad = call("set_bone_properties", armature="Arm", bone="hand_r", parent="hand_r")
        rb = res_bad.get("result") if isinstance(res_bad.get("result"), dict) else {}
        assert "parent" in (rb.get("unset") or {}) or "error" in rb, "self-parenting must be refused under unset or error: %s" % A(res_bad)
        assert a.data.bones["hand_r"].parent.name == "fore", "self-parent attempt changed the parent"
    finally:
        if "hand_r" in a.data.bones:
            ok(call("set_bone_properties", armature="Arm", bone="hand_r", new_name="hand"))
    assert "hand" in a.data.bones
    return "roll 30 deg set and read back via get_armature_info (%.2f), rename, self-parent refused, renamed back" % roll_deg


step("B1-2b set_bone_properties", r2b, tag="B1")


# ---- doc step 3: cylinder (fixture) + bind_armature AUTO + get_vertex_groups
def r3():
    cyl = _cylinder("Cyl")
    anchor = make_cube("B1Anchor", 0.5, (5, 5, 5))
    bpy.context.view_layer.objects.active = anchor
    r = ok(call("bind_armature", mesh="Cyl", armature="Arm", method="AUTO"), must=["modifier", "group_count"])
    assert any(m.type == "ARMATURE" and m.object == _arm("Arm") for m in cyl.modifiers), "no ARMATURE modifier"
    assert cyl.parent == _arm("Arm") and r["group_count"] == 3, r
    assert bpy.context.view_layer.objects.active == anchor, "bind_armature must restore the previous active object (K15)"
    g = ok(call("get_vertex_groups", mesh="Cyl"), must=["groups"])
    names = {x["name"]: x for x in g["groups"]}
    assert set(names) == {"upper", "fore", "hand"}, names.keys()
    assert all(names[n]["vertex_count"] > 0 and names[n]["has_bone"] for n in names), names
    for k in ("index", "lock", "weight_min", "weight_max", "weight_mean"):
        assert k in names["upper"], k
    err(call("bind_armature", mesh="Cyl", armature="Arm", method="BOGUS"))
    return "ARMATURE modifier + parent, 3 groups with verts and has_bone, active restored"


step("B1-3 bind_armature AUTO + get_vertex_groups", r3, tag="B1")


# ---- doc step 4: find_unweighted_vertices
def r4():
    r = ok(call("find_unweighted_vertices", mesh="Cyl"), must=["unweighted_count"])
    assert r["unweighted_count"] == 0, r
    # discriminating: remove one vertex from every group, expect exactly that one
    cyl = bpy.data.objects["Cyl"]
    for vg in cyl.vertex_groups:
        vg.remove([0])
    r2 = ok(call("find_unweighted_vertices", mesh="Cyl"))
    assert r2["unweighted_count"] == 1 and 0 in (r2.get("unweighted") or r2.get("indices") or []), r2
    cyl.vertex_groups["upper"].add([0], 1.0, "REPLACE")
    return "0 unweighted after AUTO; vertex 0 stripped -> reported as the one unweighted"


step("B1-4 find_unweighted_vertices", r4, tag="B1")


# ---- A1.3 (ruling R-A1.3): find_unweighted_vertices(render=True) must leave the mesh element
# selection (vertex, edge AND polygon flags) exactly as it found it, also on the headless path
# where the capture itself fails. Discriminating: FAILS on addon f4dc37b1 (verts rewritten to the
# unweighted set, edges/polygons cleared).
def _mesh_select_flags(name):
    me = bpy.data.objects[name].data
    out = []
    for coll in (me.vertices, me.edges, me.polygons):
        flags = [False] * len(coll)
        coll.foreach_get("select", flags)
        out.append(flags)
    return out


def r4b():
    me = bpy.data.objects["Cyl"].data
    assert len(me.vertices) > 8 and len(me.edges) > 8 and len(me.polygons) > 4, "fixture too small"
    want = [[False] * len(me.vertices), [False] * len(me.edges), [False] * len(me.polygons)]
    for i in range(0, len(want[0]), 3):
        want[0][i] = True
    for i in range(1, len(want[1]), 4):
        want[1][i] = True
    for i in range(0, len(want[2]), 2):
        want[2][i] = True
    me.vertices.foreach_set("select", want[0])
    me.edges.foreach_set("select", want[1])
    me.polygons.foreach_set("select", want[2])
    before = _mesh_select_flags("Cyl")
    assert before == want, "fixture: mixed selection did not take"
    res = call("find_unweighted_vertices", mesh="Cyl", render=True)
    # headless: the capture may fail (error envelope, or success carrying an error); the restore contract holds either way
    assert res.get("status") in ("success", "error"), A(res)
    after = _mesh_select_flags("Cyl")
    diffs = []
    for label, b, a in zip(("vertices", "edges", "polygons"), before, after):
        changed = [i for i, (x, y) in enumerate(zip(b, a)) if x != y]
        if changed:
            diffs.append("%s: %d of %d flags changed (first %s)" % (label, len(changed), len(b), changed[:5]))
    assert not diffs, "select flags not restored after find_unweighted_vertices(render=True): " + "; ".join(diffs)
    return "vert/edge/face select flags identical before/after on the headless capture path (reply status %s)" % res["status"]


step("B1-4b find_unweighted render=True restores select flags (A1.3)", r4b, tag="B1")


# ---- doc step 5: set_vertex_weights REPLACE + get_vertex_weights
def r5():
    idx = [1, 2, 3, 4, 5]
    r = ok(call("set_vertex_weights", mesh="Cyl", group="upper", weights={str(i): 0.42 for i in idx}, mode="REPLACE"), must=["written"])
    assert r["written"] == 5, r
    g = ok(call("get_vertex_weights", mesh="Cyl", indices=idx), must=["weights"])
    w = {int(k): v for k, v in g["weights"].items()}      # int keys in-process, str keys after the JSON wire
    assert set(w) == set(idx), "weights returned for %s, wanted %s" % (sorted(w), idx)
    for i in idx:
        assert abs(w[i]["upper"] - 0.42) < 1e-6, w[i]
    err(call("set_vertex_weights", mesh="Cyl", group="upper", weights={"9999": 1.0}), "out of range")
    r2 = ok(call("set_vertex_weights", mesh="Cyl", group="NewGroup", weights=[[1, 0.1]], create_group=True))
    assert r2.get("group_created") is True and "NewGroup" in bpy.data.objects["Cyl"].vertex_groups
    bpy.data.objects["Cyl"].vertex_groups.remove(bpy.data.objects["Cyl"].vertex_groups["NewGroup"])
    return "5 weights written and read back at 0.42; index 9999 rejected; create_group"


step("B1-5 set/get_vertex_weights", r5, tag="B1")


# ---- doc step 6: IK constraint on hand
def r6():
    tgt = bpy.data.objects.new("IKTarget", None)
    bpy.context.scene.collection.objects.link(tgt)
    tgt.location = (0.5, 0, 2.5)
    r = ok(call("add_constraint", owner="Arm", bone="hand", constraint_type="IK", params={"target": "IKTarget", "chain_count": 2, "nonsense": 1}), must=["set", "unset"])
    assert "target" in r["set"] and "chain_count" in r["set"] and "nonsense" in r["unset"], r
    c = ok(call("get_constraints", owner="Arm", bone="hand"), must=["constraints"])
    ik = [x for x in c["constraints"] if x["type"] == "IK"][0]
    assert ik["target"] == "IKTarget" and ik["chain_count"] == 2 and "influence" in ik and "mute" in ik, ik
    _only_names(c)
    msg = err(call("add_constraint", owner="Arm", bone="hand", constraint_type="WARP_DRIVE"))
    assert "IK" in msg and "COPY_ROTATION" in msg, "error must list valid types: %s" % A(msg)
    ok(call("add_constraint", owner="IKTarget", constraint_type="COPY_LOCATION", name="CL", params={"target": "Cyl"}))
    assert bpy.data.objects["IKTarget"].constraints["CL"].target == bpy.data.objects["Cyl"]
    ok(call("remove_constraint", owner="IKTarget", name="CL"))
    assert not bpy.data.objects["IKTarget"].constraints
    return "IK on hand chain_count 2 read back; bad type lists types; object-level constraint add/remove"


step("B1-6 add/get/remove_constraint IK", r6, tag="B1")


# ---- doc step 7: set_pose / get_pose / reset_pose
def r7():
    r = ok(call("set_pose", armature="Arm", bones={"upper": {"rotation": [45, 0, 0]}}, rotation_mode="XYZ"))
    a = _arm("Arm")
    pb = a.pose.bones["upper"]
    assert pb.rotation_mode == "XYZ" and abs(math.degrees(pb.rotation_euler.x) - 45) < 1e-3, (pb.rotation_mode, pb.rotation_euler)
    changed = r.get("rotation_mode_changed") or {}
    assert changed.get("upper", {}).get("to") == "XYZ" and "upper" in r.get("written", []), "quaternion->XYZ mode change must be reported: %s" % A(r)
    g = ok(call("get_pose", armature="Arm", bones="upper"))
    up = g["bones"]["upper"] if isinstance(g.get("bones"), dict) else [b for b in g["bones"] if b["name"] == "upper"][0]
    assert abs(up["rotation"][0] - 45) < 1e-3 and "head_world" in up and "tail_world" in up, up
    bpy.context.view_layer.update()
    want_head = a.matrix_world @ pb.head
    assert all(abs(x - y) < 1e-4 for x, y in zip(up["head_world"], want_head)), (up["head_world"], tuple(want_head))
    ok(call("reset_pose", armature="Arm"))
    assert abs(pb.rotation_euler.x) < 1e-6 and bpy.context.mode == "OBJECT"
    rq = ok(call("set_pose", armature="Arm", bones={"fore": {"rotation": [1, 0, 0, 0]}}, rotation_mode="QUATERNION"))
    assert a.pose.bones["fore"].rotation_mode == "QUATERNION"
    return "XYZ 45deg set (mode change reported), get_pose world head matches matrix_world @ head (K20), reset clears"


step("B1-7 set/get/reset_pose", r7, tag="B1")


# ---- doc step 8: set_keyframes on the target Empty + get_animation_info (K2 / K3 / K17)
def r8():
    keys = [[1, [0, 0, 2.5]], [10, [1, 0, 2.5]], {"frame": 20, "value": [0, 1, 2.5], "interpolation": "LINEAR"}]
    r = ok(call("set_keyframes", target="IKTarget", data_path="location", keys=keys), must=["keyed", "action"])
    assert r.get("keyed_count", len(r["keyed"]) if isinstance(r["keyed"], list) else r["keyed"]) == 3 and r["keyed"] in (3, [1, 10, 20]), r
    tgt = bpy.data.objects["IKTarget"]
    fcs = _fcurves_of(tgt)
    assert len(fcs) == 3 and all(len(fc.keyframe_points) == 3 for fc in fcs), [(fc.data_path, len(fc.keyframe_points)) for fc in fcs]
    if hasattr(tgt.animation_data, "action_slot"):
        assert tgt.animation_data.action_slot is not None, "5.x: action_slot must be set after keying (K17)"
    info = ok(call("get_animation_info", target="IKTarget", include_keys=True), must=["action", "fcurves"])
    paths = sorted((f["data_path"], f["index"], f["keyframe_count"]) for f in info["fcurves"])
    assert paths == [("location", 0, 3), ("location", 1, 3), ("location", 2, 3)], paths
    kf = info["fcurves"][0]["keys"]
    assert kf[0][0] == 1 and kf[2][2] == "LINEAR", kf
    bpy.context.scene.frame_set(10)
    assert abs(tgt.location.x - 1.0) < 1e-5
    bpy.context.scene.frame_set(1)
    err(call("set_keyframes", target="IKTarget", data_path="location", keys=[[1, 5]]), "expects")
    return "3 keys x 3 fcurves (K2 readback identical on this build: %s), LINEAR on key 3, slot set on 5.x, arity rejected" % ("channelbag" if IS_5X else "legacy fcurves")


step("B1-8 set_keyframes + get_animation_info (K2/K17)", r8, tag="B1")


def r8b():
    s = ok(call("get_animation_info"), must=["fps", "frame_start", "frame_end", "actions"])
    assert any(a.get("users", 1) >= 1 for a in s["actions"]) and s["fps"] == bpy.context.scene.render.fps
    r = ok(call("add_keyframe", name="Arm", bone="upper", data_path="rotation_euler", frame=5, value=[10, 0, 0]))
    a = _arm("Arm")
    assert a.pose.bones["upper"].rotation_mode == "XYZ" and r.get("rotation_mode_changed") in (True, "XYZ", None), r
    assert any("upper" in fc.data_path and "rotation_euler" in fc.data_path for fc in _fcurves_of(a)), "bone key not on the armature action"
    r2 = ok(call("set_frame_range", start=1, end=30))
    assert (bpy.context.scene.frame_start, bpy.context.scene.frame_end) == (1, 30)
    return "scene summary; add_keyframe(bone=) keys the pose bone in XYZ; set_frame_range"


step("B1-8b get_animation_info scene + add_keyframe bone + frame range", r8b, tag="B1")


# ---- doc step 9: bake_action (K4 / K12)
def r9():
    before_active = bpy.context.view_layer.objects.active
    r = ok(call("bake_action", armature="Arm", start=1, end=10, step=1, visual_keying=True), must=["action", "fcurve_count"])
    a = _arm("Arm")
    assert bpy.context.mode == "OBJECT" and bpy.context.view_layer.objects.active == before_active
    fcs = _fcurves_of(a)
    for b in ("upper", "fore", "hand"):
        assert any('pose.bones["%s"]' % b in fc.data_path for fc in fcs), "no baked fcurves for %s: %s" % (b, sorted({fc.data_path for fc in fcs})[:8])
    assert r["fcurve_count"] == len(fcs) and r["fcurve_count"] > 0, (r["fcurve_count"], len(fcs))
    info = ok(call("get_animation_info", target="Arm"))
    assert len(info["fcurves"]) == r["fcurve_count"]
    return "baked 1-10: %d fcurves on all 3 bones (%s), mode/active restored" % (r["fcurve_count"], "channelbag" if IS_5X else "legacy")


step("B1-9 bake_action (K12)", r9, tag="B1")


# ---- doc step 10 + K7 + K8: export FBX with hierarchy, re-import; glTF Icosphere
def r10():
    fbx = os.path.join(B1DIR, "rig.fbx")
    r = ok(call("export_object", name="Cyl", filepath=fbx, file_format="fbx", include_hierarchy=True, add_leaf_bones=False))
    assert os.path.getsize(fbx) > 1000
    if IS_5X:
        ok(call("export_object", name="Cyl", filepath=os.path.join(B1DIR, "rig_sg.fbx"), file_format="fbx", include_hierarchy=True, mesh_smooth_type="SMOOTH_GROUP"))
        k7 = "SMOOTH_GROUP accepted (5.x)"
    else:
        msg = err(call("export_object", name="Cyl", filepath=os.path.join(B1DIR, "rig_sg.fbx"), file_format="fbx", mesh_smooth_type="SMOOTH_GROUP"))
        assert "FACE" in msg and "EDGE" in msg, "must list the live enum: %s" % A(msg)
        k7 = "SMOOTH_GROUP rejected with the live enum (4.x)"
    glb = os.path.join(B1DIR, "rig.glb")
    ok(call("export_object", name="Cyl", filepath=glb, file_format="glb", include_hierarchy=True, export_animations=True))
    bpy.ops.wm.read_homefile(use_empty=True)
    ri = ok(call("import_file", filepath=fbx), must=["imported_objects", "armatures", "actions", "new_actions"])
    assert ri["armatures"] and any(bpy.data.objects[n].type == "ARMATURE" for n in ri["armatures"]), ri
    assert ri["new_actions"], "no action came back from the FBX"
    bpy.ops.wm.read_homefile(use_empty=True)
    rg = ok(call("import_file", filepath=glb), must=["imported_objects", "armatures", "actions", "new_actions", "bone_shape_objects"])
    assert rg["armatures"] and rg["new_actions"], rg
    # K8: invariant on every hash = no bone-shape mesh is counted as user geometry. Two landed forms are accepted
    # (Ada m_8888a2c2): L1 = the importer's Icosphere exists and is listed under bone_shape_objects; L2+ = import_file
    # passes disable_bone_shape=True so no Icosphere exists and bone_shape_objects == [] (key present).
    assert not any("Icosphere" in n for n in rg["imported_objects"]), "K8: Icosphere must not count as user geometry: %s" % A(rg)
    ico_present = [o.name for o in bpy.data.objects if "Icosphere" in o.name]
    if ico_present:
        assert any("Icosphere" in n for n in rg["bone_shape_objects"]), "K8 (L1 form): Icosphere exists but is not listed under bone_shape_objects: %s" % A(rg)
        k8 = "L1 form: Icosphere reported under bone_shape_objects, excluded from user geometry"
    else:
        assert rg["bone_shape_objects"] == [], "no Icosphere exists yet bone_shape_objects is not empty: %s" % A(rg["bone_shape_objects"])
        k8 = "L2 form: disable_bone_shape suppressed the Icosphere, bone_shape_objects=[]"
    return "FBX round trip brought an ARMATURE + action; %s; %s" % (k7, k8)


step("B1-10 export/import rigged FBX + glTF (K7/K8)", r10, tag="B1")


# ---- rebuild the rig for the remaining steps (the re-import wiped the scene)
def _rebuild():
    clear_scene()
    ok(call("create_armature", name="Arm", bones=BONES))
    _cylinder("Cyl")
    ok(call("bind_armature", mesh="Cyl", armature="Arm", method="AUTO"))
    make_camera("B1Cam", (4, -6, 2), (0, 0, 1.2))
    bpy.context.scene.camera = bpy.data.objects["B1Cam"]


_rebuild()


# ---- K9: playblast headless = camera fallback
def r11():
    sc = bpy.context.scene
    sc.frame_set(3)
    n_sc, n_ng = len(bpy.data.scenes), len(bpy.data.node_groups)
    ok(call("set_render_settings", engine="BLENDER_WORKBENCH", width=48, height=32))
    r = ok(call("playblast", start=1, end=4, step=1, max_size=48), must=["images"])
    images = r["images"]
    assert [im["frame"] for im in images] == [1, 2, 3, 4], images
    dets = [assert_not_blank(im["filepath"]) for im in images]
    method = r.get("method") or r.get("path") or r.get("mode") or "camera"
    assert method != "opengl", "headless must take the camera fallback (K9): %s" % A(r)
    assert sc.frame_current == 3, "frame_current not restored"
    assert len(bpy.data.scenes) == n_sc and len(bpy.data.node_groups) == n_ng
    err(call("playblast", start=10, end=1))
    return "4 frames via camera fallback, all non-blank (%s), frame restored, no leaks" % dets[0]


step("B1-11 playblast headless camera fallback (K9)", r11, tag="B1")


# ---- section 2 fixes
def r12():
    a = ok(call("get_object_info", name="Arm"), must=["bones", "pose_position", "action", "bone_collections"])
    m = ok(call("get_object_info", name="Cyl"), must=["vertex_groups", "shape_keys", "armature", "parent", "parent_type", "mesh", "world_bounding_box"])
    assert a["bones"] == 3 and m["armature"] == "Arm" and set(m["vertex_groups"]) == {"upper", "fore", "hand"}, (a, m)
    child = make_cube("BoneChild", 0.2, (0, 0, 2.6))
    r = ok(call("parent_object", child_name="BoneChild", parent_name="Arm", parent_type="BONE", bone="hand"))
    assert child.parent_type == "BONE" and child.parent_bone == "hand", (child.parent_type, child.parent_bone)
    err(call("parent_object", child_name="BoneChild", parent_name="Arm", parent_type="BONE", bone="nope"))
    arm = _arm("Arm")
    arm.show_in_front = False           # known baseline; the overlay must flip it only for the capture and restore it
    r2 = call("capture_viewport_angle", angle="front", overlay="bones_in_front", filepath=os.path.join(B1DIR, "ov.png"))
    err(r2)
    assert arm.show_in_front is False, "overlay state must be restored after the headless failure"
    msg = err(call("capture_viewport_angle", angle="front", overlay="x_ray_vision", filepath=os.path.join(B1DIR, "ov2.png")))
    assert "bones_in_front" in msg, "bad overlay must list the valid overlays: %s" % A(msg)
    return "get_object_info rig keys on ARMATURE/MESH; parent_object BONE; overlay param restores state on failure"


step("B1-12 section-2 fixes: get_object_info, parent_object BONE, overlay restore", r12, tag="B1")


def r12b():
    cyl = bpy.data.objects["Cyl"]
    mode_before = bpy.context.mode
    active_before = bpy.context.view_layer.objects.active
    msg = err(call("render_weight_map", mesh="Cyl", group="upper"))
    assert "GUI" in msg or "headless" in msg.lower() or "background" in msg.lower(), "headless must fail gracefully naming the GUI need: %s" % A(msg)
    assert bpy.context.mode == mode_before and bpy.context.view_layer.objects.active == active_before, "mode/active must be restored after the headless failure"
    assert cyl.mode == "OBJECT", "mesh left in %s after the failed capture" % cyl.mode
    err(call("render_weight_map", mesh="Cyl", group="no_such_group"))
    return "graceful headless error (%s), mode/active restored, unknown group rejected (LIVE PASS REQUIRED for the image)" % A(msg)[:50]


step("B1-12b render_weight_map headless graceful", r12b, tag="LIVE")


# ---- K6 mirror (Tier 2 L4) and K10 BVH
def r13():
    if "mirror_bones" not in srv._build_handlers():
        # Tier 2 (L4) is ruled OUT of this run (Ada 13:56:09); the step stays and runs in full once L4 lands
        return "mirror_bones absent: Tier 2 L4 not in this run per ruling 13:56 (K6 symmetrize unverified through a tool)"
    ok(call("add_bones", armature="Arm", bones=[{"name": "finger.L", "head": [0.2, 0, 2.5], "tail": [0.4, 0, 2.5], "parent": "hand"}]))
    r = ok(call("mirror_bones", armature="Arm", bones="finger.L"))
    a = _arm("Arm")
    assert "finger.R" in a.data.bones and "finger.R" in (r.get("created") or []), (r, [b.name for b in a.data.bones])
    return "finger.L mirrored to finger.R on this build (K6)"


step("B1-13 mirror_bones symmetrize (K6, L4)", r13, tag="B1")


def r14():
    bvh = os.path.join(B1DIR, "arm.bvh")
    a = _arm("Arm")
    bpy.context.view_layer.objects.active = a
    a.select_set(True)
    bpy.ops.export_anim.bvh(filepath=bvh, frame_start=1, frame_end=5)      # fixture, not the tool under test
    assert os.path.exists(bvh)
    r = ok(call("import_file", filepath=bvh), must=["armatures"])
    assert r["armatures"], r
    import addon_utils
    addon_utils.disable("io_anim_bvh", default_set=True)
    try:
        msg = err(call("import_file", filepath=bvh))
        assert "io_anim_bvh" in msg or "BVH" in msg, "teach-style error must name the add-on: %s" % A(msg)
    finally:
        addon_utils.enable("io_anim_bvh", default_set=True)
    return "BVH imported as an armature; with io_anim_bvh disabled the error names the add-on (K10)"


step("B1-14 import_file .bvh (K10)", r14, tag="B1")


# ---- M8 sweep over the new mutating handlers
def r15():
    anchor = bpy.data.objects.get("B1Anchor") or make_cube("B1Anchor", 0.5, (5, 5, 5))
    tgt = bpy.data.objects.get("IKTarget") or bpy.data.objects.new("IKTarget", None)
    if tgt.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(tgt)
    payloads = [
        ("create_armature", dict(name="M8Arm", bones=BONES)),
        ("add_bones", dict(armature="M8Arm", bones=[{"name": "extra", "head": [1, 0, 0], "tail": [1, 0, 1]}])),
        ("set_bone_properties", dict(armature="M8Arm", bone="extra", roll=10.0)),
        ("delete_bones", dict(armature="M8Arm", bones="extra")),
        ("set_vertex_weights", dict(mesh="Cyl", group="upper", weights={"1": 0.5})),
        ("set_pose", dict(armature="Arm", bones={"upper": {"rotation": [5, 0, 0]}})),
        ("reset_pose", dict(armature="Arm")),
        ("add_constraint", dict(owner="Arm", bone="hand", constraint_type="COPY_LOCATION", name="M8C", params={"target": "IKTarget"})),
        ("remove_constraint", dict(owner="Arm", bone="hand", name="M8C")),
        ("set_keyframes", dict(target="IKTarget", data_path="location", keys=[[1, [0, 0, 0]]])),
        ("bake_action", dict(armature="Arm", start=1, end=2)),
        ("set_frame_range", dict(start=1, end=10)),
    ]
    if "mirror_bones" in srv._build_handlers():          # Tier 2 (L4); swept once it lands
        payloads.append(("mirror_bones", dict(armature="Arm", bones="finger.L")))
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
        # create_armature: F2 ruled result-active for add_primitive/import/join; Ton's create_armature restores the
        # caller's active object instead. Both are accepted here and the form is reported (Lead to rule if it matters).
        if cmd == "create_armature":
            expect = "%s or B1Anchor" % r.get("name")
            good = after_name in (r.get("name"), "B1Anchor") and mode == "OBJECT"
        else:
            expect = "B1Anchor"
            good = after_name == expect and mode == "OBJECT"
        if not good:
            changed.append("%s active->%s (expected %s) mode=%s" % (cmd, after_name, expect, mode))
        P("M8B1 %s %s active=%s mode=%s" % (cmd, "ok" if good else "CHANGED", after_name, mode))
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    assert not failed, "handlers errored during sweep: %s" % failed
    assert not changed, "%d handlers changed active/mode: %s" % (len(changed), changed)
    return "%d rigging handlers keep the active object (creator = result) and OBJECT mode" % len(payloads)


step("B1-15 M8 sweep over rigging handlers", r15, tag="B1")


def r16():
    wanted = ["create_armature", "add_bones", "get_armature_info", "set_bone_properties", "delete_bones", "bind_armature",
              "get_vertex_groups", "get_vertex_weights", "set_vertex_weights", "render_weight_map", "find_unweighted_vertices",
              "set_pose", "get_pose", "reset_pose", "add_constraint", "get_constraints", "remove_constraint", "set_keyframes",
              "get_animation_info", "set_frame_range", "playblast", "bake_action"]
    table = srv._build_handlers()
    missing = [w for w in wanted if w not in table]
    assert not missing, "B1 Tier 1 handlers missing from the dispatch table: %s" % missing
    return "%d Tier 1 rigging handlers present" % len(wanted)


step("B1-16 every Tier 1 rigging command is dispatchable", r16, readonly=True, tag="B1")
P("LIVE render_weight_map :: LIVE PASS REQUIRED (doc step 11): user runs in a 5.2.1 GUI session, image not single-colour")
P("LIVE playblast (opengl path + video_path) :: LIVE PASS REQUIRED (doc step 11 / L11)")
P("LIVE capture_viewport_angle overlay=bones_in_front :: LIVE PASS REQUIRED (L12)")
