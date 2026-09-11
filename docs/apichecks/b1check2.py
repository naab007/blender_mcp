# Headless facts for Phase B1 (rigging and animation), part 2: the PLAN.md 10.5 list.
# Runs on 4.x and 5.x. Prints one line: B1CHECK2 {json}. ASCII only. Never imports addon.py.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\b1check2.py 2>&1 | findstr B1CHECK2
import bpy
import json
import sys
import inspect
import math

out = {"version": bpy.app.version_string, "python": sys.version.split()[0]}


def tryf(key, fn):
    try:
        out[key] = fn()
    except Exception as e:
        out[key] = "Error: " + str(e).strip()[:200]


def err(e):
    return "Error: " + str(e).strip()[:140]


def rna_func_params(struct, fname):
    try:
        f = struct.bl_rna.functions[fname]
        return [(p.identifier, p.type, getattr(p, "is_required", None)) for p in f.parameters]
    except Exception as e:
        return err(e)


def build_rig():
    arm = bpy.data.armatures.new("b1b_arm")
    ao = bpy.data.objects.new("b1b_arm", arm)
    bpy.context.scene.collection.objects.link(ao)
    bpy.context.view_layer.objects.active = ao
    ao.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = arm.edit_bones.new("root")
    root.head, root.tail = (0, 0, 0), (0, 0, 1)
    child = arm.edit_bones.new("child")
    child.head, child.tail = (0, 0, 1), (0, 0, 2)
    child.parent = root
    child.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    return ao


# --- 1. action slots: creation and auto-pick ---------------------------------------
def slots():
    ao = build_rig()
    res = {"ActionSlots_new_params": rna_func_params(bpy.types.ActionSlots, "new") if hasattr(bpy.types, "ActionSlots") else "no ActionSlots type"}
    act = bpy.data.actions.new("b1b_fresh")
    res["fresh_action_attrs"] = {a: hasattr(act, a) for a in ("slots", "layers", "fcurves", "id_root")}
    ad = ao.animation_data_create()
    ad.action = act
    res["assign_fresh_no_slots"] = {"action_slot": (ad.action_slot.identifier if getattr(ad, "action_slot", None) else None),
                                    "suitable": [s.identifier for s in ad.action_suitable_slots] if hasattr(ad, "action_suitable_slots") else "n/a"}
    if hasattr(act, "slots"):
        try:
            slot = act.slots.new(id_type="OBJECT", name="b1b_arm")
            res["slots_new"] = {"identifier": slot.identifier, "name_display": getattr(slot, "name_display", None),
                                "target_id_type": slot.target_id_type}
            res["after_slots_new"] = {"action_slot": (ad.action_slot.identifier if ad.action_slot else None),
                                      "suitable": [s.identifier for s in ad.action_suitable_slots]}
            ad.action_slot = ad.action_suitable_slots[0]
            res["after_explicit_assign"] = ad.action_slot.identifier
        except Exception as e:
            res["slots_new"] = err(e)
    # keyframe on a fresh action with explicit slot: does the key land in that slot's channelbag?
    pb = ao.pose.bones["child"]
    pb.rotation_mode = "XYZ"
    pb.keyframe_insert(data_path="rotation_euler", frame=1)
    res["slot_after_keyframe"] = (ad.action_slot.identifier if getattr(ad, "action_slot", None) else "n/a")
    res["action_after_keyframe"] = ad.action.name
    return res


tryf("slots", slots)


