# b2_oracle: the exact handler calls and expected replies for engine readiness (PLAN.md 13.8)

Owner: Barbara (Planner). Written 2026-10-21 from PLAN.md section 13 at B2.3 (hash fdb3ce31). QA (Margaret)
turns these into `tests/b2_steps.py` (step ids F/V/O/C/S/E/G of `docs/TEST-PLAN-B2.md`); Linus runs them before
posting a landing; Grace's FakeConn tests pin the wire tuples the server emits for the same calls. A disagreement
between the two halves is a contract question for the Planner, not a negotiation between coders. Everything
below uses the harness helpers (`call`, `ok`, `err`, `make_cube`, `clear_scene`, `step`) of
`tests/headless_handlers.py`; `call(cmd, **params)` goes through `srv.execute_command`, so a handler reply is
`{"status": "success", "result": {...}}` and `ok(res)` returns the inner dict. "error status" below = `ok()`
fails because the result carries an `error` key. ASCII only. Degrees everywhere (PLAN H14). Counts named
`triangles` are evaluated (E13).

Conventions the oracle relies on (PLAN 13.2):
- `objects` is a comma list with fnmatch globs OR `collection=` names a collection; both or neither -> error;
  an unknown name -> `{error, unknown: [...]}` and NOTHING applied.
- Enum params refused with `{error, valid: [...]}` in the add-on (the server refuses first with zero sends).
- Every mutating handler leaves OBJECT mode and the caller's active/selection; the harness `step()` asserts it.
- Fixtures are built with bpy primitives, never with the handler under test. Oracle rulings get numbered O5+ in
  `docs/LEAD-RULINGS-B2.md`.

## Fixtures (bpy, per step; names used below)

```python
def _prop():            # unapplied scale + rotation, origin at centre
    o = make_cube("Prop"); o.scale = (2, 1, 1); o.rotation_euler = (0, 0, math.radians(45)); return o
def _suz():             # monkey, 500 faces, ngons 0, open eye sockets (boundary edges)
    bpy.ops.mesh.primitive_monkey_add(); o = bpy.context.active_object; o.name = "Suz"; return o
def _sphere():          # 512 faces, 960 evaluated triangles... see E3: 32x16 UV sphere
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16); o = bpy.context.active_object; o.name = "Sph"; return o
def _torus():           # C1 hull fixture, b2check.collision: 288 faces source
    bpy.ops.mesh.primitive_torus_add(major_segments=24, minor_segments=12); o = bpy.context.active_object; o.name = "Tor"; return o
def _ngon_cyl():        # ngon caps -> tangents abort (E5)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12); o = bpy.context.active_object; o.name = "Cyl"; return o
```

## F: section-1 fixes (L1)

