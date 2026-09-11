import bpy, json
out = {}
out["vg_ops"] = {n: bpy.ops.object.__getattr__(n).get_rna_type().identifier for n in ("vertex_group_normalize_all","vertex_group_clean","vertex_group_smooth","vertex_group_limit_total")}
out["pose_armature_apply"] = bpy.ops.pose.armature_apply.get_rna_type().identifier
out["render_opengl"] = list(bpy.ops.render.opengl.get_rna_type().properties.keys())
print("APICHECK2", json.dumps(out))
