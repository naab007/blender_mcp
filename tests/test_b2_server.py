"""Phase B2 server-side tests: the engine-readiness wrappers (PLAN.md s13 B2.x 13.2.1-13.2.6, R-B2-14/20/22/26/27/28,
TEST-PLAN-B2 section 2 seam checks S1-S8).

    .venv\\Scripts\\python.exe -m pytest tests\\test_b2_server.py -q

FakeConn + server_in copied from tests/test_drv_server.py (R14, no conftest). Every test asserts the EXACT
(handler key, params) the wrapper puts on the wire, zero sends on a refused parameter, and the rendering of the
add-on's reply. BLENDER_MCP_SRC=<dir containing blender_mcp/> runs the tests against another copy of the package
(red proof: the committed 2.2.0 server has none of the 22 tools, every test fails there).
"""
import importlib
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("BLENDER_MCP_SRC") or os.path.join(REPO, "src"))
sys.path.insert(0, TESTS)

B2_TOOLS = ["validate_asset", "find_mesh_issues", "compare_meshes", "apply_transforms", "fix_normals", "cleanup_mesh",
            "decimate_mesh", "generate_lods", "set_normals", "triangulate_for_export", "get_triangle_budget",
            "create_collision", "get_collision_info", "check_collision_fit", "add_socket", "list_sockets", "set_pivot",
            "snap_to_grid", "build_export_hierarchy", "export_for_engine", "batch_export", "get_export_preset"]
assert len(B2_TOOLS) == 22


class FakeConn:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}
        self.sock = object()

    def send_command(self, command_type, params=None):
        self.calls.append((command_type, params or {}))
        r = self.replies.get(command_type)
        if callable(r):
            return r(params or {})
        return r if r is not None else {"success": True}


@pytest.fixture
def server_in(tmp_path, monkeypatch):
    """Reload blender_mcp.server with a fresh settings dir and the env vars cleared."""
    sdir = tmp_path / "mcp_settings"
    monkeypatch.setenv("BLENDER_MCP_SETTINGS_DIR", str(sdir))
    for v in ("BLENDER_HOST", "BLENDER_PORT", "BLENDER_EXE", "BLENDER_DRIVER_REPLY_MAX", "BLENDER_DRIVER_BATCH_MAX"):
        monkeypatch.delenv(v, raising=False)

    def load(settings_json=None, env=None):
        sdir.mkdir(parents=True, exist_ok=True)
        if settings_json is not None:
            (sdir / "settings.json").write_text(json.dumps(settings_json), encoding="utf-8")
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        import blender_mcp.settings as settings_mod
        importlib.reload(settings_mod)
        import blender_mcp.server as server
        server = importlib.reload(server)
        fake = FakeConn()
        monkeypatch.setattr(server, "get_blender_connection", lambda: fake)
        server._fake = fake
        return server, sdir

    return load


def _one_call(server, key):
    calls = [c for c in server._fake.calls if c[0] == key]
    assert len(calls) == 1, "expected exactly one %s send, got %s" % (key, [c[0] for c in server._fake.calls])
    return calls[0][1]


def _refused(server, out, needle=None):
    assert isinstance(out, str) and out.startswith("Error:"), out
    if needle:
        assert needle in out, (needle, out)
    assert server._fake.calls == [], "a refused parameter must never reach the add-on: %s" % server._fake.calls


# ---------------------------------------------------------------- S6: the 22 tools are registered (182 + 22 = 204)
def test_tools_list_has_the_22_b2_tools(server_in):
    server, _ = server_in()
    import dump_tools_list
    names = [r["name"] for r in dump_tools_list.tools_payload(server)]
    missing = [n for n in B2_TOOLS if n not in names]
    assert not missing, missing
    assert len(names) == 204, len(names)
    base = [l.strip() for l in open(os.path.join(TESTS, "baseline_tools_2.3.txt"), encoding="ascii") if l.strip()]
    assert names[:182] == base, "the 2.3 tools must keep their order; new tools are appended"


# ---------------------------------------------------------------- 13.2.1 fixes on the wire
def test_apply_modifier_2_2_tuple_unchanged_and_apply_all(server_in):
    server, _ = server_in()
    server.apply_modifier(None, name="Cube", modifier_name="Bev")
    assert _one_call(server, "apply_modifier") == {"name": "Cube", "modifier_name": "Bev"}, "2.2 wire tuple changed"
    server._fake.calls.clear()
    server.apply_modifier(None, name="Cube", apply_all=True, keep="ARMATURE,Shrink")
    p = _one_call(server, "apply_modifier")
    assert p["name"] == "Cube" and p["apply_all"] is True and p["keep"] == "ARMATURE,Shrink", p
    server._fake.calls.clear()
    out = server.apply_modifier(None, name="Cube")                 # R-B2-20: modifier_name optional only with apply_all
    _refused(server, out, "modifier_name")


def test_export_object_name_xor_objects_and_params_on_wire(server_in):
    server, _ = server_in()
    out = server.export_object(None, name="A", objects="A,B", file_format="fbx", filepath="x.fbx")
    _refused(server, out)
    server.export_object(None, objects="Suz,Prop", file_format="fbx", filepath="x.fbx", axis_up="Y", use_tspace=True,
                         export_vertex_color="MATERIAL")
    p = _one_call(server, "export_object")
    assert p["objects"] == "Suz,Prop" and p["filepath"] == "x.fbx" and p["axis_up"] == "Y" and p["use_tspace"] is True \
        and p["export_vertex_color"] == "MATERIAL" and p["name"] is None, p
    for k in ("apply_modifiers", "axis_forward", "axis_up", "global_scale", "use_tspace", "use_triangles", "use_custom_props",
              "colors_type", "export_apply", "export_tangents", "export_extras", "export_yup", "export_attributes",
              "export_vertex_color", "export_animation_mode"):
        assert k in p, "13.2.1 param %s not on the wire" % k