```python
# F1 export_object: new params + counts
o = _suz()
r = ok(call("export_object", name="Suz", filepath=P("f1.fbx"), file_format="fbx", use_tspace=True, use_triangles=True,
            axis_forward="-Y", axis_up="Z", use_custom_props=True),
       must=["filepath", "size_bytes", "object_count", "vertices_written", "triangles_written", "warnings", "transform_baked"])
assert r["object_count"] == 1 and r["triangles_written"] == 968 and r["size_bytes"] == os.path.getsize(P("f1.fbx"))
assert r["transform_baked"] is False                          # FBX never bakes
assert any("no UV layer" in w for w in r["warnings"]) is False  # Suzanne has UVs
o2 = _prop()
r = ok(call("export_object", name="Prop", filepath=P("f1b.glb"), file_format="glb", export_tangents=True))
assert any("unapplied scale on Prop" in w for w in r["warnings"])
r = ok(call("export_object", objects="Suz,Prop", filepath=P("f1c.obj"), file_format="obj"))
assert r["object_count"] == 2 and isinstance(r["transform_baked"], bool)   # True on 4.3.2 (E10), False on 5.2.1: report, never assert the value
err(call("export_object", name="Suz", objects="Prop", filepath=P("x.fbx")))          # both given
msg = err(call("export_object", name="Suz", filepath=P("x.fbx"), file_format="fbx", mesh_smooth_type="NOPE"))
assert "FACE" in msg                                          # live enum tuple quoted (D9)

# F2 import_file: new params + per-object report
clear_scene()
r = ok(call("import_file", filepath=P("f1.fbx"), use_custom_normals=True, axis_forward="-Y", axis_up="Z"),
       must=["imported_objects", "armatures", "objects"])
e = [x for x in r["objects"] if x["type"] == "MESH"][0]
assert set(e) >= {"name", "type", "vertices", "triangles", "has_custom_normals", "uv_layers", "materials"}
assert e["triangles"] == 967 and e["uv_layers"] == ["UVMap"] and e["has_custom_normals"] is True   # E19: Blender's FBX use_triangles path drops one Suzanne face (968 written -> 967 read), not add-on behaviour
r = ok(call("import_file", filepath=P("f1b.glb"), import_shading="FLAT", merge_vertices=False))
assert "ignored" not in r or r["ignored"] == []               # both kwargs exist on both installs (E9)

# F3 get_mesh_stats (readonly): evaluated triangles + 13 keys
clear_scene(); o = make_cube("Cube"); m = o.modifiers.new("S", "SUBSURF"); m.levels = 2
r = ok(call("get_mesh_stats", name="Cube"))
assert r["triangles"] == 192 and r["triangles_simple"] == 12                          # E13
assert r["ngons"] == 0 and r["quads"] == 6 and r["tris"] == 0
assert r["loose_verts"] == 0 and r["loose_edges"] == 0 and r["non_manifold_edges"] == 0
assert r["has_custom_normals"] is False and r["uv_layers"] == ["UVMap"] and r["color_attributes"] == []
assert r["material_slots"] == [] and len(r["dimensions"]) == 3
assert r["unapplied_transform"] == {"rotation": False, "scale": False, "negative_scale": False}
assert abs(r["origin_offset"]["to_bbox_bottom"][2] - (-1.0)) < 1e-6 and r["origin_offset"]["to_bbox_center"] == [0.0, 0.0, 0.0]
o.scale = (1, 1, -1)
assert ok(call("get_mesh_stats", name="Cube"))["unapplied_transform"]["negative_scale"] is True

# F4 get_scene_info (readonly): totals + per-object triangles + hidden
o.scale = (1, 1, 1); o.hide_set(True)
r = ok(call("get_scene_info"))
assert r["total_triangles"] == 192 and r["total_vertices"] == 8
e = [x for x in r["objects"] if x["name"] == "Cube"][0]
assert e["triangles"] == 192 and e["hidden"] is True        # R-B2-17 (7)
ch = make_cube("Kid"); ch.parent = o; ch.hide_set(True)
r = ok(call("get_scene_info")); k = [x for x in r["objects"] if x["name"] == "Kid"][0]
assert k["parent"] == "Cube" and k["hidden"] is True                                   # R-B2-28: parented, hidden child
assert [x for x in r["objects"] if x["name"] == "Cube"][0]["parent"] is None            # a root carries parent null
assert [x for x in r["objects"] if x["name"] == "Cube"][0]["collections"] == ["Collection"]   # R-B2-30
o.hide_set(False); bpy.data.objects.remove(ch)

# F5 apply_modifier(apply_all, keep)
o.modifiers.new("A", "ARMATURE"); o.modifiers.new("B", "BEVEL")
r = ok(call("apply_modifier", name="Cube", apply_all=True, keep="ARMATURE"))
assert r["applied"] == ["S", "B"] and r["kept"] == ["A"] and r["failed"] == {}
assert [m.name for m in o.modifiers] == ["A"]
err(call("apply_modifier", name="Cube"))                       # neither modifier_name nor apply_all

# F6 set_origin BOTTOM_CENTER / TOP_CENTER / CUSTOM on a ROTATED object: world coords unchanged
clear_scene(); o = _prop(); before = [o.matrix_world @ v.co for v in o.data.vertices]
r = ok(call("set_origin", name="Prop", origin_type="BOTTOM_CENTER"), must=["origin_type", "new_location", "offset_applied"])
after = [o.matrix_world @ v.co for v in o.data.vertices]
assert all((a - b).length < 1e-5 for a, b in zip(before, after))
assert abs(min((o.matrix_world @ v.co).z for v in o.data.vertices) - o.location.z) < 1e-5     # origin at the bottom
r = ok(call("set_origin", name="Prop", origin_type="CUSTOM", location="1,2,3"))
assert [round(c, 5) for c in o.location] == [1.0, 2.0, 3.0]
err(call("set_origin", name="Prop", origin_type="CUSTOM"))                                      # no location
msg = err(call("set_origin", name="Prop", origin_type="NOPE")); assert "BOTTOM_CENTER" in msg
```

## V: validation (L1)

