"""Background job status and the live event stream."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import Principal
from ..jobs import Job
from .deps import Deps
from .tags import JOBS


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[JOBS])
    jobs = d.jobs
    current_principal = d.current_principal

    def _job(job_id: str, p: Principal) -> Job:
        job = jobs.get(job_id)
        # A job's log and result name the paths it touched: only its owner (or an admin) sees them.
        if job is None or not (p.admin or job.owner in (p.name, "system")):
            raise HTTPException(404, "job not found")
        return job

    @router.get("/v1/jobs/{job_id}")
    def job_status(job_id: str, p: Principal = Depends(current_principal)):
        """A job's status, result or error."""
        return _job(job_id, p).view()

    @router.get("/v1/jobs/{job_id}/events")
    def job_events(job_id: str, p: Principal = Depends(current_principal)):
        """A job's log as a server-sent event stream; it ends with an `end` event carrying the final status."""
        job = _job(job_id, p)

        def stream():
            sent = 0
            while True:
                finished = job.finished  # read before the log so a final event is never missed
                while sent < len(job.events):
                    yield f"data: {job.events[sent]}\n\n"
                    sent += 1
                if finished:
                    yield f"event: end\ndata: {job.status}\n\n"
                    return
                time.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
