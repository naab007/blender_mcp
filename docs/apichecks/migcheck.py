# Headless proof for the four Blender 5.x migration breaks in addon.py and the
# facts the fixes need. Runs on 4.x and 5.x. Prints one line: MIGCHECK {json}.
#   "<blender>\blender.exe" --background --factory-startup --python docs\apichecks\migcheck.py 2>&1 | findstr MIGCHECK
# ASCII only. Loads addon.py from the repo root (two levels up) without installing it.
import bpy
import json
import os
import sys
import tempfile
import importlib.util

out = {"version": bpy.app.version_string, "background": bpy.app.background,
       "python": sys.version.split()[0]}


def tryf(key, fn):
    try:
        out[key] = fn()
    except Exception as e:
        out[key] = "Error: " + str(e).strip()[:200]


def err(e):
    return "Error: " + str(e).strip()[:120]


# --- break 1: engine identifier, resolve by assignment -------------------------
def engine_assign():
    scene = bpy.context.scene
    res = {}
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH", "CYCLES"):
        try:
            scene.render.engine = eng
            res[eng] = "ok"
        except Exception as e:
            res[eng] = err(e)
    scene.render.engine = "BLENDER_WORKBENCH"
    return res


tryf("engine_assign", engine_assign)
out["engine_enum_static"] = [e.identifier for e in
                             bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]


# --- break 3: boolean solver enum -----------------------------------------------
def boolean_solver():
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    mod = obj.modifiers.new("Boolean", "BOOLEAN")
    res = {"enum": [e.identifier for e in mod.bl_rna.properties["solver"].enum_items]}
    for s in ("EXACT", "FAST", "FLOAT", "MANIFOLD"):
        try:
            mod.solver = s
            res[s] = "ok"
        except Exception as e:
            res[s] = err(e)
    bpy.data.objects.remove(obj, do_unlink=True)
    return res


tryf("boolean_solver", boolean_solver)


# --- break 4: image_settings.media_type before file_format ----------------------
def media_type():
    ims = bpy.context.scene.render.image_settings
    res = {"has_media_type": hasattr(ims, "media_type")}
    if res["has_media_type"]:
        res["media_type_enum"] = [e.identifier for e in
                                  ims.bl_rna.properties["media_type"].enum_items]
        res["media_type_default"] = ims.media_type
    res["file_format_enum"] = [e.identifier for e in
                               ims.bl_rna.properties["file_format"].enum_items]
    for key, fmt in (("png_no_media", "PNG"), ("ffmpeg_no_media", "FFMPEG"),
                     ("exr_no_media", "OPEN_EXR")):
        try:
            ims.file_format = fmt
            res[key] = "ok"
        except Exception as e:
            res[key] = err(e)
    if res["has_media_type"]:
        for key, mt, fmt in (("ffmpeg_with_video", "VIDEO", "FFMPEG"),
                             ("png_with_image", "IMAGE", "PNG"),
                             ("exr_multilayer", "MULTI_LAYER", "OPEN_EXR_MULTILAYER"),
                             ("png_with_video_media", "VIDEO", "PNG")):
            try:
                ims.media_type = mt
                ims.file_format = fmt
                res[key] = "ok"
            except Exception as e:
                res[key] = err(e)
        ims.media_type = "IMAGE"
    ims.file_format = "PNG"
    res["file_format_after_reset"] = ims.file_format
    # Image datablock path used by the addon resize helper
    img = bpy.data.images.new("migcheck_img", 8, 8)
    try:
        img.file_format = "PNG"
        res["image_datablock_file_format"] = "ok"
    except Exception as e:
        res["image_datablock_file_format"] = err(e)
    bpy.data.images.remove(img)
    return res


tryf("media_type", media_type)


