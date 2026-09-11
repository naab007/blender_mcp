# Feature request: rigging and animation tools

Status: HEADLESS COMPLETE 2026-09-11 as Phase B1 of team nuzukm on branch `blender-5.2`
(2.2.0): Tier 1 + the six fixes on add-on `37d2211d` and server `6f249049`, harness 185/185 on 4.3.2 and
185/185 on 5.2.1, venv 64/64; live checks L10-L12 PASS on 5.2.1 GUI (L13 Rigify skipped, Tier 2), five GUI defects fixed
as A1.2 (addon `f4dc37b1`) and the select-flag restore as A1.3 (addon `0eb7ec4c`), headless 186/186 / 186/186
at the final pair, see `docs/LIVE-TEST-REPORT-2026-09-11.md`. Committed on `blender-5.2`, not released.
Tier 2 (section 3.2) staged on the server side, NOT shipped (next run, PLAN.md 10.4).
Requested 2026-09-06.
Target version: 2.2.0 (PLAN.md C24; the original 1.7.0 is superseded; all three version
sources move together)
Baseline: v2.1.0, 148 tools (`tests/baseline_tools_2.1.txt`)

## 0. Read this first (implementing agent)

This repo is the DEV copy at `D:\App Dev\blender_mcp\`. Do not edit the live copies.

Before writing code:

1. Read `README.md`, `TOOLS.md` and the whole of `addon.py` and `src/blender_mcp/server.py`.
   The handler and wrapper patterns below are what every new tool must copy.
2. Read the auto-memory note `project_blender_mcp.md` in
   `C:\Users\Naabin\.claude\projects\D--App-Dev\memory\` for deploy targets and the
   Blender 4.x API facts already verified.
3. Query MemPalace (`main` db) for "blender_mcp" before designing anything that feels
   like it should already exist.

Scope rule: build what is in this document. If something here turns out to be wrong or
impossible in Blender 4.3, say so in the report and keep going with the rest.

### Why this is needed

The server has 92 tools and exactly two touch animation (`add_keyframe`, `set_frame`).
There is nothing for armatures, bones, vertex groups, shape keys, constraints, drivers,
actions, NLA or playback review. The `/blender` skill tells the agent to fall back to
raw `execute_blender_code` for rigging. That is slow, error-prone and gives the agent
no way to SEE a rig, a weight map or a motion. Every act tool below is paired with a
perceive tool for that reason.

## 1. Architecture you must follow

### 1.1 Two files, one wire protocol

| Layer | File | Pattern |
|---|---|---|
| Blender add-on | `addon.py` | `BlenderMCPServer` methods. Registered by name in the `extended_handlers` dict inside `_build_handlers()`. Return a dict: `{"error": "..."}` on failure, `{"success": True, ...}` on success. |
| MCP server | `src/blender_mcp/server.py` | `@mcp.tool()` function per tool. Gets the connection with `get_blender_connection()`, calls `blender.send_command("<handler_name>", {...})`, checks `if "error" in result`, returns a short human string. Image-returning tools go through `_safe_image_return(data, "png")`. |

Server and add-on must be deployed together whenever the wire payload changes.

### 1.2 Conventions already enforced by helpers in `addon.py`

- `_ensure_object_mode(obj)` before touching mesh data.
- `_select_only(obj)` when an operator needs a selection.
- `_check_indices(indices, count, label)` for any index list from the client.
- `_bmesh_edit(obj)` context manager for BMesh work (frees in `finally`).
- `_op_exists(op)` instead of `hasattr(bpy.ops.x, "y")` (which is always True).
- `_render_settings(...)` / `_render_to_file(...)` for anything that renders. Renders
  must restore camera, resolution and samples, and fail loudly if no file was written.
- Every handler that changes mode restores the ORIGINAL mode and active object in a
  `finally` block. Add a `_mode_restore` context manager if one is missing.
- Every handler that leaves edit mode on an armature returns bone NAMES, never
  `EditBone` references (they are invalidated on mode exit).
- Errors teach: a rejected enum value lists the valid values.

### 1.3 Server-side conventions

- List parameters arrive as comma strings (`"0,1,2"`, `"1,2,3"`) or JSON strings for
  structured data. Look at `add_modifier` (JSON `params`) and `set_vertex_positions`
  (JSON list) for the two accepted styles. Parse on the server, send native types.
- Rotations from the client are DEGREES. Convert to radians before sending, or in the
  handler, but be consistent with `rotate_object` and `add_keyframe`.
- Any tool that can take more than a second of Blender time must still be synchronous
  (Blender runs handlers on its main thread via `bpy.app.timers`). Only the process
  management and TripoSR tools are `async`.

### 1.4 New shared helpers to add (addon)

```python
@contextmanager
def _armature_edit(self, arm_obj):
    """Enter EDIT mode on arm_obj, yield arm_obj.data.edit_bones, restore prior mode/active."""

