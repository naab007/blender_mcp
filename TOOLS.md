# Blender MCP — Tool Reference

169 tools exposed via `@mcp.tool()` in `src/blender_mcp/server.py` (92 in 2.0.0 + 56 in 2.1.0 + 21 in 2.2.0; the list is `tests/baseline_tools_2.2.txt`, cut at the B1 test gate from server hash `6f249049`, harness 185/185 on Blender 4.3.2 and 185/185 on 5.2.1). Blender 4.3 – 5.2 (tested on 4.3.2 and 5.2.1 LTS); version 2.2.0 on branch `blender-5.2`.

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
27. [Version & Server Settings](#27-version--server-settings)
28. [File Lifecycle](#28-file-lifecycle)
29. [Settings & Presets](#29-settings--presets)
30. [Add-ons & Preferences](#30-add-ons--preferences)
31. [Session](#31-session)
32. [Rigging: Armatures & Bones](#32-rigging-armatures--bones)
33. [Rigging: Skinning & Weights](#33-rigging-skinning--weights)
34. [Rigging: Pose & Constraints](#34-rigging-pose--constraints)

---

## 1. Scene & Object Info

### `get_scene_info()`
Get detailed information about the current Blender scene (objects, materials, render settings). The object list is capped at the first 10 objects (`object_count` carries the real total); use `find_objects_by_type` for a full listing. Since 2.1.0 the reply also carries `file` (the `get_file_state` summary) and `settings_summary` (engine, resolution, samples, frame range, units).

**Returns:** JSON string

---

### `get_object_info(object_name)`
Get detailed information about a specific object. Reply (2.2.0): every object gains `parent`, `parent_type` and `parent_bone`; a MESH adds `vertex_groups`, `shape_keys` and `armature` (the Armature modifier's target); an ARMATURE adds `bones`, `pose_position`, `display_type`, `action` and `bone_collections`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_name` | str | Name of the object |

**Returns:** JSON string

---

## 2. Process Management

### `start_blender(blend_file, blender_exe, background, wait_for_addon, python_expr, start_server, restore_session)`
Launch Blender as a managed subprocess.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `blend_file` | str | None | Path to a .blend file to open on startup (optional) |
| `blender_exe` | str | None | Full path to the Blender executable. Auto-detected if omitted: the BLENDER_EXE environment variable, then the settings file (blender_exe), then PATH, then the highest-version copy among Program Files installs and portable zip folders (blender-<ver>-windows-x64 on D:, in the user's home or on any fixed drive root), then Steam. get_blender_status shows the pick. |
| `background` | bool | False | True = headless mode (--background), no UI. Useful for rendering. |
| `wait_for_addon` | bool | True | Wait up to 30 s for the BlenderMCP addon socket to become reachable (default True). Set False for background jobs that don't use the addon. |
| `python_expr` | str | None | Optional Python expression passed to Blender via --python-expr, e.g. "import bpy; bpy.ops.wm.quit_blender()" for scripted batch runs. |
| `start_server` | bool | True | True (default) also asks the add-on to start its socket server through a --python-expr hook: 1 s after startup in GUI mode (in case the autostart preference is off), immediately in background mode (autostart never opens a port headless; only this does). |
| `restore_session` | str | None | Name of a session saved with save_session_state (e.g. "last") to reopen and re-apply once the add-on answers. Blender console output goes to blender_mcp_blender.log in the temp folder; it must never share this process stdio, which carries the MCP transport. |

Auto-detection order: `blender_exe` argument (if the file exists) → `BLENDER_EXE` env var (if the file exists) → settings-file `blender_exe` → PATH → highest-version copy among Program Files installs and portable `blender-<ver>-windows-x64` folders (on `D:\`, under the user's home and on every fixed drive root) → Steam → macOS .app. Set `BLENDER_EXE` or the settings-file `blender_exe` to pin a version when several are installed.

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
Report whether Blender is running and whether the MCP addon socket is reachable on the configured port (`BLENDER_PORT`, default 9876). The probe uses the same socket parameters as the connection itself (host `127.0.0.1`, `connect_timeout` from the settings file), so its answer matches what the tools see.

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

### `capture_viewport_angle(angle, max_size, overlay)`
Capture the Blender 3D viewport from a named angle and return it as an image. Reply (2.2.0): the image; `overlay` is echoed when one was applied. Headless the add-on answers `Viewport capture needs a GUI session: ...` instead of an image.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `angle` | str | "front" | View direction. One of: front, back, left, right, top, bottom, iso_front_right, iso_front_left |
| `max_size` | int | 800 | Maximum pixel dimension (default 800) |
| `overlay` | str | None | Optional rig view: bones_in_front (armatures drawn through meshes), wireframe, weight_paint (active vertex group as a heat map). All viewport state is restored afterwards. |

**Returns:** PNG image

---

### `capture_contact_sheet(angles, max_size, overlay)`
Capture multiple viewport angles and stitch them into a single contact sheet image. Reply (2.2.0): the sheet; `overlay` is echoed when one was applied; headless the add-on answers `Viewport capture needs a GUI session: ...`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `angles` | str | "front,right,top,iso_front_right" | Comma-separated list of angle names (default: front,right,top,iso_front_right) |
| `max_size` | int | 512 | Pixel size for each individual tile (default 512) |
| `overlay` | str | None | Optional rig view for every tile: bones_in_front, wireframe or weight_paint (see capture_viewport_angle). State restored afterwards. Returns a single composited image with all requested angles labelled. |

**Returns:** PNG image (composited grid)

---

### `render_depth_map(max_depth)`
Render a normalised depth map from the active camera using the compositor `Depth` pass. Closer objects appear lighter. Renders in a throw-away scene copy; on Blender 5.x the compositor is a temporary `CompositorNodeTree` node group assigned to that copy (`Scene.node_tree` no longer exists), on 4.x the copy's own node tree. Both are removed afterwards. Workbench has no depth pass, so the copy falls back to EEVEE.

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

### `parent_object(child_name, parent_name, keep_transform, parent_type, bone)`
Parent one object to another, creating a hierarchy. For binding a mesh to an armature with weights use bind_armature instead. Reply (2.2.0): `child`, `parent`, `parent_type` and, for `parent_type=BONE`, `bone`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `child_name` | str | — | Object that becomes the child |
| `parent_name` | str | — | Object that becomes the parent |
| `keep_transform` | bool | True | Preserve the child's world-space position (default True) |
| `parent_type` | str | "OBJECT" | OBJECT (default) or BONE (parent to one bone of an armature) |
| `bone` | str | None | Bone name, required when parent_type is BONE |

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
Render a still from every camera in the scene and return a contact sheet with all results labelled by camera name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `width` | int | 1920 | Render width per camera in pixels (default 1920) |
| `height` | int | 1080 | Render height per camera in pixels (default 1080) |
| `samples` | int | 32 | Sample count for Cycles and EEVEE (default 32) |
| `output_dir` | str | None | Directory to keep the individual full-resolution renders. If omitted, the server setting output_dir is used; if that is unset too they are rendered to the temp dir and deleted after the sheet is built. Returns a composited contact sheet image. |

Contact sheet tiles are 960×540 with a 28 px label bar, max 3 columns.  
**Returns:** PNG contact sheet; individual full-res renders saved to `output_dir`.

---

### `set_render_settings(engine, width, height, samples, output_path, file_format, transparent_background, fps, fps_base, frame_start, frame_end, resolution_percentage, color_mode, color_depth, compression, denoise, device, use_persistent_data, use_simplify, simplify_subdivision)`
Configure scene render settings. Only the parameters you pass are changed. Every value is validated against Blender's own property definitions (the same path as set_settings(scope="RENDER")); rejected values are listed.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | None | CYCLES, BLENDER_EEVEE (alias EEVEE; the 4.x id BLENDER_EEVEE_NEXT is accepted), BLENDER_WORKBENCH. The reply reports the engine id actually set. |
| `width` | int | None | Render width in pixels |
| `height` | int | None | Render height in pixels |
| `samples` | int | None | Number of render samples (affects quality/noise) |
| `output_path` | str | None | File path for saved renders (e.g. "C:/renders/frame_####.png") |
| `file_format` | str | None | PNG, JPEG, OPEN_EXR (EXR accepted), TIFF, BMP, FFMPEG (video) |
| `transparent_background` | bool | None | True to render with alpha instead of background colour |
| `fps` | float | None | Frame rate (fps=24) |
| `fps_base` | float | None | Frame rate base (1.001 for 23.976) |
| `frame_start` | int | None | Scene frame range start |
| `frame_end` | int | None | Scene frame range end |
| `resolution_percentage` | int | None | 1-100 (render at a fraction of width x height) |
| `color_mode` | str | None | BW, RGB or RGBA |
| `color_depth` | str | None | 8 or 16 (PNG/TIFF), 16 or 32 (OPEN_EXR), as a string |
| `compression` | int | None | PNG compression 0-100 |
| `denoise` | bool | None | Cycles denoising on/off |
| `device` | str | None | Cycles render device, CPU or GPU |
| `use_persistent_data` | bool | None | Keep render data between frames (faster animations) |
| `use_simplify` | bool | None | Simplify toggle |
| `simplify_subdivision` | int | None | Max subdivision level under Simplify |

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
| `operation` | str | "DIFFERENCE" | `DIFFERENCE`, `UNION`, `INTERSECT` (case-insensitive; validated against the live enum, an unknown value returns an error listing it) |
| `solver` | str | "EXACT" | `EXACT`, `FLOAT` (`FAST` accepted as alias), `MANIFOLD` (Blender 5.2+). Case-insensitive; `FAST` and `FLOAT` are aliases of each other, mapped to the live enum (`FAST` on 4.x, `FLOAT` on 5.x). An unknown value removes the modifier again and returns an error listing the valid values. The reply carries `solver`, the identifier actually set. |
| `apply` | bool | True | Apply modifier and delete cutter when done |

---

## 17. Animation

Since 2.2.0 this section also covers batch keys, animation inspection, playblasts and baking. Version facts: on Blender 5.x actions are slotted (the tools create the slot and bind `action_slot` for you; on 4.x there are no slots); F-curves are read through channelbags on 5.x and `action.fcurves` on 4.x with the same data; `playblast` uses the viewport OpenGL path in a GUI session and falls back to an EEVEE camera render headless (OpenGL rendering is refused in `--background` on both versions), `video_path` writes an MP4 through FFMPEG with `media_type` VIDEO on 5.x; `bake_action` runs `nla.bake` in POSE mode with the listed bones selected and restores mode and selection. The frame range and fps are set with `set_frame_range` (section 29).

### `add_keyframe(name, data_path, frame, value, bone)`
Insert an animation keyframe on an object or pose-bone property. Reply (2.2.0): adds `bone` when a pose bone was keyed, `rotation_mode_changed {from, to}` when an Euler path forced a switch from QUATERNION, `action` and `fcurve_count`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name (the armature when bone is given) |
| `data_path` | str | "location" | Property to key: 'location', 'rotation_euler', 'scale', or any animatable path like 'data.energy' |
| `frame` | int | None | Frame number (uses current frame if omitted) |
| `value` | str | None | Value(s) to set before keying: "1,2,3" for a vector property such as location, or a single number such as "500" for a scalar like data.energy. Rotation values are in degrees and converted automatically. |
| `bone` | str | None | Pose bone name to key instead of the object. Keying rotation_euler on a bone (or object) in quaternion/axis-angle mode switches it to XYZ Euler, and the reply says so. |

---

### `set_frame(frame)`
Set the current scene frame (scrubs the timeline).

| Parameter | Type | Description |
|-----------|------|-------------|
| `frame` | int | Target frame number |

---

### `set_keyframes(target, data_path, keys, bone, replace)`
Insert many keyframes on one property in a single call.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | str | — | Object name (the armature when bone is given) |
| `data_path` | str | — | 'location', 'rotation_euler', 'scale', or any animatable path |
| `keys` | str | — | JSON list of [frame, value] pairs or objects {"frame", "value", "interpolation"?, "easing"?}. value follows add_keyframe's rules: a number for a scalar, [x,y,z] for a vector, rotations in degrees. interpolation BEZIER/LINEAR/CONSTANT, easing AUTO/EASE_IN/EASE_OUT/EASE_IN_OUT. |
| `bone` | str | None | Pose bone name to key instead of the object (rotation_mode switched to XYZ for Euler paths, reported) |
| `replace` | bool | True | Overwrite existing keys on those frames (default True) Reply: keyed count, action name, fcurve path. |

---

### `get_animation_info(target, include_keys, max_keys)`
Animation overview. Without target: scene fps, frame range, current frame and every action with its users. With target: its action, fcurves (data_path, index, keyframe_count, frame_range), NLA tracks and shape-key action; with include_keys each fcurve lists [frame, value, interpolation] up to max_keys. Works on both the legacy F-curve API (4.x) and slotted actions (5.x).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | str | None | Object name (default: scene summary) |
| `include_keys` | bool | False | Include the keyframes per fcurve (default False) |
| `max_keys` | int | 200 | Cap per fcurve when include_keys (default 200) |

---

### `playblast(start, end, step, frames, camera, max_size, columns, video_path, overlay)`
See motion: capture frames across the range and return one labelled contact sheet. In a GUI session each frame is a viewport capture; headless it is an EEVEE render of the camera. Optionally also writes an MP4. Note (A1.2, live pass 2026-09-11): tiles are always even-sized (an odd `max_size` rounds down by 1, required by the MP4 encoder); `camera=` switches the region to the camera view for the capture and restores it.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start` | int | None | Start frame (default: the scene's frame_start) |
| `end` | int | None | End frame (default: the scene's frame_end) |
| `step` | int | None | Frame step; omit for the add-on's largest step that keeps <= 16 tiles |
| `frames` | str | None | Explicit comma list of frames (overrides start/end/step) |
| `camera` | str | None | Camera to look through (default: scene camera / current view) |
| `max_size` | int | 640 | Tile size in pixels (default 640) |
| `columns` | int | 4 | Sheet columns (default 4) |
| `video_path` | str | None | Also write an MP4 (FFMPEG H.264) of the full range; Blender appends the frame range to the file stem |
| `overlay` | str | None | bones_in_front, wireframe or weight_paint (see capture_viewport_angle) The current frame is restored afterwards. |

---

### `bake_action(armature, start, end, step, bones, visual_keying, clear_constraints, clear_parents, only_selected, bake_types)`
Bake the rig's motion (constraints, IK, drivers) into plain keyframes (bpy.ops.nla.bake in POSE mode). Do this before exporting to a game engine. Note (live pass on 5.2.1 GUI, 2026-09-11, `docs/LIVE-TEST-REPORT-2026-09-11.md`): `nla.bake` names the baked action `Action` (Blender's default); rename it afterwards if you need a specific name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `start` | int | None | Start frame (default: the scene's frame_start) |
| `end` | int | None | End frame (default: the scene's frame_end) |
| `step` | int | 1 | Frame step (default 1) |
| `bones` | str | None | Comma list of bones to bake (default: all) |
| `visual_keying` | bool | True | Bake the evaluated (constrained) transforms (default True) |
| `clear_constraints` | bool | False | Remove constraints after baking (default False) |
| `clear_parents` | bool | False | Clear bone parents after baking (default False) |
| `only_selected` | bool | True | Bake only the listed/selected bones (default True) |
| `bake_types` | str | "POSE" | POSE (default), OBJECT, "POSE,OBJECT" or "both" Reply: action name, frame range, fcurve count. Mode and selection are restored. |

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

### `export_object(name, filepath, file_format, include_hierarchy, bake_anim, add_leaf_bones, use_armature_deform_only, bake_anim_simplify_factor, mesh_smooth_type, primary_bone_axis, secondary_bone_axis, apply_scale_options, export_animations, export_skins, export_morph)`
Export an object (or the full scene) to a 3D file. A skinned mesh exports WITH its armature, children and animation by default (include_hierarchy). Reply (2.2.0): `exported_objects` (names, or "all" for a scene export), `include_hierarchy`, the `fbx_options` or `gltf_options` actually used, and `ignored` (parameters that do not apply to the chosen format).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | None | Object to export; exports entire scene if omitted |
| `filepath` | str | None | Output file path. If omitted: <server output_dir>/<name or scene>.<format> when the output_dir setting is set, else a temp-dir path. |
| `file_format` | str | "glb" | glb, gltf, fbx, obj, stl, ply (default glb) |
| `include_hierarchy` | bool | True | Also select the parent armature and all children (default True) FBX only (ignored for other formats, reported): |
| `bake_anim` | bool | True | Bake animation into the FBX (default True) |
| `add_leaf_bones` | bool | False | Add end bones for bone tails (default False, engines do not want them) |
| `use_armature_deform_only` | bool | True | Export only deforming bones (default True) |
| `bake_anim_simplify_factor` | float | 0.0 | Keyframe simplification, 0.0 = keep all (default 0.0) |
| `mesh_smooth_type` | str | None | OFF, FACE, EDGE (Blender 5.2 adds SMOOTH_GROUP); validated against this Blender's own list |
| `primary_bone_axis` | str | None | FBX bone axis, e.g. `Y` (X/Y/Z/-X/-Y/-Z); omit for Blender's default |
| `secondary_bone_axis` | str | None | FBX secondary bone axis, e.g. `X` |
| `apply_scale_options` | str | None | FBX_SCALE_NONE, FBX_SCALE_UNITS, FBX_SCALE_CUSTOM, FBX_SCALE_ALL glTF/GLB only (ignored for other formats, reported): shape keys (all default True) |
| `export_animations` | bool | True | glTF: export actions and NLA (ignored for other formats) |
| `export_skins` | bool | True | glTF: export skinning (vertex groups and joints) |
| `export_morph` | bool | True | glTF: export shape keys as morph targets |

---

### `import_file(filepath)`
Import a 3D file into the current Blender scene. Supports: .glb, .gltf, .fbx, .obj, .stl, .ply, .blend, .bvh (needs the io_anim_bvh add-on; the error names enable_addon when it is off) Reply (2.2.0): `imported_objects`, `armatures`, `actions`, `new_actions` and `bone_shape_objects` (empty since the glTF importer is called with `disable_bone_shape=True`).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Absolute path to the file to import Reply lists the new objects and, for rigged files, the armatures, the new actions and any bone-shape helper objects the importer added. |

**Supported formats:** `.glb`, `.gltf`, `.fbx`, `.obj`, `.stl`, `.ply`, `.blend`

---

### `save_blend(filepath, compress, relative_remap, copy, incremental, backup, overwrite, purge_orphans)`
Save the current Blender project as a .blend file.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | None | Absolute path to save to (e.g. "C:/projects/my_scene.blend"). If omitted, saves over the currently open file. If the file has never been saved it goes to <server output_dir>/untitled_<stamp>.blend when that setting is set, else to a temporary path (the reply warns). |
| `compress` | bool | None | True/False to force compression; omit (None) to use the Blender preference use_file_compression (on by default since Blender 5.0). |
| `relative_remap` | bool | True | Remap relative paths when the file moves (default True). |
| `copy` | bool | False | True = save a COPY without changing the working file path (default False). |
| `incremental` | bool | False | True = Blender's own numbered save beside the open file (a.blend -> a1.blend, then a2.blend); do not combine with filepath. Default False. |
| `backup` | bool | True | True (default) keeps Blender's .blend1 backups per the save_version preference; the reply reports that preference value. |
| `overwrite` | bool | True | False refuses when the target exists (default True). |
| `purge_orphans` | bool | False | True purges orphan data-blocks before saving (default False). Reply: path, bytes written, effective compress, is_dirty after, elapsed seconds. |

---

### `load_blend(filepath, force, save_first, load_ui, use_scripts, revert_on_fail)`
Open a .blend file, replacing the current Blender scene.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Absolute path to the .blend file to open |
| `force` | bool | False | The current file has unsaved changes -> the call is refused unless force=True (discard them) or save_first=True (save, then open). |
| `save_first` | bool | False | Save the current file before opening the new one (default False). |
| `load_ui` | bool | None | Load the file's UI layout; omit (None) for the preference use_load_ui. |
| `use_scripts` | bool | None | Allow the file's scripts to run; omit (None) for the preference. |
| `revert_on_fail` | bool | True | Reopen the previous file if loading fails (default True). Reply: scene name and object count, the previous file, the Blender version that saved the file, and whether unsaved changes were discarded. Files saved by Blender 5.x do not open in 4.3. |

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
| `IMG_TO_3D_PORT` | 7862 | Server port. Read by `img_to_3d_server.py` when run standalone; `load_img_to_3d_model` always starts it on 7862 and overrides this variable in the child's environment |
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
Search for assets with optional category filtering. The add-on returns at most the first 20 matching assets (`total_count` carries the real total).

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
Apply a previously downloaded PolyHaven texture to an object. Since 2.1.0 the material is wired in one pass (no duplicate Normal Map / Displacement nodes) and ARM maps use `ShaderNodeSeparateColor` on Blender 5.x.

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

## 27. Version & Server Settings

Server-side tools that need no running Blender. The server keeps its own configuration in `<settings dir>/settings.json` (`~/.blender_mcp` or `BLENDER_MCP_SETTINGS_DIR`); precedence env > file > defaults; see README "Server settings file". On every (re)connection the server asks the add-on for its version and prefixes a one-time `[blender-mcp mismatch]` notice to the next reply when versions or protocol numbers differ (a pre-2.1 add-on is detected from its "Unknown command type" answer).

### `get_version()`
Report the server version, the Blender add-on version (asked over the socket), both protocol numbers with protocol_match, where this server module and its settings file live, and the last update-check result. Never touches the network; the add-on line reads "unreachable" when Blender is not running. Reply lines: server version and protocol; server module path; settings file path and state; the add-on line (version, protocol, Blender, Python, file) or `unreachable (<reason>)`; `protocol_match: true|false|unknown`; `Compatibility: OK (versions and protocols match)`, `MISMATCH. <notice text>`, or `unknown (add-on unreachable)`; `last_update_check: null`.

---

### `get_server_settings()`
Show the Python server's own settings (not Blender's) with the source of each value: env, file or default. Keys: host, port, connect_timeout, command_timeout, blender_exe, output_dir, presets_dir, image_max_pixels, log_level, img_to_3d_port. File: <settings dir>/settings.json where the settings dir is ~/.blender_mcp or BLENDER_MCP_SETTINGS_DIR. Environment variables BLENDER_HOST, BLENDER_PORT, BLENDER_EXE and IMG_TO_3D_PORT override the file.

---

### `set_server_settings(values)`
Change the Python server's own settings and persist them to settings.json.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `values` | str | — | JSON object of key -> value, e.g. '{"port": 9877, "output_dir": "D:/renders"}'. Keys: host, port, connect_timeout, command_timeout, blender_exe, output_dir, presets_dir, image_max_pixels, log_level, img_to_3d_port. A null value resets a key to its default. Unknown keys and wrong types are reported as rejected. host and port take effect on the next connection (the current one is dropped); the other keys apply immediately. A key set by an environment variable keeps the environment value until that variable is unset (reported as overridden). |

---

## 28. File Lifecycle

Save, copy, version, revert, recover, append and link, autosave and path hygiene. `save_blend` and `load_blend` (section 19) gained the same family of parameters. Facts that differ per Blender version: 5.x defaults file compression on; `bpy.data.is_dirty` is False at startup on 5.x and, headless, stays False after edits (the dirty guards therefore fire headless only on 4.x); files saved by 5.x do not open in 4.3.

### `get_file_state()`
Report the state of the open .blend before any destructive step: filepath, is_saved, is_dirty, file_version (the Blender that SAVED the file), the running blender_version, use_autopack, packed_images, missing_files (images and libraries whose path does not exist), libraries (linked .blend paths), autosave_dir with autosave_files (newest first, with mtime), recent_files and backup_files (<name>.blend1.. beside the file). Note: an unsaved factory scene reports is_dirty False on Blender 5.2 and True on 4.3; never assume it.

---

### `new_file(template, empty, load_ui, force)`
Start a new file from the startup file (wm.read_homefile).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `template` | str | None | App template name (omit for the default startup file) |
| `empty` | bool | False | True = an empty scene instead of the startup contents (default False) |
| `load_ui` | bool | False | Load the startup file's UI layout (default False) |
| `force` | bool | False | Unsaved changes in the current file refuse the call unless force=True Reply: object count after the reset. |

---

### `revert_file(force, use_scripts)`
Reload the current file from disk, discarding unsaved changes (wm.revert_mainfile). Refuses when the file was never saved.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | False | Required (True) when there are unsaved changes |
| `use_scripts` | bool | None | Allow the file's scripts to run; omit (None) for the preference |

---

### `recover_file(mode, filepath, force)`
Recover work from Blender's session or autosave files. WARNING: mode LAST_SESSION reopens whatever Blender last quit with (its quit.blend), including a file from an unrelated session; never call it from a test, harness or batch process (Lead ruling 2026-09-11).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | str | "LAST_SESSION" | LAST_SESSION (wm.recover_last_session, reopens quit.blend) or AUTOSAVE (wm.recover_auto_save with filepath from get_file_state.autosave_files) |
| `filepath` | str | None | The autosave file to load when mode is AUTOSAVE |
| `force` | bool | False | Required (True) when the current file has unsaved changes |

---

### `save_copy(filepath, compress, relative_remap, pack_images)`
Save a COPY of the working file without changing its path (deliverable-safe export of the current state). Optionally packs images into the copy only.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Destination .blend path |
| `compress` | bool | None | True/False, or omit for the preference (on by default since 5.0) |
| `relative_remap` | bool | True | Remap relative paths for the new location (default True) |
| `pack_images` | bool | False | True packs all external images into the copy, then unpacks them again so the working file is unchanged (default False) |

---

### `save_version(note, pattern, dir, copy)`
Save a numbered version of the current file (my_scene_v001.blend, _v002, ...) with a sidecar <file>.versions.json recording n, timestamp, note, object count and triangle count. The file must have been saved at least once.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `note` | str | None | Free text stored with the version entry |
| `pattern` | str | "{stem}_v{n:03d}" | File-name pattern; {stem} = current file name without extension, {n} = version number (default "{stem}_v{n:03d}") |
| `dir` | str | None | Folder for the versions (default: beside the current file) |
| `copy` | bool | True | True (default) keeps working on the original; False switches the working file to the new version |

---

### `list_versions(filepath)`
List the numbered versions recorded in <file>.versions.json next to the current file (or the given filepath), with the matching files on disk.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | None | A .blend whose sidecar to read (default: the current file) |

---

### `append_from_blend(filepath, datablocks, names, kind, collection, instance_collections, relative, list_only)`
Append (copy) data-blocks from another .blend into the current file via bpy.data.libraries.load. Use list_only=True first to see what the file holds.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Source .blend |
| `datablocks` | str | None | JSON object of what to load, e.g. '{"objects": ["Cube"], "collections": ["Kit"], "materials": ["Steel"], "node_groups": ["Rust"]}' (objects, collections, materials, node_groups, meshes, images, actions, ...) |
| `names` | str | None | Alternative to `datablocks`: comma list of data-block names |
| `kind` | str | None | Data type for `names` (objects, collections, materials, node_groups, ...) |
| `collection` | str | None | Link appended objects into this collection (default: active) |
| `instance_collections` | bool | False | Appended collections become collection instances |
| `relative` | bool | True | Store the library path relative to the current file (default True) |
| `list_only` | bool | False | True returns the file's contents per data type without loading |

---

### `link_from_blend(filepath, datablocks, names, kind, collection, instance_collections, relative, list_only)`
Link (reference, not copy) data-blocks from another .blend; the same parameters as append_from_blend with link=True. Linked data stays read-only and follows the source file.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Source .blend |
| `datablocks` | str | None | JSON object per data type, e.g. '{"collections": ["Kit"]}' |
| `names` | str | None | Alternative to `datablocks`: comma list of data-block names |
| `kind` | str | None | Data type for `names` |
| `collection` | str | None | Collection to link objects into (default: active) |
| `instance_collections` | bool | False | Linked collections become collection instances (usual for kits) |
| `relative` | bool | True | Store the library path relative to the current file (default True) |
| `list_only` | bool | False | True returns the file's contents per data type without linking |

---

### `set_autosave(enabled, interval_minutes, save_versions, temp_dir, persist)`
Configure Blender's autosave preferences. Only passed values change.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | None | preferences.filepaths.use_auto_save_temporary_files |
| `interval_minutes` | int | None | auto_save_time (minutes between autosaves) |
| `save_versions` | int | None | Number of .blend1/.blend2 backups kept on save (save_version) |
| `temp_dir` | str | None | temporary_directory for autosave files (empty = system temp) |
| `persist` | bool | False | True also writes userpref.blend (consent rule: nothing here is persisted unless you ask); the reply says whether it was persisted |

---

### `make_paths_relative()`
Make every external file path in the current .blend relative to it (bpy.ops.file.make_paths_relative). The file must be saved first.

---

### `make_paths_absolute()`
Make every external file path in the current .blend absolute (bpy.ops.file.make_paths_absolute).

---

### `find_missing_files(directory, find_all)`
Search a folder (recursively) for external files the .blend cannot find and relink them (bpy.ops.file.find_missing_files).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `directory` | str | — | Folder to search |
| `find_all` | bool | False | True re-searches every file, not only the missing ones (default False) Reply: how many were found and the list still missing. |

---

### `pack_all()`
Pack every external image and other packable file into the .blend (bpy.ops.file.pack_all) so the file is self-contained.

---

### `unpack_all(unpack_method)`
Unpack every packed file back to disk (bpy.ops.file.unpack_all).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `unpack_method` | str | "USE_LOCAL" | USE_LOCAL (default, write next to the .blend into //textures), WRITE_LOCAL, USE_ORIGINAL, WRITE_ORIGINAL, KEEP, REMOVE |

---

## 29. Settings & Presets

Generic, `bl_rna`-driven access to scene, render, output, colour, units, viewport, preferences and add-on settings (a property removed in 5.x, such as `use_gtao`, simply does not appear), typed conveniences on top of it, portable JSON presets (six ship with the server: `game_bake`, `preview`, `final_eevee`, `final_cycles`, `sprite_sheet`, `turntable_video`; a user preset of the same name shadows a shipped one), Blender's own presets, and the project profile. Dynamic enums (`view_transform`, `compute_device_type`) are validated by assignment and the error quotes Blender's live list.

### `describe_settings(scope)`
Describe every property of a settings scope: type, current value, default, enum items, min/max, description and read-only flag. Generated from Blender's own property definitions (bl_rna), so it lists exactly what this Blender has.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scope` | str | — | SCENE, RENDER, OUTPUT (image_settings + filepath), CYCLES, EEVEE, COLOR (view + display settings), UNITS, VIEWPORT (shading and overlays of the first 3D view), PREFS_FILEPATHS, PREFS_VIEW, PREFS_EDIT, PREFS_SYSTEM, PREFS_INPUT, ADDON:<module> (that add-on's preferences), MCP (this add-on's preferences), SERVER (the Python server's settings file; answered without Blender) |

---

### `get_settings(scope, keys)`
Read current values of a settings scope (values only; enums as strings, vectors as lists, pointers as names).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scope` | str | — | One of the scopes listed in describe_settings (SERVER is answered without Blender) |
| `keys` | str | None | Comma-separated property names to read; omit for all |

---

### `set_settings(scope, values, persist)`
Write values into a settings scope. Each value is validated against Blender's property definition (enum membership, numeric range, type) before writing; read-only properties are refused with the reason.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scope` | str | — | One of the scopes listed in describe_settings. SERVER writes the Python server's settings.json (same as set_server_settings). |
| `values` | str | — | JSON object of property -> value, e.g. '{"resolution_x": 1280, "engine": "CYCLES"}' |
| `persist` | bool | False | For PREFS_*, ADDON: and MCP scopes, True also saves userpref.blend (consent rule: preferences are never persisted unless asked) Reply: set (applied keys) and unset {key: reason}, like add_modifier. |

---

### `settings_snapshot(name, scopes, filepath)`
Capture the current values of the given scopes under a name (kept in the Blender session; survives an add-on reload, dies with Blender) and optionally into a JSON file. Restore with settings_restore.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Snapshot name |
| `scopes` | str | "SCENE,RENDER,OUTPUT,CYCLES,EEVEE,COLOR,UNITS,VIEWPORT" | Comma list of scopes (default: the eight scene-level scopes) |
| `filepath` | str | None | Also write the snapshot to this JSON file |

---

### `settings_restore(name, filepath, scopes)`
Restore a settings snapshot taken with settings_snapshot (by name, or from a JSON file). Reports keys that no longer exist or failed to apply.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | None | Snapshot name (session snapshots) |
| `filepath` | str | None | JSON file written by settings_snapshot (used when name is omitted) |
| `scopes` | str | None | Comma list to restore only some scopes (default: all in the snapshot) |

---

### `list_settings_snapshots()`
List the settings snapshots held in the Blender session with the key count per scope.

---

### `delete_settings_snapshot(name)`
Delete a session settings snapshot by name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Snapshot name (see list_settings_snapshots) |

---

### `set_output_settings(filepath, file_format, color_mode, color_depth, compression, quality, film_transparent, use_stamp, use_overwrite, use_placeholder, ffmpeg)`
Set the render output path, image format and video settings in one call. Only the parameters you pass are changed. Note (live pass on 5.2.1 GUI, 2026-09-11, `docs/LIVE-TEST-REPORT-2026-09-11.md`): on Blender 5.x `FFMPEG` is rejected unless `media_type` is `VIDEO`; the add-on sets it for you, so pass `file_format="FFMPEG"` and it works.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | None | Output path (e.g. "//renders/frame_####"); Blender adds the extension |
| `file_format` | str | None | PNG, JPEG, OPEN_EXR (EXR accepted), OPEN_EXR_MULTILAYER, TIFF, BMP, TARGA, WEBP, FFMPEG (video; sets media_type VIDEO on 5.x) |
| `color_mode` | str | None | BW, RGB or RGBA |
| `color_depth` | str | None | "8" / "16" (PNG, TIFF) or "16" / "32" (OPEN_EXR) |
| `compression` | int | None | PNG compression 0-100 |
| `quality` | int | None | JPEG/WEBP quality 0-100 |
| `film_transparent` | bool | None | Transparent background (alpha) on/off |
| `use_stamp` | bool | None | Burn metadata into the image |
| `use_overwrite` | bool | None | Animation output: overwrite existing frames |
| `use_placeholder` | bool | None | Animation output: write placeholder frames first |
| `ffmpeg` | str | None | JSON for video, e.g. '{"format": "MPEG4", "codec": "H264", "constant_rate_factor": "MEDIUM"}' |

---

### `set_color_management(view_transform, look, exposure, gamma, display_device, sequencer_colorspace)`
Set scene colour management. Game-asset renders and bakes usually want view_transform "Standard" so colours match the engine (Blender's default is AgX).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `view_transform` | str | None | Standard, Filmic, AgX, Khronos PBR Neutral, Raw, False Color |
| `look` | str | None | e.g. "None", "AgX - Medium High Contrast" (names depend on the transform) |
| `exposure` | float | None | Float, 0.0 is neutral |
| `gamma` | float | None | Float, 1.0 is neutral |
| `display_device` | str | None | sRGB, Display P3, Rec.1886, Rec.2020 (or as installed) |
| `sequencer_colorspace` | str | None | Colour space for the sequencer Values are validated by assignment (the enum list is not readable headless). |

---

### `set_render_quality(preset, engine)`
Apply a quality preset. The previous values are stored in the settings snapshot "_before_quality" so settings_restore("_before_quality") undoes it.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `preset` | str | "PREVIEW" | PREVIEW (25% resolution, 16 samples, denoise on, simplify on), DRAFT (50%, 64 samples), FINAL (100%, 256 samples or the engine default, persistent data on) |
| `engine` | str | None | Optionally switch the engine first (CYCLES, BLENDER_EEVEE, BLENDER_WORKBENCH) |

---

### `set_render_device(device, backend, persist)`
Choose the Cycles render device and compute backend.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `device` | str | "GPU" | GPU or CPU (scene.cycles.device) |
| `backend` | str | None | OPTIX, CUDA, HIP, ONEAPI, METAL or NONE (Cycles preferences compute_device_type; validated by assignment, then every device of that type is enabled) |
| `persist` | bool | False | True also saves userpref.blend (consent rule) Reply: the devices Blender found with their use flag. |

---

### `list_render_devices()`
List the compute devices Cycles can see (CPU, CUDA/OPTIX/HIP/ONEAPI/METAL GPUs) with their current use flag and the active backend. Headless sessions may report CPU only.

---

### `set_simplify(enabled, subdivision, child_particles, texture_limit, volume_resolution)`
Toggle and configure render Simplify (scene.render.use_simplify and friends).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | — | Simplify on/off |
| `subdivision` | int | None | Max subdivision level for renders (simplify_subdivision) |
| `child_particles` | float | None | 0.0-1.0 fraction of child particles |
| `texture_limit` | str | None | OFF, 128, 256, 512, 1024, 2048, 4096, 8192 (Cycles texture limit) |
| `volume_resolution` | float | None | 0.0-1.0 volume resolution factor |

---

### `set_frame_range(start, end, fps, fps_base, current)`
Set the scene frame range and frame rate (shared with the animation tools). Only the parameters you pass are changed.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start` | int | None | Scene range start (`frame_start`) |
| `end` | int | None | Scene range end (`frame_end`) |
| `fps` | float | None | Frame rate (24) |
| `fps_base` | float | None | Frame rate base (1.0; 1.001 for 23.976) |
| `current` | int | None | Also set the current frame |

---

### `set_scene_units(preset, system, scale_length, length_unit, rescale_objects)`
Set scene units (shared with the engine-readiness tools), by engine preset or explicitly. Unreal wants centimetres (scale_length 0.01, CENTIMETERS); Unity, Godot, Bevy and glTF want metres (1.0, METERS).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `preset` | str | None | UNREAL, UNITY, GODOT, BEVY, TIMBERMESH or NONE (sets system, scale_length and length_unit for that engine) |
| `system` | str | None | METRIC, IMPERIAL or NONE |
| `scale_length` | float | None | Unit scale (1.0 = metres, 0.01 = centimetres) |
| `length_unit` | str | None | ADAPTIVE, KILOMETERS, METERS, CENTIMETERS, MILLIMETERS, MICROMETERS, MILES, FEET, INCHES, THOU |
| `rescale_objects` | bool | False | True also scales the scene's objects so they keep their real-world size under the new scale_length (default False) |

---

### `set_viewport_defaults(shading, light, color_type, show_overlays, show_floor, show_stats, clip_end, lens)`
Apply viewport display settings to EVERY 3D view (the persistent counterpart of the per-capture shading/overlay options). Needs a GUI session.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `shading` | str | None | WIREFRAME, SOLID, MATERIAL or RENDERED |
| `light` | str | None | STUDIO, MATCAP or FLAT (solid mode) |
| `color_type` | str | None | MATERIAL, SINGLE, OBJECT, RANDOM, VERTEX or TEXTURE (solid mode) |
| `show_overlays` | bool | None | Overlay toggle |
| `show_floor` | bool | None | Floor grid toggle |
| `show_stats` | bool | None | Statistics overlay toggle |
| `clip_end` | float | None | View clip distance |
| `lens` | float | None | Viewport focal length in mm |

---

### `save_preset(name, scopes, overwrite, description)`
Save the current values of the given settings scopes as a named preset JSON under the server's presets dir (portable across files and Blender versions).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Preset name (letters, digits, _ . -), becomes <name>.json |
| `scopes` | str | "RENDER,OUTPUT,CYCLES,EEVEE,COLOR" | Comma list of scopes to capture (default RENDER,OUTPUT,CYCLES,EEVEE,COLOR) |
| `overwrite` | bool | False | True replaces an existing preset of that name (default False) |
| `description` | str | None | Free text stored in the preset |

---

### `load_preset(name, filepath, scopes)`
Apply a preset (user preset by name, shipped preset by name, or any preset JSON by filepath) through set_settings, scope by scope. Keys that this Blender does not have are reported as unset and the rest still apply.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | None | Preset name (see list_presets); shipped: game_bake, preview, final_eevee, final_cycles, sprite_sheet, turntable_video |
| `filepath` | str | None | A preset JSON file instead of a name |
| `scopes` | str | None | Comma list to apply only some of the preset's scopes |

---

### `list_presets()`
List the shipped presets and the user presets in the server's presets dir (name, description, scopes, origin).

---

### `delete_preset(name)`
Delete a user preset file. Shipped presets cannot be deleted (a user preset that shadows one can).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Preset name |

---

### `export_preset(name, filepath)`
Write a preset (user or shipped) to a JSON file of your choice, e.g. to share it or keep it with a project.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Preset name |
| `filepath` | str | — | Destination .json path |

---

### `import_preset(filepath, name, overwrite)`
Copy a preset JSON file into the server's presets dir so it can be loaded by name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Source .json (a file written by save_preset or export_preset) |
| `name` | str | None | Name to store it under (default: the file's stem) |
| `overwrite` | bool | False | True replaces an existing preset of that name |

---

### `apply_blender_preset(category, name)`
Apply one of Blender's own Python presets (script.execute_preset), e.g. the built-in render size presets.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `category` | str | — | Preset folder, e.g. "render", "cycles/sampling", "cycles/viewport", "cloth", "fluid", "camera" (see list_blender_presets) |
| `name` | str | — | Preset name as listed, e.g. "HDTV 1080p" |

---

### `list_blender_presets(category)`
List Blender's own presets in a category (bpy.utils.preset_paths).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `category` | str | "render" | e.g. "render", "cycles/sampling", "cycles/viewport", "cloth", "fluid", "camera", "safe_areas", "tracking_camera" |

---

### `set_project_profile(engine_target, export_dir, texture_dir, render_dir, kit_unit, naming, max_triangles, texture_size, notes, apply_units)`
Store the project's target engine and conventions in the scene (custom property blendermcp_profile, travels with the file) and mirror them to <file>.mcp-profile.json. Export and texturing tools read their defaults here.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine_target` | str | — | UNITY, UNREAL, GODOT, BEVY, TIMBERMESH or NONE |
| `export_dir` | str | None | Default export folder |
| `texture_dir` | str | None | Default texture folder |
| `render_dir` | str | None | Default render folder |
| `kit_unit` | float | None | Modular kit grid unit in scene units |
| `naming` | str | None | Naming convention note (e.g. "SM_<Asset>_<Variant>") |
| `max_triangles` | int | None | Triangle budget the validation tools check against |
| `texture_size` | int | None | Texture size budget the validation tools check against |
| `notes` | str | None | Free text |
| `apply_units` | bool | False | True also sets scene units for the target (Unreal: centimetres) |

---

### `get_project_profile(apply_units)`
Read the project profile stored in the scene (see set_project_profile), or report that none is set.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `apply_units` | bool | False | True also (re)applies the scene units for the profile's engine_target, e.g. after opening the file on another machine (default False) |

---

## 30. Add-ons & Preferences

Consent rule: nothing here writes `userpref.blend` unless the caller passes `persist=True` or `confirm=True`, and the reply always says whether preferences were persisted. `enable_addon` always registers with `default_set=True` (the only form that makes the add-on visible in `preferences.addons` and lets Rigify's own `register()` run); `persist` only decides whether the preferences are saved afterwards.

### `list_addons(enabled_only, filter)`
List installed add-ons: module, name, version, category, enabled, has_preferences, path.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled_only` | bool | False | True lists only enabled add-ons |
| `filter` | str | None | Case-insensitive substring on module or name |

---

### `enable_addon(module, persist)`
Enable an add-on by module name (addon_utils.enable), e.g. "rigify", "io_anim_bvh", "node_wrangler", "cycles".

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `module` | str | — | Add-on module name (see list_addons) |
| `persist` | bool | False | True also saves userpref.blend (consent rule; default False) Reply: the add-on's bl_info, or the error text if enabling fails. |

---

### `disable_addon(module, persist)`
Disable an add-on by module name (addon_utils.disable).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `module` | str | — | Add-on module name |
| `persist` | bool | False | True also saves userpref.blend (consent rule; default False) |

---

### `get_addon_preferences(module)`
Read an add-on's preferences (same data as get_settings(scope="ADDON:<module>")).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `module` | str | — | Add-on module name |

---

### `set_addon_preferences(module, values, persist)`
Write an add-on's preferences (same machinery as set_settings(scope="ADDON:<module>")).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `module` | str | — | Add-on module name |
| `values` | str | — | JSON object of property -> value |
| `persist` | bool | False | True also saves userpref.blend (consent rule; default False) |

---

### `save_preferences(confirm)`
Write Blender's user preferences to userpref.blend (wm.save_userpref). Refuses without confirm=True and reports preferences.is_dirty and use_preferences_save so you can decide.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `confirm` | bool | False | Must be True to actually save |

---

### `list_workspaces()`
List workspaces with the area types each one contains, and the current workspace.

---

### `set_workspace(name)`
Switch the active workspace, e.g. set_workspace("Layout") to guarantee a 3D view for the capture tools. Needs a GUI session. Note (live pass on 5.2.1 GUI, 2026-09-11, `docs/LIVE-TEST-REPORT-2026-09-11.md`): the reply reads the workspace that was active BEFORE the call; Blender applies the switch on the next event-loop turn, so read `list_workspaces` afterwards if you need the new name.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Workspace name (see list_workspaces) |

---

### `get_addon_settings()`
Read the BlenderMCP add-on's own preferences: port, autostart_server and which API keys are set (keys are never echoed back; each shows set true/false).

---

### `set_addon_settings(values, persist)`
Change the BlenderMCP add-on's own preferences.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `values` | str | — | JSON object, e.g. '{"port": 9877, "autostart_server": true, "hyper3d_api_key": "..."}'. API keys are write-only: the reply says set true/false per key, never the value. A port change applies when the add-on's server is restarted (Disconnect / Connect). |
| `persist` | bool | False | True also saves userpref.blend (consent rule; default False) |

---


## Baseline tools with new parameters (all params listed; mark the new ones when writing)

---

## 31. Session

The server persists the session as `<settings dir>/sessions/<name>.json`; the add-on captures the state and, on `restore_session_state`, applies it and reopens the file named in it in ONE call with the same dirty guard as `load_blend` (`force` to discard changes), so one call gives one guard check and one reply (`file_loaded`, `not_restored`). `start_blender(restore_session="last")` is the shorthand for restoring after a restart.

### `save_session_state(name, include)`
Save what you are working on so it survives a Blender restart: open file path and dirty flag, frame range and current frame, active camera, selection and active object, mode, each 3D view's view matrix/distance/shading, workspace and the named settings snapshots. Stored as <settings dir>/sessions/<name>.json. Pairs with close_blender / start_blender(restore_session=...).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | "last" | Session name (default "last") |
| `include` | str | _SESSION_INCLUDE_DEFAULT | Comma list of FILE, FRAME, CAMERA, SELECTION, ACTIVE, MODE, VIEWPORT, WORKSPACE, SETTINGS_SNAPSHOTS (default: all) |

---

### `restore_session_state(name, load_file, force)`
Restore a session saved with save_session_state: the add-on reopens its file (with the unsaved-changes guard) and re-applies frame, camera, selection, active object, mode, views, workspace and settings snapshots. Reports what could not be restored.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | "last" | Session name (default "last") |
| `load_file` | bool | True | Reopen the session's .blend first (default True) |
| `force` | bool | False | Discard unsaved changes in the current file when reopening |

---

## 32. Rigging: Armatures & Bones

Armatures are built and read by bone NAME: `EditBone` references are undefined after leaving EDIT mode on both Blender versions, so every tool returns names and re-enters edit mode itself. Bone selection uses `Bone.select` on 4.x and `PoseBone.select` on 5.x inside the tools. `get_armature_info` is the perceive tool for every act tool in this section.

### `create_armature(name, location, display_type, show_in_front, bones)`
Create an armature object (and its data) in the active collection, optionally with bones in one go.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Armature object name |
| `location` | str | "0,0,0" | "x,y,z" (default origin) |
| `display_type` | str | "OCTAHEDRAL" | OCTAHEDRAL (default), STICK, BBONE, ENVELOPE or WIRE |
| `show_in_front` | bool | True | Draw the bones through meshes (default True) |
| `bones` | str | None | Optional JSON list exactly as add_bones takes it, e.g. '[{"name": "root", "head": [0,0,0], "tail": [0,0,1]}, {"name": "spine", "head": [0,0,1], "tail": [0,0,2], "parent": "root", "connected": true}]' Reply: armature name and bone count. |

---

### `add_bones(armature, bones)`
Add bones to an armature in one edit-mode session.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `bones` | str | — | JSON list of {"name", "head": [x,y,z], "tail": [x,y,z], "parent"?, "connected"?: bool, "roll"?: degrees, "deform"?: bool, "inherit_rotation"?: bool}. A parent may be an existing bone or an earlier entry in the same list. connected=true snaps the head to the parent's tail (reported). Name collisions get Blender's .001 suffix (reported). Reply: created names, snapped bones, warnings. |

---

### `get_armature_info(armature, include_pose, space, bone_filter)`
THE perceive tool for a rig: every bone in hierarchy order with parent, children, head, tail, length, roll (degrees), connected, deform, bone collections and constraints, plus pose_position, display_type, action and the bone-collection summary.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `include_pose` | bool | False | Add per-bone pose (location, rotation_mode, rotation in degrees or quaternion, scale, world head/tail) |
| `space` | str | "WORLD" | WORLD (default) or ARMATURE for head/tail coordinates |
| `bone_filter` | str | None | Glob on bone names, e.g. "arm.L*" or "*spine*" |

---

### `set_bone_properties(armature, bone, head, tail, roll, parent, connected, deform, inherit_rotation, inherit_scale, new_name, envelope_distance, bbone_segments)`
Change one bone's rest properties. Only the parameters you pass are set.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `bone` | str | — | Bone name |
| `head` | str | None | New head position as "x,y,z" |
| `tail` | str | None | New tail position as "x,y,z" |
| `roll` | float | None | Degrees |
| `parent` | str | None | Parent bone name ("" to clear) |
| `connected` | bool | None | Snap the head to the parent's tail and keep it there |
| `deform` | bool | None | Whether the bone deforms skinned meshes |
| `inherit_rotation` | bool | None | Bool |
| `inherit_scale` | str | None | FULL, FIX_SHEAR, ALIGNED, AVERAGE, NONE, NONE_LEGACY |
| `new_name` | str | None | Rename the bone (vertex groups are not renamed; see rename_bones) |
| `envelope_distance` | float | None | Envelope radius |
| `bbone_segments` | int | None | Bendy-bone segments (1 = plain bone) Reply: set list and unset {prop: reason}, like add_modifier. |

---

### `delete_bones(armature, bones, reparent_children)`
Delete bones in edit mode.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `bones` | str | — | Comma list of bone names, or one glob like "finger*" |
| `reparent_children` | bool | True | Attach children of a deleted bone to its parent (default True) Reply: deleted names and reparented names. |

---

## 33. Rigging: Skinning & Weights

`bind_armature` runs `parent_set` with the mesh selected and the armature active and restores the previous active object and selection. Weight tools that need WEIGHT_PAINT mode switch and restore it. `render_weight_map` and `find_unweighted_vertices(render=True)` are the perceive tools (the latter returns an image instead of numbers); both need a GUI session for the viewport path (headless the add-on answers with a teach-style error).

### `bind_armature(mesh, armature, method, keep_transform)`
Skin a mesh to an armature (parent + Armature modifier + vertex groups).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `armature` | str | — | Armature object name |
| `method` | str | "AUTO" | AUTO (automatic weights, default), ENVELOPE (envelope weights), EMPTY_GROUPS (one empty group per bone, paint later), NAME (modifier only; keeps the groups the mesh already has) |
| `keep_transform` | bool | True | Preserve the mesh's world transform (default True) Reply: modifier name, vertex-group count, groups with no weighted vertices. |

---

### `get_vertex_groups(mesh, include_stats)`
List a mesh's vertex groups: name, index, lock, and with include_stats the vertex count, weight min/max/mean and whether a matching bone exists on the bound armature.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `include_stats` | bool | True | Compute per-group weight statistics (default True) |

---

### `get_vertex_weights(mesh, indices, group, max_verts)`
Read per-vertex weights: {index: {group: weight}}.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `indices` | str | None | Comma list of vertex indices to read (default: all, up to max_verts) |
| `group` | str | None | Only vertices that belong to this group |
| `max_verts` | int | 2000 | Truncate after this many vertices (default 2000; truncation is reported) |

---

### `set_vertex_weights(mesh, group, weights, mode, create_group)`
Write vertex weights into one group.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `group` | str | — | Vertex group name |
| `weights` | str | — | JSON object {"index": weight, ...} or list of [index, weight] pairs |
| `mode` | str | "REPLACE" | REPLACE (default), ADD or SUBTRACT (VertexGroup.add semantics) |
| `create_group` | bool | True | Create the group if it does not exist (default True) Reply: vertices written, whether the group was created. |

---

### `render_weight_map(mesh, group, angle, max_size, show_zero_weights)`
See a vertex group's weights as Blender's weight-paint heat map (blue 0 to red 1) from a named viewport angle. Mode and viewport state are restored.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `group` | str | — | Vertex group to display |
| `angle` | str | "front" | front, back, left, right, top, bottom, iso_front_right, iso_front_left |
| `max_size` | int | 800 | Maximum pixel dimension (default 800) |
| `show_zero_weights` | bool | True | Draw unweighted vertices in black (default True) |

---


## Baseline tools with new parameters (all params listed; mark the new ones when writing)

---

### `find_unweighted_vertices(mesh, tolerance, render, angle)`
Find vertices whose total deform weight is below tolerance, plus vertices with any weight above 1 or below 0. Returns the numbers, or with render=True an image of those vertices selected in edit mode from a named viewport angle (mode and selection restored; needs a GUI session).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `tolerance` | float | 0.001 | Total-weight threshold (default 0.001) |
| `render` | bool | False | True returns an image instead of the numbers (default False) |
| `angle` | str | "front" | front, back, left, right, top, bottom, iso_front_right, iso_front_left Reply (render=False): counts and up to 500 indices per category as JSON. |

---

## Baseline tools with new parameters (all params listed; mark the new ones when writing)

## 34. Rigging: Pose & Constraints

Pose bones default to QUATERNION; `set_pose` switches the rotation mode you ask for and reports it. A pose value written on an ANIMATED bone is overwritten at the next depsgraph update unless keyframed (`set_pose` warns). World positions are `matrix_world @ head`. `add_constraint` copies the `set` / `unset` reply shape of `add_modifier`; object-pointer properties take object names and `subtarget` a bone name.

### `set_pose(armature, bones, rotation_mode, space, keyframe, frame)`
Pose bones by writing location / rotation / scale, optionally keying them.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `bones` | str | — | JSON object {"bone": {"location": [x,y,z], "rotation": [x,y,z] degrees or [w,x,y,z] quaternion, "scale": [x,y,z]}, ...}; each key optional |
| `rotation_mode` | str | "XYZ" | XYZ (Euler degrees, default) or QUATERNION; set on each bone before writing |
| `space` | str | "POSE" | POSE (default) - values are bone-local pose values |
| `keyframe` | bool | False | Also insert keyframes for the channels written (default False) |
| `frame` | int | None | Frame for the keyframes (default: current) Note: a pose written on an animated bone is overwritten by the animation at the next update unless keyframe=True. Reply: bones written, rotation modes changed. |

---

### `get_pose(armature, bones, space)`
Read the current pose: per bone location, rotation_mode, rotation (degrees or quaternion), scale, world head and tail.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `bones` | str | None | Comma list of bone names (default: all) |
| `space` | str | "WORLD" | WORLD (default) or ARMATURE for head/tail |

---

### `reset_pose(armature, bones, transforms)`
Reset pose transforms to rest (identity), without needing POSE-mode operators.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `armature` | str | — | Armature object name |
| `bones` | str | None | Comma list of bone names (default: all) |
| `transforms` | str | "ALL" | ALL (default), LOCATION, ROTATION or SCALE |

---

### `add_constraint(owner, constraint_type, bone, name, params)`
Add a constraint to an object or a pose bone.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `owner` | str | — | Object name (the armature when bone is given) |
| `constraint_type` | str | — | IK, COPY_LOCATION, COPY_ROTATION, COPY_TRANSFORMS, DAMPED_TRACK, TRACK_TO, STRETCH_TO, LIMIT_ROTATION, LIMIT_LOCATION, CHILD_OF, ... (the error lists the valid types) |
| `bone` | str | None | Pose bone name to own the constraint (default: the object) |
| `name` | str | None | Constraint name (default: Blender's) |
| `params` | str | None | JSON object of constraint properties, e.g. for IK: '{"target": "Rig", "subtarget": "ik_hand.L", "pole_target": "Rig", "pole_subtarget": "pole_elbow.L", "chain_count": 2, "pole_angle": -90}'. Object-pointer properties take object names; subtarget takes a bone name; angles are degrees. Reply: constraint name, set list and unset {prop: reason}, like add_modifier. |

---

### `get_constraints(owner, bone)`
List the constraints on an object or a pose bone: name, type, target, subtarget, influence, mute, plus type-specific keys (IK: chain_count, pole_target, pole_subtarget, pole_angle_deg, use_tail).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `owner` | str | — | Object name (the armature when bone is given) |
| `bone` | str | None | Pose bone name (default: the object's own constraints) |

---

### `remove_constraint(owner, name, bone)`
Remove a constraint by name from an object or a pose bone.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `owner` | str | — | Object name (the armature when bone is given) |
| `name` | str | — | Constraint name (see get_constraints) |
| `bone` | str | None | Pose bone name (default: the object) |

---

## Running the Server

```bash
cd "D:\App Dev\blender_mcp"
.venv\Scripts\blender-mcp.exe
# or
.venv\Scripts\python.exe -m blender_mcp.server
```

The addon TCP server must be running in Blender on port 9876. Install `addon.py` via Blender Preferences → Add-ons → Install.
