"""Phase B1 server-side tests (rigging and animation wrappers).

    .venv\\Scripts\\python.exe -m pytest tests\\test_b1_server.py -q

K14: superset of tests/baseline_tools_2.1.txt (no name/param removed or retyped); param additions expected on
export_object, add_keyframe, capture_viewport_angle, capture_contact_sheet, parent_object. Image-returning
wrappers (render_weight_map, find_unweighted_vertices(render=True), playblast grid) return an MCP Image built
from the PNGs the add-on wrote. Blender socket replaced by FakeConn; settings dir pointed at a temp dir.
"""
import importlib
import io
import json
import os
import sys

import pytest
from PIL import Image as PILImage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, TESTS)

BASELINE_21 = os.path.join(TESTS, "baseline_tools_2.1.txt")
SNAPSHOT_21 = os.path.join(TESTS, "baselines", "tools_list_2.1.json")
EXPECTED_ADDITIONS = {
    "export_object": {"include_hierarchy"},
    "add_keyframe": {"bone"},
    "capture_viewport_angle": {"overlay"},
    "capture_contact_sheet": {"overlay"},
    "parent_object": {"parent_type", "bone"},
}
TIER1_TOOLS = ["create_armature", "add_bones", "get_armature_info", "set_bone_properties", "delete_bones", "bind_armature",
               "get_vertex_groups", "get_vertex_weights", "set_vertex_weights", "render_weight_map", "find_unweighted_vertices",
               "set_pose", "get_pose", "reset_pose", "add_constraint", "get_constraints", "remove_constraint", "set_keyframes",
               "get_animation_info", "playblast", "bake_action"]


class FakeConn:
    def __init__(self):
        self.calls = []
        self.replies = {}
        self.sock = object()

    def send_command(self, command_type, params=None):
        self.calls.append((command_type, params or {}))
        r = self.replies.get(command_type)
        if callable(r):
            return r(params or {})
        return r if r is not None else {"success": True}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("BLENDER_MCP_SETTINGS_DIR", str(tmp_path / "mcp_settings"))
    for v in ("BLENDER_HOST", "BLENDER_PORT", "BLENDER_EXE"):
        monkeypatch.delenv(v, raising=False)
    import blender_mcp.settings as settings_mod
    importlib.reload(settings_mod)
    import blender_mcp.server as srv
    srv = importlib.reload(srv)
    fake = FakeConn()
    monkeypatch.setattr(srv, "get_blender_connection", lambda: fake)
    srv._fake = fake
    return srv


def _write_png(path, w=8, h=8, colour=(200, 30, 30)):
    img = PILImage.new("RGB", (w, h), colour)
    img.putpixel((0, 0), (0, 0, 255))     # never a single colour
    img.save(path)
    return path


# ---------------------------------------------------------------- K14 superset
def test_k14_superset_of_2_1_baseline(server):
    import dump_tools_list
    live = {r["name"]: r for r in dump_tools_list.tools_payload(server)}
    base_names = [l.strip() for l in open(BASELINE_21, encoding="ascii") if l.strip()]
    missing = [n for n in base_names if n not in live]
    assert not missing, "tools removed vs baseline_tools_2.1.txt: %s" % missing
    positions = [list(live).index(n) for n in base_names]
    assert positions == sorted(positions), "2.1 tools no longer in their original relative order"
    base = {r["name"]: r for r in json.load(open(SNAPSHOT_21, encoding="ascii"))}
    problems, added = [], {}
    for name, row in base.items():
        old = row["inputSchema"].get("properties", {})
        new = live[name]["inputSchema"].get("properties", {})
        for p, spec in old.items():
            if p not in new:
                problems.append("%s.%s removed" % (name, p))
            elif spec != new[p]:
                problems.append("%s.%s changed" % (name, p))
        if set(row["inputSchema"].get("required", [])) < set(live[name]["inputSchema"].get("required", [])):
            problems.append("%s gained required params" % name)
        extra = sorted(set(new) - set(old))
        if extra:
            added[name] = extra
    assert not problems, problems
    for tool, params in EXPECTED_ADDITIONS.items():
        assert params <= set(added.get(tool, [])), "K14 expected %s to gain %s, got %s" % (tool, sorted(params), added.get(tool))
    print("PARAMS ADDED (K14):", added)


def test_tier1_rigging_tools_present(server):
    names = {t.name for t in __import__("asyncio").run(server.mcp.list_tools())}
    missing = [t for t in TIER1_TOOLS if t not in names]
    assert not missing, "Tier 1 rigging wrappers missing: %s" % missing


