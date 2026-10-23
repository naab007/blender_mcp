# Code review, stage 1 (2.4.0), tests/** (b2_steps.py db9241d2, test_b2_server.py a4731a0d, headless_handlers.py 416a9ad0, test_f6_connection.py 09b00ec8), gate B2-G3.5

Reviewer: Lead (code-review pass, high effort, 2026-10-21 19:2x). Owner Margaret fixes every row in one landing;
harness re-run on both installs + venv; no assertion weakened.

| # | File:line | Finding | Ruled fix |
|---|---|---|---|
| RT-1 | b2_steps.py:905 | Trailing `or True` makes the E7 mode/selection-restore assert a no-op. | Drop `or True`; split into two asserts. |
| RT-2 | b2_steps.py:1362 | Trailing `or True` neuters the hull-triangle assertions in C1. | Drop `or True`; assert `_eval_tris(px) == p0["triangles"]` and all-triangles for real. |
| RT-3 | b2_steps.py:849 | `hole` computed with a type-confused predicate and never used. | Delete the line. |
| RT-4 | b2_steps.py:1008 (+1178, 1597, 1664, 1780) | `rr["result"]` indexed unconditionally after `err()` accepts either envelope: a raised handler becomes a bare KeyError. | `rr.get("result") or {}` as at line 827. |
| RT-5 | b2_steps.py:964 | Wall-clock `dt < 2.0` on a 10k-vertex compare makes V9 flaky. | Report dt in the detail; bound raised to 20 s (a real regression, not load). |
| RT-6 | b2_steps.py:1203 | `%%` in a non-formatted string prints literally. | Single `%`. |
| RT-7 | b2_steps.py:906 | V8 detail mislabels numbers (interior count hard-coded, boundary count from the wrong result). | Capture each count into its own variable; format from measured values. |
| RT-8 | b2_steps.py:1091, 645 | Hard-coded figures in details do not match the fixtures. | Format from measured values (`len(polygons)`, `max(dimensions)`). |
| RT-9 | b2_steps.py:899 | `max_indices=0` probe asserts nothing on a refusal. | Contract (L1 deviation f, ratified): `max_indices < 1` is REFUSED; assert `err(rr, "max_indices")`. |
| RT-10 | b2_steps.py:832 | Dead `if False` conditional. | Plain `ok(call("validate_asset", collection="Kit"))`. |
| RT-11 | b2_steps.py:1936 | Tautology on dict keys. | `assert "set_scene_units" in HANDLERS` or delete. |
| RT-12 | b2_steps.py:15, 25 | Unused `import types`, unused `ADDON_IS_24` (looks like a forbidden version gate). | Delete both. |
| RT-13 | b2_steps.py:69 (+7 sites) | `__import__("bmesh")` repeated. | One top-level `import bmesh`. |
| RT-14 | test_f6_connection.py:282 | 15-line schema comparison loop duplicated between the 2.2 and 2.3 superset tests. | Factor into `_schema_problems(snapshot_path, live, relaxed) -> list`; both tests call it. |

Plus the new steps/cases named in docs/REVIEW-B2-addon.md's test column (RA-1..RA-9, RA-13, RA-17), red at
addon e9c78fe0 where a behaviour changes, green at Linus's review hash.
