# Headless facts for Phase B2 (engine readiness) beyond gdcheck.py: real calls, both versions.
# Prints one line: B2CHECK {json}. ASCII only. Never imports addon.py.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\b2check.py 2>&1 | findstr B2CHECK
import bpy
import bmesh
import json
import os
import sys
import tempfile
from mathutils import Matrix, Vector

out = {"version": bpy.app.version_string, "python": sys.version.split()[0]}
TMP = tempfile.mkdtemp(prefix="b2check_")


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


# --- 1. boolean: modifier solvers applied, and the edit-mode operator enum ----------
def boolean():
    a = new_mesh_obj("b2_bool_a", bpy.ops.mesh.primitive_cube_add, location=(0, 0, 0))
    b = new_mesh_obj("b2_bool_b", bpy.ops.mesh.primitive_cube_add, location=(0.5, 0.5, 0.5))
    res = {"intersect_boolean_solver_enum": op_enum(bpy.ops.mesh.intersect_boolean, "solver"),
           "intersect_boolean_kwargs": op_kwargs(bpy.ops.mesh.intersect_boolean)}
    mod = a.modifiers.new("Boolean", "BOOLEAN")
    solvers = [i.identifier for i in mod.bl_rna.properties["solver"].enum_items]
    res["modifier_solver_enum"] = solvers
    a.modifiers.remove(mod)
    for s in solvers:
        c = a.copy()
        c.data = a.data.copy()
        c.name = "b2_bool_" + s
        bpy.context.scene.collection.objects.link(c)
        m = c.modifiers.new("Boolean", "BOOLEAN")
        m.operation = "DIFFERENCE"
        m.object = b
        m.solver = s
        select_only(c)
        try:
            r = bpy.ops.object.modifier_apply(modifier=m.name)
            res["apply_" + s] = {"result": sorted(r), "faces": len(c.data.polygons), "verts": len(c.data.vertices)}
        except Exception as e:
            res["apply_" + s] = err(e)
    return res


tryf("boolean", boolean)


# --- 2. decimate / triangulate / weighted normal modifiers applied ------------------
def modifiers():
    src = new_mesh_obj("b2_sphere", bpy.ops.mesh.primitive_uv_sphere_add, segments=32, ring_count=16)
    res = {"src_faces": len(src.data.polygons), "src_tris_evaluated": tri_count(src)}
    m = src.modifiers.new("Dec", "DECIMATE")
    res["decimate_type_enum"] = [i.identifier for i in m.bl_rna.properties["decimate_type"].enum_items]
    res["delimit_enum"] = [i.identifier for i in m.bl_rna.properties["delimit"].enum_items]
    res["symmetry_axis_enum"] = [i.identifier for i in m.bl_rna.properties["symmetry_axis"].enum_items]
    m.decimate_type = "COLLAPSE"
    m.ratio = 0.5
    m.use_collapse_triangulate = True
    try:
        m.delimit = {"UV", "SEAM"}
        res["delimit_set"] = sorted(m.delimit)
    except Exception as e:
        res["delimit_set"] = err(e)
    m.use_symmetry = True
    m.symmetry_axis = "X"
    bpy.context.view_layer.update()
    res["face_count_readback_before_apply"] = m.face_count
    select_only(src)
    r = bpy.ops.object.modifier_apply(modifier=m.name)
    res["collapse_apply"] = {"result": sorted(r), "faces": len(src.data.polygons), "tris": tri_count(src)}
    # planar
    cube = new_mesh_obj("b2_planar", bpy.ops.mesh.primitive_cube_add)
    select_only(cube)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=3)
    bpy.ops.object.mode_set(mode="OBJECT")
    faces_before = len(cube.data.polygons)
    m2 = cube.modifiers.new("Planar", "DECIMATE")
    m2.decimate_type = "DISSOLVE"
    import math
    m2.angle_limit = math.radians(5)
    m2.use_dissolve_boundaries = False
    r = bpy.ops.object.modifier_apply(modifier=m2.name)
    res["planar_apply"] = {"result": sorted(r), "faces_before": faces_before, "faces_after": len(cube.data.polygons)}
    # unsubdivide
    cube2 = new_mesh_obj("b2_unsub", bpy.ops.mesh.primitive_cube_add)
    select_only(cube2)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=3)
    bpy.ops.object.mode_set(mode="OBJECT")
    m3 = cube2.modifiers.new("Unsub", "DECIMATE")
    m3.decimate_type = "UNSUBDIV"
    m3.iterations = 2
    r = bpy.ops.object.modifier_apply(modifier=m3.name)
    res["unsubdivide_apply"] = {"result": sorted(r), "faces_after": len(cube2.data.polygons)}
    # triangulate modifier with custom normals kept
    cyl = new_mesh_obj("b2_tri", bpy.ops.mesh.primitive_cylinder_add, vertices=12)
    t = cyl.modifiers.new("Tri", "TRIANGULATE")
    res["triangulate_quad_method_enum"] = [i.identifier for i in t.bl_rna.properties["quad_method"].enum_items]
    res["triangulate_ngon_method_enum"] = [i.identifier for i in t.bl_rna.properties["ngon_method"].enum_items]
    t.quad_method = "SHORTEST_DIAGONAL"
    t.ngon_method = "BEAUTY"
    t.min_vertices = 4
    t.keep_custom_normals = True
    select_only(cyl)
    r = bpy.ops.object.modifier_apply(modifier=t.name)
    res["triangulate_apply"] = {"result": sorted(r), "faces": len(cyl.data.polygons),
                                "all_tris": all(len(p.vertices) == 3 for p in cyl.data.polygons)}
    # weighted normal modifier
    wn_obj = new_mesh_obj("b2_wn", bpy.ops.mesh.primitive_cylinder_add, vertices=12)
    w = wn_obj.modifiers.new("WN", "WEIGHTED_NORMAL")
    res["weighted_normal_mode_enum"] = [i.identifier for i in w.bl_rna.properties["mode"].enum_items]
    w.mode = "FACE_AREA_WITH_ANGLE"
    w.keep_sharp = True
    select_only(wn_obj)
    try:
        r = bpy.ops.object.modifier_apply(modifier=w.name)
        res["weighted_normal_apply"] = {"result": sorted(r), "has_custom_normals": wn_obj.data.has_custom_normals}
    except Exception as e:
        res["weighted_normal_apply"] = err(e)
    return res


