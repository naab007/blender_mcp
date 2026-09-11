# Headless facts for Phase B1 (rigging and animation) beyond apicheck.py / apicheck2.py.
# Runs on 4.x and 5.x. Prints one line: B1CHECK {json}. ASCII only. Never imports addon.py.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\b1check.py 2>&1 | findstr B1CHECK
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


# --- build a two-bone armature and a skinned cube --------------------------------
def build_rig():
    arm = bpy.data.armatures.new("b1_arm")
    ao = bpy.data.objects.new("b1_arm", arm)
    bpy.context.scene.collection.objects.link(ao)
    bpy.context.view_layer.objects.active = ao
    ao.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = arm.edit_bones.new("root")
    root.head = (0, 0, 0)
    root.tail = (0, 0, 1)
    child = arm.edit_bones.new("child.L")
    child.head = (0, 0, 1)
    child.tail = (0.5, 0, 2)
    child.parent = root
    child.use_connect = True
    res = {"edit_bones": [b.name for b in arm.edit_bones],
           "child_head_snapped": list(child.head) == [0.0, 0.0, 1.0]}
    bpy.ops.object.mode_set(mode="OBJECT")
    # EditBone invalidation after leaving edit mode
    try:
        _ = child.name
        res["editbone_after_exit"] = "still readable: " + str(_)
    except Exception as e:
        res["editbone_after_exit"] = err(e)
    res["bones_after_exit"] = [b.name for b in arm.bones]
    res["bone_parent"] = arm.bones["child.L"].parent.name
    res["bone_use_connect"] = arm.bones["child.L"].use_connect
    return res


tryf("build_rig", build_rig)


# --- Bone.select vs PoseBone.select, hide -----------------------------------------
def select_attrs():
    ao = bpy.data.objects["b1_arm"]
    arm = ao.data
    bone = arm.bones["root"]
    pb = ao.pose.bones["root"]
    res = {"Bone": {a: hasattr(bone, a) for a in ("select", "select_head", "select_tail", "hide", "hide_select")},
           "PoseBone": {a: hasattr(pb, a) for a in ("select", "hide", "bone", "rotation_mode", "constraints",
                                                    "custom_shape", "bone_group")},
           "EditBone_attrs_on_type": {a: hasattr(bpy.types.EditBone, a) for a in ("select", "select_head", "select_tail", "hide")}}
    for owner, name in ((bone, "Bone"), (pb, "PoseBone")):
        try:
            owner.select = True
            res[name + "_select_assign"] = "ok"
        except Exception as e:
            res[name + "_select_assign"] = err(e)
    res["armature_collections"] = hasattr(arm, "collections") and hasattr(arm, "collections_all")
    try:
        coll = arm.collections.new("DEF")
        coll.assign(bone)
        res["bone_collection_assign"] = [c.name for c in bone.collections]
    except Exception as e:
        res["bone_collection_assign"] = err(e)
    return res


tryf("select_attrs", select_attrs)


