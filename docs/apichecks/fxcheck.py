import bpy, json, addon_utils
out = {}
def props(op):
    try: return [k for k in list(op.get_rna_type().properties.keys())[1:] if not k.startswith("filter_")]
    except Exception as e: return f"MISSING: {e}"
def enum(t, prop):
    try: return [i.identifier for i in t.bl_rna.properties[prop].enum_items]
    except Exception as e: return f"MISSING: {e}"
def rprops(t, names): return [n for n in names if n in t.bl_rna.properties]
def opennum(op, prop): return [i.identifier for i in op.get_rna_type().properties[prop].enum_items]

for n in ("quick_smoke","quick_liquid","quick_explode","quick_fur"):
    out["object."+n] = props(getattr(bpy.ops.object, n))
out["fluid_ops"] = [n for n in ("bake_all","bake_data","bake_mesh","bake_noise","bake_particles","bake_guides","free_all","free_data","free_mesh","free_noise","free_particles","pause_bake","preset_add") if hasattr(bpy.ops.fluid, n)]
out["fluid_type"] = enum(bpy.types.FluidModifier, "fluid_type")
D = bpy.types.FluidDomainSettings
out["domain_type"] = enum(D, "domain_type"); out["cache_type"] = enum(D, "cache_type"); out["cache_data_format"] = enum(D, "cache_data_format"); out["cache_mesh_format"] = enum(D, "cache_mesh_format")
out["domain_props"] = rprops(D, ["resolution_max","use_adaptive_domain","cache_directory","cache_frame_start","cache_frame_end","cache_resumable","use_mesh","mesh_scale","use_spray_particles","use_foam_particles","use_bubble_particles","use_noise","noise_scale","cfl_condition","time_scale","gravity","use_dissolve_smoke","dissolve_speed","vorticity","burning_rate","flame_smoke","flame_vorticity","flame_max_temp","use_viscosity","viscosity_value","surface_tension","particle_maximum","particle_minimum","particle_radius","use_fractions","timesteps_min","timesteps_max","use_diffusion","has_cache_baked_data","has_cache_baked_mesh","is_cache_baking_any","export_manta_script","use_guide","openvdb_cache_compress_type","openvdb_data_depth","use_speed_vectors","use_flip_particles"])
F = bpy.types.FluidFlowSettings
out["flow_type"] = enum(F, "flow_type"); out["flow_behavior"] = enum(F, "flow_behavior"); out["flow_source"] = enum(F, "flow_source")
out["flow_props"] = rprops(F, ["use_inflow","use_initial_velocity","velocity_factor","velocity_normal","velocity_coord","velocity_random","surface_distance","density","temperature","fuel_amount","smoke_color","subframes","particle_size","use_particle_size","particle_system","density_vertex_group","use_plane_init","use_absolute","volume_density","texture","use_texture","noise_map_size","texture_map_type","texture_offset","texture_size","uv_layer"])
E = bpy.types.FluidEffectorSettings
out["effector_type"] = enum(E, "effector_type"); out["effector_props"] = rprops(E, ["use_effector","use_plane_init","surface_distance","subframes","guide_mode","velocity_factor"])
C = bpy.types.ClothSettings
out["cloth_props"] = rprops(C, ["quality","time_scale","mass","air_damping","tension_stiffness","compression_stiffness","shear_stiffness","bending_stiffness","tension_damping","bending_damping","use_pressure","uniform_pressure_force","use_internal_springs","vertex_group_mass","pin_stiffness","use_dynamic_mesh","shrink_min","use_sewing_springs","sewing_force_max","bending_model","gravity","effector_weights","rest_shape_key","vertex_group_shrink","fluid_density","target_volume"])
out["cloth_collision"] = rprops(bpy.types.ClothCollisionSettings, ["use_collision","distance_min","collision_quality","use_self_collision","self_distance_min","friction","impulse_clamp","collection","vertex_group_object_collisions","vertex_group_self_collisions"])
out["ptcache_ops"] = [n for n in ("bake","bake_all","free_bake","free_bake_all","bake_from_cache","add","remove") if hasattr(bpy.ops.ptcache, n)]
out["ptcache.bake"] = props(bpy.ops.ptcache.bake)
out["point_cache_props"] = rprops(bpy.types.PointCache, ["frame_start","frame_end","frame_step","is_baked","is_baking","is_outdated","use_disk_cache","filepath","name","info","use_library_path","compression"])
out["rigidbody_ops"] = [n for n in ("object_add","object_remove","objects_add","world_add","world_remove","bake_to_keyframes","shape_change","mass_calculate","constraint_add","connect") if hasattr(bpy.ops.rigidbody, n)]
out["rigidbody.bake_to_keyframes"] = props(bpy.ops.rigidbody.bake_to_keyframes)
out["rb_type"] = enum(bpy.types.RigidBodyObject, "type"); out["rb_shape"] = enum(bpy.types.RigidBodyObject, "collision_shape"); out["rb_mesh_source"] = enum(bpy.types.RigidBodyObject, "mesh_source")
out["rb_props"] = rprops(bpy.types.RigidBodyObject, ["mass","friction","restitution","use_margin","collision_margin","linear_damping","angular_damping","kinematic","enabled","use_deactivation","use_start_deactivated","collision_collections","use_deform"])
out["rb_world_props"] = rprops(bpy.types.RigidBodyWorld, ["enabled","time_scale","substeps_per_frame","solver_iterations","use_split_impulse","point_cache","collection","constraints","effector_weights"])
out["rb_constraint_type"] = enum(bpy.types.RigidBodyConstraint, "type")
out["softbody_props"] = rprops(bpy.types.SoftBodySettings, ["friction","mass","speed","goal_default","goal_spring","goal_friction","use_goal","vertex_group_goal","pull","push","damping","plastic","bend","spring_length","use_edges","use_stiff_quads","use_self_collision","ball_size","ball_stiff","ball_damp","step_min","step_max","use_auto_step","error_threshold","choke","fuzzy","collision_collection","use_estimate_matrix","gravity"])
out["particle_ops"] = [n for n in ("particle_system_add","particle_system_remove","duplicates_make_real") if hasattr(bpy.ops.object, n)]
P = bpy.types.ParticleSettings
out["ps_type"] = enum(P, "type"); out["ps_physics"] = enum(P, "physics_type"); out["ps_render"] = enum(P, "render_type"); out["ps_emit_from"] = enum(P, "emit_from"); out["ps_rotation_mode"] = enum(P, "rotation_mode")
out["ps_props"] = rprops(P, ["count","frame_start","frame_end","lifetime","lifetime_random","emit_from","use_emit_random","normal_factor","object_align_factor","object_factor","factor_random","particle_size","size_random","mass","effector_weights","instance_object","instance_collection","use_collection_pick_random","use_rotations","rotation_mode","rotation_factor_random","phase_factor","angular_velocity_mode","angular_velocity_factor","use_dynamic_rotation","use_die_on_collision","collision_collection","timestep","subframes","use_adaptive_subframes","hair_length","hair_step","child_type","child_percent","rendered_child_count","display_method","display_percentage","use_dead","trail_count","keyed_loops","boids","fluid","damping","drag_factor","brownian_factor","use_modifier_stack","render_step","use_even_distribution","jitter_factor","distribution","grid_resolution","use_scale_instance","use_global_instance","use_rotation_instance","use_whole_collection","integrator","kink","clump_factor","material","material_slot","size","texture_slots","distribution"])
out["psys_props"] = rprops(bpy.types.ParticleSystem, ["name","settings","seed","point_cache","particles","vertex_group_density","vertex_group_length","use_hair_dynamics","cloth","is_edited","child_seed","parent","target_object","use_keyed_timing"])
out["particle_props"] = rprops(bpy.types.Particle, ["location","velocity","rotation","size","alive_state","birth_time","die_time","lifetime","is_exist","is_visible","prev_location","hair_keys"])
out["effector_add_types"] = opennum(bpy.ops.object.effector_add, "type")
out["field_props"] = rprops(bpy.types.FieldSettings, ["type","strength","flow","noise","seed","shape","falloff_type","falloff_power","use_max_distance","distance_max","use_min_distance","distance_min","inflow","size","rest_length","use_radial_min","use_radial_max","radial_falloff","use_absorption","apply_to_location","apply_to_rotation","use_gravity_falloff","wind_factor","guide_free","guide_minimum","texture","texture_mode","use_object_coords","use_2d_force","linear_drag","quadratic_drag","harmonic_damping","z_direction"])
out["field_type"] = enum(bpy.types.FieldSettings, "type"); out["field_shape"] = enum(bpy.types.FieldSettings, "shape")
out["effector_weights"] = rprops(bpy.types.EffectorWeights, ["gravity","all","force","vortex","magnetic","wind","curve_guide","harmonic","charge","lennardjones","texture","boid","turbulence","drag","smokeflow","collection","apply_to_hair_growing"])
out["dpaint_ops"] = [n for n in ("bake","surface_slot_add","surface_slot_remove","type_toggle","output_toggle") if hasattr(bpy.ops.dpaint, n)]
out["dpaint_surface_type"] = enum(bpy.types.DynamicPaintSurface, "surface_type"); out["dpaint_format"] = enum(bpy.types.DynamicPaintSurface, "surface_format"); out["dpaint_init"] = enum(bpy.types.DynamicPaintSurface, "init_color_type")
out["dpaint_surface_props"] = rprops(bpy.types.DynamicPaintSurface, ["name","frame_start","frame_end","frame_substeps","image_output_path","image_resolution","image_fileformat","uv_layer","output_name_a","output_name_b","use_output_a","use_output_b","use_dissolve","dissolve_speed","use_drying","dry_speed","use_spread","spread_speed","use_drip","use_shrink","wave_speed","wave_damping","wave_spring","wave_smoothness","use_wave_open_border","brush_collection","init_color","init_layername","effector_weights","is_active","is_cache_user","use_antialiasing","displace_type","depth_clamp","displace_factor","use_incremental_displace","point_cache"])
out["dpaint_brush_props"] = rprops(bpy.types.DynamicPaintBrushSettings, ["paint_color","paint_alpha","paint_wetness","paint_source","paint_distance","use_proximity_project","ray_direction","proximity_falloff","use_smudge","smudge_strength","use_absolute_alpha","use_paint_erase","use_velocity_alpha","use_velocity_color","use_velocity_depth","max_velocity","wave_type","wave_factor","wave_clamp","invert_proximity","use_negative_volume","use_particle_radius","solid_radius","smooth_radius","particle_system"])
out["ocean_props"] = rprops(bpy.types.OceanModifier, ["geometry_mode","repeat_x","repeat_y","time","depth","random_seed","resolution","viewport_resolution","spatial_size","wind_velocity","damping","use_foam","foam_coverage","foam_layer_name","use_spray","spray_layer_name","choppiness","wave_scale","wave_scale_min","wave_alignment","wave_direction","use_normals","is_cached","frame_start","frame_end","bake_foam_fade","filepath","spectrum","fetch_jonswap","sharpen_peak_jonswap","invert_spray","size"])
out["ocean_bake"] = props(bpy.ops.object.ocean_bake)
out["wave_props"] = rprops(bpy.types.WaveModifier, ["use_x","use_y","use_cyclic","use_normal","time_offset","lifetime","damping_time","falloff_radius","start_position_x","start_position_y","speed","height","width","narrowness","vertex_group","texture","texture_coords","start_position_object"])
out["explode_props"] = rprops(bpy.types.ExplodeModifier, ["vertex_group","protect","use_edge_cut","show_unborn","show_alive","show_dead","use_size","particle_uv"])
out["meshcache_props"] = rprops(bpy.types.MeshCacheModifier, ["cache_format","filepath","factor","deform_mode","interpolation","time_mode","play_mode","frame_start","frame_scale","eval_frame","eval_time","eval_factor","forward_axis","up_axis","flip_axis"])
out["meshseqcache_props"] = rprops(bpy.types.MeshSequenceCacheModifier, ["cache_file","object_path","read_data","use_vertex_interpolation","velocity_scale"])
out["alembic_export"] = [p for p in props(bpy.ops.wm.alembic_export) if not p.startswith(("filemode","display_type","sort_method","check_existing","hide_props"))]
out["alembic_import"] = [p for p in props(bpy.ops.wm.alembic_import) if not p.startswith(("filemode","display_type","sort_method","check_existing","hide_props"))]
out["usd_export"] = [p for p in props(bpy.ops.wm.usd_export) if not p.startswith(("filemode","display_type","sort_method","check_existing","hide_props"))]
out["gn_bake_ops"] = [n for n in ("simulation_nodes_cache_bake","simulation_nodes_cache_calculate_to_frame","simulation_nodes_cache_delete","geometry_node_bake_single","geometry_node_bake_delete_single","geometry_nodes_move_to_nodes") if hasattr(bpy.ops.object, n)]
out["modifier_apply_as_shapekey"] = props(bpy.ops.object.modifier_apply_as_shapekey)
out["new_from_object"] = hasattr(bpy.data.meshes, "new_from_object")
out["render_passes"] = rprops(bpy.types.ViewLayer, ["use_pass_vector","use_pass_normal","use_pass_z","use_pass_emit","use_pass_combined","use_pass_mist","use_pass_position"])
out["curves_ops"] = [n for n in dir(bpy.ops.curves) if not n.startswith("_")]
out["addons_fx"] = [m.__name__ for m in addon_utils.modules() if any(k in m.__name__.lower() for k in ("fracture","cell","physics","bool","extra"))]
out["motion_path"] = props(bpy.ops.object.paths_calculate)
out["convert_target"] = opennum(bpy.ops.object.convert, "target")
out["volume_types"] = "Volume" in dir(bpy.types) and hasattr(bpy.data, "volumes")
out["vdb_import"] = props(bpy.ops.object.volume_import)
print("FXCHECK", json.dumps(out, default=str))
