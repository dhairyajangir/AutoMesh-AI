"""Run the attributed photo pair through real CPU segmentation and reconstruction."""

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import trimesh
from PIL import Image

from automesh.geometry import export_mesh, reconstruct
from automesh.schemas import ProjectConfig, ViewSettings
from automesh.store import Store
from automesh.worker import photo_mask, png_bytes


def main():
    offline = "--offline" in sys.argv
    if offline:
        import socket

        import requests

        def deny_network(*args, **kwargs):
            raise OSError("Network disabled for offline validation")

        socket.create_connection = deny_network
        requests.sessions.Session.request = deny_network
    store = Store(ROOT / ".local")
    masks = {}
    segmentation = {}
    for name in ("side", "front"):
        image = Image.open(ROOT / f"data/examples/photos/{name}.jpg").convert("RGB")
        started = time.perf_counter()
        mask = photo_mask(
            image, store, lambda stage, name=name: print(f"{name}: {stage}", flush=True)
        )
        segmentation[name] = time.perf_counter() - started
        masks[name] = mask
        (ROOT / f"validation/photo-{name}-mask.png").write_bytes(png_bytes(mask))
    started = time.perf_counter()
    mesh, stats, _ = reconstruct(
        masks, {name: ViewSettings() for name in masks}, ProjectConfig(resolution=128)
    )
    reconstruction_seconds = time.perf_counter() - started
    stats["warnings"].insert(
        0,
        "Front photograph is oblique. This capture-mismatch example is not a dimensional reference.",
    )
    checks = {}
    for extension in ("glb", "stl", "obj", "ply"):
        data = export_mesh(mesh, extension, "relative")
        (ROOT / f"validation/photo-vehicle.{extension}").write_bytes(data)
        restored = trimesh.load(io.BytesIO(data), file_type=extension, force="mesh", process=True)
        assert np.isfinite(restored.vertices).all()
        assert np.allclose(restored.bounds, mesh.bounds, atol=1e-5)
        assert restored.is_winding_consistent
        checks[extension] = {
            "bytes": len(data),
            "watertight": bool(restored.is_watertight),
            "bounds_match": True,
        }
    report = {
        "offline_network_blocked": offline,
        "segmentation_seconds": segmentation,
        "reconstruction_seconds": reconstruction_seconds,
        "stats": stats,
        "export_checks": checks,
        "attribution": "Manuel Strehl, 2005, CC BY-SA 2.5; see data/examples/photos/ATTRIBUTION.md",
    }
    (ROOT / "validation/photo-validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
