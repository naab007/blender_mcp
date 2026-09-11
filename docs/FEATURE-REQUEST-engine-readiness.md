# Feature request: engine readiness (validation, LODs, collision, sockets, export presets)

Status: OPEN, requested 2026-09-07
Target version: 1.9.0
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`)
Categories covered (see `docs/README.md`): 1 validation, 2 optimisation and LODs,
3 collision, 4 sockets and pivots, 5 engine export presets
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here.

## 0. Scope and why

Everything that decides whether a mesh survives import into an engine happens here. Today
the server can export a single object to FBX or glTF with defaults, and nothing else in this
category exists: no validation, no LODs, no collision, no sockets, no engine presets, no
batch export. The lessons that shaped this doc, all from real sessions on this workstation:

- SPT Tarkov: an OBJ round trip welded 107 verts and lost the original tangents. Exports
  must preserve tangents, custom normals and vertex order, and the agent must be able to
  DIFF a mesh against its source before shipping.
- Timberborn: Timbermesh wants "Slot" empties as attach points and exact base-game
  material names. Red normals in the viewport mean the model is inside out in the game.
- Unreal: collision comes in as `UCX_<mesh>_NN` children, sockets as `SOCKET_` empties,
  lightmaps from UV1, and the scale must be centimetres or the mesh is 100× too small.
- Unity: `_LOD0.._LODn` naming builds a LODGroup on import; Y-up; metres.
- Bevy and Godot: glTF, Y-up, Godot collision by `-col` style suffixes.

Build what is in this document. If something is wrong or impossible on 4.3.2, say so in
the report and continue.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `export_object` | Selects only the named object; defaults are engine-agnostic; no batch; no manifest | Becomes the low-level exporter under the new `export_for_engine`. Add `objects` (comma list) as an alternative to `name`, `include_hierarchy=True`, `apply_modifiers=True`, `axis_forward`, `axis_up`, `global_scale`, `apply_scale_options`, `use_tspace`, `use_triangles`, `mesh_smooth_type`, `use_custom_props`, `colors_type`, glTF `export_apply`, `export_tangents`, `export_extras`, `export_yup`, `export_attributes`, `export_vertex_color`, `export_animation_mode`. Reply must include file size, object count, vert and tri counts written, and a `warnings` list (unapplied scale, missing UVs, ngons when `use_triangles` is off, materials without nodes). |
| `import_file` | No control over axis or scale, no report of what came in | Add `axis_forward`, `axis_up`, `global_scale`, `use_custom_normals=True`, `use_image_search`, `ignore_leaf_bones`, glTF `import_shading`, `merge_vertices`. Reply adds per-object vert/tri counts, `has_custom_normals`, `uv_layers`, material names, and `armatures`. |
| `get_mesh_stats` | Missing the numbers an engine cares about | Add `triangles` (via `calc_loop_triangles`), `ngons`, `quads`, `tris`, `loose_verts`, `loose_edges`, `non_manifold_edges`, `has_custom_normals`, `uv_layers`, `color_attributes`, `material_slots`, `dimensions`, `unapplied_transform` (rotation or scale not identity), `origin_offset` (origin vs bbox centre and vs bbox bottom). |
| `get_scene_info` | No per-object budgets | Add `total_triangles`, `total_vertices`, per-object `triangles`, and the scene unit settings (`system`, `scale_length`, `length_unit`). |
| `apply_modifier` | One modifier at a time | Add `apply_all=False` and `keep: str = None` (names to skip, e.g. Armature). |
| `set_origin` | Only the built-in origin types | Add `BOTTOM_CENTER` (bbox bottom centre, the game prop standard), `TOP_CENTER`, `custom` via `location="x,y,z"`. Implement by offsetting mesh data, not by cursor tricks. |

## 2. New tools

### 2.1 Tier 1, required

**Validation**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `validate_asset` | `objects` (comma list, or a collection name with `collection=`), `profile="GENERIC"` (GENERIC / UNITY / UNREAL / GODOT / BEVY / TIMBERMESH), `max_triangles=None`, `require_uv=True`, `require_uv2=False`, `max_bone_influences=4`, `fix=False` | THE pre-flight tool. Runs every check below and returns `{object: {check: {status: PASS/WARN/FAIL, detail, count, sample_indices}}}` plus an overall verdict and a list of fixes that `fix=True` would apply. Checks: unapplied rotation or scale; negative scale (mirrored, flips normals on import); origin not at bbox bottom centre (WARN) ; dimensions plausible for the profile (WARN if > 100 m or < 1 cm); ngons; non-manifold edges; loose verts and edges; degenerate faces (zero area); duplicate verts within 1e-5; inconsistent normals (faces whose normal points inward, via `bmesh.ops.recalc_face_normals` dry-run comparison); flipped normals count; missing UV layer; UV out of bounds; missing UV2 when required; > 65,535 verts (WARN for engines with 16-bit indices); triangle count over budget; material slots empty or unused; materials without nodes; images missing on disk; vertex groups with > `max_bone_influences` per vertex; object name or material name with characters the profile rejects (spaces, dots, unicode); name collisions after suffix stripping (`Cube.001`); collection or parent structure required by the profile (UCX_ children for UNREAL, `-col` suffix for GODOT, Slot empties for TIMBERMESH). `fix=True` applies only the safe fixes: apply transforms, recalc normals outside, remove doubles, delete loose, triangulate ngons, rename illegal characters, and reports every fix applied. |
| `find_mesh_issues` | `mesh`, `issue` (NON_MANIFOLD / NGONS / LOOSE / DEGENERATE / DOUBLES / FLIPPED / INTERIOR_FACES / BOUNDARY), `render=False`, `angle="iso_front_right"`, `max_indices=500` | Returns the element indices for one issue class. `render=True` selects them in edit mode and captures the viewport with the selection highlighted, then restores. The pairing for `validate_asset`: validate says what, this shows where. |
| `compare_meshes` | `mesh_a`, `mesh_b`, `tolerance=1e-5`, `compare="ALL"` (comma list of VERTS / ORDER / NORMALS / UVS / TANGENTS / MATERIALS / GROUPS) | Numeric diff for round-trip safety (the SPT lesson). Reports vert and tri count deltas, count of verts whose position differs by more than tolerance, whether vertex ORDER matches (index by index), custom-normal presence and max angular delta, UV layer names and max delta, tangent presence, material slot names, vertex-group names. Uses `mesh.unit_test_compare` as a first pass and its own numpy pass for the details. |
| `apply_transforms` | `objects`, `location=False`, `rotation=True`, `scale=True`, `keep_children_world=True` | `object.transform_apply` with multi-user data handled (`isolate_users=True` or make single user first, reported). |
| `fix_normals` | `objects`, `mode="OUTSIDE"` (OUTSIDE / INSIDE / FLIP / CLEAR_CUSTOM), `also_sharp_by_angle=None` (deg) | `bmesh.ops.recalc_face_normals`; CLEAR_CUSTOM drops custom split normals so a re-export does not carry stale ones. |
| `cleanup_mesh` | `objects`, `remove_doubles=0.0001`, `delete_loose=True`, `dissolve_degenerate=True`, `fill_holes=0`, `limited_dissolve=None` (deg) | One BMesh pass, reports counts removed per step. |

**Optimisation and LODs**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `decimate_mesh` | `mesh`, `ratio=None` or `target_triangles=None`, `mode="COLLAPSE"` (COLLAPSE / PLANAR / UNSUBDIVIDE), `angle_limit=5` (PLANAR), `iterations=2` (UNSUBDIVIDE), `symmetry_axis=None`, `vertex_group=None`, `vertex_group_factor=1.0`, `keep_uv_seams=True`, `triangulate=True`, `apply=True`, `new_object=None` | Decimate modifier configured then applied (or left live). `target_triangles` iterates ratio (max 6 passes) until within 5 %. `keep_uv_seams` sets `delimit={'UV','SEAM'}`. `new_object` duplicates first so the source stays intact. Reply: before and after tri counts, ratio used. |
| `generate_lods` | `mesh`, `ratios="0.5,0.25,0.1"` or `triangle_targets=...`, `naming="{name}_LOD{level}"`, `collection=None`, `parent_to_lod0=False`, `mode="COLLAPSE"`, `symmetry_axis=None`, `keep_uv_seams=True`, `preserve_silhouette_group=None` (vertex group to weight), `transfer_normals=False` | Renames the source to `_LOD0`, makes each level by `decimate_mesh(new_object=...)`, shares the materials, optional Data Transfer of custom normals from LOD0. Unity-style naming by default. For UNREAL the exporter puts them in a LodGroup (see `export_for_engine`). Reply: per level name and tri count. |
| `set_normals` | `objects`, `mode="AUTO_SMOOTH"` (AUTO_SMOOTH / WEIGHTED / SMOOTH / FLAT / FROM_OBJECT), `angle=30`, `keep_sharp=True`, `source_object=None`, `apply=True` | AUTO_SMOOTH = `object.shade_auto_smooth(angle)` (4.1+ this adds a Smooth by Angle modifier; apply it when `apply=True`). WEIGHTED = Weighted Normal modifier (`keep_sharp`, mode FACE_AREA_WITH_ANGLE), applied. FROM_OBJECT = Data Transfer custom normals from a high-poly (the LOD and bake workflow). Reply: modifier used, custom normals present after. |
| `triangulate_for_export` | `objects`, `quad_method="SHORTEST_DIAGONAL"`, `ngon_method="BEAUTY"`, `keep_custom_normals=True`, `min_vertices=4`, `as_modifier=False` | Triangulate modifier (keeps custom normals, unlike the BMesh op) applied, or left live when `as_modifier=True` so the source stays quads. |
| `get_triangle_budget` | `objects=None`, `budget=None`, `group_by="OBJECT"` (OBJECT / COLLECTION / MATERIAL) | Tri counts with modifiers evaluated (`evaluated_get` on the depsgraph) and share of `budget`. Sorted descending. |

**Collision**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `create_collision` | `mesh`, `shape="CONVEX"` (CONVEX / BOX / SPHERE / CAPSULE / CYLINDER / MESH_DECIMATED / SLICED_CONVEX), `profile="UNREAL"` (UNREAL / UNITY / GODOT / GENERIC), `count=1` (SLICED_CONVEX: pieces along `axis`), `axis="Z"`, `decimate_ratio=0.1`, `padding=0.0`, `display_wire=True`, `parent=True`, `collection=None` | Creates the proxy from the evaluated mesh: CONVEX via `bmesh.ops.convex_hull`; BOX/SPHERE/CAPSULE/CYLINDER fitted to the oriented bounds (`padding` grows it); SLICED_CONVEX bisects the mesh into `count` slabs and hulls each (poor man's decomposition, good enough for props). Names per profile: UNREAL `UCX_<mesh>_00`, GODOT `<mesh>-convcol` / `-col`, UNITY `<mesh>_Collider` (Unity has no naming convention, the mesh collider is assigned in the editor), GENERIC `<mesh>_COL`. Sets `display_type='WIRE'`, `hide_render=True`, no material. Reply: names, tri counts, volume ratio proxy/source. |
| `get_collision_info` | `mesh` | Lists proxies by the naming rules of every profile and their tri counts. |
| `check_collision_fit` | `mesh`, `samples=2000` | Fraction of surface sample points inside the union of proxies and max outside distance. Numeric perceive for "does the collider cover the mesh". |

**Sockets, pivots and hierarchy**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `add_socket` | `parent`, `name`, `location="0,0,0"`, `rotation="0,0,0"`, `space="LOCAL"` (LOCAL / WORLD), `profile="UNREAL"` (UNREAL / TIMBERMESH / UNITY / GENERIC), `bone=None`, `display_size=0.1`, `snap_to="NONE"` (NONE / SURFACE / VERTEX with `vertex_index`) | Creates an Empty parented to `parent` (or to a bone), named `SOCKET_<name>` for UNREAL, `#<name>` for TIMBERMESH (a `#` prefix keeps it as its own node in the Timbermesh hierarchy, see gotchas), plain name otherwise. Reply: full name, world matrix. |
| `list_sockets` | `parent=None` | Empties matching any profile prefix, with local and world transforms and the bone they follow. |
| `set_pivot` | `objects`, `mode="BOTTOM_CENTER"` (BOTTOM_CENTER / CENTER / TOP_CENTER / WORLD_ORIGIN / CURSOR / custom "x,y,z") , `keep_world=True` | Wrapper on the extended `set_origin`, batch. |
| `snap_to_grid` | `objects`, `grid=1.0`, `axes="XYZ"`, `what="ORIGIN"` (ORIGIN / BOUNDS_MIN / BOUNDS_CENTER) | Modular-kit placement helper. |
| `build_export_hierarchy` | `root_name`, `meshes`, `collision=None`, `sockets=None`, `lods=None`, `profile` | Creates or reuses a root Empty, parents everything under it in the layout the profile's importer expects (UNREAL: LODs siblings with a LodGroup custom prop, UCX_ children; GODOT: `-col` siblings; UNITY: LOD siblings). Reply: tree as nested names. |

