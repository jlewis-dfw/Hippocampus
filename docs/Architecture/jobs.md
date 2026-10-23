# Background jobs

Crawls, repository imports and docs syncs can take minutes, so they run in the background. `src/hippocampus/jobs.py` provides a small
in-process registry; the API returns `202` with a `job_id` and the client polls or streams the log.

## The model

```python
Job(id, kind, status, events, result, error, created_at, owner)
```

| Field | Meaning |
|---|---|
| `id` | A random UUID hex string |
| `kind` | `crawl`, `ingest-repo`, `docs-sync` (a webhook push) or `docs-resync` (a manual resync) |
| `status` | `queued` (created), `running`, `done`, `error` |
| `events` | The log: a list of text lines, appended with `job.log(message)` |
| `result` | What the runner returned (a dict), set when it finishes |
| `error` | `<ExceptionType>: <message>` if the runner raised |
| `created_at` | Unix time in seconds |
| `owner` | The principal's name that started it, `anonymous`, or `system` for webhook syncs |

`finished` is true for `done` and `error`. `view()` returns the public form without the log: `id`, `kind`, `status`, `result`, `error`,
`event_count`, `created_at`.

## Running a job

`JobRegistry.submit(kind, runner, background=True, owner=...)` creates the job, registers it, and starts a **daemon thread** that sets
`running`, calls `runner(job)`, stores the returned dict in `result` and sets `done`, or catches **any** exception, stores it in `error`
and sets `error`. The runner receives the job so it can log progress. With `background=False` the runner runs in the caller's thread (used by
tests).

The runners are closures built by the routes: they open their own store connection with `store_factory()`, so a job holds no request-scoped
resources.

## Retention

The registry keeps up to `max_jobs` (200). When a new job is submitted at the limit, the **oldest finished** job is dropped; running
jobs are never dropped, so the registry can exceed the limit if everything is running. Jobs live **in memory only**: a restart loses
every job and its log. The data a job already stored is durable, and re-running the same job is safe because unchanged sources are skipped.

## Reading a job

- `GET /v1/jobs/{id}` returns `view()`.
- `GET /v1/jobs/{id}/events` streams the log as server-sent events: each line as `data: <text>`, then `event: end` with the final status.
  The generator loops with a 0.2 second sleep, reading the `finished` flag **before** the log so the last lines are never missed, and replays
  the log from the start for each connection. Many viewers of one job are fine.
- Both routes apply the owner rule: the owner, an admin, or anyone for `system` jobs; everyone else gets `404`.
- `GET /v1/status` reports `active_jobs`: the jobs that are not finished.

## Limits and consequences

- **No cancellation.** A running job cannot be stopped from the API; it ends when it finishes or the service restarts.
- **No queue or concurrency limit.** Every submitted job starts at once, each in its own thread. Many simultaneous large crawls compete for
  the CPU (embedding) and slow each other and searches; the crawl page cap and delay bound each one.
- **Thread safety.** Registration and eviction are guarded by a lock; a job's own state is mutated only by its thread, and `events` is only
  appended to, which is safe under CPython for the single-writer, many-reader use here.
- **One process only.** Jobs are invisible to other processes, so running several API workers would make job lookups inconsistent. Run
  one process (the compose service does).

## Tests

`tests/test_api.py` runs a crawl job to completion, reads its status and the event stream, and checks a failing job reports its error;
`tests/test_access.py` covers the ownership rules.
