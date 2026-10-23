# Recon B2 (Grace, server/docs owner), 2026-10-21

Tree: server.py `349c6a9e`, addon.py `f2c680bb`, tests/baseline_tools_2.3.txt `6393d60b`,
tests/baselines/tools_list_2.3.json `5e36e71c`. Every line number below is from grep on these bytes.

## Q1. Server-side recipes / batch helper

FINDING: none. `grep -n "recipe" src/blender_mcp/server.py` = 0 hits. There is no server function
that issues `drv_batch` / `drv_transaction` on behalf of another tool; `batch` (7313) and
`transaction` (7365) are `@mcp.tool` wrappers that take a JSON *string* and return a rendered
*string*, so a recipe cannot call them and read structured results.

Closest idiom (what a recipe seam would factor out):
- Wire payloads: `batch` -> `send_command("drv_batch", {commands: list, stop_on_error, undo: AUTO|NONE,
  refs: bool, max_chars: _driver_cap()})` at 7345-7349; `transaction` -> `send_command("drv_transaction",
  {action: BEGIN|COMMIT|ROLLBACK|STATUS, name})` at 7381; `call_operator` -> `send_command("drv_op",
  {op, params, context, exec_context, undo})` at 7204-7208; `rna_set` 7035, `call_method` 7215.
- Command shape inside a batch: `{"id"?: str, "type": <handler key, drv_* or legacy>, "params"?: {}}`,
  `"$<id>.<path>"` strings pass through unchanged (test_drv_server.py:140).
- Batch reply (raw dict): `results: [{status: ok|error|skipped, ...}]`, `rolled_back`, `undo_available`,
  `stopped_at`, `elapsed_ms` (7351-7360 reads them).
- Server-side refusals happen BEFORE any wire call and return a plain `"Error: ..."` str (batch 7330-7343:
  shape, empty list, >driver_batch_max, bad undo). `_parse_json_any` (5770) returns an `Error:` str for bad
  JSON / scalar (R31); callers discriminate with `isinstance(x, str)`.
- Add-on errors are dicts with an `error` key; `_driver_reply` (6964-6974) renders `Error: <text>` plus
  every other key as `key: json` teaching lines; success = `json.dumps(indent=2)`.
- Cap: `_driver_cap(max_chars)` (6943) = tool value else setting `driver_reply_max_chars` (24000).

