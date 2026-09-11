# Feature request: node-graph programming (geometry, shader, compositor trees)

Status: OPEN, requested 2026-09-09
Target version: 1.12.0
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`)
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here. The shader-node tools in
`FEATURE-REQUEST-uv-and-texturing.md` section 2.1 become thin aliases of the
tree-agnostic tools below; build these first if both are in the same pass.

## 0. Scope and why

Blender's "flow programming" is its node trees: Geometry Nodes (procedural modelling,
scattering, simulation zones, repeat zones, bake nodes, tool operators), Shader nodes,
Compositor nodes and Texture nodes. Today the server cannot create, read, link or run any
of them except through raw code, and it cannot SEE a graph at all. For game assets,
geometry nodes are where modular kits, scatter, LOD-aware detailing, procedural props,
wind and destruction setups live, and a graph the agent can inspect and diff is worth more
than any single generator tool.

Two layers:

1. **Tree-agnostic driver**: one set of tools that works on any `NodeTree` (geometry,
   shader, compositor, texture, node groups) by tree id: create, inspect, add, remove,
   link, set values, group, layout, describe node types from RNA, evaluate and read back.
2. **Geometry-nodes recipes**: named tools that build known-good graphs (scatter, array
   along curve, wireframe, extrude by attribute, LOD switch, wind vertex colours) on top of
   the driver, and the modifier binding, bake and readback tools that make a graph usable
   in a game pipeline.

Perception: a graph is text. `get_node_tree` returns a compact JSON graph, and
`render_node_graph` draws it server-side with PIL from that JSON (layered left-to-right
layout by link depth), because the node editor cannot be captured headless. Evaluation
readback (`evaluate_geometry`) is numeric: vertex and instance counts, attribute
statistics, bounding box, per-domain samples.

Build what is in this document. If something is wrong or impossible on 4.3.2, say so in
the report and continue.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `add_modifier` | A `NODES` modifier cannot be given a node group or inputs | Accept `node_group` (name) in `props`, and any `Socket_N` identifier or the socket's display NAME as an input key (resolve name → identifier through `node_group.interface.items_tree`). Report the resolved identifiers. |
| `get_object_info` | Silent about geometry-nodes modifiers | Per NODES modifier: `node_group`, inputs `{name: {identifier, value, use_attribute, attribute_name}}`, `bake_directory`, bakes with `is_baked`. |
| `create_material` / `load_texture` / `get_material_info` | Shader-only implementations | Re-implement on the tree-agnostic tools (`get_node_tree`, `add_node`, `link_nodes`, `set_node_input`) so behaviour is identical across tree types. |
| `render_depth_map` | Builds a compositor tree by hand | Use `add_node`/`link_nodes` on the temp scene's compositor tree; same code path as user-facing compositor tools. |

## 2. New tools

Tree addressing: `tree` is `"material:<name>"`, `"group:<node_group_name>"`,
`"compositor"` (the scene's), `"world:<name>"`, `"texture:<name>"`, or `"modifier:<object>/<modifier>"`
(resolves to its node group). Nodes are addressed by `name` (unique within a tree);
sockets by name, identifier or index; a `path` like `"Cube.inputs.Size"` is accepted
everywhere a node + socket pair is.

### 2.1 Tier 1, the driver (required)

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `list_node_trees` | `kind=None` (GEOMETRY / SHADER / COMPOSITOR / TEXTURE / WORLD), `include_embedded=True` | Node groups from `bpy.data.node_groups` (with `is_modifier`, `is_tool`, users) plus embedded trees (materials, worlds, scene compositor). |
| `create_node_group` | `name`, `kind="GEOMETRY"`, `inputs` JSON `[{name, socket_type, default?, min?, max?, subtype?, description?, hide_in_modifier?}]`, `outputs` JSON, `add_io_nodes=True`, `is_modifier=None`, `is_tool=None` | `bpy.data.node_groups.new(name, "<Kind>NodeTree")`, `interface.new_socket(name, in_out=, socket_type=)` for each, Group Input and Group Output nodes placed. Reply: name, interface items with their `identifier`s (`Socket_0`, ...), which the modifier tools need. |
| `get_node_tree` | `tree`, `depth=1` (expand nested group nodes to this depth), `include_values=True`, `include_layout=False`, `filter=None` (node name glob or type) | Compact graph JSON: `interface` (inputs, outputs, panels), `nodes: [{name, type (bl_idname), label, mute, hide, parent, location?, inputs: {name: value or "← Node.Socket" or "[unlinked field]"}, outputs: {name: ["→ Node.Socket", ...]}, props: {enum and settings props not sockets}, warnings}]`, `links: [[from, from_socket, to, to_socket, valid]]`, `zones: [{type, input_node, output_node, items}]`, `unlinked_required_inputs`, `dead_ends` (nodes with no path to the output). The perceive tool for graphs. |
| `describe_node_type` | `node_type` (bl_idname, e.g. `GeometryNodeMeshCube`) or `search` (substring) , `tree_kind="GEOMETRY"` | From RNA: description, input and output sockets (name, identifier, type, default, min/max), non-socket props with enum items. For `search`: a list of matching bl_idnames with one-line descriptions. Catalogue built at first call by scanning `dir(bpy.types)` for `GeometryNode*`, `FunctionNode*`, `ShaderNode*`, `CompositorNode*`, `TextureNode*` (NOT `__subclasses__()`, see facts). |
| `add_node` | `tree`, `node_type`, `name=None`, `location=None` (auto-placed right of the last added node when None), `label=None`, `props` JSON (enum and settings props; `node_tree` for group nodes accepts a group name; `image`, `object`, `collection`, `material` accept names), `inputs` JSON (socket → constant value), `parent=None` (frame) | Reply: final name, inputs with identifiers, `set`/`unset` like `add_modifier`. |
| `add_nodes` | `tree`, `nodes` JSON list of `add_node` params, `links` JSON list of `[from, from_socket, to, to_socket]` | Batch build in one round trip; names in `links` refer to names assigned in the same batch. Reply: created names, links made, failures with index. |
| `remove_node` / `remove_nodes` | `tree`, `name` or `names` or `filter`, `reconnect=False` | `reconnect=True` bridges the first matching input and output link across the removed node (like Ctrl-X). |
| `link_nodes` | `tree`, `from_node`, `from_socket`, `to_node`, `to_socket`, `replace=True` | Errors list the from-node's outputs and the to-node's inputs with types. Type mismatch returns a warning (Blender inserts implicit conversions) with `is_valid`. |
| `unlink` | `tree`, `node`, `socket=None` (all sockets of the node if None), `direction="BOTH"` | |
| `set_node_input` | `tree`, `node`, `socket`, `value`, `unlink_first=True` | Value coercion by socket type: float, int, bool, "r,g,b,a", "x,y,z", string, object/collection/material/image by name, menu switch enum by item name. Rotation sockets accept degrees. |
| `set_node_props` | `tree`, `node`, `props` JSON | Non-socket properties (`operation`, `data_type`, `domain`, `mode`, `blend_type`, `use_clamp`, ...). Errors list the enum items. |
| `get_node` | `tree`, `node` | One node in full (all sockets with identifiers, availability, values, links, props, dimensions, warnings). |
| `rename_node` / `set_node_label` / `mute_node` / `hide_node` | as named | |
| `add_reroute` | `tree`, `from_node`, `from_socket`, `to_node`, `to_socket`, `location=None` | Inserts a reroute into an existing link. |
| `add_frame` | `tree`, `label`, `nodes` (comma list), `color=None`, `text=None` | Groups nodes visually; `remove_frame(keep_nodes=True)`. |
| `group_nodes` | `tree`, `nodes`, `group_name`, `expose_inputs="AUTO"`, `expose_outputs="AUTO"` | Moves the nodes into a new node group and inserts a Group node in their place; links crossing the boundary become interface sockets (AUTO) or the explicit lists. Reply: new group's interface with identifiers. Implemented without `node.group_make` (needs editor context): build the inner tree by copying nodes and re-creating links. |
| `ungroup_node` | `tree`, `node` | Inline a group node's content. |
| `set_interface` | `tree`, `add` JSON, `remove` (comma), `rename` JSON `{old: new}`, `reorder` (comma list), `panels` JSON | `interface.new_socket/remove/move/new_panel`. Changing the interface changes modifier input identifiers ONLY for new sockets; existing identifiers are stable, say so in the reply. |
| `layout_node_tree` | `tree`, `mode="LAYERED"` (LAYERED / GRID / KEEP), `spacing="60,40"`, `align_frames=True` | Server-computed layered layout from link depth written back to `node.location`. Run automatically by `add_nodes` unless `layout=False`. Makes the graph readable when the user opens the editor and makes `render_node_graph` deterministic. |
| `render_node_graph` | `tree`, `depth=1`, `max_size=1600`, `show_values=True`, `highlight` (comma list of node names), `style="LIGHT"` | Server-side PIL drawing from `get_node_tree` JSON: boxes with type-coloured headers, socket rows with values, bezier links, frames as background rectangles, zones as dashed outlines, muted nodes greyed, invalid links red. Returns via `_safe_image_return`. |
| `diff_node_trees` | `tree_a`, `tree_b` (or `snapshot` from `snapshot_node_tree`) | Nodes and links added, removed, changed values and props. `snapshot_node_tree(tree, name)` stores JSON in `driver_namespace` for later diffs, the same pattern as reference images. |
| `copy_node_tree` | `source`, `target_name` or `target_tree` (append into), `offset="0,0"` | Duplicate a group or copy a subgraph between trees of the same kind. |
| `validate_node_tree` | `tree` | Unlinked required inputs, invalid links, dead ends, Group Output with nothing on Geometry, zones without a pair, missing image/object references, deprecated node types, cycles. Reply per issue with node names; the check the recipes run before returning. |

**Geometry-nodes binding, evaluation and baking**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `assign_node_group` | `object`, `node_group`, `modifier_name=None`, `inputs` JSON `{name_or_identifier: value}`, `position="LAST"` (LAST / FIRST / BEFORE:<mod> / AFTER:<mod>) | Adds or reuses a NODES modifier. Inputs by display name resolved to `Socket_N`. Object, collection, material and image inputs by name. `<identifier>_use_attribute` and `<identifier>_attribute_name` accepted for field inputs. |
| `get_modifier_inputs` / `set_modifier_inputs` | `object`, `modifier`, `values` JSON | Read and write the modifier's interface values with names, identifiers, types, min/max, `use_attribute`. |
| `evaluate_geometry` | `object`, `frame=None`, `stats="COUNTS,BOUNDS,ATTRIBUTES,INSTANCES"`, `attribute=None` (full per-element read, capped by `max_elements=5000`), `domain=None` | `evaluated_get(depsgraph)`: vertex, edge, face, corner counts, bounds, per-attribute `{domain, data_type, min, max, mean}` for numeric attributes (numpy via `foreach_get`), instance count and the top instanced objects (from `depsgraph.object_instances`), curves and point-cloud counts when the evaluated geometry is not a mesh (via a temporary realize + `new_from_object`). The numeric perceive tool for graphs. |
| `realize_geometry` | `object`, `name=None`, `frame=None`, `keep_original=True`, `realize_instances=True`, `apply_all_modifiers=True` | Snapshot the evaluated result as a plain mesh object (`bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=)`, plus a Realize Instances node appended temporarily when instances exist). This is how a procedural asset becomes an exportable game mesh. Reply: name, counts, attributes kept. |
| `bake_geometry_nodes` | `object`, `modifier=None`, `what="ALL"` (ALL / SIMULATION / BAKE_NODES), `directory=None`, `frame_start=None`, `frame_end=None`, `pack=False` | `object.simulation_nodes_cache_bake` / `geometry_node_bake_single` with `bake_directory` set; `pack=True` uses `geometry_node_bake_pack_single`. Reply: baked frames, directory, size on disk, `is_baked` per bake item. `free_geometry_nodes_bake` is the inverse. |
| `set_zone_items` | `tree`, `zone_node` (the output node of a simulation or repeat zone), `items` JSON `[{name, socket_type}]`, `mode="REPLACE"` | `state_items.new/remove/move` (simulation) and `repeat_items` (repeat). `add_zone(tree, kind="SIMULATION"|"REPEAT"|"FOREACH", items, location)` creates a paired zone (`pair_with_output` verified). |
| `run_node_tool` | `node_group`, `objects`, `inputs` JSON | Runs a geometry-nodes TOOL group (`is_tool=True`) on the objects via the operator path with a context override; falls back to a temporary modifier + `realize_geometry` when the operator context is unavailable headless. |

### 2.2 Tier 2, geometry-nodes recipes (each builds a validated group and returns its interface)

| Tool | Builds | Notes |
|---|---|---|
| `gn_scatter` | Distribute Points on Faces → Instance on Points, with density, seed, scale and rotation randomisation, alignment to normal, slope mask, vertex-group density, optional Realize | The node-based twin of `scatter_objects`; editable afterwards. |
| `gn_array_along_curve` | Curve to Points / Resample → Instance on Points → Realize, with count or spacing, align to tangent | Fences, rails, cables. |
| `gn_wireframe` | Mesh to Curve → Curve to Mesh with a profile circle | Cage and hologram meshes. |
| `gn_extrude_by_attribute` | Extrude Mesh driven by a named attribute or vertex group | Panels, greebles. |
| `gn_lod_switch` | Index Switch on a "LOD" integer input over N object inputs | Author LODs as one modifier input, `realize_geometry` per level for export. |
| `gn_wind_vertex_color` | Position-based gradient + noise stored into a colour attribute | Foliage wind masks for engine shaders. |
| `gn_bounds_box` / `gn_convex_hull` | Bounding Box / Convex Hull → new object | Collision proxies as nodes. |
| `gn_boolean_stack` | Mesh Boolean with a collection of cutters | Non-destructive kitbash. |
| `gn_uv_from_position` | Store Named Attribute UVMap from projected position | Quick box or planar UVs on procedural meshes. |
| `gn_point_to_sprites` | Points → camera-facing quads with UVs | Billboard clusters, impostor grass. |
| `gn_simulation_template` | Simulation zone with an integer "Substeps" input and a delta-time pattern | Starting point for custom sims. |
| `comp_render_passes` | Compositor graph writing Combined, Normal, Depth, Vector, Mist, Position to File Output | Feeds `render_billboard`, flipbooks with motion vectors and depth. |
| `comp_sprite_cleanup` | Alpha dilate, premultiply, outline | Sprite-sheet post. |

### 2.3 Tier 3, later (do NOT build unless asked)

Python-to-nodes transpiler (expression string → Math/Vector Math graph), node-group
library sync from a folder of `.blend` assets, automatic group-input exposure heuristics
beyond AUTO, live viewer-node readback (needs spreadsheet context).

## 3. Ripple points beyond the standard list

- `TOOLS.md` and `README.md`: sections "Node Graphs" and "Geometry Nodes".
- `/blender` skill: a "Build a procedural asset" pattern (`create_node_group` →
  `add_nodes` → `validate_node_tree` → `assign_node_group` → `evaluate_geometry` →
  `realize_geometry` → export) and a rule: "prefer `get_node_tree` over
  `execute_blender_code` for any graph question".
- The texturing request's shader tools become aliases; note it in `TOOLS.md`.

## 4. Blender 4.3.2 facts, verified headlessly 2026-09-09 (do not re-derive)

- Tree types are created with `bpy.data.node_groups.new(name, "GeometryNodeTree" |
  "ShaderNodeTree" | "CompositorNodeTree" | "TextureNodeTree")`. `NodeTree.__subclasses__()`
  returns an EMPTY list; do not enumerate types that way. Material trees are
  `ShaderNodeTree`, the scene compositor is `CompositorNodeTree` (`scene.use_nodes = True`
  creates it with `CompositorNodeRLayers` + `CompositorNodeComposite`).
- Node type catalogue: `GeometryNode.__subclasses__()` returned only 16 and
  `FunctionNode/ShaderNode/CompositorNode.__subclasses__()` returned 0 (RNA classes are
  materialised lazily). Enumerate `dir(bpy.types)` by prefix and read
  `getattr(bpy.types, n).bl_rna.description` (verified:
  `GeometryNodeMeshCube.bl_rna.description == "Generate a cuboid mesh with variable side
  lengths and subdivisions"`). Socket templates come from instantiating the node in a
  scratch tree and reading `inputs`/`outputs` (`name, identifier, bl_idname,
  default_value`); cache per type.
