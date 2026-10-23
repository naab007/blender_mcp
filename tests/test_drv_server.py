"""Phase D1 server-side tests: the generic-driver wrappers (PLAN.md 12.2.2, rulings R12/R13/R29, TEST-PLAN-C
section 2 seam checks S1-S5, S7).

    .venv\\Scripts\\python.exe -m pytest tests\\test_drv_server.py -q

Every test points BLENDER_MCP_SETTINGS_DIR at a temp dir and reloads the server module (FakeConn + server_in
copied from tests/test_b0_server.py:25-66 per R14, no conftest). The Blender socket is replaced by FakeConn, so
each test asserts the EXACT (handler key, params) the wrapper puts on the wire, zero sends on a refused
parameter, and the rendering of the add-on's reply. BLENDER_MCP_SRC=<dir containing blender_mcp/> runs the
tests against another copy of the package (mutants on copies only: the cap-default mutant must turn the
test_cap_* tests red). L3 cases (batch/transaction) are at the end and are red until Grace's L3 lands.
"""
import importlib
import inspect
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("BLENDER_MCP_SRC") or os.path.join(REPO, "src"))
sys.path.insert(0, TESTS)


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
        importlib.reload(settings_mod)          # the settings module caches its file; re-read from the new dir
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


L1_TOOLS = ["describe", "rna_get", "rna_set", "data_new", "data_remove", "find", "context_info"]
L2_TOOLS = ["call_operator", "call_method", "scene_snapshot", "scene_diff"]
L3_TOOLS = ["batch", "transaction"]
HANDLER_KEYS = {"describe": "drv_describe", "rna_get": "drv_get", "rna_set": "drv_set", "call_operator": "drv_op",
                "call_method": "drv_call", "data_new": "drv_new", "data_remove": "drv_remove",
                "scene_snapshot": "drv_snapshot", "scene_diff": "drv_diff", "batch": "drv_batch",
                "transaction": "drv_transaction", "find": "drv_find", "context_info": "drv_context"}


# ---------------------------------------------------------------- S1: handler keys + exact wire kwargs (L1)
def test_describe_wire_defaults_and_full(server_in):
    server, _ = server_in()
    server.describe(None, what="Object")
    assert _one_call(server, "drv_describe") == {"what": "Object", "fields": "all", "include_functions": False, "search": None, "max_chars": 24000}
    server._fake.calls.clear()
    server.describe(None, what="mesh.bevel", fields="offset,segments", include_functions=True, search="bev", max_chars=500)
    assert _one_call(server, "drv_describe") == {"what": "mesh.bevel", "fields": "offset,segments", "include_functions": True, "search": "bev", "max_chars": 500}


def test_rna_get_wire_csv_and_json_list_identical(server_in):
    server, _ = server_in()
    server.rna_get(None, refs="#127, OBJECT:Cube ,MESH:Cube/vertices")
    a = _one_call(server, "drv_get")
    server._fake.calls.clear()
    server.rna_get(None, refs='["#127", "OBJECT:Cube", "MESH:Cube/vertices"]')
    b = _one_call(server, "drv_get")
    assert a == b == {"refs": ["#127", "OBJECT:Cube", "MESH:Cube/vertices"], "fields": None, "depth": 0, "angles": "RADIANS", "max_chars": 24000}
    server._fake.calls.clear()
    server.rna_get(None, refs="#1", fields="location,modifiers[]", depth=2, angles="DEGREES", max_chars=99)
    assert _one_call(server, "drv_get") == {"refs": ["#1"], "fields": "location,modifiers[]", "depth": 2, "angles": "DEGREES", "max_chars": 99}


def test_rna_get_refused_params_zero_sends(server_in):
    server, _ = server_in()
    out = server.rna_get(None, refs="[1, 2]")
    assert out == "Error: refs must be a JSON list of strings, e.g. '[\"#127\", \"OBJECT:Cube\"]'", out
    out = server.rna_get(None, refs="[bad")
    assert out.startswith("Error: refs is not valid JSON ("), out
    out = server.rna_get(None, refs="")
    assert out.startswith("Error: refs must name at least one ref"), out
    out = server.rna_get(None, refs=None)
    assert out.startswith("Error:"), out
    assert server._fake.calls == [], "a refused parameter must never reach the add-on"