# --- keyframe_insert on a pose bone, actions, slots, channelbags -------------------
def keyframes():
    ao = bpy.data.objects["b1_arm"]
    pb = ao.pose.bones["child.L"]
    res = {"rotation_mode_default": pb.rotation_mode}
    pb.rotation_mode = "XYZ"
    bpy.context.scene.frame_set(1)
    pb.location = (0, 0, 0)
    pb.rotation_euler = (0, 0, 0)
    r1 = pb.keyframe_insert(data_path="location", frame=1)
    r2 = pb.keyframe_insert(data_path="rotation_euler", frame=1)
    bpy.context.scene.frame_set(10)
    pb.location = (0, 1, 0)
    pb.rotation_euler = (0.5, 0, 0)
    r3 = pb.keyframe_insert(data_path="location", frame=10)
    r4 = pb.keyframe_insert(data_path="rotation_euler", frame=10)
    res["keyframe_insert_results"] = [r1, r2, r3, r4]
    ad = ao.animation_data
    res["animation_data"] = ad is not None
    action = ad.action if ad else None
    res["action_name"] = action.name if action else None
    res["ad_attrs"] = {a: hasattr(ad, a) for a in ("action_slot", "action_suitable_slots", "last_slot_identifier",
                                                    "nla_tracks", "drivers")} if ad else None
    res["action_attrs"] = {a: hasattr(action, a) for a in ("fcurves", "groups", "id_root", "slots", "layers",
                                                            "frame_range", "curve_frame_range", "is_action_layered",
                                                            "is_action_legacy", "is_empty")} if action else None
    if action is not None:
        # legacy path
        try:
            res["legacy_fcurves"] = [(fc.data_path, fc.array_index, len(fc.keyframe_points)) for fc in action.fcurves][:8]
        except Exception as e:
            res["legacy_fcurves"] = err(e)
        try:
            res["legacy_groups"] = [g.name for g in action.groups]
        except Exception as e:
            res["legacy_groups"] = err(e)
        # slotted path
        try:
            res["slots"] = [(s.identifier, getattr(s, "name_display", None), s.target_id_type) for s in action.slots]
        except Exception as e:
            res["slots"] = err(e)
        try:
            res["action_slot"] = ad.action_slot.identifier if ad.action_slot else None
        except Exception as e:
            res["action_slot"] = err(e)
        try:
            from bpy_extras import anim_utils
            res["anim_utils_funcs"] = [f for f in ("action_get_channelbag_for_slot", "action_ensure_channelbag_for_slot",
                                                   "bake_action", "bake_action_objects", "BakeOptions") if hasattr(anim_utils, f)]
            if hasattr(anim_utils, "action_get_channelbag_for_slot"):
                cb = anim_utils.action_get_channelbag_for_slot(action, ad.action_slot)
                res["channelbag"] = None if cb is None else {
                    "fcurves": [(fc.data_path, fc.array_index, len(fc.keyframe_points)) for fc in cb.fcurves][:8],
                    "groups": [g.name for g in cb.groups] if hasattr(cb, "groups") else "no groups attr",
                    "fcurves_find": cb.fcurves.find('pose.bones["child.L"].location', index=1) is not None}
        except Exception as e:
            res["anim_utils"] = err(e)
        try:
            res["layers_strips"] = [(len(l.strips), [len(s.channelbags) for s in l.strips]) for l in action.layers]
        except Exception as e:
            res["layers_strips"] = err(e)
        # fcurve lookup helpers that work on both: action.fcurves.find (4.x) / channelbag.fcurves.find (5.x)
        try:
            res["action_fcurves_find"] = action.fcurves.find('pose.bones["child.L"].location', index=1) is not None
        except Exception as e:
            res["action_fcurves_find"] = err(e)
        res["frame_range"] = list(action.frame_range)
        # assign the same action to a fresh object: does it animate without a slot?
        try:
            arm2 = bpy.data.armatures.new("b1_arm2")
            ao2 = bpy.data.objects.new("b1_arm2", arm2)
            bpy.context.scene.collection.objects.link(ao2)
            ad2 = ao2.animation_data_create()
            ad2.action = action
            res["assign_action_slot_auto"] = (ad2.action_slot.identifier if getattr(ad2, "action_slot", None) else None)
            res["assign_action_suitable_slots"] = [s.identifier for s in ad2.action_suitable_slots] if hasattr(ad2, "action_suitable_slots") else "n/a"
        except Exception as e:
            res["assign_action"] = err(e)
    # evaluated pose after frame change
    bpy.context.scene.frame_set(10)
    res["pose_location_at_10"] = [round(v, 3) for v in pb.location]
    bpy.context.scene.frame_set(1)
    res["pose_location_at_1"] = [round(v, 3) for v in pb.location]
    return res


tryf("keyframes", keyframes)