- `GeometryNodeTree` props: `is_modifier, is_tool, is_mode_object, is_mode_edit,
  is_type_mesh, is_type_curve, is_type_point_cloud, interface, nodes, links, description,
  color_tag, default_group_node_width`.
- Interface: `tree.interface.new_socket(name, in_out="INPUT"|"OUTPUT",
  socket_type="NodeSocketFloat")`, `new_panel`, `copy`, `remove`, `clear`, `move`,
  `move_to_parent`. Interface socket props: `name, identifier, in_out, socket_type,
  default_value, min_value, max_value, subtype, description, hide_value,
  hide_in_modifier, force_non_field, default_attribute_name, default_input,
  is_inspect_output`. Identifiers are `Socket_0`, `Socket_1`, ... in creation order and are
  the modifier keys: `modifier["Socket_0"] = 2.5` verified. `NodeSocket.__subclasses__()`
  is also empty; socket type names come from the interface `socket_type` enum.
- Node props: `name, label, bl_idname, location, width, height, hide, mute, parent,
  select, use_custom_color, color, inputs, outputs, internal_links, show_options,
  dimensions, type, bl_description, bl_static_type, warning_propagation`. Socket props:
  `name, identifier, is_linked, is_output, hide, enabled, is_multi_input, display_shape,
  link_limit, type, bl_idname, is_unavailable, hide_value, label, node`. Link props:
  `from_node, from_socket, to_node, to_socket, is_valid, is_muted, is_hidden`.
