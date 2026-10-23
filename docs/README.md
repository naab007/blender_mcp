# Blender MCP feature requests, game-dev focus

Each request doc is self-contained for an implementing agent. They all share the
architecture rules in `FEATURE-REQUEST-rigging-and-animation.md` sections 0, 1, 4 (ripple
points) and 7 (deploy). Read that doc first whichever request you are building.

## Usage categories for game development

| # | Category | What it covers | Request doc |
|---|---|---|---|
| 1 | Asset validation and engine readiness | Pre-export checks: transforms, scale, normals, manifold, ngons, budgets, naming, UV presence, engine limits | `FEATURE-REQUEST-engine-readiness.md` |
| 2 | Optimisation and LODs | Decimation, LOD chains, triangulation, custom and weighted normals, tangents, poly budgets | `FEATURE-REQUEST-engine-readiness.md` |
| 3 | Collision and physics proxies | Convex hulls, box/capsule/sphere proxies, UCX_/-col naming, decomposition | `FEATURE-REQUEST-engine-readiness.md` |
| 4 | Sockets, pivots and hierarchy | Attach points (Timbermesh Slot, UE SOCKET_), pivots, origin rules, parenting for export | `FEATURE-REQUEST-engine-readiness.md` |
| 5 | Engine export presets and batching | Unity, Unreal, Godot, Bevy glTF, Timbermesh; per-object batch; manifests | `FEATURE-REQUEST-engine-readiness.md` |
| 6 | Modelling and hard-surface gaps | Bisect, mirror, symmetrize, remesh, dissolve, bridge, solidify, bevel by weight, arrays on curves | `FEATURE-REQUEST-asset-construction.md` |
| 7 | Modular kits, grid and blockout | Grid snapping, kit pieces with bounds, greybox primitives, instancing, kitbash | `FEATURE-REQUEST-asset-construction.md` |
| 8 | Environment assets | Terrain from heightmap, foliage cards, scatter, decals, trim-sheet application | `FEATURE-REQUEST-asset-construction.md` |
| 9 | 2D from 3D | Sprite sheets, isometric and 8-direction renders, icons, impostors and billboards | `FEATURE-REQUEST-presentation-and-library.md` |
| 10 | Game-dev perception | Wireframe and tri-count overlay, silhouette, scale mannequin, LOD and collision contact sheets, backface render, turntable | `FEATURE-REQUEST-presentation-and-library.md` |
| 11 | Asset library and import hygiene | Ripped-asset import (AssetStudio FBX, glTF), material name matching, metadata and custom properties, batch rename, asset marking | `FEATURE-REQUEST-presentation-and-library.md` |
| 12 | Rigging and animation | Armatures, skinning, pose, constraints, actions, NLA, bake, playblast | `FEATURE-REQUEST-rigging-and-animation.md` |
| 13 | UV and texturing | Seams, unwrap, packing, UV checks, node wiring, images, baking, painting | `FEATURE-REQUEST-uv-and-texturing.md` |
| 14 | Settings, save and load | File lifecycle, versions, recovery, generic settings get/set/describe, snapshots and restore, presets, project profile, add-ons, preferences, session state, server and add-on config | `FEATURE-REQUEST-settings-save-load.md` |
| 15 | Update notification | One-time new-release notice to agents (installed vs latest GitHub release), version sync across the three version sources, addon/server mismatch detection | `FEATURE-REQUEST-update-notification.md` |
| 16 | Node-graph programming | Tree-agnostic driver for geometry, shader and compositor trees (create, inspect, link, set, group, layout, render, diff, validate), modifier binding, evaluation readback, realize, bakes, zones, geometry-nodes recipes | `FEATURE-REQUEST-node-graphs.md` |
| 17 | Simulation and VFX | Fluids, particles, force fields, cloth, soft body, rigid bodies, dynamic paint, ocean, quick effects, and the game outputs: flipbooks with passes, vertex animation textures, baked keyframes, shape keys, Alembic/USD caches, realized frames | `FEATURE-REQUEST-simulation-and-vfx.md` |
| 18 | Generic driver (CDP-style) | Eleven generic tools over one add-on dispatcher: `describe` (RNA schema for any struct, property or operator), `rna_get` / `rna_set` by ref (`#uid`, `OBJECT:Name`, `/path`), `call_operator` with a declared context, `call_method`, `data_new` / `data_remove`, `scene_snapshot` / `scene_diff` keyed by `session_uid`, `batch` with `$id` references and undo rollback, `transaction`; Tier 2 events (app handlers + msgbus by cursor), `find`, `context_info`. Most convenience tools in docs 1-17 become server-side recipes on it | `FEATURE-REQUEST-generic-driver.md` |

