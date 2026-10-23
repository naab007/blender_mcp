"""B2-G5 live mechanics (TEST-PLAN-B2 s5.1 rule 3): drive the repo .venv server over MCP stdio against a GUI Blender.

    .venv\\Scripts\\python tests\\live_client.py --exe <blender.exe> --port 9877 --out <dir> plan.json

plan.json = [{"id": "L1a", "tool": "get_version", "args": {}}, {"id": "...", "sleep": 1.5}, ...]
One ClientSession for the whole plan, calls in order. Every reply is printed as ASCII:
    LIVE <id> TEXT <json-escaped text>            (cut at --max-chars unless the item has "full": true)
    LIVE <id> IMAGE <path> <w>x<h> min=<v> max=<v> stddev=<v>   (image content saved under <out>/<id>.png)
    LIVE <id> ERROR <text>
The server gets BLENDER_PORT / BLENDER_HOST / BLENDER_EXE / BLENDER_MCP_SETTINGS_DIR in its environment
(settings.py: env beats settings.json), so the user's ~/.blender_mcp/settings.json is never read or written;
<out>/settings/settings.json carries output_dir = <out>/exports. Exit code = number of ERROR lines.
"""
import argparse
import asyncio
import base64
import io
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


def P(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


def image_stats(data):
    try:
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(data)).convert("L")
        st = ImageStat.Stat(im)
        return "%dx%d min=%d max=%d stddev=%.2f" % (im.width, im.height, st.extrema[0][0], st.extrema[0][1], st.stddev[0])
    except Exception as e:
        return "stats-failed %r" % (e,)


async def run(plan, exe, port, out, max_chars):
    os.makedirs(os.path.join(out, "settings"), exist_ok=True)
    os.makedirs(os.path.join(out, "exports"), exist_ok=True)
    with open(os.path.join(out, "settings", "settings.json"), "w", encoding="ascii") as f:
        json.dump({"output_dir": os.path.join(out, "exports").replace("\\", "/")}, f)
    env = dict(os.environ)
    env.update({"BLENDER_PORT": str(port), "BLENDER_HOST": "127.0.0.1", "BLENDER_EXE": exe,
                "BLENDER_MCP_SETTINGS_DIR": os.path.join(out, "settings")})
    params = StdioServerParameters(command=sys.executable, args=["-m", "blender_mcp.server"], env=env,
                                   cwd=os.path.dirname(HERE))
    errors = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            P("LIVE _init TOOLS %d" % len(tools.tools))
            for item in plan:
                iid = item.get("id", "?")
                if "sleep" in item:
                    await asyncio.sleep(float(item["sleep"]))
                    P("LIVE %s SLEEP %s" % (iid, item["sleep"]))
                    continue
                try:
                    res = await session.call_tool(item["tool"], item.get("args") or {})
                except Exception as e:
                    errors += 1
                    P("LIVE %s ERROR %r" % (iid, e))
                    continue
                if getattr(res, "isError", False):
                    errors += 1
                for c in res.content:
                    ctype = getattr(c, "type", "")
                    if ctype == "text":
                        text = c.text
                        cut = max_chars if not item.get("full") else len(text)
                        P("LIVE %s TEXT %s%s" % (iid, json.dumps(text[:cut]), "" if len(text) <= cut else " ...(%d chars)" % len(text)))
                    elif ctype == "image":
                        data = base64.b64decode(c.data)
                        path = os.path.join(out, "%s.png" % iid)
                        with open(path, "wb") as f:
                            f.write(data)
                        P("LIVE %s IMAGE %s %s bytes=%d" % (iid, path, image_stats(data), len(data)))
                    else:
                        P("LIVE %s OTHER %s" % (iid, ctype))
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--exe", required=True)
    ap.add_argument("--port", type=int, default=9877)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=1200)
    a = ap.parse_args()
    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    errors = asyncio.run(run(plan, a.exe, a.port, os.path.abspath(a.out), a.max_chars))
    P("LIVE _done ERRORS %d" % errors)
    return min(errors, 99)


if __name__ == "__main__":
    sys.exit(main())
