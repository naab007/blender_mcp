# TEST-PLAN-B2: engine readiness Tier 1 (2.4.0), QA acceptance

Owner: QA (Margaret), team lohjud. Written 2026-10-21 from `FEATURE-REQUEST-engine-readiness.md`
sections 1, 2.1, 4, 4.1, 5, `PLAN.md` section 11 (B2.1, E1-E14) and `LEAD-RULINGS-B2.md` R-B2-1..7,
BEFORE PLAN.md section 13 (B2.2) landed. Where B2.2 changes a name, key or number, B2.2 wins and this
file is amended in the same minute (amendment log at the end).

Harness vocabulary = `tests/headless_handlers.py`: `call(cmd, **params)` goes through
`srv.execute_command({"type", "params"})` after one `view_layer.update()`; `ok(reply, must=[keys])`
asserts `status == "success"` and no `result.error`; `err(reply, "substr")` asserts an error reply whose
text contains the substring (case-insensitive) and is never a traceback; every `step()` additionally
asserts `bpy.context.mode == "OBJECT"` afterwards and that the scene list did not change
(`readonly=True`: the whole `snapshot()` is unchanged, i.e. active, selection, engine, frame, camera).
New steps live in `tests/b2_steps.py`, loaded by `-- --b2` the way `drv_steps.py` is loaded by `--drv`.
Fixtures are built with `bpy.data` / `bpy.ops` primitives, never with the handler under test. Blender
truth is read with bpy after every mutating call (reply says X, bpy must agree).

No test touches a real port: harness steps call `execute_command` in-process; server tests use the
`FakeConn` of `tests/test_b0_server.py` (R14 pattern, no conftest). Any new test that opens a socket
is a finding against the test, not the code.

## 0. Baseline (to be taken from disk by QA before L1 lands; numbers below are the 2.3.0 sign-off)

| Gate | Command | Baseline |
|---|---|---|
| Harness 4.3.2 | `D:\blender-4.3.2-windows-x64\blender.exe --background --factory-startup --python tests\headless_handlers.py -- --b0 --b1 --drv --out tests\report_b2_0_4.3.2.json` | steps 204, passed 204, failed 0, handlers 171, EXIT 0 |
| Harness 5.2.1 | same with `D:\blender-5.2.1-windows-x64`, `report_b2_0_5.2.1.json` | 204 / 204 / 0, 171 handlers, EXIT 0 |
| Venv | `.venv\Scripts\python.exe -m pytest tests -q` (no env vars) | 94 passed |
| Settings replies | `<install>\blender.exe --background --factory-startup --python tests\settings_baseline.py -- --check` | `match: true`, `differing: []`, both installs |
| tools/list | `.venv\Scripts\python.exe tests\dump_tools_list.py names` | 182 names = `tests/baseline_tools_2.3.txt` (the 2.3 pin; never overwritten, 2.4 gets `baseline_tools_2.4.txt` at B2-G3) |
| b2check facts | `docs/apichecks/out_4.3.2.jsonl` L38, `out_5.2.1.jsonl` L30 (`B2CHECK`) | present; the expected numbers below cite `b2check.<section>.<key>` |

Every "after" number is re-taken on both installs from disk at each landing (L1, L2, L3) and at
B2-G3; a count taken while a slice is landing is not evidence.

## 1. Headless steps (both installs, `-- --b0 --b1 --drv --b2`, file `tests/b2_steps.py`)

A step passes only when every condition holds on BOTH installs. Numbers are doc section 5 steps
where one exists ("doc N"). Reply key names in angle brackets `<...>` are NOT yet fixed by the
contract and must be named in PLAN.md section 13 before the step is written (see section 6, Q1).
Fixture objects: `Prop` (cube, scaled 2,1,1, rotated 45 deg Z, not applied), `Suz` (monkey,
`calc_uvs=True`), `Tor` (torus, `b2check.collision` source), `Cyl` (cylinder with NGON caps),
`Open` (cube with one face deleted), `Dirty` (cube + 2 coincident verts + 1 loose vert + 1 zero-area
face), `Sub` (cube + Subdivision levels 2, unapplied), `Big` (grid 300x300, 90601 verts).

### F. Section 1 fixes (L1)

F1 `export_object`
1. `ok(call("export_object", objects="Suz,Prop", file_format="fbx", filepath=<WORK>/f1.fbx))`: file exists,
   `<bytes>` == `os.path.getsize`, `<object_count> == 2`, `<vertices>` and `<triangles>` are ints equal to
   the bpy sums (`Suz` evaluated tris + `Prop` 12), `warnings` is a list containing an entry naming
   `Prop` and the unapplied scale.
2. `include_hierarchy=True` with a child empty under `Suz`: `<object_count>` counts the child; `False`
   does not. `apply_modifiers=False` on `Sub`: `<triangles> == 12`; `True`: `192` (`b2check.misc.eval_tris_with_subsurf`).
3. `use_triangles=False` on `Cyl`: `warnings` names ngons; `use_triangles=True`: no ngon warning.
   A mesh without UV layers: `warnings` names missing UVs. A material with `use_nodes=False`: `warnings` names it.
4. `err(call("export_object", name="Suz", file_format="fbx", axis_up="Q"), "axis")`: lists the valid
   values; NO file written. `err(..., mesh_smooth_type="SMOOTH_GROUP")` on 4.3.2 lists the live enum
   (`b2check.fbx_roundtrip.export_smooth_group`); on 5.2.1 it succeeds (E8) - the step asserts
   `VER >= (5,0)` branch accordingly, never a `bpy.app.version` compare inside addon.py (M7 static step).
5. OBJ on 4.3.2: reply `transform_baked is True` (E10) and the first `v` line carries world coords
   (`b2check.obj_roundtrip.first_v_line` idiom); on 5.2.1 `transform_baked is False` by default.
6. Regression: `export_object(name="Cube", file_format="glb")` (the B1 shape) still works byte-for-key:
   every key the 2.3 reply had is still present (compare against a key list captured at baseline).

F2 `import_file`
1. Export `Suz` to glb, `clear_scene()`, `ok(call("import_file", filepath=..., import_shading="FLAT"))`:
   reply `objects` list with one entry carrying `vertices`, `triangles`, `has_custom_normals` (bool),
   `uv_layers` (list of str, len 1), `materials` (list), and top-level `armatures == []`; bpy: every
   polygon `use_smooth is False`.
2. glTF with an armature: `disable_bone_shape` default -> no object named `Icosphere*` after import
   (E9, K8 at the source); `armatures` lists the armature name.
3. FBX import `global_scale=2.0` of a unit cube: dimensions `[4,4,4]`; `axis_forward="Y", axis_up="Z"`
   accepted; `use_custom_normals=True` default (reply echoes the kwargs used).
4. `err(call("import_file", filepath=<glb>, import_shading="BOGUS"))` lists `NORMALS/FLAT/SMOOTH`;
   nothing imported (object count unchanged). A FBX-only kwarg on a glb (`ignore_leaf_bones=True`) is
   ignored or refused with a teach message, never a TypeError traceback.

F3 `get_mesh_stats` (readonly)
1. Cube: `triangles 12, quads 6, tris 0, ngons 0, loose_verts 0, loose_edges 0, non_manifold_edges 0,
   has_custom_normals False, uv_layers ["UVMap"], color_attributes [], material_slots 0,
   dimensions [2,2,2]`, `unapplied_transform False`, `origin_offset` carries `to_center ~ [0,0,0]`
   and `to_bottom ~ [0,0,-1]` (sign per B2.2).
