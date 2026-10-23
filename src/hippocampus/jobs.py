"""In-memory job registry for background work (crawl/ingest) with a replayable event log."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    events: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    owner: str = "anonymous"  # principal that started it; "system" jobs (webhook sync) are visible to everyone

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error")

    def log(self, message: str) -> None:
        self.events.append(message)

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "result": self.result,
                "error": self.error, "event_count": len(self.events), "created_at": self.created_at}


Runner = Callable[[Job], dict | None]


class JobRegistry:
    def __init__(self, max_jobs: int = 200):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max = max_jobs

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self) -> int:
        return sum(1 for j in list(self._jobs.values()) if not j.finished)

    def submit(self, kind: str, runner: Runner, *, background: bool = True, owner: str = "anonymous") -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, owner=owner)
        with self._lock:
            if len(self._jobs) >= self._max:  # drop the oldest finished job
                for jid, j in list(self._jobs.items()):
                    if j.finished:
                        del self._jobs[jid]
                        break
            self._jobs[job.id] = job

        def run() -> None:
            job.status = "running"
            try:
                job.result = runner(job)
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller via the job
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "error"

        if background:
            threading.Thread(target=run, name=f"job-{job.id[:8]}", daemon=True).start()
        else:
            run()
        return job
