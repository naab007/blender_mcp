import bpy, json, addon_utils
out = {}
def props(op):
    try: return list(op.get_rna_type().properties.keys())[1:]
    except Exception as e: return f"MISSING: {e}"
def enum(op, prop):
    try: return [i.identifier for i in op.get_rna_type().properties[prop].enum_items]
    except Exception as e: return f"MISSING: {e}"
fbx = bpy.ops.export_scene.fbx.get_rna_type().properties
out["fbx_axis"] = [k for k in ("axis_forward","axis_up","global_scale","apply_unit_scale","apply_scale_options","use_space_transform","bake_space_transform","object_types","use_mesh_modifiers","mesh_smooth_type","use_tspace","use_triangles","use_custom_props","colors_type","prioritize_active_color","path_mode","embed_textures","batch_mode","use_batch_own_dir","use_metadata","bake_anim_use_all_actions","bake_anim_use_nla_strips","use_armature_deform_only","armature_nodetype","add_leaf_bones") if k in fbx]
out["fbx_apply_scale_options"] = [i.identifier for i in fbx["apply_scale_options"].enum_items]
out["fbx_object_types"] = [i.identifier for i in fbx["object_types"].enum_items]
out["fbx_smooth"] = [i.identifier for i in fbx["mesh_smooth_type"].enum_items]
gl = bpy.ops.export_scene.gltf.get_rna_type().properties
out["gltf"] = [k for k in ("export_format","export_apply","export_tangents","export_extras","export_yup","export_draco_mesh_compression_enable","export_texcoords","export_normals","export_colors","export_attributes","use_mesh_edges","export_hierarchy_flatten_objects","export_lights","export_cameras","use_selection","use_visible","use_active_collection","export_animation_mode","export_nla_strips","export_bake_animation","export_optimize_animation_size","export_anim_single_armature","export_reset_pose_bones","export_image_format","export_jpeg_quality","export_keep_originals","export_texture_dir","export_vertex_color","export_gn_mesh","export_shared_accessors","export_hierarchy_full_collections","export_extras","export_original_specular","export_unused_images","export_unused_textures","export_import_convert_lighting_mode","export_try_sparse_sk","will_save_settings","use_renderable") if k in gl]
out["gltf_anim_mode"] = [i.identifier for i in gl["export_animation_mode"].enum_items]
out["gltf_vertex_color"] = [i.identifier for i in gl["export_vertex_color"].enum_items] if "export_vertex_color" in gl else "MISSING"
out["decimate_mod"] = [p.identifier for p in bpy.types.DecimateModifier.bl_rna.properties if not p.identifier.startswith("rna")]
out["weighted_normal_mod"] = [p.identifier for p in bpy.types.WeightedNormalModifier.bl_rna.properties if not p.identifier.startswith("rna")]
out["triangulate_mod"] = [p.identifier for p in bpy.types.TriangulateModifier.bl_rna.properties if not p.identifier.startswith("rna")]
out["mesh.select_non_manifold"] = props(bpy.ops.mesh.select_non_manifold)
out["mesh.decimate"] = props(bpy.ops.mesh.decimate)
out["mesh.dissolve_limited"] = props(bpy.ops.mesh.dissolve_limited)
out["mesh.tris_convert_to_quads"] = props(bpy.ops.mesh.tris_convert_to_quads)
out["mesh.symmetrize"] = props(bpy.ops.mesh.symmetrize)
out["mesh.symmetry_snap"] = props(bpy.ops.mesh.symmetry_snap)
out["mesh.normals_make_consistent"] = props(bpy.ops.mesh.normals_make_consistent)
out["mesh.fill_holes"] = props(bpy.ops.mesh.fill_holes)
out["mesh.delete_loose"] = props(bpy.ops.mesh.delete_loose)
out["mesh.bisect"] = props(bpy.ops.mesh.bisect)
out["mesh.knife_project"] = props(bpy.ops.mesh.knife_project)
out["mesh.select_face_by_sides"] = props(bpy.ops.mesh.select_face_by_sides)
out["mesh.select_interior_faces"] = props(bpy.ops.mesh.select_interior_faces)
out["object.transform_apply"] = props(bpy.ops.object.transform_apply)
out["object.origin_set"] = props(bpy.ops.object.origin_set)
out["object.origin_set.type"] = enum(bpy.ops.object.origin_set, "type")
out["object.quadriflow_remesh"] = props(bpy.ops.object.quadriflow_remesh)
out["object.voxel_remesh"] = props(bpy.ops.object.voxel_remesh)
out["object.convert"] = props(bpy.ops.object.convert)
out["object.duplicates_make_real"] = props(bpy.ops.object.duplicates_make_real)
out["object.shade_auto_smooth"] = props(bpy.ops.object.shade_auto_smooth)
out["object.data_transfer"] = props(bpy.ops.object.data_transfer)
out["object.lod_add"] = props(getattr(bpy.ops.object, "lod_add", None)) if hasattr(bpy.ops.object, "lod_add") else "n/a"
out["print3d"] = [m.__name__ for m in addon_utils.modules() if "print" in m.__name__.lower()]
out["addons_all"] = sorted(m.__name__ for m in addon_utils.modules())
me = bpy.data.meshes.new("m")
out["mesh_methods"] = [a for a in ("calc_tangents","calc_loop_triangles","normals_split_custom_set","normals_split_custom_set_from_vertices","calc_normals_split","validate","calc_smooth_groups","shade_smooth","shade_flat","use_auto_smooth","has_custom_normals","polygon_normals","corner_normals","vertex_normals","attributes","transform","flip_normals","unit_test_compare") if hasattr(me, a)]
out["bmesh_ops"] = [a for a in ("convex_hull","remove_doubles","dissolve_limit","triangulate","join_triangles","recalc_face_normals","holes_fill","bisect_plane","symmetrize","mirror","inset_region","bevel","subdivide_edges","delete","split_edges","planar_faces","unsubdivide","beautify_fill","edgenet_fill","bridge_loops","weld_verts","poke","wireframe","solidify","offset_edgeloops","connect_verts","contextual_create","dissolve_degenerate","find_doubles","region_extend","scale","rotate","translate","transform","reverse_faces","smooth_vert","smooth_laplacian_vert","spin","create_uvsphere","create_cube") if hasattr(__import__("bmesh").ops, a)]
out["object_props"] = [a for a in ("display_type","display_bounds_type","show_bounds","instance_type","instance_collection","empty_display_type","empty_display_size","dimensions","bound_box","matrix_world","visible_camera","visible_shadow","hide_render","hide_viewport","color","pass_index","rigid_body","collision","use_dynamic_topology_sculpting") if hasattr(bpy.types.Object.bl_rna.properties, "keys") and a in bpy.types.Object.bl_rna.properties]
out["custom_props"] = True
out["scene_unit"] = [p.identifier for p in bpy.types.UnitSettings.bl_rna.properties if not p.identifier.startswith("rna")]
out["usd_export"] = props(bpy.ops.wm.usd_export)[:12]
out["obj_export"] = props(bpy.ops.wm.obj_export)
out["view_layer_stats"] = hasattr(bpy.context.scene, "statistics")
print("GDCHECK", json.dumps(out))
