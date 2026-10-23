"""F6: BlenderConnection.send_command must be serialised and resend at most once.

Ruling R-F6 (Ada, 2026-09-11 14:31): an RLock around the call; ONE resend only when the
connection reset happens BEFORE the request reached the add-on (sendall raised); never a
resend after the request was sent (recv raised). tools/list must stay byte-identical.

Runs without Blender: socket.socket is replaced by a scripted fake, so the tests exercise
BlenderConnection.send_command directly (that is where the lock must be effective).

    .venv\\Scripts\\python.exe -m pytest tests\\test_f6_connection.py -q

Discriminating: on server.py 6f249049 (no lock, no resend) the three transport tests FAIL.
"""
import importlib
import json
import os
import sys
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
# BLENDER_MCP_SRC=<dir containing blender_mcp/> runs these tests against another copy of the
# package (verifier use: prove the tests FAIL on the pre-fix server.py).
sys.path.insert(0, os.environ.get("BLENDER_MCP_SRC") or os.path.join(REPO, "src"))
sys.path.insert(0, TESTS)

BASELINE_22 = os.path.join(TESTS, "baseline_tools_2.2.txt")
SNAPSHOT_22 = os.path.join(TESTS, "baselines", "tools_list_2.2.json")
# R11 (2026-10-16): the exact-list assertion moves to the 2.3 baseline, cut from the real list at D-G3;
# the 2.2 files stay on disk untouched and become the superset / byte-identical-schema check.
BASELINE_23 = os.path.join(TESTS, "baseline_tools_2.3.txt")
SNAPSHOT_23 = os.path.join(TESTS, "baselines", "tools_list_2.3.json")
# T0 (2026-10-21, Lead GO m_365c9d69, R-B2-10/D4): until B2-G3 cuts the 2.4 baselines, the 2.3 files are the
# superset pin. Additive params allowed beyond the 2.3 snapshot = EXACTLY the PLAN.md 13.2.2 lists; anything
# else needs a ruling. The 2.2 superset test keeps its G11b allowance plus this list.
ADDITIVE_PARAMS_24 = {
    "export_object": ["objects", "apply_modifiers", "axis_forward", "axis_up", "global_scale", "use_tspace",
                      "use_triangles", "use_custom_props", "colors_type", "export_apply", "export_tangents",
                      "export_extras", "export_yup", "export_attributes", "export_vertex_color",
                      "export_animation_mode"],
    "import_file": ["axis_forward", "axis_up", "global_scale", "use_custom_normals", "use_image_search",
                    "ignore_leaf_bones", "import_shading", "merge_vertices"],
    "apply_modifier": ["apply_all", "keep"],
    "set_origin": ["location"],
    "get_scene_info": ["limit"],          # R-B2-32 (PLAN 13.4.1): limit: int = 10, 0 = all; forwarded only when given
}
# R-B2-20: a 2.3 `required` field may be DROPPED only when listed here (apply_modifier.modifier_name is optional once
# apply_all exists); a field may never be ADDED to a 2.3 tool's required list.
RELAXED_REQUIRED_24 = {"apply_modifier": ["modifier_name"]}


def _required_problems(name, old_schema, new_schema, relaxed):
    old_req, new_req = list(old_schema.get("required", [])), list(new_schema.get("required", []))
    added = [f for f in new_req if f not in old_req]
    dropped = [f for f in old_req if f not in new_req and f not in relaxed.get(name, [])]
    kept = [f for f in old_req if f in new_req]
    out = []
    if added:
        out.append("%s.required gained %s" % (name, added))
    if dropped:
        out.append("%s.required dropped %s without a RELAXED_REQUIRED_24 entry" % (name, dropped))
    if [f for f in new_req if f in kept] != kept:
        out.append("%s.required reordered %s -> %s" % (name, old_req, new_req))
    return out


def _schema_problems(snapshot_path, live, relaxed):
    """Every 2.x tool keeps its properties (same type / anyOf / default) and its required list (RELAXED_REQUIRED_24 aside)."""
    with open(snapshot_path, encoding="utf-8") as fh:
        snap_by_name = {r["name"]: r for r in json.load(fh)}
    live_by_name = {r["name"]: r for r in live}
    problems = []
    for name, row in snap_by_name.items():
        old_schema, new_schema = row["inputSchema"], live_by_name[name]["inputSchema"]
        old_props, new_props = old_schema.get("properties", {}), new_schema.get("properties", {})
        for p, spec in old_props.items():
            if p not in new_props:
                problems.append("%s.%s removed" % (name, p))
            elif (spec.get("type"), spec.get("anyOf"), spec.get("default")) != (new_props[p].get("type"), new_props[p].get("anyOf"), new_props[p].get("default")):
                problems.append("%s.%s retyped/redefaulted %s -> %s" % (name, p, json.dumps(spec, sort_keys=True), json.dumps(new_props[p], sort_keys=True)))
        problems += _required_problems(name, old_schema, new_schema, relaxed)
    return problems