def test_import_file_params_on_wire(server_in):
    server, _ = server_in()
    server._fake.replies["import_file"] = {"success": True, "imported_objects": ["A"], "objects": [{"name": "A"}], "armatures": []}
    server.import_file(None, filepath="x.glb", import_shading="FLAT", global_scale=2.0, merge_vertices=True)
    p = _one_call(server, "import_file")
    assert p["filepath"] == "x.glb" and p["import_shading"] == "FLAT" and p["global_scale"] == 2.0 and p["merge_vertices"] is True \
        and p["use_custom_normals"] is True, p
    for k in ("axis_forward", "axis_up", "global_scale", "use_custom_normals", "use_image_search", "ignore_leaf_bones", "import_shading", "merge_vertices"):
        assert k in p, k


def test_set_origin_custom_location_refused_before_connect_and_sent_as_list(server_in):
    server, _ = server_in()
    _refused(server, server.set_origin(None, name="Cube", origin_type="CUSTOM"), "location")
    _refused(server, server.set_origin(None, name="Cube", origin_type="CUSTOM", location="a,b"))
    server.set_origin(None, name="Cube", origin_type="CUSTOM", location="1,2,3")
    p = _one_call(server, "set_origin")
    assert p["origin_type"] == "CUSTOM" and list(p["location"]) == [1.0, 2.0, 3.0], p
    server._fake.calls.clear()
    server.set_origin(None, name="Cube", origin_type="BOTTOM_CENTER")
    assert _one_call(server, "set_origin")["origin_type"] == "BOTTOM_CENTER"


# ---------------------------------------------------------------- S1 + S2: 13.2.2 validation wrappers
def test_validate_asset_wire_and_refusals(server_in):
    server, _ = server_in()
    server._fake.replies["validate_asset"] = {"success": True, "verdict": "PASS", "objects": {}}
    server.validate_asset(None, objects="Prop")
    assert _one_call(server, "validate_asset") == {"objects": "Prop", "collection": None, "profile": "GENERIC", "max_triangles": None,
                                                   "require_uv": True, "require_uv2": False, "max_bone_influences": 4, "fix": False}
    server._fake.calls.clear()
    server.validate_asset(None, collection="Kit", profile="unreal", max_triangles=500, require_uv=False, require_uv2=True, max_bone_influences=8, fix=True)
    assert _one_call(server, "validate_asset") == {"objects": None, "collection": "Kit", "profile": "UNREAL", "max_triangles": 500,
                                                   "require_uv": False, "require_uv2": True, "max_bone_influences": 8, "fix": True}
    server._fake.calls.clear()
    _refused(server, server.validate_asset(None, objects="Prop", profile="PS5"), "TIMBERMESH")
    _refused(server, server.validate_asset(None, objects="Prop", collection="Kit"))
    _refused(server, server.validate_asset(None))
    out = server.validate_asset(None, objects="Prop")
    assert json.loads(out)["verdict"] == "PASS", out
    server._fake.replies["validate_asset"] = {"error": "unknown objects", "unknown": ["Nope"], "valid_count": 3}
    assert server.validate_asset(None, objects="Nope").startswith("Error: unknown objects")


def test_find_mesh_issues_wire_json_and_refusals(server_in):
    server, _ = server_in()
    server._fake.replies["find_mesh_issues"] = {"success": True, "mesh": "Open", "issue": "NON_MANIFOLD", "element": "EDGE",
                                                "count": 4, "indices": [0, 1, 2, 3], "truncated": False}
    out = server.find_mesh_issues(None, mesh="Open", issue="non_manifold")
    assert _one_call(server, "find_mesh_issues") == {"mesh": "Open", "issue": "NON_MANIFOLD", "render": False, "angle": "iso_front_right", "max_indices": 500}
    assert json.loads(out)["indices"] == [0, 1, 2, 3]
    server._fake.calls.clear()
    _refused(server, server.find_mesh_issues(None, mesh="Open", issue="HOLES"), "BOUNDARY")
    server._fake.calls.clear()
    server.find_mesh_issues(None, mesh="Open", issue="NGONS", max_indices=7, angle="front")
    assert _one_call(server, "find_mesh_issues")["max_indices"] == 7


def test_find_mesh_issues_render_returns_image(server_in, tmp_path):
    from PIL import Image as PILImage
    png = tmp_path / "issues.png"
    PILImage.new("RGB", (16, 16), (200, 30, 30)).save(png)
    server, _ = server_in()
    server._fake.replies["find_mesh_issues"] = {"success": True, "mesh": "Open", "issue": "NGONS", "element": "FACE", "count": 2,
                                                "indices": [0, 1], "truncated": False, "filepath": str(png)}
    out = server.find_mesh_issues(None, mesh="Open", issue="NGONS", render=True)
    assert _one_call(server, "find_mesh_issues")["render"] is True
    assert type(out).__name__ == "Image", type(out)
    assert not png.exists(), "the temp PNG must be removed after it is read"


def test_compare_meshes_wire_and_subset(server_in):
    server, _ = server_in()
    server._fake.replies["compare_meshes"] = {"success": True, "same": True}
    server.compare_meshes(None, mesh_a="A", mesh_b="B")
    assert _one_call(server, "compare_meshes") == {"mesh_a": "A", "mesh_b": "B", "tolerance": 1e-5, "compare": "ALL"}
    server._fake.calls.clear()
    server.compare_meshes(None, mesh_a="A", mesh_b="B", tolerance=0.01, compare="verts, order")
    assert _one_call(server, "compare_meshes")["compare"] == "VERTS,ORDER"
    server._fake.calls.clear()
    _refused(server, server.compare_meshes(None, mesh_a="A", mesh_b="B", compare="VERTS,BOGUS"), "TANGENTS")