@contextmanager
def _pose_mode(self, arm_obj):
    """Enter POSE mode on arm_obj, yield arm_obj.pose.bones, restore prior mode/active."""

@contextmanager
def _mode_restore(self):
    """Remember active object + mode, restore both in finally."""

def _get_armature(self, name):
    """Return (obj, None) for an ARMATURE object, or (None, error_dict) that says what it found instead."""

def _get_mesh(self, name): ...  # same shape as _get_armature
def _addon_enabled(self, module_name): ...  # bpy.context.preferences.addons
```

## 2. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `get_object_info` (addon) | Returns nothing useful for ARMATURE; omits rig data on MESH | ARMATURE: `bones` count, `pose_position`, active `action`, `bone_collections`. MESH: `vertex_groups` names, `shape_keys` names, `armature` (name of the Armature modifier's target), `parent`, `parent_type`. Keep every existing key. |
| `export_object` (addon + server) | `_select_only(obj)` drops the armature and children, so a skinned mesh exports unrigged. FBX defaults add leaf bones. | Add `include_hierarchy: bool = True` (select the object, its parent armature if any, and all children). Add FBX/glTF animation params: `bake_anim` (default True), `add_leaf_bones` (default False), `use_armature_deform_only` (default True), `bake_anim_simplify_factor` (default 0.0), `mesh_smooth_type` (OFF/FACE/EDGE), `primary_bone_axis`, `secondary_bone_axis`, `apply_scale_options`. glTF: `export_animations`, `export_skins`, `export_morph`. Ignore params that do not apply to the chosen format; report which were ignored. |
| `add_keyframe` (addon + server) | Pose bones default to QUATERNION rotation, so keying `rotation_euler` sets nothing visible | Add `bone: str = None`. When set, resolve `obj.pose.bones[bone]`. When the data_path is `rotation_euler` and the bone's `rotation_mode` is a quaternion/axis-angle mode, switch `rotation_mode` to `XYZ` and report it. Same for objects. |
| `import_file` (addon) | Silent about what a rigged import brought in | Add `armatures`, `actions`, `new_actions` to the reply. Accept `.bvh` when the `io_anim_bvh` add-on is enabled (teach-style error otherwise). |
| `capture_viewport_angle` / `capture_contact_sheet` (addon + server) | No way to see bones through a mesh | Add `overlay: str = None` with values `bones_in_front` (armature `show_in_front` + overlay bones on), `wireframe`, `weight_paint` (see `render_weight_map`). Restore all changed overlay/shading/mode state in `finally`. |
| `parent_object` | Unrelated to rig binding but agents will reach for it | Add `parent_type: str = "OBJECT"` accepting `OBJECT`, `BONE` (+ `bone: str`). Leave armature binding to `bind_armature`. |

## 3. New tools

Naming: snake_case, verb first, same as the rest of the surface. Every table row is one
`@mcp.tool()` plus one add-on handler unless marked "server only".

### 3.1 Tier 1, the rig-to-export loop (required for 1.7.0)

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `create_armature` | `name`, `location="0,0,0"`, `display_type="OCTAHEDRAL"` (OCTAHEDRAL/STICK/BBONE/ENVELOPE/WIRE), `show_in_front=True`, `bones: str = None` (same JSON as `add_bones`) | Creates armature data + object, links to the active collection. If `bones` given, calls the `add_bones` path. Reply: name, bone count. |
| `add_bones` | `armature`, `bones` JSON list of `{name, head:[x,y,z], tail:[x,y,z], parent?, connected?:bool, roll?:deg, deform?:bool, inherit_rotation?:bool}` | Batch in one edit-mode session. Parent by name; a parent may be earlier in the same list. `connected=True` snaps head to parent tail and reports the snap. Name collisions get Blender's `.001` suffix, reported. Reply: created names, snapped bones, warnings. |
| `get_armature_info` | `armature`, `include_pose=False`, `space="WORLD"` (WORLD/ARMATURE), `bone_filter: str = None` (glob) | THE perceive tool. Reply: `bones` list in hierarchy order with `name, parent, children, head, tail, length, roll, connected, deform, bone_collections, constraints:[{name,type,target,subtarget}]`, plus `pose_position`, `display_type`, `action`, `bone_collections` summary. `include_pose` adds `pose:{location, rotation_mode, rotation (deg euler or quat), scale, matrix_world_head, matrix_world_tail}` per bone. |
| `set_bone_properties` | `armature`, `bone`, `head`, `tail`, `roll` (deg), `parent`, `connected`, `deform`, `inherit_rotation`, `inherit_scale`, `new_name`, `envelope_distance`, `bbone_segments` | Only sets what was passed. Reply: `set` list, `unset {prop: reason}` (copy the `add_modifier` reply shape). |
| `delete_bones` | `armature`, `bones` (comma list or glob), `reparent_children=True` | Edit mode delete. Reply: deleted names, reparented names. |
| `bind_armature` | `mesh`, `armature`, `method="AUTO"` (AUTO/ENVELOPE/EMPTY_GROUPS/NAME), `keep_transform=True` | `bpy.ops.object.parent_set(type=...)` with the mesh selected and the armature ACTIVE. Verified enum mapping on 4.3.2: AUTO → `ARMATURE_AUTO`, ENVELOPE → `ARMATURE_ENVELOPE`, EMPTY_GROUPS → `ARMATURE_NAME`, NAME → `ARMATURE` (modifier only, keeps existing groups). Reply: modifier name, group count, any group with zero verts. |
| `get_vertex_groups` | `mesh`, `include_stats=True` | Reply per group: `name, index, lock, vertex_count, weight_min, weight_max, weight_mean, has_bone` (matching bone exists on bound armature). |
| `get_vertex_weights` | `mesh`, `indices: str = None`, `group: str = None`, `max_verts=2000` | Per vertex: `{index: {group: weight}}`. Filter by index list or by group. Truncation reported. |
| `set_vertex_weights` | `mesh`, `group`, `weights` JSON `{index: weight}` or list of `[index, weight]`, `mode="REPLACE"` (REPLACE/ADD/SUBTRACT), `create_group=True` | Uses `VertexGroup.add(...)`. `_check_indices`. Reply: written count, group created flag. |
| `render_weight_map` | `mesh`, `group`, `angle="front"`, `max_size=800`, `show_zero_weights=True` | Perceive tool. Switches to WEIGHT_PAINT mode with the group active, captures the viewport with the same `temp_override` path as `capture_viewport_angle`, restores mode in `finally`. Returns image via `_safe_image_return`. |
| `find_unweighted_vertices` | `mesh`, `tolerance=0.001`, `render=False`, `angle="front"` | Verts whose total deform weight is below tolerance, plus verts with any weight > 1 or < 0. Reply: counts + up to 500 indices. `render=True` returns an image with those verts highlighted (select them, edit mode, capture, restore). |
| `set_pose` | `armature`, `bones` JSON `{bone: {location?, rotation?, scale?}}`, `rotation_mode="XYZ"` (XYZ or QUATERNION), `space="POSE"`, `keyframe=False`, `frame=None` | Sets `rotation_mode` per bone before writing. Euler in degrees; quaternion as 4 floats. `keyframe=True` keys location/rotation/scale that were set. Reply: bones written, mode changes. |
| `get_pose` | `armature`, `bones: str = None`, `space="WORLD"` | Per bone: `location, rotation_mode, rotation, scale, head_world, tail_world`. |
| `reset_pose` | `armature`, `bones: str = None`, `transforms="ALL"` (ALL/LOCATION/ROTATION/SCALE) | Clear transforms without needing POSE mode ops (write identity directly). |
| `add_constraint` | `owner` (object name), `bone: str = None`, `constraint_type`, `name: str = None`, `params` JSON | Object or pose-bone owner. `params` keys map to constraint props; object-pointer props accept object names, `subtarget` accepts a bone name. Copy `add_modifier`'s `set`/`unset` reply. Typical types: IK, COPY_LOCATION, COPY_ROTATION, COPY_TRANSFORMS, DAMPED_TRACK, TRACK_TO, STRETCH_TO, LIMIT_ROTATION, LIMIT_LOCATION, CHILD_OF. Error lists valid types. |
| `get_constraints` | `owner`, `bone=None` | List with `name, type, target, subtarget, influence, mute` + type-specific keys (IK: `chain_count, pole_target, pole_subtarget, pole_angle_deg, use_tail`). |
| `remove_constraint` | `owner`, `bone=None`, `name` | |
| `set_keyframes` | `target` (object), `bone=None`, `data_path`, `keys` JSON list of `[frame, value]` or `{frame, value, interpolation?, easing?}`, `replace=True` | Batch keys in one call. Value shape rules identical to `add_keyframe`. `interpolation` in BEZIER/LINEAR/CONSTANT, `easing` AUTO/EASE_IN/EASE_OUT/EASE_IN_OUT. Sets rotation_mode the same way as `add_keyframe`. Reply: keyed count, action name, fcurve path. |
| `get_animation_info` | `target: str = None` (None = scene summary), `include_keys=False`, `max_keys=200` | Scene: fps, frame_start/end, current, actions list with users. Object: `action`, `fcurves:[{data_path, index, keyframe_count, frame_range}]`, `nla_tracks`, `shape_key_action`. `include_keys` adds `[frame, value, interpolation]` per fcurve up to `max_keys`. |
| `set_scene_frame_range` | SUPERSEDED as a tool (Lead ruling 2026-09-11): exists only as an add-on-side alias of the shared handler, no server tool. The shared `set_frame_range` from 2.1.0 (settings doc, PLAN C11) is the one name on both sides and accepts both spellings (`start/end/current` and `frame_start/frame_end/current_frame`, plus `frame_step`). | Only sets what was passed. |
| `playblast` | `start=None`, `end=None`, `step=None`, `frames: str = None` (explicit list), `camera: str = None`, `max_size=640`, `columns=4`, `video_path: str = None`, `overlay: str = None` | Perceive tool for motion. Captures each frame with the viewport OpenGL path (`bpy.ops.render.opengl` under the same `temp_override` as `capture_viewport_angle`) or, if there is no VIEW_3D area (background mode), an EEVEE render of the active camera. Server composes a labelled contact sheet with `_compose_grid` and returns it via `_safe_image_return`. `video_path` additionally writes an MP4 (`render.opengl(animation=True)` with FFMPEG settings saved and restored). Auto step: choose the largest step that keeps `<= 16` tiles unless `step`/`frames` given. Restores `frame_current`. |
| `bake_action` | `armature`, `start=None`, `end=None`, `step=1`, `bones: str = None`, `visual_keying=True`, `clear_constraints=False`, `clear_parents=False`, `only_selected=True`, `bake_types="POSE"` (POSE/OBJECT/both) | `bpy.ops.nla.bake` in POSE mode with the listed bones selected (all if None). Reply: action name, frame range, fcurve count. Restore mode and selection. |

### 3.2 Tier 2, productivity (target 1.7.0 if time allows, else 1.8.0)

| Tool | Parameters | Behaviour |
|---|---|---|
| `setup_ik_chain` | `armature`, `tip_bone`, `chain_length=2`, `target_name=None`, `pole_name=None`, `pole_offset=0.5`, `pole_angle=None`, `target_bone=True` | One call: creates a target (control bone by default, or an Empty if `target_bone=False`) at the tip's tail, a pole control offset from the chain's bend plane, adds the IK constraint. Computes `pole_angle` automatically if None using the standard bend-plane method. Reply: created names, constraint name, computed pole angle. |
| `add_rigify_metarig` | `metarig_type="human"` (human/basic_human/basic_quadruped/bird/cat/horse/shark/wolf), `name=None`, `location`, `scale=1.0` | Requires the `rigify` add-on; teach-style error if disabled. |
| `generate_rigify_rig` | `metarig`, `rig_name=None` | Runs the generator, returns the generated rig name and the error log text if generation fails. |
| `mirror_bones` | `armature`, `bones=None`, `direction="POSITIVE_X"` | `bpy.ops.armature.symmetrize` with the L/R naming rules explained in the docstring. |
| `rename_bones` | `armature`, `pattern`, `replacement`, `regex=False`, `dry_run=False`, `rename_vertex_groups=True` | Batch rename, keeps bound meshes' vertex groups in sync. `dry_run` returns the mapping only. |
| `normalize_weights` | `mesh`, `lock_active=False`, `all_groups=True` | `vertex_group_normalize_all` |
| `clean_weights` | `mesh`, `limit=0.001`, `keep_single=True` | `vertex_group_clean` |
| `smooth_weights` | `mesh`, `group=None`, `factor=0.5`, `repeat=1` | `vertex_group_smooth` (needs WEIGHT_PAINT mode; restore) |
| `limit_weights` | `mesh`, `limit=4` | `vertex_group_limit_total`, for game engines |
| `transfer_weights` | `source_mesh`, `target_mesh`, `method="NEAREST"` (NEAREST/INTERPOLATED/PROJECTED), `apply=True` | Data Transfer modifier, vertex groups only, `use_object_transform=True`, applied unless told otherwise. |
| `get_shape_keys` | `mesh` | Names, values, min/max, relative key, mute, driver present. |
| `add_shape_key` | `mesh`, `name`, `from_mix=False` | |
| `set_shape_key_value` | `mesh`, `name`, `value`, `keyframe=False`, `frame=None` | |
| `get_vertex_positions` / `set_vertex_positions` (existing) | add `shape_key: str = None` | Read/write a shape key's coordinates instead of the basis mesh. |
| `add_driver` | `target`, `data_path`, `index=-1`, `expression`, `variables` JSON `[{name, type:SINGLE_PROP/TRANSFORMS/ROTATION_DIFF/LOC_DIFF, id, id_type?, data_path?, bone?, transform_type?, space?}]`, `bone=None` | Reply: driver fcurve path, variables created. |
| `get_drivers` | `target` | |
| `remove_driver` | `target`, `data_path`, `index=-1` | |
| `delete_keyframes` | `target`, `bone=None`, `data_path=None`, `frames: str = None`, `start=None`, `end=None` | Range or explicit list; `data_path=None` = all fcurves. |
| `set_keyframe_interpolation` | `target`, `data_path=None`, `interpolation`, `easing=None`, `frames=None` | |
| `list_actions` | | Name, users, frame range, fcurve count, fake user. |
| `create_action` / `assign_action` / `duplicate_action` / `rename_action` / `delete_action` | as named | `assign_action(target, action=None)` with None unassigning. |
| `add_nla_strip` | `target`, `action`, `track=None`, `start=None`, `blend_in=0`, `blend_out=0`, `extrapolation="HOLD"`, `repeat=1` | Creates track if missing. |
| `get_nla_tracks` | `target` | Tracks with strips. |
| `push_down_action` | `target` | Current action to a new NLA track. |
| `get_bone_trajectory` | `armature`, `bone`, `start=None`, `end=None`, `step=1`, `point="HEAD"` (HEAD/TAIL) | World position per frame plus per-frame speed. Numeric foot-slide check. Restore `frame_current`. |
| `apply_pose_as_rest` | `armature`, `also_apply_to_meshes=True` | `pose.armature_apply`; when meshes are bound, applies the Armature modifier copy first so the mesh follows (standard workaround). |

### 3.3 Tier 3, later (do NOT build unless asked)

`retarget_animation(source, target, bone_map JSON)`, `import_animation(filepath, target_armature)` for mocap FBX/BVH with root-motion options, bendy-bone tooling.

## 4. Ripple points (every one must be touched)

1. `addon.py`: handlers + `extended_handlers` registration + new helpers + `bl_info` version.
2. `src/blender_mcp/server.py`: `@mcp.tool()` wrappers; new section comment banners
   `# ─── Rigging ───` and `# ─── Animation ───` (extend the existing Animation banner).
