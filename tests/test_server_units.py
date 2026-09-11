"""Server-side tests: run with the repo venv, no Blender needed.

    .venv\\Scripts\\python.exe -m pytest tests\\test_server_units.py -q

The Blender socket is replaced by FakeConn; tools are called as plain functions (ctx unused).
"""
import asyncio
import io
import json
import os
import re
import sys

import pytest
from PIL import Image as PILImage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
import blender_mcp.server as server  # noqa: E402
import blender_mcp  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
BASELINE_NAMES = os.path.join(TESTS, "baseline_tools_4.3.txt")
BASELINE_JSON = os.path.join(TESTS, "baselines", "tools_list_main.json")


class FakeConn:
    """Records every command and answers from `replies` (default: success)."""

    def __init__(self, replies=None, on_call=None):
        self.calls = []
        self.replies = replies or {}
        self.on_call = on_call
        self.sock = object()

    def send_command(self, command_type, params=None):
        self.calls.append((command_type, params or {}))
        if self.on_call:
            self.on_call(command_type, params or {})
        r = self.replies.get(command_type, {"success": True})
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def conn(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(server, "get_blender_connection", lambda: fake)
    return fake


def png_bytes(w=8, h=8, colour=(255, 0, 0)):
    buf = io.BytesIO()
    PILImage.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


def write_png(path, w=8, h=8, colour=(255, 0, 0)):
    PILImage.new("RGB", (w, h), colour).save(path)
    return path


# ---------------------------------------------------------------- M9 tools/list
def _tools():
    return asyncio.run(server.mcp.list_tools())


def test_tool_count_at_least_92():
    # Phase A: exactly 92; from B0 on (C12) tools are added, never removed
    assert len(_tools()) >= 92


def test_tool_names_superset_of_baseline_in_order():
    names = [t.name for t in _tools()]
    base = [l.strip() for l in open(BASELINE_NAMES, encoding="ascii") if l.strip()]
    missing = [b for b in base if b not in names]
    assert not missing, "tools removed vs tests/baseline_tools_4.3.txt: %s" % missing
    positions = [names.index(b) for b in base]
    assert positions == sorted(positions), "baseline tools no longer in their original relative order"


@pytest.mark.skipif(not os.path.exists(BASELINE_JSON), reason="no saved tools_list_main.json")
def test_tool_schemas_match_main_snapshot():
    sys.path.insert(0, TESTS)
    import dump_tools_list
    live = dump_tools_list.as_text(dump_tools_list.tools_payload(server))
    base = open(BASELINE_JSON, encoding="ascii").read()
    live_rows = {r["name"]: r for r in json.loads(live)}
    base_rows = {r["name"]: r for r in json.loads(base)}
    # C12 (B0 onwards): a pre-existing parameter may never be removed or retyped; adding parameters is allowed and reported
    schema_diff, added = [], {}
    for n in base_rows:
        old = base_rows[n]["inputSchema"].get("properties", {})
        new = live_rows.get(n, {}).get("inputSchema", {}).get("properties", {})
        for p, spec in old.items():
            if p not in new or new[p] != spec:
                schema_diff.append("%s.%s" % (n, p))
        extra = sorted(set(new) - set(old))
        if extra:
            added[n] = extra
        old_req = set(base_rows[n]["inputSchema"].get("required", []))
        new_req = set(live_rows.get(n, {}).get("inputSchema", {}).get("required", []))
        if new_req - old_req:
            schema_diff.append("%s requires new params %s" % (n, sorted(new_req - old_req)))
    desc_diff = [n for n in base_rows if live_rows.get(n, {}).get("description") != base_rows[n]["description"]]
    assert set(base_rows) <= set(live_rows), "baseline tools missing: %s" % sorted(set(base_rows) - set(live_rows))
    assert not schema_diff, "pre-existing parameters removed/retyped/made required: %s" % schema_diff
    if added:
        print("PARAMS ADDED (C12, allowed):", added)
    # descriptions may change by ruling (boolean/engine wording); report them, do not hide them
    if desc_diff:
        print("DESCRIPTION CHANGED (needs Lead ruling):", desc_diff)


# ---------------------------------------------------------------- M11 versions
def test_versions_all_agree():
    expected = os.environ.get("BLENDER_MCP_EXPECTED_VERSION", "2.0.0")
    py = re.search(r'^version\s*=\s*"([^"]+)"', open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read(), re.M).group(1)
    addon = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', open(os.path.join(REPO, "addon.py"), encoding="utf-8").read())
    addon_v = ".".join(addon.groups())
    assert (py, addon_v, blender_mcp.__version__) == (expected, expected, expected), (py, addon_v, blender_mcp.__version__)


def test_import_main():
    from blender_mcp.server import main  # noqa: F401


# ---------------------------------------------------------------- mocked tools
@pytest.mark.parametrize("solver", ["EXACT", "FAST", "FLOAT", "MANIFOLD", "fast"])
def test_boolean_forwards_solver_verbatim(conn, solver):
    out = server.boolean_operation(None, "T", "C", operation="DIFFERENCE", solver=solver, apply=True)
    assert not out.startswith("Error"), out
    assert conn.calls == [("boolean_operation", {"target_name": "T", "cutter_name": "C", "operation": "DIFFERENCE",
                                                  "solver": solver, "apply": True})]


def test_boolean_reports_addon_error(conn):
    conn.replies["boolean_operation"] = {"error": "solver 'X' not valid; valid: ['FLOAT', 'EXACT', 'MANIFOLD']"}
    out = server.boolean_operation(None, "T", "C", solver="X")
    assert out.startswith("Error") and "FLOAT" in out


def test_boolean_reply_mentions_resolved_solver(conn):
    conn.replies["boolean_operation"] = {"success": True, "target": "T", "cutter": "C", "operation": "DIFFERENCE",
                                         "applied": True, "solver": "FLOAT"}
    out = server.boolean_operation(None, "T", "C", solver="FAST")
    assert "FLOAT" in out, "server reply should surface the solver the addon actually used: %r" % out


def test_set_render_settings_forwards_engine(conn):
    conn.replies["set_render_settings"] = {"success": True, "engine": "BLENDER_EEVEE", "resolution": [64, 48],
                                           "output": "", "transparent": False}
    out = server.set_render_settings(None, engine="eevee", width=64, height=48)
    assert not out.startswith("Error"), out
    cmd, params = conn.calls[0]
    assert cmd == "set_render_settings" and params["engine"] == "eevee" and params["width"] == 64
    assert "BLENDER_EEVEE" in out


def test_render_depth_map_returns_image(conn, tmp_path):
    def on_call(cmd, params):
        if cmd == "render_depth_map":
            write_png(params["filepath"], 16, 16, (128, 128, 128))
    conn.on_call = on_call
    conn.replies["render_depth_map"] = {"success": True}
    out = server.render_depth_map(None, max_depth=5.0)
    assert isinstance(out, server.Image)
    assert conn.calls[0][1]["max_depth"] == 5.0
    img = PILImage.open(io.BytesIO(out.data))
    assert img.size == (16, 16)


def test_render_depth_map_error_raises(conn):
    # image-returning tools raise (FastMCP turns that into an error result) instead of returning a str
    conn.replies["render_depth_map"] = {"error": "Scene has no active camera"}
    with pytest.raises(Exception, match="no active camera"):
        server.render_depth_map(None)


def test_connection_error_surfaces(monkeypatch):
    def boom():
        raise Exception("Could not connect to Blender")
    monkeypatch.setattr(server, "get_blender_connection", boom)
    assert server.get_scene_info(None).startswith("Error")


# ---------------------------------------------------------------- PIL-only tools
def test_diff_images_detects_change(tmp_path):
    a = write_png(str(tmp_path / "a.png"), 32, 32, (0, 0, 255))
    img = PILImage.new("RGB", (32, 32), (0, 0, 255))
    for x in range(16):
        for y in range(32):
            img.putpixel((x, y), (255, 255, 0))
    b = str(tmp_path / "b.png"); img.save(b)
    out = server.diff_images(None, a, b, tile_size=64)
    assert isinstance(out, server.Image)
    sheet = PILImage.open(io.BytesIO(out.data)).convert("RGB")
    assert sheet.width > 64 * 3 - 1
    px = list(sheet.getdata())
    reds = sum(1 for p in px if p[0] > 200 and p[1] < 80 and p[2] < 80)
    assert reds > 64 * 64 * 0.3, "diff panel should paint the changed half red (%d red px)" % reds


def test_diff_images_identical_has_no_red(tmp_path):
    a = write_png(str(tmp_path / "a.png"), 32, 32, (10, 200, 10))
    b = write_png(str(tmp_path / "b.png"), 32, 32, (10, 200, 10))
    out = server.diff_images(None, a, b, tile_size=64)
    sheet = PILImage.open(io.BytesIO(out.data)).convert("RGB")
    reds = sum(1 for p in sheet.getdata() if p[0] > 200 and p[1] < 80 and p[2] < 80)
    assert reds == 0, reds


def test_diff_images_missing_file_raises():
    with pytest.raises(Exception, match="not found"):
        server.diff_images(None, "C:/nope_a.png", "C:/nope_b.png")


def test_compare_reference_image_composites(conn, tmp_path, monkeypatch):
    ref = write_png(str(tmp_path / "ref.png"), 40, 20, (0, 255, 0))
    server._reference_registry["r1"] = ref

    def on_call(cmd, params):
        if cmd == "capture_viewport_angle":
            write_png(params["filepath"], 40, 20, (255, 0, 255))
    conn.on_call = on_call
    conn.replies["capture_viewport_angle"] = {"success": True}
    out = server.compare_reference_image(None, "r1", angle="front", max_size=64)
    assert isinstance(out, server.Image), out
    sheet = PILImage.open(io.BytesIO(out.data)).convert("RGB")
    px = list(sheet.getdata())
    assert any(p[1] > 200 and p[0] < 60 for p in px) and any(p[0] > 200 and p[2] > 200 for p in px), "both tiles present"
    with pytest.raises(Exception, match="Unknown angle"):
        server.compare_reference_image(None, "r1", angle="upside")
    with pytest.raises(Exception, match="not found"):
        server.compare_reference_image(None, "never_stored")


# ---------------------------------------------------------------- helpers
def test_safe_image_return_rejects_empty():
    with pytest.raises(ValueError):
        server._safe_image_return(b"")


def test_safe_image_return_downscales_huge():
    big = io.BytesIO()
    PILImage.new("RGB", (4000, 3000), (1, 2, 3)).save(big, format="PNG")
    out = server._safe_image_return(big.getvalue())
    w, h = PILImage.open(io.BytesIO(out.data)).size
    assert w * h <= server._SAFE_IMAGE_MAX_PIXELS


def test_find_blender_exe_env(monkeypatch, tmp_path):
    exe = tmp_path / "blender.exe"; exe.write_bytes(b"x")
    monkeypatch.setenv("BLENDER_EXE", str(exe))
    assert server._find_blender_exe() == str(exe)
    monkeypatch.setenv("BLENDER_EXE", str(tmp_path / "missing.exe"))
    assert server._find_blender_exe() != str(tmp_path / "missing.exe")


def test_get_blender_status_no_process(monkeypatch):
    monkeypatch.setattr(server, "_blender_process", None)
    monkeypatch.setattr(server, "_probe_port", lambda h, p, timeout=1.0: False)
    out = server.get_blender_status(None)
    assert isinstance(out, str) and out