def test_apply_fix_cleanup_wire_and_refusals(server_in):
    server, _ = server_in()
    server.apply_transforms(None, objects="A,B")
    assert _one_call(server, "apply_transforms") == {"objects": "A,B", "location": False, "rotation": True, "scale": True, "keep_children_world": True}
    server._fake.calls.clear()
    server.fix_normals(None, objects="A", mode="clear_custom", also_sharp_by_angle=30.0)
    assert _one_call(server, "fix_normals") == {"objects": "A", "mode": "CLEAR_CUSTOM", "also_sharp_by_angle": 30.0}
    server._fake.calls.clear()
    server.cleanup_mesh(None, objects="A")
    assert _one_call(server, "cleanup_mesh") == {"objects": "A", "remove_doubles": 0.0001, "delete_loose": True, "dissolve_degenerate": True,
                                                 "fill_holes": 0, "limited_dissolve": None}
    server._fake.calls.clear()
    _refused(server, server.fix_normals(None, objects="A", mode="UP"), "FLIP")
    _refused(server, server.cleanup_mesh(None, objects="A", remove_doubles=-1))
    _refused(server, server.apply_transforms(None, objects=""))
    _refused(server, server.apply_transforms(None, objects=None))


# ---------------------------------------------------------------- 13.2.3 optimisation and LODs
def test_decimate_mesh_wire_xor_and_aliases(server_in):
    server, _ = server_in()
    server.decimate_mesh(None, mesh="Sph", ratio=0.5)
    assert _one_call(server, "decimate_mesh") == {"mesh": "Sph", "ratio": 0.5, "target_triangles": None, "mode": "COLLAPSE", "angle_limit": 5.0,
                                                  "iterations": 2, "symmetry_axis": None, "vertex_group": None, "vertex_group_factor": 1.0,
                                                  "keep_uv_seams": True, "triangulate": True, "apply": True, "new_object": None}
    server._fake.calls.clear()
    server.decimate_mesh(None, mesh="Sph", target_triangles=200, mode="planar", symmetry_axis="x", new_object="Lo", apply=False)
    p = _one_call(server, "decimate_mesh")
    assert p["ratio"] is None and p["target_triangles"] == 200 and p["mode"] == "PLANAR" and p["symmetry_axis"] == "X" and p["new_object"] == "Lo" and p["apply"] is False, p
    server._fake.calls.clear()
    _refused(server, server.decimate_mesh(None, mesh="Sph"))
    _refused(server, server.decimate_mesh(None, mesh="Sph", ratio=0.5, target_triangles=100))
    _refused(server, server.decimate_mesh(None, mesh="Sph", ratio=0.5, mode="NOPE"), "UNSUBDIV")
    _refused(server, server.decimate_mesh(None, mesh="Sph", ratio=0.5, symmetry_axis="W"), "symmetry_axis")


def test_generate_lods_default_targets_and_both_refused(server_in):
    server, _ = server_in()
    server.generate_lods(None, mesh="Suz")
    p = _one_call(server, "generate_lods")
    assert p["ratios"] is None and p["triangle_targets"] is None, "R-B2-26: the wire carries what the caller passed (None, None)"
    assert p == {"mesh": "Suz", "ratios": None, "triangle_targets": None, "naming": "{name}_LOD{level}", "collection": None,
                 "parent_to_lod0": False, "mode": "COLLAPSE", "symmetry_axis": None, "keep_uv_seams": True,
                 "preserve_silhouette_group": None, "transfer_normals": False}, p
    server._fake.calls.clear()
    server.generate_lods(None, mesh="Suz", triangle_targets="400,200", collection="LODs", parent_to_lod0=True, transfer_normals=True)
    p = _one_call(server, "generate_lods")
    assert p["triangle_targets"] == "400,200" and p["ratios"] is None and p["collection"] == "LODs" and p["parent_to_lod0"] is True and p["transfer_normals"] is True
    server._fake.calls.clear()
    server.generate_lods(None, mesh="Suz", ratios="0.5,0.25,0.1")
    assert _one_call(server, "generate_lods")["ratios"] == "0.5,0.25,0.1", "an explicit default string is sent verbatim"
    server._fake.calls.clear()
    _refused(server, server.generate_lods(None, mesh="Suz", ratios="0.5", triangle_targets="100"), "not both")


def test_set_normals_triangulate_budget_wire_and_refusals(server_in):
    server, _ = server_in()
    server.set_normals(None, objects="Cube")
    assert _one_call(server, "set_normals") == {"objects": "Cube", "mode": "AUTO_SMOOTH", "angle": 30.0, "keep_sharp": True, "source_object": None, "apply": True}
    server._fake.calls.clear()
    server.set_normals(None, objects="Cube", mode="from_object", source_object="Hi")
    assert _one_call(server, "set_normals")["mode"] == "FROM_OBJECT"
    server._fake.calls.clear()
    _refused(server, server.set_normals(None, objects="Cube", mode="FROM_OBJECT"), "source_object")
    _refused(server, server.set_normals(None, objects="Cube", mode="BUMPY"), "AUTO_SMOOTH_MODIFIER")
    server.triangulate_for_export(None, objects="Cube")
    assert _one_call(server, "triangulate_for_export") == {"objects": "Cube", "quad_method": "SHORTEST_DIAGONAL", "ngon_method": "BEAUTY",
                                                           "keep_custom_normals": True, "min_vertices": 4, "as_modifier": False}
    server._fake.calls.clear()
    _refused(server, server.triangulate_for_export(None, objects="Cube", quad_method="DIAGONAL"), "LONGEST_DIAGONAL")
    _refused(server, server.triangulate_for_export(None, objects="Cube", ngon_method="FAN"), "CLIP")
    server.get_triangle_budget(None)
    assert _one_call(server, "get_triangle_budget") == {"objects": None, "budget": None, "group_by": "OBJECT"}
    server._fake.calls.clear()
    server.get_triangle_budget(None, objects="A,B", budget=100, group_by="material")
    assert _one_call(server, "get_triangle_budget") == {"objects": "A,B", "budget": 100, "group_by": "MATERIAL"}
    server._fake.calls.clear()
    _refused(server, server.get_triangle_budget(None, group_by="OWNER"), "COLLECTION")