```python
# V1 validate_asset doc step 1 (fix=False is READ-ONLY: readonly=True on the step)
clear_scene(); o = _prop()
r = ok(call("validate_asset", objects="Prop"), must=["profile", "verdict", "summary", "objects", "fixes_available", "fixes_applied"])
c = r["objects"]["Prop"]["checks"]
assert len(c) == 22 and set(c) == CHECK_IDS                   # CHECK_IDS = the 22 ids of PLAN 13.2.2
assert c["unapplied_transform"]["status"] == "FAIL" and c["origin_bottom_center"]["status"] == "WARN"
assert r["verdict"] == "FAIL" and r["summary"] == {"pass": 0, "warn": 0, "fail": 1}      # counts OBJECTS (R-B2-17 (6))
assert any(f["check"] == "unapplied_transform" for f in r["fixes_available"]) and r["fixes_applied"] == []
assert bpy.context.mode == "OBJECT" and tuple(o.scale) == (2, 1, 1)                      # nothing changed
r = ok(call("validate_asset", objects="Prop", fix=True))
assert tuple(round(s, 6) for s in o.scale) == (1, 1, 1)
assert [f["check"] for f in r["fixes_applied"]][:1] == ["unapplied_transform"]
r = ok(call("validate_asset", objects="Prop", require_uv=False))
assert r["verdict"] in ("PASS", "WARN") and r["objects"]["Prop"]["checks"]["unapplied_transform"]["status"] == "PASS"

# V2 topology checks, one fixture each; sample_indices <= 20
clear_scene(); o = _ngon_cyl()
r = ok(call("validate_asset", objects="Cyl", profile="UNITY", require_uv=False))
assert r["objects"]["Cyl"]["checks"]["ngons"]["status"] == "FAIL" and r["objects"]["Cyl"]["checks"]["ngons"]["count"] == 2   # E5
r = ok(call("validate_asset", objects="Cyl", profile="GENERIC", require_uv=False))
assert r["objects"]["Cyl"]["checks"]["ngons"]["status"] == "WARN"
clear_scene(); o = make_cube("Flip"); ok(call("fix_normals", objects="Flip", mode="FLIP"))
r = ok(call("validate_asset", objects="Flip", require_uv=False))
assert r["objects"]["Flip"]["checks"]["inconsistent_normals"] == dict(status="FAIL", count=6, detail=ANY, sample_indices=ANY)
assert len(r["objects"]["Flip"]["checks"]["inconsistent_normals"]["sample_indices"]) <= 20
# ... DOUBLES: cube + duplicated verts within 1e-5; LOOSE: a lone vertex; DEGENERATE: a zero-area face
# NON_MANIFOLD (R-B2-29): boundary edges are NOT non-manifold for validate_asset
clear_scene(); bpy.ops.mesh.primitive_plane_add(); pl = bpy.context.active_object; pl.name = "Plane"
c = ok(call("validate_asset", objects="Plane", require_uv=False))["objects"]["Plane"]["checks"]["non_manifold"]
assert c["status"] == "PASS" and c["count"] == 0 and "boundary: 4" in c["detail"]
assert ok(call("get_mesh_stats", name="Plane"))["boundary_edges"] == 4 and ok(call("get_mesh_stats", name="Plane"))["non_manifold_edges"] == 4   # Blender's definition keeps boundary
bm = bmesh.new(); bm.from_mesh(pl.data); v = bm.verts.new((3, 3, 0)); bm.verts.ensure_lookup_table(); bm.edges.new((bm.verts[0], v)); bm.to_mesh(pl.data); bm.free()
c = ok(call("validate_asset", objects="Plane", require_uv=False))["objects"]["Plane"]["checks"]["non_manifold"]
assert c["status"] == "WARN" and c["count"] == 1                                               # the wire edge

# V7 names and structure per profile (E12, R-B2-17 (5))
clear_scene(); o = make_cube("My Cube.001"); col = make_cube("Col"); col.parent = o; col.display_type = "WIRE"; col.hide_render = True
r = ok(call("validate_asset", objects="My Cube.001", profile="UNREAL", require_uv=False))
c = r["objects"]["My Cube.001"]["checks"]
assert c["illegal_names"]["status"] == "WARN" and c["hierarchy"]["status"] == "FAIL"   # WIRE child not named UCX_
col.name = "UCX_My Cube.001_00"
assert ok(call("validate_asset", objects="My Cube.001", profile="UNREAL", require_uv=False))["objects"]["My Cube.001"]["checks"]["hierarchy"]["status"] == "PASS"
clear_scene(); make_cube("Solo")
assert ok(call("validate_asset", objects="Solo", profile="UNREAL", require_uv=False))["objects"]["Solo"]["checks"]["hierarchy"]["status"] == "PASS"   # no children = PASS

# V8 find_mesh_issues
clear_scene(); o = _suz()
r = ok(call("find_mesh_issues", mesh="Suz", issue="BOUNDARY"), must=["mesh", "issue", "element", "count", "indices", "truncated"])
assert r["element"] == "EDGE" and r["count"] > 0 and len(r["indices"]) == r["count"] and r["truncated"] is False
r = ok(call("find_mesh_issues", mesh="Suz", issue="BOUNDARY", max_indices=3))
assert len(r["indices"]) == 3 and r["truncated"] is True
msg = err(call("find_mesh_issues", mesh="Suz", issue="NOPE")); assert "BOUNDARY" in msg
res = call("find_mesh_issues", mesh="Suz", issue="NGONS", render=True)
assert res["result"]["background"] is True and "GUI" in res["result"]["error"]           # R20 headless
assert bpy.context.mode == "OBJECT"

# V9 compare_meshes
clear_scene(); a = _suz(); b = a.copy(); b.data = a.data.copy(); b.name = "Suz2"; bpy.context.collection.objects.link(b)
r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="Suz2"), must=["same", "unit_test_compare", "vertices", "triangles", "positions", "order_match", "custom_normals", "uvs", "tangents", "materials", "vertex_groups"])
assert r["same"] is True and r["unit_test_compare"] == "Same" and r["order_match"] is True and r["positions"]["differing"] == 0
b.data.vertices[0].co.x += 0.01
r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="Suz2"))
assert r["same"] is False and r["positions"]["differing"] == 1 and abs(r["positions"]["max_delta"] - 0.01) < 1e-6
r = ok(call("compare_meshes", mesh_a="Suz", mesh_b="Suz2", compare="UVS,MATERIALS"))
assert r["uvs"]["layers_a"] == ["UVMap"] and r["materials"]["same"] is True
assert "positions" not in r and "tangents" not in r and set(r) >= {"mesh_a", "mesh_b", "same", "unit_test_compare", "vertices", "triangles"}   # R-B2-27: unrequested sections omitted

# V10 apply_transforms on a rotated parent with a child: world coords unchanged
clear_scene(); p = _prop(); ch = make_cube("Child"); ch.parent = p; ch.matrix_parent_inverse = p.matrix_world.inverted()
wc = [ch.matrix_world @ v.co for v in ch.data.vertices]
r = ok(call("apply_transforms", objects="Prop"), must=["objects", "applied", "isolated", "negative_scale", "flip_corrected"])
assert r["applied"] == {"location": False, "rotation": True, "scale": True} and tuple(round(s, 6) for s in p.scale) == (1, 1, 1)
assert all((a - (ch.matrix_world @ v.co)).length < 1e-5 for a, v in zip(wc, ch.data.vertices))
assert isinstance(r["flip_corrected"], list)                   # [] on 4.3.2, names on 5.2.1 only when a flip happened: report, never assert the content
shared = make_cube("S1"); s2 = shared.copy(); s2.name = "S2"; bpy.context.collection.objects.link(s2); shared.scale = (2, 2, 2)
assert ok(call("apply_transforms", objects="S1"))["isolated"] == ["S1"] and shared.data.users == 1

# V11 fix_normals / V12 cleanup_mesh
clear_scene(); o = make_cube("Cube")
r = ok(call("fix_normals", objects="Cube", mode="FLIP"), must=["objects", "mode", "faces_flipped", "custom_normals_cleared"])
assert r["faces_flipped"] == {"Cube": 6}
assert ok(call("fix_normals", objects="Cube", mode="OUTSIDE"))["faces_flipped"]["Cube"] == 6
r = ok(call("fix_normals", objects="Cube", mode="OUTSIDE", also_sharp_by_angle=30.0)); assert r["sharp_edges_marked"]["Cube"] == 12
r = ok(call("cleanup_mesh", objects="Cube"), must=["objects", "per_object"])
assert set(r["per_object"]["Cube"]) == {"doubles_removed", "loose_verts_removed", "loose_edges_removed", "degenerate_dissolved", "holes_filled", "faces_before", "faces_after"}
```