tryf("modifiers", modifiers)


# --- 3. ops kwargs with real calls -------------------------------------------------------
def ops():
    res = {"transform_apply_kwargs": op_kwargs(bpy.ops.object.transform_apply),
           "tris_convert_to_quads_kwargs": op_kwargs(bpy.ops.mesh.tris_convert_to_quads),
           "shade_smooth_by_angle_kwargs": op_kwargs(bpy.ops.object.shade_smooth_by_angle),
           "shade_auto_smooth_kwargs": op_kwargs(bpy.ops.object.shade_auto_smooth),
           "set_sharpness_by_angle_kwargs": op_kwargs(bpy.ops.mesh.set_sharpness_by_angle),
           "customdata_custom_splitnormals_clear": op_kwargs(bpy.ops.mesh.customdata_custom_splitnormals_clear),
           "customdata_custom_splitnormals_add": op_kwargs(bpy.ops.mesh.customdata_custom_splitnormals_add),
           "obj_export_kwargs": op_kwargs(bpy.ops.wm.obj_export),
           "obj_import_kwargs": op_kwargs(bpy.ops.wm.obj_import),
           "fbx_import_kwargs": op_kwargs(bpy.ops.import_scene.fbx),
           "gltf_import_kwargs": op_kwargs(bpy.ops.import_scene.gltf),
           "gltf_import_shading_enum": op_enum(bpy.ops.import_scene.gltf, "import_shading"),
           "gltf_bone_heuristic_enum": op_enum(bpy.ops.import_scene.gltf, "bone_heuristic")}
    cube = new_mesh_obj("b2_xf", bpy.ops.mesh.primitive_cube_add)
    cube.scale = (2, 2, -1)
    cube.rotation_euler = (0.3, 0, 0)
    select_only(cube)
    kw = {"location": False, "rotation": True, "scale": True}
    if "corrective_flip_normals" in res["transform_apply_kwargs"]:
        kw["corrective_flip_normals"] = True
    r = bpy.ops.object.transform_apply(**kw)
    res["transform_apply_negative_scale"] = {"result": sorted(r), "kw": sorted(kw), "scale_after": [round(v, 3) for v in cube.scale],
                                             "normal0_z": round(cube.data.polygons[0].normal.z, 3)}
    # shared mesh + isolate_users
    shared = cube.copy()
    bpy.context.scene.collection.objects.link(shared)
    shared.scale = (3, 3, 3)
    select_only(shared)
    try:
        r = bpy.ops.object.transform_apply(scale=True, isolate_users=True)
        res["transform_apply_isolate_users"] = {"result": sorted(r), "users_after": shared.data.users, "same_data": shared.data == cube.data}
    except Exception as e:
        res["transform_apply_isolate_users"] = err(e)
    # tris -> quads with topology_influence when present
    tri = new_mesh_obj("b2_t2q", bpy.ops.mesh.primitive_cube_add)
    select_only(tri)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris()
    n_tris = len(bmesh.from_edit_mesh(tri.data).faces)
    kw = {"face_threshold": 0.7, "shape_threshold": 0.7}
    if "topology_influence" in res["tris_convert_to_quads_kwargs"]:
        kw["topology_influence"] = 1.0
    r = bpy.ops.mesh.tris_convert_to_quads(**kw)
    bpy.ops.object.mode_set(mode="OBJECT")
    res["tris_convert_to_quads"] = {"result": sorted(r), "tris_before": n_tris, "faces_after": len(tri.data.polygons), "kw": sorted(kw)}
    # decimate op in edit mode
    d = new_mesh_obj("b2_dec_op", bpy.ops.mesh.primitive_uv_sphere_add, segments=16, ring_count=8)
    select_only(d)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    r = bpy.ops.mesh.decimate(ratio=0.5)
    bpy.ops.object.mode_set(mode="OBJECT")
    res["mesh_decimate_op"] = {"result": sorted(r), "faces_after": len(d.data.polygons)}
    # non-manifold / interior / by sides in edit mode
    select_only(d)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")
    r1 = bpy.ops.mesh.select_non_manifold(extend=False, use_wire=True, use_boundary=True, use_multi_face=True, use_non_contiguous=True, use_verts=True)
    bm = bmesh.from_edit_mesh(d.data)
    nm = sum(1 for e in bm.edges if e.select)
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_mode(type="FACE")
    r2 = bpy.ops.mesh.select_face_by_sides(number=4, type="GREATER", extend=False)
    ng = sum(1 for f in bm.faces if f.select)
    r3 = bpy.ops.mesh.select_interior_faces()
    bpy.ops.object.mode_set(mode="OBJECT")
    res["select_ops"] = {"non_manifold": [sorted(r1), nm], "ngons_gt4": [sorted(r2), ng], "interior": sorted(r3)}
    return res


