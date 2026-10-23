# Blender MCP — Tool Reference

204 tools exposed via `@mcp.tool()` in `src/blender_mcp/server.py` (92 in 2.0.0 + 56 in 2.1.0 + 21 in 2.2.0 + 13 in 2.3.0 + 22 in 2.4.0; the list is `tests/baseline_tools_2.4.txt`, cut at the B2-G3 test gate from the frozen server; every 2.3.0 name keeps its position and schema, additive parameters only, with one relaxation: `apply_modifier.modifier_name` is optional since 2.4.0; the 2.3.0 list stays in `tests/baseline_tools_2.3.txt`). Blender 4.3 – 5.2 (tested on 4.3.2 and 5.2.1 LTS); version 2.4.0 on branch `blender-5.2`.

**Object names in list parameters (2.4.0, R-B2-41).** Every `objects` / `meshes` / `collision` / `sockets` / `lods` parameter is a comma list. Each entry is tried as an EXACT object name first, so `Panel[1]`, `Q?Mark` and `Star*Prop` are addressable as themselves; only when nothing matches exactly is the entry read as an fnmatch glob (`Prop*`, `UCX_?`). A name that itself contains a comma cannot be passed in a comma list: use `collection=` where the tool offers it, or the generic driver's `#uid` ref (section 35).

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
35. [Generic Driver](#35-generic-driver)
36. [Validation](#36-validation)
37. [Optimisation & LODs](#37-optimisation--lods)
38. [Collision](#38-collision)
39. [Sockets, Pivots & Hierarchy](#39-sockets-pivots--hierarchy)
40. [Engine Export](#40-engine-export)

---

## 1. Scene & Object Info

### `get_scene_info(limit)`
Get detailed information about the current Blender scene (objects, materials, render settings). The object list is capped at the first 10 objects unless `limit` says otherwise (`object_count` carries the real total). Since 2.1.0 the reply also carries `file` (the `get_file_state` summary) and `settings_summary` (engine, resolution, samples, frame range, units). Since 2.3.0 every object entry carries `uid` (the `session_uid`, usable as `#<uid>` in the generic driver, section 35). Since 2.4.0 the reply carries `total_triangles` and `total_vertices` (mesh objects, modifiers evaluated) and every object entry adds `triangles` (evaluated; 0 for an object in EDIT mode, a Blender limit), `hidden` (`not visible_get()`, so collection-level hiding counts), `parent` (name or null) and `collections` (the names of the collections the object is linked to). `batch_export` (section 40) resolves its roots from this reply with `limit=0`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | None | Object entries to return; omitted = the add-on default of 10, `0` = every object (2.4.0). Not sent on the wire when omitted. Non-integers are refused by the add-on |

**Returns:** JSON string

---

### `get_object_info(object_name)`
Get detailed information about a specific object. Reply (2.2.0): every object gains `parent`, `parent_type` and `parent_bone`; a MESH adds `vertex_groups`, `shape_keys` and `armature` (the Armature modifier's target); an ARMATURE adds `bones`, `pose_position`, `display_type`, `action` and `bone_collections`. Since 2.3.0 the reply carries `uid` (the `session_uid`, usable as `#<uid>` in the generic driver, section 35).

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

### `close_blender(force, save)`
Close Blender: the instance started by `start_blender()`, or, over the add-on socket, one the user launched by hand.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | False | Kill a process this server started immediately instead of asking it to quit; for a Blender the user launched by hand there is no process to kill, so `force` still asks it to quit over the socket and the reply says so (2.4.0, R-B2-45) |
| `save` | bool | False | Save the current .blend (if it has a path) before quitting |

**Returns:** Status message: `Blender closed (gracefully).`, `Blender closed (process terminated).` (only after a real terminate / kill of a managed process), `Blender closed (no running instance found).`, or `Blender asked to quit (not a process this server started; force applies only to managed processes).`

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

### `set_origin(name, origin_type, location)`
Set an object's origin point. The mesh stays where it is in the world: the mesh data is offset (shape keys move with the Basis) and the object location compensated; children keep their world transform (`children_kept` counts them). A mesh shared by several objects gets its own copy first (`isolated: true` in the reply), so the sharing objects never jump.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `origin_type` | str | "ORIGIN_GEOMETRY" | `ORIGIN_GEOMETRY`, `ORIGIN_CURSOR`, `ORIGIN_CENTER_OF_MASS`, `ORIGIN_CENTER_OF_VOLUME`; since 2.4.0 also `BOTTOM_CENTER` (bounding-box bottom centre, the game-prop standard), `TOP_CENTER` and `CUSTOM` |
| `location` | str | None | `"x,y,z"` world point, required for `CUSTOM` (refused before Blender is contacted when missing); sent as a list (2.4.0) |

**Reply (2.4.0):** `origin_type` echoed, `new_location`, `offset_applied` (local-space offset applied to the mesh), `children_kept`, `isolated`.

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

### `apply_modifier(name, modifier_name, apply_all, keep)`
Apply a named modifier on an object, collapsing it into the mesh data, or every modifier in stack order with `apply_all` (2.4.0).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `modifier_name` | str | None | Exact modifier name as shown in Blender's Properties panel. Optional since 2.4.0 (the only 2.3.0 required parameter relaxed this stage); without `apply_all` it is still required and the server answers `Error: modifier_name required unless apply_all` before contacting Blender |
| `apply_all` | bool | False | Apply every modifier in order; one failure does not stop the rest (2.4.0) |
| `keep` | str | None | Comma list of modifier NAMES or TYPES to skip with `apply_all`, e.g. `ARMATURE` (2.4.0) |

**Reply (2.4.0):** `applied` (names), `kept`, `failed` (`{name: reason}`), `remaining`; the single-modifier form returns `{error}` on failure. The wire payload is unchanged from 2.2.0 when neither new parameter is used.

---

### `get_mesh_stats(name)`
Return detailed topology statistics for a mesh object (vertex/edge/face counts, etc.). A pure read: it never changes the mode or the active object (an EDIT-mode mesh is flushed first).

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Mesh object name |

**Returns:** JSON. Since 2.4.0: `triangles` is the evaluated loop-triangle count (modifiers applied; the 2.2.0 formula is kept as `triangles_simple`), `ngons`, `quads`, `tris`, `loose_verts`, `loose_edges`, `non_manifold_edges` (Blender's definition, boundary edges included), `boundary_edges`, `has_custom_normals`, `uv_layers` (names), `color_attributes` (names), `material_slots` (`[{index, material}]`), `dimensions`, `unapplied_transform` (`{rotation, scale, negative_scale}`), `origin_offset` (`{to_bbox_center, to_bbox_bottom}`, local space).

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
Add a modifier to an object with optional parameters. Since 2.3.0 `params` go through the generic driver's coercer (section 35): dotted keys reach nested settings, pointer properties (`object`, `target`, `mirror_object`, ...) accept `#uid`, `IDTYPE:Name` or a plain object name, enum values are validated, and an out-of-range number is REFUSED under `unset` with `outside [min, max]` where 2.2.0 silently clamped it. The `set` / `unset` reply shape is unchanged.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Object name |
| `modifier_type` | str | — | `MIRROR`, `BEVEL`, `ARRAY`, `SOLIDIFY`, `SUBSURF`, `DECIMATE`, `DISPLACE`, `SHRINKWRAP`, `WIREFRAME`, `SKIN`, etc. |
| `modifier_name` | str | None | Display name (auto-generated if omitted) |
| `params` | str | None | JSON string of modifier properties (validated and coerced since 2.3.0; out-of-range values are refused, not clamped) |

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

### `export_object(name, filepath, file_format, include_hierarchy, bake_anim, add_leaf_bones, use_armature_deform_only, bake_anim_simplify_factor, mesh_smooth_type, primary_bone_axis, secondary_bone_axis, apply_scale_options, export_animations, export_skins, export_morph, objects, apply_modifiers, axis_forward, axis_up, global_scale, use_tspace, use_triangles, use_custom_props, colors_type, export_apply, export_tangents, export_extras, export_yup, export_attributes, export_vertex_color, export_animation_mode)`
Export an object (or the full scene) to a 3D file. A skinned mesh exports WITH its armature, children and animation by default (include_hierarchy). The low-level exporter under `export_for_engine` (section 40), which applies an engine preset on top of it. Reply (2.2.0): `exported_objects` (names, or "all" for a scene export), `include_hierarchy`, the `fbx_options` / `gltf_options` / `export_options` actually used, and `ignored` (parameters that do not apply to the chosen format). Reply (2.4.0): `size_bytes`, `object_count`, `vertices_written`, `triangles_written` (evaluated when modifiers were applied, `modifiers_applied` says which), `warnings` (`unapplied scale on <obj>`, `no UV layer on <obj>`, `ngons on <obj> with use_triangles off`, `material <m> has no nodes`) and `transform_baked` (True only for OBJ on Blender 4.x, which always bakes the object transform into the vertices).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | None | Object to export; exports entire scene if omitted |
| `objects` | str | None | Comma list of objects to export (2.4.0), alternative to `name`; both given is refused before Blender is contacted |
| `apply_modifiers` | bool | True | Export the evaluated mesh (2.4.0); glTF `export_apply` defaults to this |
| `axis_forward` / `axis_up` | str | None | Exporter axes (FBX, OBJ), e.g. `-Z` / `Y` for Unity, `-Y` / `Z` for Unreal (2.4.0) |
| `global_scale` | float | None | Exporter scale (2.4.0) |
| `use_tspace` / `use_triangles` / `use_custom_props` | bool | None | FBX tangent space, triangulation, custom properties (2.4.0) |
| `colors_type` | str | None | FBX colour export type (2.4.0) |
| `export_apply` / `export_tangents` / `export_extras` / `export_yup` / `export_attributes` | bool | None | glTF switches (2.4.0) |
| `export_vertex_color` | str | None | glTF `MATERIAL` / `ACTIVE` / `NONE` (Blender 5.2 adds `NAME`) (2.4.0) |
| `export_animation_mode` | str | None | glTF `ACTIONS` / `ACTIVE_ACTIONS` / `BROADCAST` / `NLA_TRACKS` / `SCENE` (2.4.0) |

All 2.4.0 parameters default to None = the exporter's own default; enum values are validated against this Blender's live enum and a kwarg the live exporter lacks is dropped with a `warnings` entry.
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

### `import_file(filepath, axis_forward, axis_up, global_scale, use_custom_normals, use_image_search, ignore_leaf_bones, import_shading, merge_vertices)`
Import a 3D file into the current Blender scene. Supports: .glb, .gltf, .fbx, .obj, .stl, .ply, .blend, .bvh (needs the io_anim_bvh add-on; the error names enable_addon when it is off) Reply (2.2.0): `imported_objects`, `armatures`, `actions`, `new_actions` and `bone_shape_objects` (empty since the glTF importer is called with `disable_bone_shape=True`). Reply (2.4.0): `objects` with one entry per imported object (`name`, `type`, `vertices`, `triangles`, `has_custom_normals`, `uv_layers`, `materials`) and `ignored` (importer kwargs this Blender's importer lacks).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | — | Absolute path to the file to import Reply lists the new objects and, for rigged files, the armatures, the new actions and any bone-shape helper objects the importer added. |
| `axis_forward` / `axis_up` | str | None | Importer axes (FBX, OBJ) (2.4.0) |
| `global_scale` | float | None | Importer scale (2.4.0) |
| `use_custom_normals` | bool | True | Keep the file's custom normals (2.4.0) |
| `use_image_search` / `ignore_leaf_bones` | bool | None | FBX importer switches (2.4.0) |
| `import_shading` | str | None | glTF `NORMALS` / `FLAT` / `SMOOTH` (2.4.0) |
| `merge_vertices` | bool | None | glTF vertex merge (2.4.0) |

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
List all objects in the scene matching a given type. Since 2.3.0 every entry carries `uid` (the `session_uid`, usable as `#<uid>` in the generic driver, section 35); `find` (section 35) is the query tool with name globs and property conditions.

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

### `execute_blender_code(code, result_expr)`
Execute arbitrary Python code inside Blender. Break complex operations into smaller chunks. Since 2.3.0 prefer `describe` and the driver tools (section 35) for anything they cover; keep this for BMesh / numpy loops. The reply is `Code executed successfully: <stdout>`, plus a `value:` line when `result_expr` is given (encoded like `rna_get`: an ID as `{uid, id_type, name}`). An exception no longer surfaces as a raw error: the add-on answers `{error: "<type>: <msg>", traceback: [last 3 frames], line}` and the server prints `Error: <type>: <msg> (line n)` followed by the frames and, since 2.4.0, a `stdout:` line with whatever the code printed before it failed.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `code` | str | — | Python code to execute in Blender's Python environment |
| `result_expr` | str | None | Expression evaluated after the code in the same namespace; its value is returned under `value` (2.3.0) |

---

## 27. Version & Server Settings

Server-side tools that need no running Blender. The server keeps its own configuration in `<settings dir>/settings.json` (`~/.blender_mcp` or `BLENDER_MCP_SETTINGS_DIR`); precedence env > file > defaults; see README "Server settings file". On every (re)connection the server asks the add-on for its version and prefixes a one-time `[blender-mcp mismatch]` notice to the next reply when versions or protocol numbers differ (a pre-2.1 add-on is detected from its "Unknown command type" answer).

### `get_version()`
Report the server version, the Blender add-on version (asked over the socket), both protocol numbers with protocol_match, where this server module and its settings file live, and the last update-check result. Never touches the network; the add-on line reads "unreachable" when Blender is not running. Reply lines: server version and protocol; server module path; settings file path and state; the add-on line (version, protocol, Blender, Python, file) or `unreachable (<reason>)`; `protocol_match: true|false|unknown`; `Compatibility: OK (versions and protocols match)`, `MISMATCH. <notice text>`, or `unknown (add-on unreachable)`; `last_update_check: null`.

---

### `get_server_settings()`
Show the Python server's own settings (not Blender's) with the source of each value: env, file or default. Keys: host, port, connect_timeout, command_timeout, blender_exe, output_dir, presets_dir, image_max_pixels, log_level, img_to_3d_port, driver_reply_max_chars, driver_batch_max. File: <settings dir>/settings.json where the settings dir is ~/.blender_mcp or BLENDER_MCP_SETTINGS_DIR. Environment variables BLENDER_HOST, BLENDER_PORT, BLENDER_EXE, IMG_TO_3D_PORT, BLENDER_DRIVER_REPLY_MAX and BLENDER_DRIVER_BATCH_MAX override the file.

---

### `set_server_settings(values)`
Change the Python server's own settings and persist them to settings.json.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `values` | str | — | JSON object of key -> value, e.g. '{"port": 9877, "output_dir": "D:/renders"}'. Keys: host, port, connect_timeout, command_timeout, blender_exe, output_dir, presets_dir, image_max_pixels, log_level, img_to_3d_port, driver_reply_max_chars (reply cap of the generic driver's reading tools, default 24000; env BLENDER_DRIVER_REPLY_MAX), driver_batch_max (commands per batch, default 200; env BLENDER_DRIVER_BATCH_MAX; since 2.4.0 the setting can only LOWER the limit, the add-on caps at 200). A null value resets a key to its default. A driver_reply_max_chars of 0 or less falls back to 24000 (2.4.0). Unknown keys and wrong types are reported as rejected. host and port take effect on the next connection (the current one is dropped); the other keys apply immediately. A key set by an environment variable keeps the environment value until that variable is unset (reported as overridden). |

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
THE perceive tool for a rig: every bone in hierarchy order with parent, children, head, tail, length, roll (degrees), connected, deform, bone collections and constraints, plus pose_position, display_type, action and the bone-collection summary. Since 2.3.0 the reply carries the armature's `uid` (usable as `#<uid>` in the generic driver, section 35).

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

## 35. Generic Driver

13 tools (2.3.0) that address Blender's RNA and operator layer directly, CDP-style, instead of one hand-written tool per question. Wire handler keys: `drv_describe`, `drv_get`, `drv_set`, `drv_new`, `drv_remove`, `drv_find`, `drv_context`, `drv_op`, `drv_call`, `drv_snapshot`, `drv_diff`, `drv_batch`, `drv_transaction` (these are the `type` values inside `batch`; any legacy key such as `add_modifier` works there too). Contract: `docs/PLAN.md` section 12 (D1.3) and `docs/LEAD-RULINGS-C.md`; wire protocol stays 1. JSON-typed parameters are strings over MCP and parsed by the server before anything is sent: an object parameter that is not a JSON object answers `Error: <name> must be a JSON object, e.g. '{"key": value}'`, a list-or-object parameter that decodes to a scalar answers `Error: <name> must be a JSON object or list`, malformed JSON answers `Error: <name> is not valid JSON (<msg> at position N)`; none of these contacts Blender.

### Refs

Every driver tool addresses data by ref; none takes `object_name`.

| Form | Resolves to | Example |
|---|---|---|
| `#<uid>` | the ID with that `session_uid` (monotonic, never reused, survives rename and undo; found by a full `bpy.data` scan on every call, no cache) | `#127` |
| `<IDTYPE>:<name>` | `bpy.data.<collection>[name]`; IDTYPE is the `ID.id_type` identifier, case-insensitive, mapped to its collection by a static table (unknown -> `{error, valid}`) | `OBJECT:Cube`, `mesh:Cube`, `NODETREE:Geometry Nodes` |
| `<id ref>/<path>` | an RNA path under that ID in `path_resolve` syntax: dotted props, `[index]`, `["key"]` collection items, `["prop"]` ID properties | `OBJECT:Cube/modifiers["Bevel"].width`, `#127/location[2]`, `#127/["my_prop"]` |
| `context` / `context/<path>` | `bpy.context` as root | `context/scene.frame_current`, `context/active_object` |
| `data` / `data/<path>` | `bpy.data` as root | `data/objects`, `data/materials["Red"]` |
| `prefs` / `prefs/<path>` | `bpy.context.preferences` as root | `prefs/edit.undo_steps` |

The path is split at the last `.` outside brackets into parent and leaf; a trailing `[<int>]` on the leaf is an index; a leaf of the form `["key"]` alone is an ID-property access. A ref ending on a struct (no leaf, or a POINTER / COLLECTION leaf) resolves to that struct. A root followed by `/` and an empty path (`context/`, `#127/`) is refused. A ref that resolves to nothing answers `{error, tried: [the collection or path searched], closest: [up to 3 difflib matches]}`. Every reply that mentions an ID carries `{uid, id_type, name}`; an embedded ID (a material's node tree, shape keys) adds `embedded: true` and is reachable by path only, never by `#uid`. **Unsafe structs (R33).** Pointer and collection members of `ToolSettings`, of every struct whose base chain reaches `Paint` (Sculpt, VertexPaint, ImagePaint, UvSculpt, GpPaint, ...) and of `CurveMapping` are never dereferenced by the depth walk: `scene.tool_settings.uv_sculpt` is an uninitialised `Paint` in the Blender 4.3.2 factory file and its getters crash the process (uncatchable). The walk reports such a member at its depth-0 shape (`{struct, path}`) and names the skip once in `truncated`; a ref whose path contains `uv_sculpt` is refused outright.

### Value encoding (`rna_get`, `call_method`, `scene_snapshot`, `batch` results, `execute_blender_code` value)

| RNA | JSON |
|---|---|
| INT / FLOAT / BOOLEAN scalar | number / bool |
| arrays (vectors, colors, bool arrays) | list; rotation properties (`unit == 'ROTATION'`) scaled when `angles="DEGREES"` |
| matrices | list of row lists |
| STRING | string |
| ENUM | identifier string; enum flags -> sorted list of identifiers |
| POINTER to an ID | `{uid, id_type, name}` or `null` |
| POINTER to a non-ID struct | `{struct, path}` at depth 0; expanded to `depth` |
| COLLECTION | `{count}`; `{count, items: [...]}` when named with `[]` in `fields` (`modifiers[]`) or with `fields="all"` and `depth > 0` |
| ID properties | under `custom: {}` (JSON as stored) |
| anything else | `str(value)` with `encoded: "str"` on the entry |

`rna_get` at depth 0 reads every non-COLLECTION property of a struct, pointers included (an ID pointer as `{uid, id_type, name}`, a non-ID struct as `{struct, path}`); collections report `{count}`; `depth >= 1` expands. Coercion on the way in (`rna_set`, `data_new` `set`, operator `params`): list -> vector / color / matrix; string -> enum (validated; `valid` lists the identifiers); `"#uid"`, `"IDTYPE:Name"` or `{"uid": n}` -> pointer resolved against the property's `fixed_type`; int -> float; 0/1 -> bool; `["key"]` ID-property paths accept any JSON value; a read-only property is refused with Blender's own message under `rna_error`. Angles are RADIANS by default: `angles="DEGREES"` converts on the way in (`rna_set`) and out (`rna_get`, `scene_snapshot`). The hand-written tools (`rotate_object`, `set_pose`, `add_keyframe`, `set_bone_properties`) keep taking degrees.

### Replies, teaching errors and the reply cap

Success replies carry `success: true`; reading replies add `chars` (length of the JSON reply, including `truncated` and the `chars` key itself) and `truncated` (compact one-line notes naming what was dropped and which filter narrows it; `[]` when nothing was dropped). Error replies carry `error` plus the teaching keys that apply: `closest` (misspelt struct, property, operator, method or IDTYPE), `valid` (enum identifiers, collections with `new`, snapshot names, context keys, actions), `tried` (where a ref was searched), `rna_error` (Blender's own error text, verbatim, never re-worded), `poll_message`, `context_applied`, `undo_available`, `background`, `refused`. The server prints an error as `Error: <text>` followed by one `<key>: <json>` line per teaching key, and a success reply as pretty-printed JSON. Handlers never raise for a bad input; a transport failure surfaces as `Error: <socket text>`.

Every reading command sends `max_chars`: the tool's own argument when given, else the server setting `driver_reply_max_chars` (default 24000; env `BLENDER_DRIVER_REPLY_MAX`; an older add-on without the key assumes 24000). The add-on builds the reply under that cap. `describe` first blanks every description text (property and enum-item descriptions; the shape is kept), and only then drops whole properties from the end of the list and names them in `truncated`; `count` is the number of entries kept and `total` the struct's full count. The envelope (`success`, `chars`, `truncated`) stays under 400 chars. A flat `Object` dump is about 4.5-4.8 KB, so the default cap holds most single-struct reads; raise `max_chars` (for example 60000) for `fields="all"` with `depth >= 2` or to read the dropped descriptions back.

---

### `describe(what, fields, include_functions, search, max_chars)`
Describe an RNA struct, one property, or an operator from Blender's own definitions (`bl_rna`), so you can learn the API before driving it.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `what` | str | — | A ref (describes the struct it resolves to, or the single property when the path ends on one), a struct name (`"Object"`, `"BooleanModifier"`, `"RenderSettings"`), an operator id (`"mesh.bevel"`), or `"ops"` to list operators |
| `fields` | str | `all` | `all` or a comma list of property identifiers |
| `include_functions` | bool | False | Also list the struct's functions (`{name: {description, params: [[id, type, is_output, is_required]]}}`) |
| `search` | str | None | Substring over identifier, name and description of the struct's properties; with `what="ops"`, over every operator id and description |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

**Reply.** Struct: `{success, kind: "struct", identifier, base, description, count, total, properties: {id: {type, subtype, unit, readonly, array_length, default, min, max, soft_min, soft_max, enum_items: [[id, name, description]], dynamic, fixed_type, is_animatable, description, is_enum_flag, length_max}}, functions?, chars, truncated}`. A `dynamic` enum (render engine, view transforms, Cycles devices: static items empty or a single entry headless) must be validated by assignment; `rna_set` then reports the live `valid` list. A property ref adds `owner` and `kind: "property"`. Operator: `{success, kind: "operator", idname, name, description, properties (same shape), poll_now (evaluated in the current context), refused}` (no `bl_options`: unreachable for C operators). `what="ops"` + `search`: `{success, kind: "ops", count, operators: [[idname, description]]}`. Not found -> `{error, closest}` over struct names or operator ids.

---

### `rna_get(refs, fields, depth, angles, max_chars)`
Read properties of one or more refs in one round trip.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `refs` | str | — | Comma list or JSON list of refs (`"#127, OBJECT:Cube/location"` or `'["#127"]'`); must name at least one |
| `fields` | str | None | Comma list (or JSON list) of property identifiers; `all` expands pointers to `depth`; a collection named with `[]` (`modifiers[]`) lists its items; default = every non-collection property |
| `depth` | int | 0 | Pointer expansion depth |
| `angles` | str | `RADIANS` | `RADIANS` (raw) or `DEGREES` for rotation properties |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

**Reply:** `{success, results: [{ref, uid?, id_type?, name?, value}], errors: [{ref, error, tried?, closest?}], chars, truncated}`. A bad ref lands in `errors`; the other refs still read. A non-ID struct result carries `struct` / `path` (and `id`); unknown field names are listed under `value.missing`.

---

### `rna_set(sets, angles, stop_on_error, publish)`
Set properties by ref. Each value is coerced and validated against the property's RNA definition (enum membership, range, type, read-only) before it is written.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sets` | str | — | JSON list of `[ref, value]` pairs, or a JSON object `{ref: value}` (normalised to pairs by the server), e.g. `'[["#127/location", [1, 2, 3]], ["SCENE:Scene/render.engine", "CYCLES"]]'`; must be non-empty |
| `angles` | str | `RADIANS` | `RADIANS` (raw) or `DEGREES` for rotation properties |
| `stop_on_error` | bool | True | Stop at the first refused item (earlier items stay applied); `False` applies every valid item and reports the rest |
| `publish` | bool | True | Call `msgbus.publish_rna` for each change. In a GUI session RNA updates publish property changes themselves, so subscribers fire with `publish=False` too and this flag adds nothing observable there; headless it is the only path, and even then msgbus delivery does not happen in a `--background` session (E21, measured on 4.3.2 and 5.2.1) |

**Reply:** `{success, results: [{ref, set, value_after?, error?, valid?, closest?, rna_error?}], applied, failed, stopped_at}`; `success` is true when `failed == 0`. Properties with side effects go through their API (`frame_current` -> `scene.frame_set`).

---

### `data_new(collection, args, kwargs, link_to, set)`
Create an ID through `bpy.data.<collection>.new(...)` and return its uid.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `collection` | str | — | A `bpy.data` collection with `new()`: `objects`, `meshes`, `materials`, `lights`, `cameras`, `collections`, `armatures`, `images`, `node_groups`, `actions`, `curves`, `texts`, ... |
| `args` | str | None | JSON list of positional arguments for `new()`, matched to the live signature; a ref is accepted where an ID is expected (`'["Cube2", "MESH:Cube"]'` for `objects`) |
| `kwargs` | str | None | JSON object of keyword arguments for `new()` |
| `link_to` | str | None | For objects: `"scene"` (the active scene's root collection, the default), `"COLLECTION:Name"` or `"#uid"`; non-object IDs are never linked |
| `set` | str | None | Initial properties as for `rna_set` (paths relative to the new ID) |

**Reply:** `{success, uid, id_type, name (final, after Blender's dedupe), linked_to, set: {applied, failed, results}}`. A collection without `new()` -> `{error, valid: [collections with new]}`.

---

### `data_remove(refs, do_unlink, purge_orphans)`
Remove IDs by ref (`bpy.data.<collection>.remove`).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `refs` | str | — | Comma list or JSON list of ID refs (`"#127, MESH:Cube"`); must name at least one |
| `do_unlink` | bool | True | Unlink from every user first; `False` refuses an ID that still has users (per item, with `users_before`) |
| `purge_orphans` | bool | False | Run `orphans_purge(do_recursive=True)` afterwards; `purged` = ID count delta |

**Reply:** `{success, results: [{ref, removed, users_before, error?}], purged}`; `success` = every ref removed. A removed uid afterwards answers the not-found error with `tried`.

---

### `find(id_type, name, where, scope, fields, limit, max_chars)`
Query IDs by type, name glob and property conditions; returns uids.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `id_type` | str | `OBJECT` | `ID.id_type` identifier (`OBJECT`, `MESH`, `MATERIAL`, ...) |
| `name` | str | None | Glob pattern (`fnmatch`), e.g. `"Cube*"` |
| `where` | str | None | JSON list of `[path, op, value]` conditions, `op` in `==, !=, <, <=, >, >=, in, contains, exists`; `[]` in a path means "any item": `'[["type", "==", "MESH"], ["modifiers[].type", "contains", "ARMATURE"]]'` |
| `scope` | str | `SCENE` | `SCENE` (objects of the active scene) or `DATA` (all of `bpy.data`); non-OBJECT types always search `bpy.data` and the reply says `scope: "DATA"` |
| `fields` | str | `name,type` | Comma list of properties reported per hit |
| `limit` | int | 100 | Maximum hits (`count` = kept, `total` = matches) |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

**Reply:** `{success, count, total, items: [{uid, id_type, name, <fields>}], chars, truncated}`. A bad `op` -> `{error, valid}`; `exists` is true when the path resolves to a non-None value.

---

### `context_info(fields)`
What `call_operator`'s context declaration is applied against.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fields` | str | None | Comma list of keys to report; omit for all |

**Reply:** `{success, mode, active_object: {uid, ...} or null, selected: [{uid, ...}], scene, view_layer, window, background, areas: [{type, regions: [str]}], frame_current, edit_object, pose_object}`. Headless under `--factory-startup` Blender still reports `window: true` and 4 areas; `background: true` is the signal that viewport operators cannot run.

---

### `call_operator(op, params, context, exec_context, undo)`
Run a Blender operator under a declared context. Parameters are validated against the operator's RNA before the call; the operator is polled first and a poll failure returns the reason without running anything.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `op` | str | — | Operator id, e.g. `"object.modifier_add"`, `"mesh.bevel"` |
| `params` | str | None | JSON object of operator properties, e.g. `'{"type": "BEVEL"}'` (unknown key -> `closest`; bad enum -> `valid`; Blender's text under `rna_error`) |
| `context` | str | None | JSON object, the context declaration below |
| `exec_context` | str | `EXEC_DEFAULT` | `EXEC_DEFAULT`; `INVOKE_*` is refused headless and allowed only with `context.area` in a GUI session |
| `undo` | bool | True | `undo_push("mcp:<op>")` before the call, so `transaction` / `batch` rollback covers it |

**Context declaration:** `{"mode": "EDIT", "active": "#127", "selected": ["#127", "OBJECT:Cutter"], "area": "VIEW_3D", "region": "WINDOW", "scene": "SCENE:Scene", "keep": false, "extra": {"edit_object": "#127"}}`. `active` / `selected` set the real selection and are restored afterwards; `mode` uses `object.mode_set` on the active object and is restored; `area` / `region` pick the first matching area of the GUI window under `temp_override`, and headless (`bpy.app.background`) answer `{error: "Viewport context needs a GUI session: ...", background: true}` (never keyed on the window being None); `extra` keys are passed to `temp_override` and WHITELISTED: `window, screen, area, region, scene, view_layer, object, active_object, selected_objects, selected_editable_objects, edit_object, pose_object, active_bone, active_pose_bone, modifier, point_cache` (an unknown key -> `{error, valid: [the 16 keys]}` before anything runs, because `temp_override` would accept it silently); `keep: true` leaves mode and selection as the operator left them. `context_applied` echoes `{mode, active: uid, selected: [uid], area, region, extra_keys}`.

**Refused operators** (the session must stay alive): `wm.quit_blender` (use `close_blender`), `wm.console_toggle`, `wm.window_new`, `wm.window_fullscreen_toggle`, `screen.animation_play` (modal, never returns headless: use `set_frame` / `playblast`), `wm.read_factory_settings` (kills the socket server), `preferences.addon_disable` when `module` names this add-on, `script.reload`. `describe("ops")` marks them `refused`. `wm.open_mainfile` / `wm.save_mainfile` run, but `load_blend` / `save_blend` carry the safety checks.

**Reply:** `{success, result: ["FINISHED"], reports: [captured print / report lines], context_applied, elapsed_ms, modal}`; `RUNNING_MODAL` / `PASS_THROUGH` are returned verbatim with `modal: true` and a `warning` that nothing waits for the operator. Poll failure: `{error, poll_message, context_applied, rna_error}`. Refused: `{error, refused: true}`.

---

### `call_method(ref, method, args, kwargs, depsgraph, max_chars)`
Call an RNA function (or a plain Python method) on an ID or struct.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | str | — | The ID or struct (`"#127"`, `"MESH:Cube"`, `"OBJECT:Cube/modifiers[\"Bevel\"]"`) |
| `method` | str | — | Function name: `from_pydata`, `validate`, `calc_tangents`, `evaluated_get`, `to_mesh`, `to_mesh_clear`, `new_from_object` (on `data/meshes`), `path_from_id`, ... |
| `args` | str | None | JSON list of positional arguments; refs accepted where an ID is expected |
| `kwargs` | str | None | JSON object of keyword arguments |
| `depsgraph` | bool | False | Pass `context.evaluated_depsgraph_get()` as the first `Depsgraph` parameter (`evaluated_get`, `to_mesh`) |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

An RNA function is validated against `bl_rna.functions` (missing required parameters are named); a callable that is not an RNA function (`Mesh.from_pydata`, `path_from_id`) is called directly. Unknown method -> `{error, closest}`.

**Reply:** `{success, value, chars, truncated}`; an ID return value is `{uid, id_type, name}`, a struct return `{struct, path}` when `path_from_id` works.

---

### `scene_snapshot(name, scope, types, fields, include, depth, return_tree, angles, max_chars)`
Capture the scene (or all of `bpy.data`) keyed by uid, for a later `scene_diff`. Lives in the Blender session (`driver_namespace["blendermcp_driver_snapshots"]`), dies with it.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Snapshot name (non-empty) |
| `scope` | str | `SCENE` | `SCENE` (IDs reachable from the active scene) or `DATA` (every ID collection) |
| `types` | str | None | Comma list or JSON list of `id_type` filters, e.g. `"OBJECT,MESH"` |
| `fields` | str | `name,type,parent,location,rotation_euler,scale,hide_viewport,hide_render,users` | Comma list (or JSON list) of per-ID properties captured; a field an ID lacks is omitted, never an error |
| `include` | str | None | Comma list or JSON list of per-object expansions: `modifiers`, `constraints`, `material_slots`, `vertex_groups`, `children` |
| `depth` | int | 1 | Parent / child tree depth |
| `return_tree` | bool | True | `False` returns only `{success, name, count, chars}` (capture without paying for the reply) |
| `angles` | str | `RADIANS` | `RADIANS` or `DEGREES` for rotation fields |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

**Reply:** `{success, name, captured_at, scope, count, ids: {"<uid>": {id_type, name, <fields>, <include lists>}}, tree: [{uid, children: [...]}], chars, truncated}`.

---

### `scene_diff(a, b, max_chars)`
Diff two snapshots, or a snapshot against the live scene.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `a` | str | — | Snapshot name |
| `b` | str | `NOW` | Snapshot name, or `NOW` = the live scene captured with `a`'s stored parameters (not stored) |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

**Reply:** `{success, a, b, added: [{uid, id_type, name}], removed: [...], renamed: [{uid, before, after}], changed: {"<uid>": {field: [before, after]}}, chars, truncated}`. A rename appears only under `renamed` (`name` is never a `changed` field). Unknown name -> `{error, valid: [snapshot names]}`.

---

### `batch(commands, stop_on_error, undo, refs, max_chars)`
Run many commands in one round trip and one main-thread slot (no other client command interleaves). Every command goes through the add-on's normal dispatch, so gate checks and errors are identical to the single tools.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `commands` | str | — | JSON list of `{"id"?: str, "type": handler key, "params"?: object}`; e.g. `'[{"id": "m", "type": "drv_new", "params": {"collection": "meshes", "args": ["M"]}}, {"type": "drv_new", "params": {"collection": "objects", "args": ["O", "$m.uid"]}}]'` |
| `stop_on_error` | bool | True | Stop at the first failing command; the rest are reported `skipped` |
| `undo` | str | `AUTO` | `AUTO` = one undo step for the batch, rolled back when a command fails and `stop_on_error` is set (Scene marker `_mcp_batch`); `NONE` for read-only batches |
| `refs` | bool | True | Replace `"$<id>.<path>"` strings in later params with that earlier result (`path` = dotted keys and `[index]`; a missing id or path is that command's error) |
| `max_chars` | int | None | Reply cap (default: `driver_reply_max_chars`) |

**Rules.** The server refuses a batch above the `driver_batch_max` setting (default 200, env `BLENDER_DRIVER_BATCH_MAX`; the setting can only lower the limit, since 2.4.0 it is clamped to 200) with `Error: batch has N commands; the limit is M ...` without contacting Blender; the add-on caps at 200 as well. Every 2.4.0 tool (sections 36-40 except `batch_export`, which is a server loop) is a valid batch item by its handler key and is covered by `undo=AUTO` and by `transaction` rollback (harness step B2-G8). A command whose reply has an `error` key OR `success: false` counts as an error (an `rna_set` whose items all failed trips `stop_on_error`). The 60 s wall cap (add-on constant `_DRV_BATCH_WALL_S`) is checked BETWEEN commands, never inside one: a single long command is never interrupted, and once the elapsed time exceeds the cap the next command is not run. On a wall stop the reply is `success: false`, `stopped_at` = the index of the first command not run, the remaining commands are `skipped`, `rolled_back` follows the undo mode (`AUTO` + `stop_on_error` rolls back, `NONE` keeps what ran) and `error` = `"wall cap <N> s reached after command <i>; <k> skipped"`; `elapsed_ms` reports the truth. `drv_batch` inside a batch -> `{error: "nested batch refused ..."}`.

**Reply:** one head line `batch: N commands, a ok, b error, c skipped; rolled_back=.., undo_available=.., stopped_at=.., elapsed_ms=..` followed by the JSON `{success, results: [{id, type, status: "ok"|"error"|"skipped", result?, error?, elapsed_ms}], rolled_back, undo_available, stopped_at, elapsed_ms, chars, truncated}`; `success` = no command errored.

---

### `transaction(action, name)`
Group driver changes into one undo step.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | str | — | `BEGIN`, `COMMIT`, `ROLLBACK` or `STATUS` (case-insensitive) |
| `name` | str | None | Transaction name (`BEGIN`); shown in Blender's undo history as `mcp:<name>` |

**Semantics.** `BEGIN` pushes `mcp:<name>:pre`, sets the Scene ID property `_mcp_txn` to a sequence number, then pushes `mcp:<name>`; `ROLLBACK` pushes a closing `mcp:<name>:rollback` step first (2.4.0 fix: Python-only mutations made after BEGIN, such as an `rna_set` or a `set_origin`, were not on the undo stack and survived a ROLLBACK that followed an exhausted stack; `batch(undo=AUTO)` shares the fix) and then calls `ed.undo` until the marker is gone (the state before the transaction), capped at `prefs.edit.undo_steps`; `COMMIT` deletes the marker. A `ROLLBACK` whose marker is already gone answers `{error: "transaction marker not found; nothing reverted", undo_calls: 0}`. Declared side effects: one Scene ID property while a transaction is open, and `is_dirty`. Interleaved GUI edits are undone by a ROLLBACK too, so keep transactions short. Undo works in `--background` Blender on both supported versions.

**Reply:** `{success, action, name, undo_calls, marker_present, undo_available, open}`. Nested `BEGIN` -> `{error: "transaction '<open>' is open; COMMIT or ROLLBACK it first", open: true, name}`; `COMMIT` / `ROLLBACK` without an open transaction -> `{error, open: false}`; `ROLLBACK` when `ed.undo` cannot run -> `{error: "undo is not available in this session; the transaction marker is dropped, nothing was reverted", undo_available: false, ...}`.

---

### Changes to existing tools in 2.3.0 (additive)
- `get_scene_info`, `get_object_info`, `find_objects_by_type`, `get_armature_info`: every object entry carries `uid` (the `session_uid`), so the friendly views and the driver share one identity.
- `execute_blender_code(code, result_expr=None)`: `value` for the expression, structured error dict instead of a raw exception (section 26).
- `get_version`: the add-on's reply gains `registries: {name: count}` over every `blendermcp_*` registry in `bpy.app.driver_namespace` (reference images, settings snapshots, session states, driver snapshots, transactions). The server's `get_version` text lines are unchanged.
- `add_modifier`: `params` go through the driver's coercer (pointer values accept a ref or a plain object name); the `set` / `unset` reply is unchanged.
- Server settings `driver_reply_max_chars` and `driver_batch_max` (section 27).
- A JSON string value passed to any list-or-object parameter (`sets`, `bones`, `weights`, `keys`, ...) is now refused with `Error: <name> must be a JSON object or list`; before 2.3.0 the 2.2.0 rigging tools echoed it back as the reply.

---

## 36. Validation

Engine-readiness pre-flight (2.4.0, stage 1 of the game-dev requests; contract `docs/PLAN.md` section 13, rulings `docs/LEAD-RULINGS-B2.md`). Every tool in sections 36-40 is an add-on handler of the same name; the server validates enums, XOR rules and csv shapes BEFORE contacting Blender (a refusal lists the valid values and sends nothing), forwards comma lists as typed (the add-on resolves exact names first, then globs, R-B2-41), and renders the add-on's reply as JSON or `Error: <text>`. Angles are DEGREES. Pure reads (`get_mesh_stats`, `validate_asset(fix=False)`, `compare_meshes`, most `find_mesh_issues` kinds) never change the mode or the active object (R-B2-36); every mutating tool restores OBJECT mode and the selection. Profiles: `GENERIC`, `UNITY`, `UNREAL`, `GODOT`, `BEVY`, `TIMBERMESH`.

### `validate_asset(objects, collection, profile, max_triangles, require_uv, require_uv2, max_bone_influences, fix)`
THE pre-flight tool: 22 fixed checks per mesh, each `PASS` / `WARN` / `FAIL` with a detail, a count and up to 20 sample indices, plus an overall verdict. `fix=True` applies only the safe fixes and re-checks; `fix=False` is read-only and lists `fixes_available`. Collision-typed meshes (wire-displayed + hidden from render, or named by a profile's collision rule) are skipped; non-mesh objects report `PASS` with `checks: {}`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | None | Comma list of object names (exact first, then globs); or |
| `collection` | str | None | Collection name (exactly one of the two; both or neither is refused before Blender is contacted) |
| `profile` | str | "GENERIC" | `GENERIC`, `UNITY`, `UNREAL`, `GODOT`, `BEVY` or `TIMBERMESH` |
| `max_triangles` | int | None | Budget for `triangle_budget` (None = PASS) |
| `require_uv` | bool | True | `missing_uv` is FAIL when set |
| `require_uv2` | bool | False | `missing_uv2` is FAIL when set |
| `max_bone_influences` | int | 4 | `bone_influences` WARNs above this many groups with weight > 0 on a vertex |
| `fix` | bool | False | Apply the safe fixes in order and re-run the checks |

**Check ids (22, every one present per mesh):** `unapplied_transform` FAIL, `negative_scale` FAIL, `origin_bottom_center` WARN, `dimensions` WARN (max extent x `scale_length` above 100 m or below 1 cm; a flat plane never WARNs), `ngons` (FAIL when the profile exports tangents: UNITY / UNREAL / GODOT / BEVY; else WARN), `non_manifold` WARN (edges with more than two faces, wire edges and opposed-winding edges; boundary edges are PASS and counted in `detail` as `boundary: <n>`), `loose` WARN, `degenerate` FAIL, `doubles` WARN (1e-5), `inconsistent_normals` FAIL (faces a recalc dry-run on a copy would flip), `missing_uv`, `uv_out_of_bounds` WARN, `missing_uv2`, `vertex_count_16bit` WARN (> 65535 vertices before export splitting), `triangle_budget`, `material_slots` WARN (empty or unused slot), `materials_without_nodes` WARN, `images_missing` FAIL (file absent and not packed), `bone_influences` WARN, `illegal_names` WARN (space, `.` or non-ASCII in an object or material name for UNITY / UNREAL / GODOT / BEVY; PASS for GENERIC / TIMBERMESH), `name_collisions` WARN (names equal after stripping `.NNN`, across the whole file), `hierarchy` (FAIL when a collision-typed child does not follow the profile's rule: UNREAL `UCX_<mesh>_NN`, GODOT `-col` / `-convcol`; TIMBERMESH without a `#` root; else PASS).

**Safe fixes (`fix=True`, in order):** apply rotation + scale (normals recalculated after a negative-scale apply), `fix_normals(OUTSIDE)`, remove doubles (1e-5), delete loose, triangulate ngons only, rename illegal characters to `_`.

**Reply:** `{success, profile, verdict, summary: {pass, warn, fail} (objects by verdict), objects: {name: {verdict, type, checks: {<id>: {status, detail, count, sample_indices}}}}, fixes_available: [{object, check, fix}], fixes_applied: [{object, check, fix, detail}]}` (both fix lists always present). Errors: `{error, valid}` for a bad profile, `{error, unknown}` for unknown names.

---

### `find_mesh_issues(mesh, issue, render, angle, max_indices)`
Element indices for one issue class (validate says what, this says where). `render=True` returns a PNG of those elements selected in edit mode from a named viewport angle (mode and selection restored; needs a GUI session: headless it answers the `needs a GUI session` error with `background: true`).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `issue` | str | — | `NON_MANIFOLD`, `NGONS`, `LOOSE`, `DEGENERATE`, `DOUBLES`, `FLIPPED`, `INTERIOR_FACES` or `BOUNDARY` |
| `render` | bool | False | Return an image instead of the indices |
| `angle` | str | "iso_front_right" | `front`, `back`, `left`, `right`, `top`, `bottom`, `iso_front_right`, `iso_front_left` |
| `max_indices` | int | 500 | Cap on the indices returned (`truncated` says when it bit); must be at least 1 |

**Reply (render=False):** `{mesh, issue, element: VERT|EDGE|FACE, count, indices, truncated}`. `NON_MANIFOLD` here keeps Blender's full definition (boundary edges included; `BOUNDARY` is its own issue). All kinds except `INTERIOR_FACES` are pure reads.

---

### `compare_meshes(mesh_a, mesh_b, tolerance, compare)`
Numeric diff for round-trip safety: counts, positions beyond `tolerance`, vertex ORDER, custom normals (max angle), UV layers (max delta), tangents, material slots, vertex groups. `same` is Blender's own `unit_test_compare == "Same"`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh_a` / `mesh_b` | str | — | Mesh object names |
| `tolerance` | float | 1e-5 | Position tolerance |
| `compare` | str | "ALL" | `ALL` or a comma list of `VERTS`, `ORDER`, `NORMALS`, `UVS`, `TANGENTS`, `MATERIALS`, `GROUPS` |

**Reply:** `mesh_a`, `mesh_b`, `same`, `unit_test_compare`, `vertices: {a, b, delta}`, `triangles: {a, b, delta}` are always present; the other sections appear only when requested (`positions: {differing, max_delta}` or `null` when counts differ, `order_match`, `custom_normals: {a, b, max_angle_deg}`, `uvs: {layers_a, layers_b, max_delta}`, `tangents: {a, b}`, `materials: {a, b, same}`, `vertex_groups: {a, b, same}`); `null` means requested but not computable. `order_match` holds when every B[i] is as close to A[i] as B's nearest vertex (exporters split vertices on UV seams and sharp edges, so assert it on unsplit meshes).

---

### `apply_transforms(objects, location, rotation, scale, keep_children_world)`
`object.transform_apply` on several objects. Shared mesh data gets its own copy first (`isolated`), a negative scale is applied anyway and reported, and on Blender 5.x its normals are flipped back (`flip_corrected`; empty on 4.3.2).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names |
| `location` | bool | False | Apply location |
| `rotation` | bool | True | Apply rotation |
| `scale` | bool | True | Apply scale |
| `keep_children_world` | bool | True | Children keep their world transform (their `matrix_world` is restored after the apply); False leaves them where the operator puts them |

**Reply:** `{objects, applied: {location, rotation, scale}, isolated, negative_scale, flip_corrected}`.

---

### `fix_normals(objects, mode, also_sharp_by_angle)`
Recalculate face normals `OUTSIDE` / `INSIDE`, `FLIP` them, or `CLEAR_CUSTOM` split normals so a re-export carries none.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names (the whole list is validated before anything is touched) |
| `mode` | str | "OUTSIDE" | `OUTSIDE`, `INSIDE`, `FLIP` or `CLEAR_CUSTOM` |
| `also_sharp_by_angle` | float | None | Also mark edges sharper than this angle (degrees) |

**Reply:** `{objects, mode, faces_flipped: {name: n}, custom_normals_cleared, sharp_edges_marked: {name: n}}` (the last key only with an angle).

---

### `cleanup_mesh(objects, remove_doubles, delete_loose, dissolve_degenerate, fill_holes, limited_dissolve)`
One BMesh pass per mesh: degenerate faces are dissolved BEFORE doubles are merged (a zero-area face's coincident vertices are not doubles), then loose elements, holes and a limited dissolve.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names (the whole list is validated first) |
| `remove_doubles` | float | 0.0001 | Merge distance; `0` skips |
| `delete_loose` | bool | True | Remove loose vertices and edges |
| `dissolve_degenerate` | bool | True | Remove zero-area faces and zero-length edges |
| `fill_holes` | int | 0 | Fill holes with up to this many sides; `0` skips |
| `limited_dissolve` | float | None | Dissolve flat regions below this angle (degrees) |

**Reply:** `{objects, per_object: {name: {doubles_removed, loose_verts_removed, loose_edges_removed, degenerate_dissolved, faces_dissolved, holes_filled, faces_before, faces_after}}}`.

---

## 37. Optimisation & LODs

### `decimate_mesh(mesh, ratio, target_triangles, mode, angle_limit, iterations, symmetry_axis, vertex_group, vertex_group_factor, keep_uv_seams, triangulate, apply, new_object)`
A Decimate modifier configured and applied (or left live). `target_triangles` reads the modifier's `face_count` back after each pass (at most 6, within 5 %) without applying per pass. `new_object` works on a copy and leaves the source untouched; on an error the copy is removed.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `ratio` | float | None | Fraction of faces to keep (0..1); or |
| `target_triangles` | int | None | Triangle target (exactly one of the two, refused before Blender is contacted otherwise) |
| `mode` | str | "COLLAPSE" | `COLLAPSE`, `PLANAR` / `DISSOLVE`, `UNSUBDIVIDE` / `UNSUBDIV` (the reply names the enum id) |
| `angle_limit` | float | 5.0 | PLANAR angle (degrees) |
| `iterations` | int | 2 | UNSUBDIVIDE passes |
| `symmetry_axis` | str | None | `X`, `Y` or `Z` |
| `vertex_group` / `vertex_group_factor` | str / float | None / 1.0 | Weight the reduction by a group |
| `keep_uv_seams` | bool | True | Delimit by UV and seam edges |
| `triangulate` | bool | True | Collapse to triangles |
| `apply` | bool | True | Apply the modifier; False leaves it live (`modifier` names it) |
| `new_object` | str | None | Name for a decimated copy |

**Reply:** `{mesh, result_object, mode, ratio_used, triangles_before, triangles_after, passes, applied, modifier}`.

---

### `generate_lods(mesh, ratios, triangle_targets, naming, collection, parent_to_lod0, mode, symmetry_axis, keep_uv_seams, preserve_silhouette_group, transfer_normals)`
Build an LOD chain: the source becomes `<name>_LOD0` (an existing `_LOD<n>` suffix is stripped first) and each level is a decimated mesh copy sharing the materials. Every target name is checked before anything changes; a collision returns `{error, existing}` with the scene untouched.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Source mesh object name |
| `ratios` | str | None | Comma list of face ratios per level; or |
| `triangle_targets` | str | None | Comma list of triangle counts per level (both given is refused before Blender is contacted; neither = the add-on uses `0.5,0.25,0.1`) |
| `naming` | str | "{name}_LOD{level}" | Pattern with `{name}` and `{level}` |
| `collection` | str | None | Put the levels in this collection |
| `parent_to_lod0` | bool | False | Parent the levels to LOD0 |
| `mode` / `symmetry_axis` / `keep_uv_seams` | | | As `decimate_mesh` |
| `preserve_silhouette_group` | str | None | Vertex group that weights the reduction |
| `transfer_normals` | bool | False | Data Transfer custom normals from LOD0 to each level |

**Reply:** `{source, lod0, levels: [{level, name, triangles, ratio}] (LOD0 included at ratio 1.0), collection, custom_normals_transferred}`.

---

### `set_normals(objects, mode, angle, keep_sharp, source_object, apply)`
Shading normals for export. `AUTO_SMOOTH` uses `shade_smooth_by_angle` on both Blender versions (marks sharp edges, no modifier); `AUTO_SMOOTH_MODIFIER` is the 5.x Smooth by Angle modifier (a teaching error on 4.3.2); `WEIGHTED` applies a Weighted Normal modifier; `FROM_OBJECT` transfers custom normals from a high-poly source; `CLEAR_CUSTOM` drops them.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names |
| `mode` | str | "AUTO_SMOOTH" | `AUTO_SMOOTH`, `AUTO_SMOOTH_MODIFIER`, `WEIGHTED`, `SMOOTH`, `FLAT`, `FROM_OBJECT`, `CLEAR_CUSTOM` |
| `angle` | float | 30.0 | Smoothing angle (degrees) |
| `keep_sharp` | bool | True | Keep marked sharp edges |
| `source_object` | str | None | Required for `FROM_OBJECT` (refused before Blender is contacted when missing) |
| `apply` | bool | True | Apply the modifier used |

**Reply:** `{objects, mode, method, modifier_used, has_custom_normals: {name: bool}, sharp_edges: {name: n}}`.

---

### `triangulate_for_export(objects, quad_method, ngon_method, keep_custom_normals, min_vertices, as_modifier)`
A Triangulate modifier (it keeps custom normals, unlike the BMesh operator), applied, or left live with `as_modifier=True` so the source stays quads.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names |
| `quad_method` | str | "SHORTEST_DIAGONAL" | `BEAUTY`, `FIXED`, `FIXED_ALTERNATE`, `SHORTEST_DIAGONAL`, `LONGEST_DIAGONAL` |
| `ngon_method` | str | "BEAUTY" | `BEAUTY` or `CLIP` |
| `keep_custom_normals` | bool | True | Preserve custom split normals |
| `min_vertices` | int | 4 | Only faces with at least this many vertices |
| `as_modifier` | bool | False | Leave the modifier live |

**Reply:** `{objects, as_modifier, per_object: {name: {triangles_before, triangles_after, modifier}}}`.

---

### `get_triangle_budget(objects, budget, group_by)`
Triangle counts with modifiers evaluated (depsgraph), sorted descending, with each item's share of `budget`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | None | Comma list; None = every mesh in the scene |
| `budget` | int | None | Triangle budget (sets `over_budget`) |
| `group_by` | str | "OBJECT" | `OBJECT`, `COLLECTION` or `MATERIAL` |

**Reply:** `{group_by, budget, total, items: [{name, triangles, share}], over_budget}`.

---

## 38. Collision

### `create_collision(mesh, shape, profile, count, axis, decimate_ratio, padding, display_wire, parent, collection)`
A collision proxy built from the evaluated mesh and named by the engine's convention: UNREAL `UCX_<mesh>_NN`, GODOT `<mesh>-convcol` (convex) or `<mesh>-col`, UNITY `<mesh>_Collider`, GENERIC / BEVY / TIMBERMESH `<mesh>_COL` (NN = next free two-digit index). The proxy is wire-displayed, hidden from render, without material, parented under the mesh. `CONVEX` and `SLICED_CONVEX` hulls are closed manifold meshes (interior and unused geometry removed); on the 24x12 torus fixture the hull volume ratio is about 1.9.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Source mesh object name |
| `shape` | str | "CONVEX" | `CONVEX`, `BOX`, `SPHERE`, `CAPSULE`, `CYLINDER`, `MESH_DECIMATED`, `SLICED_CONVEX` |
| `profile` | str | "UNREAL" | `UNREAL`, `UNITY`, `GODOT`, `BEVY`, `TIMBERMESH` or `GENERIC` |
| `count` | int | 1 | `SLICED_CONVEX` slabs along `axis` (1..32) |
| `axis` | str | "Z" | Slicing axis `X`, `Y` or `Z` |
| `decimate_ratio` | float | 0.1 | `MESH_DECIMATED` ratio |
| `padding` | float | 0.0 | Grow fitted shapes by this distance |
| `display_wire` | bool | True | Wire display |
| `parent` | bool | True | Parent under the mesh |
| `collection` | str | None | Put the proxy in this collection |

**Reply:** `{mesh, shape, profile, proxies: [{name, triangles, volume_ratio}], parent, collection}`. Errors: `{error, valid}` for shape / profile; `{error}` for a count out of range.

---

### `get_collision_info(mesh)`
Lists a mesh's collision proxies by every profile's naming rule, with triangle counts and whether each is a child of the mesh.

| Parameter | Type | Description |
|-----------|------|-------------|
| `mesh` | str | Mesh object name |

**Reply:** `{mesh, proxies: [{name, profile, triangles, is_child}]}`.

---

### `check_collision_fit(mesh, samples)`
How well the proxies cover the mesh: area-weighted surface samples (deterministic seed), the fraction inside any proxy and the largest distance outside.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mesh` | str | — | Mesh object name |
| `samples` | int | 2000 | Surface sample points (1..20000) |

**Reply:** `{mesh, samples, proxies, covered_fraction, max_outside_distance, inside_samples}`; `{error: "no collision proxies found for <mesh>"}` when there are none.

---

## 39. Sockets, Pivots & Hierarchy

### `add_socket(parent, name, location, rotation, space, profile, bone, display_size, snap_to, vertex_index)`
An attach-point Empty under an object (or one of its bones), named for the engine: UNREAL `SOCKET_<name>`, TIMBERMESH `#<name>` (the parent chain must contain a `#` root, else an error), UNITY / GENERIC `<name>`. A duplicate name gets Blender's `.001` suffix.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `parent` | str | — | Parent object name |
| `name` | str | — | Socket name (the profile prefix is added) |
| `location` / `rotation` | str | "0,0,0" | `"x,y,z"` (rotation in degrees) in `space` |
| `space` | str | "LOCAL" | `LOCAL` (relative to the parent) or `WORLD` |
| `profile` | str | "UNREAL" | `UNREAL`, `TIMBERMESH`, `UNITY` or `GENERIC` |
| `bone` | str | None | Parent to this bone (armature parents only) |
| `display_size` | float | 0.1 | Empty display size |
| `snap_to` | str | "NONE" | `NONE`, `SURFACE` (closest point on the parent from the given location) or `VERTEX` (`vertex_index`); either overrides `space` |
| `vertex_index` | int | None | Required with `VERTEX` |

**Reply:** `{name, parent, bone, profile, location_world, rotation_world, matrix_world}`.

---

### `list_sockets(parent)`
Read-only: Empties named `SOCKET_*` or `#*` (so a `#` root lists too), or with `parent` every Empty child of that object, with local and world transforms and the bone each follows.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `parent` | str | None | Restrict to this object's Empty children |

**Reply:** `{parent, count, sockets: [{name, profile, parent, bone, location_local, location_world, rotation_local}]}`.

---

### `set_pivot(objects, mode, location, keep_world)`
Batch `set_origin` in one round trip. With `keep_world=True` the meshes stay where they are in the world; with `keep_world=False` the geometry moves and the origin stays.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names (bbox modes need meshes) |
| `mode` | str | "BOTTOM_CENTER" | `BOTTOM_CENTER`, `CENTER`, `TOP_CENTER`, `WORLD_ORIGIN`, `CURSOR` or `CUSTOM` |
| `location` | str | None | `"x,y,z"` world point for `CUSTOM` (refused before Blender is contacted when missing) |
| `keep_world` | bool | True | Keep the mesh in place |

**Reply:** `{objects, mode, keep_world, per_object: {name: {new_location}}}`.

---

### `snap_to_grid(objects, grid, axes, what)`
Modular-kit placement: the origin, the world bounding-box minimum corner or its centre lands on a grid point; the move goes through `matrix_world.translation`, so parented objects are safe.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `objects` | str | — | Comma list of object names |
| `grid` | float | 1.0 | Grid step (> 0) |
| `axes` | str | "XYZ" | Any subset of `X`, `Y`, `Z` |
| `what` | str | "ORIGIN" | `ORIGIN`, `BOUNDS_MIN` or `BOUNDS_CENTER` |

**Reply:** `{objects, grid, axes, what, per_object: {name: {before, after}}}`.

---

### `build_export_hierarchy(root_name, meshes, collision, sockets, lods, profile)`
Parents meshes, LODs, collision proxies and sockets under a root Empty (reused when it exists) in the layout the engine's importer expects: UNREAL LODs under the root with a `lod_group` custom property and `UCX_` children under their mesh; GODOT `-col` siblings; UNITY / BEVY / GENERIC LOD siblings. Collision and socket targets are matched by name prefix, else the first mesh.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `root_name` | str | — | Root Empty name |
| `meshes` | str | — | Comma list of mesh names |
| `collision` / `sockets` / `lods` | str | None | Comma lists |
| `profile` | str | "UNREAL" | `UNREAL`, `UNITY`, `GODOT`, `BEVY`, `TIMBERMESH` or `GENERIC` |

**Reply:** `{root, profile, created_root, tree: {name, type, children: [...]}, custom_props_set: {object: {key: value}}}`.

---

## 40. Engine Export

Engines: `UNITY`, `UNREAL`, `GODOT`, `BEVY`, `TIMBERMESH`, `GENERIC_GLTF`, `GENERIC_FBX` (an unknown engine is refused before Blender is contacted). The preset table lives in the add-on (`_ENGINE_PRESETS`); `get_export_preset` prints it and `export_for_engine` applies the same entry. The add-on writes `<file>.manifest.json` beside every export; `batch_export` is a server loop that writes `batch_manifest.json`.

**Preset table (2.4.0):**
- `UNITY`: FBX, `axis_forward='-Z'`, `axis_up='Y'`, `apply_scale_options='FBX_SCALE_ALL'`, `global_scale=1.0`, `use_tspace=True`, `mesh_smooth_type='FACE'`, `add_leaf_bones=False`, `bake_space_transform=False`, `use_custom_props=True`; units 1.0 METERS.
- `UNREAL`: FBX, `axis_forward='-Y'`, `axis_up='Z'`, `global_scale=1.0`, `apply_scale_options='FBX_SCALE_NONE'`, `use_tspace=True`, `mesh_smooth_type='FACE'`, `add_leaf_bones=False`, `use_custom_props=True`; units 0.01 CENTIMETERS, with a warning naming `set_scene_units(UNREAL)` when the scene's `scale_length` is not 0.01.
- `GODOT`, `BEVY`: GLB, `export_yup=True`, `export_apply=True`, `export_tangents=True`, `export_extras=True`, `export_attributes=True`, `export_vertex_color='MATERIAL'`, `export_image_format='AUTO'`; units 1.0 METERS.
- `TIMBERMESH`: the Timbermesh exporter class called directly on a collection, `merge_meshes=True`, `single_animation=True`, `use_vertex_animations=False`; needs the `timbermesh_blender_plugin` add-on (enabled for the export and restored after); the set must contain a `#` root.
- `GENERIC_FBX`: FBX with Blender's defaults. `GENERIC_GLTF`: GLB with Blender's defaults.
- Naming per profile: collision `UCX_<mesh>_NN` / `<mesh>-convcol` / `<mesh>-col` / `<mesh>_Collider` / `<mesh>_COL`; sockets `SOCKET_<name>` / `#<name>` / `<name>`; LODs `{name}_LOD{level}`.

### `get_export_preset(engine)`
The exact exporter settings `export_for_engine` applies for an engine. One round trip, no change to the scene.

| Parameter | Type | Description |
|-----------|------|-------------|
| `engine` | str | One of the seven engines |

**Reply:** `{engine, format, exporter, kwargs, axis: {forward, up}, units: {scale_length, length_unit}, naming: {collision, socket, lod}, notes}`.

---

### `export_for_engine(engine, objects, collection, filepath, include_hierarchy, include_collision, include_sockets, include_lods, include_animation, apply_modifiers, triangulate, validate, textures, overrides)`
Export roots with their hierarchy, LODs, collision proxies and sockets using the engine preset, after `validate_asset` on the renderable meshes: a FAIL verdict refuses the export (no file, no manifest) unless `overrides` carries `"force": true`. `triangulate=True` adds a temporary Triangulate modifier (custom normals kept) that the exporter evaluates and removes afterwards, so the file is triangulated while the scene mesh keeps its polygons; it requires `apply_modifiers=True` (refused before Blender is contacted otherwise). Exporter kwargs the live Blender lacks are dropped with a warning; enum values are checked against the live enum.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | — | One of the seven engines |
| `objects` | str | None | Comma list of root objects (exact first, then globs); or |
| `collection` | str | None | Collection to export (exactly one of the two) |
| `filepath` | str | None | Output file; default `<output_dir>/<first root>.<ext>` (stem sanitised) when the `output_dir` server setting is set, else a temp path chosen by the add-on |
| `include_hierarchy` | bool | True | Children of the roots |
| `include_collision` | bool | True | Collision-typed meshes (wire + hidden from render, or a profile collision name) |
| `include_sockets` | bool | True | Socket Empties |
| `include_lods` | bool | True | `_LOD<n>` siblings (n >= 1) |
| `include_animation` | bool | False | Animation export |
| `apply_modifiers` | bool | True | Export the evaluated mesh |
| `triangulate` | bool | True | Temporary Triangulate modifier for the file only |
| `validate` | bool | True | Run `validate_asset` first |
| `textures` | str | "COPY" | `COPY` (files copied beside the export; basename collisions become `<parentfolder>_<basename>`), `EMBED`, `NONE` or `KEEP_PATHS` |
| `overrides` | str | None | JSON object of exporter kwargs merged over the preset (`"force": true` exports despite FAIL); a scalar or list is refused before Blender is contacted |

**Reply text:** `Exported <engine> -> <filepath> (<size> bytes, N objects, T tris); manifest: <path>; validation: <verdict>; warnings: ...`. The add-on's reply (and the manifest, plus `manifest_version`, `written_at`, `blender`, `preset`) carries `filepath, engine, format, exporter, size_bytes, manifest_path, objects, triangles, materials, textures_copied, validation: {verdict, fail, warn}, warnings, exporter_kwargs, timbermesh_root`. Known Blender fact: the FBX exporter with `use_triangles` drops one face of Suzanne-like meshes in any round trip (968 -> 967), so a manifest triangle count can read one higher than an engine import; not add-on behaviour.

---

### `batch_export(engine, objects, collection, output_dir, naming, one_file_per, skip_hidden, continue_on_error)`
A server loop: one `get_scene_info(limit=0)` call resolves the roots (every parent-less mesh, or the `objects` list with exact names first and globs expanded against the scene; hidden roots dropped when `skip_hidden`; the `collection` filter applies in both forms), then one `export_for_engine` call per root; a failed root is a `FAIL` row and the loop continues unless `continue_on_error=False`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | — | One of the seven engines |
| `objects` | str | None | Comma list of roots; default every parent-less mesh in the scene |
| `collection` | str | None | Only roots in this collection (needs a 2.4.0 add-on, whose scene entries carry `collections`) |
| `output_dir` | str | None | Folder for the files; default the `output_dir` server setting, else `<temp>/blender_batch_export` |
| `naming` | str | "{name}" | File stem pattern with `{name}` (stems sanitised) |
| `one_file_per` | str | "OBJECT" | `OBJECT` = one file per object WITHOUT its children; `ROOT` = one file per root WITH its hierarchy; `COLLECTION` = all roots in one file named after the collection (requires `collection`) |
| `skip_hidden` | bool | True | Skip roots hidden in the viewport |
| `continue_on_error` | bool | True | Keep going after a failed root |

**Reply text:** one line per root, `PASS <name> -> <file> (<tris> tris)` or `FAIL <name>: <error>`, then `manifest: <output_dir>/batch_manifest.json` (`{engine, output_dir, written_at, rows: [{object, status, filepath, triangles, error}]}`).

---

### Changes to existing tools in 2.4.0 (additive)
- `get_scene_info(limit=None)`: `0` = every object; entries gain `triangles`, `hidden`, `parent` and `collections`; the reply gains `total_triangles` / `total_vertices` (section 1).
- `set_origin`: `BOTTOM_CENTER`, `TOP_CENTER`, `CUSTOM` + `location`; reply `offset_applied`, `children_kept`, `isolated`; shape keys move with the mesh; a shared mesh gets its own copy first (section 11).
- `apply_modifier(apply_all, keep)`: `modifier_name` optional with `apply_all` (the only 2.3.0 required parameter relaxed); reply `applied`, `kept`, `failed`, `remaining` (section 16).
- `get_mesh_stats`: evaluated `triangles` (+ `triangles_simple`), topology counts, `has_custom_normals`, `uv_layers`, `color_attributes`, `material_slots`, `dimensions`, `unapplied_transform`, `origin_offset`, `boundary_edges` (section 10).
- `export_object`: `objects` + 15 engine parameters; reply `size_bytes`, `object_count`, `vertices_written`, `triangles_written`, `warnings`, `transform_baked`, `modifiers_applied` (section 19).
- `import_file`: 8 importer parameters; reply `objects` per imported object and `ignored` for kwargs the live importer lacks (section 19).
- `execute_blender_code`: the error block also prints the stdout captured before the exception (section 26).
- `driver_batch_max` can only LOWER the add-on's hard limit of 200 (section 27 / 35); a `driver_reply_max_chars` setting of 0 or less falls back to 24000.
- Driver fix (2.3.0 code, found by the 2.4.0 interop step): `transaction` ROLLBACK and `batch(undo=AUTO)` push a closing undo step before unwinding, so Python-only mutations made after BEGIN are reverted too; a ROLLBACK whose marker is already gone answers `{error: "transaction marker not found; nothing reverted"}` (section 35).
- Every 2.4.0 tool is batchable and transactional through the generic driver (`batch` with `"$id.path"` references across `validate_asset` -> `decimate_mesh` -> `create_collision`, `transaction` ROLLBACK over `set_pivot` + `add_socket`, `rna_get` on a `UCX_` proxy by `#uid`); `batch_export` is a server loop, so it is a tool call, never a batch item.
- The legacy Scene properties for API keys (`blendermcp_hyper3d_api_key`, `blendermcp_sketchfab_api_key`, `blendermcp_hunyuan3d_secret_id`, `blendermcp_hunyuan3d_secret_key`) are no longer registered; a file saved by 1.6.0-2.3.0 still migrates its values into the add-on preferences on load and the keys are deleted from every ID-property group holding them.
- Object names containing `,` cannot be addressed through a comma-list parameter: use `collection=` or the driver's `#uid` (R-B2-41).

---

## Running the Server

```bash
cd "D:\App Dev\blender_mcp"
.venv\Scripts\blender-mcp.exe
# or
.venv\Scripts\python.exe -m blender_mcp.server
```

The addon TCP server must be running in Blender on port 9876. Install `addon.py` via Blender Preferences → Add-ons → Install.
