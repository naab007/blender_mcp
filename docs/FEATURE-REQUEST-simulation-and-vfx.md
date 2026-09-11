# Feature request: simulation and VFX (physics, fluids, particles, and their game outputs)

Status: OPEN, requested 2026-09-09
Target version: 1.13.0
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`)
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here. Flipbook and sprite rendering reuse
`FEATURE-REQUEST-presentation-and-library.md`; keyframe baking and playblast reuse
`FEATURE-REQUEST-rigging-and-animation.md`; settings scopes and `_settings_scope` reuse
`FEATURE-REQUEST-settings-save-load.md`.

## 0. Scope and why

Blender's effects stack: Mantaflow fluids (Domain, Flow, Effector objects for smoke,
fire and liquid), particle systems and force fields, cloth, soft body, rigid bodies and
constraints, dynamic paint, ocean and wave modifiers, explode, quick effects, geometry-
nodes simulation zones (covered by the node-graph request), point caches, Alembic and
USD caches, OpenVDB volumes.

None of it is reachable today. For game development the simulation itself is rarely the
deliverable; what ships is one of:

- a **flipbook** (smoke, fire, explosion, splash rendered to a sprite sheet with alpha,
  optionally normal, depth and motion-vector sheets for engine particle shaders),
- a **vertex animation texture** (cloth, soft body, ocean, destruction, fluid mesh baked
  to position and normal EXRs for a VAT shader in Unity or Unreal),
- **baked keyframes** (rigid-body debris to object animation for FBX or glTF),
- **shape keys or an Alembic / USD cache** (cloth or fluid mesh as morph targets or a
  geometry cache),
- **static meshes** (a frame of a sim realised as a prop: a splash, a pile of rubble),
- **masks** (dynamic paint wetness, dust or wear baked to a texture or vertex colours).

So every simulation tool here is paired with an output tool, and the plan is judged by
whether the agent can go from "make an explosion" to a sheet in the engine's format.

Build what is in this document. If something is wrong or impossible on 4.3.2, say so in
the report and continue.

## 1. Fixes to existing tools (do these first)

| Tool | Problem | Required change |
|---|---|---|
| `add_modifier` | FLUID / CLOTH / SOFT_BODY / DYNAMIC_PAINT / PARTICLE_SYSTEM modifiers expose nested settings the current flat `props` cannot reach | Accept dotted keys in `props` (`domain_settings.resolution_max`, `settings.mass`, `flow_settings.flow_type`) and pointer values by name. The existing `set`/`unset` reply covers reporting. |
| `get_object_info` | No physics data | Per physics modifier: type, key settings, `point_cache {frame_start, frame_end, is_baked, is_outdated}`; rigid body settings when present; particle systems with count and cache state. |
| `set_frame` / `set_scene_frame_range` | Sims need the range and a way to step | `set_frame` gains `step_to=None` (advance frame by frame from current to target so caches fill), reply includes `is_baking_any`. |
| `apply_modifier` | Cannot apply a sim at a frame | Add `frame=None` (set frame, apply the evaluated state) and document that it works for CLOTH / SOFT_BODY / OCEAN / FLUID mesh via `realize_geometry`. |

## 2. New tools

### 2.1 Tier 1, required

**Setup**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `add_fluid_domain` | `name` or `object`, `domain_type="GAS"` (GAS / LIQUID), `size="4,4,4"`, `location`, `resolution=64`, `adaptive=True`, `frame_start=None`, `frame_end=None`, `cache_type="MODULAR"` (REPLAY / MODULAR / ALL), `cache_directory=None`, `settings` JSON (any `FluidDomainSettings` prop: `use_noise, noise_scale, vorticity, use_dissolve_smoke, dissolve_speed, burning_rate, flame_smoke, use_mesh, mesh_scale, use_spray_particles, use_foam_particles, use_bubble_particles, use_viscosity, viscosity_value, surface_tension, time_scale, gravity, cfl_condition, use_speed_vectors, openvdb_cache_compress_type, openvdb_data_depth`) | Creates a cube (or uses `object`), adds a FLUID modifier with `fluid_type='DOMAIN'`, applies settings, sets the cache directory to `<settings output_dir>/fluid/<name>` when None. Reply: domain name, settings applied, cache path. |
| `add_fluid_flow` | `object`, `flow_type="SMOKE"` (SMOKE / FIRE / BOTH / LIQUID), `behavior="INFLOW"` (INFLOW / OUTFLOW / GEOMETRY), `settings` JSON (`density, temperature, fuel_amount, smoke_color, surface_distance, use_initial_velocity, velocity_factor, velocity_normal, velocity_coord, velocity_random, subframes, use_plane_init, use_absolute, volume_density, particle_system, density_vertex_group, use_texture, texture_map_type, uv_layer`) | `fluid_type='FLOW'`. |
| `add_fluid_effector` | `object`, `effector_type="COLLISION"` (COLLISION / GUIDE), `settings` JSON (`surface_distance, use_plane_init, subframes, guide_mode, velocity_factor, use_effector`) | |
| `quick_effect` | `objects`, `effect="SMOKE"` (SMOKE / FIRE / LIQUID / EXPLODE / FUR), `style=None`, `amount=None`, `frame_start=None`, `frame_end=None`, `velocity=None`, `fade=None`, `density=None`, `length=None` | `object.quick_smoke(style, show_flows)`, `quick_liquid(show_flows)`, `quick_explode(style, amount, frame_duration, frame_start, frame_end, velocity, fade)`, `quick_fur(density, length, radius, view_percentage, apply_hair_guides, use_noise, use_frizz)`. Under a context override with the objects selected. Reply: created domain/particle/curves objects. |
| `add_particle_system` | `object`, `name=None`, `type="EMITTER"` (EMITTER / HAIR), `count=1000`, `frame_start=1`, `frame_end=50`, `lifetime=50`, `emit_from="FACE"` (VERT / FACE / VOLUME), `physics="NEWTON"` (NO / NEWTON / KEYED / BOIDS / FLUID), `render_type="NONE"` (NONE / HALO / LINE / PATH / OBJECT / COLLECTION), `instance_object=None`, `instance_collection=None`, `settings` JSON (any `ParticleSettings` prop: `normal_factor, object_align_factor, factor_random, particle_size, size_random, mass, use_rotations, rotation_mode, phase_factor, angular_velocity_mode, use_dynamic_rotation, use_die_on_collision, collision_collection, damping, drag_factor, brownian_factor, timestep, subframes, hair_length, child_type, child_percent, rendered_child_count, distribution, use_even_distribution, jitter_factor, use_scale_instance, use_rotation_instance, use_whole_collection, integrator, material_slot`), `effector_weights` JSON, `vertex_group_density=None`, `seed=None` | `object.particle_system_add` under override (or `modifiers.new(type='PARTICLE_SYSTEM')`), then configure `psys.settings`. Reply: system name, settings name, cache range. |
| `add_force_field` | `type="WIND"` (FORCE / WIND / VORTEX / MAGNET / HARMONIC / CHARGE / LENNARDJ / TEXTURE / GUIDE / BOID / TURBULENCE / DRAG / FLUID), `name=None`, `location`, `rotation`, `strength=1.0`, `flow=0`, `noise=0`, `seed=0`, `shape="POINT"` (POINT / LINE / PLANE / SURFACE / POINTS), `falloff_type=None`, `falloff_power=None`, `distance_max=None`, `use_max_distance=None`, `settings` JSON (any `FieldSettings` prop), `object=None` (attach a field to an existing object instead of an Empty) | `object.effector_add(type=)` under override or `obj.field.type = ...` on an existing object. Reply: object name, field settings. |
| `add_cloth` | `object`, `preset=None` (COTTON / DENIM / LEATHER / RUBBER / SILK: Blender's cloth presets via `script.execute_preset` or reproduced values), `pin_group=None`, `settings` JSON (`quality, mass, air_damping, tension_stiffness, compression_stiffness, shear_stiffness, bending_stiffness, tension_damping, bending_damping, use_pressure, uniform_pressure_force, use_internal_springs, use_sewing_springs, shrink_min, bending_model, time_scale`), `collision` JSON (`use_collision, distance_min, collision_quality, use_self_collision, self_distance_min, friction, collection`), `frame_start=None`, `frame_end=None` | CLOTH modifier; `settings.vertex_group_mass = pin_group`. Reply: cache range. |
| `add_soft_body` | `object`, `goal_group=None`, `settings` JSON (`friction, mass, speed, goal_default, goal_spring, goal_friction, use_goal, pull, push, damping, plastic, bend, use_edges, use_stiff_quads, use_self_collision, ball_size, ball_stiff, ball_damp, step_min, step_max, use_auto_step, error_threshold, collision_collection`) | |
| `add_collision` | `objects`, `settings` JSON (`thickness_outer, thickness_inner, damping, friction, cloth_friction, use_culling, permeability, stickiness, absorption`) | COLLISION modifier for cloth/soft body/particles (fluids use `add_fluid_effector`). |
| `add_rigid_body` | `objects`, `type="ACTIVE"` (ACTIVE / PASSIVE), `shape="CONVEX_HULL"` (BOX / SPHERE / CAPSULE / CYLINDER / CONE / CONVEX_HULL / MESH / COMPOUND), `mass=None` (None = `rigidbody.mass_calculate` from density), `density=1000`, `friction=0.5`, `restitution=0`, `kinematic=False`, `enabled=True`, `use_deactivation=None`, `linear_damping=None`, `angular_damping=None`, `collision_margin=None`, `mesh_source="FINAL"` (BASE / DEFORM / FINAL), `collision_collections=None` (comma list of 0-19) | Creates the rigid-body world if missing (`rigidbody.world_add`), `rigidbody.object_add` per object under override. Reply per object. |
| `add_rigid_body_constraint` | `object_a`, `object_b=None`, `type="FIXED"` (FIXED / POINT / HINGE / SLIDER / PISTON / GENERIC / GENERIC_SPRING / MOTOR), `location=None` (midpoint if None), `breaking_threshold=None`, `use_breaking=None`, `settings` JSON | Empty with `rigidbody.constraint_add`; `connect_rigid_bodies(objects, type, pattern="CHAIN"|"ALL_TO_ACTIVE"|"NEAREST")` wraps `rigidbody.connect`. |
| `set_rigid_body_world` | `time_scale=None`, `substeps_per_frame=None`, `solver_iterations=None`, `use_split_impulse=None`, `frame_start=None`, `frame_end=None`, `collection=None`, `effector_weights` JSON | |
| `add_dynamic_paint` | `canvas`, `brushes` (comma list), `surface_type="PAINT"` (PAINT / DISPLACE / WEIGHT / WAVE), `format="VERTEX"` (VERTEX / IMAGE), `image_resolution=1024`, `image_output_path=None`, `uv_layer=None`, `frame_start=None`, `frame_end=None`, `settings` JSON (`use_dissolve, dissolve_speed, use_drying, dry_speed, use_spread, spread_speed, use_drip, use_shrink, wave_speed, wave_damping, wave_spring, wave_smoothness, use_antialiasing, displace_type, displace_factor, init_color_type, init_color, brush_collection`), `brush_settings` JSON (`paint_color, paint_alpha, paint_wetness, paint_source, paint_distance, use_proximity_project, ray_direction, proximity_falloff, use_smudge, smudge_strength, use_velocity_alpha, use_velocity_color, wave_type, wave_factor, solid_radius, particle_system`) | DYNAMIC_PAINT canvas on `canvas`, brush modifiers on each brush. Wetness/wear/footprint masks for game textures. Reply: surface name, output names. Note the `surface_type` enum reads only `PAINT` headless; set by assignment and report. |
| `add_ocean` | `object` (plane created if None), `resolution=16`, `size=1.0`, `spatial_size=50`, `wave_scale=1.0`, `choppiness=1.0`, `wind_velocity=30`, `depth=200`, `damping=0.5`, `wave_alignment=0`, `wave_direction=0`, `spectrum=None`, `use_foam=True`, `foam_layer_name="foam"`, `use_spray=False`, `frame_start=None`, `frame_end=None`, `geometry_mode="GENERATE"` (GENERATE / DISPLACE), `time=None` | OCEAN modifier; `bake_ocean(object, free=False)` wraps `object.ocean_bake(modifier, free)` with `filepath` set. |
| `add_wave` | `object`, `settings` JSON (`use_x, use_y, use_cyclic, use_normal, time_offset, lifetime, damping_time, falloff_radius, start_position_x, start_position_y, speed, height, width, narrowness, vertex_group, start_position_object`) | Cheap water and flag ripples. |
| `add_explode` | `object`, `particle_system=None` (created with defaults when None), `settings` JSON (`vertex_group, protect, use_edge_cut, show_unborn, show_alive, show_dead, use_size, particle_uv`) | Shatter-by-particles. |

**Run and inspect**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `bake_simulation` | `objects=None` (all if None), `what="ALL"` (ALL / FLUID_DATA / FLUID_MESH / FLUID_NOISE / FLUID_PARTICLES / FLUID_GUIDES / CLOTH / SOFT_BODY / RIGID_BODY / PARTICLES / DYNAMIC_PAINT / OCEAN / GEOMETRY_NODES), `frame_start=None`, `frame_end=None`, `free_first=False`, `timeout_s=1800` | Fluid: `fluid.bake_*` under an override with the domain active (`fluid.bake_all` for ALL); point caches: `ptcache.bake(bake=True)` per cache with `{"point_cache": cache}` in the override, or `ptcache.bake_all`; dynamic paint: `dpaint.bake`; ocean: `object.ocean_bake`; geometry nodes: the node-graph request's bake. Synchronous on Blender's main thread (the MCP server marks the tool `async` and polls `get_simulation_status` so the stdio loop stays alive, same pattern as `start_blender`). Reply per cache: frames, seconds, disk bytes, `is_baked`. |
| `free_simulation` | `objects=None`, `what="ALL"` | The inverse. |
| `get_simulation_status` | `objects=None` | Per object: modifier type, cache range, `is_baked, is_baking, is_outdated, info`, fluid `has_cache_baked_data/mesh`, `is_cache_baking_any`, cache directory size. |
| `step_simulation` | `to_frame`, `from_frame=None` | Advances frame by frame so REPLAY caches fill; reply: seconds per frame. |
| `get_particles` | `object`, `system=None`, `frame=None`, `alive_only=True`, `max=5000`, `fields="location,velocity,size,alive_state,birth_time,lifetime"` | Reads `psys.particles` from the evaluated object at the frame. Numeric perceive for particles. |
| `get_simulation_bounds` | `object`, `frames="1,10,20"` | Bounding box of the evaluated mesh (or particle cloud) per frame; catches explosions leaving the domain and cloth falling through the floor. |
| `render_simulation_sheet` | `objects`, `frames=None` (auto 8 across the cache range), `angle="front"`, `max_size=1024`, `shading="MATERIAL"`, `overlay=None` | Contact sheet across frames via the playblast path. Smoke and fire need MATERIAL or RENDERED shading (the Principled Volume from `quick_smoke`). |
| `get_fluid_stats` | `domain`, `frame` | Resolution in use (adaptive), cell count, density/flame/velocity min-max from the cache (OpenVDB files read via `pyopenvdb` when available, else the mesh cache), mesh vertex count at the frame. |

**Outputs for game engines**

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `render_flipbook` | `objects` or `collection`, `frames` or `frame_start/frame_end/step`, `columns=8`, `size=256`, `camera=None` (auto-framed ortho if None), `passes="COLOR"` (comma list of COLOR / ALPHA / NORMAL / DEPTH / MOTION / EMISSION), `transparent=True`, `engine="EEVEE"`, `samples=32`, `output_dir`, `naming="{name}_{pass}"`, `loop_blend=0` (frames to cross-fade for seamless loops, server-side), `premultiply=True`, `power_of_two=True`, `atlas_json=True` | The sprite pipeline from the presentation request extended with render passes: `comp_render_passes` (node-graph request) writes Normal, Depth (Mist or Z normalised), Vector (Cycles `use_pass_vector`, remapped to a 0..1 RG motion-vector sheet), Emission. Server packs one sheet per pass, same tile size and pivot, plus an atlas JSON with `frames, columns, rows, fps, loop`. Reply: sheet paths, tile count, a preview via `_safe_image_return`. |
| `bake_vertex_animation_texture` | `object`, `frame_start`, `frame_end`, `step=1`, `output_dir`, `mode="SOFT"` (SOFT: position offsets per vertex per frame + normals; RIGID: per-piece transforms for rigid-body debris, pivot per piece; FLUID: variable topology handled by fixed-count resample, reported), `format="OPEN_EXR"` (16-bit float), `normalize=True` (writes min/max bounds to a JSON sidecar for the shader), `uv_layer="VAT_UV"` (a second UV layer mapping each vertex to its texel row, created), `engine_preset="UNITY"` (UNITY / UNREAL / GENERIC: axis and texel origin conventions), `max_texture_size=4096` | Per frame: evaluated mesh via `new_from_object`, offsets vs frame 0 in object space, written with numpy into an EXR of width = vertex count (padded to POT when `power_of_two`), height = frames. RIGID: one row per piece with position + quaternion. Reply: texture paths, vertex count, frame count, bounds, sidecar path, and the shader contract (which channel holds what). The single most valuable VFX export for game engines. |
| `bake_to_keyframes` | `objects`, `frame_start`, `frame_end`, `step=1`, `clear_rigid_body=True`, `visual_keying=True` | Rigid bodies: `rigidbody.bake_to_keyframes(frame_start, frame_end, step)` under override; other object animation via the rigging request's `bake_action` with OBJECT bake type. Result exports as plain object animation in FBX and glTF. |
| `bake_to_shape_keys` | `object`, `frame_start`, `frame_end`, `step=1`, `modifier=None` (the sim modifier), `relative=True`, `keyframe_values=True` | Per frame: `object.modifier_apply_as_shapekey(keep_modifier=True, modifier=)` at that frame (topology must be constant; refuses otherwise with the vertex counts). `keyframe_values=True` keys each shape key's value 0 → 1 → 0 across adjacent frames so the animation plays back as morph targets (glTF exports them). Reply: shape key count, action name. |
| `realize_simulation_frame` | `object`, `frame`, `name=None`, `keep_original=True` | A frame of any sim as a plain mesh (`new_from_object` on the evaluated object at `frame`; fluid mesh, cloth, ocean, particles via `duplicates_make_real` for OBJECT/COLLECTION render types). Splash, rubble and puddle props. |
| `export_cache` | `objects`, `filepath`, `format="ALEMBIC"` (ALEMBIC / USD), `frame_start=None`, `frame_end=None`, `flatten=False`, `uvs=True`, `normals=True`, `vcolors=False`, `export_hair=False`, `export_particles=False`, `triangulate=False`, `global_scale=1.0`, `evaluation_mode="RENDER"`, `use_instancing=True`, `xsamples=1`, `gsamples=1`, `usd` JSON (`export_animation, export_hair, export_uvmaps, export_mesh_colors, export_normals, export_materials, export_subdivision, export_shapekeys, convert_orientation, export_global_forward_selection, export_global_up_selection, xform_op_mode, triangulate_meshes`) | `wm.alembic_export` / `wm.usd_export` with `selected=True`. Alembic is Unreal's geometry-cache path; USD for Unity's and Omniverse. Reply: path, bytes, frame range. `import_cache(filepath, scale, set_frame_range, is_sequence)` is the inverse (`wm.alembic_import`, creates a Mesh Sequence Cache modifier). |
| `bake_paint_to_texture` | `canvas`, `surface=None`, `frame=None`, `output_path`, `resolution=None` | Dynamic paint IMAGE surfaces already write sequences; VERTEX surfaces go through the texturing request's `bake_textures(EMIT from the colour attribute)` at `frame`. Wear and wetness masks. |
| `particles_to_mesh` | `object`, `system=None`, `frame`, `mode="INSTANCES"` (INSTANCES: `duplicates_make_real`; POINTS: a point-cloud/vertex mesh; SPRITES: camera-facing quads with size), `join=True`, `name=None` | Debris fields and foliage clumps as static meshes. |
| `import_vdb` | `filepath`, `location`, `scale`, `sequence=True` | `object.volume_import(use_sequence_detection=)`; volumes render in Cycles/EEVEE for flipbooks. `export_vdb` is the domain's own OpenVDB cache (`cache_data_format`), report the path. |

### 2.2 Tier 2

| Tool | Parameters | Behaviour |
|---|---|---|
| `make_explosion` | `location`, `size=2`, `frame_start=1`, `duration=40`, `fire=True`, `debris_object=None`, `debris_count=50`, `flipbook` JSON | Recipe: quick smoke+fire domain with an inflow sphere keyed on/off, optional rigid-body debris, bake, `render_flipbook` with COLOR + NORMAL + MOTION. |
| `make_splash` / `make_puddle` | liquid domain recipes ending in `realize_simulation_frame` or a flipbook | |
| `make_flag` / `make_cape` / `make_curtain` | cloth recipes with pin groups and wind, ending in `bake_vertex_animation_texture` | |
| `make_destruction` | `object`, `pieces=30`, `method="VORONOI_BOOLEAN"` (built-in: Voronoi cells via geometry nodes or bisect planes + boolean; Cell Fracture is NOT bundled in 4.3), `inner_material=None`, `rigid_body=True`, `constraints="NEAREST"`, `breaking_threshold=10`, `trigger_frame=1` | Fracture → rigid bodies → constraints → bake → `bake_to_keyframes` or RIGID VAT. |
| `make_rain` / `make_snow` / `make_dust` / `make_sparks` / `make_trail` | particle recipes with force fields, each with a `to_mesh` or `flipbook` output | |
| `make_ocean_tile` | ocean recipe → `bake_vertex_animation_texture` on a tileable patch with `repeat_x/y` | |
| `set_effector_weights` | `object` or `system`, `weights` JSON (`gravity, all, force, vortex, magnetic, wind, curve_guide, harmonic, charge, lennardjones, texture, boid, turbulence, drag, smokeflow, collection`) | |
| `set_point_cache` | `object`, `modifier`, `frame_start`, `frame_end`, `frame_step`, `use_disk_cache`, `filepath`, `compression` | |
| `hair_to_cards` | `object` (Curves or particle hair), `cards_per_clump`, `width`, `segments`, `texture_atlas` | Curves → ribbon meshes with UVs for game hair; uses `curves.convert_from_particle_system` when needed. |
| `motion_vectors_from_sequence` (server only) | `frames_dir`, `output` | Optical-flow fallback for EEVEE flipbooks where the Vector pass is unavailable (numpy block matching or OpenCV if present). |
| `loop_flipbook` (server only) | `sheet`, `blend_frames` | Cross-fade the tail into the head for seamless loops. |

### 2.3 Tier 3, later (do NOT build unless asked)

Fluid guides from animation, adaptive domain visualisation, boids editors, hair dynamics
tuning, VDB → sparse 3D texture atlases, GPU particle export formats (Niagara/VFX Graph
point caches: `.pcache` is straightforward, add on request).

## 3. Ripple points beyond the standard list

- `TOOLS.md` and `README.md`: sections "Physics & Fluids", "Particles & Fields",
  "Simulation Outputs".
- `/blender` skill: "Explosion to flipbook" and "Cloth to VAT" patterns and the rule
  that sims run through `bake_simulation` (async) not `execute_blender_code`.
- Server: `bake_simulation`, `render_flipbook` and `bake_vertex_animation_texture` are
  `async` tools that poll the addon (long operations must not block the stdio loop).
- Settings request: `output_dir/fluid`, `output_dir/vat`, `output_dir/flipbooks` defaults.

## 4. Blender 4.3.2 facts, verified headlessly 2026-09-09 (do not re-derive)

- Quick effects: `object.quick_smoke(style, show_flows)`, `quick_liquid(show_flows)`,
  `quick_explode(style, amount, frame_duration, frame_start, frame_end, velocity, fade)`,
  `quick_fur(density, length, radius, view_percentage, apply_hair_guides, use_noise,
  use_frizz)`. They need selected objects in an override.
- Fluid ops: `fluid.bake_all, bake_data, bake_mesh, bake_noise, bake_particles,
  bake_guides, free_all, free_data, free_mesh, free_noise, free_particles, pause_bake,
  preset_add`. `FluidModifier.fluid_type` in NONE / DOMAIN / FLOW / EFFECTOR.
  Domain: `domain_type` GAS / LIQUID; `cache_type` REPLAY / MODULAR / ALL; the
  `cache_data_format` and `cache_mesh_format` enums read only `NONE` headless (dynamic,
  depend on OpenVDB build flags): set by assignment (`'OPENVDB'`, `'UNI'`, `'BOBJ'`) in a
  try/except and report. Domain props listed in section 2.1 are all present, including
  `use_speed_vectors`, `use_flip_particles`, `export_manta_script`,
  `has_cache_baked_data`, `has_cache_baked_mesh`, `is_cache_baking_any`.
  Flow: `flow_type` SMOKE / BOTH / FIRE / LIQUID; `flow_behavior` INFLOW / OUTFLOW /
  GEOMETRY; `flow_source` reads only NONE headless (dynamic). Effector: `effector_type`
  COLLISION / GUIDE.
- Cloth `ClothSettings` and `ClothCollisionSettings` props as listed in 2.1 are present.
  Point cache: `frame_start, frame_end, frame_step, is_baked, is_baking, is_outdated,
  use_disk_cache, filepath, name, info, use_library_path, compression`. Ops:
  `ptcache.bake(bake)`, `bake_all`, `free_bake`, `free_bake_all`, `bake_from_cache`.
- Rigid body ops: `rigidbody.object_add, object_remove, objects_add, world_add,
  world_remove, bake_to_keyframes(frame_start, frame_end, step), shape_change,
  mass_calculate, constraint_add, connect`. `type` ACTIVE / PASSIVE; `collision_shape`
  BOX / SPHERE / CAPSULE / CYLINDER / CONE / CONVEX_HULL / MESH / COMPOUND;
  `mesh_source` BASE / DEFORM / FINAL; constraint `type` FIXED / POINT / HINGE / SLIDER /
  PISTON / GENERIC / GENERIC_SPRING / MOTOR. World props: `enabled, time_scale,
  substeps_per_frame, solver_iterations, use_split_impulse, point_cache, collection,
  constraints, effector_weights`.
- Soft body props listed in 2.1 are present.
- Particles: `object.particle_system_add/remove`, `object.duplicates_make_real`.
  `ParticleSettings.type` EMITTER / HAIR; `physics_type` NO / NEWTON / KEYED / BOIDS /
  FLUID; `render_type` NONE / HALO / LINE / PATH / OBJECT / COLLECTION; `emit_from` VERT /
  FACE / VOLUME; `rotation_mode` NONE / NOR / NOR_TAN / VEL / GLOB_X/Y/Z / OB_X/Y/Z.
  `Particle` props: `location, velocity, rotation, size, alive_state, birth_time,
  die_time, lifetime, is_exist, is_visible, prev_location, hair_keys`.
- Force fields: `object.effector_add(type)` with FORCE / WIND / VORTEX / MAGNET /
  HARMONIC / CHARGE / LENNARDJ / TEXTURE / GUIDE / BOID / TURBULENCE / DRAG / FLUID;
  `FieldSettings.type` adds NONE and FLUID_FLOW; `shape` POINT / LINE / PLANE / SURFACE /
  POINTS. `EffectorWeights` props as listed.
- Dynamic paint: ops `dpaint.bake, surface_slot_add, surface_slot_remove, type_toggle,
  output_toggle`; `surface_format` VERTEX / IMAGE; `init_color_type` NONE / COLOR /
  TEXTURE / VERTEX_COLOR; `surface_type` reads only PAINT headless (dynamic): assign
  DISPLACE / WEIGHT / WAVE in a try/except. Surface and brush props as listed.
- Ocean modifier props as listed; `object.ocean_bake(modifier, free)`. Wave and Explode
  props as listed. Mesh Cache: `cache_format, filepath, factor, deform_mode,
  interpolation, time_mode, play_mode, frame_start, frame_scale, eval_frame, eval_time,
  eval_factor, forward_axis, up_axis, flip_axis`. Mesh Sequence Cache: `cache_file,
  object_path, read_data, use_vertex_interpolation, velocity_scale`.
- `wm.alembic_export` kwargs: `filepath, start, end, xsamples, gsamples, sh_open,
  sh_close, selected, visible_objects_only, flatten, collection, uvs, packuv, normals,
  vcolors, orcos, face_sets, subdiv_schema, apply_subdiv, curves_as_mesh,
  use_instancing, global_scale, triangulate, quad_method, ngon_method, export_hair,
  export_particles, export_custom_properties, as_background_job, evaluation_mode,
  init_scene_frame_range`. `wm.alembic_import`: `filepath, directory, files,
  relative_path, scale, set_frame_range, validate_meshes, always_add_cache_reader,
  is_sequence, as_background_job`. `wm.usd_export` kwargs as listed in 2.1 (all present).
- `object.modifier_apply_as_shapekey(keep_modifier, modifier, report,
  use_selected_objects)`; `bpy.data.meshes.new_from_object` exists;
  `object.convert(target)` targets CURVE / MESH / CURVES / GREASEPENCIL.
- View layer passes: `use_pass_vector, use_pass_normal, use_pass_z, use_pass_emit,
  use_pass_combined, use_pass_mist, use_pass_position` (Vector is Cycles-only in
  practice; EEVEE Next has no motion-vector pass, hence the optical-flow fallback).
- `bpy.data.volumes` and `object.volume_import(filepath, directory, files,
  use_sequence_detection, align, location, rotation, scale)` exist.
- Curves ops present: `convert_from_particle_system`, `convert_to_particle_system`,
  `snap_curves_to_surface`, `surface_set`, and the edit ops.
- No fracture add-on is bundled (Cell Fracture moved to the extensions platform):
  `make_destruction` must implement its own fracture.
- Baking a fluid is synchronous and can take minutes; the MCP-side tool must be `async`
  and poll, and the addon socket handler must not be blocked by the bake (run the bake
  through `bpy.app.timers` as every handler already is, and answer status requests from
  a second timer reading `is_cache_baking_any`; verify that the timer loop stays
  responsive during `fluid.bake_all`, and if it does not, document that status is only
  available after the bake returns).

## 5. Testing (required before you report done)

Headless, `--background --factory-startup`, handlers called directly, tiny resolutions:

1. `add_rigid_body` on 5 cubes above a passive plane; `bake_simulation(RIGID_BODY, 1..48)`;
   `get_simulation_bounds` at frame 48 shows z_min ≥ plane top − margin;
   `bake_to_keyframes` writes location fcurves on all 5; export glTF and re-import: the
   animation survives.
2. `add_cloth` on a 20×20 subdivided plane pinned by a top-row vertex group over a
   collision sphere; `bake_simulation(CLOTH)`; `bake_vertex_animation_texture(SOFT, 1..24)`
   writes a POT EXR with height 24 and a sidecar with bounds; `bake_to_shape_keys` yields
   24 shape keys; `evaluate` a middle frame differs from frame 1 by > 0.
3. `add_particle_system` count 200 with `render_type=OBJECT`; `get_particles` at frame 20
   returns ≤ 200 alive particles with velocities; `particles_to_mesh(INSTANCES)` yields
   a joined mesh with vertex count = alive × instance verts.
4. `add_force_field(WIND)` changes the mean particle velocity direction between two bakes.
5. `add_fluid_domain(GAS, resolution=24)` + `add_fluid_flow(SMOKE)` on a sphere;
   `bake_simulation(FLUID_DATA, 1..10)`; `get_simulation_status` shows
   `has_cache_baked_data` True and cache files on disk; `get_fluid_stats` at frame 10
   reports density max > 0.
6. `quick_effect(EXPLODE)` on a cube under an override: an Explode modifier and particle
   system exist.
7. `add_dynamic_paint` VERTEX canvas with a moving brush; bake; the colour attribute has
   non-zero values at frame end.
8. `add_ocean` + `bake_ocean` 1..8: `is_cached` True; `realize_simulation_frame` at 4 is a
   plain mesh.
9. `export_cache(ALEMBIC)` of the cloth plane 1..24, `import_cache` into a fresh scene:
   a Mesh Sequence Cache modifier exists and the vertex count matches.
10. `render_flipbook` on the smoke domain (4 frames, 128 px, COLOR + NORMAL + DEPTH,
    EEVEE) writes three sheets of identical size and an atlas JSON with 4 frames;
    `analyze_render` reports alpha coverage between 0.02 and 0.9 on the COLOR sheet.
11. `make_destruction` on a cube into 12 pieces: 12 rigid bodies, constraints created,
    bake runs, RIGID VAT writes 12 rows.
12. Every `add_*` reply's `unset` list is empty for the documented default parameters.

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.
