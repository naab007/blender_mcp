"""Phase B0 server-side tests (settings file, precedence, presets, session state, get_version, C12 superset).

    .venv\\Scripts\\python.exe -m pytest tests\\test_b0_server.py -q

Every test points BLENDER_MCP_SETTINGS_DIR at a temp dir and reloads the server module, so the user's
~/.blender_mcp is never touched. The Blender socket is replaced by FakeConn.
Written from docs/FEATURE-REQUEST-settings-save-load.md 2.1 + docs/PLAN.md 9.1 (C2, C7, C9, C12).
"""
import importlib
import io
import json
import os
import re
import sys

import pytest
from PIL import Image as PILImage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, TESTS)

SHIPPED_PRESETS = {"game_bake", "preview", "final_eevee", "final_cycles", "sprite_sheet", "turntable_video"}


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
    for v in ("BLENDER_HOST", "BLENDER_PORT", "BLENDER_EXE"):
        monkeypatch.delenv(v, raising=False)

    def load(settings_json=None, env=None):
        sdir.mkdir(parents=True, exist_ok=True)
        if settings_json is not None:
            (sdir / "settings.json").write_text(json.dumps(settings_json), encoding="utf-8")
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        import blender_mcp.settings as settings_mod
        importlib.reload(settings_mod)          # the settings module caches its file; re-read from the new dir
        import blender_mcp.server as server
        server = importlib.reload(server)
        fake = FakeConn()
        monkeypatch.setattr(server, "get_blender_connection", lambda: fake)
        server._fake = fake
        return server, sdir

    return load


def _settings(server):
    """get_server_settings replies as text lines `key = <repr>  [source...]`; parse them back."""
    out = server.get_server_settings(None)
    if not isinstance(out, str):
        return out
    import ast
    parsed = {}
    for line in out.splitlines():
        m = re.match(r"^([a-z_0-9]+) = (.+?)\s+\[([a-z]+)", line)
        if m:
            try:
                parsed[m.group(1)] = ast.literal_eval(m.group(2))
            except Exception:
                parsed[m.group(1)] = m.group(2)
            parsed[m.group(1) + "__source"] = m.group(3)
    parsed["__text"] = out
    return parsed


# ---------------------------------------------------------------- C7 precedence + C9 defaults
def test_defaults_when_no_file(server_in):
    server, sdir = server_in()
    s = _settings(server)
    assert s["host"] == "127.0.0.1" and s["port"] == 9876, s
    for k in ("connect_timeout", "command_timeout", "output_dir", "presets_dir", "blender_exe", "image_max_pixels", "log_level", "img_to_3d_port"):
        assert k in s, "missing key %s in %s" % (k, s["__text"])
    assert s["host__source"] == "default" and s["port__source"] == "default"
    assert server._blender_host_port() == ("127.0.0.1", 9876)
    assert "Settings file:" in s["__text"] and str(sdir) in s["__text"]


def test_file_overrides_defaults(server_in):
    server, sdir = server_in({"port": 9999, "host": "10.0.0.5", "connect_timeout": 3})
    s = _settings(server)
    assert s["port"] == 9999 and s["host"] == "10.0.0.5" and s["connect_timeout"] == 3, s
    assert s["port__source"] == "file"
    assert server._blender_host_port() == ("10.0.0.5", 9999)


def test_env_overrides_file(server_in):
    server, sdir = server_in({"port": 9999, "host": "10.0.0.5"}, env={"BLENDER_PORT": "9123", "BLENDER_HOST": "localhost"})
    s = _settings(server)
    assert s["port"] == 9123 and s["host"] == "localhost", s
    assert s["port__source"] == "env"
    assert server._blender_host_port() == ("localhost", 9123)


