import asyncio
import io
import json
import logging
import multiprocessing as mp
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .geometry import fit_projection, prepare_image, tight_mask, validate_mask
from .schemas import (
    JobInfo,
    JobRequest,
    ProjectCreate,
    ProjectInfo,
    ProjectPatch,
    ViewName,
    ViewSettings,
)
from .store import ROOT, Store
from .worker import png_bytes, worker_loop

MAX_UPLOAD = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 30_000_000
logger = logging.getLogger(__name__)


def decode_image(data: bytes, mask=False):
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Upload a PNG, JPEG, or WebP smaller than 20 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ({"PNG"} if mask else {"PNG", "JPEG", "WEBP"}):
                raise HTTPException(415, "Use PNG for masks; PNG, JPEG, or WebP for images.")
            if min(source.size) < 8 or source.width * source.height > 30_000_000:
                raise HTTPException(
                    422, "Images must be at least 8 × 8 pixels and at most 30 megapixels."
                )
            source.load()
            image = ImageOps.exif_transpose(source)
            if mask:
                return image.convert("L")
            if "A" in image.getbands():
                base = Image.new("RGBA", image.size, "white")
                base.alpha_composite(image.convert("RGBA"))
                image = base
            return image.convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(
            422, "This image cannot be decoded. Re-export it as PNG or JPEG."
        ) from exc


