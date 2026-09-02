# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025

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
from bpy.props import IntProperty
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
    "version": (1, 6, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}

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

# Add User-Agent as required by Poly Haven API
REQ_HEADERS = requests.utils.default_headers()
REQ_HEADERS.update({"User-Agent": "blender-mcp"})

class BlenderMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
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
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:  # Reduced from 20 to 10
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
        }

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

    def capture_viewport_angle(self, angle="front", max_size=800, filepath=None):
        """
        Capture the 3D viewport from a named angle.
        angle: one of front, back, left, right, top, bottom, iso_front_right, iso_front_left
        Frames the selected objects, or the whole scene when nothing is selected.
        """
        import math
        if angle not in self._VIEW_PRESETS:
            return {"error": f"Unknown angle: {angle}. Choose from: {list(self._VIEW_PRESETS.keys())}"}

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

            with bpy.context.temp_override(area=area, region=region):
                # Make sure the new view is drawn before it is read back
                with suppress(Exception):
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                bpy.ops.screen.screenshot_area(filepath=filepath)
            if not os.path.exists(filepath):
                return {"error": "Screenshot was not written"}

            # Resize if needed
            img = bpy.data.images.load(filepath)
            try:
                w, h = img.size
                if max(w, h) > max_size:
                    scale = max_size / max(w, h)
                    img.scale(max(1, int(w * scale)), max(1, int(h * scale)))
                    img.file_format = 'PNG'
                    img.save()
                    w, h = img.size
            finally:
                bpy.data.images.remove(img)

            return {"success": True, "angle": angle, "filepath": filepath, "width": w, "height": h}

        finally:
            prefs_view.smooth_view = orig_smooth_view
            r3d.view_matrix = orig_view_matrix
            r3d.view_perspective = orig_perspective

    def capture_contact_sheet(self, angles=None, max_size=512, filepath=None):
        """
        Capture multiple viewport angles and return paths for each.
        angles: list of angle names; defaults to [front, right, top, iso_front_right]
        """
        if angles is None:
            angles = ["front", "right", "top", "iso_front_right"]

        results = {}
        for angle in angles:
            fp = os.path.join(tempfile.gettempdir(), f"blender_cs_{angle}_{os.getpid()}.png")
            r = self.capture_viewport_angle(angle=angle, max_size=max_size, filepath=fp)
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
        try:
            # Workbench has no Z pass; fall back to EEVEE (identifier changed in 4.2)
            if tmp.render.engine == 'BLENDER_WORKBENCH':
                engines = [e.identifier for e in
                           bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
                for eng in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
                    if eng in engines:
                        tmp.render.engine = eng
                        break
            # Depth is deterministic: one sample is enough
            with suppress(Exception):
                tmp.eevee.taa_render_samples = 1
            with suppress(Exception):
                tmp.cycles.samples = 1

            for vl in tmp.view_layers:
                vl.use_pass_z = True

            tmp.use_nodes = True
            tree = tmp.node_tree
            nodes, links = tree.nodes, tree.links
            nodes.clear()

            # RenderLayers -> Map Range (0..max_depth -> 0..1) -> Invert -> Composite
            rl = nodes.new("CompositorNodeRLayers")
            rl.scene = tmp
            if view_layer_name in tmp.view_layers:
                rl.layer = view_layer_name
            rl.location = (0, 0)

            map_node = nodes.new("CompositorNodeMapRange")
            map_node.location = (250, 0)
            map_node.inputs["From Min"].default_value = 0.0
            map_node.inputs["From Max"].default_value = max_depth
            map_node.inputs["To Min"].default_value = 0.0
            map_node.inputs["To Max"].default_value = 1.0
            map_node.use_clamp = True

            invert = nodes.new("CompositorNodeInvert")
            invert.location = (450, 0)

            composite = nodes.new("CompositorNodeComposite")
            composite.location = (650, 0)

            links.new(rl.outputs["Depth"], map_node.inputs["Value"])
            links.new(map_node.outputs["Value"], invert.inputs["Color"])
            links.new(invert.outputs["Color"], composite.inputs["Image"])

            tmp.render.filepath = filepath
            tmp.render.image_settings.file_format = 'PNG'
            tmp.render.image_settings.color_mode = 'BW'
            if os.path.exists(filepath):
                os.remove(filepath)

            result = bpy.ops.render.render(write_still=True, scene=tmp.name)
            if 'FINISHED' not in result or not os.path.exists(filepath):
                return {"error": f"Depth render did not produce a file (operator returned {set(result)})"}
        finally:
            with suppress(Exception):
                bpy.data.scenes.remove(tmp, do_unlink=True)

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
                    "error": f"Mesh has {len(mesh.vertices)} vertices — exceeds max_verts={max_verts}. "
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
                return {"error": f"Mesh has {len(mesh.edges)} edges — exceeds "
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
                return {"error": f"Mesh has {len(mesh.polygons)} faces — exceeds "
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
            new_verts = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
            # Translate each new vert along its normal
            for v in new_verts:
                v.co += v.normal * amount

        return {"success": True, "name": name,
                "extruded_faces": len(face_indices), "amount": amount}

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

    @contextmanager
    def _render_settings(self, scene, width, height, samples, file_format='PNG'):
        """Temporarily apply render settings, restoring everything afterwards."""
        r = scene.render
        cyc = getattr(scene, "cycles", None)
        eev = getattr(scene, "eevee", None)
        saved = (scene.camera, r.resolution_x, r.resolution_y, r.resolution_percentage,
                 r.filepath, r.image_settings.file_format,
                 cyc.samples if cyc else None,
                 getattr(eev, "taa_render_samples", None) if eev else None)
        try:
            r.resolution_x = int(width)
            r.resolution_y = int(height)
            r.resolution_percentage = 100
            r.image_settings.file_format = file_format
            if cyc:
                cyc.samples = int(samples)
            if eev and hasattr(eev, "taa_render_samples"):
                eev.taa_render_samples = int(samples)
            yield
        finally:
            (scene.camera, r.resolution_x, r.resolution_y, r.resolution_percentage,
             r.filepath, r.image_settings.file_format, cyc_s, eev_s) = saved
            if cyc and cyc_s is not None:
                cyc.samples = cyc_s
            if eev and eev_s is not None:
                eev.taa_render_samples = eev_s

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

    def export_object(self, name=None, filepath=None, file_format="glb"):
        """
        Export an object (or the entire scene if name is None).
        file_format: glb, gltf, fbx, obj, stl, ply
        """
        file_format = (file_format or "glb").lower().lstrip(".")
        supported = ("glb", "gltf", "fbx", "obj", "stl", "ply")
        if file_format not in supported:
            return {"error": f"Unsupported format: {file_format}. Choose from: {list(supported)}"}
        if not filepath:
            filepath = os.path.join(tempfile.gettempdir(),
                                    f"blender_export_{os.getpid()}.{file_format}")

        # Select only the target object if specified
        selected = name is not None
        if name:
            obj = bpy.data.objects.get(name)
            if not obj:
                return {"error": f"Object not found: {name}"}
            self._select_only(obj)

        # Blender 4.x: OBJ/STL/PLY moved from the Python add-ons to wm.* C operators
        try:
            if file_format in ("glb", "gltf"):
                bpy.ops.export_scene.gltf(
                    filepath=filepath,
                    export_format="GLB" if file_format == "glb" else "GLTF_SEPARATE",
                    use_selection=selected,
                )
            elif file_format == "fbx":
                bpy.ops.export_scene.fbx(filepath=filepath, use_selection=selected)
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
        return {"success": True, "filepath": filepath, "format": file_format}

    def import_file(self, filepath):
        """
        Import a 3D file. Supports: glb/gltf, fbx, obj, stl, ply, blend.
        """
        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}

        ext = os.path.splitext(filepath)[1].lower()
        supported = (".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply", ".blend")
        if ext not in supported:
            return {"error": f"Unsupported file extension: {ext}. Choose from: {list(supported)}"}
        before = set(bpy.data.objects.keys())

        try:
            if ext in (".glb", ".gltf"):
                bpy.ops.import_scene.gltf(filepath=filepath)
            elif ext == ".fbx":
                bpy.ops.import_scene.fbx(filepath=filepath)
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
        new_objects = list(after - before)
        return {"success": True, "filepath": filepath, "imported_objects": new_objects}

    def save_blend(self, filepath=None):
        """
        Save the current Blender project as a .blend file.
        If filepath is omitted, saves over the currently open file (or to a temp path
        if the file has never been saved before).
        """
        import tempfile
        if not filepath:
            current = bpy.data.filepath
            if current:
                filepath = current
            else:
                filepath = os.path.join(tempfile.gettempdir(),
                                        f"blender_unsaved_{os.getpid()}.blend")

        if not filepath.endswith(".blend"):
            filepath += ".blend"

        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        return {"success": True, "filepath": filepath}

    def load_blend(self, filepath):
        """
        Open a .blend file, replacing the current scene.
        WARNING: unsaved changes to the current file will be lost.
        """
        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}
        if not filepath.lower().endswith(".blend"):
            return {"error": "load_blend only accepts .blend files. Use import_file for 3D model formats."}

        bpy.ops.wm.open_mainfile(filepath=filepath)
        # After open, report the new scene state
        scene = bpy.context.scene
        return {
            "success": True,
            "filepath": filepath,
            "scene_name": scene.name,
            "object_count": len(scene.objects),
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
        self._select_only(obj)
        auto_method = None
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

    def parent_object(self, child_name, parent_name, keep_transform=True):
        """Parent child_name to parent_name, optionally preserving world transform."""
        child = bpy.data.objects.get(child_name)
        parent = bpy.data.objects.get(parent_name)
        if not child:
            return {"error": f"Child object not found: {child_name}"}
        if not parent:
            return {"error": f"Parent object not found: {parent_name}"}

        orig_matrix = child.matrix_world.copy() if keep_transform else None
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
        if keep_transform and orig_matrix:
            child.matrix_world = orig_matrix
        return {"success": True, "child": child_name, "parent": parent_name}

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

        # TexCoord → Mapping → Image Texture
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
        props: dict of modifier property name → value (extra kwargs are merged in).
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
        solver: EXACT (better quality), FAST (faster, less reliable)
        apply: if True, applies the modifier and removes the cutter object
        """
        target = bpy.data.objects.get(target_name)
        cutter = bpy.data.objects.get(cutter_name)
        if not target:
            return {"error": f"Target not found: {target_name}"}
        if not cutter:
            return {"error": f"Cutter not found: {cutter_name}"}

        mod = target.modifiers.new(name="Boolean", type="BOOLEAN")
        mod.operation = operation
        mod.object    = cutter
        if hasattr(mod, 'solver'):
            mod.solver = solver

        if apply:
            bpy.context.view_layer.objects.active = target
            bpy.ops.object.modifier_apply(modifier=mod.name)
            bpy.data.objects.remove(cutter, do_unlink=True)

        return {"success": True, "target": target_name, "cutter": cutter_name,
                "operation": operation, "applied": apply}

    # ─── Render settings ─────────────────────────────────────────────────────

    def set_render_settings(self, engine=None, width=None, height=None,
                            samples=None, output_path=None, file_format=None,
                            transparent_background=None):
        """
        Configure scene render settings.
        engine: CYCLES, BLENDER_EEVEE, BLENDER_WORKBENCH
        file_format: PNG, JPEG, EXR, TIFF
        """
        scene = bpy.context.scene
        if engine:
            # Normalise legacy name: BLENDER_EEVEE was renamed to BLENDER_EEVEE_NEXT in Blender 4.x
            engine_upper = engine.upper()
            if engine_upper == 'BLENDER_EEVEE' and bpy.app.version >= (4, 0, 0):
                engine_upper = 'BLENDER_EEVEE_NEXT'
            scene.render.engine = engine_upper
        if width:
            scene.render.resolution_x = int(width)
        if height:
            scene.render.resolution_y = int(height)
        if output_path:
            scene.render.filepath = output_path
        if file_format:
            scene.render.image_settings.file_format = file_format.upper()
        if transparent_background is not None:
            scene.render.film_transparent = bool(transparent_background)

        if samples is not None:
            if scene.render.engine == 'CYCLES':
                scene.cycles.samples = int(samples)
            elif scene.render.engine in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
                if hasattr(scene, 'eevee'):
                    scene.eevee.taa_render_samples = int(samples)

        return {
            "success": True,
            "engine": scene.render.engine,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "output": scene.render.filepath,
            "transparent": scene.render.film_transparent,
        }

    # ─── Animation ───────────────────────────────────────────────────────────

    def add_keyframe(self, name, data_path="location", frame=None, value=None):
        """
        Insert a keyframe on an object property.
        data_path: 'location', 'rotation_euler', 'scale', or any animatable path
        frame: frame number (defaults to current scene frame)
        value: if given, sets the property to this value before keying.
               For location/rotation/scale pass [x, y, z]; for single values pass a number.
        """
        import math
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}

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
                owner = obj.path_resolve(data_path[:split_at])
                attr = data_path[split_at + 1:]
            else:
                owner, attr = obj, data_path
            current = getattr(owner, attr)
        except Exception as e:
            return {"error": f"Cannot resolve '{data_path}' on {name}: {e}"}

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

        return {"success": True, "name": name, "data_path": data_path,
                "keyed_on": id_owner.name, "frame": scene.frame_current}

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

                        # Use a color space that exists in all Blender versions
                        if file_format.lower() == 'exr':
                            # Try to use Linear color space for EXR files
                            try:
                                env_tex.image.colorspace_settings.name = 'Linear'
                            except:
                                # Fallback to Non-Color if Linear isn't available
                                env_tex.image.colorspace_settings.name = 'Non-Color'
                        else:  # hdr
                            # For HDR files, try these options in order
                            for color_space in ['Linear', 'Linear Rec.709', 'Non-Color']:
                                try:
                                    env_tex.image.colorspace_settings.name = color_space
                                    break  # Stop if we successfully set a color space
                                except:
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

                        # Clean up temporary file
                        try:
                            tempfile._cleanup()  # This will clean up all temporary files
                        except:
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
                                # Get the URL for the included file - this is the fix
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

                    # Debug info
                    print(f"Image size: {img.size[0]}x{img.size[1]}")
                    print(f"Color space: {img.colorspace_settings.name}")
                    print(f"File format: {img.file_format}")
                    print(f"Is packed: {bool(img.packed_file)}")

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

            # Second pass: Connect nodes with proper handling for special cases
            texture_nodes = {}

            # First find all texture nodes and store them by map type
            for node in nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    for map_type, image in texture_images.items():
                        if node.image == image:
                            texture_nodes[map_type] = node
                            break

            # Now connect everything using the nodes instead of images
            # Handle base color (diffuse)
            for map_name in ['color', 'diffuse', 'albedo']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Base Color'])
                    print(f"Connected {map_name} to Base Color")
                    break

            # Handle roughness
            for map_name in ['roughness', 'rough']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Roughness'])
                    print(f"Connected {map_name} to Roughness")
                    break

            # Handle metallic
            for map_name in ['metallic', 'metalness', 'metal']:
                if map_name in texture_nodes:
                    links.new(texture_nodes[map_name].outputs['Color'], principled.inputs['Metallic'])
                    print(f"Connected {map_name} to Metallic")
                    break

            # Handle normal maps
            for map_name in ['gl', 'dx', 'nor']:
                if map_name in texture_nodes:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (100, 100)
                    links.new(texture_nodes[map_name].outputs['Color'], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], principled.inputs['Normal'])
                    print(f"Connected {map_name} to Normal")
                    break

            # Handle displacement
            for map_name in ['displacement', 'disp', 'height']:
                if map_name in texture_nodes:
                    disp_node = nodes.new(type='ShaderNodeDisplacement')
                    disp_node.location = (300, -200)
                    disp_node.inputs['Scale'].default_value = 0.1  # Reduce displacement strength
                    links.new(texture_nodes[map_name].outputs['Color'], disp_node.inputs['Height'])
                    links.new(disp_node.outputs['Displacement'], output.inputs['Displacement'])
                    print(f"Connected {map_name} to Displacement")
                    break

            # Handle ARM texture (Ambient Occlusion, Roughness, Metallic)
            if 'arm' in texture_nodes:
                separate_rgb = nodes.new(type='ShaderNodeSeparateRGB')
                separate_rgb.location = (-200, -100)
                links.new(texture_nodes['arm'].outputs['Color'], separate_rgb.inputs['Image'])

                # Connect Roughness (G) if no dedicated roughness map
                if not any(map_name in texture_nodes for map_name in ['roughness', 'rough']):
                    links.new(separate_rgb.outputs['G'], principled.inputs['Roughness'])
                    print("Connected ARM.G to Roughness")

                # Connect Metallic (B) if no dedicated metallic map
                if not any(map_name in texture_nodes for map_name in ['metallic', 'metalness', 'metal']):
                    links.new(separate_rgb.outputs['B'], principled.inputs['Metallic'])
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
                    links.new(separate_rgb.outputs['R'], mix_node.inputs[2])
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

            # CRITICAL: Make sure to clear all existing materials from the object
            while len(obj.data.materials) > 0:
                obj.data.materials.pop(index=0)

            # Assign the new material to the object
            obj.data.materials.append(new_mat)

            # CRITICAL: Make the object active and select it
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)

            # CRITICAL: Force Blender to update the material
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
                            connections.append(f"{output.name} → {link.to_node.name}.{link.to_socket.name}")

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
            if not bpy.context.scene.blendermcp_hyper3d_api_key:
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
                f"Key type: {'private' if bpy.context.scene.blendermcp_hyper3d_api_key != RODIN_FREE_TRIAL_KEY else 'free_trial'}"
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
                    "Authorization": f"Bearer {bpy.context.scene.blendermcp_hyper3d_api_key}",
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
                    "Authorization": f"Key {bpy.context.scene.blendermcp_hyper3d_api_key}",
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
                "Authorization": f"Bearer {bpy.context.scene.blendermcp_hyper3d_api_key}",
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
                "Authorization": f"KEY {bpy.context.scene.blendermcp_hyper3d_api_key}",
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
        # imported_objects = [obj for obj in bpy.context.view_layer.objects if obj.select_get()]

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
                "Authorization": f"Bearer {bpy.context.scene.blendermcp_hyper3d_api_key}",
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
                "Authorization": f"Key {bpy.context.scene.blendermcp_hyper3d_api_key}",
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
        api_key = bpy.context.scene.blendermcp_sketchfab_api_key

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
            api_key = bpy.context.scene.blendermcp_sketchfab_api_key
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
            
            api_key = bpy.context.scene.blendermcp_sketchfab_api_key
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
            api_key = bpy.context.scene.blendermcp_sketchfab_api_key
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
                    
                    # ✅ Only apply scale to ROOT objects (not children!)
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
                    if not bpy.context.scene.blendermcp_hunyuan3d_secret_id or not bpy.context.scene.blendermcp_hunyuan3d_secret_key:
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

        # ************* Step 2: Construct the reception signature string *************
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
            secret_id = bpy.context.scene.blendermcp_hunyuan3d_secret_id
            secret_key = bpy.context.scene.blendermcp_hunyuan3d_secret_key

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
            secret_id = bpy.context.scene.blendermcp_hunyuan3d_secret_id
            secret_key = bpy.context.scene.blendermcp_hunyuan3d_secret_key

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
            if bpy.app.version>=(4, 0, 0):
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
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        layout.label(text="Configure the server port and integrations in the 3D Viewport sidebar (N) > BlenderMCP.",
                     icon='INFO')
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

        layout.prop(scene, "blendermcp_port")
        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")

        layout.prop(scene, "blendermcp_use_hyper3d", text="Use Hyper3D Rodin 3D model generation")
        if scene.blendermcp_use_hyper3d:
            layout.prop(scene, "blendermcp_hyper3d_mode", text="Rodin Mode")
            layout.prop(scene, "blendermcp_hyper3d_api_key", text="API Key")
            layout.operator("blendermcp.set_hyper3d_free_trial_api_key", text="Set Free Trial API Key")

        layout.prop(scene, "blendermcp_use_sketchfab", text="Use assets from Sketchfab")
        if scene.blendermcp_use_sketchfab:
            layout.prop(scene, "blendermcp_sketchfab_api_key", text="API Key")

        layout.prop(scene, "blendermcp_use_hunyuan3d", text="Use Tencent Hunyuan 3D model generation")
        if scene.blendermcp_use_hunyuan3d:
            layout.prop(scene, "blendermcp_hunyuan3d_mode", text="Hunyuan3D Mode")
            if scene.blendermcp_hunyuan3d_mode == 'OFFICIAL_API':
                layout.prop(scene, "blendermcp_hunyuan3d_secret_id", text="SecretId")
                layout.prop(scene, "blendermcp_hunyuan3d_secret_key", text="SecretKey")
            if scene.blendermcp_hunyuan3d_mode == 'LOCAL_API':
                layout.prop(scene, "blendermcp_hunyuan3d_api_url", text="API URL")
                layout.prop(scene, "blendermcp_hunyuan3d_octree_resolution", text="Octree Resolution")
                layout.prop(scene, "blendermcp_hunyuan3d_num_inference_steps", text="Number of Inference Steps")
                layout.prop(scene, "blendermcp_hunyuan3d_guidance_scale", text="Guidance Scale")
                layout.prop(scene, "blendermcp_hunyuan3d_texture", text="Generate Texture")
        
        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcp.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")

# Operator to set Hyper3D API Key
class BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"

    def execute(self, context):
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
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)

        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = True

        return {'FINISHED'}

# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server

        scene.blendermcp_server_running = False

        return {'FINISHED'}

# Registration functions
def register():
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535
    )

    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running",
        default=False
    )

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
        name="Hyper3D API Key",
        subtype="PASSWORD",
        description="API Key provided by Hyper3D",
        default=""
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
        name="Hunyuan 3D SecretId",
        description="SecretId provided by Hunyuan 3D",
        default=""
    )

    bpy.types.Scene.blendermcp_hunyuan3d_secret_key = bpy.props.StringProperty(
        name="Hunyuan 3D SecretKey",
        subtype="PASSWORD",
        description="SecretKey provided by Hunyuan 3D",
        default=""
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
        name="Sketchfab API Key",
        subtype="PASSWORD",
        description="API Key provided by Sketchfab",
        default=""
    )

    # Register preferences class
    bpy.utils.register_class(BLENDERMCP_AddonPreferences)

    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)

    # Auto-restart the server if it was running before the reload.
    if _restart_flag_get():
        _restart_flag_set(False)
        def _deferred_start():
            try:
                port = bpy.context.scene.blendermcp_port
                if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
                    bpy.types.blendermcp_server = BlenderMCPServer(port=port)
                bpy.types.blendermcp_server.start()
                bpy.context.scene.blendermcp_server_running = True
                print(f"BlenderMCP server auto-restarted on port {port}")
            except Exception as e:
                print(f"BlenderMCP auto-restart failed: {e}")
        bpy.app.timers.register(_deferred_start, first_interval=0.5)

    print("BlenderMCP addon registered")

def unregister():
    # Remember whether the server was running so register() can restart it.
    _restart_flag_set(
        hasattr(bpy.types, "blendermcp_server")
        and bpy.types.blendermcp_server is not None
        and getattr(bpy.context.scene, "blendermcp_server_running", False)
    )
    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    bpy.utils.unregister_class(BLENDERMCP_AddonPreferences)

    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
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
