"""BlenderMCP server: the FastMCP tools that drive Blender through the add-on socket.

Lives at src/blender_mcp/server.py and runs through the ``blender-mcp`` entry point.
Server-side configuration comes from settings.py (settings.json + environment).
"""
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
import tempfile
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List
import os
import sys
import glob as _glob
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import time as _time
from pathlib import Path
import base64
from urllib.parse import urlparse
import io
import inspect
import functools
import threading
import requests as _requests

from . import __version__, PROTOCOL
from . import settings as _settings

# Optional PIL — used for image safety guards and compositing
try:
    from PIL import Image as PILImage, ImageDraw
    _PIL_AVAILABLE = True
except ImportError:
    PILImage = None  # type: ignore
    ImageDraw = None  # type: ignore
    _PIL_AVAILABLE = False

_SAFE_IMAGE_MAX_PIXELS = _settings.DEFAULTS["image_max_pixels"]   # default; live value via _settings.get
_SAFE_IMAGE_MAX_DIM = 8000            # API per-dimension ceiling
_SAFE_IMAGE_MAX_BYTES = 4 * 1024 * 1024  # stay under the ~5 MB per-image API limit

# Configure logging early so _safe_image_return can use logger
logging.basicConfig(level=getattr(logging, str(_settings.get("log_level", "INFO")).upper(), logging.INFO),
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")


def _encode_image(img, fmt: str) -> bytes:
    """Encode a PIL image to bytes; PNG keeps alpha, JPEG is flattened."""
    buf = io.BytesIO()
    if fmt == "jpeg":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=85, optimize=True)
    else:
        if img.mode not in ("RGB", "RGBA", "L", "LA"):
            img = img.convert("RGBA" if "A" in img.mode or img.mode == "P" else "RGB")
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _safe_image_return(data: bytes, fmt: str = "png") -> Image:
    """
    Validate and, if necessary, shrink image bytes before returning them to
    Claude's API, then wrap them in an MCP ``Image``.

    - Raises ValueError when data is empty or None.
    - The real format is read from the bytes; ``fmt`` is only a fallback when
      PIL cannot identify the image (so a PNG labelled "jpeg" is corrected).
    - Downscales proportionally (LANCZOS, alpha preserved) when the image
      exceeds _SAFE_IMAGE_MAX_PIXELS total pixels or _SAFE_IMAGE_MAX_DIM on
      either side, and keeps halving until the encoded size is under
      _SAFE_IMAGE_MAX_BYTES.
    - Without PIL the byte-size check still runs but can only warn.
    """
    if not data:
        raise ValueError("Image data is empty — Blender may not have written the file")

    if _PIL_AVAILABLE:
        try:
            img = PILImage.open(io.BytesIO(data))
            detected = (img.format or "").lower()
            if detected in ("png", "jpeg"):
                fmt = detected
            elif detected:
                fmt = "png"  # webp/bmp/etc. → re-encode as PNG
            w, h = img.size
            total = w * h
            scale = 1.0
            max_pixels = int(_settings.get("image_max_pixels", _SAFE_IMAGE_MAX_PIXELS))
            if total > max_pixels:
                scale = (max_pixels / total) ** 0.5
            if max(w, h) * scale > _SAFE_IMAGE_MAX_DIM:
                scale = _SAFE_IMAGE_MAX_DIM / max(w, h)
            needs_reencode = scale < 1.0 or detected not in ("png", "jpeg")
            if needs_reencode:
                img.load()
                if scale < 1.0:
                    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                    logger.info(f"_safe_image_return: resizing {w}×{h} → {new_w}×{new_h}")
                    img = img.resize((new_w, new_h), PILImage.LANCZOS)
                data = _encode_image(img, fmt)
            # Byte-size ceiling: halve until it fits (bounded loop).
            for _ in range(4):
                if len(data) <= _SAFE_IMAGE_MAX_BYTES:
                    break
                img.load()
                w, h = img.size
                img = img.resize((max(1, w // 2), max(1, h // 2)), PILImage.LANCZOS)
                logger.info(f"_safe_image_return: {len(data)/1048576:.1f} MB > limit, halving to {img.size}")
                data = _encode_image(img, fmt)
        except Exception as e:
            logger.warning(f"_safe_image_return: PIL check failed ({e}), returning raw data")
    elif len(data) > _SAFE_IMAGE_MAX_BYTES:
        logger.warning(
            f"_safe_image_return: image is {len(data) / 1024 / 1024:.1f} MB but "
            "PIL is not available — cannot resize; API may reject it"
        )

    return Image(data=data, format=fmt)


def _read_and_remove(path: str) -> bytes:
    """Read a temp file Blender wrote and delete it; a locked file is not fatal."""
    with open(path, "rb") as f:
        data = f.read()
    _remove_quiet(path)
    return data


def _remove_quiet(*paths: str) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError as e:
            logger.warning(f"Could not remove temp file {p}: {e}")


def _pil_to_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fit_to_box(img, box_w: int, box_h: int, fill=(0, 0, 0)):
    """Scale ``img`` to fit inside box_w×box_h keeping aspect, letterboxed."""
    img = img.convert("RGB")
    ratio = min(box_w / img.width, box_h / img.height)
    nw, nh = max(1, int(img.width * ratio)), max(1, int(img.height * ratio))
    canvas = PILImage.new("RGB", (box_w, box_h), fill)
    canvas.paste(img.resize((nw, nh), PILImage.LANCZOS), ((box_w - nw) // 2, (box_h - nh) // 2))
    return canvas


def _compose_grid(tiles, columns: int, tile_w: int, tile_h: int, labels=None, gap: int = 4,
                  fill=(20, 20, 20)):
    """Lay PIL images out on a grid (letterboxed into tile_w×tile_h cells)."""
    n = len(tiles)
    columns = max(1, min(columns, n))
    rows = (n + columns - 1) // columns
    sheet = PILImage.new("RGB", (columns * tile_w + (columns - 1) * gap,
                                 rows * tile_h + (rows - 1) * gap), fill)
    draw = ImageDraw.Draw(sheet)
    for i, tile in enumerate(tiles):
        x = (i % columns) * (tile_w + gap)
        y = (i // columns) * (tile_h + gap)
        sheet.paste(_fit_to_box(tile, tile_w, tile_h), (x, y))
        if labels and i < len(labels) and labels[i]:
            draw.rectangle([x, y, x + 8 + 7 * len(labels[i]), y + 16], fill=(0, 0, 0))
            draw.text((x + 4, y + 2), labels[i], fill=(255, 255, 255))
    return sheet


def _probe_port(host: str, port: int, timeout: float = None) -> bool:
    """True if something accepts a TCP connection on host:port.

    Probes exactly the way BlenderConnection.connect connects (an AF_INET socket
    to the same host string, with connect_timeout from the settings), so
    get_blender_status and start_blender never disagree with the real connection
    about reachability (a dual-stack "localhost" lookup used to differ).
    """
    if timeout is None:
        timeout = float(_settings.get("connect_timeout", 5.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


# Connection defaults. Live values come from settings.py: the environment
# (BLENDER_HOST / BLENDER_PORT) overrides <settings dir>/settings.json, which
# overrides these. The add-on binds 127.0.0.1, so that is the default host.
DEFAULT_HOST = _settings.DEFAULTS["host"]
DEFAULT_PORT = _settings.DEFAULTS["port"]


class BlenderCommandError(Exception):
    """The add-on executed the command and answered status="error".

    The socket is healthy, only the command failed, so the connection is kept.
    Transport failures raise a plain Exception and drop the socket so the next
    call reconnects.
    """

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    
    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(float(_settings.get("connect_timeout", 5.0)))
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # command_timeout from the settings file (default 180 s): how long one reply
        # may take. The add-on has no timeout of its own to match.
        sock.settimeout(float(_settings.get("command_timeout", 180.0)))
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)

                    # A JSON object can only be complete when the last byte is '}';
                    # skip the (O(n)) parse attempt for every intermediate chunk.
                    if not chunk.rstrip().endswith(b'}'):
                        continue
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Incomplete JSON (or a multi-byte char split across chunks), continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    _send_lock = threading.RLock()   # one command on the wire at a time, whoever calls

    def _send_request(self, payload: bytes) -> None:
        """Connect if needed and send. A transport failure here means the request never
        reached the add-on, so one reconnect-and-resend is safe (no double execution)."""
        for attempt in (1, 2):
            if not self.sock and not self.connect():
                raise ConnectionError("Not connected to Blender")
            try:
                self.sock.sendall(payload)
                return
            except (ConnectionError, BrokenPipeError, ConnectionResetError, OSError) as e:
                self.disconnect()
                if attempt == 2:
                    raise
                logger.warning(f"send failed before the add-on received the command ({e}); reconnecting once")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        with self._send_lock:
            return self._send_command_locked(command_type, params)

    def _send_command_locked(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        command = {
            "type": command_type,
            "params": params or {}
        }

        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")

            # Send the command (reconnects once if the send itself fails)
            self._send_request(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Same command_timeout as receive_full_response (settings file, default 180 s)
            self.sock.settimeout(float(_settings.get("command_timeout", 180.0)))
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise BlenderCommandError(response.get("message", "Unknown error from Blender"))
            
            return response.get("result", {})
        except BlenderCommandError:
            # The add-on answered; the socket stays valid for the next command
            raise
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
        logger.info("BlenderMCP server starting up")


        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCP",
    lifespan=server_lifespan,
    instructions=(
        f"BlenderMCP server {__version__} (protocol {PROTOCOL}). If the Blender add-on "
        "reports a different version you will be told once in a tool reply; call "
        "get_version for details."
    ),
)


# ─── One-time notices ───────────────────────────────────────────────────────────
# A notice is a line prefixed to the NEXT string tool reply, once. Queued by
# _probe_addon_version() (add-on/server mismatch) and, later, the update check.
# Popping the dict inside _attach_notice() is the once-only mechanism. No lock:
# tool bodies run on the single FastMCP event loop and _attach_notice never
# awaits, so the pop is atomic between coroutines; to_thread work never calls it.
_pending_notices: Dict[str, str] = {}
_addon_info: Dict[str, Any] | None = None   # last get_version reply, or a legacy/unreachable marker
_mismatch_notified = False


def _attach_notice(result):
    if _pending_notices and isinstance(result, str):
        text = "\n\n".join(_pending_notices[k] for k in ("update", "mismatch") if k in _pending_notices)
        _pending_notices.clear()
        return f"{text}\n---\n{result}"
    return result


_orig_tool = mcp.tool


def _tool_with_notices(*a, **k):
    """Drop-in for mcp.tool: same registration (functools.wraps keeps the signature
    FastMCP reads for the schema and Context injection) plus the notice prefix on
    str results. Must be installed before the first @mcp.tool() below."""
    deco = _orig_tool(*a, **k)

    def register(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*fa, **fk):
                return _attach_notice(await fn(*fa, **fk))
        else:
            @functools.wraps(fn)
            def wrapper(*fa, **fk):
                return _attach_notice(fn(*fa, **fk))
        return deco(wrapper)
    return register


mcp.tool = _tool_with_notices


# Process-wide state shared by every tool
_blender_connection = None
_polyhaven_enabled = False

# Managed Blender process (started via start_blender tool)
_blender_process = None

def _mismatch_text(info) -> str | None:
    """The mismatch notice for a get_version reply, or None when versions agree
    or the add-on is unreachable."""
    if not info or info.get("unreachable"):
        return None
    if info.get("legacy"):
        addon_ver, addon_proto = "pre-2.1", "none"
    else:
        addon_ver, addon_proto = str(info.get("addon", "?")), info.get("protocol", "?")
    if addon_ver == __version__ and addon_proto == PROTOCOL:
        return None
    return (f"[blender-mcp mismatch] Server {__version__} / protocol {PROTOCOL} but the Blender "
            f"add-on reports {addon_ver} / protocol {addon_proto}. Wire formats differ; deploy "
            f"addon.py and cycle the add-on before continuing.")


def _probe_addon_version(conn) -> None:
    """Ask the add-on for get_version (once per (re)connection, and from the
    get_version tool) and queue the one-time mismatch notice when the versions or
    protocol numbers differ. A pre-2.1 add-on answers 'Unknown command type'."""
    global _addon_info, _mismatch_notified
    try:
        info = conn.send_command("get_version")
        if not isinstance(info, dict):
            raise BlenderCommandError(f"unexpected get_version reply: {info!r}")
        _addon_info = dict(info)
    except BlenderCommandError as e:
        if "Unknown command type" in str(e):
            _addon_info = {"legacy": True, "reason": "pre-2.1 add-on (no get_version handler)"}
        else:
            _addon_info = {"unreachable": True, "reason": str(e)}
            return
    except Exception as e:
        _addon_info = {"unreachable": True, "reason": str(e)}
        return
    mismatch = _mismatch_text(_addon_info)
    if mismatch:
        if not _mismatch_notified:
            _pending_notices["mismatch"] = mismatch
            _mismatch_notified = True
    else:
        _mismatch_notified = False   # versions agree again: re-arm for a later regression


def get_blender_connection():
    """Get or create a persistent Blender connection.

    Liveness is not probed with an extra round-trip on every call: a dead
    socket surfaces as an exception from the real command, which resets the
    connection so the next call reconnects.
    """
    global _blender_connection, _polyhaven_enabled

    if _blender_connection is not None and _blender_connection.sock is not None:
        return _blender_connection

    host, port = _blender_host_port()
    conn = BlenderConnection(host=host, port=port)
    if not conn.connect():
        _blender_connection = None
        logger.error("Failed to connect to Blender")
        raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
    _blender_connection = conn
    logger.info("Created new persistent connection to Blender")
    _probe_addon_version(conn)
    try:
        _polyhaven_enabled = bool(conn.send_command("get_polyhaven_status").get("enabled", False))
    except Exception as e:
        logger.warning(f"Could not read PolyHaven status on connect: {e}")
    return _blender_connection


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene"""
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")

        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """
    Get detailed information about a specific object in the Blender scene.
    
    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        
        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"

@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.
    
    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 800)
    
    Returns the screenshot as an Image.
    """
    try:
        blender = get_blender_connection()
        
        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")
        
        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })
        
        if "error" in result:
            raise Exception(result["error"])
        
        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")

        return _safe_image_return(_read_and_remove(temp_path))
        
    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")


@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """
    Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.

    Parameters:
    - code: The Python code to execute
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"

@mcp.tool()
def get_polyhaven_categories(ctx: Context, asset_type: str = "hdris") -> str:
    """
    Get a list of categories for a specific asset type on Polyhaven.
    
    Parameters:
    - asset_type: The type of asset to get categories for (hdris, textures, models, all)
    """
    try:
        blender = get_blender_connection()
        if not _polyhaven_enabled:
            return "PolyHaven integration is disabled. Select it in the sidebar in BlenderMCP, then run it again."
        result = blender.send_command("get_polyhaven_categories", {"asset_type": asset_type})
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        # Format the categories in a more readable way
        categories = result["categories"]
        formatted_output = f"Categories for {asset_type}:\n\n"
        
        # Sort categories by count (descending)
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        
        for category, count in sorted_categories:
            formatted_output += f"- {category}: {count} assets\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error getting Polyhaven categories: {str(e)}")
        return f"Error getting Polyhaven categories: {str(e)}"

@mcp.tool()
def search_polyhaven_assets(
    ctx: Context,
    asset_type: str = "all",
    categories: str = None
) -> str:
    """
    Search for assets on Polyhaven with optional filtering.
    
    Parameters:
    - asset_type: Type of assets to search for (hdris, textures, models, all)
    - categories: Optional comma-separated list of categories to filter by
    
    Returns a list of matching assets with basic information.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("search_polyhaven_assets", {
            "asset_type": asset_type,
            "categories": categories
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        # Format the assets in a more readable way
        assets = result["assets"]
        total_count = result["total_count"]
        returned_count = result["returned_count"]
        
        formatted_output = f"Found {total_count} assets"
        if categories:
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"
        
        # Sort assets by download count (popularity)
        sorted_assets = sorted(assets.items(), key=lambda x: x[1].get("download_count", 0), reverse=True)
        
        for asset_id, asset_data in sorted_assets:
            formatted_output += f"- {asset_data.get('name', asset_id)} (ID: {asset_id})\n"
            formatted_output += f"  Type: {['HDRI', 'Texture', 'Model'][asset_data.get('type', 0)]}\n"
            formatted_output += f"  Categories: {', '.join(asset_data.get('categories', []))}\n"
            formatted_output += f"  Downloads: {asset_data.get('download_count', 'Unknown')}\n\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error searching Polyhaven assets: {str(e)}")
        return f"Error searching Polyhaven assets: {str(e)}"

@mcp.tool()
def download_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str = None
) -> str:
    """
    Download and import a Polyhaven asset into Blender.
    
    Parameters:
    - asset_id: The ID of the asset to download
    - asset_type: The type of asset (hdris, textures, models)
    - resolution: The resolution to download (e.g., 1k, 2k, 4k)
    - file_format: Optional file format (e.g., hdr, exr for HDRIs; jpg, png for textures; gltf, fbx for models)
    
    Returns a message indicating success or failure.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("download_polyhaven_asset", {
            "asset_id": asset_id,
            "asset_type": asset_type,
            "resolution": resolution,
            "file_format": file_format
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        if result.get("success"):
            message = result.get("message", "Asset downloaded and imported successfully")
            
            # Add additional information based on asset type
            if asset_type == "hdris":
                return f"{message}. The HDRI has been set as the world environment."
            elif asset_type == "textures":
                material_name = result.get("material", "")
                maps = ", ".join(result.get("maps", []))
                return f"{message}. Created material '{material_name}' with maps: {maps}."
            elif asset_type == "models":
                return f"{message}. The model has been imported into the current scene."
            else:
                return message
        else:
            return f"Failed to download asset: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error downloading Polyhaven asset: {str(e)}")
        return f"Error downloading Polyhaven asset: {str(e)}"

@mcp.tool()
def set_texture(
    ctx: Context,
    object_name: str,
    texture_id: str
) -> str:
    """
    Apply a previously downloaded Polyhaven texture to an object.
    
    Parameters:
    - object_name: Name of the object to apply the texture to
    - texture_id: ID of the Polyhaven texture to apply (must be downloaded first)
    
    Returns a message indicating success or failure.
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("set_texture", {
            "object_name": object_name,
            "texture_id": texture_id
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        if result.get("success"):
            material_name = result.get("material", "")
            maps = ", ".join(result.get("maps", []))
            
            # Add detailed material info
            material_info = result.get("material_info", {})
            node_count = material_info.get("node_count", 0)
            has_nodes = material_info.get("has_nodes", False)
            texture_nodes = material_info.get("texture_nodes", [])
            
            output = f"Successfully applied texture '{texture_id}' to {object_name}.\n"
            output += f"Using material '{material_name}' with maps: {maps}.\n\n"
            output += f"Material has nodes: {has_nodes}\n"
            output += f"Total node count: {node_count}\n\n"
            
            if texture_nodes:
                output += "Texture nodes:\n"
                for node in texture_nodes:
                    output += f"- {node['name']} using image: {node['image']}\n"
                    if node['connections']:
                        output += "  Connections:\n"
                        for conn in node['connections']:
                            output += f"    {conn}\n"
            else:
                output += "No texture nodes found in the material.\n"
            
            return output
        else:
            return f"Failed to apply texture: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error applying texture: {str(e)}")
        return f"Error applying texture: {str(e)}"

@mcp.tool()
def get_polyhaven_status(ctx: Context) -> str:
    """
    Check if PolyHaven integration is enabled in Blender.
    Returns a message indicating whether PolyHaven features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_polyhaven_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += "PolyHaven is good at Textures, and has a wider variety of textures than Sketchfab."
        return message
    except Exception as e:
        logger.error(f"Error checking PolyHaven status: {str(e)}")
        return f"Error checking PolyHaven status: {str(e)}"

@mcp.tool()
def get_hyper3d_status(ctx: Context) -> str:
    """
    Check if Hyper3D Rodin integration is enabled in Blender.
    Returns a message indicating whether Hyper3D Rodin features are available.

    Don't emphasize the key type in the returned message, but sliently remember it. 
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_hyper3d_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += ""
        return message
    except Exception as e:
        logger.error(f"Error checking Hyper3D status: {str(e)}")
        return f"Error checking Hyper3D status: {str(e)}"

@mcp.tool()
def get_sketchfab_status(ctx: Context) -> str:
    """
    Check if Sketchfab integration is enabled in Blender.
    Returns a message indicating whether Sketchfab features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_sketchfab_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += "Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven."        
        return message
    except Exception as e:
        logger.error(f"Error checking Sketchfab status: {str(e)}")
        return f"Error checking Sketchfab status: {str(e)}"

@mcp.tool()
def search_sketchfab_models(
    ctx: Context,
    query: str,
    categories: str = None,
    count: int = 20,
    downloadable: bool = True
) -> str:
    """
    Search for models on Sketchfab with optional filtering.

    Parameters:
    - query: Text to search for
    - categories: Optional comma-separated list of categories
    - count: Maximum number of results to return (default 20)
    - downloadable: Whether to include only downloadable models (default True)

    Returns a formatted list of matching models.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Searching Sketchfab models with query: {query}, categories: {categories}, count: {count}, downloadable: {downloadable}")
        result = blender.send_command("search_sketchfab_models", {
            "query": query,
            "categories": categories,
            "count": count,
            "downloadable": downloadable
        })
        
        if "error" in result:
            logger.error(f"Error from Sketchfab search: {result['error']}")
            return f"Error: {result['error']}"
        
        # Safely get results with fallbacks for None
        if result is None:
            logger.error("Received None result from Sketchfab search")
            return "Error: Received no response from Sketchfab search"
            
        # Format the results
        models = result.get("results", []) or []
        if not models:
            return f"No models found matching '{query}'"
            
        formatted_output = f"Found {len(models)} models matching '{query}':\n\n"
        
        for model in models:
            if model is None:
                continue
                
            model_name = model.get("name", "Unnamed model")
            model_uid = model.get("uid", "Unknown ID")
            formatted_output += f"- {model_name} (UID: {model_uid})\n"
            
            # Get user info with safety checks
            user = model.get("user") or {}
            username = user.get("username", "Unknown author") if isinstance(user, dict) else "Unknown author"
            formatted_output += f"  Author: {username}\n"
            
            # Get license info with safety checks
            license_data = model.get("license") or {}
            license_label = license_data.get("label", "Unknown") if isinstance(license_data, dict) else "Unknown"
            formatted_output += f"  License: {license_label}\n"
            
            # Add face count and downloadable status
            face_count = model.get("faceCount", "Unknown")
            is_downloadable = "Yes" if model.get("isDownloadable") else "No"
            formatted_output += f"  Face count: {face_count}\n"
            formatted_output += f"  Downloadable: {is_downloadable}\n\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error searching Sketchfab models: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error searching Sketchfab models: {str(e)}"

@mcp.tool()
def get_sketchfab_model_preview(
    ctx: Context,
    uid: str
) -> Image:
    """
    Get a preview thumbnail of a Sketchfab model by its UID.
    Use this to visually confirm a model before downloading.
    
    Parameters:
    - uid: The unique identifier of the Sketchfab model (obtained from search_sketchfab_models)
    
    Returns the model's thumbnail as an Image for visual confirmation.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Getting Sketchfab model preview for UID: {uid}")
        
        result = blender.send_command("get_sketchfab_model_preview", {"uid": uid})
        
        if result is None:
            raise Exception("Received no response from Blender")
        
        if "error" in result:
            raise Exception(result["error"])
        
        # Decode base64 image data
        image_data = base64.b64decode(result["image_data"])
        img_format = result.get("format", "jpeg")  # corrected from the bytes by _safe_image_return
        
        # Log model info
        model_name = result.get("model_name", "Unknown")
        author = result.get("author", "Unknown")
        logger.info(f"Preview retrieved for '{model_name}' by {author}")
        
        return _safe_image_return(image_data, fmt=img_format)
        
    except Exception as e:
        logger.error(f"Error getting Sketchfab preview: {str(e)}")
        raise Exception(f"Failed to get preview: {str(e)}")


@mcp.tool()
def download_sketchfab_model(
    ctx: Context,
    uid: str,
    target_size: float
) -> str:
    """
    Download and import a Sketchfab model by its UID.
    The model will be scaled so its largest dimension equals target_size.
    
    Parameters:
    - uid: The unique identifier of the Sketchfab model
    - target_size: REQUIRED. The target size in Blender units/meters for the largest dimension.
                  You must specify the desired size for the model.
                  Examples:
                  - Chair: target_size=1.0 (1 meter tall)
                  - Table: target_size=0.75 (75cm tall)
                  - Car: target_size=4.5 (4.5 meters long)
                  - Person: target_size=1.7 (1.7 meters tall)
                  - Small object (cup, phone): target_size=0.1 to 0.3
    
    Returns a message with import details including object names, dimensions, and bounding box.
    The model must be downloadable and you must have proper access rights.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Downloading Sketchfab model: {uid}, target_size={target_size}")
        
        result = blender.send_command("download_sketchfab_model", {
            "uid": uid,
            "normalize_size": True,  # Always normalize
            "target_size": target_size
        })
        
        if result is None:
            logger.error("Received None result from Sketchfab download")
            return "Error: Received no response from Sketchfab download request"
            
        if "error" in result:
            logger.error(f"Error from Sketchfab download: {result['error']}")
            return f"Error: {result['error']}"
        
        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            object_names = ", ".join(imported_objects) if imported_objects else "none"
            
            output = f"Successfully imported model.\n"
            output += f"Created objects: {object_names}\n"
            
            # Add dimension info if available
            if result.get("dimensions"):
                dims = result["dimensions"]
                output += f"Dimensions (X, Y, Z): {dims[0]:.3f} x {dims[1]:.3f} x {dims[2]:.3f} meters\n"
            
            # Add bounding box info if available
            if result.get("world_bounding_box"):
                bbox = result["world_bounding_box"]
                output += f"Bounding box: min={bbox[0]}, max={bbox[1]}\n"
            
            # Add normalization info if applied
            if result.get("normalized"):
                scale = result.get("scale_applied", 1.0)
                output += f"Size normalized: scale factor {scale:.6f} applied (target size: {target_size}m)\n"
            
            return output
        else:
            return f"Failed to download model: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error downloading Sketchfab model: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error downloading Sketchfab model: {str(e)}"

def _process_bbox(original_bbox: list[float] | list[int] | None) -> list[int] | None:
    if original_bbox is None:
        return None
    if all(isinstance(i, int) for i in original_bbox):
        return original_bbox
    if any(i<=0 for i in original_bbox):
        raise ValueError("Incorrect number range: bbox must be bigger than zero!")
    return [int(float(i) / max(original_bbox) * 100) for i in original_bbox] if original_bbox else None

@mcp.tool()
def generate_hyper3d_model_via_text(
    ctx: Context,
    text_prompt: str,
    bbox_condition: list[float]=None
) -> str:
    """
    Generate 3D asset using Hyper3D by giving description of the desired asset, and import the asset into Blender.
    The 3D asset has built-in materials.
    The generated model has a normalized size, so re-scaling after generation can be useful.

    Parameters:
    - text_prompt: A short description of the desired model in **English**.
    - bbox_condition: Optional. If given, it has to be a list of floats of length 3. Controls the ratio between [Length, Width, Height] of the model.

    Returns a message indicating success or failure.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_rodin_job", {
            "text_prompt": text_prompt,
            "images": None,
            "bbox_condition": _process_bbox(bbox_condition),
        })
        succeed = result.get("submit_time", False)
        if succeed:
            return json.dumps({
                "task_uuid": result["uuid"],
                "subscription_key": result["jobs"]["subscription_key"],
            })
        else:
            return json.dumps(result)
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
def generate_hyper3d_model_via_images(
    ctx: Context,
    input_image_paths: list[str]=None,
    input_image_urls: list[str]=None,
    bbox_condition: list[float]=None
) -> str:
    """
    Generate 3D asset using Hyper3D by giving images of the wanted asset, and import the generated asset into Blender.
    The 3D asset has built-in materials.
    The generated model has a normalized size, so re-scaling after generation can be useful.
    
    Parameters:
    - input_image_paths: The **absolute** paths of input images. Even if only one image is provided, wrap it into a list. Required if Hyper3D Rodin in MAIN_SITE mode.
    - input_image_urls: The URLs of input images. Even if only one image is provided, wrap it into a list. Required if Hyper3D Rodin in FAL_AI mode.
    - bbox_condition: Optional. If given, it has to be a list of ints of length 3. Controls the ratio between [Length, Width, Height] of the model.

    Only one of {input_image_paths, input_image_urls} should be given at a time, depending on the Hyper3D Rodin's current mode.
    Returns a message indicating success or failure.
    """
    if input_image_paths is not None and input_image_urls is not None:
        return f"Error: Conflict parameters given!"
    if input_image_paths is None and input_image_urls is None:
        return f"Error: No image given!"
    if input_image_paths is not None:
        if not all(os.path.exists(i) for i in input_image_paths):
            return "Error: not all image paths are valid!"
        images = []
        for path in input_image_paths:
            with open(path, "rb") as f:
                images.append(
                    (Path(path).suffix, base64.b64encode(f.read()).decode("ascii"))
                )
    elif input_image_urls is not None:
        if not all(urlparse(i) for i in input_image_paths):
            return "Error: not all image URLs are valid!"
        images = input_image_urls.copy()
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_rodin_job", {
            "text_prompt": None,
            "images": images,
            "bbox_condition": _process_bbox(bbox_condition),
        })
        succeed = result.get("submit_time", False)
        if succeed:
            return json.dumps({
                "task_uuid": result["uuid"],
                "subscription_key": result["jobs"]["subscription_key"],
            })
        else:
            return json.dumps(result)
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
def poll_rodin_job_status(
    ctx: Context,
    subscription_key: str=None,
    request_id: str=None,
):
    """
    Check if the Hyper3D Rodin generation task is completed.

    For Hyper3D Rodin mode MAIN_SITE:
        Parameters:
        - subscription_key: The subscription_key given in the generate model step.

        Returns a list of status. The task is done if all status are "Done".
        If "Failed" showed up, the generating process failed.
        This is a polling API, so only proceed if the status are finally determined ("Done" or "Canceled").

    For Hyper3D Rodin mode FAL_AI:
        Parameters:
        - request_id: The request_id given in the generate model step.

        Returns the generation task status. The task is done if status is "COMPLETED".
        The task is in progress if status is "IN_PROGRESS".
        If status other than "COMPLETED", "IN_PROGRESS", "IN_QUEUE" showed up, the generating process might be failed.
        This is a polling API, so only proceed if the status are finally determined ("COMPLETED" or some failed state).
    """
    try:
        blender = get_blender_connection()
        kwargs = {}
        if subscription_key:
            kwargs = {
                "subscription_key": subscription_key,
            }
        elif request_id:
            kwargs = {
                "request_id": request_id,
            }
        result = blender.send_command("poll_rodin_job_status", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
def import_generated_asset(
    ctx: Context,
    name: str,
    task_uuid: str=None,
    request_id: str=None,
):
    """
    Import the asset generated by Hyper3D Rodin after the generation task is completed.

    Parameters:
    - name: The name of the object in scene
    - task_uuid: For Hyper3D Rodin mode MAIN_SITE: The task_uuid given in the generate model step.
    - request_id: For Hyper3D Rodin mode FAL_AI: The request_id given in the generate model step.

    Only give one of {task_uuid, request_id} based on the Hyper3D Rodin Mode!
    Return if the asset has been imported successfully.
    """
    try:
        blender = get_blender_connection()
        kwargs = {
            "name": name
        }
        if task_uuid:
            kwargs["task_uuid"] = task_uuid
        elif request_id:
            kwargs["request_id"] = request_id
        result = blender.send_command("import_generated_asset", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hyper3D task: {str(e)}")
        return f"Error generating Hyper3D task: {str(e)}"

@mcp.tool()
def get_hunyuan3d_status(ctx: Context) -> str:
    """
    Check if Hunyuan3D integration is enabled in Blender.
    Returns a message indicating whether Hunyuan3D features are available.

    Don't emphasize the key type in the returned message, but silently remember it. 
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_hunyuan3d_status")
        message = result.get("message", "")
        return message
    except Exception as e:
        logger.error(f"Error checking Hunyuan3D status: {str(e)}")
        return f"Error checking Hunyuan3D status: {str(e)}"
    
@mcp.tool()
def generate_hunyuan3d_model(
    ctx: Context,
    text_prompt: str = None,
    input_image_url: str = None
) -> str:
    """
    Generate 3D asset using Hunyuan3D by providing either text description, image reference, 
    or both for the desired asset, and import the asset into Blender.
    The 3D asset has built-in materials.
    
    Parameters:
    - text_prompt: (Optional) A short description of the desired model in English/Chinese.
    - input_image_url: (Optional) The local or remote url of the input image. Accepts None if only using text prompt.

    Returns: 
    - When successful, returns a JSON with job_id (format: "job_xxx") indicating the task is in progress
    - When the job completes, the status will change to "DONE" indicating the model has been imported
    - Returns error message if the operation fails
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_hunyuan_job", {
            "text_prompt": text_prompt,
            "image": input_image_url,
        })
        if "JobId" in result.get("Response", {}):
            job_id = result["Response"]["JobId"]
            formatted_job_id = f"job_{job_id}"
            return json.dumps({
                "job_id": formatted_job_id,
            })
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {str(e)}")
        return f"Error generating Hunyuan3D task: {str(e)}"
    
@mcp.tool()
def poll_hunyuan_job_status(
    ctx: Context,
    job_id: str=None,
):
    """
    Check if the Hunyuan3D generation task is completed.

    For Hunyuan3D:
        Parameters:
        - job_id: The job_id given in the generate model step.

        Returns the generation task status. The task is done if status is "DONE".
        The task is in progress if status is "RUN".
        If status is "DONE", returns ResultFile3Ds, which is the generated ZIP model path
        When the status is "DONE", the response includes a field named ResultFile3Ds that contains the generated ZIP file path of the 3D model in OBJ format.
        This is a polling API, so only proceed if the status are finally determined ("DONE" or some failed state).
    """
    try:
        blender = get_blender_connection()
        kwargs = {
            "job_id": job_id,
        }
        result = blender.send_command("poll_hunyuan_job_status", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {str(e)}")
        return f"Error generating Hunyuan3D task: {str(e)}"

@mcp.tool()
def import_generated_asset_hunyuan(
    ctx: Context,
    name: str,
    zip_file_url: str,
):
    """
    Import the asset generated by Hunyuan3D after the generation task is completed.

    Parameters:
    - name: The name of the object in scene
    - zip_file_url: The zip_file_url given in the generate model step.

    Return if the asset has been imported successfully.
    """
    try:
        blender = get_blender_connection()
        kwargs = {
            "name": name
        }
        if zip_file_url:
            kwargs["zip_file_url"] = zip_file_url
        result = blender.send_command("import_generated_asset_hunyuan", kwargs)
        return result
    except Exception as e:
        logger.error(f"Error generating Hunyuan3D task: {str(e)}")
        return f"Error generating Hunyuan3D task: {str(e)}"


# ─── Blender process management ──────────────────────────────────────────────



_BLENDER_VER_RE = _re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _blender_version_key(path: str) -> tuple:
    """(major, minor, patch) parsed from the folder holding a Blender exe.

    'Blender 4.3' -> (4, 3, 0); 'blender-5.2.1-windows-x64' -> (5, 2, 1);
    no version in the folder name -> (0, 0, 0). Numeric, so 'Blender 10.0'
    outranks 'Blender 4.3' (a plain string sort gets that wrong).
    """
    folder = os.path.basename(os.path.dirname(path))
    m = _BLENDER_VER_RE.search(folder)
    if not m:
        return (0, 0, 0)
    return tuple(int(g or 0) for g in m.groups())


def _fixed_drive_roots() -> list[str]:
    """Roots of the fixed (non-removable, non-network) drives on Windows,
    e.g. ['C:\\', 'D:\\']; empty on other platforms. Never raises."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mask = kernel32.GetLogicalDrives()
        roots = []
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                    roots.append(root)
        return roots
    except Exception as e:
        logger.debug(f"_fixed_drive_roots: falling back to a static list ({e})")
        return [f"{c}:\\" for c in "CDEFGH" if os.path.isdir(f"{c}:\\")]


def _find_blender_exe(hint: str = None) -> str | None:
    """Locate the Blender executable. Resolution only: never spawns a process.

    Order:
      (a) explicit ``hint`` (the ``blender_exe`` tool argument), if it is a file
      (b) the ``BLENDER_EXE`` environment variable, if it is a file
      (c) ``blender_exe`` in the settings file (set_server_settings), if it is a file
      (d) ``blender`` / ``blender.exe`` on PATH
      (e) one candidate pool of installed copies, highest version wins:
          Program Files "Blender *" installs plus PORTABLE folders named
          ``blender-<ver>-windows-x64`` on D:, under the user's home and on the
          root of every fixed drive (the zip layout from blender.org)
      (f) the Steam install
      (g) macOS .app bundles
    """
    # (a) + (b) + (c): explicit hint, BLENDER_EXE env var, settings-file blender_exe
    for candidate in filter(None, [hint, os.environ.get("BLENDER_EXE"), _settings.get("blender_exe")]):
        if os.path.isfile(candidate):
            return candidate

    # (d) PATH
    found = _shutil.which("blender") or _shutil.which("blender.exe")
    if found:
        return found

    # (e) Candidate pool: Program Files installs + portable zip folders
    home = os.path.expanduser("~")
    patterns = [
        r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files\Blender Foundation\blender.exe",
        r"D:\blender-*-windows-x64\blender.exe",
        os.path.join(home, "blender-*-windows-x64", "blender.exe"),
    ]
    patterns += [os.path.join(root, "blender-*-windows-x64", "blender.exe")
                 for root in _fixed_drive_roots()]
    seen = set()
    candidates = []
    for pat in patterns:
        for match in _glob.glob(pat):
            key = os.path.normcase(os.path.abspath(match))
            if key not in seen and os.path.isfile(match):
                seen.add(key)
                candidates.append(match)
    if candidates:
        # highest parsed version wins; the path string only breaks exact ties
        return max(candidates, key=lambda p: (_blender_version_key(p), os.path.normcase(p)))

    # (f) Steam (Windows)
    steam = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
    if os.path.isfile(steam):
        return steam

    # (g) macOS .app bundle
    mac_paths = [
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/Applications/Blender/blender.app/Contents/MacOS/Blender",
    ]
    for p in mac_paths:
        if os.path.isfile(p):
            return p

    return None


def _default_output_path(filename: str) -> str | None:
    """<settings output_dir>/<filename> when output_dir is set (folder created),
    else None so the caller keeps its previous default. Only for files the user
    gets to keep (C25): image-returning tools that delete their temp PNG are exempt."""
    out_dir = _settings.get("output_dir")
    if not out_dir:
        return None
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        logger.warning(f"output_dir {out_dir!r} unusable ({e}); falling back to the tool default")
        return None
    return os.path.join(out_dir, filename)


def _blender_host_port() -> tuple[str, int]:
    """Effective host/port: BLENDER_HOST/BLENDER_PORT > settings.json > defaults."""
    return str(_settings.get("host", DEFAULT_HOST)), int(_settings.get("port", DEFAULT_PORT))


def _drop_connection() -> None:
    """Forget the persistent connection so the next tool call reconnects."""
    global _blender_connection
    if _blender_connection:
        try:
            _blender_connection.disconnect()
        except Exception:
            pass
    _blender_connection = None


async def _wait_for_exit(proc, timeout: float) -> bool:
    """Poll a Popen without blocking the event loop. True if it exited."""
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        await asyncio.sleep(0.2)
    return proc.poll() is not None


# --python-expr passed by start_blender(start_server=True). The add-on publishes
# blendermcp_ensure_server in bpy.app.driver_namespace at register(); calling it
# starts the socket server if it is not running. One shot: returning None from a
# bpy.app.timers callback unregisters it.
_ENSURE_SERVER_EXPR = (
    "import bpy; "
    "bpy.app.timers.register("
    "lambda: (bpy.app.driver_namespace.get('blendermcp_ensure_server', lambda: None)(), None)[1], "
    "first_interval=1.0)"
)
# Background mode: timers never fire, so call the hook directly (no-op when the
# add-on is not enabled, so a --factory-startup run does not error out).
_ENSURE_SERVER_EXPR_BACKGROUND = (
    "import bpy; bpy.app.driver_namespace.get('blendermcp_ensure_server', lambda: None)()"
)


@mcp.tool()
async def start_blender(
    ctx: Context,
    blend_file: str = None,
    blender_exe: str = None,
    background: bool = False,
    wait_for_addon: bool = True,
    python_expr: str = None,
    start_server: bool = True,
    restore_session: str = None,
) -> str:
    """
    Launch Blender as a managed subprocess.

    Parameters:
    - blend_file: Path to a .blend file to open on startup (optional)
    - blender_exe: Full path to the Blender executable. Auto-detected if omitted:
                   the BLENDER_EXE environment variable, then the settings file
                   (blender_exe), then PATH, then the highest-version copy among
                   Program Files installs and portable zip folders
                   (blender-<ver>-windows-x64 on D:, in the user's home or on any
                   fixed drive root), then Steam. get_blender_status shows the pick.
    - background: True = headless mode (--background), no UI. Useful for rendering.
    - wait_for_addon: Wait up to 30 s for the BlenderMCP addon socket to become
                      reachable (default True). Set False for background jobs that
                      don't use the addon.
    - python_expr: Optional Python expression passed to Blender via --python-expr,
                   e.g. "import bpy; bpy.ops.wm.quit_blender()" for scripted batch runs.
    - start_server: True (default) also asks the add-on to start its socket server
                    through a --python-expr hook: 1 s after startup in GUI mode (in
                    case the autostart preference is off), immediately in background
                    mode (autostart never opens a port headless; only this does).
    - restore_session: Name of a session saved with save_session_state (e.g. "last")
                       to reopen and re-apply once the add-on answers.

    Blender console output goes to blender_mcp_blender.log in the temp folder; it
    must never share this process stdio, which carries the MCP transport.
    """
    global _blender_process

    if _blender_process is not None and _blender_process.poll() is None:
        return f"Blender is already running (pid {_blender_process.pid}). Call close_blender() first."

    exe = _find_blender_exe(blender_exe)
    if not exe:
        return (
            "Could not find the Blender executable. "
            "Set the BLENDER_EXE environment variable to the full path, put blender_exe "
            "in the settings file (set_server_settings), or pass blender_exe='/path/to/blender'."
        )

    cmd = [exe]
    if background:
        cmd.append("--background")
    if blend_file:
        if not os.path.exists(blend_file):
            return f"Blend file not found: {blend_file}"
        cmd.append(blend_file)
    if python_expr:
        cmd += ["--python-expr", python_expr]
    if start_server:
        # Belt and braces for an add-on whose autostart preference is off: the
        # add-on registers bpy.app.driver_namespace["blendermcp_ensure_server"].
        # GUI: a timer calls it once the UI is up (timers never fire headless).
        # Background: call it directly; the add-on's autostart never opens a port
        # in --background, only this explicit call does. A missing hook (add-on
        # not enabled) is a no-op; the timeout message below names that cause.
        cmd += ["--python-expr", _ENSURE_SERVER_EXPR_BACKGROUND if background else _ENSURE_SERVER_EXPR]

    log_path = os.path.join(tempfile.gettempdir(), "blender_mcp_blender.log")
    try:
        with open(log_path, "ab") as log_fh:
            _blender_process = _subprocess.Popen(
                cmd,
                stdin=_subprocess.DEVNULL,
                stdout=log_fh,
                stderr=_subprocess.STDOUT,
            )
        logger.info(f"Blender started: pid={_blender_process.pid}  cmd={cmd}  log={log_path}")
    except Exception as e:
        return f"Failed to launch Blender: {e}"

    # Reset any stale connection so get_blender_connection() reconnects fresh
    _drop_connection()

    if not wait_for_addon or background:
        return (
            f"Blender launched (pid {_blender_process.pid})."
            + ((" Background mode: socket server requested through the ensure_server hook, "
                "not waiting for it; check get_blender_status." if start_server else
                " Waiting for addon skipped (background mode).") if background else
               " Not waiting for addon (wait_for_addon=False).")
        )

    # Poll the add-on port until its TCP server is up
    host, port = _blender_host_port()
    deadline = _time.monotonic() + 30.0
    while _time.monotonic() < deadline:
        if _blender_process.poll() is not None:
            return (f"Blender exited unexpectedly (code {_blender_process.returncode}). "
                    f"See {log_path}")
        if _probe_port(host, port, 0.5):
            msg = (
                f"Blender started (pid {_blender_process.pid}) and addon is ready on {host}:{port}."
                + (f" Opened: {blend_file}" if blend_file else "")
            )
            if restore_session:
                restored = await asyncio.to_thread(restore_session_state, ctx, restore_session, True, True)
                msg += f" Session restore: {restored}"
            return msg
        await asyncio.sleep(0.5)

    return (
        f"Blender launched (pid {_blender_process.pid}) but the MCP addon did not respond "
        f"on {host}:{port} within 30 s. Two causes are possible: (1) the BlenderMCP add-on is "
        f"not enabled in Edit > Preferences > Add-ons, or (2) its 'Autostart server' preference "
        f"is off and nobody clicked 'Connect to Claude' in the N sidebar"
        + ("" if start_server else " (start_server=False, so no automatic start was attempted)")
        + f". Blender log: {log_path}"
    )

@mcp.tool()
async def close_blender(
    ctx: Context,
    force: bool = False,
    save: bool = False,
) -> str:
    """
    Close Blender. Works for an instance started with start_blender() and, via the
    addon socket, for one the user launched by hand.

    Parameters:
    - force: If True, kills the managed process immediately instead of asking
             Blender to quit gracefully via its Python API (default False).
    - save: If True, save the current .blend (if it has a path) before quitting.
            Unsaved changes are otherwise discarded without a prompt.
    """
    global _blender_process

    pid = _blender_process.pid if _blender_process else None
    host, port = _blender_host_port()
    graceful = False

    # Graceful path: ask Blender to quit via the addon socket (connect if needed)
    if not force and _probe_port(host, port, 0.5):
        try:
            conn = get_blender_connection()
            conn.send_command("quit_blender", {"save_prompt": False, "save": save})
            graceful = True
        except Exception as e:
            # A dropped socket here usually means Blender is already shutting down
            logger.info(f"quit_blender via socket: {e}")
            graceful = "Connection to Blender lost" in str(e) or "Communication error" in str(e)
        if graceful:
            if _blender_process is not None:
                await _wait_for_exit(_blender_process, 8.0)
            else:
                deadline = _time.monotonic() + 8.0
                while _time.monotonic() < deadline and _probe_port(host, port, 0.3):
                    await asyncio.sleep(0.3)

    _drop_connection()

    # Ensure a managed process is gone
    if _blender_process is not None:
        if _blender_process.poll() is None:
            logger.warning("Blender did not quit gracefully; terminating")
            _blender_process.terminate()
            if not await _wait_for_exit(_blender_process, 5.0):
                _blender_process.kill()
            graceful = False
        _blender_process = None

    if graceful:
        how = "gracefully"
    elif pid or force:
        how = "process terminated"
    else:
        how = "no running instance found"
    return f"Blender closed ({how})." + (f" (pid was {pid})" if pid else "")


@mcp.tool()
def get_blender_status(ctx: Context) -> str:
    """
    Report whether Blender is running, and whether the MCP addon is reachable.
    """
    proc_status = "not started via MCP"
    if _blender_process is not None:
        code = _blender_process.poll()
        if code is None:
            proc_status = f"running (pid {_blender_process.pid})"
        else:
            proc_status = f"exited (code {code})"

    host, port = _blender_host_port()
    if _probe_port(host, port, 1.0):
        addon_status = f"reachable on {host}:{port}"
        try:
            get_blender_connection().send_command("get_polyhaven_status")
            addon_status += " (addon responding)"
        except Exception as e:
            addon_status += f" (port open but addon not responding: {e})"
    else:
        addon_status = f"not reachable on {host}:{port}"

    exe = _find_blender_exe()
    exe_status = exe if exe else "not found (set BLENDER_EXE or pass blender_exe to start_blender)"

    return f"Process: {proc_status}\nAddon socket: {addon_status}\nBlender exe: {exe_status}"


# ─── Version & server settings ────────────────────────────────────────────────

@mcp.tool()
def get_version(ctx: Context) -> str:
    """
    Report the server version, the Blender add-on version (asked over the socket),
    both protocol numbers with protocol_match, where this server module and its
    settings file live, and the last update-check result. Never touches the
    network; the add-on line reads "unreachable" when Blender is not running.
    """
    global _addon_info
    lines = [f"BlenderMCP server {__version__} (protocol {PROTOCOL})",
             f"Server module: {os.path.abspath(__file__)}",
             f"Settings file: {_settings.settings_path()} ({_settings.file_state()})"]
    try:
        _probe_addon_version(get_blender_connection())
    except Exception as e:
        _addon_info = {"unreachable": True, "reason": str(e)}
    info = _addon_info or {"unreachable": True, "reason": "no connection attempted"}
    if info.get("unreachable"):
        lines.append(f"Blender add-on: unreachable ({info.get('reason')})")
        lines.append("protocol_match: unknown")
    elif info.get("legacy"):
        lines.append("Blender add-on: pre-2.1 (no get_version handler), protocol none")
        lines.append("protocol_match: false")
    else:
        lines.append(f"Blender add-on: {info.get('addon')} (protocol {info.get('protocol')}) on Blender "
                     f"{info.get('blender')}, Python {info.get('python')}"
                     + (f", file {info['addon_file']}" if info.get("addon_file") else ""))
        lines.append(f"protocol_match: {'true' if info.get('protocol') == PROTOCOL else 'false'}")
    mismatch = _mismatch_text(info)
    if info.get("unreachable"):
        lines.append("Compatibility: unknown (add-on unreachable)")
    elif info.get("legacy"):
        lines.append(f"Compatibility: MISMATCH. {mismatch}")
    elif mismatch:
        lines.append(f"Compatibility: MISMATCH. {mismatch}")
    else:
        lines.append("Compatibility: OK (versions and protocols match)")
    lines.append("last_update_check: null")
    return "\n".join(lines)


@mcp.tool()
def get_server_settings(ctx: Context) -> str:
    """
    Show the Python server's own settings (not Blender's) with the source of each
    value: env, file or default.

    Keys: host, port, connect_timeout, command_timeout, blender_exe, output_dir,
    presets_dir, image_max_pixels, log_level, img_to_3d_port. File:
    <settings dir>/settings.json where the settings dir is ~/.blender_mcp or
    BLENDER_MCP_SETTINGS_DIR. Environment variables BLENDER_HOST, BLENDER_PORT,
    BLENDER_EXE and IMG_TO_3D_PORT override the file.
    """
    lines = [f"Settings file: {_settings.settings_path()} ({_settings.file_state()})",
             f"Presets dir: {_settings.presets_dir()}"]
    for key, row in _settings.describe().items():
        env = f", env {row['env']}" if row["env"] else ""
        lines.append(f"{key} = {row['value']!r}  [{row['source']}{env}]")
    return "\n".join(lines)


@mcp.tool()
def set_server_settings(ctx: Context, values: str) -> str:
    """
    Change the Python server's own settings and persist them to settings.json.

    Parameters:
    - values: JSON object of key -> value, e.g. '{"port": 9877, "output_dir": "D:/renders"}'.
      Keys: host, port, connect_timeout, command_timeout, blender_exe, output_dir,
      presets_dir, image_max_pixels, log_level, img_to_3d_port. A null value resets
      a key to its default. Unknown keys and wrong types are reported as rejected.
      host and port take effect on the next connection (the current one is dropped);
      the other keys apply immediately. A key set by an environment variable keeps
      the environment value until that variable is unset (reported as overridden).
    """
    try:
        vals = json.loads(values) if values else {}
        if not isinstance(vals, dict):
            return "Error: values must be a JSON object, e.g. '{\"port\": 9877}'"
        before = _blender_host_port()
        r = _settings.update(vals)
        if _blender_host_port() != before:
            _drop_connection()
        if "log_level" in r["saved"]:
            logging.getLogger().setLevel(getattr(logging, str(_settings.get("log_level")).upper(), logging.INFO))
        msg = f"Saved {r['saved']} to {r['path']}"
        if r["rejected"]:
            msg += f". Rejected: {r['rejected']}"
        if r["overridden_by_env"]:
            msg += f". Still overridden by the environment: {r['overridden_by_env']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


# ─── Extended tools ──────────────────────────────────────────────────────────

# Subprocess management for the local image-to-3D server

_img_to_3d_process = None


def _img_to_3d_port() -> int:
    """IMG_TO_3D_PORT env > settings.json img_to_3d_port > 7862."""
    return int(_settings.get("img_to_3d_port", 7862))


def _img_to_3d_url() -> str:
    return f"http://127.0.0.1:{_img_to_3d_port()}"


# ─── Multi-angle capture ─────────────────────────────────────────────────────

_VALID_ANGLES = ("front", "back", "left", "right", "top", "bottom",
                 "iso_front_right", "iso_front_left")


_VALID_OVERLAYS = ("bones_in_front", "wireframe", "weight_paint")


def _check_overlay(overlay):
    if overlay is not None and overlay not in _VALID_OVERLAYS:
        raise Exception(f"Unknown overlay '{overlay}'. Valid overlays: {', '.join(_VALID_OVERLAYS)}")


def _temp_png(tag: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"blender_{tag}_{os.getpid()}.png")


@mcp.tool()
def capture_viewport_angle(
    ctx: Context,
    angle: str = "front",
    max_size: int = 800,
    overlay: str = None,
) -> Image:
    """
    Capture the Blender 3D viewport from a named angle and return it as an image.

    Parameters:
    - angle: View direction. One of: front, back, left, right, top, bottom,
             iso_front_right, iso_front_left
    - max_size: Maximum pixel dimension (default 800)
    - overlay: Optional rig view: bones_in_front (armatures drawn through meshes),
               wireframe, weight_paint (active vertex group as a heat map).
               All viewport state is restored afterwards.
    """
    try:
        if angle not in _VALID_ANGLES:
            raise Exception(f"Unknown angle '{angle}'. Valid angles: {', '.join(_VALID_ANGLES)}")
        _check_overlay(overlay)
        blender = get_blender_connection()
        temp_path = _temp_png(f"angle_{angle}")
        _remove_quiet(temp_path)
        result = blender.send_command("capture_viewport_angle", {
            "angle": angle,
            "max_size": max_size,
            "filepath": temp_path,
            "overlay": overlay,
        })
        if "error" in result:
            raise Exception(result["error"])
        return _safe_image_return(_read_and_remove(temp_path))
    except Exception as e:
        logger.error(f"capture_viewport_angle error: {e}")
        raise Exception(f"capture_viewport_angle failed: {e}") from e


@mcp.tool()
def capture_contact_sheet(
    ctx: Context,
    angles: str = "front,right,top,iso_front_right",
    max_size: int = 512,
    overlay: str = None,
) -> Image:
    """
    Capture multiple viewport angles and stitch them into a single contact sheet image.

    Parameters:
    - angles: Comma-separated list of angle names (default: front,right,top,iso_front_right)
    - max_size: Pixel size for each individual tile (default 512)
    - overlay: Optional rig view for every tile: bones_in_front, wireframe or
               weight_paint (see capture_viewport_angle). State restored afterwards.

    Returns a single composited image with all requested angles labelled.
    """
    try:
        angle_list = [a.strip() for a in angles.split(",") if a.strip()]
        bad = [a for a in angle_list if a not in _VALID_ANGLES]
        if bad:
            raise Exception(f"Unknown angle(s) {bad}. Valid angles: {', '.join(_VALID_ANGLES)}")
        if not angle_list:
            raise Exception("No angles given")
        _check_overlay(overlay)

        blender = get_blender_connection()
        result = blender.send_command("capture_contact_sheet", {
            "angles": angle_list,
            "max_size": max_size,
            "overlay": overlay,
        })
        if "error" in result:
            raise Exception(result["error"])

        images_info = result.get("images", {})
        paths = [(a, images_info.get(a, {}).get("filepath")) for a in angle_list]
        paths = [(a, fp) for a, fp in paths if fp and os.path.exists(fp)]
        if not paths:
            raise Exception("No images were captured")

        try:
            if not _PIL_AVAILABLE:
                # Fallback: just return the first captured image
                with open(paths[0][1], "rb") as f:
                    return _safe_image_return(f.read())

            tiles, labels = [], []
            for a, fp in paths:
                with PILImage.open(fp) as im:
                    im.load()
                    tiles.append(im.convert("RGB"))
                labels.append(a)
            sheet = _compose_grid(tiles, columns=4, tile_w=max_size, tile_h=max_size, labels=labels)
            return _safe_image_return(_pil_to_png_bytes(sheet))
        finally:
            _remove_quiet(*[fp for _, fp in paths])
    except Exception as e:
        logger.error(f"capture_contact_sheet error: {e}")
        raise Exception(f"capture_contact_sheet failed: {e}") from e


# ─── Depth map ───────────────────────────────────────────────────────────────

@mcp.tool()
def render_depth_map(
    ctx: Context,
    max_depth: float = 10.0,
) -> Image:
    """
    Render a normalised depth map from the active camera using the Blender compositor
    Z-pass. Closer objects appear lighter. Rendered in a throw-away scene copy, so the
    current scene's compositor and render settings are untouched.

    Parameters:
    - max_depth: Depth value (scene units) mapped to black (default 10.0)
    """
    try:
        blender = get_blender_connection()
        temp_path = _temp_png("depth")
        _remove_quiet(temp_path)
        result = blender.send_command("render_depth_map", {
            "filepath": temp_path,
            "max_depth": max_depth,
        })
        if "error" in result:
            raise Exception(result["error"])
        return _safe_image_return(_read_and_remove(temp_path))
    except Exception as e:
        logger.error(f"render_depth_map error: {e}")
        raise Exception(f"render_depth_map failed: {e}") from e


# ─── Reference image ─────────────────────────────────────────────────────────

# Server-side cache; the addon keeps the authoritative copy so a server restart
# (client reconnect) does not lose references stored earlier in the session.
_reference_registry: Dict[str, str] = {}


def _resolve_reference(blender, name: str) -> str:
    path = _reference_registry.get(name)
    if path is None:
        r = blender.send_command("get_reference_image", {"name": name})
        path = r.get("filepath")
        if path:
            _reference_registry[name] = path
    if not path:
        raise Exception(f"Reference '{name}' not found. Call store_reference_image first.")
    if not os.path.exists(path):
        raise Exception(f"Reference '{name}' points to a missing file: {path}")
    return path


@mcp.tool()
def store_reference_image(ctx: Context, name: str, filepath: str) -> str:
    """
    Store a local image file as a named reference for later comparison tools.

    Parameters:
    - name: Short identifier (e.g. "concept_art")
    - filepath: Absolute path to the image file on disk
    """
    try:
        if not os.path.exists(filepath):
            return f"Error: file not found: {filepath}"
        blender = get_blender_connection()
        result = blender.send_command("store_reference_image", {"name": name, "filepath": filepath})
        if "error" in result:
            return f"Error: {result['error']}"
        _reference_registry[name] = filepath
        return f"Stored reference '{name}' from {filepath}. All refs: {result.get('stored_refs', [])}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def compare_reference_image(
    ctx: Context,
    reference_name: str,
    angle: str = "front",
    max_size: int = 512,
) -> Image:
    """
    Capture the current viewport from a named angle and composite it side-by-side
    with a previously stored reference image. Both tiles keep their aspect ratio.

    Parameters:
    - reference_name: Name given to store_reference_image earlier
    - angle: Viewport angle to capture for comparison
    - max_size: Tile size for each image in the composite
    """
    try:
        if angle not in _VALID_ANGLES:
            raise Exception(f"Unknown angle '{angle}'. Valid angles: {', '.join(_VALID_ANGLES)}")
        blender = get_blender_connection()
        ref_path = _resolve_reference(blender, reference_name)

        temp_render = _temp_png("cmp_render")
        _remove_quiet(temp_render)
        r = blender.send_command("capture_viewport_angle", {
            "angle": angle,
            "max_size": max_size,
            "filepath": temp_render,
        })
        if "error" in r:
            raise Exception(r["error"])

        if not _PIL_AVAILABLE:
            return _safe_image_return(_read_and_remove(temp_render))

        try:
            with PILImage.open(temp_render) as im:
                im.load()
                render_img = im.convert("RGB")
        finally:
            _remove_quiet(temp_render)
        with PILImage.open(ref_path) as im:
            im.load()
            ref_img = im.convert("RGB")

        bar = 24
        sheet = _compose_grid([ref_img, render_img], columns=2, tile_w=max_size, tile_h=max_size, gap=8)
        out = PILImage.new("RGB", (sheet.width, sheet.height + bar), (30, 30, 30))
        out.paste(sheet, (0, bar))
        draw = ImageDraw.Draw(out)
        draw.text((4, 4), f"Reference: {reference_name}", fill=(200, 200, 255))
        draw.text((max_size + 12, 4), f"Current: {angle}", fill=(255, 200, 100))
        return _safe_image_return(_pil_to_png_bytes(out))
    except Exception as e:
        logger.error(f"compare_reference_image error: {e}")
        raise Exception(f"compare_reference_image failed: {e}") from e


@mcp.tool()
def diff_images(
    ctx: Context,
    image_path_a: str,
    image_path_b: str,
    threshold: int = 15,
    tile_size: int = 512,
) -> Image:
    """
    Compare two images and produce a 3-panel composite: [Image A] | [Image B] | [Diff].
    The diff panel desaturates the base image and paints changed regions in bright red,
    making differences immediately obvious. Image B is scaled to A's size for the
    pixel diff; panels keep A's aspect ratio.

    Parameters:
    - image_path_a: Path to the first image (treated as the reference/baseline)
    - image_path_b: Path to the second image (treated as the new/changed version)
    - threshold: Pixel difference (0-255) below which changes are ignored (default 15, filters noise)
    - tile_size: Longest side of each panel in the composite (default 512)
    """
    try:
        if not _PIL_AVAILABLE:
            raise Exception("Pillow is required for diff_images — install it with: pip install pillow")

        for p in (image_path_a, image_path_b):
            if not os.path.exists(p):
                raise Exception(f"File not found: {p}")

        import numpy as np
        from PIL import ImageChops, ImageEnhance, ImageFilter

        with PILImage.open(image_path_a) as im:
            im.load()
            img_a = im.convert("RGB")
        with PILImage.open(image_path_b) as im:
            im.load()
            img_b = im.convert("RGB")

        # Work at panel resolution, preserving A's aspect ratio
        ratio = tile_size / max(img_a.size)
        tw, th = max(1, int(img_a.width * ratio)), max(1, int(img_a.height * ratio))
        img_a = img_a.resize((tw, th), PILImage.LANCZOS)
        img_b = img_b.resize((tw, th), PILImage.LANCZOS)

        # --- Build diff mask ---
        diff_arr = np.array(ImageChops.difference(img_a, img_b).convert("L"), dtype=np.float32)
        amplified = np.clip(diff_arr * 6, 0, 255).astype(np.uint8)
        mask_arr = np.where(amplified > threshold, 255, 0).astype(np.uint8)
        mask_img = PILImage.fromarray(mask_arr, "L").filter(ImageFilter.GaussianBlur(radius=2))

        # --- Diff panel: desaturated base + red overlay ---
        base_desat = ImageEnhance.Color(img_a).enhance(0.15)
        red_overlay = PILImage.new("RGB", (tw, th), (255, 30, 30))
        diff_panel = PILImage.composite(red_overlay, base_desat, mask_img)
        changed_pct = (mask_arr > 0).sum() / (tw * th) * 100

        # --- 3-panel composite ---
        gap, bar = 6, 28
        sheet = PILImage.new("RGB", (tw * 3 + gap * 2, th + bar), (20, 20, 20))
        sheet.paste(img_a, (0, bar))
        sheet.paste(img_b, (tw + gap, bar))
        sheet.paste(diff_panel, (tw * 2 + gap * 2, bar))

        draw = ImageDraw.Draw(sheet)
        draw.text((max(0, tw // 2 - 30), 6), "Image A", fill=(200, 200, 200))
        draw.text((tw + gap + max(0, tw // 2 - 30), 6), "Image B", fill=(200, 200, 200))
        draw.text((tw * 2 + gap * 2 + max(0, tw // 2 - 50), 6),
                  f"Diff  ({changed_pct:.1f}% changed)", fill=(255, 100, 100))

        return _safe_image_return(_pil_to_png_bytes(sheet))
    except Exception as e:
        logger.error(f"diff_images error: {e}")
        raise Exception(f"diff_images failed: {e}") from e


# ─── Mesh editing ────────────────────────────────────────────────────────────

@mcp.tool()
def move_object(ctx: Context, name: str, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> str:
    """Move an object to an absolute world-space position."""
    try:
        blender = get_blender_connection()
        result = blender.send_command("move_object", {"name": name, "x": x, "y": y, "z": z})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Moved '{name}' to ({x}, {y}, {z})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def scale_object(ctx: Context, name: str, x: float = 1.0, y: float = 1.0, z: float = 1.0) -> str:
    """Set the absolute scale of an object on each axis."""
    try:
        blender = get_blender_connection()
        result = blender.send_command("scale_object", {"name": name, "x": x, "y": y, "z": z})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Scaled '{name}' to ({x}, {y}, {z})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def rotate_object(
    ctx: Context,
    name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> str:
    """
    Set the Euler rotation of an object (degrees).

    Parameters:
    - name: Object name
    - x, y, z: Rotation in degrees around each axis
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("rotate_object", {"name": name, "x": x, "y": y, "z": z})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Rotated '{name}' to ({x}°, {y}°, {z}°)"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_object_material_color(
    ctx: Context,
    name: str,
    r: float = 1.0,
    g: float = 1.0,
    b: float = 1.0,
    a: float = 1.0,
    material_index: int = 0,
) -> str:
    """
    Set the Principled BSDF base colour of an object's material.
    Creates the material if one does not exist.

    Parameters:
    - name: Object name
    - r, g, b, a: Colour channels in 0..1 range
    - material_index: Which material slot to update (default 0)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_object_material_color", {
            "name": name, "r": r, "g": g, "b": b, "a": a, "material_index": material_index
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Set material color of '{name}' to rgba({r},{g},{b},{a})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_vertex_positions(
    ctx: Context,
    name: str,
    indices: str = None,
    world_space: bool = True,
    max_verts: int = 2000,
) -> str:
    """
    Read vertex positions from a mesh object.

    Parameters:
    - name: Mesh object name
    - indices: Comma-separated vertex indices to retrieve (returns all if omitted)
    - world_space: True = world coordinates (default), False = local/object coordinates
    - max_verts: Safety cap when retrieving all vertices (default 2000)

    Returns JSON with each vertex's index and [x, y, z] position.
    """
    try:
        blender = get_blender_connection()
        idx_list = [int(i.strip()) for i in indices.split(",")] if indices else None
        result = blender.send_command("get_vertex_positions", {
            "name": name, "indices": idx_list,
            "world_space": world_space, "max_verts": max_verts,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_vertex_position(
    ctx: Context,
    name: str,
    vertex_index: int,
    x: float,
    y: float,
    z: float,
) -> str:
    """
    Move a single vertex of a mesh object to a world-space position.

    Parameters:
    - name: Mesh object name
    - vertex_index: Zero-based vertex index
    - x, y, z: Target world-space position
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_vertex_position", {
            "name": name, "vertex_index": vertex_index, "x": x, "y": y, "z": z
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Moved vertex {vertex_index} of '{name}' to ({x}, {y}, {z})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_vertex_positions(
    ctx: Context,
    name: str,
    vertices: str,
    world_space: bool = True,
) -> str:
    """
    Batch-update multiple vertex positions in a single call (much faster than
    calling set_vertex_position repeatedly for many vertices).

    Parameters:
    - name: Mesh object name
    - vertices: JSON array of {"index": int, "co": [x, y, z]} objects.
                Example: '[{"index":0,"co":[0,0,1]},{"index":3,"co":[1,0,0]}]'
    - world_space: True = co values are world-space (default), False = local/object space
    """
    try:
        blender = get_blender_connection()
        vert_list = json.loads(vertices)
        result = blender.send_command("set_vertex_positions", {
            "name": name, "vertices": vert_list, "world_space": world_space,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Updated {result['updated_count']} vertices on '{name}'"
        if result.get("errors"):
            msg += f". Errors: {result['errors']}"
        return msg
    except json.JSONDecodeError as e:
        return f"Error: 'vertices' must be valid JSON — {e}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_control_points(
    ctx: Context,
    name: str,
    spline_index: int = 0,
) -> str:
    """
    Read the control points of a curve (BEZIER, POLY, or NURBS) object.

    For BEZIER curves returns: co, handle_left, handle_right, handle types.
    For POLY/NURBS curves returns: co (and weight for NURBS).

    Parameters:
    - name: Curve object name
    - spline_index: Which spline within the curve (default 0)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_control_points", {
            "name": name, "spline_index": spline_index,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_control_point(
    ctx: Context,
    name: str,
    point_index: int,
    co: str,
    handle_left: str = None,
    handle_right: str = None,
    handle_left_type: str = None,
    handle_right_type: str = None,
    spline_index: int = 0,
) -> str:
    """
    Move a curve control point and optionally adjust its bezier handles.

    Parameters:
    - name: Curve object name
    - point_index: Zero-based control point index
    - co: Comma-separated x,y,z world-space position
    - handle_left: Comma-separated x,y,z for left handle (bezier only)
    - handle_right: Comma-separated x,y,z for right handle (bezier only)
    - handle_left_type: FREE, ALIGNED, VECTOR, or AUTO (bezier only)
    - handle_right_type: FREE, ALIGNED, VECTOR, or AUTO (bezier only)
    - spline_index: Spline index within the curve object (default 0)
    """
    try:
        blender = get_blender_connection()

        def parse_vec(s):
            return [float(v) for v in s.split(",")] if s else None

        result = blender.send_command("set_control_point", {
            "name": name,
            "point_index": point_index,
            "co": parse_vec(co),
            "handle_left": parse_vec(handle_left),
            "handle_right": parse_vec(handle_right),
            "handle_left_type": handle_left_type,
            "handle_right_type": handle_right_type,
            "spline_index": spline_index,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Control point {point_index} on '{name}' (spline {spline_index}) moved to {co}"
    except Exception as e:
        return f"Error: {e}"


# ─── Edge operations ─────────────────────────────────────────────────────────

@mcp.tool()
def get_edges(
    ctx: Context,
    name: str,
    indices: str = None,
    max_edges: int = 5000,
) -> str:
    """
    Read edge data from a mesh: vertex pair, sharp flag, seam flag, crease, and bevel weight.

    Parameters:
    - name: Mesh object name
    - indices: Comma-separated edge indices (returns all if omitted)
    - max_edges: Safety cap when retrieving all edges (default 5000)
    """
    try:
        blender = get_blender_connection()
        idx_list = [int(i.strip()) for i in indices.split(",")] if indices else None
        result = blender.send_command("get_edges", {
            "name": name, "indices": idx_list, "max_edges": max_edges,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def mark_sharp_edges(
    ctx: Context,
    name: str,
    edge_indices: str,
    sharp: bool = True,
) -> str:
    """
    Mark edges as sharp (hard) or soft, controlling auto-smooth and the Edge Split modifier.

    Sharp edges appear as hard creases when smooth shading + auto-smooth is enabled.
    Soft (unsharp) edges blend smoothly with neighbouring faces.

    Parameters:
    - name: Mesh object name
    - edge_indices: Comma-separated edge indices, or "all"
    - sharp: True = hard edge (default), False = soft/smooth edge
    """
    try:
        blender = get_blender_connection()
        idx = edge_indices if edge_indices.strip().lower() == "all" \
              else [int(i.strip()) for i in edge_indices.split(",")]
        result = blender.send_command("mark_sharp_edges", {
            "name": name, "edge_indices": idx, "sharp": sharp,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        label = "sharp (hard)" if sharp else "soft (smooth)"
        return f"Marked {result['marked_edges']} edges as {label} on '{name}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_edge_crease(
    ctx: Context,
    name: str,
    edge_indices: str,
    crease: float,
) -> str:
    """
    Set subdivision crease weight on edges.

    Crease controls how the Subdivision Surface modifier handles edge sharpness:
    0.0 = fully smooth (no crease), 1.0 = perfectly sharp crease.
    Values in between give progressively harder edges without going fully sharp.

    Parameters:
    - name: Mesh object name
    - edge_indices: Comma-separated edge indices, or "all"
    - crease: Weight 0.0–1.0
    """
    try:
        blender = get_blender_connection()
        idx = edge_indices if edge_indices.strip().lower() == "all" \
              else [int(i.strip()) for i in edge_indices.split(",")]
        result = blender.send_command("set_edge_crease", {
            "name": name, "edge_indices": idx, "crease": crease,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Set crease={result['crease']} on {result['updated_edges']} edges of '{name}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_edge_bevel_weight(
    ctx: Context,
    name: str,
    edge_indices: str,
    weight: float,
) -> str:
    """
    Set bevel weight on edges, used with the Bevel modifier (limit_method=WEIGHT).

    Only edges with weight > 0 will be bevelled when the modifier uses WEIGHT mode.
    This lets you selectively bevel specific edges without affecting the whole mesh.

    Typical workflow:
      1. set_edge_bevel_weight("Cube", "4,5,6,7", weight=1.0)   ← top edges only
      2. add_modifier("Cube", "BEVEL", params='{"width": 0.05, "limit_method": "WEIGHT"}')

    Parameters:
    - name: Mesh object name
    - edge_indices: Comma-separated edge indices, or "all"
    - weight: 0.0 (no bevel) to 1.0 (full bevel)
    """
    try:
        blender = get_blender_connection()
        idx = edge_indices if edge_indices.strip().lower() == "all" \
              else [int(i.strip()) for i in edge_indices.split(",")]
        result = blender.send_command("set_edge_bevel_weight", {
            "name": name, "edge_indices": idx, "weight": weight,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Set bevel_weight={result['bevel_weight']} on {result['updated_edges']} edges of '{name}'"
    except Exception as e:
        return f"Error: {e}"


# ─── Face operations ─────────────────────────────────────────────────────────

@mcp.tool()
def get_faces(
    ctx: Context,
    name: str,
    indices: str = None,
    world_space: bool = True,
    max_faces: int = 2000,
) -> str:
    """
    Read face data from a mesh object.

    Parameters:
    - name: Mesh object name
    - indices: Comma-separated face indices to retrieve (returns all if omitted)
    - world_space: True = world coordinates for normals and centers (default)
    - max_faces: Safety cap when retrieving all faces (default 2000)

    Returns JSON with each face's vertex_indices, normal, center, material_index, and area.
    """
    try:
        blender = get_blender_connection()
        idx_list = [int(i.strip()) for i in indices.split(",")] if indices else None
        result = blender.send_command("get_faces", {
            "name": name, "indices": idx_list,
            "world_space": world_space, "max_faces": max_faces,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_face_material_index(
    ctx: Context,
    name: str,
    face_indices: str,
    material_index: int,
) -> str:
    """
    Assign a material slot to specific faces (for multi-material objects).

    Parameters:
    - name: Mesh object name
    - face_indices: Comma-separated face indices, or "all" for every face
    - material_index: Material slot number (0-based; material must already be in the object's slots)
    """
    try:
        blender = get_blender_connection()
        idx = face_indices if face_indices.strip().lower() == "all" \
              else [int(i.strip()) for i in face_indices.split(",")]
        result = blender.send_command("set_face_material_index", {
            "name": name, "face_indices": idx, "material_index": material_index,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Assigned material slot {material_index} to "
                f"{result['updated_faces']} faces on '{name}'")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def extrude_faces(
    ctx: Context,
    name: str,
    face_indices: str,
    amount: float = 0.2,
) -> str:
    """
    Extrude faces outward along their individual normals.

    Parameters:
    - name: Mesh object name
    - face_indices: Comma-separated face indices to extrude
    - amount: Extrusion distance in Blender units (negative = inward, default 0.2)
    """
    try:
        blender = get_blender_connection()
        idx_list = [int(i.strip()) for i in face_indices.split(",")]
        result = blender.send_command("extrude_faces", {
            "name": name, "face_indices": idx_list, "amount": amount,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Extruded {result['extruded_faces']} faces on '{name}' "
                f"by {amount} units")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def inset_faces(
    ctx: Context,
    name: str,
    face_indices: str,
    thickness: float = 0.1,
    depth: float = 0.0,
    use_individual: bool = True,
) -> str:
    """
    Inset faces, creating a border ring of new polygons inside each face.

    Parameters:
    - name: Mesh object name
    - face_indices: Comma-separated face indices to inset
    - thickness: Inset distance from face edges (default 0.1)
    - depth: Push inset faces along their normals — 0 = flat, positive = raised
    - use_individual: Inset each face independently (default True)
    """
    try:
        blender = get_blender_connection()
        idx_list = [int(i.strip()) for i in face_indices.split(",")]
        result = blender.send_command("inset_faces", {
            "name": name, "face_indices": idx_list,
            "thickness": thickness, "depth": depth,
            "use_individual": use_individual,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Inset {result['inset_faces']} faces on '{name}' "
                f"(thickness={thickness}, depth={depth})")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def flip_normals(
    ctx: Context,
    name: str,
    face_indices: str = None,
) -> str:
    """
    Flip face normals on a mesh (reverses which side is the outside).

    Parameters:
    - name: Mesh object name
    - face_indices: Comma-separated face indices (flips ALL faces if omitted)
    """
    try:
        blender = get_blender_connection()
        idx_list = [int(i.strip()) for i in face_indices.split(",")] \
                   if face_indices else None
        result = blender.send_command("flip_normals", {
            "name": name, "face_indices": idx_list,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Flipped {result['flipped_faces']} normals on '{name}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def merge_vertices(
    ctx: Context,
    name: str,
    distance: float = 0.001,
) -> str:
    """
    Merge (weld) vertices that are within a distance threshold of each other.
    Equivalent to 'Merge by Distance' in Blender — useful for cleaning up
    imported meshes or fixing seams after boolean operations.

    Parameters:
    - name: Mesh object name
    - distance: Maximum distance between vertices to merge (default 0.001)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("merge_vertices", {
            "name": name, "distance": distance,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Merged {result['removed']} vertices on '{name}' "
                f"({result['vertices_before']} → {result['vertices_after']})")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def triangulate_mesh(
    ctx: Context,
    name: str,
    method: str = "BEAUTY",
) -> str:
    """
    Triangulate all faces of a mesh (convert quads/ngons to triangles).
    Useful before export to game engines or 3D printing.

    Parameters:
    - name: Mesh object name
    - method: BEAUTY (best quality, default), FIXED, FIXED_ALTERNATE,
              SHORTEST_DIAGONAL
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("triangulate_mesh", {
            "name": name, "method": method,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Triangulated '{name}': {result['triangles']} triangles"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def subdivide_mesh(
    ctx: Context,
    name: str,
    cuts: int = 1,
    smoothness: float = 0.0,
) -> str:
    """
    Subdivide all faces of a mesh (equivalent to Subdivide in Edit Mode).

    Parameters:
    - name: Mesh object name
    - cuts: Number of cuts per edge (default 1)
    - smoothness: Smooth factor 0..1 (default 0.0 = flat)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("subdivide_mesh", {
            "name": name, "cuts": cuts, "smoothness": smoothness
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Subdivided '{name}' with {cuts} cuts → "
                f"{result['vertices']} verts, {result['faces']} faces")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def apply_modifier(ctx: Context, name: str, modifier_name: str) -> str:
    """
    Apply a named modifier on a mesh object, collapsing it into the mesh data.

    Parameters:
    - name: Object name
    - modifier_name: Exact modifier name as shown in Blender's Properties panel
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("apply_modifier", {
            "name": name, "modifier_name": modifier_name
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Applied modifier '{modifier_name}' on '{name}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_mesh_stats(ctx: Context, name: str) -> str:
    """
    Return detailed topology statistics for a mesh object.

    Parameters:
    - name: Mesh object name
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_mesh_stats", {"name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


# ─── Camera management ───────────────────────────────────────────────────────

@mcp.tool()
def create_camera(
    ctx: Context,
    name: str = "Camera",
    location: str = "0,-5,3",
    look_at: str = "0,0,0",
    lens: float = 50.0,
    cam_type: str = "PERSP",
) -> str:
    """
    Add a new camera to the Blender scene.

    Parameters:
    - name: Name for the camera object
    - location: Comma-separated x,y,z position (default "0,-5,3")
    - look_at: Comma-separated x,y,z target point (default "0,0,0")
    - lens: Focal length in mm (default 50.0)
    - cam_type: PERSP or ORTHO (default PERSP)
    """
    try:
        blender = get_blender_connection()
        loc = [float(v) for v in location.split(",")]
        lat = [float(v) for v in look_at.split(",")]
        result = blender.send_command("create_camera", {
            "name": name, "location": loc, "look_at": lat,
            "lens": lens, "cam_type": cam_type,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Created camera '{result['name']}' at {result['location']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_active_camera(ctx: Context, name: str) -> str:
    """
    Set the active render camera to an existing camera object.

    Parameters:
    - name: Camera object name
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_active_camera", {"name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Active camera set to '{name}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def render_from_camera(
    ctx: Context,
    camera_name: str = None,
    width: int = 1920,
    height: int = 1080,
    samples: int = 32,
) -> Image:
    """
    Render a still from the specified (or active) camera.

    Parameters:
    - camera_name: Camera to render from (uses scene active camera if omitted)
    - width: Render width in pixels (default 1920)
    - height: Render height in pixels (default 1080)
    - samples: Sample count for Cycles and EEVEE (default 32)
    """
    try:
        blender = get_blender_connection()
        temp_path = _temp_png("render")
        _remove_quiet(temp_path)
        result = blender.send_command("render_from_camera", {
            "camera_name": camera_name,
            "filepath": temp_path,
            "width": width,
            "height": height,
            "samples": samples,
        })
        if "error" in result:
            raise Exception(result["error"])
        if not os.path.exists(temp_path):
            raise Exception("Render produced no file (render cancelled?)")
        return _safe_image_return(_read_and_remove(temp_path))
    except Exception as e:
        logger.error(f"render_from_camera error: {e}")
        raise Exception(f"render_from_camera failed: {e}") from e


@mcp.tool()
def render_all_cameras(
    ctx: Context,
    width: int = 1920,
    height: int = 1080,
    samples: int = 32,
    output_dir: str = None,
) -> Image:
    """
    Render a still from every camera in the scene and return a contact sheet with
    all results labelled by camera name.

    Parameters:
    - width: Render width per camera in pixels (default 1920)
    - height: Render height per camera in pixels (default 1080)
    - samples: Sample count for Cycles and EEVEE (default 32)
    - output_dir: Directory to keep the individual full-resolution renders.
                  If omitted, the server setting output_dir is used; if that is
                  unset too they are rendered to the temp dir and deleted after
                  the sheet is built.

    Returns a composited contact sheet image.
    """
    try:
        output_dir = output_dir or _settings.get("output_dir") or None
        blender = get_blender_connection()
        result = blender.send_command("render_all_cameras", {
            "width": width, "height": height,
            "samples": samples, "output_dir": output_dir,
        })
        if "error" in result:
            raise Exception(result["error"])

        renders = result.get("renders", [])
        successful = [r for r in renders if r.get("success") and
                      r.get("filepath") and os.path.exists(r["filepath"])]
        failed = [f"{r.get('camera')}: {r.get('error', 'no output')}"
                  for r in renders if r not in successful]

        if not successful:
            raise Exception(
                f"No renders succeeded. "
                f"Cameras found: {result.get('total_cameras', 0)}. "
                f"Details: {failed}"
            )
        if failed:
            logger.warning(f"render_all_cameras: some cameras failed: {failed}")

        try:
            if not _PIL_AVAILABLE or len(successful) == 1:
                with open(successful[0]["filepath"], "rb") as f:
                    return _safe_image_return(f.read())

            # Tile size follows the render aspect ratio
            tile_w = 960
            tile_h = max(1, int(tile_w * height / max(1, width)))
            tiles, labels = [], []
            for r in successful:
                with PILImage.open(r["filepath"]) as im:
                    im.load()
                    tiles.append(im.convert("RGB"))
                labels.append(r["camera"])
            sheet = _compose_grid(tiles, columns=3, tile_w=tile_w, tile_h=tile_h, labels=labels)
            logger.info(f"render_all_cameras: {len(successful)}/{result.get('total_cameras')} cameras rendered")
            return _safe_image_return(_pil_to_png_bytes(sheet))
        finally:
            if not output_dir:
                _remove_quiet(*[r["filepath"] for r in successful])

    except Exception as e:
        logger.error(f"render_all_cameras error: {e}")
        raise Exception(f"render_all_cameras failed: {e}") from e


# ─── Scene analysis ──────────────────────────────────────────────────────────

@mcp.tool()
def find_objects_by_type(ctx: Context, obj_type: str = "MESH") -> str:
    """
    List all objects in the scene that match the given type.

    Parameters:
    - obj_type: Blender object type: MESH, CURVE, CAMERA, LIGHT, EMPTY, ARMATURE, etc.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("find_objects_by_type", {"obj_type": obj_type})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def measure_distance(ctx: Context, name_a: str, name_b: str) -> str:
    """
    Measure the Euclidean distance between the origins of two objects.

    Parameters:
    - name_a: First object name
    - name_b: Second object name
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("measure_distance", {"name_a": name_a, "name_b": name_b})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Distance from '{name_a}' to '{name_b}': {result['distance']} units"
    except Exception as e:
        return f"Error: {e}"


# ─── Lighting ────────────────────────────────────────────────────────────────

@mcp.tool()
def add_light(
    ctx: Context,
    light_type: str = "POINT",
    name: str = None,
    location: str = "0,0,5",
    energy: float = 1000.0,
    color: str = "1,1,1",
    radius: float = 0.1,
) -> str:
    """
    Add a light to the Blender scene.

    Parameters:
    - light_type: POINT, SUN, SPOT, or AREA
    - name: Name for the light object (optional)
    - location: Comma-separated x,y,z (default "0,0,5")
    - energy: Light power in watts (default 1000)
    - color: Comma-separated r,g,b in 0..1 range (default "1,1,1")
    - radius: Shadow soft radius (default 0.1)
    """
    try:
        blender = get_blender_connection()
        loc = [float(v) for v in location.split(",")]
        col = [float(v) for v in color.split(",")]
        result = blender.send_command("add_light", {
            "light_type": light_type, "name": name,
            "location": loc, "energy": energy, "color": col, "radius": radius,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Added {light_type} light '{result['name']}' at {loc} with energy {energy}W"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_world_background(
    ctx: Context,
    color: str = "0.05,0.05,0.05",
    strength: float = 1.0,
    hdri_path: str = None,
) -> str:
    """
    Set the scene world background to a solid colour or an HDRI environment map.

    Parameters:
    - color: Comma-separated r,g,b in 0..1 range (used when hdri_path is not given)
    - strength: Background emission strength (default 1.0)
    - hdri_path: Absolute path to a .hdr or .exr file (overrides color)
    """
    try:
        blender = get_blender_connection()
        col = [float(v) for v in color.split(",")]
        result = blender.send_command("set_world_background", {
            "color": col, "strength": strength, "hdri_path": hdri_path,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        if result.get("mode") == "hdri":
            return f"World background set to HDRI: {hdri_path}"
        return f"World background set to color rgb({col[0]},{col[1]},{col[2]}) strength {strength}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def add_3point_lighting(
    ctx: Context,
    subject_name: str = None,
    key_energy: float = 1500.0,
    fill_energy: float = 500.0,
    back_energy: float = 800.0,
) -> str:
    """
    Add a classic 3-point lighting rig (key, fill, back/rim) centred on a subject.

    Parameters:
    - subject_name: Object to light (uses scene origin if omitted)
    - key_energy: Key light power in watts (default 1500)
    - fill_energy: Fill light power in watts (default 500)
    - back_energy: Back/rim light power in watts (default 800)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("add_3point_lighting", {
            "subject_name": subject_name,
            "key_energy": key_energy,
            "fill_energy": fill_energy,
            "back_energy": back_energy,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Added 3-point lighting: {result['lights']}"
    except Exception as e:
        return f"Error: {e}"


# ─── Export / import ─────────────────────────────────────────────────────────

@mcp.tool()
def export_object(
    ctx: Context,
    name: str = None,
    filepath: str = None,
    file_format: str = "glb",
    include_hierarchy: bool = True,
    bake_anim: bool = True,
    add_leaf_bones: bool = False,
    use_armature_deform_only: bool = True,
    bake_anim_simplify_factor: float = 0.0,
    mesh_smooth_type: str = None,
    primary_bone_axis: str = None,
    secondary_bone_axis: str = None,
    apply_scale_options: str = None,
    export_animations: bool = True,
    export_skins: bool = True,
    export_morph: bool = True,
) -> str:
    """
    Export an object (or the full scene) to a 3D file. A skinned mesh exports WITH
    its armature, children and animation by default (include_hierarchy).

    Parameters:
    - name: Object to export; exports entire scene if omitted
    - filepath: Output file path. If omitted: <server output_dir>/<name or scene>.<format>
                when the output_dir setting is set, else a temp-dir path.
    - file_format: glb, gltf, fbx, obj, stl, ply (default glb)
    - include_hierarchy: Also select the parent armature and all children (default True)
    FBX only (ignored for other formats, reported):
    - bake_anim: Bake animation into the FBX (default True)
    - add_leaf_bones: Add end bones for bone tails (default False, engines do not want them)
    - use_armature_deform_only: Export only deforming bones (default True)
    - bake_anim_simplify_factor: Keyframe simplification, 0.0 = keep all (default 0.0)
    - mesh_smooth_type: OFF, FACE, EDGE (Blender 5.2 adds SMOOTH_GROUP); validated
                        against this Blender's own list
    - primary_bone_axis / secondary_bone_axis: X, Y, Z, -X, -Y, -Z (Unreal: Y / X)
    - apply_scale_options: FBX_SCALE_NONE, FBX_SCALE_UNITS, FBX_SCALE_CUSTOM, FBX_SCALE_ALL
    glTF/GLB only (ignored for other formats, reported):
    - export_animations / export_skins / export_morph: Include animations, skinning,
      shape keys (all default True)
    """
    try:
        if not filepath:
            filepath = _default_output_path(f"{name or 'scene'}.{(file_format or 'glb').lower()}")
        blender = get_blender_connection()
        result = blender.send_command("export_object", {
            "name": name, "filepath": filepath, "file_format": file_format,
            "include_hierarchy": include_hierarchy,
            "bake_anim": bake_anim, "add_leaf_bones": add_leaf_bones,
            "use_armature_deform_only": use_armature_deform_only,
            "bake_anim_simplify_factor": bake_anim_simplify_factor,
            "mesh_smooth_type": mesh_smooth_type,
            "primary_bone_axis": primary_bone_axis, "secondary_bone_axis": secondary_bone_axis,
            "apply_scale_options": apply_scale_options,
            "export_animations": export_animations, "export_skins": export_skins,
            "export_morph": export_morph,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Exported to: {result.get('filepath')} ({result.get('format', file_format)})"
        ex = result.get("exported_objects")
        if isinstance(ex, list):
            msg += f" {len(ex)} object(s): {', '.join(ex[:8])}{', ...' if len(ex) > 8 else ''}"
        elif ex:
            msg += f" ({ex})"
        opts = result.get("fbx_options") or result.get("gltf_options")
        if isinstance(opts, dict) and opts:
            msg += ". Options: " + ", ".join(f"{k}={v}" for k, v in list(opts.items())[:10])
        ignored = result.get("ignored") or result.get("ignored_params")
        if ignored:
            msg += f". Ignored for {result.get('format', file_format)}: {ignored}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def import_file(ctx: Context, filepath: str) -> str:
    """
    Import a 3D file into the current Blender scene.
    Supports: .glb, .gltf, .fbx, .obj, .stl, .ply, .blend, .bvh (needs the
    io_anim_bvh add-on; the error names enable_addon when it is off)

    Parameters:
    - filepath: Absolute path to the file to import

    Reply lists the new objects and, for rigged files, the armatures, the new
    actions and any bone-shape helper objects the importer added.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("import_file", {"filepath": filepath})
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Imported {filepath}. New objects: {result.get('imported_objects', [])}"
        if result.get("armatures"):
            msg += f". Armatures: {result['armatures']}"
        if result.get("new_actions"):
            msg += f". New actions: {result['new_actions']}"
        if result.get("bone_shape_objects"):
            msg += f". Bone-shape helper objects (not your geometry): {result['bone_shape_objects']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_blend(
    ctx: Context,
    filepath: str = None,
    compress: bool = None,
    relative_remap: bool = True,
    copy: bool = False,
    incremental: bool = False,
    backup: bool = True,
    overwrite: bool = True,
    purge_orphans: bool = False,
) -> str:
    """
    Save the current Blender project as a .blend file.

    Parameters:
    - filepath: Absolute path to save to (e.g. "C:/projects/my_scene.blend").
                If omitted, saves over the currently open file. If the file has
                never been saved it goes to <server output_dir>/untitled_<stamp>.blend
                when that setting is set, else to a temporary path (the reply warns).
    - compress: True/False to force compression; omit (None) to use the Blender
                preference use_file_compression (on by default since Blender 5.0).
    - relative_remap: Remap relative paths when the file moves (default True).
    - copy: True = save a COPY without changing the working file path (default False).
    - incremental: True = Blender's own numbered save beside the open file
                   (a.blend -> a1.blend, then a2.blend); do not combine with filepath.
                   Default False.
    - backup: True (default) keeps Blender's .blend1 backups per the save_version
              preference; the reply reports that preference value.
    - overwrite: False refuses when the target exists (default True).
    - purge_orphans: True purges orphan data-blocks before saving (default False).

    Reply: path, bytes written, effective compress, is_dirty after, elapsed seconds.
    """
    try:
        blender = get_blender_connection()
        payload = {
            "compress": compress, "relative_remap": relative_remap,
            "copy": copy, "incremental": incremental, "backup": backup,
            "overwrite": overwrite, "purge_orphans": purge_orphans,
        }
        routed = None   # why an unsaved file went where it went (Ada, C25 condition)
        if not filepath and not incremental and _settings.get("output_dir"):
            # A never-saved file would go to the temp dir; with output_dir set it
            # goes there instead (C25). One cheap read; a pre-2.1 add-on answers
            # "Unknown command type" and we keep the old behaviour.
            try:
                state = blender.send_command("get_file_state")
                if isinstance(state, dict) and not state.get("is_saved", True):
                    filepath = _default_output_path(f"untitled_{_time.strftime('%Y%m%d_%H%M%S')}.blend")
                    if filepath:
                        routed = f"file had never been saved; placed under the server output_dir setting ({_settings.get('output_dir')})"
                    else:
                        routed = "file had never been saved; output_dir is unusable, so Blender used a temp path"
            except Exception as e:
                logger.info(f"save_blend: file-state pre-check skipped ({e})")
                routed = f"file-state pre-check unavailable ({e}); Blender chose the path"
        if filepath:
            payload["filepath"] = filepath   # only when given: incremental saves name the file themselves
        result = blender.send_command("save_blend", payload)
        if "error" in result:
            return f"Error: {result['error']}"
        path = result.get("path", result.get("filepath", filepath))
        msg = f"Saved: {path}"
        details = []
        if "bytes" in result:
            details.append(f"{result['bytes']} bytes")
        if "compress" in result:
            details.append(f"compress={result['compress']}")
        if "is_dirty" in result:
            details.append(f"dirty after={result['is_dirty']}")
        if "elapsed" in result:
            details.append(f"{float(result['elapsed']):.2f}s")
        if result.get("purged") is not None:
            details.append(f"purged {result['purged']} orphan(s)")
        if copy:
            details.append("copy, working file unchanged")
        if "save_versions" in result:
            details.append(f"backups kept={result['save_versions']}")
        if details:
            msg += " (" + ", ".join(details) + ")"
        if result.get("warning"):
            msg += f". Warning: {result['warning']}"
        if routed:
            msg += f". Note: {routed}"
        elif not filepath and result.get("warning") and "temp" in str(result["warning"]).lower():
            msg += (". Note: no filepath given and the file had never been saved, so Blender used a temp path"
                    + ("" if _settings.get("output_dir") else "; set the server output_dir setting to choose a folder"))
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def load_blend(
    ctx: Context,
    filepath: str,
    force: bool = False,
    save_first: bool = False,
    load_ui: bool = None,
    use_scripts: bool = None,
    revert_on_fail: bool = True,
) -> str:
    """
    Open a .blend file, replacing the current Blender scene.

    Parameters:
    - filepath: Absolute path to the .blend file to open
    - force: The current file has unsaved changes -> the call is refused unless
             force=True (discard them) or save_first=True (save, then open).
    - save_first: Save the current file before opening the new one (default False).
    - load_ui: Load the file's UI layout; omit (None) for the preference use_load_ui.
    - use_scripts: Allow the file's scripts to run; omit (None) for the preference.
    - revert_on_fail: Reopen the previous file if loading fails (default True).

    Reply: scene name and object count, the previous file, the Blender version that
    saved the file, and whether unsaved changes were discarded. Files saved by
    Blender 5.x do not open in 4.3.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("load_blend", {
            "filepath": filepath, "force": force, "save_first": save_first,
            "load_ui": load_ui, "use_scripts": use_scripts, "revert_on_fail": revert_on_fail,
        })
        if "error" in result:
            return (f"Error: {result['error']}"
                    + (f" (reverted to {result['reverted_to']})" if result.get("reverted_to") else ""))
        msg = (f"Opened '{filepath}'. Scene: {result.get('scene_name')}, "
               f"{result.get('object_count')} objects.")
        if result.get("blender_version_of_file"):
            v = result["blender_version_of_file"]
            msg += f" Saved by Blender {'.'.join(str(x) for x in v) if isinstance(v, (list, tuple)) else v}."
        if result.get("previous_file"):
            msg += f" Previous file: {result['previous_file']}."
        if result.get("unsaved_changes_discarded"):
            msg += " Unsaved changes were discarded."
        return msg
    except Exception as e:
        return f"Error: {e}"


# ─── Shared helpers for the B0 wrappers ───────────────────────────────────────

_SAFE_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")   # preset / session file names


def _fmt_version(v) -> str:
    return ".".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)


def _parse_json_object(text: str, label: str) -> dict | str:
    """Parse a JSON object argument; returns the dict or an 'Error: ...' string."""
    if text is None or str(text).strip() == "":
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return f"Error: {label} is not valid JSON ({e.msg} at position {e.pos})"
    if not isinstance(obj, dict):
        return f"Error: {label} must be a JSON object, e.g. '{{\"key\": value}}'"
    return obj


def _split_csv(text: str) -> list[str]:
    return [p.strip() for p in str(text).split(",") if p.strip()] if text else []


# ─── Session state ────────────────────────────────────────────────────────────
# The add-on captures/applies the state dict; the server persists it as
# <settings dir>/sessions/<name>.json (C10: the server writes only under ~/.blender_mcp).

_SESSION_INCLUDE_DEFAULT = "FILE,FRAME,CAMERA,SELECTION,ACTIVE,MODE,VIEWPORT,WORKSPACE,SETTINGS_SNAPSHOTS"


def _session_path(name: str):
    if not _SAFE_NAME_RE.match(name or ""):
        raise ValueError("session name must be 1-64 characters of letters, digits, '_', '.' or '-'")
    return _settings.settings_dir() / "sessions" / f"{name}.json"


def _read_session(name: str) -> dict:
    path = _session_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"no saved session '{name}' ({path})")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@mcp.tool()
def save_session_state(ctx: Context, name: str = "last", include: str = _SESSION_INCLUDE_DEFAULT) -> str:
    """
    Save what you are working on so it survives a Blender restart: open file
    path and dirty flag, frame range and current frame, active camera, selection
    and active object, mode, each 3D view's view matrix/distance/shading,
    workspace and the named settings snapshots. Stored as
    <settings dir>/sessions/<name>.json. Pairs with close_blender /
    start_blender(restore_session=...).

    Parameters:
    - name: Session name (default "last")
    - include: Comma list of FILE, FRAME, CAMERA, SELECTION, ACTIVE, MODE, VIEWPORT,
               WORKSPACE, SETTINGS_SNAPSHOTS (default: all)
    """
    try:
        path = _session_path(name)
        blender = get_blender_connection()
        result = blender.send_command("save_session_state", {"name": name, "include": _split_csv(include)})
        if "error" in result:
            return f"Error: {result['error']}"
        state = result.get("state") or {}
        doc = {"name": name, "saved_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
               "server_version": __version__, "state": state}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        f = state.get("file") or {}
        fr = state.get("frame") or {}
        return (f"Session '{name}' saved to {path}: file={f.get('filepath') or 'unsaved'}"
                f"{' (dirty)' if f.get('is_dirty') else ''}, frame={fr.get('current')}, "
                f"sections={', '.join(k for k in state if k not in ('name', 'saved_at', 'blender'))}")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def restore_session_state(ctx: Context, name: str = "last", load_file: bool = True, force: bool = False) -> str:
    """
    Restore a session saved with save_session_state: the add-on reopens its file
    (with the unsaved-changes guard) and re-applies frame, camera, selection,
    active object, mode, views, workspace and settings snapshots. Reports what
    could not be restored.

    Parameters:
    - name: Session name (default "last")
    - load_file: Reopen the session's .blend first (default True)
    - force: Discard unsaved changes in the current file when reopening
    """
    try:
        doc = _read_session(name)
        state = doc.get("state", {})
        blender = get_blender_connection()
        result = blender.send_command("restore_session_state", {
            "name": name, "state": state, "load_file": load_file, "force": force,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = (f"Session '{name}' restored (saved {doc.get('saved_at')}): "
               f"file {'reopened' if result.get('file_loaded') else 'not reopened'}")
        if result.get("not_restored"):
            msg += f". Could not restore: {result['not_restored']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


# ─── File lifecycle ───────────────────────────────────────────────────────────
# (settings doc 2.1, "File lifecycle" table; inserted before the Primitives banner)

@mcp.tool()
def get_file_state(ctx: Context) -> str:
    """
    Report the state of the open .blend before any destructive step: filepath,
    is_saved, is_dirty, file_version (the Blender that SAVED the file), the running
    blender_version, use_autopack, packed_images, missing_files (images and
    libraries whose path does not exist), libraries (linked .blend paths),
    autosave_dir with autosave_files (newest first, with mtime), recent_files and
    backup_files (<name>.blend1.. beside the file). Note: an unsaved factory scene
    reports is_dirty False on Blender 5.2 and True on 4.3; never assume it.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_file_state")
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def new_file(
    ctx: Context,
    template: str = None,
    empty: bool = False,
    load_ui: bool = False,
    force: bool = False,
) -> str:
    """
    Start a new file from the startup file (wm.read_homefile).

    Parameters:
    - template: App template name (omit for the default startup file)
    - empty: True = an empty scene instead of the startup contents (default False)
    - load_ui: Load the startup file's UI layout (default False)
    - force: Unsaved changes in the current file refuse the call unless force=True

    Reply: object count after the reset.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("new_file", {
            "template": template, "empty": empty, "load_ui": load_ui, "force": force,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"New file{' (empty)' if empty else ''}"
                + (f" from template '{template}'" if template else "")
                + f": {result.get('object_count')} objects."
                + (" Unsaved changes were discarded." if result.get("unsaved_changes_discarded") else ""))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def revert_file(ctx: Context, force: bool = False, use_scripts: bool = None) -> str:
    """
    Reload the current file from disk, discarding unsaved changes
    (wm.revert_mainfile). Refuses when the file was never saved.

    Parameters:
    - force: Required (True) when there are unsaved changes
    - use_scripts: Allow the file's scripts to run; omit (None) for the preference
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("revert_file", {"force": force, "use_scripts": use_scripts})
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Reverted to '{result.get('filepath')}': {result.get('object_count')} objects, "
                f"dirty={result.get('is_dirty')}.")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def recover_file(
    ctx: Context,
    mode: str = "LAST_SESSION",
    filepath: str = None,
    force: bool = False,
) -> str:
    """
    Recover work from Blender's session or autosave files.

    Parameters:
    - mode: LAST_SESSION (wm.recover_last_session, reopens quit.blend) or AUTOSAVE
            (wm.recover_auto_save with filepath from get_file_state.autosave_files)
    - filepath: The autosave file to load when mode is AUTOSAVE
    - force: Required (True) when the current file has unsaved changes
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("recover_file", {
            "mode": mode, "filepath": filepath, "force": force,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Recovered ({result.get('mode', mode)}): {result.get('filepath') or 'session file'}, "
                f"{result.get('object_count')} objects.")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_copy(
    ctx: Context,
    filepath: str,
    compress: bool = None,
    relative_remap: bool = True,
    pack_images: bool = False,
) -> str:
    """
    Save a COPY of the working file without changing its path (deliverable-safe
    export of the current state). Optionally packs images into the copy only.

    Parameters:
    - filepath: Destination .blend path
    - compress: True/False, or omit for the preference (on by default since 5.0)
    - relative_remap: Remap relative paths for the new location (default True)
    - pack_images: True packs all external images into the copy, then unpacks
                   them again so the working file is unchanged (default False)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("save_copy", {
            "filepath": filepath, "compress": compress,
            "relative_remap": relative_remap, "pack_images": pack_images,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Copy saved: {result.get('filepath', filepath)}"
        details = []
        if "bytes" in result:
            details.append(f"{result['bytes']} bytes")
        if "compress" in result:
            details.append(f"compress={result['compress']}")
        if result.get("packed_images"):
            details.append(f"{len(result['packed_images'])} image(s) packed in the copy")
        if details:
            msg += " (" + ", ".join(details) + ")"
        return msg + ". Working file unchanged."
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_version(
    ctx: Context,
    note: str = None,
    pattern: str = "{stem}_v{n:03d}",
    dir: str = None,
    copy: bool = True,
) -> str:
    """
    Save a numbered version of the current file (my_scene_v001.blend, _v002, ...)
    with a sidecar <file>.versions.json recording n, timestamp, note, object count
    and triangle count. The file must have been saved at least once.

    Parameters:
    - note: Free text stored with the version entry
    - pattern: File-name pattern; {stem} = current file name without extension,
               {n} = version number (default "{stem}_v{n:03d}")
    - dir: Folder for the versions (default: beside the current file)
    - copy: True (default) keeps working on the original; False switches the
            working file to the new version
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("save_version", {
            "note": note, "pattern": pattern, "dir": dir, "copy": copy,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        v = result.get("version") or {}
        return (f"Version {v.get('n')} saved: {v.get('path')}"
                + (f" (note: {v.get('note')})" if v.get("note") else "")
                + f" ({v.get('object_count')} objects, {v.get('tri_count')} tris)"
                + f". {result.get('count')} version(s) in {result.get('sidecar')}."
                + f" Working file: {result.get('working_file')}.")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_versions(ctx: Context, filepath: str = None) -> str:
    """
    List the numbered versions recorded in <file>.versions.json next to the current
    file (or the given filepath), with the matching files on disk.

    Parameters:
    - filepath: A .blend whose sidecar to read (default: the current file)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_versions", {"filepath": filepath})
        if "error" in result:
            return f"Error: {result['error']}"
        versions = result.get("versions", [])
        base = result.get("file", filepath or "the current file")
        if not versions:
            return f"No versions recorded for {base} (sidecar {result.get('sidecar')})."
        lines = [f"{len(versions)} version(s) of {base} (sidecar {result.get('sidecar')}):"]
        for v in versions:
            lines.append(f"  v{v.get('n')}: {v.get('path')}  {v.get('timestamp', '')}"
                         + (f"  note: {v['note']}" if v.get("note") else "")
                         + (f"  objects={v.get('object_count')} tris={v.get('tri_count')}" if "object_count" in v else "")
                         + ("" if v.get("exists", True) else "  [missing on disk]"))
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _append_or_link(link: bool, filepath, datablocks, names, kind, collection,
                    instance_collections, relative, list_only) -> str:
    blocks = _parse_json_object(datablocks, "datablocks") if datablocks else {}
    if isinstance(blocks, str):
        return blocks
    if names and not kind:
        return "Error: 'kind' is required with 'names' (objects, collections, materials, node_groups, ...)"
    try:
        blender = get_blender_connection()
        payload = {
            "filepath": filepath, "datablocks": blocks or None, "names": _split_csv(names) or None,
            "kind": kind or "objects", "collection": collection,
            "instance_collections": instance_collections, "relative": relative,
            "list_only": list_only,
        }
        result = blender.send_command("link_from_blend" if link else "append_from_blend", payload)
        if "error" in result:
            return f"Error: {result['error']}"
        if list_only:
            return json.dumps(result.get("contents", result), indent=2)
        verb = "Linked" if link else "Appended"
        got = result.get("appended") or result.get("linked") or {}
        parts = ([f"{len(v)} {k}" for k, v in got.items()] if isinstance(got, dict)
                 else [f"{len(got)} item(s)"] if isinstance(got, list) else [])
        msg = f"{verb} from {filepath}: " + (", ".join(parts) if parts else "nothing")
        if result.get("missing"):
            msg += f". Not found in file: {result['missing']}"
        if result.get("new_objects"):
            msg += f". New objects: {result['new_objects']}"
        if result.get("linked_into"):
            msg += f". Linked into collection: {result['linked_into']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def append_from_blend(
    ctx: Context,
    filepath: str,
    datablocks: str = None,
    names: str = None,
    kind: str = None,
    collection: str = None,
    instance_collections: bool = False,
    relative: bool = True,
    list_only: bool = False,
) -> str:
    """
    Append (copy) data-blocks from another .blend into the current file via
    bpy.data.libraries.load. Use list_only=True first to see what the file holds.

    Parameters:
    - filepath: Source .blend
    - datablocks: JSON object of what to load, e.g.
        '{"objects": ["Cube"], "collections": ["Kit"], "materials": ["Steel"], "node_groups": ["Rust"]}'
    - names / kind: Alternative to datablocks: a comma list of names plus one kind
        (objects, collections, materials, node_groups, meshes, images, actions, ...)
    - collection: Link appended objects into this collection (default: active)
    - instance_collections: Appended collections become collection instances
    - relative: Store the library path relative to the current file (default True)
    - list_only: True returns the file's contents per data type without loading
    """
    return _append_or_link(False, filepath, datablocks, names, kind, collection,
                           instance_collections, relative, list_only)


@mcp.tool()
def link_from_blend(
    ctx: Context,
    filepath: str,
    datablocks: str = None,
    names: str = None,
    kind: str = None,
    collection: str = None,
    instance_collections: bool = False,
    relative: bool = True,
    list_only: bool = False,
) -> str:
    """
    Link (reference, not copy) data-blocks from another .blend; the same
    parameters as append_from_blend with link=True. Linked data stays read-only
    and follows the source file.

    Parameters:
    - filepath: Source .blend
    - datablocks: JSON object per data type, e.g. '{"collections": ["Kit"]}'
    - names / kind: Comma list of names plus one kind, instead of datablocks
    - collection: Collection to link objects into (default: active)
    - instance_collections: Linked collections become collection instances (usual for kits)
    - relative: Store the library path relative to the current file (default True)
    - list_only: True returns the file's contents per data type without linking
    """
    return _append_or_link(True, filepath, datablocks, names, kind, collection,
                           instance_collections, relative, list_only)


@mcp.tool()
def set_autosave(
    ctx: Context,
    enabled: bool = None,
    interval_minutes: int = None,
    save_versions: int = None,
    temp_dir: str = None,
    persist: bool = False,
) -> str:
    """
    Configure Blender's autosave preferences. Only passed values change.

    Parameters:
    - enabled: preferences.filepaths.use_auto_save_temporary_files
    - interval_minutes: auto_save_time (minutes between autosaves)
    - save_versions: Number of .blend1/.blend2 backups kept on save (save_version)
    - temp_dir: temporary_directory for autosave files (empty = system temp)
    - persist: True also writes userpref.blend (consent rule: nothing here is
               persisted unless you ask); the reply says whether it was persisted
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_autosave", {
            "enabled": enabled, "interval_minutes": interval_minutes,
            "save_versions": save_versions, "temp_dir": temp_dir, "persist": persist,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(
            f"Autosave: enabled={result.get('enabled')}, every {result.get('interval_minutes')} min, "
            f"backups={result.get('save_versions')}, temp_dir={result.get('temp_dir') or 'system temp'}.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def make_paths_relative(ctx: Context) -> str:
    """
    Make every external file path in the current .blend relative to it
    (bpy.ops.file.make_paths_relative). The file must be saved first.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("make_paths_relative")
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Paths: {len(result.get('relative', []))} relative, {len(result.get('absolute', []))} absolute"
                + (f", missing: {result['missing']}" if result.get("missing") else ", none missing"))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def make_paths_absolute(ctx: Context) -> str:
    """
    Make every external file path in the current .blend absolute
    (bpy.ops.file.make_paths_absolute).
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("make_paths_absolute")
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Paths: {len(result.get('absolute', []))} absolute, {len(result.get('relative', []))} relative"
                + (f", missing: {result['missing']}" if result.get("missing") else ", none missing"))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def find_missing_files(ctx: Context, directory: str, find_all: bool = False) -> str:
    """
    Search a folder (recursively) for external files the .blend cannot find and
    relink them (bpy.ops.file.find_missing_files).

    Parameters:
    - directory: Folder to search
    - find_all: True re-searches every file, not only the missing ones (default False)

    Reply: how many were found and the list still missing.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("find_missing_files", {
            "directory": directory, "find_all": find_all,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Found {result.get('found', 0)} file(s) under {directory}; "
                f"still missing: {result.get('still_missing') or 'none'}")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def pack_all(ctx: Context) -> str:
    """
    Pack every external image and other packable file into the .blend
    (bpy.ops.file.pack_all) so the file is self-contained.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("pack_all")
        if "error" in result:
            return f"Error: {result['error']}"
        imgs = result.get("packed_images", [])
        return f"Packed: {len(imgs)} image(s) now packed" + (f": {', '.join(imgs[:10])}" if imgs else "")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def unpack_all(ctx: Context, unpack_method: str = "USE_LOCAL") -> str:
    """
    Unpack every packed file back to disk (bpy.ops.file.unpack_all).

    Parameters:
    - unpack_method: USE_LOCAL (default, write next to the .blend into //textures),
                     WRITE_LOCAL, USE_ORIGINAL, WRITE_ORIGINAL, KEEP, REMOVE
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("unpack_all", {"unpack_method": unpack_method})
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Unpacked ({result.get('method', unpack_method)}); "
                f"{len(result.get('packed_images', []))} image(s) still packed")
    except Exception as e:
        return f"Error: {e}"


# ─── Settings, generic ────────────────────────────────────────────────────────
# (settings doc 2.1, "Settings, generic" table)

_SETTINGS_SCOPES = ("SCENE", "RENDER", "OUTPUT", "CYCLES", "EEVEE", "COLOR", "UNITS",
                    "VIEWPORT", "PREFS_FILEPATHS", "PREFS_VIEW", "PREFS_EDIT",
                    "PREFS_SYSTEM", "PREFS_INPUT", "ADDON:<module>", "MCP", "SERVER")
_SETTINGS_SCOPES_DOC = ", ".join(_SETTINGS_SCOPES)


def _server_scope_describe() -> dict:
    """SERVER scope: the Python server's own settings, in the describe_settings shape."""
    out = {}
    for key, row in _settings.describe().items():
        default = _settings.DEFAULTS[key]
        out[key] = {
            "type": type(default).__name__.upper() if default is not None else "STRING",
            "value": row["value"], "default": default, "source": row["source"],
            "env": row["env"], "read_only": False,
            "description": "Python server setting (settings.json); environment overrides the file.",
        }
    return out


@mcp.tool()
def describe_settings(ctx: Context, scope: str) -> str:
    """
    Describe every property of a settings scope: type, current value, default,
    enum items, min/max, description and read-only flag. Generated from Blender's
    own property definitions (bl_rna), so it lists exactly what this Blender has.

    Parameters:
    - scope: SCENE, RENDER, OUTPUT (image_settings + filepath), CYCLES, EEVEE,
             COLOR (view + display settings), UNITS, VIEWPORT (shading and
             overlays of the first 3D view), PREFS_FILEPATHS, PREFS_VIEW,
             PREFS_EDIT, PREFS_SYSTEM, PREFS_INPUT, ADDON:<module> (that add-on's
             preferences), MCP (this add-on's preferences), SERVER (the Python
             server's settings file; answered without Blender)
    """
    try:
        sc = (scope or "").strip()
        if sc.upper() == "SERVER":
            return json.dumps({"scope": "SERVER", "properties": _server_scope_describe()}, indent=2)
        blender = get_blender_connection()
        result = blender.send_command("describe_settings", {"scope": sc})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_settings(ctx: Context, scope: str, keys: str = None) -> str:
    """
    Read current values of a settings scope (values only; enums as strings,
    vectors as lists, pointers as names).

    Parameters:
    - scope: One of the scopes listed in describe_settings (SERVER is answered
             without Blender)
    - keys: Comma-separated property names to read; omit for all
    """
    try:
        sc = (scope or "").strip()
        wanted = _split_csv(keys)
        if sc.upper() == "SERVER":
            eff = _settings.effective()
            if wanted:
                unknown = [k for k in wanted if k not in eff]
                eff = {k: eff[k] for k in wanted if k in eff}
                if unknown:
                    eff["_unknown"] = unknown
            return json.dumps({"scope": "SERVER", "values": eff}, indent=2)
        blender = get_blender_connection()
        result = blender.send_command("get_settings", {"scope": sc, "keys": wanted or None})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_settings(ctx: Context, scope: str, values: str, persist: bool = False) -> str:
    """
    Write values into a settings scope. Each value is validated against Blender's
    property definition (enum membership, numeric range, type) before writing;
    read-only properties are refused with the reason.

    Parameters:
    - scope: One of the scopes listed in describe_settings. SERVER writes the
             Python server's settings.json (same as set_server_settings).
    - values: JSON object of property -> value, e.g. '{"resolution_x": 1280, "engine": "CYCLES"}'
    - persist: For PREFS_*, ADDON: and MCP scopes, True also saves userpref.blend
               (consent rule: preferences are never persisted unless asked)

    Reply: set (applied keys) and unset {key: reason}, like add_modifier.
    """
    vals = _parse_json_object(values, "values")
    if isinstance(vals, str):
        return vals
    try:
        sc = (scope or "").strip()
        if sc.upper() == "SERVER":
            return set_server_settings(ctx, json.dumps(vals))
        blender = get_blender_connection()
        result = blender.send_command("set_settings", {"scope": sc, "values": vals, "persist": persist})
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"{sc}: set {result.get('set', [])}"
        if result.get("unset"):
            msg += f". Could not set: {result['unset']}"
        if "persisted" in result:
            msg += f". Preferences {'persisted' if result['persisted'] else 'not persisted'}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def settings_snapshot(
    ctx: Context,
    name: str,
    scopes: str = "SCENE,RENDER,OUTPUT,CYCLES,EEVEE,COLOR,UNITS,VIEWPORT",
    filepath: str = None,
) -> str:
    """
    Capture the current values of the given scopes under a name (kept in the
    Blender session; survives an add-on reload, dies with Blender) and
    optionally into a JSON file. Restore with settings_restore.

    Parameters:
    - name: Snapshot name
    - scopes: Comma list of scopes (default: the eight scene-level scopes)
    - filepath: Also write the snapshot to this JSON file
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("settings_snapshot", {
            "name": name, "scopes": _split_csv(scopes), "filepath": filepath,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        counts = result.get("keys_per_scope", {})
        return (f"Snapshot '{name}': " + ", ".join(f"{k}={v}" for k, v in counts.items())
                + (f". Written to {result['filepath']}" if result.get("filepath") else "")
                + (f". File not written: {result['file_error']}" if result.get("file_error") else ""))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def settings_restore(
    ctx: Context,
    name: str = None,
    filepath: str = None,
    scopes: str = None,
) -> str:
    """
    Restore a settings snapshot taken with settings_snapshot (by name, or from a
    JSON file). Reports keys that no longer exist or failed to apply.

    Parameters:
    - name: Snapshot name (session snapshots)
    - filepath: JSON file written by settings_snapshot (used when name is omitted)
    - scopes: Comma list to restore only some scopes (default: all in the snapshot)
    """
    if not name and not filepath:
        return "Error: give a snapshot name or a filepath"
    try:
        blender = get_blender_connection()
        result = blender.send_command("settings_restore", {
            "name": name, "filepath": filepath, "scopes": _split_csv(scopes) or None,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Restored snapshot '{name or filepath}': {result.get('restored', 0)} keys"
        if result.get("failed"):
            msg += f". Failed: {result['failed']}"
        if result.get("missing"):
            msg += f". No longer exist: {result['missing']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_settings_snapshots(ctx: Context) -> str:
    """
    List the settings snapshots held in the Blender session with the key count
    per scope.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_settings_snapshots")
        if "error" in result:
            return f"Error: {result['error']}"
        snaps = result.get("snapshots", {})
        if not snaps:
            return "No settings snapshots in this session."
        return "\n".join(
            f"{name}: " + ", ".join(f"{sc}={n}" for sc, n in scopes.items())
            for name, scopes in snaps.items())
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_settings_snapshot(ctx: Context, name: str) -> str:
    """
    Delete a session settings snapshot by name.

    Parameters:
    - name: Snapshot name (see list_settings_snapshots)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("delete_settings_snapshot", {"name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Deleted snapshot '{result.get('deleted', name)}'."
    except Exception as e:
        return f"Error: {e}"


# ─── Settings, typed conveniences ─────────────────────────────────────────────
# (settings doc 2.1, "Settings, typed conveniences" table; all implemented on
#  set_settings addon-side; the wrappers send only the parameters given)

def _settings_reply(prefix: str, result: dict) -> str:
    msg = prefix
    if result.get("set"):
        msg += f" Set: {result['set']}."
    if result.get("unset"):
        msg += f" Could not set: {result['unset']}."
    if "persisted" in result:
        msg += f" Preferences {'persisted' if result['persisted'] else 'not persisted'}."
    return msg.strip()


@mcp.tool()
def set_output_settings(
    ctx: Context,
    filepath: str = None,
    file_format: str = None,
    color_mode: str = None,
    color_depth: str = None,
    compression: int = None,
    quality: int = None,
    film_transparent: bool = None,
    use_stamp: bool = None,
    use_overwrite: bool = None,
    use_placeholder: bool = None,
    ffmpeg: str = None,
) -> str:
    """
    Set the render output path, image format and video settings in one call.
    Only the parameters you pass are changed.

    Parameters:
    - filepath: Output path (e.g. "//renders/frame_####"); Blender adds the extension
    - file_format: PNG, JPEG, OPEN_EXR (EXR accepted), OPEN_EXR_MULTILAYER, TIFF,
                   BMP, TARGA, WEBP, FFMPEG (video; sets media_type VIDEO on 5.x)
    - color_mode: BW, RGB or RGBA
    - color_depth: "8" / "16" (PNG, TIFF) or "16" / "32" (OPEN_EXR)
    - compression: PNG compression 0-100
    - quality: JPEG/WEBP quality 0-100
    - film_transparent: Transparent background (alpha) on/off
    - use_stamp: Burn metadata into the image
    - use_overwrite / use_placeholder: Animation output file handling
    - ffmpeg: JSON for video, e.g. '{"format": "MPEG4", "codec": "H264", "constant_rate_factor": "MEDIUM"}'
    """
    ff = _parse_json_object(ffmpeg, "ffmpeg") if ffmpeg else {}
    if isinstance(ff, str):
        return ff
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_output_settings", {
            "filepath": filepath, "file_format": file_format, "color_mode": color_mode,
            "color_depth": color_depth, "compression": compression, "quality": quality,
            "film_transparent": film_transparent, "use_stamp": use_stamp,
            "use_overwrite": use_overwrite, "use_placeholder": use_placeholder,
            "ffmpeg": ff or None,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply("Output settings:", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_color_management(
    ctx: Context,
    view_transform: str = None,
    look: str = None,
    exposure: float = None,
    gamma: float = None,
    display_device: str = None,
    sequencer_colorspace: str = None,
) -> str:
    """
    Set scene colour management. Game-asset renders and bakes usually want
    view_transform "Standard" so colours match the engine (Blender's default is AgX).

    Parameters:
    - view_transform: Standard, Filmic, AgX, Khronos PBR Neutral, Raw, False Color
    - look: e.g. "None", "AgX - Medium High Contrast" (names depend on the transform)
    - exposure / gamma: Floats (0.0 / 1.0 are neutral)
    - display_device: sRGB, Display P3, Rec.1886, Rec.2020 (or as installed)
    - sequencer_colorspace: Colour space for the sequencer

    Values are validated by assignment (the enum list is not readable headless).
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_color_management", {
            "view_transform": view_transform, "look": look, "exposure": exposure,
            "gamma": gamma, "display_device": display_device,
            "sequencer_colorspace": sequencer_colorspace,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(
            f"Colour management: view={result.get('view_transform')} look={result.get('look')} "
            f"exposure={result.get('exposure')} gamma={result.get('gamma')} "
            f"display={result.get('display_device')}.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_render_quality(ctx: Context, preset: str = "PREVIEW", engine: str = None) -> str:
    """
    Apply a quality preset. The previous values are stored in the settings
    snapshot "_before_quality" so settings_restore("_before_quality") undoes it.

    Parameters:
    - preset: PREVIEW (25% resolution, 16 samples, denoise on, simplify on),
              DRAFT (50%, 64 samples), FINAL (100%, 256 samples or the engine
              default, persistent data on)
    - engine: Optionally switch the engine first (CYCLES, BLENDER_EEVEE, BLENDER_WORKBENCH)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_render_quality", {"preset": preset, "engine": engine})
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(
            f"Quality {result.get('preset', preset)}: engine={result.get('engine')} "
            f"resolution={result.get('resolution_percentage')}% samples={result.get('samples')}. "
            f"Previous values in snapshot '{result.get('snapshot', '_before_quality')}'.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_render_device(
    ctx: Context,
    device: str = "GPU",
    backend: str = None,
    persist: bool = False,
) -> str:
    """
    Choose the Cycles render device and compute backend.

    Parameters:
    - device: GPU or CPU (scene.cycles.device)
    - backend: OPTIX, CUDA, HIP, ONEAPI, METAL or NONE (Cycles preferences
               compute_device_type; validated by assignment, then every device of
               that type is enabled)
    - persist: True also saves userpref.blend (consent rule)

    Reply: the devices Blender found with their use flag.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_render_device", {
            "device": device, "backend": backend, "persist": persist,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        lines = [f"Cycles device={result.get('device')} backend={result.get('backend')}."]
        for d in result.get("devices", []):
            lines.append(f"  {d.get('name')} [{d.get('type')}] use={d.get('use')}")
        if result.get("warning"):
            lines.append(f"Warning: {result['warning']}")
        lines.append(f"Preferences {'persisted' if result.get('persisted') else 'not persisted'}.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_render_devices(ctx: Context) -> str:
    """
    List the compute devices Cycles can see (CPU, CUDA/OPTIX/HIP/ONEAPI/METAL
    GPUs) with their current use flag and the active backend. Headless sessions
    may report CPU only.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_render_devices")
        if "error" in result:
            return f"Error: {result['error']}"
        devs = result.get("devices", [])
        lines = [f"Backend: {result.get('backend')}; scene device: {result.get('scene_device')}; {len(devs)} device(s):"]
        for d in devs:
            lines.append(f"  {d.get('name')} [{d.get('type')}] use={d.get('use')}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_simplify(
    ctx: Context,
    enabled: bool,
    subdivision: int = None,
    child_particles: float = None,
    texture_limit: str = None,
    volume_resolution: float = None,
) -> str:
    """
    Toggle and configure render Simplify (scene.render.use_simplify and friends).

    Parameters:
    - enabled: Simplify on/off
    - subdivision: Max subdivision level for renders (simplify_subdivision)
    - child_particles: 0.0-1.0 fraction of child particles
    - texture_limit: OFF, 128, 256, 512, 1024, 2048, 4096, 8192 (Cycles texture limit)
    - volume_resolution: 0.0-1.0 volume resolution factor
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_simplify", {
            "enabled": enabled, "subdivision": subdivision, "child_particles": child_particles,
            "texture_limit": texture_limit, "volume_resolution": volume_resolution,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(
            f"Simplify {'on' if result.get('use_simplify', enabled) else 'off'}: "
            f"subdivision={result.get('simplify_subdivision')}.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_frame_range(
    ctx: Context,
    start: int = None,
    end: int = None,
    fps: float = None,
    fps_base: float = None,
    current: int = None,
) -> str:
    """
    Set the scene frame range and frame rate (shared with the animation tools).
    Only the parameters you pass are changed.

    Parameters:
    - start / end: Scene frame range
    - fps / fps_base: Frame rate (24 / 1.0; 24 / 1.001 for 23.976)
    - current: Also set the current frame
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_frame_range", {
            "start": start, "end": end, "fps": fps, "fps_base": fps_base, "current": current,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(
            f"Frames {result.get('frame_start')}-{result.get('frame_end')} at "
            f"{result.get('fps')}/{result.get('fps_base')} fps.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_scene_units(
    ctx: Context,
    preset: str = None,
    system: str = None,
    scale_length: float = None,
    length_unit: str = None,
    rescale_objects: bool = False,
) -> str:
    """
    Set scene units (shared with the engine-readiness tools), by engine preset
    or explicitly. Unreal wants centimetres (scale_length 0.01, CENTIMETERS);
    Unity, Godot, Bevy and glTF want metres (1.0, METERS).

    Parameters:
    - preset: UNREAL, UNITY, GODOT, BEVY, TIMBERMESH or NONE (sets system,
              scale_length and length_unit for that engine)
    - system: METRIC, IMPERIAL or NONE
    - scale_length: Unit scale (1.0 = metres, 0.01 = centimetres)
    - length_unit: ADAPTIVE, KILOMETERS, METERS, CENTIMETERS, MILLIMETERS,
                   MICROMETERS, MILES, FEET, INCHES, THOU
    - rescale_objects: True also scales the scene's objects so they keep their
                       real-world size under the new scale_length (default False)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_scene_units", {
            "preset": preset, "system": system, "scale_length": scale_length,
            "length_unit": length_unit, "rescale_objects": rescale_objects,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(
            f"Units: system={result.get('system')} scale_length={result.get('scale_length')} "
            f"length={result.get('length_unit')}.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_viewport_defaults(
    ctx: Context,
    shading: str = None,
    light: str = None,
    color_type: str = None,
    show_overlays: bool = None,
    show_floor: bool = None,
    show_stats: bool = None,
    clip_end: float = None,
    lens: float = None,
) -> str:
    """
    Apply viewport display settings to EVERY 3D view (the persistent counterpart
    of the per-capture shading/overlay options). Needs a GUI session.

    Parameters:
    - shading: WIREFRAME, SOLID, MATERIAL or RENDERED
    - light: STUDIO, MATCAP or FLAT (solid mode)
    - color_type: MATERIAL, SINGLE, OBJECT, RANDOM, VERTEX or TEXTURE (solid mode)
    - show_overlays / show_floor / show_stats: Overlay toggles
    - clip_end: View clip distance
    - lens: Viewport focal length in mm
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_viewport_defaults", {
            "shading": shading, "light": light, "color_type": color_type,
            "show_overlays": show_overlays, "show_floor": show_floor, "show_stats": show_stats,
            "clip_end": clip_end, "lens": lens,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        areas = result.get("areas")
        n = len(areas) if isinstance(areas, (list, dict)) else areas
        return _settings_reply(f"Viewport defaults applied to {n} 3D view(s).", result)
    except Exception as e:
        return f"Error: {e}"


# ─── Presets and project profile ──────────────────────────────────────────────
# (settings doc 2.1, "Presets and profiles" table). Preset files are SERVER-side:
# <settings dir>/presets/<name>.json, values pulled through the add-on's
# get_settings and pushed back through set_settings. Shipped defaults live in
# src/blender_mcp/presets_default.json and are shadowed by a user file of the same name.

_SHIPPED_PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets_default.json")


def _shipped_presets() -> dict:
    try:
        with open(_SHIPPED_PRESETS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh).get("presets", {})
    except Exception as e:
        logger.warning(f"shipped presets unreadable ({_SHIPPED_PRESETS_FILE}): {e}")
        return {}


def _preset_path(name: str):
    return _settings.presets_dir() / f"{name}.json"


def _user_presets() -> dict:
    """{name: path} for every JSON file in the presets dir."""
    d = _settings.presets_dir()
    if not d.is_dir():
        return {}
    return {p.stem: p for p in sorted(d.glob("*.json"))}


def _load_preset_file(name_or_path: str) -> tuple[dict | None, str | None]:
    """Resolve a preset by name (user file, then shipped) or by JSON path.
    Returns (preset_dict, error)."""
    p = Path(name_or_path).expanduser()
    if p.suffix.lower() == ".json" and p.is_file():
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            return None, f"cannot read {p}: {e}"
        return _normalise_preset(data, p.stem), None
    name = str(name_or_path).strip()
    user = _user_presets()
    if name in user:
        try:
            with open(user[name], "r", encoding="utf-8") as fh:
                return _normalise_preset(json.load(fh), name), None
        except Exception as e:
            return None, f"cannot read {user[name]}: {e}"
    shipped = _shipped_presets()
    if name in shipped:
        pr = dict(shipped[name]); pr["name"] = name; pr["shipped"] = True
        return pr, None
    return None, (f"no preset '{name}'. Available: "
                  f"{', '.join(sorted(set(user) | set(shipped))) or 'none'}")


def _normalise_preset(data: dict, name: str) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("scopes"), dict):
        raise ValueError("preset JSON must be an object with a 'scopes' object")
    data.setdefault("name", name)
    return data


def _write_preset(path, preset: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(preset, fh, indent=2, sort_keys=True)
        fh.write("\n")


@mcp.tool()
def save_preset(
    ctx: Context,
    name: str,
    scopes: str = "RENDER,OUTPUT,CYCLES,EEVEE,COLOR",
    overwrite: bool = False,
    description: str = None,
) -> str:
    """
    Save the current values of the given settings scopes as a named preset JSON
    under the server's presets dir (portable across files and Blender versions).

    Parameters:
    - name: Preset name (letters, digits, _ . -), becomes <name>.json
    - scopes: Comma list of scopes to capture (default RENDER,OUTPUT,CYCLES,EEVEE,COLOR)
    - overwrite: True replaces an existing preset of that name (default False)
    - description: Free text stored in the preset
    """
    if not _SAFE_NAME_RE.match(name or ""):
        return "Error: preset name must be 1-64 characters of letters, digits, '_', '.' or '-'"
    path = _preset_path(name)
    if path.exists() and not overwrite:
        return f"Error: preset '{name}' exists at {path}; pass overwrite=True to replace it"
    scope_list = _split_csv(scopes)
    if not scope_list:
        return "Error: scopes is empty"
    try:
        blender = get_blender_connection()
        captured, failed = {}, {}
        for sc in scope_list:
            result = blender.send_command("get_settings", {"scope": sc, "keys": None})
            if "error" in result:
                failed[sc] = result["error"]
            else:
                captured[sc] = result.get("values", result)
        if not captured:
            return f"Error: nothing captured: {failed}"
        preset = {
            "name": name, "description": description or "",
            "created": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            "server_version": __version__,
            "scopes": captured,
        }
        _write_preset(path, preset)
        msg = f"Preset '{name}' saved to {path}: " + ", ".join(f"{k}={len(v)} keys" for k, v in captured.items())
        if failed:
            msg += f". Scopes not captured: {failed}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def load_preset(ctx: Context, name: str = None, filepath: str = None, scopes: str = None) -> str:
    """
    Apply a preset (user preset by name, shipped preset by name, or any preset
    JSON by filepath) through set_settings, scope by scope. Keys that this
    Blender does not have are reported as unset and the rest still apply.

    Parameters:
    - name: Preset name (see list_presets); shipped: game_bake, preview,
            final_eevee, final_cycles, sprite_sheet, turntable_video
    - filepath: A preset JSON file instead of a name
    - scopes: Comma list to apply only some of the preset's scopes
    """
    ref = filepath or name
    if not ref:
        return "Error: give a preset name or a filepath"
    preset, err = _load_preset_file(ref)
    if err:
        return f"Error: {err}"
    wanted = set(s.upper() for s in _split_csv(scopes)) if scopes else None
    try:
        blender = get_blender_connection()
        applied, unset, errors = {}, {}, {}
        for sc, values in preset["scopes"].items():
            if wanted and sc.upper() not in wanted:
                continue
            result = blender.send_command("set_settings", {"scope": sc, "values": values, "persist": False})
            if "error" in result:
                errors[sc] = result["error"]
                continue
            applied[sc] = result.get("set", [])
            if result.get("unset"):
                unset[sc] = result["unset"]
        msg = f"Preset '{preset.get('name', ref)}' applied: " + ", ".join(
            f"{k}={len(v)} set" for k, v in applied.items()) if applied else f"Preset '{ref}': nothing applied"
        if unset:
            msg += f". Unset: {unset}"
        if errors:
            msg += f". Scope errors: {errors}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_presets(ctx: Context) -> str:
    """
    List the shipped presets and the user presets in the server's presets dir
    (name, description, scopes, origin).
    """
    try:
        lines = [f"Presets dir: {_settings.presets_dir()}"]
        user = _user_presets()
        for name, p in user.items():
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                lines.append(f"  {name} [user]: {d.get('description', '')} scopes={','.join(d.get('scopes', {}).keys())}")
            except Exception as e:
                lines.append(f"  {name} [user, unreadable: {e}]")
        for name, d in _shipped_presets().items():
            tag = "shipped, shadowed by user preset" if name in user else "shipped"
            lines.append(f"  {name} [{tag}]: {d.get('description', '')} scopes={','.join(d.get('scopes', {}).keys())}")
        return "\n".join(lines) if len(lines) > 1 else lines[0] + "\nNo presets found."
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_preset(ctx: Context, name: str) -> str:
    """
    Delete a user preset file. Shipped presets cannot be deleted (a user preset
    that shadows one can).

    Parameters:
    - name: Preset name
    """
    user = _user_presets()
    if name not in user:
        if name in _shipped_presets():
            return f"Error: '{name}' is a shipped preset and cannot be deleted"
        return f"Error: no user preset '{name}'"
    try:
        user[name].unlink()
        return f"Deleted preset '{name}' ({user[name]})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def export_preset(ctx: Context, name: str, filepath: str) -> str:
    """
    Write a preset (user or shipped) to a JSON file of your choice, e.g. to share
    it or keep it with a project.

    Parameters:
    - name: Preset name
    - filepath: Destination .json path
    """
    preset, err = _load_preset_file(name)
    if err:
        return f"Error: {err}"
    try:
        dest = Path(filepath).expanduser()
        _write_preset(dest, preset)
        return f"Preset '{name}' exported to {dest}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def import_preset(ctx: Context, filepath: str, name: str = None, overwrite: bool = False) -> str:
    """
    Copy a preset JSON file into the server's presets dir so it can be loaded by name.

    Parameters:
    - filepath: Source .json (a file written by save_preset or export_preset)
    - name: Name to store it under (default: the file's stem)
    - overwrite: True replaces an existing preset of that name
    """
    src = Path(filepath).expanduser()
    if not src.is_file():
        return f"Error: file not found: {src}"
    name = name or src.stem
    if not _SAFE_NAME_RE.match(name):
        return "Error: preset name must be 1-64 characters of letters, digits, '_', '.' or '-'"
    preset, err = _load_preset_file(str(src))
    if err:
        return f"Error: {err}"
    dest = _preset_path(name)
    if dest.exists() and not overwrite:
        return f"Error: preset '{name}' exists; pass overwrite=True"
    try:
        preset["name"] = name
        preset.pop("shipped", None)
        _write_preset(dest, preset)
        return f"Preset '{name}' imported to {dest} (scopes: {', '.join(preset['scopes'])})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def apply_blender_preset(ctx: Context, category: str, name: str) -> str:
    """
    Apply one of Blender's own Python presets (script.execute_preset), e.g. the
    built-in render size presets.

    Parameters:
    - category: Preset folder, e.g. "render", "cycles/sampling", "cycles/viewport",
                "cloth", "fluid", "camera" (see list_blender_presets)
    - name: Preset name as listed, e.g. "HDTV 1080p"
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("apply_blender_preset", {"category": category, "name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Applied Blender preset {result.get('category', category)}/{result.get('name', name)} ({result.get('path')})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_blender_presets(ctx: Context, category: str = "render") -> str:
    """
    List Blender's own presets in a category (bpy.utils.preset_paths).

    Parameters:
    - category: e.g. "render", "cycles/sampling", "cycles/viewport", "cloth",
                "fluid", "camera", "safe_areas", "tracking_camera"
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_blender_presets", {"category": category})
        if "error" in result:
            return f"Error: {result['error']}"
        presets = result.get("presets", [])
        names = [p.get("name") if isinstance(p, dict) else str(p) for p in presets]
        if not names:
            return f"No Blender presets in category '{category}' (paths searched: {result.get('paths')})"
        return f"{len(names)} preset(s) in '{category}': " + ", ".join(names)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_project_profile(
    ctx: Context,
    engine_target: str,
    export_dir: str = None,
    texture_dir: str = None,
    render_dir: str = None,
    kit_unit: float = None,
    naming: str = None,
    max_triangles: int = None,
    texture_size: int = None,
    notes: str = None,
    apply_units: bool = False,
) -> str:
    """
    Store the project's target engine and conventions in the scene (custom
    property blendermcp_profile, travels with the file) and mirror them to
    <file>.mcp-profile.json. Export and texturing tools read their defaults here.

    Parameters:
    - engine_target: UNITY, UNREAL, GODOT, BEVY, TIMBERMESH or NONE
    - export_dir / texture_dir / render_dir: Default output folders
    - kit_unit: Modular kit grid unit in scene units
    - naming: Naming convention note (e.g. "SM_<Asset>_<Variant>")
    - max_triangles / texture_size: Budgets the validation tools check against
    - notes: Free text
    - apply_units: True also sets scene units for the target (Unreal: centimetres)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_project_profile", {
            "engine_target": engine_target, "export_dir": export_dir, "texture_dir": texture_dir,
            "render_dir": render_dir, "kit_unit": kit_unit, "naming": naming,
            "max_triangles": max_triangles, "texture_size": texture_size, "notes": notes,
            "apply_units": apply_units,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        profile = result.get("profile", {})
        units = result.get("units")
        msg = f"Project profile set: target={profile.get('engine_target', engine_target)}"
        if isinstance(units, dict) and "error" not in units:
            msg += f", units applied (system={units.get('system')} scale_length={units.get('scale_length')} {units.get('length_unit')})"
        elif isinstance(units, dict):
            msg += f", units NOT applied: {units.get('error')}"
        if result.get("sidecar"):
            msg += f", sidecar {result['sidecar']}"
        if result.get("sidecar_error"):
            msg += f", sidecar not written ({result['sidecar_error']})"
        return msg + ". Profile: " + json.dumps(profile)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_project_profile(ctx: Context, apply_units: bool = False) -> str:
    """
    Read the project profile stored in the scene (see set_project_profile), or
    report that none is set.

    Parameters:
    - apply_units: True also (re)applies the scene units for the profile's
                   engine_target, e.g. after opening the file on another machine
                   (default False)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_project_profile", {"apply_units": apply_units})
        if "error" in result:
            return f"Error: {result['error']}"
        if not result.get("profile"):
            return "No project profile set. Use set_project_profile(engine_target=...)."
        out = json.dumps(result["profile"], indent=2)
        if isinstance(result.get("units"), dict):
            out += "\nUnits applied: " + json.dumps(result["units"])
        return out
    except Exception as e:
        return f"Error: {e}"


# ─── Add-ons, workspaces, preferences ─────────────────────────────────────────
# (settings doc 2.1 table; get/set_server_settings landed in L1)

@mcp.tool()
def list_addons(ctx: Context, enabled_only: bool = False, filter: str = None) -> str:
    """
    List installed add-ons: module, name, version, category, enabled,
    has_preferences, path.

    Parameters:
    - enabled_only: True lists only enabled add-ons
    - filter: Case-insensitive substring on module or name
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_addons", {"enabled_only": enabled_only, "filter": filter})
        if "error" in result:
            return f"Error: {result['error']}"
        addons = result.get("addons", [])
        if not addons:
            return "No add-ons match."
        lines = [f"{len(addons)} add-on(s):"]
        for a in addons:
            ver = a.get("version")
            ver = ".".join(str(x) for x in ver) if isinstance(ver, (list, tuple)) else ver
            lines.append(f"  {a.get('module')}  {a.get('name')} {ver or ''}  [{a.get('category', '')}]"
                         f"  enabled={a.get('enabled')} prefs={a.get('has_preferences')}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def enable_addon(ctx: Context, module: str, persist: bool = False) -> str:
    """
    Enable an add-on by module name (addon_utils.enable), e.g. "rigify",
    "io_anim_bvh", "node_wrangler", "cycles".

    Parameters:
    - module: Add-on module name (see list_addons)
    - persist: True also saves userpref.blend (consent rule; default False)

    Reply: the add-on's bl_info, or the error text if enabling fails.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("enable_addon", {"module": module, "persist": persist})
        if "error" in result:
            return f"Error: {result['error']}"
        info = result.get("bl_info", {})
        ver = info.get("version")
        ver = ".".join(str(x) for x in ver) if isinstance(ver, (list, tuple)) else ver
        return (f"Enabled '{module}': {info.get('name')} {ver or ''} ({info.get('category')}). "
                f"Preferences {'persisted' if result.get('persisted') else 'not persisted'}"
                f"{' (dirty)' if result.get('preferences_dirty') else ''}."
                + (f" Persist error: {result['persist_error']}" if result.get("persist_error") else ""))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def disable_addon(ctx: Context, module: str, persist: bool = False) -> str:
    """
    Disable an add-on by module name (addon_utils.disable).

    Parameters:
    - module: Add-on module name
    - persist: True also saves userpref.blend (consent rule; default False)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("disable_addon", {"module": module, "persist": persist})
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Disabled '{module}' (enabled now: {result.get('enabled')}). "
                f"Preferences {'persisted' if result.get('persisted') else 'not persisted'}."
                + (f" Warnings: {result['warnings']}" if result.get("warnings") else ""))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_addon_preferences(ctx: Context, module: str) -> str:
    """
    Read an add-on's preferences (same data as get_settings(scope="ADDON:<module>")).

    Parameters:
    - module: Add-on module name
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_addon_preferences", {"module": module})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_addon_preferences(ctx: Context, module: str, values: str, persist: bool = False) -> str:
    """
    Write an add-on's preferences (same machinery as set_settings(scope="ADDON:<module>")).

    Parameters:
    - module: Add-on module name
    - values: JSON object of property -> value
    - persist: True also saves userpref.blend (consent rule; default False)
    """
    vals = _parse_json_object(values, "values")
    if isinstance(vals, str):
        return vals
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_addon_preferences", {"module": module, "values": vals, "persist": persist})
        if "error" in result:
            return f"Error: {result['error']}"
        return _settings_reply(f"'{module}' preferences:", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_preferences(ctx: Context, confirm: bool = False) -> str:
    """
    Write Blender's user preferences to userpref.blend (wm.save_userpref).
    Refuses without confirm=True and reports preferences.is_dirty and
    use_preferences_save so you can decide.

    Parameters:
    - confirm: Must be True to actually save
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("save_preferences", {"confirm": confirm})
        if "error" in result:
            extra = ""
            if "preferences_dirty" in result:
                extra = (f" (preferences_dirty={result.get('preferences_dirty')}, "
                         f"use_preferences_save={result.get('use_preferences_save')})")
            return f"Error: {result['error']}{extra}"
        return (f"Preferences {'saved to userpref.blend' if result.get('persisted') else 'NOT saved'}; "
                f"preferences_dirty={result.get('preferences_dirty')}, "
                f"use_preferences_save={result.get('use_preferences_save')}.")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_workspaces(ctx: Context) -> str:
    """
    List workspaces with the area types each one contains, and the current workspace.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("list_workspaces")
        if "error" in result:
            return f"Error: {result['error']}"
        lines = [f"Current: {result.get('current') or 'none (headless)'}"]
        for ws in result.get("workspaces", []):
            lines.append(f"  {ws.get('name')}: {', '.join(ws.get('areas', [])) or 'no areas'}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_workspace(ctx: Context, name: str) -> str:
    """
    Switch the active workspace, e.g. set_workspace("Layout") to guarantee a 3D
    view for the capture tools. Needs a GUI session.

    Parameters:
    - name: Workspace name (see list_workspaces)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_workspace", {"name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Workspace: {result.get('workspace', name)} (areas: {', '.join(result.get('areas', []))})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_addon_settings(ctx: Context) -> str:
    """
    Read the BlenderMCP add-on's own preferences: port, autostart_server and
    which API keys are set (keys are never echoed back; each shows set true/false).
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_addon_settings")
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_addon_settings(ctx: Context, values: str, persist: bool = False) -> str:
    """
    Change the BlenderMCP add-on's own preferences.

    Parameters:
    - values: JSON object, e.g. '{"port": 9877, "autostart_server": true,
              "hyper3d_api_key": "..."}'. API keys are write-only: the reply says
              set true/false per key, never the value. A port change applies when
              the add-on's server is restarted (Disconnect / Connect).
    - persist: True also saves userpref.blend (consent rule; default False)
    """
    vals = _parse_json_object(values, "values")
    if isinstance(vals, str):
        return vals
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_addon_settings", {"values": vals, "persist": persist})
        if "error" in result:
            return f"Error: {result['error']}"
        msg = _settings_reply(
            f"Add-on settings: port={result.get('port')} autostart_server={result.get('autostart_server')} "
            f"keys set={result.get('keys')}.", result)
        if result.get("note"):
            msg += f" {result['note']}."
        return msg
    except Exception as e:
        return f"Error: {e}"


# ─── Primitives & object management ─────────────────────────────────────────

@mcp.tool()
def add_primitive(
    ctx: Context,
    primitive_type: str = "cube",
    location: str = "0,0,0",
    size: float = 2.0,
    name: str = None,
    rotation: str = "0,0,0",
) -> str:
    """
    Add a standard mesh primitive to the Blender scene.

    Parameters:
    - primitive_type: cube, plane, circle, sphere, ico_sphere, cylinder, cone, torus, monkey
    - location: Comma-separated x,y,z (default "0,0,0")
    - size: Overall size in Blender units (default 2.0)
    - name: Optional name for the new object
    - rotation: Comma-separated x,y,z rotation in degrees (default "0,0,0")
    """
    try:
        import math
        blender = get_blender_connection()
        loc = [float(v) for v in location.split(",")]
        rot = [math.radians(float(v)) for v in rotation.split(",")]
        result = blender.send_command("add_primitive", {
            "primitive_type": primitive_type, "location": loc, "size": size,
            "name": name, "rotation": rot,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Added {primitive_type} '{result['name']}' at {result['location']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_object(ctx: Context, name: str) -> str:
    """
    Delete an object from the scene and purge orphaned mesh/material data.

    Parameters:
    - name: Object name to delete
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("delete_object", {"name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Deleted '{name}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def duplicate_object(
    ctx: Context,
    name: str,
    new_name: str = None,
    offset: str = "0.5,0.5,0",
    linked: bool = False,
) -> str:
    """
    Duplicate an object.

    Parameters:
    - name: Source object name
    - new_name: Name for the duplicate (auto-assigned if omitted)
    - offset: Comma-separated x,y,z displacement from original (default "0.5,0.5,0")
    - linked: If True, shares mesh data with original (instance); False = full copy
    """
    try:
        blender = get_blender_connection()
        off = [float(v) for v in offset.split(",")]
        result = blender.send_command("duplicate_object", {
            "name": name, "new_name": new_name, "offset": off, "linked": linked,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Duplicated '{name}' → '{result['duplicate']}' "
                f"at {result['location']} ({'linked' if linked else 'independent'})")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def join_objects(ctx: Context, names: str, result_name: str = None) -> str:
    """
    Join multiple mesh objects into one.

    Parameters:
    - names: Comma-separated list of object names to join
    - result_name: Name for the joined object (defaults to the first object's name)
    """
    try:
        blender = get_blender_connection()
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        result = blender.send_command("join_objects", {
            "names": name_list, "result_name": result_name,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Joined {result['merged_count']} objects → '{result['result']}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def separate_mesh(ctx: Context, name: str, method: str = "LOOSE") -> str:
    """
    Separate a mesh object into multiple objects.

    Parameters:
    - name: Mesh object name
    - method: LOOSE (by disconnected geometry), MATERIAL (by material slot),
              SELECTED (by face selection)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("separate_mesh", {"name": name, "method": method})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Separated '{name}' by {method}. New objects: {result['new_objects']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def rename_object(ctx: Context, old_name: str, new_name: str) -> str:
    """
    Rename an object and its mesh data block.

    Parameters:
    - old_name: Current object name
    - new_name: Desired new name
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("rename_object", {
            "old_name": old_name, "new_name": new_name,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Renamed '{old_name}' → '{result['new_name']}'"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_origin(ctx: Context, name: str, origin_type: str = "ORIGIN_GEOMETRY") -> str:
    """
    Set an object's origin point.

    Parameters:
    - name: Object name
    - origin_type: ORIGIN_GEOMETRY (centre of mesh),
                   ORIGIN_CURSOR (3D cursor position),
                   ORIGIN_CENTER_OF_MASS,
                   ORIGIN_CENTER_OF_VOLUME
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_origin", {
            "name": name, "origin_type": origin_type,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Origin of '{name}' set ({origin_type}). New location: {result['new_location']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def snap_to_ground(ctx: Context, name: str, ground_z: float = 0.0) -> str:
    """
    Move an object so its lowest bounding-box point rests on the ground plane.

    Parameters:
    - name: Object name
    - ground_z: Z value of the ground plane (default 0.0)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("snap_to_ground", {"name": name, "ground_z": ground_z})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"'{name}' snapped to ground Z={ground_z}. New Z origin: {result['location_z']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_smooth_shading(
    ctx: Context,
    name: str,
    smooth: bool = True,
    auto_smooth: bool = True,
    angle: float = 30.0,
) -> str:
    """
    Toggle smooth or flat shading on a mesh object.

    Parameters:
    - name: Mesh object name
    - smooth: True for smooth shading, False for flat
    - auto_smooth: Enable auto-smooth (smooths only edges below angle threshold)
    - angle: Auto-smooth threshold in degrees (default 30°)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_smooth_shading", {
            "name": name, "smooth": smooth, "auto_smooth": auto_smooth, "angle": angle,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        mode = "smooth" if smooth else "flat"
        return f"'{name}' set to {mode} shading" + (f" (auto-smooth {angle}°)" if smooth and auto_smooth else "")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def parent_object(
    ctx: Context,
    child_name: str,
    parent_name: str,
    keep_transform: bool = True,
    parent_type: str = "OBJECT",
    bone: str = None,
) -> str:
    """
    Parent one object to another, creating a hierarchy. For binding a mesh to an
    armature with weights use bind_armature instead.

    Parameters:
    - child_name: Object that becomes the child
    - parent_name: Object that becomes the parent
    - keep_transform: Preserve the child's world-space position (default True)
    - parent_type: OBJECT (default) or BONE (parent to one bone of an armature)
    - bone: Bone name, required when parent_type is BONE
    """
    try:
        pt = (parent_type or "OBJECT").strip().upper()
        if pt == "BONE" and not bone:
            return "Error: parent_type=BONE needs a bone name"
        blender = get_blender_connection()
        result = blender.send_command("parent_object", {
            "child_name": child_name, "parent_name": parent_name,
            "keep_transform": keep_transform, "parent_type": pt, "bone": bone,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Parented '{result.get('child', child_name)}' -> '{result.get('parent', parent_name)}'"
                f" ({result.get('parent_type', pt)}" + (f", bone '{result.get('bone', bone)}'" if result.get("bone") or pt == "BONE" else "") + ")")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def select_objects(
    ctx: Context,
    names: str = None,
    action: str = "SELECT",
    obj_type: str = None,
) -> str:
    """
    Select or deselect objects by name list and/or type.

    Parameters:
    - names: Comma-separated object names (if omitted, applies to all or filtered by type)
    - action: SELECT, DESELECT, TOGGLE
    - obj_type: Filter by type when names is omitted: MESH, CAMERA, LIGHT, CURVE, etc.
    """
    try:
        blender = get_blender_connection()
        name_list = [n.strip() for n in names.split(",") if n.strip()] if names else None
        result = blender.send_command("select_objects", {
            "names": name_list, "action": action, "obj_type": obj_type,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        if "selected_count" in result:
            return f"{action} all: {result['selected_count']} objects selected"
        return f"{action}: {result.get('names', [])}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def align_objects(
    ctx: Context,
    names: str,
    axis: str = "X",
    align_to: str = "FIRST",
) -> str:
    """
    Align multiple objects' origins along one axis.

    Parameters:
    - names: Comma-separated object names
    - axis: X, Y, or Z
    - align_to: FIRST, LAST, MIN, MAX, AVERAGE
    """
    try:
        blender = get_blender_connection()
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        result = blender.send_command("align_objects", {
            "names": name_list, "axis": axis, "align_to": align_to,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Aligned {len(name_list)} objects on {axis}-axis to "
                f"{align_to} ({result['value']:.4f})")
    except Exception as e:
        return f"Error: {e}"


# ─── Materials ────────────────────────────────────────────────────────────────

@mcp.tool()
def create_material(
    ctx: Context,
    name: str,
    base_color: str = "0.8,0.8,0.8",
    metallic: float = 0.0,
    roughness: float = 0.5,
    emission_color: str = None,
    emission_strength: float = 1.0,
    alpha: float = 1.0,
    assign_to: str = None,
) -> str:
    """
    Create (or replace) a PBR material using Principled BSDF.

    Parameters:
    - name: Material name
    - base_color: Comma-separated r,g,b in 0..1 (default "0.8,0.8,0.8")
    - metallic: 0.0 (dielectric) to 1.0 (fully metallic)
    - roughness: 0.0 (mirror) to 1.0 (fully rough)
    - emission_color: Comma-separated r,g,b to enable glow (e.g. "1,0.5,0")
    - emission_strength: Emission multiplier (default 1.0)
    - alpha: Opacity 0..1 (values < 1 enable alpha blending)
    - assign_to: Object name to auto-assign this material to (slot 0)
    """
    try:
        blender = get_blender_connection()
        bc = [float(v) for v in base_color.split(",")]
        ec = [float(v) for v in emission_color.split(",")] if emission_color else None
        result = blender.send_command("create_material", {
            "name": name, "base_color": bc, "metallic": metallic,
            "roughness": roughness, "emission_color": ec,
            "emission_strength": emission_strength, "alpha": alpha,
            "assign_to": assign_to,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Created material '{result['material']}'"
        if assign_to:
            msg += f" and assigned to '{assign_to}'"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def assign_material(
    ctx: Context,
    object_name: str,
    material_name: str,
    slot: int = 0,
) -> str:
    """
    Assign an existing material to an object's material slot.

    Parameters:
    - object_name: Target object
    - material_name: Material to assign (must already exist)
    - slot: Material slot index (default 0)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("assign_material", {
            "object_name": object_name, "material_name": material_name, "slot": slot,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Assigned '{material_name}' to '{object_name}' slot {slot}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def load_texture(
    ctx: Context,
    material_name: str,
    image_path: str,
    texture_slot: str = "Base Color",
    uv_scale: float = 1.0,
) -> str:
    """
    Load an image file and wire it into a material's texture slot.

    Parameters:
    - material_name: Target material (must have a Principled BSDF node)
    - image_path: Absolute path to the image file
    - texture_slot: 'Base Color', 'Roughness', 'Metallic', 'Normal', 'Emission Color'
    - uv_scale: Uniform UV tiling scale (default 1.0)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("load_texture", {
            "material_name": material_name, "image_path": image_path,
            "texture_slot": texture_slot, "uv_scale": uv_scale,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Loaded '{result['image']}' → '{material_name}' "
                f"slot '{result['texture_slot']}'")
    except Exception as e:
        return f"Error: {e}"


# ─── Modifiers ────────────────────────────────────────────────────────────────

@mcp.tool()
def add_modifier(
    ctx: Context,
    name: str,
    modifier_type: str,
    modifier_name: str = None,
    params: str = None,
) -> str:
    """
    Add a modifier to an object with optional parameters.

    Parameters:
    - name: Object name
    - modifier_type: MIRROR, BEVEL, ARRAY, SOLIDIFY, SUBSURF, DECIMATE,
                     DISPLACE, SHRINKWRAP, WIREFRAME, SKIN, LATTICE, CAST, etc.
    - modifier_name: Display name for the modifier (auto-generated if omitted)
    - params: JSON string of modifier properties, e.g.:
        MIRROR:   '{"use_axis": [true, false, false], "use_clip": true}'
        BEVEL:    '{"width": 0.1, "segments": 3}'
        ARRAY:    '{"count": 4, "relative_offset_displace": [1, 0, 0]}'
        SOLIDIFY: '{"thickness": 0.05}'
        SUBSURF:  '{"levels": 2, "render_levels": 3}'

    Returns the modifier name so you can reference it later with apply_modifier.
    """
    try:
        blender = get_blender_connection()
        props = json.loads(params) if params else {}
        if not isinstance(props, dict):
            return "Error: params must be a JSON object, e.g. '{\"width\": 0.1}'"
        result = blender.send_command("add_modifier", {
            "name": name, "modifier_type": modifier_type,
            "modifier_name": modifier_name, "props": props,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Added {result['type']} modifier '{result['modifier']}' to '{name}'"
        if result.get("unset"):
            msg += f". Could not set: {result['unset']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def boolean_operation(
    ctx: Context,
    target_name: str,
    cutter_name: str,
    operation: str = "DIFFERENCE",
    solver: str = "EXACT",
    apply: bool = True,
) -> str:
    """
    Perform a boolean operation between two mesh objects.

    Parameters:
    - target_name: Object to modify (the base mesh)
    - cutter_name: Object used as the cutting/joining tool
    - operation: DIFFERENCE (subtract), UNION (merge), INTERSECT (keep overlap)
    - solver: EXACT (default), FAST or FLOAT (the fast solver: FAST on Blender 4.x,
              FLOAT on 5.x, either name accepted), MANIFOLD (Blender 5.2+ only).
              The reply reports the solver id actually used.
    - apply: If True (default), applies the modifier and deletes the cutter object
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("boolean_operation", {
            "target_name": target_name, "cutter_name": cutter_name,
            "operation": operation, "solver": solver, "apply": apply,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Boolean {operation}: '{target_name}' ∩/− '{cutter_name}' "
                f"({'applied' if apply else 'modifier added only'})"
                + (f", solver={result['solver']}" if result.get("solver") else ""))
    except Exception as e:
        return f"Error: {e}"


# ─── Render settings ──────────────────────────────────────────────────────────

@mcp.tool()
def set_render_settings(
    ctx: Context,
    engine: str = None,
    width: int = None,
    height: int = None,
    samples: int = None,
    output_path: str = None,
    file_format: str = None,
    transparent_background: bool = None,
    fps: float = None,
    fps_base: float = None,
    frame_start: int = None,
    frame_end: int = None,
    resolution_percentage: int = None,
    color_mode: str = None,
    color_depth: str = None,
    compression: int = None,
    denoise: bool = None,
    device: str = None,
    use_persistent_data: bool = None,
    use_simplify: bool = None,
    simplify_subdivision: int = None,
) -> str:
    """
    Configure scene render settings. Only the parameters you pass are changed.
    Every value is validated against Blender's own property definitions (the
    same path as set_settings(scope="RENDER")); rejected values are listed.

    Parameters:
    - engine: CYCLES, BLENDER_EEVEE (alias EEVEE; the 4.x id BLENDER_EEVEE_NEXT
              is accepted), BLENDER_WORKBENCH. The reply reports the engine id
              actually set.
    - width / height: Render resolution in pixels
    - samples: Number of render samples (affects quality/noise)
    - output_path: File path for saved renders (e.g. "C:/renders/frame_####.png")
    - file_format: PNG, JPEG, OPEN_EXR (EXR accepted), TIFF, BMP, FFMPEG (video)
    - transparent_background: True to render with alpha instead of background colour
    - fps / fps_base: Frame rate (fps=24, fps_base=1.001 for 23.976)
    - frame_start / frame_end: Scene frame range
    - resolution_percentage: 1-100 (render at a fraction of width x height)
    - color_mode: BW, RGB or RGBA
    - color_depth: 8 or 16 (PNG/TIFF), 16 or 32 (OPEN_EXR), as a string
    - compression: PNG compression 0-100
    - denoise: Cycles denoising on/off
    - device: Cycles render device, CPU or GPU
    - use_persistent_data: Keep render data between frames (faster animations)
    - use_simplify / simplify_subdivision: Simplify toggle and max subdivision level
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_render_settings", {
            "engine": engine, "width": width, "height": height,
            "samples": samples, "output_path": output_path,
            "file_format": file_format,
            "transparent_background": transparent_background,
            "fps": fps, "fps_base": fps_base,
            "frame_start": frame_start, "frame_end": frame_end,
            "resolution_percentage": resolution_percentage,
            "color_mode": color_mode, "color_depth": color_depth,
            "compression": compression, "denoise": denoise, "device": device,
            "use_persistent_data": use_persistent_data,
            "use_simplify": use_simplify, "simplify_subdivision": simplify_subdivision,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = (f"Render settings: engine={result.get('engine', engine)}, "
               f"resolution={result.get('resolution')}, "
               f"transparent={result.get('transparent')}, "
               f"output={result.get('output')}")
        if result.get("set"):
            msg += f". Set: {result['set']}"
        if result.get("unset"):
            msg += f". Could not set: {result['unset']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


# ─── Rigging ─────────────────────────────────────────────────────────────────

# ─── Rigging: armatures, skinning, pose, constraints (B1-S L2) ───────────────
# Rigging doc 3.1 Tier 1, non-image tools. Rotations from the client are DEGREES
# (converted by the add-on, same as add_keyframe). Lists are comma strings, structured
# data JSON strings, parsed here and sent as native types.

def _parse_json_any(text: str, label: str):
    """Parse a JSON argument (object or list); returns the value or an 'Error: ...' string."""
    if text is None or str(text).strip() == "":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return f"Error: {label} is not valid JSON ({e.msg} at position {e.pos})"


def _parse_vec3(text, label: str):
    """'x,y,z' -> [x, y, z] floats; None stays None; returns an 'Error: ...' string on bad input."""
    if text is None or str(text).strip() == "":
        return None
    try:
        parts = [float(v) for v in str(text).split(",") if v.strip()]
    except ValueError:
        return f"Error: {label} must be three numbers 'x,y,z'"
    if len(parts) != 3:
        return f"Error: {label} must be three numbers 'x,y,z', got {len(parts)}"
    return parts


def _set_unset_reply(prefix: str, result: dict) -> str:
    msg = prefix
    if result.get("set"):
        msg += f" Set: {result['set']}."
    if result.get("unset"):
        msg += f" Could not set: {result['unset']}."
    if result.get("warnings"):
        msg += f" Warnings: {result['warnings']}."
    return msg.strip()


@mcp.tool()
def create_armature(
    ctx: Context,
    name: str,
    location: str = "0,0,0",
    display_type: str = "OCTAHEDRAL",
    show_in_front: bool = True,
    bones: str = None,
) -> str:
    """
    Create an armature object (and its data) in the active collection, optionally
    with bones in one go.

    Parameters:
    - name: Armature object name
    - location: "x,y,z" (default origin)
    - display_type: OCTAHEDRAL (default), STICK, BBONE, ENVELOPE or WIRE
    - show_in_front: Draw the bones through meshes (default True)
    - bones: Optional JSON list exactly as add_bones takes it, e.g.
             '[{"name": "root", "head": [0,0,0], "tail": [0,0,1]},
               {"name": "spine", "head": [0,0,1], "tail": [0,0,2], "parent": "root", "connected": true}]'

    Reply: armature name and bone count.
    """
    loc = _parse_vec3(location, "location")
    if isinstance(loc, str):
        return loc
    bone_list = _parse_json_any(bones, "bones") if bones else None
    if isinstance(bone_list, str):
        return bone_list
    if bone_list is not None and not isinstance(bone_list, list):
        return "Error: bones must be a JSON list of bone objects"
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_armature", {
            "name": name, "location": loc, "display_type": display_type,
            "show_in_front": show_in_front, "bones": bone_list,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _set_unset_reply(
            f"Created armature '{result.get('name', name)}' with {result.get('bone_count', 0)} bone(s).", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def add_bones(ctx: Context, armature: str, bones: str) -> str:
    """
    Add bones to an armature in one edit-mode session.

    Parameters:
    - armature: Armature object name
    - bones: JSON list of {"name", "head": [x,y,z], "tail": [x,y,z], "parent"?,
             "connected"?: bool, "roll"?: degrees, "deform"?: bool, "inherit_rotation"?: bool}.
             A parent may be an existing bone or an earlier entry in the same list.
             connected=true snaps the head to the parent's tail (reported).
             Name collisions get Blender's .001 suffix (reported).

    Reply: created names, snapped bones, warnings.
    """
    bone_list = _parse_json_any(bones, "bones")
    if isinstance(bone_list, str):
        return bone_list
    if not isinstance(bone_list, list) or not bone_list:
        return "Error: bones must be a non-empty JSON list of bone objects"
    try:
        blender = get_blender_connection()
        result = blender.send_command("add_bones", {"armature": armature, "bones": bone_list})
        if "error" in result:
            return (f"Error: {result['error']}"
                    + (f" (created before the error: {result['created_before_error']})" if result.get("created_before_error") else ""))
        msg = f"Added {len(result.get('created', []))} bone(s) to '{armature}' ({result.get('bone_count')} total): {result.get('created')}"
        if result.get("snapped"):
            msg += f". Snapped to parent tail: {result['snapped']}"
        if result.get("warnings"):
            msg += f". Warnings: {result['warnings']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_armature_info(
    ctx: Context,
    armature: str,
    include_pose: bool = False,
    space: str = "WORLD",
    bone_filter: str = None,
) -> str:
    """
    THE perceive tool for a rig: every bone in hierarchy order with parent,
    children, head, tail, length, roll (degrees), connected, deform, bone
    collections and constraints, plus pose_position, display_type, action and the
    bone-collection summary.

    Parameters:
    - armature: Armature object name
    - include_pose: Add per-bone pose (location, rotation_mode, rotation in
                    degrees or quaternion, scale, world head/tail)
    - space: WORLD (default) or ARMATURE for head/tail coordinates
    - bone_filter: Glob on bone names, e.g. "arm.L*" or "*spine*"
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_armature_info", {
            "armature": armature, "include_pose": include_pose, "space": space, "bone_filter": bone_filter,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_bone_properties(
    ctx: Context,
    armature: str,
    bone: str,
    head: str = None,
    tail: str = None,
    roll: float = None,
    parent: str = None,
    connected: bool = None,
    deform: bool = None,
    inherit_rotation: bool = None,
    inherit_scale: str = None,
    new_name: str = None,
    envelope_distance: float = None,
    bbone_segments: int = None,
) -> str:
    """
    Change one bone's rest properties. Only the parameters you pass are set.

    Parameters:
    - armature / bone: Armature object and bone name
    - head / tail: "x,y,z" in armature space
    - roll: Degrees
    - parent: Parent bone name ("" to clear)
    - connected: Snap the head to the parent's tail and keep it there
    - deform: Whether the bone deforms skinned meshes
    - inherit_rotation: Bool
    - inherit_scale: FULL, FIX_SHEAR, ALIGNED, AVERAGE, NONE, NONE_LEGACY
    - new_name: Rename the bone (vertex groups are not renamed; see rename_bones)
    - envelope_distance: Envelope radius
    - bbone_segments: Bendy-bone segments (1 = plain bone)

    Reply: set list and unset {prop: reason}, like add_modifier.
    """
    h = _parse_vec3(head, "head"); t = _parse_vec3(tail, "tail")
    if isinstance(h, str):
        return h
    if isinstance(t, str):
        return t
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_bone_properties", {
            "armature": armature, "bone": bone, "head": h, "tail": t, "roll": roll,
            "parent": parent, "connected": connected, "deform": deform,
            "inherit_rotation": inherit_rotation, "inherit_scale": inherit_scale,
            "new_name": new_name, "envelope_distance": envelope_distance,
            "bbone_segments": bbone_segments,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return _set_unset_reply(f"Bone '{result.get('bone', bone)}' on '{armature}':", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def delete_bones(ctx: Context, armature: str, bones: str, reparent_children: bool = True) -> str:
    """
    Delete bones in edit mode.

    Parameters:
    - armature: Armature object name
    - bones: Comma list of bone names, or one glob like "finger*"
    - reparent_children: Attach children of a deleted bone to its parent (default True)

    Reply: deleted names and reparented names.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("delete_bones", {
            "armature": armature, "bones": bones, "reparent_children": reparent_children,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Deleted {len(result.get('deleted', []))} bone(s) ({result.get('bone_count')} left): {result.get('deleted')}"
        if result.get("reparented"):
            msg += f". Reparented: {result['reparented']}"
        if result.get("unmatched"):
            msg += f". Not found: {result['unmatched']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def bind_armature(
    ctx: Context,
    mesh: str,
    armature: str,
    method: str = "AUTO",
    keep_transform: bool = True,
) -> str:
    """
    Skin a mesh to an armature (parent + Armature modifier + vertex groups).

    Parameters:
    - mesh: Mesh object name
    - armature: Armature object name
    - method: AUTO (automatic weights, default), ENVELOPE (envelope weights),
              EMPTY_GROUPS (one empty group per bone, paint later),
              NAME (modifier only; keeps the groups the mesh already has)
    - keep_transform: Preserve the mesh's world transform (default True)

    Reply: modifier name, vertex-group count, groups with no weighted vertices.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("bind_armature", {
            "mesh": mesh, "armature": armature, "method": method, "keep_transform": keep_transform,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = (f"Bound '{mesh}' to '{armature}' ({result.get('method', method)}, parent_type {result.get('parent_type')}): "
               f"modifier '{result.get('modifier')}', {result.get('group_count', 0)} vertex group(s)")
        if result.get("empty_groups"):
            msg += f". Groups with zero verts: {result['empty_groups']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_vertex_groups(ctx: Context, mesh: str, include_stats: bool = True) -> str:
    """
    List a mesh's vertex groups: name, index, lock, and with include_stats the
    vertex count, weight min/max/mean and whether a matching bone exists on the
    bound armature.

    Parameters:
    - mesh: Mesh object name
    - include_stats: Compute per-group weight statistics (default True)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_vertex_groups", {"mesh": mesh, "include_stats": include_stats})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_vertex_weights(
    ctx: Context,
    mesh: str,
    indices: str = None,
    group: str = None,
    max_verts: int = 2000,
) -> str:
    """
    Read per-vertex weights: {index: {group: weight}}.

    Parameters:
    - mesh: Mesh object name
    - indices: Comma list of vertex indices to read (default: all, up to max_verts)
    - group: Only vertices that belong to this group
    - max_verts: Truncate after this many vertices (default 2000; truncation is reported)
    """
    try:
        idx = None
        if indices:
            try:
                idx = [int(v) for v in _split_csv(indices)]
            except ValueError:
                return "Error: indices must be a comma list of integers"
        blender = get_blender_connection()
        result = blender.send_command("get_vertex_weights", {
            "mesh": mesh, "indices": idx, "group": group, "max_verts": max_verts,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_vertex_weights(
    ctx: Context,
    mesh: str,
    group: str,
    weights: str,
    mode: str = "REPLACE",
    create_group: bool = True,
) -> str:
    """
    Write vertex weights into one group.

    Parameters:
    - mesh: Mesh object name
    - group: Vertex group name
    - weights: JSON object {"index": weight, ...} or list of [index, weight] pairs
    - mode: REPLACE (default), ADD or SUBTRACT (VertexGroup.add semantics)
    - create_group: Create the group if it does not exist (default True)

    Reply: vertices written, whether the group was created.
    """
    w = _parse_json_any(weights, "weights")
    if isinstance(w, str):
        return w
    if isinstance(w, dict):
        try:
            pairs = [[int(k), float(v)] for k, v in w.items()]
        except (TypeError, ValueError):
            return "Error: weights object keys must be vertex indices and values numbers"
    elif isinstance(w, list):
        try:
            pairs = [[int(p[0]), float(p[1])] for p in w]
        except (TypeError, ValueError, IndexError):
            return "Error: weights list entries must be [index, weight] pairs"
    else:
        return "Error: weights must be a JSON object or list"
    if not pairs:
        return "Error: weights is empty"
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_vertex_weights", {
            "mesh": mesh, "group": group, "weights": pairs, "mode": mode, "create_group": create_group,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Wrote {result.get('written', len(pairs))} weight(s) into '{group}' on '{mesh}' ({result.get('mode', mode)})"
                + (" (group created)" if result.get("group_created") else "") + ".")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def find_unweighted_vertices(
    ctx: Context,
    mesh: str,
    tolerance: float = 0.001,
    render: bool = False,
    angle: str = "front",
):
    """
    Find vertices whose total deform weight is below tolerance, plus vertices with
    any weight above 1 or below 0. Returns the numbers, or with render=True an
    image of those vertices selected in edit mode from a named viewport angle
    (mode and selection restored; needs a GUI session).

    Parameters:
    - mesh: Mesh object name
    - tolerance: Total-weight threshold (default 0.001)
    - render: True returns an image instead of the numbers (default False)
    - angle: front, back, left, right, top, bottom, iso_front_right, iso_front_left

    Reply (render=False): counts and up to 500 indices per category as JSON.
    """
    if render and angle not in _VALID_ANGLES:
        raise Exception(f"Unknown angle '{angle}'. Valid angles: {', '.join(_VALID_ANGLES)}")
    try:
        blender = get_blender_connection()
        result = blender.send_command("find_unweighted_vertices", {
            "mesh": mesh, "tolerance": tolerance, "render": bool(render), "angle": angle,
        })
        if "error" in result:
            if render:
                raise Exception(result["error"])
            return f"Error: {result['error']}"
        if not render:
            return json.dumps({k: v for k, v in result.items() if k not in ("filepath", "image")}, indent=2)
        img = result.get("image") or {}
        if "error" in img:
            raise Exception(img["error"])
        fp = img.get("filepath") or result.get("filepath")
        if not fp or not os.path.exists(fp):
            raise Exception(f"the add-on reported no capture file ({result.get('unweighted_count')} unweighted vertex(es) found)")
        return _safe_image_return(_read_and_remove(fp))
    except Exception as e:
        if render:
            logger.error(f"find_unweighted_vertices error: {e}")
            raise Exception(f"find_unweighted_vertices failed: {e}") from e
        return f"Error: {e}"


@mcp.tool()
def set_pose(
    ctx: Context,
    armature: str,
    bones: str,
    rotation_mode: str = "XYZ",
    space: str = "POSE",
    keyframe: bool = False,
    frame: int = None,
) -> str:
    """
    Pose bones by writing location / rotation / scale, optionally keying them.

    Parameters:
    - armature: Armature object name
    - bones: JSON object {"bone": {"location": [x,y,z], "rotation": [x,y,z] degrees
             or [w,x,y,z] quaternion, "scale": [x,y,z]}, ...}; each key optional
    - rotation_mode: XYZ (Euler degrees, default) or QUATERNION; set on each bone
                     before writing
    - space: POSE (default) - values are bone-local pose values
    - keyframe: Also insert keyframes for the channels written (default False)
    - frame: Frame for the keyframes (default: current)

    Note: a pose written on an animated bone is overwritten by the animation at the
    next update unless keyframe=True.

    Reply: bones written, rotation modes changed.
    """
    b = _parse_json_any(bones, "bones")
    if isinstance(b, str):
        return b
    if not isinstance(b, dict) or not b:
        return "Error: bones must be a non-empty JSON object {bone: {location/rotation/scale}}"
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_pose", {
            "armature": armature, "bones": b, "rotation_mode": rotation_mode,
            "space": space, "keyframe": keyframe, "frame": frame,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        msg = f"Posed {len(result.get('written', []))} bone(s) on '{armature}': {result.get('written')}"
        rmc = result.get("rotation_mode_changed")
        if isinstance(rmc, dict) and rmc:
            msg += ". Rotation mode changed: " + ", ".join(
                f"{b} {c.get('from')}->{c.get('to')}" if isinstance(c, dict) else str(b) for b, c in rmc.items())
        if result.get("keyframe"):
            msg += f". Keyed at frame {result.get('frame', frame)}"
        if result.get("unset"):
            msg += f". Could not set: {result['unset']}"
        if result.get("warning"):
            msg += f". Warning: {result['warning']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_pose(ctx: Context, armature: str, bones: str = None, space: str = "WORLD") -> str:
    """
    Read the current pose: per bone location, rotation_mode, rotation (degrees
    or quaternion), scale, world head and tail.

    Parameters:
    - armature: Armature object name
    - bones: Comma list of bone names (default: all)
    - space: WORLD (default) or ARMATURE for head/tail
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_pose", {
            "armature": armature, "bones": _split_csv(bones) or None, "space": space,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def reset_pose(ctx: Context, armature: str, bones: str = None, transforms: str = "ALL") -> str:
    """
    Reset pose transforms to rest (identity), without needing POSE-mode operators.

    Parameters:
    - armature: Armature object name
    - bones: Comma list of bone names (default: all)
    - transforms: ALL (default), LOCATION, ROTATION or SCALE
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("reset_pose", {
            "armature": armature, "bones": _split_csv(bones) or None, "transforms": transforms,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Reset {result.get('transforms', transforms)} on {len(result.get('reset', []))} bone(s) of '{armature}'."
                + (f" Missing: {result['missing']}" if result.get("missing") else ""))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def add_constraint(
    ctx: Context,
    owner: str,
    constraint_type: str,
    bone: str = None,
    name: str = None,
    params: str = None,
) -> str:
    """
    Add a constraint to an object or a pose bone.

    Parameters:
    - owner: Object name (the armature when bone is given)
    - constraint_type: IK, COPY_LOCATION, COPY_ROTATION, COPY_TRANSFORMS, DAMPED_TRACK,
                       TRACK_TO, STRETCH_TO, LIMIT_ROTATION, LIMIT_LOCATION, CHILD_OF, ...
                       (the error lists the valid types)
    - bone: Pose bone name to own the constraint (default: the object)
    - name: Constraint name (default: Blender's)
    - params: JSON object of constraint properties, e.g. for IK:
              '{"target": "Rig", "subtarget": "ik_hand.L", "pole_target": "Rig",
                "pole_subtarget": "pole_elbow.L", "chain_count": 2, "pole_angle": -90}'.
              Object-pointer properties take object names; subtarget takes a bone
              name; angles are degrees.

    Reply: constraint name, set list and unset {prop: reason}, like add_modifier.
    """
    p = _parse_json_object(params, "params") if params else {}
    if isinstance(p, str):
        return p
    try:
        blender = get_blender_connection()
        result = blender.send_command("add_constraint", {
            "owner": owner, "bone": bone, "constraint_type": constraint_type, "name": name, "params": p,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        where = f"'{owner}'" + (f" bone '{bone}'" if bone else "")
        return _set_unset_reply(
            f"Added {result.get('type', constraint_type)} constraint '{result.get('constraint')}' to {where}.", result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_constraints(ctx: Context, owner: str, bone: str = None) -> str:
    """
    List the constraints on an object or a pose bone: name, type, target,
    subtarget, influence, mute, plus type-specific keys (IK: chain_count,
    pole_target, pole_subtarget, pole_angle_deg, use_tail).

    Parameters:
    - owner: Object name (the armature when bone is given)
    - bone: Pose bone name (default: the object's own constraints)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_constraints", {"owner": owner, "bone": bone})
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def remove_constraint(ctx: Context, owner: str, name: str, bone: str = None) -> str:
    """
    Remove a constraint by name from an object or a pose bone.

    Parameters:
    - owner: Object name (the armature when bone is given)
    - name: Constraint name (see get_constraints)
    - bone: Pose bone name (default: the object)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("remove_constraint", {"owner": owner, "bone": bone, "name": name})
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Removed constraint '{result.get('removed', name)}' from '{owner}'" + (f" bone '{bone}'" if bone else "")
                + f". Remaining: {result.get('remaining', [])}")
    except Exception as e:
        return f"Error: {e}"


# ─── Animation ────────────────────────────────────────────────────────────────

# set_scene_frame_range from the rigging doc is NOT added: set_frame_range (B0, C11)
# already covers it and Ton's handler accepts both spellings.

@mcp.tool()
def set_keyframes(
    ctx: Context,
    target: str,
    data_path: str,
    keys: str,
    bone: str = None,
    replace: bool = True,
) -> str:
    """
    Insert many keyframes on one property in a single call.

    Parameters:
    - target: Object name (the armature when bone is given)
    - data_path: 'location', 'rotation_euler', 'scale', or any animatable path
    - keys: JSON list of [frame, value] pairs or objects
            {"frame", "value", "interpolation"?, "easing"?}. value follows
            add_keyframe's rules: a number for a scalar, [x,y,z] for a vector,
            rotations in degrees. interpolation BEZIER/LINEAR/CONSTANT, easing
            AUTO/EASE_IN/EASE_OUT/EASE_IN_OUT.
    - bone: Pose bone name to key instead of the object (rotation_mode switched
            to XYZ for Euler paths, reported)
    - replace: Overwrite existing keys on those frames (default True)

    Reply: keyed count, action name, fcurve path.
    """
    k = _parse_json_any(keys, "keys")
    if isinstance(k, str):
        return k
    if not isinstance(k, list) or not k:
        return "Error: keys must be a non-empty JSON list of [frame, value] pairs or key objects"
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_keyframes", {
            "target": target, "bone": bone, "data_path": data_path, "keys": k, "replace": replace,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        where = f"'{target}'" + (f" bone '{bone}'" if bone else "")
        keyed = result.get("keyed")
        count = result.get("keyed_count", len(keyed) if isinstance(keyed, (list, tuple)) else (keyed if isinstance(keyed, int) else len(k)))
        msg = (f"Keyed {count} frame(s) on {where}.{data_path} "
               f"(action '{result.get('action')}', fcurves {result.get('fcurves', [data_path])})")
        rmc = result.get("rotation_mode_changed")
        if isinstance(rmc, dict) and rmc:
            msg += f". Rotation mode switched {rmc.get('from')} -> {rmc.get('to')}"
        elif rmc:
            msg += ". Rotation mode switched to XYZ"
        if result.get("skipped"):
            msg += f". Skipped: {result['skipped']}"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_animation_info(
    ctx: Context,
    target: str = None,
    include_keys: bool = False,
    max_keys: int = 200,
) -> str:
    """
    Animation overview. Without target: scene fps, frame range, current frame and
    every action with its users. With target: its action, fcurves (data_path,
    index, keyframe_count, frame_range), NLA tracks and shape-key action; with
    include_keys each fcurve lists [frame, value, interpolation] up to max_keys.
    Works on both the legacy F-curve API (4.x) and slotted actions (5.x).

    Parameters:
    - target: Object name (default: scene summary)
    - include_keys: Include the keyframes per fcurve (default False)
    - max_keys: Cap per fcurve when include_keys (default 200)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_animation_info", {
            "target": target, "include_keys": include_keys, "max_keys": max_keys,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def bake_action(
    ctx: Context,
    armature: str,
    start: int = None,
    end: int = None,
    step: int = 1,
    bones: str = None,
    visual_keying: bool = True,
    clear_constraints: bool = False,
    clear_parents: bool = False,
    only_selected: bool = True,
    bake_types: str = "POSE",
) -> str:
    """
    Bake the rig's motion (constraints, IK, drivers) into plain keyframes
    (bpy.ops.nla.bake in POSE mode). Do this before exporting to a game engine.

    Parameters:
    - armature: Armature object name
    - start / end: Frame range (default: scene range)
    - step: Frame step (default 1)
    - bones: Comma list of bones to bake (default: all)
    - visual_keying: Bake the evaluated (constrained) transforms (default True)
    - clear_constraints: Remove constraints after baking (default False)
    - clear_parents: Clear bone parents after baking (default False)
    - only_selected: Bake only the listed/selected bones (default True)
    - bake_types: POSE (default), OBJECT, "POSE,OBJECT" or "both"

    Reply: action name, frame range, fcurve count. Mode and selection are restored.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("bake_action", {
            "armature": armature, "start": start, "end": end, "step": step,
            "bones": bones or None, "visual_keying": visual_keying,
            "clear_constraints": clear_constraints, "clear_parents": clear_parents,
            "only_selected": only_selected, "bake_types": bake_types or "POSE",
        })
        if "error" in result:
            return f"Error: {result['error']}"
        fr = result.get("baked_range") or result.get("frame_range")
        bones_done = result.get("bones")
        bones_txt = ("all bones" if bones_done == "all" else f"{len(bones_done)} bone(s)" if isinstance(bones_done, list) else "")
        return (f"Baked '{armature}' into action '{result.get('action')}'"
                + (f" frames {fr[0]}-{fr[1]}" if isinstance(fr, (list, tuple)) and len(fr) == 2 else "")
                + (f" step {result['step']}" if result.get("step") else "")
                + f" ({', '.join(result.get('bake_types', [])) or bake_types}): {result.get('fcurve_count')} fcurve(s)"
                + (f", {bones_txt}" if bones_txt else "")
                + (f", animated: {result['animated_bones']}" if result.get("animated_bones") else "") + ".")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def playblast(
    ctx: Context,
    start: int = None,
    end: int = None,
    step: int = None,
    frames: str = None,
    camera: str = None,
    max_size: int = 640,
    columns: int = 4,
    video_path: str = None,
    overlay: str = None,
) -> Image:
    """
    See motion: capture frames across the range and return one labelled contact
    sheet. In a GUI session each frame is a viewport capture; headless it is an
    EEVEE render of the camera. Optionally also writes an MP4.

    Parameters:
    - start / end: Frame range (default: scene range)
    - step: Frame step; omit for the add-on's largest step that keeps <= 16 tiles
    - frames: Explicit comma list of frames (overrides start/end/step)
    - camera: Camera to look through (default: scene camera / current view)
    - max_size: Tile size in pixels (default 640)
    - columns: Sheet columns (default 4)
    - video_path: Also write an MP4 (FFMPEG H.264) of the full range; Blender appends
                  the frame range to the file stem
    - overlay: bones_in_front, wireframe or weight_paint (see capture_viewport_angle)

    The current frame is restored afterwards.
    """
    try:
        _check_overlay(overlay)
        if frames:
            try:
                [int(f) for f in _split_csv(frames)]
            except ValueError:
                raise Exception("frames must be a comma list of integers")
        blender = get_blender_connection()
        result = blender.send_command("playblast", {
            "start": start, "end": end, "step": step, "frames": frames or None,
            "camera": camera, "max_size": max_size, "columns": columns,
            "video_path": video_path, "overlay": overlay,
        })
        if "error" in result:
            raise Exception(result["error"])
        images = result.get("images") or []
        if isinstance(images, dict):
            images = [{"frame": fr, **(v if isinstance(v, dict) else {"filepath": v})} for fr, v in images.items()]
        shots = [(im.get("frame"), im.get("filepath")) for im in images if isinstance(im, dict)]
        shots = [(f, fp) for f, fp in shots if fp and os.path.exists(fp)]
        if not shots:
            raise Exception(f"No frames were captured (path {result.get('path')}, warnings {result.get('warnings')})")
        try:
            if not _PIL_AVAILABLE or len(shots) == 1:
                with open(shots[0][1], "rb") as f:
                    return _safe_image_return(f.read())
            tiles, labels = [], []
            for fr, fp in shots:
                with PILImage.open(fp) as im:
                    im.load()
                    tiles.append(im.convert("RGB"))
                labels.append(f"f{fr}")
            tile_w = max_size
            tile_h = max(1, int(tile_w * tiles[0].height / max(1, tiles[0].width)))
            sheet = _compose_grid(tiles, columns=max(1, columns), tile_w=tile_w, tile_h=tile_h, labels=labels)
            logger.info(f"playblast: {len(shots)} frames, path={result.get('path')}, video={result.get('video')}, "
                        f"warnings={result.get('warnings')}")
            return _safe_image_return(_pil_to_png_bytes(sheet))
        finally:
            _remove_quiet(*[fp for _, fp in shots])
    except Exception as e:
        logger.error(f"playblast error: {e}")
        raise Exception(f"playblast failed: {e}") from e


@mcp.tool()
def render_weight_map(
    ctx: Context,
    mesh: str,
    group: str,
    angle: str = "front",
    max_size: int = 800,
    show_zero_weights: bool = True,
) -> Image:
    """
    See a vertex group's weights as Blender's weight-paint heat map (blue 0 to
    red 1) from a named viewport angle. Mode and viewport state are restored.

    Parameters:
    - mesh: Mesh object name
    - group: Vertex group to display
    - angle: front, back, left, right, top, bottom, iso_front_right, iso_front_left
    - max_size: Maximum pixel dimension (default 800)
    - show_zero_weights: Draw unweighted vertices in black (default True)
    """
    try:
        if angle not in _VALID_ANGLES:
            raise Exception(f"Unknown angle '{angle}'. Valid angles: {', '.join(_VALID_ANGLES)}")
        blender = get_blender_connection()
        result = blender.send_command("render_weight_map", {
            "mesh": mesh, "group": group, "angle": angle, "max_size": max_size,
            "show_zero_weights": show_zero_weights,
        })
        if "error" in result:
            raise Exception(result["error"])
        fp = result.get("filepath")
        if not fp or not os.path.exists(fp):
            raise Exception("the add-on reported no capture file")
        return _safe_image_return(_read_and_remove(fp))
    except Exception as e:
        logger.error(f"render_weight_map error: {e}")
        raise Exception(f"render_weight_map failed: {e}") from e


@mcp.tool()
def add_keyframe(
    ctx: Context,
    name: str,
    data_path: str = "location",
    frame: int = None,
    value: str = None,
    bone: str = None,
) -> str:
    """
    Insert an animation keyframe on an object or pose-bone property.

    Parameters:
    - name: Object name (the armature when bone is given)
    - data_path: Property to key: 'location', 'rotation_euler', 'scale', or any
                 animatable path like 'data.energy'
    - frame: Frame number (uses current frame if omitted)
    - value: Value(s) to set before keying: "1,2,3" for a vector property such as
             location, or a single number such as "500" for a scalar like data.energy.
             Rotation values are in degrees and converted automatically.
    - bone: Pose bone name to key instead of the object. Keying rotation_euler on a
            bone (or object) in quaternion/axis-angle mode switches it to XYZ
            Euler, and the reply says so.
    """
    try:
        blender = get_blender_connection()
        val = None
        if value is not None and str(value).strip() != "":
            parts = [float(v) for v in str(value).split(",") if v.strip()]
            val = parts[0] if len(parts) == 1 else parts
        result = blender.send_command("add_keyframe", {
            "name": name, "data_path": data_path,
            "frame": frame, "value": val, "bone": bone,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        target = f"'{name}'" + (f" bone '{bone}'" if bone else "")
        msg = f"Keyframe on {target}.{data_path} at frame {result.get('frame')}" + (f" = {val}" if val is not None else "")
        rmc = result.get("rotation_mode_changed")
        if isinstance(rmc, dict):
            msg += f". Rotation mode switched {rmc.get('from')} -> {rmc.get('to')}"
        elif rmc:
            msg += f". Rotation mode switched to {result.get('rotation_mode', 'XYZ')}"
        if result.get("action"):
            msg += f" (action '{result['action']}'" + (f", {result['fcurve_count']} fcurves" if result.get("fcurve_count") is not None else "") + ")"
        return msg
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_frame(ctx: Context, frame: int) -> str:
    """
    Set the current scene frame (scrubs the timeline).

    Parameters:
    - frame: Target frame number
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_frame", {"frame": frame})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Frame set to {result['frame']}"
    except Exception as e:
        return f"Error: {e}"


# ─── Collections ─────────────────────────────────────────────────────────────

@mcp.tool()
def create_collection(
    ctx: Context,
    name: str,
    parent_collection: str = None,
) -> str:
    """
    Create a new collection for scene organisation.

    Parameters:
    - name: Collection name
    - parent_collection: Optional parent collection name (nests inside it)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_collection", {
            "name": name, "parent_collection": parent_collection,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Collection '{result['collection']}' created"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def move_to_collection(
    ctx: Context,
    object_names: str,
    collection_name: str,
) -> str:
    """
    Move objects into a collection (removes them from all other collections).

    Parameters:
    - object_names: Comma-separated object names
    - collection_name: Target collection (must already exist)
    """
    try:
        blender = get_blender_connection()
        names = [n.strip() for n in object_names.split(",") if n.strip()]
        result = blender.send_command("move_to_collection", {
            "object_names": names, "collection_name": collection_name,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Moved {result['moved']} → collection '{result['collection']}'"
    except Exception as e:
        return f"Error: {e}"


# ─── Image-to-3D (local TripoSR, loadable/unloadable) ────────────────────────

def _find_img_to_3d_script() -> Path | None:
    """Locate img_to_3d_server.py for editable installs and deployed copies alike."""
    env = os.environ.get("IMG_TO_3D_SERVER_SCRIPT")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve()
    candidates += [
        here.parent.parent.parent / "img_to_3d_server.py",   # repo / editable install
        here.parent / "img_to_3d_server.py",                  # packaged next to server.py
        Path.cwd() / "img_to_3d_server.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


@mcp.tool()
async def load_img_to_3d_model(ctx: Context, model_dir: str = None, timeout: float = 600.0) -> str:
    """
    Start the local image-to-3D inference server (TripoSR).
    The server process is kept running until unload_img_to_3d_model() is called.
    Frees VRAM when unloaded — load only when you need it.

    Parameters:
    - model_dir: Path to TripoSR weights directory (uses IMG_TO_3D_MODEL_DIR env var if omitted)
    - timeout: Seconds to wait for the server to come up (default 600; the first run
               downloads the weights). Its console output goes to img_to_3d_server.log
               in the temp folder.
    """
    global _img_to_3d_process
    if _img_to_3d_process is not None and _img_to_3d_process.poll() is None:
        return f"Image-to-3D server is already running on port {_img_to_3d_port()}"

    server_script = _find_img_to_3d_script()
    if server_script is None:
        return ("img_to_3d_server.py not found. Put it next to the blender_mcp package or "
                "set IMG_TO_3D_SERVER_SCRIPT to its path.")

    env = {**os.environ}
    if model_dir:
        env["IMG_TO_3D_MODEL_DIR"] = model_dir
    env["IMG_TO_3D_PORT"] = str(_img_to_3d_port())
    python = os.environ.get("IMG_TO_3D_PYTHON") or sys.executable
    log_path = os.path.join(tempfile.gettempdir(), "img_to_3d_server.log")

    try:
        with open(log_path, "ab") as log_fh:
            _img_to_3d_process = _subprocess.Popen(
                [python, str(server_script)],
                env=env,
                stdin=_subprocess.DEVNULL,
                stdout=log_fh,
                stderr=_subprocess.STDOUT,
            )
    except Exception as e:
        return f"Failed to start image-to-3D server: {e}"

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if _img_to_3d_process.poll() is not None:
            code = _img_to_3d_process.returncode
            _img_to_3d_process = None
            return f"Image-to-3D server exited on startup (code {code}). See {log_path}"
        try:
            r = await asyncio.to_thread(_requests.get, f"{_img_to_3d_url()}/status", timeout=1)
            if r.status_code == 200:
                return (f"Image-to-3D server started on port {_img_to_3d_port()} "
                        f"(pid {_img_to_3d_process.pid}, log {log_path})")
        except Exception:
            pass
        await asyncio.sleep(1.0)

    # Not ready: do not leave a half-started process behind
    _stop_img_to_3d_process()
    return f"Image-to-3D server did not become ready within {timeout:.0f} s — see {log_path}"


def _stop_img_to_3d_process() -> None:
    global _img_to_3d_process
    proc = _img_to_3d_process
    _img_to_3d_process = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


@mcp.tool()
def unload_img_to_3d_model(ctx: Context) -> str:
    """
    Stop the local image-to-3D server, freeing VRAM and memory.
    """
    if _img_to_3d_process is None or _img_to_3d_process.poll() is not None:
        _stop_img_to_3d_process()
        return "Image-to-3D server is not running"
    _stop_img_to_3d_process()
    return "Image-to-3D server stopped"


@mcp.tool()
async def generate_3d_from_image(
    ctx: Context,
    image_path: str,
    output_path: str = None,
    foreground_ratio: float = 0.85,
    mc_resolution: int = 256,
    no_remove_bg: bool = False,
) -> str:
    """
    Generate a 3D mesh (.glb) from a single image using the local TripoSR model.
    You must call load_img_to_3d_model() first.

    Parameters:
    - image_path: Absolute path to the input image
    - output_path: Where to save the .glb file (auto-generated if omitted)
    - foreground_ratio: Foreground crop ratio for background removal (default 0.85)
    - mc_resolution: Marching-cubes resolution; higher = more detail but slower (default 256)
    - no_remove_bg: Skip background removal if the image already has a clean background
    """
    if _img_to_3d_process is None or _img_to_3d_process.poll() is not None:
        return "Image-to-3D server is not running. Call load_img_to_3d_model() first."

    if not os.path.exists(image_path):
        return f"Image not found: {image_path}"

    if output_path is None:
        stem = os.path.splitext(os.path.basename(image_path))[0]
        output_path = os.path.join(tempfile.gettempdir(), f"triposr_{stem}_{int(_time.time())}.glb")

    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()

        resp = await asyncio.to_thread(
            _requests.post,
            f"{_img_to_3d_url()}/generate",
            files={"image": (os.path.basename(image_path), img_bytes)},
            data={
                "foreground_ratio": str(foreground_ratio),
                "mc_resolution": str(mc_resolution),
                "no_remove_bg": "1" if no_remove_bg else "0",
            },
            timeout=300,
        )
        if resp.status_code != 200:
            return f"Generation failed (HTTP {resp.status_code}): {resp.text[:500]}"

        with open(output_path, "wb") as f:
            f.write(resp.content)

        size_kb = len(resp.content) // 1024
        return (f"3D model generated: {output_path} ({size_kb} KB). "
                f"Use import_file('{output_path}') to load it into Blender.")
    except Exception as e:
        return f"Error calling image-to-3D server: {e}"


@mcp.prompt()
def asset_creation_strategy() -> str:
    """Defines the preferred strategy for creating assets in Blender"""
    return """When creating 3D content in Blender, always start by checking if integrations are available:

    0. Before anything, always check the scene from get_scene_info()
    1. First use the following tools to verify if the following integrations are enabled:
        1. PolyHaven
            Use get_polyhaven_status() to verify its status
            If PolyHaven is enabled:
            - For objects/models: Use download_polyhaven_asset() with asset_type="models"
            - For materials/textures: Use download_polyhaven_asset() with asset_type="textures"
            - For environment lighting: Use download_polyhaven_asset() with asset_type="hdris"
        2. Sketchfab
            Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven.
            Use get_sketchfab_status() to verify its status
            If Sketchfab is enabled:
            - For objects/models: First search using search_sketchfab_models() with your query
            - Then download specific models using download_sketchfab_model() with the UID
            - Note that only downloadable models can be accessed, and API key must be properly configured
            - Sketchfab has a wider variety of models than PolyHaven, especially for specific subjects
        3. Hyper3D(Rodin)
            Hyper3D Rodin is good at generating 3D models for single item.
            So don't try to:
            1. Generate the whole scene with one shot
            2. Generate ground using Hyper3D
            3. Generate parts of the items separately and put them together afterwards

            Use get_hyper3d_status() to verify its status
            If Hyper3D is enabled:
            - For objects/models, do the following steps:
                1. Create the model generation task
                    - Use generate_hyper3d_model_via_images() if image(s) is/are given
                    - Use generate_hyper3d_model_via_text() if generating 3D asset using text prompt
                    If key type is free_trial and insufficient balance error returned, tell the user that the free trial key can only generated limited models everyday, they can choose to:
                    - Wait for another day and try again
                    - Go to hyper3d.ai to find out how to get their own API key
                    - Go to fal.ai to get their own private API key
                2. Poll the status
                    - Use poll_rodin_job_status() to check if the generation task has completed or failed
                3. Import the asset
                    - Use import_generated_asset() to import the generated GLB model the asset
                4. After importing the asset, ALWAYS check the world_bounding_box of the imported mesh, and adjust the mesh's location and size
                    Adjust the imported mesh's location, scale, rotation, so that the mesh is on the right spot.

                You can reuse assets previous generated by running python code to duplicate the object, without creating another generation task.
        4. Hunyuan3D
            Hunyuan3D is good at generating 3D models for single item.
            So don't try to:
            1. Generate the whole scene with one shot
            2. Generate ground using Hunyuan3D
            3. Generate parts of the items separately and put them together afterwards

            Use get_hunyuan3d_status() to verify its status
            If Hunyuan3D is enabled:
                if Hunyuan3D mode is "OFFICIAL_API":
                    - For objects/models, do the following steps:
                        1. Create the model generation task
                            - Use generate_hunyuan3d_model by providing either a **text description** OR an **image(local or urls) reference**.
                            - Go to cloud.tencent.com out how to get their own SecretId and SecretKey
                        2. Poll the status
                            - Use poll_hunyuan_job_status() to check if the generation task has completed or failed
                        3. Import the asset
                            - Use import_generated_asset_hunyuan() to import the generated OBJ model the asset
                    if Hunyuan3D mode is "LOCAL_API":
                        - For objects/models, do the following steps:
                        1. Create the model generation task
                            - Use generate_hunyuan3d_model if image (local or urls)  or text prompt is given and import the asset

                You can reuse assets previous generated by running python code to duplicate the object, without creating another generation task.

    3. Always check the world_bounding_box for each item so that:
        - Ensure that all objects that should not be clipping are not clipping.
        - Items have right spatial relationship.
    
    4. Recommended asset source priority:
        - For specific existing objects: First try Sketchfab, then PolyHaven
        - For generic objects/furniture: First try PolyHaven, then Sketchfab
        - For custom or unique items not available in libraries: Use Hyper3D Rodin or Hunyuan3D
        - For environment lighting: Use PolyHaven HDRIs
        - For materials/textures: Use PolyHaven textures

    Only fall back to scripting when:
    - PolyHaven, Sketchfab, Hyper3D, and Hunyuan3D are all disabled
    - A simple primitive is explicitly requested
    - No suitable asset exists in any of the libraries
    - Hyper3D Rodin or Hunyuan3D failed to generate the desired asset
    - The task specifically requires a basic material/color
    """

# Main execution

def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()