def test_set_server_settings_writes_file_and_drops_connection(server_in):
    server, sdir = server_in()
    server._blender_connection = object()
    out = server.set_server_settings(None, values=json.dumps({"port": 9500, "log_level": "DEBUG"}))
    assert "Error" not in str(out), out
    on_disk = json.loads((sdir / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["port"] == 9500 and on_disk["log_level"] == "DEBUG", on_disk
    assert server._blender_connection is None, "host/port change must drop the persistent connection"
    assert _settings(server)["port"] == 9500
    out2 = server.set_server_settings(None, values=json.dumps({"port": None}))
    assert "Error" not in str(out2) and _settings(server)["port"] == 9876, "null must reset to default"


def test_set_server_settings_rejects_unknown_key(server_in):
    server, sdir = server_in()
    out = server.set_server_settings(None, values=json.dumps({"no_such_key": 1, "port": "not-a-number"}))
    assert "no_such_key" in str(out) and "rejected" in str(out).lower(), out
    assert "port" in str(out).split("Rejected")[-1] if "Rejected" in str(out) else True
    assert not (sdir / "settings.json").exists() or "no_such_key" not in (sdir / "settings.json").read_text(encoding="utf-8")


def test_set_server_settings_reports_env_override(server_in):
    server, sdir = server_in(env={"BLENDER_PORT": "9123"})
    out = server.set_server_settings(None, values=json.dumps({"port": 9500}))
    assert "overridden" in str(out).lower(), out
    assert _settings(server)["port"] == 9123


def test_connect_timeout_honoured(server_in, monkeypatch):
    server, sdir = server_in({"connect_timeout": 3.5})
    seen = {}

    class FakeSock:
        def settimeout(self, t):
            seen.setdefault("timeouts", []).append(t)

        def connect(self, addr):
            seen["addr"] = addr
            raise OSError("refused (fake)")

        def close(self):
            pass

    monkeypatch.setattr(server.socket, "socket", lambda *a, **k: FakeSock())
    conn = server.BlenderConnection(host="127.0.0.1", port=9876)
    assert conn.connect() is False
    assert 3.5 in seen.get("timeouts", []), "connect_timeout 3.5 never applied before connect: %s" % seen
    assert seen.get("addr") == ("127.0.0.1", 9876)


def test_status_error_raises_command_error_and_keeps_socket(server_in):
    server, sdir = server_in()
    conn = server.BlenderConnection(host="127.0.0.1", port=9876)
    sentinel = object()

    class Sock:
        def sendall(self, b): pass
        def settimeout(self, t): pass
    conn.sock = Sock()
    monkeypatch_recv = lambda sock, buffer_size=8192: json.dumps({"status": "error", "message": "Object 'X' not found"}).encode()
    conn.receive_full_response = monkeypatch_recv
    with pytest.raises(server.BlenderCommandError, match="Object 'X' not found"):
        conn.send_command("get_object_info", {"name": "X"})
    assert conn.sock is not None, "a status error must keep the socket"


def test_find_blender_exe_precedence(server_in, tmp_path, monkeypatch):
    env_exe = tmp_path / "env_blender.exe"; env_exe.write_bytes(b"x")
    file_exe = tmp_path / "file_blender.exe"; file_exe.write_bytes(b"x")
    hint_exe = tmp_path / "hint_blender.exe"; hint_exe.write_bytes(b"x")
    server, sdir = server_in({"blender_exe": str(file_exe)})
    assert server._find_blender_exe() == str(file_exe)
    monkeypatch.setenv("BLENDER_EXE", str(env_exe))
    assert server._find_blender_exe() == str(env_exe)
    assert server._find_blender_exe(str(hint_exe)) == str(hint_exe)


# ---------------------------------------------------------------- C2 get_version
def test_get_version_addon_unreachable(server_in, monkeypatch):
    server, sdir = server_in()

    def boom():
        raise Exception("Could not connect to Blender")
    monkeypatch.setattr(server, "get_blender_connection", boom)
    out = server.get_version(None)
    assert "server %s" % server.__version__ in out and "protocol 1" in out, out
    assert server.PROTOCOL == 1
    assert "unreachable" in out and "protocol_match: unknown" in out, out
    assert "Compatibility: unknown" in out, "F10: unreachable add-on must not read as Compatibility OK: %s" % out
    assert "last_update_check: null" in out and str(sdir) in out and "server.py" in out, out


def test_get_version_addon_reachable(server_in):
    server, sdir = server_in()
    v = server.__version__     # same version on both sides = no mismatch
    server._fake.replies["get_version"] = {"addon": v, "blender": "5.2.1 LTS", "python": "3.13.13", "protocol": 1}
    server._addon_info = None
    server._probe_addon_version(server._fake)
    out = server.get_version(None)
    assert "Blender add-on: %s" % v in out and "5.2.1 LTS" in out and "protocol_match: true" in out and "Compatibility: OK" in out, out


def test_get_version_mismatch_reported(server_in):
    server, sdir = server_in()
    server._fake.replies["get_version"] = {"addon": "2.0.0", "blender": "5.2.1 LTS", "python": "3.13.13", "protocol": 0}
    server._addon_info = None
    server._probe_addon_version(server._fake)
    out = server.get_version(None)
    assert "protocol_match: false" in out and "MISMATCH" in out, out


def test_legacy_addon_detected_from_unknown_command(server_in):
    server, sdir = server_in()
    server._fake.replies["get_version"] = {"status": "error", "message": "Unknown command type: get_version"}

    def raise_unknown(command_type, params=None):
        raise server.BlenderCommandError("Unknown command type: get_version")
    server._fake.send_command = raise_unknown
    server._addon_info = None
    server._probe_addon_version(server._fake)
    out = server.get_version(None)
    assert "pre-2.1" in out and "protocol_match: false" in out, out


# ---------------------------------------------------------------- presets (doc step 10, server-side files)
def test_presets_round_trip(server_in):
    server, sdir = server_in()
    values = {"RENDER": {"resolution_x": 640, "resolution_y": 480}, "OUTPUT": {"file_format": "PNG"}}
    server._fake.replies["get_settings"] = lambda p: dict(values[p["scope"]])
    server._fake.replies["set_settings"] = lambda p: {"success": True, "set": list(p["values"]), "unset": {}}
    out = server.save_preset(None, name="p1", scopes="RENDER,OUTPUT")
    assert "Error" not in str(out), out
    pfile = sdir / "presets" / "p1.json"
    assert pfile.exists(), "preset file not written under the settings dir"
    data = json.loads(pfile.read_text(encoding="utf-8"))
    scopes = data.get("scopes", data)
    assert scopes["RENDER"]["resolution_x"] == 640 and scopes["OUTPUT"]["file_format"] == "PNG", data
    listing = server.list_presets(None)
    names = set(re.findall(r"[a-z_0-9]+", str(listing)))
    assert "p1" in names and SHIPPED_PRESETS <= names, listing
    out2 = server.load_preset(None, name="p1")
    assert "Error" not in str(out2), out2
    sets = [p for c, p in server._fake.calls if c == "set_settings"]
    assert any(p["scope"] == "RENDER" and p["values"].get("resolution_x") == 640 for p in sets), sets
    assert "Error" in str(server.save_preset(None, name="p1", scopes="RENDER")), "overwrite=False must refuse"
    server.delete_preset(None, name="p1")
    assert not pfile.exists()
    assert "Error" in str(server.load_preset(None, name="p1"))


def test_shipped_presets_omit_removed_keys(server_in):
    server, sdir = server_in()
    listing = server.list_presets(None)
    for name in SHIPPED_PRESETS:
        assert name in str(listing), name
    pdir = sdir / "presets"
    texts = " ".join(p.read_text(encoding="utf-8") for p in pdir.glob("*.json")) if pdir.exists() else ""
    if not texts:
        # shipped presets may live in the package; ask the server for one
        texts = str(server.export_preset(None, name="game_bake", filepath=str(sdir / "x.json")))
    assert "use_gtao" not in texts and "use_hdr_view" not in texts, "C4: shipped presets must not carry removed keys"


# ---------------------------------------------------------------- session state (doc step 16, server-side JSON)
def test_session_state_round_trip(server_in):
    server, sdir = server_in()
    # C31 (Ada 12:15): server sends save_session_state{name, include} and persists the dict; restore is ONE
    # add-on call restore_session_state{name, state, load_file, force}; the add-on reopens the file; the
    # server never calls load_blend. Landed state layout: file{filepath,is_dirty}, frame{current,...}, selection[], active
    state = {"name": "t", "file": {"filepath": "D:/x.blend", "is_dirty": True}, "frame": {"current": 7, "start": 1, "end": 30},
             "selection": ["Cube"], "active": "Cube", "mode": "OBJECT"}
    server._fake.replies["save_session_state"] = {"success": True, "name": "t", "state": dict(state)}
    out = server.save_session_state(None, name="t")
    assert "Error" not in str(out), out
    sent = [p for c, p in server._fake.calls if c == "save_session_state"]
    assert sent and sent[0].get("name") == "t" and "include" in sent[0], sent
    sfile = sdir / "sessions" / "t.json"
    assert sfile.exists(), "session JSON not written under the settings dir"
    doc = json.loads(sfile.read_text(encoding="utf-8"))
    assert doc["state"]["frame"]["current"] == 7 and doc["state"]["file"]["filepath"] == "D:/x.blend", doc
    server._fake.replies["restore_session_state"] = {"success": True, "file_loaded": True, "restored": ["frame", "selection", "active"], "not_restored": []}
    out2 = server.restore_session_state(None, name="t", load_file=True, force=True)
    assert "Error" not in str(out2), out2
    rs = [p for c, p in server._fake.calls if c == "restore_session_state"]
    assert rs and rs[0]["state"]["frame"]["current"] == 7 and rs[0]["load_file"] is True and rs[0]["force"] is True and rs[0]["name"] == "t", rs
    assert not any(c == "load_blend" for c, _ in server._fake.calls), "the server must never reopen the file itself (C31)"
    assert "reopened" in out2.lower() or "file_loaded" in out2.lower() or "x.blend" in out2, out2
    server._fake.replies["restore_session_state"] = {"success": True, "file_loaded": False, "restored": ["frame"], "not_restored": ["active: object 'Cube' not found"]}
    out3 = server.restore_session_state(None, name="t", load_file=False)
    assert "Cube" in out3 and ("not restore" in out3.lower() or "not_restored" in out3.lower()), out3
    assert "Error" in str(server.restore_session_state(None, name="never_saved"))


# ---------------------------------------------------------------- output_dir (doc step 17)
def _under(path, root):
    return bool(path) and os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(os.path.abspath(root)))


def test_output_dir_default_for_file_leaving_tools(server_in, tmp_path):
    """Ruling C25 (doc test 17 re-pointed): output_dir is the default directory for tools that LEAVE a file when
    their path is omitted: render_all_cameras, export_object, save_blend/save_copy on a never-saved file.
    Image-returning tools (render_from_camera, capture_*, depth map, diff/compare) are exempt."""
    server, sdir = server_in()
    out_dir = tmp_path / "renders"
    server.set_server_settings(None, values=json.dumps({"output_dir": str(out_dir)}))

    def render_all(params):
        d = params.get("output_dir") or str(tmp_path / "addon_temp")   # the addon falls back to its temp dir when None
        p = os.path.join(d, "render_Camera_1.png")
        os.makedirs(d, exist_ok=True)
        PILImage.new("RGB", (4, 4), (1, 2, 3)).save(p)
        return {"success": True, "total_cameras": 1, "rendered": 1, "renders": [{"camera": "Camera", "filepath": p, "success": True}]}
    server._fake.replies["render_all_cameras"] = render_all
    server.render_all_cameras(None, width=4, height=4, samples=1)
    sent = [p for c, p in server._fake.calls if c == "render_all_cameras"][0]
    assert _under(sent.get("output_dir"), str(out_dir)), "render_all_cameras without output_dir must default to the settings output_dir: %s" % sent

    server._fake.replies["export_object"] = lambda p: {"success": True, "filepath": p.get("filepath"), "format": p.get("file_format")}
    server.export_object(None, name="Cube", file_format="glb")
    sent = [p for c, p in server._fake.calls if c == "export_object"][0]
    assert _under(sent.get("filepath"), str(out_dir)), "pathless export_object must land under output_dir: %s" % sent

    # never-saved file: the wrapper pre-checks get_file_state, then sends a filepath under output_dir and says so in a Note
    server._fake.replies["get_file_state"] = {"success": True, "filepath": "", "is_saved": False, "is_dirty": True}
    server._fake.replies["save_blend"] = lambda p: {"success": True, "filepath": p.get("filepath") or "C:/Temp/blender_unsaved_1.blend",
                                                    "bytes": 1, "is_dirty": False, "elapsed": 0.0, "compress": True}
    out = server.save_blend(None)
    sent = [p for c, p in server._fake.calls if c == "save_blend"][0]
    assert _under(sent.get("filepath"), str(out_dir)), "save_blend on a never-saved file must default to output_dir, not the temp dir: %s" % sent
    assert "note" in str(out).lower() and "output_dir" in str(out).lower(), "reply must carry the Note saying where the file went and why: %s" % out
    # a saved file keeps its path: no filepath is sent
    server._fake.calls.clear()
    server._fake.replies["get_file_state"] = {"success": True, "filepath": "D:/x.blend", "is_saved": True, "is_dirty": True}
    server.save_blend(None)
    sent2 = [p for c, p in server._fake.calls if c == "save_blend"][0]
    assert "filepath" not in sent2 or sent2["filepath"] in (None, ""), "filepath must be sent only when given: %s" % sent2


def test_output_dir_not_applied_to_image_returning_tools(server_in, tmp_path):
    server, sdir = server_in()
    out_dir = tmp_path / "renders"
    server.set_server_settings(None, values=json.dumps({"output_dir": str(out_dir)}))

    def render(params):
        PILImage.new("RGB", (4, 4), (1, 2, 3)).save(params["filepath"])
        return {"success": True, "filepath": params["filepath"], "camera": "Camera", "width": 4, "height": 4}
    server._fake.replies["render_from_camera"] = render
    out = server.render_from_camera(None, width=4, height=4, samples=1)
    assert isinstance(out, server.Image)
    sent = [p for c, p in server._fake.calls if c == "render_from_camera"][0]
    assert not os.path.exists(sent["filepath"]), "image-returning tool must delete its temp file after reading it"
    assert not (out_dir.exists() and any(out_dir.iterdir())), "exempt tool must leave nothing under output_dir"


# ---------------------------------------------------------------- C12 superset check
def test_tools_superset_of_4_3_baseline(server_in):
    server, sdir = server_in()
    import dump_tools_list
    live = {r["name"]: r for r in dump_tools_list.tools_payload(server)}
    base_names = [l.strip() for l in open(os.path.join(TESTS, "baseline_tools_4.3.txt"), encoding="ascii") if l.strip()]
    missing = [n for n in base_names if n not in live]
    assert not missing, "tools removed: %s" % missing
    main_json = os.path.join(TESTS, "baselines", "tools_list_main.json")
    base = {r["name"]: r for r in json.load(open(main_json, encoding="ascii"))}
    problems = []
    for name, row in base.items():
        old_props = row["inputSchema"].get("properties", {})
        new_props = live[name]["inputSchema"].get("properties", {})
        for p, spec in old_props.items():
            if p not in new_props:
                problems.append("%s.%s removed" % (name, p))
            elif spec.get("type") != new_props[p].get("type") or spec.get("anyOf") != new_props[p].get("anyOf"):
                problems.append("%s.%s retyped %s -> %s" % (name, p, spec.get("type") or spec.get("anyOf"), new_props[p].get("type") or new_props[p].get("anyOf")))
    assert not problems, problems
    assert len(live) > len(base), "B0 adds tools; none added"