3. `TOOLS.md`: new sections "Rigging: Armatures & Bones", "Rigging: Skinning & Weights",
   "Rigging: Pose & Constraints", "Shape Keys & Drivers", extend "Animation". Update the
   tool count in the header and the Table of Contents.
4. `README.md`: tool count, category table rows, the "What's new" list.
5. `pyproject.toml`: version 1.7.0.
6. `C:\Users\Naabin\.claude\commands\blender.md` (the `/blender` skill): add a "Rigging"
   and an "Animation" workflow pattern, add the tools to the quick-reference table, and
   REMOVE "armature rigging" and "shape keys" from the execute_blender_code fallback line.
7. Auto-memory `project_blender_mcp.md`: append a "Session: rigging and animation" block
   with the new tool inventory and any new wire-protocol facts.
8. MemPalace `main` db: one decisions drawer summarising design choices and gotchas hit.

## 5. Blender 4.3 gotchas known before you start

Re-verified on 4.3.2 and 5.2.1 on 2026-09-11 with `apichecks/b1check.py` and `b1check2.py`
(a real 2-bone armature + skinned cube; lines under `### b1check.py` / `### b1check2.py` in
`apichecks/out_*.jsonl`, Planner/API evidence, PLAN.md K1-K23). Every bullet below still holds on both versions unless annotated
`[5.2.1: ...]`; section 5.1 lists the per-tool switches.