def test_rna_set_wire_list_and_dict(server_in):
    server, _ = server_in()
    server.rna_set(None, sets='[["#127/location", [1, 2, 3]], ["SCENE:Scene/render.engine", "CYCLES"]]')
    assert _one_call(server, "drv_set") == {"sets": [["#127/location", [1, 2, 3]], ["SCENE:Scene/render.engine", "CYCLES"]],
                                            "angles": "RADIANS", "stop_on_error": True, "publish": True}
    server._fake.calls.clear()
    # Lead ruling on I2: a dict is accepted and normalised to the list of pairs SERVER-side
    server.rna_set(None, sets='{"#127/location": [1, 2, 3]}', angles="DEGREES", stop_on_error=False, publish=False)
    assert _one_call(server, "drv_set") == {"sets": [["#127/location", [1, 2, 3]]], "angles": "DEGREES", "stop_on_error": False, "publish": False}


def test_rna_set_refused_params_zero_sends(server_in):
    server, _ = server_in()
    assert server.rna_set(None, sets="{not json").startswith("Error: sets is not valid JSON (")
    assert server.rna_set(None, sets="").startswith("Error: sets must be a non-empty JSON list")
    assert server.rna_set(None, sets="[]").startswith("Error: sets must be a non-empty JSON list")
    # R31: a decoded JSON scalar is refused at the _parse_json_any chokepoint (defect class F4)
    assert server.rna_set(None, sets='"just a string"') == "Error: sets must be a JSON object or list"
    assert server.rna_set(None, sets="42") == "Error: sets must be a JSON object or list"
    assert server._fake.calls == []


def test_dollar_ref_strings_pass_through_unchanged(server_in):
    """S4: $id.path substitution is add-on side; the wrapper never touches a '$' string."""
    server, _ = server_in()
    server.rna_set(None, sets='[["$ob.uid/location", [1, 2, 3]], ["#1/parent", "$parent.uid"]]')
    assert _one_call(server, "drv_set")["sets"] == [["$ob.uid/location", [1, 2, 3]], ["#1/parent", "$parent.uid"]]
    server._fake.calls.clear()
    server.data_new(None, collection="objects", args='["Drv", "$mesh.uid"]')
    assert _one_call(server, "drv_new")["args"] == ["Drv", "$mesh.uid"]
    server._fake.calls.clear()
    server.rna_get(None, refs='["$ob.uid"]')
    assert _one_call(server, "drv_get")["refs"] == ["$ob.uid"]


def test_data_new_wire_and_refusals(server_in):
    server, _ = server_in()
    server.data_new(None, collection="meshes", args='["DrvMesh"]')
    assert _one_call(server, "drv_new") == {"collection": "meshes", "args": ["DrvMesh"], "kwargs": None, "link_to": None, "set": None}
    server._fake.calls.clear()
    server.data_new(None, collection="objects", args='["Drv", "#5"]', kwargs='{"object_data": null}', link_to="COLLECTION:Props",
                    set='{"location": [1, 2, 3]}')
    assert _one_call(server, "drv_new") == {"collection": "objects", "args": ["Drv", "#5"], "kwargs": {"object_data": None},
                                            "link_to": "COLLECTION:Props", "set": {"location": [1, 2, 3]}}
    server._fake.calls.clear()
    assert server.data_new(None, collection="objects", args='{"a": 1}').startswith("Error: args must be a JSON list")
    assert server.data_new(None, collection="objects", kwargs="[1]").startswith("Error: kwargs must be a JSON object")
    assert server.data_new(None, collection="objects", args="[bad").startswith("Error: args is not valid JSON (")
    assert server.data_new(None, collection="objects", set='"x"') == "Error: set must be a JSON object or list"   # R31
    assert server.data_new(None, collection="objects", args="7") == "Error: args must be a JSON object or list"   # R31
    assert server._fake.calls == []


