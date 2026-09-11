# Feature request: UV and texturing tools

Status: OPEN, requested 2026-09-06
Target version: 1.8.0 (or fold into 1.7.0 if built in the same pass as the rigging request)
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`)
Sibling request: `FEATURE-REQUEST-rigging-and-animation.md` (same architecture rules; read its
section 0 and section 1 first, they are not repeated here)

## 0. Scope and why

The server can create a Principled material, assign it, and wire one image per call into a
slot. It cannot unwrap, mark seams, read or write UVs, show a UV layout, inspect a node
tree, list images, save or reload an image, bake anything, paint anything, or show a
textured model (the capture tools never change viewport shading, so Solid mode hides every
texture). All of that currently means raw `execute_blender_code`.

Division of labour with the ImageTools MCP (`D:\App Dev\ImageTools_MCP\`, live on B:):
image GENERATION and EDITING (Stable Diffusion, Qwen-Image-Edit, segmentation, layers,
Photoshop-style ops) stays in ImageTools. Blender MCP owns UVs, node wiring, baking, image
bookkeeping inside the .blend, and the perceive loop (UV layout, textured captures, material
previews). The handoff is a file path: Blender exports a UV layout or a baked map, ImageTools
paints on it, Blender reloads it. `reload_images` and `export_textures` exist for that
reason. Do not add generative image tools here.

Build what is in this document. If something is wrong or impossible on Blender 4.3.2, say so
in the report and continue with the rest.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `capture_viewport_angle`, `capture_contact_sheet`, `get_viewport_screenshot` | Never set shading, so textures only show if the user happened to leave the viewport in Material mode | Add `shading: str = None` (SOLID / MATERIAL / RENDERED / WIREFRAME). When given, set `space.shading.type` under the same `temp_override`, capture, restore in `finally`. Add `overlay` values `uv_checker` (temporarily assign a generated COLOR_GRID image to every material slot of the target objects via a temp material, capture, restore the original slots) and `vertex_color` (`shading.color_type='VERTEX'` in SOLID). |
| `load_texture` (addon + server) | Adds a NEW TexCoord + Mapping pair on every call, so a 4-map material has 4 duplicate pairs. Cannot choose a UV map. Reuses an image by basename only, so a re-exported file with the same name never updates. Slots limited to Base Color, Roughness, Metallic, Normal, Emission Color. | Reuse one `ShaderNodeTexCoord` + `ShaderNodeMapping` per material (find by a `["blendermcp"]` custom prop or by name `MCP_TexCoord`/`MCP_Mapping`). New params: `uv_map: str = None` (inserts a `ShaderNodeUVMap` when it is not the active map), `projection="FLAT"` (FLAT/BOX + `projection_blend`), `interpolation="Linear"`, `extension="REPEAT"`, `reload=True` (if an image of that basename exists but `filepath` differs or the file mtime is newer, reload it). New slots: `Alpha`, `Height` (via `ShaderNodeBump`, `strength` param), `Displacement` (via `ShaderNodeDisplacement` into the Material Output Displacement socket, sets `material.displacement_method`), `AO` (multiplied into Base Color through a `ShaderNodeMix` COLOR MULTIPLY, `ao_strength`), `ORM` (packed AO/Roughness/Metallic through `ShaderNodeSeparateColor`, R → AO, G → Roughness, B → Metallic), `Specular` → `Specular IOR Level`, `Coat`, `Sheen`, `Subsurface`. Non-Color colourspace for every non-colour slot. Reply the node names created so `get_material_info` and `set_shader_node_input` can address them. |
| `create_material` | `nodes.clear()` on an existing name silently wipes its textures | Add `replace: bool = False`. When the material exists and `replace` is False, only update the passed Principled values and keep the tree. |
| `get_object_info` | No UV, colour-attribute or texture data | MESH: `uv_layers` (names, active, active_render), `color_attributes` (name, domain, data_type), and per material slot the image names wired into it. |
| `import_file` | Silent about textures | Reply `images_loaded` and `images_missing` (images whose `filepath` does not exist on disk after import). |
| `export_object` | No texture handling | glTF: `export_image_format` (AUTO/JPEG/NONE), `export_texture_dir`. FBX: `path_mode` (AUTO/COPY/RELATIVE/ABSOLUTE/STRIP), `embed_textures`. Report which files were copied. |
| `set_object_material_color` | Only Base Color | Superseded by `set_material_input`; keep it, but implement it by calling the new handler. |

## 2. New tools

Naming: verb first, snake_case. One `@mcp.tool()` plus one add-on handler each unless marked
"server only". List params are comma strings, structured params are JSON strings, exactly
like `set_vertex_positions` and `add_modifier`.

### 2.1 Tier 1, the unwrap-texture-see loop (required)

**UV layers and inspection**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `get_uv_layers` | `mesh` | Per layer: `name, active, active_render, loop_count, islands, bounds [umin,vmin,umax,vmax], loops_outside_01, pinned_count`. Island count via BMesh loop walk over shared UV coords (no edit mode). |
| `check_uvs` | `mesh`, `layer=None`, `image_size=2048`, `max_report=50` | THE numeric perceive tool. Reply: `islands`, `stretch` (per-face ratio of 3D area to UV area normalised by the mesh mean; list the worst `max_report` faces with the ratio), `flipped_faces` (negative signed UV area), `out_of_bounds_faces`, `overlapping_islands` (island bounding-box intersections, cheap), `zero_area_uv_faces`, `texel_density` (texels per metre at `image_size`, mean/min/max per island), `coverage` (fraction of 0-1 covered by UV faces, rasterised at 256²). |
| `get_uvs` | `mesh`, `layer=None`, `face_indices=None`, `max_faces=2000` | Per face: `loops:[{loop_index, vertex, uv:[u,v], pinned}]`. Uses `uv_layer.uv.foreach_get`, `uv_layer.pin.foreach_get`. `_check_indices`. |
| `set_uvs` | `mesh`, `uvs` JSON `{loop_index: [u,v]}` or list `[[loop_index,u,v]]`, `layer=None` | Batch write via `foreach_set` on a copied array. Reply: written count. |
| `render_uv_layout` | `mesh`, `layer=None`, `size=1024`, `image: str = None` (draw over this Blender image or file), `fill_opacity=0.25`, `show_stretch=False`, `face_indices=None` | Perceive tool. Server-side PIL drawing from `get_uvs` data (works headless, no context needed): 0-1 frame, face fills, edges, seams in red, pinned verts as dots, out-of-bounds tinted. `show_stretch=True` colours faces by the `check_uvs` ratio. Falls back to `bpy.ops.uv.export_layout` only if PIL is missing. Returns via `_safe_image_return`. |
| `add_uv_layer` | `mesh`, `name="UVMap"`, `set_active=True`, `copy_from: str = None` | `copy_from` copies coordinates via `foreach_get`/`foreach_set`. |
| `remove_uv_layer` / `rename_uv_layer` / `set_active_uv_layer` | as named, `set_active_uv_layer(mesh, name, for_render=True)` | |

**Seams and unwrapping**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `mark_seams` | `mesh`, `edge_indices`, `seam=True` | Mirror `mark_sharp_edges` (`e.use_seam` on mesh edges, no edit mode). |
| `seams_from_angle` | `mesh`, `angle=60` (deg), `clear_existing=False`, `also_sharp=False` | Marks seams on edges whose adjacent face normals differ by more than `angle`. Reply: seam count. |
| `seams_from_islands` | `mesh`, `layer=None`, `mark_sharp=False` | `bpy.ops.uv.seams_from_islands` in edit mode, all faces selected, restore. |
| `clear_seams` | `mesh` | |
| `unwrap` | `mesh`, `method="ANGLE_BASED"` (ANGLE_BASED / CONFORMAL / MINIMUM_STRETCH), `margin=0.02`, `fill_holes=True`, `correct_aspect=True`, `use_subsurf_data=False`, `face_indices=None`, `layer=None`, `iterations=10` (MINIMUM_STRETCH only) | Edit mode, selects the faces (all if None), runs `bpy.ops.uv.unwrap`, restores mode and selection. Reply: `check_uvs` summary (islands, worst stretch, out-of-bounds) so the caller can judge without a second call. |
| `smart_uv_project` | `mesh`, `angle_limit=66`, `island_margin=0.02`, `area_weight=0.0`, `correct_aspect=True`, `scale_to_bounds=False`, `rotate_method="AXIS_ALIGNED_Y"`, `face_indices=None`, `layer=None` | Same shape as `unwrap`. |
| `project_uvs` | `mesh`, `projection="CUBE"` (CUBE / CYLINDER / SPHERE / VIEW / CAMERA), `cube_size=1.0`, `direction="VIEW_ON_EQUATOR"`, `align="POLAR_ZX"`, `radius=1.0`, `scale_to_bounds=True`, `correct_aspect=True`, `camera: str = None`, `angle: str = None` (a `_VIEW_PRESETS` key for VIEW), `face_indices=None` | CUBE/CYLINDER/SPHERE run the `uv.*_project` ops in edit mode. VIEW uses `uv.project_from_view` under the viewport `temp_override` after aligning to `angle`. CAMERA uses `project_from_view(camera_bounds=True)` with the scene camera set, temporarily, to `camera`. |
| `pack_uv_islands` | `mesh`, `margin=0.02`, `rotate=True`, `rotate_method="ANY"`, `scale=True`, `shape_method="CONCAVE"`, `merge_overlap=False`, `pin=False`, `udim_source="CLOSEST_UDIM"`, `layer=None` | Edit mode, all faces selected, restore. Reply: `check_uvs` summary. |
| `relax_uvs` | `mesh`, `mode="MINIMIZE_STRETCH"` (MINIMIZE_STRETCH / AVERAGE_ISLANDS), `iterations=20`, `blend=0.0`, `layer=None` | |
| `pin_uvs` | `mesh`, `loop_indices=None`, `face_indices=None`, `clear=False`, `layer=None` | Direct write to `uv_layer.pin`. |
| `transform_uvs` | `mesh`, `face_indices=None` (whole islands containing them if `whole_islands=True`), `translate="0,0"`, `rotate=0` (deg), `scale="1,1"`, `pivot="ISLAND_CENTER"` (ISLAND_CENTER / ORIGIN / CENTER / custom "u,v"), `layer=None` | Numeric island manipulation without the UV editor. Negative scale flips. |
| `transfer_uvs` | `source_mesh`, `target_mesh`, `method="NEAREST_FACE_INTERPOLATED"` (NEAREST / NEAREST_FACE_INTERPOLATED / POLYINTERP_NEAREST / TOPOLOGY), `layer=None`, `apply=True` | Data Transfer modifier, `data_types_loops={'UV'}`, applied unless told otherwise. |

**Materials and nodes**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `list_materials` | `include_unused=False`, `object: str = None` | Per material: `name, users, fake_user, use_nodes, images, viewport_color, blend/surface_render_method, objects_using (≤10)`. |
| `get_material_info` | `material`, `include_values=True` | Perceive tool for node trees: `nodes:[{name, type, label, location, image, inputs:{name: value or "← node.socket"}}]`, `links:[[from_node, from_socket, to_node, to_socket]]`, `output_node`, `principled` (every Principled input with its value or source). Colours as 4-lists, vectors as lists, enums as strings. |
| `set_material_input` | `material`, `input`, `value`, `node: str = None` | Sets a default_value on the Principled BSDF (or the named node). `value` accepts a number, "r,g,b[,a]", or a string enum. Errors list the valid input names (the 4.x names: Base Color, Metallic, Roughness, IOR, Alpha, Specular IOR Level, Specular Tint, Coat Weight, Sheen Weight, Emission Color, Emission Strength, Subsurface Weight, Transmission Weight, ...). If the input is linked, report that and do not silently ignore it. |
| `add_shader_node` | `material`, `node_type` (`ShaderNodeTexImage`, `ShaderNodeMix`, `ShaderNodeMath`, ...), `name=None`, `location="0,0"`, `props` JSON (`image` accepts an image name or path, `blend_type`, `operation`, `data_type`, `interpolation`, `colorspace`) | Copy the `add_modifier` set/unset reply. Errors list a curated set of common node types. |
| `link_shader_nodes` | `material`, `from_node`, `from_socket`, `to_node`, `to_socket` | Sockets by name or index. Error lists the sockets of both nodes. |
| `remove_shader_node` | `material`, `node` | |
| `set_shader_node_input` | `material`, `node`, `input`, `value` | Same value rules as `set_material_input`. |
| `render_material_preview` | `material`, `shape="SPHERE"` (SPHERE / CUBE / PLANE / MONKEY / object name), `size=512`, `engine="EEVEE"`, `samples=32`, `hdri: str = None` | Perceive tool. `scene.copy()` like `render_depth_map`, one preview object with the material, 3-point light or HDRI, camera framed by bounding box, render, remove scene in `finally`. Returns via `_safe_image_return`. |
| `duplicate_material` / `remove_unused_materials` | `duplicate_material(material, new_name, assign_to=None)` copies the node tree; `remove_unused_materials()` returns the removed names | |

**Images**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `list_images` | `include_missing_only=False` | Per image: `name, size, channels, source, filepath, exists_on_disk, packed, is_dirty, is_float, colorspace, users, generated_type`. |
| `get_image_preview` | `image` (name or path), `max_size=1024`, `region: str = None` ("x,y,w,h" in pixels), `channel: str = None` (R/G/B/A to view a packed map channel) | Perceive tool. Saves a temp copy via `image.save_render` (respects colour management), crops server-side with PIL, returns via `_safe_image_return`. |
| `create_image` | `name`, `width=1024`, `height=1024`, `color="0,0,0,1"`, `alpha=True`, `float_buffer=False`, `generated_type="BLANK"` (BLANK / UV_GRID / COLOR_GRID), `filepath: str = None`, `colorspace: str = None` | `bpy.data.images.new`; when `filepath` given, save immediately so `source='FILE'`. |
| `save_image` | `image`, `filepath`, `file_format="PNG"` (PNG / JPEG / TIFF / OPEN_EXR / TARGA / BMP), `color_depth=None`, `quality=90`, `copy=False` | `image.save_render` through a temp `scene.render.image_settings` swap (save and restore) or `image.save()` when `copy=False` and the format matches. Reply: path, bytes. |
| `reload_images` | `images: str = None` (all if None), `only_changed=True` | `image.reload()`; with `only_changed`, compare file mtime to the last seen mtime stored in a driver_namespace dict. Reply: reloaded names, missing files. This is the ImageTools handoff return path. |
| `pack_images` | `images: str = None`, `unpack=False`, `unpack_method="USE_LOCAL"` | `image.pack()` / `image.unpack(method=)`. |
| `resize_image` | `image`, `width`, `height` | `image.scale()`; refuses on a packed generated image with a teach message. |
| `set_image_colorspace` | `image`, `colorspace` ("sRGB", "Non-Color", "Linear Rec.709", ...) | Errors list `bpy.types.ColorManagedInputColorspaceSettings.bl_rna.properties['name'].enum_items`. |
| `export_textures` | `objects: str = None` (all if None), `output_dir`, `file_format="PNG"`, `naming="{material}_{slot}"`, `overwrite=False` | Saves every image wired into the Principled inputs of the materials on those objects, one file per image, slot detected from the link target (Base Color → `basecolor`, Normal → `normal`, ...). Reply: manifest `[{material, slot, image, path, size, colorspace}]`. Feeds game-engine import and ImageTools. |

**Baking**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `bake_textures` | `objects` (comma list), `bake_type="DIFFUSE"` (COMBINED / AO / SHADOW / POSITION / NORMAL / UV / ROUGHNESS / EMIT / ENVIRONMENT / DIFFUSE / GLOSSY / TRANSMISSION), `width=2048`, `height=2048`, `image_name: str = None`, `output_path: str = None`, `uv_layer: str = None`, `margin=16`, `margin_type="ADJACENT_FACES"`, `samples=32`, `pass_filter="COLOR"` (comma list of NONE/EMIT/DIRECT/INDIRECT/COLOR/DIFFUSE/GLOSSY/TRANSMISSION), `normal_space="TANGENT"`, `use_selected_to_active=False`, `cage_object=None`, `cage_extrusion=0.05`, `max_ray_distance=0.0`, `target="IMAGE_TEXTURES"` (or VERTEX_COLORS with `color_attribute`), `use_clear=True`, `device="GPU"` | Verifies Cycles is registered (see gotchas). Creates the image (or reuses `image_name`), inserts a `ShaderNodeTexImage` with that image into EVERY material of the target objects and makes it the active node, selects the objects (active = the last one, or the low-poly target when `use_selected_to_active`), sets `scene.render.bake.*`, switches engine to CYCLES with `samples`, runs `bpy.ops.object.bake(type=...)`, saves to `output_path` if given, then in `finally` removes the temporary nodes, restores active nodes, engine, samples, device and selection. Reply: image name, path, seconds, warnings (e.g. materials without nodes, objects without UVs). |
| `bake_texture_set` | `objects`, `output_dir`, `size=2048`, `maps="DIFFUSE,NORMAL,ROUGHNESS,AO"`, `samples=32`, `ao_samples=64`, `use_selected_to_active=False`, `cage_object=None`, `cage_extrusion=0.05`, `naming="{object}_{map}"`, `assign_result=False` | Loops `bake_textures` with the right pass filters per map (DIFFUSE → COLOR only, GLOSSY → COLOR, COMBINED → all). `assign_result=True` builds a new material from the baked set on the target via the `load_texture` path. Reply: manifest. |

### 2.2 Tier 2, productivity

| Tool | Parameters | Behaviour |
|---|---|---|
| `add_texture_paint_slot` | `mesh`, `slot_type="BASE_COLOR"` (BASE_COLOR / ROUGHNESS / METALLIC / NORMAL / BUMP / DISPLACEMENT / EMISSION / ALPHA), `name=None`, `width=2048`, `height=2048`, `color="0.8,0.8,0.8,1"`, `alpha=False`, `float_buffer=False`, `material_slot=0` | Implemented MANUALLY (image + `ShaderNodeTexImage` + link into the matching Principled input, set active node), not through `paint.add_texture_paint_slot`, which needs TEXTURE_PAINT mode context and fails headless. |
| `fill_texture_faces` | `mesh`, `image`, `face_indices=None`, `material_index=None`, `color="1,1,1,1"`, `layer=None`, `feather=0` | Rasterises the UV polygons of the chosen faces into the image (numpy in the addon, or server-side PIL from `get_uvs` then written back with `image.pixels.foreach_set`). ID masks, colour blocking, region masks for ImageTools. |
| `project_image_onto_mesh` | `mesh`, `image_path`, `camera` (or `angle`), `target_image`, `uv_layer=None`, `blend="REPLACE"` | Headless-safe projection: temp UV layer via `project_uvs(CAMERA)`, temp material sampling `image_path` through that layer, `bake_textures(EMIT)` into `target_image` on the real layer, clean up. Note the alternative `paint.project_image` needs the viewport; do not use it. |
| `get_color_attributes` / `add_color_attribute` / `remove_color_attribute` | `add_color_attribute(mesh, name, domain="CORNER" (POINT/CORNER), data_type="BYTE_COLOR" (BYTE_COLOR/FLOAT_COLOR), color="1,1,1,1")` | |
| `set_vertex_colors` | `mesh`, `attribute`, `colors` JSON `{index: [r,g,b,a]}` (vertex index for POINT, loop index for CORNER), `face_indices=None` + `color` for a fill | `foreach_set`. |
| `bake_vertex_colors_to_texture` / `texture_to_vertex_colors` | `mesh`, `attribute`, `image` ... | Both via `bake_textures` (EMIT from an Attribute node, or `target=VERTEX_COLORS`). |
| `set_uv_checker` | `objects`, `enable=True`, `grid="COLOR_GRID"` (COLOR_GRID / UV_GRID), `size=1024` | Persistent version of the `uv_checker` overlay: swaps in a checker material and remembers the originals in `driver_namespace`; `enable=False` restores. |
| `get_texel_density` / `set_texel_density` | `mesh`, `image_size=2048`, `target` (texels/m), `layer=None` | Set scales every island uniformly about its centre to hit the target. |
| `straighten_uv_island` / `align_uv_islands` | `mesh`, `face_indices`, `axis` | `follow_active_quads` for grid islands (edit mode with an active face), and bounding-box alignment for the rest. |
| `copy_material_settings` | `source`, `target`, `what="ALL"` | |
| `make_paths_relative` / `find_missing_files` | `find_missing_files(search_dir)` runs `bpy.ops.file.find_missing_files(directory=)` | |
| `set_image_udim` | `image`, `tiles` (comma list of 1001..), `size` | `image.source='TILED'`, `image.tiles.new(tile_number=)`. |

### 2.3 Tier 3, later (do NOT build unless asked)

Decal projection with a Shrinkwrap plane, trim-sheet helpers, texture atlas merging across
objects (multi-object `pack_uv_islands` with `udim_source='ACTIVE_UDIM'` and a re-bake),
`paste_image_region` (ImageTools does this).

## 3. Ripple points (every one must be touched)

1. `addon.py`: handlers, `extended_handlers` registration, helpers below, `bl_info` version.
2. `src/blender_mcp/server.py`: wrappers, PIL drawing for `render_uv_layout` (reuse
   `_compose_grid` / `_fit_to_box` where they fit), new section banners `# ─── UV ───`,
   `# ─── Materials & Nodes ───`, `# ─── Images ───`, `# ─── Baking ───`.
