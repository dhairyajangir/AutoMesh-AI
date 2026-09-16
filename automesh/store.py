import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .schemas import ProjectConfig, ViewSettings

ROOT = Path(__file__).resolve().parents[1]


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier() -> str:
    return uuid.uuid4().hex


def fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("AUTOMESH_DATA_DIR", ROOT / ".local")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        (self.root / "models").mkdir(exist_ok=True)
        self.database = self.root / "studio.sqlite3"

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL,
              config TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
              created TEXT NOT NULL, updated TEXT NOT NULL, last_job_id TEXT
            );
            CREATE TABLE IF NOT EXISTS views (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              name TEXT NOT NULL, source_id TEXT NOT NULL, mask_id TEXT, image_id TEXT,
              settings TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
              UNIQUE(project_id, name)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              job_id TEXT, kind TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL, error TEXT, result TEXT,
              snapshot TEXT NOT NULL, cache_key TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_cache ON jobs(cache_key, state);
            """)

    def recover(self):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET state='failed', stage='Interrupted', error=?, updated=? WHERE state IN ('running','queued','cancelling')",
                ("The app stopped during this job. Your inputs are saved; run it again.", now()),
            )

    def create_project(self, name: str, mode: str) -> dict:
        pid = identifier()
        with self.connect() as db:
            db.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    pid,
                    name.strip() or "Untitled vehicle",
                    mode,
                    ProjectConfig().model_dump_json(),
                    0,
                    now(),
                    now(),
                ),
            )
        return self.project(pid)

    def project(self, pid: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if row is None:
                raise KeyError("Project not found")
            project = dict(row)
            project["config"] = json.loads(project["config"])
            views = db.execute(
                "SELECT * FROM views WHERE project_id=? ORDER BY name", (pid,)
            ).fetchall()
            project["views"] = [{**dict(v), "settings": json.loads(v["settings"])} for v in views]
            result = None
            stale = False
            if project["last_job_id"]:
                job = db.execute(
                    "SELECT result,revision FROM jobs WHERE id=?", (project["last_job_id"],)
                ).fetchone()
                if job and job["result"]:
                    result = json.loads(job["result"])
                    stale = job["revision"] != project["revision"]
            project.update(result=result, stale=stale)
            project["thumbnail_id"] = next(
                (v["image_id"] or v["source_id"] for v in project["views"] if v["name"] == "side"),
                None,
            )
            return project

    def projects(self) -> list[dict]:
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM projects ORDER BY updated DESC")]
        return [self.project(pid) for pid in ids]

    def patch_project(self, pid, patch):
        project = self.project(pid)
        config = patch.config.model_dump() if patch.config else project["config"]
        changed = config != project["config"]
        with self.connect() as db:
            db.execute(
                "UPDATE projects SET name=?, config=?, revision=revision+?, updated=? WHERE id=?",
                (
                    patch.name if patch.name is not None else project["name"],
                    json.dumps(config),
                    int(changed),
                    now(),
                    pid,
                ),
            )
        return self.project(pid)

    def add_artifact(
        self,
        pid: str,
        kind: str,
        data: bytes,
        ext: str,
        mime: str,
        job_id: str | None = None,
        name: str | None = None,
    ) -> dict:
        aid = identifier()
        filename = f"{aid}.{ext}"
        path = self.root / "artifacts" / filename
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        artifact = {
            "id": aid,
            "project_id": pid,
            "job_id": job_id,
            "kind": kind,
            "path": filename,
            "name": name or f"{kind}.{ext}",
            "mime": mime,
        }
        with self.connect() as db:
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", tuple(artifact.values()))
        return artifact

    def artifact(self, aid: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise KeyError("Artifact not found")
        artifact = dict(row)
        path = (self.root / "artifacts" / artifact["path"]).resolve()
        if not path.is_relative_to(self.root / "artifacts") or not path.is_file():
            raise KeyError("Artifact file is unavailable")
        artifact["file"] = str(path)
        return artifact

    def put_view(self, pid: str, name: str, source_id: str):
        self.project(pid)
        with self.connect() as db:
            db.execute(
                """INSERT INTO views VALUES (?,?,?,?,NULL,NULL,?,0)
                        ON CONFLICT(project_id,name) DO UPDATE SET source_id=excluded.source_id,
                        mask_id=NULL,image_id=NULL,settings=excluded.settings,revision=views.revision+1""",
                (identifier(), pid, name, source_id, ViewSettings().model_dump_json()),
            )
            db.execute(
                "UPDATE projects SET revision=revision+1, updated=? WHERE id=?", (now(), pid)
            )
        return self.project(pid)

    def view(self, pid: str, name: str):
        return next((v for v in self.project(pid)["views"] if v["name"] == name), None)

    def settings(self, pid: str, name: str, settings: ViewSettings):
        old = self.view(pid, name)
        if old is None:
            raise KeyError("Upload this view first")
        new = settings.model_dump(mode="json")
        if new == old["settings"]:
            return self.project(pid)
        affects_mask = any(
            new[k] != old["settings"][k]
            for k in ("crop", "rotation", "flip_x", "threshold", "invert")
        )
        with self.connect() as db:
            db.execute(
                "UPDATE views SET settings=?,revision=revision+1,mask_id=?,image_id=? WHERE id=?",
                (
                    json.dumps(new),
                    None if affects_mask else old["mask_id"],
                    None if affects_mask else old["image_id"],
                    old["id"],
                ),
            )
            db.execute("UPDATE projects SET revision=revision+1,updated=? WHERE id=?", (now(), pid))
        return self.project(pid)

    def apply_mask(self, pid, name, mask_id, image_id, expected_revision: int):
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE views SET mask_id=?,image_id=?,revision=revision+1 WHERE project_id=? AND name=? AND revision=?",
                (mask_id, image_id, pid, name, expected_revision),
            )
            if cursor.rowcount:
                db.execute(
                    "UPDATE projects SET revision=revision+1,updated=? WHERE id=?", (now(), pid)
                )
            return cursor.rowcount == 1

    def remove_view(self, pid, name):
        with self.connect() as db:
            db.execute("DELETE FROM views WHERE project_id=? AND name=?", (pid, name))
            db.execute("UPDATE projects SET revision=revision+1,updated=? WHERE id=?", (now(), pid))
        return self.project(pid)

    def create_job(self, pid, request):
        project = self.project(pid)
        snapshot = {k: project[k] for k in ("id", "mode", "config", "revision", "views")}
        snapshot["request"] = request.model_dump()
        if request.resolution:
            snapshot["config"]["resolution"] = request.resolution
        if request.kind == "segment":
            if not request.view:
                raise ValueError("Choose a view to extract its silhouette.")
            view = next((v for v in snapshot["views"] if v["name"] == request.view), None)
            if not view:
                raise ValueError("Upload the selected view first.")
            snapshot["views"] = [view]
            cache_data = {"mode": project["mode"], "view": view, "kind": request.kind}
            cache_data["view"] = {k: view[k] for k in ("source_id", "settings")}
        else:
            names = {v["name"] for v in snapshot["views"] if v["mask_id"]}
            required = (
                {"side", "front", "top"} if project["mode"] == "blueprint" else {"side", "front"}
            )
            if not required.issubset(names):
                raise ValueError(
                    "Extract and review masks for: " + ", ".join(sorted(required - names))
                )
            if any(not v["mask_id"] for v in snapshot["views"]):
                raise ValueError(
                    "Every uploaded view needs a mask. Extract it or remove that view."
                )
            if any(not v["settings"]["orientation_confirmed"] for v in snapshot["views"]):
                raise ValueError("Confirm orientation for each view in Align before building.")
            cache_data = {
                "mode": project["mode"],
                "config": snapshot["config"],
                "views": [
                    {k: v[k] for k in ("name", "mask_id", "settings")} for v in snapshot["views"]
                ],
            }
        jid = identifier()
        with self.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling')"
            ).fetchone()[0]
            if count >= 8:
                raise ValueError("The local queue is full. Wait for a job to finish.")
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?,?)",
                (
                    jid,
                    pid,
                    request.kind,
                    "queued",
                    "Queued",
                    now(),
                    now(),
                    json.dumps(snapshot),
                    fingerprint(cache_data),
                    project["revision"],
                ),
            )
        return self.job(jid)

    def job(self, jid, internal=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        job = dict(row)
        job["result"] = json.loads(job["result"]) if job["result"] else None
        if internal:
            job["snapshot"] = json.loads(job["snapshot"])
        else:
            job.pop("snapshot")
            job.pop("cache_key")
        return job

    def update_job(self, jid, state, stage, error=None, result=None):
        with self.connect() as db:
            guard = (
                " AND state NOT IN ('cancelling','cancelled')"
                if state in ("running", "completed")
                else ""
            )
            cursor = db.execute(
                "UPDATE jobs SET state=?,stage=?,error=?,result=?,updated=? WHERE id=?" + guard,
                (
                    state,
                    stage,
                    error,
                    json.dumps(result) if result is not None else None,
                    now(),
                    jid,
                ),
            )
            changed = cursor.rowcount == 1
            if changed and state == "completed":
                # Publish completion and the project's result pointer atomically.
                db.execute(
                    "UPDATE projects SET last_job_id=?,updated=? WHERE id=(SELECT project_id FROM jobs WHERE id=? AND kind='reconstruct')",
                    (jid, now(), jid),
                )
            return changed

    def jobs(self, pid):
        with self.connect() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM jobs WHERE project_id=? ORDER BY created DESC LIMIT 20", (pid,)
                )
            ]
        return [self.job(jid) for jid in ids]

    def cancel(self, jid):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET state=CASE state WHEN 'queued' THEN 'cancelled' ELSE 'cancelling' END,stage='Cancelling',updated=? WHERE id=? AND state IN ('queued','running')",
                (now(), jid),
            )
        return self.job(jid)
