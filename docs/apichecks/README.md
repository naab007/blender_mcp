# Headless API check scripts

Each script prints one JSON line (prefixed with a tag such as `APICHECK`, `UVCHECK`,
`FXCHECK`, `NODECHECK`) listing which operators, properties and enum values exist on the
running Blender. They were used on Blender 4.3.2 (2026-09-06 to 2026-09-09) to write the
"verified facts" sections of the feature-request docs.

Run one:

```
"<blender>\blender.exe" --background --factory-startup --python docs\apichecks\<script>.py 2>&1 | findstr CHECK
```

Run all and keep the output for a diff:

```
for %f in (docs\apichecks\*.py) do "<blender>\blender.exe" --background --factory-startup --python %f 2>&1 | findstr CHECK >> apicheck_<version>.jsonl
```

| Script | Covers |
|---|---|
| `apicheck.py`, `apicheck2.py` | Armatures, bone collections, Rigify and BVH add-ons, FBX/glTF animation kwargs, `nla.bake`, `armature.symmetrize`, vertex-group ops, `pose.armature_apply`, `render.opengl` |
| `uvcheck.py` | UV ops (`unwrap`, `smart_project`, `pack_islands`, projections, `export_layout`), `object.bake`, `render.bake`, image ops and attributes, paint ops, colour attributes, Principled inputs |
| `cyc.py` | Cycles availability headless and the engine enum blind spot |
| `gdcheck.py` | FBX/glTF/OBJ exporter kwargs, Decimate/Weighted Normal/Triangulate modifiers, mesh and BMesh ops, `transform_apply`, `origin_set`, remesh, data transfer, unit settings, bundled add-ons |
| `pcheck.py` | Snapping, viewport overlay and shading props, legacy texture types, Noise node, asset API, `libraries.write`, `orphans_purge`, `bridge_edge_loops`, Timbermesh plugin |
| `scheck.py` | File lifecycle ops, preferences sections, presets, colour management, render/Cycles/EEVEE/scene props, add-on utils, workspaces, config paths |
| `cdp.py` | `ID.session_uid`, msgbus, app handlers, undo push, RNA introspection |
| `fxcheck.py` | Quick effects, fluid, cloth, point cache, rigid body, soft body, particles, force fields, dynamic paint, ocean, wave, explode, mesh cache, Alembic/USD, GN bakes, volumes |
| `nodecheck.py` | Node tree creation, interface sockets and identifiers, node/socket/link props, zones, node catalogue, modifier binding, evaluation readback, frames, groups, compositor tree |
| `migcheck.py` | The four Blender 5.x migration breaks and the facts their fixes need, on 4.x and 5.x: engine id try-assign and the static engine enum, boolean solver enum (`FAST`/`EXACT` on 4.3.2, `FLOAT`/`EXACT`/`MANIFOLD` on 5.2.1), `image_settings.media_type` before `file_format`, a headless still per engine, the depth map on `scene.node_tree` (4.x) or `compositing_node_group` (5.x), material/world `use_nodes`, misc 5.x opportunities, and `addon.py` loaded from the repo root and registered headless with the four handlers exercised. Prints one `MIGCHECK {json}` line. With `MIGCHECK_ADDON=1` it also loads `addon.py` from the repo root and calls the migrated handlers (opt-in, not part of the saved baseline). |
| `b0check.py` | Phase B0 (settings, save and load) facts: `file.*` ops and kwargs, `wm.save_mainfile` / `save_as_mainfile` round trips (compression sizes, copy, incremental naming), `bpy.data` flags at startup and after save, `libraries.load` signature and refusals, scene id-prop round trip, `AddonPreferences` with PASSWORD fields, app handlers and timers, unit settings, workspaces, `image_settings` props, view transforms, Cycles devices, `addon_utils` enable/disable. One `B0CHECK {json}` line. |
| `b1check.py` | Phase B1 (rigging and animation) facts on a real 2-bone armature + skinned cube: slotted actions vs legacy `action.fcurves`, channelbags, `action_slot` binding, bone selection attributes per version, `EditBone` lifetime, `parent_set ARMATURE_AUTO`, `armature.symmetrize` kwargs, `nla.bake`, `render.opengl` headless, FBX/glTF/BVH kwargs with real exports and re-import, shape keys, drivers, constraints, pose and vertex-group ops. One `B1CHECK {json}` line. |
| `b1check2.py` | B1 follow-up facts: fresh-action slot creation (`action.slots.new`) and `action_slot` binding on 5.x, F-curve write path per version (`action.fcurves.new` vs `channelbag.fcurves.new`), interpolation/easing enums, NLA tracks/strips/pushdown (incl. the 4.3.2 strip-name gotcha), pose world math, WEIGHT_PAINT and vertex-group ops headless, Data Transfer, Rigify metarig + generate headless, `anim_utils.bake_action` with `BakeOptions`. One `B1CHECK2 {json}` line. |
| `b2check.py` | B2 (engine readiness) facts with REAL calls on both installs: boolean solver enums (modifier vs edit-mode operator), Decimate / Triangulate / Weighted Normal modifiers applied with counts, `transform_apply` and `tris_convert_to_quads` kwargs per version, the normals switch (`shade_auto_smooth` vs `shade_smooth_by_angle`), custom-normal data API, `calc_tangents` ngon abort, FBX round trip with UCX / SOCKET children and custom props, glTF export/import kwargs and re-import losses, OBJ `apply_transform`, convex hull / bisect for collision, name-length limits, evaluated tri counts, `unit_test_compare`, numpy dtype, Timbermesh presence. One `B2CHECK {json}` line. |

Saved outputs: `out_4.3.2.jsonl` and `out_5.2.1.jsonl` (2026-09-09; `migcheck.py`,
`b0check.py`, `b1check.py`, `b1check2.py` and `b2check.py` lines appended 2026-09-11 under `### <script>` headers,
pre-existing lines untouched). These on-disk files are the drift baseline (M10). Diff them with
`python diff_outputs.py out_4.3.2.jsonl out_5.2.1.jsonl`; the findings are summarised in
`../MIGRATION-blender-5.2.md`. `nodecheck.py` carries both the 4.x and the 5.x paths for
the compositor tree and for geometry-nodes modifier inputs, so it runs on either version.

The headless handler harness lives in `tests/headless_handlers.py` (run the same way,
one `PASS|FAIL <handler> <reply>` line per handler, plus the migration cases and, behind
`-- --b0`, the Phase B0 steps in `tests/b0_steps.py`; reports and logs beside it; the
header records the add-on's `git hash-object`).

When migrating Blender versions, re-run on the new version and diff the JSON against
the saved outputs and the values quoted in the request docs. Enum lists that
read empty headless (view transforms, Cycles devices, fluid cache formats, dynamic-paint
surface types) are dynamic and must be validated by assignment in the real tools.
