"""Deterministic, explicitly synthetic example. Never used as an upload fallback."""

import numpy as np
from PIL import Image, ImageDraw

from .schemas import JobRequest, ViewSettings
from .worker import png_bytes


def sample_masks():
    side = Image.new("L", (1024, 480), 0)
    d = ImageDraw.Draw(side)
    d.polygon(
        [
            (45, 295),
            (70, 230),
            (250, 208),
            (370, 107),
            (625, 107),
            (750, 212),
            (934, 242),
            (980, 286),
            (962, 347),
            (60, 347),
        ],
        fill=255,
    )
    d.ellipse((143, 283, 293, 427), fill=255)
    d.ellipse((753, 283, 903, 427), fill=255)
    front = Image.new("L", (600, 480), 0)
    d = ImageDraw.Draw(front)
    d.polygon(
        [
            (49, 342),
            (49, 243),
            (111, 217),
            (156, 110),
            (444, 110),
            (489, 217),
            (551, 243),
            (551, 342),
        ],
        fill=255,
    )
    d.rounded_rectangle((64, 307, 142, 427), radius=15, fill=255)
    d.rounded_rectangle((458, 307, 536, 427), radius=15, fill=255)
    top = Image.new("L", (1024, 580), 0)
    d = ImageDraw.Draw(top)
    d.polygon(
        [
            (47, 158),
            (110, 91),
            (815, 67),
            (945, 104),
            (980, 172),
            (980, 408),
            (945, 476),
            (815, 513),
            (110, 489),
            (47, 422),
        ],
        fill=255,
    )
    return {"side": np.array(side), "front": np.array(front), "top": np.array(top)}


def seed_example(store):
    if store.projects():
        return
    project = store.create_project("Touring coupe · example", "blueprint")
    pid = project["id"]
    for name, mask in sample_masks().items():
        # A visible, clean source drawing, distinct from the editable mask.
        image = Image.new("RGB", (mask.shape[1], mask.shape[0]), "#e8edef")
        vehicle = Image.new("RGB", image.size, "#43535b")
        image.paste(vehicle, mask=Image.fromarray(mask))
        draw = ImageDraw.Draw(image)
        if name == "side":
            draw.line(
                [(296, 207), (388, 130), (605, 130), (704, 207), (296, 207)],
                fill="#b8cbd1",
                width=3,
            )
            draw.line([(496, 130), (496, 208)], fill="#b8cbd1", width=3)
            for x in [218, 828]:
                draw.ellipse((x - 50, 305, x + 50, 405), outline="#b8cbd1", width=3)
        elif name == "front":
            draw.polygon(
                [(175, 130), (425, 130), (467, 219), (133, 219)], outline="#b8cbd1", width=3
            )
        else:
            draw.rounded_rectangle((310, 145, 685, 435), radius=40, outline="#b8cbd1", width=3)
        source_id = store.add_artifact(
            pid, "source", png_bytes(image), "png", "image/png", name=f"synthetic-{name}.png"
        )["id"]
        mask_id = store.add_artifact(pid, "mask", png_bytes(mask), "png", "image/png")["id"]
        store.put_view(pid, name, source_id)
        store.settings(pid, name, ViewSettings(orientation_confirmed=True))
        view = store.view(pid, name)
        store.apply_mask(pid, name, mask_id, source_id, view["revision"])
    store.create_job(pid, JobRequest(kind="reconstruct"))
