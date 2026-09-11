"""Dump or diff the MCP server's tools/list.

    .venv\\Scripts\\python.exe tests\\dump_tools_list.py names            -> one tool name per line (order = registration order)
    .venv\\Scripts\\python.exe tests\\dump_tools_list.py json [OUT.json]  -> full tools/list (name, description, inputSchema) sorted by name
    .venv\\Scripts\\python.exe tests\\dump_tools_list.py diff BASE.json   -> unified diff of the live server against a saved json; exit 1 on difference

Set BLENDER_MCP_SERVER_FILE to load a server.py from another path (e.g. a copy of main) instead of
the installed blender_mcp package.
"""
import asyncio
import difflib
import importlib.util
import json
import os
import sys


def load_server():
    path = os.environ.get("BLENDER_MCP_SERVER_FILE")
    if path:
        spec = importlib.util.spec_from_file_location("blender_mcp_server_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    import blender_mcp.server as mod
    return mod


def tools_payload(mod):
    tools = asyncio.run(mod.mcp.list_tools())
    rows = []
    for t in tools:
        d = t.model_dump(exclude_none=True)
        rows.append({"name": d["name"], "description": d.get("description", ""), "inputSchema": d.get("inputSchema", {})})
    return rows


def names_in_order(mod):
    return [t.name for t in asyncio.run(mod.mcp.list_tools())]


def as_text(rows):
    return json.dumps(sorted(rows, key=lambda r: r["name"]), indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv):
    mode = argv[0] if argv else "names"
    mod = load_server()
    if mode == "names":
        for n in names_in_order(mod):
            print(n)
        return 0
    rows = tools_payload(mod)
    text = as_text(rows)
    if mode == "json":
        if len(argv) > 1:
            with open(argv[1], "w", encoding="ascii", newline="\n") as f:
                f.write(text)
            print("wrote %s (%d tools)" % (argv[1], len(rows)))
        else:
            sys.stdout.write(text)
        return 0
    if mode == "diff":
        base = open(argv[1], encoding="ascii").read()
        if base == text:
            print("IDENTICAL to %s (%d tools)" % (argv[1], len(rows)))
            return 0
        for line in difflib.unified_diff(base.splitlines(), text.splitlines(), argv[1], "live", lineterm="", n=2):
            print(line.encode("ascii", "replace").decode("ascii"))
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
