# Migration viability: Blender 4.3.2 → 5.2 LTS

Status: PHASE A HEADLESS COMPLETE 2026-09-11 on branch `blender-5.2` (cut from `main` @
`ef0ff7d`, team nuzukm); the accepted add-on is `addon.py` git hash-object
`e0329ced83ca1b74e12d0c5b2caaab65c872b94e` (server.py `c080dfeb`, `__init__.py` `01a8fd68`),
harness 134/134 on 4.3.2 and 5.2.1. The live pass (M14) was run 2026-09-11 by the live-test agent on 5.2.1 GUI at addon
`f4dc37b1` (`docs/LIVE-TEST-REPORT-2026-09-11.md`): PASS; outcomes are under each block below.
G5 = the v2.0.0 tag gate (one tag v2.2.0 per the release ruling). The
working tree has moved on to 2.1.0 (Phase B0). Assessment written 2026-09-09.
Current: Blender 4.3.2 (portable, `D:\blender-4.3.2-windows-x64\`), not an LTS, no fixes
since early 2025
Target: **Blender 5.2 LTS** (5.2.0 released 2026-07-14, 5.2.1 corrective 2026-08-25,
supported until July 2028). 5.3 lands November 2026 and is not LTS.

Sources read (official pages, fetched as plain HTML): the Python API release notes for
4.4, 4.5, 5.0, 5.1 and 5.2, the 5.0 EEVEE and Physics notes, the 5.2 Physics notes, the
Compatibility Changes index, and the release download index (`Blender5.2/` present).
Third-party summaries were used only to locate those pages.

## Verdict

Viable, with a small mandatory fix list. The fork's 92 tools hit exactly four hard breaks,
all in the add-on and all mechanical. The larger cost is not the current code but the
seven feature-request docs: every "verified on 4.3.2" section must be re-verified on 5.2
because several of the APIs they rely on were renamed or restructured in 5.0 and 5.2. The
check scripts used for that verification are preserved in `docs/apichecks/` so the
re-verification is a re-run and a diff, not a rewrite.

Recommendation: migrate straight to 5.2 LTS, skip 4.4, 4.5 and 5.0/5.1 as stepping
stones, keep 4.3.2 installed side by side (portable zip, no installer), and do the
migration BEFORE building the feature requests so nothing is built against 4.3-only APIs.

## Hard breaks in the current code (must fix before 5.x)

(Line numbers as of `ef0ff7d`; function names are the anchors. Status column added at
A1 acceptance, 2026-09-11, hash `3c726b58e66101a2bc37d18f775c3e73ad8af20e`; A1.1 accepted the same day on `e0329ced83ca1b74e12d0c5b2caaab65c872b94e`, the Phase A add-on.)

| # | Where | What breaks | Since | Fix | Status |
|---|---|---|---|---|---|
| 1 | `addon.py` ~2437-2456 (`set_render_settings`) and ~761 (`render_depth_map` engine fallback) | The code maps `BLENDER_EEVEE` → `BLENDER_EEVEE_NEXT` for any Blender ≥ 4.0. In 5.0 the identifier was renamed BACK to `BLENDER_EEVEE`; assigning `BLENDER_EEVEE_NEXT` raises a TypeError (invalid enum). | 5.0 | Resolve by assignment: try `BLENDER_EEVEE`, then `BLENDER_EEVEE_NEXT`; never branch on version. The RNA enum list still does not include add-on engines (same blind spot as Cycles). | Fixed in 2.0.0. Verified on 4.3.2 and 5.2.1: A1 (`3c726b58e661`, Lead 20/20 + 21/21) and A1.1 acceptance (`addon.py` git hash-object `e0329ced83ca`; Verifier's full harness 134/134 on 4.3.2, 134/134 on 5.2.1; A1.1 accepted 2026-09-11). |
| 2 | `addon.py` ~774-775 (`render_depth_map`) | `tmp.use_nodes = True` then `tmp.node_tree` on the temp scene. `Scene.node_tree` was REMOVED in 5.0; `use_nodes` is a no-op. AttributeError. | 5.0 | `tree = bpy.data.node_groups.new("mcp_depth", "CompositorNodeTree")`, `tmp.compositing_node_group = tree`, add a `NodeGroupOutput` with an Image socket via `tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")`. Also verify the node ids used (Map Range, Invert): 5.0 replaced many compositor nodes with their Shader counterparts (`CompositorNodeGamma` → `ShaderNodeGamma`); resolve by trying the Compositor id then the Shader id. Remove the node group in `finally`. Measured 2026-09-11: `ShaderNodeMapRange` works inside a `CompositorNodeTree` on 5.2.1 and its output socket is `Result` (4.3 `CompositorNodeMapRange`: `Value`); the add-on resolves both socket names. | Fixed in 2.0.0. Verified on 4.3.2 and 5.2.1: A1 (`3c726b58e661`, Lead 20/20 + 21/21) and A1.1 acceptance (`addon.py` git hash-object `e0329ced83ca`; Verifier's full harness 134/134 on 4.3.2, 134/134 on 5.2.1; A1.1 accepted 2026-09-11). |
| 3 | `addon.py` ~2397-2415 and `server.py` ~3302 (`boolean_operation`) | Solver value `FAST` was renamed `FLOAT` in 5.0 (modifier and operator). Passing `FAST` raises. | 5.0 | Accept both, map `FAST` → `FLOAT` when the enum lacks `FAST`; error text lists the live enum. Measured 2026-09-11 (Ton's baseline harness, untouched add-on): the modifier's solver enum is `('FAST', 'EXACT')` on 4.3.2 and `('FLOAT', 'EXACT', 'MANIFOLD')` on 5.2.1; `MANIFOLD` is a 5.2 addition and is passed through where the live enum has it. | Fixed in 2.0.0. Verified on 4.3.2 and 5.2.1: A1 (`3c726b58e661`, Lead 20/20 + 21/21) and A1.1 acceptance (`addon.py` git hash-object `e0329ced83ca`; Verifier's full harness 134/134 on 4.3.2, 134/134 on 5.2.1; A1.1 accepted 2026-09-11). |
| 4 | `addon.py` 530, 709, 805, 1571, 2449 (every `image_settings.file_format = ...`) | 5.0 added `ImageFormatSettings.media_type`, which "needs to be set to an appropriate type before setting file_format". Stills default to IMAGE so PNG likely still works, but FFMPEG output (the requested playblast and turntable video) will fail unless `media_type = 'VIDEO'` is set first. | 5.0 | Set `media_type` before `file_format`, guarded by `hasattr` for 4.x. Live enum on 5.2.1 (measured 2026-09-11): `IMAGE`, `MULTI_LAYER_IMAGE`, `VIDEO` (not `MULTI_LAYER` as first assumed). Assignment matrix on 5.2.1 (migcheck `media_matrix`): under `IMAGE`, `PNG` / `OPEN_EXR` and the other still formats assign while `OPEN_EXR_MULTILAYER` and `FFMPEG` raise; `MULTI_LAYER_IMAGE` accepts only `OPEN_EXR_MULTILAYER`; `VIDEO` accepts only `FFMPEG`; 4.3.2 assigns every format directly. The add-on therefore maps per format: `FFMPEG` -> `VIDEO`, `OPEN_EXR_MULTILAYER` -> `MULTI_LAYER_IMAGE`, everything else -> `IMAGE`, and `_render_settings` restores `media_type` BEFORE `file_format` (restoring `PNG` while `VIDEO` is set raises). `Image.file_format` on image datablocks (the resize helper) is not affected. Measured 2026-09-11 (baseline harness): a PNG still succeeds headless on 5.2.1 without touching `media_type` (it defaults to `IMAGE`), so this break bites only video output; FFMPEG is checked in the live session (checklist L2). | Fixed in 2.0.0. Verified on 4.3.2 and 5.2.1: A1 (`3c726b58e661`, Lead 20/20 + 21/21) and A1.1 acceptance (`addon.py` git hash-object `e0329ced83ca`; Verifier's full harness 134/134 on 4.3.2, 134/134 on 5.2.1; A1.1 accepted 2026-09-11). |
| 5 | `addon.py` `set_texture` (PolyHaven textures, ARM-map branch; line 3302 on the A1 file) | `nodes.new(type='ShaderNodeSeparateRGB')`: the legacy Separate RGB node type is undefined on 5.x (`Error: Node type ShaderNodeSeparateRGB undefined`), so every PolyHaven texture that ships an `arm` map fails. `ShaderNodeMixRGB` (same handler) still exists on 5.2.1, deprecated but working; every other `nodes.new()` id in the add-on exists on both installs (Lead's sweep). | 5.0 | Resolve with the existing `_new_node` try-list: `ShaderNodeSeparateRGB` then `ShaderNodeSeparateColor` (outputs `R`/`G`/`B` become `Red`/`Green`/`Blue`). Found 2026-09-11 by the code-comment audit (headless probe on both installs); PolyHaven-gated, so the 85-handler headless pass could not reach it. Planner's SEPCHK evidence (both installs): `ShaderNodeSeparateRGB` exists only on 4.3.2 (outputs `R`/`G`/`B`); `ShaderNodeSeparateColor` exists on both (input `Color`, outputs `Red`/`Green`/`Blue`, mode `RGB`); `CombineRGB`/`CombineColor` follow the same pattern; `ShaderNodeMixRGB` is still present on 5.2.1. Ruled form: try-list SeparateRGB then SeparateColor with outputs read by name `R|Red` etc. | Fixed in 2.0.0 (A1.1, `addon.py` git hash-object `e0329ced83ca`; Verifier's full harness 134/134 on 4.3.2, 134/134 on 5.2.1; accepted 2026-09-11). |

Verified NOT affected: no dict-style access to `bpy.props` data (`scene['...']`) anywhere
in the add-on, so the 5.0 IDProperties split is harmless; `requests` (the add-on's only
third-party import) is bundled in Blender's own Python (4.3 ships 2.27.1 beside numpy,
certifi, urllib3, and 5.x keeps it for the extensions system; confirm on the 5.2 install);
`bl_info` legacy add-ons still load in 5.2 (the extensions platform since 4.2 keeps a
legacy path); `temp_override`, `bpy.app.timers`, `driver_namespace`, `screenshot_area`,
`libraries.load`, `mode_set`, the OBJ/STL/PLY `wm.*` operators, glTF/FBX exporters, BMesh
crease/bevel layers, `shade_smooth_by_angle`, `surface_render_method` guards: no rename
or removal listed for any of them through 5.2.

Deprecated but still working (clean up during the migration, removal is 6.0):
`material.use_nodes = True` (874, 881, 2221, 2293, 2786, 3012), `world.use_nodes`
(1730, 2666). Materials and worlds now get a node tree on creation.

## Runtime and platform changes to plan for

- **Python 3.13** inside Blender from 5.1 (was 3.11). `addon.py` is plain Python and
  should run unchanged; the MCP server's own venv is independent. The deployed
  `D:\blender-4.3.2-windows-x64\MCP\.venv` (3.11.9) is a separate interpreter and is
  unaffected, but if a new portable `D:\blender-5.2.1-windows-x64\` gets its own `MCP\`
  copy, create its venv with `uv` as before.
- **mathutils now exposes float32 buffers** (5.0). `numpy.array(vector)` yields float32
  and matrices are non-contiguous; every numpy path in the feature requests (UV checks,
  VAT baking, compare_meshes) must pass `dtype=numpy.float64` explicitly or use
  `foreach_get` into preallocated float64 arrays.
- **File format is one-way.** Files saved by 5.x open only in 4.5 or newer; 4.3.2 will
  report them invalid. Compression is on by default in 5.0. Keep 4.3.2 for old files;
  never save a 4.3 project from 5.2 without a copy.
- **Data-block names up to 255 bytes** (5.0). Any name-length assumptions in tools are
  wrong; none exist today.
- **GPU minimum** NVIDIA Maxwell or newer, macOS 13+, no Intel Mac builds. The RTX 4090
  workstation is fine.
- **Collada import/export removed** (5.0). Not used by the fork.
- **Render pass names changed** (5.0): `Z` → `Depth`, `DiffCol` → `Diffuse Color`, etc.
  Any compositor code addressing `RLayers.outputs["Z"]` must use `"Depth"` (or try both).
- **Legacy Action API removed** (5.0, after slotted actions in 4.4): `action.fcurves`,
  `action.groups`, `action.id_root` are gone; use `bpy_extras.anim_utils`
  `action_get_channelbag_for_slot` / `action_ensure_channelbag_for_slot` and
  `channelbag.fcurves`. Assigning an action may not animate anything until
  `animation_data.action_slot` is set. `keyframe_insert` is unchanged.
- **Point caches** are always compressed (5.0); `PointCache.compression` removed.
- **Blender 5.2 experimental Geometry-Nodes physics** (Cloth Dynamics, Hair Dynamics,
  XPBD solver, Collider modifier, effector bundles). Legacy cloth, soft body, rigid body,
  particles and Mantaflow remain; the physics notes for 5.0 and 5.1 list no removal of
  particle hair (a third-party article claimed one; the official notes do not).

## Feature-request docs that need re-verification on 5.2

| Doc | Items invalidated or changed |
|---|---|
| rigging-and-animation | `get_animation_info`, `set_keyframes`, `delete_keyframes`, `set_keyframe_interpolation`, `list_actions`, NLA tools: rewrite on channelbags and slots. `bake_action` unchanged. Pose bone `select` and `hide` moved (5.0): `pose.bones[i].select` / `.hide` exist, `Bone.select*` removed. |
| uv-and-texturing | `MeshUVLoopLayer.vertex_selection` and `edge_selection` REMOVED (5.0); UV selection is shared across maps with new `.uv_select_vert/edge/face` attributes and BMesh `uv_select_*` methods. `pin` no longer auto-creates its attribute: call `pin_ensure()` before writing. `material.use_nodes` deprecated. `scene.use_nodes` for bake-related compositor work: use `compositing_node_group`. |
| node-graphs | Modifier inputs: `modifier["Socket_0"]` was replaced in **5.2** by `modifier.properties.inputs.<identifier>.value`, `.type = "ATTRIBUTE"`, `.attribute_name`, and `modifier.properties.outputs.<identifier>.attribute_name`. Every RNA path to these changed (compat page). `assign_node_group`, `get/set_modifier_inputs`, `add_modifier` NODES support and the `Socket_N` facts must be rewritten with a 4.x/5.2 switch. File Output node: `file_slots`, `layer_slots`, `base_path` REMOVED; use `directory`, `file_name`, `file_output_items`. Compositor node ids partly replaced by `ShaderNode*` ids; Color node output renamed `RGBA` → `Color`; Compare and Random Value socket identifiers changed (5.2); `scene.node_tree` gone. `SpaceNodeEditor` props renamed (not used). Interface items can be looked up by identifier (5.0, helpful). Node panels can be opened/closed from Python (5.2). |
| simulation-and-vfx | `PointCache.compression` gone (drop from `set_point_cache`). `wm.alembic_export(visible_objects_only=)` REMOVED; `wm.usd_export(export_textures=)` REMOVED in favour of `export_textures_mode`, `allow_unicode` now defaults True; `wm.usd_import` renamed `import_subdiv` → `import_subdivision`, `attr_import_mode` → `property_import_mode`. Render passes for flipbooks: `Depth` not `Z`. Consider offering the 5.2 GN cloth/hair dynamics as an alternative backend in Tier 2. |
| engine-readiness | Boolean solver `FLOAT`. glTF and FBX kwargs: no listed changes through 5.2, re-verify anyway. Collada gone (never offered). |
| settings-save-load | `Preferences.filepaths.active_asset_library` index no longer matches `asset_libraries` (5.2 adds "All Libraries" and "Essentials" at indices 0 and 1). Theme API rewritten (Tier 3 only). Compression default changed (document in `save_blend`). |
| presentation-and-library | `Window.screenshot()` (5.2) returns pixels without a file: better path for every capture tool. `gpu.init()` (5.2) initialises the GPU backend in `--background` but does not make `render.opengl`, `screenshot_area` or `Window.screenshot()` work headless (measured 2026-09-11, see Opportunities); viewport captures and playblasts still need a GUI session. |
| update-notification | None. |

## Opportunities that only exist on 5.x

- `gpu.init()` in background mode (5.2): initialises the GPU module headless (after it,
  `gpu.platform.backend_type_get()` reports `OPENGL` on the RTX 4090). Measured 2026-09-11:
  it does NOT unlock captures. `render.opengl` still raises "Cannot use OpenGL render in
  background mode", `screen.screenshot_area` fails its poll and `Window.screenshot()` raises
  "not available in background mode", exactly as on 4.3.2 (where `gpu.init` does not
  exist). Offscreen drawing via `gpu.types.GPUOffScreen` after `gpu.init()` is untested.
- `Window.screenshot()` to memory (5.2): no temp files for captures.
- `bpy.app.handlers.exit_pre` (5.1): clean socket shutdown for the add-on server.
- `render.render(frame_start=, frame_end=)` (5.0): flipbook ranges without frame stepping.
- `imbuf` buffer-protocol pixel access and format conversion (5.2): faster image tools.
- `bpy.data.all_ids` iterator and `file_path_foreach` with `EXPAND_*` (5.2): asset reports
  and `find_missing_files`.
- `mathutils` slicing with step and buffer protocol (5.0/5.2): faster numpy exchange
  once dtype is handled.
- Tree interface lookup by identifier (5.0) and node panel control (5.2) for the
  node-graph driver.
- Experimental GN Cloth and Hair Dynamics with XPBD (5.2) as a node-native alternative
  to legacy cloth for the VFX request.

## Branch strategy (decided 2026-09-09)

The current `main` (v1.6.0, commit `ef0ff7d`, Blender 4.3 target) is to be ARCHIVED under
a branch named after the Blender version it targets, and the migrated code takes over
`main` once the migration is complete. Rules:

- Archive name: `blender-4.3`. It receives no new features after the rename; only a
  critical fix for someone still on 4.3, tagged `v1.6.x`.
- The migration work happens on a branch `blender-5.2` cut from `main`, so `main` keeps
  pointing at the last 4.3 state until the switch and the GitHub default branch never
  points at half-migrated code.
- The switch (rename + default branch) happens only when the 5.2 branch has passed the
  92-tool headless pass and the live viewport pass on 5.2.1, and the docs' verified-facts
  sections have been updated. Tag that commit `v2.0.0` (major bump: the add-on's
  `bl_info["blender"]` minimum rises, the depth-map and boolean wire behaviour change,
  and 4.x support is best-effort from then on).
- GitHub release for the 4.3 archive: the existing `v1.6.0` release stays as is; the
  archive branch is the source of any `v1.6.x` follow-up.
- Feature-request builds target the `blender-5.2` branch. Anything built before the
  switch is rebased onto it, never onto `main`.

Commands for the switch (run only when the conditions above hold; nothing here has been
executed):

```bash
cd "D:/App Dev/blender_mcp"
git fetch origin
git branch -m main blender-4.3                # archive the current main locally
git push origin blender-4.3                    # publish the archive
git checkout blender-5.2
git branch -m blender-5.2 main                 # the migrated branch becomes main
git push origin main --force-with-lease        # replace remote main (history diverges)
# GitHub: Settings → Branches → default branch = main (it already is; confirm), then
gh repo edit naab007/blender_mcp --default-branch main
git push origin --delete blender-5.2           # optional: the name now lives as main
git tag -a v2.0.0 -m "Blender 5.2 LTS migration" && git push origin v2.0.0
```

Do the rename on GitHub through `gh api -X POST repos/naab007/blender_mcp/branches/main/rename -f new_name=blender-4.3`
instead of the local `branch -m` + push if you want GitHub to retarget open PRs and
the default branch automatically; then push the migrated branch as the new `main`.
Either way, tell the user before the force-push: it rewrites what `main` points to and
every clone must `git fetch` and reset.

Stale upstream branches on the remote (`coderabbitai/docstrings/f0554aa`,
`revert-84-main`, `revert-86-revert-84-main`) are inherited from the fork source and can
be deleted during the same housekeeping.

## Migration plan

1. Install 5.2.1 portable beside 4.3.2 (`D:\blender-5.2.1-windows-x64\`), never over it.
   Set `BLENDER_EXE` for the MCP server explicitly; `_find_blender_exe` prefers the newest
   Program Files install and does not know about portable folders.
2. Install the add-on into the 5.2 user add-ons folder
   (`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\addon.py`), enable, and run
   the fixes 1-4 above; bump `bl_info["blender"]` minimum only if a 5.x-only API is
   adopted (keep 4.x support behind `hasattr` / try-assign, not version checks).
3. Re-run every script in `docs/apichecks/` on 5.2 headless
   (`blender.exe --background --factory-startup --python docs/apichecks/<script>.py`),
   save the JSON lines, and diff against the 4.3.2 outputs recorded in the request docs'
   "verified facts" sections. Update those sections; anything that differs is a
   4.x/5.x switch in the implementation.
4. Run the existing headless handler tests for all 92 tools on 5.2 (the ones from the
   2026-09-02 review pass); fix; then a live-session pass for the viewport tools.
5. Only then start the feature-request builds, on 5.2, with 4.3 compatibility kept where
   it costs nothing.
6. Keep 4.3.2 until every `.blend` that matters has been opened and re-saved in 5.2 (one-way).

## Verified on the installed 5.2.1 (2026-09-09)

Blender 5.2.1 LTS is extracted at `D:\blender-5.2.1-windows-x64\` (zip MD5 verified
against the official list). Every script in `docs/apichecks/` was run on both installs;
outputs are `docs/apichecks/out_4.3.2.jsonl` and `out_5.2.1.jsonl`, and
`python diff_outputs.py out_4.3.2.jsonl out_5.2.1.jsonl` reproduces the findings below.

Confirmed from the diff:

- Bundled Python 3.13 with `requests` 2.32.3 and numpy 2.3.4 in
  `5.2\python\lib\site-packages`. The add-on's `import requests` is safe.
- Engine enum: `BLENDER_EEVEE_NEXT` gone, `BLENDER_EEVEE` present (break #1 confirmed).
- `Scene.node_tree` gone (`AttributeError`), `scene.compositing_node_group` present, a
  tree is created with `bpy.data.node_groups.new(name, "CompositorNodeTree")` (break #2
  confirmed). Compositor node ids on 5.2.1:
  - still present: `CompositorNodeInvert`, `CompositorNodeNormalize`,
    `CompositorNodeOutputFile`, `CompositorNodeRLayers`, `CompositorNodeViewer`,
    `CompositorNodeSeparateColor`, `CompositorNodeCombineColor`, `CompositorNodeBlur`,
    `CompositorNodeAlphaOver`, `CompositorNodeSetAlpha`, `CompositorNodePremulKey`,
    `CompositorNodeDilateErode`, `CompositorNodeNormal`, `CompositorNodeVecBlur`,
    `NodeGroupOutput`.
  - gone, use the shader id: `CompositorNodeMapRange` → `ShaderNodeMapRange`,
    `CompositorNodeGamma` → `ShaderNodeGamma`, `CompositorNodeMath` → `ShaderNodeMath`,
    `CompositorNodeMixRGB` → `ShaderNodeMix`, `CompositorNodeValToRGB` →
    `ShaderNodeValToRGB`, `CompositorNodeSepRGBA` / `CombRGBA` →
    `CompositorNodeSeparateColor` / `CombineColor`.
  - gone with no shader twin: `CompositorNodeComposite` (the output is a
    `NodeGroupOutput` with an Image interface socket).
  - `render_depth_map` therefore needs: `ShaderNodeMapRange`, `CompositorNodeInvert`,
    `NodeGroupOutput`. Try the compositor id first, then the shader id, on both versions.
  - `CompositorNodeRLayers` outputs list only ENABLED passes on 5.2 (`Image`, `Alpha` by
    default); enable `view_layer.use_pass_z` before looking for `Depth`. The socket was
    already named `Depth` on 4.3.
  - File Output node: `base_path`, `file_slots`, `layer_slots` gone; `directory`,
    `file_name`, `file_output_items` present (node-graph request updated accordingly).
- Geometry-nodes modifier inputs (5.2): `modifier["Socket_0"]` raises
  `TypeError: id properties not supported for this type`; the new path is
  `modifier.properties.inputs["Socket_0"].value` with `.type` in `VALUE` / `ATTRIBUTE`,
  `.attribute_name`, `.name`; `modifier.properties.inputs.keys()` lists the identifiers;
  `modifier.properties.outputs` exists. Interface identifiers are still `Socket_N`.
- Boolean solver: not exercised by the scripts; the release note is authoritative
  (`FAST` → `FLOAT`). Add to `gdcheck.py` when fixing.
- `image_settings.file_format` for stills: not exercised; the scripts read
  `image_settings` props only. Test in the live pass (break #4).
- UV: `MeshUVLoopLayer.vertex_selection` / `edge_selection` gone (confirmed).
- `PointCache.compression` gone; `wm.alembic_export(visible_objects_only)` gone;
  `wm.usd_export` lost `export_textures` and `visible_objects_only`, gained
  `convert_scene_units`, `meters_per_unit`, `merge_parent_xform`, `incremental_frames`.
- Fluid cache formats read `OPENVDB / RAW / UNI` on 5.2 headless (they read only `NONE`
  on 4.3): the enum is populated at least on this build, so validation by enum works on
  5.2 but keep the try-assign fallback for 4.x.
- Additions worth using: `armature.symmetrize(copy_bone_colors)`, FBX
  `mesh_smooth_type` gains `SMOOTH_GROUP`, glTF `export_vertex_color` gains `NAME`,
  `wm.obj_export(apply_transform)`, `object.transform_apply(corrective_flip_normals)`,
  `mesh.symmetry_snap(use_topology)`, `tris_convert_to_quads(topology_influence,
  deselect_joined)`, `uv.unwrap(use_original_bounds)`, `image.resize(all_udims)`,
  `paint.add_texture_paint_slot(tiled)`, `render.bake` gains `type`, `use_multires`,
  `use_lores_mesh`, `displacement_space`, Principled BSDF gains `Thin Wall`,
  `object.convert` target `POINTCLOUD`, `bpy.app.handlers.exit_pre`,
  `Object.bl_system_properties_get`, `Node.location_absolute`,
  `prefs.filepaths.save_modified_images` / `texture_cache_directory`,
  `wm.save_mainfile(show_save_modified_images_dialog)`,
  `libraries.load(pack, set_fake, recursive, reuse_local_id, clear_asset_data)`
  (keyword-only now).
- Removed elsewhere: `object.convert(angle, seams)`, `scene.eevee.use_gtao`,
  `view_settings.use_hdr_view`, `NodeTree.is_type_point_cloud`, `filter_collada` on file
  operators. `bpy.data.orphans_purge` now documents `do_recursive=False` as the default:
  pass it explicitly.
- `bpy.data.version` of the 5.2 factory file is `(5, 1, 16)`, and it reports
  `is_dirty=False` at startup (4.3 reported True): `get_file_state` tests must not assume
  the 4.3 value.
- The 5.2 user add-ons folder (`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\`)
  is EMPTY: neither the MCP add-on nor the Timbermesh plugin is installed there yet. Both
  must be copied before any live test; `addon_utils` did not find `timbermesh_blender_plugin`
  on 5.2 for that reason, not because of incompatibility (its `bl_info` says 2.80+ and it
  uses only data APIs, expect it to load; verify).

Measured 2026-09-11 with `apichecks/migcheck.py` on both installs (lines appended to the
`out_*.jsonl` files under `### migcheck.py`; Planner/API evidence):

- `media_type` enum on 5.2.1 is `IMAGE` / `MULTI_LAYER_IMAGE` / `VIDEO` (default `IMAGE`),
  absent on 4.3.2. A PNG still renders headless on 5.2.1 with no `media_type` change;
  `FFMPEG` needs `VIDEO`, `OPEN_EXR_MULTILAYER` needs `MULTI_LAYER_IMAGE` (matrix in break #4).
- Depth graph on 5.2.1: `bpy.data.node_groups.new(name, "CompositorNodeTree")` assigned to
  `scene.compositing_node_group`; `ShaderNodeMapRange` (float inputs by name, output `Result`,
  clamp property `clamp`), `CompositorNodeInvert` (input `Color`) on both versions,
  `NodeGroupOutput` after `interface.new_socket(name="Image", in_out="OUTPUT",
  socket_type="NodeSocketColor")`. Render FINISHED, pixels vary, `node_groups` empty after
  cleanup, source scene untouched.
- The default camera sits about 11.2 units from the default cube: at `max_depth=10` a depth
  PNG spans only 0.000-0.035; tests use `max_depth=25`.
- 92 MCP tools = 84 wrappers with their own handler key (the two Rodin wrappers share
  `create_rodin_job`; `execute_blender_code` -> `execute_code` and
  `generate_hunyuan3d_model` -> `create_hunyuan_job` are renames) + 5 add-on-free wrappers
  (`start_blender`, `diff_images`, `load_img_to_3d_model`, `unload_img_to_3d_model`,
  `generate_3d_from_image`) + 3 wrappers that call another tool's handler (`close_blender`
  -> `quit_blender`, `get_blender_status` -> `get_polyhaven_status` probe,
  `compare_reference_image` -> `capture_viewport_angle`). The live handler table has 85
  entries; the one handler without a wrapper is `get_reference_image`, reached only through
  the server helper `_resolve_reference` (measured by AST, Server Dev; named by the Verifier's
  G3 run, 2026-09-11).
- `RenderSettings.engine` `enum_items` lists only one EEVEE id on both versions (static and
  per-scene); validate by assignment, the exception text carries the live tuple.
- `addon.py` registers and unregisters headless on 5.2.1 (legacy `bl_info` add-on loads);
  `numpy.array(Vector)` is float32 on 5.2.1.

Still to confirm in a live (non-headless) 5.2 session (the exact steps are the "Live
checklist (M14)" section below):

- FFMPEG output with `media_type = 'VIDEO'` (break #4; PNG stills were measured fine
  headless on 2026-09-11).
- Legacy add-on loading and any warning shown for `bl_info`-only add-ons.
- The `--python-expr` and `--factory-startup` paths of `start_blender` on 5.2.
- Resolved headless 2026-09-11: `gpu.init()` does not make `screen.screenshot_area` or
  `render.opengl` usable in background mode (see Opportunities).

## Migration log (team nuzukm, 2026-09-11)

Rulings and measured facts recorded as they landed. Claims of the form "fix N verified
on 5.2.1" appear here only once the Lead has posted the harness evidence.

- Branch `blender-5.2` cut from `main` @ `ef0ff7d`; all migration work happens there.
  Nothing is committed, deployed or launched with a GUI by agents; headless
  `--background` runs are the only Blender use.
- Version: 2.0.0 in all three sources (`pyproject.toml`, `addon.py` `bl_info`,
  `src/blender_mcp/__init__.py`). `bl_info["blender"]` stays `(4, 0, 0)`: no 5-only API
  is adopted.
- `material.use_nodes` / `world.use_nodes` assignments stay: removing them breaks 4.x;
  they are a no-op on 5.x until 6.0.
- 4.x/5.x differences are resolved by try-assign or `hasattr`, never by comparing
  `bpy.app.version`; the two comparisons the assessment found in `addon.py` (old lines
  2439 and 4414) are removed by the migration. Harness rule M7 (refined 2026-09-11):
  the comparison form `bpy.app.version <op> ...` must not occur; reading
  `bpy.app.version_string` (as `get_version` does from 2.1.0) is allowed.
- Break #3 gained `MANIFOLD`: the 5.2.1 solver enum is `FLOAT / EXACT / MANIFOLD`. Wording
  everywhere (server docstring, TOOLS.md, skill): "EXACT, FLOAT (FAST accepted as alias),
  MANIFOLD (Blender 5.2+)". The add-on passes `MANIFOLD` through only where the live enum
  has it and lists the live enum in errors.
- Break #4 bites only video output: PNG stills succeed headless on 5.2.1 with
  `media_type` untouched (defaults to `IMAGE`). The live enum is `IMAGE` /
  `MULTI_LAYER_IMAGE` / `VIDEO`; the add-on sets it per format (`FFMPEG` -> `VIDEO`,
  `OPEN_EXR_MULTILAYER` -> `MULTI_LAYER_IMAGE`, else `IMAGE`). FFMPEG output itself is a
  live-session check.
- Break #2 detail: `ShaderNodeMapRange` is accepted inside a `CompositorNodeTree` on
  5.2.1; its output socket is `Result` where 4.3's `CompositorNodeMapRange` has `Value`.
- Input normalisation (add-on): `set_render_settings` accepts bare `EEVEE` / `CYCLES` /
  `WORKBENCH` and lowercase; `boolean_operation` accepts lowercase and validates
  `operation` against the live enum too; both replies carry the resolved id.
- Pre-existing bug found by the migration grep (F6): the OBJ branch of
  `download_polyhaven_asset` (models) called the removed `bpy.ops.import_scene.obj`
  unguarded; fixed in the same pass. All three OBJ-import sites (`import_file`,
  `download_polyhaven_asset`, Hunyuan `import_generated_asset_hunyuan`) now go through
  `_op_exists(bpy.ops.wm.obj_import)`.
- Handler count: 92 MCP tools = 84 wrappers with their own handler key (the two Rodin wrappers share
  `create_rodin_job`; `execute_blender_code` -> `execute_code` and
  `generate_hunyuan3d_model` -> `create_hunyuan_job` are renames) + 5 add-on-free wrappers
  (`start_blender`, `diff_images`, `load_img_to_3d_model`, `unload_img_to_3d_model`,
  `generate_3d_from_image`) + 3 wrappers that call another tool's handler (`close_blender`
  -> `quit_blender`, `get_blender_status` -> `get_polyhaven_status` probe,
  `compare_reference_image` -> `capture_viewport_angle`). The live handler table has 85
  entries; the one handler without a wrapper is `get_reference_image`, reached only through
  the server helper `_resolve_reference` (measured by AST, Server Dev; named by the Verifier's
  G3 run, 2026-09-11).
  Harness tables count the 85 handlers.
- Engine enum blind spot: `RenderSettings.engine` `enum_items`, read statically or from a
  scene instance, lists only ONE EEVEE id on both versions, and `file_format`
  `enum_items` is not filtered by `media_type`; a "valid: [...]" list built from
  `enum_items` is wrong for these two props. The add-on validates by assignment and the
  error reply carries the live tuple from the assignment exception.
- `file_format` aliases (add-on `_set_file_format`, case-insensitive): `EXR` -> `OPEN_EXR`,
  `EXR_MULTILAYER` -> `OPEN_EXR_MULTILAYER`, `JPG` -> `JPEG`, `TIF` -> `TIFF`, `TGA` -> `TARGA`.
- `gpu.init()` (5.2) does not enable headless captures (probe output above, under
  Opportunities). The presentation-and-library request keeps its GUI-session requirement
  for every capture tool.
- Server (`src/blender_mcp/server.py`, accepted by the Lead 2026-09-11): `tools/list`
  identical to `tests/baseline_tools_4.3.txt` (92 names, same order, same input schemas);
  `_find_blender_exe` now also searches portable `blender-<ver>-windows-x64` folders on
  `D:\`, under the user's home and on every fixed drive root and returns the highest
  version; `BLENDER_EXE` wins when it points at an existing file and is ignored
  otherwise. `boolean_operation` and `set_render_settings` replies name the solver and
  engine id actually set.
- Baseline of the UNFIXED add-on on both installs (Ton's harness, 2026-09-11): 4.3.2
  passed 5 of 13 migration cases, 5.2.1 passed 3 of 13; breaks #1, #2 and #3 reproduce on
  5.2.1 as `TypeError` / `AttributeError`.
- G3 harness result on the A1 file (`git hash-object 3c726b58`, `tests/headless_handlers.py`
  + `tests/test_server_units.py` + `tests/test_version_sync.py`, reports in `tests/`): 131
  steps, 129 PASS / 2 FAIL on BOTH installs with an identical fail set, and every migration
  case M1-M13 PASS on both. The two fails are pre-existing 4.x behaviour, not migration
  regressions: `extrude_faces` leaves an interior cap (12 verts / 11 faces on a single
  extruded face) and five handlers (`subdivide_mesh`, `apply_modifier`, `export_object`,
  `set_origin`, `set_smooth_shading`) do not restore the previously active object.
  `separate_mesh` keeps the SOURCE active by design (ruling). 13 network-gated handlers
  are not run headless; 3 viewport handlers wait for the live pass (M14).
  Verifier's per-case table on the same hash: M1 register (requests 2.27.1 / 2.32.3), M3
  engine (6 spellings, resolves `BLENDER_EEVEE_NEXT` on 4.3.2 and `BLENDER_EEVEE` on 5.2.1),
  M4 depth map (near > far, scene and node-group counts unchanged, no-camera error), M5
  solver (`FLOAT` -> `FAST` on 4.3.2 where `MANIFOLD` is an enum error; `FAST` -> `FLOAT`
  and `MANIFOLD` native on 5.2.1; no modifier left on `BOGUS`), M6 still + `media_type`
  restore (`VIDEO` then `IMAGE` on 5.2.1), M7 zero `bpy.app.version` compares, M9 tools/list
  names and input schemas identical to the baseline (descriptions differ only for
  `boolean_operation`, `set_render_settings`, `start_blender`), M10 apichecks drift none
  (noise: `app.tempdir`), M11 versions 2.0.0 x3, M12 one numpy site (a PIL image, float32
  by design), M13 malformed inputs: all PASS on both; venv unit tests 30/30.
- A1.1 ACCEPTED 2026-09-11 on `addon.py` git hash-object `e0329ced83ca1b74e12d0c5b2caaab65c872b94e` (Verifier's full harness 134/134 on
  4.3.2, 134/134 on 5.2.1). New helper `_selection_scope`; `extrude_faces` reply gains `new_faces`;
  the five M8 handlers and `boolean_operation` leave active object and selection as found.
  Corroborating evidence on the same hash: Lead's independent script 20/20 (4.3.2) and 21/21
  (5.2.1), Planner's migcheck handler mode 4/4 on both, venv unit tests 30/30.
  Content of A1.1: the two G3 fails above, break #5 above, plus the HDRI download temp file
  leak: `download_polyhaven_asset` (hdris) called `tempfile._cleanup()`, which does not exist
  on Blender's Python 3.11 or 3.13, inside a bare `except: pass`, so the downloaded `.hdr` /
  `.exr` was never deleted. Both found by the 2026-09-11 code-comment audit.
- Deferred to B0 as add-on cleanup (comment audit, cosmetic): EXR HDRI colour-space fallback
  order (`Linear` exists on neither version; the hdr branch's `Linear Rec.709` list should be
  shared), `set_texture` second pass re-creating NormalMap/Displacement nodes and links,
  debug prints, stale/noise comments, one non-ASCII comment.
- Docs updated on the branch (Sybren, 2026-09-11): README (Blender 5.2 line, requirements,
  one-way `.blend` warning, 5.2 add-ons folder, `BLENDER_EXE` row, troubleshooting),
  TOOLS.md (`start_blender` detection order, `render_depth_map`, `set_render_settings`
  engine row, `boolean_operation` solver row), `/blender` skill (solver rule), this doc,
  `apichecks/README.md` (`migcheck.py`), auto-memory note.

## Live checklist (M14): Blender 5.2.1 GUI session, run by the user at the Lead's gate

Every step has a PASS condition; paste the printed output back, never a summary. Paths
assume the portable install `D:\blender-5.2.1-windows-x64\blender.exe` and the migrated
`addon.py` from branch `blender-5.2`. Preconditions: the headless harness is green on
both installs; the add-on is copied into
`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\addon.py` (that folder was empty
on 2026-09-09) with no stale `__pycache__`. Run Python steps from the Scripting workspace
(Text Editor > New > paste > Run Script); open Window > Toggle System Console first so
the printed lines are visible.

### L1. Legacy add-on loads on 5.2 GUI
1. Launch `D:\blender-5.2.1-windows-x64\blender.exe` with no flags.
2. Edit > Preferences > Add-ons, search "Blender MCP", enable it.
3. Read the system console. PASS: the add-on enables with no traceback. Copy any
   warning about `bl_info` / legacy add-ons verbatim (a notice is PASS, a traceback is
   FAIL).
4. Sidebar (N) > BlenderMCP tab > start the server. PASS: `netstat -an | findstr 9876`
   in a cmd window shows LISTENING.

### L2. Still PNG without media_type, then FFMPEG with it (break #4)
Paste and run:
```
import bpy, os, tempfile
r = bpy.context.scene.render
print("media_type present:", hasattr(r.image_settings, "media_type"))
if hasattr(r.image_settings, "media_type"):
    print("media_type default:", r.image_settings.media_type)
r.image_settings.file_format = 'PNG'
p = os.path.join(tempfile.gettempdir(), "m14_still.png")
r.filepath = p
bpy.ops.render.render(write_still=True)
print("still written:", os.path.exists(p), os.path.getsize(p) if os.path.exists(p) else 0)
try:
    r.image_settings.file_format = 'FFMPEG'
    print("FFMPEG without media_type: accepted ->", r.image_settings.file_format)
except Exception as e:
    print("FFMPEG without media_type: REJECTED:", e)
if hasattr(r.image_settings, "media_type"):
    r.image_settings.media_type = 'VIDEO'
    r.image_settings.file_format = 'FFMPEG'
    print("FFMPEG with media_type=VIDEO ->", r.image_settings.file_format, r.ffmpeg.format)
    r.image_settings.media_type = 'IMAGE'
    r.image_settings.file_format = 'PNG'
    print("restored ->", r.image_settings.media_type, r.image_settings.file_format)
```
PASS: "still written: True <size > 0>"; the with-media_type line prints `FFMPEG`; the
restored line prints `IMAGE PNG`. Record the without-media_type line: it decides whether
the docs call `media_type` mandatory or optional for video.
OUTCOME (live pass on 5.2.1 GUI, 2026-09-11, `docs/LIVE-TEST-REPORT-2026-09-11.md`): PASS; `FFMPEG` without `media_type` is REJECTED in the GUI too, so
`media_type = 'VIDEO'` is MANDATORY for video output on 5.x (the add-on sets it).

### L3. Viewport capture tools through the MCP server (GUI only)
With the add-on listening (L1) and the MCP server started from a Claude Code session
whose environment has `BLENDER_EXE=D:\blender-5.2.1-windows-x64\blender.exe`, call in
order and paste every reply verbatim:
1. `get_blender_status()`. PASS: addon socket reachable (known quirk: "not reachable"
   while netstat shows LISTENING is recorded, not a FAIL).
2. `get_viewport_screenshot()`. PASS: an image, non-blank (save it and run the pixel
   stddev > 0 check; do not eyeball).
3. `capture_viewport_angle(angle="iso_front_right")`. PASS: image, non-blank.
4. `capture_contact_sheet()`. PASS: tiled image, non-blank.
5. Note `len(bpy.data.scenes)` and `len(bpy.data.node_groups)`, then `render_depth_map()`.
   PASS: image, non-blank, and both counts unchanged afterwards.
6. `render_from_camera()` with the default camera. PASS: image, non-blank.
7. `set_render_settings(engine="BLENDER_EEVEE")`. PASS: the reply names the resolved
   id and `bpy.context.scene.render.engine` prints `BLENDER_EEVEE`.
8. `boolean_operation("Cube", "<a second primitive>", solver="FAST")`. PASS: the reply
   says `solver=FLOAT`.

### L4. gpu.init() headless probe: resolved by Sybren 2026-09-11, no user step
See Opportunities: `gpu.init()` exists on 5.2.1 and brings up the OPENGL backend, but
`render.opengl`, `screen.screenshot_area` and `Window.screenshot()` all still refuse in
background mode. Probe script: `l4_gpu_probe.py` in the migration scratchpad; output is
quoted in the team log.

### L5. start_blender launch paths on 5.2 (from the MCP server)
1. `close_blender()` if a managed instance is running.
2. `start_blender(background=False, wait_for_addon=True)`. PASS: PID returned. A 30 s
   socket timeout is the KNOWN state (the add-on does not autostart on a normal launch,
   see the settings request section 0.1); record it as such.
3. `close_blender()`, then
   `start_blender(background=True, python_expr="import bpy; print('expr ok', bpy.app.version_string)")`.
   PASS: `<tempdir>\blender_mcp_blender.log` contains "expr ok 5.2.1".
4. `close_blender()`, then `start_blender(background=True, blend_file="<any 4.3 .blend>")`.
   PASS: opens; record the console line about the file version.

### L6. One-way file format (documentation fact)
Save a new file from 5.2 (File > Save As, default compression) and open it in
`D:\blender-4.3.2-windows-x64\blender.exe`. PASS: 4.3.2 refuses or warns; paste the
message. That wording goes into README.
OUTCOME (live pass on 5.2.1 GUI, 2026-09-11, `docs/LIVE-TEST-REPORT-2026-09-11.md`): PASS; 4.3.2 refuses with "incomplete header, may be from a newer version of
Blender".

Phase B0 (2.1.0) adds its own live steps L7 (autostart on a cold launch), L8
(start_blender(start_server=True)) and L9 (session restore) in
FEATURE-REQUEST-settings-save-load.md section 8; they run at the B0-G5 gate, not here.
