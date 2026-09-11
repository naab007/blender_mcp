"""M11: the three version sources agree and the package imports.

    .venv\\Scripts\\python.exe -m pytest tests\\test_version_sync.py -q
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

# Phase A = 2.0.0; Phase B0 landing = 2.1.0 (run with BLENDER_MCP_EXPECTED_VERSION=2.1.0 until the default flips)
EXPECTED = os.environ.get("BLENDER_MCP_EXPECTED_VERSION", "2.0.0")


def _pyproject_version():
    text = open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read()
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def _bl_info():
    text = open(os.path.join(REPO, "addon.py"), encoding="utf-8").read()
    block = text[text.index("bl_info = {"):text.index("}", text.index("bl_info = {")) + 1]
    ver = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', block).groups()
    blender = re.search(r'"blender":\s*\((\d+),\s*(\d+),\s*(\d+)\)', block).groups()
    return ".".join(ver), tuple(int(x) for x in blender)


def _init_version():
    import blender_mcp
    return blender_mcp.__version__


def test_pyproject_is_2_0_0():
    assert _pyproject_version() == EXPECTED


def test_bl_info_is_2_0_0():
    assert _bl_info()[0] == EXPECTED


def test_init_is_2_0_0():
    assert _init_version() == EXPECTED


def test_three_sources_agree():
    assert _pyproject_version() == _bl_info()[0] == _init_version()


def test_bl_info_blender_minimum_stays_4_0_0():
    # ruling: minimum stays (4, 0, 0); 4.x support is kept behind try-assign / hasattr
    assert _bl_info()[1] == (4, 0, 0)


def test_server_main_imports():
    from blender_mcp.server import main  # noqa: F401