tryf("ops", ops)


# --- 4. custom normals and auto smooth across versions -----------------------------------
def normals():
    cyl = new_mesh_obj("b2_norm", bpy.ops.mesh.primitive_cylinder_add, vertices=16)
    me = cyl.data
    res = {"mesh_attrs": {a: hasattr(me, a) for a in ("use_auto_smooth", "auto_smooth_angle", "has_custom_normals",
                                                       "normals_split_custom_set", "normals_split_custom_set_from_vertices",
                                                       "calc_normals_split", "calc_smooth_groups", "calc_tangents",
                                                       "corner_normals", "polygon_normals", "vertex_normals", "normals_domain",
                                                       "set_sharp_from_angle", "shade_smooth", "shade_flat")}}
    select_only(cyl)
    import math
    try:
        r = bpy.ops.object.shade_auto_smooth(angle=math.radians(30))
        res["shade_auto_smooth"] = {"result": sorted(r), "modifiers": [(m.name, m.type) for m in cyl.modifiers],
                                    "node_group": (cyl.modifiers[-1].node_group.name if cyl.modifiers and cyl.modifiers[-1].type == "NODES" and cyl.modifiers[-1].node_group else None)}
        if cyl.modifiers:
            m = cyl.modifiers[-1]
            # GN modifier inputs: 4.x idprop vs 5.x properties.inputs
            try:
                keys = list(m.properties.inputs.keys()) if hasattr(m, "properties") else [k for k in m.keys()]
                res["smooth_by_angle_inputs"] = keys
            except Exception as e:
                res["smooth_by_angle_inputs"] = err(e)
            r = bpy.ops.object.modifier_apply(modifier=m.name)
            res["shade_auto_smooth_apply"] = {"result": sorted(r), "has_custom_normals": me.has_custom_normals,
                                              "sharp_edges": sum(1 for e in me.edges if e.use_edge_sharp),
                                              "smooth_faces": sum(1 for p in me.polygons if p.use_smooth)}
    except Exception as e:
        res["shade_auto_smooth"] = err(e)
    try:
        r = bpy.ops.object.shade_smooth_by_angle(angle=math.radians(30), keep_sharp_edges=True)
        res["shade_smooth_by_angle"] = {"result": sorted(r), "modifiers": [(m.name, m.type) for m in cyl.modifiers],
                                        "sharp_edges": sum(1 for e in me.edges if e.use_edge_sharp)}
    except Exception as e:
        res["shade_smooth_by_angle"] = err(e)
    # custom normals set from vertices
    try:
        me.normals_split_custom_set_from_vertices([v.normal for v in me.vertices])
        res["custom_set_from_vertices"] = {"has_custom_normals": me.has_custom_normals}
    except Exception as e:
        res["custom_set_from_vertices"] = err(e)
    try:
        res["corner_normals_len"] = len(me.corner_normals)
        res["normals_domain"] = getattr(me, "normals_domain", None)
    except Exception as e:
        res["corner_normals_len"] = err(e)
    try:
        res["calc_smooth_groups"] = [len(x) if hasattr(x, "__len__") else x for x in me.calc_smooth_groups(use_bitflags=False)]
    except Exception as e:
        res["calc_smooth_groups"] = err(e)
    try:
        if not me.uv_layers:
            me.uv_layers.new(name="UVMap")
        me.calc_tangents(uvmap="UVMap")
        res["calc_tangents"] = {"tangent0": [round(v, 3) for v in me.loops[0].tangent], "bitangent_sign": me.loops[0].bitangent_sign}
        me.free_tangents()
    except Exception as e:
        res["calc_tangents"] = err(e)
    # clear custom normals via op
    select_only(cyl)
    try:
        r = bpy.ops.mesh.customdata_custom_splitnormals_clear()
        res["clear_custom_normals"] = {"result": sorted(r), "has_custom_normals": me.has_custom_normals}
    except Exception as e:
        res["clear_custom_normals"] = err(e)
    # data transfer of custom normals from a hi-poly
    hi = new_mesh_obj("b2_hi", bpy.ops.mesh.primitive_uv_sphere_add, segments=48, ring_count=24)
    lo = new_mesh_obj("b2_lo", bpy.ops.mesh.primitive_uv_sphere_add, segments=12, ring_count=6)
    mod = lo.modifiers.new("DT", "DATA_TRANSFER")
    mod.object = hi
    mod.use_loop_data = True
    try:
        mod.data_types_loops = {"CUSTOM_NORMAL"}
        mod.loop_mapping = "POLYINTERP_NEAREST"
        select_only(lo)
        r = bpy.ops.object.modifier_apply(modifier=mod.name)
        res["data_transfer_custom_normals"] = {"result": sorted(r), "has_custom_normals": lo.data.has_custom_normals}
    except Exception as e:
        res["data_transfer_custom_normals"] = err(e)
    res["loop_mapping_enum"] = [i.identifier for i in bpy.types.DataTransferModifier.bl_rna.properties["loop_mapping"].enum_items]
    return res


