"""Orthographic visual hull. Coordinates: X forward, Y up, Z vehicle right.

Side and top inputs have the nose pointing right. Front is viewed from ahead;
rear is viewed from behind. These are assumptions for photographs, not calibration.
"""

from collections.abc import Callable

import cv2
import numpy as np
import trimesh
from PIL import Image
from skimage.measure import marching_cubes

from .schemas import ProjectConfig, ViewSettings

cv2.setNumThreads(2)


def prepare_image(image: Image.Image, settings: ViewSettings) -> Image.Image:
    x, y, w, h = settings.crop
    width, height = image.size
    box = (round(x * width), round(y * height), round((x + w) * width), round((y + h) * height))
    result = image.crop(box)
    if min(result.size) < 8:
        raise ValueError("The crop is too small. Keep at least 8 pixels on each side.")
    if settings.rotation:
        result = result.rotate(-settings.rotation, expand=True)
    if settings.flip_x:
        result = result.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    result.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    return result.convert("RGB")


def blueprint_mask(image: Image.Image, threshold: int = 160, invert: bool = False) -> np.ndarray:
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    foreground = (gray < threshold) if not invert else (gray > threshold)
    binary = foreground.astype(np.uint8) * 255
    # Close small breaks in outline drawings; annotations remain editable.
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("No silhouette found. Adjust threshold or paint a mask manually.")
    mask = np.zeros_like(binary)
    cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 255, cv2.FILLED)
    validate_mask(mask)
    return mask


def validate_mask(mask: np.ndarray) -> np.ndarray:
    if mask.ndim != 2 or min(mask.shape) < 8:
        raise ValueError("Mask must be a grayscale image at least 8 × 8 pixels.")
    binary = mask > 127
    count = int(binary.sum())
    if count < 16:
        raise ValueError("Mask is empty or too small. Paint the vehicle foreground.")
    if count == binary.size:
        raise ValueError("Mask fills the entire image. Leave background around the vehicle.")
    return binary


def tight_mask(mask: np.ndarray) -> np.ndarray:
    mask = validate_mask(mask)
    ys, xs = np.nonzero(mask)
    return mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def fit_projection(
    mask: np.ndarray, width: int, height: int, settings: ViewSettings, ground=True
) -> np.ndarray:
    """Uniform fit, with explicit offsets. Never independently stretch axes."""
    cropped = tight_mask(mask).astype(np.uint8)
    h, w = cropped.shape
    factor = min(width / w, height / h) * settings.scale
    scaled_w, scaled_h = w * factor, h * factor
    matrix = np.array(
        [
            [factor, 0, (width - scaled_w) / 2 + settings.offset_x * width],
            [0, factor, (height - scaled_h) / (1 if ground else 2) + settings.offset_y * height],
        ],
        dtype=np.float32,
    )
    return cv2.warpAffine(cropped, matrix, (width, height), flags=cv2.INTER_NEAREST) > 0


