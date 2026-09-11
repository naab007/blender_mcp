# Feature request: presentation and library (2D from 3D, game-dev perception, import hygiene)

Status: OPEN, requested 2026-09-07
Target version: 1.11.0 (perception tools may be pulled forward into any earlier build)
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`)
Categories covered (see `docs/README.md`): 9 2D from 3D, 10 game-dev perception,
11 asset library and import hygiene
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here.

## 0. Scope and why

The agent cannot see. Every check it makes on a game asset is either a number or a
render. The existing capture tools give an unlit solid view from fixed angles, which is
enough for blocking but hides the things that break assets: triangle density, inverted
faces, silhouette, scale against a human, how the LODs degrade, whether the collider hugs
the mesh. This request adds the game-dev perceive layer, the sprite pipeline that Stratum
and 2D projects need, and the import hygiene that ripped assets (AssetStudio FBX, glTF
from AssetRipper, Timbermesh) always need.

Build what is in this document. If something is wrong or impossible on 4.3.2, say so in
the report and continue.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `capture_viewport_angle` / `capture_contact_sheet` | Fixed to the current shading; no overlays; no framing control; no background control | Add `shading` (SOLID / MATERIAL / RENDERED / WIREFRAME), `overlay` (comma list: `wireframe`, `face_orientation`, `bones_in_front`, `normals`, `statistics`, `uv_checker`, `vertex_color`, `bounds`), `frame_objects` (comma list to `view_selected` on, else all), `zoom=1.0`, `ortho=True`, `background="0.2,0.2,0.2"` (solid viewport colour), `transparent=False`, `light="STUDIO"` (STUDIO / MATCAP / FLAT), `matcap=None`. Restore every changed overlay, shading and space setting in `finally`. `statistics` overlay puts Blender's tri/vert counts in the corner of the capture, which is the cheapest tri-count perceive there is. The texturing request also asks for `shading`; implement once. |
| `render_from_camera` | No transparent film, no output format control, no engine per call | Add `transparent=False`, `file_format="PNG"`, `color_mode="RGBA"`, `engine=None` (temporary override through `_engine_override`), `color_depth`. |
| `measure_distance` | Origin to origin only | Add `mode="ORIGIN"` (ORIGIN / BOUNDS / SURFACE) so clearance checks are possible. |
| `import_file` | See engine-readiness fix; also no post-import hygiene | Add `post="NONE"` (NONE / GAME_ASSET: apply the `clean_import` tool below after import). |

## 2. New tools

### 2.1 Tier 1, required

**Game-dev perception**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `render_wireframe` | `objects`, `angle="iso_front_right"`, `max_size=1024`, `show_tris=True`, `color_by="NONE"` (NONE / DENSITY / MATERIAL / NGON), `on_shaded=True` | Wireframe capture with per-face triangle count annotated in the label bar. DENSITY colours faces by area (small = red) through a temp vertex-colour attribute; NGON highlights faces with > 4 sides. Restore everything. |
| `render_face_orientation` | `objects`, `angles="front,back,iso_front_right"`, `max_size=800` | Blender's face-orientation overlay (blue outside, red inside) on a contact sheet. Red anywhere means inside-out in the engine. Reply includes the numeric count of inward faces from `find_mesh_issues(FLIPPED)` so the image is not the only evidence. |
| `render_silhouette` | `objects`, `angles="front,right,top"`, `max_size=512`, `background="white"` | Flat black on white, orthographic. Readability of the silhouette is a core game-art check. Server composes the sheet. |
| `render_with_mannequin` | `objects`, `mannequin="HUMAN_180"` (HUMAN_180 / HUMAN_160 / DOOR_210 / CUBE_1M / custom height), `angle="front"`, `position="RIGHT"` (LEFT / RIGHT / BEHIND), `max_size=1024` | Adds a temporary proportioned mannequin (capsule body, sphere head, at the given height in scene units, scaled by `unit_settings.scale_length`) beside the object, captures, removes it. The "is this the right size" check. Reply: object height vs mannequin ratio. |
| `render_lod_sheet` | `base_name` (matches `_LOD*`), `angle="iso_front_right"`, `distances=None`, `max_size=1024`, `wireframe=True` | One tile per LOD, labelled with tri count and the ratio to LOD0; with `distances` each tile is captured with the camera at that distance so the agent sees the LOD as the player would. |
| `render_collision_overlay` | `mesh`, `angles="iso_front_right,front"`, `max_size=800`, `proxy_color="0,1,0,0.4"` | Shows the collision proxies (found via `get_collision_info`) as translucent colour over the shaded mesh. Reply includes `check_collision_fit` numbers. |
| `render_turntable` | `objects`, `frames=8`, `elevation=15`, `max_size=512`, `shading="MATERIAL"`, `video_path=None`, `light="STUDIO"` | Orbit camera contact sheet (and optional MP4 via the playblast path from the rigging request). |
| `render_scale_grid` | `objects`, `angle="front"`, `grid=1.0`, `max_size=1024` | Object over a labelled metre grid floor plane, orthographic. |
| `render_thumbnail` | `objects` or `collection`, `size=256`, `style="STUDIO"` (STUDIO / FLAT / WIRE), `transparent=True`, `output_path=None` | Icon-quality render with an auto-framed camera and 3-point light in a temp scene. The asset-library and inventory-icon tool. |
| `analyze_render` | `image_path`, `checks="BLANK,ALPHA_COVERAGE,DOMINANT_COLORS,EDGE_DENSITY"` (server only) | PIL and numpy readback so the agent can assert on an image: is it blank, what fraction is non-transparent, top colours, edge density (a proxy for detail). Used by every test that asserts an image is "not blank". |

**2D from 3D**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `render_sprite_sheet` | `objects` or `collection`, `directions=8` (1 / 4 / 8 / 16, or explicit yaw list), `elevation=30` (30 = classic 2:1 iso is 30°, true iso is 35.264°), `frames=1` (animation frames from `frame_start..frame_end` stepped), `size=128`, `padding=2`, `ortho_scale=None` (auto-fit across ALL directions and frames so the sprite never shifts), `pivot="BOTTOM_CENTER"`, `transparent=True`, `light="SUN"` (SUN with fixed direction / STUDIO / FLAT), `light_rotates=False`, `shadow=False`, `outline=0` (px, server-side), `output_path`, `layout="ROW_PER_DIRECTION"` (ROW_PER_DIRECTION / GRID / SEPARATE_FILES), `engine="EEVEE"`, `samples=16` | THE sprite pipeline. Temp scene (`scene.copy()`), orthographic camera on an orbit, fixed world light unless `light_rotates`, alpha film, every frame rendered to temp PNGs, server packs the sheet with PIL and writes a JSON atlas (`frame`, `direction`, `x`, `y`, `w`, `h`, `pivot_px`). Same ortho scale for every tile so animations do not jitter. Reply: sheet path, atlas path, tile size, count, and a downscaled preview via `_safe_image_return`. |
| `render_isometric_tile` | `objects`, `tile_width=64`, `projection="2:1"` (2:1 / TRUE_ISO / 3:2 / custom `elevation`, `yaw`), `output_path`, `directions=1`, `light` as above | Preset of `render_sprite_sheet` for tile sets: ortho scale derived from `tile_width` and the object's footprint so the footprint diamond is exactly `tile_width` wide. Reply: pixel footprint and vertical offset. |
| `render_billboard` | `objects`, `size=512`, `angles="front"` (or 8 for a flipbook impostor), `include_normal=True`, `include_depth=False`, `output_dir` | Colour, normal (world-space via `render_depth_map` style temp compositor with a Normal pass) and optional depth renders for impostor cards. Reply: paths. |
| `create_billboard_card` | `name`, `image` (colour), `normal_image=None`, `size=None` (from image aspect and object height), `pivot="BOTTOM_CENTER"`, `double_sided=True` | Plane with the alpha material; pairs with `render_billboard` to make a far-LOD card. |
| `render_icon_set` | `objects` (comma list) or `collection`, `size=128`, `style="STUDIO"`, `output_dir`, `naming="{name}_icon"`, `sheet=True` | Batch `render_thumbnail`, optional combined sheet with atlas JSON. Inventory icons. |

**Asset library and import hygiene**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `clean_import` | `objects` (or the last import's objects when None), `apply_transforms=True`, `remove_empties=True` (empties with no children after re-parenting), `join_by_material=False`, `merge_by_distance=None`, `strip_name_suffixes=True` (`.001`), `rename_from_file=True`, `fix_normals=True`, `set_origin="KEEP"` (KEEP / BOTTOM_CENTER / CENTER), `remove_unused_materials=True`, `collection=None`, `scale_to_meters=None` (multiply, e.g. 0.01 for cm sources) | The ripped-asset routine. Reply: every action taken with counts. |
| `match_material_names` | `objects`, `reference` JSON list of allowed names (or a text file path), `strategy="FUZZY"` (EXACT / FUZZY / PREFIX), `dry_run=True`, `strip_suffixes=True` | Renames materials to the closest allowed name (difflib ratio ≥ 0.8) and reports unmatched ones. Timbermesh and any engine that binds materials by name. |
| `rename_objects` | `objects` or `collection`, `pattern`, `replacement`, `regex=False`, `prefix=None`, `suffix=None`, `numbering="{i:02d}"`, `also_data=True`, `dry_run=False` | Batch rename with data-block sync. |
| `get_asset_report` | `objects` or `collection` | One JSON: per object type, tri count, materials, textures with resolution, UV layers, armature, LODs, collision, sockets, custom props, dimensions. The manifest an agent reads before deciding what to do with a ripped asset. |
| `set_custom_properties` / `get_custom_properties` | shared with engine-readiness | |
| `mark_as_asset` | `objects` or `collection` or `material`, `catalog=None`, `tags=None`, `description=None`, `generate_preview=True` | `id.asset_mark()`, `asset_data.catalog_id`, tags, and `id.asset_generate_preview()`; for the Blender Asset Browser library workflow. |
| `list_assets` | `library=None` | Marked assets in the file (or in a library path via `bpy.data.libraries.load`). |
| `link_from_blend` | `filepath`, `names` (objects or collections), `link=False`, `collection=None` | Append or link specific data-blocks (the current `import_file` appends everything). |
| `save_asset_blend` | `objects` or `collection`, `filepath`, `pack_images=True`, `relative_paths=True` | Writes a standalone `.blend` with only those data-blocks (`bpy.data.libraries.write`). |
| `find_unused_data` / `purge_unused_data` | | Orphan report and `bpy.data.orphans_purge(do_recursive=True)`. |

### 2.2 Tier 2

| Tool | Parameters | Behaviour |
|---|---|---|
| `render_normal_map_view` | `objects`, `angle` | World or tangent normal render for eyeballing baked normals. |
| `render_uv_checker_sheet` | `objects`, `angles` | Shortcut for the `uv_checker` overlay on a contact sheet with `check_uvs` numbers in the labels. |
| `render_material_id` | `objects`, `angle` | Flat colour per material slot with a legend. |
| `render_comparison` | `objects_a`, `objects_b`, `angle`, `mode="SIDE_BY_SIDE"` (SIDE_BY_SIDE / WIPE / DIFF via `diff_images`) | Before/after of an optimisation. |
| `render_sprite_animation` | `armature` or `objects`, `action`, `directions`, `fps_out` | `render_sprite_sheet` with an action assigned and frames sampled at `fps_out`. |
| `pack_sprite_atlas` (server only) | `image_paths`, `output_path`, `padding`, `power_of_two=True`, `max_size=4096` | Shelf packer with atlas JSON for sheets built from separate renders. |
| `render_pixel_art` | `objects`, `size=64`, `palette=None`, `dither=False`, `outline=1` | Renders at 4× then downsamples nearest with optional palette quantisation (PIL). |
| `create_asset_catalog` | `path`, `name` | Writes `blender_assets.cats.txt` entries. |
| `dedupe_materials` / `dedupe_images` | `by="NAME_SUFFIX"` (NAME_SUFFIX / CONTENT_HASH) | Ripped assets bring `Mat.001..Mat.037` that are identical. |
| `import_batch` | `directory`, `pattern="*.fbx"`, `post="GAME_ASSET"`, `collection_per_file=True` | Bulk ingest. |
| `get_import_diff` | | What the last import added, modified or duplicated. |

### 2.3 Tier 3, later (do NOT build unless asked)

Octahedral impostor atlases, automatic pixel-art palette extraction from reference sheets,
asset library sync to an external folder structure, screenshot-driven regression tests of
the perception tools.

## 3. Ripple points beyond the standard list

- `TOOLS.md` and `README.md`: sections "Game-Dev Perception", "2D from 3D", "Asset
  Library & Import Hygiene".
- `/blender` skill: a "Verify an asset visually" pattern (silhouette → face orientation →
  wireframe → mannequin → LOD sheet), a "Sprite sheet" pattern with the ortho-scale rule,
  and a "Ripped asset cleanup" pattern (import_file → clean_import →
  match_material_names → get_asset_report).
- `analyze_render` replaces every ad-hoc "check the PNG is not a single colour" in the
  other requests' test plans; use it there too.

## 4. Blender 4.3.2 facts and rules (verified where stated)

- Viewport overlay props (verified on `View3DOverlay`): `show_overlays, show_wireframes,
  wireframe_threshold, wireframe_opacity, show_face_orientation, show_stats, show_bones,
  show_floor, show_axis_x/y/z, grid_scale, grid_subdivisions, show_extras,
  show_outline_selected, show_relationship_lines, show_cursor, show_text, show_annotation,
  normals_length, show_face_normals, show_vertex_normals, show_split_normals,
  show_edge_seams, show_edge_sharp, show_edge_crease, show_edge_bevel_weight, show_faces,
  show_weight, show_wpaint_contours, show_paint_wire`.
- Viewport shading props (verified on `View3DShading`): `type` (WIREFRAME / SOLID /
  MATERIAL / RENDERED), `light` (STUDIO / MATCAP / FLAT), `color_type` (MATERIAL / SINGLE /
  OBJECT / RANDOM / VERTEX / TEXTURE), `background_type` (THEME / WORLD / VIEWPORT),
  `background_color, show_xray, xray_alpha, studio_light, show_object_outline,
  show_shadows, show_cavity, show_backface_culling, single_color, wireframe_color_type,
  use_dof, show_specular_highlight, render_pass, use_scene_lights, use_scene_world`.
  Reading them is verified; setting them needs a live VIEW_3D area (same `temp_override`
  as the capture tools).
- `view3d.view_selected(use_all_regions)`, `view3d.view_axis(type, align_active,
  relative)` and `view3d.camera_to_view_selected()` exist (verified) for framing.
- `render.film_transparent`, `image_settings.color_mode='RGBA'`,
  `image_settings.file_format`, `image_settings.color_depth` for transparent PNG output.
- Sprite ortho-scale rule: compute the bounding sphere of the union of every object across
  every frame and direction ONCE, set `camera.data.ortho_scale = 2 * radius * margin`, and
  never change it between tiles. The pivot pixel is the projection of the bottom-centre of
  the bounds, recorded in the atlas.
- Light rule for sprites: the Stratum art uses a radial light rule so rotating the sprite
  is legal; for 8-direction sheets default to a light fixed in WORLD space
  (`light_rotates=False`) so shading is consistent per direction, and expose the option.
- Temp scenes: use `scene.copy()` + `bpy.data.scenes.remove(tmp)` in `finally` exactly like
  `render_depth_map`; never mutate the user's compositor or world.
- Every image reply goes through `_safe_image_return`; contact sheets through
  `_compose_grid`. Sheets larger than 8 MP are returned downscaled and the full-res file
  path is reported.
- Asset API (verified): `id.asset_mark()`, `id.asset_clear()`,
  `id.asset_generate_preview()`, `id.asset_data` with `catalog_id, tags, description,
  author, copyright, license, active_tag`; `asset_data.tags.new(name)` works.
- `bpy.data.libraries.write(filepath, datablocks: set[ID], path_remap=False,
  fake_user=False, compress=False)` (verified signature; `path_remap` accepts the enum
  strings 'NONE' / 'RELATIVE' / 'RELATIVE_ALL' / 'ABSOLUTE' per the docstring).
- `bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)`
  (verified kwargs).
- `scene.render.film_transparent` and `image_settings.file_format / color_mode /
  color_depth / compression / quality` exist (verified).
- difflib is stdlib; no new dependency for `match_material_names`.

## 5. Testing (required before you report done)

Headless where possible (`--background --factory-startup`, handlers direct); viewport
captures need a live session and are listed last.

1. Monkey "Suz" with one inverted face (flip one face's normal by hand): `render_face_orientation`
   reply reports `inward_faces == 1` and `analyze_render` on the returned image finds a red
   dominant colour cluster > 0 %.
2. `render_silhouette` produces 3 tiles; `analyze_render(ALPHA_COVERAGE)` between 0.05 and
   0.95 for each.
3. `render_with_mannequin("Suz", HUMAN_180)`: reply ratio equals Suz height / 1.8 within 1 %.
4. `generate_lods` (engine-readiness) then `render_lod_sheet`: 3 tiles, labels carry the tri
   counts from `get_mesh_stats`.
5. `render_thumbnail("Suz", transparent=True)`: PNG has alpha, coverage > 0.2.
6. `render_sprite_sheet("Suz", directions=8, size=64)`: sheet is 8 tiles wide (or the chosen
   layout), atlas JSON has 8 entries, every tile's bounding box of non-alpha pixels has the
   same bottom row ±1 px (no vertical jitter), pivot recorded.
7. `render_isometric_tile("Suz", tile_width=64, projection="2:1")`: footprint diamond width
   64 ±1 px measured on the output.
8. Import a saved FBX of three cubes named `Cube.001`, `Cube.002`, `Cube.003` with a shared
   `Mat.001`: `clean_import(strip_name_suffixes=True)` gives `Cube`, `Cube_1`, `Cube_2` (or
   the documented scheme) and one material; `match_material_names(reference=["Wood"],
   FUZZY)` reports `Mat` unmatched; with reference `["Mat"]` renames it.
9. `get_asset_report` on the scene lists LODs, collision and sockets created in the
   engine-readiness tests when run together.
10. `mark_as_asset("Suz")` then `list_assets` shows it with a preview generated.
11. `save_asset_blend("Suz", tmp)` then `link_from_blend(tmp, "Suz")` into a fresh scene works.
12. Live session only: `capture_viewport_angle(overlay="wireframe,statistics")`,
    `render_wireframe`, `render_collision_overlay`, `render_turntable` return non-blank
    images per `analyze_render`, and every overlay and shading setting is restored (read
    them before and after through `execute_blender_code`).

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.