def _added_params_beyond(live, snapshot_path, allowed):
    """{tool: [params in the live schema but not in the snapshot]} minus the allowed additions."""
    with open(snapshot_path, encoding="utf-8") as fh:
        snap_by_name = {r["name"]: r for r in json.load(fh)}
    live_by_name = {r["name"]: r for r in live}
    added = {}
    for n, r in snap_by_name.items():
        extra = sorted(set(live_by_name[n]["inputSchema"].get("properties", {}))
                       - set(r["inputSchema"].get("properties", {})) - set(allowed.get(n, [])))
        if extra:
            added[n] = extra
    return added

WINERR_RESET = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")


class FakeSock:
    """Scripted socket. Every sendall/recv is appended to the shared `log` as
    (event, socket_id, command_type) so a test can check ordering across threads."""

    def __init__(self, log, fail_send=False, fail_recv=False, send_delay=0.0, recv_delay=0.0):
        self.log = log
        self.fail_send = fail_send
        self.fail_recv = fail_recv
        self.send_delay = send_delay
        self.recv_delay = recv_delay
        self.pending = None
        self.closed = False

    def settimeout(self, t):
        pass

    def connect(self, addr):
        self.log.append(("connect", id(self), addr))

    def sendall(self, data):
        cmd = json.loads(data.decode("utf-8"))["type"]
        self.log.append(("send", id(self), cmd))
        if self.fail_send:
            self.fail_send = False
            raise WINERR_RESET
        time.sleep(self.send_delay)
        self.pending = cmd

    def recv(self, n):
        if self.fail_recv:
            self.fail_recv = False
            raise WINERR_RESET
        time.sleep(self.recv_delay)
        cmd = self.pending
        self.pending = None
        self.log.append(("recv", id(self), cmd))
        return json.dumps({"status": "success", "result": {"echo": cmd}}).encode("utf-8")

    def close(self):
        self.closed = True


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("BLENDER_MCP_SETTINGS_DIR", str(tmp_path / "mcp_settings"))
    for v in ("BLENDER_HOST", "BLENDER_PORT", "BLENDER_EXE"):
        monkeypatch.delenv(v, raising=False)
    import blender_mcp.settings as settings_mod
    importlib.reload(settings_mod)
    import blender_mcp.server as server_mod
    return importlib.reload(server_mod)


def _conn(srv, monkeypatch, first_sock, spare_socks):
    """A BlenderConnection already holding first_sock; any reconnect through socket.socket()
    hands out the next entry of spare_socks (a real socket is never opened)."""
    spares = list(spare_socks)

    def factory(*a, **k):
        assert spares, "reconnect attempted more often than the test allows"
        return spares.pop(0)

    monkeypatch.setattr(srv.socket, "socket", factory)
    conn = srv.BlenderConnection(host="127.0.0.1", port=0)
    conn.sock = first_sock
    return conn, spares


def _sends(log):
    return [e for e in log if e[0] == "send"]


# ------------------------------------------------ reset BEFORE the request reached the add-on
def test_reset_before_send_resends_exactly_once(srv, monkeypatch):
    log = []
    s1 = FakeSock(log, fail_send=True)
    s2 = FakeSock(log)
    conn, spares = _conn(srv, monkeypatch, s1, [s2])

    result = conn.send_command("get_scene_info", {})

    assert result == {"echo": "get_scene_info"}, result
    sends = _sends(log)
    assert len(sends) == 2, "expected one failed send + one resend, got %r" % (log,)
    assert sends[0][1] == id(s1) and sends[1][1] == id(s2), "resend must go over a fresh socket: %r" % (log,)
    assert conn.sock is s2 and not spares, "connection must keep the reconnected socket"


def test_reset_before_send_twice_gives_up_after_one_resend(srv, monkeypatch):
    log = []
    s1 = FakeSock(log, fail_send=True)
    s2 = FakeSock(log, fail_send=True)
    s3 = FakeSock(log)
    conn, spares = _conn(srv, monkeypatch, s1, [s2, s3])

    with pytest.raises(Exception):
        conn.send_command("get_scene_info", {})

    assert len(_sends(log)) == 2, "at most ONE resend: %r" % (log,)
    assert spares == [s3], "no third socket may be opened for a second resend"
    assert conn.sock is None, "a failed connection must be dropped so the next call reconnects"


# ------------------------------------------------ reset AFTER the request was sent: never resend
def test_reset_after_send_raises_and_never_resends(srv, monkeypatch):
    log = []
    s1 = FakeSock(log, fail_recv=True)
    s2 = FakeSock(log)
    conn, spares = _conn(srv, monkeypatch, s1, [s2])

    with pytest.raises(Exception) as ei:
        conn.send_command("delete_object", {"name": "Cube"})

    assert not isinstance(ei.value, AssertionError), ei.value
    assert len(_sends(log)) == 1, "a request that reached the add-on must NOT be resent: %r" % (log,)
    assert spares == [s2] and not any(e[0] == "connect" for e in log), "no reconnect-resend after send"
    assert conn.sock is None, "the dead socket must be dropped"


