import bpy, json
out = {}
def props(op):
    try: return [k for k in list(op.get_rna_type().properties.keys())[1:] if not k.startswith("filter_")]
    except Exception as e: return f"MISSING: {e}"
def rprops(t, names): return [n for n in names if n in t.bl_rna.properties]
# tree types
out["tree_types"] = [t.__name__ for t in bpy.types.NodeTree.__subclasses__()]
gn = bpy.data.node_groups.new("GN", "GeometryNodeTree")
out["tree_props"] = rprops(bpy.types.GeometryNodeTree, ["is_modifier","is_tool","is_mode_object","is_mode_edit","is_type_mesh","is_type_curve","is_type_point_cloud","interface","nodes","links","description","color_tag","default_group_node_width"])
out["interface_funcs"] = [f.identifier for f in bpy.types.NodeTreeInterface.bl_rna.functions]
sock = gn.interface.new_socket("Size", in_out="INPUT", socket_type="NodeSocketFloat")
out["iface_socket_props"] = rprops(bpy.types.NodeTreeInterfaceSocketFloat, ["name","identifier","in_out","socket_type","default_value","min_value","max_value","subtype","description","hide_value","hide_in_modifier","force_non_field","default_attribute_name","default_input","is_inspect_output","layout"])
out["iface_socket_identifier"] = sock.identifier
out["socket_types_sample"] = [t.__name__ for t in bpy.types.NodeSocket.__subclasses__() if t.__name__.startswith("NodeSocket")][:60]
n_in = gn.nodes.new("NodeGroupInput"); n_out = gn.nodes.new("NodeGroupOutput")
gn.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
gn.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
cube = gn.nodes.new("GeometryNodeMeshCube")
out["node_props"] = rprops(bpy.types.Node, ["name","label","bl_idname","location","width","height","hide","mute","parent","select","use_custom_color","color","inputs","outputs","internal_links","show_options","dimensions","type","bl_description","bl_static_type","warning_propagation","location_absolute"])
out["cube_inputs"] = [(s.name, s.identifier, s.bl_idname, getattr(s,"default_value",None)) for s in cube.inputs]
out["socket_props"] = rprops(bpy.types.NodeSocket, ["name","identifier","is_linked","is_output","hide","enabled","is_multi_input","display_shape","link_limit","type","bl_idname","is_unavailable","hide_value","label","show_expanded","node"])
l = gn.links.new(cube.outputs["Mesh"], n_out.inputs[0])
out["link_props"] = rprops(bpy.types.NodeLink, ["from_node","from_socket","to_node","to_socket","is_valid","is_muted","is_hidden","from_socket"])
# zones
sim_in = gn.nodes.new("GeometryNodeSimulationInput"); sim_out = gn.nodes.new("GeometryNodeSimulationOutput")
out["sim_pair"] = hasattr(sim_in, "pair_with_output") and sim_in.pair_with_output(sim_out)
out["sim_out_props"] = rprops(bpy.types.GeometryNodeSimulationOutput, ["state_items","active_index","active_item","use_bake_path"]) if hasattr(bpy.types, "GeometryNodeSimulationOutput") else "no"
out["sim_state_items_funcs"] = [f.identifier for f in bpy.types.NodeGeometrySimulationOutputItems.bl_rna.functions] if hasattr(bpy.types, "NodeGeometrySimulationOutputItems") else "no"
rep_in = gn.nodes.new("GeometryNodeRepeatInput"); rep_out = gn.nodes.new("GeometryNodeRepeatOutput")
out["repeat_pair"] = rep_in.pair_with_output(rep_out)
out["bake_node"] = hasattr(bpy.types, "GeometryNodeBake")
out["zone_nodes"] = [n for n in ("GeometryNodeSimulationInput","GeometryNodeRepeatInput","GeometryNodeForeachGeometryElementInput","GeometryNodeIndexSwitch","GeometryNodeMenuSwitch","GeometryNodeBake","GeometryNodeViewer","GeometryNodeWarning","GeometryNodeTool3DCursor","GeometryNodeInputActiveCamera","GeometryNodeGizmoDial","GeometryNodeGizmoLinear","GeometryNodeGizmoTransform") if hasattr(bpy.types, n)]
# node catalogue
gnodes = [t.bl_rna.identifier for t in bpy.types.GeometryNode.__subclasses__()]
out["geometry_node_count"] = len(gnodes)
out["fn_node_count"] = len([t for t in bpy.types.FunctionNode.__subclasses__()])
out["shader_node_count"] = len(bpy.types.ShaderNode.__subclasses__())
out["compositor_node_count"] = len(bpy.types.CompositorNode.__subclasses__())
out["node_descr_sample"] = bpy.types.GeometryNodeMeshCube.bl_rna.description
out["node_input_template"] = [f.identifier for f in bpy.types.Node.bl_rna.functions]
# modifier binding
ob = bpy.data.objects.new("o", bpy.data.meshes.new("m")); bpy.context.scene.collection.objects.link(ob)
mod = ob.modifiers.new("GN", "NODES"); mod.node_group = gn
out["mod_props"] = rprops(bpy.types.NodesModifier, ["node_group","bake_directory","bakes","bake_target","show_group_selector","open_output_attributes_panel","panels","simulation_bake_directory"])
try:
    mod[sock.identifier] = 2.5
    out["mod_input_by_identifier"] = mod[sock.identifier]
    out["mod_input_api"] = "idprop"