def reconstruct(
    masks: dict[str, np.ndarray],
    settings: dict[str, ViewSettings],
    config: ProjectConfig,
    progress: Callable[[str], None] = lambda _: None,
) -> tuple[trimesh.Trimesh, dict, dict[str, np.ndarray]]:
    if not {"side", "front"}.issubset(masks):
        raise ValueError("Add and review both side and front masks before building.")
    side = tight_mask(masks["side"])
    front = tight_mask(masks["front"])
    length, height, width = side.shape[1] / side.shape[0], 1.0, front.shape[1] / front.shape[0]
    proportions = np.array([length, height, width], dtype=float)
    if np.max(proportions) / np.min(proportions) > 30:
        raise ValueError("View proportions are implausible. Crop to the vehicle and review masks.")
    dims = np.maximum(8, np.rint(proportions / proportions.max() * config.resolution).astype(int))
    nx, ny, nz = map(int, dims)
    progress("Aligning silhouettes")
    projected = {
        "side": fit_projection(masks["side"], nx, ny, settings["side"]),
        "front": fit_projection(masks["front"], nz, ny, settings["front"]),
    }
    progress("Intersecting silhouettes")
    side_cart = np.flipud(projected["side"])
    front_cart = np.flipud(np.fliplr(projected["front"]))
    volume = side_cart.T[:, :, None] & front_cart[None, :, :]
    if "rear" in masks:
        projected["rear"] = fit_projection(masks["rear"], nz, ny, settings["rear"])
        volume &= np.flipud(projected["rear"])[None, :, :]
    if "top" in masks:
        projected["top"] = fit_projection(masks["top"], nx, nz, settings["top"], ground=False)
        volume &= projected["top"].T[:, None, :]
    if not volume.any():
        raise ValueError("The silhouettes do not overlap. Reset alignment or correct the masks.")
    # Bound mesh memory before allocating marching-cubes vertices and adjacency.
    surface_faces = 0
    for axis in range(3):
        surface_faces += int(np.count_nonzero(np.diff(volume, axis=axis)))
        surface_faces += int(np.count_nonzero(np.take(volume, 0, axis=axis)))
        surface_faces += int(np.count_nonzero(np.take(volume, -1, axis=axis)))
    if surface_faces > 1_000_000:
        raise ValueError(
            "Masks produce an excessively fragmented surface. Remove noise or use a lower resolution before rebuilding."
        )
    progress("Extracting surface")
    spacing = proportions / dims
    vertices, faces, _, _ = marching_cubes(
        np.pad(volume, 1), level=0.5, spacing=tuple(spacing), allow_degenerate=False
    )
    vertices -= spacing / 2
    vertices[:, 0] -= length / 2
    vertices[:, 2] -= width / 2
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    mesh.fix_normals()
    if config.smoothing:
        progress("Smoothing surface")
        trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=0.5, iterations=5)
    # A single measured dimension applies uniform scale after any smoothing.
    unit = "relative"
    if config.known_dimension is not None:
        axis = {"length": 0, "height": 1, "width": 2}[config.known_axis]
        mesh.apply_scale(config.known_dimension / float(mesh.extents[axis]))
        unit = config.unit
    progress("Checking geometry")
    if not np.isfinite(mesh.vertices).all() or not len(mesh.faces):
        raise ValueError("Surface extraction produced invalid geometry. Review the masks.")
    projections = {
        "side": np.flipud(volume.any(axis=2).T),
        "front": np.fliplr(np.flipud(volume.any(axis=0))),
        "rear": np.flipud(volume.any(axis=0)),
        "top": volume.any(axis=1).T,
    }
    agreement = {}
    for name, expected in projected.items():
        actual = projections[name]
        union = (expected | actual).sum()
        agreement[name] = round(float((expected & actual).sum() / union), 4) if union else 0
    warnings = [
        "Silhouettes constrain the exterior only; hidden recesses and internal geometry are unknown."
    ]
    if "top" not in masks:
        warnings.append("Top profile unconstrained; width may be overestimated.")
    if unit == "relative":
        warnings.append(
            "Relative scale. Set one known dimension before using physical measurements."
        )
    if any(v < 0.85 for v in agreement.values()):
        warnings.append("Some silhouette projections disagree. Review alignment and mask overlays.")
    stats = {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "components": int(
            trimesh.graph.connected_component_labels(
                mesh.face_adjacency, node_count=len(mesh.faces)
            ).max()
            + 1
        ),
        "dimensions": {
            "length": float(mesh.extents[0]),
            "height": float(mesh.extents[1]),
            "width": float(mesh.extents[2]),
        },
        "bounds": mesh.bounds.tolist(),
        "volume": float(abs(mesh.volume)),
        "unit": unit,
        "grid": dims.tolist(),
        "resolution": config.resolution,
        "source_views": sorted(masks),
        "projection_iou": agreement,
        "warnings": warnings,
        "method": "orthographic_visual_hull",
        "axes": "X forward, Y up, Z vehicle right",
        "physical_accuracy": "Not measured; silhouette agreement is not geometric accuracy.",
    }
    return (
        mesh,
        stats,
        {
            name: image.astype(np.uint8) * 255
            for name, image in projections.items()
            if name in masks
        },
    )


def export_mesh(mesh: trimesh.Trimesh, file_type: str, unit: str) -> bytes:
    if file_type not in {"glb", "obj", "stl", "ply"}:
        raise ValueError("Unsupported mesh format")
    exported = mesh.copy()
    if file_type == "glb" and unit != "relative":
        # glTF uses meters; STL/OBJ/PLY retain displayed coordinate units.
        exported.apply_scale({"mm": 0.001, "cm": 0.01, "m": 1}[unit])
    data = exported.export(file_type=file_type)
    return data.encode("utf-8") if isinstance(data, str) else data