# --- parent_set ARMATURE_* on a skinned cube ---------------------------------------
def parent_set():
    ao = bpy.data.objects["b1_arm"]
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1))
    cube = bpy.context.active_object
    cube.name = "b1_cube"
    res = {"enum": op_enum(bpy.ops.object.parent_set, "type")}
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    cube.select_set(True)
    ao.select_set(True)
    bpy.context.view_layer.objects.active = ao
    try:
        r = bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        res["ARMATURE_AUTO"] = {"result": sorted(r), "parent": cube.parent.name if cube.parent else None,
                                "parent_type": cube.parent_type,
                                "modifiers": [(m.type, m.object.name if getattr(m, "object", None) else None) for m in cube.modifiers],
                                "vertex_groups": [(vg.name, len([v for v in cube.data.vertices if any(g.group == vg.index for g in v.groups)])) for vg in cube.vertex_groups]}
    except Exception as e:
        res["ARMATURE_AUTO"] = err(e)
    res["mode_after"] = bpy.context.mode
    res["active_after"] = bpy.context.view_layer.objects.active.name
    return res


tryf("parent_set", parent_set)


# --- symmetrize and nla.bake called for real -----------------------------------------
def symmetrize():
    ao = bpy.data.objects["b1_arm"]
    res = {"kwargs": op_kwargs(bpy.ops.armature.symmetrize)}
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ao.select_set(True)
    bpy.context.view_layer.objects.active = ao
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        for eb in ao.data.edit_bones:
            eb.select = eb.select_head = eb.select_tail = (eb.name == "child.L")
        try:
            r = bpy.ops.armature.symmetrize(direction="POSITIVE_X", copy_bone_colors=True)
            res["call_with_copy_bone_colors"] = sorted(r)
        except TypeError as e:
            res["call_with_copy_bone_colors"] = err(e)
            r = bpy.ops.armature.symmetrize(direction="POSITIVE_X")
            res["call_without"] = sorted(r)
        res["bones_after"] = [b.name for b in ao.data.edit_bones]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    return res


tryf("symmetrize", symmetrize)


def nla_bake():
    ao = bpy.data.objects["b1_arm"]
    res = {"kwargs": op_kwargs(bpy.ops.nla.bake), "bake_types": op_enum(bpy.ops.nla.bake, "bake_types"),
           "channel_types": op_enum(bpy.ops.nla.bake, "channel_types")}
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ao.select_set(True)
    bpy.context.view_layer.objects.active = ao
    bpy.ops.object.mode_set(mode="POSE")
    try:
        # bone selection lives on Bone (4.x) or PoseBone (5.x): set whichever exists
        for pb in ao.pose.bones:
            if hasattr(pb.bone, "select"):
                pb.bone.select = True
            if hasattr(pb, "select"):
                pb.select = True
        res["selected_via"] = "PoseBone.select" if hasattr(ao.pose.bones[0], "select") else "Bone.select"
        try:
            r = bpy.ops.nla.bake(frame_start=1, frame_end=10, step=1, only_selected=True, visual_keying=True,
                                 clear_constraints=False, use_current_action=True, bake_types={"POSE"})
            res["call"] = sorted(r)
        except Exception as e:
            res["call"] = err(e)
        ad = ao.animation_data
        res["action_after"] = ad.action.name if ad and ad.action else None
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    # bake_action helper from anim_utils
    try:
        from bpy_extras import anim_utils
        import inspect
        res["bake_action_sig"] = str(inspect.signature(anim_utils.bake_action))[:300]
        if hasattr(anim_utils, "BakeOptions"):
            res["BakeOptions_fields"] = list(getattr(anim_utils.BakeOptions, "_fields", []))
    except Exception as e:
        res["bake_action_sig"] = err(e)
    return res


tryf("nla_bake", nla_bake)


# --- render.opengl headless -------------------------------------------------------
def render_opengl():
    res = {"kwargs": op_kwargs(bpy.ops.render.opengl)}
    try:
        res["poll"] = bpy.ops.render.opengl.poll()
    except Exception as e:
        res["poll"] = err(e)
    fp = os.path.join(tempfile.gettempdir(), "b1check_opengl.png")
    if os.path.exists(fp):
        os.remove(fp)
    bpy.context.scene.render.filepath = fp
    try:
        r = bpy.ops.render.opengl(write_still=True, view_context=False)
        res["call"] = {"result": sorted(r), "file": os.path.exists(fp)}
    except Exception as e:
        res["call"] = err(e)
    res["screenshot_area_poll"] = None
    try:
        res["screenshot_area_poll"] = bpy.ops.screen.screenshot_area.poll()
    except Exception as e:
        res["screenshot_area_poll"] = err(e)
    return res