def create_app(data_dir: str | Path | None = None, run_worker=True, seed=True):
    store = Store(data_dir)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        store.recover()
        if seed:
            from .demo import seed_example

            seed_example(store)
        ctx = mp.get_context("spawn")
        stop = ctx.Event()
        process = None
        monitor_stop = threading.Event()

        def launch():
            child = ctx.Process(target=worker_loop, args=(str(store.root), stop), daemon=True)
            child.start()
            return child

        def monitor():
            nonlocal process
            cancelling_since = {}
            while not monitor_stop.wait(0.5):
                with store.connect() as db:
                    active = list(
                        db.execute(
                            "SELECT id,state FROM jobs WHERE state IN ('running','cancelling')"
                        )
                    )
                terminate = False
                for row in active:
                    if row["state"] == "cancelling":
                        cancelling_since.setdefault(row["id"], time.monotonic())
                        if time.monotonic() - cancelling_since[row["id"]] > 5:
                            terminate = True
                if process and (not process.is_alive() or terminate):
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=2)
                    for row in active:
                        cancelled = row["state"] == "cancelling"
                        store.update_job(
                            row["id"],
                            "cancelled" if cancelled else "failed",
                            "Cancelled" if cancelled else "Worker restarted",
                            error=None
                            if cancelled
                            else "The worker stopped. Retry this job; inputs are saved.",
                        )
                    cancelling_since.clear()
                    if not monitor_stop.is_set():
                        process = launch()

        if run_worker:
            process = launch()
            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
        yield
        monitor_stop.set()
        if run_worker:
            monitor_thread.join(timeout=2)
        stop.set()
        if process:
            process.join(timeout=3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)

    app = FastAPI(title="AutoMesh Studio", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
    )

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):
            origin = request.headers.get("origin")
            allowed = {
                f"http://{host}:{port}"
                for host in ("localhost", "127.0.0.1")
                for port in (8765, 5173)
            } | {"http://testserver"}
            if (origin and origin not in allowed) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse(
                    {"detail": "Cross-site requests are not allowed."}, status_code=403
                )
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > MAX_UPLOAD + 1024 * 1024):
                return JSONResponse({"detail": "Upload exceeds the 20 MB limit."}, status_code=413)
            if "multipart/form-data" in request.headers.get("content-type", "") and length is None:
                return JSONResponse(
                    {"detail": "A bounded Content-Length is required for uploads."}, status_code=411
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {
            "application": "automesh-studio",
            "status": "ok",
            "version": __version__,
            "compute": "Local CPU",
            "model_ready": any((store.root / "models").rglob("u2netp.onnx")),
        }

    @app.get("/api/projects", response_model=list[ProjectInfo])
    def projects():
        return store.projects()

    @app.post("/api/projects", response_model=ProjectInfo, status_code=201)
    def create_project(body: ProjectCreate):
        return store.create_project(body.name, body.mode)

    @app.get("/api/projects/{pid}", response_model=ProjectInfo)
    def project(pid: str):
        return store.project(pid)

    @app.patch("/api/projects/{pid}", response_model=ProjectInfo)
    def patch_project(pid: str, body: ProjectPatch):
        return store.patch_project(pid, body)

    @app.post("/api/projects/{pid}/views/{name}", response_model=ProjectInfo)
    async def upload_view(pid: str, name: ViewName, file: Annotated[UploadFile, File()]):
        store.project(pid)
        image = decode_image(await file.read(MAX_UPLOAD + 1))
        artifact = store.add_artifact(
            pid, "source", png_bytes(image), "png", "image/png", name=f"{name}-source.png"
        )
        return store.put_view(pid, name, artifact["id"])

    @app.put("/api/projects/{pid}/views/{name}", response_model=ProjectInfo)
    def view_settings(pid: str, name: ViewName, body: ViewSettings):
        return store.settings(pid, name, body)

    @app.delete("/api/projects/{pid}/views/{name}", response_model=ProjectInfo)
    def delete_view(pid: str, name: ViewName):
        store.project(pid)
        return store.remove_view(pid, name)

    @app.get("/api/projects/{pid}/views/{name}/image")
    def prepared_view(pid: str, name: ViewName):
        view = store.view(pid, name)
        if not view:
            raise KeyError("View not found")
        with Image.open(store.artifact(view["source_id"])["file"]) as source:
            image = prepare_image(source, ViewSettings(**view["settings"]))
        return Response(
            png_bytes(image), media_type="image/png", headers={"Cache-Control": "no-cache"}
        )

    @app.put("/api/projects/{pid}/views/{name}/mask", response_model=ProjectInfo)
    async def save_mask(
        pid: str, name: ViewName, revision: int, file: Annotated[UploadFile, File()]
    ):
        view = store.view(pid, name)
        if not view:
            raise KeyError("View not found")
        if view["revision"] != revision:
            raise HTTPException(409, "This view changed. Reload it before saving your mask.")
        image = decode_image(await file.read(MAX_UPLOAD + 1), mask=True)
        import numpy as np

        binary = validate_mask(np.array(image)).astype(np.uint8) * 255
        with Image.open(store.artifact(view["source_id"])["file"]) as source:
            prepared = prepare_image(source, ViewSettings(**view["settings"]))
        if image.size != prepared.size:
            raise HTTPException(422, "Mask dimensions must match the prepared image.")
        mask_id = store.add_artifact(pid, "mask", png_bytes(binary), "png", "image/png")["id"]
        image_id = (
            view["image_id"]
            or store.add_artifact(pid, "prepared", png_bytes(prepared), "png", "image/png")["id"]
        )
        if not store.apply_mask(pid, name, mask_id, image_id, revision):
            raise HTTPException(
                409, "The view changed while saving. Reload it before saving again."
            )
        return store.project(pid)

    @app.get("/api/projects/{pid}/views/{name}/alignment")
    def alignment_image(pid: str, name: ViewName):
        import numpy as np

        project = store.project(pid)
        view = next((v for v in project["views"] if v["name"] == name), None)
        if not view or not view["mask_id"]:
            raise KeyError("Extract a mask first")
        masks = {}
        for v in project["views"]:
            if v["mask_id"]:
                with Image.open(store.artifact(v["mask_id"])["file"]) as im:
                    masks[v["name"]] = np.array(im.convert("L"))
        if {"side", "front"}.issubset(masks):
            side, front = tight_mask(masks["side"]), tight_mask(masks["front"])
            length, height, width = (
                side.shape[1] / side.shape[0],
                1.0,
                front.shape[1] / front.shape[0],
            )
            w, h = {
                "side": (length, height),
                "front": (width, height),
                "rear": (width, height),
                "top": (length, width),
            }[name]
        else:
            cropped = tight_mask(masks[name])
            h, w = cropped.shape
        target_w, target_h = round(w / max(w, h) * 900), round(h / max(w, h) * 900)
        projection = fit_projection(
            masks[name], target_w, target_h, ViewSettings(**view["settings"]), ground=name != "top"
        )
        rgba = np.zeros((*projection.shape, 4), dtype=np.uint8)
        rgba[:, :, :3] = [77, 201, 191]
        rgba[:, :, 3] = projection.astype(np.uint8) * 200
        return Response(
            png_bytes(rgba), media_type="image/png", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/projects/{pid}/jobs", response_model=list[JobInfo])
    def jobs(pid: str):
        store.project(pid)
        return store.jobs(pid)

    @app.post("/api/projects/{pid}/jobs", response_model=JobInfo, status_code=202)
    def queue_job(pid: str, body: JobRequest):
        return store.create_job(pid, body)

    @app.get("/api/jobs/{jid}", response_model=JobInfo)
    def job(jid: str):
        return store.job(jid)

    @app.post("/api/jobs/{jid}/cancel", response_model=JobInfo)
    def cancel(jid: str):
        return store.cancel(jid)

    @app.get("/api/jobs/{jid}/events")
    async def events(jid: str, request: Request):
        store.job(jid)

        async def stream():
            last = None
            while not await request.is_disconnected():
                data = store.job(jid)
                encoded = json.dumps(data)
                if encoded != last:
                    yield f"data: {encoded}\n\n"
                    last = encoded
                if data["state"] in ("completed", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.4)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/artifacts/{aid}")
    def artifact(aid: str, download: bool = False):
        info = store.artifact(aid)
        return FileResponse(
            info["file"],
            media_type=info["mime"],
            filename=info["name"] if download else None,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @app.get("/api/examples/blueprint")
    def original_blueprint():
        return FileResponse(
            ROOT / "data" / "blueprints" / "car_blueprint.jpeg",
            media_type="image/jpeg",
            filename="car_blueprint.jpeg",
        )

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse(
            {
                "message": "Build the frontend first: cd frontend; npm ci; npm run build",
                "api": "/docs",
            }
        )

    return app


app = create_app()