- Zones: `GeometryNodeSimulationInput.pair_with_output(sim_out)` and
  `GeometryNodeRepeatInput.pair_with_output(rep_out)` both return True headless.
  Simulation output has `state_items` (`new, remove, clear, move`), `active_index`,
  `active_item`. Present node types: `GeometryNodeSimulationInput/Output`,
  `GeometryNodeRepeatInput/Output`, `GeometryNodeForeachGeometryElementInput`,
  `GeometryNodeIndexSwitch`, `GeometryNodeMenuSwitch`, `GeometryNodeBake`,
  `GeometryNodeViewer`, `GeometryNodeWarning`, gizmo nodes.
- `NodeFrame` props `label_size, shrink, text`; `node.parent = frame` works;
  `NodeReroute` exists; `GeometryNodeGroup.node_tree` accepts another group.
- NodesModifier props: `node_group, bake_directory, bakes, bake_target,
  show_group_selector, open_output_attributes_panel, panels`. Bake ops:
  `object.simulation_nodes_cache_bake(selected)`, `simulation_nodes_cache_calculate_to_frame`,
  `simulation_nodes_cache_delete`, `geometry_node_bake_single`, `geometry_node_bake_delete_single`,
  `geometry_node_bake_pack_single`, `geometry_node_bake_unpack_single`.
