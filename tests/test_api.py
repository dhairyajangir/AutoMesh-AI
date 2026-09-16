import numpy as np
import pytest
from fastapi.testclient import TestClient

from automesh.api import create_app
from automesh.schemas import JobRequest, ViewSettings
from automesh.worker import png_bytes, run_job


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, run_worker=False, seed=False)
    with TestClient(app) as client:
        yield client


def make_ready(client, mode="blueprint"):
    project = client.post("/api/projects", json={"name": "Test vehicle", "mode": mode}).json()
    pid = project["id"]
    for name in ["side", "front", "top"] if mode == "blueprint" else ["side", "front"]:
        arr = np.full((80, 180 if name != "front" else 100, 3), 255, np.uint8)
        arr[10:-10, 10:-10] = 0
        upload = client.post(
            f"/api/projects/{pid}/views/{name}",
            files={"file": ("source.png", png_bytes(arr), "image/png")},
        )
        assert upload.status_code == 200, upload.text
        settings = ViewSettings(orientation_confirmed=True).model_dump(mode="json")
        assert client.put(f"/api/projects/{pid}/views/{name}", json=settings).status_code == 200
        if mode == "blueprint":
            job = client.post(
                f"/api/projects/{pid}/jobs", json={"kind": "segment", "view": name}
            ).json()
            run_job(client.app.state.store, job["id"])
            assert client.get("/api/jobs/" + job["id"]).json()["state"] == "completed"
        else:
            view = client.app.state.store.view(pid, name)
            mask = (arr[:, :, 0] == 0).astype(np.uint8) * 255
            response = client.put(
                f"/api/projects/{pid}/views/{name}/mask?revision={view['revision']}",
                files={"file": ("mask.png", png_bytes(mask), "image/png")},
            )
            assert response.status_code == 200, response.text
    return pid


def test_real_job_and_all_artifacts(client):
    pid = make_ready(client)
    response = client.post(
        f"/api/projects/{pid}/jobs", json={"kind": "reconstruct", "resolution": 128}
    )
    assert response.status_code == 202, response.text
    jid = response.json()["id"]
    run_job(client.app.state.store, jid)
    job = client.get("/api/jobs/" + jid).json()
    assert job["state"] == "completed", job
    assert job["result"]["stats"]["watertight"]
    for aid in job["result"]["artifacts"].values():
        response = client.get("/api/artifacts/" + aid + "?download=true")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert len(response.content) > 100
    project = client.get("/api/projects/" + pid).json()
    assert not project["stale"]
    settings = project["views"][0]["settings"]
    mask_id = project["views"][0]["mask_id"]
    settings["offset_x"] = 0.05
    changed = client.put(
        f"/api/projects/{pid}/views/{project['views'][0]['name']}", json=settings
    ).json()
    assert changed["stale"]
    assert changed["views"][0]["mask_id"] == mask_id  # alignment must not erase reviewed masks


def test_rebuild_cache(client):
    pid = make_ready(client)
    ids = []
    for _ in range(2):
        jid = client.post(
            f"/api/projects/{pid}/jobs", json={"kind": "reconstruct", "resolution": 128}
        ).json()["id"]
        ids.append(jid)
        run_job(client.app.state.store, jid)
    first, second = [client.get("/api/jobs/" + jid).json() for jid in ids]
    assert not first["result"]["cached"] and second["result"]["cached"]
    assert first["result"]["artifacts"] == second["result"]["artifacts"]


def test_job_snapshot_is_immutable(client):
    pid = make_ready(client)
    jid = client.post(
        f"/api/projects/{pid}/jobs", json={"kind": "reconstruct", "resolution": 128}
    ).json()["id"]
    before = client.app.state.store.job(jid, internal=True)["snapshot"]
    changed = ViewSettings(orientation_confirmed=True, offset_x=0.1).model_dump(mode="json")
    client.put(f"/api/projects/{pid}/views/side", json=changed)
    after = client.app.state.store.job(jid, internal=True)["snapshot"]
    assert before == after
    run_job(client.app.state.store, jid)
    assert client.get("/api/projects/" + pid).json()["stale"]


def test_crop_invalidates_mask_but_alignment_does_not(client):
    pid = make_ready(client)
    settings = ViewSettings(orientation_confirmed=True, scale=0.9).model_dump(mode="json")
    client.put(f"/api/projects/{pid}/views/side", json=settings)
    assert client.app.state.store.view(pid, "side")["mask_id"]
    settings["crop"] = [0, 0, 0.8, 1]
    client.put(f"/api/projects/{pid}/views/side", json=settings)
    assert client.app.state.store.view(pid, "side")["mask_id"] is None