2. `Prop`: `unapplied_transform True` (rotation and scale reported separately if B2.2 says so).
3. `Sub`: `triangles 192` (evaluated, E13); the unevaluated `polygons == 6` is NOT what is reported.
4. `Cyl`: `ngons 2`; `Open`: `non_manifold_edges 4`; `Dirty`: `loose_verts 1`.
5. `err(call("get_mesh_stats", name="Lamp"), "not a mesh")`; missing -> `not found`.

F4 `get_scene_info` (readonly)
1. `total_triangles == sum(obj.triangles for obj in objects)` and equals the bpy evaluated sum;
   `total_vertices` likewise; `units == {system, scale_length, length_unit}` with the live values;
   non-mesh objects carry `triangles 0` or no key (B2.2 decides; never a crash on a light).
2. Empty scene: totals 0, lists empty.

F5 `apply_modifier(apply_all, keep)`
1. Object with Subdivision + Bevel + Armature: `apply_all=True, keep="Armature"`: Armature remains, the
   other two gone, reply `applied` lists the two names, `kept` lists `Armature`, bpy polygon count grew.
2. `apply_all=True` on an object without modifiers: success, `applied == []`, not an error.
3. `keep="NoSuch"`: reported as unknown in the reply, the rest still applied.
4. Old shape `apply_modifier(name, modifier_name)` unchanged (regression).

F6 `set_origin` BOTTOM_CENTER / TOP_CENTER / custom
1. Cube at origin: `BOTTOM_CENTER` -> `min(v.co.z) == 0` (tol 1e-6), `obj.location.z == -1`
   (`b2check.misc.origin_bottom_center`), world-space vertex positions unchanged (compare 8 world coords before/after).
2. `TOP_CENTER` -> `max(v.co.z) == 0`, `location.z == 1`. `location="1,2,3"` -> origin at world (1,2,3),
   geometry unchanged in world space. Rotated + scaled `Prop`: world coords still unchanged (the
   offset must go through the object matrix).
3. `err(call("set_origin", name="Cube", type="custom"))` without `location` teaches; `location="a,b"` teaches.

### V. Validation (L2)

V1 `validate_asset` doc 1
1. `ok(call("validate_asset", objects="Prop"))`: `<objects>.Prop.unapplied_transform.status == "FAIL"`,
   `.origin.status == "WARN"`, `<verdict> == "FAIL"`, `<fixes>` non-empty and names apply transforms.
   Readonly step: the snapshot is unchanged (validate without fix mutates nothing, selection included).
2. `ok(call("validate_asset", objects="Prop", fix=True))`: `<fixes_applied>` lists the applied fixes;
   bpy `Prop.scale == (1,1,1)`, `rotation_euler == (0,0,0)`, origin at bbox bottom centre, world
   vertex coords unchanged (8 coords before/after, tol 1e-5). Third call: `<verdict> == "PASS"` and no FAIL check.
3. Negative scale `(-1,1,1)`: `negative_scale FAIL` BEFORE apply (E6); `fix=True` reply carries
   `flip_corrected` (True on 5.2.1, False/None on 4.3.2, never a TypeError); after fix all face
   normals point outward (dot(normal, centroid - centre) > 0 for every face).
4. Dimension plausibility: cube scaled 60 (120 m) -> `dimensions WARN`; scaled 0.004 (8 mm) -> WARN; 2 m -> PASS.

V2 topology checks, one fixture each, `count` and `sample_indices` asserted against bpy
1. `Cyl`: `ngons` status FAIL under `profile` UNITY / UNREAL / GODOT / BEVY (E5: tangents exported), status
   in (WARN, FAIL) under GENERIC; `count == 2`, `sample_indices` are the two cap face indices.
2. `Open`: `non_manifold FAIL count 4` (edge indices of the hole); `Dirty`: `loose_verts count 1`,
   `doubles count 1` (within 1e-5), `degenerate count >= 1`; cube with one face flipped: `flipped count 1`
   and `inconsistent_normals FAIL`.
3. `fix=True` on `Dirty`: doubles merged (vert count -1), loose vert gone, degenerate dissolved, normals
   recalculated outside, `Cyl` ngons triangulated when the profile exports tangents; re-run clean. The
   fix NEVER triangulates under GENERIC unless B2.2 says so (safe-fix list: apply transforms, recalc
   normals, remove doubles, delete loose, triangulate ngons, rename illegal chars; nothing else).
4. `fix=True` is applied with `bmesh` and the modifier path only (E2): the step greps nothing, but a
   `mesh.intersect_boolean` or edit-mode operator call would break the mode invariant in `step()`.

V3 UV checks
1. Cube with its UV layer removed: `require_uv=True` (default) -> `missing_uv FAIL`; `require_uv=False` -> no FAIL.
2. `require_uv2=True` on a one-layer mesh -> `missing_uv2 FAIL`; add a second layer -> PASS.
3. UVs shifted by +2.0 on one face: `uv_out_of_bounds` WARN or FAIL (B2.2 fixes which) with `count 4` loops or 1 face.

V4 budgets and sizes
1. `max_triangles=10` on the cube: `triangle_budget FAIL`, `count == 12`; `max_triangles=12`: PASS.
2. `Big` (90601 verts): `vertex_count_16bit WARN` with the count; cube: PASS. B2.2 says whether the
   count is before or after triangulation (Q4).
3. `Sub`: budget counts the evaluated 192, not 12 (E13).

V5 materials and images
1. Cube with an empty material slot: `material_slots` WARN/FAIL naming the slot index; a slot whose
   material is assigned to no face: `unused_material` WARN; `mat.use_nodes = False`: `material_nodes` FAIL/WARN;
   an Image whose `filepath` points to a missing file: `images_missing` FAIL with the image name.
2. Cube with no slots at all: `material_slots` is WARN (empty), not a crash.

V6 bone influences
1. Mesh with one vertex in 5 vertex groups (weights > 0): `max_bone_influences=4` -> `bone_influences FAIL`
   `count 1`; `max_bone_influences=8` -> PASS; a vertex with 5 groups but one weight 0.0: counts 4 (zero
   weights do not count; B2.2 confirms, Q5).

V7 names and structure per profile (E12)
1. Object `My Prop.001` + material `Wood Ok`: UNREAL -> `name_chars FAIL` listing both; GENERIC -> WARN.
   Two objects `Cube` and `Cube.001`: `name_collision FAIL`. A 63-char name is PASS on both installs;
   the limit the check uses is `len(name)` after assignment, never a constant (mutant: set a constant
   64 and the 5.2.1 step with a 200-char name must fail).
2. UNREAL on a mesh without `UCX_` children: `collision_structure WARN`; with `UCX_Suz_00` child: PASS.
   GODOT without `-col` sibling: WARN. TIMBERMESH without a `#` root in the parent chain: FAIL.
3. `profile="PS5"` -> error listing the six profiles; `objects="Nope"` -> `not found`; `collection="NoColl"`
   -> error; neither `objects` nor `collection` -> error; `objects="Lamp"` -> the light is reported as
   skipped/non-mesh in the reply, no traceback.

V8 `find_mesh_issues`
1. `Open` NON_MANIFOLD -> `indices` = the 4 hole edge indices (set equality with bmesh), `element == "EDGE"`,
   `count 4`; `Cyl` NGONS -> 2 face indices; `Dirty` LOOSE -> 1 vert index, DOUBLES -> 2 (or 1 pair),
   DEGENERATE -> >= 1 face; flipped cube FLIPPED -> 1 face; `Suz` BOUNDARY -> `count > 0` (doc 2);
   INTERIOR_FACES on two cubes joined sharing a coincident face -> >= 1.
2. `max_indices=2` on `Open`: `len(indices) == 2`, `count == 4`, `truncated True`.
3. `err(call("find_mesh_issues", mesh="Open", issue="HOLES"))` lists the 8 classes; `mesh="Lamp"` -> `not a mesh`.
4. Mode and selection restored after every call INCLUDING the error path (E7 `finally`): a call with
   a bad `issue` after the handler would have entered edit mode leaves `bpy.context.mode == "OBJECT"`
   and the pre-call selection (readonly=True step).