# ---------------------------------------------------------------- 13.2.4 collision
def test_collision_wire_and_refusals(server_in):
    server, _ = server_in()
    server.create_collision(None, mesh="Tor")
    assert _one_call(server, "create_collision") == {"mesh": "Tor", "shape": "CONVEX", "profile": "UNREAL", "count": 1, "axis": "Z",
                                                     "decimate_ratio": 0.1, "padding": 0.0, "display_wire": True, "parent": True, "collection": None}
    server._fake.calls.clear()
    server.create_collision(None, mesh="Tor", shape="sliced_convex", profile="godot", count=3, axis="y", padding=0.1, parent=False, collection="Col")
    p = _one_call(server, "create_collision")
    assert p["shape"] == "SLICED_CONVEX" and p["profile"] == "GODOT" and p["count"] == 3 and p["axis"] == "Y" and p["parent"] is False and p["collection"] == "Col", p
    server._fake.calls.clear()
    _refused(server, server.create_collision(None, mesh="Tor", shape="NOPE"), "MESH_DECIMATED")
    _refused(server, server.create_collision(None, mesh="Tor", profile="PS5"), "BEVY")
    _refused(server, server.create_collision(None, mesh="Tor", count=0), "32")
    _refused(server, server.create_collision(None, mesh="Tor", count=33), "32")
    _refused(server, server.create_collision(None, mesh="Tor", axis="W"), "axis")
    server.get_collision_info(None, mesh="Tor")
    assert _one_call(server, "get_collision_info") == {"mesh": "Tor"}
    server._fake.calls.clear()
    server.check_collision_fit(None, mesh="Tor")
    assert _one_call(server, "check_collision_fit") == {"mesh": "Tor", "samples": 2000}
    server._fake.calls.clear()
    _refused(server, server.check_collision_fit(None, mesh="Tor", samples=0), "20000")
    _refused(server, server.check_collision_fit(None, mesh="Tor", samples=20001), "20000")


# ---------------------------------------------------------------- 13.2.5 sockets, pivots, hierarchy
def test_socket_pivot_grid_hierarchy_wire_and_refusals(server_in):
    server, _ = server_in()
    server.add_socket(None, parent="Suz", name="Muzzle")
    assert _one_call(server, "add_socket") == {"parent": "Suz", "name": "Muzzle", "location": "0,0,0", "rotation": "0,0,0", "space": "LOCAL",
                                               "profile": "UNREAL", "bone": None, "display_size": 0.1, "snap_to": "NONE", "vertex_index": None}
    server._fake.calls.clear()
    server.add_socket(None, parent="Suz", name="V", location="0,1,0", space="world", profile="timbermesh", snap_to="vertex", vertex_index=3)
    p = _one_call(server, "add_socket")
    assert p["location"] == "0,1,0" and p["space"] == "WORLD" and p["profile"] == "TIMBERMESH" and p["snap_to"] == "VERTEX" and p["vertex_index"] == 3, p
    server._fake.calls.clear()
    _refused(server, server.add_socket(None, parent="Suz", name=""), "name")
    _refused(server, server.add_socket(None, parent="Suz", name="X", snap_to="VERTEX"), "vertex_index")
    _refused(server, server.add_socket(None, parent="Suz", name="X", space="SIDEWAYS"), "WORLD")
    _refused(server, server.add_socket(None, parent="Suz", name="X", profile="GODOT"), "TIMBERMESH")
    _refused(server, server.add_socket(None, parent="Suz", name="X", location="a,b"))
    server.list_sockets(None)
    assert _one_call(server, "list_sockets") == {"parent": None}
    server._fake.calls.clear()
    server.set_pivot(None, objects="A,B")
    assert _one_call(server, "set_pivot") == {"objects": "A,B", "mode": "BOTTOM_CENTER", "location": None, "keep_world": True}
    server._fake.calls.clear()
    server.set_pivot(None, objects="A", mode="custom", location="2,0,0")
    assert _one_call(server, "set_pivot") == {"objects": "A", "mode": "CUSTOM", "location": "2,0,0", "keep_world": True}
    server._fake.calls.clear()
    _refused(server, server.set_pivot(None, objects="A", mode="CUSTOM"), "location")
    _refused(server, server.set_pivot(None, objects="A", mode="LEFT"), "WORLD_ORIGIN")
    server.snap_to_grid(None, objects="G")
    assert _one_call(server, "snap_to_grid") == {"objects": "G", "grid": 1.0, "axes": "XYZ", "what": "ORIGIN"}
    server._fake.calls.clear()
    server.snap_to_grid(None, objects="G", grid=0.5, axes="XY", what="bounds_min")
    assert _one_call(server, "snap_to_grid") == {"objects": "G", "grid": 0.5, "axes": "XY", "what": "BOUNDS_MIN"}
    server._fake.calls.clear()
    _refused(server, server.snap_to_grid(None, objects="G", grid=0), "grid")
    _refused(server, server.snap_to_grid(None, objects="G", axes="W"), "axes")
    _refused(server, server.snap_to_grid(None, objects="G", what="EDGE"), "BOUNDS_CENTER")
    server.build_export_hierarchy(None, root_name="Gun", meshes="A")
    assert _one_call(server, "build_export_hierarchy") == {"root_name": "Gun", "meshes": "A", "collision": None, "sockets": None, "lods": None, "profile": "UNREAL"}
    server._fake.calls.clear()
    _refused(server, server.build_export_hierarchy(None, root_name="", meshes="A"), "root_name")
    _refused(server, server.build_export_hierarchy(None, root_name="Gun", meshes=""), "meshes")
    _refused(server, server.build_export_hierarchy(None, root_name="Gun", meshes="A", profile="PS5"), "GODOT")