tryf("normals", normals)


# --- 5. FBX round trip with UCX_/SOCKET_ naming, custom props, tangents, smooth groups ----
def fbx_roundtrip():
    root = bpy.data.objects.new("b2_Prop", None)
    bpy.context.scene.collection.objects.link(root)
    mesh = new_mesh_obj("b2_Prop_LOD0", bpy.ops.mesh.primitive_cube_add)
    mesh.parent = root
    ucx = new_mesh_obj("UCX_b2_Prop_00", bpy.ops.mesh.primitive_cube_add)
    ucx.scale = (1.1, 1.1, 1.1)
    ucx.parent = root
    ucx.display_type = "WIRE"
    ucx.hide_render = True
    sock = bpy.data.objects.new("SOCKET_Muzzle", None)
    sock.empty_display_type = "ARROWS"
    sock.location = (0, 1, 0)
    sock.parent = mesh
    bpy.context.scene.collection.objects.link(sock)
    mesh["lod_group"] = "b2_Prop"
    mesh["fbx_custom_int"] = 7
    if not mesh.data.uv_layers:
        mesh.data.uv_layers.new(name="UVMap")
    names = ["b2_Prop", "b2_Prop_LOD0", "UCX_b2_Prop_00", "SOCKET_Muzzle"]
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name in names)
    bpy.context.view_layer.objects.active = mesh
    fp = os.path.join(TMP, "prop.fbx")
    res = {}
    kw = dict(filepath=fp, use_selection=True, axis_forward="-Y", axis_up="Z", apply_scale_options="FBX_SCALE_NONE",
              use_tspace=True, use_triangles=True, use_custom_props=True, mesh_smooth_type="FACE",
              object_types={"EMPTY", "MESH"}, add_leaf_bones=False, bake_space_transform=False)
    try:
        r = bpy.ops.export_scene.fbx(**kw)
        res["export"] = {"result": sorted(r), "bytes": os.path.getsize(fp)}
    except Exception as e:
        res["export"] = err(e)
    kw["mesh_smooth_type"] = "SMOOTH_GROUP"
    kw["filepath"] = os.path.join(TMP, "prop_sg.fbx")
    try:
        r = bpy.ops.export_scene.fbx(**kw)
        res["export_smooth_group"] = sorted(r)
    except Exception as e:
        res["export_smooth_group"] = err(e)
    # re-import into the same scene, compare names / hierarchy / custom props
    before = set(o.name for o in bpy.data.objects)
    try:
        r = bpy.ops.import_scene.fbx(filepath=fp, use_custom_normals=True, ignore_leaf_bones=True, use_custom_props=True)
        new = [o for o in bpy.data.objects if o.name not in before]
        res["import"] = {"result": sorted(r),
                         "objects": sorted((o.name, o.type, o.parent.name if o.parent else None) for o in new),
                         "custom_props": {o.name: {k: o[k] for k in o.keys() if not k.startswith("_")} for o in new if o.keys()},
                         "mesh_has_custom_normals": [o.data.has_custom_normals for o in new if o.type == "MESH"],
                         "mesh_tris": [len(o.data.polygons) for o in new if o.type == "MESH"],
                         "socket_location": [[round(v, 3) for v in o.location] for o in new if o.name.startswith("SOCKET_")]}
    except Exception as e:
        res["import"] = err(e)
    return res