3. `TOOLS.md`: new sections, tool count, Table of Contents.
4. `README.md`: tool count, category rows, "What's new".
5. `pyproject.toml`: version.
6. `C:\Users\Naabin\.claude\commands\blender.md`: a "UV unwrap and check" pattern, a
   "Texture with ImageTools handoff" pattern (export layout → ImageTools → reload), a
   "Bake a PBR set" pattern; add tools to the quick-reference table; REMOVE "UV unwrapping"
   and "custom node setups" from the execute_blender_code fallback line.
7. Auto-memory `project_blender_mcp.md`: append the session block.
8. MemPalace `main` db: one decisions drawer.

New add-on helpers to add alongside the rigging ones:

```python
@contextmanager
def _edit_faces(self, obj, face_indices=None):
    """Edit mode with exactly these faces selected (all if None); restores mode + selection."""

@contextmanager
def _engine_override(self, engine, samples=None, device=None):
    """Swap scene.render.engine (+ cycles samples/device), restore in finally."""

def _uv_layer(self, mesh, name=None):
    """Return the named or active UV layer, or an error dict listing the available names."""

def _get_image(self, name_or_path, load=True):
    """bpy.data.images by name, else by matching filepath, else load from disk if load=True."""

def _uv_islands(self, mesh, layer):
    """Return list[set[face_index]] via BMesh shared-UV walk. Cached per (mesh, layer, update tag)."""
```

