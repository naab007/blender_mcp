"""Phase C regression gate: describe_settings / get_settings / set_settings replies, byte for byte.

Capture (pre-driver add-on, 2.2.0 = the implementation the 2.1.0 replies came from):
    <install>\\blender.exe --background --factory-startup --python tests\\settings_baseline.py -- --capture
    -> writes tests/baselines/settings_replies_<blender ver>.json (sorted keys, ASCII)
Check (after the driver re-implements the three handlers, doc section 3 / TEST-PLAN-C gate G2):
    <install>\\blender.exe --background --factory-startup --python tests\\settings_baseline.py -- --check
    -> prints SETTINGS_BASELINE {"match": true/false, ...} and one DIFF line per differing call

The same fixed call list is replayed in the same order on a factory-startup scene, so the only
thing that can change the bytes is the handler. The fixture is per Blender version (RNA differs).
Loads addon.py exactly like tests/headless_handlers.py (importlib, register(), execute_command).
"""
import importlib.util
import json
import os
import sys

import bpy

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PATH = os.environ.get("BLENDERMCP_ADDON") or os.path.join(REPO, "addon.py")
VER = "%d.%d.%d" % bpy.app.version
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines", "settings_replies_%s.json" % VER)


def P(msg):
    print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)


_spec = importlib.util.spec_from_file_location("addon", ADDON_PATH)
addon = importlib.util.module_from_spec(_spec)
sys.modules["addon"] = addon
_spec.loader.exec_module(addon)
addon.register()
srv = addon.BlenderMCPServer()


def call(cmd, **params):
    bpy.context.view_layer.update()
    return srv.execute_command({"type": cmd, "params": params})


SCOPES = ["SCENE", "RENDER", "OUTPUT", "CYCLES", "EEVEE", "COLOR", "UNITS", "VIEWPORT",
          "PREFS_FILEPATHS", "PREFS_VIEW", "PREFS_EDIT", "PREFS_SYSTEM", "PREFS_INPUT",
          "ADDON:no_such_addon", "MCP", "BOGUS", "render"]

# (label, command, params). Order matters: reads first, then writes, then reads again.
CALLS = [("describe_%s" % s, "describe_settings", {"scope": s}) for s in SCOPES]
CALLS += [("get_%s" % s, "get_settings", {"scope": s}) for s in SCOPES]
CALLS += [
    ("get_RENDER_keys", "get_settings", {"scope": "RENDER", "keys": "resolution_x,engine"}),
    ("get_RENDER_keys_list", "get_settings", {"scope": "RENDER", "keys": ["resolution_y", "fps"]}),
    ("get_RENDER_keys_missing", "get_settings", {"scope": "RENDER", "keys": "resolution_x,no_such_key"}),
    ("get_OUTPUT_ffmpeg_only", "get_settings", {"scope": "OUTPUT", "keys": "ffmpeg,file_format"}),
    ("set_RENDER_ok_and_bad_enum", "set_settings", {"scope": "RENDER", "values": {"resolution_x": 1280, "engine": "BOGUS"}}),
    ("set_RENDER_json_string", "set_settings", {"scope": "RENDER", "values": "{\"resolution_y\": 720, \"resolution_percentage\": 50}"}),
    ("set_RENDER_wrong_type", "set_settings", {"scope": "RENDER", "values": {"resolution_x": "abc", "use_border": 1}}),
    ("set_RENDER_unknown_key", "set_settings", {"scope": "RENDER", "values": {"no_such_key": 1, "fps": 30}}),
    ("set_RENDER_not_json", "set_settings", {"scope": "RENDER", "values": "{not json"}),
    ("set_RENDER_not_object", "set_settings", {"scope": "RENDER", "values": [1, 2]}),
    ("set_COLOR_bad_transform", "set_settings", {"scope": "COLOR", "values": {"view_transform": "NoSuchTransform", "exposure": 0.5}}),
    ("set_COLOR_standard", "set_settings", {"scope": "COLOR", "values": {"view_transform": "Standard", "gamma": 1.1}}),
    ("set_UNITS_imperial", "set_settings", {"scope": "UNITS", "values": {"system": "IMPERIAL", "scale_length": 2.0}}),
    ("set_SCENE_frame", "set_settings", {"scope": "SCENE", "values": {"frame_current": 7, "frame_end": 42}}),
    ("set_OUTPUT_format", "set_settings", {"scope": "OUTPUT", "values": {"file_format": "JPEG", "quality": 55}}),
    ("set_CYCLES_samples", "set_settings", {"scope": "CYCLES", "values": {"samples": 11}}),
    ("set_EEVEE_taa", "set_settings", {"scope": "EEVEE", "values": {"taa_render_samples": 9}}),
    ("set_BOGUS", "set_settings", {"scope": "BOGUS", "values": {"a": 1}}),
    ("set_VIEWPORT_headless", "set_settings", {"scope": "VIEWPORT", "values": {"show_floor": False}}),
    ("set_MCP_headless", "set_settings", {"scope": "MCP", "values": {"port": 9999}, "persist": False}),
    ("set_PREFS_EDIT_undo", "set_settings", {"scope": "PREFS_EDIT", "values": {"undo_steps": 40}}),
]
CALLS += [("get_after_%s" % s, "get_settings", {"scope": s}) for s in ("SCENE", "RENDER", "OUTPUT", "CYCLES", "EEVEE", "COLOR", "UNITS", "PREFS_EDIT")]


def _readonly_render_key():
    d = call("describe_settings", scope="RENDER")
    props = (d.get("result") or {}).get("properties") or {}
    ro = sorted(k for k, v in props.items() if v.get("readonly"))
    return ro[0] if ro else None


def capture():
    replies = {}
    for label, cmd, params in CALLS:
        replies[label] = call(cmd, **params)
    ro = _readonly_render_key()
    replies["set_RENDER_readonly_%s" % ro] = call("set_settings", scope="RENDER", values={ro: 1}) if ro else {"skipped": "no read-only RENDER prop"}
    return replies


def dumps(obj):
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True, default=str)


replies = capture()
if "--capture" in _argv:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii", newline="\n") as f:
        f.write(dumps({"blender": VER, "addon_sha256": __import__("hashlib").sha256(open(ADDON_PATH, "rb").read()).hexdigest(),
                       "calls": len(replies), "replies": replies}))
    P("SETTINGS_BASELINE " + json.dumps({"mode": "capture", "blender": VER, "calls": len(replies), "out": OUT, "bytes": os.path.getsize(OUT)}))
elif "--check" in _argv:
    if not os.path.exists(OUT):
        P("SETTINGS_BASELINE " + json.dumps({"mode": "check", "blender": VER, "match": False, "error": "no fixture at %s" % OUT}))
        sys.exit(0)
    base = json.load(open(OUT, encoding="ascii"))["replies"]
    diffs = []
    for label in sorted(set(base) | set(replies)):
        a, b = dumps(base.get(label)), dumps(replies.get(label))
        if a != b:
            diffs.append(label)
            al, bl = a.splitlines(), b.splitlines()
            first = next((i for i in range(min(len(al), len(bl))) if al[i] != bl[i]), min(len(al), len(bl)))
            P("DIFF %s :: line %d baseline=%r now=%r" % (label, first + 1, al[first] if first < len(al) else "<eof>", bl[first] if first < len(bl) else "<eof>"))
    P("SETTINGS_BASELINE " + json.dumps({"mode": "check", "blender": VER, "match": not diffs, "calls": len(replies), "differing": diffs}))
else:
    P("usage: -- --capture | --check")
