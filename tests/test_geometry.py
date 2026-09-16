import io

import numpy as np
import pytest
import trimesh
from PIL import Image

from automesh.demo import sample_masks
from automesh.geometry import (
    blueprint_mask,
    export_mesh,
    fit_projection,
    prepare_image,
    reconstruct,
    validate_mask,
)
from automesh.schemas import ProjectConfig, ViewSettings


def test_fragmented_masks_rejected_before_large_mesh_allocation():
    mask = ((np.indices((128, 128)).sum(axis=0) % 2) * 255).astype(np.uint8)
    with pytest.raises(ValueError, match="fragmented"):
        reconstruct(
            {"side": mask, "front": mask},
            {"side": ViewSettings(), "front": ViewSettings()},
            ProjectConfig(resolution=128),
        )


def box_masks():
    side = np.zeros((60, 180), np.uint8)
    front = np.zeros((60, 90), np.uint8)
    top = np.zeros((90, 180), np.uint8)
    side[10:50, 10:170] = 255
    front[10:50, 10:80] = 255
    top[10:80, 10:170] = 255
    return {"side": side, "front": front, "top": top}


def build(masks=None, **config):
    masks = masks or box_masks()
    return reconstruct(masks, {name: ViewSettings() for name in masks}, ProjectConfig(**config))


def test_cuboid_dimensions_and_closed_surface():
    mesh, stats, projections = build(resolution=128)
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert np.isfinite(mesh.vertices).all() and mesh.volume > 0
    assert stats["components"] == 1
    assert np.allclose(mesh.extents, [4, 1, 1.75], atol=4 / 128)
    assert all(value == 1 for value in stats["projection_iou"].values())
    assert set(projections) == {"side", "front", "top"}


def test_asymmetric_vehicle_has_correct_front_and_rear():
    masks = sample_masks()
    mesh, stats, projections = build(masks, resolution=128)
    side = projections["side"] > 0
    # Roof of our source fixture starts behind the center; front (+X) hood is lower.
    front_heights = side[:, int(side.shape[1] * 0.88)].sum()
    cabin_heights = side[:, int(side.shape[1] * 0.48)].sum()
    assert front_heights < cabin_heights
    assert mesh.is_watertight and stats["components"] == 1
    assert abs(mesh.bounds[:, 2].mean()) < 0.05


@pytest.mark.parametrize("fmt", ["glb", "stl", "obj", "ply"])
def test_export_roundtrip(fmt):
    mesh, _, _ = build(resolution=128)
    data = export_mesh(mesh, fmt, "relative")
    restored = trimesh.load(io.BytesIO(data), file_type=fmt, force="mesh", process=True)
    assert restored.is_watertight
    assert np.allclose(restored.bounds, mesh.bounds, atol=1e-5)
    assert len(restored.faces) == len(mesh.faces)


def test_scaled_exports_have_explicit_unit_conversion():
    mesh, stats, _ = build(resolution=128, known_axis="length", known_dimension=4200, unit="mm")
    assert mesh.extents[0] == pytest.approx(4200)
    assert stats["unit"] == "mm"
    glb = trimesh.load(io.BytesIO(export_mesh(mesh, "glb", "mm")), file_type="glb", force="mesh")
    stl = trimesh.load(io.BytesIO(export_mesh(mesh, "stl", "mm")), file_type="stl", force="mesh")
    assert glb.extents[0] == pytest.approx(4.2, rel=1e-5)
    assert stl.extents[0] == pytest.approx(4200, rel=1e-5)


def test_missing_top_is_not_invented():
    masks = box_masks()
    del masks["top"]
    mesh, stats, projections = build(masks, resolution=128)
    assert mesh.is_watertight
    assert "top" not in stats["source_views"] and "top" not in projections
    assert any("Top profile unconstrained" in w for w in stats["warnings"])


@pytest.mark.parametrize(
    "mask",
    [np.zeros((32, 32), np.uint8), np.ones((32, 32), np.uint8) * 255, np.zeros((4, 4), np.uint8)],
)
def test_bad_masks_explain_recovery(mask):
    with pytest.raises(ValueError):
        validate_mask(mask)


def test_masks_must_overlap():
    masks = box_masks()
    settings = {name: ViewSettings() for name in masks}
    settings["side"] = ViewSettings(offset_y=1)
    with pytest.raises(ValueError, match="do not overlap"):
        reconstruct(masks, settings, ProjectConfig(resolution=128))


def test_uniform_fit_preserves_aspect_ratio():
    mask = np.zeros((60, 180), np.uint8)
    mask[10:50, 10:170] = 255
    fitted = fit_projection(mask, 100, 100, ViewSettings())
    rows, cols = np.nonzero(fitted)
    assert (cols.max() - cols.min() + 1) / (rows.max() - rows.min() + 1) == pytest.approx(
        4, abs=0.2
    )


def test_image_crop_rotate_and_flip():
    a = np.zeros((80, 120, 3), np.uint8)
    a[:, :60] = [255, 0, 0]
    im = prepare_image(Image.fromarray(a), ViewSettings(crop=(0, 0, 0.5, 1), rotation=90))
    assert im.size == (80, 60)
    assert np.asarray(im)[0, 0, 0] == 255


def test_blueprint_extraction_and_annotation_filter():
    im = np.full((80, 180, 3), 255, np.uint8)
    im[20:65, 20:160] = 0
    im[5:8, 5:8] = 0
    mask = blueprint_mask(Image.fromarray(im))
    assert mask[30, 80] == 255 and mask[6, 6] == 0


@pytest.mark.parametrize(
    "kwargs", [{"crop": (0, 0, 0, 1)}, {"crop": (0.5, 0, 0.6, 1)}, {"scale": 100}]
)
def test_invalid_transforms_rejected(kwargs):
    with pytest.raises(ValueError):
        ViewSettings(**kwargs)


def test_cancellation_checkpoint_interrupts_geometry():
    def progress(stage):
        if stage == "Extracting surface":
            raise RuntimeError("cancelled")

    masks = box_masks()
    with pytest.raises(RuntimeError, match="cancelled"):
        reconstruct(
            masks, {n: ViewSettings() for n in masks}, ProjectConfig(resolution=128), progress
        )
