# Code created by Siddharth Ahuja: www.github.com/ahujasid (c) 2025

import re
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
    "version": (2, 2, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

# Wire protocol number, mirrored by the server's PROTOCOL constant. Bumped when a handler key
# is renamed or removed, or an existing payload / reply key changes meaning or type; purely
# additive keys with addon-side defaults do not bump it.
PROTOCOL = 1

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


def _secret(name):
    """
    API key / secret by short name (see _SECRET_NAMES): preferences first, then the legacy
    Scene property as a read-only fallback. The legacy Scene properties stay registered but
    undrawn through 2.1.x so pre-2.1 files still load and migrate; they go away in 2.2.0.
    """
    prefs = _prefs()
    if prefs is not None:
        value = getattr(prefs, name, "") or ""
        if value:
            return value
    scene = getattr(bpy.context, "scene", None)
    return (getattr(scene, "blendermcp_" + name, "") or "") if scene is not None else ""


def _migrate_legacy_secrets(prefs, scene=None):
    """
    One-time move of non-empty legacy Scene secrets into prefs (only where the prefs value is
    empty), blanking the Scene copy so it is not saved into the file again. prefs is a
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
        legacy = getattr(scene, "blendermcp_" + name, "") or ""
        if legacy and not (getattr(prefs, name, "") or ""):
            try:
                setattr(prefs, name, legacy)
                setattr(scene, "blendermcp_" + name, "")
                migrated.append(name)
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



    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
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

            # Collect minimal object information (limit to first 10 objects; documented in TOOLS.md)
            for i, obj in enumerate(scene.objects):
                if i >= 10:
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
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

    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}

            # Capture stdout during execution, and return it as result
            capture_buffer = io.StringIO()
            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            captured_output = capture_buffer.getvalue()
            return {"executed": True, "result": captured_output}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")



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
                "background": bool(bpy.app.background)}

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

    def apply_modifier(self, name, modifier_name):
        """Apply a named modifier on an object."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        mod = obj.modifiers.get(modifier_name)
        if not mod:
            return {"error": f"Modifier '{modifier_name}' not found on {name}"}
        with self._selection_scope():
            self._select_only(obj)
            with bpy.context.temp_override(object=obj, active_object=obj):
                bpy.ops.object.modifier_apply(modifier=modifier_name)
        return {"success": True, "name": name, "applied_modifier": modifier_name}

    def get_mesh_stats(self, name):
        """Return detailed mesh topology stats for an object."""
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        if obj.type != 'MESH':
            return {"error": f"{name} is not a mesh object"}
        mesh = obj.data
        n_polys = len(mesh.polygons)
        loop_totals = array.array('i', [0]) * n_polys
        mesh.polygons.foreach_get("loop_total", loop_totals)
        tri_count = sum(loop_totals) - 2 * n_polys
        return {
            "name": name,
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
            "triangles": tri_count,
            "materials": len(obj.material_slots),
            "modifiers": [m.name for m in obj.modifiers],
            "bounding_box": self._get_aabb(obj),
        }

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

    def export_object(self, name=None, filepath=None, file_format="glb", include_hierarchy=True,
                      bake_anim=True, add_leaf_bones=False, use_armature_deform_only=True,
                      bake_anim_simplify_factor=0.0, mesh_smooth_type=None, primary_bone_axis=None,
                      secondary_bone_axis=None, apply_scale_options=None,
                      export_animations=True, export_skins=True, export_morph=True):
        """
        Export an object (or the entire scene if name is None).
        file_format: glb, gltf, fbx, obj, stl, ply
        include_hierarchy: also select the parent armature (if any) and every child, so a
                           skinned mesh exports rigged (selection restored afterwards).
        FBX only: bake_anim, add_leaf_bones (default False: no *_end bones), use_armature_deform_only,
                  bake_anim_simplify_factor, mesh_smooth_type (live enum: OFF/FACE/EDGE, +SMOOTH_GROUP
                  on 5.2), primary_bone_axis / secondary_bone_axis (X/Y/Z/-X/-Y/-Z), apply_scale_options.
        glTF only: export_animations, export_skins, export_morph.
        Params that do not apply to the chosen format are reported under "ignored".
        """
        file_format = (file_format or "glb").lower().lstrip(".")
        supported = ("glb", "gltf", "fbx", "obj", "stl", "ply")
        if file_format not in supported:
            return {"error": f"Unsupported format: {file_format}. Choose from: {list(supported)}"}
        if not filepath:
            filepath = os.path.join(tempfile.gettempdir(),
                                    f"blender_export_{os.getpid()}.{file_format}")

        # Select only the target object (and its rig hierarchy) if specified
        selected = name is not None
        obj = None
        if name:
            obj = bpy.data.objects.get(name)
            if not obj:
                return {"error": f"Object not found: {name}"}

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

        exported = []
        # Blender 4.x: OBJ/STL/PLY moved from the Python add-ons to wm.* C operators
        try:
            with self._selection_scope():
                if obj is not None:
                    self._select_only(obj)
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
                        bpy.ops.wm.obj_export(filepath=filepath, export_selected_objects=selected)
                    else:
                        bpy.ops.export_scene.obj(filepath=filepath, use_selection=selected)
                elif file_format == "stl":
                    if self._op_exists(bpy.ops.wm.stl_export):
                        bpy.ops.wm.stl_export(filepath=filepath, export_selected_objects=selected)
                    else:
                        bpy.ops.export_mesh.stl(filepath=filepath, use_selection=selected)
                elif file_format == "ply":
                    if self._op_exists(bpy.ops.wm.ply_export):
                        bpy.ops.wm.ply_export(filepath=filepath, export_selected_objects=selected)
                    else:
                        bpy.ops.export_mesh.ply(filepath=filepath, use_selection=selected)
        except Exception as e:
            return {"error": f"Export failed: {e}"}

        if not os.path.exists(filepath) and file_format != "gltf":
            return {"error": f"Exporter finished but {filepath} was not written"}
        reply = {"success": True, "filepath": filepath, "format": file_format,
                 "exported_objects": exported if selected else "all",
                 "include_hierarchy": bool(include_hierarchy) if selected else None}
        if fbx_kwargs:
            reply["fbx_options"] = fbx_kwargs
        if gltf_kwargs:
            reply["gltf_options"] = gltf_kwargs
        if ignored:
            reply["ignored"] = ignored
        return reply

    def import_file(self, filepath):
        """
        Import a 3D file. Supports: glb/gltf, fbx, obj, stl, ply, blend, bvh (needs the
        io_anim_bvh add-on, enabled in factory preferences).
        Reply: imported_objects (user geometry), armatures, actions (all), new_actions, and
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

        try:
            if ext in (".glb", ".gltf"):
                # K8: the importer's bone custom-shape mesh (Icosphere) is suppressed at the source
                # on both versions; the kwarg is try-passed so an importer without it still works
                try:
                    bpy.ops.import_scene.gltf(filepath=filepath, disable_bone_shape=True)
                except TypeError:
                    bpy.ops.import_scene.gltf(filepath=filepath)
            elif ext == ".fbx":
                bpy.ops.import_scene.fbx(filepath=filepath)
            elif ext == ".bvh":
                bpy.ops.import_anim.bvh(filepath=filepath)
            elif ext == ".obj":
                if self._op_exists(bpy.ops.wm.obj_import):
                    bpy.ops.wm.obj_import(filepath=filepath)
                else:
                    bpy.ops.import_scene.obj(filepath=filepath)
            elif ext == ".stl":
                if self._op_exists(bpy.ops.wm.stl_import):
                    bpy.ops.wm.stl_import(filepath=filepath)
                else:
                    bpy.ops.import_mesh.stl(filepath=filepath)
            elif ext == ".ply":
                if self._op_exists(bpy.ops.wm.ply_import):
                    bpy.ops.wm.ply_import(filepath=filepath)
                else:
                    bpy.ops.import_mesh.ply(filepath=filepath)
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
        return {"success": True, "filepath": filepath, "imported_objects": imported,
                "armatures": armatures, "actions": sorted(bpy.data.actions.keys()),
                "new_actions": new_actions, "bone_shape_objects": bone_shapes}

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

    def set_origin(self, name, origin_type="ORIGIN_GEOMETRY"):
        """
        Set the object origin.
        origin_type: ORIGIN_GEOMETRY, ORIGIN_CURSOR, ORIGIN_CENTER_OF_MASS,
                     ORIGIN_CENTER_OF_VOLUME
        """
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        with self._selection_scope():
            self._select_only(obj)
            bpy.ops.object.origin_set(type=origin_type, center='MEDIAN')
        return {"success": True, "name": name, "origin_type": origin_type,
                "new_location": list(obj.location)}

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
            if not hasattr(mod, k):
                unset[k] = "no such property"
                continue
            try:
                # Object references (e.g. BOOLEAN.object, SHRINKWRAP.target) come in by name
                if isinstance(v, str) and isinstance(getattr(mod, k), (bpy.types.Object, type(None))) \
                        and mod.bl_rna.properties[k].type == 'POINTER':
                    target = bpy.data.objects.get(v)
                    if target is None:
                        unset[k] = f"object '{v}' not found"
                        continue
                    v = target
                setattr(mod, k, v)
            except Exception as ex:
                unset[k] = str(ex)

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
        """Expand globs (fnmatch) in pattern_list against names; returns (matched, unmatched_patterns)."""
        import fnmatch
        matched, unmatched = [], []
        for pat in pattern_list:
            hits = [n for n in names if fnmatch.fnmatchcase(n, pat)]
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
        return {"success": True, "armature": arm.name, "bone_count": len(arm.data.bones), "bones": bones,
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
        if prefs is not None:
            prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        else:
            context.scene.blendermcp_hyper3d_api_key = RODIN_FREE_TRIAL_KEY
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
    # The four legacy Scene secret properties below stay registered but undrawn through
    # 2.1.x so pre-2.1 files load and migrate silently; they are removed in 2.2.0.
    _LEGACY = "Legacy pre-2.1 storage; migrated to the add-on preferences, removed in 2.2.0"

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

    bpy.types.Scene.blendermcp_hyper3d_api_key = bpy.props.StringProperty(
        name="Hyper3D API Key (legacy)",
        subtype="PASSWORD",
        description=_LEGACY,
        default="",
        options={'HIDDEN'}
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

    bpy.types.Scene.blendermcp_hunyuan3d_secret_id = bpy.props.StringProperty(
        name="Hunyuan 3D SecretId (legacy)",
        subtype="PASSWORD",
        description=_LEGACY,
        default="",
        options={'HIDDEN'}
    )

    bpy.types.Scene.blendermcp_hunyuan3d_secret_key = bpy.props.StringProperty(
        name="Hunyuan 3D SecretKey (legacy)",
        subtype="PASSWORD",
        description=_LEGACY,
        default="",
        options={'HIDDEN'}
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

    bpy.types.Scene.blendermcp_sketchfab_api_key = bpy.props.StringProperty(
        name="Sketchfab API Key (legacy)",
        subtype="PASSWORD",
        description=_LEGACY,
        default="",
        options={'HIDDEN'}
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
    del bpy.types.Scene.blendermcp_hyper3d_api_key
    del bpy.types.Scene.blendermcp_use_sketchfab
    del bpy.types.Scene.blendermcp_sketchfab_api_key
    del bpy.types.Scene.blendermcp_use_hunyuan3d
    del bpy.types.Scene.blendermcp_hunyuan3d_mode
    del bpy.types.Scene.blendermcp_hunyuan3d_secret_id
    del bpy.types.Scene.blendermcp_hunyuan3d_secret_key
    del bpy.types.Scene.blendermcp_hunyuan3d_api_url
    del bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution
    del bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps
    del bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale
    del bpy.types.Scene.blendermcp_hunyuan3d_texture

    print("BlenderMCP addon unregistered")

if __name__ == "__main__":
    register()