# ---------------------------------------------------------------- wrapper wire shapes
def test_create_armature_forwards_bones_json(server):
    bones = [{"name": "upper", "head": [0, 0, 0], "tail": [0, 0, 1]}]
    server._fake.replies["create_armature"] = {"success": True, "name": "Arm", "bone_count": 1}
    out = server.create_armature(None, name="Arm", bones=json.dumps(bones))
    assert "Error" not in str(out) and "Arm" in str(out), out
    sent = [p for c, p in server._fake.calls if c == "create_armature"][0]
    assert sent["name"] == "Arm" and sent["bones"] == bones, sent


def test_set_pose_degrees_and_json(server):
    server._fake.replies["set_pose"] = {"success": True, "bones": ["upper"], "mode_changes": {"upper": "XYZ"}}
    out = server.set_pose(None, armature="Arm", bones=json.dumps({"upper": {"rotation": [45, 0, 0]}}), rotation_mode="XYZ")
    assert "Error" not in str(out), out
    sent = [p for c, p in server._fake.calls if c == "set_pose"][0]
    assert sent["bones"]["upper"]["rotation"] == [45, 0, 0] and sent["rotation_mode"] == "XYZ", sent


def test_add_constraint_params_json_and_error_lists_types(server):
    server._fake.replies["add_constraint"] = {"success": True, "set": ["target", "chain_count"], "unset": {}}
    out = server.add_constraint(None, owner="Arm", bone="hand", constraint_type="IK", params=json.dumps({"target": "T", "chain_count": 2}))
    assert "Error" not in str(out), out
    sent = [p for c, p in server._fake.calls if c == "add_constraint"][0]
    assert sent["params"] == {"target": "T", "chain_count": 2} and sent["bone"] == "hand", sent
    server._fake.replies["add_constraint"] = {"error": "constraint_type 'WARP' not valid; valid: ['IK', 'COPY_LOCATION']"}
    out2 = server.add_constraint(None, owner="Arm", constraint_type="WARP")
    assert "Error" in str(out2) and "IK" in str(out2)


def test_set_keyframes_keys_json(server):
    keys = [[1, [0, 0, 0]], {"frame": 10, "value": [1, 0, 0], "interpolation": "LINEAR"}]
    server._fake.replies["set_keyframes"] = {"success": True, "keyed": [1, 10], "keyed_count": 2, "skipped": [], "action": "TAction",
                                             "fcurves": ["location[0]", "location[1]", "location[2]"]}
    out = server.set_keyframes(None, target="T", data_path="location", keys=json.dumps(keys))
    assert "Error" not in str(out) and "TAction" in str(out) and "2" in str(out), out
    sent = [p for c, p in server._fake.calls if c == "set_keyframes"][0]
    assert sent["keys"] == keys and sent["data_path"] == "location", sent


def test_bake_action_forwards_range(server):
    server._fake.replies["bake_action"] = {"success": True, "action": "ArmAction", "frame_range": [1, 10], "fcurve_count": 30}
    out = server.bake_action(None, armature="Arm", start=1, end=10, step=1)
    assert "ArmAction" in str(out) and "30" in str(out), out
    sent = [p for c, p in server._fake.calls if c == "bake_action"][0]
    assert sent["start"] == 1 and sent["end"] == 10, sent


# ---------------------------------------------------------------- image-returning wrappers
def test_render_weight_map_returns_image(server, tmp_path):
    # the add-on chooses the capture path and reports it as "filepath"; the wrapper reads it and deletes the file
    p = str(tmp_path / "wm.png")

    def reply(params):
        _write_png(p, 16, 16)
        return {"success": True, "filepath": p, "mesh": params["mesh"], "group": params.get("group")}
    server._fake.replies["render_weight_map"] = reply
    out = server.render_weight_map(None, mesh="Cyl", group="upper", angle="front", max_size=64)
    assert isinstance(out, server.Image), out
    img = PILImage.open(io.BytesIO(out.data))
    assert img.size == (16, 16)
    sent = [x for c, x in server._fake.calls if c == "render_weight_map"][0]
    assert sent["mesh"] == "Cyl" and sent["group"] == "upper" and sent["angle"] == "front", sent
    assert not os.path.exists(p), "temp PNG must be deleted after reading"


def test_render_weight_map_error_raises(server):
    server._fake.replies["render_weight_map"] = {"error": "group 'nope' not found"}
    with pytest.raises(Exception, match="nope"):
        server.render_weight_map(None, mesh="Cyl", group="nope")


