# AutoMesh Studio

AutoMesh creates an approximate exterior mesh by intersecting vehicle silhouettes. It runs on your computer. There are no API keys or cloud reconstruction services.

## Start and stop

Double-click `Launch.cmd`. It serves the interface and API at `http://127.0.0.1:8765`. Run `Stop.ps1` to stop the server. Closing the browser does not stop the engine.

The supplied checkout is already set up. On another Windows computer, install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run the launcher. It installs the Python 3.12 dependencies from `uv.lock`. The release source ZIP includes the compiled frontend. A source-only Git checkout also needs Node.js 22.12+ and npm to build the frontend from `package-lock.json`.

The first automatic photo mask downloads the small U2Netp model. The job status shows the download stage. Subsequent inference uses the cached file in `.local/models`. After dependencies and the model are installed, processing works offline. If the model cannot load or download, the job shows an error and manual mask painting remains available. Windows VC runtime DLLs are installed inside the virtual environment, without modifying system directories.

Projects, edits, input snapshots, meshes and logs are stored in `.local`. Back up that directory to preserve your work. Do not remove it to repair setup. Startup errors are recorded in `.local/server-error.log`.

## Work through a vehicle

1. Create a blueprint or photo project. The supplied Touring coupe is a synthetic example, identified as such in the app.
2. Upload one image per view. To use a drawing sheet, upload the same sheet into each required slot and crop each perspective. Crop values are fractions of the image. Rotation is clockwise in 90-degree steps.
3. Generate a mask. Blueprints use a threshold and the largest closed contour. Photo masks use U2Netp on CPU. Inspect the overlay: annotations, shadows and background objects can require correction.
4. Paint foreground or background, or fill a polygon. Undo/redo and zoom are available. Polygon points can also be entered numerically for keyboard operation. Mask edits autosave after two seconds of inactivity; **Save mask** commits them immediately. Undo history stays available after autosave. Settings and alignment also autosave. The interface warns before leaving unsaved mask edits.
5. Review orientation. Side and top must point nose-right. Front means looking at the vehicle from ahead; rear means looking from behind. Confirm this in Align. Use the guides, drag the image or enter uniform scale and offsets. Scaling preserves proportions.
6. Build a preview at 128 cells, then use 256 or 384 when needed. Only one reconstruction runs at a time. Jobs can be cancelled and retried. A changed input marks the last successful mesh outdated but leaves it downloadable.
7. Inspect the model, compare projected silhouettes and export GLB, STL, OBJ or PLY. Download the JSON report with the mesh.

Blueprints require side, front and top views. Photos require side and front; rear and top are optional. Capture the same vehicle from far enough away to reduce perspective distortion, near its center height, with the camera level and each view as square-on as possible. Keep the complete vehicle in frame. The application does not calibrate cameras or correct perspective.

## Coordinates, scale and exports

X points forward, Y points up, and Z points toward the vehicle's right. Silhouettes are trimmed to their foreground bounds. Side length-to-height and front width-to-height determine the initial volume proportions. Alignment uses uniform fitting, a shared ground plane for elevations and centered top projection. Offsets are fractions of the projection canvas.

The longest voxel-grid axis is 128, 256 or 384 cells; the other axes follow those proportions. Marching cubes runs on a padded volume with physical voxel spacing. Optional Taubin smoothing is off by default and changes silhouette fit.

Relative units are the default. Enter one real dimension to scale all axes uniformly. STL, OBJ and PLY retain the displayed coordinate units. GLB follows glTF's meter convention when a real dimension is supplied; an unscaled GLB uses arbitrary scene units. STL itself does not encode unit metadata, so keep the report with it.

The report contains actual geometry counts, bounds, connected components, watertightness, winding consistency, grid dimensions, settings, source artifact identifiers and silhouette intersection-over-union values. Silhouette agreement is not physical accuracy. No accuracy score is inferred from it.

## Limits

Photo results are labeled **Approximate silhouette model**. Without a top view, the top profile remains unconstrained. Exterior recesses, undercuts, windows, wheel detail, interior geometry and hidden surfaces cannot be recovered from silhouettes alone. Segmentation can miss mirrors and thin structures. Mismatched silhouettes can cut away valid geometry or split the result into components.

This release does not provide calibrated photogrammetry, single-image generative completion, textures, CAD surfaces or CFD certification. A watertight export is a topology result, not evidence of dimensional accuracy or suitability for manufacturing.

## Development and diagnostics

```powershell
uv sync --frozen --python 3.12
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\validate_examples.py
.venv\Scripts\python.exe scripts\export_schema.py
cd frontend
npm ci
npm run types
npm run build
npm test
```

Browser tests use the installed Microsoft Edge channel and the running localhost server. They exercise the real backend. Use a disposable data directory through `AUTOMESH_DATA_DIR` when running tests on a machine with personal projects.

The backend entry point is `automesh.api:app`. Reusable headless geometry is in `automesh/geometry.py`; `store.py` owns SQLite and server-controlled artifacts; `worker.py` runs immutable job snapshots. Interrupted queued/running jobs are marked failed on restart. Completed artifacts survive restarts. Cache reuse requires the same project, inputs and settings hash.

The original research scripts remain in `src/` for attribution and reference. They are not loaded by the application. The application does not load their large generative models or use their hard-coded file paths.