## 4. Blender 4.3.2 facts, verified headlessly 2026-09-06 (do not re-derive)

- UV data access without edit mode: `mesh.uv_layers[...]` exposes `.uv`, `.pin`,
  `.vertex_selection`, `.edge_selection` collections with `foreach_get`/`foreach_set`;
  `.data[i].uv` also works. Seams are `mesh.edges[i].use_seam`. `get_edges` already reports
  them.
- `uv.unwrap` props: `method` (ANGLE_BASED / CONFORMAL / MINIMUM_STRETCH), `fill_holes`,
  `correct_aspect`, `use_subsurf_data`, `margin_method`, `margin`, `no_flip`, `iterations`,
  `use_weights`, `weight_group`, `weight_factor`. Needs EDIT mode and selected faces.
- `uv.smart_project` props: `angle_limit` (RADIANS), `margin_method`, `rotate_method`,
  `island_margin`, `area_weight`, `correct_aspect`, `scale_to_bounds`.
- `uv.pack_islands` props: `udim_source`, `rotate`, `rotate_method`, `scale`,
  `merge_overlap`, `margin_method`, `margin`, `pin`, `pin_method`, `shape_method`
  (CONCAVE / CONVEX / AABB).
- `uv.minimize_stretch(fill_holes, blend, iterations)`, `uv.average_islands_scale(scale_uv,
  shear)`, `uv.seams_from_islands(mark_seams, mark_sharp)`, `uv.pin(clear, invert)`,
  `uv.reset()`, `uv.cube_project(cube_size, ...)`, `uv.cylinder_project(direction, align,
  pole, seam, radius, ...)`, `uv.sphere_project(direction, align, pole, seam, ...)`,
  `uv.project_from_view(orthographic, camera_bounds, correct_aspect, clip_to_bounds,
  scale_to_bounds)` (needs a VIEW_3D override), `uv.follow_active_quads(mode)`.