def test_find_unweighted_render_true_returns_image(server, tmp_path):
    # F16 (Ada 13:58): ONE tool; numbers by default, an Image with render=True (doc 3.1). The add-on chooses the
    # capture path and reports it under image.filepath; the wrapper reads and deletes it.
    p = str(tmp_path / "unw.png")

    def reply(params):
        if params.get("render"):
            _write_png(p, 12, 12)
            return {"success": True, "unweighted_count": 2, "unweighted": [0, 1], "image": {"filepath": p}}
        return {"success": True, "unweighted_count": 2, "unweighted": [0, 1], "over_one": [], "negative": []}
    server._fake.replies["find_unweighted_vertices"] = reply
    txt = server.find_unweighted_vertices(None, mesh="Cyl")
    assert "2" in str(txt) and not isinstance(txt, server.Image)
    sent = [x for c, x in server._fake.calls if c == "find_unweighted_vertices"][0]
    assert not sent.get("render"), sent
    img = server.find_unweighted_vertices(None, mesh="Cyl", render=True, angle="front")
    assert isinstance(img, server.Image), img
    assert PILImage.open(io.BytesIO(img.data)).size == (12, 12)
    sent2 = [x for c, x in server._fake.calls if c == "find_unweighted_vertices"][1]
    assert sent2.get("render") is True and sent2.get("angle") == "front", sent2
    assert not os.path.exists(p), "temp PNG must be deleted after reading"
    assert not hasattr(server, "find_unweighted_vertices_image"), "the split _image tool must be gone (F16)"


def test_playblast_grid_returns_image(server, tmp_path):
    paths = []

    def reply(params):
        images = []
        for i, f in enumerate(range(params.get("start", 1), params.get("end", 4) + 1)):
            p = str(tmp_path / ("pb_%d.png" % f))
            _write_png(p, 20, 12, (30, 30, 200 + (i * 10) % 50))
            paths.append(p)
            images.append({"frame": f, "filepath": p, "width": 20, "height": 12})
        return {"success": True, "images": images, "path": "camera", "warnings": [], "video": None}
    server._fake.replies["playblast"] = reply
    out = server.playblast(None, start=1, end=4, step=1, max_size=40, columns=2)
    assert isinstance(out, server.Image), out
    sent = [x for c, x in server._fake.calls if c == "playblast"][0]
    assert sent["start"] == 1 and sent["end"] == 4 and sent["columns"] == 2, sent
    sheet = PILImage.open(io.BytesIO(out.data)).convert("RGB")
    assert sheet.width >= 2 * 20 and sheet.height >= 2 * 12, sheet.size
    px = list(sheet.getdata())
    assert len(set(px)) > 1, "playblast sheet must not be a single colour"
    assert not any(os.path.exists(p) for p in paths), "frame PNGs must be deleted after composing"


def test_playblast_bad_range_error(server):
    server._fake.replies["playblast"] = {"error": "start (10) must not exceed end (1)"}
    with pytest.raises(Exception, match="must not exceed"):
        server.playblast(None, start=10, end=1)


# ---------------------------------------------------------------- section 2 fixes at the wrapper
def test_export_object_forwards_rig_params(server, tmp_path):
    p = str(tmp_path / "r.fbx")
    # Ton's landed keys: exported_objects, fbx_options/gltf_options, ignored
    server._fake.replies["export_object"] = {"success": True, "filepath": p, "format": "fbx", "exported_objects": ["Cyl", "Arm"],
                                             "include_hierarchy": True, "fbx_options": {"add_leaf_bones": False}, "ignored": ["export_animations"]}
    out = server.export_object(None, name="Cyl", filepath=p, file_format="fbx", include_hierarchy=True, add_leaf_bones=False, export_animations=True)
    assert "Error" not in str(out), out
    sent = [x for c, x in server._fake.calls if c == "export_object"][0]
    assert sent["include_hierarchy"] is True and sent["add_leaf_bones"] is False, sent
    assert "ignored" in str(out).lower() and "export_animations" in str(out), "ignored params must be reported: %s" % out
    assert "Arm" in str(out), "exported objects must be listed: %s" % out


def test_add_keyframe_bone_forwarded(server):
    server._fake.replies["add_keyframe"] = {"success": True, "name": "Arm", "bone": "upper", "data_path": "rotation_euler", "keyed_on": "Arm", "frame": 5, "rotation_mode_changed": "XYZ"}
    out = server.add_keyframe(None, name="Arm", bone="upper", data_path="rotation_euler", frame=5, value="10,0,0")
    assert "Error" not in str(out), out
    sent = [x for c, x in server._fake.calls if c == "add_keyframe"][0]
    assert sent["bone"] == "upper" and sent["value"] == [10, 0, 0], sent
    assert "XYZ" in str(out), "rotation_mode change must be surfaced: %s" % out


def test_parent_object_bone(server):
    server._fake.replies["parent_object"] = {"success": True, "child": "C", "parent": "Arm", "parent_type": "BONE", "bone": "hand"}
    out = server.parent_object(None, child_name="C", parent_name="Arm", parent_type="BONE", bone="hand")
    assert "Error" not in str(out) and "hand" in str(out), out
    sent = [x for c, x in server._fake.calls if c == "parent_object"][0]
    assert sent["parent_type"] == "BONE" and sent["bone"] == "hand", sent