# --- 2. write path: fcurves.new / keyframe_points.insert / interpolation enums --------
def write_path():
    ao = bpy.data.objects["b1b_arm"]
    ad = ao.animation_data
    act = ad.action
    res = {}
    try:
        from bpy_extras import anim_utils
        if hasattr(anim_utils, "action_ensure_channelbag_for_slot"):
            cb = anim_utils.action_ensure_channelbag_for_slot(act, ad.action_slot)
            coll = cb.fcurves
            res["path"] = "channelbag"
            res["ActionChannelbagFCurves_new_params"] = rna_func_params(type(coll), "new")
        else:
            coll = act.fcurves
            res["path"] = "action.fcurves"
            res["ActionFCurves_new_params"] = rna_func_params(type(coll), "new")
    except Exception as e:
        return err(e)
    try:
        fc = coll.new('pose.bones["child"].location', index=2, action_group="child")
        res["fcurves_new"] = {"data_path": fc.data_path, "index": fc.array_index, "group": fc.group.name if fc.group else None}
    except TypeError as e:
        res["fcurves_new_action_group_kw"] = err(e)
        fc = coll.new('pose.bones["child"].location', index=2)
        res["fcurves_new"] = {"data_path": fc.data_path, "index": fc.array_index}
    kp = fc.keyframe_points.insert(1, 0.0)
    kp2 = fc.keyframe_points.insert(10, 2.0)
    res["keyframe_points_insert"] = [round(kp.co[0]), round(kp2.co[0]), len(fc.keyframe_points)]
    res["KeyframePoints_insert_params"] = rna_func_params(type(fc.keyframe_points), "insert")
    res["interpolation_enum"] = [i.identifier for i in bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items]
    res["easing_enum"] = [i.identifier for i in bpy.types.Keyframe.bl_rna.properties["easing"].enum_items]
    kp2.interpolation = "LINEAR"
    kp2.easing = "EASE_IN_OUT"
    res["set_interp"] = [kp2.interpolation, kp2.easing]
    res["fcurve_find_after"] = coll.find('pose.bones["child"].location', index=2) is not None
    res["fcurve_remove"] = rna_func_params(type(coll), "remove")
    res["keyframe_points_remove_params"] = rna_func_params(type(fc.keyframe_points), "remove")
    res["fcurve_update"] = hasattr(fc, "update")
    res["fcurve_evaluate_frame10"] = round(fc.evaluate(10), 3)
    bpy.context.scene.frame_set(10)
    res["pose_z_at_10"] = round(ao.pose.bones["child"].location[2], 3)
    return res


tryf("write_path", write_path)


# --- 3. NLA tracks and strips --------------------------------------------------------
def nla():
    ao = bpy.data.objects["b1b_arm"]
    ad = ao.animation_data
    act = ad.action
    res = {"NlaTracks_new_params": rna_func_params(bpy.types.NlaTracks, "new"),
           "NlaStrips_new_params": rna_func_params(bpy.types.NlaStrips, "new")}
    try:
        track = ad.nla_tracks.new()
        track.name = "b1b_track"
        strip = track.strips.new("b1b_strip", 1, act)
        res["strip"] = {"name": strip.name, "frame_start": strip.frame_start, "frame_end": strip.frame_end,
                        "action": strip.action.name, "extrapolation": strip.extrapolation, "blend_in": strip.blend_in,
                        "blend_out": strip.blend_out, "repeat": strip.repeat, "blend_type": strip.blend_type,
                        "has_action_slot": hasattr(strip, "action_slot")}
        res["extrapolation_enum"] = [i.identifier for i in strip.bl_rna.properties["extrapolation"].enum_items]
        res["blend_type_enum"] = [i.identifier for i in strip.bl_rna.properties["blend_type"].enum_items]
        if hasattr(strip, "action_slot"):
            res["strip_action_slot"] = strip.action_slot.identifier if strip.action_slot else None
        res["tracks"] = [(t.name, len(t.strips), t.mute, t.is_solo) for t in ad.nla_tracks]
        # push down: assign a fresh action then move it to a track by API (no operator)
        res["push_down_op_kwargs"] = [p.identifier for p in bpy.ops.nla.action_pushdown.get_rna_type().properties if p.identifier != "rna_type"]
        ad.nla_tracks.remove(track)
        res["removed"] = len(ad.nla_tracks)
    except Exception as e:
        res["strip"] = err(e)
    return res


tryf("nla", nla)