**Engine export presets**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `export_for_engine` | `objects` (or `collection`), `engine` (UNITY / UNREAL / GODOT / BEVY / TIMBERMESH / GENERIC_GLTF / GENERIC_FBX), `filepath`, `include_hierarchy=True`, `include_collision=True`, `include_sockets=True`, `include_lods=True`, `include_animation=False`, `apply_modifiers=True`, `triangulate=True`, `validate=True`, `textures="COPY"` (COPY / EMBED / NONE / KEEP_PATHS), `overrides` JSON (any exporter kwarg) | Applies the preset table below, runs `validate_asset` first when `validate=True` and refuses on FAIL unless `overrides` has `"force": true`, selects the full hierarchy, exports, and writes a `<file>.manifest.json` beside the file (objects, tri counts, materials, textures copied, preset used, warnings). Presets: UNITY → FBX, `axis_forward='-Z'`, `axis_up='Y'`, `apply_scale_options='FBX_SCALE_ALL'`, `global_scale=1.0`, `use_tspace=True`, `mesh_smooth_type='FACE'`, `add_leaf_bones=False`, `bake_space_transform=False`; UNREAL → FBX, `axis_forward='-Y'`, `axis_up='Z'`, `global_scale=1.0` with scene unit scale 0.01 handled by `apply_scale_options='FBX_SCALE_NONE'` and a WARN if `scene.unit_settings.scale_length != 0.01` (offer `set_scene_units`), `use_tspace=True`, `mesh_smooth_type='FACE'`, `add_leaf_bones=False`, `use_custom_props=True`; GODOT and BEVY → GLB, `export_yup=True`, `export_apply=True`, `export_tangents=True`, `export_extras=True`, `export_attributes=True`, `export_vertex_color='MATERIAL'`, `export_image_format='AUTO'`; TIMBERMESH → the Timbermesh exporter class called directly on a collection (see gotchas), `merge_meshes=True`, `single_animation=True`, `use_vertex_animations=False`, all overridable; GENERIC_* → Blender defaults plus `include_hierarchy`. Reply: path, size, manifest path, validation summary. |
| `batch_export` | `objects=None` (all mesh roots in `collection` if given), `engine`, `output_dir`, `naming="{name}"`, `one_file_per="OBJECT"` (OBJECT / ROOT / COLLECTION), `skip_hidden=True`, `continue_on_error=True` | Loops `export_for_engine` per root with its hierarchy. Reply: manifest of files with PASS/FAIL per asset. |
| `set_scene_units` | `preset="UNREAL"` (UNREAL: metric, `scale_length=0.01`, cm; UNITY / GODOT / BEVY: metric, 1.0, m; or explicit `scale_length`, `length_unit`) | Also reports whether existing objects would need rescaling and does it when `rescale_objects=True`. |
| `get_export_preset` | `engine` | Returns the preset table so the caller can see exactly what `export_for_engine` will pass. |