## Engines and pipelines these are written against

| Target | Format | Facts that shaped the requests |
|---|---|---|
| Unity (SPT Tarkov asset bundles, Timberborn) | FBX / glTF / Timbermesh | Y-up, metres; `_LOD0.._LODn` suffix auto-builds a LODGroup; OBJ round trips weld verts and drop tangents (SPT tri-rail lesson); Timbermesh exports a collection, splits meshes by exact material name, and only `#`-prefixed objects keep their own node, so attach-point empties are named `#<Slot>` (verified from the plugin source); red normals = inside out |
| Unreal 5 (TerraTech Legion, Palworld) | FBX | Z-up, centimetres; `UCX_<mesh>_NN` collision, `SOCKET_` empties, UV1 lightmaps, smoothing groups FACE/EDGE, tangent space export |
| Bevy (Stratum) and Godot | glTF / GLB | Y-up glTF; Godot `-col`, `-convcol`, `-colonly`, `-navmesh` suffixes; Bevy reads named nodes and extras |
| 2D and isometric games (Stratum sprites, Vampire Survivors) | PNG sheets | Orthographic multi-angle renders, alpha, consistent light rule, sheet packing |

## Blender version

All "verified facts" sections were checked on Blender 4.3.2. The migration to 5.2 LTS
(5.2.1) is in progress on branch `blender-5.2` since 2026-09-11 (version 2.0.0); see
`MIGRATION-blender-5.2.md` for the four hard breaks, the measured 4.3.2 vs 5.2.1 API diff,
the per-doc list of facts that change and the migration log. The check scripts live in
`apichecks/` (`migcheck.py` proves the four breaks and their fixes on either version).
A request doc's facts count as re-verified on 5.2.1 only where its section says so with
the evidence line; until then build against the 5.2.1 diff in the migration doc.

## Known bugs (status as of 2026-09-11, branch `blender-5.2`)

- FIXED in 2.1.0 (B0-A L1, add-on hash `39162cbf`): the add-on never started its socket
  server on a normal Blender launch and saved the running flag into `.blend` files. Now: an
  `autostart_server` preference (default on) starts it from `register()` through a timer,
  the running state is runtime truth (`_server_running()`), the `ensure_server_running`
  command and the panel button share one function. Write-up:
  `FEATURE-REQUEST-settings-save-load.md` section 0.1.
- FIXED in 2.1.0 (same landing): API keys and the port were `Scene` properties saved into
  every `.blend`. Now in `AddonPreferences` (password fields), migrated once from old files
  on load; the legacy Scene properties stay hidden through 2.2.0 and go in 2.3.0 (Lead ruling 2026-09-11). Same doc,
  section 1.
- FIXED in 2.0.0: the three version sources disagreed (`pyproject.toml`, `addon.py`
  `bl_info`, `src/blender_mcp/__init__.py`). All three are bumped together from now on and
  `tests/test_version_sync.py` fails when they differ. Write-up:
  `FEATURE-REQUEST-update-notification.md` section 2.1.

## Build order recommendation

0. Settings, save and load: DONE headless 2026-09-11 as 2.1.0 (addon `81038008`, server
   `598a765d`; harness 165/165 on 4.3.2 and 5.2.1; live L7-L9 pending in the user's batch).
   Its `_settings_scope` helper and secrets migration are used by everything after it.
1. Rigging and animation Tier 1: DONE as 2.2.0 (`PLAN.md` section 10; committed `3459566`, deployed 2026-09-11, unreleased)
2. Engine readiness Tier 1 (validation, LODs, collision, sockets, export presets): DONE headless as 2.4.0 (team lohjud, B2-G3 passed 2026-10-22; `PLAN.md` section 13; frozen pair addon `ee9b9dc0` / server `a2cdd77f`, 204 tools, harness 252/252 on 4.3.2 and 5.2.1; G5 live pass pending; Tier 2 set deferred, `PLAN.md` 13.10). Uncommitted with 2.3.0 (one commit, user's call).
   2a. Generic driver Tier 1 + `find` / `context_info`: DONE headless as 2.3.0 (`PLAN.md` section 12, team nosubl, 2026-10-16); events (Tier 2) = their own later stage (user directive 2026-10-21: one feature request per stage).
3. UV and texturing Tier 1: NEXT stage, 2.5.0, starts only on the user's explicit go (stage plan `PLAN.md` 13.1 H24)
4. Presentation and library Tier 1 (perception tools pay back immediately)
5. Asset construction
6. Node graphs Tier 1 (the driver; the shader tools in texturing become aliases)
7. Simulation and VFX Tier 1 (outputs first: flipbook, VAT, bake to keyframes)
8. Remaining tiers
