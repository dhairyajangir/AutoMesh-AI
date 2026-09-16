"""Add the repository drawing to the running studio through its public API."""

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    client = httpx.Client(base_url="http://127.0.0.1:8765", timeout=60)
    name = "Supplied sedan blueprint · example"
    existing = next((p for p in client.get("/api/projects").json() if p["name"] == name), None)
    if existing:
        print(json.dumps(existing))
        return
    response = client.post("/api/projects", json={"name": name, "mode": "blueprint"})
    response.raise_for_status()
    project = response.json()
    pid = project["id"]
    boxes = {"side": (28, 20, 425, 148), "front": (300, 245, 417, 320), "top": (33, 325, 241, 424)}
    for view, (x0, y0, x1, y1) in boxes.items():
        response = client.post(
            f"/api/projects/{pid}/views/{view}",
            files={
                "file": (
                    "car_blueprint.jpeg",
                    (ROOT / "data/blueprints/car_blueprint.jpeg").read_bytes(),
                    "image/jpeg",
                )
            },
        )
        response.raise_for_status()
        settings = {
            "crop": [x0 / 447, y0 / 447, (x1 - x0) / 447, (y1 - y0) / 447],
            "flip_x": view in ("side", "top"),
            "threshold": 220,
            "orientation_confirmed": True,
        }
        response = client.put(f"/api/projects/{pid}/views/{view}", json=settings)
        response.raise_for_status()
        project = response.json()
        revision = next(v["revision"] for v in project["views"] if v["name"] == view)
        response = client.put(
            f"/api/projects/{pid}/views/{view}/mask?revision={revision}",
            files={
                "file": (
                    "mask.png",
                    (ROOT / f"validation/blueprint-{view}-mask.png").read_bytes(),
                    "image/png",
                )
            },
        )
        response.raise_for_status()
    response = client.post(f"/api/projects/{pid}/jobs", json={"kind": "reconstruct"})
    response.raise_for_status()
    jid = response.json()["id"]
    for _ in range(180):
        job = client.get(f"/api/jobs/{jid}").json()
        if job["state"] in ("completed", "failed", "cancelled"):
            print(json.dumps(job, indent=2))
            (ROOT / "validation/blueprint-job.json").write_text(
                json.dumps(job, indent=2), encoding="utf-8"
            )
            if job["state"] != "completed":
                raise RuntimeError(job.get("error"))
            return
        time.sleep(1)
    raise TimeoutError("Blueprint job is still running; inspect it in the studio.")


if __name__ == "__main__":
    main()
