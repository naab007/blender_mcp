import bpy, json, addon_utils
out = {}
ts = bpy.context.scene.tool_settings
out["tool_settings_snap"] = [p for p in ("use_snap","snap_elements","snap_target","snap_elements_base","use_snap_grid_absolute","snap_elements_individual") if p in ts.bl_rna.properties]
ov = bpy.types.View3DOverlay.bl_rna.properties
out["overlay"] = [p for p in ("show_wireframes","wireframe_threshold","wireframe_opacity","show_face_orientation","show_stats","show_bones","show_floor","show_axis_x","show_axis_y","show_axis_z","grid_scale","grid_subdivisions","show_overlays","show_extras","show_outline_selected","show_relationship_lines","show_cursor","show_text","show_annotation","normals_length","show_face_normals","show_vertex_normals","show_split_normals","show_edge_seams","show_edge_sharp","show_edge_crease","show_edge_bevel_weight","show_faces","show_weight","show_wpaint_contours","show_paint_wire","display_handle","show_bone_wire" ) if p in ov]
sh = bpy.types.View3DShading.bl_rna.properties
out["shading"] = [p for p in ("type","light","color_type","background_type","background_color","show_xray","xray_alpha","studio_light","show_object_outline","show_shadows","show_cavity","show_backface_culling","single_color","wireframe_color_type","use_dof","show_specular_highlight","render_pass","use_scene_lights","use_scene_world") if p in sh]
out["shading_type"] = [i.identifier for i in sh["type"].enum_items]
out["shading_light"] = [i.identifier for i in sh["light"].enum_items]
out["shading_color_type"] = [i.identifier for i in sh["color_type"].enum_items]
out["shading_bg"] = [i.identifier for i in sh["background_type"].enum_items]
out["legacy_tex_types"] = [i.identifier for i in bpy.types.Texture.bl_rna.properties["type"].enum_items]
out["noise_node_types"] = [i.identifier for i in bpy.types.ShaderNodeTexNoise.bl_rna.properties["noise_type"].enum_items] if "noise_type" in bpy.types.ShaderNodeTexNoise.bl_rna.properties else "no noise_type"
out["musgrave_node"] = hasattr(bpy.types, "ShaderNodeTexMusgrave")
ob = bpy.data.objects.new("o", bpy.data.meshes.new("m")); bpy.context.scene.collection.objects.link(ob)
out["asset_api"] = [a for a in ("asset_mark","asset_clear","asset_generate_preview","asset_data") if hasattr(ob, a)]
ob.asset_mark()
ad = ob.asset_data
out["asset_data_props"] = [p for p in ("catalog_id","tags","description","author","copyright","license","active_tag") if p in ad.bl_rna.properties]
try:
    ad.tags.new("t"); out["tags_new"] = True
except Exception as e: out["tags_new"] = str(e)
import inspect
out["libraries_write_doc"] = bpy.data.libraries.write.__doc__[:400] if bpy.data.libraries.write.__doc__ else "nodoc"
out["orphans_purge_doc"] = bpy.data.orphans_purge.__doc__[:300] if bpy.data.orphans_purge.__doc__ else "nodoc"
out["film_transparent"] = hasattr(bpy.context.scene.render, "film_transparent")
imgs = bpy.context.scene.render.image_settings
out["image_settings"] = [p for p in ("file_format","color_mode","color_depth","compression","quality") if p in imgs.bl_rna.properties]
out["bridge_edge_loops"] = list(bpy.ops.mesh.bridge_edge_loops.get_rna_type().properties.keys())[1:]
import bmesh
out["bmesh_subdivide_edgering"] = hasattr(bmesh.ops, "subdivide_edgering")
out["bmesh_bridge_loops_args"] = bmesh.ops.bridge_loops.__doc__[:300] if bmesh.ops.bridge_loops.__doc__ else "nodoc"
out["evaluated_get"] = hasattr(ob, "evaluated_get")
out["view_selected"] = list(bpy.ops.view3d.view_selected.get_rna_type().properties.keys())[1:]
out["view_axis"] = list(bpy.ops.view3d.view_axis.get_rna_type().properties.keys())[1:]
out["camera_to_view_selected"] = hasattr(bpy.ops.view3d, "camera_to_view_selected")
out["displace_mod"] = [p.identifier for p in bpy.types.DisplaceModifier.bl_rna.properties if p.identifier in ("texture","texture_coords","direction","strength","mid_level","vertex_group","space","texture_coords_object","uv_layer")]
out["smooth_by_angle_mod"] = [i.identifier for i in bpy.types.ObjectModifiers.bl_rna.functions["new"].parameters["type"].enum_items if "SMOOTH" in i.identifier or "NODES" in i.identifier]
# timbermesh
en = addon_utils.enable("timbermesh_blender_plugin", default_set=False)
out["timbermesh_enabled"] = en is not None
ops = []
for cat in ("export_scene","export_mesh","import_scene","import_mesh","wm","object","timbermesh"):
    mod = getattr(bpy.ops, cat, None)
    if mod is None: continue
    for name in dir(mod):
        if "timber" in name.lower():
            op = getattr(mod, name)
            try: ops.append({"op": f"{cat}.{name}", "props": list(op.get_rna_type().properties.keys())[1:]})
            except Exception as e: ops.append({"op": f"{cat}.{name}", "err": str(e)})
out["timbermesh_ops"] = ops
import sys
m = sys.modules.get("timbermesh_blender_plugin")
out["timbermesh_file"] = getattr(m, "__file__", None)
out["timbermesh_bl_info"] = getattr(m, "bl_info", None)
print("PCHECK", json.dumps(out, default=str))