def test_data_remove_wire_and_refusals(server_in):
    server, _ = server_in()
    server.data_remove(None, refs="#1,MESH:Cube")
    assert _one_call(server, "drv_remove") == {"refs": ["#1", "MESH:Cube"], "do_unlink": True, "purge_orphans": False}
    server._fake.calls.clear()
    server.data_remove(None, refs='["#1"]', do_unlink=False, purge_orphans=True)
    assert _one_call(server, "drv_remove") == {"refs": ["#1"], "do_unlink": False, "purge_orphans": True}
    server._fake.calls.clear()
    assert server.data_remove(None, refs="").startswith("Error: refs must name at least one ID ref")
    assert server.data_remove(None, refs="[1]").startswith("Error: refs must be a JSON list of strings")
    assert server._fake.calls == []


def test_find_wire_and_refusals(server_in):
    server, _ = server_in()
    server.find(None)
    assert _one_call(server, "drv_find") == {"id_type": "OBJECT", "name": None, "where": None, "scope": "SCENE", "fields": "name,type", "limit": 100, "max_chars": 24000}
    server._fake.calls.clear()
    server.find(None, id_type="MESH", name="Drv*", where='[["type", "==", "MESH"], ["modifiers[].type", "contains", "ARMATURE"]]',
                scope="DATA", fields="name", limit=5, max_chars=300)
    assert _one_call(server, "drv_find") == {"id_type": "MESH", "name": "Drv*", "where": [["type", "==", "MESH"], ["modifiers[].type", "contains", "ARMATURE"]],
                                             "scope": "DATA", "fields": "name", "limit": 5, "max_chars": 300}
    server._fake.calls.clear()
    assert server.find(None, where='{"type": "MESH"}').startswith("Error: where must be a JSON list")
    assert server.find(None, where="[nope").startswith("Error: where is not valid JSON (")
    assert server._fake.calls == []


def test_context_info_wire(server_in):
    server, _ = server_in()
    server.context_info(None)
    assert _one_call(server, "drv_context") == {"fields": None}
    server._fake.calls.clear()
    server.context_info(None, fields="mode,active_object")
    assert _one_call(server, "drv_context") == {"fields": "mode,active_object"}


# ---------------------------------------------------------------- defect class F4 closed at the chokepoint (R31)
def test_json_scalar_refused_at_a_2_2_site_too(server_in):
    """F4 was a class: every _parse_json_any caller echoed a decoded JSON string as the reply. R31 fixed the
    helper, so a pre-existing 2.2.0 wrapper (add_bones, B1) must now refuse a scalar with the same text and
    zero sends. Red against the committed 2.2.0 server (BLENDER_MCP_SRC), green from server.py 718a5066."""
    server, _ = server_in()
    out = server.add_bones(None, armature="Arm", bones='"x"')
    assert out == "Error: bones must be a JSON object or list", out
    out = server.add_bones(None, armature="Arm", bones="3")
    assert out == "Error: bones must be a JSON object or list", out
    assert server._fake.calls == [], "a refused parameter must never reach the add-on"


# ---------------------------------------------------------------- S3: the cap comes from settings and is ALWAYS sent
def test_cap_default_from_settings_on_every_reading_tool(server_in):
    server, _ = server_in()
    server.describe(None, what="Object")
    server.rna_get(None, refs="#1")
    server.find(None)
    sent = {c[0]: c[1].get("max_chars") for c in server._fake.calls}
    assert sent == {"drv_describe": 24000, "drv_get": 24000, "drv_find": 24000}, sent


def test_cap_override_file_env_and_bad_values(server_in):
    server, _ = server_in({"driver_reply_max_chars": 777})
    server.describe(None, what="Object")
    assert _one_call(server, "drv_describe")["max_chars"] == 777
    server, _ = server_in({"driver_reply_max_chars": 777}, env={"BLENDER_DRIVER_REPLY_MAX": "500"})
    server.rna_get(None, refs="#1")
    assert _one_call(server, "drv_get")["max_chars"] == 500, "env must beat the file"
    for bad in (0, -1, "x", None):
        server._fake.calls.clear()
        server.find(None, max_chars=bad)
        assert _one_call(server, "drv_find")["max_chars"] == 500, "non-positive / non-int max_chars falls back to the setting"
    server._fake.calls.clear()
    server.find(None, max_chars=12)
    assert _one_call(server, "drv_find")["max_chars"] == 12


