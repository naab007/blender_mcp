# Code review, stage 1 (2.4.0), src/blender_mcp/server.py at a39fc368 (gate B2-G3.5, started early)

Reviewer: Lead (code-review pass, high effort, 2026-10-21 19:08Z). Every finding is fixed at the root by the owner
(Grace; RS-1 has an add-on half for Linus), QA re-runs the case + the full suite, then the pair is re-frozen.
Rulings are the Lead's (R-B2-32..). Owner ticks the item on card `b2-review-server` with the hash.

| # | Line | Finding | Ruled fix |
|---|---|---|---|
| RS-1 | 8494 | `batch_export` resolves roots/hidden from `get_scene_info`, whose object list is capped at the first 10 scene objects (addon.py:778). Roots beyond the 10th are silently never exported; `skip_hidden` is blind to them. | **R-B2-32**: `get_scene_info` gains `limit: int = 10` (0 = all), additive on server and add-on (Linus L3.1: the cap reads the param; Grace: param on the wrapper, forwarded only when given so the 2.2 wire tuple is unchanged); `batch_export` sends `limit=0`. Margaret: F-step on a 15-object scene with `limit=0` returns 15; S5 asserts `limit: 0` on the wire. |
| RS-2 | 8488 | `_B2_ENGINES` accepts `GENERIC` but `_B2_ENGINE_EXT` has no key and the lookup is outside the `try`: uncaught `KeyError`. | `_B2_ENGINES = (UNITY, UNREAL, GODOT, BEVY, TIMBERMESH, GENERIC_GLTF, GENERIC_FBX)`; `GENERIC` dropped from the three docstrings (RS-7 is the same root). |
| RS-3 | 7620 | `find_mesh_issues(render=True)` reads `result['image']['error']`; the add-on reports `image_error` (addon.py:2841), so the real capture error is replaced by a misleading text. | Read `image_error` first; copy the `find_unweighted_vertices` wrapper's handling exactly. |
| RS-4 | 8516 | The first `objects` entry is used verbatim as the file stem although the add-on accepts fnmatch globs: `Prop*.fbx` is an invalid path; `batch_export` treats the glob as one root. | `batch_export` expands globs against the `get_scene_info` names (fnmatch) before the loop; default stems are sanitised (`[^A-Za-z0-9_.-]` -> `_`), and a stem that is empty after sanitising becomes `export`. |
| RS-5 | 8504 | `collection` filter applied only when `objects` is None; with both given the collection is ignored. | Apply the `collections` membership filter to the resolved roots in BOTH branches. |
| RS-6 | 7450 | `driver_batch_max` setting / env is presented as raising the limit, but the add-on hard-codes 200. | Docstring + TOOLS.md say "lower only (the add-on caps at 200)"; server clamps the setting to `min(setting, 200)`. |
| RS-7 | 8351 | Docstrings list `GENERIC` as an engine; default-filepath path turns it into `Error: 'GENERIC'`. | Same root as RS-2. |
| RS-8 | 8513 | `one_file_per` ROOT and OBJECT are indistinguishable (both send `include_hierarchy=True`). | **R-B2-33**: OBJECT sends `include_hierarchy=False` (one file per object, no children); ROOT keeps the hierarchy. Docstring says so; Margaret S5 asserts the flag per mode. |
| RS-9 | 637 | `execute_blender_code` error branch drops the captured stdout the add-on returns under `result`. | Print the error block AND `result` (stdout) when present. |
| RS-10 | 2864 | `apply_modifier` legacy success return is unreachable (reply always carries `applied`). | Delete the dead line. |
| RS-11 | 8490 | `batch_export` re-implements output_dir resolution + makedirs instead of `_default_output_path`. | Use `_default_output_path` (same fallback behaviour as every other exporter). |
| RS-12 | 7056 | `_driver_cap` validates per-call `max_chars` but forwards the `driver_reply_max_chars` setting unchecked (0 breaks every driver read). | Guard in `_driver_cap`: a setting `<= 0` falls back to 24000 (one line); Margaret: unit test. |

Scope note: RS-6, RS-9, RS-12 touch 2.3.0 code that ships uncommitted in the same tree; they are in scope per the
user's "review at the end of each stage" directive.