- `EditBone` references are invalid after leaving EDIT mode. Return names. [Measured: a kept
  `EditBone` read after `mode_set('OBJECT')` is UNDEFINED on both versions: garbage (utf-8
  decode error on `.name`) on 4.3.2 and in one of two 5.2.1 runs, the old name in the other
  5.2.1 run (Planner + Verifier, 2026-09-11). Never rely on it.]
- `bpy.ops.object.parent_set(type='ARMATURE_AUTO')` needs the mesh SELECTED and the
  armature ACTIVE, in OBJECT mode. `parent_object` bypasses ops and cannot do this.
- Pose bones default to `rotation_mode='QUATERNION'`. Writing `rotation_euler` without
  changing the mode is silently ignored in the viewport.
- `bpy.ops.nla.bake` needs POSE mode and selected bones; it also needs
  `bpy.context.view_layer.objects.active` to be the armature.
- `bpy.ops.render.opengl` needs a VIEW_3D area override; there is none in
  `--background`. Detect with the same area search `get_viewport_screenshot` uses and
  fall back to a camera render.
- WEIGHT_PAINT mode requires the mesh active; the armature may be selected too for
  bone-click behaviour but is not needed for capture. Set
  `mesh.vertex_groups.active_index` for the displayed group.
- Rigify and BVH import are add-ons: check `bpy.context.preferences.addons.keys()`
  for `rigify` / `io_anim_bvh`. Verified on 4.3.2 factory startup: `io_anim_bvh` is
  enabled, `rigify` is available but NOT enabled. Enable with
  `addon_utils.enable("rigify", default_set=True)` when the caller asks, else teach.
