# Design decisions

The choices a reviewer is most likely to question, each with the reason, what was considered, the code and test that back it, and what
would make us change it. Short on purpose: the topic pages have the detail.

## Tokens are hashed with plain SHA-256

- **Decision.** An API token is `hippo_` plus 32 random bytes (`secrets.token_urlsafe`). Only `sha256(token)` is stored; the token is shown once.
- **Why.** Salts and slow hashes (bcrypt, argon2) defend low-entropy passwords against guessing. A 256-bit random token cannot be guessed or
  looked up in a rainbow table, so a fast hash is enough, and it lets authentication be one indexed lookup per request.
- **Considered.** argon2 per request (about 50 ms of CPU on every call, no gain in strength); HMAC with a server key (adds a secret to manage).
- **Code and test.** `auth.py` (`new_token`, `hash_token`); `test_tokens_are_random_and_only_the_hash_is_stored` in `tests/unit/test_access.py`.
- **Revisit when.** Tokens become human-chosen or shorter, or a database leak must not allow offline use of a leaked hash with a keyed check.

## The dashboard keeps the token in `sessionStorage`

- **Decision.** The pasted token lives in `sessionStorage`: gone when the tab closes, never in a cookie, never sent to another origin.
- **Why.** A cookie would be sent automatically and need CSRF protection; `localStorage` would outlive the session. The dashboard is served
  with a strict Content-Security-Policy (`script-src 'self'`, no inline script), which is what keeps injected script, the real risk to a stored
  token, out.
- **Considered.** An HttpOnly session cookie (needs login endpoints, CSRF tokens and server-side sessions); keeping the token in memory only
  (lost on every reload).
- **Code.** `ui/app.js` (`getToken`, `setToken`); `UI_CSP` in `api.py`.
- **Revisit when.** The dashboard needs user accounts or single sign-on, or when it must load third-party script.

## Raw SQL in one file

- **Decision.** `store.py` is the only code that runs SQL. Queries are parameterised (`%s`); the few interpolated fragments are constant
  column lists, never request data.
- **Why.** The schema is small, the interesting query (vector search with access filtering before the limit) is clearer as SQL than through
  an ORM, and one file is one place to audit.
- **Considered.** SQLAlchemy (more dependencies and a second model of the schema, for about two dozen queries).
- **Code and test.** `store.py`; `MemStore` in `tests/unit/test_api.py` is the fake with the same methods, and the integration tests run the real SQL.
- **Revisit when.** The schema or the number of queries grows enough that hand-written SQL is the main cost of a change.

## The file filter exists in two languages

- **Decision.** The folder-upload filter runs in the browser (`ui/folder.js`) to show status before anything is sent, and on the server
  (`sync_core.py`), which is authoritative.
- **Why.** The browser reads the folder and the server never sees rejected files, so the preview must match. The server re-applies every rule,
  and the guarded path fragments (`HIPPO_EXCLUDE_PATH_PARTS`) cannot be removed by a client setting.
- **Considered.** Server-only filtering (uploads every file, including secrets, just to refuse them); one implementation shared through WebAssembly (a build step for the sake of one function).
- **Code and test.** `sync_core.py`, `ui/folder.js`; both run `tests/vectors/filter_vectors.json` and `vault_paths.json`
  (`tests/unit/test_filter_vectors.py`, `tests/js/check_vectors.js`), so a rule that differs fails a test.
- **Revisit when.** A third client needs the rules, or the vectors keep disagreeing.

## Jobs and upload sessions are in process memory

- **Decision.** `JobRegistry` and `UploadSessions` hold their state in the API process, with a size cap and an expiry.
- **Why.** One small service on one machine; a database table for job logs and upload sessions adds writes and a migration for no user benefit.
- **Consequence.** The service must run as one worker. A restart loses job history and any upload in progress (the client gets a 404 and starts again).
- **Considered.** Persisting both in PostgreSQL (needed for several workers); a queue such as Celery (a broker to run).
- **Code.** `jobs.py`, `files_ingest.py`; see [jobs.md](jobs.md).
- **Revisit when.** More than one worker is wanted, or job history must survive a restart.

## The dashboard has no framework

- **Decision.** `src/hippocampus/ui/` is plain HTML, CSS and a few static scripts, with no bundler and no build step.
- **Why.** It is a small admin surface; static files keep the image simple, let the CSP forbid inline script, and leave nothing to rebuild or patch.
- **Considered.** React or Vue with a bundler (a toolchain and a dependency tree larger than the dashboard).
- **Code and test.** `ui/`; the Chromium tests in `tests/e2e`.
- **Revisit when.** The dashboard grows state that is hard to keep consistent by hand. The first step would be ES modules, still without a framework.
