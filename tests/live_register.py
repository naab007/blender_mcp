"""B2-G5 live mechanics (TEST-PLAN-B2 s5.1, R-B2-25 / R-B2-31): load the DEV add-on into a GUI Blender.

    <install>\\blender.exe --factory-startup --python tests\\live_register.py            (port 9877)
    <install>\\blender.exe --factory-startup --python tests\\live_register.py -- --port N

Does exactly what the headless harness does (headless_handlers.py: importlib load of D:/App Dev/blender_mcp/addon.py,
register()), then creates the preferences entry so _prefs() is real, sets port + autostart off, and starts the socket
server through the explicit ensure_server(port) path. Never saves user preferences, never copies addon.py anywhere,
never touches %APPDATA% or B:. --factory-startup keeps the user's installed 2.2.0 add-on out of this process.
Prints one LIVE_REGISTER {json} line (ASCII).
"""
import importlib.util
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_PATH = os.path.join(os.path.dirname(HERE), "addon.py")
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PORT = int(_argv[_argv.index("--port") + 1]) if "--port" in _argv else 9877


def P(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


try:
    spec = importlib.util.spec_from_file_location("addon", ADDON_PATH)
    addon = importlib.util.module_from_spec(spec)
    sys.modules["addon"] = addon
    spec.loader.exec_module(addon)
    entry = bpy.context.preferences.addons.get("addon")
    if entry is None:
        entry = bpy.context.preferences.addons.new()
        entry.module = "addon"
    addon.register()
    prefs = addon._prefs()
    prefs.port = PORT
    prefs.autostart_server = False
    res = addon.ensure_server(port=PORT)
    P("LIVE_REGISTER " + json.dumps({
        "addon": ADDON_PATH, "bl_info_version": list(addon.bl_info["version"]), "blender": bpy.app.version_string,
        "background": bpy.app.background, "prefs_port": prefs.port, "autostart": prefs.autostart_server,
        "ensure_server": res}, sort_keys=True))
except Exception as e:  # keep the GUI alive so the failure can be read; never raise into Blender's startup
    import traceback
    P("LIVE_REGISTER " + json.dumps({"error": repr(e), "traceback": traceback.format_exc()[-1500:]}))
