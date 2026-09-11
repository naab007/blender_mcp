import bpy, json
out = {}
ob = bpy.data.objects.new("o", bpy.data.meshes.new("m")); bpy.context.scene.collection.objects.link(ob)
out["session_uid"] = getattr(ob, "session_uid", None)
out["session_uid_stable_after_rename"] = (lambda u: (ob.__setattr__("name","renamed"), ob.session_uid == u)[1])(ob.session_uid)
out["as_pointer"] = hasattr(ob, "as_pointer")
out["msgbus"] = [a for a in ("subscribe_rna","publish_rna","clear_by_owner") if hasattr(bpy.msgbus, a)]
out["handlers"] = [h for h in dir(bpy.app.handlers) if not h.startswith("_") and h not in ("persistent",)]
out["undo_push"] = list(bpy.ops.ed.undo_push.get_rna_type().properties.keys())[1:]
out["undo"] = bpy.ops.ed.undo.get_rna_type().identifier
out["rna_introspect"] = {"props": len(bpy.types.Object.bl_rna.properties), "funcs": [f.identifier for f in bpy.types.Object.bl_rna.functions][:6]}
out["op_introspect"] = {"desc": bpy.ops.mesh.bevel.get_rna_type().description[:60], "props": [(p.identifier, p.type) for p in bpy.ops.mesh.bevel.get_rna_type().properties][1:5]}
out["path_resolve"] = ob.path_resolve("location").__class__.__name__
out["path_from_id"] = bpy.context.scene.render.path_from_id("resolution_x")
out["depsgraph_updates"] = hasattr(bpy.types.Depsgraph, "updates")
out["timers"] = hasattr(bpy.app.timers, "register")
print("CDP", json.dumps(out, default=str))