### 2.2 Tier 2

| Tool | Parameters | Behaviour |
|---|---|---|
| `generate_lightmap_uv` | `objects`, `layer_name="UVMap_Lightmap"`, `margin=0.03`, `method="SMART"` (SMART / PACK_EXISTING / LIGHTMAP_PACK), `set_active_render=False` | Creates UV2 by smart-projecting with a large margin, or packing a copy of UV1. Uses `add_uv_layer` and `pack_uv_islands` from the texturing request. Unreal wants it as UV index 1. |
| `remesh_object` | `mesh`, `mode="QUADRIFLOW"` (QUADRIFLOW / VOXEL), `target_faces=5000`, `voxel_size=0.02`, `preserve_sharp=True`, `preserve_boundary=True`, `use_symmetry=True`, `new_object=None` | Quadriflow for game-ready retopo starting points, voxel for cleanup of kitbashed boolean messes. |
| `reduce_bone_influences` | `mesh`, `limit=4`, `normalize=True` | `vertex_group_limit_total` + normalize (mobile and Unity default). |
| `check_vertex_count_split` | `mesh`, `limit=65535`, `split=False` | Reports whether the mesh exceeds 16-bit index range after triangulation and, with `split=True`, separates it by loose parts or by a bisect into pieces under the limit. |
| `make_single_user` | `objects`, `what="OBDATA"` | Fixes shared-mesh surprises before per-object edits. |
| `export_timbermesh` | `objects`, `filepath` | Thin wrapper once the plugin operator is confirmed (gotchas). |
| `merge_for_export` | `objects`, `result_name`, `keep_originals=True`, `merge_materials=True`, `merge_uv_layers=True` | Joins a kit-built prop into one mesh in a separate collection for export while keeping the editable parts. |
| `set_custom_properties` / `get_custom_properties` | `object`, `props` JSON | For glTF extras and FBX `use_custom_props` (Bevy reads extras; Unreal reads FBX custom props into metadata). |