# ------------------------------------------------ two threads through one connection: serialised
def test_two_threads_are_serialised(srv, monkeypatch):
    log = []
    s = FakeSock(log, send_delay=0.05, recv_delay=0.02)
    conn, spares = _conn(srv, monkeypatch, s, [])
    results = {}

    def worker(name):
        try:
            results[name] = conn.send_command(name, {})
        except Exception as e:  # noqa: BLE001 - recorded, asserted below
            results[name] = e

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("cmd_a", "cmd_b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "send_command deadlocked"

    assert results == {"cmd_a": {"echo": "cmd_a"}, "cmd_b": {"echo": "cmd_b"}}, results
    events = [e for e in log if e[0] in ("send", "recv")]
    assert len(events) == 4, events
    for i in (0, 2):
        assert events[i][0] == "send", events
        assert events[i + 1] == ("recv", events[i][1], events[i][2]), \
            "second request interleaved with the first (no lock around send_command): %r" % (events,)


# ------------------------------------------------ the fix must not touch the tool surface
def test_tools_list_superset_of_2_2_baseline(srv):
    """R11 + R29: every 2.2.0 tool keeps its name, its position relative to the other 2.2.0 tools, every
    2.2.0 parameter with its type, and its `required` list; ADDITIVE parameters and description changes
    are allowed (G11b adds result_expr to execute_blender_code); new tools may only be appended."""
    import dump_tools_list
    live = dump_tools_list.tools_payload(srv)
    names = [r["name"] for r in live]
    base_names = [l.strip() for l in open(BASELINE_22, encoding="ascii") if l.strip()]
    missing = [n for n in base_names if n not in names]
    assert not missing, "tools removed vs baseline_tools_2.2.txt: %s" % missing
    assert [n for n in names if n in base_names] == base_names, "2.2.0 tools reordered vs baseline_tools_2.2.txt"
    problems = _schema_problems(SNAPSHOT_22, live, RELAXED_REQUIRED_24)
    assert not problems, problems
    allowed = dict(ADDITIVE_PARAMS_24)
    allowed["execute_blender_code"] = ["result_expr"]          # G11b
    added = _added_params_beyond(live, SNAPSHOT_22, allowed)
    assert not added, "additive params beyond G11b + PLAN.md 13.2.2 need a ruling: %s" % added


def test_tools_list_superset_of_2_3_baseline(srv):
    """T0 (R-B2-10, D4): every 2.3.0 tool keeps its name, its order relative to the other 2.3.0 tools, every
    property (same type / anyOf / default) and its `required` list; the ONLY additive params allowed are the
    PLAN.md 13.2.2 lists (ADDITIVE_PARAMS_24); new tools may only be appended. The byte-identical 2.4 check and
    its baseline files (baseline_tools_2.4.txt, baselines/tools_list_2.4.json) are added at B2-G3. Never skips."""
    import dump_tools_list
    live = dump_tools_list.tools_payload(srv)
    names = [r["name"] for r in live]
    base_names = [l.strip() for l in open(BASELINE_23, encoding="ascii") if l.strip()]
    missing = [n for n in base_names if n not in names]
    assert not missing, "tools removed vs baseline_tools_2.3.txt: %s" % missing
    assert [n for n in names if n in base_names] == base_names, "2.3.0 tools reordered vs baseline_tools_2.3.txt"
    problems = _schema_problems(SNAPSHOT_23, live, RELAXED_REQUIRED_24)
    assert not problems, problems
    added = _added_params_beyond(live, SNAPSHOT_23, ADDITIVE_PARAMS_24)
    assert not added, "additive params beyond PLAN.md 13.2.2 need a ruling: %s" % added


BASELINE_24 = os.path.join(TESTS, "baseline_tools_2.4.txt")
SNAPSHOT_24 = os.path.join(TESTS, "baselines", "tools_list_2.4.json")


def test_tools_list_identical_to_2_4_baseline(srv):
    """B2-G3 (R-B2-10/D4): the 2.4.0 tools/list is byte-identical to the baseline cut at the freeze
    (server a2cdd77f): same names in the same registration order, same descriptions, same inputSchema.
    Re-cut the two files on purpose only (dump_tools_list.py names / json); never skips."""
    import dump_tools_list
    names = dump_tools_list.names_in_order(srv)
    base_names = [l.rstrip("\n") for l in open(BASELINE_24, encoding="ascii") if l.strip()]
    assert names == base_names, "tools/list names or order differ from baseline_tools_2.4.txt: %s" % [
        (i, a, b) for i, (a, b) in enumerate(zip(names, base_names)) if a != b][:5] + [("len", len(names), len(base_names))]
    live = dump_tools_list.as_text(dump_tools_list.tools_payload(srv))
    base = open(SNAPSHOT_24, encoding="ascii").read()
    assert live == base, "tools/list payload differs from baselines/tools_list_2.4.json (run: dump_tools_list.py diff tests/baselines/tools_list_2.4.json)"