# --- media_type x file_format matrix and the engine enum read from an instance ---
def media_matrix():
    ims = bpy.context.scene.render.image_settings
    res = {}
    if hasattr(ims, "media_type"):
        for mt in [e.identifier for e in ims.bl_rna.properties["media_type"].enum_items]:
            ims.media_type = mt
            row = {"file_format_enum": [e.identifier for e in
                                        ims.bl_rna.properties["file_format"].enum_items]}
            for fmt in ("PNG", "OPEN_EXR", "OPEN_EXR_MULTILAYER", "FFMPEG"):
                try:
                    ims.file_format = fmt
                    row[fmt] = "ok"
                except Exception as e:
                    row[fmt] = err(e)
            res[mt] = row
        ims.media_type = "IMAGE"
    ims.file_format = "PNG"
    render = bpy.context.scene.render
    res["engine_enum_from_instance"] = [e.identifier for e in
                                        render.bl_rna.properties["engine"].enum_items]
    res["engine_enum_from_type"] = [e.identifier for e in
                                    bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    try:
        res["engine_enum_rna_get"] = [e.identifier for e in
                                      render.bl_rna.properties["engine"].enum_items_static]
    except Exception as e:
        res["engine_enum_rna_get"] = err(e)
    return res


tryf("media_matrix", media_matrix)


# --- still render headless per engine (needed before the depth test) ----------
def render_still():
    scene = bpy.context.scene
    res = {}
    for eng in ("BLENDER_WORKBENCH", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = eng
        except Exception:
            continue
        scene.render.resolution_x = 64
        scene.render.resolution_y = 64
        scene.render.resolution_percentage = 100
        eev = getattr(scene, "eevee", None)
        if eev is not None and hasattr(eev, "taa_render_samples"):
            eev.taa_render_samples = 1
        fp = os.path.join(tempfile.gettempdir(), "migcheck_%s.png" % eng)
        if os.path.exists(fp):
            os.remove(fp)
        scene.render.filepath = fp
        if hasattr(scene.render.image_settings, "media_type"):
            scene.render.image_settings.media_type = "IMAGE"
        scene.render.image_settings.file_format = "PNG"
        try:
            r = bpy.ops.render.render(write_still=True)
            res[eng] = {"result": sorted(r), "file": os.path.exists(fp),
                        "bytes": os.path.getsize(fp) if os.path.exists(fp) else 0}
        except Exception as e:
            res[eng] = err(e)
    scene.render.engine = "BLENDER_WORKBENCH"
    return res


tryf("render_still", render_still)


# --- break 2: depth map on scene.node_tree (4.x) or compositing_node_group (5.x)
def depth_map():
    src = bpy.context.scene
    res = {"src_has_compositing_node_group": hasattr(src, "compositing_node_group"),
           "src_has_node_tree": hasattr(src, "node_tree")}
    tmp = src.copy()
    tmp.name = "migcheck_depth"
    tree = None
    fp = os.path.join(tempfile.gettempdir(), "migcheck_depth.png")
    try:
        for vl in tmp.view_layers:
            vl.use_pass_z = True
        for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
            try:
                tmp.render.engine = eng
                res["engine"] = eng
                break
            except Exception:
                pass
        eev = getattr(tmp, "eevee", None)
        if eev is not None and hasattr(eev, "taa_render_samples"):
            eev.taa_render_samples = 1
        if hasattr(tmp, "compositing_node_group"):
            res["tmp_comp_group_after_copy"] = (tmp.compositing_node_group.name
                                                if tmp.compositing_node_group else None)
            tree = bpy.data.node_groups.new("migcheck_comp", "CompositorNodeTree")
            tmp.compositing_node_group = tree
            res["api"] = "compositing_node_group"
        else:
            tmp.use_nodes = True
            tree = tmp.node_tree
            res["api"] = "node_tree"
        nodes, links = tree.nodes, tree.links
        nodes.clear()
        rl = nodes.new("CompositorNodeRLayers")
        rl.scene = tmp
        res["rl_outputs"] = [s.name for s in rl.outputs]
        map_node = None
        for nid in ("CompositorNodeMapRange", "ShaderNodeMapRange"):
            try:
                map_node = nodes.new(nid)
                res["map_range_id"] = nid
                break
            except Exception as e:
                res["map_range_err_" + nid] = err(e)
        res["map_range_inputs"] = [s.name for s in map_node.inputs]
        res["map_range_outputs"] = [s.name for s in map_node.outputs]
        res["map_range_props"] = [p for p in ("use_clamp", "clamp", "data_type",
                                              "interpolation_type") if hasattr(map_node, p)]
        map_node.inputs["From Min"].default_value = 0.0
        map_node.inputs["From Max"].default_value = 10.0
        map_node.inputs["To Min"].default_value = 0.0
        map_node.inputs["To Max"].default_value = 1.0
        for p in ("use_clamp", "clamp"):
            if hasattr(map_node, p):
                setattr(map_node, p, True)
        invert = None
        for nid in ("CompositorNodeInvert", "ShaderNodeInvert"):
            try:
                invert = nodes.new(nid)
                res["invert_id"] = nid
                break
            except Exception as e:
                res["invert_err_" + nid] = err(e)
        res["invert_inputs"] = [s.name for s in invert.inputs]
        res["invert_outputs"] = [s.name for s in invert.outputs]
        if res["api"] == "compositing_node_group":
            tree.interface.new_socket(name="Image", in_out="OUTPUT",
                                      socket_type="NodeSocketColor")
            out_node = nodes.new("NodeGroupOutput")
            res["output_id"] = "NodeGroupOutput"
        else:
            out_node = nodes.new("CompositorNodeComposite")
            res["output_id"] = "CompositorNodeComposite"
        res["output_inputs"] = [s.name for s in out_node.inputs]
        links.new(rl.outputs["Depth"], map_node.inputs["Value"])
        links.new(map_node.outputs[0], invert.inputs["Color"])
        links.new(invert.outputs[0], out_node.inputs["Image"])
        res["links"] = len(links)
        tmp.render.resolution_x = 64
        tmp.render.resolution_y = 64
        tmp.render.resolution_percentage = 100
        tmp.render.filepath = fp
        if hasattr(tmp.render.image_settings, "media_type"):
            tmp.render.image_settings.media_type = "IMAGE"
        tmp.render.image_settings.file_format = "PNG"
        tmp.render.image_settings.color_mode = "BW"
        if os.path.exists(fp):
            os.remove(fp)
        r = bpy.ops.render.render(write_still=True, scene=tmp.name)
        res["render"] = sorted(r)
        res["file"] = os.path.exists(fp)
        if res["file"]:
            img = bpy.data.images.load(fp)
            vals = list(img.pixels)[0::4]
            res["pixel_min"] = round(min(vals), 3)
            res["pixel_max"] = round(max(vals), 3)
            res["pixel_varies"] = (max(vals) - min(vals)) > 0.05
            bpy.data.images.remove(img)
        if hasattr(src, "compositing_node_group"):
            res["src_comp_group_untouched"] = src.compositing_node_group is None
    finally:
        bpy.data.scenes.remove(tmp, do_unlink=True)
        if tree is not None and res.get("api") == "compositing_node_group":
            bpy.data.node_groups.remove(tree)
        res["node_groups_left"] = [g.name for g in bpy.data.node_groups]
    return res


tryf("depth_map", depth_map)


# --- deprecation: material / world use_nodes ------------------------------------
def material_nodes():
    m = bpy.data.materials.new("migcheck_mat")
    res = {"mat_node_tree_on_create": m.node_tree is not None,
           "mat_use_nodes_default": m.use_nodes}
    m.use_nodes = True
    res["mat_node_tree_after_use_nodes"] = m.node_tree is not None
    res["mat_principled_present"] = (m.node_tree.nodes.get("Principled BSDF") is not None
                                     if m.node_tree else None)
    bpy.data.materials.remove(m)
    w = bpy.data.worlds.new("migcheck_world")
    res["world_node_tree_on_create"] = w.node_tree is not None
    w.use_nodes = True
    res["world_node_tree_after"] = w.node_tree is not None
    bpy.data.worlds.remove(w)
    return res


tryf("material_nodes", material_nodes)


# --- misc facts the migration doc lists as opportunities -----------------------
def misc():
    import gpu
    import mathutils
    res = {"gpu_init": hasattr(gpu, "init"),
           "window_screenshot": hasattr(bpy.types.Window, "screenshot"),
           "exit_pre_handler": hasattr(bpy.app.handlers, "exit_pre"),
           "render_render_kwargs": [p.identifier for p in
                                    bpy.ops.render.render.get_rna_type().properties]}
    try:
        import numpy
        res["numpy"] = numpy.__version__
        res["numpy_vector_dtype"] = str(numpy.array(mathutils.Vector((1, 2, 3))).dtype)
        res["numpy_vector_f64_dtype"] = str(numpy.array(mathutils.Vector((1, 2, 3)),
                                                        dtype=numpy.float64).dtype)
    except Exception as e:
        res["numpy"] = err(e)
    try:
        import requests
        res["requests"] = requests.__version__
    except Exception as e:
        res["requests"] = err(e)
    return res


tryf("misc", misc)


# --- addon.py registers headless, and the four broken handlers as they are now --
def addon_register():
    repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    path = os.path.join(repo, "addon.py")
    res = {"addon_path": path}
    spec = importlib.util.spec_from_file_location("addon", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["addon"] = mod
    spec.loader.exec_module(mod)
    res["bl_info_blender"] = list(mod.bl_info["blender"])
    res["bl_info_version"] = list(mod.bl_info["version"])
    mod.register()
    res["panel_registered"] = hasattr(bpy.types, "BLENDERMCP_PT_Panel")
    res["scene_props"] = [p for p in ("blendermcp_port", "blendermcp_server_running")
                          if hasattr(bpy.context.scene, p)]
    srv = mod.BlenderMCPServer()
    try:
        handlers = srv._build_handlers()
        res["handler_count"] = len(handlers) if hasattr(handlers, "__len__") else str(type(handlers))
    except Exception as e:
        res["handler_count"] = err(e)
    smoke = {}

    def bool_smoke():
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        a = bpy.context.active_object
        a.name = "migcheck_target"
        bpy.ops.mesh.primitive_cube_add(location=(0.5, 0.5, 0.5))
        b = bpy.context.active_object
        b.name = "migcheck_cutter"
        try:
            return srv.boolean_operation("migcheck_target", "migcheck_cutter",
                                         operation="DIFFERENCE", solver="FAST", apply=False)
        finally:
            for o in (a, b):
                bpy.data.objects.remove(o, do_unlink=True)

    tmpd = tempfile.gettempdir()
    checks = (
        ("set_render_settings_eevee", lambda: srv.set_render_settings(engine="BLENDER_EEVEE")),
        ("boolean_fast", bool_smoke),
        ("render_depth_map", lambda: srv.render_depth_map(
            filepath=os.path.join(tmpd, "migcheck_h_depth.png"), max_depth=10.0)),
        ("render_from_camera", lambda: srv.render_from_camera(
            filepath=os.path.join(tmpd, "migcheck_h_cam.png"), width=64, height=64, samples=1)),
    )
    for name, fn in checks:
        try:
            r = fn()
            smoke[name] = r if isinstance(r, dict) else str(r)[:200]
        except Exception as e:
            smoke[name] = "Exception: " + str(e).strip()[:160]
    res["handler_smoke"] = smoke
    mod.unregister()
    res["panel_unregistered"] = not hasattr(bpy.types, "BLENDERMCP_PT_Panel")
    return res


# Opt-in only (MIGCHECK_ADDON=1): the default run proves the idioms against bpy and is
# deterministic for the apichecks drift test; this section depends on addon.py state.
if os.environ.get("MIGCHECK_ADDON") == "1":
    tryf("addon_register", addon_register)
else:
    out["addon_register"] = "skipped (set MIGCHECK_ADDON=1 to load the repo addon.py and call the four handlers)"

print("MIGCHECK " + json.dumps(out))