tryf("render_opengl", render_opengl)


# --- FBX / glTF exports of the rigged cube with the doc's kwargs ------------------
def exporters():
    res = {"fbx_mesh_smooth_type": op_enum(bpy.ops.export_scene.fbx, "mesh_smooth_type"),
           "fbx_primary_bone_axis": op_enum(bpy.ops.export_scene.fbx, "primary_bone_axis"),
           "fbx_apply_scale_options": op_enum(bpy.ops.export_scene.fbx, "apply_scale_options"),
           "fbx_armature_nodetype": op_enum(bpy.ops.export_scene.fbx, "armature_nodetype"),
           "gltf_export_format": op_enum(bpy.ops.export_scene.gltf, "export_format"),
           "gltf_export_vertex_color": op_enum(bpy.ops.export_scene.gltf, "export_vertex_color"),
           "gltf_export_animation_mode": op_enum(bpy.ops.export_scene.gltf, "export_animation_mode"),
           "gltf_anim_kwargs": [k for k in ("export_animations", "export_skins", "export_morph", "export_def_bones",
                                            "export_anim_single_armature", "export_reset_pose_bones", "export_bake_animation",
                                            "export_frame_range", "export_nla_strips", "export_optimize_animation_size",
                                            "export_rest_position_armature", "export_influence_nb", "export_all_influences")
                                if k in op_kwargs(bpy.ops.export_scene.gltf)],
           "fbx_anim_kwargs": [k for k in ("bake_anim", "bake_anim_use_all_bones", "bake_anim_use_nla_strips",
                                           "bake_anim_use_all_actions", "bake_anim_force_startend_keying",
                                           "bake_anim_step", "bake_anim_simplify_factor", "add_leaf_bones",
                                           "use_armature_deform_only", "mesh_smooth_type", "use_tspace",
                                           "colors_type", "use_custom_props", "use_selection", "use_visible",
                                           "use_active_collection", "object_types", "armature_nodetype")
                               if k in op_kwargs(bpy.ops.export_scene.fbx)]}
    ao = bpy.data.objects["b1_arm"]
    cube = bpy.data.objects.get("b1_cube")
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name in ("b1_arm", "b1_cube"))
    bpy.context.view_layer.objects.active = cube or ao
    d = tempfile.mkdtemp(prefix="b1check_")
    fbx = os.path.join(d, "rig.fbx")
    try:
        r = bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, bake_anim=True, add_leaf_bones=False,
                                     use_armature_deform_only=True, bake_anim_simplify_factor=0.0,
                                     mesh_smooth_type="FACE", primary_bone_axis="Y", secondary_bone_axis="X",
                                     apply_scale_options="FBX_SCALE_NONE")
        res["fbx_export"] = {"result": sorted(r), "bytes": os.path.getsize(fbx) if os.path.exists(fbx) else 0}
    except Exception as e:
        res["fbx_export"] = err(e)
    try:
        r = bpy.ops.export_scene.fbx(filepath=os.path.join(d, "rig_sg.fbx"), use_selection=True,
                                     mesh_smooth_type="SMOOTH_GROUP")
        res["fbx_smooth_group"] = sorted(r)
    except Exception as e:
        res["fbx_smooth_group"] = err(e)
    glb = os.path.join(d, "rig.glb")
    try:
        r = bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_animations=True,
                                      export_skins=True, export_morph=True)
        res["gltf_export"] = {"result": sorted(r), "bytes": os.path.getsize(glb) if os.path.exists(glb) else 0}
    except Exception as e:
        res["gltf_export"] = err(e)
    # re-import the glb and see what came back
    try:
        before = set(o.name for o in bpy.data.objects)
        acts_before = set(a.name for a in bpy.data.actions)
        r = bpy.ops.import_scene.gltf(filepath=glb)
        new_objs = [o for o in bpy.data.objects if o.name not in before]
        res["gltf_reimport"] = {"result": sorted(r), "new_objects": [(o.name, o.type) for o in new_objs],
                               "new_actions": sorted(set(a.name for a in bpy.data.actions) - acts_before)}
    except Exception as e:
        res["gltf_reimport"] = err(e)
    res["bvh_ops"] = {"import": op_kwargs(bpy.ops.import_anim.bvh)[:6] if hasattr(bpy.ops, "import_anim") else "n/a",
                      "export": op_kwargs(bpy.ops.export_anim.bvh)[:6] if hasattr(bpy.ops, "export_anim") else "n/a"}
    return res


