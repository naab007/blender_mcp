# B2 follow-up facts (PLAN.md 11.5, Tier 2 engine readiness) with REAL calls on both installs.
# Prints one line: B2CHECK2 {json}. ASCII only. Never imports addon.py. Does not touch b2check.py.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\b2check2.py 2>&1 | findstr B2CHECK2
# Facts: object.quadriflow_remesh and object.voxel_remesh called headless (counts before/after, wall time);
#        mesh.separate(type='LOOSE') result naming; object.make_single_user kwargs + effect vs obj.data.copy().
import bpy
import json
import sys
import time

out = {"version": bpy.app.version_string, "python": sys.version.split()[0], "background": bpy.app.background}


def tryf(key, fn):
    try:
        out[key] = fn()
    except Exception as e:
        out[key] = "Error: " + str(e).strip()[:200]


def err(e):
    return "Error: " + str(e).strip()[:140]


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


def select_only(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(o == obj)
    bpy.context.view_layer.objects.active = obj


def tri_count(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    me = obj.evaluated_get(dg).data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def new_mesh_obj(name, op, **kw):
    op(**kw)
    o = bpy.context.active_object
    o.name = name
    return o


def counts(obj):
    return {"verts": len(obj.data.vertices), "faces": len(obj.data.polygons), "tris": tri_count(obj),
            "quads": sum(1 for p in obj.data.polygons if len(p.vertices) == 4)}


# --- 1. quadriflow remesh, real call ---------------------------------------------------------
def quadriflow():
    res = {"kwargs": op_kwargs(bpy.ops.object.quadriflow_remesh),
           "mode_enum": op_enum(bpy.ops.object.quadriflow_remesh, "mode")}
    sph = new_mesh_obj("b2c2_qf", bpy.ops.mesh.primitive_uv_sphere_add, segments=32, ring_count=16)
    res["before"] = counts(sph)
    select_only(sph)
    t0 = time.perf_counter()
    try:
        r = bpy.ops.object.quadriflow_remesh(mode="FACES", target_faces=500, use_preserve_sharp=True,
                                             use_preserve_boundary=True, use_mesh_symmetry=False, seed=0)
        res["faces_500"] = {"result": sorted(r), "after": counts(sph), "seconds": round(time.perf_counter() - t0, 2),
                            "mode_after": bpy.context.mode}
    except Exception as e:
        res["faces_500"] = err(e)
    # non-manifold input (open cylinder): does it refuse or run?
    cyl = new_mesh_obj("b2c2_qf_open", bpy.ops.mesh.primitive_cylinder_add, vertices=16, end_fill_type="NOTHING")
    select_only(cyl)
    try:
        r = bpy.ops.object.quadriflow_remesh(mode="FACES", target_faces=200)
        res["open_mesh"] = {"result": sorted(r), "after": counts(cyl)}
    except Exception as e:
        res["open_mesh"] = err(e)
    # ngon input (cylinder caps are ngons): refuse or run?
    cap = new_mesh_obj("b2c2_qf_ngon", bpy.ops.mesh.primitive_cylinder_add, vertices=16, end_fill_type="NGON")
    select_only(cap)
    try:
        r = bpy.ops.object.quadriflow_remesh(mode="FACES", target_faces=200)
        res["ngon_mesh"] = {"result": sorted(r), "after": counts(cap)}
    except Exception as e:
        res["ngon_mesh"] = err(e)
    # RATIO mode
    sph2 = new_mesh_obj("b2c2_qf_ratio", bpy.ops.mesh.primitive_uv_sphere_add, segments=32, ring_count=16)
    select_only(sph2)
    try:
        r = bpy.ops.object.quadriflow_remesh(mode="RATIO", target_ratio=0.25)
        res["ratio_025"] = {"result": sorted(r), "after": counts(sph2)}
    except Exception as e:
        res["ratio_025"] = err(e)
    # edit-mode poll: does it refuse outside OBJECT mode?
    select_only(sph2)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        res["poll_in_edit_mode"] = bpy.ops.object.quadriflow_remesh.poll()
    except Exception as e:
        res["poll_in_edit_mode"] = err(e)
    bpy.ops.object.mode_set(mode="OBJECT")
    return res


tryf("quadriflow", quadriflow)


# --- 2. voxel remesh, real call (reads mesh.remesh_voxel_size) ------------------------------
def voxel():
    res = {"kwargs": op_kwargs(bpy.ops.object.voxel_remesh)}
    cube = new_mesh_obj("b2c2_vx", bpy.ops.mesh.primitive_cube_add)
    me = cube.data
    res["mesh_props"] = {a: hasattr(me, a) for a in ("remesh_voxel_size", "remesh_voxel_adaptivity", "use_remesh_fix_poles",
                                                     "use_remesh_preserve_volume", "use_remesh_preserve_attributes",
                                                     "remesh_mode", "use_remesh_smooth_normals")}
    res["before"] = counts(cube)
    me.remesh_voxel_size = 0.25
    me.remesh_voxel_adaptivity = 0.0
    select_only(cube)
    t0 = time.perf_counter()
    try:
        r = bpy.ops.object.voxel_remesh()
        res["voxel_025"] = {"result": sorted(r), "after": counts(cube), "seconds": round(time.perf_counter() - t0, 2),
                            "all_quads": all(len(p.vertices) == 4 for p in me.polygons)}
    except Exception as e:
        res["voxel_025"] = err(e)
    # voxel size 0 must be refused, not hang
    cube2 = new_mesh_obj("b2c2_vx0", bpy.ops.mesh.primitive_cube_add)
    cube2.data.remesh_voxel_size = 0.0
    res["voxel_size_zero_clamped_to"] = cube2.data.remesh_voxel_size
    # preserve volume + fix poles with a real call
    cube3 = new_mesh_obj("b2c2_vx3", bpy.ops.mesh.primitive_cube_add)
    cube3.data.remesh_voxel_size = 0.2
    for a, v in (("use_remesh_fix_poles", True), ("use_remesh_preserve_volume", True)):
        if hasattr(cube3.data, a):
            setattr(cube3.data, a, v)
    select_only(cube3)
    try:
        r = bpy.ops.object.voxel_remesh()
        res["voxel_02_fixpoles_volume"] = {"result": sorted(r), "after": counts(cube3)}
    except Exception as e:
        res["voxel_02_fixpoles_volume"] = err(e)
    # open mesh: voxel remesh closes or refuses?
    plane = new_mesh_obj("b2c2_vx_plane", bpy.ops.mesh.primitive_plane_add)
    plane.data.remesh_voxel_size = 0.2
    select_only(plane)
    try:
        r = bpy.ops.object.voxel_remesh()
        res["voxel_open_plane"] = {"result": sorted(r), "after": counts(plane)}
    except Exception as e:
        res["voxel_open_plane"] = err(e)
    return res


tryf("voxel", voxel)


# --- 3. mesh.separate(type='LOOSE') result naming --------------------------------------------
def separate():
    res = {"kwargs": op_kwargs(bpy.ops.mesh.separate), "type_enum": op_enum(bpy.ops.mesh.separate, "type")}
    a = new_mesh_obj("b2c2_sep", bpy.ops.mesh.primitive_cube_add, location=(0, 0, 0))
    b = new_mesh_obj("b2c2_sep_b", bpy.ops.mesh.primitive_cube_add, location=(4, 0, 0))
    c = new_mesh_obj("b2c2_sep_c", bpy.ops.mesh.primitive_cube_add, location=(8, 0, 0))
    for o in (a, b, c):
        o.select_set(True)
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.join()
    res["joined"] = {"name": a.name, "verts": len(a.data.vertices), "objects_left": [o.name for o in bpy.data.objects if o.name.startswith("b2c2_sep")]}
    a.data.name = "b2c2_sep_mesh"
    select_only(a)
    before = set(o.name for o in bpy.data.objects)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        r = bpy.ops.mesh.separate(type="LOOSE")
        bpy.ops.object.mode_set(mode="OBJECT")
        new = sorted(o.name for o in bpy.data.objects if o.name not in before)
        res["loose"] = {"result": sorted(r), "source_name": a.name, "source_verts": len(a.data.vertices),
                        "new_objects": new, "new_mesh_names": sorted(bpy.data.objects[n].data.name for n in new),
                        "new_verts": [len(bpy.data.objects[n].data.vertices) for n in new],
                        "selected_after": sorted(o.name for o in bpy.context.view_layer.objects if o.select_get()),
                        "active_after": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
                        "new_in_same_collections": [sorted(c.name for c in bpy.data.objects[n].users_collection) for n in new],
                        "source_collections": sorted(c.name for c in a.users_collection)}
    except Exception as e:
        bpy.ops.object.mode_set(mode="OBJECT")
        res["loose"] = err(e)
    # SELECTED: only half the verts selected
    d = new_mesh_obj("b2c2_sep_sel", bpy.ops.mesh.primitive_cube_add, location=(0, 4, 0))
    select_only(d)
    before = set(o.name for o in bpy.data.objects)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for p in d.data.polygons:
        p.select = p.index < 2
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        r = bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")
        new = sorted(o.name for o in bpy.data.objects if o.name not in before)
        res["selected"] = {"result": sorted(r), "new_objects": new, "source_faces": len(d.data.polygons),
                           "new_faces": [len(bpy.data.objects[n].data.polygons) for n in new]}
    except Exception as e:
        bpy.ops.object.mode_set(mode="OBJECT")
        res["selected"] = err(e)
    res["mode_after"] = bpy.context.mode
    return res


tryf("separate", separate)


# --- 4. make_single_user kwargs and effect vs obj.data.copy() --------------------------------
def single_user():
    res = {"kwargs": op_kwargs(bpy.ops.object.make_single_user), "type_enum": op_enum(bpy.ops.object.make_single_user, "type")}
    src = new_mesh_obj("b2c2_su", bpy.ops.mesh.primitive_cube_add)
    mat = bpy.data.materials.new("b2c2_su_mat")
    src.data.materials.append(mat)
    dup = src.copy()  # linked duplicate: same mesh data
    dup.name = "b2c2_su_dup"
    bpy.context.scene.collection.objects.link(dup)
    dup2 = src.copy()
    dup2.name = "b2c2_su_dup2"
    bpy.context.scene.collection.objects.link(dup2)
    res["before"] = {"data_users": src.data.users, "same_data": dup.data == src.data, "mat_users": mat.users}
    # the operator on the selected duplicate only
    select_only(dup)
    kw = {"type": "SELECTED_OBJECTS", "object": True, "obdata": True}
    if "material" in res["kwargs"]:
        kw["material"] = False
    try:
        r = bpy.ops.object.make_single_user(**kw)
        res["op_selected_obdata"] = {"result": sorted(r), "kw": sorted(kw), "dup_data": dup.data.name, "src_data": src.data.name,
                                     "same_data": dup.data == src.data, "src_data_users": src.data.users,
                                     "dup_data_users": dup.data.users, "dup2_same_as_src": dup2.data == src.data,
                                     "dup_mat_same": (dup.data.materials[0] == mat) if dup.data.materials else None}
    except Exception as e:
        res["op_selected_obdata"] = err(e)
    # the data API path on the other duplicate
    try:
        old = dup2.data
        dup2.data = dup2.data.copy()
        res["data_copy"] = {"dup2_data": dup2.data.name, "old_users": old.users, "new_users": dup2.data.users,
                            "same_data": dup2.data == src.data, "mat_kept": (dup2.data.materials[0] == mat) if dup2.data.materials else None,
                            "custom_props_kept": None}
    except Exception as e:
        res["data_copy"] = err(e)
    # custom props on the mesh survive a copy?
    src.data["b2c2_prop"] = 7
    dup3 = src.copy()
    dup3.name = "b2c2_su_dup3"
    bpy.context.scene.collection.objects.link(dup3)
    dup3.data = dup3.data.copy()
    res["data_copy_custom_prop"] = dup3.data.get("b2c2_prop")
    # make_single_user with nothing selected / in edit mode: poll
    select_only(dup3)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        res["poll_in_edit_mode"] = bpy.ops.object.make_single_user.poll()
    except Exception as e:
        res["poll_in_edit_mode"] = err(e)
    bpy.ops.object.mode_set(mode="OBJECT")
    # type=ALL semantics: does it touch unselected linked duplicates too?
    s2 = new_mesh_obj("b2c2_su_all", bpy.ops.mesh.primitive_cube_add)
    l2 = s2.copy()
    l2.name = "b2c2_su_all_dup"
    bpy.context.scene.collection.objects.link(l2)
    select_only(s2)
    try:
        r = bpy.ops.object.make_single_user(type="ALL", object=True, obdata=True)
        res["op_all"] = {"result": sorted(r), "s2_data": s2.data.name, "l2_data": l2.data.name, "same_data": s2.data == l2.data}
    except Exception as e:
        res["op_all"] = err(e)
    res["mode_after"] = bpy.context.mode
    return res


tryf("single_user", single_user)

print("B2CHECK2 " + json.dumps(out))