# ---------------------------------------------------------------- S4: get_export_preset = ONE send (H6 / R-B2-14)
def test_get_export_preset_one_send_and_refusal(server_in):
    server, _ = server_in()
    table = {"success": True, "engine": "UNREAL", "format": "fbx", "exporter": "fbx", "kwargs": {"axis_up": "Z"}, "axis": {"forward": "-Y", "up": "Z"},
             "units": {"scale_length": 0.01, "length_unit": "CENTIMETERS"}, "naming": {"collision": "UCX_{name}_{nn}", "socket": "SOCKET_{name}", "lod": "{name}_LOD{level}"}, "notes": []}
    server._fake.replies["get_export_preset"] = table
    out = server.get_export_preset(None, engine="unreal")
    assert server._fake.calls == [("get_export_preset", {"engine": "UNREAL"})], server._fake.calls
    assert json.loads(out)["kwargs"] == {"axis_up": "Z"} and json.loads(out)["units"]["scale_length"] == 0.01, out
    server._fake.calls.clear()
    _refused(server, server.get_export_preset(None, engine="PS5"), "GENERIC_GLTF")


# ---------------------------------------------------------------- S3 + S8: export_for_engine wire, manifest on the add-on path, JSON overrides
def _export_reply(params):
    fp = params["filepath"] or "C:/tmp/x.fbx"
    return {"success": True, "filepath": fp, "engine": params["engine"], "format": "fbx", "exporter": "fbx", "size_bytes": 1234,
            "manifest_path": fp + ".manifest.json", "objects": ["Gun", "Suz_LOD0"], "triangles": 968, "materials": [],
            "textures_copied": [], "validation": {"verdict": "WARN", "fail": 0, "warn": 1}, "warnings": ["scene scale_length is 1.0, Unreal expects 0.01: run set_scene_units(UNREAL)"],
            "exporter_kwargs": {"axis_up": "Z"}, "timbermesh_root": None}