### 2.3 Tier 3, later (do NOT build unless asked)

V-HACD style convex decomposition (needs an external binary or a Python port), impostor
baking (belongs with presentation), automatic LOD screen-size tables, USD export presets.

## 3. Ripple points beyond the standard list

- `TOOLS.md` and `README.md`: new sections "Validation", "Optimisation & LODs",
  "Collision", "Sockets & Hierarchy", "Engine Export".
- `/blender` skill: a "Ship an asset to <engine>" pattern that runs
  validate → generate_lods → create_collision → add_socket → export_for_engine, and the
  preset table copied verbatim.
- MemPalace: a decisions drawer with the preset table and the Timbermesh findings.

## 4. Blender facts, verified headlessly on 4.3.2 (2026-09-07) and re-verified on 4.3.2 and 5.2.1 (2026-09-11) (do not re-derive)

Evidence for the 2026-09-11 re-verification: `apichecks/b2check.py` run on both installs with
REAL calls (modifiers applied, files exported and re-imported), lines under `### b2check.py`
in `apichecks/out_4.3.2.jsonl` and `out_5.2.1.jsonl` (Planner/API, B2-G0). Bullets below hold
on both versions unless section 4.1 lists a switch.

- FBX exporter kwargs present: `axis_forward, axis_up, global_scale, apply_unit_scale,
  apply_scale_options (FBX_SCALE_NONE / FBX_SCALE_UNITS / FBX_SCALE_CUSTOM / FBX_SCALE_ALL),
  use_space_transform, bake_space_transform, object_types (EMPTY / CAMERA / LIGHT /
  ARMATURE / MESH / OTHER), use_mesh_modifiers, mesh_smooth_type (OFF / FACE / EDGE),
  use_tspace, use_triangles, use_custom_props, colors_type, prioritize_active_color,
  path_mode, embed_textures, batch_mode, use_batch_own_dir, use_metadata,
  bake_anim_use_all_actions, bake_anim_use_nla_strips, use_armature_deform_only,
  armature_nodetype, add_leaf_bones`.