- `uv.export_layout` exists (add-on `io_mesh_uv_layout`, enabled by default): `filepath,
  export_all, export_tiles, modified, mode (SVG/EPS/PNG), size, opacity`. It needs edit
  mode and an active mesh; prefer the PIL path.
- `object.bake` props: `type` (COMBINED / AO / SHADOW / POSITION / NORMAL / UV / ROUGHNESS /
  EMIT / ENVIRONMENT / DIFFUSE / GLOSSY / TRANSMISSION), `pass_filter` set (NONE / EMIT /
  DIRECT / INDIRECT / COLOR / DIFFUSE / GLOSSY / TRANSMISSION), `filepath, width, height,
  margin, margin_type, use_selected_to_active, max_ray_distance, cage_extrusion,
  cage_object, normal_space, normal_r/g/b, target (IMAGE_TEXTURES / VERTEX_COLORS),
  save_mode, use_clear, use_cage, use_split_materials, use_automatic_name, uv_layer`.
  `scene.render.bake` mirrors these plus `use_pass_*` booleans and `view_from`.
- Baking requires Cycles. Verified: Cycles IS available under `--background
  --factory-startup` (`cycles` add-on enabled, `scene.render.engine = 'CYCLES'` succeeds).
  BUT both `bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items` and
  `scene.render.bl_rna.properties['engine'].enum_items` list ONLY `BLENDER_EEVEE_NEXT`,
  because add-on engines are not in the RNA enum. Never detect Cycles through the enum.
  Detect by assignment in a try/except, or via
  `[getattr(e, 'bl_idname', None) for e in bpy.types.RenderEngine.__subclasses__()]`
  (plain `e.bl_idname` raises `AttributeError` on `HydraRenderEngine`). On failure return a
  teach-style error naming `addon_utils.enable("cycles")`.
  Side finding for the report: the existing `render_depth_map` fallback (addon.py ~line 759)
  uses the same enum lookup to pick a fallback engine, so it can never pick CYCLES; leave it
  unless it blocks you, but mention it.