- Rigify WORKS HEADLESS on both versions (b1check2): `addon_utils.enable("rigify",
  default_set=True)`, `armature_human_metarig_add` (159 bones), `rigify.generate.generate_rig
  (context, metarig)` gives `rig` with 706 bones and leaves OBJECT mode; all 8 metarig ops
  present; `get_rigify_target_rig` importable.
- Rigify errors are NOT readable through `bpy.ops.pose.rigify_generate`: the operator
  catches `MetarigError` and any `Exception` and routes them to `self.report()`, which a
  script cannot read. Call the generator directly instead:
  `from rigify import generate; generate.generate_rig(bpy.context, metarig_obj)` inside
  `try/except Exception as e` and return `str(e)` plus `traceback.format_exc()`. Set the
  metarig active and in OBJECT mode first, and `mode_set(mode='OBJECT')` in `finally`
  (the operator does the same). The generated rig is
  `rigify.utils.rig.get_rigify_target_rig(metarig.data)`.
- Vertex groups are on the OBJECT, weights are on `MeshVertex.groups` (data). Use
  `VertexGroup.add(indices, weight, mode)` and `VertexGroup.weight(index)` (raises
  when the vertex is not in the group; catch and treat as 0).
- Bone collections replaced armature layers in 4.0: `armature.collections` (4.0)
  became `armature.collections_all` in 4.1+. Guard with `getattr`.