- glTF exporter kwargs present: `export_format, export_apply, export_tangents,
  export_extras, export_yup, export_draco_mesh_compression_enable, export_texcoords,
  export_normals, export_attributes, use_mesh_edges, export_lights, export_cameras,
  use_selection, use_visible, use_active_collection, export_animation_mode (ACTIONS /
  ACTIVE_ACTIONS / BROADCAST / NLA_TRACKS / SCENE), export_nla_strips,
  export_bake_animation, export_optimize_animation_size, export_anim_single_armature,
  export_reset_pose_bones, export_image_format, export_jpeg_quality, export_keep_originals,
  export_texture_dir, export_vertex_color (MATERIAL / ACTIVE / NONE), export_gn_mesh,
  export_shared_accessors, export_hierarchy_full_collections, export_original_specular,
  export_unused_images, export_unused_textures, export_try_sparse_sk, use_renderable`.
  `export_colors` does NOT exist in 4.3 (replaced by `export_vertex_color`).
- `wm.obj_export` kwargs: `forward_axis, up_axis, global_scale, apply_modifiers,
  export_eval_mode, export_selected_objects, export_uv, export_normals, export_colors,
  export_materials, export_pbr_extensions, path_mode, export_triangulated_mesh,
  export_vertex_groups, export_smooth_groups, smooth_group_bitflags`. OBJ has no tangents
  and welds on re-import: never use it for a shipping round trip.
- Decimate modifier props: `decimate_type, ratio, iterations, angle_limit, vertex_group,
  invert_vertex_group, use_collapse_triangulate, use_symmetry, symmetry_axis,
  vertex_group_factor, use_dissolve_boundaries, delimit, face_count` (read-only result).
- Weighted Normal modifier props: `weight, mode, thresh, keep_sharp, vertex_group,
  use_face_influence`. Triangulate modifier: `quad_method, ngon_method, min_vertices,
  keep_custom_normals`.
- `object.shade_auto_smooth(use_auto_smooth, angle)` exists (adds the Smooth by Angle
  geometry-nodes modifier in 4.1+). `Mesh.use_auto_smooth` is gone.
- Mesh methods present: `calc_tangents, calc_loop_triangles, normals_split_custom_set,
  normals_split_custom_set_from_vertices, validate, calc_smooth_groups, shade_smooth,
  shade_flat, has_custom_normals, polygon_normals, corner_normals, vertex_normals,
  transform, flip_normals, unit_test_compare`. `calc_normals_split` is GONE in 4.3.
- BMesh ops present: `convex_hull, remove_doubles, dissolve_limit, triangulate,
  join_triangles, recalc_face_normals, holes_fill, bisect_plane, symmetrize, mirror,
  dissolve_degenerate, find_doubles, weld_verts, split_edges, planar_faces, unsubdivide`.
