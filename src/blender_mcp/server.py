# blender_mcp_server.py
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
from pathlib import Path
import base64
from urllib.parse import urlparse


# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876

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
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(180.0)  # Match the addon's timeout
        
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
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
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

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(180.0)  # Match the addon's timeout
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))
            
            return response.get("result", {})
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
            blender = get_blender_connection()
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
    lifespan=server_lifespan
)

# Resource endpoints

# Global connection for resources (since resources can't access context)
_blender_connection = None
_polyhaven_enabled = False  # Add this global variable

# Managed Blender process (started via start_blender tool)
_blender_process = None

def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global _blender_connection, _polyhaven_enabled  # Add _polyhaven_enabled to globals
    
    # If we have an existing connection, check if it's still valid
    if _blender_connection is not None:
        try:
            # First check if PolyHaven is enabled by sending a ping command
            result = _blender_connection.send_command("get_polyhaven_status")
            # Store the PolyHaven status globally
            _polyhaven_enabled = result.get("enabled", False)
            return _blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None
    
    # Create a new connection if needed
    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
    
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
        
        # Read the file
        with open(temp_path, 'rb') as f:
            image_bytes = f.read()
        
        # Delete the temp file
        os.remove(temp_path)
        
        return Image(data=image_bytes, format="png")
        
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
        img_format = result.get("format", "jpeg")
        
        # Log model info
        model_name = result.get("model_name", "Unknown")
        author = result.get("author", "Unknown")
        logger.info(f"Preview retrieved for '{model_name}' by {author}")
        
        return Image(data=image_data, format=img_format)
        
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

import glob as _glob
import shutil as _shutil


def _find_blender_exe(hint: str = None) -> str | None:
    """Locate the Blender executable, checking in priority order."""
    # 1. Explicit hint or env var
    for candidate in filter(None, [hint, os.environ.get("BLENDER_EXE")]):
        if os.path.isfile(candidate):
            return candidate

    # 2. PATH
    found = _shutil.which("blender") or _shutil.which("blender.exe")
    if found:
        return found

    # 3. Windows default install locations (newest version wins)
    win_patterns = [
        r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
        r"C:\Program Files\Blender Foundation\blender.exe",
    ]
    matches = []
    for pat in win_patterns:
        matches.extend(_glob.glob(pat))
    if matches:
        return sorted(matches)[-1]   # highest version string sorts last

    # 4. Steam (Windows)
    steam = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
    if os.path.isfile(steam):
        return steam

    # 5. macOS .app bundle
    mac_paths = [
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/Applications/Blender/blender.app/Contents/MacOS/Blender",
    ]
    for p in mac_paths:
        if os.path.isfile(p):
            return p

    return None


