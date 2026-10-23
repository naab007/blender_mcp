# Code created by Siddharth Ahuja: www.github.com/ahujasid (c) 2025

import re
import math
import random
import bpy
import mathutils
import json
import threading
import socket
import time
import requests
import tempfile
import traceback
import os
import shutil
import zipfile
import platform
from bpy.props import IntProperty
from bpy.app.handlers import persistent
import io
from datetime import datetime
import hashlib, hmac, base64
import os.path as osp
from contextlib import redirect_stdout, suppress, contextmanager
import array
import bmesh

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (2, 4, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

# Wire protocol number, mirrored by the server's PROTOCOL constant. Bumped when a handler key
# is renamed or removed, or an existing payload / reply key changes meaning or type; purely
# additive keys with addon-side defaults do not bump it.
PROTOCOL = 1

# Generic driver (D1) constants, docs/PLAN.md 12.2.5. The server owns the settings keys
# (driver_reply_max_chars, driver_batch_max) and sends max_chars on every read; these are the
# add-on defaults for an older server.
_DRV_REPLY_MAX_DEFAULT = 24000
_DRV_BATCH_MAX = 200
_DRV_BATCH_WALL_S = 60.0
# Operators the driver refuses to keep the session alive (request doc 2.5); preferences.addon_disable
# only when `module` names this add-on.
_DRV_REFUSED_OPS = frozenset({"wm.quit_blender", "wm.console_toggle", "wm.window_new",
                              "wm.window_fullscreen_toggle", "screen.animation_play",
                              "wm.read_factory_settings", "preferences.addon_disable", "script.reload"})
# R33: structs whose POINTER / COLLECTION members the encoder never dereferences (reported as {struct, path}
# without a getattr): ToolSettings, every struct whose bl_rna.base chain reaches Paint (Sculpt, VertexPaint,
# ImagePaint, UvSculpt, GpPaint, ...) and CurveMapping. ponytail: ceiling = the 4.3.2 factory file, where
# scene.tool_settings.uv_sculpt is an uninitialised Paint: its getters fault the process (uncatchable), crash-file
# frames rna_Paint_eraser_brush_get -> RNA_id_pointer_create -> rna_ID_refine and CurveMapping_tone_get <-
# pyrna_prop_to_py (uv_sculpt.cavity_curve.tone); extend the set if another crash site appears (measured
# 2026-10-16: with uv_sculpt skipped a depth-3 Scene walk of 1482 reads completes on 4.3.2).
_DRV_UNSAFE_STRUCTS = frozenset({"ToolSettings", "Paint", "CurveMapping"})


class _DrvRefError(Exception):
    """A ref that did not resolve; .reply is the teaching error dict ({error, tried, closest, valid, rna_error})."""

    def __init__(self, reply):
        super().__init__(reply.get("error", "ref error"))
        self.reply = reply


def _drv_handler(fn):
    """Driver handlers never raise on bad input (12.2.2): an unexpected exception becomes {error, rna_error}."""
    import functools

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except _DrvRefError as e:
            return dict(e.reply)
        except TypeError as e:
            # a wrong keyword reaches here before the body runs: keep the dispatcher's wording family;
            # the traceback is printed too (RA-16) because a TypeError from inside the body lands here as well
            traceback.print_exc()
            return {"error": f"{fn.__name__}: {e}", "rna_error": str(e)}
        except Exception as e:
            traceback.print_exc()
            return {"error": f"{type(e).__name__}: {e}", "rna_error": str(e)}
    return wrapper

RODIN_FREE_TRIAL_KEY = "k9TcfFoEhNd9cCPP2guHAHHHkctZHIRhZDywZ1euGUXwihbYLpOjQhofby80NJez"

# unregister() records "the server was running" so register() can auto-restart it
# after an addon reload. The flag lives in bpy.app.driver_namespace, not a module
# global: a reload re-executes this module, which would reset a global.
_RESTART_FLAG = "blendermcp_restart_server_on_register"


def _restart_flag_get() -> bool:
    return bool(bpy.app.driver_namespace.get(_RESTART_FLAG, False))


def _restart_flag_set(value: bool) -> None:
    if value:
        bpy.app.driver_namespace[_RESTART_FLAG] = True
    else:
        bpy.app.driver_namespace.pop(_RESTART_FLAG, None)


# ─── Preferences and server-lifecycle helpers ────────────────────────────────
# The port, the autostart switch and every API key live in BLENDERMCP_AddonPreferences (never
# in the .blend). A sys.path import (headless harness) is NOT in preferences.addons, so
# _prefs() returns None there and every consumer falls back to defaults.

_DEFAULT_PORT = 9876
_SERVER_HOST = "127.0.0.1"      # AF_INET only; 'localhost' may resolve to ::1 first on the client
_SECRET_NAMES = ("hyper3d_api_key", "sketchfab_api_key", "hunyuan3d_secret_id", "hunyuan3d_secret_key")
_ENSURE_SERVER_HOOK = "blendermcp_ensure_server"     # driver_namespace key for --python-expr launches


def _prefs():
    """This add-on's AddonPreferences, or None when the module is not in preferences.addons."""
    try:
        entry = bpy.context.preferences.addons.get(__name__)
    except Exception:
        return None
    return entry.preferences if entry is not None else None


def _port():
    """Configured server port; the default when preferences are unavailable."""
    prefs = _prefs()
    try:
        return int(prefs.port) if prefs is not None else _DEFAULT_PORT
    except Exception:
        return _DEFAULT_PORT


def _legacy_groups(scene):
    """
    The ID-property groups a pre-2.4 Scene secret can live in, in read order (R-B2-18 / H15).
    The four legacy Scene StringProperties are no longer registered (2.4.0), so their saved
    values are orphaned ID properties: in the plain group (files written on 4.x, incl. every
    1.6.0 file: scene.get) or, for files saved on 5.x while the props were registered, in the
    5.x system group (scene.bl_system_properties_get; absent on 4.x). Yields (label, group).
    """
    yield "user", scene
    getter = getattr(scene, "bl_system_properties_get", None)
    if getter is not None:
        try:
            grp = getter(do_create=False)
        except Exception:
            grp = None
        if grp is not None:
            yield "system", grp


def _legacy_secret_get(scene, name):
    """Legacy Scene secret by short name from whichever group holds it: (value, group) or ("", None)."""
    key = "blendermcp_" + name
    for label, grp in _legacy_groups(scene):
        try:
            value = grp.get(key)
        except Exception:
            value = None
        if isinstance(value, str) and value:
            return value, grp
    return "", None


def _secret(name):
    """
    API key / secret by short name (see _SECRET_NAMES): preferences first, then an orphaned
    legacy Scene ID property as a read-only fallback (a file whose load_post migration has not
    run yet, e.g. the harness).
    """
    prefs = _prefs()
    if prefs is not None:
        value = getattr(prefs, name, "") or ""
        if value:
            return value
    scene = getattr(bpy.context, "scene", None)
    return _legacy_secret_get(scene, name)[0] if scene is not None else ""


def _migrate_legacy_secrets(prefs, scene=None):
    """
    One-time move of non-empty legacy Scene secrets into prefs (only where the prefs value is
    empty) and deletion of the legacy copy from whichever ID-property group held it, so a
    secret is never saved into the file again (R-B2-18: moved, not copied). prefs is a
    parameter so a headless test can pass a stand-in object. Returns the migrated names.
    """
    if prefs is None:
        return []
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return []
    migrated = []
    for name in _SECRET_NAMES:
        legacy, _grp = _legacy_secret_get(scene, name)
        if not legacy:
            continue
        try:
            if not (getattr(prefs, name, "") or ""):
                setattr(prefs, name, legacy)
                migrated.append(name)
            # 5.x opens a 4.x file with the legacy value in BOTH groups (measured 2026-10-21): delete every copy
            key = "blendermcp_" + name
            for _label, grp in _legacy_groups(scene):
                if key in grp.keys():
                    del grp[key]
        except Exception as e:
            print(f"BlenderMCP: could not migrate legacy {name}: {e}")
    return migrated


def _server_running():
    """Runtime truth about the socket server (replaces the old Scene.blendermcp_server_running)."""
    srv = getattr(bpy.types, "blendermcp_server", None)
    return srv is not None and bool(getattr(srv, "running", False))


def ensure_server(port=None):
    """
    Start the socket server if it is not running; never raises.
    port=None reads the preference (9876 without preferences); port=0 asks the OS for a free
    port (tests). Returns {"running", "port", "host", "started_now"} plus "error" on a failed
    bind. Shared by the deferred autostart timer, the panel button, the ensure_server_running
    command and the bpy.app.driver_namespace["blendermcp_ensure_server"] launch hook.
    """
    srv = getattr(bpy.types, "blendermcp_server", None)
    if srv is not None and srv.running:
        return {"running": True, "port": srv.port, "host": srv.host, "started_now": False}
    wanted = _port() if port is None else int(port)
    if srv is None or srv.port != wanted or srv.host != _SERVER_HOST:
        srv = BlenderMCPServer(host=_SERVER_HOST, port=wanted)
        bpy.types.blendermcp_server = srv
    srv.start()
    result = {"running": bool(srv.running), "port": srv.port, "host": srv.host,
              "started_now": bool(srv.running)}
    if not srv.running:
        result["error"] = srv.last_error or f"could not bind {srv.host}:{wanted}"
    return result


@persistent
def _on_load_post(_filepath=None):
    """A newly opened file may carry pre-2.1 secrets: migrate them once. The server is
    process-global and is left alone."""
    try:
        _migrate_legacy_secrets(_prefs())
    except Exception as e:
        print(f"BlenderMCP load_post migration failed: {e}")


def _on_exit_pre(*_args):
    """Blender 5.1+: close the socket before the process exits (4.x relies on unregister())."""
    srv = getattr(bpy.types, "blendermcp_server", None)
    if srv is not None:
        with suppress(Exception):
            srv.stop()


# Add User-Agent as required by Poly Haven API
REQ_HEADERS = requests.utils.default_headers()
REQ_HEADERS.update({"User-Agent": "blender-mcp"})

class BlenderMCPServer:
    def __init__(self, host=_SERVER_HOST, port=_DEFAULT_PORT):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        self.last_error = None

    def start(self):
        if self.running:
            print("Server is already running")
            return

        self.running = True
        self.last_error = None

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.port = self.socket.getsockname()[1]      # port=0 -> the OS-assigned port
            self.socket.listen(5)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            self.last_error = str(e)
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("BlenderMCP server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    # A JSON object can only be complete when the buffer ends with '}'
                    if not buffer.rstrip().endswith(b'}'):
                        continue
                    try:
                        text = buffer.decode('utf-8')
                    except UnicodeDecodeError:
                        continue  # multi-byte character split across chunks
                    decoder = json.JSONDecoder()
                    pos = 0
                    while True:
                        while pos < len(text) and text[pos].isspace():
                            pos += 1
                        if pos >= len(text):
                            buffer = b''
                            break
                        try:
                            command, end = decoder.raw_decode(text, pos)
                        except json.JSONDecodeError:
                            # Incomplete trailing command, keep what is left
                            buffer = text[pos:].encode('utf-8')
                            break
                        pos = end

                        # Execute command in Blender's main thread
                        def execute_wrapper(command=command):
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None

                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    # Integration handlers are only reachable while the matching scene toggle is on.
    _GATED_COMMANDS = {
        "get_polyhaven_categories": "blendermcp_use_polyhaven",
        "search_polyhaven_assets": "blendermcp_use_polyhaven",
        "download_polyhaven_asset": "blendermcp_use_polyhaven",
        "set_texture": "blendermcp_use_polyhaven",
        "create_rodin_job": "blendermcp_use_hyper3d",
        "poll_rodin_job_status": "blendermcp_use_hyper3d",
        "import_generated_asset": "blendermcp_use_hyper3d",
        "search_sketchfab_models": "blendermcp_use_sketchfab",
        "get_sketchfab_model_preview": "blendermcp_use_sketchfab",
        "download_sketchfab_model": "blendermcp_use_sketchfab",
        "create_hunyuan_job": "blendermcp_use_hunyuan3d",
        "poll_hunyuan_job_status": "blendermcp_use_hunyuan3d",
        "import_generated_asset_hunyuan": "blendermcp_use_hunyuan3d",
    }
    _GATE_LABELS = {
        "blendermcp_use_polyhaven": "PolyHaven",
        "blendermcp_use_hyper3d": "Hyper3D",
        "blendermcp_use_sketchfab": "Sketchfab",
        "blendermcp_use_hunyuan3d": "Hunyuan3D",
    }

    def _build_handlers(self):
        handlers = {
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "get_polyhaven_status": self.get_polyhaven_status,
            "get_hyper3d_status": self.get_hyper3d_status,
            "get_sketchfab_status": self.get_sketchfab_status,
            "get_hunyuan3d_status": self.get_hunyuan3d_status,
            # PolyHaven
            "get_polyhaven_categories": self.get_polyhaven_categories,
            "search_polyhaven_assets": self.search_polyhaven_assets,
            "download_polyhaven_asset": self.download_polyhaven_asset,
            "set_texture": self.set_texture,
            # Hyper3D
            "create_rodin_job": self.create_rodin_job,
            "poll_rodin_job_status": self.poll_rodin_job_status,
            "import_generated_asset": self.import_generated_asset,
            # Sketchfab
            "search_sketchfab_models": self.search_sketchfab_models,
            "get_sketchfab_model_preview": self.get_sketchfab_model_preview,
            "download_sketchfab_model": self.download_sketchfab_model,
            # Hunyuan3D
            "create_hunyuan_job": self.create_hunyuan_job,
            "poll_hunyuan_job_status": self.poll_hunyuan_job_status,
            "import_generated_asset_hunyuan": self.import_generated_asset_hunyuan,
        }
        extended_handlers = {
            # Multi-angle capture
            "capture_viewport_angle": self.capture_viewport_angle,
            "capture_contact_sheet": self.capture_contact_sheet,
            # Depth map
            "render_depth_map": self.render_depth_map,
            # Reference image
            "store_reference_image": self.store_reference_image,
            "get_reference_image": self.get_reference_image,
            # Mesh editing
            "move_object": self.move_object,
            "scale_object": self.scale_object,
            "rotate_object": self.rotate_object,
            "set_object_material_color": self.set_object_material_color,
            "get_vertex_positions": self.get_vertex_positions,
            "set_vertex_position": self.set_vertex_position,
            "set_vertex_positions": self.set_vertex_positions,
            "get_control_points": self.get_control_points,
            "set_control_point": self.set_control_point,
            # Lifecycle
            "quit_blender": self.quit_blender,
            "get_version": self.get_version,
            "ensure_server_running": self.ensure_server_running,
            # Edge operations
            "get_edges": self.get_edges,
            "mark_sharp_edges": self.mark_sharp_edges,
            "set_edge_crease": self.set_edge_crease,
            "set_edge_bevel_weight": self.set_edge_bevel_weight,
            # Face operations
            "get_faces": self.get_faces,
            "set_face_material_index": self.set_face_material_index,
            "extrude_faces": self.extrude_faces,
            "inset_faces": self.inset_faces,
            "flip_normals": self.flip_normals,
            "merge_vertices": self.merge_vertices,
            "triangulate_mesh": self.triangulate_mesh,
            "subdivide_mesh": self.subdivide_mesh,
            "apply_modifier": self.apply_modifier,
            "get_mesh_stats": self.get_mesh_stats,
            # Camera management
            "create_camera": self.create_camera,
            "set_active_camera": self.set_active_camera,
            "render_from_camera": self.render_from_camera,
            "render_all_cameras": self.render_all_cameras,
            # Scene analysis
            "find_objects_by_type": self.find_objects_by_type,
            "measure_distance": self.measure_distance,
            # Lighting
            "add_light": self.add_light,
            "set_world_background": self.set_world_background,
            "add_3point_lighting": self.add_3point_lighting,
            # Export / import / blend save-load
            "export_object": self.export_object,
            "import_file": self.import_file,
            "save_blend": self.save_blend,
            "load_blend": self.load_blend,
            # Primitives & object management
            "add_primitive": self.add_primitive,
            "delete_object": self.delete_object,
            "duplicate_object": self.duplicate_object,
            "join_objects": self.join_objects,
            "separate_mesh": self.separate_mesh,
            "rename_object": self.rename_object,
            "set_origin": self.set_origin,
            "snap_to_ground": self.snap_to_ground,
            "set_smooth_shading": self.set_smooth_shading,
            "parent_object": self.parent_object,
            "select_objects": self.select_objects,
            "align_objects": self.align_objects,
            # Materials
            "create_material": self.create_material,
            "assign_material": self.assign_material,
            "load_texture": self.load_texture,
            # Modifiers
            "add_modifier": self.add_modifier_ext,
            "boolean_operation": self.boolean_operation,
            # Render settings
            "set_render_settings": self.set_render_settings,
            # Animation
            "add_keyframe": self.add_keyframe,
            "set_frame": self.set_frame,
            # Collections
            "create_collection": self.create_collection,
            "move_to_collection": self.move_to_collection,
            # Settings, save and load (B0 Tier 1): file lifecycle
            "get_file_state": self.get_file_state,
            "new_file": self.new_file,
            "revert_file": self.revert_file,
            "recover_file": self.recover_file,
            "save_copy": self.save_copy,
            "save_version": self.save_version,
            "list_versions": self.list_versions,
            "append_from_blend": self.append_from_blend,
            "link_from_blend": self.link_from_blend,
            "set_autosave": self.set_autosave,
            "make_paths_relative": self.make_paths_relative,
            "make_paths_absolute": self.make_paths_absolute,
            "find_missing_files": self.find_missing_files,
            "pack_all": self.pack_all,
            "unpack_all": self.unpack_all,
            # Settings, generic
            "describe_settings": self.describe_settings,
            "get_settings": self.get_settings,
            "set_settings": self.set_settings,
            "settings_snapshot": self.settings_snapshot,
            "settings_restore": self.settings_restore,
            "list_settings_snapshots": self.list_settings_snapshots,
            "delete_settings_snapshot": self.delete_settings_snapshot,
            # Settings, typed conveniences
            "set_output_settings": self.set_output_settings,
            "set_color_management": self.set_color_management,
            "set_render_quality": self.set_render_quality,
            "set_render_device": self.set_render_device,
            "list_render_devices": self.list_render_devices,
            "set_simplify": self.set_simplify,
            "set_frame_range": self.set_frame_range,
            "set_scene_units": self.set_scene_units,
            "set_viewport_defaults": self.set_viewport_defaults,
            # Presets and profiles
            "list_blender_presets": self.list_blender_presets,
            "apply_blender_preset": self.apply_blender_preset,
            "set_project_profile": self.set_project_profile,
            "get_project_profile": self.get_project_profile,
            # Add-ons, workspaces, preferences
            "list_addons": self.list_addons,
            "enable_addon": self.enable_addon,
            "disable_addon": self.disable_addon,
            "get_addon_preferences": self.get_addon_preferences,
            "set_addon_preferences": self.set_addon_preferences,
            "save_preferences": self.save_preferences,
            "list_workspaces": self.list_workspaces,
            "set_workspace": self.set_workspace,
            "get_addon_settings": self.get_addon_settings,
            "set_addon_settings": self.set_addon_settings,
            # Session state
            "save_session_state": self.save_session_state,
            "restore_session_state": self.restore_session_state,
            "get_session_state": self.get_session_state,        # server wrapper of save_session_state
            "apply_session_state": self.apply_session_state,    # server wrapper of restore_session_state
            # Rigging (B1 Tier 1): armatures and bones
            "create_armature": self.create_armature,
            "add_bones": self.add_bones,
            "get_armature_info": self.get_armature_info,
            "set_bone_properties": self.set_bone_properties,
            "delete_bones": self.delete_bones,
            # Rigging: skinning and weights
            "bind_armature": self.bind_armature,
            "get_vertex_groups": self.get_vertex_groups,
            "get_vertex_weights": self.get_vertex_weights,
            "set_vertex_weights": self.set_vertex_weights,
            "render_weight_map": self.render_weight_map,
            "find_unweighted_vertices": self.find_unweighted_vertices,
            # Rigging: pose and constraints
            "set_pose": self.set_pose,
            "get_pose": self.get_pose,
            "reset_pose": self.reset_pose,
            "add_constraint": self.add_constraint,
            "get_constraints": self.get_constraints,
            "remove_constraint": self.remove_constraint,
            # Animation (B1 Tier 1)
            "set_keyframes": self.set_keyframes,
            "get_animation_info": self.get_animation_info,
            "set_scene_frame_range": self.set_scene_frame_range,
            "playblast": self.playblast,
            "bake_action": self.bake_action,
            # Engine readiness (B2 L1): validation
            "validate_asset": self.validate_asset,
            "find_mesh_issues": self.find_mesh_issues,
            "compare_meshes": self.compare_meshes,
            "apply_transforms": self.apply_transforms,
            "fix_normals": self.fix_normals,
            "cleanup_mesh": self.cleanup_mesh,
            # Engine readiness (B2 L2): optimisation, LODs, collision
            "decimate_mesh": self.decimate_mesh,
            "generate_lods": self.generate_lods,
            "set_normals": self.set_normals,
            "triangulate_for_export": self.triangulate_for_export,
            "get_triangle_budget": self.get_triangle_budget,
            "create_collision": self.create_collision,
            "get_collision_info": self.get_collision_info,
            "check_collision_fit": self.check_collision_fit,
            # Engine readiness (B2 L3): sockets, pivots, hierarchy, export
            "add_socket": self.add_socket,
            "list_sockets": self.list_sockets,
            "set_pivot": self.set_pivot,
            "snap_to_grid": self.snap_to_grid,
            "build_export_hierarchy": self.build_export_hierarchy,
            "export_for_engine": self.export_for_engine,
            "get_export_preset": self.get_export_preset,
            # Generic driver (D1)
            "drv_describe": self.drv_describe,
            "drv_get": self.drv_get,
            "drv_set": self.drv_set,
            "drv_new": self.drv_new,
            "drv_remove": self.drv_remove,
            "drv_find": self.drv_find,
            "drv_context": self.drv_context,
            "drv_op": self.drv_op,
            "drv_call": self.drv_call,
            "drv_snapshot": self.drv_snapshot,
            "drv_diff": self.drv_diff,
            "drv_batch": self.drv_batch,
            "drv_transaction": self.drv_transaction,
        }
        handlers.update(extended_handlers)
        return handlers

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {}) or {}

        handlers = getattr(self, "_handlers", None)
        if handlers is None:
            handlers = self._handlers = self._build_handlers()

        gate = self._GATED_COMMANDS.get(cmd_type)
        if gate and not getattr(bpy.context.scene, gate, False):
            return {"status": "error",
                    "message": f"{self._GATE_LABELS[gate]} integration is disabled. "
                               f"Enable it in the BlenderMCP panel (N sidebar) first."}

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}



    def get_scene_info(self, limit=10):
        """Get information about the current Blender scene. limit = objects listed (default 10, 0 = all; R-B2-32)."""
        try:
            try:
                limit = int(limit) if limit is not None else 10
            except (TypeError, ValueError):
                return {"error": f"limit must be an integer >= 0 (0 = all), got {limit!r}"}
            print("Getting scene info...")
            scene = bpy.context.scene
            r, vs, units = scene.render, scene.view_settings, scene.unit_settings
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": scene.name,
                "object_count": len(scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
                # File state: is_dirty is reported, never assumed (True at 4.3.2 startup, False at 5.2.1);
                # version is the Blender that SAVED the file, not the running one
                "file": {
                    "filepath": bpy.data.filepath,
                    "is_saved": bool(bpy.data.is_saved),
                    "is_dirty": bool(bpy.data.is_dirty),
                    "version": list(bpy.data.version),
                },
                "settings_summary": {
                    "engine": r.engine,
                    "resolution": [r.resolution_x, r.resolution_y],
                    "resolution_percentage": r.resolution_percentage,
                    "fps": r.fps,
                    "fps_base": r.fps_base,
                    "frame_start": scene.frame_start,
                    "frame_end": scene.frame_end,
                    "frame_current": scene.frame_current,
                    "units": {"system": units.system, "scale_length": units.scale_length,
                              "length_unit": units.length_unit},
                    "view_transform": vs.view_transform,
                    "look": vs.look,
                },
            }

            # B2 (13.2.2): scene budgets with modifiers evaluated (E13), MESH objects only; one evaluated_get
            # per mesh gives both counts (RA-10)
            dg = bpy.context.evaluated_depsgraph_get()
            tri_by_name, total_verts = {}, 0
            for o in scene.objects:
                if o.type != 'MESH':
                    continue
                try:
                    me = o.evaluated_get(dg).data
                    me.calc_loop_triangles()
                    tri_by_name[o.name] = len(me.loop_triangles)
                    total_verts += len(me.vertices)
                except Exception:
                    tri_by_name[o.name] = 0
            scene_info["total_triangles"] = sum(tri_by_name.values())
            scene_info["total_vertices"] = total_verts

            # Collect minimal object information (first `limit` objects, default 10; 0 = all; documented in TOOLS.md)
            for i, obj in enumerate(scene.objects):
                if limit > 0 and i >= limit:
                    break

                obj_info = {
                    "name": obj.name,
                    "uid": obj.session_uid,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
                    "triangles": tri_by_name.get(obj.name, 0),
                    "hidden": not obj.visible_get(),     # R-B2-17 (7)
                    "parent": obj.parent.name if obj.parent else None,     # R-B2-28: batch_export resolves roots here
                    "collections": [c.name for c in obj.users_collection],  # R-B2-30: batch_export(collection=) filters here
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    @staticmethod
    def _get_aabb(obj):
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]



    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}

        # Basic object info
        obj_info = {
            "name": obj.name,
            "uid": obj.session_uid,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
            "parent": obj.parent.name if obj.parent else None,
            "parent_type": obj.parent_type,
        }
        if obj.parent_type == 'BONE':
            obj_info["parent_bone"] = obj.parent_bone

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }
            # Rig data on a mesh
            obj_info["vertex_groups"] = [vg.name for vg in obj.vertex_groups]
            obj_info["shape_keys"] = [kb.name for kb in mesh.shape_keys.key_blocks] if mesh.shape_keys else []
            arm_mod = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
            obj_info["armature"] = arm_mod.object.name if arm_mod is not None and arm_mod.object else None

        if obj.type == 'ARMATURE' and obj.data:
            ad = obj.animation_data
            obj_info["bones"] = len(obj.data.bones)
            obj_info["pose_position"] = obj.data.pose_position
            obj_info["display_type"] = obj.data.display_type
            obj_info["action"] = ad.action.name if ad is not None and ad.action else None
            obj_info["bone_collections"] = self._bone_collections(obj.data)

        return obj_info

    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """
        Capture a screenshot of the current 3D viewport and save it to the specified path.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Path where to save the screenshot file
        - format: Image format (png, jpg, etc.)

        Returns success/error status
        """
        try:
            if not filepath:
                return {"error": "No filepath provided"}

            # Find the active 3D viewport
            area = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    break

            if not area:
                return {"error": "No 3D viewport found"}

            # Take screenshot with proper context override
            with bpy.context.temp_override(area=area):
                bpy.ops.screen.screenshot_area(filepath=filepath)

            # Load and resize if needed
            img = bpy.data.images.load(filepath)
            width, height = img.size

            if max(width, height) > max_size:
                scale = max_size / max(width, height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                img.scale(new_width, new_height)

                # Set format and save
                img.file_format = format.upper()
                img.save()
                width, height = new_width, new_height

            # Cleanup Blender image data
            bpy.data.images.remove(img)

            return {
                "success": True,
                "width": width,
                "height": height,
                "filepath": filepath
            }

        except Exception as e:
            return {"error": str(e)}

    def execute_code(self, code, result_expr=None):
        """
        Execute arbitrary Blender Python code. result_expr: an expression evaluated in the same
        namespace after the code, returned under "value" (encoded like rna_get, 12.2.4). An
        exception returns {"error": "<type>: <msg>", "traceback": [last 3 frames], "line": n}
        instead of raising (D1 G11b); the stdout capture stays under "result".
        """
        # This is powerful but potentially dangerous - use with caution
        # Create a local namespace for execution
        namespace = {"bpy": bpy}
        capture_buffer = io.StringIO()
        try:
            # Capture stdout during execution, and return it as result
            value = None
            with redirect_stdout(capture_buffer):
                exec(code, namespace)
                if result_expr:
                    value = self._encode_value(eval(str(result_expr), namespace), None, 1)
            reply = {"executed": True, "result": capture_buffer.getvalue()}     # RA-15: built once, after the capture
            if result_expr:
                reply["value"] = value
            return reply
        except Exception as e:
            frames = traceback.extract_tb(e.__traceback__)
            line = next((f.lineno for f in reversed(frames) if f.filename == "<string>"), None)
            return {"error": f"{type(e).__name__}: {e}",
                    "traceback": [f"{f.filename}:{f.lineno} in {f.name}: {f.line or ''}".rstrip(": ") for f in frames[-3:]],
                    "line": line, "result": capture_buffer.getvalue(), "executed": False}



    # ─── Shared helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _ensure_object_mode(obj):
        """Leave edit/sculpt mode on obj so its mesh data can be read and written."""
        if obj.mode != 'OBJECT':
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='OBJECT')

    def _select_only(self, obj):
        """Make obj the sole selected + active object, in object mode."""
        self._ensure_object_mode(obj)
        for o in bpy.context.view_layer.objects:
            if o.select_get():
                o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    @contextmanager
    def _selection_scope(self):
        """
        Snapshot the active object and the selection; restore both in finally, whatever
        happens inside. Objects removed meanwhile are skipped; when the previous active
        object is gone the current active (usually the handler's target) is kept. The mode
        is left as the body leaves it (handlers end in OBJECT mode).
        """
        view_layer = bpy.context.view_layer
        active = view_layer.objects.active
        active_name = active.name if active is not None else None
        selected_names = [o.name for o in view_layer.objects if o.select_get()]
        try:
            yield
        finally:
            for o in view_layer.objects:
                with suppress(Exception):
                    o.select_set(o.name in selected_names)
            restore = bpy.data.objects.get(active_name) if active_name else None
            if restore is not None and restore.name in view_layer.objects:
                view_layer.objects.active = restore

    @contextmanager
    def _mesh_select_scope(self, mesh):
        """
        Snapshot the vertex / edge / polygon select flags of a Mesh datablock and put them
        back in finally (foreach_get / foreach_set, OBJECT-mode data). Use it OUTSIDE
        _mode_restore so the flags are written after the mode is back (A1.3, 2026-09-11).
        """
        saved = []
        for coll in (mesh.vertices, mesh.edges, mesh.polygons):
            buf = [False] * len(coll)
            with suppress(Exception):
                coll.foreach_get("select", buf)
            saved.append((coll, buf))
        try:
            yield
        finally:
            for coll, buf in saved:
                if len(coll) == len(buf):
                    with suppress(Exception):
                        coll.foreach_set("select", buf)

    # ─── Rigging helpers (B1) ────────────────────────────────────────────────

    @contextmanager
    def _mode_restore(self):
        """
        Remember the active object and its interaction mode; restore both in finally.
        Leaves whatever mode the body entered, then re-activates the previous object and
        re-enters its previous mode (a removed object is skipped, OBJECT mode remains).
        """
        view_layer = bpy.context.view_layer
        active = view_layer.objects.active
        active_name = active.name if active is not None else None
        mode = active.mode if active is not None else 'OBJECT'
        try:
            yield
        finally:
            with suppress(Exception):
                if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            restore = bpy.data.objects.get(active_name) if active_name else None
            if restore is not None and restore.name in view_layer.objects:
                view_layer.objects.active = restore
                if mode != 'OBJECT':
                    with suppress(Exception):
                        bpy.ops.object.mode_set(mode=mode)

    @contextmanager
    def _armature_edit(self, arm_obj):
        """
        Enter EDIT mode on arm_obj and yield arm_obj.data.edit_bones; prior mode and active
        object are restored afterwards. EditBone references are UNDEFINED once the block
        exits (measured on both versions): callers return bone NAMES only.
        """
        with self._mode_restore():
            self._ensure_object_mode(arm_obj)
            bpy.context.view_layer.objects.active = arm_obj
            bpy.ops.object.mode_set(mode='EDIT')
            try:
                yield arm_obj.data.edit_bones
            finally:
                with suppress(Exception):
                    bpy.ops.object.mode_set(mode='OBJECT')

    @contextmanager
    def _pose_mode(self, arm_obj):
        """Enter POSE mode on arm_obj and yield arm_obj.pose.bones; prior mode and active restored."""
        with self._mode_restore():
            self._ensure_object_mode(arm_obj)
            bpy.context.view_layer.objects.active = arm_obj
            bpy.ops.object.mode_set(mode='POSE')
            try:
                yield arm_obj.pose.bones
            finally:
                with suppress(Exception):
                    bpy.ops.object.mode_set(mode='OBJECT')

    @staticmethod
    def _get_typed(name, obj_type):
        obj = bpy.data.objects.get(name)
        if obj is None:
            return None, {"error": f"Object not found: {name}"}
        if obj.type != obj_type:
            others = [o.name for o in bpy.data.objects if o.type == obj_type][:20]
            article = "an" if obj_type[0] in "AEIOU" else "a"
            return None, {"error": f"{name} is a {obj.type}, not {article} {obj_type}; {obj_type} objects: {others}"}
        return obj, None

    def _get_armature(self, name):
        """(obj, None) for an ARMATURE object, or (None, error_dict) that says what it found instead."""
        return self._get_typed(name, 'ARMATURE')

    def _get_mesh(self, name):
        """(obj, None) for a MESH object, or (None, error_dict)."""
        return self._get_typed(name, 'MESH')

    @staticmethod
    def _addon_enabled(module_name):
        return module_name in bpy.context.preferences.addons.keys()

    @staticmethod
    def _bone_collections(arm_data):
        """Bone collections on 4.0 (collections) and 4.1+ (collections_all), by name."""
        coll = getattr(arm_data, "collections_all", None)
        if coll is None:
            coll = getattr(arm_data, "collections", None)
        return [c.name for c in coll] if coll is not None else []

    def _action_channels(self, id_obj, ensure=False):
        """
        F-curve access that hides the 5.0 slotted-action API. Returns
        (fcurves, groups, new_fcurve) for the action on id_obj.animation_data, or
        (None, None, None) when there is no action (or no channels yet and ensure=False).
        4.x: action.fcurves / action.groups; new_fcurve passes action_group=.
        5.x: the slot's channelbag via bpy_extras.anim_utils (get, or ensure when writing);
        a missing slot is bound from action_suitable_slots or created (ensure=True);
        new_fcurve passes group_name= (falls back to action_group= on TypeError).
        """
        ad = getattr(id_obj, "animation_data", None)
        action = ad.action if ad is not None else None
        if action is None:
            return None, None, None
        if hasattr(action, "fcurves"):
            fcurves, groups = action.fcurves, action.groups

            def new_fcurve(data_path, index=0, group=None):
                return fcurves.new(data_path, index=index, action_group=group or "")
            return fcurves, groups, new_fcurve

        from bpy_extras import anim_utils
        slot = getattr(ad, "action_slot", None)
        if slot is None:
            suitable = list(getattr(ad, "action_suitable_slots", []) or [])
            if suitable:
                ad.action_slot = suitable[0]
            elif ensure:
                id_type = getattr(id_obj, "id_type", 'OBJECT')
                ad.action_slot = action.slots.new(id_type=id_type, name=id_obj.name)
            slot = getattr(ad, "action_slot", None)
            if slot is None:
                return None, None, None
        if ensure:
            bag = anim_utils.action_ensure_channelbag_for_slot(action, slot)
        else:
            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
        if bag is None:
            return None, None, None

        def new_fcurve(data_path, index=0, group=None):
            try:
                return bag.fcurves.new(data_path, index=index, group_name=group or "")
            except TypeError:
                return bag.fcurves.new(data_path, index=index, action_group=group or "")
        return bag.fcurves, bag.groups, new_fcurve

    @staticmethod
    def _select_bones(arm_obj, names=None, select=True):
        """
        Select (or deselect) pose bones by name (None = all) through the version-correct
        attribute: PoseBone.select on 5.x, Bone.select (+head/tail) on 4.x.
        Returns (selected_names, missing_names).
        """
        wanted = None if names is None else set(names)
        done, missing = [], []
        for pb in arm_obj.pose.bones:
            if wanted is not None and pb.name not in wanted:
                continue
            if hasattr(pb, "select"):
                pb.select = bool(select)
            else:
                pb.bone.select = bool(select)
                with suppress(Exception):
                    pb.bone.select_head = bool(select)
                    pb.bone.select_tail = bool(select)
            done.append(pb.name)
        if wanted is not None:
            missing = sorted(wanted - set(done))
        return done, missing

    @staticmethod
    def _check_indices(indices, count, label):
        """Return an error dict when any index is outside [0, count), else None."""
        try:
            bad = [i for i in indices if not (0 <= int(i) < count)]
        except (TypeError, ValueError):
            return {"error": f"{label} indices must be integers, got {indices!r}"}
        if bad:
            plural = {"vertex": "vertices"}.get(label.lower(), label.lower() + "s")
            return {"error": f"{label} indices out of range: {bad} (mesh has {count} {plural})"}
        return None

    @contextmanager
    def _bmesh_edit(self, obj, write=True):
        """
        Yield a BMesh built from obj.data with lookup tables ready.
        Writes it back on success (write=True) and always frees it.
        """
        self._ensure_object_mode(obj)
        mesh = obj.data
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            yield bm
            if write:
                bm.to_mesh(mesh)
                mesh.update()
        finally:
            bm.free()

    @staticmethod
    def _op_exists(op):
        """bpy.ops attributes resolve lazily, so hasattr() is always True; ask RNA instead."""
        try:
            op.get_rna_type()
            return True
        except Exception:
            return False

    @staticmethod
    def _new_node(tree, *ids):
        """
        Add a node to tree trying each type id in turn: the Compositor id first, then its
        Shader twin (5.0 replaced several CompositorNode* ids with ShaderNode* ones).
        Raises RuntimeError naming every id tried when none exists on this Blender.
        """
        tried = []
        for node_id in ids:
            try:
                return tree.nodes.new(node_id)
            except Exception as e:
                tried.append(f"{node_id} ({e})")
        raise RuntimeError(f"No usable node type among {list(ids)}; tried: {tried}")

    @staticmethod
    def _engine_ids(render=None):
        """
        Identifiers of the live render-engine enum. The static RNA enum lists only the built-in
        EEVEE id on both 4.x and 5.x, so probe by assignment: the TypeError text carries the
        full live tuple including add-on engines such as CYCLES. Falls back to the static list.
        """
        render = render or bpy.context.scene.render
        try:
            render.engine = "__MCP_PROBE__"        # always rejected; nothing is changed
        except Exception as e:
            live = re.search(r"not found in \((.*?)\)", str(e))
            if live:
                return [v.strip().strip("'\"") for v in live.group(1).split(",")]
        return [e.identifier for e in render.bl_rna.properties['engine'].enum_items]

    # EEVEE was BLENDER_EEVEE (<=4.1), BLENDER_EEVEE_NEXT (4.2-4.5), BLENDER_EEVEE again (5.0+).
    _ENGINE_ALIASES = {
        'BLENDER_EEVEE': ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'),
        'BLENDER_EEVEE_NEXT': ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'),
    }

    @classmethod
    def _set_render_engine(cls, render, engine):
        """
        Try-assign an engine id, case-insensitively, accepting bare EEVEE / CYCLES / WORKBENCH
        and the BLENDER_EEVEE <-> BLENDER_EEVEE_NEXT alias. Never inspects the Blender version.
        Returns an error dict listing the live enum on failure, else None.
        """
        name = str(engine or "").strip().upper()
        if not name:
            return {"error": f"engine must be a non-empty string; valid: {cls._engine_ids(render)}"}
        candidates = []
        for base in (name, "BLENDER_" + name):
            for cand in cls._ENGINE_ALIASES.get(base, (base,)):
                if cand not in candidates:
                    candidates.append(cand)
        for cand in candidates:
            try:
                render.engine = cand
                return None
            except Exception:
                continue
        return {"error": f"engine '{engine}' not valid (tried {candidates}); valid: {cls._engine_ids(render)}. "
                         "Add-on engines that are disabled do not appear in this list."}

    @staticmethod
    def _set_file_format(image_settings, file_format):
        """
        Assign image_settings.file_format. Blender 5.0 added ImageFormatSettings.media_type,
        which must match the format before file_format is assigned (IMAGE for stills, VIDEO
        for FFMPEG, MULTI_LAYER_IMAGE for OPEN_EXR_MULTILAYER on 5.2.1; the spelling is
        try-assigned so a renamed item cannot break stills); 4.x has no such attribute.
        Returns an error dict listing the live enum on failure, else None.
        """
        fmt = str(file_format or "").strip().upper().lstrip(".")
        # Common file-extension spellings map onto Blender's enum identifiers
        fmt = {'EXR': 'OPEN_EXR', 'EXR_MULTILAYER': 'OPEN_EXR_MULTILAYER',
               'JPG': 'JPEG', 'TIF': 'TIFF', 'TGA': 'TARGA'}.get(fmt, fmt)
        if hasattr(image_settings, "media_type"):
            media_candidates = {'FFMPEG': ('VIDEO',),
                                'OPEN_EXR_MULTILAYER': ('MULTI_LAYER_IMAGE', 'MULTI_LAYER')}.get(fmt, ('IMAGE',))
            for media in media_candidates:
                try:
                    image_settings.media_type = media
                    break
                except Exception:
                    continue
        try:
            image_settings.file_format = fmt
        except Exception:
            # Unlike the engine enum, the static file_format enum is complete; on 5.x Blender's
            # TypeError tuple is filtered by the current media_type (under IMAGE it omits FFMPEG
            # and OPEN_EXR_MULTILAYER, which this helper accepts), so the static list is the truth.
            valid = [i.identifier for i in image_settings.bl_rna.properties['file_format'].enum_items]
            return {"error": f"file_format '{file_format}' not valid; valid: {valid}"}
        return None

    @staticmethod
    def _reference_images():
        """Reference registry, kept in driver_namespace so an addon reload does not drop it."""
        return bpy.app.driver_namespace.setdefault("blendermcp_reference_images", {})

    # ─── Multi-angle viewport capture ────────────────────────────────────────

    # Predefined view presets: view3d.view_axis type, or None for the isometric views
    _VIEW_PRESETS = {
        "front":  "FRONT",  "back":   "BACK",
        "left":   "LEFT",   "right":  "RIGHT",
        "top":    "TOP",    "bottom": "BOTTOM",
        "iso_front_right": None,
        "iso_front_left":  None,
    }

    _CAPTURE_OVERLAYS = ("bones_in_front", "wireframe", "weight_paint")

    @contextmanager
    def _capture_overlay(self, space, overlay):
        """
        Apply a capture overlay on a VIEW_3D space and undo every change in finally.
        bones_in_front: every armature's show_in_front + the bones overlay on.
        wireframe: shading type WIREFRAME.
        weight_paint: WEIGHT_PAINT mode on the active MESH (its active vertex group is shown).
        """
        if overlay is None:
            yield None
            return
        name = str(overlay).lower()
        if name not in self._CAPTURE_OVERLAYS:
            raise ValueError(f"overlay '{overlay}' not valid; valid: {list(self._CAPTURE_OVERLAYS)}")
        saved_in_front = {}
        saved_show_bones = space.overlay.show_bones
        saved_shading = space.shading.type
        try:
            if name == "bones_in_front":
                for o in bpy.context.scene.objects:
                    if o.type == 'ARMATURE':
                        saved_in_front[o.name] = o.show_in_front
                        o.show_in_front = True
                space.overlay.show_bones = True
                yield name
            elif name == "wireframe":
                space.shading.type = 'WIREFRAME'
                yield name
            else:
                active = bpy.context.view_layer.objects.active
                if active is None or active.type != 'MESH':
                    raise ValueError("overlay weight_paint needs an active MESH object (select_objects first)")
                with self._mode_restore():
                    bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
                    yield name
        finally:
            for oname, value in saved_in_front.items():
                o = bpy.data.objects.get(oname)
                if o is not None:
                    o.show_in_front = value
            with suppress(Exception):
                space.overlay.show_bones = saved_show_bones
            with suppress(Exception):
                space.shading.type = saved_shading

    def capture_viewport_angle(self, angle="front", max_size=800, filepath=None, overlay=None):
        """
        Capture the 3D viewport from a named angle.
        angle: one of front, back, left, right, top, bottom, iso_front_right, iso_front_left
        overlay: None, bones_in_front, wireframe or weight_paint (all changes restored afterwards)
        Frames the selected objects, or the whole scene when nothing is selected.
        """
        import math
        if angle not in self._VIEW_PRESETS:
            return {"error": f"Unknown angle: {angle}. Choose from: {list(self._VIEW_PRESETS.keys())}"}
        if overlay is not None and str(overlay).lower() not in self._CAPTURE_OVERLAYS:
            return {"error": f"overlay '{overlay}' not valid; valid: {list(self._CAPTURE_OVERLAYS)}"}

        area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
        if not area:
            return {"error": "No 3D viewport found"}
        space = next((s for s in area.spaces if s.type == 'VIEW_3D'), None)
        if not space:
            return {"error": "No VIEW_3D space found"}
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if not region:
            return {"error": "No WINDOW region found in VIEW_3D"}

        r3d = space.region_3d
        prefs_view = bpy.context.preferences.view

        # Save original state
        orig_view_matrix = r3d.view_matrix.copy()
        orig_perspective = r3d.view_perspective
        orig_view = (r3d.view_location.copy(), r3d.view_rotation.copy(), r3d.view_distance)   # F-B2-6
        orig_smooth_view = prefs_view.smooth_view

        try:
            with self._capture_overlay(space, overlay):
                return self._capture_angle_inner(angle, max_size, filepath, area, space, region, r3d, prefs_view, overlay)
        except ValueError as e:
            return {"error": str(e)}
        finally:
            prefs_view.smooth_view = orig_smooth_view
            r3d.view_matrix = orig_view_matrix
            r3d.view_perspective = orig_perspective
            # F-B2-6: on 4.3.2 the next redraw rebuilds the matrix from these three, so put them back too
            r3d.view_location, r3d.view_rotation, r3d.view_distance = orig_view

    def _capture_angle_inner(self, angle, max_size, filepath, area, space, region, r3d, prefs_view, overlay):
        import math
        try:
            # Smooth-view animates view changes over time; the screenshot is taken
            # right away, so it must be applied instantly.
            prefs_view.smooth_view = 0
            with bpy.context.temp_override(area=area, region=region):
                preset = self._VIEW_PRESETS[angle]
                if preset is None:
                    r3d.view_perspective = 'PERSP'
                    yaw = 45.0 if angle == "iso_front_right" else -45.0
                    rot = mathutils.Euler((math.radians(54.736), 0.0, math.radians(yaw)), 'XYZ')
                    r3d.view_rotation = rot.to_quaternion()
                else:
                    bpy.ops.view3d.view_axis(type=preset, align_active=False)

                if bpy.context.selected_objects:
                    bpy.ops.view3d.view_selected(use_all_regions=False)
                else:
                    bpy.ops.view3d.view_all(use_all_regions=False, center=False)

            if not filepath:
                filepath = os.path.join(tempfile.gettempdir(), f"blender_angle_{angle}_{os.getpid()}.png")
            if os.path.exists(filepath):
                os.remove(filepath)

            # Make sure the new view is drawn before it is read back. The redraw runs OUTSIDE
            # the area/region override: on Blender 5.2 a DRAW_WIN_SWAP redraw inside the
            # override makes the following screenshot_area write a 1x1 image (measured live,
            # 2026-09-11); outside it the capture is full size and shows the new view.
            with suppress(Exception):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            with bpy.context.temp_override(area=area, region=region):
                if not bpy.ops.screen.screenshot_area.poll():
                    return {"error": "Viewport capture needs a GUI session: screen.screenshot_area is "
                                     "unavailable in --background (use render_from_camera headless)"}
                bpy.ops.screen.screenshot_area(filepath=filepath)
            if not os.path.exists(filepath):
                return {"error": "Screenshot was not written"}

            # Resize if needed
            img = bpy.data.images.load(filepath)
            try:
                w, h = img.size
                if w <= 1 or h <= 1:
                    # Viewport was not drawn when read back: one retry without any redraw
                    bpy.data.images.remove(img)
                    img = None
                    os.remove(filepath)
                    with bpy.context.temp_override(area=area, region=region):
                        bpy.ops.screen.screenshot_area(filepath=filepath)
                    img = bpy.data.images.load(filepath)
                    w, h = img.size
                    if w <= 1 or h <= 1:
                        return {"error": f"screenshot_area wrote a {w}x{h} image; the viewport was not drawn"}
                if max(w, h) > max_size:
                    scale = max_size / max(w, h)
                    img.scale(max(1, int(w * scale)), max(1, int(h * scale)))
                    img.file_format = 'PNG'
                    img.save()
                    w, h = img.size
            finally:
                if img is not None:
                    bpy.data.images.remove(img)

            reply = {"success": True, "angle": angle, "filepath": filepath, "width": w, "height": h}
            if overlay:
                reply["overlay"] = str(overlay).lower()
            return reply

        finally:
            pass  # view / smooth_view restore happens in capture_viewport_angle's own finally

    def capture_contact_sheet(self, angles=None, max_size=512, filepath=None, overlay=None):
        """
        Capture multiple viewport angles and return paths for each.
        angles: list of angle names; defaults to [front, right, top, iso_front_right]
        overlay: forwarded to capture_viewport_angle (bones_in_front, wireframe, weight_paint)
        """
        if angles is None:
            angles = ["front", "right", "top", "iso_front_right"]
        if overlay is not None and str(overlay).lower() not in self._CAPTURE_OVERLAYS:
            return {"error": f"overlay '{overlay}' not valid; valid: {list(self._CAPTURE_OVERLAYS)}"}

        results = {}
        for angle in angles:
            fp = os.path.join(tempfile.gettempdir(), f"blender_cs_{angle}_{os.getpid()}.png")
            r = self.capture_viewport_angle(angle=angle, max_size=max_size, filepath=fp, overlay=overlay)
            results[angle] = r

        return {"images": results}

    # ─── Depth map ──────────────────────────────────────────────────────────

    def render_depth_map(self, filepath=None, max_depth=10.0):
        """
        Render a normalised depth map from the active camera using the compositor Z-pass.
        Renders in a throw-away copy of the scene (objects are shared, settings are not),
        so the user's compositor tree, passes and render settings are never touched.
        """
        if not filepath:
            filepath = os.path.join(tempfile.gettempdir(), f"blender_depth_{os.getpid()}.png")

        src = bpy.context.scene
        if src.camera is None:
            return {"error": "Scene has no active camera. Create one with create_camera / set_active_camera."}

        view_layer_name = bpy.context.view_layer.name
        tmp = src.copy()
        tmp.name = f"{src.name}_mcp_depth"
        group = None
        try:
            # Workbench has no Z pass; fall back to EEVEE by try-assign (BLENDER_EEVEE on 5.x,
            # BLENDER_EEVEE_NEXT on 4.2-4.5; _set_render_engine tries both spellings)
            if tmp.render.engine == 'BLENDER_WORKBENCH':
                self._set_render_engine(tmp.render, 'BLENDER_EEVEE')
            # Depth is deterministic: one sample is enough
            with suppress(Exception):
                tmp.eevee.taa_render_samples = 1
            with suppress(Exception):
                tmp.cycles.samples = 1

            # The Depth socket only exists on the Render Layers node once the pass is enabled
            for vl in tmp.view_layers:
                vl.use_pass_z = True

            if hasattr(tmp, "compositing_node_group"):
                # 5.0+: Scene.node_tree is gone; the compositor is a node group assigned to the
                # scene, and its output is a NodeGroupOutput with an Image interface socket.
                group = bpy.data.node_groups.new(f"{tmp.name}_tree", "CompositorNodeTree")
                group.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')
                tmp.compositing_node_group = group
                tree = group
            else:
                tmp.use_nodes = True
                tree = tmp.node_tree
            nodes, links = tree.nodes, tree.links
            nodes.clear()

            # RenderLayers -> Map Range (0..max_depth -> 0..1) -> Invert -> output
            rl = self._new_node(tree, "CompositorNodeRLayers")
            rl.scene = tmp
            if view_layer_name in tmp.view_layers:
                rl.layer = view_layer_name
            rl.location = (0, 0)

            map_node = self._new_node(tree, "CompositorNodeMapRange", "ShaderNodeMapRange")
            map_node.location = (250, 0)
            map_node.inputs["From Min"].default_value = 0.0
            map_node.inputs["From Max"].default_value = max_depth
            map_node.inputs["To Min"].default_value = 0.0
            map_node.inputs["To Max"].default_value = 1.0
            for clamp_attr in ("use_clamp", "clamp"):   # Compositor id vs Shader id spelling
                if hasattr(map_node, clamp_attr):
                    setattr(map_node, clamp_attr, True)
            map_out = map_node.outputs.get("Value") or map_node.outputs.get("Result")
            if map_out is None:
                return {"error": f"Map Range node has no Value/Result output; outputs: {[s.name for s in map_node.outputs]}"}

            invert = self._new_node(tree, "CompositorNodeInvert", "ShaderNodeInvert")
            invert.location = (450, 0)

            if group is not None:
                out_node = self._new_node(tree, "NodeGroupOutput")
            else:
                out_node = self._new_node(tree, "CompositorNodeComposite")
            out_node.location = (650, 0)

            depth_out = rl.outputs.get("Depth") or rl.outputs.get("Z")
            if depth_out is None:
                return {"error": f"Render Layers node has no Depth output; outputs: {[s.name for s in rl.outputs]}"}
            links.new(depth_out, map_node.inputs["Value"])
            links.new(map_out, invert.inputs["Color"])
            links.new(invert.outputs["Color"], out_node.inputs["Image"])

            tmp.render.filepath = filepath
            err = self._set_file_format(tmp.render.image_settings, 'PNG')
            if err:
                return err
            tmp.render.image_settings.color_mode = 'BW'
            if os.path.exists(filepath):
                os.remove(filepath)

            result = bpy.ops.render.render(write_still=True, scene=tmp.name)
            if 'FINISHED' not in result or not os.path.exists(filepath):
                return {"error": f"Depth render did not produce a file (operator returned {set(result)})"}
        finally:
            with suppress(Exception):
                bpy.data.scenes.remove(tmp, do_unlink=True)
            if group is not None:
                with suppress(Exception):
                    bpy.data.node_groups.remove(group, do_unlink=True)

        return {"success": True, "filepath": filepath, "max_depth": max_depth}

    # ─── Reference image ────────────────────────────────────────────────────

    def store_reference_image(self, name, filepath):
        """Register a local image path under a short name for later comparison."""
        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}
        refs = self._reference_images()
        refs[name] = filepath
        return {"success": True, "name": name, "filepath": filepath,
                "stored_refs": list(refs.keys())}

    def get_reference_image(self, name):
        """Look up a reference registered with store_reference_image."""
        refs = self._reference_images()
        filepath = refs.get(name)
        if not filepath:
            return {"error": f"Reference '{name}' not found. Stored: {list(refs.keys())}"}
        return {"success": True, "name": name, "filepath": filepath,
                "exists": os.path.exists(filepath)}

    # ─── Mesh editing ───────────────────────────────────────────────────────

    def move_object(self, name, x=0.0, y=0.0, z=0.0):
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        obj.location = (x, y, z)
        return {"success": True, "name": name, "location": [x, y, z]}

    def scale_object(self, name, x=1.0, y=1.0, z=1.0):
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        obj.scale = (x, y, z)
        return {"success": True, "name": name, "scale": [x, y, z]}

    def rotate_object(self, name, x=0.0, y=0.0, z=0.0, mode="XYZ"):
        """Rotate object (Euler angles in degrees)."""
        import math
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        obj.rotation_mode = mode
        obj.rotation_euler = (math.radians(x), math.radians(y), math.radians(z))
        return {"success": True, "name": name, "rotation_deg": [x, y, z]}

    def set_object_material_color(self, name, r=1.0, g=1.0, b=1.0, a=1.0, material_index=0):
        """Set or create a Principled BSDF material on an object with the given base colour."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}

        # Ensure object has a material slot
        if len(obj.material_slots) <= material_index or obj.material_slots[material_index].material is None:
            mat = bpy.data.materials.new(name=f"{name}_mat_{material_index}")
            mat.use_nodes = True
            if len(obj.material_slots) <= material_index:
                obj.data.materials.append(mat)
            else:
                obj.material_slots[material_index].material = mat
        else:
            mat = obj.material_slots[material_index].material
            if not mat.use_nodes:
                mat.use_nodes = True

        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is None:
            bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")

        bsdf.inputs["Base Color"].default_value = (r, g, b, a)
        return {"success": True, "name": name, "material": mat.name, "color": [r, g, b, a]}

    def get_vertex_positions(self, name, indices=None, world_space=True, max_verts=2000):
        """
        Return vertex positions for a mesh object.
        indices: list of specific vertex indices; returns all if None (capped at max_verts)
        world_space: True = world coordinates, False = local/object coordinates
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        self._ensure_object_mode(obj)
        mesh = obj.data
        mat = obj.matrix_world if world_space else mathutils.Matrix.Identity(4)

        if indices is not None:
            err = self._check_indices(indices, len(mesh.vertices), "Vertex")
            if err:
                return err
            verts = [(i, mesh.vertices[i]) for i in indices]
        else:
            if len(mesh.vertices) > max_verts:
                return {
                    "error": f"Mesh has {len(mesh.vertices)} vertices - exceeds max_verts={max_verts}. "
                             f"Pass specific indices or increase max_verts."
                }
            verts = list(enumerate(mesh.vertices))

        positions = [
            {"index": i, "co": [round(v, 6) for v in (mat @ vert.co)]}
            for i, vert in verts
        ]
        return {
            "name": name,
            "total_vertices": len(mesh.vertices),
            "returned": len(positions),
            "world_space": world_space,
            "vertices": positions,
        }

    def set_vertex_position(self, name, vertex_index, x, y, z):
        """Move a single vertex of a mesh object to world-space coordinates."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh object"}
        self._ensure_object_mode(obj)
        mesh = obj.data
        err = self._check_indices([vertex_index], len(mesh.vertices), "Vertex")
        if err:
            return err
        local = obj.matrix_world.inverted() @ mathutils.Vector((x, y, z))
        mesh.vertices[vertex_index].co = local
        mesh.update()
        return {"success": True, "name": name, "vertex_index": vertex_index, "local": list(local)}

    def set_vertex_positions(self, name, vertices, world_space=True):
        """
        Batch-update multiple vertex positions in a single call.
        vertices: list of {"index": int, "co": [x, y, z]}
        world_space: if True, co values are in world space and will be converted to local
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        self._ensure_object_mode(obj)
        mesh = obj.data
        inv = obj.matrix_world.inverted() if world_space else mathutils.Matrix.Identity(4)

        updated = []
        errors = []
        for entry in vertices:
            idx = entry.get("index")
            co  = entry.get("co")
            if idx is None or co is None:
                errors.append(f"Missing 'index' or 'co' in entry: {entry}")
                continue
            if not (0 <= idx < len(mesh.vertices)):
                errors.append(f"Index {idx} out of range (mesh has {len(mesh.vertices)} vertices)")
                continue
            local = inv @ mathutils.Vector(co)
            mesh.vertices[idx].co = local
            updated.append(idx)

        mesh.update()
        result = {"success": True, "name": name, "updated_count": len(updated), "updated": updated}
        if errors:
            result["errors"] = errors
        return result

    # ─── Curve control points ─────────────────────────────────────────────────

    def get_control_points(self, name, spline_index=0):
        """
        Return the control points of a curve or bezier spline object.
        For BEZIER splines returns: co, handle_left, handle_right, handle_left_type, handle_right_type
        For POLY/NURBS splines returns: co, weight (NURBS only)
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'CURVE':
            return {"error": f"{name} is not a curve object (type={obj.type})"}
        curve = obj.data
        if spline_index >= len(curve.splines):
            return {"error": f"Spline index {spline_index} out of range ({len(curve.splines)} splines)"}

        spline = curve.splines[spline_index]
        mat = obj.matrix_world
        points_out = []

        if spline.type == 'BEZIER':
            for i, pt in enumerate(spline.bezier_points):
                world_co = mat @ pt.co
                world_hl = mat @ pt.handle_left
                world_hr = mat @ pt.handle_right
                points_out.append({
                    "index": i,
                    "co":           [round(v, 6) for v in world_co],
                    "handle_left":  [round(v, 6) for v in world_hl],
                    "handle_right": [round(v, 6) for v in world_hr],
                    "handle_left_type":  pt.handle_left_type,
                    "handle_right_type": pt.handle_right_type,
                })
        else:  # POLY or NURBS
            for i, pt in enumerate(spline.points):
                world_co = mat @ mathutils.Vector(pt.co[:3])
                entry = {"index": i, "co": [round(v, 6) for v in world_co]}
                if spline.type == 'NURBS':
                    entry["weight"] = pt.weight
                points_out.append(entry)

        return {
            "name": name,
            "spline_index": spline_index,
            "spline_type": spline.type,
            "spline_count": len(curve.splines),
            "point_count": len(points_out),
            "points": points_out,
        }

    def set_control_point(self, name, point_index, co,
                           handle_left=None, handle_right=None,
                           handle_left_type=None, handle_right_type=None,
                           spline_index=0):
        """
        Move a curve control point (and optionally its handles).
        co: [x, y, z] in world space.
        handle_left / handle_right: [x, y, z] in world space (bezier only).
        handle_*_type: FREE, ALIGNED, VECTOR, AUTO (bezier only).
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'CURVE':
            return {"error": f"{name} is not a curve object"}
        curve = obj.data
        if not (0 <= spline_index < len(curve.splines)):
            return {"error": f"Spline index {spline_index} out of range ({len(curve.splines)} splines)"}
        spline = curve.splines[spline_index]
        inv = obj.matrix_world.inverted()

        local_co = inv @ mathutils.Vector(co)

        if spline.type == 'BEZIER':
            if not (0 <= point_index < len(spline.bezier_points)):
                return {"error": f"Point index {point_index} out of range ({len(spline.bezier_points)} points)"}
            pt = spline.bezier_points[point_index]
            pt.co = local_co
            if handle_left:
                pt.handle_left  = inv @ mathutils.Vector(handle_left)
            if handle_right:
                pt.handle_right = inv @ mathutils.Vector(handle_right)
            if handle_left_type:
                pt.handle_left_type  = handle_left_type
            if handle_right_type:
                pt.handle_right_type = handle_right_type
        else:
            if not (0 <= point_index < len(spline.points)):
                return {"error": f"Point index {point_index} out of range ({len(spline.points)} points)"}
            pt = spline.points[point_index]
            pt.co = (*local_co, pt.co[3])  # preserve W

        curve.id_data.update_tag()
        return {
            "success": True,
            "name": name,
            "spline_index": spline_index,
            "point_index": point_index,
            "co_world": co,
        }

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    def get_version(self):
        """Addon version, wire protocol and the Blender / Python it runs in (never raises)."""
        return {"addon": ".".join(str(v) for v in bl_info["version"]),
                "protocol": PROTOCOL,
                "blender": bpy.app.version_string,
                "python": platform.python_version(),
                "addon_file": __file__,
                "background": bool(bpy.app.background),
                # D1 G11c: every blendermcp_* driver_namespace registry (reference images, settings
                # snapshots, session states, driver snapshots / transactions) and its entry count
                "registries": {k: len(v) for k, v in bpy.app.driver_namespace.items()
                               if k.startswith("blendermcp_") and hasattr(v, "__len__")}}

    def ensure_server_running(self, port=None):
        """
        Start the socket server if it is not running (it trivially is when this arrives over
        the socket; the panel button and the launch hook share the same function).
        Reply: {"success": True, "running", "port", "host", "started_now"}.
        """
        info = ensure_server(port)
        if "error" in info:
            return {"error": f"Server not running: {info['error']}", **{k: v for k, v in info.items() if k != "error"}}
        return {"success": True, **info}

    def quit_blender(self, save_prompt=False, save=False):
        """
        Quit Blender. The quit is deferred to the next timer tick so this reply
        still reaches the client.
        save: save the current file first (only when it already has a path).
        save_prompt=False suppresses the "save changes?" dialog for this session
        without persisting the preference change.
        """
        saved = False
        if save and bpy.data.filepath:
            bpy.ops.wm.save_mainfile()
            saved = True

        if not save_prompt:
            prefs = bpy.context.preferences
            prefs.view.use_save_prompt = False
            # Do not write this session-only change back to userpref.blend on exit
            prefs.use_preferences_save = False

        def _do_quit():
            wm = bpy.context.window_manager
            win = wm.windows[0] if wm.windows else None
            if win is not None:
                with bpy.context.temp_override(window=win, screen=win.screen):
                    bpy.ops.wm.quit_blender()
            else:
                bpy.ops.wm.quit_blender()
            return None

        bpy.app.timers.register(_do_quit, first_interval=0.3)
        return {"success": True, "saved": saved, "quitting": True}

    # ─── Edge operations ─────────────────────────────────────────────────────

    def get_edges(self, name, indices=None, max_edges=5000):
        """
        Read edge data: vertex pair, sharpness, crease, and bevel weight.
        indices: list of edge indices; returns all if None (capped at max_edges).
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}

        self._ensure_object_mode(obj)
        mesh = obj.data

        if indices is None:
            if len(mesh.edges) > max_edges:
                return {"error": f"Mesh has {len(mesh.edges)} edges - exceeds "
                                  f"max_edges={max_edges}. Pass specific indices or increase max_edges."}
        else:
            err = self._check_indices(indices, len(mesh.edges), "Edge")
            if err:
                return err

        out = []
        with self._bmesh_edit(obj, write=False) as bm:
            crease_layer = bm.edges.layers.float.get("crease_edge")
            bevel_layer  = bm.edges.layers.float.get("bevel_weight_edge")
            edge_list = [bm.edges[i] for i in indices] if indices is not None else list(bm.edges)
            for e in edge_list:
                out.append({
                    "index":        e.index,
                    "vertices":     [e.verts[0].index, e.verts[1].index],
                    "sharp":        not e.smooth,
                    "seam":         e.seam,
                    "crease":       round(e[crease_layer], 6) if crease_layer else 0.0,
                    "bevel_weight": round(e[bevel_layer],  6) if bevel_layer  else 0.0,
                })

        return {
            "name":        name,
            "total_edges": len(mesh.edges),
            "returned":    len(out),
            "edges":       out,
        }

    def mark_sharp_edges(self, name, edge_indices, sharp=True):
        """
        Mark or unmark edges as sharp.
        Sharp edges are respected by auto-smooth and the Edge Split modifier.
        edge_indices: list of edge indices, or "all"
        sharp: True = hard edge, False = soft edge
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}

        if edge_indices != "all":
            err = self._check_indices(edge_indices, len(obj.data.edges), "Edge")
            if err:
                return err

        with self._bmesh_edit(obj) as bm:
            edges = list(bm.edges) if edge_indices == "all" else [bm.edges[i] for i in edge_indices]
            for e in edges:
                e.smooth = not sharp   # smooth=False means sharp in Blender

        return {"success": True, "name": name,
                "marked_edges": len(edges), "sharp": sharp}

    def set_edge_crease(self, name, edge_indices, crease):
        """
        Set subdivision crease weight on edges (0.0 = no crease, 1.0 = fully sharp crease).
        Controls how the Subdivision Surface modifier handles edge sharpness.
        edge_indices: list of edge indices, or "all"
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}

        crease = max(0.0, min(1.0, float(crease)))
        if edge_indices != "all":
            err = self._check_indices(edge_indices, len(obj.data.edges), "Edge")
            if err:
                return err

        with self._bmesh_edit(obj) as bm:
            crease_layer = bm.edges.layers.float.get("crease_edge") or bm.edges.layers.float.new("crease_edge")
            edges = list(bm.edges) if edge_indices == "all" else [bm.edges[i] for i in edge_indices]
            for e in edges:
                e[crease_layer] = crease

        return {"success": True, "name": name,
                "updated_edges": len(edges), "crease": crease}

    def set_edge_bevel_weight(self, name, edge_indices, weight):
        """
        Set bevel weight on edges (0.0 = no bevel, 1.0 = full bevel).
        Used with the Bevel modifier when limit_method is set to WEIGHT.
        edge_indices: list of edge indices, or "all"
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}

        weight = max(0.0, min(1.0, float(weight)))
        if edge_indices != "all":
            err = self._check_indices(edge_indices, len(obj.data.edges), "Edge")
            if err:
                return err

        with self._bmesh_edit(obj) as bm:
            bevel_layer = bm.edges.layers.float.get("bevel_weight_edge") or bm.edges.layers.float.new("bevel_weight_edge")
            edges = list(bm.edges) if edge_indices == "all" else [bm.edges[i] for i in edge_indices]
            for e in edges:
                e[bevel_layer] = weight

        return {"success": True, "name": name,
                "updated_edges": len(edges), "bevel_weight": weight}

    # ─── Face operations ─────────────────────────────────────────────────────

    def get_faces(self, name, indices=None, world_space=True, max_faces=2000):
        """
        Return face data for a mesh object.
        indices: list of face indices; returns all if None (capped at max_faces)
        Each face includes: vertex_indices, normal, center, material_index, area
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}

        self._ensure_object_mode(obj)
        mesh = obj.data
        mat  = obj.matrix_world

        if indices is not None:
            err = self._check_indices(indices, len(mesh.polygons), "Face")
            if err:
                return err
            polys = [(i, mesh.polygons[i]) for i in indices]
        else:
            if len(mesh.polygons) > max_faces:
                return {"error": f"Mesh has {len(mesh.polygons)} faces - exceeds "
                                  f"max_faces={max_faces}. Pass specific indices or increase max_faces."}
            polys = list(enumerate(mesh.polygons))

        faces_out = []
        for i, poly in polys:
            if world_space:
                center = mat @ poly.center
                normal = (mat.to_3x3().inverted().transposed() @ poly.normal).normalized()
            else:
                center = poly.center
                normal = poly.normal

            faces_out.append({
                "index":            i,
                "vertex_indices":   list(poly.vertices),
                "normal":           [round(v, 6) for v in normal],
                "center":           [round(v, 6) for v in center],
                "material_index":   poly.material_index,
                "area":             round(poly.area, 6),
                "loop_total":       poly.loop_total,
            })

        return {
            "name":         name,
            "total_faces":  len(mesh.polygons),
            "returned":     len(faces_out),
            "world_space":  world_space,
            "faces":        faces_out,
        }

    def set_face_material_index(self, name, face_indices, material_index):
        """
        Assign a material slot index to specific faces.
        face_indices: list of face indices, or "all" to affect every face
        material_index: slot number (material must already be in the object's slot list)
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        if material_index >= len(obj.material_slots):
            return {"error": f"Material slot {material_index} does not exist "
                              f"(object has {len(obj.material_slots)} slots)"}

        self._ensure_object_mode(obj)
        mesh = obj.data
        if face_indices == "all":
            face_indices = range(len(mesh.polygons))
        else:
            err = self._check_indices(face_indices, len(mesh.polygons), "Face")
            if err:
                return err

        updated = 0
        for i in face_indices:
            mesh.polygons[i].material_index = material_index
            updated += 1

        mesh.update()
        return {"success": True, "name": name, "updated_faces": updated,
                "material_slot": material_index}

    def extrude_faces(self, name, face_indices, amount=0.2):
        """
        Extrude faces outward along their individual normals.
        face_indices: list of face indices to extrude
        amount: extrusion distance (negative = inward)
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        if not face_indices:
            return {"error": "No face indices provided"}
        err = self._check_indices(face_indices, len(obj.data.polygons), "Face")
        if err:
            return err

        with self._bmesh_edit(obj) as bm:
            faces = [bm.faces[i] for i in face_indices]
            result = bmesh.ops.extrude_face_region(bm, geom=faces)
            new_faces = {g for g in result["geom"] if isinstance(g, bmesh.types.BMFace)}
            new_verts = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
            # extrude_face_region does not recompute normals; do it before reading them
            bm.normal_update()
            # Move every new vert once, along the mean normal of the NEW faces it belongs to
            # (a vert shared by two extruded faces follows their average, as edit-mode extrude does)
            for v in new_verts:
                n = mathutils.Vector((0.0, 0.0, 0.0))
                for f in v.link_faces:
                    if f in new_faces:
                        n += f.normal
                if n.length_squared > 0.0:
                    n.normalize()
                else:
                    n = v.normal
                v.co += n * amount
            # extrude_face_region keeps the source faces; remove them so no cap is left inside
            bmesh.ops.delete(bm, geom=faces, context='FACES')

        return {"success": True, "name": name,
                "extruded_faces": len(face_indices), "new_faces": len(new_faces),
                "amount": amount}

    def inset_faces(self, name, face_indices, thickness=0.1, depth=0.0,
                    use_individual=True):
        """
        Inset (shrink inward) faces, creating a border ring of new faces.
        thickness: inset distance from face edges
        depth: push inset faces along their normals (0 = flat inset)
        use_individual: True = inset each face independently
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        if not face_indices:
            return {"error": "No face indices provided"}
        err = self._check_indices(face_indices, len(obj.data.polygons), "Face")
        if err:
            return err

        with self._bmesh_edit(obj) as bm:
            faces = [bm.faces[i] for i in face_indices]
            if use_individual:
                bmesh.ops.inset_individual(bm, faces=faces,
                                           thickness=thickness, depth=depth,
                                           use_even_offset=True)
            else:
                bmesh.ops.inset_region(bm, faces=faces,
                                       thickness=thickness, depth=depth,
                                       use_even_offset=True)

        return {"success": True, "name": name,
                "inset_faces": len(face_indices),
                "thickness": thickness, "depth": depth}

    def flip_normals(self, name, face_indices=None):
        """
        Flip the normals of specified faces (or all faces if face_indices is None).
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        if face_indices is not None:
            err = self._check_indices(face_indices, len(obj.data.polygons), "Face")
            if err:
                return err

        with self._bmesh_edit(obj) as bm:
            faces = list(bm.faces) if face_indices is None else [bm.faces[i] for i in face_indices]
            bmesh.ops.reverse_faces(bm, faces=faces)

        return {"success": True, "name": name, "flipped_faces": len(faces)}

    def merge_vertices(self, name, distance=0.001):
        """
        Merge (weld) vertices that are within `distance` of each other.
        Equivalent to 'Merge by Distance' in Blender.
        Returns number of vertices removed.
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}

        mesh = obj.data
        with self._bmesh_edit(obj) as bm:
            before = len(bm.verts)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)

        after = len(mesh.vertices)
        return {"success": True, "name": name,
                "removed": before - after,
                "vertices_before": before, "vertices_after": after}

    def triangulate_mesh(self, name, method="BEAUTY"):
        """
        Triangulate all faces of a mesh.
        method: BEAUTY (best quality), FIXED, FIXED_ALTERNATE, SHORTEST_DIAGONAL
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh"}
        valid_methods = ("BEAUTY", "FIXED", "FIXED_ALTERNATE", "SHORTEST_DIAGONAL", "LONGEST_DIAGONAL")
        if method not in valid_methods:
            return {"error": f"Unknown method '{method}'. Choose from: {list(valid_methods)}"}

        mesh = obj.data
        with self._bmesh_edit(obj) as bm:
            bmesh.ops.triangulate(bm, faces=bm.faces, quad_method=method, ngon_method='BEAUTY')

        return {"success": True, "name": name,
                "triangles": len(mesh.polygons)}

    def subdivide_mesh(self, name, cuts=1, smoothness=0.0):
        """Apply a subdivision (loop cuts) to a mesh object."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh object"}

        with self._selection_scope():
            self._select_only(obj)
            bpy.ops.object.mode_set(mode='EDIT')
            try:
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.subdivide(number_cuts=cuts, smoothness=smoothness)
            finally:
                bpy.ops.object.mode_set(mode='OBJECT')

        return {"success": True, "name": name, "cuts": cuts,
                "vertices": len(obj.data.vertices), "faces": len(obj.data.polygons)}

    def apply_modifier(self, name, modifier_name=None, apply_all=False, keep=None):
        """
        Apply a named modifier on an object, or every modifier in stack order with apply_all=True.
        keep: comma list of modifier NAMES or TYPES to skip (e.g. "ARMATURE"); one failure does not
        stop the rest (B2 13.2.2). Reply: applied, kept, failed {name: reason} (+ applied_modifier
        for the single-modifier form).
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        keep_set = {k.upper() for k in (self._names_arg(keep) or [])} | set(self._names_arg(keep) or [])
        if apply_all:
            targets = [m for m in obj.modifiers]
        else:
            if not modifier_name:
                return {"error": "modifier_name is required unless apply_all=True"}
            mod = obj.modifiers.get(modifier_name)
            if not mod:
                return {"error": f"Modifier '{modifier_name}' not found on {name}; modifiers: {[m.name for m in obj.modifiers]}"}
            targets = [mod]
        applied, kept, failed = [], [], {}
        with self._selection_scope():
            self._select_only(obj)
            for mod in targets:
                mname, mtype = mod.name, mod.type
                if mname in keep_set or mtype in keep_set:
                    kept.append(mname)
                    continue
                try:
                    with bpy.context.temp_override(object=obj, active_object=obj):
                        bpy.ops.object.modifier_apply(modifier=mname)
                    applied.append(mname)
                except Exception as e:
                    failed[mname] = str(e).strip()
        if not apply_all and failed:
            return {"error": f"could not apply '{modifier_name}': {failed[modifier_name]}", "name": name, "failed": failed}
        reply = {"success": True, "name": name, "applied": applied, "kept": kept, "failed": failed,
                 "remaining": [m.name for m in obj.modifiers]}
        if not apply_all:
            reply["applied_modifier"] = modifier_name
        return reply

    def get_mesh_stats(self, name):
        """Return detailed mesh topology stats for an object."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh object"}
        self._sync_editmesh(obj)
        mesh = obj.data
        n_polys = len(mesh.polygons)
        loop_totals = array.array('i', [0]) * n_polys
        mesh.polygons.foreach_get("loop_total", loop_totals)
        tri_simple = sum(loop_totals) - 2 * n_polys
        topo = self._topology_counts(obj)
        # local-space bbox (the origin is local 0, so an offset IS the bbox point)
        corners = [mathutils.Vector(c) for c in obj.bound_box]
        lo = mathutils.Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
        hi = mathutils.Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
        center = (lo + hi) / 2.0
        rot_identity = self._rotation_is_identity(obj)
        return {
            "name": name,
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
            "triangles": self._eval_tri_count(obj),       # B2: evaluated (E13); the 2.2 formula stays below
            "triangles_simple": tri_simple,
            "ngons": int(sum(1 for n in loop_totals if n > 4)),
            "quads": int(sum(1 for n in loop_totals if n == 4)),
            "tris": int(sum(1 for n in loop_totals if n == 3)),
            "loose_verts": topo["loose_verts"],
            "loose_edges": topo["loose_edges"],
            "non_manifold_edges": topo["non_manifold_edges"],
            "boundary_edges": topo["boundary_edges"],
            "has_custom_normals": bool(mesh.has_custom_normals),
            "uv_layers": [uv.name for uv in mesh.uv_layers],
            "color_attributes": [ca.name for ca in mesh.color_attributes],
            "material_slots": [{"index": i, "material": s.material.name if s.material else None}
                               for i, s in enumerate(obj.material_slots)],
            "dimensions": [round(float(v), 6) for v in obj.dimensions],
            "unapplied_transform": {"rotation": not rot_identity,
                                    "scale": any(abs(s - 1.0) > 1e-6 for s in obj.scale),
                                    "negative_scale": any(s < 0 for s in obj.scale)},
            "origin_offset": {"to_bbox_center": [round(float(v), 6) for v in center],
                              "to_bbox_bottom": [round(float(center.x), 6), round(float(center.y), 6), round(float(lo.z), 6)]},
            "materials": len(obj.material_slots),
            "modifiers": [m.name for m in obj.modifiers],
            "bounding_box": self._get_aabb(obj),
        }

    def _topology_counts(self, obj):
        """
        Loose / non-manifold / boundary counts from a read-only BMesh of obj.data. Non-manifold =
        BMEdge.is_manifold False (Blender's definition: >2 faces, opposed winding, boundary or wire);
        boundary edges are also counted on their own, loose = verts without edges / edges without faces.
        """
        out = {"loose_verts": 0, "loose_edges": 0, "non_manifold_edges": 0, "boundary_edges": 0}
        with self._bmesh_read(obj) as bm:          # R-B2-36: a read never changes mode or active
            out["loose_verts"] = sum(1 for v in bm.verts if not v.link_edges)
            out["loose_edges"] = sum(1 for e in bm.edges if not e.link_faces)
            out["boundary_edges"] = sum(1 for e in bm.edges if e.is_boundary)
            # Blender's own definition (BMEdge.is_manifold, = select_non_manifold with every flag): boundary and
            # wire edges count too, so an open cube reports 4 and a plane 4
            out["non_manifold_edges"] = sum(1 for e in bm.edges if not e.is_manifold)
        return out

    # ─── Engine readiness: validation (B2 L1, PLAN 13.2.3) ───────────────────

    # H12 profiles. tangents: the preset exports tangents (E5: ngons then abort calc_tangents -> FAIL).
    # collision: (prefix, suffixes) naming rule for collision children; socket: attach-point prefix;
    # strict_names: spaces / dots / non-ASCII in object or material names are flagged.
    _PROFILES = {
        "GENERIC":    {"tangents": False, "collision": ("", ("_COL",)), "socket": "", "strict_names": False},
        "UNITY":      {"tangents": True, "collision": ("", ("_Collider",)), "socket": "", "strict_names": True},
        "UNREAL":     {"tangents": True, "collision": ("UCX_", ()), "socket": "SOCKET_", "strict_names": True},
        "GODOT":      {"tangents": True, "collision": ("", ("-col", "-convcol", "-colonly")), "socket": "", "strict_names": True},
        "BEVY":       {"tangents": True, "collision": ("", ("-col",)), "socket": "", "strict_names": True},
        "TIMBERMESH": {"tangents": False, "collision": ("", ()), "socket": "#", "strict_names": False},
    }
    _ISSUE_KINDS = ("NON_MANIFOLD", "NGONS", "LOOSE", "DEGENERATE", "DOUBLES", "FLIPPED", "INTERIOR_FACES", "BOUNDARY")
    _VALIDATE_CHECKS = ("unapplied_transform", "negative_scale", "origin_bottom_center", "dimensions", "ngons",
                        "non_manifold", "loose", "degenerate", "doubles", "inconsistent_normals", "missing_uv",
                        "uv_out_of_bounds", "missing_uv2", "vertex_count_16bit", "triangle_budget", "material_slots",
                        "materials_without_nodes", "images_missing", "bone_influences", "illegal_names",
                        "name_collisions", "hierarchy")
    _ILLEGAL_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]")

    def _profile_names(self, profile):
        """(profile id, rules dict) for an H12 profile name, or (None, {error, valid})."""
        pid = str(profile or "GENERIC").strip().upper()
        rules = self._PROFILES.get(pid)
        if rules is None:
            return None, {"error": f"profile '{profile}' not valid; valid: {list(self._PROFILES)}", "valid": list(self._PROFILES)}
        return pid, rules

    @classmethod
    def _collision_named(cls, name, mesh_name):
        """True when name matches ANY profile's collision naming rule for mesh_name (R-B2-17 (5))."""
        for rules in cls._PROFILES.values():
            prefix, suffixes = rules["collision"]
            if prefix and name.startswith(prefix):
                return True
            if any(name.startswith(mesh_name + s) or name.endswith(s) for s in suffixes):
                return True
        return False

    @staticmethod
    def _flipped_face_indices(bm):
        """Faces whose normal disagrees with a consistent outward recalculation (dry run on a copy)."""
        before = [f.normal.copy() for f in bm.faces]
        probe = bm.copy()
        try:
            probe.faces.ensure_lookup_table()
            bmesh.ops.recalc_face_normals(probe, faces=probe.faces)
            return [i for i, f in enumerate(probe.faces) if before[i].length > 0 and f.normal.dot(before[i]) < 0.0]
        finally:
            probe.free()

    def _mesh_issue_indices(self, obj, issue, max_indices=500):
        """
        (element, count, indices, truncated) for one issue class on a MESH object. INTERIOR_FACES uses the
        edit-mode select op (E7; its region rule is not a per-face predicate) under the three restore scopes,
        the user's prior active object and mode come back in finally. Every other kind reads a BMesh copy
        (R-B2-36): NON_MANIFOLD = BMEdge.is_manifold False (= select_non_manifold with every flag), NGONS =
        faces with more than 4 verts (= select_face_by_sides GREATER 4).
        """
        issue = str(issue or "").strip().upper()
        if issue not in self._ISSUE_KINDS:
            return None, None, None, {"error": f"issue '{issue}' not valid; valid: {list(self._ISSUE_KINDS)}",
                                      "valid": list(self._ISSUE_KINDS)}
        try:
            cap = int(max_indices)
        except (TypeError, ValueError):
            return None, None, None, {"error": f"max_indices must be an integer >= 1, got {max_indices!r}"}
        if cap < 1:
            return None, None, None, {"error": f"max_indices must be >= 1, got {cap}"}
        element = {"NON_MANIFOLD": "EDGE", "BOUNDARY": "EDGE", "LOOSE": "VERT", "DOUBLES": "VERT"}.get(issue, "FACE")
        indices = []
        # read-only: _selection_scope keeps the handler's target active when nothing was active before;
        # put None back so the caller's state is byte-identical
        view_layer = bpy.context.view_layer
        active_before = view_layer.objects.active
        if issue == "INTERIOR_FACES":
            with self._selection_scope(), self._mesh_select_scope(obj.data), self._mode_restore():
                self._select_only(obj)
                bpy.ops.object.mode_set(mode='EDIT')
                try:
                    bpy.ops.mesh.select_mode(type='FACE')
                    bpy.ops.mesh.select_all(action='DESELECT')
                    bpy.ops.mesh.select_interior_faces()
                    bm = bmesh.from_edit_mesh(obj.data)
                    seq = bm.edges if element == "EDGE" else bm.faces
                    seq.ensure_lookup_table()
                    seq.index_update()      # edit-mesh indices are not guaranteed valid (-1) until updated
                    indices = [x.index for x in seq if x.select]
                finally:
                    with suppress(Exception):
                        bpy.ops.object.mode_set(mode='OBJECT')
        else:
            with self._bmesh_read(obj) as bm:           # R-B2-36: pure read
                if issue == "NON_MANIFOLD":
                    indices = [e.index for e in bm.edges if not e.is_manifold]
                elif issue == "NGONS":
                    indices = [f.index for f in bm.faces if len(f.verts) > 4]
                elif issue == "BOUNDARY":
                    indices = [e.index for e in bm.edges if e.is_boundary]
                elif issue == "LOOSE":
                    indices = [v.index for v in bm.verts if not v.link_faces]
                elif issue == "DEGENERATE":
                    indices = [f.index for f in bm.faces if f.calc_area() < 1e-8]
                elif issue == "DOUBLES":
                    res = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=1e-5)
                    indices = sorted(v.index for v in res["targetmap"].keys())
                elif issue == "FLIPPED":
                    indices = self._flipped_face_indices(bm)
        if active_before is None:
            with suppress(Exception):
                view_layer.objects.active = None
        return element, len(indices), indices[:cap], len(indices) > cap

    def validate_asset(self, objects=None, collection=None, profile="GENERIC", max_triangles=None, require_uv=True,
                       require_uv2=False, max_bone_influences=4, fix=False, _objs=None):
        """
        THE pre-flight tool (13.2.3): 22 fixed checks per mesh, verdict = worst status, summary counts
        OBJECTS by verdict (R-B2-17 (6)); fix=False is read-only. fix=True applies only the six safe
        fixes in order (apply transforms, normals outside, remove doubles, delete loose, triangulate
        ngons, rename illegal characters), re-runs the checks and reports the post-fix verdict.
        """
        pid, rules = self._profile_names(profile)
        if pid is None:
            return rules
        objs, oerr = self._names_or_collection(objects, collection, _objs=_objs)
        if oerr:
            return oerr
        try:
            max_tris = int(max_triangles) if max_triangles is not None else None
            max_inf = int(max_bone_influences)
        except (TypeError, ValueError):
            return {"error": f"max_triangles / max_bone_influences must be integers, got {max_triangles!r} / {max_bone_influences!r}"}
        opts = {"require_uv": bool(require_uv), "require_uv2": bool(require_uv2), "max_triangles": max_tris,
                "max_bone_influences": max_inf}
        all_names = {o.name for o in objs}

        def run_checks():
            dg = bpy.context.evaluated_depsgraph_get()
            report, available = {}, []
            stems = {}                                  # RA-11: one stem -> names map per call
            for x in bpy.data.objects:
                stems.setdefault(re.sub(r"\.\d{3,}$", "", x.name), []).append(x.name)
            for o in objs:
                if o.type != 'MESH':
                    report[o.name] = {"verdict": "PASS", "checks": {}, "detail": "not a mesh"}
                    continue
                checks = self._validate_mesh(o, pid, rules, opts, dg, all_names, stems)
                worst = "PASS"
                for c in checks.values():
                    if c["status"] == "FAIL":
                        worst = "FAIL"
                    elif c["status"] == "WARN" and worst == "PASS":
                        worst = "WARN"
                report[o.name] = {"verdict": worst, "checks": checks}
                for cid, fix_name in (("unapplied_transform", "apply_transforms"), ("negative_scale", "apply_transforms"),
                                      ("inconsistent_normals", "fix_normals_outside"), ("doubles", "remove_doubles"),
                                      ("loose", "delete_loose"), ("ngons", "triangulate_ngons"),
                                      ("illegal_names", "rename_illegal_characters")):
                    if checks[cid]["status"] != "PASS" and not any(a["object"] == o.name and a["fix"] == fix_name for a in available):
                        available.append({"object": o.name, "check_id": cid, "fix": fix_name})
            return report, available

        report, available = run_checks()
        applied = []
        if fix and available:
            with self._selection_scope(), self._mode_restore():
                for o in objs:
                    mine = [a for a in available if a["object"] == o.name]
                    if not mine or o.type != 'MESH':
                        continue
                    fixes = [a["fix"] for a in mine]
                    try:
                        if "apply_transforms" in fixes:
                            negative = any(s < 0 for s in o.scale)
                            r = self.apply_transforms(_objs=[o], rotation=True, scale=True)      # RA-4: refs, not names
                            applied.append({"object": o.name, "check_id": "unapplied_transform", "fix": "apply_transforms",
                                            "detail": "error: " + r["error"] if "error" in r else "rotation and scale applied"})
                            if negative and "error" not in r:
                                # a mirrored object comes out inside out once the scale is baked (E6): recalc outward
                                r2 = self.fix_normals(objects=o.name, mode="OUTSIDE")
                                applied.append({"object": o.name, "check_id": "negative_scale", "fix": "fix_normals_outside",
                                                "detail": "error: " + r2["error"] if "error" in r2 else
                                                f"{r2['faces_flipped'].get(o.name, 0)} faces flipped outward after baking the mirror"})
                                fixes = [f for f in fixes if f != "fix_normals_outside"]
                        if "fix_normals_outside" in fixes:
                            r = self.fix_normals(_objs=[o], mode="OUTSIDE")
                            applied.append({"object": o.name, "check_id": "inconsistent_normals", "fix": "fix_normals_outside",
                                            "detail": "error: " + r["error"] if "error" in r else f"{r['faces_flipped'].get(o.name, 0)} faces flipped"})
                        if "remove_doubles" in fixes or "delete_loose" in fixes:
                            r = self.cleanup_mesh(_objs=[o], remove_doubles=1e-5 if "remove_doubles" in fixes else 0.0,
                                                  delete_loose="delete_loose" in fixes, dissolve_degenerate=False)
                            per = r.get("per_object", {}).get(o.name, {}) if "error" not in r else {}
                            if "remove_doubles" in fixes:
                                applied.append({"object": o.name, "check_id": "doubles", "fix": "remove_doubles",
                                                "detail": "error: " + r["error"] if "error" in r else f"{per.get('doubles_removed', 0)} vertices merged"})
                            if "delete_loose" in fixes:
                                applied.append({"object": o.name, "check_id": "loose", "fix": "delete_loose",
                                                "detail": "error: " + r["error"] if "error" in r else
                                                f"{per.get('loose_verts_removed', 0)} verts, {per.get('loose_edges_removed', 0)} edges removed"})
                        if "triangulate_ngons" in fixes:
                            with self._bmesh_edit(o) as bm:
                                ngons = [f for f in bm.faces if len(f.verts) > 4]
                                n = len(ngons)
                                if ngons:
                                    bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY', ngon_method='BEAUTY')
                            applied.append({"object": o.name, "check_id": "ngons", "fix": "triangulate_ngons",
                                            "detail": f"{n} ngons triangulated"})
                        if "rename_illegal_characters" in fixes:
                            renamed = []
                            new = self._ILLEGAL_NAME_RE.sub("_", o.name)
                            if new != o.name:
                                old = o.name
                                o.name = new
                                all_names.discard(old)
                                all_names.add(o.name)
                                renamed.append(f"{old} -> {o.name}")
                            for slot in o.material_slots:
                                m = slot.material
                                if m is not None:
                                    new = self._ILLEGAL_NAME_RE.sub("_", m.name)
                                    if new != m.name:
                                        old = m.name
                                        m.name = new
                                        renamed.append(f"material {old} -> {m.name}")
                            applied.append({"object": o.name, "check_id": "illegal_names", "fix": "rename_illegal_characters",
                                            "detail": "; ".join(renamed) or "nothing to rename"})
                    except Exception as e:
                        applied.append({"object": o.name, "check_id": "-", "fix": "-", "detail": f"error: {e}"})
            bpy.context.view_layer.update()
            report, available = run_checks()
        verdict = "PASS"
        summary = {"pass": 0, "warn": 0, "fail": 0}
        for entry in report.values():
            summary[entry["verdict"].lower()] += 1
            if entry["verdict"] == "FAIL":
                verdict = "FAIL"
            elif entry["verdict"] == "WARN" and verdict == "PASS":
                verdict = "WARN"
        return {"success": True, "profile": pid, "verdict": verdict, "summary": summary, "objects": report,
                "fixes_available": available, "fixes_applied": applied, "fixed": bool(fix)}

    def _validate_mesh(self, o, pid, rules, opts, dg, all_names, stems=None):
        """The 22 checks for one MESH object; every id present, status PASS when clean."""
        self._sync_editmesh(o)
        me = o.data
        n_v = len(me.vertices)
        checks = {}

        def put(cid, bad, status, detail, count=None, sample=()):
            checks[cid] = {"status": status if bad else "PASS", "detail": detail if bad else "ok",
                           "count": int(count if count is not None else (1 if bad else 0)),
                           "sample_indices": [int(i) for i in list(sample)[:20]]}

        # transforms
        rot_id = self._rotation_is_identity(o)
        scale_id = all(abs(s - 1.0) < 1e-6 for s in o.scale)
        put("unapplied_transform", not (rot_id and scale_id), "FAIL",
            f"rotation {[round(math.degrees(a), 3) for a in o.rotation_euler]} deg, scale {[round(float(s), 4) for s in o.scale]} not applied")
        put("negative_scale", any(s < 0 for s in o.scale), "FAIL", f"negative scale {[round(float(s), 4) for s in o.scale]} mirrors the mesh")
        # origin + dimensions (local bbox)
        corners = [mathutils.Vector(c) for c in o.bound_box]
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        size = max(hi[i] - lo[i] for i in range(3))
        tol = 1e-4 * max(size, 1.0)
        off = ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2])
        put("origin_bottom_center", n_v > 0 and any(abs(v) > tol for v in off), "WARN",
            f"origin is {[round(v, 4) for v in off]} from the bbox bottom centre")
        scale_len = float(bpy.context.scene.unit_settings.scale_length) or 1.0
        dims = [float(d) * scale_len for d in o.dimensions]
        put("dimensions", n_v > 0 and (max(dims) > 100.0 or max(dims) < 0.01), "WARN",
            f"dimensions {[round(d, 4) for d in dims]} m are outside 1 cm .. 100 m")        # topology (one read-only bmesh, R-B2-36)
        uvs = list(me.uv_layers)
        oob = []
        with self._bmesh_read(o) as bm:
            ngons = [f.index for f in bm.faces if len(f.verts) > 4]
            # UVs from the BMesh copy: Mesh.uv_layers[].data reads as length 0 while the object is in EDIT mode
            # (RNA), the BMesh sees the layer either way; loop order = mesh loop order (from_mesh)
            uvl = bm.loops.layers.uv.get(uvs[0].name) if uvs else None
            if uvl is not None:
                i = 0
                for f in bm.faces:
                    for l in f.loops:
                        u, v = l[uvl].uv
                        if not (-1e-6 <= u <= 1.0 + 1e-6 and -1e-6 <= v <= 1.0 + 1e-6):
                            oob.append(i)
                        i += 1
            # R-B2-29: boundary edges are NOT non-manifold for this check (foliage cards, decals and trim pieces are
            # open by design); >2 faces, wire and opposed-winding edges are. get_mesh_stats keeps Blender's definition.
            nonman = [e.index for e in bm.edges
                      if len(e.link_faces) > 2 or not e.link_faces or (len(e.link_faces) == 2 and not e.is_contiguous)]
            n_boundary = sum(1 for e in bm.edges if e.is_boundary)
            loose_v = [v.index for v in bm.verts if not v.link_edges]
            loose_e = [e.index for e in bm.edges if not e.link_faces]
            degen = [f.index for f in bm.faces if f.calc_area() < 1e-8]
            doubles = sorted(v.index for v in bmesh.ops.find_doubles(bm, verts=bm.verts, dist=1e-5)["targetmap"].keys())
            flipped = self._flipped_face_indices(bm) if len(bm.faces) else []
        put("ngons", bool(ngons), "FAIL" if rules["tangents"] else "WARN",
            f"{len(ngons)} faces with more than 4 vertices" + (" (tangent export aborts on ngons, E5)" if rules["tangents"] else ""),
            len(ngons), ngons)
        put("non_manifold", bool(nonman), "WARN", f"{len(nonman)} non-manifold edges (>2 faces, wire or opposed winding)",
            len(nonman), nonman)
        checks["non_manifold"]["detail"] += f"; boundary: {n_boundary}"      # boundary edges reported, never flagged
        put("loose", bool(loose_v or loose_e), "WARN", f"{len(loose_v)} loose vertices, {len(loose_e)} loose edges",
            len(loose_v) + len(loose_e), loose_v or loose_e)
        put("degenerate", bool(degen), "FAIL", f"{len(degen)} zero-area faces", len(degen), degen)
        put("doubles", bool(doubles), "WARN", f"{len(doubles)} vertices within 1e-5 of another", len(doubles), doubles)
        put("inconsistent_normals", bool(flipped), "FAIL", f"{len(flipped)} faces point inward", len(flipped), flipped)
        # UVs (oob read above, from the BMesh)
        put("missing_uv", opts["require_uv"] and not uvs, "FAIL", "no UV layer")
        put("uv_out_of_bounds", bool(oob), "WARN", f"{len(oob)} loops of '{uvs[0].name if uvs else ''}' outside 0..1", len(oob), oob)
        put("missing_uv2", opts["require_uv2"] and len(uvs) < 2, "FAIL", f"{len(uvs)} UV layer(s); a second (lightmap) layer is required")
        # budgets (evaluated)
        ev_v, ev_t = self._eval_vert_count(o, dg), self._eval_tri_count(o, dg)
        put("vertex_count_16bit", ev_v > 65535, "WARN", f"{ev_v} evaluated vertices exceed a 16-bit index buffer", ev_v)
        put("triangle_budget", opts["max_triangles"] is not None and ev_t > opts["max_triangles"], "FAIL",
            f"{ev_t} evaluated triangles over the budget of {opts['max_triangles']}", ev_t)
        # materials and images
        slots = list(o.material_slots)
        used = set()
        if len(me.polygons):
            mi = array.array('i', [0]) * len(me.polygons)
            me.polygons.foreach_get("material_index", mi)
            used = set(mi)
        bad_slots = [i for i, s in enumerate(slots) if s.material is None or i not in used]
        put("material_slots", not slots or bool(bad_slots), "WARN",
            "no material slots" if not slots else
            "empty or unused slots: " + ", ".join(f"{i}={slots[i].material.name if slots[i].material else '<empty>'}" for i in bad_slots),
            len(bad_slots) if slots else 1, bad_slots)
        nonodes = [i for i, s in enumerate(slots) if s.material is not None and not s.material.use_nodes]
        put("materials_without_nodes", bool(nonodes), "WARN",
            f"materials without node trees: {[slots[i].material.name for i in nonodes]}", len(nonodes), nonodes)
        missing = []
        for s in slots:
            m = s.material
            if m is None or not m.use_nodes or m.node_tree is None:
                continue
            for node in m.node_tree.nodes:
                img = getattr(node, "image", None)
                if img is None or img.packed_file is not None or img.source in ('GENERATED', 'VIEWER'):
                    continue
                p = bpy.path.abspath(img.filepath, library=img.library) if img.filepath else ""
                if not p or not os.path.exists(p):
                    missing.append(f"{m.name}:{img.name}")
        put("images_missing", bool(missing), "FAIL", f"images not on disk: {missing}", len(missing))
        # skinning
        over = [v.index for v in me.vertices if sum(1 for g in v.groups if g.weight > 0.0) > opts["max_bone_influences"]]
        put("bone_influences", bool(over), "WARN", f"{len(over)} vertices with more than {opts['max_bone_influences']} weighted groups", len(over), over)
        # names
        if rules["strict_names"]:
            bad_names = [n for n in [o.name] + [s.material.name for s in slots if s.material is not None]
                         if self._ILLEGAL_NAME_RE.search(n)]
            put("illegal_names", bool(bad_names), "WARN", f"names with spaces, dots or non-ASCII: {bad_names}", len(bad_names))
        else:
            put("illegal_names", False, "WARN", "ok")
        stem = re.sub(r"\.\d{3,}$", "", o.name)
        if stems is None:
            stems = {}
            for x in bpy.data.objects:
                stems.setdefault(re.sub(r"\.\d{3,}$", "", x.name), []).append(x.name)
        twins = [n for n in stems.get(stem, []) if n != o.name]
        put("name_collisions", bool(twins), "WARN", f"'{o.name}' collides with {twins} once the .NNN suffix is stripped", len(twins))
        # hierarchy (R-B2-17 (5))
        children = list(o.children)
        coll_children = [c for c in children if (c.type == 'MESH' and c.display_type == 'WIRE' and c.hide_render)
                         or self._collision_named(c.name, o.name)]
        h_bad, h_detail = False, "ok"
        if pid == "UNREAL":
            wrong = [c.name for c in coll_children if not c.name.startswith("UCX_")]
            h_bad, h_detail = bool(wrong), f"collision children without UCX_ naming: {wrong}"
        elif pid == "GODOT":
            wrong = [c.name for c in coll_children if not any(c.name.endswith(s) for s in ("-col", "-convcol", "-colonly"))]
            h_bad, h_detail = bool(wrong), f"collision children without a -col suffix: {wrong}"
        elif pid == "TIMBERMESH":
            chain, p = [], o
            while p is not None:
                chain.append(p.name)
                p = p.parent
            h_bad, h_detail = not any(n.startswith("#") for n in chain), f"no '#' root in the parent chain {chain}"
        put("hierarchy", h_bad, "FAIL", h_detail, len(coll_children) if h_bad else 0)
        return {cid: checks[cid] for cid in self._VALIDATE_CHECKS}

    def find_mesh_issues(self, mesh, issue, render=False, angle="iso_front_right", max_indices=500):
        """
        Element indices for one issue class (NON_MANIFOLD / NGONS / LOOSE / DEGENERATE / DOUBLES / FLIPPED /
        INTERIOR_FACES / BOUNDARY): validate_asset says what, this says where. render=True selects them
        in edit mode and captures the viewport (GUI only; headless returns the R20 error).
        """
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        element, count, indices, truncated = self._mesh_issue_indices(m_obj, issue, max_indices)
        if element is None:
            return truncated
        reply = {"success": True, "mesh": m_obj.name, "issue": str(issue).strip().upper(), "element": element,
                 "count": count, "indices": indices, "truncated": truncated}
        if render:
            if bpy.app.background:
                return {"error": "find_mesh_issues(render=True) needs a GUI session: the viewport capture cannot run under --background",
                        "background": True, "mesh": m_obj.name, "issue": reply["issue"], "count": count}
            wanted = set(indices)
            with self._selection_scope(), self._mesh_select_scope(m_obj.data), self._mode_restore():
                self._select_only(m_obj)
                for coll in (m_obj.data.vertices, m_obj.data.edges, m_obj.data.polygons):
                    for x in coll:
                        x.select = False
                seq = {"VERT": m_obj.data.vertices, "EDGE": m_obj.data.edges, "FACE": m_obj.data.polygons}[element]
                for x in seq:
                    x.select = x.index in wanted
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_mode(type=element)
                cap = self.capture_viewport_angle(angle=angle)
                with suppress(Exception):
                    bpy.ops.object.mode_set(mode='OBJECT')
            if "error" in cap:
                reply["image_error"] = cap["error"]
            else:
                reply["image"] = cap
                if cap.get("filepath"):
                    reply["image_path"] = cap["filepath"]
        return reply

    _COMPARE_KINDS = ("VERTS", "ORDER", "NORMALS", "UVS", "TANGENTS", "MATERIALS", "GROUPS")

    def compare_meshes(self, mesh_a, mesh_b, tolerance=1e-5, compare="ALL"):
        """
        Numeric diff of two meshes for round-trip safety (the SPT lesson, 13.2.3): unit_test_compare first
        pass (same == "Same", E13), then numpy passes via foreach_get into float64 buffers (M12).
        """
        import numpy as np
        a, err = self._get_mesh(mesh_a)
        if err:
            return err
        b, err = self._get_mesh(mesh_b)
        if err:
            return err
        self._sync_editmesh(a)      # a mesh in EDIT mode is compared as it is now, not as it was at edit entry
        self._sync_editmesh(b)
        kinds = [k.strip().upper() for k in str(compare or "ALL").split(",") if k.strip()]
        if "ALL" in kinds:
            kinds = list(self._COMPARE_KINDS)
        bad = [k for k in kinds if k not in self._COMPARE_KINDS]
        if bad:
            return {"error": f"compare kinds not valid: {bad}; valid: {list(self._COMPARE_KINDS) + ['ALL']}",
                    "valid": list(self._COMPARE_KINDS) + ["ALL"]}
        try:
            tol = float(tolerance)
        except (TypeError, ValueError):
            return {"error": f"tolerance must be a number, got {tolerance!r}"}
        ma, mb = a.data, b.data
        try:
            utc = ma.unit_test_compare(mesh=mb, threshold=tol)
        except TypeError:
            utc = ma.unit_test_compare(mesh=mb)
        na, nb = len(ma.vertices), len(mb.vertices)
        reply = {"success": True, "mesh_a": a.name, "mesh_b": b.name, "same": utc == "Same", "unit_test_compare": str(utc),
                 "compare": kinds, "vertices": {"a": na, "b": nb, "delta": nb - na},
                 "triangles": {"a": self._eval_tri_count(a), "b": self._eval_tri_count(b)},
                 "positions": None, "order_match": None, "custom_normals": None, "uvs": None, "tangents": None,
                 "materials": None, "vertex_groups": None}
        reply["triangles"]["delta"] = reply["triangles"]["b"] - reply["triangles"]["a"]

        def buf(coll, attr, width):
            out = np.empty(len(coll) * width, dtype=np.float64)
            coll.foreach_get(attr, out)
            return out.reshape(-1, width)

        if ("VERTS" in kinds or "ORDER" in kinds) and na == nb and na > 0:
            ca, cb = buf(ma.vertices, "co", 3), buf(mb.vertices, "co", 3)
            d = np.linalg.norm(ca - cb, axis=1)
            differing = int((d > tol).sum())
            if "VERTS" in kinds:
                reply["positions"] = {"differing": differing, "max_delta": float(d.max())}
            if "ORDER" in kinds:
                # ORDER = index i of A corresponds to index i of B: B's nearest vertex to A[i] is B[i] for every i
                # (a moved vertex keeps its index; a permutation does not). KD-tree, O(n log n).
                from mathutils import kdtree
                tree = kdtree.KDTree(nb)
                for i in range(nb):
                    tree.insert(cb[i], i)
                tree.balance()
                # B[i] must be as close to A[i] as B's closest vertex is (ties from duplicate vertices allowed)
                reply["order_match"] = all(d[i] <= tree.find(ca[i])[2] + 1e-9 for i in range(na))
        elif na == nb == 0:
            if "VERTS" in kinds:
                reply["positions"] = {"differing": 0, "max_delta": 0.0}
            if "ORDER" in kinds:
                reply["order_match"] = True
        if "NORMALS" in kinds:
            cn = {"a": bool(ma.has_custom_normals), "b": bool(mb.has_custom_normals), "max_angle_deg": None}
            if len(ma.loops) == len(mb.loops) and len(ma.loops) > 0:
                try:
                    va, vb = buf(ma.corner_normals, "vector", 3), buf(mb.corner_normals, "vector", 3)
                    dots = np.clip((va * vb).sum(axis=1), -1.0, 1.0)
                    cn["max_angle_deg"] = float(np.degrees(np.arccos(dots)).max())
                except Exception:
                    cn["max_angle_deg"] = None
            reply["custom_normals"] = cn
        if "UVS" in kinds:
            uv = {"layers_a": [l.name for l in ma.uv_layers], "layers_b": [l.name for l in mb.uv_layers], "max_delta": None}
            if ma.uv_layers and mb.uv_layers and len(ma.loops) == len(mb.loops) and len(ma.loops) > 0:
                d = np.linalg.norm(buf(ma.uv_layers[0].data, "uv", 2) - buf(mb.uv_layers[0].data, "uv", 2), axis=1)
                uv["max_delta"] = float(d.max())
            reply["uvs"] = uv
        if "TANGENTS" in kinds:
            def can_tangent(m):
                if not m.uv_layers or not len(m.loops):
                    return False
                try:
                    m.calc_tangents(uvmap=m.uv_layers[0].name)
                    m.free_tangents()
                    return True
                except Exception:
                    return False
            reply["tangents"] = {"a": can_tangent(ma), "b": can_tangent(mb)}
        if "MATERIALS" in kinds:
            la = [s.material.name if s.material else None for s in a.material_slots]
            lb = [s.material.name if s.material else None for s in b.material_slots]
            reply["materials"] = {"a": la, "b": lb, "same": la == lb}
        if "GROUPS" in kinds:
            ga, gb = [g.name for g in a.vertex_groups], [g.name for g in b.vertex_groups]
            reply["vertex_groups"] = {"a": ga, "b": gb, "same": ga == gb}
        # sections that were not asked for are left out of the reply, not reported as null
        for kind, key in (("VERTS", "positions"), ("ORDER", "order_match"), ("NORMALS", "custom_normals"), ("UVS", "uvs"),
                          ("TANGENTS", "tangents"), ("MATERIALS", "materials"), ("GROUPS", "vertex_groups")):
            if kind not in kinds:
                reply.pop(key, None)
        return reply

    def apply_transforms(self, objects=None, location=False, rotation=True, scale=True, keep_children_world=True, _objs=None):
        """
        object.transform_apply per object with isolate_users=True (a shared mesh gets its own data, E6);
        5.x corrective_flip_normals is try-passed (flip_corrected). Negative scale is applied anyway and
        reported. Children keep their world transform (Blender counter-transforms them).
        """
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        flags = {"location": bool(location), "rotation": bool(rotation), "scale": bool(scale)}
        if not any(flags.values()):
            return {"error": "nothing to apply: set location, rotation or scale"}
        isolated, flipped, negative = [], [], [o.name for o in objs if any(s < 0 for s in o.scale)]
        with self._selection_scope(), self._mode_restore():
            for o in objs:
                if o.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'LATTICE', 'EMPTY', 'ARMATURE', 'META', 'GPENCIL', 'GREASEPENCIL'):
                    continue
                shared = o.data is not None and getattr(o.data, "users", 1) > 1
                # R-B2-39: children either keep their world matrix (default) or keep their parent-inverse and
                # follow the applied transform
                kids = [(c, c.matrix_world.copy(), c.matrix_parent_inverse.copy()) for c in o.children]
                self._select_only(o)
                kw = dict(flags, isolate_users=True)
                try:
                    bpy.ops.object.transform_apply(**kw, corrective_flip_normals=True)
                    if any(s < 0 for s in o.scale) or o.name in negative:
                        flipped.append(o.name)
                except TypeError:
                    bpy.ops.object.transform_apply(**kw)
                except Exception as e:
                    return {"error": f"transform_apply failed on {o.name}: {e}", "objects": [x.name for x in objs]}
                bpy.context.view_layer.update()
                for c, world, pinv in kids:
                    if keep_children_world:
                        c.matrix_world = world
                    else:
                        c.matrix_parent_inverse = pinv
                if shared and o.data is not None and o.data.users == 1:
                    isolated.append(o.name)
        bpy.context.view_layer.update()
        return {"success": True, "objects": [o.name for o in objs], "applied": flags, "isolated": isolated,
                "flip_corrected": [n for n in flipped if n in negative], "negative_scale": negative}

    def fix_normals(self, objects=None, mode="OUTSIDE", also_sharp_by_angle=None, _objs=None):
        """
        OUTSIDE / INSIDE = bmesh recalc_face_normals (+ reverse for INSIDE); FLIP reverses every face;
        CLEAR_CUSTOM drops custom split normals (customdata_custom_splitnormals_clear in OBJECT mode, E4).
        also_sharp_by_angle (degrees) marks edges sharper than the angle. Reply counts per object.
        """
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        mode_id = str(mode or "").strip().upper()
        valid = ("OUTSIDE", "INSIDE", "FLIP", "CLEAR_CUSTOM")
        if mode_id not in valid:
            return {"error": f"mode '{mode}' not valid; valid: {list(valid)}", "valid": list(valid)}
        thresh = None
        if also_sharp_by_angle is not None:
            try:
                thresh = math.radians(float(also_sharp_by_angle))
            except (TypeError, ValueError):
                return {"error": f"also_sharp_by_angle must be degrees, got {also_sharp_by_angle!r}"}
        for o in objs:                          # RA-6: the whole list is checked before anything changes (H11)
            if o.type != 'MESH':
                return {"error": f"{o.name} is not a mesh object (it is {o.type}); nothing changed"}
        flipped_by, cleared, sharp_by = {}, [], {}
        with self._selection_scope(), self._mode_restore():
            for o in objs:
                if mode_id == "CLEAR_CUSTOM":
                    self._select_only(o)
                    if o.data.has_custom_normals:
                        with bpy.context.temp_override(object=o, active_object=o):
                            bpy.ops.mesh.customdata_custom_splitnormals_clear()
                        cleared.append(o.name)
                    flipped_by[o.name] = 0
                else:
                    with self._bmesh_edit(o) as bm:
                        before = [f.normal.copy() for f in bm.faces]
                        if mode_id == "FLIP":
                            bmesh.ops.reverse_faces(bm, faces=bm.faces)
                        else:
                            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
                            if mode_id == "INSIDE":
                                bmesh.ops.reverse_faces(bm, faces=bm.faces)
                        bm.normal_update()
                        flipped_by[o.name] = sum(1 for i, f in enumerate(bm.faces)
                                                 if before[i].length > 0 and f.normal.dot(before[i]) < 0.0)
                if thresh is not None:
                    with self._bmesh_edit(o) as bm:
                        n = 0
                        for e in bm.edges:
                            if len(e.link_faces) == 2:
                                try:
                                    if e.calc_face_angle() > thresh:
                                        e.smooth = False
                                        n += 1
                                except ValueError:
                                    pass
                        sharp_by[o.name] = n
        return {"success": True, "objects": [o.name for o in objs], "mode": mode_id, "faces_flipped": flipped_by,
                "custom_normals_cleared": cleared, "sharp_edges_marked": sharp_by}

    def cleanup_mesh(self, objects=None, remove_doubles=0.0001, delete_loose=True, dissolve_degenerate=True, fill_holes=0,
                     limited_dissolve=None, _objs=None):
        """One BMesh pass per object: remove doubles (0 = skip), delete loose verts/edges, dissolve degenerate,
        fill holes up to N sides (0 = skip), limited dissolve by angle (degrees). Counts per step."""
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        try:
            dist = float(remove_doubles or 0.0)
            sides = int(fill_holes or 0)
            dissolve = math.radians(float(limited_dissolve)) if limited_dissolve is not None else None
        except (TypeError, ValueError):
            return {"error": f"remove_doubles / fill_holes / limited_dissolve must be numbers, got "
                             f"{remove_doubles!r} / {fill_holes!r} / {limited_dissolve!r}"}
        for o in objs:                          # RA-6: the whole list is checked before anything changes (H11)
            if o.type != 'MESH':
                return {"error": f"{o.name} is not a mesh object (it is {o.type}); nothing changed"}
        per = {}
        with self._selection_scope(), self._mode_restore():
            for o in objs:
                with self._bmesh_edit(o) as bm:
                    rec = {"faces_before": len(bm.faces), "doubles_removed": 0, "loose_verts_removed": 0,
                           "loose_edges_removed": 0, "degenerate_dissolved": 0, "holes_filled": 0, "faces_dissolved": 0}
                    # degenerate first: a zero-area face's coincident verts would otherwise be counted as doubles
                    if dissolve_degenerate:
                        n = len(bm.faces)
                        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
                        rec["degenerate_dissolved"] = n - len(bm.faces)
                    if dist > 0:
                        n = len(bm.verts)
                        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
                        rec["doubles_removed"] = n - len(bm.verts)
                    if delete_loose:
                        wire = [e for e in bm.edges if not e.link_faces]
                        rec["loose_edges_removed"] = len(wire)
                        if wire:
                            bmesh.ops.delete(bm, geom=wire, context='EDGES')
                        lone = [v for v in bm.verts if not v.link_edges]
                        rec["loose_verts_removed"] = len(lone)
                        if lone:
                            bmesh.ops.delete(bm, geom=lone, context='VERTS')
                    if sides > 0:
                        res = bmesh.ops.holes_fill(bm, edges=bm.edges, sides=sides)
                        rec["holes_filled"] = len(res.get("faces", []))
                    if dissolve is not None:
                        n = len(bm.faces)
                        bmesh.ops.dissolve_limit(bm, angle_limit=dissolve, verts=bm.verts, edges=bm.edges)
                        rec["faces_dissolved"] = n - len(bm.faces)
                    rec["faces_after"] = len(bm.faces)
                    per[o.name] = rec
        return {"success": True, "objects": [o.name for o in objs], "per_object": per}

    # ─── Engine readiness: optimisation and LODs (B2 L2, PLAN 13.2.3) ─────────

    _DECIMATE_ALIASES = {"COLLAPSE": "COLLAPSE", "PLANAR": "DISSOLVE", "DISSOLVE": "DISSOLVE",
                         "UNSUBDIVIDE": "UNSUBDIV", "UNSUBDIV": "UNSUBDIV"}
    _LOD_SUFFIX_RE = re.compile(r"_LOD\d+$")

    def _duplicate_object(self, obj, new_name):
        """Object + mesh copy linked to the same collections (materials shared through the mesh copy)."""
        dup = obj.copy()
        dup.data = obj.data.copy()
        dup.name = new_name
        dup.data.name = new_name
        dup.animation_data_clear() if dup.animation_data else None
        for coll in (obj.users_collection or [bpy.context.scene.collection]):
            coll.objects.link(dup)
        return dup

    @staticmethod
    def _remove_object_and_mesh(obj):
        """Delete an object this handler created, and its mesh when nothing else uses it (RA-7)."""
        with suppress(Exception):
            me = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)

    def _apply_modifier_on(self, obj, mod_name):
        """Apply one modifier with the object active (the apply_modifier idiom); raises on failure."""
        self._select_only(obj)
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=mod_name)

    def decimate_mesh(self, mesh, ratio=None, target_triangles=None, mode="COLLAPSE", angle_limit=5.0, iterations=2,
                      symmetry_axis=None, vertex_group=None, vertex_group_factor=1.0, keep_uv_seams=True,
                      triangulate=True, apply=True, new_object=None):
        """
        Decimate modifier configured then applied (or left live with apply=False). ratio XOR target_triangles;
        target_triangles iterates the ratio (max 6 passes) until the evaluated triangle count is within 5 %
        (E3: the modifier's result is read back before applying, never applied per pass). mode COLLAPSE /
        PLANAR (= DISSOLVE) / UNSUBDIVIDE (= UNSUBDIV); the reply carries the enum id. keep_uv_seams =
        delimit {UV, SEAM}; triangulate = use_collapse_triangulate; new_object duplicates first (source untouched).
        """
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        if (ratio is None) == (target_triangles is None):
            return {"error": "give either ratio or target_triangles, not both and not neither"}
        mode_id = self._DECIMATE_ALIASES.get(str(mode or "").strip().upper())
        if mode_id is None:
            return {"error": f"mode '{mode}' not valid; valid: {list(self._DECIMATE_ALIASES)}", "valid": list(self._DECIMATE_ALIASES)}
        if ratio is not None:
            try:
                ratio = float(ratio)
            except (TypeError, ValueError):
                return {"error": f"ratio must be a number in (0, 1], got {ratio!r}"}
            if not (0.0 < ratio <= 1.0):
                return {"error": f"ratio must be in (0, 1], got {ratio}"}
        if target_triangles is not None:
            try:
                target_triangles = int(target_triangles)
            except (TypeError, ValueError):
                return {"error": f"target_triangles must be a positive integer, got {target_triangles!r}"}
            if target_triangles < 1:
                return {"error": f"target_triangles must be >= 1, got {target_triangles}"}
        axis = None
        if symmetry_axis is not None:
            axis = str(symmetry_axis).strip().upper()
            if axis not in ("X", "Y", "Z"):
                return {"error": f"symmetry_axis '{symmetry_axis}' not valid; valid: ['X', 'Y', 'Z']", "valid": ["X", "Y", "Z"]}
        if vertex_group is not None and m_obj.vertex_groups.get(str(vertex_group)) is None:
            return {"error": f"vertex group '{vertex_group}' not found on {m_obj.name}; groups: {[g.name for g in m_obj.vertex_groups]}"}
        if new_object is not None and bpy.data.objects.get(str(new_object)) is not None:
            return {"error": f"new_object '{new_object}' already exists", "existing": [str(new_object)]}
        tris_before = self._eval_tri_count(m_obj)
        with self._selection_scope(), self._mode_restore():
            target = self._duplicate_object(m_obj, str(new_object)) if new_object is not None else m_obj
            self._ensure_object_mode(target)
            mod = target.modifiers.new(name="Decimate", type='DECIMATE')
            mod.decimate_type = mode_id
            if mode_id == "COLLAPSE":
                mod.use_collapse_triangulate = bool(triangulate)
                mod.delimit = {'UV', 'SEAM'} if keep_uv_seams else set()
                if axis is not None:
                    mod.use_symmetry = True
                    mod.symmetry_axis = axis
                if vertex_group is not None:
                    mod.vertex_group = str(vertex_group)
                    mod.vertex_group_factor = float(vertex_group_factor)
            elif mode_id == "DISSOLVE":
                mod.angle_limit = math.radians(float(angle_limit))
            else:
                mod.iterations = int(iterations)
            passes = 1
            if mode_id == "COLLAPSE":
                if ratio is not None:
                    mod.ratio = ratio
                    ratio_used = ratio
                else:
                    ratio_used = max(1e-4, min(1.0, target_triangles / float(tris_before or 1)))
                    for passes in range(1, 7):
                        mod.ratio = ratio_used
                        bpy.context.view_layer.update()
                        got = self._eval_tri_count(target)
                        if got <= 0 or abs(got - target_triangles) <= 0.05 * target_triangles:
                            break
                        ratio_used = max(1e-4, min(1.0, ratio_used * target_triangles / float(got)))
            else:
                ratio_used = ratio if ratio is not None else None
            bpy.context.view_layer.update()
            if apply:
                try:
                    self._apply_modifier_on(target, mod.name)
                except Exception as e:
                    if new_object is not None:          # RA-7: no orphan duplicate on an error
                        self._remove_object_and_mesh(target)
                    else:
                        with suppress(Exception):
                            target.modifiers.remove(mod)
                    return {"error": f"could not apply the Decimate modifier on {target.name}: {e}"}
                mod_name = None
            else:
                mod_name = mod.name
            bpy.context.view_layer.update()
            tris_after = self._eval_tri_count(target)
        return {"success": True, "mesh": m_obj.name, "result_object": target.name, "mode": mode_id,
                "ratio_used": ratio_used, "triangles_before": tris_before, "triangles_after": tris_after,
                "passes": passes, "applied": bool(apply), "modifier": mod_name}

    def generate_lods(self, mesh, ratios=None, triangle_targets=None, naming="{name}_LOD{level}", collection=None,
                      parent_to_lod0=False, mode="COLLAPSE", symmetry_axis=None, keep_uv_seams=True,
                      preserve_silhouette_group=None, transfer_normals=False):
        """
        Renames the source to <base>_LOD0 (an existing _LOD<n> suffix is stripped to get base, R-B2-17 (9)) and
        makes each level with decimate_mesh(new_object=...). ratios XOR triangle_targets; both None = the
        default "0.5,0.25,0.1" (R-B2-26). Every target name is checked BEFORE any change: a collision returns
        {error, existing} with the scene untouched. Materials are shared through the mesh copies;
        transfer_normals = Data Transfer of custom normals from LOD0 (applied).
        """
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        if ratios is not None and triangle_targets is not None:
            return {"error": "give ratios or triangle_targets, not both"}
        if ratios is None and triangle_targets is None:
            ratios = "0.5,0.25,0.1"
        levels_spec = []
        if ratios is not None:
            parts = [p.strip() for p in str(ratios).split(",") if p.strip()] if isinstance(ratios, str) else list(ratios)
            if not parts:
                return {"error": "ratios is empty"}
            for p in parts:
                try:
                    v = float(p)
                except (TypeError, ValueError):
                    return {"error": f"ratios must be numbers in (0, 1), got {p!r}"}
                if not (0.0 < v < 1.0):
                    return {"error": f"ratios must be in (0, 1), got {v}"}
                levels_spec.append(("ratio", v))
        else:
            parts = [p.strip() for p in str(triangle_targets).split(",") if p.strip()] if isinstance(triangle_targets, str) else list(triangle_targets)
            if not parts:
                return {"error": "triangle_targets is empty"}
            for p in parts:
                try:
                    v = int(p)
                except (TypeError, ValueError):
                    return {"error": f"triangle_targets must be positive integers, got {p!r}"}
                if v < 1:
                    return {"error": f"triangle_targets must be >= 1, got {v}"}
                levels_spec.append(("target", v))
        base = self._LOD_SUFFIX_RE.sub("", m_obj.name)
        try:
            names = [str(naming).format(name=base, level=i) for i in range(len(levels_spec) + 1)]
        except (KeyError, IndexError, ValueError) as e:
            return {"error": f"naming '{naming}' is not a valid template ({{name}}, {{level}}): {e}"}
        existing = [n for n in names if n != m_obj.name and bpy.data.objects.get(n) is not None]
        if existing:
            return {"error": f"LOD names already exist: {existing}", "existing": existing}
        if preserve_silhouette_group is not None and m_obj.vertex_groups.get(str(preserve_silhouette_group)) is None:
            return {"error": f"vertex group '{preserve_silhouette_group}' not found on {m_obj.name}"}
        original_name = m_obj.name
        m_obj.name = names[0]
        made = []
        try:
            with self._selection_scope(), self._mode_restore():
                levels = [{"level": 0, "name": m_obj.name, "triangles": self._eval_tri_count(m_obj), "ratio": 1.0}]
                for i, (kind, value) in enumerate(levels_spec, start=1):
                    kw = {"mode": mode, "symmetry_axis": symmetry_axis, "keep_uv_seams": keep_uv_seams, "apply": True,
                          "new_object": names[i], "vertex_group": preserve_silhouette_group}
                    if kind == "ratio":
                        kw["ratio"] = value
                    else:
                        kw["target_triangles"] = value
                    r = self.decimate_mesh(m_obj.name, **kw)
                    if "error" in r:
                        raise RuntimeError(f"level {i}: {r['error']}")
                    made.append(bpy.data.objects[names[i]])
                    levels.append({"level": i, "name": names[i], "triangles": r["triangles_after"],
                                   "ratio": r["ratio_used"] if r["ratio_used"] is not None else
                                   (r["triangles_after"] / float(levels[0]["triangles"] or 1))})
                coll_name = None
                if collection is not None:
                    coll = bpy.data.collections.get(str(collection))
                    if coll is None:
                        coll = bpy.data.collections.new(str(collection))
                        bpy.context.scene.collection.children.link(coll)
                    for o in [m_obj] + made:
                        if o.name not in coll.objects:
                            coll.objects.link(o)
                    coll_name = coll.name
                if parent_to_lod0:
                    for o in made:
                        o.parent = m_obj
                        o.matrix_parent_inverse = m_obj.matrix_world.inverted()
                transferred = False
                if transfer_normals and made:
                    for o in made:
                        dt = o.modifiers.new(name="LODNormals", type='DATA_TRANSFER')
                        dt.object = m_obj
                        dt.use_loop_data = True
                        dt.data_types_loops = {'CUSTOM_NORMAL'}
                        dt.loop_mapping = 'POLYINTERP_NEAREST'
                        self._apply_modifier_on(o, dt.name)
                    transferred = all(o.data.has_custom_normals for o in made)
        except Exception as e:
            for o in made:
                with suppress(Exception):
                    me = o.data
                    bpy.data.objects.remove(o, do_unlink=True)
                    if me.users == 0:
                        bpy.data.meshes.remove(me)
            m_obj.name = original_name
            return {"error": f"generate_lods failed, scene restored: {e}"}
        return {"success": True, "source": base, "lod0": m_obj.name, "levels": levels, "collection": coll_name,
                "custom_normals_transferred": transferred}

    _NORMAL_MODES = ("AUTO_SMOOTH", "AUTO_SMOOTH_MODIFIER", "WEIGHTED", "SMOOTH", "FLAT", "FROM_OBJECT", "CLEAR_CUSTOM")

    def set_normals(self, objects=None, mode="AUTO_SMOOTH", angle=30.0, keep_sharp=True, source_object=None, apply=True, _objs=None):
        """
        AUTO_SMOOTH = object.shade_smooth_by_angle (marks sharp edges, no modifier, both versions, E4);
        AUTO_SMOOTH_MODIFIER = object.shade_auto_smooth (the 5.x Smooth by Angle nodes modifier; CANCELLED headless
        on 4.3.2 -> teach error); WEIGHTED = Weighted Normal modifier (FACE_AREA_WITH_ANGLE, keep_sharp);
        FROM_OBJECT = Data Transfer of custom normals from source_object; SMOOTH / FLAT = mesh shading;
        CLEAR_CUSTOM = customdata_custom_splitnormals_clear. apply=False leaves the modifier live. angle in degrees.
        """
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        mode_id = str(mode or "").strip().upper()
        if mode_id not in self._NORMAL_MODES:
            return {"error": f"mode '{mode}' not valid; valid: {list(self._NORMAL_MODES)}", "valid": list(self._NORMAL_MODES)}
        src = None
        if mode_id == "FROM_OBJECT":
            if not source_object:
                return {"error": "source_object is required for mode FROM_OBJECT"}
            src, serr = self._get_mesh(str(source_object))
            if serr:
                return serr
        try:
            rad = math.radians(float(angle))
        except (TypeError, ValueError):
            return {"error": f"angle must be degrees, got {angle!r}"}
        for o in objs:
            if o.type != 'MESH':
                return {"error": f"{o.name} is not a mesh object (it is {o.type})"}
        method = {"AUTO_SMOOTH": "shade_smooth_by_angle", "AUTO_SMOOTH_MODIFIER": "smooth_by_angle_modifier",
                  "WEIGHTED": "weighted_normal", "SMOOTH": "shade_smooth", "FLAT": "shade_flat",
                  "FROM_OBJECT": "data_transfer", "CLEAR_CUSTOM": "clear_custom"}[mode_id]
        modifier_used = None
        with self._selection_scope(), self._mode_restore():
            for o in objs:
                self._ensure_object_mode(o)
                me = o.data
                if mode_id == "AUTO_SMOOTH":
                    self._select_only(o)
                    with bpy.context.temp_override(object=o, active_object=o, selected_editable_objects=[o]):
                        res = bpy.ops.object.shade_smooth_by_angle(angle=rad, keep_sharp_edges=bool(keep_sharp))
                    if 'FINISHED' not in res:
                        return {"error": f"shade_smooth_by_angle returned {sorted(res)} on {o.name}"}
                elif mode_id == "AUTO_SMOOTH_MODIFIER":
                    self._select_only(o)
                    before = {m.name for m in o.modifiers}
                    with bpy.context.temp_override(object=o, active_object=o, selected_editable_objects=[o]):
                        res = bpy.ops.object.shade_auto_smooth(angle=rad)
                    added = [m for m in o.modifiers if m.name not in before]
                    if 'FINISHED' not in res or not added:
                        for m in added:
                            o.modifiers.remove(m)
                        return {"error": f"shade_auto_smooth returned {sorted(res)} on this Blender (no Smooth by Angle "
                                         f"modifier was added, E4); use mode AUTO_SMOOTH instead", "valid": list(self._NORMAL_MODES)}
                    modifier_used = added[-1].name
                    if apply:
                        self._apply_modifier_on(o, modifier_used)
                        modifier_used = None
                elif mode_id == "WEIGHTED":
                    m = o.modifiers.new(name="WeightedNormal", type='WEIGHTED_NORMAL')
                    m.mode = 'FACE_AREA_WITH_ANGLE'
                    m.keep_sharp = bool(keep_sharp)
                    modifier_used = m.name
                    if apply:
                        self._apply_modifier_on(o, m.name)
                        modifier_used = None
                elif mode_id == "FROM_OBJECT":
                    m = o.modifiers.new(name="NormalsFrom", type='DATA_TRANSFER')
                    m.object = src
                    m.use_loop_data = True
                    m.data_types_loops = {'CUSTOM_NORMAL'}
                    m.loop_mapping = 'POLYINTERP_NEAREST'
                    modifier_used = m.name
                    if apply:
                        self._apply_modifier_on(o, m.name)
                        modifier_used = None
                elif mode_id == "SMOOTH":
                    me.shade_smooth()
                elif mode_id == "FLAT":
                    me.shade_flat()
                else:
                    self._select_only(o)
                    if me.has_custom_normals:
                        with bpy.context.temp_override(object=o, active_object=o):
                            bpy.ops.mesh.customdata_custom_splitnormals_clear()
                me.update()
        bpy.context.view_layer.update()
        return {"success": True, "objects": [o.name for o in objs], "mode": mode_id, "method": method,
                "modifier_used": modifier_used,
                "has_custom_normals": {o.name: bool(o.data.has_custom_normals) for o in objs},
                "sharp_edges": {o.name: sum(1 for e in o.data.edges if e.use_edge_sharp) for o in objs}}

    def triangulate_for_export(self, objects=None, quad_method="SHORTEST_DIAGONAL", ngon_method="BEAUTY", keep_custom_normals=True,
                               min_vertices=4, as_modifier=False, _objs=None):
        """Triangulate modifier (keeps custom normals, unlike the BMesh op) applied, or left live with as_modifier=True."""
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        for o in objs:
            if o.type != 'MESH':
                return {"error": f"{o.name} is not a mesh object (it is {o.type})"}
        rna = bpy.types.TriangulateModifier.bl_rna.properties
        q_valid = [i.identifier for i in rna["quad_method"].enum_items]
        n_valid = [i.identifier for i in rna["ngon_method"].enum_items]
        q = str(quad_method or "").strip().upper()
        n = str(ngon_method or "").strip().upper()
        if q not in q_valid:
            return {"error": f"quad_method '{quad_method}' not valid; valid: {q_valid}", "valid": q_valid}
        if n not in n_valid:
            return {"error": f"ngon_method '{ngon_method}' not valid; valid: {n_valid}", "valid": n_valid}
        try:
            min_v = int(min_vertices)
        except (TypeError, ValueError):
            return {"error": f"min_vertices must be an integer >= 4, got {min_vertices!r}"}
        per = {}
        with self._selection_scope(), self._mode_restore():
            for o in objs:
                self._ensure_object_mode(o)
                before = self._eval_tri_count(o)
                m = o.modifiers.new(name="Triangulate", type='TRIANGULATE')
                m.quad_method = q
                m.ngon_method = n
                m.min_vertices = max(4, min_v)
                m.keep_custom_normals = bool(keep_custom_normals)
                name = m.name
                if not as_modifier:
                    try:
                        self._apply_modifier_on(o, m.name)
                    except Exception as e:
                        return {"error": f"could not apply the Triangulate modifier on {o.name}: {e}"}
                    name = None
                bpy.context.view_layer.update()
                per[o.name] = {"triangles_before": before, "triangles_after": self._eval_tri_count(o), "modifier": name}
        return {"success": True, "objects": [o.name for o in objs], "as_modifier": bool(as_modifier), "per_object": per}

    _BUDGET_GROUPS = ("OBJECT", "COLLECTION", "MATERIAL")

    def get_triangle_budget(self, objects=None, budget=None, group_by="OBJECT", _objs=None):
        """Evaluated triangle counts (E13) grouped by OBJECT / COLLECTION / MATERIAL, sorted descending, with the share of
        the total and the budget verdict (over_budget null when no budget). Read-only."""
        group = str(group_by or "OBJECT").strip().upper()
        if group not in self._BUDGET_GROUPS:
            return {"error": f"group_by '{group_by}' not valid; valid: {list(self._BUDGET_GROUPS)}", "valid": list(self._BUDGET_GROUPS)}
        if objects is None:
            objs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        else:
            objs, oerr = self._names_or_collection(objects, _objs=_objs)
            if oerr:
                return oerr
            objs = [o for o in objs if o.type == 'MESH']
        if budget is not None:
            try:
                budget = int(budget)
            except (TypeError, ValueError):
                return {"error": f"budget must be an integer, got {budget!r}"}
        dg = bpy.context.evaluated_depsgraph_get()
        counts = {}
        for o in objs:
            if group == "OBJECT":
                counts[o.name] = counts.get(o.name, 0) + self._eval_tri_count(o, dg)
            elif group == "COLLECTION":
                key = o.users_collection[0].name if o.users_collection else "(none)"
                counts[key] = counts.get(key, 0) + self._eval_tri_count(o, dg)
            else:
                ev = o.evaluated_get(dg)
                me = ev.data
                try:
                    me.calc_loop_triangles()
                    mats = list(me.materials)
                    for t in me.loop_triangles:
                        mat = mats[t.material_index] if 0 <= t.material_index < len(mats) else None
                        key = mat.name if mat is not None else "(none)"
                        counts[key] = counts.get(key, 0) + 1
                except Exception:
                    counts["(none)"] = counts.get("(none)", 0) + self._eval_tri_count(o, dg)
        total = sum(counts.values())
        items = [{"name": k, "triangles": v, "share": (v / float(total)) if total else 0.0}
                 for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        return {"success": True, "group_by": group, "budget": budget, "total": total, "items": items,
                "over_budget": (total > budget) if budget is not None else None}

    # ─── Engine readiness: collision (B2 L2, PLAN 13.2.4) ─────────────────────

    _COLLISION_SHAPES = ("CONVEX", "BOX", "SPHERE", "CAPSULE", "CYLINDER", "MESH_DECIMATED", "SLICED_CONVEX")

    def _collision_name(self, pid, mesh_name, shape):
        """Next free proxy name for a profile (13.2.4 naming rules); Blender's own .NNN suffix handles the rest."""
        if pid == "UNREAL":
            n = 0
            while bpy.data.objects.get(f"UCX_{mesh_name}_{n:02d}") is not None:
                n += 1
            return f"UCX_{mesh_name}_{n:02d}"
        if pid == "GODOT":
            return f"{mesh_name}-convcol" if shape in ("CONVEX", "SLICED_CONVEX") else f"{mesh_name}-col"
        if pid == "UNITY":
            return f"{mesh_name}_Collider"
        return f"{mesh_name}_COL"

    @staticmethod
    def _collision_profile_of(name, mesh_name):
        """Profile whose naming rule the proxy name matches (None when none), used by get_collision_info."""
        stem = re.sub(r"\.\d{3,}$", "", name)
        if re.match(rf"^UCX_{re.escape(mesh_name)}_\d+$", stem):
            return "UNREAL"
        if stem in (f"{mesh_name}-col", f"{mesh_name}-convcol", f"{mesh_name}-colonly"):
            return "GODOT"
        if stem == f"{mesh_name}_Collider":
            return "UNITY"
        if stem == f"{mesh_name}_COL":
            return "GENERIC"
        return None

    @staticmethod
    def _hull_bmesh(src):
        """
        NEW BMesh = convex hull of src's vertex cloud (E11): coincident vertices merged first (a bisected slab
        carries doubles along the cut), hull faces built on bare vertices so no source face survives, interior and
        unused geometry deleted, zero-area faces dissolved, every face a triangle, normals outward. src is untouched.
        """
        bm = bmesh.new()
        for v in src.verts:
            bm.verts.new(v.co)
        bm.verts.ensure_lookup_table()
        if len(bm.verts) >= 2:
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
        if len(bm.verts) < 4:
            return bm
        res = bmesh.ops.convex_hull(bm, input=bm.verts)
        doomed = [g for g in res.get("geom_interior", []) + res.get("geom_unused", []) if g.is_valid]
        verts = list(dict.fromkeys(g for g in doomed if isinstance(g, bmesh.types.BMVert)))   # a vert can sit in both lists
        if verts:
            bmesh.ops.delete(bm, geom=verts, context='VERTS')
        if bm.faces:
            bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
            bmesh.ops.triangulate(bm, faces=bm.faces, quad_method='BEAUTY', ngon_method='BEAUTY')
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        return bm

    @staticmethod
    def _bm_volume(bm):
        try:
            return abs(float(bm.calc_volume(signed=False)))
        except Exception:
            return 0.0

    def _proxy_bmesh(self, src_bm, shape, count, axis, padding, lo, hi):
        """Build the proxy BMeshes for one shape from the evaluated source bmesh (local space). Returns [bm, ...]."""
        ext = [hi[i] - lo[i] for i in range(3)]
        center = mathutils.Vector([(lo[i] + hi[i]) / 2.0 for i in range(3)])
        pad = float(padding)
        if shape == "CONVEX":
            return [self._hull_bmesh(src_bm)]
        if shape == "SLICED_CONVEX":
            ai = "XYZ".index(axis)
            out = []
            step = ext[ai] / float(count) if count else ext[ai]
            for k in range(count):
                a0 = lo[ai] + k * step
                a1 = lo[ai] + (k + 1) * step
                bm = src_bm.copy()
                no = mathutils.Vector((0, 0, 0))
                no[ai] = 1.0
                if k > 0:
                    p = center.copy()
                    p[ai] = a0
                    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=p, plane_no=no,
                                           clear_inner=True, clear_outer=False)
                if k < count - 1:
                    p = center.copy()
                    p[ai] = a1
                    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=p, plane_no=no,
                                           clear_inner=False, clear_outer=True)
                hull = self._hull_bmesh(bm)
                bm.free()
                out.append(hull)
            return out
        if shape == "BOX":
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=mathutils.Vector([ext[i] + 2 * pad for i in range(3)]), verts=bm.verts)
            bmesh.ops.translate(bm, vec=center, verts=bm.verts)
            return [bm]
        if shape == "SPHERE":
            r = max((mathutils.Vector(v.co) - center).length for v in src_bm.verts) + pad if len(src_bm.verts) else pad
            bm = bmesh.new()
            bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=r)
            bmesh.ops.translate(bm, vec=center, verts=bm.verts)
            return [bm]
        # CAPSULE / CYLINDER: along the longest extent
        ai = max(range(3), key=lambda i: ext[i])
        minor = [ext[i] for i in range(3) if i != ai]
        r = max(minor) / 2.0 + pad
        h = ext[ai] + 2 * pad
        bm = bmesh.new()
        if shape == "CYLINDER":
            bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=24, radius1=r, radius2=r, depth=h)
        else:
            body = max(0.0, h - 2 * r)
            bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=r)
            for v in bm.verts:
                v.co.z += body / 2.0 if v.co.z >= 0 else -body / 2.0
        rot = {0: mathutils.Matrix.Rotation(math.radians(90.0), 4, 'Y'), 1: mathutils.Matrix.Rotation(math.radians(90.0), 4, 'X'),
               2: mathutils.Matrix.Identity(4)}[ai]
        bmesh.ops.transform(bm, matrix=mathutils.Matrix.Translation(center) @ rot, verts=bm.verts)
        return [bm]

    def create_collision(self, mesh, shape="CONVEX", profile="UNREAL", count=1, axis="Z", decimate_ratio=0.1, padding=0.0,
                         display_wire=True, parent=True, collection=None):
        """
        Collision proxy from the EVALUATED mesh in the source's local space: CONVEX hull (E11), BOX / SPHERE /
        CAPSULE / CYLINDER fitted to the local bounds (+padding), SLICED_CONVEX = count slabs along axis each hulled,
        MESH_DECIMATED = the mesh decimated by decimate_ratio. Named per profile, WIRE display, hide_render, no
        material, parented to the source (parent=True). volume_ratio = proxy volume / source volume.
        """
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        shape_id = str(shape or "").strip().upper()
        if shape_id not in self._COLLISION_SHAPES:
            return {"error": f"shape '{shape}' not valid; valid: {list(self._COLLISION_SHAPES)}", "valid": list(self._COLLISION_SHAPES)}
        pid, rules = self._profile_names(profile)
        if pid is None:
            return rules
        try:
            count = int(count)
        except (TypeError, ValueError):
            return {"error": f"count must be an integer 1..32, got {count!r}"}
        if not (1 <= count <= 32):
            return {"error": f"count must be 1..32, got {count}"}
        axis_id = str(axis or "").strip().upper()
        if axis_id not in ("X", "Y", "Z"):
            return {"error": f"axis '{axis}' not valid; valid: ['X', 'Y', 'Z']", "valid": ["X", "Y", "Z"]}
        try:
            decimate_ratio = float(decimate_ratio)
            padding = float(padding)
        except (TypeError, ValueError):
            return {"error": f"decimate_ratio / padding must be numbers, got {decimate_ratio!r} / {padding!r}"}
        if not (0.0 < decimate_ratio <= 1.0):
            return {"error": f"decimate_ratio must be in (0, 1], got {decimate_ratio}"}
        coll = None
        if collection is not None:
            coll = bpy.data.collections.get(str(collection))
            if coll is None:
                coll = bpy.data.collections.new(str(collection))
                bpy.context.scene.collection.children.link(coll)
        else:
            coll = m_obj.users_collection[0] if m_obj.users_collection else bpy.context.scene.collection
        dg = bpy.context.evaluated_depsgraph_get()
        src_bm = bmesh.new()
        try:
            src_bm.from_mesh(m_obj.evaluated_get(dg).data)
        except Exception as e:
            src_bm.free()
            return {"error": f"could not read the evaluated mesh of {m_obj.name}: {e}"}
        if not src_bm.verts:
            src_bm.free()
            return {"error": f"{m_obj.name} has no vertices"}
        src_volume = self._bm_volume(src_bm)
        lo = [min(v.co[i] for v in src_bm.verts) for i in range(3)]
        hi = [max(v.co[i] for v in src_bm.verts) for i in range(3)]
        proxies = []
        with self._selection_scope(), self._mode_restore():
            try:
                if shape_id == "MESH_DECIMATED":
                    bms = [src_bm.copy()]
                else:
                    bms = self._proxy_bmesh(src_bm, shape_id, count if shape_id == "SLICED_CONVEX" else 1, axis_id,
                                            padding, lo, hi)
            finally:
                src_bm.free()
            for bm in bms:
                name = self._collision_name(pid, m_obj.name, shape_id)
                me = bpy.data.meshes.new(name)
                try:
                    bm.to_mesh(me)
                    volume = self._bm_volume(bm)
                finally:
                    bm.free()
                me.update()
                px = bpy.data.objects.new(name, me)
                coll.objects.link(px)
                if shape_id == "MESH_DECIMATED":
                    mod = px.modifiers.new(name="Decimate", type='DECIMATE')
                    mod.decimate_type = 'COLLAPSE'
                    mod.ratio = decimate_ratio
                    mod.use_collapse_triangulate = True
                    try:
                        self._apply_modifier_on(px, mod.name)
                    except Exception as e:
                        self._remove_object_and_mesh(px)            # RA-7: no orphan proxy on an error
                        for p in proxies:
                            self._remove_object_and_mesh(bpy.data.objects.get(p["name"]))
                        return {"error": f"could not decimate the proxy: {e}"}
                    tmp = bmesh.new()
                    tmp.from_mesh(px.data)
                    volume = self._bm_volume(tmp)
                    tmp.free()
                if parent:
                    px.parent = m_obj
                    px.matrix_parent_inverse = mathutils.Matrix.Identity(4)
                else:
                    px.matrix_world = m_obj.matrix_world.copy()
                px.display_type = 'WIRE' if display_wire else 'TEXTURED'
                px.hide_render = True
                bpy.context.view_layer.update()
                proxies.append({"name": px.name, "triangles": self._eval_tri_count(px),
                                "volume_ratio": (volume / src_volume) if src_volume > 1e-12 else None})
        return {"success": True, "mesh": m_obj.name, "shape": shape_id, "profile": pid, "proxies": proxies,
                "parent": m_obj.name if parent else None, "collection": coll.name if collection is not None else None}

    def get_collision_info(self, mesh):
        """Proxies of mesh by every profile's naming rule (UCX_<mesh>_NN, -col / -convcol / -colonly, _Collider, _COL),
        with their evaluated triangle counts and whether they are children of the mesh. Read-only."""
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        dg = bpy.context.evaluated_depsgraph_get()
        out = []
        for o in bpy.data.objects:
            if o is m_obj or o.type != 'MESH':
                continue
            pid = self._collision_profile_of(o.name, m_obj.name)
            if pid is None:
                continue
            out.append({"name": o.name, "profile": pid, "triangles": self._eval_tri_count(o, dg), "is_child": o.parent is m_obj})
        out.sort(key=lambda p: p["name"])
        return {"success": True, "mesh": m_obj.name, "proxies": out}

    def check_collision_fit(self, mesh, samples=2000):
        """
        Fraction of area-weighted random surface samples of the evaluated mesh (seed 0, deterministic) that lie inside
        the union of its proxies (BVH nearest-point normal test), and the largest distance an uncovered sample is
        from the nearest proxy surface. The numeric answer to "does the collider cover the mesh".
        """
        from mathutils.bvhtree import BVHTree
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        try:
            samples = int(samples)
        except (TypeError, ValueError):
            return {"error": f"samples must be an integer 1..20000, got {samples!r}"}
        if not (1 <= samples <= 20000):
            return {"error": f"samples must be 1..20000, got {samples}"}
        info = self.get_collision_info(m_obj.name)
        names = [p["name"] for p in info.get("proxies", [])]
        if not names:
            return {"error": f"no collision proxies found for {m_obj.name} (create_collision first)", "mesh": m_obj.name}
        dg = bpy.context.evaluated_depsgraph_get()
        me = m_obj.evaluated_get(dg).data
        me.calc_loop_triangles()
        tris = list(me.loop_triangles)
        if not tris:
            return {"error": f"{m_obj.name} has no faces to sample"}
        mw = m_obj.matrix_world
        verts = me.vertices
        areas = []
        for t in tris:
            a, b, c = (mw @ verts[i].co for i in t.vertices)
            areas.append((b - a).cross(c - a).length / 2.0)
        total = sum(areas)
        if total <= 0:
            return {"error": f"{m_obj.name} has zero surface area"}
        cum, acc = [], 0.0
        for ar in areas:
            acc += ar
            cum.append(acc)
        import bisect
        rng = random.Random(0)
        proxies = []
        for n in names:
            px = bpy.data.objects[n]
            try:
                proxies.append((BVHTree.FromObject(px, dg), px.matrix_world.inverted(), px.matrix_world))
            except Exception:
                continue
        if not proxies:
            return {"error": f"no usable proxy geometry for {m_obj.name}", "proxies": names}
        inside = 0
        max_outside = 0.0
        for _ in range(samples):
            t = tris[min(bisect.bisect_left(cum, rng.random() * total), len(tris) - 1)]
            r1, r2 = rng.random(), rng.random()
            if r1 + r2 > 1.0:
                r1, r2 = 1.0 - r1, 1.0 - r2
            a, b, c = (mw @ verts[i].co for i in t.vertices)
            p = a + (b - a) * r1 + (c - a) * r2
            covered = False
            best = None
            for tree, inv, pmw in proxies:
                lp = inv @ p
                loc, nor, idx, dist = tree.find_nearest(lp)
                if loc is None:
                    continue
                if nor.dot(lp - loc) <= 1e-6:
                    covered = True
                    break
                d = (pmw @ loc - p).length
                best = d if best is None else min(best, d)
            if covered:
                inside += 1
            elif best is not None:
                max_outside = max(max_outside, best)
        return {"success": True, "mesh": m_obj.name, "samples": samples, "proxies": names,
                "covered_fraction": inside / float(samples), "max_outside_distance": max_outside, "inside_samples": inside}

    # ─── Engine readiness: sockets, pivots and hierarchy (B2 L3, PLAN 13.2.5) ──

    _SOCKET_PROFILES = ("UNREAL", "TIMBERMESH", "UNITY", "GENERIC")
    _SOCKET_PREFIX = {"UNREAL": "SOCKET_", "TIMBERMESH": "#", "UNITY": "", "GENERIC": ""}
    _PIVOT_MODES = ("BOTTOM_CENTER", "CENTER", "TOP_CENTER", "WORLD_ORIGIN", "CURSOR", "CUSTOM")
    _SNAP_WHAT = ("ORIGIN", "BOUNDS_MIN", "BOUNDS_CENTER")

    def _vec3_arg(self, value, label):
        """"x,y,z" string or a 3-list -> ([x, y, z], None) or (None, error dict)."""
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",")]
        else:
            parts = value
        try:
            vec, err = self._vec3(parts, label)
        except TypeError:
            vec, err = None, f"{label} must be 3 numbers, got {value!r}"
        return (vec, None) if err is None else (None, {"error": err})

    @staticmethod
    def _has_hash_root(obj):
        p = obj
        while p is not None:
            if p.name.startswith("#"):
                return True
            p = p.parent
        return False

    @staticmethod
    def _parent_keep_world(child, parent, bone=None):
        """Parent child under parent (or a bone) keeping its world transform."""
        world = child.matrix_world.copy()
        child.parent = parent
        if bone:
            child.parent_type = 'BONE'
            child.parent_bone = bone
        else:
            child.parent_type = 'OBJECT'
        child.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        child.matrix_world = world

    def add_socket(self, parent, name, location="0,0,0", rotation="0,0,0", space="LOCAL", profile="UNREAL", bone=None,
                   display_size=0.1, snap_to="NONE", vertex_index=None):
        """
        Empty parented to `parent` (or to `bone` of an ARMATURE parent) named SOCKET_<name> (UNREAL), #<name>
        (TIMBERMESH, needs a '#' root in the parent chain) or <name>. location / rotation (degrees) in LOCAL or
        WORLD space; snap_to SURFACE = closest point on the parent mesh from the location, VERTEX = vertex_index.
        """
        p_obj = bpy.data.objects.get(str(parent))
        if p_obj is None:
            return {"error": f"Object not found: {parent}"}
        if not name:
            return {"error": "name is required"}
        pid = str(profile or "UNREAL").strip().upper()
        if pid not in self._SOCKET_PROFILES:
            return {"error": f"profile '{profile}' not valid; valid: {list(self._SOCKET_PROFILES)}", "valid": list(self._SOCKET_PROFILES)}
        space_id = str(space or "LOCAL").strip().upper()
        if space_id not in ("LOCAL", "WORLD"):
            return {"error": f"space '{space}' not valid; valid: ['LOCAL', 'WORLD']", "valid": ["LOCAL", "WORLD"]}
        snap_id = str(snap_to or "NONE").strip().upper()
        if snap_id not in ("NONE", "SURFACE", "VERTEX"):
            return {"error": f"snap_to '{snap_to}' not valid; valid: ['NONE', 'SURFACE', 'VERTEX']", "valid": ["NONE", "SURFACE", "VERTEX"]}
        loc, lerr = self._vec3_arg(location, "location")
        if lerr:
            return lerr
        rot, rerr = self._vec3_arg(rotation, "rotation")
        if rerr:
            return rerr
        try:
            display_size = float(display_size)
        except (TypeError, ValueError):
            return {"error": f"display_size must be a number, got {display_size!r}"}
        if bone is not None:
            if p_obj.type != 'ARMATURE':
                return {"error": f"bone given but {p_obj.name} is a {p_obj.type}, not an ARMATURE"}
            if p_obj.data.bones.get(str(bone)) is None:
                return {"error": f"bone '{bone}' not found on {p_obj.name}; bones: {[b.name for b in p_obj.data.bones][:30]}"}
        if pid == "TIMBERMESH" and not self._has_hash_root(p_obj):
            return {"error": f"TIMBERMESH sockets need a '#' root in the parent chain of {p_obj.name} (Timbermesh merges "
                             f"everything else into its nearest '#' node); none found"}
        loc_v = mathutils.Vector(loc)
        world_loc = (p_obj.matrix_world @ loc_v) if space_id == "LOCAL" else loc_v
        if snap_id == "VERTEX":
            if p_obj.type != 'MESH':
                return {"error": f"snap_to VERTEX needs a MESH parent, {p_obj.name} is {p_obj.type}"}
            if vertex_index is None:
                return {"error": "vertex_index is required for snap_to VERTEX"}
            try:
                vi = int(vertex_index)
            except (TypeError, ValueError):
                return {"error": f"vertex_index must be an integer, got {vertex_index!r}"}
            n = len(p_obj.data.vertices)
            if not (0 <= vi < n):
                return {"error": f"vertex_index {vi} out of range (mesh has {n} vertices)"}
            world_loc = p_obj.matrix_world @ p_obj.data.vertices[vi].co
        elif snap_id == "SURFACE":
            if p_obj.type != 'MESH':
                return {"error": f"snap_to SURFACE needs a MESH parent, {p_obj.name} is {p_obj.type}"}
            hit, lp, _nor, _idx = p_obj.closest_point_on_mesh(p_obj.matrix_world.inverted() @ world_loc)
            if not hit:
                return {"error": f"closest_point_on_mesh found no surface on {p_obj.name}"}
            world_loc = p_obj.matrix_world @ lp
        rot_m = mathutils.Euler([math.radians(v) for v in rot], 'XYZ').to_matrix().to_4x4()
        full = self._SOCKET_PREFIX[pid] + str(name)
        sock = bpy.data.objects.new(full, None)
        sock.empty_display_type = 'ARROWS'
        sock.empty_display_size = display_size
        coll = p_obj.users_collection[0] if p_obj.users_collection else bpy.context.scene.collection
        coll.objects.link(sock)
        sock.parent = p_obj
        if bone is not None:
            sock.parent_type = 'BONE'
            sock.parent_bone = str(bone)
        sock.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        bpy.context.view_layer.update()
        if space_id == "LOCAL" and snap_id == "NONE":
            sock.location = loc_v
            sock.rotation_euler = [math.radians(v) for v in rot]
        else:
            sock.matrix_world = mathutils.Matrix.Translation(world_loc) @ (rot_m if space_id == "WORLD" else
                                                                           (p_obj.matrix_world.to_3x3().to_4x4() @ rot_m))
        bpy.context.view_layer.update()
        mw = sock.matrix_world
        return {"success": True, "name": sock.name, "parent": p_obj.name, "bone": str(bone) if bone is not None else None,
                "profile": pid, "location_world": [round(float(v), 6) for v in mw.translation],
                "rotation_world": [round(math.degrees(a), 6) for a in mw.to_euler('XYZ')],
                "matrix_world": [[round(float(v), 6) for v in row] for row in mw]}

    def list_sockets(self, parent=None):
        """EMPTY objects named SOCKET_* or #* (any parent), or every EMPTY child of `parent` when given. Read-only."""
        if parent is not None:
            p_obj = bpy.data.objects.get(str(parent))
            if p_obj is None:
                return {"error": f"Object not found: {parent}"}
            pool = [o for o in p_obj.children if o.type == 'EMPTY']
        else:
            pool = [o for o in bpy.data.objects if o.type == 'EMPTY' and (o.name.startswith("SOCKET_") or o.name.startswith("#"))]
        out = []
        for o in sorted(pool, key=lambda x: x.name):
            prof = "UNREAL" if o.name.startswith("SOCKET_") else ("TIMBERMESH" if o.name.startswith("#") else "GENERIC")
            out.append({"name": o.name, "profile": prof, "parent": o.parent.name if o.parent else None,
                        "bone": o.parent_bone if (o.parent is not None and o.parent_type == 'BONE' and o.parent_bone) else None,
                        "location_local": [round(float(v), 6) for v in o.location],
                        "location_world": [round(float(v), 6) for v in o.matrix_world.translation],
                        "rotation_local": [round(math.degrees(a), 6) for a in o.rotation_euler]})
        return {"success": True, "parent": p_obj.name if parent is not None else None, "count": len(out), "sockets": out}

    def set_pivot(self, objects=None, mode="BOTTOM_CENTER", location=None, keep_world=True, _objs=None):
        """Batch wrapper on set_origin: BOTTOM_CENTER / TOP_CENTER / CUSTOM pass through, CENTER = ORIGIN_GEOMETRY,
        WORLD_ORIGIN = CUSTOM at 0,0,0, CURSOR = ORIGIN_CURSOR. World geometry never moves; every name is checked first."""
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        mode_id = str(mode or "").strip().upper()
        if mode_id not in self._PIVOT_MODES:
            return {"error": f"mode '{mode}' not valid; valid: {list(self._PIVOT_MODES)}", "valid": list(self._PIVOT_MODES)}
        if mode_id == "CUSTOM" and location is None:
            return {"error": "location=\"x,y,z\" (world) is required for mode CUSTOM"}
        for o in objs:
            if o.type != 'MESH' and mode_id in ("BOTTOM_CENTER", "TOP_CENTER", "CUSTOM", "WORLD_ORIGIN"):
                return {"error": f"{o.name} is not a mesh object (it is {o.type})"}
        per = {}
        for o in objs:
            if mode_id in ("BOTTOM_CENTER", "TOP_CENTER"):
                r = self.set_origin(o.name, mode_id, keep_world=keep_world)          # R-B2-39
            elif mode_id == "CUSTOM":
                r = self.set_origin(o.name, "CUSTOM", location, keep_world=keep_world)
            elif mode_id == "WORLD_ORIGIN":
                r = self.set_origin(o.name, "CUSTOM", "0,0,0", keep_world=keep_world)
            elif mode_id == "CENTER":
                r = self.set_origin(o.name, "ORIGIN_GEOMETRY")
            else:
                r = self.set_origin(o.name, "ORIGIN_CURSOR")
            if "error" in r:
                return {"error": f"{o.name}: {r['error']}", "objects": [x.name for x in objs], "done": list(per)}
            per[o.name] = {"new_location": [float(v) for v in o.location]}
        return {"success": True, "objects": [o.name for o in objs], "mode": mode_id, "per_object": per,
                "keep_world": bool(keep_world)}

    def snap_to_grid(self, objects=None, grid=1.0, axes="XYZ", what="ORIGIN", _objs=None):
        """Move each object so its origin (or world bbox min / centre) lands on the grid on the chosen axes."""
        objs, oerr = self._names_or_collection(objects, _objs=_objs)
        if oerr:
            return oerr
        try:
            grid = float(grid)
        except (TypeError, ValueError):
            return {"error": f"grid must be a number > 0, got {grid!r}"}
        if grid <= 0:
            return {"error": f"grid must be > 0, got {grid}"}
        ax = str(axes or "").strip().upper()
        if not ax or any(c not in "XYZ" for c in ax):
            return {"error": f"axes '{axes}' not valid; use any of X, Y, Z (e.g. \"XY\")", "valid": ["X", "Y", "Z", "XY", "XZ", "YZ", "XYZ"]}
        what_id = str(what or "ORIGIN").strip().upper()
        if what_id not in self._SNAP_WHAT:
            return {"error": f"what '{what}' not valid; valid: {list(self._SNAP_WHAT)}", "valid": list(self._SNAP_WHAT)}
        per = {}
        for o in objs:
            before = [float(v) for v in o.location]
            if what_id == "ORIGIN":
                anchor = o.matrix_world.translation.copy()
            else:
                corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
                lo = [min(c[i] for c in corners) for i in range(3)]
                hi = [max(c[i] for c in corners) for i in range(3)]
                anchor = mathutils.Vector(lo) if what_id == "BOUNDS_MIN" else mathutils.Vector([(lo[i] + hi[i]) / 2.0 for i in range(3)])
            delta = mathutils.Vector((0, 0, 0))
            for i, c in enumerate("XYZ"):
                if c in ax:
                    delta[i] = round(anchor[i] / grid) * grid - anchor[i]
            mw = o.matrix_world.copy()
            mw.translation = mw.translation + delta
            o.matrix_world = mw
            bpy.context.view_layer.update()
            per[o.name] = {"before": before, "after": [round(float(v), 6) for v in o.location]}
        return {"success": True, "objects": [o.name for o in objs], "grid": grid, "axes": ax, "what": what_id, "per_object": per}

    def build_export_hierarchy(self, root_name, meshes, collision=None, sockets=None, lods=None, profile="UNREAL"):
        """
        Root EMPTY (reused when it exists) with the meshes under it; LODs as root siblings with an int `lod_group`
        custom prop; collision under its mesh (GODOT: a root sibling); sockets under their mesh. World transforms
        are kept. Every name is checked before anything changes ({error, unknown}).
        """
        pid, rules = self._profile_names(profile)
        if pid is None:
            return rules
        if not root_name:
            return {"error": "root_name is required"}
        groups = {}
        for label, value in (("meshes", meshes), ("collision", collision), ("sockets", sockets), ("lods", lods)):
            names = self._names_arg(value) or []
            if label == "meshes" and not names:
                return {"error": "meshes is required (comma list)"}
            unknown = [n for n in names if bpy.data.objects.get(n) is None]
            if unknown:
                return {"error": f"{label} not found: {unknown}", "unknown": unknown}
            groups[label] = [bpy.data.objects[n] for n in names]
        root = bpy.data.objects.get(str(root_name))
        created = False
        if root is None:
            root = bpy.data.objects.new(str(root_name), None)
            root.empty_display_type = 'PLAIN_AXES'
            coll = groups["meshes"][0].users_collection[0] if groups["meshes"][0].users_collection else bpy.context.scene.collection
            coll.objects.link(root)
            created = True
        elif root.type != 'EMPTY':
            return {"error": f"root_name '{root_name}' exists but is a {root.type}, not an EMPTY"}
        props_set = {}

        def set_prop(o, key, value):
            o[key] = value
            props_set.setdefault(o.name, {})[key] = value

        def mesh_for(name):
            """The mesh a collision / socket name refers to (naming rule prefix), else the first mesh."""
            best = None
            for m in groups["meshes"] + groups["lods"]:
                if name.startswith(m.name) or name.startswith("UCX_" + m.name):
                    if best is None or len(m.name) > len(best.name):
                        best = m
            return best or groups["meshes"][0]

        with self._selection_scope(), self._mode_restore():
            for i, m in enumerate(groups["meshes"]):
                if m.parent is not root:
                    self._parent_keep_world(m, root)
                mm = self._LOD_SUFFIX_RE.search(m.name)
                set_prop(m, "lod_group", int(mm.group(0)[4:]) if mm else 0)
            for i, l in enumerate(groups["lods"], start=1):
                if l.parent is not root:
                    self._parent_keep_world(l, root)
                mm = self._LOD_SUFFIX_RE.search(l.name)
                set_prop(l, "lod_group", int(mm.group(0)[4:]) if mm else i)
            for c in groups["collision"]:
                target = root if pid == "GODOT" else mesh_for(c.name)
                if c.parent is not target:
                    self._parent_keep_world(c, target)
            for s in groups["sockets"]:
                target = s.parent if (s.parent is not None and s.parent in groups["meshes"] + groups["lods"]) else mesh_for(s.name)
                if s.parent is not target:
                    self._parent_keep_world(s, target)
            bpy.context.view_layer.update()

        def tree(o):
            return {"name": o.name, "type": o.type, "children": [tree(c) for c in sorted(o.children, key=lambda x: x.name)]}

        return {"success": True, "root": root.name, "profile": pid, "created_root": created, "tree": tree(root),
                "custom_props_set": props_set}

    # ─── Engine readiness: engine export (B2 L3, PLAN 13.2.6) ─────────────────

    _ENGINE_PRESETS = {
        "UNITY": {"format": "fbx", "exporter": "fbx", "axis": {"forward": "-Z", "up": "Y"},
                  "units": {"scale_length": 1.0, "length_unit": "METERS"},
                  "kwargs": {"axis_forward": "-Z", "axis_up": "Y", "apply_scale_options": "FBX_SCALE_ALL", "global_scale": 1.0,
                             "use_tspace": True, "mesh_smooth_type": "FACE", "add_leaf_bones": False, "bake_space_transform": False,
                             "use_custom_props": True},
                  "notes": ["Y-up, metres; _LOD0.._LODn naming builds a LODGroup on import"]},
        "UNREAL": {"format": "fbx", "exporter": "fbx", "axis": {"forward": "-Y", "up": "Z"},
                   "units": {"scale_length": 0.01, "length_unit": "CENTIMETERS"},
                   "kwargs": {"axis_forward": "-Y", "axis_up": "Z", "global_scale": 1.0, "apply_scale_options": "FBX_SCALE_NONE",
                              "use_tspace": True, "mesh_smooth_type": "FACE", "add_leaf_bones": False, "use_custom_props": True},
                   "notes": ["Z-up, centimetres: run set_scene_units(UNREAL) so scale_length is 0.01",
                             "UCX_<mesh>_NN collision children, SOCKET_ empties, UV1 lightmaps"]},
        "GODOT": {"format": "glb", "exporter": "gltf", "axis": {"forward": "-Z", "up": "Y"},
                  "units": {"scale_length": 1.0, "length_unit": "METERS"},
                  "kwargs": {"export_yup": True, "export_apply": True, "export_tangents": True, "export_extras": True,
                             "export_attributes": True, "export_vertex_color": "MATERIAL", "export_image_format": "AUTO"},
                  "notes": ["glTF Y-up; -col / -convcol / -colonly suffixes; colour attribute names are not preserved by glTF"]},
        "BEVY": {"format": "glb", "exporter": "gltf", "axis": {"forward": "-Z", "up": "Y"},
                 "units": {"scale_length": 1.0, "length_unit": "METERS"},
                 "kwargs": {"export_yup": True, "export_apply": True, "export_tangents": True, "export_extras": True,
                            "export_attributes": True, "export_vertex_color": "MATERIAL", "export_image_format": "AUTO"},
                 "notes": ["glTF Y-up; named nodes and extras are readable from Bevy"]},
        "TIMBERMESH": {"format": "timbermesh", "exporter": "timbermesh", "axis": None,
                       "units": {"scale_length": 1.0, "length_unit": "METERS"},
                       "kwargs": {"merge_meshes": True, "single_animation": True, "use_vertex_animations": False},
                       "notes": ["exports a collection; '#'-prefixed objects keep their own node; meshes split per material name"]},
        "GENERIC_FBX": {"format": "fbx", "exporter": "fbx", "axis": None,
                        "units": {"scale_length": 1.0, "length_unit": "METERS"}, "kwargs": {}, "notes": ["Blender FBX defaults"]},
        "GENERIC_GLTF": {"format": "glb", "exporter": "gltf", "axis": None,
                         "units": {"scale_length": 1.0, "length_unit": "METERS"}, "kwargs": {}, "notes": ["Blender glTF defaults"]},
    }
    _TEXTURE_MODES = ("COPY", "EMBED", "NONE", "KEEP_PATHS")

    def _engine_profile(self, engine):
        return engine if engine in self._PROFILES else "GENERIC"

    def _engine_naming(self, engine):
        pid = self._engine_profile(engine)
        prefix, suffixes = self._PROFILES[pid]["collision"]
        return {"collision": (prefix + "{mesh}_{nn}") if prefix else ("{mesh}" + (suffixes[0] if suffixes else "_COL")),
                "socket": (self._PROFILES[pid]["socket"] or "") + "{name}", "lod": "{name}_LOD{level}"}

    def get_export_preset(self, engine):
        """The preset table entry export_for_engine applies (single source, H6)."""
        eid = str(engine or "").strip().upper()
        entry = self._ENGINE_PRESETS.get(eid)
        if entry is None:
            return {"error": f"engine '{engine}' not valid; valid: {list(self._ENGINE_PRESETS)}", "valid": list(self._ENGINE_PRESETS)}
        return {"success": True, "engine": eid, "format": entry["format"], "exporter": entry["exporter"],
                "kwargs": dict(entry["kwargs"]), "axis": dict(entry["axis"]) if entry["axis"] else None,
                "units": dict(entry["units"]), "naming": self._engine_naming(eid), "notes": list(entry["notes"])}

    @staticmethod
    def _images_of(objs):
        """(material, image) pairs referenced by the node trees of the objects' materials."""
        seen, out = set(), []
        for o in objs:
            if o.type != 'MESH':
                continue
            for slot in o.material_slots:
                m = slot.material
                if m is None or not m.use_nodes or m.node_tree is None:
                    continue
                for node in m.node_tree.nodes:
                    img = getattr(node, "image", None)
                    if img is not None and img.name not in seen:
                        seen.add(img.name)
                        out.append((m, img))
        return out

    def export_for_engine(self, objects=None, collection=None, engine=None, filepath=None, include_hierarchy=True,
                          include_collision=True, include_sockets=True, include_lods=True, include_animation=False,
                          apply_modifiers=True, triangulate=True, validate=True, textures="COPY", overrides=None, _objs=None):
        """
        Preset export (13.2.6): validate_asset first (FAIL refuses unless overrides {"force": true}), the full
        hierarchy selected, a TEMPORARY Triangulate modifier per mesh during the export (D6: removed in finally, the
        scene mesh keeps its polygons), the preset kwargs + overrides checked against the live exporter, textures
        copied beside the file, and <file>.manifest.json written beside it (H7).
        """
        eid = str(engine or "").strip().upper()
        entry = self._ENGINE_PRESETS.get(eid)
        if entry is None:
            return {"error": f"engine '{engine}' not valid; valid: {list(self._ENGINE_PRESETS)}", "valid": list(self._ENGINE_PRESETS)}
        roots, oerr = self._names_or_collection(objects, collection, _objs=_objs)
        if oerr:
            return oerr
        if triangulate and not apply_modifiers:
            # R-B2-38: the temporary Triangulate modifier only reaches the file when the exporter evaluates modifiers
            return {"error": "triangulate=True requires apply_modifiers=True (the exporter must evaluate the temporary "
                             "Triangulate modifier); pass triangulate=False to export the polygons as they are"}
        tex_mode = str(textures or "COPY").strip().upper()
        if tex_mode not in self._TEXTURE_MODES:
            return {"error": f"textures '{textures}' not valid; valid: {list(self._TEXTURE_MODES)}", "valid": list(self._TEXTURE_MODES)}
        ov, jerr = self._json_arg(overrides, "overrides")
        if jerr:
            return jerr
        if ov is None:
            ov = {}
        if not isinstance(ov, dict):
            return {"error": f"overrides must be a JSON object, got {type(ov).__name__}"}
        force = bool(ov.pop("force", False))
        fmt = entry["format"]
        if not filepath:
            filepath = os.path.join(tempfile.gettempdir(), f"blender_export_{os.getpid()}.{fmt}")
        filepath = str(filepath)
        out_dir = os.path.dirname(os.path.abspath(filepath))
        if not os.path.isdir(out_dir):
            return {"error": f"directory does not exist: {out_dir}"}
        # the object set
        objs = []
        for r in roots:
            if r not in objs:
                objs.append(r)
            if include_hierarchy:
                for c in r.children_recursive:
                    if c not in objs:
                        objs.append(c)
        mesh_names = {o.name for o in objs if o.type == 'MESH'}

        def is_collision(o):
            return o.type == 'MESH' and (any(self._collision_profile_of(o.name, m) for m in mesh_names if m != o.name)
                                         or (o.display_type == 'WIRE' and o.hide_render))

        def is_socket(o):
            return o.type == 'EMPTY' and o.parent is not None and (o.name.startswith("SOCKET_") or o.name.startswith("#") or o.parent.type == 'MESH')

        def is_lod(o):
            mm = self._LOD_SUFFIX_RE.search(o.name)
            return o.type == 'MESH' and mm is not None and int(mm.group(0)[4:]) >= 1

        if not include_collision:
            objs = [o for o in objs if o in roots or not is_collision(o)]
        if not include_sockets:
            objs = [o for o in objs if o in roots or not is_socket(o)]
        if not include_lods:
            objs = [o for o in objs if o in roots or not is_lod(o)]
        meshes = [o for o in objs if o.type == 'MESH']
        if not meshes and fmt != "timbermesh":
            return {"error": "nothing to export: the object set holds no mesh", "objects": [o.name for o in objs]}
        warnings = []
        # validation
        validation = None
        if validate:
            # collision proxies are helpers (no UVs, no materials by design): the renderable meshes are validated
            to_validate = [m for m in meshes if not is_collision(m)] or meshes
            if meshes:
                v = self.validate_asset(_objs=to_validate, profile=self._engine_profile(eid))     # RA-4: refs, not names
                if "error" in v:
                    return {"error": f"validation failed to run: {v['error']}"}
                validation = {"verdict": v["verdict"], "fail": v["summary"]["fail"], "warn": v["summary"]["warn"]}
                if v["verdict"] == "FAIL" and not force:
                    failed = sorted({cid for o in v["objects"].values() for cid, c in o.get("checks", {}).items() if c["status"] == "FAIL"})
                    return {"error": f"validation FAIL on {len(failed)} check(s): {failed}; pass overrides with force true to export anyway",
                            "validation": validation, "checks_failed": failed}
            else:
                validation = {"verdict": "PASS", "fail": 0, "warn": 0}
        # exporter kwargs: preset + overrides, checked against the live operator
        kwargs = dict(entry["kwargs"])
        kwargs.update(ov)
        op = {"fbx": bpy.ops.export_scene.fbx, "gltf": bpy.ops.export_scene.gltf}.get(entry["exporter"])
        if op is not None:
            live_props = op.get_rna_type().properties
            for key in list(kwargs):
                prop = live_props.get(key)
                if prop is None:
                    warnings.append(f"exporter kwarg '{key}' is not available on this Blender; dropped")
                    kwargs.pop(key)
                    continue
                if prop.type == 'ENUM':
                    valid = [i.identifier for i in prop.enum_items]
                    val = str(kwargs[key]).upper()
                    if val not in valid:
                        return {"error": f"{key} '{kwargs[key]}' not valid for the {entry['exporter']} exporter on this Blender; valid: {valid}",
                                "valid": valid}
                    kwargs[key] = val
            if entry["exporter"] == "fbx":
                kwargs.setdefault("use_mesh_modifiers", bool(apply_modifiers))
                kwargs.setdefault("bake_anim", bool(include_animation))
                kwargs.setdefault("object_types", {'EMPTY', 'MESH', 'ARMATURE', 'OTHER'})
                if tex_mode == "EMBED":
                    kwargs.setdefault("path_mode", 'COPY')
                    kwargs.setdefault("embed_textures", True)
                elif tex_mode == "NONE":
                    kwargs.setdefault("path_mode", 'STRIP')
            else:
                kwargs.setdefault("export_apply", bool(apply_modifiers))
                kwargs.setdefault("export_animations", bool(include_animation))
                kwargs.setdefault("export_format", 'GLB')
                if tex_mode == "NONE":
                    kwargs.setdefault("export_image_format", 'NONE')
        if eid == "UNREAL":
            sl = float(bpy.context.scene.unit_settings.scale_length)
            if abs(sl - 0.01) > 1e-9:
                warnings.append(f"scene scale_length is {sl}, Unreal expects 0.01: run set_scene_units(UNREAL)")
        if eid in ("GODOT", "BEVY"):
            for m in meshes:
                for ca in m.data.color_attributes:
                    warnings.append(f"{m.name}: colour attribute '{ca.name}' is renamed by glTF (names are not stored, E9)")
        for m in meshes:
            if not m.data.uv_layers:
                warnings.append(f"{m.name}: no UV layer")
        # textures: COPY writes each image beside the export; a basename collision gets <parentfolder>_<basename>
        # (RA-17) and the exporter is pointed at the copies for the duration of the export
        textures_copied = []
        retarget = []
        if tex_mode == "COPY":
            used = {}
            for mat, img in self._images_of(meshes):
                if img.packed_file is not None or img.source in ('GENERATED', 'VIEWER') or not img.filepath:
                    continue
                src = os.path.abspath(bpy.path.abspath(img.filepath, library=img.library))
                if not os.path.exists(src):
                    warnings.append(f"image '{img.name}' of material '{mat.name}' not on disk: {src}")
                    continue
                base = os.path.basename(src)
                if base in used and used[base] != src:
                    base = f"{os.path.basename(os.path.dirname(src)) or 'img'}_{base}"
                used.setdefault(base, src)
                dst = os.path.join(out_dir, base)
                try:
                    if src != os.path.abspath(dst):
                        shutil.copy2(src, dst)
                    textures_copied.append(dst)
                    retarget.append((img, img.filepath, img.filepath_raw))
                    img.filepath = dst
                except Exception as e:
                    warnings.append(f"could not copy '{src}': {e}")
        # the export, with a temporary Triangulate modifier per mesh (D6)
        temp_mods = []
        timbermesh_root = None
        try:
            with self._selection_scope(), self._mode_restore():
                for m in meshes:
                    self._ensure_object_mode(m)
                if triangulate:
                    for m in meshes:
                        tm = m.modifiers.new(name="MCP_TmpTriangulate", type='TRIANGULATE')
                        tm.keep_custom_normals = True
                        temp_mods.append((m, tm.name))
                bpy.context.view_layer.update()
                if entry["exporter"] == "timbermesh":
                    timbermesh_root = self._export_timbermesh(objs, filepath, kwargs)
                    if isinstance(timbermesh_root, dict):
                        return timbermesh_root
                else:
                    for o in bpy.context.view_layer.objects:
                        o.select_set(False)
                    for o in objs:
                        if o.name in bpy.context.view_layer.objects:
                            o.select_set(True)
                    bpy.context.view_layer.objects.active = meshes[0] if meshes else objs[0]
                    if entry["exporter"] == "fbx":
                        bpy.ops.export_scene.fbx(filepath=filepath, use_selection=True, **kwargs)
                    else:
                        bpy.ops.export_scene.gltf(filepath=filepath, use_selection=True, **kwargs)
        except Exception as e:
            return {"error": f"export failed: {e}", "engine": eid, "filepath": filepath}
        finally:
            for m, name in temp_mods:
                with suppress(Exception):
                    m.modifiers.remove(m.modifiers[name])
            for img, old_fp, old_raw in retarget:          # RA-17: the scene's images point back at their originals
                with suppress(Exception):
                    img.filepath = old_fp
                    img.filepath_raw = old_raw
        if not os.path.exists(filepath):
            return {"error": f"exporter finished but {filepath} was not written", "engine": eid}
        dg = bpy.context.evaluated_depsgraph_get()
        materials = sorted({s.material.name for m in meshes for s in m.material_slots if s.material is not None})
        reply = {"success": True, "filepath": filepath, "engine": eid, "format": fmt, "exporter": entry["exporter"],
                 "size_bytes": os.path.getsize(filepath), "manifest_path": filepath + ".manifest.json",
                 "objects": [o.name for o in objs], "triangles": sum(self._eval_tri_count(m, dg) for m in meshes),
                 "materials": materials, "textures_copied": textures_copied, "validation": validation, "warnings": warnings,
                 "exporter_kwargs": {k: (sorted(v) if isinstance(v, set) else v) for k, v in kwargs.items()},
                 "timbermesh_root": timbermesh_root}
        manifest = {k: v for k, v in reply.items() if k not in ("manifest_path", "success")}
        manifest.update({"manifest_version": 1, "written_at": datetime.now().isoformat(timespec="seconds"),
                         "blender": bpy.app.version_string, "preset": eid})
        try:
            with open(reply["manifest_path"], "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=1, sort_keys=True)
        except Exception as e:
            reply["warnings"].append(f"manifest not written: {e}")
            reply["manifest_path"] = None
        return reply

    def _export_timbermesh(self, objs, filepath, kwargs):
        """Timbermesh export via the plugin's exporter class on a temporary collection (H17/E14). Returns the '#'
        root name, or an error dict. The temporary collection is removed in finally."""
        roots = [o for o in objs if o.name.startswith("#") and (o.parent is None or o.parent not in objs)]
        if not roots:
            hashed = [o.name for o in objs if o.name.startswith("#")]
            if not hashed:
                return {"error": "TIMBERMESH export needs a '#'-prefixed root object in the set (Timbermesh merges everything "
                                 "into its nearest '#' node); none found", "objects": [o.name for o in objs]}
            roots = hashed[:1]
        import addon_utils
        try:
            addon_utils.enable("timbermesh_blender_plugin", default_set=False)
            from timbermesh_blender_plugin import timbermesh_exporter as te
        except Exception as e:
            return {"error": f"the Timbermesh add-on (timbermesh_blender_plugin) could not be enabled on this Blender: {e}; "
                             f"install it from https://github.com/mechanistry/timbermesh and retry"}
        coll = bpy.data.collections.new(f"MCP_TmpTimbermesh_{os.getpid()}")
        scene = bpy.context.scene
        frame = scene.frame_current                 # F-B2-7: the plugin's single_animation export rewinds it
        try:
            scene.collection.children.link(coll)
            for o in objs:
                if o.type in ('MESH', 'EMPTY') and o.name not in coll.objects:
                    coll.objects.link(o)
            bpy.context.view_layer.update()
            settings = te.ExportSettings(bpy.context, bool(kwargs.get("merge_meshes", True)),
                                         bool(kwargs.get("single_animation", True)),
                                         bool(kwargs.get("use_vertex_animations", False)))
            te.Exporter.export_collection(coll, filepath, settings)
        except Exception as e:
            return {"error": f"Timbermesh export failed: {e}"}
        finally:
            with suppress(Exception):
                if scene.frame_current != frame:
                    scene.frame_set(frame)
            with suppress(Exception):
                bpy.data.collections.remove(coll)
        return roots[0].name if hasattr(roots[0], "name") else str(roots[0])

    # ─── Camera management ──────────────────────────────────────────────────

    def create_camera(self, name="Camera", location=None, look_at=None,
                      lens=50.0, cam_type="PERSP"):
        """
        Add a new camera to the scene.
        location: [x, y, z] (default: [0, -5, 3])
        look_at: [x, y, z] target point the camera points toward (default: origin)
        """
        import math
        if location is None:
            location = [0, -5, 3]
        if look_at is None:
            look_at = [0, 0, 0]

        cam_data = bpy.data.cameras.new(name=name)
        cam_data.lens = lens
        cam_data.type = cam_type
        cam_obj = bpy.data.objects.new(name, cam_data)
        bpy.context.scene.collection.objects.link(cam_obj)
        cam_obj.location = location

        # Point camera toward look_at
        direction = mathutils.Vector(look_at) - mathutils.Vector(location)
        rot_quat = direction.to_track_quat('-Z', 'Y')
        cam_obj.rotation_euler = rot_quat.to_euler()

        return {"success": True, "name": cam_obj.name,
                "location": list(cam_obj.location),
                "rotation_deg": [math.degrees(a) for a in cam_obj.rotation_euler]}

    def set_active_camera(self, name):
        """Set the scene's active render camera."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'CAMERA':
            return {"error": f"{name} is not a camera"}
        bpy.context.scene.camera = obj
        return {"success": True, "active_camera": name}

    # ─── Settings scopes (shared by every temporary-settings tool) ───────────

    _SCOPES = ("SCENE", "RENDER", "OUTPUT", "CYCLES", "EEVEE", "COLOR", "UNITS", "VIEWPORT",
               "PREFS_FILEPATHS", "PREFS_VIEW", "PREFS_EDIT", "PREFS_SYSTEM", "PREFS_INPUT",
               "ADDON:<module>", "MCP")
    # Properties that must be restored before the rest of their scope (dependent enums).
    _SCOPE_RESTORE_FIRST = {"OUTPUT": ("media_type",), "RENDER": ("engine",)}

    def _scope_owner(self, scope, scene=None):
        """
        Map a scope name to the RNA struct instances it covers. Returns [] when the scope
        exists but has no owner on this Blender / session (no VIEW_3D, add-on without prefs);
        raises ValueError for an unknown scope name (the caller turns it into an error reply).
        """
        scene = scene or bpy.context.scene
        name = str(scope).upper()
        if name == "SCENE":
            return [scene]
        if name == "RENDER":
            return [scene.render]
        if name == "OUTPUT":
            return [scene.render.image_settings]
        if name == "CYCLES":
            return [scene.cycles] if hasattr(scene, "cycles") else []
        if name == "EEVEE":
            return [scene.eevee] if hasattr(scene, "eevee") else []
        if name == "COLOR":
            return [scene.view_settings, scene.display_settings]
        if name == "UNITS":
            return [scene.unit_settings]
        if name == "VIEWPORT":
            wm = bpy.context.window_manager
            for win in (wm.windows if wm else []):
                for area in win.screen.areas:
                    if area.type == 'VIEW_3D':
                        space = area.spaces.active
                        return [space.shading, space.overlay]
            return []
        if name.startswith("PREFS_"):
            section = name[6:].lower()
            section = {"input": "inputs"}.get(section, section)
            prefs = bpy.context.preferences
            return [getattr(prefs, section)] if hasattr(prefs, section) else []
        if name.startswith("ADDON:"):
            entry = bpy.context.preferences.addons.get(scope[6:])
            return [entry.preferences] if entry is not None and entry.preferences is not None else []
        if name == "MCP":
            prefs = _prefs()
            return [prefs] if prefs is not None else []
        raise ValueError(f"Unknown settings scope '{scope}'; valid: {list(self._SCOPES)}")

    @staticmethod
    def _rna_simple_props(owner):
        """Identifiers of owner's writable, non-pointer, non-collection RNA properties."""
        return [p.identifier for p in owner.bl_rna.properties
                if p.identifier != "rna_type" and not p.is_readonly
                and p.type not in ('POINTER', 'COLLECTION')]

    @staticmethod
    def _rna_get(owner, ident):
        """Read a simple RNA property as plain Python (arrays -> list, enum flags -> sorted list)."""
        value = getattr(owner, ident)
        if isinstance(value, (set, frozenset)):
            return sorted(value)
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            try:
                return list(value)
            except TypeError:
                return value
        return value

    @staticmethod
    def _rna_set_validated(owner, key, value):
        """
        Validate value against owner's RNA property (existence, read-only, type, enum membership,
        numeric range) and assign it. Returns None on success, else the reason string.
        Dynamic enums whose static item list is empty (view_transform, compute_device_type
        headless) are validated by the assignment itself and the exception text is the reason.
        """
        prop = owner.bl_rna.properties.get(key)
        if prop is None:
            return f"no such property on {owner.bl_rna.identifier}"
        if prop.is_readonly:
            return "read-only"
        if prop.type in ('POINTER', 'COLLECTION'):
            return f"{prop.type.lower()} property; set it through its dedicated tool"
        is_array = bool(getattr(prop, "is_array", False)) and getattr(prop, "array_length", 0) > 0
        try:
            if prop.type == 'ENUM':
                if prop.is_enum_flag:
                    value = {value} if isinstance(value, str) else set(value)
                else:
                    # The static item list is only a hint: dynamic enums (view_transform, length_unit,
                    # compute_device_type) read ['NONE'] / ['DEFAULT'] / [] headless. Match case-
                    # insensitively when possible, otherwise let the assignment decide: Blender's own
                    # TypeError text carries the live tuple and becomes the reason.
                    value = str(value)
                    ids = [i.identifier for i in prop.enum_items]
                    if ids and value not in ids:
                        match = next((i for i in ids if i.upper() == value.upper()), None)
                        if match is not None:
                            value = match
            elif prop.type in ('INT', 'FLOAT'):
                cast = int if prop.type == 'INT' else float
                if is_array:
                    value = [cast(v) for v in value]
                    bad = [v for v in value if not (prop.hard_min <= v <= prop.hard_max)]
                    if bad:
                        return f"{bad} outside [{prop.hard_min}, {prop.hard_max}]"
                else:
                    value = cast(value)
                    if not (prop.hard_min <= value <= prop.hard_max):
                        return f"{value} outside [{prop.hard_min}, {prop.hard_max}]"
            elif prop.type == 'BOOLEAN':
                value = [bool(v) for v in value] if is_array else bool(value)
            elif prop.type == 'STRING':
                value = str(value)
            setattr(owner, key, value)
        except Exception as e:
            return str(e)
        return None

    def _apply_settings(self, scope, values, scene=None):
        """
        Set several properties in one scope with per-key validation. Returns
        {"set": [keys], "unset": {key: reason}} in the add_modifier shape. Raises ValueError for
        an unknown scope (from _scope_owner); a scope with no owner marks every key unset.
        """
        owners = self._scope_owner(scope, scene)
        name = str(scope).upper()
        done, unset = [], {}
        for key, value in dict(values or {}).items():
            if not owners:
                unset[key] = f"scope {scope} has no owner in this session"
                continue
            # Two enums whose static item list is incomplete go through the live-tuple setters
            if name == "RENDER" and key == "engine":
                err = self._set_render_engine(owners[0], value)
            elif name == "OUTPUT" and key == "file_format":
                err = self._set_file_format(owners[0], value)
            elif name == "OUTPUT" and key == "ffmpeg":
                # Nested video settings (presets carry {"ffmpeg": {format, codec, constant_rate_factor}})
                sub, jerr = self._json_arg(value, "ffmpeg")
                if jerr or not isinstance(sub, dict):
                    unset[key] = "ffmpeg must be an object {format, codec, constant_rate_factor, ...}"
                    continue
                ff = (scene or bpy.context.scene).render.ffmpeg
                for sk, sv in sub.items():
                    reason = self._rna_set_validated(ff, sk, sv)
                    if reason is None:
                        done.append(f"ffmpeg.{sk}")
                    else:
                        unset[f"ffmpeg.{sk}"] = reason
                continue
            else:
                err = "not special"
            if err != "not special":
                if err is None:
                    done.append(key)
                else:
                    unset[key] = err["error"]
                continue
            reason = None
            for owner in owners:
                if owner.bl_rna.properties.get(key) is None:
                    continue
                reason = self._rna_set_validated(owner, key, value)
                break
            else:
                reason = f"no such property in scope {scope}"
            if reason is None:
                done.append(key)
            else:
                unset[key] = reason
        return {"set": done, "unset": unset}

    @contextmanager
    def _settings_scope(self, scopes=("RENDER", "OUTPUT", "CYCLES", "EEVEE"), scene=None):
        """
        Snapshot every simple property of the named scopes (plus scene.camera and
        scene.frame_current) on enter and restore whatever changed in finally, whatever
        happens inside. Yields a list that collects {"scope", "key", "error"} entries for
        values that could not be restored (dynamic enums that read empty headless, properties
        the engine refuses); callers may report it, nothing raises. Restore order: media_type
        before file_format, engine before the engine-specific properties.
        """
        scene = scene or bpy.context.scene
        snapshot = []
        for scope in scopes:
            for owner in self._scope_owner(scope, scene):
                values = {}
                for ident in self._rna_simple_props(owner):
                    try:
                        values[ident] = self._rna_get(owner, ident)
                    except Exception:
                        pass
                snapshot.append((str(scope).upper(), owner, values))
        camera, frame = scene.camera, scene.frame_current
        failed = []
        try:
            yield failed
        finally:
            for scope, owner, values in snapshot:
                first = self._SCOPE_RESTORE_FIRST.get(scope, ())
                order = [k for k in first if k in values] + [k for k in values if k not in first]
                for ident in order:
                    saved = values[ident]
                    try:
                        if self._rna_get(owner, ident) == saved:
                            continue
                        prop = owner.bl_rna.properties[ident]
                        if prop.type == 'ENUM' and prop.is_enum_flag:
                            saved = set(saved)
                        setattr(owner, ident, saved)
                    except Exception as e:
                        failed.append({"scope": scope, "key": ident, "error": str(e)})
            with suppress(Exception):
                if scene.camera != camera:
                    scene.camera = camera
            with suppress(Exception):
                if scene.frame_current != frame:
                    scene.frame_set(frame)

    @contextmanager
    def _render_settings(self, scene, width, height, samples, file_format='PNG'):
        """Temporarily apply render settings; _settings_scope restores everything afterwards."""
        with self._settings_scope(("RENDER", "OUTPUT", "CYCLES", "EEVEE"), scene=scene):
            r = scene.render
            r.resolution_x = int(width)
            r.resolution_y = int(height)
            r.resolution_percentage = 100
            err = self._set_file_format(r.image_settings, file_format)
            if err:
                raise ValueError(err["error"])
            cyc = getattr(scene, "cycles", None)
            eev = getattr(scene, "eevee", None)
            if cyc:
                cyc.samples = int(samples)
            if eev and hasattr(eev, "taa_render_samples"):
                eev.taa_render_samples = int(samples)
            yield

    @staticmethod
    def _render_to_file(scene, filepath):
        """Render a still to filepath; raise if Blender produced nothing."""
        if os.path.exists(filepath):
            os.remove(filepath)
        scene.render.filepath = filepath
        result = bpy.ops.render.render(write_still=True)
        if 'FINISHED' not in result:
            raise RuntimeError(f"render operator returned {set(result)}")
        if not os.path.exists(filepath):
            raise RuntimeError("render finished but no file was written")

    def render_from_camera(self, camera_name=None, filepath=None,
                           width=1920, height=1080, samples=32):
        """
        Render a still from the specified (or current active) camera and save it.
        """
        if not filepath:
            filepath = os.path.join(tempfile.gettempdir(),
                                    f"blender_render_{os.getpid()}.png")

        scene = bpy.context.scene
        cam_obj = scene.camera
        if camera_name:
            cam_obj = bpy.data.objects.get(camera_name)
            if not cam_obj:
                return {"error": f"Camera not found: {camera_name}"}
            if cam_obj.type != 'CAMERA':
                return {"error": f"{camera_name} is not a camera"}
        if cam_obj is None:
            return {"error": "Scene has no active camera. Create one with create_camera / set_active_camera."}

        try:
            with self._render_settings(scene, width, height, samples):
                scene.camera = cam_obj
                self._render_to_file(scene, filepath)
        except Exception as e:
            return {"error": f"Render failed: {e}"}

        return {"success": True, "filepath": filepath, "camera": cam_obj.name,
                "width": width, "height": height}

    def render_all_cameras(self, width=1920, height=1080, samples=32,
                           output_dir=None, file_format="PNG"):
        """
        Render a still from every camera object in the scene.
        Returns a list of {camera, filepath, success[, error]} dicts.
        """
        cameras = [obj for obj in bpy.context.scene.objects if obj.type == 'CAMERA']
        if not cameras:
            return {"error": "No camera objects found in the scene"}

        if output_dir is None:
            output_dir = tempfile.gettempdir()
        elif not os.path.isdir(output_dir):
            return {"error": f"output_dir does not exist: {output_dir}"}

        scene = bpy.context.scene
        results = []
        with self._render_settings(scene, width, height, samples, file_format):
            for cam in cameras:
                safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in cam.name)
                filepath = os.path.join(output_dir, f"render_{safe_name}_{os.getpid()}.png")
                entry = {"camera": cam.name, "filepath": filepath, "success": False}
                try:
                    scene.camera = cam
                    self._render_to_file(scene, filepath)
                    entry["success"] = True
                except Exception as e:
                    entry["error"] = str(e)
                results.append(entry)

        succeeded = [r for r in results if r["success"]]
        return {
            "success":       True,
            "total_cameras": len(cameras),
            "rendered":      len(succeeded),
            "renders":       results,
        }

    # ─── Scene analysis ─────────────────────────────────────────────────────

    def find_objects_by_type(self, obj_type="MESH"):
        """Return names and locations of all objects matching obj_type."""
        obj_type = obj_type.upper()
        results = []
        for obj in bpy.context.scene.objects:
            if obj.type == obj_type:
                results.append({
                    "name": obj.name,
                    "uid": obj.session_uid,
                    "location": [round(float(v), 4) for v in obj.location],
                    "visible": obj.visible_get(),
                })
        return {"type": obj_type, "count": len(results), "objects": results}

    def measure_distance(self, name_a, name_b):
        """Return the Euclidean distance between the origins of two objects."""
        a = bpy.data.objects.get(name_a)
        b = bpy.data.objects.get(name_b)
        if not a:
            return {"error": f"Object not found: {name_a}"}
        if not b:
            return {"error": f"Object not found: {name_b}"}
        dist = (a.location - b.location).length
        return {"distance": round(dist, 6), "from": name_a, "to": name_b,
                "loc_a": list(a.location), "loc_b": list(b.location)}

    # ─── Lighting ───────────────────────────────────────────────────────────

    def add_light(self, light_type="POINT", name=None, location=None,
                  energy=1000.0, color=None, radius=0.1):
        """
        Add a light to the scene.
        light_type: POINT, SUN, SPOT, AREA
        """
        if location is None:
            location = [0, 0, 5]
        if color is None:
            color = [1.0, 1.0, 1.0]
        if name is None:
            name = light_type.capitalize() + "Light"

        light_data = bpy.data.lights.new(name=name, type=light_type)
        light_data.energy = energy
        light_data.color = color[:3]
        if hasattr(light_data, 'shadow_soft_size'):
            light_data.shadow_soft_size = radius

        light_obj = bpy.data.objects.new(name, light_data)
        bpy.context.scene.collection.objects.link(light_obj)
        light_obj.location = location

        return {"success": True, "name": light_obj.name, "type": light_type,
                "location": location, "energy": energy}

    def set_world_background(self, color=None, strength=1.0, hdri_path=None):
        """
        Set the world background to a solid colour or an HDRI.
        color: [r, g, b] for solid colour
        hdri_path: local file path to .hdr / .exr
        """
        world = bpy.context.scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            bpy.context.scene.world = world
        world.use_nodes = True
        tree = world.node_tree
        tree.nodes.clear()

        bg = tree.nodes.new("ShaderNodeBackground")
        out = tree.nodes.new("ShaderNodeOutputWorld")
        tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
        bg.inputs["Strength"].default_value = strength

        if hdri_path:
            if not os.path.exists(hdri_path):
                return {"error": f"HDRI file not found: {hdri_path}"}
            env_tex = tree.nodes.new("ShaderNodeTexEnvironment")
            env_tex.image = bpy.data.images.load(hdri_path)
            mapping = tree.nodes.new("ShaderNodeMapping")
            tex_coord = tree.nodes.new("ShaderNodeTexCoord")
            tree.links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])
            tree.links.new(mapping.outputs["Vector"], env_tex.inputs["Vector"])
            tree.links.new(env_tex.outputs["Color"], bg.inputs["Color"])
            return {"success": True, "mode": "hdri", "path": hdri_path}
        else:
            if color is None:
                color = [0.05, 0.05, 0.05]
            bg.inputs["Color"].default_value = (*color[:3], 1.0)
            return {"success": True, "mode": "color", "color": color}

    def add_3point_lighting(self, subject_name=None, key_energy=1500.0,
                            fill_energy=500.0, back_energy=800.0):
        """
        Add a classic 3-point lighting rig around the subject (or scene origin).
        Returns names of created lights.
        """
        if subject_name:
            obj = bpy.data.objects.get(subject_name)
            center = list(obj.location) if obj else [0, 0, 0]
        else:
            center = [0, 0, 0]

        cx, cy, cz = center
        key_loc   = [cx - 3,  cy - 3,  cz + 4]
        fill_loc  = [cx + 3,  cy - 2,  cz + 2]
        back_loc  = [cx,      cy + 4,  cz + 3]

        key  = self.add_light("AREA",  "Key_Light",  key_loc,  key_energy,  [1.0, 0.95, 0.9])
        fill = self.add_light("AREA",  "Fill_Light", fill_loc, fill_energy, [0.9, 0.95, 1.0])
        back = self.add_light("POINT", "Back_Light", back_loc, back_energy, [1.0, 1.0, 0.95])

        return {"success": True, "lights": [key["name"], fill["name"], back["name"]]}

    # ─── Export / import ────────────────────────────────────────────────────

    _FBX_EXPORT_PARAMS = ("bake_anim", "add_leaf_bones", "use_armature_deform_only", "bake_anim_simplify_factor",
                          "mesh_smooth_type", "primary_bone_axis", "secondary_bone_axis", "apply_scale_options")
    _GLTF_EXPORT_PARAMS = ("export_animations", "export_skins", "export_morph")

    @staticmethod
    def _op_enum_ids(op, prop):
        try:
            return [e.identifier for e in op.get_rna_type().properties[prop].enum_items]
        except Exception:
            return []

    @staticmethod
    def _op_kwarg_ids(op):
        """The kwargs this operator accepts on the running Blender (live RNA, never a version compare)."""
        try:
            return {p.identifier for p in op.get_rna_type().properties}
        except Exception:
            return set()

    # ─── Engine readiness helpers (B2) ───────────────────────────────────────

    @staticmethod
    def _eval_tri_count(obj, depsgraph=None):
        """
        Triangle count of obj with modifiers evaluated (E13: evaluated_get + calc_loop_triangles),
        0 for anything that does not evaluate to a mesh. Pass the depsgraph when counting many objects.
        """
        if obj is None or obj.type != 'MESH':
            return 0
        try:
            dg = depsgraph or bpy.context.evaluated_depsgraph_get()
            me = obj.evaluated_get(dg).data
            me.calc_loop_triangles()
            return len(me.loop_triangles)
        except Exception:
            return 0

    @staticmethod
    def _rotation_is_identity(obj, tol=1e-6):
        """True when the object carries no rotation, keyed on rotation_mode (RA-5): Euler modes read
        rotation_euler, QUATERNION reads w, AXIS_ANGLE reads the angle."""
        mode = obj.rotation_mode
        if mode == 'QUATERNION':
            return abs(abs(obj.rotation_quaternion.w) - 1.0) < tol
        if mode == 'AXIS_ANGLE':
            return abs(obj.rotation_axis_angle[0]) < tol
        return all(abs(a) < tol for a in obj.rotation_euler)

    @staticmethod
    def _sync_editmesh(obj):
        """
        A MESH object in EDIT mode (also as a co-edited object of a multi-object edit session) keeps its
        live geometry in the edit-mesh; obj.data is stale and its custom-data layers can be empty
        (uv.data of length 0 -> foreach_get length mismatch). update_from_editmode() flushes it into
        obj.data without leaving EDIT mode, so R-B2-36 reads stay mode-neutral. Returns True when flushed.
        """
        if obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT':
            with suppress(Exception):
                return bool(obj.update_from_editmode())
        return False

    @contextmanager
    def _bmesh_read(self, obj):
        """
        Read-only BMesh of obj.data with lookup tables (R-B2-36): bmesh.new() + from_mesh, no mode or active
        change, never written back, always freed. An object in EDIT mode is flushed first (_sync_editmesh).
        """
        self._sync_editmesh(obj)
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            yield bm
        finally:
            bm.free()

    @staticmethod
    def _eval_vert_count(obj, depsgraph=None):
        if obj is None or obj.type != 'MESH':
            return 0
        try:
            dg = depsgraph or bpy.context.evaluated_depsgraph_get()
            return len(obj.evaluated_get(dg).data.vertices)
        except Exception:
            return 0

    def _names_or_collection(self, objects=None, collection=None, label="objects", _objs=None):
        """
        Resolve the B2 `objects` (comma list with fnmatch globs, H11) / `collection` pair to a list of
        Objects: (objects, None) or (None, error dict). Nothing is applied when a name is unknown or a
        glob matches nothing ({error, unknown, valid_count}); both or neither given -> {error}.
        _objs: internal callers pass bpy Object references here (RA-4) so names with , * ? [ never
        meet the wire parser; anything that is not an Object is refused.
        """
        if _objs is not None:
            refs = list(_objs) if isinstance(_objs, (list, tuple)) else [_objs]
            bad = [r for r in refs if not isinstance(r, bpy.types.Object)]
            if bad or not refs:
                return None, {"error": f"_objs is internal: it takes Blender Object references only ({len(bad)} refused)"}
            return refs, None
        if objects is None and collection is None:
            return None, {"error": f"give {label} (comma list, globs allowed) or collection"}
        if objects is not None and collection is not None:
            return None, {"error": f"give either {label} or collection, not both"}
        if collection is not None:
            coll = bpy.data.collections.get(str(collection))
            if coll is None:
                return None, {"error": f"collection '{collection}' not found; collections: {[c.name for c in bpy.data.collections][:30]}"}
            found = list(coll.all_objects)
            if not found:
                return None, {"error": f"collection '{collection}' holds no objects", "unknown": [], "valid_count": 0}
            return found, None
        if isinstance(objects, str) and objects.strip() in bpy.data.objects:
            patterns = [objects.strip()]        # R-B2-41: a whole string that IS a name ("Wall, left") is never split
        else:
            patterns = self._names_arg(objects)
        if not patterns:
            return None, {"error": f"{label} is empty"}
        names = [o.name for o in bpy.data.objects]
        matched, unmatched = self._glob_names(patterns, names)
        if unmatched:
            return None, {"error": f"{label} not found: {unmatched}", "unknown": unmatched, "valid_count": len(matched)}
        return [bpy.data.objects[n] for n in matched], None

    def export_object(self, name=None, filepath=None, file_format="glb", include_hierarchy=True,
                      bake_anim=True, add_leaf_bones=False, use_armature_deform_only=True,
                      bake_anim_simplify_factor=0.0, mesh_smooth_type=None, primary_bone_axis=None,
                      secondary_bone_axis=None, apply_scale_options=None,
                      export_animations=True, export_skins=True, export_morph=True,
                      objects=None, apply_modifiers=True, axis_forward=None, axis_up=None, global_scale=None,
                      use_tspace=None, use_triangles=None, use_custom_props=None, colors_type=None,
                      export_apply=None, export_tangents=None, export_extras=None, export_yup=None,
                      export_attributes=None, export_vertex_color=None, export_animation_mode=None):
        """
        Export an object (or the entire scene if name and objects are None).
        file_format: glb, gltf, fbx, obj, stl, ply
        name / objects: one object, or a comma list (fnmatch globs) as the alternative; not both.
        include_hierarchy: also select the parent armature (if any) and every child, so a
                           skinned mesh exports rigged (selection restored afterwards).
        FBX only: bake_anim, add_leaf_bones (default False: no *_end bones), use_armature_deform_only,
                  bake_anim_simplify_factor, mesh_smooth_type (live enum: OFF/FACE/EDGE, +SMOOTH_GROUP
                  on 5.2), primary_bone_axis / secondary_bone_axis (X/Y/Z/-X/-Y/-Z), apply_scale_options.
        glTF only: export_animations, export_skins, export_morph.
        B2 engine params (13.2.2; None = exporter default): apply_modifiers (FBX use_mesh_modifiers /
        glTF export_apply / OBJ-STL-PLY apply_modifiers), axis_forward / axis_up / global_scale
        (FBX, OBJ, STL, PLY), use_tspace / use_triangles / use_custom_props / colors_type (FBX; OBJ and
        PLY take use_triangles and colors as their own kwargs), export_apply / export_tangents /
        export_extras / export_yup / export_attributes / export_vertex_color / export_animation_mode (glTF).
        Enum values are checked against the live operator enum; a param the chosen exporter does not
        take is reported under "ignored". Reply adds size_bytes, object_count, vertices_written,
        triangles_written (evaluated, E13), warnings and, for OBJ, transform_baked (E10).
        """
        file_format = (file_format or "glb").lower().lstrip(".")
        supported = ("glb", "gltf", "fbx", "obj", "stl", "ply")
        if file_format not in supported:
            return {"error": f"Unsupported format: {file_format}. Choose from: {list(supported)}"}
        if not filepath:
            filepath = os.path.join(tempfile.gettempdir(),
                                    f"blender_export_{os.getpid()}.{file_format}")
        if name is not None and objects is not None:
            return {"error": "give either name or objects, not both"}

        # Select only the target object(s) (and their rig hierarchy) if specified
        targets = []
        if name:
            obj = bpy.data.objects.get(name)
            if not obj:
                return {"error": f"Object not found: {name}"}
            targets = [obj]
        elif objects is not None:
            targets, oerr = self._names_or_collection(objects=objects)
            if oerr:
                return oerr
        selected = bool(targets)

        fbx_given = {k: v for k, v in {"bake_anim": bake_anim, "add_leaf_bones": add_leaf_bones,
                                       "use_armature_deform_only": use_armature_deform_only,
                                       "bake_anim_simplify_factor": bake_anim_simplify_factor,
                                       "mesh_smooth_type": mesh_smooth_type, "primary_bone_axis": primary_bone_axis,
                                       "secondary_bone_axis": secondary_bone_axis,
                                       "apply_scale_options": apply_scale_options}.items() if v is not None}
        gltf_given = {"export_animations": bool(export_animations), "export_skins": bool(export_skins),
                      "export_morph": bool(export_morph)}
        ignored = []
        fbx_kwargs, gltf_kwargs = {}, {}
        if file_format == "fbx":
            for key in ("mesh_smooth_type", "primary_bone_axis", "secondary_bone_axis", "apply_scale_options"):
                if key in fbx_given:
                    valid = self._op_enum_ids(bpy.ops.export_scene.fbx, key)
                    value = str(fbx_given[key]).upper()
                    if valid and value not in valid:
                        return {"error": f"{key} '{fbx_given[key]}' not valid on this Blender; valid: {valid}"}
                    fbx_given[key] = value
            fbx_kwargs = dict(fbx_given)
            fbx_kwargs["bake_anim"] = bool(fbx_kwargs.get("bake_anim", True))
            fbx_kwargs["add_leaf_bones"] = bool(fbx_kwargs.get("add_leaf_bones", False))
            fbx_kwargs["use_armature_deform_only"] = bool(fbx_kwargs.get("use_armature_deform_only", True))
            fbx_kwargs["bake_anim_simplify_factor"] = float(fbx_kwargs.get("bake_anim_simplify_factor", 0.0))
            ignored = [k for k in self._GLTF_EXPORT_PARAMS if gltf_given[k] is not True]
        elif file_format in ("glb", "gltf"):
            gltf_kwargs = dict(gltf_given)
            defaults = {"bake_anim": True, "add_leaf_bones": False, "use_armature_deform_only": True,
                        "bake_anim_simplify_factor": 0.0}
            ignored = [k for k, v in fbx_given.items() if defaults.get(k, None) != v]
        else:
            defaults = {"bake_anim": True, "add_leaf_bones": False, "use_armature_deform_only": True,
                        "bake_anim_simplify_factor": 0.0}
            ignored = [k for k, v in fbx_given.items() if defaults.get(k, None) != v] + \
                      [k for k in self._GLTF_EXPORT_PARAMS if gltf_given[k] is not True]

        # B2 engine params: map each to the chosen exporter's kwarg name, check enums against the live
        # operator, report the rest under "ignored" (live RNA decides what exists, never the version)
        op = {"fbx": bpy.ops.export_scene.fbx, "glb": bpy.ops.export_scene.gltf, "gltf": bpy.ops.export_scene.gltf,
              "obj": bpy.ops.wm.obj_export if self._op_exists(bpy.ops.wm.obj_export) else bpy.ops.export_scene.obj,
              "stl": bpy.ops.wm.stl_export if self._op_exists(bpy.ops.wm.stl_export) else bpy.ops.export_mesh.stl,
              "ply": bpy.ops.wm.ply_export if self._op_exists(bpy.ops.wm.ply_export) else bpy.ops.export_mesh.ply}[file_format]
        live = self._op_kwarg_ids(op)
        b2_given = {k: v for k, v in {
            "axis_forward": axis_forward, "axis_up": axis_up, "global_scale": global_scale, "use_tspace": use_tspace,
            "use_triangles": use_triangles, "use_custom_props": use_custom_props, "colors_type": colors_type,
            "export_apply": export_apply, "export_tangents": export_tangents, "export_extras": export_extras,
            "export_yup": export_yup, "export_attributes": export_attributes, "export_vertex_color": export_vertex_color,
            "export_animation_mode": export_animation_mode}.items() if v is not None}
        # param -> candidate kwarg names per exporter family (first one the live operator has wins)
        aliases = {"axis_forward": ("axis_forward", "forward_axis"), "axis_up": ("axis_up", "up_axis"),
                   "use_triangles": ("use_triangles", "export_triangulated_mesh"),
                   "colors_type": ("colors_type",)}
        enum_params = {"axis_forward", "axis_up", "colors_type", "export_vertex_color", "export_animation_mode"}
        extra_kwargs = {}
        for key, value in b2_given.items():
            target = next((a for a in aliases.get(key, (key,)) if a in live), None)
            if target is None:
                ignored.append(key)
                continue
            if key in enum_params:
                valid = self._op_enum_ids(op, target)
                value = str(value).upper() if key != "axis_forward" and key != "axis_up" else str(value).upper().replace("NEG_", "-")
                if valid and value not in valid:
                    return {"error": f"{key} '{b2_given[key]}' not valid for {file_format} on this Blender; valid: {valid}"}
            elif key == "global_scale":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    return {"error": f"global_scale must be a number, got {value!r}"}
            else:
                value = bool(value)
            extra_kwargs[target] = value
        # apply_modifiers: one caller flag, three exporter spellings; glTF export_apply explicit wins
        mod_key = next((a for a in ("use_mesh_modifiers", "apply_modifiers") if a in live), None)
        if mod_key is not None:
            extra_kwargs[mod_key] = bool(apply_modifiers)
        elif "export_apply" in live and "export_apply" not in extra_kwargs:
            extra_kwargs["export_apply"] = bool(apply_modifiers)
        if file_format == "fbx":
            fbx_kwargs.update(extra_kwargs)
        elif file_format in ("glb", "gltf"):
            gltf_kwargs.update(extra_kwargs)

        exported = []
        written = []
        # Blender 4.x: OBJ/STL/PLY moved from the Python add-ons to wm.* C operators
        try:
            with self._selection_scope():
                if targets:
                    self._select_only(targets[0])
                    for obj in targets:
                        if obj.name in bpy.context.view_layer.objects:
                            obj.select_set(True)
                        if include_hierarchy:
                            extra = []
                            if obj.parent is not None and obj.parent.type == 'ARMATURE':
                                extra.append(obj.parent)
                                extra += list(obj.parent.children_recursive)
                            arm_mod = next((m for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)
                            if arm_mod is not None:
                                extra.append(arm_mod.object)
                            extra += list(obj.children_recursive)
                            for o in extra:
                                if o.name in bpy.context.view_layer.objects:
                                    o.select_set(True)
                    exported = sorted(o.name for o in bpy.context.view_layer.objects if o.select_get())
                    written = [bpy.data.objects[n] for n in exported]
                else:
                    written = list(bpy.context.scene.objects)
                if file_format in ("glb", "gltf"):
                    bpy.ops.export_scene.gltf(
                        filepath=filepath,
                        export_format="GLB" if file_format == "glb" else "GLTF_SEPARATE",
                        use_selection=selected, **gltf_kwargs,
                    )
                elif file_format == "fbx":
                    bpy.ops.export_scene.fbx(filepath=filepath, use_selection=selected, **fbx_kwargs)
                elif file_format == "obj":
                    if self._op_exists(bpy.ops.wm.obj_export):
                        bpy.ops.wm.obj_export(filepath=filepath, export_selected_objects=selected, **extra_kwargs)
                    else:
                        bpy.ops.export_scene.obj(filepath=filepath, use_selection=selected)
                elif file_format == "stl":
                    if self._op_exists(bpy.ops.wm.stl_export):
                        bpy.ops.wm.stl_export(filepath=filepath, export_selected_objects=selected, **extra_kwargs)
                    else:
                        bpy.ops.export_mesh.stl(filepath=filepath, use_selection=selected)
                elif file_format == "ply":
                    if self._op_exists(bpy.ops.wm.ply_export):
                        bpy.ops.wm.ply_export(filepath=filepath, export_selected_objects=selected, **extra_kwargs)
                    else:
                        bpy.ops.export_mesh.ply(filepath=filepath, use_selection=selected)
        except Exception as e:
            return {"error": f"Export failed: {e}"}

        if not os.path.exists(filepath) and file_format != "gltf":
            return {"error": f"Exporter finished but {filepath} was not written"}
        # B2 counts + warnings over the objects that went into the file: evaluated (E13) when the exporter
        # applied modifiers, the base mesh otherwise (what the file actually holds)
        dg = bpy.context.evaluated_depsgraph_get()
        meshes = [o for o in written if o.type == 'MESH']
        applied_mods = bool(extra_kwargs.get(mod_key, extra_kwargs.get("export_apply", apply_modifiers))) if mod_key or "export_apply" in live \
            else bool(apply_modifiers)
        if not applied_mods:
            dg = None
            self_eval_tris = lambda o, _dg: sum(len(p.vertices) - 2 for p in o.data.polygons)
            self_eval_verts = lambda o, _dg: len(o.data.vertices)
        else:
            self_eval_tris, self_eval_verts = self._eval_tri_count, self._eval_vert_count
        warnings = []
        for o in meshes:
            if any(abs(s - 1.0) > 1e-6 for s in o.scale):
                warnings.append(f"{o.name}: unapplied scale {[round(float(s), 4) for s in o.scale]}")
            if not o.data.uv_layers:
                warnings.append(f"{o.name}: no UV layer")
            if file_format == "fbx" and not fbx_kwargs.get("use_triangles", False) \
                    and any(len(p.vertices) > 4 for p in o.data.polygons):
                warnings.append(f"{o.name}: ngons exported as-is (use_triangles is off)")
            for slot in o.material_slots:
                if slot.material is not None and not slot.material.use_nodes:
                    warnings.append(f"{o.name}: material '{slot.material.name}' has no nodes")
        reply = {"success": True, "filepath": filepath, "format": file_format,
                 "exported_objects": exported if selected else "all",
                 "include_hierarchy": bool(include_hierarchy) if selected else None,
                 "size_bytes": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                 "object_count": len(written),
                 "vertices_written": sum(self_eval_verts(o, dg) for o in meshes),
                 "triangles_written": sum(self_eval_tris(o, dg) for o in meshes),
                 "modifiers_applied": applied_mods,
                 "warnings": warnings}
        # E10: wm.obj_export bakes the object transform into the vertices: always on 4.x (no apply_transform
        # kwarg), and by default on 5.x (the kwarg is not exposed here). Other exporters keep transforms as nodes.
        reply["transform_baked"] = file_format == "obj" and bool(extra_kwargs.get("apply_transform", True))
        if fbx_kwargs:
            reply["fbx_options"] = fbx_kwargs
        if gltf_kwargs:
            reply["gltf_options"] = gltf_kwargs
        if extra_kwargs and file_format in ("obj", "stl", "ply"):
            reply["export_options"] = extra_kwargs
        if ignored:
            reply["ignored"] = ignored
        return reply

    def import_file(self, filepath, axis_forward=None, axis_up=None, global_scale=None, use_custom_normals=True,
                    use_image_search=None, ignore_leaf_bones=None, import_shading=None, merge_vertices=None):
        """
        Import a 3D file. Supports: glb/gltf, fbx, obj, stl, ply, blend, bvh (needs the
        io_anim_bvh add-on, enabled in factory preferences).
        B2 (13.2.2): axis_forward / axis_up / global_scale (FBX, OBJ, STL, PLY), use_custom_normals /
        use_image_search / ignore_leaf_bones (FBX), import_shading / merge_vertices (glTF); every kwarg
        is passed only when the live importer has it (E8/E9), the rest are listed under "ignored".
        Reply: imported_objects (user geometry), objects (per object: name, type, vertices, triangles,
        has_custom_normals, uv_layers, materials), armatures, actions (all), new_actions, and
        bone_shape_objects (the glTF importer's bone custom-shape meshes, e.g. "Icosphere",
        which are not user geometry).
        """
        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}

        ext = os.path.splitext(filepath)[1].lower()
        supported = (".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply", ".blend", ".bvh")
        if ext not in supported:
            return {"error": f"Unsupported file extension: {ext}. Choose from: {list(supported)}"}
        if ext == ".bvh" and not self._addon_enabled("io_anim_bvh"):
            return {"error": "BVH import needs the io_anim_bvh add-on; enable it with enable_addon('io_anim_bvh')"}
        before = set(bpy.data.objects.keys())
        actions_before = set(bpy.data.actions.keys())

        # B2 kwargs, mapped per importer against the live operator (never a version compare)
        op = None
        if ext in (".glb", ".gltf"):
            op = bpy.ops.import_scene.gltf
        elif ext == ".fbx":
            op = bpy.ops.import_scene.fbx
        elif ext == ".obj":
            op = bpy.ops.wm.obj_import if self._op_exists(bpy.ops.wm.obj_import) else bpy.ops.import_scene.obj
        elif ext == ".stl":
            op = bpy.ops.wm.stl_import if self._op_exists(bpy.ops.wm.stl_import) else bpy.ops.import_mesh.stl
        elif ext == ".ply":
            op = bpy.ops.wm.ply_import if self._op_exists(bpy.ops.wm.ply_import) else bpy.ops.import_mesh.ply
        live = self._op_kwarg_ids(op) if op is not None else set()
        given = {k: v for k, v in {"axis_forward": axis_forward, "axis_up": axis_up, "global_scale": global_scale,
                                   "use_custom_normals": use_custom_normals, "use_image_search": use_image_search,
                                   "ignore_leaf_bones": ignore_leaf_bones, "import_shading": import_shading,
                                   "merge_vertices": merge_vertices}.items() if v is not None}
        aliases = {"axis_forward": ("axis_forward", "forward_axis"), "axis_up": ("axis_up", "up_axis")}
        kwargs, ignored = {}, []
        for key, value in given.items():
            target = next((a for a in aliases.get(key, (key,)) if a in live), None)
            if target is None:
                if not (key == "use_custom_normals" and use_custom_normals is True):   # the default, silently
                    ignored.append(key)
                continue
            if key in ("axis_forward", "axis_up", "import_shading"):
                value = str(value).upper()
                valid = self._op_enum_ids(op, target)
                if valid and value not in valid:
                    return {"error": f"{key} '{given[key]}' not valid for {ext} on this Blender; valid: {valid}"}
            elif key == "global_scale":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    return {"error": f"global_scale must be a number, got {value!r}"}
            else:
                value = bool(value)
            kwargs[target] = value

        try:
            if ext in (".glb", ".gltf"):
                # K8: the importer's bone custom-shape mesh (Icosphere) is suppressed at the source
                # on both versions; the kwarg is try-passed so an importer without it still works
                try:
                    bpy.ops.import_scene.gltf(filepath=filepath, disable_bone_shape=True, **kwargs)
                except TypeError:
                    bpy.ops.import_scene.gltf(filepath=filepath, **kwargs)
            elif ext == ".fbx":
                bpy.ops.import_scene.fbx(filepath=filepath, **kwargs)
            elif ext == ".bvh":
                bpy.ops.import_anim.bvh(filepath=filepath)
            elif ext in (".obj", ".stl", ".ply"):
                op(filepath=filepath, **kwargs)
            elif ext == ".blend":
                # Append every object from the file (dependencies come along)
                with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
                    data_to.objects = list(data_from.objects)
                target = bpy.context.collection or bpy.context.scene.collection
                for o in data_to.objects:
                    if o is not None:
                        target.objects.link(o)
        except Exception as e:
            return {"error": f"Import failed: {e}"}

        after = set(bpy.data.objects.keys())
        new_names = sorted(after - before)
        new_objs = [bpy.data.objects[n] for n in new_names]
        armatures = [o.name for o in new_objs if o.type == 'ARMATURE']
        # Bone custom-shape meshes the importer adds (glTF: "Icosphere") are not user geometry
        shapes = set()
        for o in new_objs:
            if o.type == 'ARMATURE':
                for pb in o.pose.bones:
                    if pb.custom_shape is not None:
                        shapes.add(pb.custom_shape.name)
        bone_shapes = [o.name for o in new_objs
                       if o.name in shapes or (o.type == 'MESH' and not o.users_collection)]
        imported = [n for n in new_names if n not in bone_shapes]
        new_actions = sorted(set(bpy.data.actions.keys()) - actions_before)
        dg = bpy.context.evaluated_depsgraph_get()
        per_object = []
        for n in imported:
            o = bpy.data.objects[n]
            entry = {"name": o.name, "type": o.type, "vertices": 0, "triangles": 0, "has_custom_normals": False,
                     "uv_layers": [], "materials": [s.material.name for s in o.material_slots if s.material]}
            if o.type == 'MESH':
                entry.update({"vertices": len(o.data.vertices), "triangles": self._eval_tri_count(o, dg),
                              "has_custom_normals": bool(o.data.has_custom_normals),
                              "uv_layers": [uv.name for uv in o.data.uv_layers]})
            per_object.append(entry)
        reply = {"success": True, "filepath": filepath, "imported_objects": imported, "objects": per_object,
                 "armatures": armatures, "actions": sorted(bpy.data.actions.keys()),
                 "new_actions": new_actions, "bone_shape_objects": bone_shapes}
        if kwargs:
            reply["import_options"] = kwargs
        if ignored:
            reply["ignored"] = ignored
        return reply
    def save_blend(self, filepath=None, compress=None, relative_remap=True, copy=False,
                   incremental=False, backup=True, overwrite=True, purge_orphans=False):
        """
        Save the current Blender project as a .blend file.
        filepath: omitted = save over the open file, or to a temp path when the file has never
                  been saved (reply then carries warning "unsaved file, saved to temp").
        compress: None = the use_file_compression preference (default False on 4.3, True on 5.x);
                  the reply reports the effective value.
        copy: save a copy WITHOUT changing the working file path (wm.save_as_mainfile(copy=True)).
        incremental: Blender's own numbering beside the open file (a.blend -> a1.blend); needs a
                  saved file and no filepath.
        backup: False disables the .blend1 backup for this save (save_version preference is
                  restored afterwards); the reply reports the preference in "save_versions".
        overwrite: False refuses when the target exists and is not the file already open.
        purge_orphans: run bpy.data.orphans_purge before saving; the reply reports the count.
        Reply: filepath, bytes, is_dirty (reported, differs per Blender version), elapsed, compress.
        """
        t0 = time.perf_counter()
        fp_prefs = bpy.context.preferences.filepaths
        current = os.path.abspath(bpy.data.filepath) if bpy.data.filepath else ""
        warning = None

        if incremental:
            if not current:
                return {"error": "incremental=True needs a file that has already been saved; "
                                 "pass filepath (or use incremental=False) for the first save"}
            if filepath:
                return {"error": "incremental=True saves beside the open file; do not pass filepath"}
            if copy:
                return {"error": "incremental=True and copy=True are exclusive; choose one"}
        if not filepath:
            if current:
                filepath = current
            else:
                filepath = os.path.join(tempfile.gettempdir(), f"blender_unsaved_{os.getpid()}.blend")
                warning = "unsaved file, saved to temp"
        if not filepath.lower().endswith(".blend"):
            filepath += ".blend"
        filepath = os.path.abspath(filepath)

        if not overwrite and not incremental and os.path.exists(filepath) and filepath != current:
            return {"error": f"{filepath} exists and overwrite=False; pass overwrite=True or another filepath"}

        compress = bool(fp_prefs.use_file_compression) if compress is None else bool(compress)
        purged = None
        if purge_orphans:
            purged = int(bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True))

        saved_versions = int(fp_prefs.save_version)
        try:
            if not backup:
                fp_prefs.save_version = 0
            # never pass show_save_modified_images_dialog (5.2 only); keyword args only
            if incremental:
                result = bpy.ops.wm.save_mainfile(incremental=True, compress=compress,
                                                  relative_remap=bool(relative_remap))
            elif copy:
                result = bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=True, compress=compress,
                                                     relative_remap=bool(relative_remap))
            elif filepath == current:
                result = bpy.ops.wm.save_mainfile(filepath=filepath, compress=compress,
                                                  relative_remap=bool(relative_remap))
            else:
                result = bpy.ops.wm.save_as_mainfile(filepath=filepath, compress=compress,
                                                     relative_remap=bool(relative_remap))
            if 'FINISHED' not in result:
                return {"error": f"save operator returned {set(result)}"}
        except Exception as e:
            return {"error": f"Save failed: {e}"}
        finally:
            if not backup:
                fp_prefs.save_version = saved_versions

        written = os.path.abspath(bpy.data.filepath) if (incremental or (not copy and bpy.data.filepath)) else filepath
        if not os.path.exists(written):
            return {"error": f"save finished but {written} was not written"}
        reply = {"success": True, "filepath": written, "bytes": os.path.getsize(written),
                 "is_dirty": bool(bpy.data.is_dirty), "elapsed": round(time.perf_counter() - t0, 3),
                 "compress": compress, "copy": bool(copy), "incremental": bool(incremental),
                 "save_versions": saved_versions, "purged": purged}
        if warning:
            reply["warning"] = warning
        return reply

    def load_blend(self, filepath, force=False, save_first=False, load_ui=None, use_scripts=None,
                   revert_on_fail=True):
        """
        Open a .blend file, replacing the current scene.
        Refuses while bpy.data.is_dirty unless force=True (discard) or save_first=True (save the
        open file, which must already have a path, then load). Note 4.3.2 reports is_dirty True
        right after startup, 5.2.1 False.
        load_ui / use_scripts: None = the use_load_ui / use_scripts_auto_execute preferences.
        revert_on_fail: reopen the previous file when the load raises.
        Reply: previous_file, blender_version_of_file (the saving Blender), unsaved_changes_discarded.
        """
        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}
        if not filepath.lower().endswith(".blend"):
            return {"error": "load_blend only accepts .blend files. Use import_file for 3D model formats."}

        previous = bpy.data.filepath
        dirty = bool(bpy.data.is_dirty)
        discarded = False
        if dirty and not force and not save_first:
            return {"error": f"Unsaved changes in {previous or '<never-saved file>'} would be lost. "
                             "Pass force=True to discard them or save_first=True to save them first."}
        if dirty and save_first:
            if not previous:
                return {"error": "save_first=True but the open file has never been saved; "
                                 "call save_blend(filepath=...) first or pass force=True"}
            try:
                bpy.ops.wm.save_mainfile()
            except Exception as e:
                return {"error": f"save_first failed: {e}"}
        elif dirty and force:
            discarded = True

        fp_prefs = bpy.context.preferences.filepaths
        load_ui = bool(fp_prefs.use_load_ui) if load_ui is None else bool(load_ui)
        use_scripts = bool(fp_prefs.use_scripts_auto_execute) if use_scripts is None else bool(use_scripts)
        try:
            result = bpy.ops.wm.open_mainfile(filepath=filepath, load_ui=load_ui, use_scripts=use_scripts)
            if 'FINISHED' not in result:
                raise RuntimeError(f"open operator returned {set(result)}")
        except Exception as e:
            reverted = False
            if revert_on_fail and previous and os.path.exists(previous):
                with suppress(Exception):
                    bpy.ops.wm.open_mainfile(filepath=previous, load_ui=load_ui, use_scripts=use_scripts)
                    reverted = True
            return {"error": f"Could not open {filepath}: {e}", "reverted_to": previous if reverted else None}

        scene = bpy.context.scene
        return {
            "success": True,
            "filepath": bpy.data.filepath,
            "previous_file": previous,
            "blender_version_of_file": list(bpy.data.version),
            "unsaved_changes_discarded": discarded,
            "scene_name": scene.name,
            "object_count": len(scene.objects),
            "load_ui": load_ui,
            "use_scripts": use_scripts,
        }

    # ─── Primitives & object management ─────────────────────────────────────

    def add_primitive(self, primitive_type="cube", location=None, size=2.0,
                      name=None, rotation=None):
        """Add a standard mesh primitive to the scene."""
        if location is None:
            location = [0, 0, 0]
        if rotation is None:
            rotation = [0, 0, 0]

        ptype = primitive_type.lower()
        r = size / 2  # convenience radius

        # Find viewport area for context override
        area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)

        def _add(op, **kw):
            if area:
                with bpy.context.temp_override(area=area):
                    op(**kw)
            else:
                op(**kw)

        ops = {
            "cube":       (bpy.ops.mesh.primitive_cube_add,
                           dict(size=size, location=location, rotation=rotation)),
            "plane":      (bpy.ops.mesh.primitive_plane_add,
                           dict(size=size, location=location, rotation=rotation)),
            "circle":     (bpy.ops.mesh.primitive_circle_add,
                           dict(radius=r, location=location, rotation=rotation)),
            "sphere":     (bpy.ops.mesh.primitive_uv_sphere_add,
                           dict(radius=r, location=location, rotation=rotation)),
            "ico_sphere": (bpy.ops.mesh.primitive_ico_sphere_add,
                           dict(radius=r, location=location, rotation=rotation)),
            "cylinder":   (bpy.ops.mesh.primitive_cylinder_add,
                           dict(radius=r, depth=size, location=location, rotation=rotation)),
            "cone":       (bpy.ops.mesh.primitive_cone_add,
                           dict(radius1=r, radius2=0, depth=size, location=location, rotation=rotation)),
            "torus":      (bpy.ops.mesh.primitive_torus_add,
                           dict(major_radius=r, minor_radius=r*0.3, location=location, rotation=rotation)),
            "monkey":     (bpy.ops.mesh.primitive_monkey_add,
                           dict(size=size, location=location, rotation=rotation)),
        }

        if ptype not in ops:
            return {"error": f"Unknown primitive '{ptype}'. Choose from: {list(ops.keys())}"}

        op_fn, kw = ops[ptype]
        _add(op_fn, **kw)

        obj = bpy.context.active_object
        if name and obj:
            obj.name = name
            if obj.data:
                obj.data.name = name

        return {"success": True, "name": obj.name if obj else "?",
                "type": ptype, "location": list(obj.location) if obj else location}

    def delete_object(self, name):
        """Delete an object and purge orphaned mesh/material data."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        bpy.data.objects.remove(obj, do_unlink=True)
        # Purge orphaned datablocks so mesh/material memory is freed
        for _ in range(3):
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
        return {"success": True, "deleted": name}

    def duplicate_object(self, name, new_name=None, offset=None, linked=False):
        """
        Duplicate an object.
        linked=True shares mesh data (instance); linked=False is a full independent copy.
        offset: [x, y, z] displacement from original (default [0.5, 0.5, 0])
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if offset is None:
            offset = [0.5, 0.5, 0.0]

        new_obj = obj.copy()
        new_obj.data = obj.data if linked else (obj.data.copy() if obj.data else None)
        new_obj.location = (obj.location.x + offset[0],
                            obj.location.y + offset[1],
                            obj.location.z + offset[2])
        if new_name:
            new_obj.name = new_name
        # Keep the copy next to the original in the outliner
        collections = [c for c in obj.users_collection] or [bpy.context.scene.collection]
        for col in collections:
            col.objects.link(new_obj)
        return {"success": True, "original": name, "duplicate": new_obj.name,
                "location": list(new_obj.location), "linked": linked}

    def join_objects(self, names, result_name=None):
        """Join multiple objects into the first one in the list."""
        objects = []
        for n in names:
            o = bpy.data.objects.get(n)
            if not o:
                return {"error": f"Object not found: {n}"}
            objects.append(o)

        bpy.ops.object.select_all(action='DESELECT')
        for o in objects:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.object.join()

        result = bpy.context.active_object
        if result_name and result:
            result.name = result_name
        return {"success": True, "result": result.name if result else "?",
                "merged_count": len(objects)}

    def separate_mesh(self, name, method="LOOSE"):
        """Separate a mesh by LOOSE parts, MATERIAL, or SELECTED faces."""
        obj = bpy.data.objects.get(name)
        if not obj or obj.type != 'MESH':
            return {"error": f"Mesh object not found: {name}"}

        if method not in ("LOOSE", "MATERIAL", "SELECTED"):
            return {"error": f"Unknown method '{method}'. Choose from: ['LOOSE', 'MATERIAL', 'SELECTED']"}
        before = set(bpy.data.objects.keys())
        self._select_only(obj)
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            if method != "SELECTED":
                bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.separate(type=method)
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')

        after = set(bpy.data.objects.keys())
        new_objs = list(after - before)
        return {"success": True, "method": method, "new_objects": new_objs}

    def rename_object(self, old_name, new_name):
        """Rename an object and its mesh data."""
        obj = bpy.data.objects.get(old_name)
        if not obj:
            return {"error": f"Object not found: {old_name}"}
        obj.name = new_name
        if obj.data:
            obj.data.name = new_name
        return {"success": True, "old_name": old_name, "new_name": obj.name}

    _ORIGIN_B2_TYPES = ("BOTTOM_CENTER", "TOP_CENTER", "CUSTOM")

    def set_origin(self, name, origin_type="ORIGIN_GEOMETRY", location=None, keep_world=True):
        """
        Set the object origin.
        origin_type: ORIGIN_GEOMETRY, ORIGIN_CURSOR, ORIGIN_CENTER_OF_MASS, ORIGIN_CENTER_OF_VOLUME
                     (Blender's origin_set), or B2: BOTTOM_CENTER (bbox bottom centre, the game-prop
                     standard), TOP_CENTER, CUSTOM (location="x,y,z" in WORLD space, required).
        The B2 types move the mesh data by -delta (shape keys included, RA-1) and, with keep_world=True,
        the object by +delta so world vertex positions and children stay where they were (E13:
        mesh.transform + location, never cursor tricks); keep_world=False moves the geometry instead
        (R-B2-39). A mesh shared by other objects is isolated first (R-B2-37, reply `isolated`).
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        origin_id = str(origin_type or "").strip().upper()
        if origin_id not in self._ORIGIN_B2_TYPES:
            valid = self._op_enum_ids(bpy.ops.object.origin_set, "type")
            if valid and origin_id not in valid:
                return {"error": f"origin_type '{origin_type}' not valid; valid: {valid + list(self._ORIGIN_B2_TYPES)}"}
            with self._selection_scope():
                self._select_only(obj)
                bpy.ops.object.origin_set(type=origin_id, center='MEDIAN')
            return {"success": True, "name": name, "origin_type": origin_id,
                    "new_location": list(obj.location)}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh object; {origin_id} needs mesh data (object is {obj.type})"}
        self._ensure_object_mode(obj)
        isolated = False
        if obj.data.users > 1:                      # R-B2-37: never move the geometry of the other users
            obj.data = obj.data.copy()
            isolated = True
        mesh = obj.data
        if origin_id == "CUSTOM":
            if location is None:
                return {"error": "location=\"x,y,z\" (world space) is required for origin_type CUSTOM"}
            parts = location.split(",") if isinstance(location, str) else location
            vec, verr = self._vec3(parts, "location")
            if verr:
                return {"error": verr}
            delta = obj.matrix_world.inverted() @ mathutils.Vector(vec)
        else:
            if not mesh.vertices:
                return {"error": f"{name} has no vertices"}
            n = len(mesh.vertices)
            co = array.array('f', [0.0]) * (n * 3)
            mesh.vertices.foreach_get("co", co)
            xs, ys, zs = co[0::3], co[1::3], co[2::3]
            cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
            delta = mathutils.Vector((cx, cy, min(zs) if origin_id == "BOTTOM_CENTER" else max(zs)))
        mesh.transform(mathutils.Matrix.Translation(-delta), shape_keys=True)     # RA-1: every key block moves too
        mesh.update()
        if keep_world:
            shift = mathutils.Matrix.Translation(delta)
            # M' = M @ T(delta) keeps every vertex in place; children get the inverse in their parent-inverse
            for child in obj.children:
                child.matrix_parent_inverse = mathutils.Matrix.Translation(-delta) @ child.matrix_parent_inverse
            obj.matrix_world = obj.matrix_world @ shift
        bpy.context.view_layer.update()
        return {"success": True, "name": name, "origin_type": origin_id,
                "new_location": [float(v) for v in obj.location],
                "offset_applied": [round(float(v), 6) for v in delta],      # F-B2-1: local-space shift of the mesh data
                "keep_world": bool(keep_world), "isolated": isolated,
                "children_kept": [c.name for c in obj.children] if keep_world else []}

    def snap_to_ground(self, name, ground_z=0.0):
        """Translate an object so its lowest bounding-box point sits at ground_z."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        bbox_world = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        min_z = min(v.z for v in bbox_world)
        obj.location.z += (ground_z - min_z)
        return {"success": True, "name": name, "location_z": round(obj.location.z, 6)}

    def set_smooth_shading(self, name, smooth=True, auto_smooth=True, angle=30.0):
        """
        Toggle smooth/flat shading and optionally enable auto-smooth by angle.
        Blender 4.1+ has no mesh auto-smooth flag; there the "Smooth by Angle"
        modifier is used instead. The reply says which one was applied.
        """
        import math
        obj = bpy.data.objects.get(name)
        if not obj or obj.type != 'MESH':
            return {"error": f"Mesh object not found: {name}"}
        auto_method = None
        with self._selection_scope():
            self._select_only(obj)
            if smooth:
                if auto_smooth and hasattr(obj.data, 'use_auto_smooth'):
                    bpy.ops.object.shade_smooth()
                    obj.data.use_auto_smooth = True
                    obj.data.auto_smooth_angle = math.radians(angle)
                    auto_method = "mesh.use_auto_smooth"
                elif auto_smooth and self._op_exists(bpy.ops.object.shade_smooth_by_angle):
                    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle))
                    auto_method = "smooth_by_angle"
                elif auto_smooth and self._op_exists(bpy.ops.object.shade_auto_smooth):
                    bpy.ops.object.shade_auto_smooth(use_auto_smooth=True, angle=math.radians(angle))
                    auto_method = "smooth_by_angle_modifier"
                else:
                    bpy.ops.object.shade_smooth()
            else:
                bpy.ops.object.shade_flat()
        return {"success": True, "name": name, "smooth": smooth,
                "auto_smooth": auto_method is not None,
                "auto_smooth_method": auto_method,
                "auto_smooth_angle": angle if auto_method else None}

    def parent_object(self, child_name, parent_name, keep_transform=True, parent_type="OBJECT", bone=None):
        """
        Parent child_name to parent_name, optionally preserving the world transform.
        parent_type: OBJECT (default) or BONE (+ bone: a bone of the ARMATURE parent; the child
        follows that bone). Armature deform binding is bind_armature, not this tool.
        """
        child = bpy.data.objects.get(child_name)
        parent = bpy.data.objects.get(parent_name)
        if not child:
            return {"error": f"Child object not found: {child_name}"}
        if not parent:
            return {"error": f"Parent object not found: {parent_name}"}
        if child is parent or parent in child.children_recursive:
            return {"error": f"{parent_name} is {child_name} itself or one of its descendants"}
        ptype = str(parent_type or "OBJECT").upper()
        if ptype not in ("OBJECT", "BONE"):
            return {"error": f"parent_type '{parent_type}' not valid; valid: ['OBJECT', 'BONE']"}
        if ptype == "BONE":
            if parent.type != 'ARMATURE':
                return {"error": f"parent_type BONE needs an ARMATURE parent; {parent_name} is a {parent.type}"}
            if not bone or bone not in parent.data.bones:
                return {"error": f"bone {bone!r} not found on {parent_name}; bones: {[b.name for b in parent.data.bones][:50]}"}

        orig_matrix = child.matrix_world.copy() if keep_transform else None
        child.parent = parent
        if ptype == "BONE":
            child.parent_type = 'BONE'
            child.parent_bone = bone
            child.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        else:
            child.parent_type = 'OBJECT'
            child.matrix_parent_inverse = parent.matrix_world.inverted()
        if keep_transform and orig_matrix:
            child.matrix_world = orig_matrix
        reply = {"success": True, "child": child_name, "parent": parent_name, "parent_type": ptype}
        if ptype == "BONE":
            reply["bone"] = bone
        return reply

    def select_objects(self, names=None, action="SELECT", obj_type=None):
        """
        Select/deselect objects by name list and/or type filter.
        action: SELECT, DESELECT, TOGGLE
        obj_type: if given and names is None, selects all objects of that type
        """
        if names is None and obj_type is None:
            bpy.ops.object.select_all(action=action)
            return {"success": True, "action": action,
                    "selected_count": len(bpy.context.selected_objects)}

        if names is None:
            names = [o.name for o in bpy.context.scene.objects
                     if o.type == obj_type.upper()]

        for n in names:
            o = bpy.data.objects.get(n)
            if o:
                if action == "SELECT":
                    o.select_set(True)
                elif action == "DESELECT":
                    o.select_set(False)
                elif action == "TOGGLE":
                    o.select_set(not o.select_get())

        return {"success": True, "action": action, "names": names}

    def align_objects(self, names, axis="X", align_to="FIRST"):
        """
        Align objects' origins on one axis.
        align_to: FIRST, LAST, MIN, MAX, AVERAGE
        """
        objects = []
        for n in names:
            o = bpy.data.objects.get(n)
            if not o:
                return {"error": f"Object not found: {n}"}
            objects.append(o)

        ax = {"X": 0, "Y": 1, "Z": 2}.get(axis.upper(), 0)
        locs = [o.location[ax] for o in objects]

        target = {
            "FIRST":   locs[0],
            "LAST":    locs[-1],
            "MIN":     min(locs),
            "MAX":     max(locs),
            "AVERAGE": sum(locs) / len(locs),
        }.get(align_to.upper())

        if target is None:
            return {"error": f"Unknown align_to '{align_to}'."}

        for o in objects:
            loc = list(o.location)
            loc[ax] = target
            o.location = loc

        return {"success": True, "axis": axis, "align_to": align_to,
                "value": target, "names": names}

    # ─── Materials ───────────────────────────────────────────────────────────

    def create_material(self, name, base_color=None, metallic=0.0, roughness=0.5,
                        emission_color=None, emission_strength=0.0, alpha=1.0,
                        assign_to=None):
        """
        Create or replace a PBR material with Principled BSDF.
        base_color: [r, g, b]  (default [0.8, 0.8, 0.8])
        emission_color: [r, g, b] (optional, enables emission)
        assign_to: object name to assign the material to (slot 0)
        """
        if base_color is None:
            base_color = [0.8, 0.8, 0.8]

        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        out  = nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
        links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

        bsdf.inputs["Base Color"].default_value   = (*base_color[:3], 1.0)
        bsdf.inputs["Metallic"].default_value     = metallic
        bsdf.inputs["Roughness"].default_value    = roughness
        bsdf.inputs["Alpha"].default_value        = alpha

        if alpha < 1.0:
            # 4.2+ EEVEE Next uses surface_render_method; older builds use blend/shadow_method
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = 'BLENDED'
            if hasattr(mat, "blend_method"):
                mat.blend_method = 'BLEND'
            if hasattr(mat, "shadow_method"):
                mat.shadow_method = 'CLIP'
            if hasattr(mat, "use_transparent_shadow"):
                mat.use_transparent_shadow = True

        if emission_color:
            # Blender 4.x uses "Emission Color" input; earlier uses separate "Emission"
            em_input = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
            if em_input:
                em_input.default_value = (*emission_color[:3], 1.0)
            es_input = bsdf.inputs.get("Emission Strength")
            if es_input:
                es_input.default_value = emission_strength

        if assign_to:
            obj = bpy.data.objects.get(assign_to)
            if obj:
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    obj.data.materials[0] = mat

        return {"success": True, "material": mat.name}

    def assign_material(self, object_name, material_name, slot=0):
        """Assign an existing material to an object's material slot."""
        obj = bpy.data.objects.get(object_name)
        if not obj:
            return {"error": f"Object not found: {object_name}"}
        mat = bpy.data.materials.get(material_name)
        if not mat:
            return {"error": f"Material not found: {material_name}"}

        while len(obj.data.materials) <= slot:
            obj.data.materials.append(None)
        obj.data.materials[slot] = mat
        return {"success": True, "object": object_name, "material": material_name, "slot": slot}

    def load_texture(self, material_name, image_path, texture_slot="Base Color",
                     uv_scale=1.0):
        """
        Load an image file and connect it to a texture slot of a material.
        texture_slot: 'Base Color', 'Roughness', 'Metallic', 'Normal', 'Emission Color'
        For 'Normal', a Normal Map node is inserted automatically.
        """
        mat = bpy.data.materials.get(material_name)
        if not mat:
            return {"error": f"Material not found: {material_name}"}
        if not os.path.exists(image_path):
            return {"error": f"Image not found: {image_path}"}

        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if not bsdf:
            return {"error": "No Principled BSDF node found in material"}

        # Load or reuse image
        img = bpy.data.images.get(os.path.basename(image_path))
        if img is None:
            img = bpy.data.images.load(image_path)

        # TexCoord -> Mapping -> Image Texture
        tc  = nodes.new("ShaderNodeTexCoord")
        mp  = nodes.new("ShaderNodeMapping")
        tex = nodes.new("ShaderNodeTexImage")
        tc.location  = (-800, 0)
        mp.location  = (-600, 0)
        tex.location = (-300, 0)
        tex.image    = img

        if uv_scale != 1.0:
            mp.inputs["Scale"].default_value = (uv_scale, uv_scale, uv_scale)

        links.new(tc.outputs["UV"],    mp.inputs["Vector"])
        links.new(mp.outputs["Vector"], tex.inputs["Vector"])

        if texture_slot == "Normal":
            nm = nodes.new("ShaderNodeNormalMap")
            nm.location = (-100, -200)
            links.new(tex.outputs["Color"], nm.inputs["Color"])
            links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
            img.colorspace_settings.name = 'Non-Color'
        else:
            target = bsdf.inputs.get(texture_slot)
            if not target:
                return {"error": f"Input '{texture_slot}' not found on Principled BSDF"}
            links.new(tex.outputs["Color"], target)
            if texture_slot in ("Roughness", "Metallic"):
                img.colorspace_settings.name = 'Non-Color'

        return {"success": True, "material": material_name, "texture_slot": texture_slot,
                "image": os.path.basename(image_path)}

    # ─── Modifiers ───────────────────────────────────────────────────────────

    def add_modifier_ext(self, name, modifier_type, modifier_name=None, props=None, **kwargs):
        """
        Add a modifier to an object.
        modifier_type: MIRROR, BEVEL, ARRAY, SOLIDIFY, SUBSURF, BOOLEAN,
                       DECIMATE, DISPLACE, SHRINKWRAP, WIREFRAME, SKIN, etc.
        props: dict of modifier property name -> value (extra kwargs are merged in).
        Properties that could not be set are reported under "unset".
        Common examples:
          MIRROR:   use_axis=[True,True,False], use_clip=True
          BEVEL:    width=0.1, segments=3, limit_method='ANGLE', angle_limit=0.523
          ARRAY:    count=3, use_relative_offset=True, relative_offset_displace=[1,0,0]
          SOLIDIFY: thickness=0.05
          SUBSURF:  levels=2, render_levels=3
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}

        modifier_type = str(modifier_type).upper()
        if modifier_name is None:
            modifier_name = modifier_type.replace("_", " ").title()

        try:
            mod = obj.modifiers.new(name=modifier_name, type=modifier_type)
        except Exception as e:
            valid = [i.identifier for i in bpy.types.Modifier.bl_rna.properties['type'].enum_items]
            return {"error": f"Could not add modifier of type '{modifier_type}': {e}. Valid types: {valid}"}
        if mod is None:
            return {"error": f"Modifier type '{modifier_type}' cannot be added to a {obj.type} object"}

        values = dict(props or {})
        values.update(kwargs)
        unset = {}
        for k, v in values.items():
            # D1 L3: every value goes through the driver coercer (_coerce_value via _drv_assign): dotted keys reach
            # nested settings (domain_settings.resolution_max), pointers accept "#uid" / "IDTYPE:Name" / {"uid"} and,
            # as before, a plain object name (R28). The two legacy reason texts are kept.
            parent, leaf, index = self._split_path(str(k))
            owner = mod
            if parent:
                try:
                    owner = mod.path_resolve(parent, False)
                except Exception as ex:
                    unset[k] = f"no such property ({ex})"
                    continue
            if self._drv_idprop_key(leaf) is None and owner.bl_rna.properties.get(leaf) is None:
                unset[k] = "no such property"
                continue
            prop = owner.bl_rna.properties.get(leaf)
            if prop is not None and prop.type == 'POINTER' and isinstance(v, str) and not (v.startswith("#") or ":" in v) \
                    and prop.fixed_type.identifier == "Object" and bpy.data.objects.get(v) is None:
                unset[k] = f"object '{v}' not found"
                continue
            ex = self._drv_assign(owner, leaf, index, v)
            if ex:
                unset[k] = ex.get("rna_error") or ex["error"]

        result = {"success": True, "object": name, "modifier": mod.name, "type": modifier_type,
                  "set": [k for k in values if k not in unset]}
        if unset:
            result["unset"] = unset
        return result

    def boolean_operation(self, target_name, cutter_name,
                          operation="DIFFERENCE", solver="EXACT", apply=True):
        """
        Apply a boolean modifier on target_name using cutter_name.
        operation: DIFFERENCE, UNION, INTERSECT
        solver: EXACT (better quality), FAST / FLOAT (faster, less reliable; FAST was renamed
                FLOAT in Blender 5.0, either spelling is mapped to the live enum), MANIFOLD (Blender 5.2+)
        apply: if True, applies the modifier and removes the cutter object
        Reply carries "solver": the identifier actually set on this Blender.
        """
        target = bpy.data.objects.get(target_name)
        cutter = bpy.data.objects.get(cutter_name)
        if not target:
            return {"error": f"Target not found: {target_name}"}
        if not cutter:
            return {"error": f"Cutter not found: {cutter_name}"}
        if target.type != 'MESH':
            return {"error": f"Target must be a MESH object, {target_name} is {target.type}"}

        operation_id = str(operation or "").strip().upper()
        solver_id = str(solver or "").strip().upper()

        mod = target.modifiers.new(name="Boolean", type="BOOLEAN")
        ops_valid = [e.identifier for e in mod.bl_rna.properties['operation'].enum_items]
        if operation_id not in ops_valid:
            target.modifiers.remove(mod)
            return {"error": f"operation '{operation}' not valid; valid: {ops_valid}"}
        mod.operation = operation_id
        mod.object = cutter

        resolved_solver = None
        if hasattr(mod, 'solver'):
            solvers_valid = [e.identifier for e in mod.bl_rna.properties['solver'].enum_items]
            alias = {'FAST': 'FLOAT', 'FLOAT': 'FAST'}
            for cand in (solver_id, alias.get(solver_id)):
                if cand and cand in solvers_valid:
                    mod.solver = cand
                    resolved_solver = cand
                    break
            if resolved_solver is None:
                target.modifiers.remove(mod)
                return {"error": f"solver '{solver}' not valid; valid: {solvers_valid} "
                                 "(FAST and FLOAT are accepted as aliases of each other)"}

        if apply:
            with self._selection_scope():
                self._select_only(target)
                bpy.ops.object.modifier_apply(modifier=mod.name)
                bpy.data.objects.remove(cutter, do_unlink=True)

        return {"success": True, "target": target_name, "cutter": cutter_name,
                "operation": operation_id, "solver": resolved_solver, "applied": bool(apply)}

    # ─── Render settings ─────────────────────────────────────────────────────

    def set_render_settings(self, engine=None, width=None, height=None,
                            samples=None, output_path=None, file_format=None,
                            transparent_background=None, fps=None, fps_base=None,
                            frame_start=None, frame_end=None, resolution_percentage=None,
                            color_mode=None, color_depth=None, compression=None, denoise=None,
                            device=None, use_persistent_data=None, use_simplify=None,
                            simplify_subdivision=None):
        """
        Configure scene render settings (the convenience wrapper over the RENDER / OUTPUT /
        SCENE / CYCLES settings scopes; every value goes through the same RNA validation as
        set_settings and lands in "set" / "unset" {key: reason} exactly like add_modifier).
        engine: CYCLES, BLENDER_EEVEE (or EEVEE), BLENDER_WORKBENCH (or WORKBENCH), case-insensitive;
                the EEVEE id differs per Blender version and is resolved by try-assign
        file_format: PNG, JPEG, OPEN_EXR, TIFF, FFMPEG, ... (live enum listed in the error)
        color_mode: BW / RGB / RGBA; color_depth: '8' / '16' / '32' (format dependent);
        device: CPU / GPU (Cycles); denoise: Cycles use_denoising.
        Reply carries "engine": the identifier actually set on this Blender.
        """
        scene = bpy.context.scene
        if engine is not None:
            err = self._set_render_engine(scene.render, engine)
            if err:
                return err
        if file_format is not None:
            err = self._set_file_format(scene.render.image_settings, file_format)
            if err:
                return err

        plan = {
            "RENDER": {"resolution_x": width, "resolution_y": height, "filepath": output_path,
                       "film_transparent": transparent_background, "fps": fps, "fps_base": fps_base,
                       "resolution_percentage": resolution_percentage,
                       "use_persistent_data": use_persistent_data, "use_simplify": use_simplify,
                       "simplify_subdivision": simplify_subdivision},
            "SCENE": {"frame_start": frame_start, "frame_end": frame_end},
            "OUTPUT": {"color_mode": color_mode, "color_depth": color_depth, "compression": compression},
            "CYCLES": {"use_denoising": denoise, "device": device},
        }
        done, unset = [], {}
        for scope, values in plan.items():
            wanted = {k: v for k, v in values.items() if v is not None}
            if not wanted:
                continue
            outcome = self._apply_settings(scope, wanted, scene)
            done += outcome["set"]
            unset.update(outcome["unset"])
        if engine is not None:
            done.append("engine")
        if file_format is not None:
            done.append("file_format")

        if samples is not None:
            if scene.render.engine == 'CYCLES':
                scene.cycles.samples = int(samples)
                done.append("samples")
            elif scene.render.engine in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT') and hasattr(scene, 'eevee'):
                scene.eevee.taa_render_samples = int(samples)
                done.append("samples")
            else:
                unset["samples"] = f"engine {scene.render.engine} has no sample count here"

        reply = {
            "success": True,
            "engine": scene.render.engine,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "output": scene.render.filepath,
            "transparent": scene.render.film_transparent,
            "set": done,
        }
        if unset:
            reply["unset"] = unset
        return reply

    # ─── Animation ───────────────────────────────────────────────────────────

    def add_keyframe(self, name, data_path="location", frame=None, value=None, bone=None):
        """
        Insert a keyframe on an object property.
        data_path: 'location', 'rotation_euler', 'scale', or any animatable path
        frame: frame number (defaults to current scene frame)
        value: if given, sets the property to this value before keying.
               For location/rotation/scale pass [x, y, z] (rotation in degrees); for single values pass a number.
        bone: pose bone name on an ARMATURE object; data_path is then relative to the pose bone.
        Pose bones (and objects) whose rotation_mode is QUATERNION / AXIS_ANGLE are switched to
        XYZ when rotation_euler is keyed (reply: rotation_mode_changed), otherwise the key is invisible.
        """
        import math
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}

        root = obj
        if bone is not None:
            if obj.type != 'ARMATURE':
                return {"error": f"bone={bone!r} needs an ARMATURE object; {name} is a {obj.type}"}
            pb = obj.pose.bones.get(bone)
            if pb is None:
                return {"error": f"Bone not found: {bone}; bones: {[b.name for b in obj.pose.bones][:50]}"}
            root = pb

        scene = bpy.context.scene
        if frame is not None:
            scene.frame_set(int(frame))

        # Split "a.b[\"c\"].d" into owner path + attribute (last '.' outside brackets)
        depth, split_at = 0, -1
        for i, ch in enumerate(data_path):
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
            elif ch == '.' and depth == 0:
                split_at = i
        try:
            if split_at >= 0:
                owner = root.path_resolve(data_path[:split_at])
                attr = data_path[split_at + 1:]
            else:
                owner, attr = root, data_path
            current = getattr(owner, attr)
        except Exception as e:
            return {"error": f"Cannot resolve '{data_path}' on {name}{'.' + bone if bone else ''}: {e}"}

        rotation_mode_changed = None
        if attr in ("rotation_euler", "delta_rotation_euler") and hasattr(owner, "rotation_mode"):
            if owner.rotation_mode in ('QUATERNION', 'AXIS_ANGLE'):
                rotation_mode_changed = {"from": owner.rotation_mode, "to": "XYZ"}
                owner.rotation_mode = 'XYZ'

        if value is not None:
            is_vector = hasattr(current, "__len__") and not isinstance(current, str)
            if attr in ("rotation_euler", "delta_rotation_euler") and isinstance(value, (list, tuple)):
                value = [math.radians(v) for v in value]
            if is_vector and not isinstance(value, (list, tuple)):
                return {"error": f"{data_path} expects {len(current)} values, got a single number"}
            if not is_vector and isinstance(value, (list, tuple)):
                if len(value) != 1:
                    return {"error": f"{data_path} expects a single value, got {len(value)}"}
                value = value[0]
            if is_vector and len(value) != len(current):
                return {"error": f"{data_path} expects {len(current)} values, got {len(value)}"}
            try:
                setattr(owner, attr, value)
            except Exception as e:
                return {"error": f"Could not set {data_path}: {e}"}

        # Key on the ID that owns the property (obj.data for data.*, obj for modifiers[...])
        try:
            id_owner = owner if isinstance(owner, bpy.types.ID) else owner.id_data
            key_path = attr if id_owner is owner else owner.path_from_id(attr)
            id_owner.keyframe_insert(data_path=key_path, frame=scene.frame_current)
        except Exception as e:
            return {"error": f"Could not insert keyframe on {data_path}: {e}"}

        reply = {"success": True, "name": name, "data_path": data_path,
                 "keyed_on": id_owner.name, "frame": scene.frame_current}
        if bone is not None:
            reply["bone"] = bone
        if rotation_mode_changed:
            reply["rotation_mode_changed"] = rotation_mode_changed
        ad = getattr(id_owner, "animation_data", None)
        if ad is not None and ad.action is not None:
            reply["action"] = ad.action.name
            fcurves, _groups, _new = self._action_channels(id_owner)
            reply["fcurve_count"] = len(fcurves) if fcurves is not None else 0
        return reply

    def set_frame(self, frame):
        """Set the current scene frame."""
        bpy.context.scene.frame_set(int(frame))
        return {"success": True, "frame": bpy.context.scene.frame_current}

    # ─── Collections ─────────────────────────────────────────────────────────

    def create_collection(self, name, parent_collection=None):
        """Create a new collection and link it to the scene (or a parent collection)."""
        col = bpy.data.collections.get(name)
        if col is None:
            col = bpy.data.collections.new(name)

        if parent_collection:
            parent = bpy.data.collections.get(parent_collection)
            if not parent:
                return {"error": f"Parent collection not found: {parent_collection}"}
            if col.name not in parent.children:
                parent.children.link(col)
        else:
            scene_cols = [c.name for c in bpy.context.scene.collection.children]
            if col.name not in scene_cols:
                bpy.context.scene.collection.children.link(col)

        return {"success": True, "collection": col.name}

    def move_to_collection(self, object_names, collection_name):
        """Move objects into a collection (removes them from all other collections)."""
        if isinstance(object_names, str):
            object_names = [object_names]

        col = bpy.data.collections.get(collection_name)
        if col is None:
            return {"error": f"Collection not found: '{collection_name}'. "
                             "Create it first with create_collection."}

        moved = []
        for n in object_names:
            obj = bpy.data.objects.get(n)
            if not obj:
                return {"error": f"Object not found: {n}"}
            # Unlink from all current collections
            for c in list(obj.users_collection):
                c.objects.unlink(obj)
            col.objects.link(obj)
            moved.append(n)

        return {"success": True, "collection": collection_name, "moved": moved}

    # ─── Settings, save and load (B0 Tier 1) ────────────────────────────────

    _SNAPSHOT_SCOPES = ("SCENE", "RENDER", "OUTPUT", "CYCLES", "EEVEE", "COLOR", "UNITS", "VIEWPORT")
    _UNIT_PRESETS = {"UNREAL": ("METRIC", 0.01, "CENTIMETERS"), "UNITY": ("METRIC", 1.0, "METERS"),
                     "GODOT": ("METRIC", 1.0, "METERS"), "BEVY": ("METRIC", 1.0, "METERS"),
                     "TIMBERMESH": ("METRIC", 1.0, "METERS"), "NONE": None}
    _PROFILE_KEY = "blendermcp_profile"

    @staticmethod
    def _scope_list(scopes, default):
        if scopes is None:
            return list(default)
        if isinstance(scopes, str):
            return [s.strip().upper() for s in scopes.split(",") if s.strip()]
        return [str(s).upper() for s in scopes]

    @staticmethod
    def _json_arg(value, label):
        """Accept a dict/list or a JSON string; return (parsed, error dict|None)."""
        if value is None or isinstance(value, (dict, list)):
            return value, None
        try:
            return json.loads(value), None
        except Exception as e:
            return None, {"error": f"{label} must be JSON (or a dict/list): {e}"}

    @staticmethod
    def _dirty_guard(force):
        """Error dict when unsaved changes would be discarded, else None."""
        if bpy.data.is_dirty and not force:
            return {"error": f"Unsaved changes in {bpy.data.filepath or '<never-saved file>'} would be lost; "
                             "pass force=True to discard them (4.3.2 reports is_dirty right after startup)"}
        return None

    @staticmethod
    def _missing_files():
        missing = []
        for img in bpy.data.images:
            if img.source == 'FILE' and not img.packed_file and img.filepath:
                path = bpy.path.abspath(img.filepath)
                if not os.path.exists(path):
                    missing.append({"type": "image", "name": img.name, "path": path})
        for lib in bpy.data.libraries:
            path = bpy.path.abspath(lib.filepath)
            if not os.path.exists(path):
                missing.append({"type": "library", "name": lib.name, "path": path})
        return missing

    def get_file_state(self):
        """File lifecycle facts; is_dirty and file_version are reported, never assumed."""
        fp = bpy.data.filepath
        tempdir = bpy.app.tempdir
        autosaves = []
        with suppress(Exception):
            for entry in os.scandir(tempdir):
                if entry.is_file() and entry.name.lower().endswith(".blend"):
                    autosaves.append({"path": entry.path, "mtime": entry.stat().st_mtime})
        autosaves.sort(key=lambda d: d["mtime"], reverse=True)
        recent = []
        with suppress(Exception):
            recent_txt = os.path.join(bpy.utils.user_resource('CONFIG'), "recent-files.txt")
            if os.path.exists(recent_txt):
                recent = [l.strip() for l in open(recent_txt, encoding="utf-8", errors="replace") if l.strip()]
        backups = []
        if fp:
            n = 1
            while os.path.exists(f"{fp}{n}"):
                backups.append(f"{fp}{n}")
                n += 1
        return {"success": True, "filepath": fp, "is_saved": bool(bpy.data.is_saved),
                "is_dirty": bool(bpy.data.is_dirty), "file_version": list(bpy.data.version),
                "blender_version": bpy.app.version_string, "use_autopack": bool(bpy.data.use_autopack),
                "packed_images": [i.name for i in bpy.data.images if i.packed_file],
                "missing_files": self._missing_files(),
                "libraries": [bpy.path.abspath(l.filepath) for l in bpy.data.libraries],
                "autosave_dir": tempdir, "autosave_files": autosaves[:20],
                "recent_files": recent, "backup_files": backups}

    def new_file(self, template=None, empty=False, load_ui=False, force=False):
        """wm.read_homefile with the same dirty guard as load_blend. Reply: object count after."""
        err = self._dirty_guard(force)
        if err:
            return err
        kwargs = {"use_empty": bool(empty), "load_ui": bool(load_ui)}
        if template:
            kwargs["app_template"] = str(template)
        try:
            result = bpy.ops.wm.read_homefile(**kwargs)
            if 'FINISHED' not in result:
                return {"error": f"read_homefile returned {set(result)}"}
        except Exception as e:
            return {"error": f"new_file failed: {e}"}
        return {"success": True, "template": template, "empty": bool(empty),
                "object_count": len(bpy.context.scene.objects), "filepath": bpy.data.filepath}

    def revert_file(self, force=False, use_scripts=None):
        """wm.revert_mainfile; refuses when the file was never saved."""
        if not bpy.data.filepath:
            return {"error": "revert_file needs a saved file; this file has never been saved"}
        err = self._dirty_guard(force)
        if err:
            return err
        fp_prefs = bpy.context.preferences.filepaths
        use_scripts = bool(fp_prefs.use_scripts_auto_execute) if use_scripts is None else bool(use_scripts)
        try:
            result = bpy.ops.wm.revert_mainfile(use_scripts=use_scripts)
            if 'FINISHED' not in result:
                return {"error": f"revert_mainfile returned {set(result)}"}
        except Exception as e:
            return {"error": f"revert failed: {e}"}
        return {"success": True, "filepath": bpy.data.filepath, "object_count": len(bpy.context.scene.objects),
                "is_dirty": bool(bpy.data.is_dirty)}

    def recover_file(self, mode="LAST_SESSION", filepath=None, force=False):
        """wm.recover_last_session / wm.recover_auto_save(filepath from get_file_state.autosave_files)."""
        mode = str(mode or "").upper()
        if mode not in ("LAST_SESSION", "AUTOSAVE"):
            return {"error": f"mode '{mode}' not valid; valid: ['LAST_SESSION', 'AUTOSAVE']"}
        err = self._dirty_guard(force)
        if err:
            return err
        try:
            if mode == "LAST_SESSION":
                result = bpy.ops.wm.recover_last_session()
            else:
                if not filepath or not os.path.exists(filepath):
                    return {"error": f"AUTOSAVE needs an existing filepath (see get_file_state.autosave_files); got {filepath!r}"}
                result = bpy.ops.wm.recover_auto_save(filepath=filepath)
            if 'FINISHED' not in result:
                return {"error": f"recover returned {set(result)} (no session file to recover?)"}
        except Exception as e:
            return {"error": f"recover failed: {e}"}
        return {"success": True, "mode": mode, "filepath": bpy.data.filepath,
                "object_count": len(bpy.context.scene.objects)}

    def save_copy(self, filepath, compress=None, relative_remap=True, pack_images=False):
        """save_blend(copy=True) plus an optional temporary pack of external images, undone afterwards."""
        if not filepath:
            return {"error": "filepath is required"}
        newly_packed = []
        if pack_images:
            for img in bpy.data.images:
                if img.source == 'FILE' and not img.packed_file and img.filepath:
                    try:
                        img.pack()
                        newly_packed.append(img.name)
                    except Exception as e:
                        return {"error": f"could not pack image {img.name}: {e}"}
        try:
            reply = self.save_blend(filepath=filepath, compress=compress, relative_remap=relative_remap, copy=True)
        finally:
            for name in newly_packed:
                img = bpy.data.images.get(name)
                if img is not None and img.packed_file:
                    with suppress(Exception):
                        img.unpack(method='USE_ORIGINAL')
        if "error" in reply:
            return reply
        reply["packed_images"] = newly_packed
        return reply

    @staticmethod
    def _tri_count():
        return sum(sum(len(p.vertices) - 2 for p in o.data.polygons)
                   for o in bpy.context.scene.objects if o.type == 'MESH')

    @staticmethod
    def _sidecar_path(filepath, suffix):
        return f"{filepath}{suffix}"

    def save_version(self, note=None, pattern="{stem}_v{n:03d}", dir=None, copy=True):
        """
        Numbered copy of the open file (a.blend -> a_v001.blend) plus a sidecar
        <file>.versions.json (n, path, timestamp, note, object_count, tri_count). copy=True keeps
        working on the original; copy=False switches to the new version. Needs a saved file.
        """
        current = bpy.data.filepath
        if not current:
            return {"error": "save_version needs a saved file; call save_blend(filepath=...) first"}
        current = os.path.abspath(current)
        stem = os.path.splitext(os.path.basename(current))[0]
        folder = os.path.abspath(dir) if dir else os.path.dirname(current)
        os.makedirs(folder, exist_ok=True)
        sidecar = self._sidecar_path(current, ".versions.json")
        entries = []
        if os.path.exists(sidecar):
            with suppress(Exception):
                entries = json.load(open(sidecar, encoding="utf-8"))
        n = (max((e.get("n", 0) for e in entries), default=0)) + 1
        try:
            name = pattern.format(stem=stem, n=n)
        except Exception as e:
            return {"error": f"pattern '{pattern}' is not valid; use {{stem}} and {{n}} placeholders: {e}"}
        target = os.path.join(folder, name + (".blend" if not name.lower().endswith(".blend") else ""))
        while os.path.exists(target):
            n += 1
            name = pattern.format(stem=stem, n=n)
            target = os.path.join(folder, name + (".blend" if not name.lower().endswith(".blend") else ""))
        reply = self.save_blend(filepath=target, copy=bool(copy))
        if "error" in reply:
            return reply
        entry = {"n": n, "path": target, "timestamp": datetime.now().isoformat(timespec="seconds"),
                 "note": note, "object_count": len(bpy.context.scene.objects), "tri_count": self._tri_count()}
        entries.append(entry)
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        return {"success": True, "version": entry, "sidecar": sidecar, "working_file": bpy.data.filepath,
                "count": len(entries)}

    def list_versions(self, filepath=None):
        """Read <file>.versions.json beside filepath (default: the open file) and check each version exists."""
        base = os.path.abspath(filepath) if filepath else bpy.data.filepath
        if not base:
            return {"error": "no filepath given and the open file has never been saved"}
        sidecar = self._sidecar_path(base, ".versions.json")
        if not os.path.exists(sidecar):
            return {"success": True, "file": base, "sidecar": sidecar, "versions": []}
        try:
            entries = json.load(open(sidecar, encoding="utf-8"))
        except Exception as e:
            return {"error": f"could not read {sidecar}: {e}"}
        for e in entries:
            e["exists"] = os.path.exists(e.get("path", ""))
        return {"success": True, "file": base, "sidecar": sidecar, "versions": entries}

    _LIB_KINDS = ("objects", "collections", "materials", "node_groups", "meshes", "images",
                  "actions", "worlds", "cameras", "lights", "armatures", "curves", "texts")

    def append_from_blend(self, filepath, datablocks=None, names=None, kind="objects", link=False,
                          collection=None, instance_collections=False, relative=True, list_only=False):
        """
        Append (link=False) or link (link=True) data-blocks from another .blend through
        bpy.data.libraries.load (keyword-only on both versions; refuses the open file).
        datablocks: {"objects": [...], "collections": [...], ...} (JSON or dict), or names + kind.
        list_only=True returns the file's contents per data type without loading anything.
        Objects land in `collection` (default: the scene collection); collections are linked to
        the scene collection, or instanced as empties when instance_collections=True.
        """
        if not filepath or not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}
        if not filepath.lower().endswith(".blend"):
            return {"error": f"{filepath} is not a .blend file"}
        if bpy.data.filepath and os.path.abspath(filepath) == os.path.abspath(bpy.data.filepath):
            return {"error": "cannot load from the currently open file (Blender refuses it)"}
        wanted, err = self._json_arg(datablocks, "datablocks")
        if err:
            return err
        if wanted is None:
            if names is None:
                if not list_only:
                    return {"error": "pass datablocks (JSON per kind) or names + kind, or list_only=True"}
                wanted = {}
            else:
                if isinstance(names, str):
                    names = [n.strip() for n in names.split(",") if n.strip()]
                k = str(kind or "objects").lower()
                if k not in self._LIB_KINDS:
                    return {"error": f"kind '{kind}' not valid; valid: {list(self._LIB_KINDS)}"}
                wanted = {k: list(names)}
        bad_kinds = [k for k in wanted if k not in self._LIB_KINDS]
        if bad_kinds:
            return {"error": f"unknown data kinds {bad_kinds}; valid: {list(self._LIB_KINDS)}"}

        try:
            if list_only:
                with bpy.data.libraries.load(filepath, link=False, relative=bool(relative)) as (data_from, _):
                    listing = {k: list(getattr(data_from, k)) for k in self._LIB_KINDS if hasattr(data_from, k)}
                return {"success": True, "filepath": filepath, "contents": listing}

            missing, plan = {}, {}
            before_objects = set(bpy.data.objects.keys())
            with bpy.data.libraries.load(filepath, link=bool(link), relative=bool(relative)) as (data_from, data_to):
                for k, wanted_names in wanted.items():
                    available = list(getattr(data_from, k))
                    found = [n for n in wanted_names if n in available]
                    lost = [n for n in wanted_names if n not in available]
                    if lost:
                        missing[k] = lost
                    if found:
                        setattr(data_to, k, found)
                        plan[k] = found
        except Exception as e:
            return {"error": f"libraries.load failed: {e}"}

        scene = bpy.context.scene
        target = scene.collection
        if collection:
            target = bpy.data.collections.get(collection)
            if target is None:
                return {"error": f"Collection not found: {collection}"}
        linked = {}
        for obj in getattr(data_to, "objects", []) or []:
            if obj is not None and obj.name not in target.objects:
                target.objects.link(obj)
                linked.setdefault("objects", []).append(obj.name)
        for col in getattr(data_to, "collections", []) or []:
            if col is None:
                continue
            if instance_collections:
                empty = bpy.data.objects.new(col.name, None)
                empty.instance_type = 'COLLECTION'
                empty.instance_collection = col
                target.objects.link(empty)
                linked.setdefault("instances", []).append(empty.name)
            elif col.name not in target.children:
                target.children.link(col)
                linked.setdefault("collections", []).append(col.name)
        appended = {k: [d.name for d in (getattr(data_to, k) or []) if d is not None] for k in plan}
        reply = {"success": True, "filepath": filepath, "link": bool(link), "appended": appended,
                 "linked_into": target.name, "new_objects": sorted(set(bpy.data.objects.keys()) - before_objects),
                 "object_count": len(scene.objects)}
        if missing:
            reply["missing"] = missing
        if linked:
            reply["scene_links"] = linked
        return reply

    def link_from_blend(self, filepath, datablocks=None, names=None, kind="objects", collection=None,
                        instance_collections=False, relative=True, list_only=False):
        """append_from_blend with link=True (data stays in the library file)."""
        return self.append_from_blend(filepath, datablocks=datablocks, names=names, kind=kind, link=True,
                                      collection=collection, instance_collections=instance_collections,
                                      relative=relative, list_only=list_only)

    def _persist_prefs(self, persist):
        """Consent rule: preferences are written only on explicit request. Returns (persisted, error)."""
        if not persist:
            return False, None
        try:
            bpy.ops.wm.save_userpref()
            return True, None
        except Exception as e:
            return False, str(e)

    def set_autosave(self, enabled=None, interval_minutes=None, save_versions=None, temp_dir=None, persist=False):
        """Preferences filepaths.use_auto_save_temporary_files / auto_save_time / save_version / temporary_directory."""
        fp = bpy.context.preferences.filepaths
        values = {"use_auto_save_temporary_files": enabled, "auto_save_time": interval_minutes,
                  "save_version": save_versions, "temporary_directory": temp_dir}
        outcome = self._apply_settings("PREFS_FILEPATHS", {k: v for k, v in values.items() if v is not None})
        persisted, perr = self._persist_prefs(persist)
        reply = {"success": True, "enabled": fp.use_auto_save_temporary_files, "interval_minutes": fp.auto_save_time,
                 "save_versions": fp.save_version, "temp_dir": fp.temporary_directory,
                 "set": outcome["set"], "persisted": persisted, "preferences_dirty": bool(bpy.context.preferences.is_dirty)}
        if outcome["unset"]:
            reply["unset"] = outcome["unset"]
        if perr:
            reply["persist_error"] = perr
        return reply

    def _path_op(self, op, label, **kwargs):
        if not bpy.data.filepath and label.startswith("make_paths"):
            return {"error": f"{label} needs a saved file (relative paths are relative to it)"}
        try:
            result = op(**kwargs)
            if 'FINISHED' not in result:
                return {"error": f"{label} returned {set(result)}"}
        except Exception as e:
            return {"error": f"{label} failed: {e}"}
        return None

    def _path_report(self):
        rel = [i.name for i in bpy.data.images if i.filepath.startswith("//")]
        rel += [l.name for l in bpy.data.libraries if l.filepath.startswith("//")]
        absolute = [i.name for i in bpy.data.images if i.filepath and not i.filepath.startswith("//")]
        absolute += [l.name for l in bpy.data.libraries if l.filepath and not l.filepath.startswith("//")]
        return {"relative": rel, "absolute": absolute, "missing": self._missing_files()}

    def make_paths_relative(self):
        err = self._path_op(bpy.ops.file.make_paths_relative, "make_paths_relative")
        return err or {"success": True, **self._path_report()}

    def make_paths_absolute(self):
        err = self._path_op(bpy.ops.file.make_paths_absolute, "make_paths_absolute")
        return err or {"success": True, **self._path_report()}

    def find_missing_files(self, directory, find_all=False):
        """bpy.ops.file.find_missing_files(directory, find_all); reply lists what is still missing."""
        if not directory or not os.path.isdir(directory):
            return {"error": f"directory not found: {directory}"}
        before = self._missing_files()
        err = self._path_op(bpy.ops.file.find_missing_files, "find_missing_files",
                            directory=directory, find_all=bool(find_all))
        if err:
            return err
        after = self._missing_files()
        return {"success": True, "directory": directory, "missing_before": len(before),
                "found": len(before) - len(after), "still_missing": after}

    def pack_all(self):
        err = self._path_op(bpy.ops.file.pack_all, "pack_all")
        return err or {"success": True, "packed_images": [i.name for i in bpy.data.images if i.packed_file]}

    def unpack_all(self, unpack_method="USE_LOCAL"):
        method = str(unpack_method or "").upper()
        valid = [e.identifier for e in bpy.ops.file.unpack_all.get_rna_type().properties["method"].enum_items]
        if method not in valid:
            return {"error": f"unpack_method '{unpack_method}' not valid; valid: {valid}"}
        err = self._path_op(bpy.ops.file.unpack_all, "unpack_all", method=method)
        return err or {"success": True, "method": method,
                       "packed_images": [i.name for i in bpy.data.images if i.packed_file]}

    # ── Settings, generic ──

    @staticmethod
    def _prop_default(prop):
        try:
            if getattr(prop, "is_array", False) and getattr(prop, "array_length", 0) > 0:
                return list(prop.default_array)
            if prop.type == 'ENUM' and prop.is_enum_flag:
                return sorted(prop.default_flag)
            return prop.default
        except Exception:
            return None

    def describe_settings(self, scope):
        """Every property of a scope from bl_rna: type, value, default, enum items, range, description, read-only."""
        try:
            owners = self._scope_owner(scope)
        except ValueError as e:
            return {"error": str(e)}
        if not owners:
            return {"error": f"scope {scope} has no owner in this session (no VIEW_3D / add-on without preferences)"}
        props = {}
        for owner in owners:
            for prop in owner.bl_rna.properties:
                if prop.identifier == "rna_type" or prop.identifier in props:
                    continue
                entry = {"type": prop.type, "description": prop.description, "readonly": bool(prop.is_readonly),
                         "owner": owner.bl_rna.identifier}
                if prop.type in ('POINTER', 'COLLECTION'):
                    try:
                        v = getattr(owner, prop.identifier)
                        entry["value"] = getattr(v, "name", None) if prop.type == 'POINTER' else len(v)
                    except Exception:
                        entry["value"] = None
                    entry["readonly"] = True
                    props[prop.identifier] = entry
                    continue
                try:
                    entry["value"] = self._rna_get(owner, prop.identifier)
                except Exception as e:
                    entry["value"] = None
                    entry["read_error"] = str(e)
                entry["default"] = self._prop_default(prop)
                if prop.type == 'ENUM':
                    entry["enum_items"] = [i.identifier for i in prop.enum_items]
                    if not entry["enum_items"]:
                        entry["note"] = "dynamic enum: empty headless, validated by assignment"
                if prop.type in ('INT', 'FLOAT'):
                    entry["min"], entry["max"] = prop.hard_min, prop.hard_max
                    entry["soft_min"], entry["soft_max"] = prop.soft_min, prop.soft_max
                    if getattr(prop, "subtype", "NONE") != 'NONE':
                        entry["subtype"] = prop.subtype
                if getattr(prop, "is_array", False) and getattr(prop, "array_length", 0) > 0:
                    entry["array_length"] = prop.array_length
                props[prop.identifier] = entry
        return {"success": True, "scope": str(scope).upper(), "count": len(props), "properties": props}

    def get_settings(self, scope, keys=None):
        """Values only (enums as strings, vectors as lists, pointers as names)."""
        try:
            owners = self._scope_owner(scope)
        except ValueError as e:
            return {"error": str(e)}
        if not owners:
            return {"error": f"scope {scope} has no owner in this session"}
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split(",") if k.strip()]
        values, missing = {}, []
        for owner in owners:
            for prop in owner.bl_rna.properties:
                ident = prop.identifier
                if ident == "rna_type" or ident in values or (keys and ident not in keys):
                    continue
                try:
                    if prop.type == 'POINTER':
                        v = getattr(owner, ident)
                        values[ident] = getattr(v, "name", None)
                    elif prop.type == 'COLLECTION':
                        continue
                    else:
                        values[ident] = self._rna_get(owner, ident)
                except Exception:
                    pass
        if str(scope).upper() == "OUTPUT" and (not keys or "ffmpeg" in keys):
            ff = bpy.context.scene.render.ffmpeg
            values["ffmpeg"] = {}
            for ident in self._rna_simple_props(ff):
                with suppress(Exception):
                    values["ffmpeg"][ident] = self._rna_get(ff, ident)
        if keys:
            missing = [k for k in keys if k not in values]
        reply = {"success": True, "scope": str(scope).upper(), "values": values}
        if missing:
            reply["missing"] = missing
        return reply

    def set_settings(self, scope, values, persist=False):
        """Validated bulk set (see _apply_settings); persist=True saves preferences for PREFS_* / ADDON: / MCP scopes."""
        values, err = self._json_arg(values, "values")
        if err:
            return err
        if not isinstance(values, dict):
            return {"error": "values must be a JSON object {key: value}"}
        try:
            outcome = self._apply_settings(scope, values)
        except ValueError as e:
            return {"error": str(e)}
        reply = {"success": True, "scope": str(scope).upper(), **outcome}
        name = str(scope).upper()
        if persist and (name.startswith("PREFS_") or name.startswith("ADDON:") or name == "MCP"):
            persisted, perr = self._persist_prefs(True)
            reply["persisted"] = persisted
            if perr:
                reply["persist_error"] = perr
        else:
            reply["persisted"] = False
        return reply

    @staticmethod
    def _snapshots():
        """Named settings snapshots, kept in driver_namespace so an add-on reload does not drop them."""
        return bpy.app.driver_namespace.setdefault("blendermcp_settings_snapshots", {})

    def _capture_scopes(self, scopes):
        data = {}
        for scope in scopes:
            owners = self._scope_owner(scope)
            values = {}
            for owner in owners:
                for ident in self._rna_simple_props(owner):
                    if ident in values:
                        continue
                    try:
                        values[ident] = self._rna_get(owner, ident)
                    except Exception:
                        pass
            data[scope] = values
        return data

    def _restore_scopes(self, data, scopes=None):
        restored, failed, missing = {}, {}, []
        for scope, values in data.items():
            if scopes and scope not in scopes:
                continue
            try:
                owners = self._scope_owner(scope)
            except ValueError as e:
                failed[scope] = str(e)
                continue
            first = self._SCOPE_RESTORE_FIRST.get(scope, ())
            order = [k for k in first if k in values] + [k for k in values if k not in first]
            count = 0
            for key in order:
                owner = next((o for o in owners if o.bl_rna.properties.get(key) is not None), None)
                if owner is None:
                    missing.append(f"{scope}.{key}")
                    continue
                try:
                    if self._rna_get(owner, key) == values[key]:
                        count += 1
                        continue
                except Exception:
                    pass
                reason = self._rna_set_validated(owner, key, values[key])
                if reason is None:
                    count += 1
                else:
                    failed[f"{scope}.{key}"] = reason
            restored[scope] = count
        return restored, failed, missing

    def settings_snapshot(self, name, scopes=None, filepath=None):
        """Capture the named scopes into a session snapshot (and a JSON file when filepath is given)."""
        if not name:
            return {"error": "name is required"}
        scopes = self._scope_list(scopes, self._SNAPSHOT_SCOPES)
        try:
            data = self._capture_scopes(scopes)
        except ValueError as e:
            return {"error": str(e)}
        self._snapshots()[name] = data
        reply = {"success": True, "name": name, "keys_per_scope": {s: len(v) for s, v in data.items()}}
        if filepath:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, default=str)
                reply["filepath"] = filepath
            except Exception as e:
                reply["file_error"] = str(e)
        return reply

    def settings_restore(self, name=None, filepath=None, scopes=None):
        """Restore a snapshot by name or from a JSON file; reports failed and missing keys."""
        if filepath:
            if not os.path.exists(filepath):
                return {"error": f"File not found: {filepath}"}
            try:
                data = json.load(open(filepath, encoding="utf-8"))
            except Exception as e:
                return {"error": f"could not read {filepath}: {e}"}
        elif name:
            data = self._snapshots().get(name)
            if data is None:
                return {"error": f"no snapshot named '{name}'; have: {sorted(self._snapshots().keys())}"}
        else:
            return {"error": "pass name or filepath"}
        scopes = self._scope_list(scopes, list(data.keys())) if scopes else None
        restored, failed, missing = self._restore_scopes(data, scopes)
        reply = {"success": True, "restored": restored}
        if failed:
            reply["failed"] = failed
        if missing:
            reply["missing"] = missing
        return reply

    def list_settings_snapshots(self):
        return {"success": True, "snapshots": {n: {s: len(v) for s, v in d.items()} for n, d in self._snapshots().items()}}

    def delete_settings_snapshot(self, name):
        if name not in self._snapshots():
            return {"error": f"no snapshot named '{name}'; have: {sorted(self._snapshots().keys())}"}
        del self._snapshots()[name]
        return {"success": True, "deleted": name}

    # ── Settings, typed conveniences ──

    def _outcome_reply(self, outcomes, **extra):
        done, unset = [], {}
        for o in outcomes:
            done += o["set"]
            unset.update(o["unset"])
        reply = {"success": True, "set": done, **extra}
        if unset:
            reply["unset"] = unset
        return reply

    def set_output_settings(self, filepath=None, file_format=None, color_mode=None, color_depth=None,
                            compression=None, quality=None, film_transparent=None, use_stamp=None,
                            use_overwrite=None, use_placeholder=None, ffmpeg=None):
        """Output path, format and video settings in one call (ffmpeg: JSON {format, codec, constant_rate_factor, ...})."""
        scene = bpy.context.scene
        outcomes = []
        done_extra = []
        if file_format is not None:
            err = self._set_file_format(scene.render.image_settings, file_format)
            if err:
                return err
            done_extra.append("file_format")
        outcomes.append(self._apply_settings("RENDER", {k: v for k, v in {
            "filepath": filepath, "film_transparent": film_transparent, "use_stamp": use_stamp,
            "use_overwrite": use_overwrite, "use_placeholder": use_placeholder}.items() if v is not None}))
        outcomes.append(self._apply_settings("OUTPUT", {k: v for k, v in {
            "color_mode": color_mode, "color_depth": color_depth, "compression": compression,
            "quality": quality}.items() if v is not None}))
        ffmpeg, err = self._json_arg(ffmpeg, "ffmpeg")
        if err:
            return err
        if ffmpeg:
            ff = scene.render.ffmpeg
            o = {"set": [], "unset": {}}
            for k, v in ffmpeg.items():
                reason = self._rna_set_validated(ff, k, v)
                (o["set"].append(f"ffmpeg.{k}") if reason is None else o["unset"].__setitem__(f"ffmpeg.{k}", reason))
            outcomes.append(o)
        reply = self._outcome_reply(outcomes, filepath=scene.render.filepath,
                                    file_format=scene.render.image_settings.file_format,
                                    color_mode=scene.render.image_settings.color_mode)
        reply["set"] = done_extra + reply["set"]
        return reply

    def set_color_management(self, view_transform=None, look=None, exposure=None, gamma=None,
                             display_device=None, sequencer_colorspace=None):
        """Scene colour management; enum items read empty headless, so every value is validated by assignment."""
        scene = bpy.context.scene
        outcomes = [self._apply_settings("COLOR", {k: v for k, v in {
            "view_transform": view_transform, "look": look, "exposure": exposure, "gamma": gamma,
            "display_device": display_device}.items() if v is not None})]
        if sequencer_colorspace is not None:
            o = {"set": [], "unset": {}}
            reason = self._rna_set_validated(scene.sequencer_colorspace_settings, "name", sequencer_colorspace)
            (o["set"].append("sequencer_colorspace") if reason is None else o["unset"].__setitem__("sequencer_colorspace", reason))
            outcomes.append(o)
        vs = scene.view_settings
        return self._outcome_reply(outcomes, view_transform=vs.view_transform, look=vs.look,
                                   exposure=vs.exposure, gamma=vs.gamma,
                                   display_device=scene.display_settings.display_device)

    _QUALITY_PRESETS = {
        "PREVIEW": {"resolution_percentage": 25, "samples": 16, "denoise": True, "simplify": True, "persistent": False},
        "DRAFT": {"resolution_percentage": 50, "samples": 64, "denoise": True, "simplify": False, "persistent": False},
        "FINAL": {"resolution_percentage": 100, "samples": None, "denoise": True, "simplify": False, "persistent": True},
    }

    def set_render_quality(self, preset="PREVIEW", engine=None):
        """PREVIEW / DRAFT / FINAL; the previous values are kept in snapshot '_before_quality' for settings_restore."""
        name = str(preset or "").upper()
        if name not in self._QUALITY_PRESETS:
            return {"error": f"preset '{preset}' not valid; valid: {list(self._QUALITY_PRESETS)}"}
        scene = bpy.context.scene
        self._snapshots()["_before_quality"] = self._capture_scopes(("RENDER", "CYCLES", "EEVEE"))
        if engine is not None:
            err = self._set_render_engine(scene.render, engine)
            if err:
                return err
        p = self._QUALITY_PRESETS[name]
        samples = p["samples"]
        if samples is None:
            samples = 256 if scene.render.engine == 'CYCLES' else 64
        outcomes = [self._apply_settings("RENDER", {"resolution_percentage": p["resolution_percentage"],
                                                    "use_simplify": p["simplify"],
                                                    "use_persistent_data": p["persistent"]})]
        if hasattr(scene, "cycles"):
            outcomes.append(self._apply_settings("CYCLES", {"samples": samples, "use_denoising": p["denoise"]}))
        if hasattr(scene, "eevee"):
            outcomes.append(self._apply_settings("EEVEE", {"taa_render_samples": samples}))
        return self._outcome_reply(outcomes, preset=name, engine=scene.render.engine, samples=samples,
                                   resolution_percentage=scene.render.resolution_percentage,
                                   snapshot="_before_quality")

    @staticmethod
    def _cycles_prefs():
        entry = bpy.context.preferences.addons.get("cycles")
        return entry.preferences if entry is not None else None

    def _devices_report(self, prefs):
        devices = []
        with suppress(Exception):
            prefs.get_devices()
        for d in getattr(prefs, "devices", []):
            devices.append({"name": d.name, "type": d.type, "use": bool(d.use)})
        return devices

    def list_render_devices(self):
        """Cycles compute backend, its devices with their use flags, and scene.cycles.device."""
        prefs = self._cycles_prefs()
        if prefs is None:
            return {"error": "Cycles add-on preferences unavailable (add-on disabled?)"}
        scene = bpy.context.scene
        return {"success": True, "backend": prefs.compute_device_type, "devices": self._devices_report(prefs),
                "scene_device": scene.cycles.device if hasattr(scene, "cycles") else None}

    def set_render_device(self, device="GPU", backend=None, persist=False):
        """
        Cycles device: backend (OPTIX / CUDA / HIP / ONEAPI / METAL / NONE, try-assigned: the enum is
        empty headless), then per-device use flags (GPU: every device of that backend on, CPU off;
        CPU: CPU on), then scene.cycles.device. persist=True saves preferences (consent rule).
        """
        prefs = self._cycles_prefs()
        if prefs is None:
            return {"error": "Cycles add-on preferences unavailable (add-on disabled?)"}
        scene = bpy.context.scene
        if not hasattr(scene, "cycles"):
            return {"error": "scene.cycles unavailable"}
        dev = str(device or "").upper()
        if dev not in ("GPU", "CPU"):
            return {"error": f"device '{device}' not valid; valid: ['GPU', 'CPU']"}
        if backend is not None:
            reason = self._rna_set_validated(prefs, "compute_device_type", str(backend).upper())
            if reason:
                return {"error": f"backend '{backend}' not valid: {reason}"}
        with suppress(Exception):
            prefs.get_devices()
        backend_now = prefs.compute_device_type
        for d in getattr(prefs, "devices", []):
            if dev == "GPU":
                d.use = (d.type == backend_now and d.type != 'CPU')
            else:
                d.use = (d.type == 'CPU')
        scene.cycles.device = dev
        persisted, perr = self._persist_prefs(persist)
        reply = {"success": True, "device": dev, "backend": backend_now, "devices": self._devices_report(prefs),
                 "persisted": persisted}
        if dev == "GPU" and not any(x["use"] for x in reply["devices"]):
            reply["warning"] = f"no {backend_now} device enabled; pick a backend that lists devices (list_render_devices)"
        if perr:
            reply["persist_error"] = perr
        return reply

    def set_simplify(self, enabled, subdivision=None, child_particles=None, texture_limit=None, volume_resolution=None):
        scene = bpy.context.scene
        outcomes = [self._apply_settings("RENDER", {k: v for k, v in {
            "use_simplify": bool(enabled), "simplify_subdivision": subdivision,
            "simplify_child_particles": child_particles, "simplify_volumes": volume_resolution}.items() if v is not None})]
        if texture_limit is not None and hasattr(scene, "cycles"):
            outcomes.append(self._apply_settings("CYCLES", {"texture_limit_render": texture_limit,
                                                            "texture_limit": texture_limit}))
        return self._outcome_reply(outcomes, use_simplify=scene.render.use_simplify,
                                   simplify_subdivision=scene.render.simplify_subdivision)

    def set_frame_range(self, start=None, end=None, fps=None, fps_base=None, current=None,
                        frame_start=None, frame_end=None, frame_step=None, current_frame=None):
        """
        Shared with the rigging request (set_scene_frame_range): only sets what was passed.
        start / frame_start, end / frame_end and current / current_frame are synonyms (wire uses
        the long names, the rigging doc the short ones).
        """
        scene = bpy.context.scene
        start = frame_start if start is None else start
        end = frame_end if end is None else end
        current = current_frame if current is None else current
        if start is not None and end is not None and int(start) > int(end):
            return {"error": f"start ({start}) must not exceed end ({end})"}
        outcomes = [self._apply_settings("SCENE", {k: v for k, v in {"frame_start": start, "frame_end": end,
                                                                      "frame_step": frame_step}.items() if v is not None}),
                    self._apply_settings("RENDER", {k: v for k, v in {"fps": fps, "fps_base": fps_base}.items() if v is not None})]
        if current is not None:
            scene.frame_set(int(current))
            outcomes.append({"set": ["frame_current"], "unset": {}})
        return self._outcome_reply(outcomes, frame_start=scene.frame_start, frame_end=scene.frame_end,
                                   frame_step=scene.frame_step, frame_current=scene.frame_current,
                                   fps=scene.render.fps, fps_base=scene.render.fps_base)

    def set_scene_units(self, preset=None, system=None, scale_length=None, length_unit=None, rescale_objects=False,
                        mass_unit=None, time_unit=None, rotation_unit=None):
        """
        Shared with the engine-readiness request. preset UNREAL = METRIC, 0.01, CENTIMETERS;
        UNITY / GODOT / BEVY / TIMBERMESH = METRIC, 1.0, METERS; or explicit system / scale_length /
        length_unit / mass_unit / time_unit / rotation_unit (DEGREES / RADIANS). Reports the rescale
        factor existing objects would need (old / new scale_length) and applies it to root objects
        when rescale_objects=True.
        """
        scene = bpy.context.scene
        units = scene.unit_settings
        if preset is not None:
            name = str(preset).upper()
            if name not in self._UNIT_PRESETS:
                return {"error": f"preset '{preset}' not valid; valid: {list(self._UNIT_PRESETS)}"}
            chosen = self._UNIT_PRESETS[name]
            if chosen is not None:
                system, scale_length, length_unit = chosen
        old_scale = float(units.scale_length)
        outcomes = [self._apply_settings("UNITS", {k: v for k, v in {
            "system": system, "scale_length": scale_length, "length_unit": length_unit,
            "mass_unit": mass_unit, "time_unit": time_unit, "system_rotation": rotation_unit}.items() if v is not None})]
        new_scale = float(units.scale_length)
        factor = old_scale / new_scale if new_scale else 1.0
        roots = [o for o in scene.objects if o.parent is None]
        rescaled = []
        if rescale_objects and abs(factor - 1.0) > 1e-9:
            for o in roots:
                o.scale = [s * factor for s in o.scale]
                o.location = [c * factor for c in o.location]
                rescaled.append(o.name)
        return self._outcome_reply(outcomes, preset=preset, system=units.system, scale_length=units.scale_length,
                                   length_unit=units.length_unit, mass_unit=units.mass_unit,
                                   time_unit=units.time_unit, rotation_unit=units.system_rotation,
                                   rescale_factor=factor,
                                   objects_needing_rescale=[] if abs(factor - 1.0) < 1e-9 else [o.name for o in roots],
                                   rescaled=rescaled)

    def set_viewport_defaults(self, shading=None, light=None, color_type=None, show_overlays=None,
                              show_floor=None, show_stats=None, clip_end=None, lens=None):
        """Apply to every VIEW_3D area in every window (0 areas headless is reported, not an error)."""
        wm = bpy.context.window_manager
        areas = 0
        unset = {}
        for win in (wm.windows if wm else []):
            for area in win.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                space = area.spaces.active
                areas += 1
                for owner, key, value in ((space.shading, "type", shading), (space.shading, "light", light),
                                          (space.shading, "color_type", color_type),
                                          (space.overlay, "show_overlays", show_overlays),
                                          (space.overlay, "show_floor", show_floor),
                                          (space.overlay, "show_stats", show_stats),
                                          (space, "clip_end", clip_end), (space, "lens", lens)):
                    if value is None:
                        continue
                    reason = self._rna_set_validated(owner, key, value)
                    if reason:
                        unset[key] = reason
        reply = {"success": True, "areas": areas}
        if unset:
            reply["unset"] = unset
        return reply

    # ── Presets and profiles ──

    def list_blender_presets(self, category="render"):
        """Blender's own .py presets under bpy.utils.preset_paths(category)."""
        paths = bpy.utils.preset_paths(str(category))
        presets = []
        for folder in paths:
            with suppress(Exception):
                for entry in sorted(os.scandir(folder), key=lambda e: e.name):
                    if entry.is_file() and entry.name.lower().endswith(".py"):
                        presets.append({"name": entry.name[:-3], "path": entry.path})
        if not paths:
            return {"error": f"no preset directory for category '{category}' (try render, cycles/sampling, cycles/viewport)"}
        return {"success": True, "category": category, "paths": list(paths), "presets": presets}

    def apply_blender_preset(self, category, name):
        """Run a Blender .py preset (what script.execute_preset does: the file assigns bpy.context.* values)."""
        listing = self.list_blender_presets(category)
        if "error" in listing:
            return listing
        match = next((p for p in listing["presets"] if p["name"].lower() == str(name).lower()), None)
        if match is None:
            return {"error": f"preset '{name}' not found in {category}; valid: {[p['name'] for p in listing['presets']]}"}
        try:
            bpy.utils.execfile(match["path"])
        except Exception as e:
            return {"error": f"preset {match['path']} failed: {e}"}
        return {"success": True, "category": category, "name": match["name"], "path": match["path"]}

    def set_project_profile(self, engine_target="NONE", export_dir=None, texture_dir=None, render_dir=None,
                            kit_unit=None, naming=None, max_triangles=None, texture_size=None, notes=None,
                            apply_units=False):
        """scene['blendermcp_profile'] (travels with the file) mirrored to <file>.mcp-profile.json when saved."""
        target = str(engine_target or "NONE").upper()
        if target not in self._UNIT_PRESETS:
            return {"error": f"engine_target '{engine_target}' not valid; valid: {list(self._UNIT_PRESETS)}"}
        scene = bpy.context.scene
        profile = {"engine_target": target}
        for key, value in (("export_dir", export_dir), ("texture_dir", texture_dir), ("render_dir", render_dir),
                           ("kit_unit", kit_unit), ("naming", naming), ("max_triangles", max_triangles),
                           ("texture_size", texture_size), ("notes", notes)):
            if value is not None:
                profile[key] = value
        scene[self._PROFILE_KEY] = profile
        reply = {"success": True, "profile": profile}
        if apply_units and self._UNIT_PRESETS.get(target):
            reply["units"] = self.set_scene_units(preset=target)
        if bpy.data.filepath:
            sidecar = self._sidecar_path(os.path.abspath(bpy.data.filepath), ".mcp-profile.json")
            try:
                with open(sidecar, "w", encoding="utf-8") as f:
                    json.dump(profile, f, indent=2)
                reply["sidecar"] = sidecar
            except Exception as e:
                reply["sidecar_error"] = str(e)
        return reply

    def get_project_profile(self, apply_units=False):
        scene = bpy.context.scene
        raw = scene.get(self._PROFILE_KEY)
        if raw is None:
            return {"success": True, "profile": None}
        profile = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        reply = {"success": True, "profile": profile}
        target = str(profile.get("engine_target", "NONE")).upper()
        if apply_units and self._UNIT_PRESETS.get(target):
            reply["units"] = self.set_scene_units(preset=target)
        return reply

    # ── Add-ons, workspaces, preferences ──

    def list_addons(self, enabled_only=False, filter=None):
        import addon_utils
        enabled = set(bpy.context.preferences.addons.keys())
        out = []
        needle = str(filter).lower() if filter else None
        for mod in addon_utils.modules(refresh=False):
            info = addon_utils.module_bl_info(mod)
            name = mod.__name__
            if enabled_only and name not in enabled:
                continue
            if needle and needle not in name.lower() and needle not in str(info.get("name", "")).lower():
                continue
            entry = bpy.context.preferences.addons.get(name)
            out.append({"module": name, "name": info.get("name"), "version": list(info.get("version", ()) or ()),
                        "category": info.get("category"), "enabled": name in enabled,
                        "has_preferences": bool(entry is not None and entry.preferences is not None),
                        "path": getattr(mod, "__file__", None)})
        return {"success": True, "count": len(out), "addons": out}

    def enable_addon(self, module, persist=False):
        """addon_utils.enable(module, default_set=True) (C13); a failed enable is unwound so a retry works."""
        import addon_utils
        errors = []
        try:
            mod = addon_utils.enable(module, default_set=True, handle_error=lambda ex: errors.append(str(ex)))
        except Exception as e:
            mod = None
            errors.append(str(e))
        if mod is None:
            with suppress(Exception):
                addon_utils.disable(module, default_set=True)
            return {"error": f"could not enable '{module}': {'; '.join(errors) or 'unknown module'}"}
        info = addon_utils.module_bl_info(mod)
        persisted, perr = self._persist_prefs(persist)
        reply = {"success": True, "module": module, "bl_info": {k: (list(v) if isinstance(v, tuple) else v) for k, v in info.items()},
                 "enabled": module in bpy.context.preferences.addons, "preferences_dirty": bool(bpy.context.preferences.is_dirty),
                 "persisted": persisted}
        if perr:
            reply["persist_error"] = perr
        return reply

    def disable_addon(self, module, persist=False):
        import addon_utils
        if module not in bpy.context.preferences.addons:
            return {"error": f"'{module}' is not enabled; enabled: {sorted(bpy.context.preferences.addons.keys())}"}
        errors = []
        try:
            addon_utils.disable(module, default_set=True, handle_error=lambda ex: errors.append(str(ex)))
        except Exception as e:
            errors.append(str(e))
        persisted, perr = self._persist_prefs(persist)
        reply = {"success": True, "module": module, "enabled": module in bpy.context.preferences.addons,
                 "preferences_dirty": bool(bpy.context.preferences.is_dirty), "persisted": persisted}
        if errors:
            reply["warnings"] = errors
        if perr:
            reply["persist_error"] = perr
        return reply

    def get_addon_preferences(self, module):
        return self.get_settings(f"ADDON:{module}")

    def set_addon_preferences(self, module, values, persist=False):
        return self.set_settings(f"ADDON:{module}", values, persist=persist)

    def save_preferences(self, confirm=False):
        """wm.save_userpref; refuses without confirm=True (consent rule)."""
        prefs = bpy.context.preferences
        if not confirm:
            return {"error": "save_preferences writes userpref.blend; pass confirm=True to do it",
                    "preferences_dirty": bool(prefs.is_dirty), "use_preferences_save": bool(prefs.use_preferences_save)}
        persisted, perr = self._persist_prefs(True)
        if perr:
            return {"error": f"save_userpref failed: {perr}"}
        return {"success": True, "persisted": persisted, "preferences_dirty": bool(prefs.is_dirty),
                "use_preferences_save": bool(prefs.use_preferences_save)}

    @staticmethod
    def _gui_window():
        """The GUI window; bpy.context.window is None in a timer right after wm.open_mainfile
        (session restore at launch reported a GUI session as headless, 2026-09-11)."""
        win = bpy.context.window
        if win is None and not bpy.app.background:
            wins = bpy.context.window_manager.windows
            win = wins[0] if len(wins) else None
        return win

    def list_workspaces(self):
        win = self._gui_window()
        current = win.workspace.name if win and win.workspace else None
        out = [{"name": ws.name, "areas": sorted({a.type for s in ws.screens for a in s.areas})}
               for ws in bpy.data.workspaces]
        return {"success": True, "current": current, "workspaces": out}

    def set_workspace(self, name):
        ws = bpy.data.workspaces.get(name)
        if ws is None:
            return {"error": f"workspace '{name}' not found; valid: {[w.name for w in bpy.data.workspaces]}"}
        win = self._gui_window()
        if win is None:
            return {"error": "no window in this session (headless); workspaces need a GUI session"}
        try:
            win.workspace = ws
        except Exception as e:
            return {"error": f"could not switch workspace: {e}"}
        return {"success": True, "workspace": win.workspace.name,
                "areas": sorted({a.type for a in win.screen.areas})}

    def get_addon_settings(self):
        """The MCP add-on's own preferences; keys are reported as set/unset, never echoed."""
        prefs = _prefs()
        if prefs is None:
            return {"success": True, "available": False, "port": _port(), "autostart_server": None,
                    "keys": {n: bool(_secret(n)) for n in _SECRET_NAMES},
                    "note": "add-on preferences unavailable (not in preferences.addons: headless import); defaults reported"}
        return {"success": True, "available": True, "port": int(prefs.port),
                "autostart_server": bool(prefs.autostart_server),
                "keys": {n: bool(getattr(prefs, n, "")) for n in _SECRET_NAMES},
                "server_running": _server_running()}

    def set_addon_settings(self, values, persist=False):
        values, err = self._json_arg(values, "values")
        if err:
            return err
        if not isinstance(values, dict):
            return {"error": "values must be a JSON object"}
        prefs = _prefs()
        if prefs is None:
            return {"error": "add-on preferences unavailable in this session (module not in preferences.addons)"}
        allowed = ("port", "autostart_server") + _SECRET_NAMES
        unknown = [k for k in values if k not in allowed]
        if unknown:
            return {"error": f"unknown keys {unknown}; valid: {list(allowed)}"}
        outcome = self._apply_settings("MCP", values)
        persisted, perr = self._persist_prefs(persist)
        reply = {"success": True, "set": outcome["set"], "port": int(prefs.port),
                 "autostart_server": bool(prefs.autostart_server),
                 "keys": {n: bool(getattr(prefs, n, "")) for n in _SECRET_NAMES}, "persisted": persisted}
        if outcome["unset"]:
            reply["unset"] = outcome["unset"]
        if perr:
            reply["persist_error"] = perr
        if "port" in outcome["set"] and _server_running():
            reply["note"] = "port changes apply when the server is restarted (Disconnect / Connect)"
        return reply

    # ── Session state ──

    _SESSION_PARTS = ("FILE", "FRAME", "CAMERA", "SELECTION", "ACTIVE", "MODE", "VIEWPORT", "WORKSPACE", "SETTINGS_SNAPSHOTS")

    @staticmethod
    def _session_states():
        return bpy.app.driver_namespace.setdefault("blendermcp_session_states", {})

    def save_session_state(self, name="last", include=None):
        """Capture the session as a dict (the server persists it); also kept in this session under name."""
        parts = self._scope_list(include, self._SESSION_PARTS)
        bad = [p for p in parts if p not in self._SESSION_PARTS]
        if bad:
            return {"error": f"unknown include parts {bad}; valid: {list(self._SESSION_PARTS)}"}
        scene = bpy.context.scene
        vl = bpy.context.view_layer
        state = {"name": name, "saved_at": datetime.now().isoformat(timespec="seconds"),
                 "blender": bpy.app.version_string}
        if "FILE" in parts:
            state["file"] = {"filepath": bpy.data.filepath, "is_dirty": bool(bpy.data.is_dirty)}
        if "FRAME" in parts:
            state["frame"] = {"current": scene.frame_current, "start": scene.frame_start, "end": scene.frame_end}
        if "CAMERA" in parts:
            state["camera"] = scene.camera.name if scene.camera else None
        if "SELECTION" in parts:
            state["selection"] = [o.name for o in vl.objects if o.select_get()]
        if "ACTIVE" in parts:
            state["active"] = vl.objects.active.name if vl.objects.active else None
        if "MODE" in parts:
            state["mode"] = bpy.context.mode
        if "VIEWPORT" in parts:
            views = []
            wm = bpy.context.window_manager
            for win in (wm.windows if wm else []):
                for area in win.screen.areas:
                    if area.type == 'VIEW_3D':
                        sp = area.spaces.active
                        r3d = sp.region_3d
                        views.append({"view_matrix": [list(row) for row in r3d.view_matrix],
                                      "view_distance": r3d.view_distance, "view_perspective": r3d.view_perspective,
                                      "shading": sp.shading.type, "show_overlays": sp.overlay.show_overlays})
            state["viewports"] = views
        if "WORKSPACE" in parts:
            win = bpy.context.window
            state["workspace"] = win.workspace.name if win and win.workspace else None
        if "SETTINGS_SNAPSHOTS" in parts:
            state["settings_snapshots"] = {n: d for n, d in self._snapshots().items() if not n.startswith("_")}
        self._session_states()[name] = state
        return {"success": True, "name": name, "state": state}

    def get_session_state(self, include=None, name="last"):
        """Server-facing alias of save_session_state: the reply's "state" dict is what the server persists."""
        return self.save_session_state(name=name, include=include)

    def apply_session_state(self, state, load_file=False, force=False):
        """Server-facing alias of restore_session_state for a state dict the server persisted (file already reopened by the server)."""
        if state is None:
            return {"error": "state (the persisted session dict) is required"}
        return self.restore_session_state(state=state, load_file=load_file, force=force)

    def restore_session_state(self, name="last", state=None, load_file=True, force=False):
        """Apply a session state (dict from the server, or the one saved under name); reports what could not be restored."""
        state, err = self._json_arg(state, "state")
        if err:
            return err
        if state is None:
            state = self._session_states().get(name)
            if state is None:
                return {"error": f"no session state named '{name}' in this session; pass state= from the server file"}
        not_restored = []
        loaded = False
        fp = (state.get("file") or {}).get("filepath")
        if load_file and fp:
            if not os.path.exists(fp):
                not_restored.append(f"file: {fp} not found")
            elif os.path.abspath(fp) != os.path.abspath(bpy.data.filepath or ""):
                r = self.load_blend(fp, force=force)
                if "error" in r:
                    return r
                loaded = True
        scene = bpy.context.scene
        vl = bpy.context.view_layer
        if "frame" in state:
            fr = state["frame"]
            with suppress(Exception):
                scene.frame_start, scene.frame_end = int(fr["start"]), int(fr["end"])
            with suppress(Exception):
                scene.frame_set(int(fr["current"]))
        if "camera" in state:
            cam = bpy.data.objects.get(state["camera"]) if state["camera"] else None
            if state["camera"] and cam is None:
                not_restored.append(f"camera: {state['camera']} missing")
            else:
                scene.camera = cam
        if "selection" in state:
            with suppress(Exception):
                if bpy.context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
            names = set(state["selection"])
            for o in vl.objects:
                with suppress(Exception):
                    o.select_set(o.name in names)
            for n in names - set(vl.objects.keys()):
                not_restored.append(f"selection: {n} missing")
        if "active" in state:
            a = bpy.data.objects.get(state["active"]) if state["active"] else None
            if state["active"] and (a is None or a.name not in vl.objects):
                not_restored.append(f"active: {state['active']} missing")
            else:
                vl.objects.active = a
        if "mode" in state and state["mode"] and state["mode"] != bpy.context.mode:
            target = {"EDIT_MESH": "EDIT", "EDIT_ARMATURE": "EDIT", "POSE": "POSE", "SCULPT": "SCULPT",
                      "OBJECT": "OBJECT"}.get(state["mode"], "OBJECT")
            try:
                if vl.objects.active is not None:
                    bpy.ops.object.mode_set(mode=target)
            except Exception as e:
                not_restored.append(f"mode: {e}")
        if "viewports" in state and state["viewports"]:
            wm = bpy.context.window_manager
            areas = [a for win in (wm.windows if wm else []) for a in win.screen.areas if a.type == 'VIEW_3D']
            if not areas:
                not_restored.append("viewports: no VIEW_3D in this session")
            for area, saved in zip(areas, state["viewports"]):
                sp = area.spaces.active
                with suppress(Exception):
                    sp.region_3d.view_matrix = mathutils.Matrix(saved["view_matrix"])
                    sp.region_3d.view_distance = saved["view_distance"]
                    sp.region_3d.view_perspective = saved["view_perspective"]
                    sp.shading.type = saved["shading"]
                    sp.overlay.show_overlays = saved["show_overlays"]
        if "workspace" in state and state["workspace"]:
            r = self.set_workspace(state["workspace"])
            if "error" in r:
                not_restored.append(f"workspace: {r['error']}")
        if "settings_snapshots" in state:
            self._snapshots().update(state["settings_snapshots"] or {})
        reply = {"success": True, "name": state.get("name", name), "file_loaded": loaded,
                 "frame_current": scene.frame_current, "active": vl.objects.active.name if vl.objects.active else None,
                 "selection": [o.name for o in vl.objects if o.select_get()]}
        if not_restored:
            reply["not_restored"] = not_restored
        return reply

    # ─── Rigging (B1 Tier 1): armatures, skinning, pose, constraints ─────────

    _BIND_METHODS = {"AUTO": "ARMATURE_AUTO", "ENVELOPE": "ARMATURE_ENVELOPE",
                     "EMPTY_GROUPS": "ARMATURE_NAME", "NAME": "ARMATURE"}

    @staticmethod
    def _names_arg(value):
        """Comma string / list / None -> list of names or None."""
        if value is None:
            return None
        if isinstance(value, str):
            return [n.strip() for n in value.split(",") if n.strip()]
        return [str(n) for n in value]

    @staticmethod
    def _glob_names(pattern_list, names):
        """
        Expand pattern_list against names; returns (matched, unmatched_patterns). R-B2-41: an item that IS
        a name matches exactly and only itself ("Panel[1]", "Star*Prop"); fnmatch runs only when no name
        equals the item.
        """
        import fnmatch
        matched, unmatched = [], []
        for pat in pattern_list:
            hits = [pat] if pat in names else [n for n in names if fnmatch.fnmatchcase(n, pat)]
            if hits:
                matched += [h for h in hits if h not in matched]
            else:
                unmatched.append(pat)
        return matched, unmatched

    @staticmethod
    def _vec3(value, label):
        try:
            v = [float(x) for x in value]
            if len(v) != 3:
                raise ValueError
            return v, None
        except Exception:
            return None, f"{label} must be 3 numbers, got {value!r}"

    def _apply_bone_spec(self, edit_bones, spec, created, snapped, warnings):
        """One bone from an add_bones JSON entry; returns the created name or an error string."""
        import math
        name = str(spec.get("name") or "").strip()
        if not name:
            return None, "every bone needs a name"
        head, err = self._vec3(spec.get("head", (0, 0, 0)), f"{name}.head")
        if err:
            return None, err
        tail, err = self._vec3(spec.get("tail", (0, 0, 1)), f"{name}.tail")
        if err:
            return None, err
        eb = edit_bones.new(name)
        if eb.name != name:
            warnings.append(f"bone '{name}' already existed; created '{eb.name}'")
        eb.head, eb.tail = head, tail
        parent = spec.get("parent")
        if parent:
            pb = edit_bones.get(created.get(parent, parent))
            if pb is None:
                edit_bones.remove(eb)
                return None, f"bone '{name}': parent '{parent}' not found (parents must exist or come earlier in the list)"
            eb.parent = pb
            if spec.get("connected"):
                if (mathutils.Vector(head) - pb.tail).length > 1e-6:
                    snapped.append(eb.name)
                eb.head = pb.tail.copy()
                eb.use_connect = True
        elif spec.get("connected"):
            warnings.append(f"bone '{name}': connected=True ignored (no parent)")
        if "roll" in spec and spec["roll"] is not None:
            eb.roll = math.radians(float(spec["roll"]))
        if "deform" in spec and spec["deform"] is not None:
            eb.use_deform = bool(spec["deform"])
        if "inherit_rotation" in spec and spec["inherit_rotation"] is not None:
            eb.use_inherit_rotation = bool(spec["inherit_rotation"])
        if (eb.tail - eb.head).length < 1e-6:
            edit_bones.remove(eb)
            return None, f"bone '{name}': head and tail coincide (zero-length bones are not allowed)"
        created[name] = eb.name
        return eb.name, None

    def add_bones(self, armature, bones):
        """
        Batch-create bones in one EDIT session. bones: JSON list of
        {name, head:[x,y,z], tail:[x,y,z], parent?, connected?, roll? (deg), deform?, inherit_rotation?}.
        A parent may be earlier in the same list. connected=True snaps head to the parent's tail
        (reported under "snapped"). Name collisions get Blender's .001 suffix (reported).
        Reply: created names (in order), snapped, warnings. EditBone refs never leave this call.
        """
        arm, err = self._get_armature(armature)
        if err:
            return err
        specs, jerr = self._json_arg(bones, "bones")
        if jerr:
            return jerr
        if isinstance(specs, dict):
            specs = [specs]
        if not isinstance(specs, list) or not specs:
            return {"error": "bones must be a non-empty JSON list of {name, head, tail, ...}"}
        created, snapped, warnings, names = {}, [], [], []
        with self._armature_edit(arm) as edit_bones:
            for spec in specs:
                if not isinstance(spec, dict):
                    return {"error": f"bone entries must be objects, got {spec!r}"}
                new_name, berr = self._apply_bone_spec(edit_bones, spec, created, snapped, warnings)
                if berr:
                    return {"error": berr, "created_before_error": names}
                names.append(new_name)
        reply = {"success": True, "armature": arm.name, "created": names, "bone_count": len(arm.data.bones),
                 "snapped": snapped}
        if warnings:
            reply["warnings"] = warnings
        return reply

    def create_armature(self, name="Armature", location=None, display_type="OCTAHEDRAL", show_in_front=True, bones=None):
        """Create armature data + object in the active collection; optional bones JSON as in add_bones."""
        dtype = str(display_type or "OCTAHEDRAL").upper()
        valid = [e.identifier for e in bpy.types.Armature.bl_rna.properties["display_type"].enum_items]
        if dtype not in valid:
            return {"error": f"display_type '{display_type}' not valid; valid: {valid}"}
        loc = None
        if location is not None:
            if isinstance(location, str):
                location = [p for p in location.split(",") if p.strip()]
            loc, err = self._vec3(location, "location")
            if err:
                return {"error": err}
        data = bpy.data.armatures.new(name)
        data.display_type = dtype
        obj = bpy.data.objects.new(name, data)
        obj.show_in_front = bool(show_in_front)
        if loc:
            obj.location = loc
        target = bpy.context.collection or bpy.context.scene.collection
        target.objects.link(obj)
        reply = {"success": True, "name": obj.name, "data": data.name, "bone_count": 0,
                 "collection": target.name, "display_type": dtype}
        if bones is not None:
            r = self.add_bones(obj.name, bones)
            if "error" in r:
                return {"error": f"armature '{obj.name}' created but bones failed: {r['error']}", "name": obj.name}
            reply.update({"bone_count": r["bone_count"], "created": r["created"], "snapped": r["snapped"]})
            if r.get("warnings"):
                reply["warnings"] = r["warnings"]
        return reply

    @staticmethod
    def _constraint_summary(con):
        d = {"name": con.name, "type": con.type, "influence": round(con.influence, 4), "mute": bool(con.mute)}
        for key in ("target", "pole_target"):
            if hasattr(con, key):
                v = getattr(con, key)
                d[key] = v.name if v is not None else None
        for key in ("subtarget", "pole_subtarget", "chain_count", "use_tail", "iterations", "use_stretch",
                    "track_axis", "up_axis", "mix_mode", "owner_space", "target_space"):
            if hasattr(con, key):
                d[key] = getattr(con, key)
        if hasattr(con, "pole_angle"):
            import math
            d["pole_angle_deg"] = round(math.degrees(con.pole_angle), 4)
        return d

    def get_armature_info(self, armature, include_pose=False, space="WORLD", bone_filter=None):
        """
        THE perceive tool for rigs. Bones in hierarchy order with parent, children, head, tail,
        length, roll (deg), connected, deform, bone_collections, constraints; plus pose_position,
        display_type, action and the bone-collection summary. include_pose adds the pose transform
        per bone. space WORLD (matrix_world applied) or ARMATURE (rest-space).
        """
        import math
        arm, err = self._get_armature(armature)
        if err:
            return err
        sp = str(space or "WORLD").upper()
        if sp not in ("WORLD", "ARMATURE"):
            return {"error": f"space '{space}' not valid; valid: ['WORLD', 'ARMATURE']"}
        mw = arm.matrix_world
        conv = (lambda v: list(mw @ v)) if sp == "WORLD" else (lambda v: list(v))
        names = [b.name for b in arm.data.bones]
        if bone_filter:
            names, _ = self._glob_names(self._names_arg(bone_filter), names)
        wanted = set(names)
        ordered = []

        def walk(bone):
            if bone.name in wanted:
                ordered.append(bone)
            for child in bone.children:
                walk(child)
        for root in (b for b in arm.data.bones if b.parent is None):
            walk(root)
        bones = []
        for b in ordered:
            pb = arm.pose.bones.get(b.name)
            entry = {"name": b.name, "parent": b.parent.name if b.parent else None,
                     "children": [c.name for c in b.children],
                     "head": [round(x, 5) for x in conv(b.head_local)], "tail": [round(x, 5) for x in conv(b.tail_local)],
                     "length": round(b.length, 5), "roll": None, "connected": bool(b.use_connect),
                     "deform": bool(b.use_deform), "bone_collections": [c.name for c in b.collections],
                     "constraints": [self._constraint_summary(c) for c in pb.constraints] if pb else []}
            with suppress(Exception):
                # Bone.roll is only exposed on EditBone; recover it from the rest matrix along the bone axis
                axis = (b.tail_local - b.head_local).normalized()
                _m, roll = bpy.types.Bone.AxisRollFromMatrix(b.matrix_local.to_3x3(), axis=axis)
                entry["roll"] = round(math.degrees(roll), 4)
            if include_pose and pb is not None:
                rot = list(pb.rotation_quaternion) if pb.rotation_mode == 'QUATERNION' else \
                    ([math.degrees(a) for a in pb.rotation_euler] if pb.rotation_mode != 'AXIS_ANGLE' else list(pb.rotation_axis_angle))
                entry["pose"] = {"location": list(pb.location), "rotation_mode": pb.rotation_mode,
                                 "rotation": [round(x, 5) for x in rot], "scale": list(pb.scale),
                                 "matrix_world_head": [round(x, 5) for x in (mw @ pb.head)],
                                 "matrix_world_tail": [round(x, 5) for x in (mw @ pb.tail)]}
            bones.append(entry)
        ad = arm.animation_data
        return {"success": True, "armature": arm.name, "uid": arm.session_uid, "bone_count": len(arm.data.bones), "bones": bones,
                "space": sp, "pose_position": arm.data.pose_position, "display_type": arm.data.display_type,
                "show_in_front": arm.show_in_front, "action": ad.action.name if ad and ad.action else None,
                "bone_collections": self._bone_collections(arm.data)}

    def set_bone_properties(self, armature, bone, head=None, tail=None, roll=None, parent=None, connected=None,
                            deform=None, inherit_rotation=None, inherit_scale=None, new_name=None,
                            envelope_distance=None, bbone_segments=None):
        """Only sets what was passed (EDIT mode). Reply: set list, unset {prop: reason}, name (after rename)."""
        import math
        arm, err = self._get_armature(armature)
        if err:
            return err
        if bone not in arm.data.bones:
            return {"error": f"Bone not found: {bone}; bones: {[b.name for b in arm.data.bones][:50]}"}
        done, unset = [], {}
        final_name = bone
        with self._armature_edit(arm) as edit_bones:
            eb = edit_bones.get(bone)
            if eb is None:
                return {"error": f"Bone not found in edit mode: {bone}"}
            for key, value in (("head", head), ("tail", tail)):
                if value is not None:
                    v, e = self._vec3(value, key)
                    if e:
                        unset[key] = e
                    else:
                        setattr(eb, key, v)
                        done.append(key)
            if roll is not None:
                eb.roll = math.radians(float(roll)); done.append("roll")
            if parent is not None:
                if parent == "":
                    eb.parent = None; done.append("parent")
                else:
                    p = edit_bones.get(parent)
                    if p is None:
                        unset["parent"] = f"'{parent}' not found"
                    elif p == eb:
                        unset["parent"] = "a bone cannot parent itself"
                    else:
                        eb.parent = p; done.append("parent")
            if connected is not None:
                if eb.parent is None and connected:
                    unset["connected"] = "no parent to connect to"
                else:
                    eb.use_connect = bool(connected); done.append("connected")
            for key, attr, value in (("deform", "use_deform", deform), ("inherit_rotation", "use_inherit_rotation", inherit_rotation),
                                     ("inherit_scale", "inherit_scale", inherit_scale),
                                     ("envelope_distance", "envelope_distance", envelope_distance),
                                     ("bbone_segments", "bbone_segments", bbone_segments)):
                if value is None:
                    continue
                reason = self._rna_set_validated(eb, attr, value)
                if reason is None:
                    done.append(key)
                else:
                    unset[key] = reason
            if (eb.tail - eb.head).length < 1e-6:
                unset["tail"] = "head and tail coincide; tail moved back"; eb.tail = eb.head + mathutils.Vector((0, 0, 0.1))
            if new_name:
                eb.name = str(new_name)
                final_name = eb.name
                done.append("new_name")
        reply = {"success": True, "armature": arm.name, "bone": final_name, "set": done}
        if unset:
            reply["unset"] = unset
        return reply

    def delete_bones(self, armature, bones, reparent_children=True):
        """Delete bones by name or glob in one EDIT session; children are re-parented to the deleted bone's parent by default."""
        arm, err = self._get_armature(armature)
        if err:
            return err
        patterns = self._names_arg(bones) or []
        if not patterns:
            return {"error": "bones is required (comma list or glob)"}
        names = [b.name for b in arm.data.bones]
        targets, unmatched = self._glob_names(patterns, names)
        if not targets:
            return {"error": f"no bone matches {patterns}; bones: {names[:50]}"}
        deleted, reparented = [], []
        with self._armature_edit(arm) as edit_bones:
            for name in targets:
                eb = edit_bones.get(name)
                if eb is None:
                    continue
                grand = eb.parent
                for child in list(eb.children):
                    if reparent_children:
                        child.parent = grand
                        child.use_connect = False
                        reparented.append(child.name)
                    else:
                        child.parent = None
                edit_bones.remove(eb)
                deleted.append(name)
        reply = {"success": True, "armature": arm.name, "deleted": deleted, "reparented": reparented,
                 "bone_count": len(arm.data.bones)}
        if unmatched:
            reply["unmatched"] = unmatched
        return reply

    def bind_armature(self, mesh, armature, method="AUTO", keep_transform=True):
        """
        parent_set with the mesh selected and the armature ACTIVE (OBJECT mode). method AUTO ->
        ARMATURE_AUTO, ENVELOPE -> ARMATURE_ENVELOPE, EMPTY_GROUPS -> ARMATURE_NAME, NAME -> ARMATURE
        (modifier only, keeps existing groups). The previous active object and selection are restored.
        """
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        arm, err = self._get_armature(armature)
        if err:
            return err
        meth = str(method or "AUTO").upper()
        if meth not in self._BIND_METHODS:
            return {"error": f"method '{method}' not valid; valid: {list(self._BIND_METHODS)}"}
        with self._selection_scope(), self._mode_restore():
            self._ensure_object_mode(m_obj)
            self._ensure_object_mode(arm)
            for o in bpy.context.view_layer.objects:
                o.select_set(False)
            m_obj.select_set(True)
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            try:
                result = bpy.ops.object.parent_set(type=self._BIND_METHODS[meth], keep_transform=bool(keep_transform))
            except Exception as e:
                return {"error": f"parent_set({self._BIND_METHODS[meth]}) failed: {e}"}
            if 'FINISHED' not in result:
                return {"error": f"parent_set returned {set(result)}"}
        mod = next((m for m in m_obj.modifiers if m.type == 'ARMATURE' and m.object == arm), None)
        empty = []
        counts = {}
        for vg in m_obj.vertex_groups:
            n = 0
            for v in m_obj.data.vertices:
                if any(g.group == vg.index and g.weight > 0.0 for g in v.groups):
                    n += 1
            counts[vg.name] = n
            if n == 0:
                empty.append(vg.name)
        return {"success": True, "mesh": m_obj.name, "armature": arm.name, "method": meth,
                "parent_type": self._BIND_METHODS[meth], "modifier": mod.name if mod else None,
                "group_count": len(m_obj.vertex_groups), "groups": counts, "empty_groups": empty,
                "parent": m_obj.parent.name if m_obj.parent else None}

    def _bound_armature(self, m_obj):
        mod = next((m for m in m_obj.modifiers if m.type == 'ARMATURE' and m.object), None)
        return mod.object if mod else None

    def get_vertex_groups(self, mesh, include_stats=True):
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        arm = self._bound_armature(m_obj)
        bone_names = {b.name for b in arm.data.bones} if arm else set()
        groups = []
        per_group = {vg.index: [] for vg in m_obj.vertex_groups}
        if include_stats:
            for v in m_obj.data.vertices:
                for g in v.groups:
                    if g.group in per_group:
                        per_group[g.group].append(g.weight)
        for vg in m_obj.vertex_groups:
            entry = {"name": vg.name, "index": vg.index, "lock": bool(vg.lock_weight), "has_bone": vg.name in bone_names}
            if include_stats:
                w = per_group[vg.index]
                entry.update({"vertex_count": len(w), "weight_min": round(min(w), 5) if w else None,
                              "weight_max": round(max(w), 5) if w else None,
                              "weight_mean": round(sum(w) / len(w), 5) if w else None})
            groups.append(entry)
        return {"success": True, "mesh": m_obj.name, "armature": arm.name if arm else None,
                "group_count": len(groups), "groups": groups}

    def get_vertex_weights(self, mesh, indices=None, group=None, max_verts=2000):
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        idx_list = self._names_arg(indices)
        if idx_list is not None:
            try:
                idx_list = [int(i) for i in idx_list]
            except ValueError:
                return {"error": f"indices must be integers, got {indices!r}"}
            ierr = self._check_indices(idx_list, len(m_obj.data.vertices), "Vertex")
            if ierr:
                return ierr
        vg_filter = None
        if group is not None:
            vg = m_obj.vertex_groups.get(group)
            if vg is None:
                return {"error": f"vertex group '{group}' not found; groups: {[g.name for g in m_obj.vertex_groups]}"}
            vg_filter = vg.index
        names = {vg.index: vg.name for vg in m_obj.vertex_groups}
        verts = m_obj.data.vertices if idx_list is None else [m_obj.data.vertices[i] for i in idx_list]
        out, truncated = {}, False
        for v in verts:
            entry = {names[g.group]: round(g.weight, 5) for g in v.groups
                     if g.group in names and (vg_filter is None or g.group == vg_filter)}
            if vg_filter is not None and not entry:
                continue
            if len(out) >= int(max_verts):
                truncated = True
                break
            out[v.index] = entry
        return {"success": True, "mesh": m_obj.name, "weights": out, "count": len(out), "truncated": truncated}

    def set_vertex_weights(self, mesh, group, weights, mode="REPLACE", create_group=True):
        """weights: JSON {index: weight} or [[index, weight], ...]; mode REPLACE / ADD / SUBTRACT (VertexGroup.add)."""
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        md = str(mode or "REPLACE").upper()
        if md not in ("REPLACE", "ADD", "SUBTRACT"):
            return {"error": f"mode '{mode}' not valid; valid: ['REPLACE', 'ADD', 'SUBTRACT']"}
        data, jerr = self._json_arg(weights, "weights")
        if jerr:
            return jerr
        pairs = []
        try:
            if isinstance(data, dict):
                pairs = [(int(k), float(v)) for k, v in data.items()]
            else:
                pairs = [(int(p[0]), float(p[1])) for p in data]
        except Exception:
            return {"error": "weights must be {index: weight} or [[index, weight], ...]"}
        if not pairs:
            return {"error": "weights is empty"}
        ierr = self._check_indices([i for i, _ in pairs], len(m_obj.data.vertices), "Vertex")
        if ierr:
            return ierr
        vg = m_obj.vertex_groups.get(group)
        created = False
        if vg is None:
            if not create_group:
                return {"error": f"vertex group '{group}' not found and create_group=False; groups: {[g.name for g in m_obj.vertex_groups]}"}
            vg = m_obj.vertex_groups.new(name=group)
            created = True
        self._ensure_object_mode(m_obj)
        for i, w in pairs:
            vg.add([i], w, md)
        return {"success": True, "mesh": m_obj.name, "group": vg.name, "written": len(pairs), "mode": md,
                "group_created": created}

    def find_unweighted_vertices(self, mesh, tolerance=0.001, render=False, angle="front"):
        """Verts whose total DEFORM weight is below tolerance, plus any weight > 1 or < 0. render=True highlights them in a capture."""
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        arm = self._bound_armature(m_obj)
        deform = {vg.index for vg in m_obj.vertex_groups
                  if arm is None or (vg.name in arm.data.bones and arm.data.bones[vg.name].use_deform)}
        unweighted, out_of_range = [], []
        for v in m_obj.data.vertices:
            total = sum(g.weight for g in v.groups if g.group in deform)
            if total < float(tolerance):
                unweighted.append(v.index)
            if any(g.weight > 1.0 + 1e-6 or g.weight < 0.0 for g in v.groups):
                out_of_range.append(v.index)
        reply = {"success": True, "mesh": m_obj.name, "armature": arm.name if arm else None,
                 "vertex_count": len(m_obj.data.vertices), "unweighted_count": len(unweighted),
                 "unweighted": unweighted[:500], "out_of_range_count": len(out_of_range),
                 "out_of_range": out_of_range[:500], "deform_groups": sorted(m_obj.vertex_groups[i].name for i in deform)}
        if render:
            # The user's mesh element selection is restored after the mode is back (A1.3)
            with self._selection_scope(), self._mesh_select_scope(m_obj.data), self._mode_restore():
                self._select_only(m_obj)
                # Edge and face flags are separate from vertex flags: a stale face selection
                # would draw the faces highlighted in the capture (seen live 2026-09-11)
                for e in m_obj.data.edges:
                    e.select = False
                for p in m_obj.data.polygons:
                    p.select = False
                wanted = set(unweighted)
                for v in m_obj.data.vertices:
                    v.select = v.index in wanted
                bpy.ops.object.mode_set(mode='EDIT')
                cap = self.capture_viewport_angle(angle=angle)
                with suppress(Exception):
                    bpy.ops.object.mode_set(mode='OBJECT')
            reply["image"] = cap
        return reply

    def render_weight_map(self, mesh, group, angle="front", max_size=800, show_zero_weights=True):
        """WEIGHT_PAINT capture of one vertex group (viewport tool: needs a GUI session; headless returns the capture error)."""
        m_obj, err = self._get_mesh(mesh)
        if err:
            return err
        vg = m_obj.vertex_groups.get(group)
        if vg is None:
            return {"error": f"vertex group '{group}' not found; groups: {[g.name for g in m_obj.vertex_groups]}"}
        prefs_view = bpy.context.preferences.view
        saved_zero = getattr(prefs_view, "show_zero_weights", None) if hasattr(prefs_view, "show_zero_weights") else None
        with self._selection_scope(), self._mode_restore():
            self._select_only(m_obj)
            m_obj.vertex_groups.active_index = vg.index
            try:
                if hasattr(prefs_view, "show_zero_weights"):
                    prefs_view.show_zero_weights = 'ALL' if show_zero_weights else 'NONE'
                cap = self.capture_viewport_angle(angle=angle, max_size=max_size, overlay="weight_paint")
            finally:
                if saved_zero is not None:
                    with suppress(Exception):
                        prefs_view.show_zero_weights = saved_zero
        if "error" in cap:
            return {"error": cap["error"], "mesh": m_obj.name, "group": vg.name}
        cap.update({"mesh": m_obj.name, "group": vg.name})
        return cap

    # ── Pose ──

    def _pose_rotation(self, pb):
        import math
        if pb.rotation_mode == 'QUATERNION':
            return list(pb.rotation_quaternion)
        if pb.rotation_mode == 'AXIS_ANGLE':
            return list(pb.rotation_axis_angle)
        return [round(math.degrees(a), 5) for a in pb.rotation_euler]

    def set_pose(self, armature, bones, rotation_mode="XYZ", space="POSE", keyframe=False, frame=None):
        """
        bones: JSON {bone: {location?:[x,y,z], rotation?:[deg,deg,deg] or [w,x,y,z], scale?:[x,y,z]}}.
        rotation_mode XYZ (Euler degrees) or QUATERNION is set per bone before writing.
        keyframe=True keys the channels written (at frame when given). K20: a pose value on an
        ANIMATED bone is overwritten by the action unless keyframed (warning in the reply).
        """
        import math
        arm, err = self._get_armature(armature)
        if err:
            return err
        spec, jerr = self._json_arg(bones, "bones")
        if jerr:
            return jerr
        if not isinstance(spec, dict) or not spec:
            return {"error": "bones must be a JSON object {bone: {location, rotation, scale}}"}
        rmode = str(rotation_mode or "XYZ").upper()
        if rmode not in ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX", "QUATERNION"):
            return {"error": f"rotation_mode '{rotation_mode}' not valid; valid: ['XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX', 'QUATERNION']"}
        if str(space or "POSE").upper() != "POSE":
            return {"error": "only space='POSE' is supported in this version"}
        scene = bpy.context.scene
        if keyframe and frame is not None:
            scene.frame_set(int(frame))
        animated = arm.animation_data is not None and arm.animation_data.action is not None
        written, mode_changes, unset = [], {}, {}
        for name, values in spec.items():
            pb = arm.pose.bones.get(name)
            if pb is None:
                unset[name] = f"bone not found; bones: {[b.name for b in arm.pose.bones][:30]}"
                continue
            if not isinstance(values, dict):
                unset[name] = "expected an object {location, rotation, scale}"
                continue
            if pb.rotation_mode != rmode and "rotation" in values:
                mode_changes[name] = {"from": pb.rotation_mode, "to": rmode}
                pb.rotation_mode = rmode
            keyed = []
            if "location" in values:
                v, e = self._vec3(values["location"], f"{name}.location")
                if e:
                    unset[name] = e; continue
                pb.location = v; keyed.append("location")
            if "rotation" in values:
                rot = values["rotation"]
                try:
                    if rmode == "QUATERNION":
                        if len(rot) != 4:
                            raise ValueError
                        pb.rotation_quaternion = [float(x) for x in rot]; keyed.append("rotation_quaternion")
                    else:
                        if len(rot) != 3:
                            raise ValueError
                        pb.rotation_euler = [math.radians(float(x)) for x in rot]; keyed.append("rotation_euler")
                except Exception:
                    unset[name] = f"rotation must be {'4 numbers (quaternion)' if rmode == 'QUATERNION' else '3 degrees'}"
                    continue
            if "scale" in values:
                v, e = self._vec3(values["scale"], f"{name}.scale")
                if e:
                    unset[name] = e; continue
                pb.scale = v; keyed.append("scale")
            if keyframe:
                for path in keyed:
                    pb.keyframe_insert(data_path=path, frame=scene.frame_current)
            written.append(name)
        reply = {"success": True, "armature": arm.name, "written": written, "keyframe": bool(keyframe),
                 "frame": scene.frame_current if keyframe else None}
        if mode_changes:
            reply["rotation_mode_changed"] = mode_changes
        if unset:
            reply["unset"] = unset
        if animated and not keyframe and written:
            reply["warning"] = "armature is animated: the pose will be overwritten by its action on the next update unless keyframe=True"
        return reply

    def get_pose(self, armature, bones=None, space="WORLD"):
        arm, err = self._get_armature(armature)
        if err:
            return err
        sp = str(space or "WORLD").upper()
        if sp not in ("WORLD", "POSE"):
            return {"error": f"space '{space}' not valid; valid: ['WORLD', 'POSE']"}
        names = self._names_arg(bones)
        mw = arm.matrix_world
        out, missing = {}, []
        for name in (names if names is not None else [pb.name for pb in arm.pose.bones]):
            pb = arm.pose.bones.get(name)
            if pb is None:
                missing.append(name)
                continue
            head = mw @ pb.head if sp == "WORLD" else pb.head
            tail = mw @ pb.tail if sp == "WORLD" else pb.tail
            out[name] = {"location": [round(x, 5) for x in pb.location], "rotation_mode": pb.rotation_mode,
                         "rotation": self._pose_rotation(pb), "scale": [round(x, 5) for x in pb.scale],
                         "head_world" if sp == "WORLD" else "head": [round(x, 5) for x in head],
                         "tail_world" if sp == "WORLD" else "tail": [round(x, 5) for x in tail]}
        reply = {"success": True, "armature": arm.name, "space": sp, "bones": out}
        if missing:
            reply["missing"] = missing
        return reply

    def reset_pose(self, armature, bones=None, transforms="ALL"):
        """Clear pose transforms by writing identity directly (no POSE-mode ops)."""
        arm, err = self._get_armature(armature)
        if err:
            return err
        t = str(transforms or "ALL").upper()
        if t not in ("ALL", "LOCATION", "ROTATION", "SCALE"):
            return {"error": f"transforms '{transforms}' not valid; valid: ['ALL', 'LOCATION', 'ROTATION', 'SCALE']"}
        names = self._names_arg(bones)
        done, missing = [], []
        for name in (names if names is not None else [pb.name for pb in arm.pose.bones]):
            pb = arm.pose.bones.get(name)
            if pb is None:
                missing.append(name)
                continue
            if t in ("ALL", "LOCATION"):
                pb.location = (0.0, 0.0, 0.0)
            if t in ("ALL", "ROTATION"):
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
            if t in ("ALL", "SCALE"):
                pb.scale = (1.0, 1.0, 1.0)
            done.append(name)
        reply = {"success": True, "armature": arm.name, "reset": done, "transforms": t}
        if missing:
            reply["missing"] = missing
        return reply

    # ── Constraints ──

    def _constraint_owner(self, owner, bone):
        obj = bpy.data.objects.get(owner)
        if obj is None:
            return None, {"error": f"Object not found: {owner}"}
        if bone is None:
            return obj, None
        if obj.type != 'ARMATURE':
            return None, {"error": f"bone={bone!r} needs an ARMATURE owner; {owner} is a {obj.type}"}
        pb = obj.pose.bones.get(bone)
        if pb is None:
            return None, {"error": f"Bone not found: {bone}; bones: {[b.name for b in obj.pose.bones][:50]}"}
        return pb, None

    def add_constraint(self, owner, constraint_type, bone=None, name=None, params=None):
        """
        Object or pose-bone constraint. params: JSON of constraint properties; object-pointer props
        (target, pole_target, ...) take object names, subtarget / pole_subtarget take bone names,
        pole_angle is DEGREES. Reply set / unset {key: reason} like add_modifier.
        """
        import math
        holder, err = self._constraint_owner(owner, bone)
        if err:
            return err
        ctype = str(constraint_type or "").upper()
        valid = [e.identifier for e in bpy.types.Constraint.bl_rna.properties["type"].enum_items]
        if ctype not in valid:
            return {"error": f"constraint_type '{constraint_type}' not valid; valid: {valid}"}
        values, jerr = self._json_arg(params, "params")
        if jerr:
            return jerr
        values = dict(values or {})
        try:
            con = holder.constraints.new(type=ctype)
        except Exception as e:
            return {"error": f"could not add {ctype}: {e}"}
        if name:
            con.name = str(name)
        done, unset = [], {}
        for key, value in values.items():
            prop = con.bl_rna.properties.get(key)
            if prop is None:
                unset[key] = "no such property"
                continue
            try:
                if prop.type == 'POINTER':
                    target = bpy.data.objects.get(value) if isinstance(value, str) else value
                    if target is None:
                        unset[key] = f"object '{value}' not found"
                        continue
                    setattr(con, key, target)
                    done.append(key)
                    continue
                if key in ("pole_angle",) and prop.type == 'FLOAT':
                    value = math.radians(float(value))
                reason = self._rna_set_validated(con, key, value)
                if reason is None:
                    done.append(key)
                else:
                    unset[key] = reason
            except Exception as e:
                unset[key] = str(e)
        reply = {"success": True, "owner": owner, "bone": bone, "constraint": con.name, "type": ctype, "set": done,
                 "summary": self._constraint_summary(con)}
        if unset:
            reply["unset"] = unset
        return reply

    def get_constraints(self, owner, bone=None):
        holder, err = self._constraint_owner(owner, bone)
        if err:
            return err
        return {"success": True, "owner": owner, "bone": bone,
                "constraints": [self._constraint_summary(c) for c in holder.constraints]}

    def remove_constraint(self, owner, name, bone=None):
        holder, err = self._constraint_owner(owner, bone)
        if err:
            return err
        con = holder.constraints.get(name)
        if con is None:
            return {"error": f"constraint '{name}' not found; have: {[c.name for c in holder.constraints]}"}
        holder.constraints.remove(con)
        return {"success": True, "owner": owner, "bone": bone, "removed": name,
                "remaining": [c.name for c in holder.constraints]}

    # ─── Animation (B1 Tier 1): keyframes, animation info, playblast, bake ───

    _KEY_INTERPOLATION = ("CONSTANT", "LINEAR", "BEZIER", "SINE", "QUAD", "CUBIC", "QUART", "QUINT",
                          "EXPO", "CIRC", "BACK", "BOUNCE", "ELASTIC")
    _KEY_EASING = ("AUTO", "EASE_IN", "EASE_OUT", "EASE_IN_OUT")

    def _resolve_anim_target(self, name, bone, data_path):
        """(root, owner, attr, id_owner, key_path) or (None, error_dict)."""
        obj = bpy.data.objects.get(name)
        if obj is None:
            return None, {"error": f"Object not found: {name}"}
        root = obj
        if bone is not None:
            if obj.type != 'ARMATURE':
                return None, {"error": f"bone={bone!r} needs an ARMATURE object; {name} is a {obj.type}"}
            pb = obj.pose.bones.get(bone)
            if pb is None:
                return None, {"error": f"Bone not found: {bone}; bones: {[b.name for b in obj.pose.bones][:50]}"}
            root = pb
        depth, split_at = 0, -1
        for i, ch in enumerate(data_path):
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
            elif ch == '.' and depth == 0:
                split_at = i
        try:
            if split_at >= 0:
                owner = root.path_resolve(data_path[:split_at])
                attr = data_path[split_at + 1:]
            else:
                owner, attr = root, data_path
            getattr(owner, attr)
        except Exception as e:
            return None, {"error": f"Cannot resolve '{data_path}' on {name}{'.' + bone if bone else ''}: {e}"}
        id_owner = owner if isinstance(owner, bpy.types.ID) else owner.id_data
        key_path = attr if id_owner is owner else owner.path_from_id(attr)
        return (root, owner, attr, id_owner, key_path), None

    def set_keyframes(self, target, data_path, keys, bone=None, replace=True):
        """
        Batch keys in one call. keys: JSON list of [frame, value] or {frame, value, interpolation?, easing?}.
        Value rules as add_keyframe (vectors as lists, rotation_euler in DEGREES; QUATERNION/AXIS_ANGLE
        owners switch to XYZ for rotation_euler). interpolation: CONSTANT/LINEAR/BEZIER/...; easing:
        AUTO/EASE_IN/EASE_OUT/EASE_IN_OUT. replace=False skips frames that already carry a key.
        Reply: keyed, skipped, action, fcurves (paths), rotation_mode_changed?.
        """
        import math
        resolved, err = self._resolve_anim_target(target, bone, data_path)
        if err:
            return err
        root, owner, attr, id_owner, key_path = resolved
        entries, jerr = self._json_arg(keys, "keys")
        if jerr:
            return jerr
        if not isinstance(entries, list) or not entries:
            return {"error": "keys must be a non-empty JSON list of [frame, value] or {frame, value, ...}"}
        rotation_mode_changed = None
        if attr in ("rotation_euler", "delta_rotation_euler") and hasattr(owner, "rotation_mode"):
            if owner.rotation_mode in ('QUATERNION', 'AXIS_ANGLE'):
                rotation_mode_changed = {"from": owner.rotation_mode, "to": "XYZ"}
                owner.rotation_mode = 'XYZ'
        current = getattr(owner, attr)
        is_vector = hasattr(current, "__len__") and not isinstance(current, str)
        existing = set()
        if not replace:
            fcurves, _g, _n = self._action_channels(id_owner)
            if fcurves is not None:
                for fc in fcurves:
                    if fc.data_path == key_path:
                        existing.update(int(round(kp.co.x)) for kp in fc.keyframe_points)
        keyed, skipped, styles = [], [], {}
        for entry in entries:
            try:
                if isinstance(entry, dict):
                    frame, value = int(entry["frame"]), entry["value"]
                    interp = str(entry.get("interpolation", "") or "").upper() or None
                    easing = str(entry.get("easing", "") or "").upper() or None
                else:
                    frame, value = int(entry[0]), entry[1]
                    interp, easing = None, None
            except Exception:
                return {"error": f"bad key entry {entry!r}; use [frame, value] or {{frame, value, interpolation, easing}}"}
            if interp and interp not in self._KEY_INTERPOLATION:
                return {"error": f"interpolation '{interp}' not valid; valid: {list(self._KEY_INTERPOLATION)}"}
            if easing and easing not in self._KEY_EASING:
                return {"error": f"easing '{easing}' not valid; valid: {list(self._KEY_EASING)}"}
            if frame in existing:
                skipped.append(frame)
                continue
            if attr in ("rotation_euler", "delta_rotation_euler") and isinstance(value, (list, tuple)):
                value = [math.radians(float(v)) for v in value]
            if is_vector:
                if not isinstance(value, (list, tuple)) or len(value) != len(current):
                    return {"error": f"{data_path} expects {len(current)} values per key, got {value!r}"}
            elif isinstance(value, (list, tuple)):
                if len(value) != 1:
                    return {"error": f"{data_path} expects a single value per key, got {value!r}"}
                value = value[0]
            try:
                setattr(owner, attr, value)
                id_owner.keyframe_insert(data_path=key_path, frame=frame)
            except Exception as e:
                return {"error": f"Could not key {data_path} at frame {frame}: {e}", "keyed_before_error": keyed}
            keyed.append(frame)
            if interp or easing:
                styles[frame] = (interp, easing)
        fcurves, _g, _n = self._action_channels(id_owner)
        paths = []
        if fcurves is not None:
            for fc in fcurves:
                if fc.data_path != key_path:
                    continue
                paths.append(f"{fc.data_path}[{fc.array_index}]")
                if styles:
                    for kp in fc.keyframe_points:
                        style = styles.get(int(round(kp.co.x)))
                        if style:
                            if style[0]:
                                kp.interpolation = style[0]
                            if style[1]:
                                kp.easing = style[1]
        ad = getattr(id_owner, "animation_data", None)
        reply = {"success": True, "target": target, "bone": bone, "data_path": data_path, "keyed": keyed,
                 "keyed_count": len(keyed), "skipped": skipped,
                 "action": ad.action.name if ad is not None and ad.action else None, "fcurves": paths}
        if rotation_mode_changed:
            reply["rotation_mode_changed"] = rotation_mode_changed
        return reply

    def _fcurve_entries(self, fcurves, include_keys, max_keys):
        out = []
        for fc in fcurves:
            entry = {"data_path": fc.data_path, "index": fc.array_index, "keyframe_count": len(fc.keyframe_points),
                     "frame_range": [round(x, 3) for x in fc.range()] if len(fc.keyframe_points) else None,
                     "group": fc.group.name if fc.group else None}
            if include_keys:
                pts = fc.keyframe_points
                entry["keys"] = [[round(kp.co.x, 3), round(kp.co.y, 6), kp.interpolation] for kp in pts[:int(max_keys)]]
                entry["keys_truncated"] = len(pts) > int(max_keys)
            out.append(entry)
        return out

    @staticmethod
    def _action_fcurve_count(action):
        """F-curve count of an action without an owner: legacy fcurves or the sum over slot channelbags."""
        if hasattr(action, "fcurves"):
            return len(action.fcurves)
        total = 0
        with suppress(Exception):
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        total += len(bag.fcurves)
        return total

    def get_animation_info(self, target=None, include_keys=False, max_keys=200):
        """Scene summary (target=None) or per-object action / fcurves / NLA / shape-key action (K2 helper)."""
        scene = bpy.context.scene
        if target is None:
            actions = [{"name": a.name, "users": a.users, "frame_range": [round(x, 3) for x in a.frame_range],
                        "fcurves": self._action_fcurve_count(a), "fake_user": a.use_fake_user} for a in bpy.data.actions]
            return {"success": True, "scene": scene.name, "fps": scene.render.fps, "fps_base": scene.render.fps_base,
                    "frame_start": scene.frame_start, "frame_end": scene.frame_end, "frame_current": scene.frame_current,
                    "actions": actions}
        obj = bpy.data.objects.get(target)
        if obj is None:
            return {"error": f"Object not found: {target}"}
        ad = obj.animation_data
        fcurves, groups, _n = self._action_channels(obj)
        info = {"success": True, "target": obj.name, "type": obj.type,
                "action": ad.action.name if ad is not None and ad.action else None,
                "fcurves": self._fcurve_entries(fcurves, include_keys, max_keys) if fcurves is not None else [],
                "groups": [g.name for g in groups] if groups is not None else [],
                "nla_tracks": [], "shape_key_action": None}
        if ad is not None:
            slot = getattr(ad, "action_slot", None)
            if slot is not None:
                info["action_slot"] = getattr(slot, "identifier", str(slot))
            for track in ad.nla_tracks:
                info["nla_tracks"].append({"name": track.name, "mute": track.mute, "strips": [
                    {"name": s.name, "action": s.action.name if s.action else None,
                     "frame_start": round(s.frame_start, 3), "frame_end": round(s.frame_end, 3)} for s in track.strips]})
        sk = getattr(obj.data, "shape_keys", None) if obj.data else None
        if sk is not None and sk.animation_data is not None and sk.animation_data.action is not None:
            info["shape_key_action"] = sk.animation_data.action.name
        if obj.type == 'ARMATURE' and fcurves is not None:
            bones = set()
            for fc in fcurves:
                if fc.data_path.startswith('pose.bones["'):
                    bones.add(fc.data_path.split('"')[1])
            info["animated_bones"] = sorted(bones)
        return info

    def set_scene_frame_range(self, start=None, end=None, fps=None, fps_base=None, current=None):
        """Rigging-doc name for B0's set_frame_range (one implementation)."""
        return self.set_frame_range(start=start, end=end, fps=fps, fps_base=fps_base, current=current)

    def playblast(self, start=None, end=None, step=None, frames=None, camera=None, max_size=640, columns=4,
                  video_path=None, overlay=None):
        """
        Perceive motion: one image per frame. Path "opengl" (render.opengl under the VIEW_3D
        override; GUI sessions only, K9) or "camera" (an engine render of the active camera per
        frame through _render_settings; the headless path). Auto step keeps <= 16 tiles unless
        step / frames are given. video_path writes FFMPEG (MPEG4 / H264) over the same frame range
        (media_type VIDEO on 5.x). frame_current and every render setting are restored. The server
        composes the contact sheet from "images".
        """
        scene = bpy.context.scene
        if frames is not None:
            try:
                frame_list = [int(f) for f in self._names_arg(frames)]
            except (TypeError, ValueError):
                return {"error": f"frames must be integers, got {frames!r}"}
            if not frame_list:
                return {"error": "frames is empty"}
        else:
            f0 = scene.frame_start if start is None else int(start)
            f1 = scene.frame_end if end is None else int(end)
            if f1 < f0:
                return {"error": f"end ({f1}) is before start ({f0})"}
            if step is None:
                step_v = max(1, -(-(f1 - f0 + 1) // 16))
            else:
                step_v = int(step)
                if step_v < 1:
                    return {"error": "step must be >= 1"}
            frame_list = list(range(f0, f1 + 1, step_v))
            if frame_list[-1] != f1:
                frame_list.append(f1)
        cam_obj = scene.camera
        if camera is not None:
            cam_obj = bpy.data.objects.get(camera)
            if cam_obj is None or cam_obj.type != 'CAMERA':
                return {"error": f"Camera not found: {camera}; cameras: {[o.name for o in bpy.data.objects if o.type == 'CAMERA']}"}
        if overlay is not None and str(overlay).lower() not in self._CAPTURE_OVERLAYS:
            return {"error": f"overlay '{overlay}' not valid; valid: {list(self._CAPTURE_OVERLAYS)}"}

        area = None if bpy.app.background else next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None) if bpy.context.screen else None
        use_opengl = area is not None and self._op_exists(bpy.ops.render.opengl)
        if not use_opengl and cam_obj is None:
            return {"error": "No VIEW_3D for an OpenGL playblast and no camera for the render fallback: create_camera / set_active_camera first"}

        r = scene.render
        aspect = r.resolution_y / max(1, r.resolution_x)
        width = max(2, int(max_size) // 2 * 2)                    # H.264 needs even dimensions
        height = max(2, int(round(width * aspect)) // 2 * 2)
        outdir = tempfile.mkdtemp(prefix="blender_playblast_")
        images, warnings = [], []
        path_used = "opengl" if use_opengl else "camera"
        if overlay and not use_opengl:
            warnings.append("overlay ignored: the camera fallback has no viewport overlays")
        frame_before = scene.frame_current
        video_written = None
        try:
            # SCENE is in the scope because the video branch rewrites frame_start / frame_end / frame_step
            with self._settings_scope(("SCENE", "RENDER", "OUTPUT", "CYCLES", "EEVEE"), scene=scene):
                r.resolution_x, r.resolution_y, r.resolution_percentage = width, height, 100
                err = self._set_file_format(r.image_settings, "PNG")
                if err:
                    return err
                if cam_obj is not None:
                    scene.camera = cam_obj
                with suppress(Exception):
                    scene.cycles.samples = 1
                with suppress(Exception):
                    scene.eevee.taa_render_samples = 1
                overlay_cm = self._capture_overlay(area.spaces.active, overlay) if (use_opengl and overlay) else None
                if overlay_cm:
                    overlay_cm.__enter__()
                view_saved = None
                if use_opengl and camera is not None:
                    # render.opengl(view_context=True) draws the viewport's own view; look through
                    # the requested camera and put the view back afterwards
                    r3d = area.spaces.active.region_3d
                    view_saved = (r3d.view_perspective, r3d.view_matrix.copy(), r3d.view_distance)
                    with suppress(Exception):
                        r3d.view_perspective = 'CAMERA'
                try:
                    for f in frame_list:
                        scene.frame_set(int(f))
                        fp = os.path.join(outdir, f"frame_{int(f):05d}.png")
                        if os.path.exists(fp):
                            os.remove(fp)
                        r.filepath = fp
                        if use_opengl:
                            try:
                                region = next((rg for rg in area.regions if rg.type == 'WINDOW'), None)
                                with bpy.context.temp_override(area=area, region=region):
                                    result = bpy.ops.render.opengl(write_still=True, view_context=True)
                            except Exception as e:
                                # K9: poll() lies in --background; fall back to the camera render for the rest
                                if cam_obj is None:
                                    return {"error": f"OpenGL playblast failed and no camera for the fallback: {e}"}
                                use_opengl, path_used = False, "camera"
                                warnings.append(f"opengl path failed ({e}); camera fallback used")
                                result = bpy.ops.render.render(write_still=True)
                        else:
                            result = bpy.ops.render.render(write_still=True)
                        if 'FINISHED' not in result or not os.path.exists(fp):
                            return {"error": f"frame {f} did not render (operator returned {set(result)})", "images": images}
                        images.append({"frame": int(f), "filepath": fp, "width": width, "height": height})
                    if video_path:
                        stem, ext = os.path.splitext(video_path)
                        verr = self._set_file_format(r.image_settings, "FFMPEG")
                        if verr:
                            return verr
                        with suppress(Exception):
                            r.ffmpeg.format = 'MPEG4'
                        with suppress(Exception):
                            r.ffmpeg.codec = 'H264'
                        r.filepath = stem
                        r.use_file_extension = True
                        scene.frame_start, scene.frame_end = int(frame_list[0]), int(frame_list[-1])
                        scene.frame_step = 1
                        if use_opengl:
                            region = next((rg for rg in area.regions if rg.type == 'WINDOW'), None)
                            with bpy.context.temp_override(area=area, region=region):
                                vres = bpy.ops.render.opengl(animation=True, view_context=True)
                        else:
                            vres = bpy.ops.render.render(animation=True)
                        folder, prefix = os.path.split(stem)
                        folder = folder or os.getcwd()
                        candidates = [os.path.join(folder, n) for n in os.listdir(folder)
                                      if n.startswith(prefix) and n.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))]
                        if 'FINISHED' not in vres or not candidates:
                            warnings.append(f"video not written (operator returned {set(vres)})")
                        else:
                            video_written = max(candidates, key=os.path.getmtime)
                finally:
                    if view_saved is not None:
                        with suppress(Exception):
                            r3d.view_perspective = view_saved[0]
                            r3d.view_matrix = view_saved[1]
                            r3d.view_distance = view_saved[2]
                    if overlay_cm:
                        with suppress(Exception):
                            overlay_cm.__exit__(None, None, None)
        finally:
            with suppress(Exception):
                scene.frame_set(frame_before)
        reply = {"success": True, "path": path_used, "frames": [int(f) for f in frame_list], "images": images,
                 "columns": int(columns), "output_dir": outdir, "camera": cam_obj.name if cam_obj else None,
                 "frame_current_restored": scene.frame_current == frame_before}
        if video_path:
            reply["video"] = video_written
        if warnings:
            reply["warnings"] = warnings
        return reply

    def bake_action(self, armature, start=None, end=None, step=1, bones=None, visual_keying=True,
                    clear_constraints=False, clear_parents=False, only_selected=True, bake_types="POSE",
                    use_current_action=False):
        """
        bpy.ops.nla.bake in POSE mode with the listed bones selected (all when None), armature
        active (K12). Falls back to bpy_extras.anim_utils.bake_action when the operator's poll
        fails (K23). Mode, active object and bone selection are restored. Reply: action, frame_range,
        fcurve_count, animated_bones.
        """
        arm, err = self._get_armature(armature)
        if err:
            return err
        scene = bpy.context.scene
        f0 = scene.frame_start if start is None else int(start)
        f1 = scene.frame_end if end is None else int(end)
        if f1 < f0:
            return {"error": f"end ({f1}) is before start ({f0})"}
        types = self._names_arg(bake_types) or ["POSE"]
        types = {t.strip().upper() for t in types}
        if "BOTH" in types:
            types = {"POSE", "OBJECT"}
        valid = {"POSE", "OBJECT"}
        if not types <= valid:
            return {"error": f"bake_types {sorted(types)} not valid; valid: POSE, OBJECT, both"}
        names = self._names_arg(bones)
        if names is not None:
            missing = [n for n in names if n not in arm.pose.bones]
            if missing:
                return {"error": f"bones not found: {missing}; bones: {[b.name for b in arm.pose.bones][:50]}"}
        prev_sel = {pb.name: (pb.select if hasattr(pb, "select") else pb.bone.select) for pb in arm.pose.bones}
        frame_before = scene.frame_current
        method = "nla.bake"
        try:
            with self._selection_scope(), self._pose_mode(arm):
                self._select_bones(arm, None, False)
                self._select_bones(arm, names, True)
                try:
                    result = bpy.ops.nla.bake(frame_start=f0, frame_end=f1, step=int(step), only_selected=bool(only_selected),
                                              visual_keying=bool(visual_keying), clear_constraints=bool(clear_constraints),
                                              clear_parents=bool(clear_parents), use_current_action=bool(use_current_action),
                                              bake_types=types)
                    if 'FINISHED' not in result:
                        raise RuntimeError(f"nla.bake returned {set(result)}")
                except Exception as op_err:
                    from bpy_extras import anim_utils
                    method = f"anim_utils.bake_action (nla.bake: {op_err})"
                    opts = anim_utils.BakeOptions(only_selected=bool(only_selected), do_pose="POSE" in types,
                                                  do_object="OBJECT" in types, do_visual_keying=bool(visual_keying),
                                                  do_constraint_clear=bool(clear_constraints),
                                                  do_parents_clear=bool(clear_parents), do_clean=False,
                                                  do_location=True, do_rotation=True, do_scale=True, do_bbone=True,
                                                  do_custom_props=True)
                    action = arm.animation_data.action if (use_current_action and arm.animation_data) else None
                    anim_utils.bake_action(arm, action=action, frames=range(f0, f1 + 1, int(step)), bake_options=opts)
        finally:
            for pb in arm.pose.bones:
                with suppress(Exception):
                    if hasattr(pb, "select"):
                        pb.select = prev_sel.get(pb.name, False)
                    else:
                        pb.bone.select = prev_sel.get(pb.name, False)
            with suppress(Exception):
                scene.frame_set(frame_before)
        ad = arm.animation_data
        action = ad.action if ad is not None else None
        fcurves, _g, _n = self._action_channels(arm)
        animated = sorted({fc.data_path.split('"')[1] for fc in fcurves if fc.data_path.startswith('pose.bones["')}) if fcurves is not None else []
        return {"success": True, "armature": arm.name, "method": method,
                "action": action.name if action else None,
                "frame_range": [round(x, 3) for x in action.frame_range] if action else None,
                "baked_range": [f0, f1], "step": int(step), "bake_types": sorted(types),
                "fcurve_count": len(fcurves) if fcurves is not None else 0, "animated_bones": animated,
                "bones": names if names is not None else "all"}

    # ─── Generic driver (D1): ref grammar, encoding, coercion, describe ──────
    # Contract: docs/PLAN.md 12.2 (D1.0) + rulings R16-R21. Every handler returns a dict and
    # never raises on bad input; teaching errors carry closest / valid / tried and `rna_error`
    # (Blender's own message, verbatim). Reads apply max_chars while building (chars + truncated).

    _DRV_ROOTS = ("context", "data", "prefs")
    # RNA members that crash the process when read headless (an access violation, not an exception, so
    # it cannot be caught): never read or expanded by the encoder, refused in any ref path. Measured
    # 2026-10-16 on Blender 4.3.2, factory file: scene.tool_settings.uv_sculpt is an uninitialised Paint
    # struct (rna_Paint_eraser_brush_get, brush_asset_reference.*, cavity_curve.* all fault inside it);
    # with it skipped a depth-3 walk of Scene (1482 reads, sculpt.eraser_brush included) completes.
    _DRV_UNSAFE_PROPS = frozenset({"uv_sculpt"})

    @staticmethod
    def _drv_unsafe_struct(srna):
        """True when srna or any struct in its base chain is in _DRV_UNSAFE_STRUCTS (R33)."""
        while srna is not None:
            if srna.identifier in _DRV_UNSAFE_STRUCTS:
                return True
            srna = srna.base
        return False

    @staticmethod
    def _drv_cap_arg(max_chars):
        """max_chars -> (cap, error dict | None): None = the default, otherwise a positive integer."""
        if max_chars is None:
            return _DRV_REPLY_MAX_DEFAULT, None
        try:
            cap = int(max_chars)
        except (TypeError, ValueError):
            cap = 0
        if cap < 1:
            return None, {"error": f"max_chars must be a positive integer (default {_DRV_REPLY_MAX_DEFAULT}), got {max_chars!r}"}
        return cap, None

    @classmethod
    def _drv_unsafe_path(cls, path):
        """The first unsafe segment of an RNA path, else None."""
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(path or "")):
            if name in cls._DRV_UNSAFE_PROPS:
                return name
        return None
    _DRV_WHERE_OPS = ("==", "!=", "<", "<=", ">", ">=", "in", "contains", "exists")
    # Properties whose assignment must go through the API that triggers the update (12.2.2 rna_set)
    _DRV_SETTERS = {("Scene", "frame_current"): lambda owner, value: owner.frame_set(int(value))}
    # ID.id_type identifier -> ordered bpy.data collection candidates (R26; measured on 4.3.2 and
    # 5.2.1, 2026-10-16: struct names are not stable across versions, so no derived rule). The first
    # candidate that exists on this Blender wins (hasattr, never a version compare).
    _DRV_IDTYPE_TABLE = (
        ("ACTION", "actions"), ("ARMATURE", "armatures"), ("BRUSH", "brushes"), ("CACHEFILE", "cache_files"),
        ("CAMERA", "cameras"), ("COLLECTION", "collections"), ("CURVE", "curves"), ("CURVES", "hair_curves"),
        ("FONT", "fonts"), ("GREASEPENCIL", "annotations", "grease_pencils"),
        ("GREASEPENCIL_V3", "grease_pencils_v3", "grease_pencils"), ("IMAGE", "images"), ("KEY", "shape_keys"),
        ("LATTICE", "lattices"), ("LIBRARY", "libraries"), ("LIGHT", "lights"), ("LIGHT_PROBE", "lightprobes"),
        ("LINESTYLE", "linestyles"), ("MASK", "masks"), ("MATERIAL", "materials"), ("MESH", "meshes"),
        ("META", "metaballs"), ("MOVIECLIP", "movieclips"), ("NODETREE", "node_groups"), ("OBJECT", "objects"),
        ("PAINTCURVE", "paint_curves"), ("PALETTE", "palettes"), ("PARTICLE", "particles"),
        ("POINTCLOUD", "pointclouds"), ("SCENE", "scenes"), ("SCREEN", "screens"), ("SOUND", "sounds"),
        ("SPEAKER", "speakers"), ("TEXT", "texts"), ("TEXTURE", "textures"), ("VOLUME", "volumes"),
        ("WINDOWMANAGER", "window_managers"), ("WORKSPACE", "workspaces"), ("WORLD", "worlds"))

    @staticmethod
    def _drv_list(value):
        """Comma string / JSON-list string / list / None -> list of str or None."""
        if value is None:
            return None
        if isinstance(value, str) and value.lstrip().startswith("["):
            with suppress(Exception):
                value = json.loads(value)
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return [str(v) for v in value]

    @staticmethod
    def _drv_closest(word, candidates, n=3):
        import difflib
        return difflib.get_close_matches(str(word), [str(c) for c in candidates], n=n, cutoff=0.5)

    @staticmethod
    def _drv_data_collections():
        """bpy.data COLLECTION props minus the 5.x `all_ids` union view (it double-counts every ID)."""
        return [p.identifier for p in bpy.data.bl_rna.properties
                if p.type == 'COLLECTION' and p.identifier != "all_ids"]

    @staticmethod
    def _id_ref(idb):
        """{uid, id_type, name}: every reply that mentions an ID carries this. An embedded ID (a scene's
        master collection, a material's node tree) has no id_type value (4.3.2 warns and reads 0): its
        type comes from the struct and the entry says embedded (it is not in any bpy.data collection)."""
        if getattr(idb, "is_embedded_data", False):
            base = idb.bl_rna.base
            kind = base.identifier if base is not None and base.identifier != "ID" else idb.bl_rna.identifier
            return {"uid": idb.session_uid, "id_type": kind.upper(), "name": idb.name, "embedded": True}
        return {"uid": idb.session_uid, "id_type": idb.id_type, "name": idb.name}

    @staticmethod
    def _drv_name(struct):
        if isinstance(struct, bpy.types.ID):
            return f"{struct.id_type}:{struct.name}"
        return struct.bl_rna.identifier if hasattr(struct, "bl_rna") else type(struct).__name__

    @classmethod
    def _id_type_map(cls):
        """ID.id_type identifier -> bpy.data collection name on this Blender (R26): the static ordered
        table resolved by hasattr at first use, limited to identifiers the running ID.id_type enum has."""
        cached = getattr(cls, "_drv_idtype_cache", None)
        if cached:
            return cached
        enum_ids = {i.identifier for i in bpy.types.ID.bl_rna.properties["id_type"].enum_items}
        result = {}
        for idt, *candidates in cls._DRV_IDTYPE_TABLE:
            if idt not in enum_ids:
                continue
            for c in candidates:
                if hasattr(bpy.data, c):
                    result[idt] = c
                    break
        cls._drv_idtype_cache = result
        return result

    @classmethod
    def _uid_lookup(cls, uid):
        """The ID with that session_uid or None. Full bpy.data scan per call (R16: no cache; measured
        0.27 ms worst case on 2000 IDs); uids are monotonic, never reused, survive rename and undo."""
        for c in cls._drv_data_collections():
            for idb in getattr(bpy.data, c):
                if getattr(idb, "session_uid", None) == uid:
                    return idb
        return None

    @staticmethod
    def _split_path(path):
        """
        'modifiers["Bevel"].width' -> ('modifiers["Bevel"]', 'width', None); 'location[2]' ->
        ('', 'location', 2); '["key"]' -> ('', '["key"]', None). Split at the LAST '.' outside
        brackets; a trailing [<int>] on a plain leaf is the index.
        """
        depth, quote, cut = 0, None, -1
        for i, ch in enumerate(path):
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == "." and depth == 0:
                cut = i
        parent, leaf = (path[:cut], path[cut + 1:]) if cut >= 0 else ("", path)
        index = None
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\[(-?\d+)\]$', leaf)
        if m:
            leaf, index = m.group(1), int(m.group(2))
        return parent, leaf, index

    @staticmethod
    def _drv_idprop_key(leaf):
        """'["key"]' -> 'key', else None."""
        m = re.match(r'''^\[\s*(["'])(.*)\1\s*\]$''', leaf or "")
        return m.group(2) if m else None

    def _resolve_root(self, ref):
        """ref -> (root struct, remaining path). Raises _DrvRefError({error, tried, closest, valid})."""
        ref = str(ref).strip()
        head, sep, path = ref.partition("/")
        head = head.strip()
        if len(ref) > 80:
            ref = ref[:77] + "..."
        if sep and not path:
            raise _DrvRefError({"error": f"bad ref '{ref}': nothing after '/'; drop the slash or add an RNA path", "tried": []})
        if head.startswith("#"):
            try:
                uid = int(head[1:])
            except ValueError:
                raise _DrvRefError({"error": f"bad uid in ref '{ref}': expected #<int>", "tried": []})
            idb = self._uid_lookup(uid)
            if idb is None:
                raise _DrvRefError({"error": f"no ID with session_uid {uid} (uids are per session; use find or IDTYPE:Name)",
                                    "tried": self._drv_data_collections()})
            return idb, path
        if head in self._DRV_ROOTS:
            root = {"context": bpy.context, "data": bpy.data, "prefs": bpy.context.preferences}[head]
            return root, path
        if ":" in head:
            idt, _, name = head.partition(":")
            idmap = self._id_type_map()
            key = idt.strip().upper()
            coll = idmap.get(key)
            if coll is None:
                raise _DrvRefError({"error": f"unknown IDTYPE '{idt}' in ref '{ref}'", "valid": sorted(idmap),
                                    "closest": self._drv_closest(key, idmap)})
            idb = getattr(bpy.data, coll).get(name)
            if idb is None:
                raise _DrvRefError({"error": f"no {key} named '{name}' in bpy.data.{coll}", "tried": [coll],
                                    "closest": self._drv_closest(name, [i.name for i in getattr(bpy.data, coll)])})
            return idb, path
        raise _DrvRefError({"error": f"bad ref '{ref}': expected #<uid>, IDTYPE:Name, context, data or prefs, optionally /<path>",
                            "tried": [], "valid": ["#<uid>", "IDTYPE:Name", "context", "data", "prefs"]})

    def _resolve_ref(self, ref, keep_prop=False):
        """
        ref -> (owner, leaf, index) per PLAN.md 12.2.1. leaf None = the ref ends on a struct;
        leaf '["key"]' = an ID property. A POINTER / COLLECTION leaf resolves to the struct it
        points at unless keep_prop (rna_set needs owner + leaf). Raises _DrvRefError on failure.
        """
        root, path = self._resolve_root(ref)
        if not path:
            return root, None, None
        unsafe = self._drv_unsafe_path(path)
        if unsafe:
            raise _DrvRefError({"error": f"'{unsafe}' in '{ref}' is not read: that RNA member crashes Blender headless on some builds (4.3.2 tool_settings.uv_sculpt)",
                                "tried": [path]})
        parent, leaf, index = self._split_path(path)
        owner = root
        if parent:
            try:
                owner = root.path_resolve(parent, False)
            except Exception as e:
                raise _DrvRefError({"error": f"could not resolve '{parent}' under {self._drv_name(root)}: {e}",
                                    "tried": [parent], "rna_error": str(e)})
        if self._drv_idprop_key(leaf) is not None:
            return owner, leaf, index
        props = owner.bl_rna.properties if hasattr(owner, "bl_rna") else None
        prop = props.get(leaf) if props is not None else None
        if prop is None:
            raw = leaf if index is None else f"{leaf}[{index}]"
            try:
                return root.path_resolve(path, False), None, None
            except Exception as e:
                raise _DrvRefError({"error": f"'{raw}' is not a property of {self._drv_name(owner)}: {e}", "tried": [path],
                                    "rna_error": str(e), "count": len(props) if props is not None else 0,
                                    "closest": self._drv_closest(leaf, [p.identifier for p in props]) if props is not None else []})
        if prop.type in ('POINTER', 'COLLECTION') and index is None and not keep_prop:
            target = getattr(owner, leaf)
            if target is not None and prop.type == 'POINTER':
                return target, None, None
            if prop.type == 'COLLECTION':
                return target, None, None
        return owner, leaf, index

    # -- encoding (12.2.4) --------------------------------------------------------------

    @staticmethod
    def _drv_path_from_id(struct):
        with suppress(Exception):
            return struct.path_from_id()
        return None

    @staticmethod
    def _drv_is_rotation(prop):
        return prop is not None and getattr(prop, "unit", None) == 'ROTATION'

    @staticmethod
    def _drv_enum_valid(prop, rna_error):
        """The live enum tuple from Blender's own error text ("enum \"X\" not found in ('A', 'B')"), else
        the static items: dynamic enums (render.engine, view transforms) read incomplete headless."""
        m = re.search(r"not found in \((.*)\)", rna_error or "")
        if m:
            return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
        return [i.identifier for i in prop.enum_items]

    def _encode_idprop(self, value):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "to_list"):
            return value.to_list()
        if isinstance(value, bpy.types.ID):
            return self._id_ref(value)
        return value

    def _encode_value(self, value, prop=None, depth=0, fields=None, angles="RADIANS"):
        """RNA value -> JSON per PLAN.md 12.2.4, on _rna_get's scalar rules; angles="DEGREES" scales
        unit ROTATION properties."""
        import math
        if value is None:
            return None
        if isinstance(value, bpy.types.ID):
            return self._id_ref(value)
        if isinstance(value, mathutils.Matrix):
            return [list(row) for row in value]
        if isinstance(value, bpy.types.bpy_prop_collection):
            return {"count": len(value)}
        if isinstance(value, bpy.types.bpy_struct):
            if depth > 0:
                return self._encode_struct(value, depth - 1, fields, angles)
            return {"struct": value.bl_rna.identifier, "path": self._drv_path_from_id(value)}
        if isinstance(value, (set, frozenset)):
            return sorted(value)
        if isinstance(value, (bool, int, float, str)):
            if angles == "DEGREES" and self._drv_is_rotation(prop) and isinstance(value, float):
                return math.degrees(value)
            return value
        if isinstance(value, dict):
            # R-B2-43 (F-B2-4): a plain dict round-trips (keys as str, values encoded); dicts only, an
            # IDPropertyGroup is not a dict so driver replies are unchanged
            return {str(k): self._encode_value(v, None, depth, None, angles) for k, v in value.items()}
        if hasattr(value, "__len__") and not isinstance(value, (bytes, bytearray)):
            try:
                out = [self._encode_value(v, None, depth, None, angles) for v in value]
            except TypeError:
                return str(value)
            if angles == "DEGREES" and self._drv_is_rotation(prop):
                out = [math.degrees(v) if isinstance(v, float) else v for v in out]
            return out
        return str(value)

    def _drv_skip_note(self):
        """The R33 note for truncated[] when the walk met an unsafe struct, else None (reset per read handler)."""
        skipped = getattr(self, "_drv_skips", None)
        if not skipped:
            return None
        names = sorted(skipped)
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
        return f"skipped unsafe structs: {shown} (pointers on ToolSettings / Paint-family structs are never dereferenced: 4.3.2 factory-file dangling brush)"

    def _encode_struct(self, owner, depth=0, fields=None, angles="RADIANS"):
        """
        Struct -> {prop: value}. fields None = every non-COLLECTION property (pointers as refs, R23);
        "all" = everything to depth; a name with [] (modifiers[]) lists that collection's items to depth.
        ID properties go under "custom". Unknown field names are listed under "missing".
        """
        props = owner.bl_rna.properties
        unsafe_owner = self._drv_unsafe_struct(owner.bl_rna)
        skips = self.__dict__.setdefault("_drv_skips", set())
        wanted = None if fields in (None, "all") else self._drv_list(fields)
        expand = set()
        if wanted is not None:
            cleaned = []
            for f in wanted:                      # RA-14: a plain loop, no set mutation inside a comprehension filter
                if f.endswith("[]"):
                    expand.add(f[:-2])
                    cleaned.append(f[:-2])
                else:
                    cleaned.append(f)
            wanted = cleaned
        out, missing = {}, []
        for ident in (wanted if wanted is not None else [p.identifier for p in props]):
            if ident == "rna_type":
                continue
            prop = props.get(ident)
            if prop is None:
                if ident == "custom":
                    out["custom"] = self._drv_custom(owner)
                else:
                    missing.append(ident)
                continue
            if ident in self._DRV_UNSAFE_PROPS:
                if wanted is not None:
                    out[ident] = {"read_error": f"'{ident}' is not read: that RNA member crashes Blender headless on some builds (4.3.2 tool_settings.uv_sculpt)"}
                continue
            if wanted is None and fields != "all" and prop.type == 'COLLECTION':
                continue
            if prop.type in ('POINTER', 'COLLECTION') and unsafe_owner:
                # R33: no getattr at all on these owners; depth-0 shape from the descriptor
                owner_path = self._drv_path_from_id(owner)
                out[ident] = {"struct": prop.fixed_type.identifier, "path": f"{owner_path}.{ident}" if owner_path else None}
                skips.add(owner_path or owner.bl_rna.identifier)
                continue
            try:
                raw = getattr(owner, ident)
            except Exception as e:
                out[ident] = {"read_error": str(e)}
                continue
            if prop.type == 'COLLECTION':
                if ident in expand or (fields == "all" and depth > 0):
                    out[ident] = {"count": len(raw), "items": [self._encode_value(i, None, depth, None, angles) for i in raw]}
                else:
                    out[ident] = {"count": len(raw)}
            elif prop.type == 'POINTER':
                out[ident] = self._encode_value(raw, prop, depth, None, angles)
            else:
                out[ident] = self._encode_value(raw, prop, 0, None, angles)
        if wanted is None:
            custom = self._drv_custom(owner)
            if custom:
                out["custom"] = custom
        if missing:
            out["missing"] = missing
        return out

    def _drv_custom(self, owner):
        custom = {}
        with suppress(Exception):
            for k in owner.keys():
                with suppress(Exception):
                    custom[k] = self._encode_idprop(owner[k])
        return custom

    def _drv_finish(self, reply, max_chars, droppable=(), items_key=None, items_hint="", stage1=None, order="largest", notes=()):
        """
        Fill chars + truncated. While the JSON exceeds max_chars (default _DRV_REPLY_MAX_DEFAULT):
        first run stage1() once if given (returns the note for truncated[], R32: describe blanks its
        description texts), then remove droppable entries (droppable = [(container dict, key, group,
        hint)]; order "largest" first or from the "end"; one truncated line per group names every key
        removed) or pop the last entry of reply[items_key]. chars counts the reply without the
        chars key itself.
        """
        try:
            cap = int(max_chars) if max_chars else _DRV_REPLY_MAX_DEFAULT
        except (TypeError, ValueError):
            cap = _DRV_REPLY_MAX_DEFAULT
        reply["truncated"] = []
        droppable = list(droppable)
        groups = {}
        notes = [n for n in notes if n]

        def size():
            # the final json including truncated[] and the chars key itself (R34): 12 = ', "chars": ' + the digits
            return len(json.dumps(reply, default=str)) + 12 + len(str(cap))

        dropped_items = 0

        def lines():
            out = list(notes)
            for group, g in groups.items():
                total = len(g["keys"]) + sum(len(c) for c in g["containers"].values())
                out.append(f"{group}: {len(g['keys'])} of {total} dropped, e.g. {', '.join(g['keys'][:3])}; narrow with {g['hint']}")
            if dropped_items:
                out.append(f"{items_key}: {dropped_items} entries dropped, {len(reply[items_key])} kept; narrow with {items_hint}")
            return out

        while True:
            reply["truncated"] = lines()
            n = size()
            if n <= cap:
                break
            if stage1 is not None:
                note = stage1()
                stage1 = None
                if note:
                    notes.append(note)
                continue
            if droppable:
                if order == "end":
                    biggest = droppable.pop()
                else:
                    biggest = max(droppable, key=lambda d: len(json.dumps(d[0].get(d[1]), default=str)))
                    droppable.remove(biggest)
                container, key, group, hint = biggest
                was = len(json.dumps(container.pop(key), default=str))
                g = groups.setdefault(group, {"keys": [], "chars": 0, "hint": hint, "containers": {}})
                g["keys"].append(key)
                g["chars"] += was
                g["containers"][id(container)] = container
            elif items_key and reply.get(items_key):
                reply[items_key].pop()
                dropped_items += 1
            else:
                break
        reply["chars"] = len(json.dumps(reply, default=str)) + 12 + len(str(n))
        return reply

    # -- coercion (12.2.4, built on _rna_set_validated) ------------------------------------

    def _drv_pointer_value(self, prop, value):
        """'#uid' / 'IDTYPE:Name' / {'uid': n} / bare name -> the ID for a POINTER prop; (id, error)."""
        want = prop.fixed_type.identifier
        want_cls = getattr(bpy.types, want, bpy.types.ID)
        if value is None:
            return None, None
        if isinstance(value, dict):
            if "uid" not in value:
                return None, {"error": f'pointer value for "{prop.identifier}" must be "#uid", "IDTYPE:Name" or {{"uid": n}}'}
            value = f"#{value['uid']}"
        if isinstance(value, int) and not isinstance(value, bool):
            value = f"#{value}"  # a bare uid, e.g. a "$mesh.uid" batch substitution
        if isinstance(value, bpy.types.ID):
            target = value
        else:
            text = str(value)
            if text.startswith("#") or ":" in text:
                try:
                    target, rest = self._resolve_root(text)
                except _DrvRefError as e:
                    return None, e.reply
                if rest:
                    return None, {"error": f"pointer value '{text}' must name an ID, not a path"}
            else:
                # bare name: the bpy.data collection whose items are the pointer's fixed_type
                target = None
                for c in self._drv_data_collections():
                    if bpy.data.bl_rna.properties[c].fixed_type.identifier == want:
                        target = getattr(bpy.data, c).get(text)
                        if target is not None:
                            break
                if target is None:
                    return None, {"error": f"no {want} named '{text}' for \"{prop.identifier}\"; pass \"#uid\" or \"IDTYPE:Name\""}
        if not isinstance(target, want_cls):
            return None, {"error": f'"{prop.identifier}" expects a {want}, got {self._drv_name(target)}'}
        return target, None

    def _coerce_value(self, prop, value, angles="RADIANS"):
        """
        JSON value -> RNA-ready value for prop (12.2.4): refs -> pointers (via fixed_type), DEGREES ->
        radians on unit ROTATION props, nested lists -> Matrix, enum case fix; read-only refused here
        (and again by _rna_set_validated on assignment). Returns (value, error dict | None).
        """
        import math
        if prop.is_readonly:
            return None, {"error": f'property "{prop.identifier}" of {prop.srna.identifier if getattr(prop, "srna", None) else "struct"} is read-only'}
        if prop.type == 'POINTER':
            return self._drv_pointer_value(prop, value)
        if prop.type == 'COLLECTION':
            return None, {"error": f'"{prop.identifier}" is a collection; address its items by path'}
        if prop.type == 'ENUM' and isinstance(value, str) and not prop.is_enum_flag:
            # case fix only: the static list is a hint (render.engine reads one item headless); the
            # assignment decides and its error text carries the live tuple (F7, R21)
            ids = [i.identifier for i in prop.enum_items]
            if ids and value not in ids:
                value = next((i for i in ids if i.upper() == value.upper()), value)
        if prop.type == 'FLOAT':
            if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
                try:
                    return mathutils.Matrix([list(map(float, row)) for row in value]), None
                except Exception as e:
                    return None, {"error": f"not a matrix: {e}"}
            if angles == "DEGREES" and self._drv_is_rotation(prop):
                try:
                    value = [math.radians(float(v)) for v in value] if isinstance(value, (list, tuple)) else math.radians(float(value))
                except (TypeError, ValueError) as e:
                    return None, {"error": f"angle must be a number or a list of numbers: {e}"}
        return value, None

    def _drv_assign(self, owner, leaf, index, value, angles="RADIANS"):
        """Coerce + assign one property / array element / ID property. Returns error dict | None."""
        key = self._drv_idprop_key(leaf)
        if key is not None:
            try:
                owner[key] = value
            except Exception as e:
                return {"error": f"could not set ID property {leaf}: {e}", "rna_error": str(e)}
            return None
        prop = owner.bl_rna.properties.get(leaf)
        if prop is None:
            return {"error": f"'{leaf}' is not a property of {self._drv_name(owner)}",
                    "closest": self._drv_closest(leaf, [p.identifier for p in owner.bl_rna.properties]),
                    "count": len(owner.bl_rna.properties)}
        setter = self._DRV_SETTERS.get((owner.bl_rna.identifier, leaf))
        if index is not None:
            if not (getattr(prop, "is_array", False) and prop.array_length):
                return {"error": f'"{leaf}" is not an array; drop the [{index}]'}
            if not (-prop.array_length <= index < prop.array_length):
                return {"error": f"index {index} out of range for {leaf}[{prop.array_length}]"}
            current = list(getattr(owner, leaf))
            element, err = self._coerce_value(prop, [value] if prop.type == 'FLOAT' else value, angles)
            if err:
                return err
            current[index] = element[0] if isinstance(element, list) else element
            value = current
        else:
            value, err = self._coerce_value(prop, value, angles)
            if err:
                return err
        try:
            if setter is not None:
                setter(owner, value)
            elif prop.type == 'POINTER' or isinstance(value, mathutils.Matrix):
                setattr(owner, leaf, value)
            else:
                reason = self._rna_set_validated(owner, leaf, value)
                if reason is not None:
                    err = {"error": f'could not set "{leaf}" on {self._drv_name(owner)}: {reason}', "rna_error": reason}
                    if prop.type == 'ENUM':
                        err["valid"] = self._drv_enum_valid(prop, reason)
                        err["closest"] = self._drv_closest(value, err["valid"])
                    return err
        except Exception as e:
            err = {"error": f'could not set "{leaf}" on {self._drv_name(owner)}: {e}', "rna_error": str(e)}
            if prop.type == 'ENUM':
                err["valid"] = self._drv_enum_valid(prop, str(e))
            return err
        return None

    # -- describe (12.2.2) ------------------------------------------------------------------

    def _describe_prop(self, prop):
        """One property descriptor: the describe_settings entry shape plus unit, fixed_type, is_animatable,
        enum_items [id, name, description], dynamic (ENUM with empty static items). Fields may be null."""
        g = lambda name: getattr(prop, name, None)  # noqa: E731 (12.6 row 5: every field may be null)
        entry = {"type": prop.type, "subtype": g("subtype"), "unit": g("unit"), "readonly": bool(prop.is_readonly),
                 "description": g("description"), "is_animatable": g("is_animatable")}
        if g("is_array") and (g("array_length") or 0) > 0:
            entry["array_length"] = prop.array_length
        if prop.type in ('INT', 'FLOAT', 'BOOLEAN', 'STRING', 'ENUM'):
            entry["default"] = self._prop_default(prop)
        if prop.type in ('INT', 'FLOAT'):
            entry["min"], entry["max"] = g("hard_min"), g("hard_max")
            entry["soft_min"], entry["soft_max"] = g("soft_min"), g("soft_max")
        if prop.type == 'ENUM':
            entry["enum_items"] = [[i.identifier, i.name, i.description] for i in prop.enum_items]
            # dynamic = the enum has a generator. RNA gives no flag for it and enum_items_static mirrors
            # enum_items; measured 2026-10-16 on both installs: generator enums expose a 0- or 1-entry
            # fallback headless (render.engine 1, view_transform 1, length_unit 1) while static enums read
            # their full list (Object.type 16/17, file_format 15/16). The live list is incomplete for these:
            # only assignment validates (the error text carries the live tuple, R21).
            entry["dynamic"] = len(entry["enum_items"]) <= 1
            entry["is_enum_flag"] = bool(g("is_enum_flag"))
        if prop.type == 'STRING':
            entry["length_max"] = g("length_max")
        if prop.type in ('POINTER', 'COLLECTION'):
            ft = g("fixed_type")
            entry["fixed_type"] = ft.identifier if ft is not None else None
        return entry

    def _describe_struct(self, srna, fields="all", include_functions=False, search=None):
        """{identifier, base, description, count, properties{...}, functions?{...}} from an RNA struct type
        (bl_rna). The full catalogue is cached per identifier for the session; filters apply per call."""
        cache = self.__class__.__dict__.get("_drv_struct_cache")
        if cache is None:
            cache = {}
            self.__class__._drv_struct_cache = cache
        ident = srna.identifier
        full = cache.get(ident)
        if full is None:
            props = {p.identifier: self._describe_prop(p) for p in srna.properties if p.identifier != "rna_type"}
            funcs = {f.identifier: {"description": f.description,
                                    "params": [[p.identifier, p.type, bool(p.is_output), bool(p.is_required)] for p in f.parameters]}
                     for f in srna.functions}
            full = {"identifier": ident, "base": srna.base.identifier if srna.base else None,
                    "description": srna.description, "properties": props, "functions": funcs}
            cache[ident] = full
        wanted = None if fields in (None, "all") else self._drv_list(fields)
        props = dict(full["properties"])  # a copy: _drv_finish may drop entries from the reply, never from the cache
        if wanted is not None:
            props = {k: props[k] for k in wanted if k in props}
        if search:
            s = str(search).lower()
            props = {k: v for k, v in props.items()
                     if s in k.lower() or s in str(v.get("description") or "").lower()}
        reply = {"kind": "struct", "identifier": full["identifier"], "base": full["base"],
                 "description": full["description"], "count": len(full["properties"]), "properties": props}
        if wanted is not None:
            missing = [k for k in wanted if k not in full["properties"]]
            if missing:
                reply["missing"] = missing
        if include_functions:
            reply["functions"] = dict(full["functions"])
        return reply

    @classmethod
    def _drv_ops_catalogue(cls):
        """[[idname, description], ...] over every operator (get_rna_type, never hasattr); cached per process."""
        cached = getattr(cls, "_drv_ops_cache", None)
        if cached:
            return cached
        out = []
        for mod_name in dir(bpy.ops):
            if mod_name.startswith("_"):
                continue
            mod = getattr(bpy.ops, mod_name)
            for op_name in dir(mod):
                with suppress(Exception):
                    rt = getattr(mod, op_name).get_rna_type()
                    out.append([f"{mod_name}.{op_name}", rt.description])
        cls._drv_ops_cache = out
        return out

    def _describe_op(self, idname):
        """{idname, name, description, properties, poll_now, refused} (no bl_options: unreachable for C operators, R19)."""
        mod_name, _, op_name = str(idname).partition(".")
        op = getattr(getattr(bpy.ops, mod_name, None), op_name, None) if mod_name and op_name else None
        if op is None or not self._op_exists(op):
            names = [o[0] for o in self._drv_ops_catalogue()]
            return {"error": f"unknown operator '{idname}'", "closest": self._drv_closest(idname, names)}
        rt = op.get_rna_type()
        props = {p.identifier: self._describe_prop(p) for p in rt.properties if p.identifier != "rna_type"}
        try:
            poll_now = bool(op.poll())
        except Exception:
            poll_now = False
        return {"kind": "operator", "idname": f"{mod_name}.{op_name}", "name": rt.name, "description": rt.description,
                "properties": props, "poll_now": poll_now, "refused": f"{mod_name}.{op_name}" in _DRV_REFUSED_OPS}

    # -- handlers ----------------------------------------------------------------------------

    @_drv_handler
    def drv_describe(self, what, fields="all", include_functions=False, search=None, max_chars=None):
        """describe: a ref, a struct name ("Object"), an operator id ("mesh.bevel") or "ops" (+ search)."""
        what = str(what or "").strip()
        if not what:
            return {"error": "what is required: a ref, a struct name, an operator id or \"ops\""}
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        if what.lower() == "ops":
            ops = self._drv_ops_catalogue()
            if search:
                s = str(search).lower()
                ops = [o for o in ops if s in o[0].lower() or s in (o[1] or "").lower()]
            reply = {"success": True, "kind": "ops", "count": len(ops), "total": len(ops), "operators": list(ops)}
            self._drv_finish(reply, cap, items_key="operators", items_hint="search= or raise max_chars=")
            reply["count"] = len(reply["operators"])
            return reply
        if what.startswith("#") or ":" in what or "/" in what or what in self._DRV_ROOTS:
            try:
                owner, leaf, index = self._resolve_ref(what)
            except _DrvRefError as e:
                return e.reply
            if leaf is not None and self._drv_idprop_key(leaf) is None:
                prop = owner.bl_rna.properties[leaf]
                reply = {"success": True, "kind": "property", "identifier": leaf, "owner": owner.bl_rna.identifier,
                         **self._describe_prop(prop)}
                if isinstance(owner, bpy.types.ID):
                    reply.update(self._id_ref(owner))
                return self._drv_finish(reply, cap)
            if leaf is not None:
                return {"error": f"{leaf} is an ID property; describe its owner instead"}
            if isinstance(owner, bpy.types.bpy_prop_collection):
                return {"error": "a collection has no descriptor; describe its item type (e.g. Modifier) or an item"}
            reply = {"success": True, **self._describe_struct(owner.bl_rna, fields, include_functions, search)}
            if isinstance(owner, bpy.types.ID):
                reply.update(self._id_ref(owner))
            elif hasattr(owner, "id_data") and isinstance(owner.id_data, bpy.types.ID):
                reply["id"] = self._id_ref(owner.id_data)
                reply["path"] = self._drv_path_from_id(owner)
        elif "." in what:
            reply = self._describe_op(what)
            if "error" in reply:
                return reply
            reply = {"success": True, **reply}
        else:
            cls = getattr(bpy.types, what, None)
            if cls is None or not hasattr(cls, "bl_rna"):
                return {"error": f"unknown struct '{what}'",
                        "closest": self._drv_closest(what, [n for n in dir(bpy.types) if not n.startswith("_")])}
            reply = {"success": True, **self._describe_struct(cls.bl_rna, fields, include_functions, search)}
        # R32 truncation order: (1) blank every description text (property + enum item, shape kept),
        # (2) only then whole properties from the END of the dict, named in truncated[]
        def blank_descriptions():
            # replace, never mutate: the entry dicts are shared with the per-session catalogue cache
            props = reply.get("properties", {})
            for k, entry in list(props.items()):
                if entry.get("description") or entry.get("enum_items"):
                    copy = dict(entry)
                    copy["description"] = ""
                    if entry.get("enum_items"):
                        copy["enum_items"] = [[i[0], i[1], ""] for i in entry["enum_items"]]
                    props[k] = copy
            if reply.get("description"):
                reply["description"] = ""
            return "descriptions dropped; pass max_chars= to keep them"
        droppable = [(reply["properties"], k, "properties", "fields= or raise max_chars=") for k in list(reply.get("properties", {}))]
        if "functions" in reply:
            droppable.append((reply, "functions", "functions", "include_functions=False"))
        reply["total"] = reply.get("count")
        self._drv_finish(reply, cap, droppable, stage1=blank_descriptions, order="end")
        reply["count"] = len(reply.get("properties", {}))  # R32b: count = kept entries, total = the struct's
        return reply

    @_drv_handler
    def drv_get(self, refs, fields=None, depth=0, angles="RADIANS", max_chars=None):
        """rna_get: many refs in one round trip; a bad ref lands in errors, the others still read."""
        refs = self._drv_list(refs)
        if not refs:
            return {"error": "refs is required: a comma list or JSON list of refs"}
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        angles = str(angles or "RADIANS").upper()
        if angles not in ("RADIANS", "DEGREES"):
            return {"error": f"angles '{angles}' not valid", "valid": ["RADIANS", "DEGREES"]}
        try:
            depth = int(depth or 0)
        except (TypeError, ValueError):
            return {"error": "depth must be an integer"}
        self._drv_skips = set()
        results, errors, droppable = [], [], []
        for ref in refs:
            try:
                owner, leaf, index = self._resolve_ref(ref)
            except _DrvRefError as e:
                errors.append({"ref": ref, **e.reply})
                continue
            entry = {"ref": ref}
            try:
                if leaf is None:
                    if isinstance(owner, bpy.types.bpy_prop_collection):
                        entry["value"] = {"count": len(owner),
                                          "items": [self._encode_value(i, None, depth, None, angles) for i in owner]}
                    else:
                        if isinstance(owner, bpy.types.ID):
                            entry.update(self._id_ref(owner))
                        else:
                            entry["struct"] = owner.bl_rna.identifier
                            entry["path"] = self._drv_path_from_id(owner)
                            if isinstance(getattr(owner, "id_data", None), bpy.types.ID):
                                entry["id"] = self._id_ref(owner.id_data)
                        entry["value"] = self._encode_struct(owner, depth, fields, angles)
                else:
                    if isinstance(owner, bpy.types.ID):
                        entry.update(self._id_ref(owner))
                    elif isinstance(getattr(owner, "id_data", None), bpy.types.ID):
                        entry["id"] = self._id_ref(owner.id_data)
                    key = self._drv_idprop_key(leaf)
                    if key is not None:
                        if key not in owner.keys():
                            errors.append({"ref": ref, "error": f"no ID property '{key}' on {self._drv_name(owner)}",
                                           "valid": list(owner.keys())})
                            continue
                        entry["value"] = self._encode_idprop(owner[key])
                    else:
                        prop = owner.bl_rna.properties[leaf]
                        raw = getattr(owner, leaf)
                        if index is not None:
                            raw = raw[index]
                        entry["value"] = self._encode_value(raw, prop, depth, fields, angles)
            except Exception as e:
                errors.append({"ref": ref, "error": f"{type(e).__name__}: {e}", "rna_error": str(e)})
                continue
            results.append(entry)
            droppable.append((entry, "value", f"results[{len(results) - 1}] ({ref[:60]})", "fields= or depth= or raise max_chars="))
        reply = {"success": True, "results": results, "errors": errors}
        return self._drv_finish(reply, cap, droppable, notes=[self._drv_skip_note()])

    def _drv_apply_sets(self, sets, angles="RADIANS", stop_on_error=True, publish=True, prefix=""):
        """Shared by rna_set and data_new(set=): -> {results, applied, failed, stopped_at}."""
        pairs = list(sets.items()) if isinstance(sets, dict) else [tuple(s) for s in sets]
        results, applied, failed, stopped_at = [], 0, 0, None
        for i, pair in enumerate(pairs):
            if len(pair) != 2:
                item = {"ref": str(pair), "set": False, "error": "each item must be [ref, value]"}
            else:
                ref, value = pair
                ref = f"{prefix}{ref}" if prefix else str(ref)
                item = {"ref": ref, "set": False}
                try:
                    owner, leaf, index = self._resolve_ref(ref, keep_prop=True)
                except _DrvRefError as e:
                    item.update(e.reply)
                else:
                    if leaf is None:
                        item["error"] = f"'{ref}' ends on a struct; rna_set needs a property path"
                    else:
                        err = self._drv_assign(owner, leaf, index, value, angles)
                        if err:
                            item.update(err)
                        else:
                            item["set"] = True
                            key = self._drv_idprop_key(leaf)
                            with suppress(Exception):
                                if key is not None:
                                    item["value_after"] = self._encode_idprop(owner[key])
                                else:
                                    raw = getattr(owner, leaf)
                                    item["value_after"] = self._encode_value(raw[index] if index is not None else raw,
                                                                             owner.bl_rna.properties[leaf], 0, None, angles)
                            if publish and key is None:
                                with suppress(Exception):
                                    bpy.msgbus.publish_rna(key=(getattr(bpy.types, owner.bl_rna.identifier), leaf))
            results.append(item)
            if item["set"]:
                applied += 1
            else:
                failed += 1
                if stop_on_error:
                    stopped_at = i
                    break
        return {"results": results, "applied": applied, "failed": failed, "stopped_at": stopped_at}

    @_drv_handler
    def drv_set(self, sets, angles="RADIANS", stop_on_error=True, publish=True):
        """rna_set: sets = [[ref, value], ...] or {ref: value}; success when failed == 0."""
        sets, err = self._json_arg(sets, "sets")
        if err:
            return err
        if not isinstance(sets, (list, dict)) or not sets:
            return {"error": "sets must be a non-empty JSON list of [ref, value] pairs or an object {ref: value}"}
        angles = str(angles or "RADIANS").upper()
        if angles not in ("RADIANS", "DEGREES"):
            return {"error": f"angles '{angles}' not valid", "valid": ["RADIANS", "DEGREES"]}
        outcome = self._drv_apply_sets(sets, angles, bool(stop_on_error), bool(publish))
        return {"success": outcome["failed"] == 0, **outcome}

    @_drv_handler
    def drv_new(self, collection, args=None, kwargs=None, link_to=None, set=None):
        """data_new: bpy.data.<collection>.new(...) validated against the live RNA signature."""
        collection = str(collection or "").strip()
        cols = self._drv_data_collections()
        with_new = [c for c in cols if bpy.data.bl_rna.properties[c].srna is not None
                    and "new" in bpy.data.bl_rna.properties[c].srna.functions]
        if collection not in cols:
            return {"error": f"unknown bpy.data collection '{collection}'", "valid": with_new,
                    "closest": self._drv_closest(collection, cols)}
        if collection not in with_new:
            return {"error": f"bpy.data.{collection} has no new(); IDs of this type come from import / load tools",
                    "valid": with_new}
        args, err = self._json_arg(args, "args")
        if err:
            return err
        kwargs, err = self._json_arg(kwargs, "kwargs")
        if err:
            return err
        args = list(args or [])
        kwargs = dict(kwargs or {})
        fn = bpy.data.bl_rna.properties[collection].srna.functions["new"]
        params = [p for p in fn.parameters if not p.is_output]
        names = [p.identifier for p in params]
        if len(args) > len(params):
            return {"error": f"{collection}.new takes {len(params)} arguments {names}, got {len(args)}", "valid": names}
        call = dict(zip(names, args))
        for k, v in kwargs.items():
            if k not in names:
                return {"error": f"unknown argument '{k}' for {collection}.new", "valid": names,
                        "closest": self._drv_closest(k, names)}
            call[k] = v
        missing = [p.identifier for p in params if p.is_required and p.identifier not in call and p.type != 'POINTER']
        if missing:
            return {"error": f"{collection}.new is missing required argument(s) {missing}", "valid": names}
        for p in params:
            if p.type == 'POINTER' and p.identifier in call:
                target, perr = self._drv_pointer_value(p, call[p.identifier])
                if perr:
                    return {"error": f"argument '{p.identifier}': {perr['error']}", **{k: v for k, v in perr.items() if k != 'error'}}
                call[p.identifier] = target
        try:
            idb = getattr(bpy.data, collection).new(**call)
        except Exception as e:
            return {"error": f"{collection}.new failed: {e}", "rna_error": str(e),
                    "params": [[p.identifier, p.type, bool(p.is_required)] for p in params]}
        reply = {"success": True, **self._id_ref(idb), "linked_to": None}
        if collection == "objects":
            target = None
            if link_to in (None, "", "scene"):
                target = bpy.context.scene.collection
            else:
                try:
                    root, rest = self._resolve_root(str(link_to))
                except _DrvRefError as e:
                    bpy.data.objects.remove(idb)
                    return {"error": f"link_to: {e.reply['error']}", **{k: v for k, v in e.reply.items() if k != 'error'}}
                if isinstance(root, bpy.types.Scene):
                    target = root.collection
                elif isinstance(root, bpy.types.Collection) and not rest:
                    target = root
                else:
                    bpy.data.objects.remove(idb)
                    return {"error": f"link_to must be \"scene\", \"COLLECTION:Name\" or \"#uid\" of a collection, got '{link_to}'"}
            try:
                target.objects.link(idb)
                reply["linked_to"] = target.name
            except Exception as e:
                reply["linked_to"] = None
                reply["link_error"] = str(e)
        if set:
            sets, err = self._json_arg(set, "set")
            if err:
                reply["set"] = {"applied": 0, "failed": 0, "results": [], **err}
            else:
                outcome = self._drv_apply_sets(sets, "RADIANS", False, True, prefix=f"#{idb.session_uid}/")
                reply["set"] = {"applied": outcome["applied"], "failed": outcome["failed"], "results": outcome["results"]}
        return reply

    @_drv_handler
    def drv_remove(self, refs, do_unlink=True, purge_orphans=False):
        """data_remove: per ref {ref, removed, users_before, error?}; purge_orphans runs orphans_purge afterwards."""
        refs = self._drv_list(refs)
        if not refs:
            return {"error": "refs is required: a comma list or JSON list of ID refs"}
        idmap = self._id_type_map()
        results = []
        for ref in refs:
            item = {"ref": ref, "removed": False, "users_before": None}
            try:
                root, rest = self._resolve_root(ref)
            except _DrvRefError as e:
                item.update(e.reply)
                results.append(item)
                continue
            if rest or not isinstance(root, bpy.types.ID):
                item["error"] = f"'{ref}' must name an ID (#uid or IDTYPE:Name), not a path"
                results.append(item)
                continue
            item.update(self._id_ref(root))
            item["users_before"] = int(root.users)
            coll = idmap.get(root.id_type)
            if coll is None:
                item["error"] = f"no bpy.data collection known for id_type {root.id_type}"
                results.append(item)
                continue
            if root.users and not do_unlink:
                item["error"] = f"{self._drv_name(root)} has {root.users} user(s); pass do_unlink=True to remove it anyway"
                results.append(item)
                continue
            try:
                getattr(bpy.data, coll).remove(root, do_unlink=bool(do_unlink))
                item["removed"] = True
            except Exception as e:
                item["error"] = f"remove failed: {e}"
                item["rna_error"] = str(e)
            results.append(item)
        reply = {"success": all(r["removed"] for r in results), "results": results, "purged": None}
        if purge_orphans:
            before = sum(len(getattr(bpy.data, c)) for c in self._drv_data_collections())
            try:
                bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
                reply["purged"] = before - sum(len(getattr(bpy.data, c)) for c in self._drv_data_collections())
            except Exception as e:
                reply["purge_error"] = str(e)
        return reply

    def _drv_where_match(self, idb, path, op, value):
        """One [path, op, value] clause; a path with [] means any item of that collection."""
        if self._drv_unsafe_path(path):
            return False
        if "[]" in path:
            head, _, tail = path.partition("[]")
            try:
                coll = idb.path_resolve(head, False)
            except Exception:
                return op == "exists" and value is False
            tail = tail.lstrip(".")
            return any(self._drv_where_match(item, tail, op, value) for item in coll) if tail else \
                (len(coll) > 0 if op == "exists" else any(self._drv_compare(self._encode_value(i), op, value) for i in coll))
        try:
            raw = idb.path_resolve(path) if path else idb
        except Exception:
            return op == "exists" and value is False
        if op == "exists":
            # present = the path resolves to a non-None value; ["parent", "exists", False] = no parent
            return (raw is not None) == (True if value is None else bool(value))
        return self._drv_compare(self._encode_value(raw), op, value)

    @staticmethod
    def _drv_compare(actual, op, value):
        try:
            if op == "==":
                return actual == value
            if op == "!=":
                return actual != value
            if op == "<":
                return actual < value
            if op == "<=":
                return actual <= value
            if op == ">":
                return actual > value
            if op == ">=":
                return actual >= value
            if op == "in":
                return actual in value
            if op == "contains":
                return value in actual
        except Exception:
            return False
        return False

    @_drv_handler
    def drv_find(self, id_type="OBJECT", name=None, where=None, scope="SCENE", fields="name,type", limit=100, max_chars=None):
        """find: glob on name + where clauses [[path, op, value], ...]; scope SCENE only for OBJECT."""
        import fnmatch
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        self._drv_skips = set()
        idmap = self._id_type_map()
        idt = str(id_type or "OBJECT").upper()
        coll = idmap.get(idt)
        if coll is None:
            return {"error": f"unknown id_type '{id_type}'", "valid": sorted(idmap), "closest": self._drv_closest(idt, idmap)}
        where, err = self._json_arg(where, "where")
        if err:
            return err
        clauses = []
        for clause in (where or []):
            if not isinstance(clause, (list, tuple)) or len(clause) != 3:
                return {"error": f"where clause must be [path, op, value], got {clause!r}"}
            path, op, value = clause
            if op not in self._DRV_WHERE_OPS:
                return {"error": f"where op '{op}' not valid", "valid": list(self._DRV_WHERE_OPS)}
            clauses.append((str(path), op, value))
        try:
            limit = int(limit if limit is not None else 100)
        except (TypeError, ValueError):
            return {"error": "limit must be an integer"}
        scope = str(scope or "SCENE").upper()
        if scope not in ("SCENE", "DATA"):
            return {"error": f"scope '{scope}' not valid", "valid": ["SCENE", "DATA"]}
        if idt == "OBJECT" and scope == "SCENE":
            candidates = list(bpy.context.scene.objects)
        else:
            candidates = list(getattr(bpy.data, coll))
            scope = "DATA"
        if name:
            candidates = [i for i in candidates if fnmatch.fnmatchcase(i.name, str(name))]
        matches = [i for i in candidates if all(self._drv_where_match(i, p, o, v) for p, o, v in clauses)]
        items = []
        for idb in matches[:max(limit, 0)]:
            entry = self._id_ref(idb)
            if fields:
                entry.update(self._encode_struct(idb, 0, fields, "RADIANS"))
            items.append(entry)
        reply = {"success": True, "id_type": idt, "scope": scope, "count": len(items), "total": len(matches), "items": items}
        self._drv_finish(reply, cap, items_key="items", items_hint="fields= or limit= or raise max_chars=", notes=[self._drv_skip_note()])
        reply["count"] = len(reply["items"])
        return reply

    @_drv_handler
    def drv_context(self, fields=None):
        """context_info: what call_operator's context declaration is applied against (window AND background, R20)."""
        ctx = bpy.context
        ref = lambda ob: self._id_ref(ob) if ob is not None else None  # noqa: E731
        win = ctx.window
        screen = ctx.screen or (win.screen if win is not None else None)
        info = {"mode": ctx.mode, "active_object": ref(ctx.view_layer.objects.active if ctx.view_layer else None),
                "selected": [self._id_ref(o) for o in (ctx.selected_objects or [])],
                "scene": ref(ctx.scene), "view_layer": ctx.view_layer.name if ctx.view_layer else None,
                "window": win is not None, "background": bool(bpy.app.background),
                "areas": [{"type": a.type, "regions": [r.type for r in a.regions]} for a in (screen.areas if screen else [])],
                "frame_current": ctx.scene.frame_current if ctx.scene else None,
                "edit_object": ref(getattr(ctx, "edit_object", None)), "pose_object": ref(getattr(ctx, "pose_object", None))}
        wanted = self._drv_list(fields)
        if wanted:
            missing = [k for k in wanted if k not in info]
            info = {k: info[k] for k in wanted if k in info}
            if missing:
                info["missing"] = missing
        return {"success": True, **info}

    # -- L2: context declaration, operators, methods, snapshots, diffs (12.2.3, R19, R20, R33) ---

    # The 16 temp_override keys the driver accepts (R19: Blender takes ANY key silently, so the whitelist is ours)
    _DRV_CONTEXT_KEYS = ("window", "screen", "area", "region", "scene", "view_layer", "object", "active_object",
                         "selected_objects", "selected_editable_objects", "edit_object", "pose_object",
                         "active_bone", "active_pose_bone", "modifier", "point_cache")
    _DRV_DECL_KEYS = ("mode", "active", "selected", "area", "region", "scene", "keep", "extra")
    _DRV_SNAPSHOT_FIELDS = "name,type,parent,location,rotation_euler,scale,hide_viewport,hide_render,users"
    _DRV_SNAPSHOT_INCLUDE = ("modifiers", "constraints", "material_slots", "vertex_groups", "children")

    def _drv_object_ref(self, ref, label):
        """A context declaration ref -> the Object it names (error for anything else)."""
        root, rest = self._resolve_root(str(ref))
        if rest or not isinstance(root, bpy.types.Object):
            raise _DrvRefError({"error": f"{label} must name an OBJECT (#uid or OBJECT:Name), got '{ref}'"})
        return root

    def _drv_context_value(self, value):
        """An `extra` value: refs (str / {uid}) resolve to IDs or structs, lists element-wise, others pass through."""
        if isinstance(value, dict) and "uid" in value:
            value = f"#{value['uid']}"
        if isinstance(value, str) and (value.startswith("#") or ":" in value or value.split("/")[0] in self._DRV_ROOTS):
            owner, leaf, index = self._resolve_ref(value)
            return owner if leaf is None else getattr(owner, leaf)
        if isinstance(value, (list, tuple)):
            return [self._drv_context_value(v) for v in value]
        return value

    @contextmanager
    def _apply_context(self, decl):
        """
        Apply a call_operator context declaration (12.2.3) and yield `applied` = {mode, active, selected,
        area, region, extra_keys}. active/selected -> real selection inside _selection_scope; mode ->
        object.mode_set inside _mode_restore; area/region -> the first matching area of _gui_window() in a
        temp_override (refused under bpy.app.background, R20); extra -> temp_override keys against the
        R19 whitelist. keep=True skips every restore. Resolution happens BEFORE any side effect: a bad
        declaration raises _DrvRefError and nothing was touched.
        """
        from contextlib import ExitStack
        decl = dict(decl or {})
        unknown = [k for k in decl if k not in self._DRV_DECL_KEYS]
        if unknown:
            raise _DrvRefError({"error": f"unknown context key(s) {unknown}", "valid": list(self._DRV_DECL_KEYS),
                                "closest": self._drv_closest(unknown[0], self._DRV_DECL_KEYS)})
        applied = {"mode": None, "active": None, "selected": [], "area": None, "region": None, "extra_keys": []}
        keep = bool(decl.get("keep"))
        active = self._drv_object_ref(decl["active"], "active") if decl.get("active") else None
        selected = [self._drv_object_ref(r, "selected[]") for r in self._drv_list(decl.get("selected")) or []]
        scene = None
        if decl.get("scene"):
            scene, rest = self._resolve_root(str(decl["scene"]))
            if rest or not isinstance(scene, bpy.types.Scene):
                raise _DrvRefError({"error": f"scene must name a SCENE, got '{decl['scene']}'"})
        extra = decl.get("extra") or {}
        if not isinstance(extra, dict):
            raise _DrvRefError({"error": "extra must be a JSON object of temp_override keys", "valid": list(self._DRV_CONTEXT_KEYS)})
        for key in extra:
            if key not in self._DRV_CONTEXT_KEYS:
                raise _DrvRefError({"error": f"unknown temp_override key '{key}' in extra", "valid": list(self._DRV_CONTEXT_KEYS),
                                    "closest": self._drv_closest(key, self._DRV_CONTEXT_KEYS)})
        override = {k: self._drv_context_value(v) for k, v in extra.items()}
        mode = str(decl["mode"]).upper() if decl.get("mode") else None
        area = region = None
        if decl.get("area") or decl.get("region"):
            if bpy.app.background:
                raise _DrvRefError({"error": "Viewport context needs a GUI session: area/region were declared under --background",
                                    "background": True})
            win = self._gui_window()
            screen = win.screen if win is not None else None
            want_area = str(decl.get("area") or "VIEW_3D").upper()
            area = next((a for a in (screen.areas if screen else []) if a.type == want_area), None)
            if area is None:
                raise _DrvRefError({"error": f"no {want_area} area in the current screen",
                                    "valid": sorted({a.type for a in (screen.areas if screen else [])})})
            want_region = str(decl.get("region") or "WINDOW").upper()
            region = next((r for r in area.regions if r.type == want_region), None)
            if region is None:
                raise _DrvRefError({"error": f"no {want_region} region in the {want_area} area",
                                    "valid": sorted({r.type for r in area.regions})})
            override.update({"window": win, "screen": screen, "area": area, "region": region})
            applied["area"], applied["region"] = want_area, want_region
        if scene is not None:
            override["scene"] = scene
        applied["extra_keys"] = sorted(extra)
        with ExitStack() as stack:
            if not keep:
                stack.enter_context(self._selection_scope())
                if mode:
                    stack.enter_context(self._mode_restore())
            if active is not None or selected:
                view_layer = bpy.context.view_layer
                for o in view_layer.objects:
                    if o.select_get():
                        o.select_set(False)
                for o in selected:
                    with suppress(Exception):
                        o.select_set(True)
                target = active if active is not None else (selected[0] if selected else None)
                if target is not None:
                    view_layer.objects.active = target
                    applied["active"] = target.session_uid
                applied["selected"] = [o.session_uid for o in selected]
                override.setdefault("object", target)
                override.setdefault("active_object", target)
                override.setdefault("selected_objects", list(selected) or ([target] if target else []))
                override.setdefault("selected_editable_objects", override["selected_objects"])
            if mode:
                if bpy.context.view_layer.objects.active is None:
                    raise _DrvRefError({"error": f"mode {mode} needs an active object: declare active"})
                try:
                    bpy.ops.object.mode_set(mode=mode)
                except Exception as e:
                    raise _DrvRefError({"error": f"could not enter mode {mode}: {e}", "rna_error": str(e),
                                        "valid": self._drv_enum_valid(bpy.ops.object.mode_set.get_rna_type().properties["mode"], str(e))})
                applied["mode"] = mode
            with bpy.context.temp_override(**override):
                yield applied

    @_drv_handler
    def drv_op(self, op, params=None, context=None, exec_context="EXEC_DEFAULT", undo=True):
        """call_operator: refused list, INVOKE gate, context declaration, poll first, teaching errors, reports."""
        t0 = time.perf_counter()
        op = str(op or "").strip()
        mod_name, _, op_name = op.partition(".")
        params, err = self._json_arg(params, "params")
        if err:
            return err
        context, err = self._json_arg(context, "context")
        if err:
            return err
        params = dict(params or {})
        if not isinstance(context, (dict, type(None))):
            return {"error": "context must be a JSON object (12.2.3)", "valid": list(self._DRV_DECL_KEYS)}
        if op in _DRV_REFUSED_OPS and (op != "preferences.addon_disable" or str(params.get("module", "")) in (__name__, __name__.split(".")[0])):
            return {"error": f"operator '{op}' is refused by the driver: it would end the session or hang headless "
                             f"(use the dedicated tool: quit_blender, set_frame/playblast, load_blend/save_blend)", "refused": True}
        fn = getattr(getattr(bpy.ops, mod_name, None), op_name, None) if mod_name and op_name else None
        if fn is None or not self._op_exists(fn):
            return {"error": f"unknown operator '{op}'", "closest": self._drv_closest(op, [o[0] for o in self._drv_ops_catalogue()])}
        exec_context = str(exec_context or "EXEC_DEFAULT").upper()
        if exec_context != "EXEC_DEFAULT" and (bpy.app.background or not (context or {}).get("area")):
            return {"error": f"exec_context {exec_context} is refused: INVOKE_* needs a GUI session and context.area; use EXEC_DEFAULT",
                    "background": bool(bpy.app.background), "valid": ["EXEC_DEFAULT"]}
        rt = fn.get_rna_type()
        known = [p.identifier for p in rt.properties if p.identifier != "rna_type"]
        unknown = [k for k in params if k not in known]
        buf = io.StringIO()
        try:
            with self._apply_context(context) as applied:
                if unknown:
                    reply = {"error": f"unknown parameter(s) {unknown} for {op}", "closest": self._drv_closest(unknown[0], known),
                             "valid": known, "context_applied": applied}
                    try:
                        fn(exec_context, **params)  # conversion (or the poll) fails before anything runs: the RNA text, verbatim
                    except Exception as e:
                        reply["rna_error"] = str(e)
                    return reply
                if not fn.poll():
                    reply = {"error": f"operator {op} cannot run in this context (poll failed)", "context_applied": applied}
                    try:
                        fn(exec_context, **params)
                    except Exception as e:
                        reply["rna_error"] = str(e)
                        reply["poll_message"] = str(e).partition("failed, ")[2] or str(e)
                    else:
                        reply["poll_message"] = "poll() returned False"
                    return reply
                if undo:
                    with suppress(Exception):
                        bpy.ops.ed.undo_push(message=f"mcp:{op}")
                try:
                    with redirect_stdout(buf):
                        res = fn(exec_context, **params)
                except Exception as e:
                    reply = {"error": f"{op} failed: {e}", "rna_error": str(e), "context_applied": applied,
                             "reports": [l for l in buf.getvalue().splitlines() if l.strip()]}
                    text = str(e)
                    if "not found in (" in text:
                        m = re.search(r'enum "([^"]*)"', text)
                        prop = next((p for p in rt.properties if p.type == 'ENUM' and m and str(params.get(p.identifier)) == m.group(1)), None)
                        reply["valid"] = self._drv_enum_valid(prop, text) if prop else self._drv_enum_valid(rt.properties["rna_type"], text)
                    if "unrecognized" in text:
                        m = re.search(r'keyword "([^"]*)"', text)
                        reply["closest"] = self._drv_closest(m.group(1) if m else "", known)
                    return reply
                result = sorted(res)
        except _DrvRefError as e:
            reply = dict(e.reply)
            reply.setdefault("context_applied", {"mode": None, "active": None, "selected": [], "area": None, "region": None, "extra_keys": []})
            return reply
        reply = {"success": True, "result": result, "reports": [l for l in buf.getvalue().splitlines() if l.strip()],
                 "context_applied": applied, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                 "modal": "RUNNING_MODAL" in result or "PASS_THROUGH" in result}
        if reply["modal"]:
            reply["warning"] = "the operator is modal: nothing waits for it, the reply carries its first return value only"
        return reply

    @_drv_handler
    def drv_call(self, ref, method, args=None, kwargs=None, depsgraph=False, max_chars=None):
        """call_method: an RNA function (validated against bl_rna.functions) or a plain Python method of the struct."""
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        args, err = self._json_arg(args, "args")
        if err:
            return err
        kwargs, err = self._json_arg(kwargs, "kwargs")
        if err:
            return err
        args = list(args or [])
        kwargs = dict(kwargs or {})
        method = str(method or "").strip()
        owner, leaf, index = self._resolve_ref(ref)
        if leaf is not None:
            return {"error": f"'{ref}' ends on a property; call_method needs an ID, struct or collection ref"}
        rna = getattr(owner, "bl_rna", None)
        funcs = rna.functions if rna is not None else {}
        fn_rna = funcs.get(method) if rna is not None else None
        target = getattr(owner, method, None)
        if fn_rna is None and not callable(target):
            names = [f.identifier for f in funcs] + [n for n in dir(owner) if not n.startswith("_") and callable(getattr(owner, n, None))]
            return {"error": f"'{method}' is not a method of {self._drv_name(owner)}", "closest": self._drv_closest(method, names),
                    "valid": [f.identifier for f in funcs]}
        call = dict(kwargs)
        if fn_rna is not None:
            params = [p for p in fn_rna.parameters if not p.is_output]
            names = [p.identifier for p in params]
            if len(args) > len(params):
                return {"error": f"{method} takes {len(params)} argument(s) {names}, got {len(args)}", "valid": names}
            call.update(zip(names, args))
            for k in kwargs:
                if k not in names:
                    return {"error": f"unknown argument '{k}' for {method}", "valid": names, "closest": self._drv_closest(k, names)}
            for p in params:
                if p.type == 'POINTER':
                    if p.identifier not in call and p.fixed_type.identifier == "Depsgraph" and depsgraph:
                        call[p.identifier] = bpy.context.evaluated_depsgraph_get()
                    elif p.identifier in call and isinstance(call[p.identifier], (str, dict)):
                        # RA-12: the same ref grammar and fixed_type check as drv_new / drv_set
                        target_id, perr = self._drv_pointer_value(p, call[p.identifier])
                        if perr:
                            return perr
                        call[p.identifier] = target_id
            missing = [p.identifier for p in params if p.is_required and p.identifier not in call]
            if missing:
                hint = "; pass depsgraph=True" if any(p.fixed_type.identifier == "Depsgraph" for p in params if p.type == 'POINTER' and p.identifier in missing) else ""
                return {"error": f"{method} is missing required argument(s) {missing}{hint}", "valid": names,
                        "params": [[p.identifier, p.type, bool(p.is_required)] for p in params]}
            args = []
        try:
            value = target(*args, **call)
        except Exception as e:
            reply = {"error": f"{method} failed: {e}", "rna_error": str(e)}
            if fn_rna is not None:
                reply["params"] = [[p.identifier, p.type, bool(p.is_required)] for p in fn_rna.parameters if not p.is_output]
            return reply
        self._drv_skips = set()
        reply = {"success": True, "value": self._encode_value(value, None, 0)}
        return self._drv_finish(reply, cap, [(reply, "value", "value", "raise max_chars=")], notes=[self._drv_skip_note()])

    @staticmethod
    def _drv_snapshot_store():
        """Named scene snapshots, kept in driver_namespace so an add-on reload does not drop them."""
        return bpy.app.driver_namespace.setdefault("blendermcp_driver_snapshots", {})

    def _drv_capture(self, scope, types, fields, include, depth, angles):
        """The snapshot body: {captured_at, scope, count, ids: {uid: {...}}, tree: [...]}."""
        if scope == "SCENE":
            ids = list(bpy.context.scene.objects)
        else:
            ids = [i for c in self._drv_data_collections() for i in getattr(bpy.data, c)]
        if types:
            ids = [i for i in ids if i.id_type in types]
        entries = {}
        for idb in ids:
            entry = self._id_ref(idb)
            values = self._encode_struct(idb, 0, fields, angles)
            values.pop("missing", None)
            entry.update(values)
            for name in include or []:
                coll = getattr(idb, name, None)
                if coll is None or not hasattr(coll, "__len__"):
                    continue
                items = []
                for item in coll:
                    if isinstance(item, bpy.types.ID):
                        items.append(self._id_ref(item))
                    elif item is None:
                        items.append(None)
                    else:
                        items.append(self._encode_struct(item, 0, "name,type", angles))
                        items[-1].pop("missing", None)
                entry[name] = items
            entries[str(idb.session_uid)] = entry
        parents = {}
        for idb in ids:
            p = getattr(idb, "parent", None)
            parents[idb.session_uid] = p.session_uid if p is not None and str(p.session_uid) in entries else None
        children = {}
        for uid, pu in parents.items():
            children.setdefault(pu, []).append(uid)

        def node(uid, level):
            kids = children.get(uid, []) if level < depth else []
            return {"uid": uid, "children": [node(k, level + 1) for k in kids]}

        tree = [node(uid, 0) for uid in parents if parents[uid] is None]
        return {"captured_at": round(time.time(), 3), "scope": scope, "count": len(entries), "ids": entries, "tree": tree}

    @_drv_handler
    def drv_snapshot(self, name, scope="SCENE", types=None, fields=None, include=None, depth=1, return_tree=True,
                     angles="RADIANS", max_chars=None):
        """scene_snapshot: a uid-keyed tree stored under driver_namespace['blendermcp_driver_snapshots'][name]."""
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        name = str(name or "").strip()
        if not name:
            return {"error": "name is required (the snapshot is addressed by it in scene_diff)"}
        scope = str(scope or "SCENE").upper()
        if scope not in ("SCENE", "DATA"):
            return {"error": f"scope '{scope}' not valid", "valid": ["SCENE", "DATA"]}
        enum_ids = sorted(self._id_type_map())
        types = [str(t).upper() for t in self._drv_list(types) or []]
        bad = [t for t in types if t not in enum_ids]
        if bad:
            return {"error": f"unknown id_type(s) {bad} in types", "valid": enum_ids}
        include = self._drv_list(include) or []
        bad = [i for i in include if i not in self._DRV_SNAPSHOT_INCLUDE]
        if bad:
            return {"error": f"unknown include(s) {bad}", "valid": list(self._DRV_SNAPSHOT_INCLUDE)}
        fields = ",".join(self._drv_list(fields) or []) or self._DRV_SNAPSHOT_FIELDS
        angles = str(angles or "RADIANS").upper()
        if angles not in ("RADIANS", "DEGREES"):
            return {"error": f"angles '{angles}' not valid", "valid": ["RADIANS", "DEGREES"]}
        try:
            depth = int(depth if depth is not None else 1)
        except (TypeError, ValueError):
            return {"error": "depth must be an integer"}
        self._drv_skips = set()
        params = {"scope": scope, "types": types, "fields": fields, "include": include, "depth": depth, "angles": angles}
        data = self._drv_capture(scope, types, fields, include, depth, angles)
        self._drv_snapshot_store()[name] = {"params": params, "data": data}
        if not return_tree:
            reply = {"success": True, "name": name, "count": data["count"]}
            reply["chars"] = len(json.dumps(reply)) + 12 + len(str(len(json.dumps(reply))))
            return reply
        reply = {"success": True, "name": name, **json.loads(json.dumps(data, default=str))}
        droppable = [(reply["ids"], k, "ids", "fields= or types= or raise max_chars=") for k in list(reply["ids"])]
        droppable.append((reply, "tree", "tree", "return_tree=False or raise max_chars="))
        return self._drv_finish(reply, cap, droppable, notes=[self._drv_skip_note()])

    @_drv_handler
    def drv_diff(self, a, b="NOW", max_chars=None):
        """scene_diff: added / removed / renamed (by uid) / changed between two snapshots (b="NOW" = the live scene)."""
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        store = self._drv_snapshot_store()
        a = str(a or "").strip()
        if a not in store:
            return {"error": f"no snapshot named '{a}'", "valid": sorted(store), "closest": self._drv_closest(a, store)}
        b = str(b or "NOW").strip()
        if b.upper() == "NOW":
            b = "NOW"
            p = store[a]["params"]
            self._drv_skips = set()
            bdata = self._drv_capture(p["scope"], p["types"], p["fields"], p["include"], p["depth"], p["angles"])
        elif b in store:
            bdata = store[b]["data"]
        else:
            return {"error": f"no snapshot named '{b}'", "valid": sorted(store) + ["NOW"], "closest": self._drv_closest(b, store)}
        ia, ib = store[a]["data"]["ids"], bdata["ids"]
        ref = lambda e: {"uid": e["uid"], "id_type": e["id_type"], "name": e["name"]}  # noqa: E731
        added = [ref(ib[k]) for k in ib if k not in ia]
        removed = [ref(ia[k]) for k in ia if k not in ib]
        renamed, changed = [], {}
        for k in ia:
            if k not in ib:
                continue
            ea, eb = ia[k], ib[k]
            if ea["name"] != eb["name"]:
                renamed.append({"uid": ea["uid"], "before": ea["name"], "after": eb["name"]})
            diff = {}
            for field in set(ea) | set(eb):
                if field in ("uid", "id_type", "name", "embedded"):
                    continue
                va, vb = ea.get(field), eb.get(field)
                if json.dumps(va, sort_keys=True, default=str) != json.dumps(vb, sort_keys=True, default=str):
                    diff[field] = [va, vb]
            if diff:
                changed[k] = diff
        reply = {"success": True, "a": a, "b": b, "added": added, "removed": removed, "renamed": renamed, "changed": changed}
        # one group: a tiny cap must still leave a <= 200-char envelope with ONE truncated line
        droppable = [(reply["changed"], k, "diff", "raise max_chars=") for k in list(changed)]
        droppable += [(reply, k, "diff", "raise max_chars=") for k in ("added", "removed", "renamed")]
        return self._drv_finish(reply, cap, droppable, notes=[self._drv_skip_note()])

    # -- L3: transactions and batches (R22 marker sequence, R27 wall cap, R30 item rule, G9 dispatch) ---

    _DRV_TXN_KEY = "blendermcp_driver_txn"

    @staticmethod
    def _drv_undo_available():
        try:
            return bool(bpy.ops.ed.undo.poll())
        except Exception:
            return False

    @staticmethod
    def _drv_next_seq():
        ns = bpy.app.driver_namespace
        ns["blendermcp_driver_seq"] = int(ns.get("blendermcp_driver_seq", 0)) + 1
        return ns["blendermcp_driver_seq"]

    @staticmethod
    def _drv_mark(marker, label):
        """R22 three-step BEGIN: push the pre-state, set the Scene idprop marker, push the marked state. Returns seq.
        The pre push is what stops a rollback from walking into edits made before BEGIN."""
        seq = BlenderMCPServer._drv_next_seq()
        bpy.ops.ed.undo_push(message=f"mcp:{label}:pre")
        bpy.context.scene[marker] = seq
        bpy.ops.ed.undo_push(message=f"mcp:{label}")
        return seq

    @staticmethod
    def _drv_unwind(marker, seq, label="txn"):
        """
        ed.undo while the marker still reads seq (cap = prefs undo_steps). Returns (undo_calls, marker_present).
        R-B2-42 (F-B2-3): a closing undo_push first, so Python-only mutations made since the last push
        (drv_set, set_origin, ...) are captured and the unwind reverts them too; both callers route here.
        """
        with suppress(Exception):
            bpy.ops.ed.undo_push(message=f"mcp:{label}:rollback")
        cap = max(int(bpy.context.preferences.edit.undo_steps), 1)
        n = 0
        while bpy.context.scene.get(marker) == seq and n < cap:
            if not BlenderMCPServer._drv_undo_available():
                break
            bpy.ops.ed.undo()
            n += 1
        present = bpy.context.scene.get(marker) == seq
        if present:  # undo could not reach it: drop the marker so the scene is not left tagged
            with suppress(Exception):
                del bpy.context.scene[marker]
        return n, present

    @_drv_handler
    def drv_transaction(self, action, name=None):
        """transaction: BEGIN / COMMIT / ROLLBACK / STATUS on the Scene marker _mcp_txn (R22)."""
        action = str(action or "").strip().upper()
        valid = ["BEGIN", "COMMIT", "ROLLBACK", "STATUS"]
        if action not in valid:
            return {"error": f"action '{action}' not valid", "valid": valid}
        ns = bpy.app.driver_namespace
        state = ns.get(self._DRV_TXN_KEY)
        marker_present = state is not None and bpy.context.scene.get("_mcp_txn") == state.get("seq")
        base = {"success": True, "action": action, "name": state["name"] if state else None,
                "undo_calls": state["undo_calls"] if state else 0, "marker_present": marker_present,
                "undo_available": self._drv_undo_available(), "open": state is not None}
        if action == "STATUS":
            return base
        if action == "BEGIN":
            if state is not None:
                return {"error": f"transaction '{state['name']}' is open; COMMIT or ROLLBACK it first", "open": True,
                        "name": state["name"]}
            name = str(name or "").strip() or f"txn{int(ns.get('blendermcp_driver_seq', 0)) + 1}"
            seq = self._drv_mark("_mcp_txn", name)
            ns[self._DRV_TXN_KEY] = {"name": name, "seq": seq, "undo_calls": 0}
            return {**base, "name": name, "undo_calls": 0, "marker_present": True, "open": True,
                    "undo_available": self._drv_undo_available()}
        if state is None:
            return {"error": "no transaction is open; BEGIN one first", "open": False}
        if action == "COMMIT":
            with suppress(Exception):
                del bpy.context.scene["_mcp_txn"]
            ns.pop(self._DRV_TXN_KEY, None)
            return {**base, "marker_present": False, "open": False}
        # ROLLBACK
        if not marker_present:
            # RA-9: the marker is already gone (deleted or undone past): nothing can be reverted, say so
            ns.pop(self._DRV_TXN_KEY, None)
            return {"error": "transaction marker not found; nothing reverted", "success": False, "undo_calls": 0,
                    "marker_present": False, "open": False, "name": state["name"],
                    "undo_available": self._drv_undo_available()}
        n, present = self._drv_unwind("_mcp_txn", state["seq"], state["name"])
        ns.pop(self._DRV_TXN_KEY, None)
        reply = {**base, "undo_calls": n, "marker_present": False, "open": False, "undo_available": self._drv_undo_available()}
        if present:
            reply = {"error": "undo is not available in this session; the transaction marker is dropped, nothing was reverted",
                     "undo_available": False, "undo_calls": n, "marker_present": False, "open": False, "name": state["name"]}
        return reply

    @staticmethod
    def _drv_json_path(value, path):
        """'results[0].value.name' over a JSON-like value; raises KeyError / IndexError / TypeError on a miss."""
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\[-?\d+\]", path):
            if token.startswith("["):
                value = value[int(token[1:-1])]
            elif isinstance(value, dict):
                value = value[token]
            else:
                value = getattr(value, token)
        return value

    def _drv_substitute(self, value, results):
        """Replace "$<id>.<path>" / "$<id>" strings by that earlier command's result (recursively)."""
        if isinstance(value, str) and value.startswith("$") and len(value) > 1:
            ident, _, path = value[1:].partition(".")
            if ident not in results:
                raise KeyError(f"'{value}': no earlier command with id '{ident}'" + (f" (known: {sorted(results)})" if results else ""))
            try:
                return self._drv_json_path(results[ident], path) if path else results[ident]
            except Exception as e:
                raise KeyError(f"'{value}': path '{path}' not found in the result of '{ident}' ({type(e).__name__}: {e})")
        if isinstance(value, list):
            return [self._drv_substitute(v, results) for v in value]
        if isinstance(value, dict):
            return {k: self._drv_substitute(v, results) for k, v in value.items()}
        return value

    @_drv_handler
    def drv_batch(self, commands, stop_on_error=True, undo="AUTO", refs=True, max_chars=None):
        """batch: many commands in one round trip through _execute_command_internal (G9), $id.path substitution,
        undo=AUTO marker rollback on the first error when stop_on_error (R22), wall cap between commands (R27)."""
        cap, cerr = self._drv_cap_arg(max_chars)
        if cerr:
            return cerr
        commands, err = self._json_arg(commands, "commands")
        if err:
            return err
        if not isinstance(commands, list) or not commands:
            return {"error": "commands must be a non-empty JSON list of {\"type\": ..., \"params\": {...}, \"id\": ...} objects"}
        if len(commands) > _DRV_BATCH_MAX:
            return {"error": f"batch has {len(commands)} commands; the limit is {_DRV_BATCH_MAX}; split the batch"}
        for i, c in enumerate(commands):
            if not isinstance(c, dict) or not isinstance(c.get("type"), str) or not c.get("type"):
                return {"error": f"commands[{i}] must be an object with a string \"type\" (handler key)"}
            if c.get("params") is not None and not isinstance(c["params"], dict):
                return {"error": f"commands[{i}].params must be a JSON object"}
        undo = str(undo or "AUTO").upper()
        if undo not in ("AUTO", "NONE"):
            return {"error": f"undo '{undo}' not valid", "valid": ["AUTO", "NONE"]}
        stop_on_error = bool(stop_on_error)
        t0 = time.perf_counter()
        seq = self._drv_mark("_mcp_batch", "batch") if undo == "AUTO" else None
        results, by_id, stopped_at, errored, wall_hit = [], {}, None, False, None
        for i, c in enumerate(commands):
            cid = str(c.get("id") or f"c{i}")
            ctype = c["type"]
            if stopped_at is None and i > 0 and time.perf_counter() - t0 > _DRV_BATCH_WALL_S:
                # R27/R39: the wall cap is checked BETWEEN commands and counts as an error for the rollback decision
                stopped_at, wall_hit, errored = i, i, True
            if stopped_at is not None:
                results.append({"id": cid, "type": ctype, "status": "skipped", "elapsed_ms": 0})
                continue
            t1 = time.perf_counter()
            item = {"id": cid, "type": ctype}
            params = dict(c.get("params") or {})
            if ctype == "drv_batch":
                item.update(status="error", error="nested batch refused: flatten the commands into this batch")
            else:
                try:
                    if refs:
                        params = self._drv_substitute(params, by_id)
                except KeyError as e:
                    item.update(status="error", error=str(e.args[0]))
                else:
                    out = self._execute_command_internal({"type": ctype, "params": params})
                    if out.get("status") != "success":
                        item.update(status="error", error=str(out.get("message", "error")))
                    else:
                        res = out.get("result")
                        bad = isinstance(res, dict) and ("error" in res or res.get("success") is False)  # R30
                        item.update(status="error" if bad else "ok", result=res)
                        if bad:
                            item["error"] = str(res.get("error") or "the command reported success: false")
                        else:
                            by_id[cid] = res
            item["elapsed_ms"] = round((time.perf_counter() - t1) * 1000, 2)
            results.append(item)
            if item["status"] == "error":
                errored = True
                if stop_on_error:
                    stopped_at = i
        rolled_back = False
        if seq is not None:
            if errored and stop_on_error:
                n, present = self._drv_unwind("_mcp_batch", seq, "batch")
                rolled_back = n > 0 and not present
            else:
                with suppress(Exception):
                    del bpy.context.scene["_mcp_batch"]
        reply = {"success": not errored, "results": results, "rolled_back": rolled_back,
                 "undo_available": self._drv_undo_available(), "stopped_at": stopped_at,
                 "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}
        if wall_hit is not None:
            skipped = sum(1 for r in results if r["status"] == "skipped")
            reply["error"] = f"wall cap {_DRV_BATCH_WALL_S:g} s reached after command {wall_hit - 1}; {skipped} skipped"
        droppable = [(r, "result", "results", "raise max_chars=") for r in results if "result" in r]
        return self._drv_finish(reply, cap, droppable)

    # ─── PolyHaven handlers (begin) ──────────────────────────────────────────

    def get_polyhaven_categories(self, asset_type):
        """Get categories for a specific asset type from Polyhaven"""
        try:
            if asset_type not in ["hdris", "textures", "models", "all"]:
                return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}

            response = requests.get(f"https://api.polyhaven.com/categories/{asset_type}", headers=REQ_HEADERS)
            if response.status_code == 200:
                return {"categories": response.json()}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def search_polyhaven_assets(self, asset_type=None, categories=None):
        """Search for assets from Polyhaven with optional filtering"""
        try:
            url = "https://api.polyhaven.com/assets"
            params = {}

            if asset_type and asset_type != "all":
                if asset_type not in ["hdris", "textures", "models"]:
                    return {"error": f"Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"}
                params["type"] = asset_type

            if categories:
                params["categories"] = categories

            response = requests.get(url, params=params, headers=REQ_HEADERS)
            if response.status_code == 200:
                # Limit the response size to avoid overwhelming Blender
                assets = response.json()
                # Return only the first 20 assets to keep response size manageable
                limited_assets = {}
                for i, (key, value) in enumerate(assets.items()):
                    if i >= 20:  # Limit to 20 assets
                        break
                    limited_assets[key] = value

                return {"assets": limited_assets, "total_count": len(assets), "returned_count": len(limited_assets)}
            else:
                return {"error": f"API request failed with status code {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def download_polyhaven_asset(self, asset_id, asset_type, resolution="1k", file_format=None):
        try:
            # First get the files information
            files_response = requests.get(f"https://api.polyhaven.com/files/{asset_id}", headers=REQ_HEADERS)
            if files_response.status_code != 200:
                return {"error": f"Failed to get asset files: {files_response.status_code}"}

            files_data = files_response.json()

            # Handle different asset types
            if asset_type == "hdris":
                # For HDRIs, download the .hdr or .exr file
                if not file_format:
                    file_format = "hdr"  # Default format for HDRIs

                if "hdri" in files_data and resolution in files_data["hdri"] and file_format in files_data["hdri"][resolution]:
                    file_info = files_data["hdri"][resolution][file_format]
                    file_url = file_info["url"]

                    # For HDRIs, we need to save to a temporary file first
                    # since Blender can't properly load HDR data directly from memory
                    with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                        # Download the file
                        response = requests.get(file_url, headers=REQ_HEADERS)
                        if response.status_code != 200:
                            return {"error": f"Failed to download HDRI: {response.status_code}"}

                        tmp_file.write(response.content)
                        tmp_path = tmp_file.name

                    try:
                        # Create a new world if none exists
                        if not bpy.data.worlds:
                            bpy.data.worlds.new("World")

                        world = bpy.data.worlds[0]
                        world.use_nodes = True
                        node_tree = world.node_tree

                        # Clear existing nodes
                        for node in node_tree.nodes:
                            node_tree.nodes.remove(node)

                        # Create nodes
                        tex_coord = node_tree.nodes.new(type='ShaderNodeTexCoord')
                        tex_coord.location = (-800, 0)

                        mapping = node_tree.nodes.new(type='ShaderNodeMapping')
                        mapping.location = (-600, 0)

                        # Load the image from the temporary file
                        env_tex = node_tree.nodes.new(type='ShaderNodeTexEnvironment')
                        env_tex.location = (-400, 0)
                        env_tex.image = bpy.data.images.load(tmp_path)

                        # Scene-linear colour space for HDR and EXR alike, by try-assign: 'Linear Rec.709'
                        # exists on 4.3 and 5.x, 'Linear' only on older OCIO configs, 'Non-Color' everywhere
                        for color_space in ['Linear Rec.709', 'Linear', 'Non-Color']:
                            try:
                                env_tex.image.colorspace_settings.name = color_space
                                break
                            except Exception:
                                continue

                        background = node_tree.nodes.new(type='ShaderNodeBackground')
                        background.location = (-200, 0)

                        output = node_tree.nodes.new(type='ShaderNodeOutputWorld')
                        output.location = (0, 0)

                        # Connect nodes
                        node_tree.links.new(tex_coord.outputs['Generated'], mapping.inputs['Vector'])
                        node_tree.links.new(mapping.outputs['Vector'], env_tex.inputs['Vector'])
                        node_tree.links.new(env_tex.outputs['Color'], background.inputs['Color'])
                        node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])

                        # Set as active world
                        bpy.context.scene.world = world

                        # Clean up the downloaded HDRI file (the old private tempfile helper never existed;
                        # the image data block already holds the pixels in memory)
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                        return {
                            "success": True,
                            "message": f"HDRI {asset_id} imported successfully",
                            "image_name": env_tex.image.name
                        }
                    except Exception as e:
                        return {"error": f"Failed to set up HDRI in Blender: {str(e)}"}
                else:
                    return {"error": f"Requested resolution or format not available for this HDRI"}

            elif asset_type == "textures":
                if not file_format:
                    file_format = "jpg"  # Default format for textures

                downloaded_maps = {}

                try:
                    for map_type in files_data:
                        if map_type not in ["blend", "gltf"]:  # Skip non-texture files
                            if resolution in files_data[map_type] and file_format in files_data[map_type][resolution]:
                                file_info = files_data[map_type][resolution][file_format]
                                file_url = file_info["url"]

                                # Use NamedTemporaryFile like we do for HDRIs
                                with tempfile.NamedTemporaryFile(suffix=f".{file_format}", delete=False) as tmp_file:
                                    # Download the file
                                    response = requests.get(file_url, headers=REQ_HEADERS)
                                    if response.status_code == 200:
                                        tmp_file.write(response.content)
                                        tmp_path = tmp_file.name

                                        # Load image from temporary file
                                        image = bpy.data.images.load(tmp_path)
                                        image.name = f"{asset_id}_{map_type}.{file_format}"

                                        # Pack the image into .blend file
                                        image.pack()

                                        # Set color space based on map type
                                        if map_type in ['color', 'diffuse', 'albedo']:
                                            try:
                                                image.colorspace_settings.name = 'sRGB'
                                            except:
                                                pass
                                        else:
                                            try:
                                                image.colorspace_settings.name = 'Non-Color'
                                            except:
                                                pass

                                        downloaded_maps[map_type] = image

                                        # Clean up temporary file
                                        try:
                                            os.unlink(tmp_path)
                                        except:
                                            pass

                    if not downloaded_maps:
                        return {"error": f"No texture maps found for the requested resolution and format"}

                    # Create a new material with the downloaded textures
                    mat = bpy.data.materials.new(name=asset_id)
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links

                    # Clear default nodes
                    for node in nodes:
                        nodes.remove(node)

                    # Create output node
                    output = nodes.new(type='ShaderNodeOutputMaterial')
                    output.location = (300, 0)

                    # Create principled BSDF node
                    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                    principled.location = (0, 0)
                    links.new(principled.outputs[0], output.inputs[0])

                    # Add texture nodes based on available maps
                    tex_coord = nodes.new(type='ShaderNodeTexCoord')
                    tex_coord.location = (-800, 0)

                    mapping = nodes.new(type='ShaderNodeMapping')
                    mapping.location = (-600, 0)
                    mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
                    links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

                    # Position offset for texture nodes
                    x_pos = -400
                    y_pos = 300

                    # Connect different texture maps
                    for map_type, image in downloaded_maps.items():
                        tex_node = nodes.new(type='ShaderNodeTexImage')
                        tex_node.location = (x_pos, y_pos)
                        tex_node.image = image

                        # Set color space based on map type
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            try:
                                tex_node.image.colorspace_settings.name = 'sRGB'
                            except:
                                pass  # Use default if sRGB not available
                        else:
                            try:
                                tex_node.image.colorspace_settings.name = 'Non-Color'
                            except:
                                pass  # Use default if Non-Color not available

                        links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])

                        # Connect to appropriate input on Principled BSDF
                        if map_type.lower() in ['color', 'diffuse', 'albedo']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                        elif map_type.lower() in ['roughness', 'rough']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                        elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                            links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                        elif map_type.lower() in ['normal', 'nor']:
                            # Add normal map node
                            normal_map = nodes.new(type='ShaderNodeNormalMap')
                            normal_map.location = (x_pos + 200, y_pos)
                            links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                        elif map_type in ['displacement', 'disp', 'height']:
                            # Add displacement node
                            disp_node = nodes.new(type='ShaderNodeDisplacement')
                            disp_node.location = (x_pos + 200, y_pos - 200)
                            links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                            links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])

                        y_pos -= 250

                    return {
                        "success": True,
                        "message": f"Texture {asset_id} imported as material",
                        "material": mat.name,
                        "maps": list(downloaded_maps.keys())
                    }

                except Exception as e:
                    return {"error": f"Failed to process textures: {str(e)}"}

            elif asset_type == "models":
                # For models, prefer glTF format if available
                if not file_format:
                    file_format = "gltf"  # Default format for models

                if file_format in files_data and resolution in files_data[file_format]:
                    file_info = files_data[file_format][resolution][file_format]
                    file_url = file_info["url"]

                    # Create a temporary directory to store the model and its dependencies
                    temp_dir = tempfile.mkdtemp()
                    main_file_path = ""

                    try:
                        # Download the main model file
                        main_file_name = file_url.split("/")[-1]
                        main_file_path = os.path.join(temp_dir, main_file_name)

                        response = requests.get(file_url, headers=REQ_HEADERS)
                        if response.status_code != 200:
                            return {"error": f"Failed to download model: {response.status_code}"}

                        with open(main_file_path, "wb") as f:
                            f.write(response.content)

                        # Check for included files and download them
                        if "include" in file_info and file_info["include"]:
                            for include_path, include_info in file_info["include"].items():
                                include_url = include_info["url"]

                                # Create the directory structure for the included file
                                include_file_path = os.path.join(temp_dir, include_path)
                                os.makedirs(os.path.dirname(include_file_path), exist_ok=True)

                                # Download the included file
                                include_response = requests.get(include_url, headers=REQ_HEADERS)
                                if include_response.status_code == 200:
                                    with open(include_file_path, "wb") as f:
                                        f.write(include_response.content)
                                else:
                                    print(f"Failed to download included file: {include_path}")

                        # Import the model into Blender
                        if file_format == "gltf" or file_format == "glb":
                            bpy.ops.import_scene.gltf(filepath=main_file_path)
                        elif file_format == "fbx":
                            bpy.ops.import_scene.fbx(filepath=main_file_path)
                        elif file_format == "obj":
                            if self._op_exists(bpy.ops.wm.obj_import):
                                bpy.ops.wm.obj_import(filepath=main_file_path)
                            else:
                                bpy.ops.import_scene.obj(filepath=main_file_path)
                        elif file_format == "blend":
                            # For blend files, we need to append or link
                            with bpy.data.libraries.load(main_file_path, link=False) as (data_from, data_to):
                                data_to.objects = data_from.objects

                            # Link the objects to the scene
                            for obj in data_to.objects:
                                if obj is not None:
                                    bpy.context.collection.objects.link(obj)
                        else:
                            return {"error": f"Unsupported model format: {file_format}"}

                        # Get the names of imported objects
                        imported_objects = [obj.name for obj in bpy.context.selected_objects]

                        return {
                            "success": True,
                            "message": f"Model {asset_id} imported successfully",
                            "imported_objects": imported_objects
                        }
                    except Exception as e:
                        return {"error": f"Failed to import model: {str(e)}"}
                    finally:
                        # Clean up temporary directory
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                else:
                    return {"error": f"Requested format or resolution not available for this model"}

            else:
                return {"error": f"Unsupported asset type: {asset_type}"}

        except Exception as e:
            return {"error": f"Failed to download asset: {str(e)}"}

    def set_texture(self, object_name, texture_id):
        """Apply a previously downloaded Polyhaven texture to an object by creating a new material"""
        try:
            # Get the object
            obj = bpy.data.objects.get(object_name)
            if not obj:
                return {"error": f"Object not found: {object_name}"}

            # Make sure object can accept materials
            if not hasattr(obj, 'data') or not hasattr(obj.data, 'materials'):
                return {"error": f"Object {object_name} cannot accept materials"}

            # Find all images related to this texture and ensure they're properly loaded
            texture_images = {}
            for img in bpy.data.images:
                if img.name.startswith(texture_id + "_"):
                    # Extract the map type from the image name
                    map_type = img.name.split('_')[-1].split('.')[0]

                    # Force a reload of the image
                    img.reload()

                    # Ensure proper color space
                    if map_type.lower() in ['color', 'diffuse', 'albedo']:
                        try:
                            img.colorspace_settings.name = 'sRGB'
                        except:
                            pass
                    else:
                        try:
                            img.colorspace_settings.name = 'Non-Color'
                        except:
                            pass

                    # Ensure the image is packed
                    if not img.packed_file:
                        img.pack()

                    texture_images[map_type] = img
                    print(f"Loaded texture map: {map_type} - {img.name}")

            if not texture_images:
                return {"error": f"No texture images found for: {texture_id}. Please download the texture first."}

            # Create a new material
            new_mat_name = f"{texture_id}_material_{object_name}"

            # Remove any existing material with this name to avoid conflicts
            existing_mat = bpy.data.materials.get(new_mat_name)
            if existing_mat:
                bpy.data.materials.remove(existing_mat)

            new_mat = bpy.data.materials.new(name=new_mat_name)
            new_mat.use_nodes = True

            # Set up the material nodes
            nodes = new_mat.node_tree.nodes
            links = new_mat.node_tree.links

            # Clear default nodes
            nodes.clear()

            # Create output node
            output = nodes.new(type='ShaderNodeOutputMaterial')
            output.location = (600, 0)

            # Create principled BSDF node
            principled = nodes.new(type='ShaderNodeBsdfPrincipled')
            principled.location = (300, 0)
            links.new(principled.outputs[0], output.inputs[0])

            # Add texture nodes based on available maps
            tex_coord = nodes.new(type='ShaderNodeTexCoord')
            tex_coord.location = (-800, 0)

            mapping = nodes.new(type='ShaderNodeMapping')
            mapping.location = (-600, 0)
            mapping.vector_type = 'TEXTURE'  # Changed from default 'POINT' to 'TEXTURE'
            links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

            # Position offset for texture nodes
            x_pos = -400
            y_pos = 300

            # Connect different texture maps
            for map_type, image in texture_images.items():
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.location = (x_pos, y_pos)
                tex_node.image = image

                # Set color space based on map type
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    try:
                        tex_node.image.colorspace_settings.name = 'sRGB'
                    except:
                        pass  # Use default if sRGB not available
                else:
                    try:
                        tex_node.image.colorspace_settings.name = 'Non-Color'
                    except:
                        pass  # Use default if Non-Color not available

                links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])

                # Connect to appropriate input on Principled BSDF
                if map_type.lower() in ['color', 'diffuse', 'albedo']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
                elif map_type.lower() in ['roughness', 'rough']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Roughness'])
                elif map_type.lower() in ['metallic', 'metalness', 'metal']:
                    links.new(tex_node.outputs['Color'], principled.inputs['Metallic'])
                elif map_type.lower() in ['normal', 'nor', 'dx', 'gl']:
                    # Add normal map node
                    normal_map = nodes.new(type='ShaderNodeNormalMap')
                    normal_map.location = (x_pos + 200, y_pos)
                    links.new(tex_node.outputs['Color'], normal_map.inputs['Color'])
                    links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
                elif map_type.lower() in ['displacement', 'disp', 'height']:
                    # Add displacement node
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (x_pos + 200, y_pos - 200)
                    disp_node.inputs['Scale'].default_value = 0.1  # Reduce displacement strength
                    links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])

                y_pos -= 250

            # Index the texture nodes by map type for the ARM wiring below. Base colour, roughness,
            # metallic, normal and displacement are already linked by the loop above (a second
            # linking pass used to duplicate the NormalMap / Displacement nodes on every call).
            texture_nodes = {}
            for node in nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break

            # Handle ARM texture (Ambient Occlusion, Roughness, Metallic)
            if 'arm' in texture_nodes:
                # ShaderNodeSeparateRGB is gone on 5.x; SeparateColor (mode RGB) is its twin with
                # input "Color" and outputs "Red"/"Green"/"Blue" instead of "Image" and "R"/"G"/"B"
                separate_rgb = self._new_node(new_mat.node_tree, 'ShaderNodeSeparateRGB', 'ShaderNodeSeparateColor')
                separate_rgb.location = (-200, -100)
                sep_in = separate_rgb.inputs.get('Image') or separate_rgb.inputs.get('Color')
                links.new(texture_nodes['arm'].outputs['Color'], sep_in)

                def _sep_out(short, long):
                    return separate_rgb.outputs.get(short) or separate_rgb.outputs.get(long)

                # Connect Roughness (G) if no dedicated roughness map
                if not any(map_name in texture_nodes for map_name in ['roughness', 'rough']):
                    links.new(_sep_out('G', 'Green'), principled.inputs['Roughness'])
                    print("Connected ARM.G to Roughness")

                # Connect Metallic (B) if no dedicated metallic map
                if not any(map_name in texture_nodes for map_name in ['metallic', 'metalness', 'metal']):
                    links.new(_sep_out('B', 'Blue'), principled.inputs['Metallic'])
                    print("Connected ARM.B to Metallic")

                # For AO (R channel), multiply with base color if we have one
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence

                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)

                    # Connect through the mix node
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(_sep_out('R', 'Red'), mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected ARM.R to AO mix with Base Color")

            # Handle AO (Ambient Occlusion) if separate
            if 'ao' in texture_nodes:
                base_color_node = None
                for map_name in ['color', 'diffuse', 'albedo']:
                    if map_name in texture_nodes:
                        base_color_node = texture_nodes[map_name]
                        break

                if base_color_node:
                    mix_node = nodes.new(type='ShaderNodeMixRGB')
                    mix_node.location = (100, 200)
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs['Fac'].default_value = 0.8  # 80% influence

                    # Disconnect direct connection to base color
                    for link in base_color_node.outputs['Color'].links:
                        if link.to_socket == principled.inputs['Base Color']:
                            links.remove(link)

                    # Connect through the mix node
                    links.new(base_color_node.outputs['Color'], mix_node.inputs[1])
                    links.new(texture_nodes['ao'].outputs['Color'], mix_node.inputs[2])
                    links.new(mix_node.outputs['Color'], principled.inputs['Base Color'])
                    print("Connected AO to mix with Base Color")

            # Replace every existing material slot with the new material
            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)
            obj.data.materials.append(new_mat)

            # Make the object active and selected so the material shows in the UI
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)

            # Depsgraph update so the evaluated object carries the new material
            bpy.context.view_layer.update()

            # Get the list of texture maps
            texture_maps = list(texture_images.keys())

            # Get info about texture nodes for debugging
            material_info = {
                "name": new_mat.name,
                "has_nodes": new_mat.use_nodes,
                "node_count": len(new_mat.node_tree.nodes),
                "texture_nodes": []
            }

            for node in new_mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    connections = []
                    for output in node.outputs:
                        for link in output.links:
                            connections.append(f"{output.name} -> {link.to_node.name}.{link.to_socket.name}")

                    material_info["texture_nodes"].append({
                        "name": node.name,
                        "image": node.image.name,
                        "colorspace": node.image.colorspace_settings.name,
                        "connections": connections
                    })

            return {
                "success": True,
                "message": f"Created new material and applied texture {texture_id} to {object_name}",
                "material": new_mat.name,
                "maps": texture_maps,
                "material_info": material_info
            }

        except Exception as e:
            print(f"Error in set_texture: {str(e)}")
            traceback.print_exc()
            return {"error": f"Failed to apply texture: {str(e)}"}

    def get_polyhaven_status(self):
        """Get the current status of PolyHaven integration"""
        enabled = bpy.context.scene.blendermcp_use_polyhaven
        if enabled:
            return {"enabled": True, "message": "PolyHaven integration is enabled and ready to use."}
        else:
            return {
                "enabled": False,
                "message": """PolyHaven integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Poly Haven' checkbox
                            3. Restart the connection to Claude"""
        }

    #region Hyper3D
    def get_hyper3d_status(self):
        """Get the current status of Hyper3D Rodin integration"""
        enabled = bpy.context.scene.blendermcp_use_hyper3d
        if enabled:
            if not _secret('hyper3d_api_key'):
                return {
                    "enabled": False,
                    "message": """Hyper3D Rodin integration is currently enabled, but API key is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Hyper3D Rodin 3D model generation' checkbox checked
                                3. Choose the right plaform and fill in the API Key
                                4. Restart the connection to Claude"""
                }
            mode = bpy.context.scene.blendermcp_hyper3d_mode
            message = f"Hyper3D Rodin integration is enabled and ready to use. Mode: {mode}. " + \
                f"Key type: {'private' if _secret('hyper3d_api_key') != RODIN_FREE_TRIAL_KEY else 'free_trial'}"
            return {
                "enabled": True,
                "message": message
            }
        else:
            return {
                "enabled": False,
                "message": """Hyper3D Rodin integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use Hyper3D Rodin 3D model generation' checkbox
                            3. Restart the connection to Claude"""
            }

    def create_rodin_job(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.create_rodin_job_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.create_rodin_job_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def create_rodin_job_main_site(
            self,
            text_prompt: str=None,
            images: list[tuple[str, str]]=None,
            bbox_condition=None
        ):
        try:
            if images is None:
                images = []
            """Call Rodin API, get the job uuid and subscription key"""
            files = [
                *[("images", (f"{i:04d}{img_suffix}", img)) for i, (img_suffix, img) in enumerate(images)],
                ("tier", (None, "Sketch")),
                ("mesh_mode", (None, "Raw")),
            ]
            if text_prompt:
                files.append(("prompt", (None, text_prompt)))
            if bbox_condition:
                files.append(("bbox_condition", (None, json.dumps(bbox_condition))))
            response = requests.post(
                "https://hyperhuman.deemos.com/api/v2/rodin",
                headers={
                    "Authorization": f"Bearer {_secret('hyper3d_api_key')}",
                },
                files=files
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}

    def create_rodin_job_fal_ai(
            self,
            text_prompt: str=None,
            images: list[tuple[str, str]]=None,
            bbox_condition=None
        ):
        try:
            req_data = {
                "tier": "Sketch",
            }
            if images:
                req_data["input_image_urls"] = images
            if text_prompt:
                req_data["prompt"] = text_prompt
            if bbox_condition:
                req_data["bbox_condition"] = bbox_condition
            response = requests.post(
                "https://queue.fal.run/fal-ai/hyper3d/rodin",
                headers={
                    "Authorization": f"Key {_secret('hyper3d_api_key')}",
                    "Content-Type": "application/json",
                },
                json=req_data
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}

    def poll_rodin_job_status(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.poll_rodin_job_status_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.poll_rodin_job_status_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def poll_rodin_job_status_main_site(self, subscription_key: str):
        """Call the job status API to get the job status"""
        response = requests.post(
            "https://hyperhuman.deemos.com/api/v2/status",
            headers={
                "Authorization": f"Bearer {_secret('hyper3d_api_key')}",
            },
            json={
                "subscription_key": subscription_key,
            },
        )
        data = response.json()
        return {
            "status_list": [i["status"] for i in data["jobs"]]
        }

    def poll_rodin_job_status_fal_ai(self, request_id: str):
        """Call the job status API to get the job status"""
        response = requests.get(
            f"https://queue.fal.run/fal-ai/hyper3d/requests/{request_id}/status",
            headers={
                "Authorization": f"KEY {_secret('hyper3d_api_key')}",
            },
        )
        data = response.json()
        return data

    @staticmethod
    def _clean_imported_glb(filepath, mesh_name=None):
        # Get the set of existing objects before import
        existing_objects = set(bpy.data.objects)

        # Import the GLB file
        bpy.ops.import_scene.gltf(filepath=filepath)

        # Ensure the context is updated
        bpy.context.view_layer.update()

        # Get all imported objects
        imported_objects = list(set(bpy.data.objects) - existing_objects)

        if not imported_objects:
            print("Error: No objects were imported.")
            return

        # Identify the mesh object
        mesh_obj = None

        if len(imported_objects) == 1 and imported_objects[0].type == 'MESH':
            mesh_obj = imported_objects[0]
            print("Single mesh imported, no cleanup needed.")
        else:
            if len(imported_objects) == 2:
                empty_objs = [i for i in imported_objects if i.type == "EMPTY"]
                if len(empty_objs) != 1:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
                parent_obj = empty_objs.pop()
                if len(parent_obj.children) == 1:
                    potential_mesh = parent_obj.children[0]
                    if potential_mesh.type == 'MESH':
                        print("GLB structure confirmed: Empty node with one mesh child.")

                        # Unparent the mesh from the empty node
                        potential_mesh.parent = None

                        # Remove the empty node
                        bpy.data.objects.remove(parent_obj)
                        print("Removed empty node, keeping only the mesh.")

                        mesh_obj = potential_mesh
                    else:
                        print("Error: Child is not a mesh object.")
                        return
                else:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
            else:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return

        # Rename the mesh if needed
        try:
            if mesh_obj and mesh_obj.name is not None and mesh_name:
                mesh_obj.name = mesh_name
                if mesh_obj.data.name is not None:
                    mesh_obj.data.name = mesh_name
                print(f"Mesh renamed to: {mesh_name}")
        except Exception as e:
            print("Having issue with renaming, give up renaming.")

        return mesh_obj

    def import_generated_asset(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hyper3d_mode:
            case "MAIN_SITE":
                return self.import_generated_asset_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.import_generated_asset_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def import_generated_asset_main_site(self, task_uuid: str, name: str):
        """Fetch the generated asset, import into blender"""
        response = requests.post(
            "https://hyperhuman.deemos.com/api/v2/download",
            headers={
                "Authorization": f"Bearer {_secret('hyper3d_api_key')}",
            },
            json={
                'task_uuid': task_uuid
            }
        )
        data_ = response.json()
        temp_file = None
        for i in data_["list"]:
            if i["name"].endswith(".glb"):
                temp_file = tempfile.NamedTemporaryFile(
                    delete=False,
                    prefix=task_uuid,
                    suffix=".glb",
                )

                try:
                    # Download the content
                    response = requests.get(i["url"], stream=True)
                    response.raise_for_status()  # Raise an exception for HTTP errors

                    # Write the content to the temporary file
                    for chunk in response.iter_content(chunk_size=8192):
                        temp_file.write(chunk)

                    # Close the file
                    temp_file.close()

                except Exception as e:
                    # Clean up the file if there's an error
                    temp_file.close()
                    os.unlink(temp_file.name)
                    return {"succeed": False, "error": str(e)}

                break
        else:
            return {"succeed": False, "error": "Generation failed. Please first make sure that all jobs of the task are done and then try again later."}

        try:
            obj = self._clean_imported_glb(
                filepath=temp_file.name,
                mesh_name=name
            )
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return {
                "succeed": True, **result
            }
        except Exception as e:
            return {"succeed": False, "error": str(e)}

    def import_generated_asset_fal_ai(self, request_id: str, name: str):
        """Fetch the generated asset, import into blender"""
        response = requests.get(
            f"https://queue.fal.run/fal-ai/hyper3d/requests/{request_id}",
            headers={
                "Authorization": f"Key {_secret('hyper3d_api_key')}",
            }
        )
        data_ = response.json()
        temp_file = None

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            prefix=request_id,
            suffix=".glb",
        )

        try:
            # Download the content
            response = requests.get(data_["model_mesh"]["url"], stream=True)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Write the content to the temporary file
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)

            # Close the file
            temp_file.close()

        except Exception as e:
            # Clean up the file if there's an error
            temp_file.close()
            os.unlink(temp_file.name)
            return {"succeed": False, "error": str(e)}

        try:
            obj = self._clean_imported_glb(
                filepath=temp_file.name,
                mesh_name=name
            )
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return {
                "succeed": True, **result
            }
        except Exception as e:
            return {"succeed": False, "error": str(e)}
    #endregion
 
    #region Sketchfab API
    def get_sketchfab_status(self):
        """Get the current status of Sketchfab integration"""
        enabled = bpy.context.scene.blendermcp_use_sketchfab
        api_key = _secret('sketchfab_api_key')

        # Test the API key if present
        if api_key:
            try:
                headers = {
                    "Authorization": f"Token {api_key}"
                }

                response = requests.get(
                    "https://api.sketchfab.com/v3/me",
                    headers=headers,
                    timeout=30  # Add timeout of 30 seconds
                )

                if response.status_code == 200:
                    user_data = response.json()
                    username = user_data.get("username", "Unknown user")
                    return {
                        "enabled": True,
                        "message": f"Sketchfab integration is enabled and ready to use. Logged in as: {username}"
                    }
                else:
                    return {
                        "enabled": False,
                        "message": f"Sketchfab API key seems invalid. Status code: {response.status_code}"
                    }
            except requests.exceptions.Timeout:
                return {
                    "enabled": False,
                    "message": "Timeout connecting to Sketchfab API. Check your internet connection."
                }
            except Exception as e:
                return {
                    "enabled": False,
                    "message": f"Error testing Sketchfab API key: {str(e)}"
                }

        if enabled and api_key:
            return {"enabled": True, "message": "Sketchfab integration is enabled and ready to use."}
        elif enabled and not api_key:
            return {
                "enabled": False,
                "message": """Sketchfab integration is currently enabled, but API key is not given. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Keep the 'Use Sketchfab' checkbox checked
                            3. Enter your Sketchfab API Key
                            4. Restart the connection to Claude"""
            }
        else:
            return {
                "enabled": False,
                "message": """Sketchfab integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Sketchfab' checkbox
                            3. Enter your Sketchfab API Key
                            4. Restart the connection to Claude"""
            }

    def search_sketchfab_models(self, query, categories=None, count=20, downloadable=True):
        """Search for models on Sketchfab based on query and optional filters"""
        try:
            api_key = _secret('sketchfab_api_key')
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            # Build search parameters with exact fields from Sketchfab API docs
            params = {
                "type": "models",
                "q": query,
                "count": count,
                "downloadable": downloadable,
                "archives_flavours": False
            }

            if categories:
                params["categories"] = categories

            # Make API request to Sketchfab search endpoint
            # The proper format according to Sketchfab API docs for API key auth
            headers = {
                "Authorization": f"Token {api_key}"
            }


            # Use the search endpoint as specified in the API documentation
            response = requests.get(
                "https://api.sketchfab.com/v3/search",
                headers=headers,
                params=params,
                timeout=30  # Add timeout of 30 seconds
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code != 200:
                return {"error": f"API request failed with status code {response.status_code}"}

            response_data = response.json()

            # Safety check on the response structure
            if response_data is None:
                return {"error": "Received empty response from Sketchfab API"}

            # Handle 'results' potentially missing from response
            results = response_data.get("results", [])
            if not isinstance(results, list):
                return {"error": f"Unexpected response format from Sketchfab API: {response_data}"}

            return response_data

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    def get_sketchfab_model_preview(self, uid):
        """Get thumbnail preview image of a Sketchfab model by its UID"""
        try:
            import base64
            
            api_key = _secret('sketchfab_api_key')
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            headers = {"Authorization": f"Token {api_key}"}
            
            # Get model info which includes thumbnails
            response = requests.get(
                f"https://api.sketchfab.com/v3/models/{uid}",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}
            
            if response.status_code == 404:
                return {"error": f"Model not found: {uid}"}
            
            if response.status_code != 200:
                return {"error": f"Failed to get model info: {response.status_code}"}
            
            data = response.json()
            thumbnails = data.get("thumbnails", {}).get("images", [])
            
            if not thumbnails:
                return {"error": "No thumbnail available for this model"}
            
            # Find a suitable thumbnail (prefer medium size ~640px)
            selected_thumbnail = None
            for thumb in thumbnails:
                width = thumb.get("width", 0)
                if 400 <= width <= 800:
                    selected_thumbnail = thumb
                    break
            
            # Fallback to the first available thumbnail
            if not selected_thumbnail:
                selected_thumbnail = thumbnails[0]
            
            thumbnail_url = selected_thumbnail.get("url")
            if not thumbnail_url:
                return {"error": "Thumbnail URL not found"}
            
            # Download the thumbnail image
            img_response = requests.get(thumbnail_url, timeout=30)
            if img_response.status_code != 200:
                return {"error": f"Failed to download thumbnail: {img_response.status_code}"}
            
            # Encode image as base64
            image_data = base64.b64encode(img_response.content).decode('ascii')
            
            # Determine format from content type or URL
            content_type = img_response.headers.get("Content-Type", "")
            if "png" in content_type or thumbnail_url.endswith(".png"):
                img_format = "png"
            else:
                img_format = "jpeg"
            
            # Get additional model info for context
            model_name = data.get("name", "Unknown")
            author = data.get("user", {}).get("username", "Unknown")
            
            return {
                "success": True,
                "image_data": image_data,
                "format": img_format,
                "model_name": model_name,
                "author": author,
                "uid": uid,
                "thumbnail_width": selected_thumbnail.get("width"),
                "thumbnail_height": selected_thumbnail.get("height")
            }
            
        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Failed to get model preview: {str(e)}"}

    def download_sketchfab_model(self, uid, normalize_size=False, target_size=1.0):
        """Download a model from Sketchfab by its UID
        
        Parameters:
        - uid: The unique identifier of the Sketchfab model
        - normalize_size: If True, scale the model so its largest dimension equals target_size
        - target_size: The target size in Blender units (meters) for the largest dimension
        """
        try:
            api_key = _secret('sketchfab_api_key')
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            # Use proper authorization header for API key auth
            headers = {
                "Authorization": f"Token {api_key}"
            }

            # Request download URL using the exact endpoint from the documentation
            download_endpoint = f"https://api.sketchfab.com/v3/models/{uid}/download"

            response = requests.get(
                download_endpoint,
                headers=headers,
                timeout=30  # Add timeout of 30 seconds
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code != 200:
                return {"error": f"Download request failed with status code {response.status_code}"}

            data = response.json()

            # Safety check for None data
            if data is None:
                return {"error": "Received empty response from Sketchfab API for download request"}

            # Extract download URL with safety checks
            gltf_data = data.get("gltf")
            if not gltf_data:
                return {"error": "No gltf download URL available for this model. Response: " + str(data)}

            download_url = gltf_data.get("url")
            if not download_url:
                return {"error": "No download URL available for this model. Make sure the model is downloadable and you have access."}

            # Download the model (already has timeout)
            model_response = requests.get(download_url, timeout=60)  # 60 second timeout

            if model_response.status_code != 200:
                return {"error": f"Model download failed with status code {model_response.status_code}"}

            # Save to temporary file
            temp_dir = tempfile.mkdtemp()
            zip_file_path = os.path.join(temp_dir, f"{uid}.zip")

            with open(zip_file_path, "wb") as f:
                f.write(model_response.content)

            # Extract the zip file with enhanced security
            with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                # More secure zip slip prevention
                for file_info in zip_ref.infolist():
                    # Get the path of the file
                    file_path = file_info.filename

                    # Convert directory separators to the current OS style
                    # This handles both / and \ in zip entries
                    target_path = os.path.join(temp_dir, os.path.normpath(file_path))

                    # Get absolute paths for comparison
                    abs_temp_dir = os.path.abspath(temp_dir)
                    abs_target_path = os.path.abspath(target_path)

                    # Ensure the normalized path doesn't escape the target directory
                    if not abs_target_path.startswith(abs_temp_dir):
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                        return {"error": "Security issue: Zip contains files with path traversal attempt"}

                    # Additional explicit check for directory traversal
                    if ".." in file_path:
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                        return {"error": "Security issue: Zip contains files with directory traversal sequence"}

                # If all files passed security checks, extract them
                zip_ref.extractall(temp_dir)

            # Find the main glTF file
            gltf_files = [f for f in os.listdir(temp_dir) if f.endswith('.gltf') or f.endswith('.glb')]

            if not gltf_files:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
                return {"error": "No glTF file found in the downloaded model"}

            main_file = os.path.join(temp_dir, gltf_files[0])

            # Import the model
            bpy.ops.import_scene.gltf(filepath=main_file)

            # Get the imported objects
            imported_objects = list(bpy.context.selected_objects)
            imported_object_names = [obj.name for obj in imported_objects]

            # Clean up temporary files
            with suppress(Exception):
                shutil.rmtree(temp_dir)

            # Find root objects (objects without parents in the imported set)
            root_objects = [obj for obj in imported_objects if obj.parent is None]

            # Helper function to recursively get all mesh children
            def get_all_mesh_children(obj):
                """Recursively collect all mesh objects in the hierarchy"""
                meshes = []
                if obj.type == 'MESH':
                    meshes.append(obj)
                for child in obj.children:
                    meshes.extend(get_all_mesh_children(child))
                return meshes

            # Collect ALL meshes from the entire hierarchy (starting from roots)
            all_meshes = []
            for obj in root_objects:
                all_meshes.extend(get_all_mesh_children(obj))
            
            if all_meshes:
                # Calculate combined world bounding box for all meshes
                all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))
                
                for mesh_obj in all_meshes:
                    # Get world-space bounding box corners
                    for corner in mesh_obj.bound_box:
                        world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                        all_min.x = min(all_min.x, world_corner.x)
                        all_min.y = min(all_min.y, world_corner.y)
                        all_min.z = min(all_min.z, world_corner.z)
                        all_max.x = max(all_max.x, world_corner.x)
                        all_max.y = max(all_max.y, world_corner.y)
                        all_max.z = max(all_max.z, world_corner.z)
                
                # Calculate dimensions
                dimensions = [
                    all_max.x - all_min.x,
                    all_max.y - all_min.y,
                    all_max.z - all_min.z
                ]
                max_dimension = max(dimensions)
                
                # Apply normalization if requested
                scale_applied = 1.0
                if normalize_size and max_dimension > 0:
                    scale_factor = target_size / max_dimension
                    scale_applied = scale_factor
                    
                    # Only apply scale to ROOT objects (not children!)
                    # Child objects inherit parent's scale through matrix_world
                    for root in root_objects:
                        root.scale = (
                            root.scale.x * scale_factor,
                            root.scale.y * scale_factor,
                            root.scale.z * scale_factor
                        )
                    
                    # Update the scene to recalculate matrix_world for all objects
                    bpy.context.view_layer.update()
                    
                    # Recalculate bounding box after scaling
                    all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                    all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))
                    
                    for mesh_obj in all_meshes:
                        for corner in mesh_obj.bound_box:
                            world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                            all_min.x = min(all_min.x, world_corner.x)
                            all_min.y = min(all_min.y, world_corner.y)
                            all_min.z = min(all_min.z, world_corner.z)
                            all_max.x = max(all_max.x, world_corner.x)
                            all_max.y = max(all_max.y, world_corner.y)
                            all_max.z = max(all_max.z, world_corner.z)
                    
                    dimensions = [
                        all_max.x - all_min.x,
                        all_max.y - all_min.y,
                        all_max.z - all_min.z
                    ]
                
                world_bounding_box = [[all_min.x, all_min.y, all_min.z], [all_max.x, all_max.y, all_max.z]]
            else:
                world_bounding_box = None
                dimensions = None
                scale_applied = 1.0

            result = {
                "success": True,
                "message": "Model imported successfully",
                "imported_objects": imported_object_names
            }
            
            if world_bounding_box:
                result["world_bounding_box"] = world_bounding_box
            if dimensions:
                result["dimensions"] = [round(d, 4) for d in dimensions]
            if normalize_size:
                result["scale_applied"] = round(scale_applied, 6)
                result["normalized"] = True
            
            return result

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection and try again with a simpler model."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Failed to download model: {str(e)}"}
    #endregion

    #region Hunyuan3D
    def get_hunyuan3d_status(self):
        """Get the current status of Hunyuan3D integration"""
        enabled = bpy.context.scene.blendermcp_use_hunyuan3d
        hunyuan3d_mode = bpy.context.scene.blendermcp_hunyuan3d_mode
        if enabled:
            match hunyuan3d_mode:
                case "OFFICIAL_API":
                    if not _secret('hunyuan3d_secret_id') or not _secret('hunyuan3d_secret_key'):
                        return {
                            "enabled": False, 
                            "mode": hunyuan3d_mode, 
                            "message": """Hunyuan3D integration is currently enabled, but SecretId or SecretKey is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the SecretId and SecretKey
                                4. Restart the connection to Claude"""
                        }
                case "LOCAL_API":
                    if not bpy.context.scene.blendermcp_hunyuan3d_api_url:
                        return {
                            "enabled": False, 
                            "mode": hunyuan3d_mode, 
                            "message": """Hunyuan3D integration is currently enabled, but API URL  is not given. To enable it:
                                1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the API URL
                                4. Restart the connection to Claude"""
                        }
                case _:
                    return {
                        "enabled": False, 
                        "message": "Hunyuan3D integration is enabled and mode is not supported."
                    }
            return {
                "enabled": True, 
                "mode": hunyuan3d_mode,
                "message": "Hunyuan3D integration is enabled and ready to use."
            }
        return {
            "enabled": False, 
            "message": """Hunyuan3D integration is currently disabled. To enable it:
                        1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                        2. Check the 'Use Tencent Hunyuan 3D model generation' checkbox
                        3. Restart the connection to Claude"""
        }
    
    @staticmethod
    def get_tencent_cloud_sign_headers(
        method: str,
        path: str,
        headParams: dict,
        data: dict,
        service: str,
        region: str,
        secret_id: str,
        secret_key: str,
        host: str = None
    ):
        """Generate the signature header required for Tencent Cloud API requests headers"""
        # Generate timestamp
        timestamp = int(time.time())
        date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
        
        # If host is not provided, it is generated based on service and region.
        if not host:
            host = f"{service}.tencentcloudapi.com"
        
        endpoint = f"https://{host}"
        
        # Constructing the request body
        payload_str = json.dumps(data)
        
        # ************* Step 1: Concatenate the canonical request string *************
        canonical_uri = path
        canonical_querystring = ""
        ct = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{headParams.get('Action', '').lower()}\n"
        signed_headers = "content-type;host;x-tc-action"
        hashed_request_payload = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        
        canonical_request = (method + "\n" +
                            canonical_uri + "\n" +
                            canonical_querystring + "\n" +
                            canonical_headers + "\n" +
                            signed_headers + "\n" +
                            hashed_request_payload)

        # ************* Step 2: Build the string to sign (TC3-HMAC-SHA256) *************
        credential_scope = f"{date}/{service}/tc3_request"
        hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = ("TC3-HMAC-SHA256" + "\n" +
                        str(timestamp) + "\n" +
                        credential_scope + "\n" +
                        hashed_canonical_request)

        # ************* Step 3: Calculate the signature *************
        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
        secret_service = sign(secret_date, service)
        secret_signing = sign(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, 
            string_to_sign.encode("utf-8"), 
            hashlib.sha256
        ).hexdigest()

        # ************* Step 4: Connect Authorization *************
        authorization = ("TC3-HMAC-SHA256" + " " +
                        "Credential=" + secret_id + "/" + credential_scope + ", " +
                        "SignedHeaders=" + signed_headers + ", " +
                        "Signature=" + signature)

        # Constructing request headers
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": headParams.get("Action", ""),
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": headParams.get("Version", ""),
            "X-TC-Region": region
        }

        return headers, endpoint

    def create_hunyuan_job(self, *args, **kwargs):
        match bpy.context.scene.blendermcp_hunyuan3d_mode:
            case "OFFICIAL_API":
                return self.create_hunyuan_job_main_site(*args, **kwargs)
            case "LOCAL_API":
                return self.create_hunyuan_job_local_site(*args, **kwargs)
            case _:
                return f"Error: Unknown Hunyuan3D mode!"

    def create_hunyuan_job_main_site(
        self,
        text_prompt: str = None,
        image: str = None
    ):
        try:
            secret_id = _secret('hunyuan3d_secret_id')
            secret_key = _secret('hunyuan3d_secret_key')

            if not secret_id or not secret_key:
                return {"error": "SecretId or SecretKey is not given"}

            # Parameter verification
            if not text_prompt and not image:
                return {"error": "Prompt or Image is required"}
            if text_prompt and image:
                return {"error": "Prompt and Image cannot be provided simultaneously"}
            # Fixed parameter configuration
            service = "hunyuan"
            action = "SubmitHunyuanTo3DJob"
            version = "2023-09-01"
            region = "ap-guangzhou"

            headParams={
                "Action": action,
                "Version": version,
                "Region": region,
            }

            # Constructing request parameters
            data = {
                "Num": 1  # The current API limit is only 1
            }

            # Handling text prompts
            if text_prompt:
                if len(text_prompt) > 200:
                    return {"error": "Prompt exceeds 200 characters limit"}
                data["Prompt"] = text_prompt

            # Handling image
            if image:
                if re.match(r'^https?://', image, re.IGNORECASE) is not None:
                    data["ImageUrl"] = image
                else:
                    try:
                        # Convert to Base64 format
                        with open(image, "rb") as f:
                            image_base64 = base64.b64encode(f.read()).decode("ascii")
                        data["ImageBase64"] = image_base64
                    except Exception as e:
                        return {"error": f"Image encoding failed: {str(e)}"}
            
            # Get signed headers
            headers, endpoint = self.get_tencent_cloud_sign_headers("POST", "/", headParams, data, service, region, secret_id, secret_key)

            response = requests.post(
                endpoint,
                headers = headers,
                data = json.dumps(data)
            )

            if response.status_code == 200:
                return response.json()
            return {
                "error": f"API request failed with status {response.status_code}: {response}"
            }
        except Exception as e:
            return {"error": str(e)}

    def create_hunyuan_job_local_site(
        self,
        text_prompt: str = None,
        image: str = None):
        try:
            base_url = bpy.context.scene.blendermcp_hunyuan3d_api_url.rstrip('/')
            octree_resolution = bpy.context.scene.blendermcp_hunyuan3d_octree_resolution
            num_inference_steps = bpy.context.scene.blendermcp_hunyuan3d_num_inference_steps
            guidance_scale = bpy.context.scene.blendermcp_hunyuan3d_guidance_scale
            texture = bpy.context.scene.blendermcp_hunyuan3d_texture

            if not base_url:
                return {"error": "API URL is not given"}
            # Parameter verification
            if not text_prompt and not image:
                return {"error": "Prompt or Image is required"}

            # Constructing request parameters
            data = {
                "octree_resolution": octree_resolution,
                "num_inference_steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "texture": texture,
            }

            # Handling text prompts
            if text_prompt:
                data["text"] = text_prompt

            # Handling image
            if image:
                if re.match(r'^https?://', image, re.IGNORECASE) is not None:
                    try:
                        resImg = requests.get(image)
                        resImg.raise_for_status()
                        image_base64 = base64.b64encode(resImg.content).decode("ascii")
                        data["image"] = image_base64
                    except Exception as e:
                        return {"error": f"Failed to download or encode image: {str(e)}"} 
                else:
                    try:
                        # Convert to Base64 format
                        with open(image, "rb") as f:
                            image_base64 = base64.b64encode(f.read()).decode("ascii")
                        data["image"] = image_base64
                    except Exception as e:
                        return {"error": f"Image encoding failed: {str(e)}"}

            response = requests.post(
                f"{base_url}/generate",
                json = data,
            )

            if response.status_code != 200:
                return {
                    "error": f"Generation failed: {response.text}"
                }
        
            # Decode base64 and save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".glb") as temp_file:
                temp_file.write(response.content)
                temp_file_name = temp_file.name

            # Import the GLB file in the main thread
            def import_handler():
                bpy.ops.import_scene.gltf(filepath=temp_file_name)
                os.unlink(temp_file.name)
                return None
            
            bpy.app.timers.register(import_handler)

            return {
                "status": "DONE",
                "message": "Generation and Import glb succeeded"
            }
        except Exception as e:
            print(f"An error occurred: {e}")
            return {"error": str(e)}
        
    
    def poll_hunyuan_job_status(self, *args, **kwargs):
        return self.poll_hunyuan_job_status_ai(*args, **kwargs)
    
    def poll_hunyuan_job_status_ai(self, job_id: str):
        """Call the job status API to get the job status"""
        print(job_id)
        try:
            secret_id = _secret('hunyuan3d_secret_id')
            secret_key = _secret('hunyuan3d_secret_key')

            if not secret_id or not secret_key:
                return {"error": "SecretId or SecretKey is not given"}
            if not job_id:
                return {"error": "JobId is required"}
            
            service = "hunyuan"
            action = "QueryHunyuanTo3DJob"
            version = "2023-09-01"
            region = "ap-guangzhou"

            headParams={
                "Action": action,
                "Version": version,
                "Region": region,
            }

            clean_job_id = job_id.removeprefix("job_")
            data = {
                "JobId": clean_job_id
            }

            headers, endpoint = self.get_tencent_cloud_sign_headers("POST", "/", headParams, data, service, region, secret_id, secret_key)

            response = requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(data)
            )

            if response.status_code == 200:
                return response.json()
            return {
                "error": f"API request failed with status {response.status_code}: {response}"
            }
        except Exception as e:
            return {"error": str(e)}

    def import_generated_asset_hunyuan(self, *args, **kwargs):
        return self.import_generated_asset_hunyuan_ai(*args, **kwargs)
            
    def import_generated_asset_hunyuan_ai(self, name: str , zip_file_url: str):
        if not zip_file_url:
            return {"error": "Zip file not found"}
        
        # Validate URL
        if not re.match(r'^https?://', zip_file_url, re.IGNORECASE):
            return {"error": "Invalid URL format. Must start with http:// or https://"}
        
        # Create a temporary directory
        temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
        zip_file_path = osp.join(temp_dir, "model.zip")
        obj_file_path = osp.join(temp_dir, "model.obj")
        mtl_file_path = osp.join(temp_dir, "model.mtl")

        try:
            # Download ZIP file
            zip_response = requests.get(zip_file_url, stream=True)
            zip_response.raise_for_status()
            with open(zip_file_path, "wb") as f:
                for chunk in zip_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Unzip the ZIP
            with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
                zip_ref.extractall(temp_dir)

            # Find the .obj file (there may be multiple, assuming the main file is model.obj)
            for file in os.listdir(temp_dir):
                if file.endswith(".obj"):
                    obj_file_path = osp.join(temp_dir, file)

            if not osp.exists(obj_file_path):
                return {"succeed": False, "error": "OBJ file not found after extraction"}

            # Import obj file
            if self._op_exists(bpy.ops.wm.obj_import):
                bpy.ops.wm.obj_import(filepath=obj_file_path)
            else:
                bpy.ops.import_scene.obj(filepath=obj_file_path)

            imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
            if not imported_objs:
                return {"succeed": False, "error": "No mesh objects imported"}

            obj = imported_objs[0]
            if name:
                obj.name = name

            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return {"succeed": True, **result}
        except Exception as e:
            return {"succeed": False, "error": str(e)}
        finally:
            #  Clean up temporary zip and obj, save texture and mtl
            try:
                if os.path.exists(zip_file_path):
                    os.remove(zip_file_path) 
                if os.path.exists(obj_file_path):
                    os.remove(obj_file_path)
            except Exception as e:
                print(f"Failed to clean up temporary directory {temp_dir}: {e}")
    #endregion

# Blender Addon Preferences (no settings; everything lives in the sidebar panel)
class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    """Port, autostart and API keys: user preferences, never written into .blend files."""
    bl_idname = __name__

    port: bpy.props.IntProperty(
        name="Port", description="TCP port the BlenderMCP server listens on (127.0.0.1)",
        default=_DEFAULT_PORT, min=1024, max=65535)
    autostart_server: bpy.props.BoolProperty(
        name="Start server automatically",
        description="Start the MCP socket server when Blender starts (never in --background)",
        default=True)
    hyper3d_api_key: bpy.props.StringProperty(
        name="Hyper3D API Key", subtype='PASSWORD', description="API Key provided by Hyper3D", default="")
    sketchfab_api_key: bpy.props.StringProperty(
        name="Sketchfab API Key", subtype='PASSWORD', description="API Key provided by Sketchfab", default="")
    hunyuan3d_secret_id: bpy.props.StringProperty(
        name="Hunyuan 3D SecretId", subtype='PASSWORD', description="SecretId provided by Hunyuan 3D", default="")
    hunyuan3d_secret_key: bpy.props.StringProperty(
        name="Hunyuan 3D SecretKey", subtype='PASSWORD', description="SecretKey provided by Hunyuan 3D", default="")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "port")
        layout.prop(self, "autostart_server")
        box = layout.box()
        box.label(text="API keys (stored in your preferences, never in .blend files)", icon='LOCKED')
        box.prop(self, "hyper3d_api_key")
        box.operator("blendermcp.set_hyper3d_free_trial_api_key", text="Use the Hyper3D free trial key")
        box.prop(self, "sketchfab_api_key")
        box.prop(self, "hunyuan3d_secret_id")
        box.prop(self, "hunyuan3d_secret_key")
        layout.label(text="Per-file integration toggles: 3D Viewport sidebar (N) > BlenderMCP.", icon='INFO')
        layout.label(text="This fork sends no telemetry.", icon='CHECKMARK')

# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")

        layout.prop(scene, "blendermcp_use_hyper3d", text="Use Hyper3D Rodin 3D model generation")
        if scene.blendermcp_use_hyper3d:
            layout.prop(scene, "blendermcp_hyper3d_mode", text="Rodin Mode")
            layout.label(text="API key: Preferences > Add-ons > Blender MCP", icon='LOCKED')
            layout.operator("blendermcp.set_hyper3d_free_trial_api_key", text="Use the free trial key")

        layout.prop(scene, "blendermcp_use_sketchfab", text="Use assets from Sketchfab")
        if scene.blendermcp_use_sketchfab:
            layout.label(text="API key: Preferences > Add-ons > Blender MCP", icon='LOCKED')

        layout.prop(scene, "blendermcp_use_hunyuan3d", text="Use Tencent Hunyuan 3D model generation")
        if scene.blendermcp_use_hunyuan3d:
            layout.prop(scene, "blendermcp_hunyuan3d_mode", text="Hunyuan3D Mode")
            if scene.blendermcp_hunyuan3d_mode == 'OFFICIAL_API':
                layout.label(text="SecretId / SecretKey: Preferences > Add-ons > Blender MCP", icon='LOCKED')
            if scene.blendermcp_hunyuan3d_mode == 'LOCAL_API':
                layout.prop(scene, "blendermcp_hunyuan3d_api_url", text="API URL")
                layout.prop(scene, "blendermcp_hunyuan3d_octree_resolution", text="Octree Resolution")
                layout.prop(scene, "blendermcp_hunyuan3d_num_inference_steps", text="Number of Inference Steps")
                layout.prop(scene, "blendermcp_hunyuan3d_guidance_scale", text="Guidance Scale")
                layout.prop(scene, "blendermcp_hunyuan3d_texture", text="Generate Texture")

        # Runtime truth, never a saved Scene flag
        if not _server_running():
            layout.operator("blendermcp.start_server", text=f"Connect to MCP server (port {_port()})")
        else:
            layout.operator("blendermcp.stop_server", text="Disconnect from MCP server")
            srv = bpy.types.blendermcp_server
            layout.label(text=f"Running on {srv.host}:{srv.port}")

# Operator to set Hyper3D API Key
class BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"

    def execute(self, context):
        prefs = _prefs()
        if prefs is None:
            # 2.4.0: the legacy Scene secret property is gone; the key lives in preferences only
            self.report({'ERROR'}, "Add-on preferences unavailable; set the key under Preferences > Add-ons")
            return {'CANCELLED'}
        prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        context.scene.blendermcp_hyper3d_mode = 'MAIN_SITE'
        self.report({'INFO'}, "API Key set successfully!")
        return {'FINISHED'}

# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        info = ensure_server()
        if info.get("running"):
            self.report({'INFO'}, f"BlenderMCP server running on {info['host']}:{info['port']}")
            return {'FINISHED'}
        self.report({'ERROR'}, f"BlenderMCP server could not start: {info.get('error')}")
        return {'CANCELLED'}

# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server
        return {'FINISHED'}

# Registration functions
def register():
    # Port, autostart and API keys are add-on preferences (BLENDERMCP_AddonPreferences).
    # The four legacy Scene secret StringProperties (pre-2.1 storage) are no longer registered
    # as of 2.4.0 (H15): a pre-2.1 file still migrates because _migrate_legacy_secrets reads
    # the orphaned ID properties directly (user group on 4.x files, system group on 5.x files).

    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )

    bpy.types.Scene.blendermcp_use_hyper3d = bpy.props.BoolProperty(
        name="Use Hyper3D Rodin",
        description="Enable Hyper3D Rodin generatino integration",
        default=False
    )

    bpy.types.Scene.blendermcp_hyper3d_mode = bpy.props.EnumProperty(
        name="Rodin Mode",
        description="Choose the platform used to call Rodin APIs",
        items=[
            ("MAIN_SITE", "hyper3d.ai", "hyper3d.ai"),
            ("FAL_AI", "fal.ai", "fal.ai"),
        ],
        default="MAIN_SITE"
    )

    bpy.types.Scene.blendermcp_use_hunyuan3d = bpy.props.BoolProperty(
        name="Use Hunyuan 3D",
        description="Enable Hunyuan asset integration",
        default=False
    )

    bpy.types.Scene.blendermcp_hunyuan3d_mode = bpy.props.EnumProperty(
        name="Hunyuan3D Mode",
        description="Choose a local or official APIs",
        items=[
            ("LOCAL_API", "local api", "local api"),
            ("OFFICIAL_API", "official api", "official api"),
        ],
        default="LOCAL_API"
    )

    bpy.types.Scene.blendermcp_hunyuan3d_api_url = bpy.props.StringProperty(
        name="API URL",
        description="URL of the Hunyuan 3D API service",
        default="http://localhost:8081"
    )

    bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution = bpy.props.IntProperty(
        name="Octree Resolution",
        description="Octree resolution for the 3D generation",
        default=256,
        min=128,
        max=512,
    )

    bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps = bpy.props.IntProperty(
        name="Number of Inference Steps",
        description="Number of inference steps for the 3D generation",
        default=20,
        min=20,
        max=50,
    )

    bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale = bpy.props.FloatProperty(
        name="Guidance Scale",
        description="Guidance scale for the 3D generation",
        default=5.5,
        min=1.0,
        max=10.0,
    )

    bpy.types.Scene.blendermcp_hunyuan3d_texture = bpy.props.BoolProperty(
        name="Generate Texture",
        description="Whether to generate texture for the 3D model",
        default=False,
    )
    
    bpy.types.Scene.blendermcp_use_sketchfab = bpy.props.BoolProperty(
        name="Use Sketchfab",
        description="Enable Sketchfab asset integration",
        default=False
    )

    # Register preferences class
    bpy.utils.register_class(BLENDERMCP_AddonPreferences)

    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)

    # Launch hook for `blender --python-expr` (start_blender) and the load / exit handlers
    bpy.app.driver_namespace[_ENSURE_SERVER_HOOK] = ensure_server
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if hasattr(bpy.app.handlers, "exit_pre") and _on_exit_pre not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_on_exit_pre)

    # Autostart: on a cold start when the preference says so, or after an add-on reload when the
    # server was running. Deferred through a timer because bpy.context.scene is not available
    # inside register() at startup. Never in --background (no port opens during headless work;
    # ensure_server() still works there when called explicitly).
    restart = _restart_flag_get()
    _restart_flag_set(False)
    prefs = _prefs()
    autostart = prefs is not None and bool(prefs.autostart_server)
    if (restart or autostart) and not bpy.app.background:
        def _deferred_start():
            try:
                _migrate_legacy_secrets(_prefs())
                info = ensure_server()
                if info.get("running"):
                    state = "started" if info.get("started_now") else "already running"
                    print(f"BlenderMCP server {state} on {info['host']}:{info['port']}")
                else:
                    print(f"BlenderMCP autostart failed: {info.get('error')}")
            except Exception as e:
                print(f"BlenderMCP autostart failed: {e}")
            return None
        bpy.app.timers.register(_deferred_start, first_interval=0.5)

    print("BlenderMCP addon registered")

def unregister():
    # Remember whether the server was running so register() can restart it after a reload.
    _restart_flag_set(_server_running())
    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    bpy.app.driver_namespace.pop(_ENSURE_SERVER_HOOK, None)
    with suppress(ValueError):
        bpy.app.handlers.load_post.remove(_on_load_post)
    if hasattr(bpy.app.handlers, "exit_pre"):
        with suppress(ValueError):
            bpy.app.handlers.exit_pre.remove(_on_exit_pre)

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    bpy.utils.unregister_class(BLENDERMCP_AddonPreferences)

    del bpy.types.Scene.blendermcp_use_polyhaven
    del bpy.types.Scene.blendermcp_use_hyper3d
    del bpy.types.Scene.blendermcp_hyper3d_mode
    del bpy.types.Scene.blendermcp_use_sketchfab
    del bpy.types.Scene.blendermcp_use_hunyuan3d
    del bpy.types.Scene.blendermcp_hunyuan3d_mode
    del bpy.types.Scene.blendermcp_hunyuan3d_api_url
    del bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution
    del bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps
    del bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale
    del bpy.types.Scene.blendermcp_hunyuan3d_texture

    print("BlenderMCP addon unregistered")

if __name__ == "__main__":
    register()
