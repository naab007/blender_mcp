"""Blender integration through the Model Context Protocol."""

__version__ = "2.2.0"
# Wire-protocol number. Must equal PROTOCOL in addon.py; bumped only when a
# handler key is renamed/removed or an existing payload/reply key changes
# meaning or type (additive keys do not bump). 2.1.0 is the first version that
# declares one; older add-ons are detected by their "Unknown command type" reply.
PROTOCOL = 1

# Expose key classes and functions for easier imports
from .server import BlenderConnection, get_blender_connection
