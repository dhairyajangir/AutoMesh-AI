"""Write delivery evidence only when the final test reports pass."""
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
validation = ROOT / "validation"
suite = ET.parse(validation / "pytest-results.xml").getroot().find("testsuite")
assert suite is not None and suite.get("failures") == "0" and suite.get("errors") == "0"
browser = json.loads((ROOT / "frontend/test-results/results.json").read_text(encoding="utf-8"))
assert browser["stats"]["unexpected"] == 0 and browser["stats"]["expected"] >= 5, "Finish browser validation first"
shutil.copyfile(ROOT / "frontend/test-results/results.json", validation / "browser-results.json")
benchmark = None
for group in browser["suites"]:
    for spec in group.get("specs", []):
        for test in spec["tests"]:
            for result in test["results"]:
                for line in result.get("stdout", []):
                    text = line.get("text", "")
                    if "VIEWER_BENCHMARK " in text:
                        benchmark = json.loads(text.split("VIEWER_BENCHMARK ", 1)[1].strip())
assert benchmark is not None
(validation / "viewer-performance.json").write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
photos = json.loads((validation / "photo-validation.json").read_text())
assert photos["offline_network_blocked"]
assert json.loads((validation / "download-failure.json").read_text())["passed"]
blueprint = json.loads((validation / "blueprint-validation.json").read_text())
table = "\n".join(f"| {r['resolution']} | {r['geometry_seconds']:.2f} s | {r['with_exports_and_reimport_seconds']:.2f} s | {r['stats']['faces']:,} |" for r in blueprint)
report = f"""# Validation and measured limits

Validated locally on Windows 11, Intel Core i7-1065G7, 16 GB RAM and Intel Iris Plus. Runs used the active workstation with other applications open; these are observed timings, not controlled hardware benchmarks or latency guarantees.

## Checks completed

- **{suite.get('tests')} pytest checks passed**, including cuboid/asymmetric orientation and scale fixtures, silhouette agreement, all four export re-imports, bad masks/uploads, model failure handling, cache reuse, immutable job inputs, cancellation, interrupted-job recovery and atomic result publication.
- **{browser['stats']['expected']} Playwright checks passed** in Microsoft Edge: model rendering and downloads; real file upload, polygon edits, undo/redo, alignment, rebuild and reload persistence; 375 px layout, zoom and keyboard focus; automated WCAG A/AA checks on the build screen and project dialog; viewer performance.
- Production TypeScript/Vite build succeeded. Python lint passed. The Python dependency audit and production npm audit reported no known vulnerabilities at the time of testing. Automated scans do not establish the absence of every defect or accessibility barrier.
- The model was downloaded from rembg's official release, stored locally, and run with ONNX Runtime's CPU provider. Cached photo processing completed with Python network requests blocked. The real empty-cache download failure produced an actionable error and retained the manual-mask workflow.
- Launcher stop/start preserved the synthetic coupe, supplied sedan drawing and attributed photo example, including completed mesh artifacts.

## Geometry and exports

The supplied `car_blueprint.jpeg` was cropped using the recorded boxes in `scripts/validate_examples.py`, with side/top flipped nose-right and threshold 220. The standard result has 100,096 vertices, 200,188 triangles, one connected component, consistent winding and a watertight surface. GLB, STL, OBJ and PLY were each re-imported and checked for finite vertices, matching bounds and watertightness.

| Blueprint grid, longest axis | Geometry only | Geometry + exports + re-import checks | Triangles |
| --- | ---: | ---: | ---: |
{table}

The photo example uses Manuel Strehl's side and oblique front photographs of a Citroën 2CV. At 128 cells it produced {photos['stats']['vertices']:,} vertices and {photos['stats']['faces']:,} triangles, one connected component and a watertight surface. Every exported format re-imported with matching bounds. See [photo attribution and capture limitations](../data/examples/photos/ATTRIBUTION.md). The oblique front view does **not** validate straight-on capture accuracy.

## Performance targets

| Target | Observation | Result |
| --- | --- | --- |
| Prepared-mask preview within 3 s | Blueprint geometry: 2.52 s; geometry plus exports/re-import: 7.58 s. Photo geometry: 4.92 s in the first run and {photos['reconstruction_seconds']:.2f} s during the offline run. | Not consistently met; full jobs take longer than geometry alone. |
| Standard build within 15 s | Initial synthetic example: 13.87 s. Supplied sedan: 42.65 s recorded processing; 44.56 s from job creation to final state under concurrent load. | Not consistently met. |
| Interactive viewing at 30 FPS | {benchmark['orbit_updates_per_second']:.1f} orbit updates/s over 120 animation frames, 1440 × 1000, DPR 1. Renderer: {benchmark['renderer']}. | Orbit-update cadence exceeds target; this is browser animation timing, not a GPU profiler measurement. |

Warm front-photo inference measured 4.26 s initially and {photos['segmentation_seconds']['front']:.2f} s in the offline run. The first photo in a fresh process included native imports/model-session initialization and took {photos['segmentation_seconds']['side']:.2f} s in that offline run. Initial installation/import time is separate from prepared-mask reconstruction.

Only one job worker runs. The frontend loads the 3D viewer on demand and renders on demand with capped pixel ratio. Highly fragmented volumes are rejected before allocating a large surface mesh. Unchanged jobs can reuse cached artifacts, but cache timings are not used to claim uncached performance.

## Boundaries

These checks establish executable workflows, geometry consistency and export readability. They do not establish real-world dimensional accuracy. Silhouette intersection cannot recover hidden recesses, internal structures, textures or details absent from the masks. A missing top view stays unconstrained. The application does not calibrate camera perspective or certify CAD/CFD suitability.

Keyboard controls, reduced-motion styles, zoom/reflow and automated contrast checks were exercised. A manual screen-reader audit was not performed. Browser automation used Edge; other browsers have not received the same complete test run.

## Evidence files

- [Pytest XML](../validation/pytest-results.xml)
- [Browser results](../validation/browser-results.json)
- [Viewer timing](../validation/viewer-performance.json)
- [Blueprint geometry and export checks](../validation/blueprint-validation.json)
- [Full blueprint job](../validation/blueprint-job.json)
- [Offline photo reconstruction](../validation/photo-validation.json)
- [Full photo job](../validation/photo-job.json)
- [Model download failure](../validation/download-failure.json)
- [Python dependency audit](../validation/dependency-audit.json)
- [Desktop screenshot](../validation/studio-desktop.png)

The original source revision is `c8c4a8fc30d697e80c6f46d7b8ac24cc08031362`. The application code lives in `automesh/` and `frontend/`; upstream research scripts remain separately in `src/`.
"""
(ROOT / "docs/VALIDATION.md").write_text(report, encoding="utf-8")
print("Validation report written from passing test reports")