5. `render=True` headless: graceful (image path or a `headless` note), mode OBJECT after, tag LIVE.

V9 `compare_meshes`
1. `Suz` vs a `Suz` copy: `<same> True` (first pass `unit_test_compare == "Same"`, E13: match the word only),
   `vert_delta 0`, `tri_delta 0`, `verts_differing 0`, `order_matches True`, `max_position_delta 0.0`,
   `uv_layers` equal lists, `custom_normals` both False, `material_slots` equal, `vertex_groups` equal.
2. Copy with vertex 3 moved by 1e-3: `tolerance=1e-5` -> `verts_differing 1`, `max_position_delta`
   within 1e-6 of 1e-3, `order_matches True`; `tolerance=1e-2` -> `verts_differing 0`.
3. Copy with permuted vertex order (same positions set): `order_matches False`, `verts_differing > 0`
   index by index. Copy with 2 extra verts: `vert_delta 2`, no exception, `order_matches False`.
4. `compare="VERTS,ORDER"`: only those sections present; `compare="BOGUS"` -> error listing the 7;
   `mesh_b="Lamp"` -> `not a mesh`. Numpy pass: a mesh of 10k verts completes < 2 s (M12 float64 buffers).

V10 `apply_transforms`
1. `A` and `B` share one mesh; `apply_transforms(objects="A", scale=True)`: `A.data.users == 1`,
   `A.data is not B.data`, `B` dimensions unchanged, reply `isolated` names `A` (E6).
2. `keep_children_world=True`: parent rotated 45 with a child; after apply, child world matrix unchanged
   (16 floats, tol 1e-5). `location=True` moves origin to world zero with geometry fixed in world space.
3. Negative scale object: reply `flip_corrected` (5.2.1 True, 4.3.2 False/None), no TypeError.
4. `objects="Nope"` -> `not found`; `objects="Lamp"` -> applied (lights can apply too) or reported skipped, no crash.

V11 `fix_normals`
1. Cube with all normals flipped: `OUTSIDE` -> every face normal points away from the centroid; `INSIDE`
   -> every face points inward; `FLIP` -> each face normal negated vs before; `CLEAR_CUSTOM` after
   `normals_split_custom_set_from_vertices` -> `has_custom_normals False` (`b2check.normals.clear_custom_normals`).
2. `also_sharp_by_angle=30` on the cube: 12 sharp edges marked (`b2check.normals.shade_smooth_by_angle` idiom).
3. `mode="UP"` -> error listing the 4 modes.

V12 `cleanup_mesh`
1. `Dirty` defaults: reply `doubles_removed 1`, `loose_removed 1`, `degenerate_dissolved >= 1`; bpy vert
   count agrees. Clean cube: all counts 0, 8 verts, 6 faces (no-op is a no-op).
2. `Open` `fill_holes=4`: `holes_filled 1`, 6 faces after. Subdivided plane (4x4): `limited_dissolve=5`
   -> 1 face. `remove_doubles=0.0` -> doubles not merged (threshold honoured).
3. `delete_loose=False` keeps the loose vert.

### O. Optimisation and LODs (L2)

O1 `decimate_mesh` (fixture: UV sphere 32x16, 512 faces, 960 evaluated tris; `b2check.modifiers`)
1. `ratio=0.5, mode="COLLAPSE"`: reply `triangles_before 960`, `triangles_after` within 5 % of 480,
   `mode == "COLLAPSE"`, `ratio_used 0.5`; no Decimate modifier left (`apply=True`).
2. `mode="PLANAR"` and `mode="DISSOLVE"` both accepted, reply `mode == "DISSOLVE"` (E3); `UNSUBDIVIDE` and
   `UNSUBDIV` -> `UNSUBDIV`; on a 4x4 grid `iterations=2` -> faces 24 equivalent (`b2check.modifiers.unsubdivide_apply`).
3. `target_triangles=240`: `triangles_after` within 5 % of 240, `passes <= 6`, `ratio_used` in (0,1).
4. `apply=False`: Decimate modifier present with `delimit == {'UV','SEAM'}` (`keep_uv_seams`),
   `use_collapse_triangulate True`, `use_symmetry True, symmetry_axis "X"` when `symmetry_axis="X"`; the
   reply's `triangles_after` equals the modifier's `face_count` readback (E3).
5. `new_object="Lo"`: source tri count unchanged, `Lo` exists with fewer tris, same materials.
6. Errors: both `ratio` and `target_triangles` -> error; neither -> error; `ratio=0` / `1.5` -> error;
   `vertex_group="NoSuch"` -> error listing groups; `mesh="Lamp"` -> `not a mesh`.

O2 `generate_lods` doc 3
1. `generate_lods(mesh="Suz", ratios="0.5,0.25")`: objects `Suz_LOD0, Suz_LOD1, Suz_LOD2` exist, `Suz` gone
   (renamed); tri counts strictly descending, LOD1 within 10 % of 0.5 x LOD0, LOD2 within 10 % of 0.25 x LOD0;
   all three share the SAME material datablocks (`is` identity); reply lists `{name, triangles}` per level.
2. `collection="LODs"` -> all levels linked there; `parent_to_lod0=True` -> LOD1/2 parent is LOD0;
   `naming="{name}_lod{level}"` honoured; `transfer_normals=True` -> LOD1 `has_custom_normals True`.
3. `triangle_targets="200,100"` alternative within 5 %.
4. Idempotence: calling on `Suz_LOD0` again must not produce `Suz_LOD0_LOD0` (B2.2 rules: error or
   strip-and-rebuild; Q6). `ratios="a,b"` -> error; `ratios="1.5"` -> error; `ratios=""` -> error.

O3 `set_normals` (E4)
1. `AUTO_SMOOTH, angle=30` on the cube: 12 sharp edges (`edge.use_edge_sharp`), NO modifier added on
   either install, reply `modifier is None` or `"NONE"`; polygons smooth.
2. `AUTO_SMOOTH_MODIFIER, apply=False`: 5.2.1 -> one `NODES` modifier named "Smooth by Angle"; 4.3.2 ->
   teach-style error naming 5.x; no `bpy.app.version` compare (M7 static step still green).
3. `WEIGHTED`: `has_custom_normals True`, no modifier left; `apply=False`: `WEIGHTED_NORMAL` modifier with
   `mode FACE_AREA_WITH_ANGLE`, `keep_sharp True`.
4. `SMOOTH` -> every `polygon.use_smooth True`; `FLAT` -> all False. `FROM_OBJECT, source_object=<hi-poly>`
   -> `has_custom_normals True` (Data Transfer applied); `source_object` missing -> error; `FROM_OBJECT`
   without `source_object` -> error.
5. `mode="BUMPY"` -> error listing the modes (including AUTO_SMOOTH_MODIFIER; Q2).

O4 `triangulate_for_export`
1. Cube: 12 faces all `len(verts) == 3`; `Cyl`: caps triangulated, no ngon left.
2. Custom normals set first (`normals_split_custom_set_from_vertices`): after triangulation
   `has_custom_normals True` (the modifier path, not `bmesh.ops.triangulate`; mutant: swap to bmesh -> fails).
3. `as_modifier=True`: Triangulate modifier live, mesh polygons still 6, reply names the modifier.
4. `min_vertices=5` on the cube: 6 quads untouched. `quad_method="DIAGONAL"` -> error listing the live
   enum (`b2check.modifiers.triangulate_quad_method_enum`).