tryf("exporters", exporters)


# --- misc: shape keys, drivers, constraints, weight paint data paths ---------------
def misc():
    cube = bpy.data.objects.get("b1_cube")
    res = {}
    if cube is not None:
        try:
            sk0 = cube.shape_key_add(name="Basis", from_mix=False)
            sk1 = cube.shape_key_add(name="Smile", from_mix=False)
            sk1.data[0].co.z += 0.5
            sk1.value = 0.5
            res["shape_keys"] = [k.name for k in cube.data.shape_keys.key_blocks]
            res["shape_key_keyframe"] = sk1.keyframe_insert(data_path="value", frame=1)
        except Exception as e:
            res["shape_keys"] = err(e)
        try:
            fc = cube.driver_add("location", 2)
            fc.driver.type = "SCRIPTED"
            v = fc.driver.variables.new()
            v.name = "s"
            v.type = "SINGLE_PROP"
            v.targets[0].id = bpy.data.objects["b1_arm"]
            v.targets[0].data_path = 'pose.bones["root"].location[0]'
            fc.driver.expression = "s * 2"
            res["driver"] = {"type": fc.driver.type, "expression": fc.driver.expression,
                             "valid": fc.driver.is_valid, "drivers_count": len(cube.animation_data.drivers)}
        except Exception as e:
            res["driver"] = err(e)
    ao = bpy.data.objects["b1_arm"]
    try:
        pb = ao.pose.bones["child.L"]
        c = pb.constraints.new("COPY_ROTATION")
        c.target = ao
        c.subtarget = "root"
        res["constraint"] = {"type": c.type, "target": c.target.name, "subtarget": c.subtarget,
                             "types_sample": [t for t in ("COPY_LOCATION", "COPY_ROTATION", "IK", "DAMPED_TRACK",
                                                          "CHILD_OF", "LIMIT_ROTATION", "STRETCH_TO", "ARMATURE")]}
        ik = pb.constraints.new("IK")
        res["ik_attrs"] = [a for a in ("target", "subtarget", "pole_target", "pole_subtarget", "chain_count",
                                       "use_tail", "iterations", "pole_angle") if hasattr(ik, a)]
    except Exception as e:
        res["constraint"] = err(e)
    def op_exists(op):
        try:
            op.get_rna_type()
            return True
        except Exception:
            return False
    res["pose_ops"] = {n: op_exists(getattr(bpy.ops.pose, n)) for n in
                       ("armature_apply", "transforms_clear", "select_all", "copy", "paste", "loc_clear",
                        "rot_clear", "scale_clear", "user_transforms_clear", "visual_transform_apply")}
    res["armature_apply_kwargs"] = op_kwargs(bpy.ops.pose.armature_apply)
    res["object_vg_ops"] = {n: op_exists(getattr(bpy.ops.object, n)) for n in
                            ("vertex_group_normalize_all", "vertex_group_clean", "vertex_group_smooth",
                             "vertex_group_limit_total", "vertex_group_mirror", "vertex_group_remove_unused")}
    res["object_mode_set_modes"] = op_enum(bpy.ops.object.mode_set, "mode")
    res["armature_display_types"] = [i.identifier for i in bpy.types.Armature.bl_rna.properties["display_type"].enum_items]
    res["pose_position_enum"] = [i.identifier for i in bpy.types.Armature.bl_rna.properties["pose_position"].enum_items]
    res["vertex_group_add_signature"] = str(bpy.types.VertexGroup.bl_rna.functions["add"].parameters.keys())
    res["mode_after_all"] = bpy.context.mode
    return res


tryf("misc", misc)

print("B1CHECK " + json.dumps(out))
