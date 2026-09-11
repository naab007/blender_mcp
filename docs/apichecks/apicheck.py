import bpy
out = {}
arm = bpy.data.armatures.new("A")
out["collections"] = hasattr(arm, "collections")
out["collections_all"] = hasattr(arm, "collections_all")
out["addons"] = [k for k in bpy.context.preferences.addons.keys() if "rigify" in k or "bvh" in k]
import addon_utils
out["rigify_available"] = any(m.__name__ == "rigify" for m in addon_utils.modules())
out["bvh_available"] = any(m.__name__ == "io_anim_bvh" for m in addon_utils.modules())
fbx = bpy.ops.export_scene.fbx.get_rna_type().properties.keys()
out["fbx"] = [k for k in ("add_leaf_bones","bake_anim","bake_anim_simplify_factor","use_armature_deform_only","mesh_smooth_type","primary_bone_axis","secondary_bone_axis","apply_scale_options") if k in fbx]
gltf = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
out["gltf"] = [k for k in ("export_animations","export_skins","export_morph","export_def_bones") if k in gltf]
out["nla_bake_props"] = list(bpy.ops.nla.bake.get_rna_type().properties.keys())
out["symmetrize"] = list(bpy.ops.armature.symmetrize.get_rna_type().properties.keys())
out["vg_ops"] = [n for n in ("vertex_group_normalize_all","vertex_group_clean","vertex_group_smooth","vertex_group_limit_total") if hasattr(bpy.types, "OBJECT_OT_"+n)]
out["parent_set_types"] = [i.identifier for i in bpy.ops.object.parent_set.get_rna_type().properties["type"].enum_items]
pb = bpy.data.objects.new("AO", arm); bpy.context.scene.collection.objects.link(pb)
bpy.context.view_layer.objects.active = pb
bpy.ops.object.mode_set(mode="EDIT")
eb = arm.edit_bones.new("b"); eb.head=(0,0,0); eb.tail=(0,0,1)
bpy.ops.object.mode_set(mode="OBJECT")
out["pose_rotation_mode_default"] = pb.pose.bones["b"].rotation_mode
out["bone_collections_type"] = type(getattr(arm, "collections_all", None)).__name__
import json; print("APICHECK", json.dumps(out))
