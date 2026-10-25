# Dashboard internals

How the web dashboard is built. For how to *use* it, see [../User_Guide/dashboard.md](../User_Guide/dashboard.md).

The dashboard is a few static files served by the API itself: no build step, no framework, and no request to any other site. The one
third-party file is the vendored Swagger UI for the API tab. Source: `src/hippocampus/service/ui/`.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## How it is served

`api.py` mounts the folder with Starlette's `StaticFiles(directory=UI_DIR, html=True)` at `/ui`, and `GET /` redirects to `/ui/`. The files are
packaged with the Python package (`package-data` in `pyproject.toml` includes `service/ui/*` and the vendored Swagger UI), so they travel in the container image.

A middleware adds these headers to every response whose path starts with `/ui`:

| Header | Value | Purpose |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'` | The page may load only from its own origin. No inline script, no inline style, no framing, no `<base>` tricks, forms only post to itself |
| `Cache-Control` | `no-cache` | A new version is picked up on the next load |
| `X-Content-Type-Options` | `nosniff` | The browser must trust the declared content type |
| `Referrer-Policy` | `no-referrer` | Nothing leaks in a referrer |

One page is different: `/ui/api.html` (the API tab, below) gets the same policy with `style-src 'self' 'unsafe-inline'` and
`frame-ancestors 'self'`, because Swagger UI writes inline styles and the dashboard frames it. It still runs no inline script and loads
nothing from outside.

The dashboard can launch crawls and delete collections, so this policy is part of its safety. Keep it: any change that needs a looser policy
(an inline `<script>`, a CDN, an inline `style=""`) is the wrong change.

The `/ui` path is **not** covered by the network gate (it holds no data); every API call it makes is.

## Files

| File | Role |
|---|---|
| `index.html` | The page structure: header, the tab bar (Collections, Search, Local, Web, Access, API) and its six panels, and the job log |
| `app.css` | Dark theme with a few CSS variables; responsive grid; badges, pills, the token box |
| `app.js` | The behaviour of everything except the folder drop, about 400 lines, no modules or dependencies |
| `sync.js` | The folder drop: reading a dropped or chosen folder, the root-folder tree, the rules, the preview, batched upload with cancel, and the saved folders table. Loaded before `app.js`, which calls `initFolderSync()` |
| `api.html`, `api.js`, `api.css` | The API tab's page: Swagger UI reading `/openapi.json`, framed by the dashboard, in the dashboard's dark theme (`api.css` overrides the vendored stylesheet with the same colours as `app.css`; Swagger UI has no dark mode of its own). `api.js` adds the dashboard's token (from the shared `sessionStorage`) to requests to this server only, so **Try it out** runs with the access already entered. The iframe loads on first use of the tab |
| `vendor/swagger-ui/` | `swagger-ui-dist` 5.34.0 from npm, unmodified (`swagger-ui-bundle.js`, `swagger-ui.css`, licence and notice; `VERSION.txt`). To update: `npm pack swagger-ui-dist@<version>` in an empty directory and copy the same files |
| `folder.js` | The folder rules as pure functions (path normalisation, globs, default excludes, secret patterns), the same decisions as `sync_core.py`. Checked against it by `tests/vectors/filter_vectors.json` |

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

## Tabs

`showTab(name)` in `app.js` shows one `role="tabpanel"` and hides the others, sets `aria-selected` and the roving `tabindex`, and writes
the name to the URL hash (`history.replaceState`, so the back button is not filled with tab changes). `hashchange` and the first load
read it back. An unknown hash falls back to Collections. Other code that needs a panel calls `showTab` (the identity button, and **Drop
again** on a saved folder). Tests that touch a control must click its tab first; the e2e helpers do.

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

1. Edit the files in `src/hippocampus/service/ui/`.
2. Run the service from the source tree to see the change without rebuilding: `PYTHONPATH=src .venv/bin/uvicorn --factory hippocampus.service.api:app_from_env --port 8099`
   with `HIPPO_DSN` set, or rebuild the container (`./hippocampus up`, which always rebuilds the image first).
3. Hard-reload the browser (the files are served `no-cache`, so a normal reload also works).
4. Run `scripts/check.sh`: the dashboard JavaScript is syntax-checked with node, and the UI test asserts the headers and that the files contain no external addresses.

Guidelines: keep it dependency-free; keep each function small; use `textContent` and `el()`; show every failure in the banner; add the new control to
this page.

### Folder drop (`sync.js`, `folder.js`)

- A folder arrives three ways: **drop** (`DataTransferItem.webkitGetAsEntry()`, entries are taken synchronously before the first `await`),
  **Choose folder** (`<input webkitdirectory>`), or a stored `FileSystemDirectoryHandle` (Chromium, kept in IndexedDB for the one-click
  **Re-sync**; a failure to read it just means "drop again"). All three produce the same list of `{path, size, getFile}`.
- `.git`, `node_modules`, `.svn` and `.hg` are never walked into. Everything else is listed, and the rules decide.
- `folder.js` repeats the server's path rules and secret patterns, so a file with a secret in it is never sent. The server checks again.
- Upload is one session per target collection, 200 files or 4 million characters per request, then a final batch that prunes. A
  cancel closes the session (`DELETE /v1/ingest/files/{session}`); nothing is pruned.
- When a saved source's plan changes (a folder unticked or moved to another collection), the collections it no longer feeds get an
  empty final batch, which removes what the source left there.
- Everything from the folder or the server is written with `textContent`.

## Tests

`tests/unit/test_api.py::test_api_tab_page_allows_inline_styles_only_there_and_is_framable_only_by_the_dashboard` checks the separate policy of `api.html`, that the other pages keep the strict one, and that the vendored files are served.

`tests/unit/test_api.py::test_ui_is_served_with_strict_headers` checks the redirect from `/`, the page title, the CSP, `no-cache`, that the JavaScript and CSS
are served, and that none of the dashboard files contains an `http://` or `https://` address (apart from example text in a placeholder). There is no
browser test in the default suite (`tests/e2e/test_folder_drop.py` drives the folder drop in Chromium when you run the e2e tests); `scripts/smoke_live.py` and a manual run cover the behaviour of the page against a real instance.