# --- 4. get_pose world math -----------------------------------------------------------
def pose_math():
    ao = bpy.data.objects["b1b_arm"]
    ao.location = (1, 2, 3)
    pb = ao.pose.bones["child"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.view_layer.update()
    mw = ao.matrix_world
    res = {"pb_head_pose_space": [round(v, 3) for v in pb.head], "pb_tail_pose_space": [round(v, 3) for v in pb.tail],
           "head_world": [round(v, 3) for v in (mw @ pb.head)], "tail_world": [round(v, 3) for v in (mw @ pb.tail)],
           "matrix_world_head": [round(v, 3) for v in (mw @ pb.matrix).translation],
           "matrix_basis_euler_deg": [round(math.degrees(a), 1) for a in pb.matrix_basis.to_euler("XYZ")],
           "rotation_euler_deg": [round(math.degrees(a), 1) for a in pb.rotation_euler],
           "rotation_quaternion": [round(v, 3) for v in pb.rotation_quaternion],
           "bone_matrix_local_attrs": [a for a in ("matrix_local", "matrix", "head_local", "tail_local", "length", "x_axis") if hasattr(pb.bone, a)],
           "pb_attrs": [a for a in ("matrix", "matrix_basis", "matrix_channel", "head", "tail", "length", "x_axis", "y_axis", "z_axis", "custom_shape", "bbone_segments") if hasattr(pb, a)]}
    ao.location = (0, 0, 0)
    pb.rotation_euler = (0, 0, 0)
    return res


tryf("pose_math", pose_math)


# --- 5. WEIGHT_PAINT headless, vertex group ops ---------------------------------------
def weight_paint():
    ao = bpy.data.objects["b1b_arm"]
    bpy.ops.mesh.primitive_cylinder_add(vertices=8, depth=2, location=(0, 0, 1))
    cube = bpy.context.active_object
    cube.name = "b1b_mesh"
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name in ("b1b_mesh", "b1b_arm"))
    bpy.context.view_layer.objects.active = ao
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    res = {"groups": [vg.name for vg in cube.vertex_groups]}
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name == "b1b_mesh")
    bpy.context.view_layer.objects.active = cube
    try:
        r = bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        res["mode_set_weight_paint"] = {"result": sorted(r), "mode": bpy.context.mode}
        cube.vertex_groups.active_index = 1
        res["active_group"] = cube.vertex_groups.active.name
        for name, fn in (("smooth", lambda: bpy.ops.object.vertex_group_smooth(factor=0.5, repeat=1, group_select_mode="ALL")),
                         ("normalize_all", lambda: bpy.ops.object.vertex_group_normalize_all(lock_active=False)),
                         ("clean", lambda: bpy.ops.object.vertex_group_clean(limit=0.001, keep_single=True, group_select_mode="ALL")),
                         ("limit_total", lambda: bpy.ops.object.vertex_group_limit_total(limit=4, group_select_mode="ALL"))):
            try:
                res["op_" + name + "_in_weight_paint"] = sorted(fn())
            except Exception as e:
                res["op_" + name + "_in_weight_paint"] = err(e)
        res["group_select_mode_enum"] = [i.identifier for i in bpy.ops.object.vertex_group_smooth.get_rna_type().properties["group_select_mode"].enum_items]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    # same ops in OBJECT mode
    for name, fn in (("normalize_all", lambda: bpy.ops.object.vertex_group_normalize_all(lock_active=False)),
                     ("clean", lambda: bpy.ops.object.vertex_group_clean(limit=0.001, keep_single=True, group_select_mode="ALL")),
                     ("limit_total", lambda: bpy.ops.object.vertex_group_limit_total(limit=4, group_select_mode="ALL")),
                     ("smooth", lambda: bpy.ops.object.vertex_group_smooth(factor=0.5, repeat=1, group_select_mode="ALL"))):
        try:
            res["op_" + name + "_in_object"] = sorted(fn())
        except Exception as e:
            res["op_" + name + "_in_object"] = err(e)
    # weight read semantics
    vg = cube.vertex_groups[0]
    try:
        vg.weight(10 ** 6)
    except Exception as e:
        res["weight_missing_vertex"] = err(e)
    res["weight_read"] = round(vg.weight(0), 4) if any(g.group == vg.index for g in cube.data.vertices[0].groups) else "vertex 0 not in group 0"
    # data transfer modifier for vertex groups
    try:
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 1))
        dst = bpy.context.active_object
        dst.name = "b1b_dst"
        mod = dst.modifiers.new("DT", "DATA_TRANSFER")
        mod.object = cube
        mod.use_vert_data = True
        mod.data_types_verts = {"VGROUP_WEIGHTS"}
        mod.vert_mapping = "NEAREST"
        mod.use_object_transform = True
        res["data_transfer"] = {"vert_mapping_enum": [i.identifier for i in mod.bl_rna.properties["vert_mapping"].enum_items][:8],
                                "data_types_verts": sorted(mod.data_types_verts),
                                "layers_vgroup_select_src": mod.layers_vgroup_select_src}
        bpy.context.view_layer.objects.active = dst
        r = bpy.ops.object.datalayout_transfer(modifier=mod.name)
        res["data_transfer"]["datalayout_transfer"] = sorted(r)
        r = bpy.ops.object.modifier_apply(modifier=mod.name)
        res["data_transfer"]["apply"] = sorted(r)
        res["data_transfer"]["dst_groups"] = [vg.name for vg in dst.vertex_groups]
    except Exception as e:
        res["data_transfer"] = err(e)
    res["mode_after"] = bpy.context.mode
    return res