## O: optimisation and LODs (L2)

```python
# O1 decimate_mesh (fixture: 32x16 UV sphere, 512 faces, 960 evaluated tris)
clear_scene(); o = _sphere(); before = len(o.data.polygons)
r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5, new_object="Sph_half"),
       must=["mesh", "result_object", "mode", "ratio_used", "triangles_before", "triangles_after", "passes", "applied", "modifier"])
assert r["result_object"] == "Sph_half" and r["mode"] == "COLLAPSE" and r["applied"] is True and r["modifier"] is None
assert len(o.data.polygons) == before                        # source untouched
assert 0.4 * r["triangles_before"] <= r["triangles_after"] <= 0.6 * r["triangles_before"]
r = ok(call("decimate_mesh", mesh="Sph", target_triangles=200, new_object="Sph_200"))
assert r["passes"] <= 6 and abs(r["triangles_after"] - 200) <= 10 and len(o.data.polygons) == before
r = ok(call("decimate_mesh", mesh="Sph", ratio=0.5, mode="PLANAR", apply=False, new_object="Sph_live"))
assert r["mode"] == "DISSOLVE" and r["applied"] is False and r["modifier"] is not None   # E3 alias
err(call("decimate_mesh", mesh="Sph")); err(call("decimate_mesh", mesh="Sph", ratio=0.5, target_triangles=100))
msg = err(call("decimate_mesh", mesh="Sph", ratio=0.5, mode="NOPE")); assert "COLLAPSE" in msg

# O2 generate_lods doc step 3 (+ O2b collisions / bad ratios, R-B2-17 (9); R-B2-26 default/targets/both forms)
clear_scene(); o = _suz()
r = ok(call("generate_lods", mesh="Suz"))                      # (a) default form: ratios None + targets None -> add-on default 0.5,0.25,0.1
assert [round(l["ratio"], 3) for l in r["levels"]] == [1.0, 0.5, 0.25, 0.1] and len(r["levels"]) == 4
err(call("generate_lods", mesh="Suz_LOD0", ratios="0.5", triangle_targets="100"))   # (c) both given -> refused (the server refuses first, zero sends)
clear_scene(); o = _suz()
r = ok(call("generate_lods", mesh="Suz", ratios="0.5,0.25"), must=["source", "lod0", "levels", "collection", "custom_normals_transferred"])
assert r["lod0"] == "Suz_LOD0" and [l["name"] for l in r["levels"]] == ["Suz_LOD0", "Suz_LOD1", "Suz_LOD2"]
t = [l["triangles"] for l in r["levels"]]; assert t[0] > t[1] > t[2] and abs(t[1] / t[0] - 0.5) < 0.1 and abs(t[2] / t[0] - 0.25) < 0.1
assert bpy.data.objects["Suz_LOD1"].data.materials.keys() == bpy.data.objects["Suz_LOD0"].data.materials.keys()
r = ok(call("generate_lods", mesh="Suz_LOD0", ratios="0.5"))   # existing suffix stripped, source keeps its name
assert r["source"] == "Suz" and r["lod0"] == "Suz_LOD0"       # ... and the pre-existing Suz_LOD1 -> collision:
res = call("generate_lods", mesh="Suz_LOD0", ratios="0.5,0.25")
assert "existing" in res["result"] and "Suz_LOD1" in res["result"]["existing"]
clear_scene(); o = _suz(); n = len(o.data.polygons)
err(call("generate_lods", mesh="Suz", ratios="2,abc")); assert o.name == "Suz" and len(o.data.polygons) == n

# O3 set_normals (E4)
clear_scene(); o = make_cube("Cube")
r = ok(call("set_normals", objects="Cube", mode="AUTO_SMOOTH", angle=30.0), must=["objects", "mode", "method", "modifier_used", "has_custom_normals", "sharp_edges"])
assert r["method"] == "shade_smooth_by_angle" and r["modifier_used"] is None and r["sharp_edges"]["Cube"] == 12
r = ok(call("set_normals", objects="Cube", mode="WEIGHTED"))
assert r["method"] == "weighted_normal" and r["has_custom_normals"]["Cube"] is True
assert ok(call("set_normals", objects="Cube", mode="CLEAR_CUSTOM"))["has_custom_normals"]["Cube"] is False
res = call("set_normals", objects="Cube", mode="AUTO_SMOOTH_MODIFIER", apply=False)
# 4.3.2: error mentioning AUTO_SMOOTH (shade_auto_smooth CANCELLED); 5.2.1: method smooth_by_angle_modifier, modifier_used not None
assert ("error" in res["result"] and "AUTO_SMOOTH" in res["result"]["error"]) or res["result"]["method"] == "smooth_by_angle_modifier"
hi = _suz(); r = ok(call("set_normals", objects="Cube", mode="FROM_OBJECT", source_object="Suz")); assert r["method"] == "data_transfer" and r["has_custom_normals"]["Cube"] is True
err(call("set_normals", objects="Cube", mode="FROM_OBJECT"))   # no source

# O4 triangulate_for_export / O5 get_triangle_budget
clear_scene(); o = make_cube("Cube")
r = ok(call("triangulate_for_export", objects="Cube"), must=["objects", "as_modifier", "per_object"])
assert r["per_object"]["Cube"] == {"triangles_before": 12, "triangles_after": 12, "modifier": None} and len(o.data.polygons) == 12
o2 = make_cube("Live"); r = ok(call("triangulate_for_export", objects="Live", as_modifier=True))
assert r["per_object"]["Live"]["modifier"] is not None and len(o2.data.polygons) == 6
r = ok(call("get_triangle_budget", budget=20), must=["group_by", "budget", "total", "items", "over_budget"])
assert r["total"] == 24 and r["items"][0]["triangles"] == 12 and r["over_budget"] is True and abs(sum(i["share"] for i in r["items"]) - 1.0) < 1e-6
assert ok(call("get_triangle_budget"))["over_budget"] is None
```

