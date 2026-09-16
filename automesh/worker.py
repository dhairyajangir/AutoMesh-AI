import io
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .geometry import blueprint_mask, export_mesh, prepare_image, reconstruct, validate_mask
from .schemas import ProjectConfig, ViewSettings
from .store import Store

logger = logging.getLogger(__name__)
_session = None
_dll_handles = []


class Cancelled(Exception):
    pass


def png_bytes(image) -> bytes:
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def photo_mask(image, store, progress):
    global _session
    # uv's standalone Python does not search the virtualenv for VC runtime DLLs.
    # msvc-runtime supplies these locally; keep the search-directory handles alive.
    if sys.platform == "win32" and not _dll_handles:
        for directory in (Path(sys.prefix), Path(sys.prefix) / "Scripts"):
            _dll_handles.append(os.add_dll_directory(str(directory)))
    # Rembg versions use one of these variables. Both are scoped to our data folder.
    os.environ["U2NET_HOME"] = str(store.root / "models")
    os.environ["REMBG_MODEL_DIR"] = str(store.root / "models")
    os.environ["OMP_NUM_THREADS"] = "2"
    progress("Loading local photo runtime" if _session is None else "Preparing photo inference")
    try:
        from rembg import new_session, remove
    except (ImportError, SystemExit) as exc:
        raise ValueError(
            "Photo inference could not load its native runtime. Re-run setup and check the diagnostics. Paint mask remains available."
        ) from exc
    if _session is None:
        exists = any((store.root / "models").rglob("u2netp.onnx"))
        progress("Loading photo model" if exists else "Downloading photo model (first use)")
        try:
            _session = new_session("u2netp", providers=["CPUExecutionProvider"])
        except Exception as exc:
            logger.warning("Photo model initialization failed: %s", type(exc).__name__)
            raise ValueError(
                "Photo model could not load. Check your internet connection for the first download, or use Paint mask."
            ) from exc
    progress("Separating vehicle from background")
    mask = np.asarray(remove(image, session=_session, only_mask=True))
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    mask = (mask > 127).astype(np.uint8) * 255
    validate_mask(mask)
    return mask


def run_job(store: Store, jid: str):
    job = store.job(jid, internal=True)
    snapshot = job["snapshot"]
    pid = job["project_id"]
    started = time.perf_counter()
    timings = {}
    last_stage = "Starting"
    last_time = started

    def progress(stage):
        nonlocal last_stage, last_time
        if store.job(jid)["state"] in ("cancelling", "cancelled"):
            raise Cancelled()
        timestamp = time.perf_counter()
        timings[last_stage] = round(timestamp - last_time, 4)
        last_stage, last_time = stage, timestamp
        if not store.update_job(jid, "running", stage):
            raise Cancelled()

    try:
        progress("Checking cached result")
        with store.connect() as db:
            cached = db.execute(
                "SELECT result FROM jobs WHERE project_id=? AND cache_key=? AND state='completed' AND id<>? ORDER BY created DESC LIMIT 1",
                (pid, job["cache_key"], jid),
            ).fetchone()
        result = json.loads(cached[0]) if cached else None
        if result:
            result = {
                **result,
                "cached": True,
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            }
            if job["kind"] == "segment":
                view = snapshot["views"][0]
                result["applied"] = store.apply_mask(
                    pid, view["name"], result["mask_id"], result["image_id"], view["revision"]
                )
        elif job["kind"] == "segment":
            view = snapshot["views"][0]
            settings = ViewSettings(**view["settings"])
            progress("Preparing image")
            with Image.open(store.artifact(view["source_id"])["file"]) as source:
                image = prepare_image(source, settings)
            if snapshot["mode"] == "photo":
                mask = photo_mask(image, store, progress)
            else:
                progress("Extracting blueprint silhouette")
                mask = blueprint_mask(image, settings.threshold, settings.invert)
            progress("Saving editable mask")
            image_id = store.add_artifact(
                pid, "prepared", png_bytes(image), "png", "image/png", jid
            )["id"]
            mask_id = store.add_artifact(pid, "mask", png_bytes(mask), "png", "image/png", jid)[
                "id"
            ]
            result = {
                "image_id": image_id,
                "mask_id": mask_id,
                "cached": False,
                "applied": store.apply_mask(pid, view["name"], mask_id, image_id, view["revision"]),
            }
        else:
            progress("Reading reviewed masks")
            masks = {}
            for view in snapshot["views"]:
                with Image.open(store.artifact(view["mask_id"])["file"]) as image:
                    masks[view["name"]] = np.array(image.convert("L"))
            settings = {v["name"]: ViewSettings(**v["settings"]) for v in snapshot["views"]}
            mesh, stats, projections = reconstruct(
                masks, settings, ProjectConfig(**snapshot["config"]), progress
            )
            if snapshot["mode"] == "photo":
                stats["warnings"].insert(
                    0,
                    "Photographs are treated as orthographic views. Perspective and lens distortion are not calibrated.",
                )
            progress("Writing mesh files")
            artifacts = {}
            for ext, mime in (
                ("glb", "model/gltf-binary"),
                ("stl", "model/stl"),
                ("obj", "text/plain"),
                ("ply", "application/octet-stream"),
            ):
                progress(f"Exporting {ext.upper()}")
                artifacts[ext] = store.add_artifact(
                    pid,
                    ext,
                    export_mesh(mesh, ext, stats["unit"]),
                    ext,
                    mime,
                    jid,
                    f"vehicle.{ext}",
                )["id"]
            projection_ids = {
                name: store.add_artifact(
                    pid,
                    "projection",
                    png_bytes(mask),
                    "png",
                    "image/png",
                    jid,
                    f"{name}-projection.png",
                )["id"]
                for name, mask in projections.items()
            }
            progress("Saving reconstruction report")
            result = {
                "stats": stats,
                "artifacts": artifacts,
                "projections": projection_ids,
                "cached": False,
                "settings": snapshot["config"],
                "revision": job["revision"],
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "timings": timings,
            }
            report = {
                **result,
                "inputs": snapshot["views"],
                "mode": snapshot["mode"],
                "export_units": {
                    "glb": "meters if scaled; arbitrary scene units otherwise",
                    "stl": stats["unit"],
                    "obj": stats["unit"],
                    "ply": stats["unit"],
                },
            }
            artifacts["report"] = store.add_artifact(
                pid,
                "report",
                json.dumps(report, indent=2).encode(),
                "json",
                "application/json",
                jid,
                "reconstruction-report.json",
            )["id"]
        progress("Complete")
        if "elapsed_seconds" not in result:
            result["elapsed_seconds"] = round(time.perf_counter() - started, 4)
        if not store.update_job(jid, "completed", "Complete", result=result):
            raise Cancelled()
    except Cancelled:
        store.update_job(jid, "cancelled", "Cancelled")
    except (ValueError, KeyError, OSError) as exc:
        logger.exception("Job %s failed", jid)
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "An input file could not be read or written. Re-upload it and check free disk space."
        )
        store.update_job(jid, "failed", "Needs attention", error=message)
    except Exception:
        logger.exception("Unexpected worker error for %s", jid)
        store.update_job(
            jid,
            "failed",
            "Needs attention",
            error="Reconstruction stopped unexpectedly. Your inputs are saved. Retry or inspect the local server log.",
        )


def worker_loop(root: str, stop):
    store = Store(Path(root))
    while not stop.is_set():
        with store.connect() as db:
            row = db.execute(
                "SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1"
            ).fetchone()
        if row:
            run_job(store, row[0])
        else:
            stop.wait(0.25)
