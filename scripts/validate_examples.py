"""Reconstruct the actual repository blueprint and benchmark prepared masks."""

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

from automesh.geometry import blueprint_mask, export_mesh, prepare_image, reconstruct
from automesh.schemas import ProjectConfig, ViewSettings
from automesh.worker import png_bytes


def main():
    output = ROOT / "validation"
    output.mkdir(exist_ok=True)
    source = Image.open(ROOT / "data/blueprints/car_blueprint.jpeg").convert("RGB")
    width, height = source.size
    # These are reviewed crop boxes for this supplied sheet, not universal auto-detection.
    boxes = {"side": (28, 20, 425, 148), "front": (300, 245, 417, 320), "top": (33, 325, 241, 424)}
    masks = {}
    for name, (x0, y0, x1, y1) in boxes.items():
        settings = ViewSettings(
            crop=(x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height),
            flip_x=name in ("side", "top"),
            threshold=220,
        )
        prepared = prepare_image(source, settings)
        mask = blueprint_mask(prepared, 220)
        masks[name] = mask
        (output / f"blueprint-{name}-source.png").write_bytes(png_bytes(prepared))
        (output / f"blueprint-{name}-mask.png").write_bytes(png_bytes(mask))
    timings = []
    for resolution in [128, 256, 384]:
        started = time.perf_counter()
        mesh, stats, _projections = reconstruct(
            masks, {name: ViewSettings() for name in masks}, ProjectConfig(resolution=resolution)
        )
        geometry_seconds = time.perf_counter() - started
        exports = {}
        for ext in ["glb", "stl", "obj", "ply"]:
            data = export_mesh(mesh, ext, "relative")
            restored = trimesh.load(io.BytesIO(data), file_type=ext, force="mesh", process=True)
            assert np.isfinite(restored.vertices).all() and restored.is_watertight
            assert np.allclose(restored.bounds, mesh.bounds, atol=1e-5)
            if resolution == 256:
                (output / f"blueprint-vehicle.{ext}").write_bytes(data)
            exports[ext] = {
                "bytes": len(data),
                "watertight_after_import": bool(restored.is_watertight),
            }
        record = {
            "resolution": resolution,
            "geometry_seconds": geometry_seconds,
            "with_exports_and_reimport_seconds": time.perf_counter() - started,
            "stats": stats,
            "exports": exports,
        }
        timings.append(record)
        print(
            json.dumps(
                {
                    "resolution": resolution,
                    "geometry_seconds": round(geometry_seconds, 3),
                    "triangles": len(mesh.faces),
                    "watertight": bool(mesh.is_watertight),
                }
            ),
            flush=True,
        )
    (output / "blueprint-validation.json").write_text(
        json.dumps(timings, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