- Ops: `mesh.select_non_manifold(extend, use_wire, use_boundary, use_multi_face,
  use_non_contiguous, use_verts)`, `mesh.select_interior_faces()`,
  `mesh.select_face_by_sides(number, type, extend)`, `mesh.decimate(ratio, ...)`,
  `mesh.dissolve_limited(angle_limit, use_dissolve_boundaries, delimit)`,
  `mesh.normals_make_consistent(inside)`, `mesh.fill_holes(sides)`,
  `mesh.delete_loose(use_verts, use_edges, use_faces)`, `object.transform_apply(location,
  rotation, scale, properties, isolate_users)`, `object.origin_set(type, center)` with
  types `GEOMETRY_ORIGIN / ORIGIN_GEOMETRY / ORIGIN_CURSOR / ORIGIN_CENTER_OF_MASS /
  ORIGIN_CENTER_OF_VOLUME`, `object.quadriflow_remesh(use_mesh_symmetry,
  use_preserve_sharp, use_preserve_boundary, preserve_attributes, smooth_normals, mode,
  target_ratio, target_edge_length, target_faces, mesh_area, seed)`,
  `object.voxel_remesh()` (reads `mesh.remesh_voxel_size`), `object.convert(target,
  keep_original, ...)`, `object.duplicates_make_real(use_base_parent, use_hierarchy)`,
  `object.data_transfer(data_type, use_create, vert_mapping, loop_mapping, poly_mapping,
  use_object_transform, mix_mode, mix_factor, ...)`.
- `object.lod_add` does NOT exist (Blender Game Engine leftover). LODs are naming only.
- `scene.unit_settings` props: `system, system_rotation, scale_length, use_separate,
  length_unit, mass_unit, time_unit, temperature_unit`.
- Object props present: `display_type, display_bounds_type, show_bounds, instance_type,
  instance_collection, empty_display_type, empty_display_size, dimensions, bound_box,
  hide_render, hide_viewport, color, pass_index, rigid_body, collision`.
- The 3D-Print Toolbox is NOT bundled in 4.3 (it moved to the extensions platform);
  implement the manifold and degenerate checks with BMesh, do not depend on it.
- Bundled add-on modules on this install: `copy_global_transform, cycles, hydra_storm,
  io_anim_bvh, io_curve_svg, io_mesh_uv_layout, io_scene_fbx, io_scene_gltf2,
  node_wrangler, pose_library, rigify, timbermesh_blender_plugin, ui_translate,
  viewport_vr_preview`.
- Timbermesh plugin (verified from source, `%APPDATA%\Blender Foundation\Blender\4.3\
  scripts\addons\timbermesh_blender_plugin\`, v1.2.0, author Mechanistry, manual at
  https://github.com/mechanistry/timbermesh/wiki/Timbermesh-Blender-Plugin-manual):
  - It exports a COLLECTION, not a selection. Operators: `export_collection.timbermesh`
    (`filepath, merge_meshes=True, single_animation=True, use_vertex_animations=False`)
    and `export_collections.timbermesh` (batch, `directory, append_model_to_name`). Both
    read `context.selected_ids` (outliner selection), which does not exist headless or in
    the MCP context. Call the exporter class directly instead:
    `from timbermesh_blender_plugin import timbermesh_exporter as te;
    te.Exporter.export_collection(collection, path,
    te.ExportSettings(bpy.context, merge_meshes, single_animation, use_vertex_animations))`.
    The plugin's modules use bare imports (`import blender_utils`), so enable the add-on
    first (`addon_utils.enable("timbermesh_blender_plugin", default_set=False)`) so its
    folder is on `sys.path`.
  - Exportable objects are MESH and EMPTY only (parents pulled in automatically); the
    export sets `frame_set(0)` first; meshes are split per MATERIAL NAME (so names must
    match the game's material names exactly); vertex data written: position, normal,
    tangent, uv0, uv1, uv2, colour.
  - Hierarchy rule: an object whose name starts with `#` is a ROOT node and always gets
    its own node. With `merge_meshes=True` every other object merges into its nearest
    root ancestor's node. Attach points ("Slot" in the Timberborn community's terms) are
    therefore EMPTY objects that must keep their own node: name them `#<SlotName>` and
    parent them under the model root. The exact slot names Timberborn reads (for example
    for building attachment points) are game-side and NOT in the plugin; leave the
    `Slot_` prefix out and let the caller pass the full name. `add_socket(profile=
    TIMBERMESH)` therefore names the empty `#<name>` and errors if the parent chain
    contains no `#` root.
  - `export_for_engine(TIMBERMESH)` must: require a `collection` (or create a temporary
    one containing the object hierarchy), verify a `#` root exists, run the exporter class
    directly, and remove the temporary collection in `finally`.
