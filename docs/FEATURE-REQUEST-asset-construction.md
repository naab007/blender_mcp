# Feature request: asset construction (modelling gaps, modular kits, environment)

Status: OPEN, requested 2026-09-07
Target version: 1.10.0
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`)
Categories covered (see `docs/README.md`): 6 modelling and hard-surface gaps, 7 modular
kits and blockout, 8 environment assets
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here.

## 0. Scope and why

The mesh tools that exist are element-level (vertex, edge, face ops) plus primitives,
booleans and generic modifiers. Building a game prop from them takes dozens of calls and
still leaves the common hard-surface operations to raw code: cutting, mirroring, bridging,
solidifying, arrays along curves, symmetry, weighted bevels. Level work has nothing: no
grid, no kit pieces with known bounds, no instancing, no greybox. Environment work has
nothing: no terrain, no foliage cards, no scatter, no decals.

The goal is fewer, higher-level calls that each do one thing an artist would do in a
single operator, with a numeric or visual readback so the agent can verify without a
render.

Build what is in this document. If something is wrong or impossible on 4.3.2, say so in
the report and continue.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `add_primitive` | No segments or vertex-count control, no depth for cylinder/cone, no `radius1/radius2` for cones | Add `segments`, `ring_count`, `depth`, `radius`, `radius2` (cone tip), `subdivisions` (ico sphere), `calc_uvs=True`, `align_to="WORLD"`. Reply the resulting tri count. |
| `boolean_operation` | Deletes the cutter unconditionally; no hole-only workflow; no multi-cutter | Add `keep_cutter=False`, `cutters` (comma list, applied sequentially), `hide_cutter_instead=False`. Report `use_self` and `use_hole_tolerant` when EXACT. |
| `add_modifier` | Object-pointer props accept names, but curve and collection pointers untested | Confirm ARRAY `offset_object`, CURVE `object`, and `instance_collection` style props resolve by name; add to the unset reason text which pointer types are supported. |
| `duplicate_object` | No array-style multi-duplication | Add `count=1` with `offset` applied cumulatively, and `linked=True` default for kit pieces (shared mesh data). |
| `join_objects` | Loses per-object identity | Reply the vertex index range each source occupied so `separate_mesh` by material or by loose parts can be reasoned about later. |
| `set_smooth_shading` | Angle-based smoothing adds the geometry-nodes modifier in 4.1+ | Document and expose `apply=True` so exports do not depend on a live modifier. |

## 2. New tools

### 2.1 Tier 1, required

**Hard-surface and mesh operations**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `bisect_mesh` | `mesh`, `plane_point="0,0,0"`, `plane_normal="1,0,0"`, `space="LOCAL"`, `clear_inner=False`, `clear_outer=False`, `fill=False`, `face_indices=None` | `bmesh.ops.bisect_plane` on all or selected faces. The cut-in-half-and-mirror workflow. Reply: verts and faces after, cut edge count. |
| `mirror_mesh` | `mesh`, `axis="X"`, `merge_threshold=0.001`, `clip=True`, `mirror_u=False`, `bisect_first=True`, `apply=False` | Mirror modifier configured (with optional bisect) or, with `apply=True`, `bmesh.ops.mirror` + `weld_verts`. Reply: modifier name or merged vert count. |
| `symmetrize_mesh` | `mesh`, `direction="POSITIVE_X"`, `threshold=0.0001` | `bmesh.ops.symmetrize`. Reply: vert count before and after. |
| `bridge_edge_loops` | `mesh`, `loop_a` (edge indices), `loop_b`, `segments=1`, `interpolation="LINEAR"` (LINEAR / PATH / SURFACE), `smoothness=0`, `twist=0` | `bmesh.ops.bridge_loops`, or the `mesh.bridge_edge_loops` op when `segments > 1` (edit mode, restore). |
| `loop_cut` | `mesh`, `edge_index`, `cuts=1`, `smoothness=0` | Finds the edge ring from `edge_index` and subdivides it (`bmesh.ops.subdivide_edgering` or `subdivide_edges` with `use_grid_fill`). Reply: new edge indices. |
| `bevel_edges` | `mesh`, `edge_indices=None` (by weight when None), `width=0.02`, `segments=2`, `profile=0.5`, `clamp_overlap=True`, `harden_normals=False`, `use_weight=False`, `apply=True` | BMesh bevel on explicit edges, or a Bevel modifier in WEIGHT limit mode when `use_weight=True` (pairs with the existing `set_edge_bevel_weight`). |
| `solidify_mesh` | `mesh`, `thickness=0.05`, `offset=-1`, `even=True`, `rim=True`, `apply=True` | Solidify modifier or `bmesh.ops.solidify`. Thin walls, sheets, cloth props. |
| `array_along_curve` | `mesh`, `curve`, `count=None`, `fit="CURVE"` (CURVE / COUNT / LENGTH), `length=None`, `merge=True`, `deform=True`, `apply=False` | Array + Curve modifier pair (fences, pipes, cables, rails). Reply: instance count. |
| `array_object` | `mesh`, `count=3`, `offset="1,0,0"`, `relative=True`, `merge=False`, `merge_distance=0.001`, `apply=False` | Array modifier; the earlier `add_modifier` can do this but the shorthand is the common case. |
| `dissolve_edges` / `dissolve_verts` / `limited_dissolve` | `mesh`, `indices` / `angle=5` | Cleanup after booleans. |
| `select_by_material` / `select_by_normal` / `select_by_bounds` | `mesh`, `material_index` / `direction`, `angle` / `min`, `max` | Return element index lists for the other tools; no selection state kept. |
| `separate_by` | `mesh`, `by="MATERIAL"` (MATERIAL / LOOSE / SELECTION with `face_indices`) | Extends `separate_mesh` with an explicit face list. Reply: new object names with tri counts. |
| `shrinkwrap_to` | `mesh`, `target`, `mode="NEAREST_SURFACEPOINT"`, `offset=0.0`, `vertex_group=None`, `apply=True` | Conform decals, retopo sheets, clothing, ground-hugging props. |
| `knife_project` | `mesh`, `cutter` (mesh or curve), `angle="top"` (a `_VIEW_PRESETS` key, defines the projection direction), `cut_through=True` | `mesh.knife_project` under the viewport override. Falls back with an error in background mode. |
| `remesh_object` | see engine-readiness Tier 2, shared | |

**Modular kits, grid and blockout**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `set_grid` | `size=1.0`, `subdivisions=10`, `snap=True`, `snap_target="INCREMENT"`, `scale_grid=True` | Sets the viewport grid and snap settings (every 3D area) and stores the kit unit in `scene["blendermcp_kit_unit"]`. |
| `snap_to_grid` | shared with engine-readiness | |
| `create_kit_piece` | `name`, `kind="WALL"` (WALL / FLOOR / CORNER_IN / CORNER_OUT / DOORWAY / WINDOW / PILLAR / STAIR / RAMP / ARCH / CUSTOM), `unit=None` (kit unit), `size="1,1,1"` (multiples of unit), `thickness=0.1`, `opening="0.5,0.8"` (doorway/window w,h), `pivot="BOTTOM_CENTER"`, `collection="Kit"`, `material=None` | Parametric greybox pieces with the pivot at the bottom-back-centre and dimensions on exact unit multiples, so they snap edge to edge. Reply: dimensions, pivot, tri count. |
| `get_bounds` | `objects`, `space="WORLD"` | Per object: min, max, centre, dimensions, bottom-centre, and unit multiples relative to the kit unit (`fits_grid` flag with the residual). |
| `place_instances` | `source` (object or collection), `positions` JSON list of `[x,y,z]` or `{loc, rot, scale}`, `linked=True`, `collection=None`, `snap=True`, `name_pattern="{source}_{i:03d}"` | Bulk placement of kit pieces or props by explicit transforms. Collection instances when `source` is a collection. Reply: names. |
| `place_along` | `source`, `start`, `end`, `count=None`, `spacing=None`, `align_to_path=True`, `curve=None` | Fence posts, lamp posts, railings. |
| `place_grid` | `source`, `origin`, `counts="4,4,1"`, `spacing="1,1,1"`, `stagger=0` | Tiles, floors, crates. |
| `make_instances_real` | `objects`, `keep_hierarchy=True` | `object.duplicates_make_real`. |
| `greybox_from_bounds` | `name`, `min`, `max`, `hollow=False`, `wall_thickness=0.2`, `openings` JSON | Room or volume blockout in one call. |
| `get_object_neighbors` | `object`, `radius=0.01`, `axis=None` | Which kit pieces touch or overlap this one (bbox tests). The numeric "did it snap" readback. |
| `kitbash_join` | `objects`, `result_name`, `boolean="UNION"` (UNION / NONE), `cleanup=True`, `keep_originals=True` | Join, optional union, `cleanup_mesh`, reply tri count. |

**Environment assets**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `create_terrain` | `name`, `heightmap` (image path or Blender image, or `None` for noise), `size="100,100"`, `height=10.0`, `resolution=256`, `noise` JSON (`type`, `scale`, `octaves`, `seed`) when no heightmap, `uv=True`, `smooth=True` | Subdivided grid displaced by the heightmap (Displace modifier with an image texture, applied) or by a Musgrave/Noise texture. Reply: tri count, min and max height. |
| `sample_terrain_height` | `terrain`, `points` JSON | Ray-cast heights for placing props; `place_instances` accepts `snap_to_surface=<terrain>` to use this. |
| `create_foliage_card` | `name`, `texture` (path with alpha), `width=1.0`, `height=1.0`, `cards=2` (crossed planes), `bend=0.0`, `double_sided=True`, `pivot="BOTTOM_CENTER"`, `vertex_color_wind=True` | Alpha-clipped card material (Base Color + Alpha, `blend_method`/`surface_render_method` set), crossed planes, optional vertex-colour gradient (bottom black, top white) for wind shaders. |
| `scatter_objects` | `sources` (object or collection), `surface`, `count=100`, `density=None`, `seed=0`, `align_to_normal=True`, `min_scale=0.8`, `max_scale=1.2`, `random_rotation_z=True`, `slope_max=45`, `min_distance=0.0`, `vertex_group=None` (density mask), `linked=True`, `collection="Scatter"`, `as_instances=True` | Surface sampling (face-area weighted, Poisson-ish rejection by `min_distance`), instances placed. Reply: count placed, count rejected by slope. |
| `create_decal` | `name`, `texture`, `target`, `location`, `size="0.5,0.5"`, `normal=None` (ray-cast from location if None), `offset=0.002`, `shrinkwrap=True` | Plane with an alpha material, oriented to the surface normal, shrinkwrapped with offset, parented to `target`. |
| `apply_trim_sheet` | `mesh`, `face_indices`, `sheet_image`, `trim_rect="u0,v0,u1,v1"`, `fit="STRETCH"` (STRETCH / TILE_U / TILE_V), `rotate=0` | Maps the faces' UVs into a rectangle of a trim sheet. Relies on `get_uvs`/`set_uvs` from the texturing request. |

### 2.2 Tier 2

| Tool | Parameters | Behaviour |
|---|---|---|
| `inset_extrude_panel` | `mesh`, `face_indices`, `inset=0.02`, `depth=-0.01` | The hard-surface panel line in one call. |
| `add_edge_loops_at` | `mesh`, `edge_index`, `positions="0.1,0.9"` | Support loops for subdivision. |
| `create_pipe` | `name`, `points` JSON, `radius=0.05`, `bevel_resolution=8`, `fill_caps=True`, `to_mesh=True` | Curve with bevel depth, converted. |
| `create_cable` | `name`, `start`, `end`, `sag=0.2`, `radius=0.01` | Catenary-ish curve to mesh. |
| `create_stairs` / `create_railing` / `create_ladder` | parametric | Kit generators. |
| `create_rock` | `name`, `size`, `seed`, `detail=2`, `flatten_bottom=True` | Ico sphere + displace noise + decimate. |
| `create_crystal` / `create_barrel` / `create_crate` | parametric | Common prop starters. |
| `slice_by_grid` | `mesh`, `grid` | Cuts a large mesh into chunks for streaming or occlusion. |
| `align_to_surface` | `objects`, `surface`, `up="Z"` | Snap props to terrain or floors with normal alignment. |
| `randomize_transforms` | `objects`, `location`, `rotation`, `scale`, `seed` | Break up repetition in scattered kits. |
| `create_lod_impostor_card` | see presentation request | |
| `heightmap_from_mesh` | `mesh`, `resolution`, `output_path` | The reverse of `create_terrain`, for engines that want a heightmap. |
| `road_from_curve` | `name`, `curve`, `width`, `segments`, `uv_along=True`, `conform_to=None` | Ribbon mesh along a curve with UVs running along it, optionally shrinkwrapped to terrain. |

### 2.3 Tier 3, later (do NOT build unless asked)

Geometry-nodes based procedural kits, building generators, river and cliff tools,
L-system foliage, wall-run boolean cutters with auto-trim.

## 3. Ripple points beyond the standard list

- `TOOLS.md` and `README.md`: sections "Hard-Surface", "Kits & Blockout", "Environment".
- `/blender` skill: a "Modular kit" pattern (set_grid → create_kit_piece × N → place_grid →
  get_object_neighbors) and an "Environment" pattern (create_terrain → scatter_objects →
  create_foliage_card).

## 4. Blender 4.3.2 facts, verified headlessly 2026-09-07 (do not re-derive)

- BMesh ops present: `bisect_plane, mirror, symmetrize, bridge_loops, bevel,
  subdivide_edges, inset_region, solidify, dissolve_limit, dissolve_degenerate,
  weld_verts, remove_doubles, find_doubles, split_edges, planar_faces, unsubdivide,
  holes_fill, edgenet_fill, beautify_fill, poke, wireframe, offset_edgeloops,
  connect_verts, contextual_create, region_extend, smooth_vert, smooth_laplacian_vert,
  spin, scale, rotate, translate, transform, reverse_faces, delete, triangulate,
  join_triangles, recalc_face_normals, convex_hull, create_uvsphere, create_cube`.
- Ops present with these props: `mesh.bisect(plane_co, plane_no, use_fill, clear_inner,
  clear_outer, threshold, ...)`, `mesh.symmetrize(direction, threshold)`,
  `mesh.symmetry_snap(direction, threshold, factor, use_center)`,
  `mesh.knife_project(cut_through)` (viewport context required),
  `mesh.tris_convert_to_quads(face_threshold, shape_threshold, uvs, vcols, seam, sharp,
  materials)`, `mesh.dissolve_limited(angle_limit, use_dissolve_boundaries, delimit)`,
  `mesh.fill_holes(sides)`, `object.convert(target, keep_original, merge_customdata,
  angle, thickness, seams, faces, offset)`, `object.duplicates_make_real(use_base_parent,
  use_hierarchy)`, `object.quadriflow_remesh(...)`, `object.voxel_remesh()`.
- Object props present: `instance_type, instance_collection, display_type,
  display_bounds_type, show_bounds, empty_display_type, empty_display_size, dimensions,
  bound_box, hide_render, hide_viewport, color, pass_index`.
- Snapping (verified): `scene.tool_settings` has `use_snap, snap_elements,
  snap_elements_base, snap_elements_individual, snap_target, use_snap_grid_absolute`.
  Grid (verified): `View3DOverlay.grid_scale, grid_subdivisions, show_floor,
  show_axis_x/y/z` on every `SpaceView3D.overlay`.
- Terrain noise (verified): `ShaderNodeTexMusgrave` is GONE; `ShaderNodeTexNoise` has
  `noise_type` in MULTIFRACTAL / RIDGED_MULTIFRACTAL / HYBRID_MULTIFRACTAL / FBM /
  HETERO_TERRAIN. Legacy `bpy.data.textures` types for the Displace modifier: BLEND,
  CLOUDS, DISTORTED_NOISE, IMAGE, MAGIC, MARBLE, MUSGRAVE, NOISE, STUCCI, VORONOI, WOOD.
  Displace modifier props: `texture, texture_coords, texture_coords_object, uv_layer,
  direction, strength, mid_level, vertex_group, space`.
- Smooth by Angle (verified): there is no dedicated modifier type; `object.shade_auto_smooth`
  adds a NODES modifier backed by the bundled geometry-nodes asset. Apply it like any
  modifier before export when `apply=True`.
- `mesh.bridge_edge_loops` props: `type, use_merge, merge_factor, twist_offset,
  number_cuts, interpolation, smoothness, profile_shape_factor, profile_shape`.
  `bmesh.ops.bridge_loops(bm, edges, use_pairs, use_cyclic, use_merge, merge_factor,
  twist_offset)` has no segments; use the op for `segments > 1`.
  `bmesh.ops.subdivide_edgering` exists for `loop_cut`.
- `evaluated_get(depsgraph)` for tri counts after modifiers.

## 5. Testing (required before you report done)

Headless, `--background --factory-startup`, handlers called directly:

1. `add_primitive` cylinder with `segments=8`: tri count matches 8-segment expectation.
2. Cube "Body": `bisect_mesh` at x=0 with `clear_inner`, then `mirror_mesh(apply=True)`:
   vert count equals the original and `find_mesh_issues(DOUBLES)` returns 0.
3. `bevel_edges` on 4 edges of a cube with `segments=2`: face count rises as expected;
   `set_edge_bevel_weight` + `bevel_edges(use_weight=True)` bevels only weighted edges.
4. `solidify_mesh` on a plane: closed mesh, `find_mesh_issues(NON_MANIFOLD)` is 0.
5. `array_along_curve` on a bezier: instance count matches `fit=COUNT`.
6. `set_grid(1.0)`; `create_kit_piece(WALL, size="2,1,3")`: `get_bounds` reports
   `fits_grid` True and dimensions 2,0.1,3 with pivot at bottom-centre.
7. `place_grid` 3×3 of a floor piece; `get_object_neighbors` on the centre piece lists 4
   edge neighbours.
8. `create_terrain` from noise 64² and `scatter_objects(count=50, slope_max=30)`: placed +
   rejected == 50, every placed instance's z equals `sample_terrain_height` at its x,y
   within 1e-3.
9. `create_foliage_card` with a generated alpha image: 2 crossed planes, material has an
   Alpha link, vertex colour attribute exists with bottom 0 / top 1.
10. `create_decal` on the terrain: decal's z is above the surface by `offset` at its centre.
11. `kitbash_join` of 3 overlapping cubes with UNION: single manifold mesh.

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.
