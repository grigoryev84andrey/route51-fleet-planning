"""Background jobs: identity, revision, working directory and an honest state.

Contract (plan S11):
  * every job has an id, a kind, the revision of the conditions it was started
    for, its own working directory, timestamps and a log;
  * states: running | completed | failed | interrupted; there is no `cancelled`
    — the console can hide the progress panel, but the computation continues;
  * progress is counted in completed units of work, never an invented percentage;
  * each job summary is written to `.work/jobs/<id>/job.json`, so a job that was
    running when the server stopped is reported as `interrupted` after a restart
    instead of being polled for ever;
  * the caller publishes a result into the session only if its revision is still
    current (see server.py); a stale job keeps its result as history.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from settings import WORKDIR

JOBS_DIR = WORKDIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
SERVER_INSTANCE = secrets.token_hex(6)


@dataclass
class Job:
    id: str
    kind: str
    revision: dict
    workdir: Path
    state: str = "running"
    stage: str = ""
    done_units: int = 0
    total_units: int = 0
    note: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    result: Any = None
    error: str | None = None
    published: bool | None = None
    log: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state, "revision": self.revision,
                "stage": self.stage, "done_units": self.done_units, "total_units": self.total_units,
                "note": self.note, "started": self.started, "finished": self.finished,
                "elapsed_s": round((self.finished or time.time()) - self.started, 1),
                "error": self.error, "published": self.published, "log": self.log[-40:],
                "server_instance": SERVER_INSTANCE}

    def persist(self) -> None:
        try:
            (self.workdir / "job.json").write_text(
                json.dumps(self.public(), ensure_ascii=False, default=str), encoding="utf-8")
        except OSError:
            pass


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, revision: dict,
               fn: Callable[[Callable[..., None], Job], Any],
               publish: Callable[[Job], bool] | None = None) -> Job:
        jid = f"{time.strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(4)}"
        wd = JOBS_DIR / jid
        wd.mkdir(parents=True, exist_ok=False)
        job = Job(id=jid, kind=kind, revision=dict(revision), workdir=wd)
        with self._lock:
            self._jobs[jid] = job
        job.persist()

        def progress(stage: str, done: int = 0, total: int = 0, note: str = "") -> None:
            job.stage, job.done_units, job.total_units, job.note = stage, int(done), int(total), note
            if not job.log or job.log[-1]["stage"] != stage:
                job.log.append({"stage": stage, "at": time.strftime("%H:%M:%S"), "note": note})
            else:
                job.log[-1]["note"] = note

        def run() -> None:
            try:
                job.result = fn(progress, job)
                job.state = "completed"
                if publish is not None:
                    job.published = bool(publish(job))
            except Exception as exc:
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.log.append({"stage": "failed", "at": time.strftime("%H:%M:%S"),
                                "note": traceback.format_exc(limit=6)})
            finally:
                job.finished = time.time()
                job.persist()

        threading.Thread(target=run, name=f"job-{kind}-{jid}", daemon=True).start()
        return job

    def get(self, job_id: str) -> dict | None:
        """Live job, or the persisted summary of a job from a previous server process."""
        job = self._jobs.get(job_id)
        if job is not None:
            return job.public()
        p = JOBS_DIR / job_id / "job.json"
        if not p.exists():
            return None
        old = json.loads(p.read_text(encoding="utf-8"))
        if old.get("state") == "running":
            old["state"] = "interrupted"
            old["error"] = ("The server stopped while this job was running; its result was not "
                            "kept. Start the calculation again.")
        old["from_previous_server"] = True
        return old

    def result(self, job_id: str):
        job = self._jobs.get(job_id)
        return None if job is None else job.result

    def recent(self, limit: int = 40) -> list[dict]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)
        return [j.public() for j in jobs[:limit]]


registry = JobRegistry()
