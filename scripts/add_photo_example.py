"""Import the validated, attributed photo example into the running studio."""

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    client = httpx.Client(base_url="http://127.0.0.1:8765", timeout=60)
    name = "Citroën 2CV · oblique photo example"
    if any(p["name"] == name for p in client.get("/api/projects").json()):
        print("Photo example already exists")
        return
    response = client.post("/api/projects", json={"name": name, "mode": "photo"})
    response.raise_for_status()
    pid = response.json()["id"]
    for view in ("side", "front"):
        response = client.post(
            f"/api/projects/{pid}/views/{view}",
            files={
                "file": (
                    f"{view}.jpg",
                    (ROOT / f"data/examples/photos/{view}.jpg").read_bytes(),
                    "image/jpeg",
                )
            },
        )
        response.raise_for_status()
        response = client.put(
            f"/api/projects/{pid}/views/{view}", json={"orientation_confirmed": True}
        )
        response.raise_for_status()
        revision = next(v["revision"] for v in response.json()["views"] if v["name"] == view)
        response = client.put(
            f"/api/projects/{pid}/views/{view}/mask?revision={revision}",
            files={
                "file": (
                    "mask.png",
                    (ROOT / f"validation/photo-{view}-mask.png").read_bytes(),
                    "image/png",
                )
            },
        )
        response.raise_for_status()
    response = client.post(
        f"/api/projects/{pid}/jobs", json={"kind": "reconstruct", "resolution": 128}
    )
    response.raise_for_status()
    jid = response.json()["id"]
    for _ in range(180):
        job = client.get(f"/api/jobs/{jid}").json()
        if job["state"] in ("completed", "failed", "cancelled"):
            (ROOT / "validation/photo-job.json").write_text(
                json.dumps(job, indent=2), encoding="utf-8"
            )
            print(json.dumps(job, indent=2))
            if job["state"] != "completed":
                raise RuntimeError(job.get("error"))
            return
        time.sleep(1)
    raise TimeoutError("Photo reconstruction still running")


if __name__ == "__main__":
    main()
