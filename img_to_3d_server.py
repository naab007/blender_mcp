"""
Local image-to-3D inference server using TripoSR.

Start via load_img_to_3d_model() MCP tool, or manually:
    IMG_TO_3D_PORT=7862 python img_to_3d_server.py

Environment variables:
    IMG_TO_3D_PORT         TCP port (default 7862)
    IMG_TO_3D_MODEL_DIR    TripoSR weights directory (default: downloads automatically)
    IMG_TO_3D_CHUNK_SIZE   Render chunk size for memory control (default 8192)
    IMG_TO_3D_DEVICE       torch device string, e.g. "cuda:0" or "cpu" (default: auto)

Endpoints:
    GET  /status            Returns {"status": "ready"}
    POST /generate          Accepts multipart form with:
                              image          — image file
                              foreground_ratio — float, default 0.85
                              mc_resolution  — int, default 256
                              no_remove_bg   — "1" to skip bg removal
                            Returns the raw .glb binary on success (Content-Type: model/gltf-binary)
"""

import os
import sys
import io
import tempfile
import traceback

PORT = int(os.environ.get("IMG_TO_3D_PORT", "7862"))
MODEL_DIR = os.environ.get("IMG_TO_3D_MODEL_DIR", "stabilityai/TripoSR")
CHUNK_SIZE = int(os.environ.get("IMG_TO_3D_CHUNK_SIZE", "8192"))
DEVICE_OVERRIDE = os.environ.get("IMG_TO_3D_DEVICE", "")

print(f"[img_to_3d_server] Starting on port {PORT}", flush=True)
print(f"[img_to_3d_server] Model dir: {MODEL_DIR}", flush=True)

# ─── Lazy-loaded globals ───────────────────────────────────────────────────────
_model = None
_device = None


def get_device():
    global _device
    if _device is None:
        import torch
        if DEVICE_OVERRIDE:
            _device = torch.device(DEVICE_OVERRIDE)
        elif torch.cuda.is_available():
            _device = torch.device("cuda:0")
        else:
            _device = torch.device("cpu")
    return _device


def get_model():
    global _model
    if _model is None:
        print("[img_to_3d_server] Loading TripoSR model…", flush=True)
        try:
            from tsr.system import TSR
        except ImportError:
            raise RuntimeError(
                "TripoSR is not installed. "
                "Install it with: pip install git+https://github.com/VAST-AI-Research/TripoSR"
            )
        device = get_device()
        _model = TSR.from_pretrained(
            MODEL_DIR,
            config_name="config.yaml",
            weight_name="model.ckpt",
        )
        _model = _model.to(device)
        print(f"[img_to_3d_server] Model loaded on {device}", flush=True)
    return _model


# ─── Flask app ─────────────────────────────────────────────────────────────────
try:
    from flask import Flask, request, jsonify, Response
except ImportError:
    raise RuntimeError(
        "Flask is not installed. Install it with: pip install flask"
    )

app = Flask(__name__)


@app.route("/status")
def status():
    return jsonify({"status": "ready", "port": PORT, "model_dir": MODEL_DIR})


@app.route("/generate", methods=["POST"])
def generate():
    try:
        import torch
        from PIL import Image as PILImage

        # ── Read parameters ──────────────────────────────────────────────────
        fg_ratio = float(request.form.get("foreground_ratio", 0.85))
        mc_res   = int(request.form.get("mc_resolution", 256))
        skip_bg  = request.form.get("no_remove_bg", "0") == "1"

        if "image" not in request.files:
            return jsonify({"error": "No 'image' field in request"}), 400

        img_bytes = request.files["image"].read()
        image = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")

        # ── Optional background removal ──────────────────────────────────────
        if not skip_bg:
            try:
                from rembg import remove as rembg_remove
                image = rembg_remove(image)
                print("[img_to_3d_server] Background removed via rembg", flush=True)
            except ImportError:
                print("[img_to_3d_server] rembg not available, skipping bg removal", flush=True)

        # ── Crop to foreground ────────────────────────────────────────────────
        image = _crop_to_foreground(image, fg_ratio)

        # ── Run TripoSR ───────────────────────────────────────────────────────
        model = get_model()
        device = get_device()

        with torch.no_grad():
            scene_codes = model([image], device=device)
            meshes = model.extract_mesh(scene_codes, resolution=mc_res)

        mesh = meshes[0]

        # ── Export to GLB ─────────────────────────────────────────────────────
        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
            tmp_path = tmp.name

        mesh.export(tmp_path)
        with open(tmp_path, "rb") as f:
            glb_bytes = f.read()
        os.unlink(tmp_path)

        print(f"[img_to_3d_server] Generated mesh: {len(glb_bytes)//1024} KB", flush=True)
        return Response(glb_bytes, mimetype="model/gltf-binary")

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _crop_to_foreground(image, ratio):
    """Pad/crop a RGBA image so the foreground fills `ratio` of the shortest side."""
    from PIL import Image as PILImage
    import numpy as np

    arr = np.array(image)
    alpha = arr[:, :, 3]
    rows = np.any(alpha > 0, axis=1)
    cols = np.any(alpha > 0, axis=0)

    if not rows.any():
        return image

    r_min, r_max = np.where(rows)[0][[0, -1]]
    c_min, c_max = np.where(cols)[0][[0, -1]]

    fg_h = r_max - r_min + 1
    fg_w = c_max - c_min + 1
    img_h, img_w = arr.shape[:2]

    # Target canvas size so fg occupies `ratio` of it
    canvas = max(int(fg_h / ratio), int(fg_w / ratio))
    pad_y  = (canvas - fg_h) // 2
    pad_x  = (canvas - fg_w) // 2

    # Crop with padding applied
    y0 = max(r_min - pad_y, 0)
    y1 = min(r_max + pad_y + 1, img_h)
    x0 = max(c_min - pad_x, 0)
    x1 = min(c_max + pad_x + 1, img_w)

    cropped = arr[y0:y1, x0:x1]

    # Pad to square if needed
    h, w = cropped.shape[:2]
    sq = max(h, w)
    padded = np.zeros((sq, sq, 4), dtype=np.uint8)
    dy = (sq - h) // 2
    dx = (sq - w) // 2
    padded[dy:dy+h, dx:dx+w] = cropped

    return PILImage.fromarray(padded)


if __name__ == "__main__":
    # Warm up the model before accepting requests
    try:
        get_model()
    except Exception as e:
        print(f"[img_to_3d_server] WARNING: could not pre-load model: {e}", flush=True)
        print("[img_to_3d_server] Model will load on first /generate request", flush=True)

    app.run(host="127.0.0.1", port=PORT, threaded=False)
