import bpy, json, addon_utils
out = {}
def props(op):
    try: return list(op.get_rna_type().properties.keys())[1:]
    except Exception as e: return f"MISSING: {e}"
def enum(op, prop):
    try: return [i.identifier for i in op.get_rna_type().properties[prop].enum_items]
    except Exception as e: return f"MISSING: {e}"
out["uv.smart_project"] = props(bpy.ops.uv.smart_project)
out["uv.unwrap"] = props(bpy.ops.uv.unwrap)
out["uv.unwrap.method"] = enum(bpy.ops.uv.unwrap, "method")
out["uv.pack_islands"] = props(bpy.ops.uv.pack_islands)
out["uv.pack_islands.shape_method"] = enum(bpy.ops.uv.pack_islands, "shape_method")
out["uv.minimize_stretch"] = props(bpy.ops.uv.minimize_stretch)
out["uv.average_islands_scale"] = props(bpy.ops.uv.average_islands_scale)
out["uv.seams_from_islands"] = props(bpy.ops.uv.seams_from_islands)
out["uv.cube_project"] = props(bpy.ops.uv.cube_project)
out["uv.cylinder_project"] = props(bpy.ops.uv.cylinder_project)
out["uv.sphere_project"] = props(bpy.ops.uv.sphere_project)
out["uv.project_from_view"] = props(bpy.ops.uv.project_from_view)
out["uv.follow_active_quads"] = props(bpy.ops.uv.follow_active_quads)
out["uv.export_layout"] = props(bpy.ops.uv.export_layout)
out["uv.export_layout.mode"] = enum(bpy.ops.uv.export_layout, "mode")
out["uv.pin"] = props(bpy.ops.uv.pin)
out["uv.reset"] = props(bpy.ops.uv.reset)
out["mesh.mark_seam"] = props(bpy.ops.mesh.mark_seam)
out["mesh.uv_texture_add"] = props(bpy.ops.mesh.uv_texture_add)
out["object.bake"] = props(bpy.ops.object.bake)
out["object.bake.type"] = enum(bpy.ops.object.bake, "type")
out["object.bake.pass_filter"] = enum(bpy.ops.object.bake, "pass_filter")
out["object.bake.target"] = enum(bpy.ops.object.bake, "target")
out["render.bake_props"] = [p.identifier for p in bpy.context.scene.render.bake.bl_rna.properties][1:]
out["image.save_as"] = props(bpy.ops.image.save_as)
out["image.pack"] = props(bpy.ops.image.pack)
out["image.resize"] = props(bpy.ops.image.resize)
out["paint.project_image"] = props(bpy.ops.paint.project_image)
out["paint.image_from_view"] = props(bpy.ops.paint.image_from_view)
out["paint.add_texture_paint_slot"] = props(bpy.ops.paint.add_texture_paint_slot)
out["paint.add_texture_paint_slot.type"] = enum(bpy.ops.paint.add_texture_paint_slot, "type")
out["paint.vertex_color_set"] = props(bpy.ops.paint.vertex_color_set)
out["file.pack_all"] = props(bpy.ops.file.pack_all)
out["file.make_paths_relative"] = props(bpy.ops.file.make_paths_relative)
out["addons_enabled_uv"] = [k for k in bpy.context.preferences.addons.keys() if "uv" in k or "image" in k or "node" in k]
out["addon_modules_uv"] = [m.__name__ for m in addon_utils.modules() if "uv" in m.__name__.lower() or "node_wrangler" in m.__name__]
# data-level checks
me = bpy.data.meshes.new("m"); ob = bpy.data.objects.new("o", me); bpy.context.scene.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob
bpy.ops.object.mode_set(mode='OBJECT')
bm_ok = True
import bmesh
bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(me); bm.free()
uvl = me.uv_layers.new(name="UVMap")
out["uv_layer_attrs"] = [a for a in ("uv","data","active","active_render","pin","vertex_selection","edge_selection") if hasattr(uvl, a)]
out["uv_loop_has_uv"] = hasattr(uvl.data[0], "uv")
out["uv_layer.uv_foreach"] = hasattr(uvl.uv, "foreach_get")
out["color_attributes"] = hasattr(me, "color_attributes")
ca = me.color_attributes.new("Col", 'BYTE_COLOR', 'CORNER'); out["color_attr_domain"] = ca.domain
img = bpy.data.images.new("T", 64, 64, alpha=True, float_buffer=False)
out["image_attrs"] = [a for a in ("pixels","filepath","filepath_raw","source","tiles","colorspace_settings","packed_file","is_dirty","file_format","alpha_mode","generated_type") if hasattr(img, a)]
out["image.pixels_foreach"] = hasattr(img.pixels, "foreach_get")
out["image.save_render"] = hasattr(img, "save_render")
out["image.scale"] = hasattr(img, "scale")
out["image_source_enum"] = [i.identifier for i in img.bl_rna.properties["source"].enum_items]
out["tex_image_node_props"] = [a for a in ("image","interpolation","projection","extension","image_user") if hasattr(bpy.types.ShaderNodeTexImage.bl_rna, "properties") and a in bpy.types.ShaderNodeTexImage.bl_rna.properties]
out["uvmap_node"] = "ShaderNodeUVMap" in dir(bpy.types)
out["principled_inputs"] = [s.name for s in bpy.data.materials.new("x").node_tree.nodes.new("ShaderNodeBsdfPrincipled").inputs] if False else None
mat = bpy.data.materials.new("pm"); mat.use_nodes=True
out["principled_inputs"] = [s.name for s in mat.node_tree.nodes["Principled BSDF"].inputs]
out["cycles_device"] = hasattr(bpy.context.scene, "cycles")
out["render_engines"] = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
out["uv_editor_area"] = "IMAGE_EDITOR" in [i.identifier for i in bpy.types.Area.bl_rna.properties["type"].enum_items]
out["seam_attr"] = hasattr(me.edges[0], "use_seam")
print("UVCHECK", json.dumps(out))