except TypeError as e:
    out["mod_input_by_identifier"] = f"IDPROP PATH REMOVED: {e}"
    out["mod_input_api"] = "rna"
if hasattr(mod, "properties"):
    p = mod.properties
    out["mod_properties_attrs"] = [a for a in ("inputs", "outputs") if hasattr(p, a)]
    try:
        inp = getattr(p.inputs, sock.identifier, None) or p.inputs[sock.identifier]
        inp.value = 2.5
        out["mod_rna_input_props"] = [a for a in ("value", "type", "attribute_name", "identifier", "name") if hasattr(inp, a)]
        out["mod_rna_input_value"] = inp.value
        out["mod_rna_input_type_enum"] = [i.identifier for i in inp.bl_rna.properties["type"].enum_items] if "type" in inp.bl_rna.properties else "n/a"
        out["mod_rna_inputs_keys"] = list(p.inputs.keys()) if hasattr(p.inputs, "keys") else "no keys()"
    except Exception as e:
        out["mod_rna_input_error"] = str(e)
else:
    out["mod_properties_attrs"] = "no modifier.properties (4.x)"
out["nodes_ops"] = [n for n in ("new_geometry_nodes_modifier","new_geometry_node_group_assign","new_geometry_node_group_tool","group_make","group_ungroup","group_edit","join","detach","mute_toggle","hide_toggle","add_node","add_reroute","link","links_cut","duplicate","delete","select_all","clipboard_copy","clipboard_paste","find_node","view_all","tree_path_parent","interface_item_new","interface_item_remove","enum_definition_item_add","repeat_zone_item_add","simulation_zone_item_add","bake_node_item_add","index_switch_item_add","link_viewer") if hasattr(bpy.ops.node, n)]
out["gn_bake_ops"] = [n for n in ("simulation_nodes_cache_bake","simulation_nodes_cache_calculate_to_frame","simulation_nodes_cache_delete","geometry_node_bake_single","geometry_node_bake_delete_single","geometry_node_bake_pack_single","geometry_node_bake_unpack_single") if hasattr(bpy.ops.object, n)]
out["bake_ops_props"] = props(bpy.ops.object.simulation_nodes_cache_bake)
# evaluation readback
dg = bpy.context.evaluated_depsgraph_get()
ev = ob.evaluated_get(dg)
out["eval_mesh_verts"] = len(ev.data.vertices)
out["eval_attrs"] = [(a.name, a.domain, a.data_type) for a in ev.data.attributes][:10]
out["instances_readback"] = [f for f in ("object_instances",) if hasattr(dg, f)]
out["depsgraph_iter_instances"] = sum(1 for i in dg.object_instances if i.is_instance)
# frames, reroutes, groups
fr = gn.nodes.new("NodeFrame"); rr = gn.nodes.new("NodeReroute"); cube.parent = fr
out["frame_props"] = rprops(bpy.types.NodeFrame, ["label_size","shrink","text"])
grp = gn.nodes.new("GeometryNodeGroup"); grp.node_tree = bpy.data.node_groups.new("Inner", "GeometryNodeTree")
out["group_node_ok"] = grp.node_tree.name
# shader / compositor trees
mat = bpy.data.materials.new("M"); mat.use_nodes = True
out["shader_tree_type"] = mat.node_tree.bl_idname
sc = bpy.context.scene
if hasattr(sc, "compositing_node_group"):
    ct = bpy.data.node_groups.new("mcp_comp", "CompositorNodeTree"); sc.compositing_node_group = ct
    out["comp_api"] = "compositing_node_group (5.0+)"
    out["comp_tree_type"] = ct.bl_idname
    out["comp_nodes_sample"] = [n.bl_idname for n in ct.nodes]
    ids = {}
    for cid in ("CompositorNodeMapRange","CompositorNodeInvert","CompositorNodeNormalize","CompositorNodeGamma","CompositorNodeOutputFile","CompositorNodeRLayers","CompositorNodeComposite","CompositorNodeViewer","CompositorNodeMixRGB","CompositorNodeMath","CompositorNodeValToRGB","CompositorNodeSepRGBA","CompositorNodeCombRGBA","CompositorNodeSeparateColor","CompositorNodeCombineColor","CompositorNodeBlur","CompositorNodeAlphaOver","CompositorNodeSetAlpha","CompositorNodePremulKey","CompositorNodeDilateErode","CompositorNodeNormal","CompositorNodeVecBlur","ShaderNodeMapRange","ShaderNodeInvert","ShaderNodeGamma","ShaderNodeMath","ShaderNodeMix","ShaderNodeValToRGB","ShaderNodeSeparateColor","ShaderNodeCombineColor","NodeGroupOutput"):
        try: n = ct.nodes.new(cid); ids[cid] = True; ct.nodes.remove(n)
        except Exception as e: ids[cid] = str(e)[:60]
    out["comp_node_ids"] = ids
    try:
        fo = ct.nodes.new("CompositorNodeOutputFile")
        out["file_output_props"] = [a for a in ("directory","file_name","file_output_items","base_path","file_slots","layer_slots","format") if hasattr(fo, a)]
    except Exception as e: out["file_output_props"] = str(e)
    try:
        rl = ct.nodes.new("CompositorNodeRLayers"); out["rlayers_outputs"] = [s.name for s in rl.outputs][:20]
    except Exception as e: out["rlayers_outputs"] = str(e)
    out["scene_use_nodes_attr"] = hasattr(sc, "use_nodes")