tryf("fbx_roundtrip", fbx_roundtrip)


# --- 6. glTF round trip: extras, vertex colours by NAME, tangents, attributes ------------
def gltf_roundtrip():
    mesh = new_mesh_obj("b2_gltf", bpy.ops.mesh.primitive_cube_add)
    me = mesh.data
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    ca = me.color_attributes.new(name="TintMask", type="BYTE_COLOR", domain="CORNER")
    for i in range(len(ca.data)):
        ca.data[i].color = (1.0, 0.0, 0.0, 1.0)
    ca2 = me.color_attributes.new(name="Col2", type="FLOAT_COLOR", domain="POINT")
    mesh["extra_str"] = "hello"
    mesh["extra_int"] = 3
    attr = me.attributes.new(name="custom_float", type="FLOAT", domain="POINT")
    res = {"export_vertex_color_enum": op_enum(bpy.ops.export_scene.gltf, "export_vertex_color"),
           "export_image_format_enum": op_enum(bpy.ops.export_scene.gltf, "export_image_format"),
           "color_attribute_names": [c.name for c in me.color_attributes], "active_color": me.color_attributes.active_color_name if hasattr(me.color_attributes, "active_color_name") else None}
    select_only(mesh)
    fp = os.path.join(TMP, "prop.glb")
    kw = dict(filepath=fp, export_format="GLB", use_selection=True, export_yup=True, export_apply=True,
              export_tangents=True, export_extras=True, export_attributes=True, export_vertex_color="MATERIAL",
              export_image_format="AUTO")
    try:
        r = bpy.ops.export_scene.gltf(**kw)
        res["export_material_vc"] = {"result": sorted(r), "bytes": os.path.getsize(fp)}
    except Exception as e:
        res["export_material_vc"] = err(e)
    kw2 = dict(kw)
    kw2["filepath"] = os.path.join(TMP, "prop_name.glb")
    kw2["export_vertex_color"] = "NAME"
    try:
        kw2["export_vertex_color_name"] = "TintMask"
        r = bpy.ops.export_scene.gltf(**kw2)
        res["export_name_vc"] = {"result": sorted(r), "bytes": os.path.getsize(kw2["filepath"])}
    except TypeError as e:
        res["export_name_vc"] = err(e)
        kw2.pop("export_vertex_color_name", None)
        try:
            r = bpy.ops.export_scene.gltf(**kw2)
            res["export_name_vc_no_name_kw"] = sorted(r)
        except Exception as e2:
            res["export_name_vc_no_name_kw"] = err(e2)
    except Exception as e:
        res["export_name_vc"] = err(e)
    res["gltf_kwargs_vc"] = [k for k in op_kwargs(bpy.ops.export_scene.gltf) if "vertex_color" in k or "active_vertex" in k]
    before = set(o.name for o in bpy.data.objects)
    try:
        r = bpy.ops.import_scene.gltf(filepath=fp, import_shading="NORMALS", merge_vertices=False)
        new = [o for o in bpy.data.objects if o.name not in before]
        res["import"] = {"result": sorted(r), "objects": [(o.name, o.type) for o in new],
                         "extras": {o.name: {k: o[k] for k in o.keys()} for o in new},
                         "color_attributes": [[c.name for c in o.data.color_attributes] for o in new if o.type == "MESH"],
                         "attributes": [[a.name for a in o.data.attributes if not a.name.startswith(".") and a.name not in ("position", "sharp_face", "UVMap", "material_index")] for o in new if o.type == "MESH"],
                         "verts": [len(o.data.vertices) for o in new if o.type == "MESH"]}
    except Exception as e:
        res["import"] = err(e)
    return res