Seam Barbara should specify (my proposal, not built): a private `_drv_batch(conn, commands, undo="AUTO",
stop_on_error=True, refs=True) -> dict` lifted from 7345-7349 plus a `_drv_result(reply, i)` accessor for
`results[i]`, so a recipe = build commands list -> `_drv_batch` -> inspect dict -> render its OWN reply
shape (the doc's), never `_driver_reply`'s. Transaction wrapping = BEGIN/COMMIT/ROLLBACK via the same
`send_command` calls; the batch already rolls back on failure with `undo=AUTO`, so most recipes need no
explicit transaction. Open question for the contract: what a recipe returns when one command fails
(`Error:` + which command, or the doc's reply shape with a `failed_at`).
Blocks: Barbara (contract), Linus (which parts still need an add-on handler: bmesh/numpy per doc s5).

## Q2. Section-11 tools already in server.py

| Tool | server.py signature | add-on | reply handling | section-1 state |
|---|---|---|---|---|
| export_object | 3175-3192 (15 params from B1: name, filepath, file_format, include_hierarchy, bake_anim, add_leaf_bones, use_armature_deform_only, bake_anim_simplify_factor, mesh_smooth_type, primary/secondary_bone_axis, apply_scale_options, export_animations/skins/morph) | 2773 | 3232-3248: `error`->`Error:`, else one line: filepath, format, exported_objects (first 8), fbx_options/gltf_options (first 10), ignored | NONE of the doc's engine params exist: `grep -n "axis_forward\|global_scale\|use_tspace\|use_custom_props\|export_tangents\|export_yup" addon.py src/blender_mcp/server.py` = 0 hits. No `objects` list, no size/vert/tri counts, no `warnings` |
| import_file | 3252 (`filepath` only) | 2898 | 3263-3275: imported_objects, armatures, new_actions, bone_shape_objects | no axis/scale/report params |
| set_scene_units | 4480-4487 (preset, system, scale_length, length_unit, rescale_objects) | 4710 | `_settings_reply` 4510-4512 | COMPLETE per B0 C11; B2 extends only if the contract adds something |
| get_mesh_stats | 2850 (`name`) | 2259 | json dump 2858-2860 | add-on returns vertices/edges/polygons/triangles/materials/modifiers/bounding_box only; ngons/quads/tris/loose/non_manifold/has_custom_normals/uv_layers/color_attributes/dimensions/unapplied_transform/origin_offset ABSENT (`grep -n "has_custom_normals\|non_manifold" addon.py` = 0 in that handler) |
| get_scene_info | 553 (no params) | 669 | json dump 559 | add-on already carries `units` {system, scale_length, ...} at 698; `total_triangles` = 0 hits |
| apply_modifier | 2829 (name, modifier_name) | 2245 | fixed text 2841 | `apply_all` / `keep` = 0 hits |
| set_origin | 5327 (name, origin_type) | 3269 | 5343 `new_location` | `BOTTOM_CENTER` / `TOP_CENTER` / custom = 0 hits |

So all six section-1 fixes are open; L1 is the full list. Blocks: Barbara (L1 scope is not smaller than
s11.2 says), Linus (handlers), Grace (wrappers).

## Q3. tools/list baseline mechanics

- Producer: `tests/dump_tools_list.py` (`names` -> one name per line in registration order; `json [OUT]`
  -> sorted rows {name, description, inputSchema}; `diff BASE.json`). PowerShell `>` writes UTF-16 and
  the tests open baselines with `encoding="ascii"`: write the files from Python or `cmd /c`.
- Consumers and what they assert:
  - `tests/test_f6_connection.py:223` `test_tools_list_byte_identical_to_2_3_baseline`: EXACT names+order
    == baseline_tools_2.3.txt AND exact schema per tool == tools_list_2.3.json. Files exist, so it does
    not skip: the FIRST 2.4 tool or additive param turns it RED.
  - `tests/test_f6_connection.py:189` `test_tools_list_superset_of_2_2_baseline`: superset+order vs 2.2,
    no param removed/retyped/redefaulted, `required` unchanged, AND an additive-param whitelist at
    218-220 = `{"execute_blender_code": ["result_expr"]}` only. Section-1 params on export_object /
    import_file / get_mesh_stats / apply_modifier / set_origin turn it RED until the whitelist grows.
  - `tests/test_b1_server.py:76` superset vs 2.1; `tests/test_b0_server.py:369` and
    `tests/test_server_units.py:74` superset vs 4.3 (in order). These stay green under additive change.
- A 2.4 cut needs: `tests/baseline_tools_2.4.txt` + `tests/baselines/tools_list_2.4.json` (Margaret
  regenerates at G3 from the frozen server), the 2.3 identity test re-pointed to 2.4 (or demoted to a
  superset test with the 2.4 additive whitelist), and the 2.2 whitelist extended per the ratified
  contract. Sequencing hazard: my L1 lands before the cut, so two tests are red by design between L1 and
  G3 unless Margaret's test change lands first. Needs a Lead ruling on order.
  Blocks: Margaret (owner), Grace (every landing's pytest tail), Ada (ruling).

## Q4. Counts (commands the release note cites)

- `grep -c "^@mcp.tool" src/blender_mcp/server.py` = 182 (= lines in baseline_tools_2.3.txt).
- `grep -cE '^\s+"[a-z_0-9]+": self\.[a-z_0-9]+,?\s*(#.*)?$' addon.py` = 171 handler keys
  (`handlers` dict at 428 = 21, `extended_handlers` at 455 = 150; `drv_*` = 13). Matches the 2.3.0
  release note (182 tools / 171 handler keys).