def test_cap_keys_in_server_settings_tool(server_in):
    server, sdir = server_in()
    text = server.get_server_settings(None)
    assert "driver_reply_max_chars = 24000" in text and "driver_batch_max = 200" in text, text
    out = server.set_server_settings(None, values=json.dumps({"driver_reply_max_chars": "x"}))
    assert "rejected" in str(out).lower() and "driver_reply_max_chars" in str(out), out
    out = server.set_server_settings(None, values=json.dumps({"driver_batch_max": 50, "driver_reply_max_chars": 1000}))
    assert "Error" not in str(out), out
    on_disk = json.loads((sdir / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["driver_batch_max"] == 50 and on_disk["driver_reply_max_chars"] == 1000, on_disk
    server.describe(None, what="Object")
    assert _one_call(server, "drv_describe")["max_chars"] == 1000


# ---------------------------------------------------------------- S5: reply rendering
def test_error_reply_renders_teaching_keys(server_in):
    server, _ = server_in()
    server._fake.replies["drv_get"] = {"error": "property 'locatoin' not found on Object", "closest": ["location", "rotation_mode"], "tried": ["objects"]}
    out = server.rna_get(None, refs="#1/locatoin")
    lines = out.splitlines()
    assert lines[0] == "Error: property 'locatoin' not found on Object", out
    assert 'closest: ["location", "rotation_mode"]' in lines and 'tried: ["objects"]' in lines, out


def test_success_reply_pretty_printed_and_bounded(server_in):
    server, _ = server_in()
    reply = {"success": True, "results": [{"ref": "#1", "uid": 1, "value": {"location": [1.0, 2.0, 3.0]}}], "errors": [], "chars": 80, "truncated": []}
    server._fake.replies["drv_get"] = reply
    out = server.rna_get(None, refs="#1")
    assert json.loads(out) == reply and "\n" in out, "pretty-printed JSON expected"


def test_send_failure_becomes_error_text(server_in):
    server, _ = server_in()

    def boom(params):
        raise RuntimeError("socket closed by the add-on")

    server._fake.replies["drv_describe"] = boom
    out = server.describe(None, what="Object")
    assert out.startswith("Error: ") and "socket closed" in out, out


# ---------------------------------------------------------------- G11 (b): execute_blender_code(result_expr)
def test_execute_blender_code_wire_unchanged_without_result_expr(server_in):
    server, _ = server_in()
    server._fake.replies["execute_code"] = {"executed": True, "result": "hi\n"}
    out = server.execute_blender_code(None, code="print('hi')")
    assert _one_call(server, "execute_code") == {"code": "print('hi')"}, "old calls must stay byte-identical on the wire"
    assert "hi" in out


def test_execute_blender_code_result_expr_and_error_dict(server_in):
    server, _ = server_in()
    server._fake.replies["execute_code"] = {"executed": True, "result": "", "value": {"uid": 7, "id_type": "OBJECT", "name": "Cube"}}
    out = server.execute_blender_code(None, code="ob = bpy.data.objects['Cube']", result_expr="ob")
    assert _one_call(server, "execute_code") == {"code": "ob = bpy.data.objects['Cube']", "result_expr": "ob"}
    assert '"uid": 7' in out and "Cube" in out, out
    server._fake.calls.clear()
    server._fake.replies["execute_code"] = {"error": "ValueError: boom", "traceback": ["  File \"<string>\", line 2, in <module>", "ValueError: boom"], "line": 2}
    out = server.execute_blender_code(None, code="x = 1\nraise ValueError('boom')")
    assert out.startswith("Error: ValueError: boom") and "line 2" in out and "<module>" in out, out
    sig = inspect.signature(server.execute_blender_code)
    assert "result_expr" in sig.parameters and sig.parameters["result_expr"].default is None


# ---------------------------------------------------------------- S7: tools/list surface of the driver
def test_driver_tools_registered_with_str_json_params(server_in):
    server, _ = server_in()
    import dump_tools_list
    live = {r["name"]: r for r in dump_tools_list.tools_payload(server)}
    for name in L1_TOOLS:
        assert name in live, "%s missing from tools/list" % name
    props = live["rna_get"]["inputSchema"]["properties"]
    assert props["refs"]["type"] == "string" and props["angles"].get("default") == "RADIANS" and props["depth"].get("default") == 0, props
    assert live["rna_set"]["inputSchema"]["properties"]["sets"]["type"] == "string"
    assert live["execute_blender_code"]["inputSchema"]["properties"]["result_expr"]["default"] is None
    assert live["rna_get"]["inputSchema"]["required"] == ["refs"] and live["rna_set"]["inputSchema"]["required"] == ["sets"]
    # docstrings are short and point at describe (12.3 D1-S)
    for name in ("rna_get", "rna_set", "data_new", "find"):
        assert "describe" in live[name]["description"], "%s docstring must point at describe" % name


# ---------------------------------------------------------------- L3 cases (red until Grace's L3 lands)
def test_batch_cap_200_ok_201_refused_zero_sends(server_in):
    server, _ = server_in()
    cmds = [{"id": "c%d" % i, "type": "get_version"} for i in range(200)]
    server.batch(None, commands=json.dumps(cmds))
    sent = _one_call(server, "drv_batch")
    assert sent["commands"] == cmds and sent["stop_on_error"] is True and sent["undo"] == "AUTO" and sent["refs"] is True and sent["max_chars"] == 24000, sent
    server._fake.calls.clear()
    out = server.batch(None, commands=json.dumps(cmds + [{"type": "get_version"}]))
    assert out.startswith("Error: batch has 201 commands; the limit is 200"), out
    assert server._fake.calls == [], "a refused batch must never reach the add-on"
    server, _ = server_in({"driver_batch_max": 3})
    out = server.batch(None, commands=json.dumps(cmds[:4]))
    assert out.startswith("Error: batch has 4 commands; the limit is 3") and server._fake.calls == [], out
    server.batch(None, commands=json.dumps(cmds[:3]))
    assert _one_call(server, "drv_batch")["commands"] == cmds[:3]


def test_batch_refused_shapes_zero_sends(server_in):
    server, _ = server_in()
    assert server.batch(None, commands="[nope").startswith("Error: commands is not valid JSON (")
    assert server.batch(None, commands='{"type": "x"}').startswith("Error: commands must be a non-empty JSON list")
    assert server.batch(None, commands="[]").startswith("Error: commands must be a non-empty JSON list")
    assert server.batch(None, commands='[{"params": {}}]').startswith("Error:"), "a command without type is refused server-side"
    assert server._fake.calls == []


def test_batch_dollar_refs_pass_through_and_results_rendered(server_in):
    server, _ = server_in()
    cmds = [{"id": "m", "type": "data_new", "params": {"collection": "meshes", "args": ["M"]}},
            {"id": "o", "type": "data_new", "params": {"collection": "objects", "args": ["O", "$m.uid"]}}]
    server._fake.replies["drv_batch"] = {"success": True, "results": [{"id": "m", "type": "data_new", "status": "ok", "result": {"uid": 1}, "elapsed_ms": 1},
                                                                   {"id": "o", "type": "data_new", "status": "ok", "result": {"uid": 2}, "elapsed_ms": 1}],
                                         "rolled_back": False, "undo_available": True, "stopped_at": None, "elapsed_ms": 2, "chars": 100, "truncated": []}
    out = server.batch(None, commands=json.dumps(cmds), undo="NONE", stop_on_error=False)
    sent = _one_call(server, "drv_batch")
    assert sent["commands"][1]["params"]["args"] == ["O", "$m.uid"] and sent["undo"] == "NONE" and sent["stop_on_error"] is False
    # 12.2.2: the batch reply is pretty-printed: a one-line head (counts, rolled_back, stopped_at) then the JSON
    head, body = out.split("\n", 1)
    assert head.startswith("batch: 2 commands, 2 ok, 0 error, 0 skipped") and "rolled_back=False" in head and "stopped_at=None" in head, head
    assert json.loads(body)["results"][1]["result"]["uid"] == 2


def test_transaction_wire(server_in):
    server, _ = server_in()
    server.transaction(None, action="BEGIN", name="t1")
    assert _one_call(server, "drv_transaction") == {"action": "BEGIN", "name": "t1"}
    server._fake.calls.clear()
    server.transaction(None, action="STATUS")
    assert _one_call(server, "drv_transaction") == {"action": "STATUS", "name": None}
    server._fake.calls.clear()
    out = server.transaction(None, action="NOPE")
    assert out.startswith("Error:") and "BEGIN" in out and server._fake.calls == [], out
