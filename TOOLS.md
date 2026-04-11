# Blender MCP — Tool Reference

92 tools exposed via `@mcp.tool()` in `src/blender_mcp/server.py`.

---

## Table of Contents

1. [Scene & Object Info](#1-scene--object-info)
2. [Process Management](#2-process-management)
3. [Viewport Capture](#3-viewport-capture)
4. [Reference Images](#4-reference-images)
5. [Object Transform](#5-object-transform)
6. [Vertex Operations](#6-vertex-operations)
7. [Curve Control Points](#7-curve-control-points)
8. [Edge Operations](#8-edge-operations)
9. [Face Operations](#9-face-operations)
10. [Mesh Editing](#10-mesh-editing)
11. [Primitives & Object Management](#11-primitives--object-management)
12. [Camera](#12-camera)
13. [Rendering](#13-rendering)
14. [Lighting](#14-lighting)
15. [Materials](#15-materials)
16. [Modifiers](#16-modifiers)
17. [Animation](#17-animation)
18. [Collections](#18-collections)
19. [Export / Import / Save / Load](#19-export--import--save--load)
20. [Scene Analysis](#20-scene-analysis)
21. [Image-to-3D (TripoSR)](#21-image-to-3d-triposr)
22. [PolyHaven Integration](#22-polyhaven-integration)
23. [Sketchfab Integration](#23-sketchfab-integration)
24. [Hyper3D (Rodin) Integration](#24-hyper3d-rodin-integration)
25. [Hunyuan3D Integration](#25-hunyuan3d-integration)
26. [Scripting](#26-scripting)

---

## 1. Scene & Object Info

### `get_scene_info()`
Get detailed information about the current Blender scene (objects, materials, render settings).

**Returns:** JSON string

---

### `get_object_info(object_name)`
Get detailed information about a specific object.

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_name` | str | Name of the object |

**Returns:** JSON string

---

## 2. Process Management

### `start_blender(blend_file, blender_exe, background, wait_for_addon, python_expr)`
Launch Blender as a managed subprocess.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `blend_file` | str | None | Path to a `.blend` file to open on startup |
| `blender_exe` | str | None | Full path to Blender executable — auto-detected if omitted |
| `background` | bool | False | Headless mode (`--background`), no UI |
| `wait_for_addon` | bool | True | Wait up to 30 s for the addon socket on port 9876 |
| `python_expr` | str | None | Python expression passed via `--python-expr` |

Auto-detection order: `BLENDER_EXE` env var → PATH → `C:\Program Files\Blender Foundation\Blender *\blender.exe` (newest) → Steam → macOS .app.

**Returns:** Status message with PID

---

### `close_blender(force)`
Close the Blender instance started by `start_blender()`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | False | Kill immediately; if False, asks Blender to quit gracefully first |

**Returns:** Status message

---

### `get_blender_status()`
Report whether Blender is running and whether the MCP addon socket is reachable on port 9876.

**Returns:** String with process state and addon socket state

---

## 3. Viewport Capture

### `get_viewport_screenshot(max_size)`
Capture a screenshot of the current 3D viewport.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_size` | int | 800 | Maximum pixel dimension |

**Returns:** PNG image

---

### `capture_viewport_angle(angle, max_size)`
Capture the viewport from a named orthographic or isometric angle.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `angle` | str | "front" | One of: `front`, `back`, `left`, `right`, `top`, `bottom`, `iso_front_right`, `iso_front_left` |
| `max_size` | int | 800 | Maximum pixel dimension |

**Returns:** PNG image

---

### `capture_contact_sheet(angles, max_size)`
Capture multiple viewport angles and stitch them into a labelled contact sheet.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `angles` | str | "front,right,top,iso_front_right" | Comma-separated angle names |
| `max_size` | int | 512 | Pixel size for each tile |

**Returns:** PNG image (composited grid)

---

### `render_depth_map(max_depth)`
Render a normalised depth map from the active camera using the compositor Z-pass. Closer objects appear lighter.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_depth` | float | 10.0 | Depth (scene units) mapped to black |

**Returns:** PNG image

---

## 4. Reference Images

### `store_reference_image(name, filepath)`
Store a local image file as a named reference for later comparison.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Short identifier (e.g. `"concept_art"`) |
| `filepath` | str | Absolute path to the image file |

**Returns:** Confirmation string

---

### `compare_reference_image(reference_name, angle, max_size)`
Capture the viewport and composite it side-by-side with a stored reference image.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reference_name` | str | — | Name given to `store_reference_image` |
| `angle` | str | "front" | Viewport angle for the live capture |
| `max_size` | int | 512 | Tile size for each image |

**Returns:** PNG image (reference left, current render right)

---

### `diff_images(image_path_a, image_path_b, threshold, tile_size)`
Compare two images and produce a 3-panel composite showing differences in bright red.
The diff panel desaturates Image A to near-grayscale and paints changed regions red with
a soft glow, making even small differences immediately obvious.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image_path_a` | str | — | Path to the first image (baseline) |
| `image_path_b` | str | — | Path to the second image (changed version) |
| `threshold` | int | 15 | Per-pixel difference (0–255) below which changes are ignored; filters noise |
| `tile_size` | int | 512 | Width/height of each panel in the composite |

**Returns:** PNG image — three panels: `[Image A] | [Image B] | [Diff (X.X% changed)]`

**Notes:**
- Images are resized to `tile_size × tile_size` before comparison; aspect ratio is not preserved
- Diff mask is amplified 6× before thresholding so subtle changes become visible
- Requires `numpy` and `Pillow` (both included in default deps)

---

## 5. Object Transform

### `move_object(name, x, y, z)`
Move an object to an absolute world-space position.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `x`, `y`, `z` | float | 0.0 | World-space coordinates |

---

### `scale_object(name, x, y, z)`
Set the absolute scale of an object on each axis.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `x`, `y`, `z` | float | 1.0 | Scale per axis |

---

### `rotate_object(name, x, y, z)`
Set the Euler rotation of an object.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `x`, `y`, `z` | float | 0.0 | Rotation in **degrees** per axis |

---

### `set_object_material_color(name, r, g, b, a, material_index)`
Set the Principled BSDF base colour of an object's material. Creates the material if none exists.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `r`, `g`, `b`, `a` | float | 1.0 | RGBA in 0..1 range |
| `material_index` | int | 0 | Which material slot to update |

---

### `rename_object(old_name, new_name)`
Rename an object and its mesh data block.

| Parameter | Type | Description |
|-----------|------|-------------|
| `old_name` | str | Current object name |
| `new_name` | str | Desired new name |

---

### `set_origin(name, origin_type)`
Set an object's origin point.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `origin_type` | str | "ORIGIN_GEOMETRY" | `ORIGIN_GEOMETRY`, `ORIGIN_CURSOR`, `ORIGIN_CENTER_OF_MASS`, `ORIGIN_CENTER_OF_VOLUME` |

---

### `snap_to_ground(name, ground_z)`
Move an object so its lowest bounding-box point rests on the ground plane.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `ground_z` | float | 0.0 | Z value of the ground plane |

---

### `parent_object(child_name, parent_name, keep_transform)`
Parent one object to another.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `child_name` | str | — | Object that becomes the child |
| `parent_name` | str | — | Object that becomes the parent |
| `keep_transform` | bool | True | Preserve child's world-space position |

---

### `select_objects(names, action, obj_type)`
Select or deselect objects by name list and/or type.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `names` | str | None | Comma-separated object names |
| `action` | str | "SELECT" | `SELECT`, `DESELECT`, `TOGGLE` |
| `obj_type` | str | None | Filter by type when `names` is omitted |

---

### `align_objects(names, axis, align_to)`
Align multiple objects' origins along one axis.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `names` | str | — | Comma-separated object names |
| `axis` | str | "X" | `X`, `Y`, or `Z` |
| `align_to` | str | "FIRST" | `FIRST`, `LAST`, `MIN`, `MAX`, `AVERAGE` |

---

## 6. Vertex Operations

### `get_vertex_positions(name, indices, world_space, max_verts)`
Read vertex positions from a mesh object.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `indices` | str | None | Comma-separated vertex indices; returns all if omitted |
| `world_space` | bool | True | True = world coords, False = local/object coords |
| `max_verts` | int | 2000 | Safety cap when retrieving all vertices |

**Returns:** JSON with `{index, co: [x, y, z]}` per vertex

---

### `set_vertex_position(name, vertex_index, x, y, z)`
Move a single vertex to a world-space position.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Mesh object name |
| `vertex_index` | int | Zero-based vertex index |
| `x`, `y`, `z` | float | Target world-space position |

---

### `set_vertex_positions(name, vertices, world_space)`
Batch-update multiple vertex positions in a single call (much faster than repeated single-vertex calls).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `vertices` | str | — | JSON array: `[{"index": 0, "co": [x, y, z]}, ...]` |
| `world_space` | bool | True | True = world-space coords |

---

## 7. Curve Control Points

### `get_control_points(name, spline_index)`
Read the control points of a curve object (BEZIER, POLY, or NURBS).

For BEZIER: returns `co`, `handle_left`, `handle_right`, handle types.  
For POLY/NURBS: returns `co` and weight (NURBS).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Curve object name |
| `spline_index` | int | 0 | Which spline within the curve |

**Returns:** JSON

---

### `set_control_point(name, point_index, co, handle_left, handle_right, handle_left_type, handle_right_type, spline_index)`
Move a curve control point and optionally adjust its bezier handles.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Curve object name |
| `point_index` | int | — | Zero-based control point index |
| `co` | str | — | `"x,y,z"` world-space position |
| `handle_left` | str | None | `"x,y,z"` for left handle (bezier only) |
| `handle_right` | str | None | `"x,y,z"` for right handle (bezier only) |
| `handle_left_type` | str | None | `FREE`, `ALIGNED`, `VECTOR`, or `AUTO` |
| `handle_right_type` | str | None | `FREE`, `ALIGNED`, `VECTOR`, or `AUTO` |
| `spline_index` | int | 0 | Spline index within the curve |

---

## 8. Edge Operations

### `get_edges(name, indices, max_edges)`
Read edge data: vertex pair, sharp flag, seam flag, crease, and bevel weight.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `indices` | str | None | Comma-separated edge indices; returns all if omitted |
| `max_edges` | int | 5000 | Safety cap when retrieving all edges |

**Returns:** JSON

---

### `mark_sharp_edges(name, edge_indices, sharp)`
Mark edges as sharp (hard) or soft, controlling auto-smooth and the Edge Split modifier.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `edge_indices` | str | — | Comma-separated indices or `"all"` |
| `sharp` | bool | True | True = hard edge, False = soft/smooth |

---

### `set_edge_crease(name, edge_indices, crease)`
Set subdivision crease weight on edges. Controls how the Subdivision Surface modifier treats edge sharpness.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Mesh object name |
| `edge_indices` | str | Comma-separated indices or `"all"` |
| `crease` | float | 0.0 (smooth) to 1.0 (perfectly sharp) |

---

### `set_edge_bevel_weight(name, edge_indices, weight)`
Set bevel weight on edges, used with the Bevel modifier (`limit_method=WEIGHT`).

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Mesh object name |
| `edge_indices` | str | Comma-separated indices or `"all"` |
| `weight` | float | 0.0 (no bevel) to 1.0 (full bevel) |

**Typical workflow:**
```
set_edge_bevel_weight("Cube", "4,5,6,7", weight=1.0)
add_modifier("Cube", "BEVEL", params='{"width": 0.05, "limit_method": "WEIGHT"}')
```

---

## 9. Face Operations

### `get_faces(name, indices, world_space, max_faces)`
Read face data from a mesh object.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `indices` | str | None | Comma-separated face indices; returns all if omitted |
| `world_space` | bool | True | World coords for normals and centers |
| `max_faces` | int | 2000 | Safety cap |

**Returns:** JSON with `vertex_indices`, `normal`, `center`, `material_index`, `area` per face

---

### `set_face_material_index(name, face_indices, material_index)`
Assign a material slot to specific faces (for multi-material objects).

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Mesh object name |
| `face_indices` | str | Comma-separated indices or `"all"` |
| `material_index` | int | Material slot number (0-based; must already exist in object slots) |

---

### `extrude_faces(name, face_indices, amount)`
Extrude faces outward along their individual normals.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `face_indices` | str | — | Comma-separated face indices |
| `amount` | float | 0.2 | Extrusion distance (negative = inward) |

---

### `inset_faces(name, face_indices, thickness, depth, use_individual)`
Inset faces, creating a border ring of new polygons inside each face.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `face_indices` | str | — | Comma-separated face indices |
| `thickness` | float | 0.1 | Inset distance from face edges |
| `depth` | float | 0.0 | Push inset faces along normals; 0 = flat |
| `use_individual` | bool | True | Inset each face independently |

---

### `flip_normals(name, face_indices)`
Flip face normals (reverses which side is the outside).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `face_indices` | str | None | Comma-separated indices; flips ALL if omitted |

---

### `merge_vertices(name, distance)`
Merge (weld) vertices within a distance threshold. Equivalent to *Merge by Distance* in Blender.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `distance` | float | 0.001 | Maximum merge distance |

---

### `triangulate_mesh(name, method)`
Triangulate all faces of a mesh (convert quads/ngons to triangles).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `method` | str | "BEAUTY" | `BEAUTY`, `FIXED`, `FIXED_ALTERNATE`, `SHORTEST_DIAGONAL` |

---

## 10. Mesh Editing

### `subdivide_mesh(name, cuts, smoothness)`
Subdivide all faces of a mesh (equivalent to Subdivide in Edit Mode).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `cuts` | int | 1 | Number of cuts per edge |
| `smoothness` | float | 0.0 | Smooth factor 0..1 |

---

### `apply_modifier(name, modifier_name)`
Apply a named modifier on an object, collapsing it into the mesh data.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Object name |
| `modifier_name` | str | Exact modifier name as shown in Blender's Properties panel |

---

### `get_mesh_stats(name)`
Return detailed topology statistics for a mesh object (vertex/edge/face counts, etc.).

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Mesh object name |

**Returns:** JSON

---

## 11. Primitives & Object Management

### `add_primitive(primitive_type, location, size, name, rotation)`
Add a standard mesh primitive to the scene.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primitive_type` | str | "cube" | `cube`, `plane`, `circle`, `sphere`, `ico_sphere`, `cylinder`, `cone`, `torus`, `monkey` |
| `location` | str | "0,0,0" | `"x,y,z"` position |
| `size` | float | 2.0 | Overall size in Blender units |
| `name` | str | None | Optional name for the new object |
| `rotation` | str | "0,0,0" | `"x,y,z"` rotation in degrees |

---

### `delete_object(name)`
Delete an object from the scene and purge orphaned mesh/material data.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Object name to delete |

---

### `duplicate_object(name, new_name, offset, linked)`
Duplicate an object.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Source object name |
| `new_name` | str | None | Name for the duplicate |
| `offset` | str | "0.5,0.5,0" | `"x,y,z"` displacement from original |
| `linked` | bool | False | True = share mesh data (instance); False = full copy |

---

### `join_objects(names, result_name)`
Join multiple mesh objects into one.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `names` | str | — | Comma-separated object names |
| `result_name` | str | None | Name for the joined object (defaults to first object's name) |

---

### `separate_mesh(name, method)`
Separate a mesh object into multiple objects.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `method` | str | "LOOSE" | `LOOSE` (disconnected geometry), `MATERIAL` (by material slot), `SELECTED` (by face selection) |

---

### `set_smooth_shading(name, smooth, auto_smooth, angle)`
Toggle smooth or flat shading on a mesh object.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Mesh object name |
| `smooth` | bool | True | True for smooth, False for flat |
| `auto_smooth` | bool | True | Enable auto-smooth |
| `angle` | float | 30.0 | Auto-smooth threshold in degrees |

---

## 12. Camera

### `create_camera(name, location, look_at, lens, cam_type)`
Add a new camera to the scene.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | "Camera" | Name for the camera object |
| `location` | str | "0,-5,3" | `"x,y,z"` position |
| `look_at` | str | "0,0,0" | `"x,y,z"` target point |
| `lens` | float | 50.0 | Focal length in mm |
| `cam_type` | str | "PERSP" | `PERSP` or `ORTHO` |

---

### `set_active_camera(name)`
Set the active render camera.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Camera object name |

---

## 13. Rendering

### `render_from_camera(camera_name, width, height, samples)`
Render a still from the specified (or active) camera.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `camera_name` | str | None | Camera to render from (uses scene active camera if omitted) |
| `width` | int | 1920 | Render width in pixels |
| `height` | int | 1080 | Render height in pixels |
| `samples` | int | 32 | Cycles sample count (ignored for EEVEE) |

**Returns:** PNG image

---

### `render_all_cameras(width, height, samples, output_dir)`
Render a still from every camera in the scene and return a labelled contact sheet.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `width` | int | 1920 | Render width per camera |
| `height` | int | 1080 | Render height per camera |
| `samples` | int | 32 | Cycles sample count |
| `output_dir` | str | None | Directory to save individual renders (temp dir if omitted) |

Contact sheet tiles are 960×540 with a 28 px label bar, max 3 columns.  
**Returns:** PNG contact sheet; individual full-res renders saved to `output_dir`.

---

### `set_render_settings(engine, width, height, samples, output_path, file_format, transparent_background)`
Configure scene render settings.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | None | `CYCLES`, `BLENDER_EEVEE`, `BLENDER_WORKBENCH` |
| `width` / `height` | int | None | Render resolution |
| `samples` | int | None | Sample count |
| `output_path` | str | None | File path (e.g. `"C:/renders/frame_####.png"`) |
| `file_format` | str | None | `PNG`, `JPEG`, `EXR`, `TIFF` |
| `transparent_background` | bool | None | Alpha instead of background colour |

---

## 14. Lighting

### `add_light(light_type, name, location, energy, color, radius)`
Add a light to the scene.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `light_type` | str | "POINT" | `POINT`, `SUN`, `SPOT`, `AREA` |
| `name` | str | None | Name for the light object |
| `location` | str | "0,0,5" | `"x,y,z"` position |
| `energy` | float | 1000.0 | Power in watts |
| `color` | str | "1,1,1" | `"r,g,b"` in 0..1 range |
| `radius` | float | 0.1 | Shadow soft radius |

---

### `set_world_background(color, strength, hdri_path)`
Set the scene world background to a solid colour or an HDRI environment map.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `color` | str | "0.05,0.05,0.05" | `"r,g,b"` (used when `hdri_path` is omitted) |
| `strength` | float | 1.0 | Background emission strength |
| `hdri_path` | str | None | Path to `.hdr` or `.exr` file (overrides colour) |

---

### `add_3point_lighting(subject_name, key_energy, fill_energy, back_energy)`
Add a classic 3-point lighting rig (key, fill, back/rim) centred on a subject.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `subject_name` | str | None | Object to light (scene origin if omitted) |
| `key_energy` | float | 1500.0 | Key light watts |
| `fill_energy` | float | 500.0 | Fill light watts |
| `back_energy` | float | 800.0 | Back/rim light watts |

---

## 15. Materials

### `create_material(name, base_color, metallic, roughness, emission_color, emission_strength, alpha, assign_to)`
Create (or replace) a PBR material using Principled BSDF.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Material name |
| `base_color` | str | "0.8,0.8,0.8" | `"r,g,b"` in 0..1 |
| `metallic` | float | 0.0 | 0 = dielectric, 1 = fully metallic |
| `roughness` | float | 0.5 | 0 = mirror, 1 = fully rough |
| `emission_color` | str | None | `"r,g,b"` to enable glow |
| `emission_strength` | float | 1.0 | Emission multiplier |
| `alpha` | float | 1.0 | Opacity; values < 1 enable alpha blending |
| `assign_to` | str | None | Object name to auto-assign this material to slot 0 |

---

### `assign_material(object_name, material_name, slot)`
Assign an existing material to an object's material slot.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `object_name` | str | — | Target object |
| `material_name` | str | — | Material to assign (must already exist) |
| `slot` | int | 0 | Material slot index |

---

### `load_texture(material_name, image_path, texture_slot, uv_scale)`
Load an image file and wire it into a material's texture slot.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `material_name` | str | — | Target material (must have a Principled BSDF node) |
| `image_path` | str | — | Absolute path to the image |
| `texture_slot` | str | "Base Color" | `Base Color`, `Roughness`, `Metallic`, `Normal`, `Emission Color` |
| `uv_scale` | float | 1.0 | Uniform UV tiling scale |

Normal maps are automatically connected through a Normal Map node.

---

## 16. Modifiers

### `add_modifier(name, modifier_type, modifier_name, params)`
Add a modifier to an object with optional parameters.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `modifier_type` | str | — | `MIRROR`, `BEVEL`, `ARRAY`, `SOLIDIFY`, `SUBSURF`, `DECIMATE`, `DISPLACE`, `SHRINKWRAP`, `WIREFRAME`, `SKIN`, etc. |
| `modifier_name` | str | None | Display name (auto-generated if omitted) |
| `params` | str | None | JSON string of modifier properties |

**Common params examples:**
```
MIRROR:   '{"use_axis": [true, false, false], "use_clip": true}'
BEVEL:    '{"width": 0.05, "segments": 3, "limit_method": "ANGLE"}'
ARRAY:    '{"count": 4, "use_relative_offset": true, "relative_offset_displace": [1, 0, 0]}'
SOLIDIFY: '{"thickness": 0.05, "offset": -1.0}'
SUBSURF:  '{"levels": 2, "render_levels": 3}'
```

---

### `boolean_operation(target_name, cutter_name, operation, solver, apply)`
Perform a boolean operation between two mesh objects.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_name` | str | — | Object to modify (base mesh) |
| `cutter_name` | str | — | Object used as the cutting/joining tool |
| `operation` | str | "DIFFERENCE" | `DIFFERENCE`, `UNION`, `INTERSECT` |
| `solver` | str | "EXACT" | `EXACT` (better quality) or `FAST` |
| `apply` | bool | True | Apply modifier and delete cutter when done |

---

## 17. Animation

### `add_keyframe(name, data_path, frame, value)`
Insert an animation keyframe on an object property.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `data_path` | str | "location" | `location`, `rotation_euler`, `scale`, or any animatable path |
| `frame` | int | None | Frame number (uses current frame if omitted) |
| `value` | str | None | `"x,y,z"` values; rotation values given in degrees are auto-converted |

---

### `set_frame(frame)`
Set the current scene frame (scrubs the timeline).

| Parameter | Type | Description |
|-----------|------|-------------|
| `frame` | int | Target frame number |

---

## 18. Collections

### `create_collection(name, parent_collection)`
Create a new collection for scene organisation.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Collection name |
| `parent_collection` | str | None | Parent collection name (nests inside it) |

---

### `move_to_collection(object_names, collection_name)`
Move objects into a collection (removes them from all other collections).

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_names` | str | Comma-separated object names |
| `collection_name` | str | Target collection (must already exist) |

---

## 19. Export / Import / Save / Load

### `export_object(name, filepath, file_format)`
Export an object (or the full scene) to a 3D file.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | None | Object to export; entire scene if omitted |
| `filepath` | str | None | Output path (auto-generated in temp dir if omitted) |
| `file_format` | str | "glb" | `glb`, `gltf`, `fbx`, `obj`, `stl`, `ply` |

---

### `import_file(filepath)`
Import a 3D file into the current scene.

| Parameter | Type | Description |
|-----------|------|-------------|
| `filepath` | str | Absolute path to the file |

**Supported formats:** `.glb`, `.gltf`, `.fbx`, `.obj`, `.stl`, `.ply`, `.blend`

---

### `save_blend(filepath)`
Save the current Blender project as a `.blend` file.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | None | Absolute path to save to. If omitted, saves over currently open file. |

---

### `load_blend(filepath)`
Open a `.blend` file, replacing the current scene. **Unsaved changes will be lost** — save first if needed.

| Parameter | Type | Description |
|-----------|------|-------------|
| `filepath` | str | Absolute path to the `.blend` file |

---

## 20. Scene Analysis

### `find_objects_by_type(obj_type)`
List all objects in the scene matching a given type.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `obj_type` | str | "MESH" | `MESH`, `CURVE`, `CAMERA`, `LIGHT`, `EMPTY`, `ARMATURE`, etc. |

---

### `measure_distance(name_a, name_b)`
Measure the Euclidean distance between the origins of two objects.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name_a` | str | First object name |
| `name_b` | str | Second object name |

---

## 21. Image-to-3D (TripoSR)

Requires `img_to_3d_server.py` in the project root and `pip install git+https://github.com/VAST-AI-Research/TripoSR`.

### `load_img_to_3d_model(model_dir)`
Start the local TripoSR inference server. Poll `/status` until ready (up to 30 s).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model_dir` | str | None | Path to weights directory (uses `IMG_TO_3D_MODEL_DIR` env var or HuggingFace hub ID `stabilityai/TripoSR`) |

---

### `unload_img_to_3d_model()`
Stop the local image-to-3D server, freeing VRAM and memory.

---

### `generate_3d_from_image(image_path, output_path, foreground_ratio, mc_resolution, no_remove_bg)`
Generate a 3D mesh (`.glb`) from a single image. Requires `load_img_to_3d_model()` first.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image_path` | str | — | Absolute path to the input image |
| `output_path` | str | None | Output `.glb` path (auto-generated if omitted) |
| `foreground_ratio` | float | 0.85 | Foreground crop ratio for background removal |
| `mc_resolution` | int | 256 | Marching-cubes resolution; higher = more detail |
| `no_remove_bg` | bool | False | Skip background removal for already-clean images |

After generation, call `import_file(output_path)` to load the model into Blender.

**Env vars:**

| Variable | Default | Description |
|----------|---------|-------------|
| `IMG_TO_3D_PORT` | 7862 | Server port |
| `IMG_TO_3D_MODEL_DIR` | `stabilityai/TripoSR` | HuggingFace hub ID or local weights path |
| `IMG_TO_3D_DEVICE` | auto | `cuda` or `cpu` |
| `IMG_TO_3D_CHUNK_SIZE` | 8192 | Response chunk size |

---

## 22. PolyHaven Integration

Requires the PolyHaven integration to be enabled in Blender's BlenderMCP sidebar.

### `get_polyhaven_status()`
Check if PolyHaven integration is enabled.

---

### `get_polyhaven_categories(asset_type)`
Get categories for a specific asset type.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `asset_type` | str | "hdris" | `hdris`, `textures`, `models`, `all` |

---

### `search_polyhaven_assets(asset_type, categories)`
Search for assets with optional category filtering.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `asset_type` | str | "all" | `hdris`, `textures`, `models`, `all` |
| `categories` | str | None | Comma-separated category names |

---

### `download_polyhaven_asset(asset_id, asset_type, resolution, file_format)`
Download and import a PolyHaven asset into Blender.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `asset_id` | str | — | Asset ID |
| `asset_type` | str | — | `hdris`, `textures`, `models` |
| `resolution` | str | "1k" | `1k`, `2k`, `4k` |
| `file_format` | str | None | `hdr`/`exr` for HDRIs; `jpg`/`png` for textures; `gltf`/`fbx` for models |

---

### `set_texture(object_name, texture_id)`
Apply a previously downloaded PolyHaven texture to an object.

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_name` | str | Target object name |
| `texture_id` | str | PolyHaven texture ID (must be downloaded first) |

---

## 23. Sketchfab Integration

Requires the Sketchfab integration to be enabled in Blender's BlenderMCP sidebar.

### `get_sketchfab_status()`
Check if Sketchfab integration is enabled.

---

### `search_sketchfab_models(query, categories, count, downloadable)`
Search for models on Sketchfab.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | — | Search text |
| `categories` | str | None | Comma-separated category names |
| `count` | int | 20 | Maximum results |
| `downloadable` | bool | True | Only include downloadable models |

---

### `get_sketchfab_model_preview(uid)`
Get a thumbnail preview of a Sketchfab model before downloading.

| Parameter | Type | Description |
|-----------|------|-------------|
| `uid` | str | Model UID from search results |

**Returns:** JPEG image

---

### `download_sketchfab_model(uid, target_size)`
Download and import a Sketchfab model, scaled to a target real-world size.

| Parameter | Type | Description |
|-----------|------|-------------|
| `uid` | str | Model UID |
| `target_size` | float | **Required.** Target size in Blender units/meters for the largest dimension (e.g. `1.7` for a person, `4.5` for a car) |

---

## 24. Hyper3D (Rodin) Integration

Requires the Hyper3D Rodin integration to be enabled in Blender's BlenderMCP sidebar.

### `get_hyper3d_status()`
Check if Hyper3D Rodin integration is enabled.

---

### `generate_hyper3d_model_via_text(text_prompt, bbox_condition)`
Generate a 3D asset from a text description.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text_prompt` | str | — | Short description in **English** |
| `bbox_condition` | list[float] | None | Optional `[Length, Width, Height]` ratio list |

**Returns:** JSON with `task_uuid` and `subscription_key`

---

### `generate_hyper3d_model_via_images(input_image_paths, input_image_urls, bbox_condition)`
Generate a 3D asset from reference images.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_image_paths` | list[str] | None | Absolute image paths (MAIN_SITE mode) |
| `input_image_urls` | list[str] | None | Image URLs (FAL_AI mode) |
| `bbox_condition` | list[float] | None | Optional `[Length, Width, Height]` ratio |

---

### `poll_rodin_job_status(subscription_key, request_id)`
Check if a Hyper3D generation task is complete. Poll until `"Done"` or `"COMPLETED"`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `subscription_key` | str | From generate step (MAIN_SITE mode) |
| `request_id` | str | From generate step (FAL_AI mode) |

---

### `import_generated_asset(name, task_uuid, request_id)`
Import the asset generated by Hyper3D after the task completes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Name of the object in scene |
| `task_uuid` | str | From generate step (MAIN_SITE mode) |
| `request_id` | str | From generate step (FAL_AI mode) |

---

## 25. Hunyuan3D Integration

Requires the Hunyuan3D integration to be enabled in Blender's BlenderMCP sidebar.

### `get_hunyuan3d_status()`
Check if Hunyuan3D integration is enabled.

---

### `generate_hunyuan3d_model(text_prompt, input_image_url)`
Generate a 3D asset using Hunyuan3D from text, image, or both.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text_prompt` | str | None | Text description |
| `input_image_url` | str | None | Image URL or local path |

**Returns:** JSON with `job_id`

---

### `poll_hunyuan_job_status(job_id)`
Check if a Hunyuan3D generation task is complete. Poll until status is `"DONE"`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | str | From generate step |

When `"DONE"`, response includes `ResultFile3Ds` with the ZIP file path.

---

### `import_generated_asset_hunyuan(name, zip_file_url)`
Import the asset generated by Hunyuan3D after the task completes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Name of the object in scene |
| `zip_file_url` | str | ZIP file URL from poll step |

---

## 26. Scripting

### `execute_blender_code(code)`
Execute arbitrary Python code inside Blender. Break complex operations into smaller chunks.

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | str | Python code to execute in Blender's Python environment |

---

## Running the Server

```bash
cd "D:\App Dev\blender_mcp"
.venv\Scripts\blender-mcp.exe
# or
.venv\Scripts\python.exe -m blender_mcp.server
```

The addon TCP server must be running in Blender on port 9876. Install `addon.py` via Blender Preferences → Add-ons → Install.