O5 `get_triangle_budget` (readonly)
1. `objects=None`: every mesh in the scene; `Sub` reported as 192 (E13); sorted descending by triangles.
2. `budget=100`: `Sub` `share` 1.92 (or `percent 192`; B2.2 names it), `total` and `over_budget True`.
3. `group_by="COLLECTION"` sums per collection; `"MATERIAL"` per material with `None`/`<no material>` bucket;
   `group_by="OWNER"` -> error listing the 3.

### C. Collision (L3)

C1 `create_collision` (fixture `Tor`, `b2check.collision`: src 288 faces, volume 1.1647; hull 476 faces, 3.8736)
1. `CONVEX, profile UNREAL`: object `UCX_Tor_00`, `parent is Tor`, `display_type WIRE`, `hide_render True`,
   `len(material_slots) == 0`, convex (every vert of `Tor` inside or on the hull: `closest_point_on_mesh`
   normal test, or `bmesh` volume check), reply `volume_ratio` within 2 % of 3.3257 (3.8736 / 1.1647),
   `triangles` > 0. Second call -> `UCX_Tor_01` (numbering continues).
2. `BOX`: 12 tris, `dimensions == Tor.dimensions + 2*padding` (padding 0.1); `SPHERE` / `CAPSULE` / `CYLINDER`
   bound the source (all source verts inside, tol 1e-3). `MESH_DECIMATED, decimate_ratio=0.1`: tris ~10 % of source.
3. `SLICED_CONVEX, count=3, axis="Z"`: 3 children `UCX_Tor_00..02`, each convex, union bounds cover the source z-range.
4. Names: GODOT CONVEX -> `Tor-convcol`, MESH_DECIMATED -> `Tor-col`; UNITY -> `Tor_Collider`; GENERIC -> `Tor_COL`.
5. Source untouched: vert/face counts, modifiers, selection and active object identical before/after.
   `parent=False` -> no parent; `collection="Col"` -> linked there.
6. `shape="HULL"` -> error listing the 7 shapes; `count=0` -> error; `padding=-5` (bigger than the mesh) -> error
   or clamped with a warning (B2.2); `mesh="Lamp"` -> `not a mesh`.
7. Evaluated source: `Sub` CONVEX hull volume > hull volume of the unsubdivided cube's... (subsurf shrinks:
   assert the hull was built from the EVALUATED mesh by comparing with `bmesh` on `evaluated_get`).

C2 `get_collision_info` (readonly)
1. After C1: lists `UCX_Tor_00/01`, the `-col` and `_Collider` and `_COL` proxies with their profile and
   tri counts; a mesh with no proxies -> `proxies == []`, success.

C3 `check_collision_fit` doc 4
1. `Suz` + `UCX_Suz_00` CONVEX: `covered > 0.95`, `max_outside_distance < 1e-3`.
2. Discriminating fixture: `Suz` with a proxy that is the hull of a cube half its size (renamed `UCX_Suz_00`):
   `covered < 0.6`, `max_outside_distance > 0.1`. (A convex hull always scores 1.0; this fixture is the
   one that fails when the sampler is broken.)
3. `samples=10` works; `samples=0` -> error; mesh with no proxies -> error naming the naming rules, or
   `covered 0.0` with a note (B2.2).

### S. Sockets, pivots, hierarchy (L3)

S1 `add_socket` doc 5
1. `add_socket(parent="Suz", name="Muzzle", location="0,1,0", profile="UNREAL")`: object `SOCKET_Muzzle`,
   `type EMPTY`, `parent is Suz`, `empty_display_size 0.1`, local location (0,1,0); move `Suz` to (5,0,0):
   reply `world_matrix` translation (5,1,0) (recomputed on a second call) and `matrix_world` agrees.
2. `space="WORLD", location="5,1,0"` with `Suz` at (5,0,0): local location (0,1,0).
3. `profile="TIMBERMESH"` with a `#Prop` root above `Suz`: name `#Muzzle`; without any `#` root in the chain
   -> error (doc 10). `profile="UNITY"` / `"GENERIC"` -> plain `Muzzle`.
4. `bone="Bone"` on an armature parent: `parent_type == "BONE"`, `parent_bone == "Bone"`; `bone="NoBone"` -> error listing bones.
5. `snap_to="VERTEX", vertex_index=3`: world location == `Suz.matrix_world @ v[3].co` (tol 1e-5);
   `snap_to="SURFACE"`: `closest_point_on_mesh` distance < 1e-4; `vertex_index=99999` -> `out of range`.
6. Second `Muzzle`: Blender's `.001` is reported, not the requested name (E12 suffix rule).

S2 `list_sockets` (readonly)
1. Finds `SOCKET_Muzzle` and `#Muzzle`, not a plain empty named `Helper`; `parent="Suz"` filters; each
   entry has `local` and `world` transforms and `bone` (None or name).

S3 `set_pivot`
1. `objects="A,B", mode="BOTTOM_CENTER"`: both `min(v.co.z) == 0`; world vertex coords unchanged (`keep_world`).
2. `CENTER`, `TOP_CENTER`, `WORLD_ORIGIN` (origin at world 0, geometry fixed), `CURSOR` (cursor moved to
   (1,1,1) first), custom `"2,0,0"`. `mode="LEFT"` -> error listing the modes.

S4 `snap_to_grid`
1. Origin (0.4, 1.6, 0.2), `grid=1.0`: -> (0, 2, 0); `axes="XY"`: z stays 0.2; `grid=0.5`: (0.5, 1.5, 0).
2. `what="BOUNDS_MIN"`: bbox min lands on the grid; `"BOUNDS_CENTER"` likewise. `grid=0` / `-1` -> error;
   `axes="W"` -> error.

S5 `build_export_hierarchy`
1. UNREAL: `root_name="Gun"`, `meshes="Suz_LOD0,Suz_LOD1"`, `collision="UCX_Suz_00"`, `sockets="SOCKET_Muzzle"`:
   root EMPTY `Gun` created; LODs are siblings under it with the LodGroup custom prop
   (`b2check.fbx_roundtrip.import.custom_props` idiom: `lod_group` or `fbx_type`, B2.2 names it); UCX children
   under the LOD0 mesh (or root; B2.2); reply `tree` nested names equals the bpy hierarchy walk.
2. Second call with the same `root_name`: reuses `Gun`, no `Gun.001`. GODOT: `-col` siblings. UNITY: LOD siblings.
3. `meshes="Nope"` -> `not found`; world positions of every object unchanged after re-parenting
   (parent inverse set).

### E. Engine export presets (L3)

E1 `get_export_preset` (add-on handler, H8 / R-B2-14; readonly)
1. For each of the 7 engines: `ok(call("get_export_preset", engine=E))` carries `engine, format, exporter, kwargs,
   naming, axis, units, notes`; UNITY kwargs == {axis_forward -Z, axis_up Y, apply_scale_options FBX_SCALE_ALL,
   global_scale 1.0, use_tspace True, mesh_smooth_type FACE, add_leaf_bones False, bake_space_transform False,
   use_custom_props True}; UNREAL {-Y, Z, 1.0, FBX_SCALE_NONE, use_tspace, FACE, add_leaf_bones False,
   use_custom_props True}; GODOT and BEVY glb {export_yup, export_apply, export_tangents, export_extras,
   export_attributes True, export_vertex_color MATERIAL, export_image_format AUTO}; TIMBERMESH {merge_meshes True,
   single_animation True, use_vertex_animations False}; GENERIC_GLTF / GENERIC_FBX = defaults.
2. `engine="PS5"` -> error listing the 7 (H12). Single source: E2.1 asserts `preset_used` of a real export equals
   this reply's `kwargs` key-for-key (mutant: change one table entry -> both fail).