## C: collision (L2)

```python
# C1 create_collision (torus, E11)
clear_scene(); o = _torus()
r = ok(call("create_collision", mesh="Tor", shape="CONVEX", profile="UNREAL"), must=["mesh", "shape", "profile", "proxies", "parent", "collection"])
p = r["proxies"][0]; assert p["name"] == "UCX_Tor_00" and 1.7 <= p["volume_ratio"] <= 2.3 and r["parent"] == "Tor"   # R-B2-34: clean closed hull 1.885 (analytic 1.99)
hb = bmesh.new(); hb.from_mesh(bpy.data.objects["UCX_Tor_00"].data); assert all(e.is_manifold for e in hb.edges); hb.free()
px = bpy.data.objects["UCX_Tor_00"]; assert px.display_type == "WIRE" and px.hide_render is True and px.parent == o and not px.data.materials
r = ok(call("create_collision", mesh="Tor", shape="SLICED_CONVEX", count=3, profile="UNREAL"))
assert [q["name"] for q in r["proxies"]] == ["UCX_Tor_01", "UCX_Tor_02", "UCX_Tor_03"]
assert ok(call("create_collision", mesh="Tor", shape="BOX", profile="GODOT"))["proxies"][0]["name"] == "Tor-col"
assert ok(call("create_collision", mesh="Tor", shape="CONVEX", profile="GODOT"))["proxies"][0]["name"] == "Tor-convcol"
assert ok(call("create_collision", mesh="Tor", shape="SPHERE", profile="UNITY"))["proxies"][0]["name"] == "Tor_Collider"
assert ok(call("create_collision", mesh="Tor", shape="CAPSULE", profile="GENERIC"))["proxies"][0]["name"] == "Tor_COL"
msg = err(call("create_collision", mesh="Tor", shape="NOPE")); assert "CONVEX" in msg

# C2 get_collision_info (readonly) / C3 check_collision_fit
r = ok(call("get_collision_info", mesh="Tor"), must=["mesh", "proxies"])
assert {q["name"] for q in r["proxies"]} >= {"UCX_Tor_00", "Tor-col", "Tor-convcol", "Tor_Collider", "Tor_COL"} and all(q["is_child"] for q in r["proxies"])
clear_scene(); o = make_cube("Box"); ok(call("create_collision", mesh="Box", shape="BOX", profile="GENERIC"))
r = ok(call("check_collision_fit", mesh="Box"), must=["mesh", "samples", "proxies", "covered_fraction", "max_outside_distance", "inside_samples"])
assert r["covered_fraction"] > 0.95 and r["samples"] == 2000
bpy.data.objects["Box_COL"].scale = (0.5, 0.5, 0.5)
assert ok(call("check_collision_fit", mesh="Box"))["covered_fraction"] < 0.6          # discriminating (Margaret headline 6)
clear_scene(); make_cube("Lonely"); msg = err(call("check_collision_fit", mesh="Lonely")); assert "no collision proxies" in msg
```