- Evaluation readback: `obj.evaluated_get(depsgraph).data` has `vertices` and
  `attributes` (`position` POINT FLOAT_VECTOR, internal `.edge_verts`, `.corner_vert`,
  `.corner_edge`, `sharp_face`); `depsgraph.object_instances` iterates instances with
  `is_instance`. `bpy.data.meshes.new_from_object` exists.
- `node.*` operators (`group_make`, `add_node`, `link_viewer`, `find_node`, ...) need a
  node-editor context; every driver tool above works on data, never through these ops.
- `nodeitems_utils` imports headless (menu categories), usable for a friendlier
  `describe_node_type(search=)` grouping; not required.

## 5. Testing (required before you report done)

Headless, `--background --factory-startup`, handlers called directly:

1. `create_node_group("Scatter", inputs=[Geometry, Density float 0..100 default 10,
   Seed int])` returns identifiers `Socket_0..2`; `list_node_trees(kind=GEOMETRY)` shows it.
2. `add_nodes` builds Group Input → Distribute Points on Faces → Instance on Points (with
   a cube from `GeometryNodeMeshCube` as instance) → Realize → Group Output, links by
   batch names; `validate_node_tree` reports 0 issues; `get_node_tree` shows every link
   and the Density input as `← Group Input.Density`.
