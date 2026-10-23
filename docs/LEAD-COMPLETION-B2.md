# Lead completion report, stage 1 (engine readiness 2.4.0), team lohjud, 2026-10-21 .. 2026-10-23

STATUS: COMPLETE. Commit c002722 on blender-5.2, pushed to origin 2026-10-23 (user directive). B2-G3 2026-10-22, G3.5 2026-10-22, G4 2026-10-22, G5 PASSED 2026-10-23 after a ruled reopen for four live findings. Nothing deployed to the live targets, no GitHub release.
2026-10-23 after a ruled reopen for four live findings. Committed to blender-5.2 and pushed on the user's directive
("when this stage is considered complete you can upload it to git on the 5.2 branch"); nothing deployed to the live
targets, no GitHub release.

WHAT WAS BUILT (stage 1 = docs/FEATURE-REQUEST-engine-readiness.md Tier 1, one request per stage per the user's
directive): 22 tools, 182 -> 204 (21 add-on handlers 171 -> 192 + batch_export as a server loop). Validation:
validate_asset (22 fixed check ids, 6 safe fixes), find_mesh_issues, compare_meshes, apply_transforms, fix_normals,
cleanup_mesh. Optimisation/LODs: decimate_mesh, generate_lods, set_normals, triangulate_for_export,
get_triangle_budget. Collision: create_collision (7 shapes, 6 profile naming rules), get_collision_info,
check_collision_fit. Sockets/pivots/hierarchy: add_socket, list_sockets, set_pivot, snap_to_grid,
build_export_hierarchy. Export: export_for_engine (UNITY/UNREAL/GODOT/BEVY/TIMBERMESH/GENERIC_GLTF/GENERIC_FBX
presets, add-on manifest, non-destructive temporary triangulation), batch_export, get_export_preset. Six fixes to
existing tools (export_object +16 params, import_file +8, get_mesh_stats evaluated triangles + topology keys,
get_scene_info totals/hidden/parent/collections/limit, apply_modifier apply_all/keep, set_origin
BOTTOM_CENTER/TOP_CENTER/CUSTOM). Debts closed: legacy Scene secret properties removed with a two-group migration
(#13); server get_version prints registries (#14). PROTOCOL stays 1; version 2.4.0 in all three sources.
Live-test fixes (reopen, R-B2-43/44/45): F-B2-4 dict results from execute_blender_code encoded as dicts; F-B2-5
close_blender(force=True) on an unmanaged instance asks it to quit and says so; F-B2-6 capture tools restore
view_location/view_rotation/view_distance (4.3.2 kept the capture framing); F-B2-7 Timbermesh export keeps
frame_current.

FILES CHANGED, BY WHOM (git hash-object, FINAL at the re-freeze 2026-10-23):
- addon.py 2a97736c (Linus; L1 393390af, L2 a611e0e3, L3 e9c78fe0, review ee9b9dc0, reopen 2a97736c)
- src/blender_mcp/server.py 8e3ca441 (Grace; review a2cdd77f, reopen 78df0099 -> 8e3ca441 docstring kept frozen because
  tools/list carries descriptions); __init__.py 9ce248f3, pyproject.toml 45b5c465, settings.py f877cf66
- tests/ (Margaret): b2_steps.py ba354830 (47 steps), drv_steps.py a45e37c4 (21), test_b2_server.py b70cbc20 (36),
  headless_handlers.py 416a9ad0 (--b2), test_f6_connection.py ebb3e9ae (2.4 identity test), test_b1_server.py 4df76d1b,
  test_server_units.py 1a6d03c2, test_version_sync.py 38e8d4c5, b0_steps.py a83f7da5, fixtures legacy_secrets_1.6.0.blend
  743d93ba + _5x.blend 48a1de54, baselines settings_replies_4.3.2.json d7df3d0f + _5.2.1.json df292342,
  baseline_tools_2.4.txt e24a34cd + baselines/tools_list_2.4.json 3c6026b3, live_register.py b47a716f, live_client.py
  6211aa3c, Tests.md 783a58aa, docs/TEST-PLAN-B2.md c53b8b78, docs/LIVE-TEST-REPORT-2026-10-22.md 848f6617
- docs/PLAN.md section 13 B2.2..B2.22 (Barbara), docs/apichecks/b2_oracle.md, b2check2.py 018534df, evcheck.py
  43f98c7b, docs/README.md, FEATURE-REQUEST-engine-readiness.md status, FEATURE-REQUEST-generic-driver.md s5 note
- docs/LEAD-RULINGS-B2.md (R-B2-1..47 + landing log), docs/REVIEW-B2-server.md, -addon.md, -tests.md,
  docs/HANDOFF-2026-10-21-lohjud.md + -2026-10-22-lohjud.md (Lead)
- TOOLS.md 1b2c183f, README.md df3cbe13, docs/release-notes/v2.4.0.md be044f36, ~/.claude/commands/blender.md 98b9f504
  (Grace; B2-D G4 PASSED 2026-10-22, B2-D.2 live-fix lines 2026-10-23)

HOW IT WAS VERIFIED (final lines at the re-freeze): headless harness `<install>\blender.exe --background
--factory-startup --python tests\headless_handlers.py -- --b0 --b1 --drv --b2` on 4.3.2 AND 5.2.1 = 253/253, EXIT 0,
run-time head "addon 2a97736c b2_steps ba354830 drv_steps a45e37c4", by QA from disk (tests/report_qa6_<ver>.json) and
by Linus independently; the Lead's own from-disk runs at the G3 pair were 252/252 (report_lead_g3_<ver>.json). venv
`pytest tests -q` (no env vars) 131 passed, 0 skipped at server 8e3ca441. tools/list 204 names, byte-identical to
tests/baselines/tools_list_2.4.json (identity test, no skip path); superset of the 2.3 pin in order.
settings_baseline --check match:true both installs (diff vs 2.3 = exactly the four legacy secret keys).
Red-before-green on every test landing with run-time hashes; discriminating mutants on scratch copies only (ngon rule
off, legacy secret copy kept, loop_total triangles, hull interior kept, no-readback decimate, temp Triangulate left
behind, validation refusal skipped, enum guard removed, XOR guard removed, continue_on_error ignored, engine refusal
removed, edit-mesh flush removed, dict branch removed, frame restore removed, view-field restore removed (live), server
close_blender guard reverted).
Measured facts that overturned the plan: hull volume ratio 1.99 analytic vs the 3.87 artefact (E11); Blender's FBX
use_triangles drops one Suzanne face (E19); 5.x stores registered props in a system idprop group; addon_utils.check()
does not report presence; get_scene_info entries capped at 10; EDIT-mode mesh RNA layers empty until flushed (E20);
GUI msgbus fires on publish=False too (E21); Blender undo captures state only at a push (F-B2-3, R-B2-42).
Code review (user directive, gate G3.5): 12 server + 17 add-on + 14 test findings, all fixed at the root before the
freeze; Lead re-review of the four reopen hunks: empty list.
LIVE (B2-G5, R-B2-25, QA on separate GUI instances from the dev tree, port 9877, the user's sessions untouched;
docs/LIVE-TEST-REPORT-2026-10-22.md 848f6617): the full list GREEN on 5.2.1 AND 4.3.2 at the G3 pair (render=True image,
Timbermesh 4.3.2 file / 5.2.1 teach error, ship-an-asset chain to an UNREAL FBX with viewport evidence, reads with an
object in EDIT mode, transaction rollback visible + mcp:<name> undo entries, the four 2.3.0 D-G5 driver checks,
batch_export of 3 roots). Four live findings, ALL FIXED in one ruled reopen (one add-on hash, one server hash, three test
hashes) and re-probed green on both builds at the re-frozen pair, with mut_f6 as the live red for the viewport fix.

NOT VERIFIED: a real Unreal/Unity import of the FBX (the user's engine); EMBED/KEEP_PATHS texture modes beyond kwargs;
performance above a few thousand objects; SCULPT-mode flush (only EDIT is flushed); the G5 list L1-L10 were not re-run
live at 2a97736c (verified live at ee9b9dc0; the reopen hunks are outside their paths and the harness covers them).

LEFT OUT AND WHY: engine-readiness Tier 2 (stage scope kept to Tier 1 by the user's one-request-per-stage directive);
driver events (a different request; facts measured in evcheck.py for that stage); recipes on the driver (all 22 tools are
add-on handlers, R-B2-9 seam unused).

USER DECISIONS: (1) the 2.3.0 bytes were never committed and were overwritten by this stage's landings, so 2.3.0 + 2.4.0
went as ONE commit on blender-5.2 (release notes v2.3.0.md + v2.4.0.md both ride); (2) deploy to the live targets and any
GitHub release remain the user's call; (3) stage 2 = UV and texturing (2.5.0) only on the user's go.
