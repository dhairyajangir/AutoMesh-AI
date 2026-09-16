"""Read-only setup diagnostics; never downloads a model."""

import json
import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
handles = []
if sys.platform == "win32":
    handles = [
        os.add_dll_directory(str(p)) for p in (Path(sys.prefix), Path(sys.prefix) / "Scripts")
    ]
report = {
    "python": sys.version,
    "platform": platform.platform(),
    "frontend_built": (ROOT / "frontend/dist/index.html").exists(),
    "model_cached": any((ROOT / ".local/models").rglob("u2netp.onnx")),
}
for package in (
    "fastapi",
    "numpy",
    "opencv-python-headless",
    "scikit-image",
    "trimesh",
    "rembg",
    "onnxruntime",
):
    try:
        report[package] = version(package)
    except PackageNotFoundError as exc:
        report[package] = str(exc)
try:
    import onnxruntime

    report["onnx_providers"] = onnxruntime.get_available_providers()
except (ImportError, OSError, RuntimeError) as exc:
    report["onnx_error"] = str(exc)
print(json.dumps(report, indent=2))
sys.exit(1 if "onnx_error" in report else 0)