tryf("weight_paint", weight_paint)


# --- 6. Rigify import path and a real generate ---------------------------------------
def rigify():
    import addon_utils
    errs = []
    mod = addon_utils.enable("rigify", default_set=True, handle_error=lambda e: errs.append(str(e)[:160]))
    res = {"enabled": mod is not None, "errs": errs}
    if mod is None:
        return res
    try:
        from rigify import generate
        from rigify.utils.rig import get_rigify_target_rig
        res["imports"] = ["rigify.generate.generate_rig" if hasattr(generate, "generate_rig") else "no generate_rig",
                          "get_rigify_target_rig"]
        res["generate_rig_sig"] = str(inspect.signature(generate.generate_rig))
    except Exception as e:
        res["imports"] = err(e)
    try:
        r = bpy.ops.object.armature_human_metarig_add()
        meta = bpy.context.active_object
        res["metarig_add"] = {"result": sorted(r), "name": meta.name, "bones": len(meta.data.bones)}
        res["metarig_ops"] = [n for n in ("armature_human_metarig_add", "armature_basic_human_metarig_add",
                                          "armature_basic_quadruped_metarig_add", "armature_bird_metarig_add",
                                          "armature_cat_metarig_add", "armature_horse_metarig_add",
                                          "armature_shark_metarig_add", "armature_wolf_metarig_add")
                              if getattr(bpy.ops.object, n).get_rna_type() is not None]
        bpy.context.view_layer.objects.active = meta
        try:
            generate.generate_rig(bpy.context, meta)
            rig = get_rigify_target_rig(meta.data)
            res["generate"] = {"rig": rig.name if rig else None, "bones": len(rig.data.bones) if rig else None,
                               "mode_after": bpy.context.mode}
        except Exception as e:
            res["generate"] = err(e)
        finally:
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
    except Exception as e:
        res["metarig_add"] = err(e)
    addon_utils.disable("rigify", default_set=True)
    res["disabled"] = "rigify" not in bpy.context.preferences.addons.keys()
    return res


tryf("rigify", rigify)


# --- 7. anim_utils.bake_action options ---------------------------------------------------
def bake_action_api():
    from bpy_extras import anim_utils
    res = {"bake_action_sig": str(inspect.signature(anim_utils.bake_action))}
    try:
        res["BakeOptions_sig"] = str(inspect.signature(anim_utils.BakeOptions))[:400]
    except Exception as e:
        res["BakeOptions_sig"] = err(e)
    ao = bpy.data.objects["b1b_arm"]
    try:
        opts = anim_utils.BakeOptions(only_selected=False, do_pose=True, do_object=False, do_visual_keying=True,
                                      do_constraint_clear=False, do_parents_clear=False, do_clean=False,
                                      do_location=True, do_rotation=True, do_scale=True, do_bbone=False,
                                      do_custom_props=False)
        act = anim_utils.bake_action(ao, action=None, frames=range(1, 5), bake_options=opts)
        res["bake_action_call"] = {"action": act.name if act else None}
    except Exception as e:
        res["bake_action_call"] = err(e)
    return res


tryf("bake_action_api", bake_action_api)

print("B1CHECK2 " + json.dumps(out))