def test_export_for_engine_wire_defaults_and_text(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["export_for_engine"] = _export_reply
    out = server.export_for_engine(None, objects="Gun", engine="unreal", filepath=str(tmp_path / "gun.fbx"))
    p = _one_call(server, "export_for_engine")
    assert p == {"objects": "Gun", "collection": None, "engine": "UNREAL", "filepath": str(tmp_path / "gun.fbx"), "include_hierarchy": True,
                 "include_collision": True, "include_sockets": True, "include_lods": True, "include_animation": False, "apply_modifiers": True,
                 "triangulate": True, "validate": True, "textures": "COPY", "overrides": None}, p
    assert "manifest: " in out and str(tmp_path / "gun.fbx") + ".manifest.json" in out and "WARN" in out and "set_scene_units" in out, out
    assert not (tmp_path / "gun.fbx.manifest.json").exists(), "S3: the server must not write the per-file manifest (add-on owns it, H7/R-B2-14)"
    server._fake.calls.clear()
    server.export_for_engine(None, collection="Kit", engine="godot", filepath="k.glb", include_collision=False, triangulate=False, validate=False,
                             textures="none", overrides='{"export_tangents": false, "force": true}')
    p = _one_call(server, "export_for_engine")
    assert p["objects"] is None and p["collection"] == "Kit" and p["engine"] == "GODOT" and p["include_collision"] is False and p["triangulate"] is False \
        and p["validate"] is False and p["textures"] == "NONE" and p["overrides"] == {"export_tangents": False, "force": True}, p


def test_export_for_engine_default_filepath_from_output_dir(server_in, tmp_path):
    out_dir = tmp_path / "exports"
    server, _ = server_in(settings_json={"output_dir": str(out_dir)})
    server._fake.replies["export_for_engine"] = _export_reply
    server.export_for_engine(None, objects="Gun,Suz", engine="UNREAL")
    assert _one_call(server, "export_for_engine")["filepath"] == os.path.join(str(out_dir), "Gun.fbx")
    server._fake.calls.clear()
    server.export_for_engine(None, objects="Gun", engine="BEVY")
    assert _one_call(server, "export_for_engine")["filepath"] == os.path.join(str(out_dir), "Gun.glb")
    server._fake.calls.clear()
    server.export_for_engine(None, collection="Kit", engine="TIMBERMESH")
    assert _one_call(server, "export_for_engine")["filepath"] == os.path.join(str(out_dir), "Kit.timbermesh")


def test_export_for_engine_default_filepath_none_without_output_dir(server_in):
    server, _ = server_in()                        # no output_dir -> None (the add-on picks its temp path)
    server._fake.replies["export_for_engine"] = _export_reply
    server.export_for_engine(None, objects="Gun", engine="UNREAL")
    assert _one_call(server, "export_for_engine")["filepath"] is None


def test_export_for_engine_refusals_zero_sends(server_in):
    server, _ = server_in()
    _refused(server, server.export_for_engine(None, objects="Gun", collection="Kit", engine="UNREAL"))
    _refused(server, server.export_for_engine(None, engine="UNREAL"))
    _refused(server, server.export_for_engine(None, objects="Gun", engine="PS5"), "GENERIC_FBX")
    _refused(server, server.export_for_engine(None, objects="Gun", engine="UNREAL", textures="ZIP"), "KEEP_PATHS")
    _refused(server, server.export_for_engine(None, objects="Gun", engine="UNREAL", overrides="not json"), "overrides")
    _refused(server, server.export_for_engine(None, objects="Gun", engine="UNREAL", overrides="7"), "JSON object")     # S8 scalar
    _refused(server, server.export_for_engine(None, objects="Gun", engine="UNREAL", overrides='["a"]'), "JSON object")


def test_export_for_engine_error_reply_renders_error_with_validation(server_in):
    server, _ = server_in()
    server._fake.replies["export_for_engine"] = {"error": "validation FAIL on 1 check(s): unapplied_transform; pass overrides with force true to export anyway",
                                                 "validation": {"verdict": "FAIL", "fail": 1, "warn": 0}}
    out = server.export_for_engine(None, objects="Prop", engine="UNREAL", filepath="p.fbx")
    assert out.startswith("Error: validation FAIL") and "unapplied_transform" in out, out
    assert len(server._fake.calls) == 1


# ---------------------------------------------------------------- S5: batch_export = server loop (H10 / R-B2-28)
def _scene_reply(_params):
    return {"success": True, "objects": [
        {"name": "A", "type": "MESH", "parent": None, "hidden": False},
        {"name": "B", "type": "MESH", "parent": None, "hidden": False},
        {"name": "C", "type": "MESH", "parent": None, "hidden": False},
        {"name": "Child", "type": "MESH", "parent": "A", "hidden": False},
        {"name": "Hid", "type": "MESH", "parent": None, "hidden": True},
        {"name": "Lamp", "type": "LIGHT", "parent": None, "hidden": False},
    ]}


def _export_by_root(fail=("B",)):
    def reply(params):
        if params["objects"] in fail:
            return {"error": "validation FAIL on 1 check(s): ngons"}
        return {"success": True, "filepath": params["filepath"], "triangles": 12, "objects": [params["objects"]], "manifest_path": params["filepath"] + ".manifest.json"}
    return reply


def test_batch_export_loop_roots_rows_manifest_continue(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = _scene_reply
    server._fake.replies["export_for_engine"] = _export_by_root(fail=("B",))
    out_dir = tmp_path / "kit"
    out = server.batch_export(None, engine="unity", output_dir=str(out_dir))
    kinds = [c[0] for c in server._fake.calls]
    assert kinds == ["get_scene_info", "export_for_engine", "export_for_engine", "export_for_engine"], kinds     # ONE scene call, 3 roots (Child, Hid, Lamp excluded)
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert [e["objects"] for e in exports] == ["A", "B", "C"] and all(e["engine"] == "UNITY" for e in exports)
    assert [e["filepath"] for e in exports] == [os.path.join(str(out_dir), n + ".fbx") for n in ("A", "B", "C")], [e["filepath"] for e in exports]
    assert all(e["include_hierarchy"] is False and e["validate"] is True and e["textures"] == "COPY" for e in exports)   # R-B2-33: OBJECT = no children
    assert server._fake.calls[0] == ("get_scene_info", {"limit": 0}), server._fake.calls[0]                              # R-B2-32: every root, not the first 10
    man = out_dir / "batch_manifest.json"
    assert man.exists() and ("manifest: " + str(man)) in out, out
    m = json.loads(man.read_text(encoding="utf-8"))
    assert m["engine"] == "UNITY" and m["output_dir"] == str(out_dir) and m["written_at"], m
    assert [(r["object"], r["status"]) for r in m["rows"]] == [("A", "PASS"), ("B", "FAIL"), ("C", "PASS")], m["rows"]
    assert m["rows"][1]["error"].startswith("validation FAIL") and m["rows"][1]["filepath"] is None and m["rows"][0]["triangles"] == 12, m["rows"]
    lines = out.splitlines()
    assert lines[0].startswith("PASS A ->") and lines[1].startswith("FAIL B") and lines[2].startswith("PASS C ->"), lines


def test_batch_export_stops_without_continue_on_error(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = _scene_reply
    server._fake.replies["export_for_engine"] = _export_by_root(fail=("B",))
    out = server.batch_export(None, engine="UNITY", output_dir=str(tmp_path), continue_on_error=False)
    exports = [c[1]["objects"] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert exports == ["A", "B"], exports
    m = json.loads((tmp_path / "batch_manifest.json").read_text(encoding="utf-8"))
    assert [(r["object"], r["status"]) for r in m["rows"]] == [("A", "PASS"), ("B", "FAIL")], m["rows"]


def test_batch_export_objects_given_hidden_and_naming(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = _scene_reply
    server._fake.replies["export_for_engine"] = _export_by_root(fail=())
    server.batch_export(None, objects="C,Hid,A", engine="UNREAL", output_dir=str(tmp_path), naming="prop_{name}")
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert [e["objects"] for e in exports] == ["C", "A"], [e["objects"] for e in exports]                   # Hid skipped (skip_hidden)
    assert os.path.basename(exports[0]["filepath"]) == "prop_C.fbx"
    server._fake.calls.clear()
    server.batch_export(None, objects="Hid", engine="UNREAL", output_dir=str(tmp_path), skip_hidden=False)
    assert [c[1]["objects"] for c in server._fake.calls if c[0] == "export_for_engine"] == ["Hid"]
    server._fake.calls.clear()
    out = server.batch_export(None, objects="Hid", engine="UNREAL", output_dir=str(tmp_path))
    assert out.startswith("Error: no root meshes") and [c[0] for c in server._fake.calls] == ["get_scene_info"], out


def test_batch_export_refusals_zero_sends(server_in, tmp_path):
    server, _ = server_in()
    _refused(server, server.batch_export(None, engine="PS5", output_dir=str(tmp_path)), "GENERIC_GLTF")
    _refused(server, server.batch_export(None, engine="UNITY", output_dir=str(tmp_path), one_file_per="FILE"), "COLLECTION")
    _refused(server, server.batch_export(None, engine="UNITY", output_dir=str(tmp_path), naming="prop"), "{name}")
    _refused(server, server.batch_export(None, engine="UNITY", output_dir=str(tmp_path), one_file_per="COLLECTION"), "collection")
    server._fake.replies["get_scene_info"] = {"error": "no scene"}
    out = server.batch_export(None, engine="UNITY", output_dir=str(tmp_path))
    assert out == "Error: no scene" and [c[0] for c in server._fake.calls] == ["get_scene_info"], out


def test_batch_export_one_file_per_collection_sends_all_roots_in_one_call(server_in, tmp_path):
    server, _ = server_in()
    def scene(_p):
        r = _scene_reply(_p)
        for o in r["objects"]:
            o["collections"] = ["Kit"] if o["name"] in ("A", "B") else ["Other"]
        return r
    server._fake.replies["get_scene_info"] = scene
    server._fake.replies["export_for_engine"] = _export_by_root(fail=())
    server.batch_export(None, collection="Kit", engine="GODOT", output_dir=str(tmp_path), one_file_per="COLLECTION")
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert len(exports) == 1 and sorted(exports[0]["objects"].split(",")) == ["A", "B"] and exports[0]["collection"] is None, exports
    assert os.path.basename(exports[0]["filepath"]) == "Kit.glb", exports[0]["filepath"]


# ---------------------------------------------------------------- B2-G3.5 review findings (docs/REVIEW-B2-server.md): RS-1, RS-4, RS-8, RS-12
def test_rs1_get_scene_info_limit_forwarded_only_when_given(server_in):
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = {"success": True, "objects": [], "object_count": 0}
    server.get_scene_info(None)
    assert server._fake.calls == [("get_scene_info", {})], "the 2.2 wire tuple must stay unchanged without limit"
    server._fake.calls.clear()
    server.get_scene_info(None, limit=0)
    assert server._fake.calls == [("get_scene_info", {"limit": 0})], server._fake.calls
    server._fake.calls.clear()
    server.get_scene_info(None, limit=25)
    assert server._fake.calls == [("get_scene_info", {"limit": 25})], server._fake.calls


def test_rs1_batch_export_asks_for_every_root(server_in, tmp_path):
    server, _ = server_in()
    many = {"success": True, "objects": [{"name": "R%02d" % i, "type": "MESH", "parent": None, "hidden": False} for i in range(15)]}
    server._fake.replies["get_scene_info"] = many
    server._fake.replies["export_for_engine"] = _export_by_root(fail=())
    server.batch_export(None, engine="UNITY", output_dir=str(tmp_path))
    assert server._fake.calls[0] == ("get_scene_info", {"limit": 0}), server._fake.calls[0]
    exports = [c[1]["objects"] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert exports == ["R%02d" % i for i in range(15)], exports


def test_rs4_batch_export_expands_globs_and_sanitises_stems(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = {"success": True, "objects": [
        {"name": "Prop_A", "type": "MESH", "parent": None, "hidden": False},
        {"name": "Prop_B", "type": "MESH", "parent": None, "hidden": False},
        {"name": "Other", "type": "MESH", "parent": None, "hidden": False},
        {"name": "My Prop?", "type": "MESH", "parent": None, "hidden": False},
        {"name": "Prop_Hidden", "type": "MESH", "parent": None, "hidden": True},
    ]}
    server._fake.replies["export_for_engine"] = _export_by_root(fail=())
    out = server.batch_export(None, objects="Prop*", engine="UNITY", output_dir=str(tmp_path))
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert [e["objects"] for e in exports] == ["Prop_A", "Prop_B"], [e["objects"] for e in exports]     # glob expanded, hidden dropped
    assert all("*" not in e["filepath"] for e in exports) and os.path.basename(exports[0]["filepath"]) == "Prop_A.fbx", exports
    assert "Error" not in out, out
    server._fake.calls.clear()
    server.batch_export(None, objects="My Prop?", engine="UNITY", output_dir=str(tmp_path))
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert len(exports) == 1 and os.path.basename(exports[0]["filepath"]) == "My_Prop_.fbx", exports     # stem sanitised, object name intact
    assert exports[0]["objects"] == "My Prop?", exports[0]["objects"]
    server._fake.calls.clear()
    out = server.batch_export(None, objects="Nope*", engine="UNITY", output_dir=str(tmp_path))
    assert out.startswith("Error: no root meshes") and [c[0] for c in server._fake.calls] == ["get_scene_info"], out


def test_rs4_export_for_engine_default_stem_sanitised(server_in, tmp_path):
    out_dir = tmp_path / "exports"
    server, _ = server_in(settings_json={"output_dir": str(out_dir)})
    server._fake.replies["export_for_engine"] = _export_reply
    server.export_for_engine(None, objects="Prop*", engine="UNREAL")
    fp = _one_call(server, "export_for_engine")["filepath"]
    assert os.path.basename(fp) == "Prop_.fbx" and "*" not in fp, fp
    server._fake.calls.clear()
    server.export_for_engine(None, objects="???", engine="UNREAL")
    assert os.path.basename(_one_call(server, "export_for_engine")["filepath"]) == "export.fbx"


def test_rs8_one_file_per_flag_on_the_wire(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = _scene_reply
    server._fake.replies["export_for_engine"] = _export_by_root(fail=())
    server.batch_export(None, engine="UNITY", output_dir=str(tmp_path), one_file_per="OBJECT")
    assert all(c[1]["include_hierarchy"] is False for c in server._fake.calls if c[0] == "export_for_engine"), "OBJECT = one file per object, no children (R-B2-33)"
    server._fake.calls.clear()
    server.batch_export(None, engine="UNITY", output_dir=str(tmp_path), one_file_per="ROOT")
    assert all(c[1]["include_hierarchy"] is True for c in server._fake.calls if c[0] == "export_for_engine"), "ROOT keeps the hierarchy"
    server._fake.calls.clear()
    server._fake.replies["get_scene_info"] = lambda p: {"success": True, "objects": [dict(o, collections=["Kit"]) for o in _scene_reply(p)["objects"]]}
    server.batch_export(None, collection="Kit", engine="GODOT", output_dir=str(tmp_path), one_file_per="COLLECTION")
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert len(exports) == 1 and exports[0]["include_hierarchy"] is True, exports


def test_rs12_driver_cap_setting_zero_or_negative_falls_back(server_in):
    server, _ = server_in(settings_json={"driver_reply_max_chars": 0})
    assert server._driver_cap(None) == 24000
    assert server._driver_cap(500) == 500
    server, _ = server_in(settings_json={"driver_reply_max_chars": -5})
    assert server._driver_cap(None) == 24000
    server, _ = server_in(settings_json={"driver_reply_max_chars": 3000})
    assert server._driver_cap(None) == 3000
    server, _ = server_in(env={"BLENDER_DRIVER_REPLY_MAX": "0"})
    assert server._driver_cap(None) == 24000
    server.describe(None, what="Object")
    assert _one_call(server, "drv_describe")["max_chars"] == 24000, "a 0 setting must never reach the wire"


# ---------------------------------------------------------------- RS-13 (R-B2-38) and RS-14 (R-B2-37), server f15808b3
def test_rs13_triangulate_requires_apply_modifiers_zero_sends(server_in, tmp_path):
    server, _ = server_in()
    server._fake.replies["export_for_engine"] = _export_reply
    out = server.export_for_engine(None, objects="Gun", engine="UNREAL", filepath=str(tmp_path / "g.fbx"), triangulate=True, apply_modifiers=False)
    _refused(server, out, "requires apply_modifiers=True")
    for tri, mods in ((True, True), (False, False), (False, True)):
        server._fake.calls.clear()
        server.export_for_engine(None, objects="Gun", engine="UNREAL", filepath=str(tmp_path / "g.fbx"), triangulate=tri, apply_modifiers=mods)
        p = _one_call(server, "export_for_engine")
        assert p["triangulate"] is tri and p["apply_modifiers"] is mods, (tri, mods, p)


def test_rs14_set_origin_isolated_text(server_in):
    server, _ = server_in()
    server._fake.replies["set_origin"] = {"success": True, "name": "A", "origin_type": "BOTTOM_CENTER", "offset_applied": [0, 0, -1], "new_location": [0, 0, -1], "isolated": True}
    out = server.set_origin(None, name="A", origin_type="BOTTOM_CENTER")
    assert "isolated" in out, out
    server._fake.replies["set_origin"] = {"success": True, "name": "A", "origin_type": "BOTTOM_CENTER", "offset_applied": [0, 0, -1], "new_location": [0, 0, -1], "isolated": False}
    assert "isolated" not in server.set_origin(None, name="A", origin_type="BOTTOM_CENTER")
    server._fake.replies["set_origin"] = {"success": True, "name": "A", "origin_type": "ORIGIN_GEOMETRY", "new_location": [0, 0, 0]}
    out = server.set_origin(None, name="A", origin_type="ORIGIN_GEOMETRY")
    assert "isolated" not in out and "ORIGIN_GEOMETRY" in out, out


def test_rs15_batch_export_exact_name_before_glob(server_in, tmp_path):
    # R-B2-41 / RS-15: a literal name is always addressable; fnmatch only when nothing matches exactly
    server, _ = server_in()
    server._fake.replies["get_scene_info"] = {"success": True, "objects": [
        {"name": "Panel[1]", "type": "MESH", "parent": None, "hidden": False},
        {"name": "Panel1", "type": "MESH", "parent": None, "hidden": False},
        {"name": "Panel2", "type": "MESH", "parent": None, "hidden": False},
    ]}
    server._fake.replies["export_for_engine"] = _export_by_root(fail=())
    out = server.batch_export(None, objects="Panel[1]", engine="UNITY", output_dir=str(tmp_path))
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert [e["objects"] for e in exports] == ["Panel[1]"], [e["objects"] for e in exports]   # exact hit wins over the [1] char class
    assert "Error" not in out, out
    server._fake.calls.clear()
    server.batch_export(None, objects="Panel[12]", engine="UNITY", output_dir=str(tmp_path))
    exports = [c[1] for c in server._fake.calls if c[0] == "export_for_engine"]
    assert [e["objects"] for e in exports] == ["Panel1", "Panel2"], [e["objects"] for e in exports]   # no exact hit: glob as before
    server._fake.calls.clear()
    server._fake.replies["get_scene_info"] = {"success": True, "objects": [
        {"name": "Q?Mark", "type": "MESH", "parent": None, "hidden": False},
        {"name": "QxMark", "type": "MESH", "parent": None, "hidden": False},
    ]}
    server.batch_export(None, objects="Q?Mark", engine="UNITY", output_dir=str(tmp_path))
    assert [c[1]["objects"] for c in server._fake.calls if c[0] == "export_for_engine"] == ["Q?Mark"]   # exact beats the ? wildcard
    server._fake.calls.clear()
    server._fake.replies["get_scene_info"]["objects"].pop(0)
    server.batch_export(None, objects="Q?Mark", engine="UNITY", output_dir=str(tmp_path))
    assert [c[1]["objects"] for c in server._fake.calls if c[0] == "export_for_engine"] == ["QxMark"]   # no exact hit: fallback glob


def test_rs16_close_blender_force_without_managed_process_asks_to_quit(server_in, monkeypatch):
    # R-B2-45 (F-B2-5): no managed process + force=True -> quit_blender over the socket, never a "terminated" claim
    import asyncio
    server, _ = server_in()
    monkeypatch.setattr(server, "_blender_process", None)
    probes = iter([True, False])          # reachable once (so the graceful path runs), then gone
    monkeypatch.setattr(server, "_probe_port", lambda h, p, timeout=1.0: next(probes, False))
    out = asyncio.run(server.close_blender(None, force=True))
    assert [c for c in server._fake.calls] == [("quit_blender", {"save_prompt": False, "save": False})], server._fake.calls
    assert "asked to quit" in out and "terminated" not in out, out