- `paint.add_texture_paint_slot(type, slot_type, name, color, width, height, alpha,
  generated_type, float, domain, data_type)` and `paint.project_image(image)` need paint-mode
  context; both tools above are implemented manually instead.
- Images: `bpy.data.images.new(name, w, h, alpha=, float_buffer=)`; attributes `pixels`
  (with `foreach_get`/`foreach_set`, flat RGBA floats, bottom-up rows), `filepath`,
  `filepath_raw`, `source` (FILE / SEQUENCE / MOVIE / GENERATED / VIEWER / TILED), `tiles`,
  `colorspace_settings.name`, `packed_file`, `is_dirty`, `file_format`, `alpha_mode`,
  `generated_type`; methods `save_render(filepath, scene=)`, `save()`, `scale(w, h)`,
  `reload()`, `pack()`, `unpack(method=)`. `image.resize` op takes `size`.
- Colour attributes: `mesh.color_attributes.new(name, 'BYTE_COLOR'|'FLOAT_COLOR',
  'POINT'|'CORNER')`.
- `ShaderNodeTexImage` props: `image, interpolation, projection, extension, image_user`.
  `ShaderNodeUVMap` exists. Principled BSDF 4.x input names: Base Color, Metallic,
  Roughness, IOR, Alpha, Normal, Weight, Diffuse Roughness, Subsurface Weight/Radius/Scale/
  IOR/Anisotropy, Specular IOR Level, Specular Tint, Anisotropic, Anisotropic Rotation,
  Tangent, Transmission Weight, Coat Weight/Roughness/IOR/Tint/Normal, Sheen Weight/
  Roughness/Tint, Emission Color, Emission Strength, Thin Film Thickness, Thin Film IOR.
