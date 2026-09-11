"""Server-side settings for blender-mcp.

One JSON file, ``<settings dir>/settings.json``, read once at import and rewritten by
``update()``. Precedence, highest first: environment variable, settings file, code
default. The settings dir is ``~/.blender_mcp`` unless ``BLENDER_MCP_SETTINGS_DIR`` is
set (tests and harness runs MUST set it so they never touch the user's real file).

This module imports nothing from server.py or mcp so it can be unit-tested alone.
A missing or corrupt file logs one WARNING and falls back to defaults; it never raises.
"""
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("BlenderMCPSettings")

FILE_NAME = "settings.json"
DIR_ENV = "BLENDER_MCP_SETTINGS_DIR"

# Code defaults. ``None`` means "derived at use time" (see presets_dir()).
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9876,
    "connect_timeout": 5.0,
    "command_timeout": 180.0,
    "blender_exe": None,
    "output_dir": None,
    "presets_dir": None,
    "image_max_pixels": 8_000_000,
    "log_level": "INFO",
    "img_to_3d_port": 7862,
}

# Environment variables that override the file for a key.
ENV_KEYS = {
    "host": "BLENDER_HOST",
    "port": "BLENDER_PORT",
    "blender_exe": "BLENDER_EXE",
    "img_to_3d_port": "IMG_TO_3D_PORT",
}

_lock = threading.RLock()
_file_data: dict = {}          # whole file as loaded (unknown keys preserved)
_file_state: str = "not loaded"  # "loaded" | "not created yet" | "corrupt (...)"
_loaded = False


def settings_dir() -> Path:
    override = os.environ.get(DIR_ENV)
    return Path(override).expanduser() if override else Path.home() / ".blender_mcp"


def settings_path() -> Path:
    return settings_dir() / FILE_NAME


def presets_dir() -> Path:
    custom = get("presets_dir")
    return Path(custom).expanduser() if custom else settings_dir() / "presets"


def _coerce(key: str, value, source: str):
    """Coerce ``value`` to the type of the default for ``key``; None on failure."""
    default = DEFAULTS.get(key)
    if value is None or default is None:
        return value if value in (None,) or isinstance(value, (str, int, float)) else None
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            return str(value)
    except (TypeError, ValueError):
        logger.warning(f"settings: ignoring {source} value for {key!r}: {value!r} is not a {type(default).__name__}")
        return None
    return value


def load() -> dict:
    """(Re)read the settings file. Returns the effective settings. Never raises."""
    global _file_data, _file_state, _loaded
    path = settings_path()
    with _lock:
        _file_data = {}
        if not path.is_file():
            _file_state = "not created yet"
        else:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("top level is not a JSON object")
                _file_data = data
                _file_state = "loaded"
            except Exception as e:  # corrupt file must never take the server down
                _file_state = f"corrupt ({e.__class__.__name__}: {e})"
                logger.warning(f"settings: {path} unreadable, using defaults: {e}")
        _loaded = True
    return effective()


def _ensure_loaded() -> None:
    if not _loaded:
        load()


def effective() -> dict:
    """Merged view: defaults < file < environment (known keys only)."""
    _ensure_loaded()
    out = dict(DEFAULTS)
    with _lock:
        for key in DEFAULTS:
            if key in _file_data:
                v = _coerce(key, _file_data[key], "file")
                if v is not None or _file_data[key] is None:
                    out[key] = v
    for key, env in ENV_KEYS.items():
        raw = os.environ.get(env)
        if raw not in (None, ""):
            v = _coerce(key, raw, f"env {env}")
            if v is not None:
                out[key] = v
    return out


def get(key: str, default=None):
    """Effective value for one known key (env > file > default)."""
    if key not in DEFAULTS:
        return default
    return effective().get(key, default)


def source_of(key: str) -> str:
    """Which layer supplies the effective value: 'env', 'file' or 'default'."""
    env = ENV_KEYS.get(key)
    if env and os.environ.get(env) not in (None, "") and _coerce(key, os.environ[env], "env") is not None:
        return "env"
    _ensure_loaded()
    with _lock:
        if key in _file_data:
            return "file"
    return "default"


def describe() -> dict:
    """{key: {"value": ..., "source": ..., "env": <var or None>}} for every known key."""
    eff = effective()
    return {k: {"value": eff[k], "source": source_of(k), "env": ENV_KEYS.get(k)} for k in DEFAULTS}


def file_state() -> str:
    _ensure_loaded()
    return _file_state


def update(values: dict) -> dict:
    """Merge ``values`` into the file (known keys only, type-checked), write it
    atomically, and return {"saved": {...}, "rejected": {key: reason},
    "overridden_by_env": [keys], "path": str}. Raises OSError only if the file
    cannot be written."""
    if not isinstance(values, dict):
        raise TypeError("values must be a dict")
    _ensure_loaded()
    saved, rejected = {}, {}
    for key, value in values.items():
        if key not in DEFAULTS:
            rejected[key] = f"unknown key; known keys: {', '.join(DEFAULTS)}"
            continue
        if value is None:
            saved[key] = None
            continue
        v = _coerce(key, value, "update")
        if v is None:
            rejected[key] = f"expected {type(DEFAULTS[key]).__name__ if DEFAULTS[key] is not None else 'str'}, got {value!r}"
            continue
        saved[key] = v
    with _lock:
        for key, v in saved.items():
            if v is None:
                _file_data.pop(key, None)   # None = back to default
            else:
                _file_data[key] = v
        _write(_file_data)
        global _file_state
        _file_state = "loaded"
    overridden = [k for k in saved if source_of(k) == "env"]
    return {"saved": saved, "rejected": rejected, "overridden_by_env": overridden, "path": str(settings_path())}


def _write(data: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
