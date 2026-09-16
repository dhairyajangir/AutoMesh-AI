"""Create the portable source release, excluding private projects and environments."""

import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
README = """# AutoMesh-AI · Local reconstruction studio

Turn vehicle blueprints or guided front/side photographs into editable silhouettes and approximate 3D exterior meshes. Processing stays on your computer.

**Start on Windows:** double-click `Launch.cmd`, then open http://127.0.0.1:8765. The prepared checkout is ready to run. A fresh installation requires uv; the launcher installs locked Python 3.12 dependencies. Source-only checkouts also require Node.js 22.12+ to build the UI. The release ZIP includes the production frontend.

- Blueprint and photo projects, persisted in SQLite.
- Crop, rotate, flip, threshold, brush/polygon masks, undo/redo and uniform alignment.
- Vectorized silhouette intersection at 128, 256 or 384 cells along the longest axis.
- Local CPU photo masks with U2Netp/rembg; manual masking if model setup fails.
- Interactive 3D inspection, projection comparison and GLB/STL/OBJ/PLY exports.
- Single worker, cancellation, immutable job snapshots, cache reuse and stale-result indicators.

Read [the studio guide](docs/STUDIO_GUIDE.md) for capture directions, coordinates, setup, offline use and export units. Read [validation evidence](docs/VALIDATION.md) for measured results and unresolved limits. Run `scripts/diagnose.py` with the virtualenv Python for setup diagnostics.

## What the geometry means

This is a visual-hull reconstruction. It constrains the exterior using masks; it cannot recover hidden recesses, interiors, textures or fine details absent from the silhouettes. Photo results are labeled **Approximate silhouette model**. Without a top view, that profile is explicitly unconstrained. A known dimension sets one uniform global scale. Watertightness is measured; physical accuracy is not established.

This release does not provide calibrated photogrammetry, single-photo generative completion, CAD or CFD certification.

## Architecture

React 19, TypeScript, Vite, Tailwind, Radix and React Three Fiber provide the interface. FastAPI, SQLite, NumPy, OpenCV, scikit-image and trimesh provide local processing and storage. ONNX Runtime uses the CPU provider. `automesh/` is the application; the original `src/` research prototypes are retained as reference and are not imported by the studio.

Python dependencies are locked in `uv.lock`; frontend dependencies are locked in `frontend/package-lock.json`. FastAPI's OpenAPI schema generates the frontend API types. See the guide for test and build commands.

## Attribution

Built from [dhairyajangir/AutoMesh-AI](https://github.com/dhairyajangir/AutoMesh-AI), revision `c8c4a8fc30d697e80c6f46d7b8ac24cc08031362`. The upstream MIT license and copyright notice are preserved in [LICENSE](LICENSE). The original project overview is retained in [UPSTREAM_README.md](docs/UPSTREAM_README.md). See [photo attribution](data/examples/photos/ATTRIBUTION.md) for example images and their separate license.
"""


def main():
    original = ROOT / "docs/UPSTREAM_README.md"
    if not original.exists():
        original.write_bytes((ROOT / "README.md").read_bytes())
    (ROOT / "README.md").write_text(README, encoding="utf-8")
    notices = ["Bundled frontend dependency notices. Python packages are installed separately from their published distributions."]
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    for installed, info in lock["packages"].items():
        if not installed.startswith("node_modules/") or info.get("dev"):
            continue
        folder = ROOT / "frontend" / installed
        if not folder.is_dir():
            continue
        licenses = [p for p in folder.iterdir() if p.is_file() and (p.name.lower().startswith("license") or p.name.lower().startswith("ofl"))]
        for license_file in licenses:
            notices.append(f"\n{'=' * 72}\n{installed} {info.get('version', '')}\n{license_file.read_text(encoding='utf-8', errors='replace')}")
    if len(notices) > 1:
        (ROOT / "docs/THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    excluded = {
        ".git",
        ".venv",
        ".local",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "test-results",
        "playwright-report",
        ".codegraph",
    }
    output = ROOT.parent / "AutoMesh-Studio-source.zip"
    count = 0
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for directory, children, files in os.walk(ROOT):
            children[:] = sorted(
                child
                for child in children
                if child not in excluded and not child.endswith(".egg-info")
            )
            for filename in sorted(files):
                path = Path(directory) / filename
                relative = path.relative_to(ROOT)
                if path.name.startswith(".env") or path.suffix in (
                    ".pyc",
                    ".onnx",
                    ".pt",
                    ".pth",
                    ".tsbuildinfo",
                ):
                    continue
                archive.write(path, Path("AutoMesh-AI") / relative)
                count += 1
    with ZipFile(output) as archive:
        assert archive.testzip() is None
    print(f"Packaged {count} files, {output.stat().st_size:,} bytes: {output}")


if __name__ == "__main__":
    main()