tryf("gltf_roundtrip", gltf_roundtrip)


# --- 7. OBJ export apply_transform + smooth groups, re-import weld ------------------------
def obj_roundtrip():
    mesh = new_mesh_obj("b2_obj", bpy.ops.mesh.primitive_cube_add)
    mesh.location = (5, 0, 0)
    select_only(mesh)
    fp = os.path.join(TMP, "prop.obj")
    kw = dict(filepath=fp, export_selected_objects=True, export_smooth_groups=True, smooth_group_bitflags=False,
              export_triangulated_mesh=False, export_uv=True, export_normals=True, export_materials=False)
    res = {"has_apply_transform": "apply_transform" in op_kwargs(bpy.ops.wm.obj_export)}
    if res["has_apply_transform"]:
        kw["apply_transform"] = False
    try:
        r = bpy.ops.wm.obj_export(**kw)
        res["export"] = {"result": sorted(r), "bytes": os.path.getsize(fp)}
        txt = open(fp, encoding="utf-8", errors="replace").read()
        res["first_v_line"] = next((l for l in txt.splitlines() if l.startswith("v ")), None)
        res["has_s_lines"] = any(l.startswith("s ") for l in txt.splitlines())
    except Exception as e:
        res["export"] = err(e)
    before = set(o.name for o in bpy.data.objects)
    try:
        r = bpy.ops.wm.obj_import(filepath=fp)
        new = [o for o in bpy.data.objects if o.name not in before]
        res["import"] = {"result": sorted(r), "objects": [(o.name, len(o.data.vertices), len(o.data.polygons)) for o in new],
                         "location": [[round(v, 3) for v in o.location] for o in new],
                         "vert0": [[round(v, 3) for v in o.data.vertices[0].co] for o in new]}
    except Exception as e:
        res["import"] = err(e)
    return res


tryf("obj_roundtrip", obj_roundtrip)


