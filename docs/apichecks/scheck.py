import bpy, json, addon_utils
out = {}
def props(op):
    try: return [k for k in op.get_rna_type().properties.keys()[1:] if not k.startswith("filter_")]
    except Exception as e: return f"MISSING: {e}"
for name in ("save_mainfile","save_as_mainfile","open_mainfile","revert_mainfile","recover_last_session","recover_auto_save","read_homefile","save_homefile","read_factory_settings","save_userpref","read_userpref","read_factory_userpref","append","link","previews_batch_generate","quit_blender","window_new","save_as_mainfile"):
    out["wm."+name] = props(getattr(bpy.ops.wm, name))
out["script.execute_preset"] = props(bpy.ops.script.execute_preset)
out["render.preset_add"] = props(bpy.ops.render.preset_add)
out["preset_paths_render"] = bpy.utils.preset_paths("render")
out["preset_paths_cycles"] = bpy.utils.preset_paths("cycles/sampling")
out["data_flags"] = {"is_dirty": bpy.data.is_dirty, "is_saved": bpy.data.is_saved, "filepath": bpy.data.filepath, "version": list(bpy.data.version), "use_autopack": bpy.data.use_autopack}
out["app"] = {"tempdir": bpy.app.tempdir, "version": bpy.app.version_string, "autoexec_fail": bpy.app.autoexec_fail, "background": bpy.app.background}
pf = bpy.context.preferences.filepaths
out["prefs.filepaths"] = [p.identifier for p in pf.bl_rna.properties if not p.identifier.startswith("rna")]
out["prefs.view"] = [p for p in ("show_splash","show_tooltips","ui_scale","render_display_type","show_developer_ui","use_translate_interface","language") if p in bpy.context.preferences.view.bl_rna.properties]
out["prefs.edit"] = [p for p in ("undo_steps","undo_memory_limit","use_global_undo","object_align","use_enter_edit_mode","material_link") if p in bpy.context.preferences.edit.bl_rna.properties]
out["prefs.system"] = [p for p in ("memory_cache_limit","scrollback","gl_texture_limit","anisotropic_filter","viewport_aa","use_gpu_subdivision","texture_time_out") if p in bpy.context.preferences.system.bl_rna.properties]
out["prefs.top"] = [p for p in ("is_dirty","use_preferences_save","active_section","autoexec_paths","addons","themes","keymap","apps","filepaths","view","edit","inputs","system","experimental","studio_lights") if p in bpy.context.preferences.bl_rna.properties]
out["asset_libraries"] = hasattr(pf, "asset_libraries")
out["asset_lib_add"] = hasattr(bpy.ops.preferences, "asset_library_add")
out["cycles_prefs_devices"] = hasattr(bpy.context.preferences.addons.get("cycles").preferences, "get_devices") if bpy.context.preferences.addons.get("cycles") else None
cp = bpy.context.preferences.addons["cycles"].preferences
out["cycles_compute_device_type"] = [i.identifier for i in cp.bl_rna.properties["compute_device_type"].enum_items]
sc = bpy.context.scene
out["view_settings"] = [p for p in ("view_transform","look","exposure","gamma","use_curve_mapping","use_hdr_view") if p in sc.view_settings.bl_rna.properties]
out["view_transforms"] = [i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items]
out["display_settings"] = [p.identifier for p in sc.display_settings.bl_rna.properties if not p.identifier.startswith("rna")]
out["render_props"] = [p for p in ("engine","resolution_x","resolution_y","resolution_percentage","fps","fps_base","filepath","film_transparent","use_motion_blur","use_persistent_data","threads_mode","threads","use_border","use_crop_to_border","pixel_aspect_x","use_simplify","simplify_subdivision","simplify_child_particles","use_stamp","use_compositing","use_sequencer","dither_intensity","use_lock_interface","hair_type") if p in sc.render.bl_rna.properties]
out["cycles_scene"] = [p for p in ("samples","preview_samples","use_denoising","denoiser","device","adaptive_threshold","use_adaptive_sampling","max_bounces","time_limit","use_light_tree","tile_size","use_auto_tile") if p in sc.cycles.bl_rna.properties]
out["eevee"] = [p for p in ("taa_render_samples","taa_samples","use_raytracing","use_shadows","shadow_ray_count","use_volumetric_shadows","use_bloom","use_gtao","use_ssr","use_motion_blur") if p in sc.eevee.bl_rna.properties]
out["scene_props"] = [p for p in ("frame_start","frame_end","frame_current","frame_step","use_preview_range","camera","world","unit_settings","gravity","use_gravity","audio_volume","use_nodes","background_set","cursor") if p in sc.bl_rna.properties]
out["addon_utils"] = [a for a in ("enable","disable","modules","check","module_bl_info","paths","reset_all") if hasattr(addon_utils, a)]
out["workspaces"] = [w.name for w in bpy.data.workspaces]
out["wm.save_mainfile.compress_default"] = bpy.ops.wm.save_mainfile.get_rna_type().properties["compress"].default
out["libs_load_sig"] = bpy.data.libraries.load.__doc__[:200].replace("\n"," ")
out["app_templates"] = list(bpy.utils.app_template_paths()) if hasattr(bpy.utils, "app_template_paths") else "n/a"
out["startup_file"] = bpy.utils.user_resource("CONFIG", path="startup.blend")
out["userpref_file"] = bpy.utils.user_resource("CONFIG", path="userpref.blend")
print("SCHECK", json.dumps(out, default=str))
