import bpy, json
out = {}
out["subclasses"] = [getattr(e, "bl_idname", e.__name__) for e in bpy.types.RenderEngine.__subclasses__()]
out["cycles_addon_enabled"] = "cycles" in bpy.context.preferences.addons.keys()
try:
    bpy.context.scene.render.engine = 'CYCLES'; out["set_cycles"] = bpy.context.scene.render.engine
except Exception as e:
    out["set_cycles"] = f"FAIL {e}"
out["static_enum"] = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
out["dynamic_enum"] = [i.identifier for i in bpy.context.scene.render.bl_rna.properties["engine"].enum_items]
print("CYC", json.dumps(out))