else:
    sc.use_nodes = True
    out["comp_api"] = "scene.node_tree (4.x)"
    out["comp_tree_type"] = sc.node_tree.bl_idname
    out["comp_nodes_sample"] = [n.bl_idname for n in sc.node_tree.nodes]
    ids = {}
    for cid in ("CompositorNodeMapRange","CompositorNodeInvert","CompositorNodeNormalize","CompositorNodeGamma","CompositorNodeOutputFile","CompositorNodeRLayers","CompositorNodeComposite","CompositorNodeViewer","CompositorNodeMixRGB","CompositorNodeMath","CompositorNodeValToRGB","CompositorNodeSepRGBA","CompositorNodeCombRGBA","CompositorNodeSeparateColor","CompositorNodeCombineColor","CompositorNodeBlur","CompositorNodeAlphaOver","CompositorNodeSetAlpha","CompositorNodePremulKey","CompositorNodeDilateErode","CompositorNodeNormal","CompositorNodeVecBlur","ShaderNodeMapRange","ShaderNodeInvert","ShaderNodeGamma","ShaderNodeMath","ShaderNodeMix","ShaderNodeValToRGB","ShaderNodeSeparateColor","ShaderNodeCombineColor","NodeGroupOutput"):
        try: n = sc.node_tree.nodes.new(cid); ids[cid] = True; sc.node_tree.nodes.remove(n)
        except Exception as e: ids[cid] = str(e)[:60]
    out["comp_node_ids"] = ids
    fo = sc.node_tree.nodes.new("CompositorNodeOutputFile")
    out["file_output_props"] = [a for a in ("directory","file_name","file_output_items","base_path","file_slots","layer_slots","format") if hasattr(fo, a)]
    rl = [n for n in sc.node_tree.nodes if n.bl_idname=="CompositorNodeRLayers"][0]
    out["rlayers_outputs"] = [s.name for s in rl.outputs][:20]
    out["scene_use_nodes_attr"] = hasattr(sc, "use_nodes")
out["comp_node_count"] = len(bpy.types.CompositorNode.__subclasses__())
# node search via bl_rna categories? node category API
try:
    import nodeitems_utils
    out["nodeitems_utils"] = True
except Exception as e:
    out["nodeitems_utils"] = str(e)
out["node_tree_to_dict_ok"] = True
print("NODECHECK", json.dumps(out, default=str))