def test_photo_masks_without_model_download(client):
    pid = make_ready(client, "photo")
    jid = client.post(
        f"/api/projects/{pid}/jobs", json={"kind": "reconstruct", "resolution": 128}
    ).json()["id"]
    run_job(client.app.state.store, jid)
    result = client.get("/api/jobs/" + jid).json()["result"]
    assert result["stats"]["watertight"]
    assert any("Perspective" in w for w in result["stats"]["warnings"])
    assert "top" not in result["projections"]


def test_job_completion_publishes_project_result_atomically(client):
    pid = make_ready(client)
    job = client.post(f"/api/projects/{pid}/jobs", json={"kind": "reconstruct"}).json()
    result = {"artifact_check": "published"}
    assert client.app.state.store.update_job(job["id"], "completed", "Complete", result=result)
    project = client.get(f"/api/projects/{pid}").json()
    assert project["last_job_id"] == job["id"]
    assert project["result"] == result


def test_model_failure_is_actionable(client, monkeypatch):
    from automesh import worker

    pid = make_ready(client, "photo")

    def fail(*args):
        raise ValueError("Photo model could not load. Use Paint mask.")

    monkeypatch.setattr(worker, "photo_mask", fail)
    jid = client.post(f"/api/projects/{pid}/jobs", json={"kind": "segment", "view": "side"}).json()[
        "id"
    ]
    run_job(client.app.state.store, jid)
    result = client.get("/api/jobs/" + jid).json()
    assert result["state"] == "failed" and "Paint mask" in result["error"]


def test_reject_unready_reconstruction(client):
    pid = client.post("/api/projects", json={"name": "No inputs", "mode": "blueprint"}).json()["id"]
    response = client.post(f"/api/projects/{pid}/jobs", json={"kind": "reconstruct"})
    assert response.status_code == 422 and "masks" in response.json()["detail"]


def test_cancellation_and_restart_recovery(client):
    pid = make_ready(client)
    store = client.app.state.store
    first = store.create_job(pid, JobRequest(kind="reconstruct"))
    assert client.post("/api/jobs/" + first["id"] + "/cancel").json()["state"] == "cancelled"
    second = store.create_job(pid, JobRequest(kind="reconstruct"))
    store.update_job(second["id"], "running", "Extracting surface")
    client.post("/api/jobs/" + second["id"] + "/cancel")
    run_job(store, second["id"])
    assert store.job(second["id"])["state"] == "cancelled"
    third = store.create_job(pid, JobRequest(kind="reconstruct"))
    store.recover()
    assert store.job(third["id"])["state"] == "failed"
    assert store.project(pid)["views"]


def test_revision_conflict_preserves_existing_mask(client):
    pid = make_ready(client)
    view = client.app.state.store.view(pid, "side")
    old_id = view["mask_id"]
    mask = np.zeros((80, 180), np.uint8)
    mask[10:-10, 10:-10] = 255
    response = client.put(
        f"/api/projects/{pid}/views/side/mask?revision=0",
        files={"file": ("mask.png", png_bytes(mask), "image/png")},
    )
    assert response.status_code == 409
    assert client.app.state.store.view(pid, "side")["mask_id"] == old_id


def test_invalid_upload_and_path_traversal(client):
    pid = client.post("/api/projects", json={"name": "Uploads"}).json()["id"]
    response = client.post(
        f"/api/projects/{pid}/views/side",
        files={"file": ("../../x.png", b"not an image", "image/png")},
    )
    assert response.status_code == 422
    assert client.get("/api/artifacts/..%2Fstudio.sqlite3").status_code == 404
    assert client.get("/api/artifacts/nonexistent").status_code == 404
    assert (
        client.post(
            f"/api/projects/{pid}/views/unknown", files={"file": ("x.png", b"a", "image/png")}
        ).status_code
        == 422
    )


def test_local_origin_boundary_and_headers(client):
    assert (
        client.post(
            "/api/projects", json={"name": "Malicious"}, headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_project_isolation(client):
    first = make_ready(client)
    second = make_ready(client)
    a = client.app.state.store.project(first)["views"][0]
    b = client.app.state.store.project(second)["views"][0]
    assert a["source_id"] != b["source_id"] and a["mask_id"] != b["mask_id"]


def test_sse_contains_terminal_state(client):
    pid = make_ready(client)
    job = client.app.state.store.create_job(pid, JobRequest(kind="reconstruct", resolution=128))
    client.post("/api/jobs/" + job["id"] + "/cancel")
    response = client.get("/api/jobs/" + job["id"] + "/events")
    assert '"state": "cancelled"' in response.text
