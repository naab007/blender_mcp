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
def test_tools_list_byte_identical_to_2_2_baseline(srv):
    import dump_tools_list
    live = dump_tools_list.tools_payload(srv)
    names = [r["name"] for r in live]
    base_names = [l.strip() for l in open(BASELINE_22, encoding="ascii") if l.strip()]
    assert names == base_names, "tools/list names differ from baseline_tools_2.2.txt"
    with open(SNAPSHOT_22, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    # order is asserted against the txt baseline above; the JSON snapshot is stored in another order
    live_by_name = {r["name"]: r for r in live}
    snap_by_name = {r["name"]: r for r in snapshot}
    assert set(live_by_name) == set(snap_by_name), "tools/list names differ from baselines/tools_list_2.2.json"
    changed = [n for n in live_by_name
               if json.dumps(live_by_name[n], sort_keys=True) != json.dumps(snap_by_name[n], sort_keys=True)]
    assert not changed, "tools/list schema differs from baselines/tools_list_2.2.json for: %s" % changed