3. `assign_node_group("Plane", "Scatter", inputs={"Density": 50, "Seed": 3})` sets
   `modifier["Socket_1"] == 50`; `evaluate_geometry("Plane")` reports vertex count > 8
   and an instance count of 0 after realize (or > 0 with Realize removed).
4. `set_node_input(..., "Density", 200)` on the group-input default changes
   `evaluate_geometry` counts; `describe_node_type("GeometryNodeMeshCube")` lists the
   four inputs with defaults.
5. `render_node_graph("group:Scatter")` returns an image that `analyze_render` reports as
   non-blank with ≥ 5 distinct colour clusters.
6. `add_zone(kind="SIMULATION", items=[Geometry])` creates a paired zone; `set_zone_items`
   adds a "Velocity" vector item; `get_node_tree` lists the zone with both items.
7. `group_nodes` on three nodes creates a nested group with the crossing links exposed;
   `ungroup_node` restores the original graph (`diff_node_trees` vs a snapshot: empty).
8. `realize_geometry("Plane")` produces a mesh object whose vertex count equals
   `evaluate_geometry`'s; `validate_asset` (engine-readiness) passes on it.
9. Shader: `create_material` re-implemented on the driver produces a tree byte-equal (as
   `get_node_tree` JSON) to the pre-refactor output for the same inputs.
10. Compositor: `comp_render_passes` on a temp scene, render one frame, assert the File
    Output wrote Normal and Depth files.
11. `bake_geometry_nodes` on a simulation-zone group over frames 1..10 writes files under
    `bake_directory` and `is_baked` is True; `free_geometry_nodes_bake` clears them.
12. `layout_node_tree(LAYERED)` on the Scatter group leaves no two nodes overlapping
    (bounding-box test on `location` + `dimensions` or width/height).

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.
