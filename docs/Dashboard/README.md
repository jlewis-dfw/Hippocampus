# Dashboard internals

How the web dashboard is built. For how to *use* it, see [../User_Guide/dashboard.md](../User_Guide/dashboard.md).

The dashboard is three static files served by the API itself: no build step, no framework, no dependency, and no request to any other
site. Source: `src/hippocampus/ui/` (`index.html`, `app.js`, `app.css`).

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## How it is served

`api.py` mounts the folder with Starlette's `StaticFiles(directory=UI_DIR, html=True)` at `/ui`, and `GET /` redirects to `/ui/`. The files are
packaged with the Python package (`package-data` in `pyproject.toml` includes `ui/*`), so they travel in the container image.

A middleware adds these headers to every response whose path starts with `/ui`:

| Header | Value | Purpose |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'` | The page may load only from its own origin. No inline script, no inline style, no framing, no `<base>` tricks, forms only post to itself |
| `Cache-Control` | `no-cache` | A new version is picked up on the next load |
| `X-Content-Type-Options` | `nosniff` | The browser must trust the declared content type |
| `Referrer-Policy` | `no-referrer` | Nothing leaks in a referrer |

The dashboard can launch crawls and delete collections, so this policy is part of its safety. Keep it: any change that needs a looser policy
(an inline `<script>`, a CDN, an inline `style=""`) is the wrong change.

The `/ui` path is **not** covered by the network gate (it holds no data); every API call it makes is.

## Files

| File | Role |
|---|---|
| `index.html` | The page structure: header, the Access panel, the crawl form, the job log, the collections and sources tables, the search drawer |
| `app.css` | Dark theme with a few CSS variables; responsive grid; badges, pills, the token box |
| `app.js` | All behaviour, about 400 lines, no modules or dependencies |

## The rules the code follows

1. **Everything from the API or from a crawled site is untrusted.** It is written to the page with `textContent`, never `innerHTML`. The `el(tag,
   props, ...kids)` helper builds elements: string children become text nodes, attributes are set with `setAttribute`, and any property whose name
   starts with `on` becomes an event listener. No HTML string from the API is ever parsed.
2. **No external requests.** No CDN scripts, fonts or images; the favicon is an empty data URI.
3. **No inline script or style.** Handlers are attached in code; styling lives in `app.css`.
4. **The token stays in the tab.** It is kept in `sessionStorage` under the key `hippo-token` (every access wrapped in try/catch, because storage can
   be blocked) and sent as an `X-Hippo-Token` header, **not** `Authorization`, because a login proxy in front of the page may set its own
   `Authorization`. It is never put in a URL, a cookie or the page text.
5. **Destructive actions confirm.** Delete, Restrict/Share, Rotate and Revoke use `confirm()`.

## Structure of `app.js`

| Section | Functions | Notes |
|---|---|---|
| Helpers | `$`, `el`, `banner`, `fmtDate`, `fmtNum` | `banner` shows errors under the header |
| Token and identity | `getToken`, `setToken`, `authHeaders`, `loadWho` | `loadWho` calls `GET /v1/whoami`, sets the button text and shows or hides the admin-only panel |
| `api(path, options)` | | `fetch` with `credentials: 'same-origin'` and `redirect: 'manual'`; adds the token header; turns a non-2xx answer into an `Error("<status>: <detail>")`; an opaque redirect (a login page from a proxy) becomes "Session expired" |
| Status | `loadStatus` | Pills from `GET /v1/status`; refreshed every 15 seconds |
| Collections | `loadCollections`, `fillSelect`, `toggleVisibility`, `deleteCollection`, `showSources` | The visibility badge and the admin-only Restrict/Share button come from `me.admin` |
| Jobs | `watchJob`, `setJob`, `logLine`, `startCrawl`, `resyncDocs` | See below |
| Search | `openSearch`, `runSearch` | Posts to `/v1/search`; shows score, collection, title, URI, heading path and the passage |
| Access | `loadClients`, `createClient`, `showTokenOnce`, `rotateClient`, `editClient`, `revokeClient`, `useToken` | Admin only; a new or rotated token goes into the one-time box |
| Wiring | `refreshAll` and the event listeners | `refreshAll` = whoami, then status and collections |

### The live job log

The browser's `EventSource` cannot send custom headers, so it could not carry the token. `watchJob` therefore uses `fetch` and reads the response
body as a stream, splitting it on blank lines and parsing the server-sent-event format by hand (`data: <text>` lines, and a final `event: end` block).
When the stream ends it fetches `GET /v1/jobs/{id}` for the final status and result, then refreshes the tables. A new job aborts the previous
stream (`AbortController`).

## Changing it

1. Edit the files in `src/hippocampus/ui/`.
2. Run the service from the source tree to see the change without rebuilding: `PYTHONPATH=src .venv/bin/uvicorn --factory hippocampus.api:app_from_env --port 8099`
   with `HIPPO_DSN` set, or rebuild the container (`./hippocampus up`, which always rebuilds the image first).
3. Hard-reload the browser (the files are served `no-cache`, so a normal reload also works).
4. Run `scripts/check.sh`: the dashboard JavaScript is syntax-checked with node, and the UI test asserts the headers and that the files contain no external addresses.

Guidelines: keep it dependency-free; keep each function small; use `textContent` and `el()`; show every failure in the banner; add the new control to
this page.

## Tests

`tests/test_api.py::test_ui_is_served_with_strict_headers` checks the redirect from `/`, the page title, the CSP, `no-cache`, that the JavaScript and CSS
are served, and that none of the three files contains an `http://` or `https://` address (apart from example text in a placeholder). There is no
browser test in the suite; `scripts/smoke_live.py` and a manual run cover the behaviour of the page against a real instance.