@mcp.tool()
def start_blender(
    ctx: Context,
    blend_file: str = None,
    blender_exe: str = None,
    background: bool = False,
    wait_for_addon: bool = True,
    python_expr: str = None,
) -> str:
    """
    Launch Blender as a managed subprocess.

    Parameters:
    - blend_file: Path to a .blend file to open on startup (optional)
    - blender_exe: Full path to the Blender executable. Auto-detected if omitted
                   (checks BLENDER_EXE env var, PATH, then common install locations).
    - background: True = headless mode (--background), no UI. Useful for rendering.
    - wait_for_addon: Wait up to 30 s for the BlenderMCP addon socket to become
                      reachable on port 9876 (default True). Set False for background
                      jobs that don't use the addon.
    - python_expr: Optional Python expression passed to Blender via --python-expr,
                   e.g. "import bpy; bpy.ops.wm.quit_blender()" for scripted batch runs.
    """
    global _blender_process, _blender_connection

    if _blender_process is not None and _blender_process.poll() is None:
        return f"Blender is already running (pid {_blender_process.pid}). Call close_blender() first."

    exe = _find_blender_exe(blender_exe)
    if not exe:
        return (
            "Could not find the Blender executable. "
            "Set the BLENDER_EXE environment variable to the full path, "
            "or pass blender_exe='/path/to/blender'."
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

    try:
        _blender_process = _subprocess.Popen(
            cmd,
            stdout=_subprocess.DEVNULL if background else None,
            stderr=_subprocess.DEVNULL if background else None,
        )
        logger.info(f"Blender started: pid={_blender_process.pid}  cmd={cmd}")
    except Exception as e:
        return f"Failed to launch Blender: {e}"

    # Reset any stale connection so get_blender_connection() reconnects fresh
    if _blender_connection:
        try:
            _blender_connection.disconnect()
        except Exception:
            pass
        _blender_connection = None

    if not wait_for_addon or background:
        return (
            f"Blender launched (pid {_blender_process.pid})."
            + (" Waiting for addon skipped (background mode)." if background else
               " Not waiting for addon (wait_for_addon=False).")
        )

    # Poll port 9876 until the addon TCP server is up
    host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
    port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
    for _ in range(60):   # 30 s total
        _time.sleep(0.5)
        if _blender_process.poll() is not None:
            return f"Blender exited unexpectedly (code {_blender_process.returncode})"
        try:
            test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test.settimeout(0.5)
            test.connect((host, port))
            test.close()
            return (
                f"Blender started (pid {_blender_process.pid}) and addon is ready on port {port}."
                + (f" Opened: {blend_file}" if blend_file else "")
            )
        except (ConnectionRefusedError, OSError):
            pass

    return (
        f"Blender launched (pid {_blender_process.pid}) but the MCP addon did not respond "
        f"on port {port} within 30 s. Make sure the BlenderMCP addon is installed and enabled "
        "in Blender Preferences → Add-ons."
    )


@mcp.tool()
def close_blender(
    ctx: Context,
    force: bool = False,
) -> str:
    """
    Close the Blender instance that was started with start_blender().

    Parameters:
    - force: If True, kills the process immediately instead of asking Blender
             to quit gracefully via its Python API (default False).
    """
    global _blender_process, _blender_connection

    pid = _blender_process.pid if _blender_process else None

    # Graceful path: ask Blender to quit via the addon socket
    if not force and _blender_connection:
        try:
            _blender_connection.send_command("quit_blender", {"save_prompt": False})
            # Give it 3 s to actually close
            if _blender_process:
                try:
                    _blender_process.wait(timeout=3)
                except _subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass   # socket gone = Blender already closing

    # Also disconnect on our side
    if _blender_connection:
        try:
            _blender_connection.disconnect()
        except Exception:
            pass
        _blender_connection = None

    # Ensure the process is gone
    if _blender_process is not None:
        if _blender_process.poll() is None:
            try:
                _blender_process.terminate()
                _blender_process.wait(timeout=5)
            except _subprocess.TimeoutExpired:
                _blender_process.kill()
        _blender_process = None

    return f"Blender closed." + (f" (pid was {pid})" if pid else "")


@mcp.tool()
def get_blender_status(ctx: Context) -> str:
    """
    Report whether Blender is running, and whether the MCP addon is reachable.
    """
    global _blender_process

    proc_status = "not started via MCP"
    if _blender_process is not None:
        code = _blender_process.poll()
        if code is None:
            proc_status = f"running (pid {_blender_process.pid})"
        else:
            proc_status = f"exited (code {code})"

    # Try to ping the addon socket
    host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
    port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test.settimeout(1.0)
        test.connect((host, port))
        test.close()
        addon_status = f"reachable on {host}:{port}"
    except (ConnectionRefusedError, OSError):
        addon_status = f"not reachable on {host}:{port}"

    return f"Process: {proc_status}\nAddon socket: {addon_status}"


# ─── Extended tools ──────────────────────────────────────────────────────────

# Optional PIL for contact-sheet compositing (server-side only)
try:
    from PIL import Image as PILImage, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# Subprocess management for the local image-to-3D server
import subprocess as _subprocess
import time as _time
import requests as _requests

_img_to_3d_process = None
_IMG_TO_3D_PORT = 7862
_IMG_TO_3D_URL = f"http://127.0.0.1:{_IMG_TO_3D_PORT}"


# ─── Multi-angle capture ─────────────────────────────────────────────────────

@mcp.tool()
def capture_viewport_angle(
    ctx: Context,
    angle: str = "front",
    max_size: int = 800,
) -> Image:
    """
    Capture the Blender 3D viewport from a named angle and return it as an image.

    Parameters:
    - angle: View direction. One of: front, back, left, right, top, bottom,
             iso_front_right, iso_front_left
    - max_size: Maximum pixel dimension (default 800)
    """
    try:
        blender = get_blender_connection()
        temp_path = os.path.join(tempfile.gettempdir(), f"blender_angle_{angle}_{os.getpid()}.png")
        result = blender.send_command("capture_viewport_angle", {
            "angle": angle,
            "max_size": max_size,
            "filepath": temp_path,
        })
        if "error" in result:
            raise Exception(result["error"])
        with open(temp_path, "rb") as f:
            data = f.read()
        os.remove(temp_path)
        return Image(data=data, format="png")
    except Exception as e:
        logger.error(f"capture_viewport_angle error: {e}")
        raise Exception(f"capture_viewport_angle failed: {e}")


@mcp.tool()
def capture_contact_sheet(
    ctx: Context,
    angles: str = "front,right,top,iso_front_right",
    max_size: int = 512,
) -> Image:
    """
    Capture multiple viewport angles and stitch them into a single contact sheet image.

    Parameters:
    - angles: Comma-separated list of angle names (default: front,right,top,iso_front_right)
    - max_size: Pixel size for each individual tile (default 512)

    Returns a single composited image with all requested angles labelled.
    """
    try:
        blender = get_blender_connection()
        angle_list = [a.strip() for a in angles.split(",") if a.strip()]
        result = blender.send_command("capture_contact_sheet", {
            "angles": angle_list,
            "max_size": max_size,
        })
        if "error" in result:
            raise Exception(result["error"])

        images_info = result.get("images", {})

        # If PIL is available, composite into a grid
        if _PIL_AVAILABLE:
            tiles = []
            for angle in angle_list:
                info = images_info.get(angle, {})
                fp = info.get("filepath")
                if fp and os.path.exists(fp):
                    tile = PILImage.open(fp).convert("RGB")
                    tiles.append((angle, tile))

            if tiles:
                cols = min(len(tiles), 4)
                rows = (len(tiles) + cols - 1) // cols
                tw, th = tiles[0][1].size
                sheet = PILImage.new("RGB", (cols * tw, rows * th), (30, 30, 30))
                for i, (label, tile) in enumerate(tiles):
                    x = (i % cols) * tw
                    y = (i // cols) * th
                    sheet.paste(tile, (x, y))
                    draw = ImageDraw.Draw(sheet)
                    draw.text((x + 4, y + 4), label, fill=(255, 255, 0))

                out_path = os.path.join(tempfile.gettempdir(), f"blender_contact_{os.getpid()}.png")
                sheet.save(out_path)
                with open(out_path, "rb") as f:
                    data = f.read()
                os.remove(out_path)

                # Clean up individual tiles
                for angle, _ in tiles:
                    fp = images_info[angle].get("filepath")
                    if fp and os.path.exists(fp):
                        try:
                            os.remove(fp)
                        except Exception:
                            pass

                return Image(data=data, format="png")

        # Fallback: just return the first captured image
        for angle in angle_list:
            fp = images_info.get(angle, {}).get("filepath")
            if fp and os.path.exists(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                os.remove(fp)
                return Image(data=data, format="png")

        raise Exception("No images were captured")
    except Exception as e:
        logger.error(f"capture_contact_sheet error: {e}")
        raise Exception(f"capture_contact_sheet failed: {e}")


# ─── Depth map ───────────────────────────────────────────────────────────────

@mcp.tool()
def render_depth_map(
    ctx: Context,
    max_depth: float = 10.0,
) -> Image:
    """
    Render a normalised depth map from the active camera using the Blender compositor
    Z-pass. Closer objects appear lighter.

    Parameters:
    - max_depth: Depth value (scene units) mapped to black (default 10.0)
    """
    try:
        blender = get_blender_connection()
        temp_path = os.path.join(tempfile.gettempdir(), f"blender_depth_{os.getpid()}.png")
        result = blender.send_command("render_depth_map", {
            "filepath": temp_path,
            "max_depth": max_depth,
        })
        if "error" in result:
            raise Exception(result["error"])
        with open(temp_path, "rb") as f:
            data = f.read()
        os.remove(temp_path)
        return Image(data=data, format="png")
    except Exception as e:
        logger.error(f"render_depth_map error: {e}")
        raise Exception(f"render_depth_map failed: {e}")


# ─── Reference image ─────────────────────────────────────────────────────────

# Server-side registry so compare_reference_image can find paths without a round-trip
_reference_registry: Dict[str, str] = {}


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
        _reference_registry[name] = filepath
        blender = get_blender_connection()
        result = blender.send_command("store_reference_image", {"name": name, "filepath": filepath})
        if "error" in result:
            return f"Error: {result['error']}"
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
    with a previously stored reference image.

    Parameters:
    - reference_name: Name given to store_reference_image earlier
    - angle: Viewport angle to capture for comparison
    - max_size: Tile size for each image in the composite
    """
    try:
        blender = get_blender_connection()

        # Capture current viewport
        temp_render = os.path.join(tempfile.gettempdir(), f"blender_cmp_render_{os.getpid()}.png")
        r = blender.send_command("capture_viewport_angle", {
            "angle": angle,
            "max_size": max_size,
            "filepath": temp_render,
        })
        if "error" in r:
            raise Exception(r["error"])

        ref_path = _reference_registry.get(reference_name)
        if ref_path is None:
            raise Exception(f"Reference '{reference_name}' not found. Call store_reference_image first.")

        if not _PIL_AVAILABLE:
            # Just return the render
            with open(temp_render, "rb") as f:
                data = f.read()
            os.remove(temp_render)
            return Image(data=data, format="png")

        render_img = PILImage.open(temp_render).convert("RGB").resize((max_size, max_size))
        ref_img    = PILImage.open(ref_path).convert("RGB").resize((max_size, max_size))

        sheet = PILImage.new("RGB", (max_size * 2 + 8, max_size + 24), (30, 30, 30))
        sheet.paste(ref_img,    (0,          24))
        sheet.paste(render_img, (max_size + 8, 24))

        draw = ImageDraw.Draw(sheet)
        draw.text((4,            4), f"Reference: {reference_name}", fill=(200, 200, 255))
        draw.text((max_size + 12, 4), f"Current: {angle}",            fill=(255, 200, 100))

        out_path = os.path.join(tempfile.gettempdir(), f"blender_compare_{os.getpid()}.png")
        sheet.save(out_path)
        with open(out_path, "rb") as f:
            data = f.read()

        for p in (temp_render, out_path):
            try:
                os.remove(p)
            except Exception:
                pass

        return Image(data=data, format="png")
    except Exception as e:
        logger.error(f"compare_reference_image error: {e}")
        raise Exception(f"compare_reference_image failed: {e}")


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
    making differences immediately obvious.

    Parameters:
    - image_path_a: Path to the first image (treated as the reference/baseline)
    - image_path_b: Path to the second image (treated as the new/changed version)
    - threshold: Pixel difference (0-255) below which changes are ignored (default 15, filters noise)
    - tile_size: Width/height of each panel in the composite (default 512)
    """
    try:
        if not _PIL_AVAILABLE:
            raise Exception("Pillow is required for diff_images — install it with: pip install pillow")

        for p in (image_path_a, image_path_b):
            if not os.path.exists(p):
                raise Exception(f"File not found: {p}")

        import numpy as np
        from PIL import ImageChops, ImageEnhance, ImageFilter

        img_a = PILImage.open(image_path_a).convert("RGB").resize((tile_size, tile_size), PILImage.LANCZOS)
        img_b = PILImage.open(image_path_b).convert("RGB").resize((tile_size, tile_size), PILImage.LANCZOS)

        # --- Build diff mask ---
        diff = ImageChops.difference(img_a, img_b)
        diff_gray = diff.convert("L")
        diff_arr  = np.array(diff_gray, dtype=np.float32)

        # Amplify so subtle changes become visible, then threshold
        amplified = np.clip(diff_arr * 6, 0, 255).astype(np.uint8)
        mask_arr  = np.where(amplified > threshold, 255, 0).astype(np.uint8)

        # Soft glow: slight blur on the mask so hard edges bleed outward
        mask_img  = PILImage.fromarray(mask_arr, "L").filter(ImageFilter.GaussianBlur(radius=2))

        # --- Diff panel: desaturated base + red overlay ---
        base_desat  = ImageEnhance.Color(img_a).enhance(0.15)   # near-grayscale
        red_overlay = PILImage.new("RGB", (tile_size, tile_size), (255, 30, 30))
        diff_panel  = PILImage.composite(red_overlay, base_desat, mask_img)

        # Annotate changed pixel percentage
        changed_pct = (mask_arr > 0).sum() / (tile_size * tile_size) * 100

        # --- 3-panel composite ---
        gap    = 6
        bar    = 28
        w      = tile_size * 3 + gap * 2
        h      = tile_size + bar
        sheet  = PILImage.new("RGB", (w, h), (20, 20, 20))

        sheet.paste(img_a,      (0,                         bar))
        sheet.paste(img_b,      (tile_size + gap,           bar))
        sheet.paste(diff_panel, (tile_size * 2 + gap * 2,   bar))

        draw = ImageDraw.Draw(sheet)
        x_a    = tile_size // 2 - 30
        x_b    = tile_size + gap + tile_size // 2 - 30
        x_diff = tile_size * 2 + gap * 2 + tile_size // 2 - 50
        draw.text((x_a,    6), "Image A",                       fill=(200, 200, 200))
        draw.text((x_b,    6), "Image B",                       fill=(200, 200, 200))
        draw.text((x_diff, 6), f"Diff  ({changed_pct:.1f}% changed)",  fill=(255, 100, 100))

        out_path = os.path.join(tempfile.gettempdir(), f"blender_diff_{os.getpid()}.png")
        sheet.save(out_path)
        with open(out_path, "rb") as f:
            data = f.read()
        try:
            os.remove(out_path)
        except Exception:
            pass

        return Image(data=data, format="png")
    except Exception as e:
        logger.error(f"diff_images error: {e}")
        raise Exception(f"diff_images failed: {e}")


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
    - samples: Cycles sample count, ignored for EEVEE (default 32)
    """
    try:
        blender = get_blender_connection()
        temp_path = os.path.join(tempfile.gettempdir(), f"blender_render_{os.getpid()}.png")
        result = blender.send_command("render_from_camera", {
            "camera_name": camera_name,
            "filepath": temp_path,
            "width": width,
            "height": height,
            "samples": samples,
        })
        if "error" in result:
            raise Exception(result["error"])
        with open(temp_path, "rb") as f:
            data = f.read()
        os.remove(temp_path)
        return Image(data=data, format="png")
    except Exception as e:
        logger.error(f"render_from_camera error: {e}")
        raise Exception(f"render_from_camera failed: {e}")


@mcp.tool()
def render_all_cameras(
    ctx: Context,
    width: int = 1920,
    height: int = 1080,
    samples: int = 32,
    output_dir: str = None,
) -> Image:
    """
    Render a still from every camera in the scene simultaneously and return
    a contact sheet with all results labelled by camera name.

    Parameters:
    - width: Render width per camera in pixels (default 1920)
    - height: Render height per camera in pixels (default 1080)
    - samples: Cycles sample count (default 32, ignored for EEVEE)
    - output_dir: Directory to save individual renders (temp dir if omitted)

    Returns a composited contact sheet image. Individual renders are also
    saved to output_dir so you can access them at full resolution.
    """
    try:
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

        if not successful:
            raise Exception(
                f"No renders succeeded. "
                f"Cameras found: {result.get('total_cameras', 0)}. "
                f"Details: {renders}"
            )

        # Build contact sheet with PIL if available, otherwise return first image
        if _PIL_AVAILABLE and len(successful) > 1:
            # Thumbnail each render to a consistent tile size
            TILE_W, TILE_H = 960, 540
            LABEL_H = 28
            cols = min(len(successful), 3)
            rows = (len(successful) + cols - 1) // cols

            sheet = PILImage.new(
                "RGB",
                (cols * TILE_W, rows * (TILE_H + LABEL_H)),
                (20, 20, 20),
            )

            for i, r in enumerate(successful):
                tile = PILImage.open(r["filepath"]).convert("RGB")
                tile = tile.resize((TILE_W, TILE_H), PILImage.LANCZOS)
                x = (i % cols) * TILE_W
                y = (i // cols) * (TILE_H + LABEL_H)
                sheet.paste(tile, (x, y + LABEL_H))
                draw = ImageDraw.Draw(sheet)
                draw.rectangle([x, y, x + TILE_W, y + LABEL_H], fill=(40, 40, 40))
                draw.text((x + 6, y + 6), r["camera"], fill=(220, 220, 100))

            out_path = os.path.join(
                tempfile.gettempdir(),
                f"blender_all_cameras_{os.getpid()}.png",
            )
            sheet.save(out_path)
            with open(out_path, "rb") as f:
                data = f.read()
            os.remove(out_path)

            logger.info(
                f"render_all_cameras: {len(successful)}/{result['total_cameras']} "
                f"cameras rendered"
            )
            return Image(data=data, format="png")

        # Fallback: return the first render as-is
        with open(successful[0]["filepath"], "rb") as f:
            data = f.read()
        return Image(data=data, format="png")

    except Exception as e:
        logger.error(f"render_all_cameras error: {e}")
        raise Exception(f"render_all_cameras failed: {e}")


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
) -> str:
    """
    Export an object (or the full scene) to a 3D file.

    Parameters:
    - name: Object to export; exports entire scene if omitted
    - filepath: Output file path (auto-generated in temp dir if omitted)
    - file_format: glb, gltf, fbx, obj, stl, ply (default glb)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("export_object", {
            "name": name, "filepath": filepath, "file_format": file_format,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Exported to: {result['filepath']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def import_file(ctx: Context, filepath: str) -> str:
    """
    Import a 3D file into the current Blender scene.
    Supports: .glb, .gltf, .fbx, .obj, .stl, .ply, .blend

    Parameters:
    - filepath: Absolute path to the file to import
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("import_file", {"filepath": filepath})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Imported {filepath}. New objects: {result.get('imported_objects', [])}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_blend(ctx: Context, filepath: str = None) -> str:
    """
    Save the current Blender project as a .blend file.

    Parameters:
    - filepath: Absolute path to save to (e.g. "C:/projects/my_scene.blend").
                If omitted, saves over the currently open file. If the file has
                never been saved, a temporary path is used and returned.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("save_blend", {"filepath": filepath} if filepath else {})
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Saved: {result['filepath']}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def load_blend(ctx: Context, filepath: str) -> str:
    """
    Open a .blend file, replacing the current Blender scene.
    Unsaved changes to the current file will be lost — save first if needed.

    Parameters:
    - filepath: Absolute path to the .blend file to open
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("load_blend", {"filepath": filepath})
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Opened '{filepath}'. "
                f"Scene: {result['scene_name']}, {result['object_count']} objects.")
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
) -> str:
    """
    Parent one object to another, creating a hierarchy.

    Parameters:
    - child_name: Object that becomes the child
    - parent_name: Object that becomes the parent
    - keep_transform: Preserve the child's world-space position (default True)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("parent_object", {
            "child_name": child_name, "parent_name": parent_name,
            "keep_transform": keep_transform,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Parented '{child_name}' → '{parent_name}'"
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
        kwargs = json.loads(params) if params else {}
        result = blender.send_command("add_modifier", {
            "name": name, "modifier_type": modifier_type,
            "modifier_name": modifier_name, **kwargs,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Added {result['type']} modifier '{result['modifier']}' to '{name}'"
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
    - solver: EXACT (better quality) or FAST (faster but less reliable)
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
                f"({'applied' if apply else 'modifier added only'})")
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
) -> str:
    """
    Configure scene render settings.

    Parameters:
    - engine: CYCLES (ray-traced, photorealistic), BLENDER_EEVEE (real-time),
              BLENDER_WORKBENCH (solid view)
    - width / height: Render resolution in pixels
    - samples: Number of render samples (affects quality/noise)
    - output_path: File path for saved renders (e.g. "C:/renders/frame_####.png")
    - file_format: PNG, JPEG, EXR, TIFF
    - transparent_background: True to render with alpha instead of background colour
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_render_settings", {
            "engine": engine, "width": width, "height": height,
            "samples": samples, "output_path": output_path,
            "file_format": file_format,
            "transparent_background": transparent_background,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Render settings: engine={result['engine']}, "
                f"resolution={result['resolution']}, "
                f"transparent={result['transparent']}, "
                f"output={result['output']}")
    except Exception as e:
        return f"Error: {e}"


# ─── Animation ────────────────────────────────────────────────────────────────

@mcp.tool()
def add_keyframe(
    ctx: Context,
    name: str,
    data_path: str = "location",
    frame: int = None,
    value: str = None,
) -> str:
    """
    Insert an animation keyframe on an object property.

    Parameters:
    - name: Object name
    - data_path: Property to key — 'location', 'rotation_euler', 'scale',
                 or any animatable path like 'data.energy'
    - frame: Frame number (uses current frame if omitted)
    - value: Comma-separated values to set before keying, e.g. "1,2,3" for location.
             Rotation values are in degrees and converted automatically.
    """
    try:
        blender = get_blender_connection()
        val = [float(v) for v in value.split(",")] if value else None
        result = blender.send_command("add_keyframe", {
            "name": name, "data_path": data_path,
            "frame": frame, "value": val,
        })
        if "error" in result:
            return f"Error: {result['error']}"
        return (f"Keyframe on '{name}.{data_path}' at frame {result['frame']}"
                + (f" = {val}" if val else ""))
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

@mcp.tool()
def load_img_to_3d_model(ctx: Context, model_dir: str = None) -> str:
    """
    Start the local image-to-3D inference server (TripoSR).
    The server process is kept running until unload_img_to_3d_model() is called.
    Frees VRAM when unloaded — load only when you need it.

    Parameters:
    - model_dir: Path to TripoSR weights directory (uses IMG_TO_3D_MODEL_DIR env var if omitted)
    """
    global _img_to_3d_process
    if _img_to_3d_process is not None and _img_to_3d_process.poll() is None:
        return f"Image-to-3D server is already running on port {_IMG_TO_3D_PORT}"

    server_script = Path(__file__).parent.parent.parent / "img_to_3d_server.py"
    if not server_script.exists():
        return (f"img_to_3d_server.py not found at {server_script}. "
                "Please ensure it is present in the blender_mcp root directory.")

    env = {**os.environ}
    if model_dir:
        env["IMG_TO_3D_MODEL_DIR"] = model_dir
    env["IMG_TO_3D_PORT"] = str(_IMG_TO_3D_PORT)

    try:
        _img_to_3d_process = _subprocess.Popen(
            ["python", str(server_script)],
            env=env,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.PIPE,
        )
        # Wait up to 30 s for the server to become ready
        for _ in range(60):
            _time.sleep(0.5)
            try:
                r = _requests.get(f"{_IMG_TO_3D_URL}/status", timeout=1)
                if r.status_code == 200:
                    return f"Image-to-3D server started on port {_IMG_TO_3D_PORT} (pid {_img_to_3d_process.pid})"
            except Exception:
                pass
            if _img_to_3d_process.poll() is not None:
                err = _img_to_3d_process.stderr.read(2000).decode(errors="replace")
                return f"Image-to-3D server crashed on startup: {err}"
        return "Image-to-3D server did not become ready within 30 s — check logs"
    except Exception as e:
        return f"Failed to start image-to-3D server: {e}"


@mcp.tool()
def unload_img_to_3d_model(ctx: Context) -> str:
    """
    Stop the local image-to-3D server, freeing VRAM and memory.
    """
    global _img_to_3d_process
    if _img_to_3d_process is None or _img_to_3d_process.poll() is not None:
        _img_to_3d_process = None
        return "Image-to-3D server is not running"
    try:
        _img_to_3d_process.terminate()
        _img_to_3d_process.wait(timeout=10)
    except Exception:
        _img_to_3d_process.kill()
    _img_to_3d_process = None
    return "Image-to-3D server stopped"


@mcp.tool()
def generate_3d_from_image(
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
    global _img_to_3d_process
    if _img_to_3d_process is None or _img_to_3d_process.poll() is not None:
        return "Image-to-3D server is not running. Call load_img_to_3d_model() first."

    if not os.path.exists(image_path):
        return f"Image not found: {image_path}"

    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), f"triposr_{os.getpid()}.glb")

    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()

        resp = _requests.post(
            f"{_IMG_TO_3D_URL}/generate",
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