E2 `export_for_engine` doc 6 (UNREAL)
1. Fixture: `Suz_LOD0..2` (O2), `UCX_Suz_00..02` (C1 SLICED), `SOCKET_Muzzle` (S1), under root `Gun` (S5),
   `scale_length 1.0`. `ok(call("export_for_engine", objects="Gun", engine="UNREAL", filepath=<WORK>/gun.fbx))`:
   file exists, `bytes > 0`, `manifest_path == <WORK>/gun.fbx.manifest.json` exists and parses; manifest
   is the reply minus `manifest_path` plus `written_at` (ISO) and `blender` (H9, written by the ADD-ON); reply/manifest
   `objects` lists the 8 exported names (3 LODs, 3 UCX, 1 socket, root), `triangles` is the evaluated sum,
   `preset_used` equals `get_export_preset("UNREAL").kwargs` key-for-key, `warnings` contains the
   `scale_length != 0.01` text naming `set_scene_units` (doc 8), `validation.verdict` present, `format "fbx"`.
2. `clear_scene()`, import `gun.fbx` with `use_custom_props=True`: object names `Gun, Suz_LOD0, Suz_LOD1, Suz_LOD2,
   UCX_Suz_00, UCX_Suz_01, UCX_Suz_02, SOCKET_Muzzle` all present (no `.001`, fresh scene); `SOCKET_Muzzle`
   location (0,1,0) relative to its parent; LOD custom prop survives (E8).
3. `include_collision=False`: no `UCX_` name in `objects` nor in the re-import; `include_sockets=False` likewise;
   `include_lods=False` exports LOD0 only. D6: `triangulate=True` uses a TEMPORARY Triangulate modifier removed in
   `finally`: the source objects' polygon counts and modifier lists are identical before/after (also on the error path).
4. `validate=True` (default) on a FAILing asset (`Prop` unapplied): reply error names the failed checks, NO
   file and NO manifest written; `overrides='{"force": true}'` -> exported, manifest `validation.verdict == "FAIL"`
   and `forced True`.
5. `overrides='{"mesh_smooth_type": "SMOOTH_GROUP"}'`: 4.3.2 -> error listing the live enum, nothing written;
   5.2.1 -> exported (E8). `overrides="not json"` -> JSON teach error; `overrides='7'` (scalar) -> error, the
   reply is NOT the scalar `7` (F4 class, `_parse_json_any` chokepoint).
6. `filepath` in a missing directory -> `does not exist`; `engine="PS5"` -> error listing the 7 engines;
   `objects` and `collection` both missing -> error.
7. `textures="COPY"` with an image-textured material: the image file is copied beside the FBX and listed in
   manifest `textures`; `"NONE"` copies nothing; `"EMBED"` -> `embed_textures` on the wire (manifest `preset`).
8. Selection, active object and mode are what they were before the call (export selects the hierarchy and
   must restore; readonly-style assertion on `snapshot()["selected"]`).

E3 `export_for_engine` doc 7 (BEVY / GODOT, GLB)
1. R-B2-15: the ORDER assertion runs ONLY on an unsplit fixture: `Ico` = smooth-shaded ico sphere, no UV layer, no
   custom normals. `export_for_engine(objects="Ico", engine="BEVY", filepath=ico.glb)`: GLB header magic `glTF`,
   JSON chunk 0 parses, some `meshes[].primitives[].attributes` has `TANGENT`; `clear_scene()`, import,
   `compare_meshes(Ico_source_copy, imported)`: `vertices.delta == 0`, `order_match is True`,
   `positions.max_delta < 1e-5`.
2. `Suz_LOD0` (UV seams): order after a split export is NOT a contract; the step records `vertices.delta` and
   `order_match` in its detail and asserts only that the export succeeds and the GLB carries TANGENT.
3. GODOT with a colour attribute named `TintMask`: `validate`/manifest `warnings` says colour attribute names
   are lost in glTF (E9); after re-import the attribute is `Color` (fact check, both installs).
4. `export_vertex_color` on 4.3.2 is `MATERIAL` in the preset; an override `NAME` on 4.3.2 -> error listing the
   live enum; on 5.2.1 `NAME` without `export_vertex_color_name` -> error naming the missing kwarg.