- [5.2.1 ONLY] The legacy Action API is gone: `action.fcurves`, `action.groups`,
  `action.id_root` raise `AttributeError`. Actions are slotted: `action.slots`,
  `action.layers[0].strips[0].channelbags`, `animation_data.action_slot`,
  `action_suitable_slots`, `last_slot_identifier`; F-curves are read through
  `bpy_extras.anim_utils.action_get_channelbag_for_slot` /
  `action_ensure_channelbag_for_slot` and `channelbag.fcurves` (same `data_path`,
  `array_index` and keyframe counts as 4.3.2's `action.fcurves`; `channelbag.fcurves.find
  (data_path, index=)` and `channelbag.groups` work). Neither helper exists on 4.3.2.
  `keyframe_insert`, `action.frame_range`, `bake_action(obj, *, action, frames,
  bake_options)` and `BakeOptions` are the same on both.
- [5.2.1 ONLY] Assigning `animation_data.action` on a fresh object leaves `action_slot`
  None while `action_suitable_slots` lists the slot (`OB<name>`): `assign_action` must set
  `ad.action_slot = ad.action_suitable_slots[0]` (hasattr-guarded) or nothing animates.
- Bone selection moved: `Bone.select` / `select_head` / `select_tail` exist on 4.3.2 only;
  `PoseBone.select` and `PoseBone.hide` exist on 5.2.1 only (`Bone.hide` / `hide_select`
  on both). Rule: `pb.select = x if hasattr(pb, "select") else pb.bone.select = x`.
  `nla.bake` finishes headless in POSE mode on both once bones are selected that way.
- FBX exporter kwargs: `add_leaf_bones`, `bake_anim`, `bake_anim_simplify_factor`,
  `use_armature_deform_only`, `mesh_smooth_type`, `primary_bone_axis`,
  `secondary_bone_axis`, `apply_scale_options`. glTF: `export_animations`,
  `export_skins`, `export_morph`, `export_def_bones`.
- Every image reply goes through `_safe_image_return`.
- Verified headlessly on 4.3.2 (2026-09-06), so do not re-derive these: `armature.collections`
  AND `collections_all` both exist; `bpy.ops.nla.bake` props are `frame_start, frame_end,
  step, only_selected, visual_keying, clear_constraints, clear_parents, use_current_action,
  clean_curves, bake_types, channel_types`; `armature.symmetrize` takes only `direction` [5.2.1: also `copy_bone_colors`; passing it
  on 4.3.2 raises "keyword unrecognized": try/except TypeError and retry without];
  `object.vertex_group_normalize_all / _clean / _smooth / _limit_total` all exist;
  `pose.armature_apply` exists; `render.opengl` props are `animation, render_keyed_only,
  sequencer, write_still, view_context`; the FBX and glTF kwargs listed above all exist;
  new pose bones report `rotation_mode == 'QUATERNION'`.

- Verifier notes from the B1-G3 harness (185/185 on both installs, addon `37d2211d`), recorded as-is
  (Lead ruling 14:03): `capture_contact_sheet` headless returns a SUCCESS envelope whose per-image
  entries carry the "Viewport capture needs a GUI session" error (the sheet itself is not an
  error reply); `create_armature` keeps the caller's active object (it does not leave the new
  armature active).

### 5.1 4.x/5.x switches per tool (B1-G0, 2026-09-11, `b1check.py` on both installs)

| Tool | Switch | 4.3.2 | 5.2.1 |
|---|---|---|---|
| `get_animation_info`, `set_keyframes`, `delete_keyframes`, `set_keyframe_interpolation`, `list_actions`, NLA tools | F-curve access | `action.fcurves` / `action.groups` | `channelbag.fcurves` / `channelbag.groups` via `anim_utils.action_get_channelbag_for_slot` (`action.fcurves` raises) |
| `assign_action` | slot binding | no slots | set `animation_data.action_slot = action_suitable_slots[0]` after assigning, else nothing animates |
| `create_action`, `assign_action` (fresh action) | slot creation | no slots API | a fresh action has NO slots and `action_suitable_slots` is EMPTY: `action.slots.new(id_type='OBJECT', name=...)` makes `OB<name>`, then assign `action_slot` explicitly (still None otherwise); `keyframe_insert` then lands in that slot (K17) |
| `set_keyframes` (F-curve creation) | write path | `action.fcurves.new(data_path, index=, action_group=)` | `channelbag.fcurves.new(data_path, index=, group_name=)` (`action_group` rejected). `keyframe_points.insert(frame, value)`, `fc.evaluate/update/find`, interpolation and easing enums identical on both (K18) |
| `add_nla_strip`, `get_nla_tracks`, `push_down_action` (Tier 2, L4) | NLA API | `nla_tracks.new()`, `strips.new(name, start, action)` but the NAME ARGUMENT IS IGNORED (strip named after the action): set `strip.name` afterwards | same API honours the name; strips carry `action_slot`. Extrapolation NOTHING/HOLD/HOLD_FORWARD, blend_type REPLACE/COMBINE/ADD/SUBTRACT/MULTIPLY, `nla.action_pushdown(track_index)` on both (K19) |
| `get_pose`, `set_pose`, `get_bone_world` | pose math | identical: `pb.head/tail` are armature-local, world = `matrix_world @ head`; `(matrix_world @ pb.matrix).translation` == world head; a pose value written on an ANIMATED bone is overwritten at the next depsgraph update unless keyframed (`set_pose` warns) | same (K20) |
| weight tools (`smooth_weights`, `normalize_weights`, `clean_weights`, `transfer_weights`) | modes | identical: WEIGHT_PAINT `mode_set` works headless with the mesh active; `vertex_group_smooth` needs WEIGHT_PAINT (poll fails in OBJECT), `normalize_all/clean/limit_total` run in either; `group_select_mode` enum reads empty headless but `ALL` is accepted; Data Transfer modifier + `datalayout_transfer` + apply copies vertex groups | same (K21) |
| `generate_rigify_rig`, `add_metarig` (Tier 2, L4) | headless | works (see bullet above) | same (K22) |
| `bake_action` | helper | `anim_utils.bake_action(obj, *, action, frames, bake_options)` with `BakeOptions` (12 bools) runs headless and returns an action | same (K23) |
| every tool that selects bones (`nla.bake`, pose ops, `select_bones`) | selection attribute | `pose_bone.bone.select` | `pose_bone.select` (`Bone.select` gone); `PoseBone.hide` new |
| tools that create or rename bones in EDIT mode | reference lifetime | kept `EditBone` reads garbage after mode exit | undefined (garbage in one run, the old name in another); return names on both |
| `symmetrize_armature` | kwargs | `direction` only | `direction`, `copy_bone_colors` (retry without on TypeError). A bone with no `.R`/`.L` twin leaves the count unchanged on both |
| `bind_armature` (`parent_set ARMATURE_AUTO`) | identical | FINISHED: parent set, ARMATURE modifier, one vertex group per bone, mode OBJECT, armature active | same; enum identical |
| `export_object` FBX | `mesh_smooth_type` | `OFF` / `FACE` / `EDGE` | adds `SMOOTH_GROUP` (raises on 4.3.2): validate against the live enum. All doc kwargs present and a real export finishes on both |
| `export_object` glTF | kwargs | all doc kwargs present, GLB export finishes | same; `export_vertex_color` adds `NAME`; `export_format` enum reads EMPTY headless on both (try-assign) |
| `import_file` glTF | new-object report | re-import brings back the armature, the mesh, one action PLUS an `Icosphere` MESH (the importer's bone custom-shape object) | same; the report must expect it |
| `import_file` BVH | ops | `import_anim.bvh(filepath, target, global_scale, frame_start, use_fps_scale, ...)`, `export_anim.bvh`; `io_anim_bvh` enabled in factory prefs | same |
| `playblast`, `render_turntable` | `render.opengl` | `poll()` True but raises "Cannot use OpenGL render in background mode"; `screenshot_area.poll()` False | same: live session only |
| shape keys, drivers, pose constraints (COPY_ROTATION, IK with pole and chain), pose ops (`armature_apply(selected)`, `transforms_clear`, `select_all`, `copy`/`paste`, `*_clear`, `visual_transform_apply`), vertex-group ops (`normalize_all`, `clean`, `smooth`, `limit_total`, `mirror`; `remove_unused` is NOT an op), `VertexGroup.add(index, weight, type)`, `Armature.display_type`, `pose_position`, bone collections (`collections.new` + `assign`), pose `rotation_mode` default QUATERNION | identical on both | | |

## 6. Testing (required before you report done)

Run headless against the installed Blender:

```
"D:\blender-4.3.2-windows-x64\blender.exe" --background --factory-startup --python <test script>
```

The test script imports `addon.py`, instantiates `BlenderMCPServer`, and calls handlers
directly (no socket). Minimum scenario, in order:

1. `create_armature` with a 3-bone arm chain via `bones` JSON (upper, fore, hand).
2. `get_armature_info` shows 3 bones, correct parents, `connected` on fore and hand.
3. `add_primitive` cylinder, `bind_armature` AUTO, `get_vertex_groups` shows 3 groups
   with non-zero counts.
4. `find_unweighted_vertices` returns 0 unweighted.
5. `set_vertex_weights` REPLACE on 5 verts, `get_vertex_weights` reads them back.
6. `add_constraint` IK on hand with `chain_count=2` and a target Empty; `get_constraints`
   reads it back.
7. `set_pose` rotation on upper, `get_pose` reflects it, `reset_pose` clears it.
8. `set_keyframes` 3 keys on the target Empty's location, `get_animation_info` lists them.
9. `bake_action` on the armature, `get_animation_info` shows fcurves on all 3 bones.
10. `export_object` FBX with `include_hierarchy=True`; re-import into an empty scene and
    assert an ARMATURE object and an action came back.
11. `render_weight_map` and `playblast` are viewport tools: test them in a live Blender
    session via the MCP, not headless. Report both images were returned and not blank
    (check the PNG is not a single colour).

Report results per tool as PASS / FAIL with the error text. Do not weaken an assertion to
make it pass; report the failure.

## 7. Deploy (only after tests pass and the user says to deploy)

Copy with `shutil` (robocopy/xcopy silent-fail in bash):

| Source | Target |
|---|---|
| `addon.py` | `%APPDATA%\Blender Foundation\Blender\4.3\scripts\addons\addon.py` (delete its `__pycache__\addon.cpython-311.pyc`) |
| `addon.py`, `src/blender_mcp/server.py`, `pyproject.toml`, `TOOLS.md`, `README.md` | `B:\-AI-Stuff-\-=MCP-Servers=-\blender_mcp\` (THE live registration) |
| same set | `D:\blender-4.3.2-windows-x64\MCP\` (project-scoped registration used by unity_mcp) |

Then `uv pip install -e .` in each target venv and verify with
`python -c "import blender_mcp.server as s; print(s.__file__)"` that `src/` is what loads.
Cycle the add-on off/on in Blender (it auto-restarts the socket server). The MCP server
needs a Claude Code session restart to pick up new tools.

Do not launch Blender or deploy without being told; build and test headless only.