- `evaluated_get(depsgraph)` is the only correct way to count triangles with modifiers.

### 4.1 4.x/5.x switches per tool (B2-G0, `b2check.py` on both installs)

| Tool | Fact | 4.3.2 | 5.2.1 |
|---|---|---|---|
| `boolean_operation`, `make_collision` (boolean cuts) | modifier solver enum | `FAST` / `EXACT` | `FLOAT` / `EXACT` / `MANIFOLD`; identical geometry for DIFFERENCE with every solver (9 faces / 14 verts) |
| edit-mode boolean (`mesh.intersect_boolean`) | operator solver enum | `FAST` / `EXACT` | `FLOAT` / `EXACT` (NO `MANIFOLD` on the operator) |
| `decimate`, `build_lods` | Decimate modifier | `decimate_type` COLLAPSE / UNSUBDIV / DISSOLVE (the doc's "planar" = DISSOLVE, "unsubdivide" = UNSUBDIV); COLLAPSE 0.5: 512 -> 480 faces, `face_count` readable BEFORE apply after `view_layer.update()`; `delimit={'UV','SEAM'}`, `use_symmetry` + `symmetry_axis`; DISSOLVE 5 deg 96 -> 6; UNSUBDIV 2 iterations -> 24 | same |
| `triangulate_for_export` | Triangulate modifier | `quad_method` BEAUTY / FIXED / FIXED_ALTERNATE / SHORTEST_DIAGONAL / LONGEST_DIAGONAL, `ngon_method` BEAUTY / CLIP, `keep_custom_normals`; applied -> all tris | same |
| `set_normals` (weighted) | Weighted Normal modifier | FACE_AREA_WITH_ANGLE + keep_sharp applied -> `has_custom_normals` True | same |
| `set_normals` AUTO_SMOOTH | operator | `object.shade_auto_smooth(angle)` is CANCELLED headless (no modifier); `object.shade_smooth_by_angle(angle, keep_sharp_edges)` FINISHES and marks the sharp edges (32 on the test mesh), no modifier | `shade_auto_smooth` FINISHES and adds a "Smooth by Angle" NODES modifier (inputs `Input_0` / `Input_1` / `Socket_1`; applying it marks the same 32 sharp edges, no custom normals); `shade_smooth_by_angle` identical to 4.3.2. Rule: AUTO_SMOOTH = `shade_smooth_by_angle` on both; the modifier form is a 5.x-only option |
| `set_normals` custom / clear / transfer | data API | `Mesh.use_auto_smooth` absent; `normals_split_custom_set_from_vertices`, `corner_normals` (96), `normals_domain` CORNER, `calc_smooth_groups` -> (18 groups, 3), `customdata_custom_splitnormals_clear` clears `has_custom_normals`; Data Transfer CUSTOM_NORMAL POLYINTERP_NEAREST from a hi-poly, applied -> custom normals | same |
| `validate_asset` (tangents) | `calc_tangents` | ABORTS on a mesh with ngons ("only for tris/quads"): flag ngons before any tangent export | same |
| `apply_transforms` | `transform_apply` kwargs | standard kwargs; `isolate_users=True` on a shared mesh -> users 1, own data | adds `corrective_flip_normals` (try-kwargs) |
| `tris_to_quads` | `tris_convert_to_quads` | 12 tris -> 6 quads | adds `topology_influence`, `deselect_joined` (try-kwargs) |
| `validate_asset` (topology) | edit-mode selects | `mesh.decimate(ratio)`, `select_non_manifold`, `select_face_by_sides(GREATER 4)`, `select_interior_faces` all run headless with `select_mode` set; counts read from `bmesh.from_edit_mesh` | same |
| `export_for_engine` FBX | round trip | export `use_tspace` / `use_triangles` / `use_custom_props` / FACE, `object_types` EMPTY+MESH, then import `use_custom_normals` / `use_custom_props`: root EMPTY, mesh, `UCX_<name>_00` child mesh and `SOCKET_<name>` child empty all return with names, hierarchy, custom props (`lod_group`, int), socket location, custom normals, 12 tris each | identical; `mesh_smooth_type` SMOOTH_GROUP exports on 5.2.1 only (enum error on 4.3.2: validate against the live enum) |
| `export_for_engine` glTF | kwargs and re-import | `export_vertex_color` MATERIAL works; `export_all_vertex_colors`, `export_active_vertex_color_when_no_material` exist; re-import keeps extras (custom props) but RENAMES colour attributes `Color` / `Color.001` (names lost); import kwargs `import_shading` NORMALS/FLAT/SMOOTH, `merge_vertices`, `bone_heuristic`, `disable_bone_shape` (pass True in `import_file` to keep the importer's Icosphere out, K8 at the source) | adds `export_vertex_color` NAME (needs `export_vertex_color_name=<attr>`), import adds a `custom_normal` attribute and kwargs `import_scene_extras`, `import_select_created_objects`, `import_merge_material_slots`, `import_unused_materials` |
| `export_for_engine` OBJ | transforms | `obj_export` ALWAYS bakes the object transform into the vertices (no `apply_transform` kwarg); `export_smooth_groups` writes `s` lines; `obj_import` lands at the origin | `apply_transform` kwarg exists; `apply_transform=False` writes local coords |
| `make_collision` (hull, boxes) | BMesh | `bmesh.ops.convex_hull` returns `geom` / `geom_holes` / `geom_interior` / `geom_unused` (delete interior + unused -> hull of the torus 476 faces; `bm.calc_volume` 3.87 vs 1.16 source); `bisect_plane(clear_inner)` halves it (192 faces); `display_type` WIRE, `hide_render`, `parent`, `bound_box`, `dimensions` all usable | same |
| `batch_rename`, naming checks | data-block names | max name length 63 bytes; collision suffix `.001` / `.002` | max 255 bytes; same suffixes |
| `get_mesh_stats` (evaluated) | `evaluated_get` | subsurf 6 polys -> 192 tris | same |
| `compare_meshes` | `mesh.unit_test_compare(threshold=)` | "Same" or a difference string mentioning "vertex attributes" | difference string says "point attributes": compare by prefix, not exact text |
| numpy paths | dtype | `foreach_get` into a float64 buffer | same; `numpy.array(Vector)` is float32: always pass `dtype=float64` (M12) |
| `set_origin` bottom-centre | `mesh.transform` + location | works | same |
| Timbermesh export | plugin presence | `timbermesh_blender_plugin` present in the 4.3 user add-ons | ABSENT on 5.2.1 (not installed there yet; teach-style error until the user installs it) |

## 5. Testing (required before you report done)

Headless, `--background --factory-startup`, handlers called directly:

1. `add_primitive` cube "Prop", scale it 2,1,1 without applying, rotate 45°: `validate_asset`
   FAILs unapplied transforms and WARNs origin; `validate_asset(fix=True)` clears both and
   re-running passes.
2. Monkey "Suz": `get_mesh_stats` reports triangles, ngons 0, `find_mesh_issues(BOUNDARY)`
   returns the eye-socket boundary edges (count > 0).
3. `generate_lods("Suz", ratios="0.5,0.25")` produces `Suz_LOD0..2` with descending tri
   counts within 10 % of target; `set_normals(WEIGHTED)` leaves `has_custom_normals` True.
4. `create_collision("Suz", CONVEX, UNREAL)` yields `UCX_Suz_00` under Suz; `check_collision_fit`
   reports > 0.95 covered. `SLICED_CONVEX count=3` yields three UCX children.
5. `add_socket("Suz", "Muzzle", profile=UNREAL)` yields `SOCKET_Muzzle` parented; `list_sockets`
   finds it.
6. `export_for_engine(UNREAL)` to a temp FBX: manifest exists, lists 3 LODs, 3 collision
   objects, 1 socket; re-import in a fresh scene and assert the object names survive.
7. `export_for_engine(BEVY)` GLB: re-import, `compare_meshes` between the LOD0 source and the
   re-imported mesh reports identical vert count and ORDER, max position delta < 1e-5,
   tangents present in the file (check the GLB JSON chunk for a TANGENT attribute).
8. `set_scene_units(UNREAL)` sets `scale_length` 0.01 and `export_for_engine(UNREAL)` no longer
   WARNs.
9. `batch_export` on a collection of 3 props writes 3 files and a manifest with 3 PASS rows.
10. Timbermesh: collection "Prop" with a `#Prop` root empty, a mesh child and a `#Socket`
    empty child: `export_for_engine(TIMBERMESH)` writes a `.timbermesh` file > 100 bytes
    whose zlib-decompressed protobuf contains the strings `Prop` and `Socket`.
    `add_socket(profile=TIMBERMESH)` on a parent chain with no `#` root returns an error.

Report PASS / FAIL per step with error text. Do not weaken an assertion to make it pass.