E4 `export_for_engine` TIMBERMESH doc 10
1. 4.3.2 (plugin present, `b2check.misc.timbermesh_module true`): collection `Prop` with `#Prop` root empty, a
   mesh child, `#Socket` empty child: `export_for_engine(collection="Prop", engine="TIMBERMESH", filepath=p.timbermesh)`:
   file > 100 bytes; `zlib.decompress` of the payload (per the plugin's framing; B2.2 states the offset) contains
   `b"Prop"` and `b"Socket"`.
2. 5.2.1 (plugin absent): teach-style error naming `timbermesh_blender_plugin` and the install path (E14); no file.
3. `objects="Suz"` (no collection): a temporary collection is created and removed in `finally`:
   `bpy.data.collections` names identical before/after, on the success path AND on the no-`#`-root error path.
4. No `#` root -> error (doc 10); `merge_meshes` override reaches the exporter settings (reply echoes settings).

E5 `batch_export` doc 9
1. Collection `Kit` with 3 valid props: `batch_export(collection="Kit", engine="UNITY", output_dir=<WORK>/kit)`:
   3 `.fbx` files + 3 `.manifest.json`, reply `manifest` with 3 rows `status PASS`, `files` paths exist,
   `batch_manifest_path` (B2.2 name) exists.
2. One prop made invalid (unapplied scale), `continue_on_error=True`: 2 PASS + 1 FAIL row with the error text,
   2 files written; `continue_on_error=False`: stops at the failing index, `stopped_at` reported, later props
   not written.
3. `skip_hidden=True` skips a hidden prop (row `SKIPPED` or absent; B2.2); `naming="{name}_v1"` honoured;
   `one_file_per="ROOT"` puts a root's hierarchy in one file; `"COLLECTION"` one file total.
4. `output_dir` missing -> `does not exist`; empty collection -> success with 0 rows or a teach error, never
   a traceback.

E6 `set_scene_units` extended (B0 handler extended, NOT re-added: the handler table has exactly one key)
1. `preset="UNREAL"`: `unit_settings.system METRIC, scale_length 0.01, length_unit CENTIMETERS`; reply
   `needs_rescale` lists existing mesh objects; `rescale_objects=True` scales them so world dimensions in
   metres are preserved (cube 2 m -> `dimensions` 200 in the new unit, i.e. `scale` x100; B2.2 fixes the
   semantic, Q7). `UNITY` / `GODOT` / `BEVY` -> 1.0 METERS. Explicit `scale_length=0.5` honoured.
2. Doc 8: after UNREAL units, `export_for_engine(UNREAL)` manifest has no `scale_length` WARN.
3. B0 regression: the 2.1 `set_scene_units` calls in `b0_steps.py` still pass; `preset="N64"` -> error.

### G. Cross-cutting

G1 Handler table: `s0_handlers` (step "handler table complete") includes every new handler name; the
   count of handlers rises by the number of new add-on tools (B2.2 lists them; `get_export_preset` is server-only).
G2 Static: `M7 no bpy.app.version comparisons`, `M12 numpy dtype`, `F8 no tempfile._cleanup` stay green on the
   grown addon.py (the 4.x/5.x switches in E2/E6/E7/E8/E9/E10 are try/except TypeError + live enums).
G3 Legacy: Phase A + B0 + B1 + DRV = 204 PASS unchanged with `--b2` on.
G4 Driver seam: `describe(what="validate_asset")`-style lookups are not expected (driver describes RNA, not
   handlers), but `drv_find(where="objects", name="UCX_*")` returns the collision proxies by uid.
G5 Every mutating B2 tool restores mode OBJECT and leaves active/selection as before unless the tool's
   purpose is selection (none is). Every B2 tool refuses a non-mesh with `not a mesh` and a missing name
   with `not found` (the harness already asserts these texts for the legacy tools).
G6 Reply hygiene: no bpy structs anywhere (`_enc_ok` of `drv_steps.py` reused on every B2 reply); every
   float is finite; every path in a reply is absolute.

## 2. Server-side seam checks (`tests/test_b2_server.py`, FakeConn, venv)

S1 Wire shape: for every new wrapper, the exact `(handler, params)` sent; defaults NOT sent when the
   add-on owns the default (or always sent; B2.2 picks one and the test pins it). Comma strings and JSON
   lists for `objects` produce the same wire params (I2 of TEST-PLAN-C).
S2 Refusals with zero sends: bad `profile` / `engine` / `shape` / `mode` / `issue` enum at the server ->
   `len(fake.calls) == 0` and the error text lists the valid values (R12/R31 idiom).
S3 Manifest (H9 / R-B2-14): the ADD-ON writes `<file>.manifest.json`; the server only renders `manifest_path`
   from the reply and never writes a per-file manifest. FakeConn test: a reply without `manifest_path` renders
   without crashing; no file appears beside `filepath` from the server side. The real manifest assertions are
   harness step E2.1.
S4 `get_export_preset` (H8 / R-B2-14): ONE send `("get_export_preset", {"engine": E})`; a bad `engine` is refused
   at the server with zero sends and the 7 valid names in the text (H12); the reply dict is rendered as JSON
   verbatim (the table itself is asserted in harness step E1).
S5 `batch_export` loops on the server (H10): roots resolved by one `get_scene_info` send, then N
   `export_for_engine` sends with per-root `objects`, `engine`, `filepath` = `<output_dir>/<naming>.<ext>`;
   a FakeConn error on root 2 with `continue_on_error=True` yields rows PASS/FAIL/PASS and 3 export sends; with
   `False` 2 sends and a `stopped` note; `<output_dir>/batch_manifest.json` written server-side with
   `{engine, rows: [{object, status, filepath, error}], written_at}`; `output_dir` missing -> refused, zero sends.
S6 tools/list: `dump_tools_list.py names` is a superset of `baseline_tools_2.3.txt`; the added names equal the
   B2.2 Tier 1 list exactly (no stray tool); at B2-G3 the list is cut as `baseline_tools_2.4.txt` and the exact
   test repoints (R11/R29 idiom). Every new name appears in `TOOLS.md` (docs seam, grep).
S7 Version: `test_version_sync` EXPECTED = 2.4.0; all three sources agree; `bl_info["blender"] == (4,0,0)`.
S8 JSON params (`overrides`, `props`): a scalar JSON (`7`, `"x"`) is refused at the chokepoint, never echoed
   as the reply (F4 class, proven at one B2 site).
S9 Settings regression: `settings_baseline.py -- --check` still `match: true` on both installs after L1
   (`get_scene_info` grew keys: if the fixture covers it, the fixture is re-cut with a Lead ruling, not silently).

## 3. Mutation evidence to request from owners (break X, show exactly which step fails)

| Mutant (scratch copy only) | Expected red |
|---|---|
| ngon check skipped in `validate_asset` | V2.1 |
| `corrective_flip_normals` passed without try/except | V1.3 / V10.3 on 4.3.2 (TypeError) |
| `isolate_users` dropped | V10.1 |
| `keep_custom_normals=False` in triangulate | O4.2 |
| UCX numbering starts at 01 or name constant `UCX_Tor_00` | C1.1 second call |
| hull built from `obj.data` instead of evaluated | C1.7 |
| `check_collision_fit` returns `len(inside)/samples` with inside always True | C3.2 |
| manifest written before the add-on reply is checked | S3 error case |
| UNREAL preset `axis_up` Y | S4 + E2.1 |
| `validate=True` refusal removed | E2.4 |
| temp Timbermesh collection not removed in `finally` | E4.3 error path |
| `generate_lods` shares copies of materials | O2.1 identity |
| `set_origin` offsets by local delta without the object matrix | F6.2 rotated Prop |
| `_DRV`-style cap on `sample_indices` removed | V8.2 |

## 4. Per-seam integration checks (where teams break)

1. add-on reply keys vs server rendering: for every new tool, the server test's FakeConn reply uses the
   EXACT key names the add-on step asserts (one shared key list per tool in B2.2; QA diffs the two files).
2. server preset table vs add-on exporter kwargs: every key in `get_export_preset` output is accepted by the
   add-on's exporter call on BOTH installs (E2.1 succeeds with the full preset, no `TypeError`/enum error).
3. manifest counts vs bpy: `lods/collision/sockets/objects` in the manifest equal what the re-import finds.
4. docs vs code: TOOLS.md names, parameter lists and enum values grep-match the server signatures (every enum
   string in TOOLS.md's B2 sections appears in server.py).
5. harness flags: `--b2` alone (without `--b0 --b1 --drv`) also runs (the fixtures are self-contained).

## 5. Live batch at B2-G5 (R-B2-25: QA runs it on a separate instance; mechanics ratified as a B2.x row)

### 5.1 Mechanics (never the user's own Blender or MCP session; memory rule "separate instance")

1. Instance: QA starts `D:\blender-5.2.1-windows-x64\blender.exe --factory-startup --python tests\live_register.py`
   (GUI, no `--background`), then the same with `D:\blender-4.3.2-windows-x64`. `tests/live_register.py` (QA file,
   written at G5, ASCII, never imports anything from `%APPDATA%` or `B:`) does exactly what the harness does at
   `headless_handlers.py:79-83`: `importlib.util.spec_from_file_location("addon", "D:/App Dev/blender_mcp/addon.py")`,
   `module_from_spec`, exec, then creates the preferences entry the B0-15a step uses (`preferences.addons.new()`,
   `module = "addon"`) so `_prefs()` is real, sets `prefs.port = 9877` and `prefs.autostart_server = False`, calls
   `addon.register()` and finally `addon.ensure_server(port=9877)` (the explicit path, same function as the panel
   button and the autostart timer; `register()` itself never opens a port while autostart is off). It never calls
   `wm.save_userpref` (the 2.2.0 live plan's L7 rule) and prints one `LIVE_REGISTER {json}` line with the port, the
   add-on path and the bl_info version. The dev `addon.py` is never copied anywhere; the user's `%APPDATA%` add-on
   (2.2.0 deployed) is not loaded because `--factory-startup` disables user add-ons.
2. Port: 9877 (the user's deployed pair owns 9876). The server side gets `BLENDER_PORT=9877`,
   `BLENDER_HOST=127.0.0.1`, `BLENDER_EXE=<the install under test>` and `BLENDER_MCP_SETTINGS_DIR=<temp dir>` in its
   environment (server.py:206-209: env overrides settings.json), so nothing touches the user's settings file.
3. Driving the tools: through the repo `.venv` server (`.venv\Scripts\python -m blender_mcp.server`, `main()` at
   server.py:8641, stdio transport) with a small MCP client script (`tests/live_client.py`, QA file, `mcp` package
   from the venv, `stdio_client` + `ClientSession`, `call_tool(name, args)`, replies pasted verbatim). Trade-off,
   stated: the raw socket the harness uses (`execute_command` over 127.0.0.1:9877) would bypass server.py entirely
   and test nothing of the wrappers, the manifest rendering, the image path or the 180 s command timeout; the MCP
   client is the real contract and is what the user's Claude Code uses. Blender-side truth goes through
   `execute_blender_code(code=...)` as in `docs/LIVE-TEST-PLAN.md` (2.2.0 live plan: deployed pair, user's own
   GUI, steps S/L1-L13/R, replies pasted verbatim, `lt_` fixture prefix, `L<n>.<k>: PASS|FAIL` blocks; its L5
   `start_blender` steps are NOT repeated here because the instance is started by hand per rule 1).
4. Order: 5.2.1 first, then 4.3.2; one instance at a time; `get_version` first on each (addon 2.4.0 / server
   2.4.0 / protocol 1 / `background: false`). Everything saved goes under a temp dir with the `lt_` prefix.
5. End: `close_blender` (or the instance's own File > Quit if that tool refuses), confirm the process is gone
   (`tasklist`), server process ended, settings temp dir removed. Both Blenders closed before the sign-off.
6. Evidence: replies and images pasted on card `b2-t-t3` (or `b2-live` if the Lead creates it) and
   `docs/LIVE-TEST-REPORT-2026-10-21.md` in the shape of `docs/LIVE-TEST-REPORT-2026-09-11.md` (Registration /
   Results table Step | Result | Evidence / Findings with fix hashes / Not run). Defects go to the owner's card
   with a repro.

### 5.2 List (= this plan's headless-unverifiable items + PLAN 13.5 B2-G5 rows + the four 2.3.0 driver checks of `docs/LEAD-COMPLETION-D1.md`)

1. `find_mesh_issues(render=True)` on `Open` NON_MANIFOLD: image non-blank with the 4 hole edges highlighted;
   mode and selection restored after (L14).
2. Timbermesh: `export_for_engine(TIMBERMESH)` on 4.3.2 (plugin present) writes the file (section E4); on 5.2.1 the
   teach error names the module until the user installs it (L15).
3. Export files opened in the user's engine: Unreal import of `UCX_` / `SOCKET_` / LOD naming, Unity LODGroup from
   `_LOD` names, Godot `-col` (L16): QA writes the files to the temp dir and hands the paths to the user; the user's
   engine check is reported by the user, not asserted by QA.
4. Viewport: proxies drawn as wire, `empty_display_size` of sockets visible, `validate_asset(fix=True)` result visible.
5. The four 2.3.0 driver checks (D-G5, same tree): `call_operator("view3d.view_all", context={"area":"VIEW_3D",
   "region":"WINDOW"})` returns FINISHED; `transaction` ROLLBACK visibly reverts and Edit > Undo History shows
   `mcp:<name>` (+ `:pre`); one `batch` of 50 `rna_set` calls in one round trip with the viewport responsive;
   `rna_set(publish=True)` msgbus delivery (R18).
6. Capture tools on a real window (2.2.0 L3 regression): `get_viewport_screenshot`, `capture_viewport_angle`,
   `capture_contact_sheet` non-blank at 2.4.0.

## 6. Questions for Barbara / the Lead (answers land in PLAN.md section 13; the test is written to the answer)

Q1 Reply key names and check identifiers: `validate_asset` (`<objects>`, `<verdict>`, `<fixes>`,
   `<fixes_applied>`, the snake_case id of every check), `export_object` (`bytes/object_count/vertices/triangles`),
   `compare_meshes`, `create_collision` (`names/triangles/volume_ratio`), `check_collision_fit`
   (`covered/max_outside_distance`), `batch_export` rows, manifest keys. Without an exact list the tests
   cannot key on anything; TEST-PLAN-C had this list in 12.2 and it saved a day.
Q2 `set_normals` mode enum must include `AUTO_SMOOTH_MODIFIER` (E4) - doc 2.1 does not list it.
Q3 Doc step 7 "identical vert count and ORDER" after a glTF round trip is only true for a mesh the exporter
   does not split (smooth shaded, no UV seams, no custom normals). Fixture ruling needed (E3.1 vs E3.2).
Q4 The > 65,535 verts WARN: before or after triangulation? (Tier 2 `check_vertex_count_split` says after.)
Q5 Bone influences: do zero-weight groups count?
Q6 `generate_lods` on an object already named `*_LOD0`: error, or strip and rebuild?
Q7 `set_scene_units(rescale_objects=True)` semantic: preserve world metres (scale x100 on UNREAL) or
   preserve numbers?
Q8 Section 11 says 2.3.0 everywhere (E1, 11.2 `bl_info (2,3,0)`, `__version__`, `baseline_tools_2.3.txt`
   re-cut); R-B2-5 says 2.4.0. Section 13 must renumber, and the 2.3 baseline file is the pin, not the target.
Q9 11.2 B2-T says "superset vs `baseline_tools_2.2.txt`": must be 2.3.
Q10 4.1 names `make_collision`, `build_lods`, `decimate`, `tris_to_quads`, `batch_rename`; 2.1 names
   `create_collision`, `generate_lods`, `decimate_mesh`, and has no `tris_to_quads`/`batch_rename` tool.
   Section 13 should say the 4.1 rows are facts only, one tool name each.
Q11 11.4 L4 Tier 2 set was ruled "for 2.3.0". Does it ride in 2.4.0? If yes the plan gets a T-block
   (`reduce_bone_influences`, `check_vertex_count_split`, `make_single_user`, `set/get_custom_properties`,
   `merge_for_export`, `remesh_object`) and 11.5 facts must be measured first.
Q12 `find_mesh_issues` takes `mesh`, everything else `objects`: fine, but B2.2 should say so explicitly so
   the server test does not guess.
Q13 Does `validate_asset(fix=False)` guarantee a read-only pass (no selection or mode change leaks)? The plan
   asserts it as `readonly=True`.

## 7. Reconciliation with PLAN.md section 13 (D2.0 draft, hash 12d3aff7, read 2026-10-21 18:15Z; NOT yet ratified)

Where section 13 fixes a name or key, it supersedes sections 1-2 above (B2.2 wins). Adopted as written:
- Q1 answered: reply keys per tool are in 13.2.2-13.2.7; `validate_asset` = 22 fixed check ids (13.2.3) with the
  statuses there (`non_manifold`, `loose`, `doubles`, `bone_influences`, `illegal_names`, `name_collisions` are WARN,
  not FAIL as sections V2/V6/V7 guessed; `hierarchy`, `degenerate`, `inconsistent_normals`, `images_missing`,
  `triangle_budget`, `missing_uv`, `missing_uv2`, `unapplied_transform`, `negative_scale` FAIL). Steps key on
  `objects.<name>.checks.<id>.{status,detail,count,sample_indices(<=20)}`, `verdict`, `summary`, `fixes_available`,
  `fixes_applied`.
- Q2 answered: `set_normals` modes incl. `AUTO_SMOOTH_MODIFIER` and `CLEAR_CUSTOM` (also in `fix_normals`: both
  tested, same bpy truth).
- H8/H9 supersede 11.2: `get_export_preset` IS an add-on handler (one send; section 2 S4 becomes a wire test +
  harness step E1 asserting the 7 entries); the manifest is written by the ADD-ON (S3 moves to the harness: file
  beside the export, JSON = reply minus `manifest_path` plus `written_at`, `blender`; error reply -> no file).
  `batch_export` stays a server loop with `<output_dir>/batch_manifest.json` (S5 stands).
- H17: `set_scene_units` unchanged (verified from disk: addon.py:4710 has presets incl. TIMBERMESH and
  `rescale_objects`); section E6 shrinks to the doc-8 warning check + B0 regression.
- H11: unknown names -> `{error, unknown: [...], valid_count}` and NOTHING applied: every `objects` tool gets the
  "one unknown name in a list of two -> error and the known one untouched" case.
- H20: degrees everywhere in B2. H21: every mutating handler under `_selection_scope` + `_mode_restore`
  (addon.py:910/955 verified).
- `export_object` keys: `size_bytes, object_count, vertices_written, triangles_written, warnings, transform_baked`.
  `get_mesh_stats`: `triangles` (evaluated) + `triangles_simple`, `unapplied_transform: {rotation, scale,
  negative_scale}`, `origin_offset: {to_bbox_center, to_bbox_bottom}`, `material_slots: [{index, material}]`.
  `set_origin(origin_type=BOTTOM_CENTER|TOP_CENTER|CUSTOM, location=)` (param name verified addon.py:3269).
- Debt #13 (H15, R-B2-11): fixture `tests/fixtures/legacy_secrets_1.6.0.blend` generated on 4.3.2 (459876 bytes,
  FIXTURE line = 4 FIXTURE-* strings + port 9999 + server_running 1). Step B2-0: open the fixture with the 2.4.0
  add-on registered -> prefs hold the 4 values, `scene.get(<key>)` is None for each (moved, not copied),
  `blendermcp_port` / `blendermcp_server_running` idprops do NOT land in prefs; both installs.
- R-B2-10 (T0): `test_f6_connection.py:223` identity-vs-2.3 becomes superset-vs-2.3 with the additive-param
  whitelist = exactly the params listed in 13.2.2 for the six fixed tools; a 2.4 identity test is added at G3
  against `baseline_tools_2.4.txt` + `baselines/tools_list_2.4.json`. No skipped tests (overrides 13.3 D2-T "SKIP").
- Events (13.2.1, DRV-10) are a CONDITIONAL L4 per R-B2-8: the DRV-10 block (subscribe/poll/unsubscribe, ring
  buffer drop-oldest, cursor paging, double-register guard, `errors == 0`, server `wait_ms` clamp with patched
  `asyncio.sleep`, at most 121 polls) is written only on the Lead's L4 call, in `tests/drv_steps.py`.

Inconsistencies in section 13 for Barbara (section numbers; routed via the Lead):
1. 13.3/13.4/H3/H14 are written for scope A (events = L1, fixes in L2, tools/list 185 after L1). R-B2-8 = scope B
   + conditional tails: L1 = debts #13/#14 (+ bump), events conditional L4. Landing numbers, handler counts per
   landing (171 -> 174 -> ...) and "first end-to-end proof = events" must be re-cut.
2. 13.3 D2-T names `tests/test_d2_server.py` and `docs/TEST-PLAN-D2.md`; the Lead's phase-0 DM names
   `tests/test_b2_server.py` and `docs/TEST-PLAN-B2.md` (this file exists). One name each, ruling needed.
3. 13.3 D2-T "T0 ... SKIP with reason until D2-G3" contradicts R-B2-10 "No skipped tests".
4. H1 says Grace bumps all three version sources; `addon.py` `bl_info` is Linus's file (13.3 D2-A L1 says so).
   Both must land in the same gate or `test_version_sync` is red between them.
5. 13.2.3 `hierarchy` FAIL "for UNREAL without `UCX_` naming on children typed as collision": "typed as
   collision" is undefined (display WIRE? hide_render? any child mesh?). A mesh with no children at all: PASS or FAIL?
6. 13.2.3 `summary: {pass, warn, fail}`: counts of objects or of checks? And are `fixes_available` and
   `fixes_applied` both always present (empty list) regardless of `fix`?
7. 13.2.7 `batch_export` "roots resolved by one `find_objects_by_type`/`get_scene_info` call" with `skip_hidden`:
   `get_scene_info` object entries carry `parent` (addon.py:766) but no hide flags; the server cannot honour
   `skip_hidden` from that reply. Either `get_scene_info` gains `hide_viewport`/`hide_render` per object (13.2.2) or
   the loop uses `rna_get`.
8. 13.2.2 `import_file` drops the request doc's `armatures` reply key (doc section 1). Deliberate?
9. 13.2.4 `generate_lods` on an object already named `*_LOD0` (Q6 above) still unruled; 13.5 "B2-3b invalid ratios
   -> source untouched" covers the failure path only.
10. 13.5 glTF row promises `order_match True` on "B2-7" without naming the fixture; the claim only holds for an
    unsplit mesh (Q3). Name the fixture (smooth ico sphere, no UVs) or relax to "matched vertices".
11. 13.2.7 `export_for_engine` `triangulate=True` is destructive on the user's objects (13.8 Q2 open). If the Lead
    rules destructive, step E2 asserts the source LOD0 polygon count changed and `warnings` says so; if copies,
    it asserts the source is byte-identical (vert/poly counts).
12. Request doc step 8 says `set_scene_units(UNREAL)` "sets scale_length 0.01"; H17 says the tool is unchanged;
    consistent only if the B0 tool already does that (verified: addon.py:3901 table has UNREAL; OK, no action).

## 8. Amendment log

- 2026-10-21 v1: written from the request doc + PLAN.md 11 + R-B2-1..7, before B2.2.
- 2026-10-21 v1.5 (T3): section 5 = live mechanics + list (R-B2-25); T3 steps S1-S5/E1-E4/E6 and tests/test_b2_server.py
  (26 tests) landed; T1 folded R-B2-28 (parent key), R-B2-29 (non_manifold excludes boundary; boundary_edges),
  F-B2-1 (offset_applied), R-B2-26/27 (generate_lods None default, compare subset omitted); `dimensions` = max-extent
  rule (Linus deviation (b), accepted). Settings fixtures re-cut at addon a611e0e3 (R-B2-24): diff vs 2.3 = exactly the
  four legacy secret keys (32 leaves) + describe_SCENE count 93->89 / 98->94; --check match:true both installs.
- 2026-10-21 v1.3/v1.4 (T1/T2): steps keyed on B2.2-B2.9 reply keys; test-side corrections listed on card b2-t-t1.
- 2026-10-21 v1.2 (T0): E1/E2/E3/S3/S4/S5 rewritten to H8/H9/H10, D6 and R-B2-14/15; landing map = R-B2-13
  (T1 = 13.2.2 + 13.2.3 + B2-0 before Linus L1; T2 = 13.2.4 + 13.2.5; T3 = 13.2.6 + 13.2.7).
- 2026-10-21 v1.1: section 7 added after reading PLAN.md section 13 (D2.0 draft); baseline re-taken from disk
  (both installs 204/204 EXIT 0 at addon f2c680bb / server 349c6a9e, venv 94 passed, tools/list 182 == 2.3 pin);
  debt #13 fixture generated.
- 2026-10-22 v1.6: B2-G3 freeze evidence recorded (Tests.md e05c0387, card b2-t-t3): frozen pair addon ee9b9dc0 /
  server a2cdd77f; harness 252/252 both installs EXIT 0 at b2_steps 0362c7af / drv_steps 60f2bdec; venv 130 passed
  0 skipped (2.4 identity test test_tools_list_identical_to_2_4_baseline added, never a skip); 2.4 baselines cut
  (baseline_tools_2.4.txt e24a34cd, baselines/tools_list_2.4.json 3c6026b3, diff vs 2.3 = 22 added / 0 removed /
  0 reordered, "-" lines only in the 7 ruled tools); settings --check match:true both; RA2 extended with the two
  edit-mode cases (mut11 / mut13 harness-red); G8 = R-B2-42 regression guard. Section 5 (G5 live) is the remainder.
- 2026-10-23 v1.7: B2-G5 live batch DONE on both builds at the G3 pair (docs/LIVE-TEST-REPORT-2026-10-22.md); four
  live findings F-B2-4..7 fixed in the reopen and verified live again at the RE-FROZEN pair addon 2a97736c / server
  8e3ca441 (re-probe section of the report); guards added: DRV-12c (dict result_expr), E4 frame assert (b2_steps
  ba354830 / drv_steps a45e37c4), RS-16 FakeConn close_blender (test_b2_server b70cbc20); harness 253/253 both
  installs, venv 131, tools IDENTICAL, settings match. Section 5.2 gains the F-B2-6 item: view_location / view_distance
  unchanged after find_mesh_issues(render=True), capture_contact_sheet and capture_viewport_angle (4.3.2 and 5.2.1).