## S: sockets, pivots, hierarchy (L3)

```python
# S1 add_socket doc step 5 (+ S1b TIMBERMESH root rule) / S2 list_sockets
clear_scene(); o = _suz()
r = ok(call("add_socket", parent="Suz", name="Muzzle", location="0,1,0", profile="UNREAL"), must=["name", "parent", "bone", "profile", "location_world", "rotation_world", "matrix_world"])
assert r["name"] == "SOCKET_Muzzle" and bpy.data.objects["SOCKET_Muzzle"].parent == o and bpy.data.objects["SOCKET_Muzzle"].type == "EMPTY"
r = ok(call("list_sockets", parent="Suz"), must=["parent", "count", "sockets"])
assert r["count"] == 1 and r["sockets"][0]["name"] == "SOCKET_Muzzle" and r["sockets"][0]["profile"] == "UNREAL"
err(call("add_socket", parent="Suz", name="Slot", profile="TIMBERMESH"))          # no # root in the chain
root = bpy.data.objects.new("#Prop", None); bpy.context.collection.objects.link(root); o.parent = root
assert ok(call("add_socket", parent="Suz", name="Slot", profile="TIMBERMESH"))["name"] == "#Slot"
arm = bpy.data.objects.new("Arm", bpy.data.armatures.new("Arm")); bpy.context.collection.objects.link(arm)
err(call("add_socket", parent="Suz", name="X", bone="nope"))                        # bone on a non-armature

# S3 set_pivot (world coords unchanged on a rotated object) / S4 snap_to_grid
clear_scene(); o = _prop(); wc = [o.matrix_world @ v.co for v in o.data.vertices]
r = ok(call("set_pivot", objects="Prop", mode="BOTTOM_CENTER"), must=["objects", "mode", "per_object"])
assert all((a - (o.matrix_world @ v.co)).length < 1e-5 for a, v in zip(wc, o.data.vertices)) and "new_location" in r["per_object"]["Prop"]
o.location = (0.3, 1.7, 0.0)
r = ok(call("snap_to_grid", objects="Prop", grid=1.0), must=["objects", "grid", "axes", "what", "per_object"])
assert [round(c, 5) for c in o.location] == [0.0, 2.0, 0.0] and r["per_object"]["Prop"]["before"][0] == 0.3
err(call("snap_to_grid", objects="Prop", grid=0))

# S5 build_export_hierarchy
clear_scene(); _suz(); ok(call("generate_lods", mesh="Suz", ratios="0.5")); ok(call("create_collision", mesh="Suz_LOD0", profile="UNREAL")); ok(call("add_socket", parent="Suz_LOD0", name="Muzzle"))
r = ok(call("build_export_hierarchy", root_name="PropRoot", meshes="Suz_LOD0", lods="Suz_LOD1", collision="UCX_Suz_LOD0_00", sockets="SOCKET_Muzzle", profile="UNREAL"),
       must=["root", "profile", "created_root", "tree", "custom_props_set"])
assert r["created_root"] is True and bpy.data.objects["Suz_LOD1"].parent.name == "PropRoot" and bpy.data.objects["Suz_LOD1"]["lod_group"] == 1
assert bpy.data.objects["UCX_Suz_LOD0_00"].parent.name == "Suz_LOD0" and r["tree"]["name"] == "PropRoot"
```