- `node_wrangler` add-on is available but NOT enabled; do not depend on it.
- Every image reply goes through `_safe_image_return`. Every render goes through
  `_render_settings` / `_render_to_file` or the `scene.copy()` pattern.

## 5. Testing (required before you report done)

Headless script, same harness as the rigging request (`--background --factory-startup`
works for every step including the bakes, Cycles is available there):

1. `add_primitive` cube "Crate"; `seams_from_angle(60)` marks 12 seams (report count).
2. `unwrap` ANGLE_BASED; `check_uvs` reports 0 flipped, 0 out-of-bounds, islands ≥ 1.
3. `smart_uv_project` on a `monkey`; `check_uvs` worst stretch is finite; `pack_uv_islands`
   reduces `loops_outside_01` to 0.
4. `get_uvs` on 3 faces, `transform_uvs` translate by 0.1, `get_uvs` shows the delta.
5. `render_uv_layout` writes a PNG that is not a single colour.
6. `create_material` "Wood" with `replace=False` twice; second call keeps nodes. `load_texture`
   of a `create_image(COLOR_GRID)` saved to disk into Base Color, then Roughness, then Normal;
   `get_material_info` shows ONE TexCoord and ONE Mapping node, three image nodes, correct
   colourspaces.
7. `set_material_input("Wood", "Coat Weight", 0.5)` reads back through `get_material_info`.
8. `add_shader_node` Mix + `link_shader_nodes` into Base Color; `get_material_info` shows the
   link and reports Base Color as linked.
9. `list_images`, `save_image` PNG, `reload_images` after touching the file's mtime returns
   the name; `pack_images` sets `packed`.
10. `bake_textures` AO 256² on Crate with `samples=4` writes a PNG that is not a single
    colour and leaves the material's node tree identical to before (compare
    `get_material_info` before and after).
11. `bake_texture_set` DIFFUSE,NORMAL 256² writes two files and the manifest lists them.
12. `export_textures` for Crate writes the files with the `{material}_{slot}` names.
13. `render_material_preview("Wood")` and the `shading="MATERIAL"` and `overlay="uv_checker"`
    captures are viewport/render tools: test them in a live session via the MCP and confirm
    non-blank images, and that the original material slots are restored afterwards.

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.

## 6. Deploy

Identical to section 7 of the rigging request. Do not launch Blender or deploy until told.