# --- 8. convex hull, bisect slabs, volumes, bounds ---------------------------------------
def collision():
    torus = new_mesh_obj("b2_torus", bpy.ops.mesh.primitive_torus_add, major_segments=24, minor_segments=12)
    bm = bmesh.new()
    bm.from_mesh(torus.data)
    res = {"src_faces": len(bm.faces), "src_volume": round(bm.calc_volume(signed=False), 4)}
    hull = bmesh.ops.convex_hull(bm, input=bm.verts, use_existing_faces=False)
    res["convex_hull_keys"] = sorted(hull.keys())
    # keep only hull geometry
    interior = [g for g in hull["geom_interior"] if isinstance(g, bmesh.types.BMVert)]
    unused = [g for g in hull["geom_unused"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.delete(bm, geom=interior + unused, context="VERTS")
    hm = bpy.data.meshes.new("b2_hull")
    bm.to_mesh(hm)
    res["hull_faces"] = len(hm.polygons)
    res["hull_volume"] = round(bm.calc_volume(signed=False), 4)
    bm.free()
    # bisect into 2 slabs along Z
    bm2 = bmesh.new()
    bm2.from_mesh(torus.data)
    r = bmesh.ops.bisect_plane(bm2, geom=bm2.verts[:] + bm2.edges[:] + bm2.faces[:], plane_co=(0, 0, 0), plane_no=(0, 0, 1),
                               clear_inner=True, clear_outer=False)
    res["bisect_keys"] = sorted(r.keys())
    res["bisect_faces_upper"] = len(bm2.faces)
    bm2.free()
    # bounds
    res["bound_box"] = [[round(c, 3) for c in v] for v in torus.bound_box][:2]
    res["dimensions"] = [round(v, 3) for v in torus.dimensions]
    # oriented bounds via matrix of principal axes is app-side; record the world bbox helper inputs
    res["matrix_world_identity"] = torus.matrix_world == Matrix.Identity(4)
    # object display / naming rules
    proxy = bpy.data.objects.new("UCX_b2_torus_00", hm)
    bpy.context.scene.collection.objects.link(proxy)
    proxy.display_type = "WIRE"
    proxy.hide_render = True
    proxy.parent = torus
    res["proxy"] = {"display_type": proxy.display_type, "hide_render": proxy.hide_render, "parent": proxy.parent.name}
    res["display_type_enum"] = [i.identifier for i in bpy.types.Object.bl_rna.properties["display_type"].enum_items]
    return res


tryf("collision", collision)


# --- 9. names, evaluated tri counts, mesh compare ------------------------------------------
def misc():
    res = {}
    long_name = "n" * 300
    o = bpy.data.objects.new(long_name, None)
    res["max_name_len"] = len(o.name)
    bpy.data.objects.remove(o)
    o2 = bpy.data.objects.new("Cube", None)
    o3 = bpy.data.objects.new("Cube", None)
    res["collision_suffix"] = [o2.name, o3.name]
    bpy.data.objects.remove(o2)
    bpy.data.objects.remove(o3)
    cube = new_mesh_obj("b2_eval", bpy.ops.mesh.primitive_cube_add)
    m = cube.modifiers.new("Sub", "SUBSURF")
    m.levels = 2
    res["eval_tris_with_subsurf"] = {"data_polys": len(cube.data.polygons), "evaluated_tris": tri_count(cube)}
    a = new_mesh_obj("b2_cmp_a", bpy.ops.mesh.primitive_cube_add)
    b = new_mesh_obj("b2_cmp_b", bpy.ops.mesh.primitive_cube_add)
    res["unit_test_compare_same"] = a.data.unit_test_compare(mesh=b.data)
    b.data.vertices[0].co.x += 0.01
    res["unit_test_compare_moved"] = a.data.unit_test_compare(mesh=b.data)
    res["unit_test_compare_threshold_kw"] = "threshold" in str(bpy.types.Mesh.bl_rna.functions["unit_test_compare"].parameters.keys())
    res["validate"] = a.data.validate(verbose=False)
    # numpy readback with explicit dtype
    try:
        import numpy as np
        n = len(a.data.vertices)
        co = np.empty(n * 3, dtype=np.float64)
        a.data.vertices.foreach_get("co", co)
        res["foreach_get_float64"] = str(co.dtype)
        res["numpy_vector_dtype"] = str(np.array(a.data.vertices[0].co).dtype)
    except Exception as e:
        res["foreach_get_float64"] = err(e)
    # origin move via mesh transform (BOTTOM_CENTER)
    lo = min(v.co.z for v in a.data.vertices)
    a.data.transform(Matrix.Translation((0, 0, -lo)))
    a.location.z += lo
    res["origin_bottom_center"] = {"min_z_after": round(min(v.co.z for v in a.data.vertices), 3), "location_z": round(a.location.z, 3)}
    # timbermesh plugin presence
    import addon_utils
    res["timbermesh_module"] = any(m.__name__ == "timbermesh_blender_plugin" for m in addon_utils.modules())
    res["mode_after"] = bpy.context.mode
    return res


tryf("misc", misc)

print("B2CHECK " + json.dumps(out))