## E: engine export (L3)

```python
# E1 get_export_preset (one send; table verbatim per PLAN 13.2.6)
r = ok(call("get_export_preset", engine="UNREAL"), must=["engine", "format", "exporter", "kwargs", "axis", "units", "naming", "notes"])
assert r["format"] == "fbx" and r["kwargs"]["axis_up"] == "Z" and r["kwargs"]["apply_scale_options"] == "FBX_SCALE_NONE" and r["units"]["scale_length"] == 0.01
assert ok(call("get_export_preset", engine="BEVY"))["kwargs"]["export_tangents"] is True
assert ok(call("get_export_preset", engine="GENERIC_FBX"))["kwargs"] == {}
msg = err(call("get_export_preset", engine="NOPE")); assert "UNREAL" in msg

# E2 export_for_engine UNREAL doc step 6 (+ E2b refusal). R-B2-35 / E19: the fixture is a 32x16 UV sphere NAMED Suz
# (Blender's own FBX export with use_triangles drops one Suzanne face, 968 -> 967, regardless of doubles; the sphere round-trips 960 -> 960).
clear_scene(); o = _sphere(); o.name = "Suz"; ok(call("generate_lods", mesh="Suz", ratios="0.5,0.25")); ok(call("create_collision", mesh="Suz_LOD0", shape="SLICED_CONVEX", count=3, profile="UNREAL")); ok(call("add_socket", parent="Suz_LOD0", name="Muzzle"))
polys = len(bpy.data.objects["Suz_LOD0"].data.polygons)
r = ok(call("export_for_engine", objects="Suz_LOD0", engine="UNREAL", filepath=P("e2.fbx")),
       must=["filepath", "engine", "format", "exporter", "size_bytes", "manifest_path", "objects", "triangles", "materials", "textures_copied", "validation", "warnings", "exporter_kwargs", "timbermesh_root"])
assert os.path.exists(P("e2.fbx")) and r["manifest_path"] == P("e2.fbx") + ".manifest.json" and os.path.exists(r["manifest_path"])
m = json.load(open(r["manifest_path"])); assert m["manifest_version"] == 1 and m["preset"] == "UNREAL" and m["triangles"] == r["triangles"]
assert {"Suz_LOD0", "Suz_LOD1", "Suz_LOD2", "UCX_Suz_LOD0_00", "UCX_Suz_LOD0_01", "UCX_Suz_LOD0_02", "SOCKET_Muzzle"} <= set(r["objects"])
assert len(bpy.data.objects["Suz_LOD0"].data.polygons) == polys and not bpy.data.objects["Suz_LOD0"].modifiers       # D6 non-destructive
assert any("set_scene_units" in w for w in r["warnings"])        # scale_length 1.0
clear_scene(); ok(call("import_file", filepath=P("e2.fbx")))
assert {"Suz_LOD0", "UCX_Suz_LOD0_00", "SOCKET_Muzzle"} <= set(bpy.data.objects.keys()) and bpy.data.objects["Suz_LOD1"]["lod_group"] == 1
# E2b refusal: no file, no manifest
clear_scene(); _prop()
res = call("export_for_engine", objects="Prop", engine="UNREAL", filepath=P("e2b.fbx"))
assert "validation FAIL" in res["result"]["error"] and res["result"]["validation"]["verdict"] == "FAIL"
assert not os.path.exists(P("e2b.fbx")) and not os.path.exists(P("e2b.fbx.manifest.json"))
r = ok(call("export_for_engine", objects="Prop", engine="UNREAL", filepath=P("e2b.fbx"), overrides='{"force": true}'))
assert os.path.exists(P("e2b.fbx")) and json.load(open(r["manifest_path"]))["validation"]["verdict"] == "FAIL"

# E3 BEVY GLB doc step 7 (H22: unsplit fixture = smooth UV sphere, no seams, no custom normals)
clear_scene(); o = _sphere(); bpy.ops.object.shade_smooth()
r = ok(call("export_for_engine", objects="Sph", engine="BEVY", filepath=P("e3.glb"), triangulate=False))
assert os.path.exists(P("e3.glb")) and b"TANGENT" in open(P("e3.glb"), "rb").read()
ok(call("import_file", filepath=P("e3.glb")))
imp = [n for n in bpy.data.objects.keys() if n.startswith("Sph") and n != "Sph"][0]
c = ok(call("compare_meshes", mesh_a="Sph", mesh_b=imp))
assert c["vertices"]["delta"] == 0 and c["order_match"] is True and c["positions"]["max_delta"] < 1e-5

# E4 TIMBERMESH doc step 10 (H17: branch on addon_utils.enable success, never on version)
clear_scene(); root = bpy.data.objects.new("#Prop", None); bpy.context.collection.objects.link(root)
m = make_cube("Body"); m.parent = root; s = bpy.data.objects.new("#Socket", None); bpy.context.collection.objects.link(s); s.parent = root
import addon_utils
try:
    addon_utils.enable("timbermesh_blender_plugin", default_set=False); present = True
except Exception:
    present = False
res = call("export_for_engine", objects="#Prop", engine="TIMBERMESH", filepath=P("e4.timbermesh"), validate=False)
if present:
    r = ok(res); assert os.path.getsize(P("e4.timbermesh")) > 100 and r["timbermesh_root"] == "#Prop"
    import zlib; blob = zlib.decompress(open(P("e4.timbermesh"), "rb").read()); assert b"Prop" in blob and b"Socket" in blob
    addon_utils.disable("timbermesh_blender_plugin", default_set=False)
else:
    assert "timbermesh_blender_plugin" in res["result"]["error"] and not os.path.exists(P("e4.timbermesh"))
assert "TmpTimbermesh" not in bpy.data.collections.keys()        # temp collection removed in finally (whatever its name: assert the collection count is back)

# E5 batch_export doc step 9 is a SERVER loop: harness proves the per-root export_for_engine calls; the venv test (S5) proves the loop
# E6 set_scene_units UNREAL doc step 8 (existing B0 handler, NOT changed)
clear_scene(); make_cube("U")
r = ok(call("set_scene_units", preset="UNREAL")); assert r["scale_length"] == 0.01
r = ok(call("export_for_engine", objects="U", engine="UNREAL", filepath=P("e6.fbx"), validate=False))
assert not any("set_scene_units" in w for w in r["warnings"])
ok(call("set_scene_units", preset="UNITY"))
```

## G: cross-cutting

```python
# G7 legacy secret migration (H12, R-B2-18): both fixtures
for fx in ("legacy_secrets_1.6.0.blend", "legacy_secrets_5x.blend"):      # 5x only on 5.2.1 (4.3.2 cannot open it)
    bpy.ops.wm.open_mainfile(filepath=os.path.join(FIXTURES, fx))
    prefs = addon._prefs(); [setattr(prefs, n, "") for n in addon._SECRET_NAMES]
    addon._migrate_legacy_secrets(prefs, bpy.context.scene)
    assert prefs.hyper3d_api_key == "k-hyper" and prefs.sketchfab_api_key == "k-sketch"      # values the generator wrote
    sc = bpy.context.scene
    assert sc.get("blendermcp_hyper3d_api_key") is None
    if hasattr(sc, "bl_system_properties_get"):
        grp = sc.bl_system_properties_get(do_create=False); assert grp is None or "blendermcp_hyper3d_api_key" not in grp.keys()
    assert not hasattr(bpy.types.Scene, "blendermcp_hyper3d_api_key")       # StringProperty no longer registered
```
