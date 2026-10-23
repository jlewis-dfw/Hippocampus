# The crawler

`src/hippocampus/crawler.py` fetches web pages and turns them into Markdown documents for the ingestion pipeline. It has three
parts: an HTML-to-Markdown extractor, a crawl loop, and two fetchers (static HTTP and headless Chromium). Fetched content is
untrusted data; nothing in this module interprets it as instructions.

## Overview

For the legal and ethical side of crawling (what to check before you crawl, what this crawler does and does not honour) see
[../User_Guide/legal-and-ethics.md](../User_Guide/legal-and-ethics.md).

```
POST /v1/crawl ─▶ job ─▶ make_crawler()'s callable
                          ├─ check_public_url(start)
                          ├─ pick a fetcher: HttpxFetcher (static) | BrowserFetcher (browser)
                          ├─ crawl_pages(start, options, fetcher, log)   breadth first, same host
                          │     for each page: robots.txt, delay, fetch, parse_html, queue links
                          ├─ docs = {normalised url: Document(title, markdown)}
                          └─ sync_collection(collection, docs, prune=False)
```

The crawl loop is independent of how pages are fetched: it talks to a `Fetcher` protocol (`fetch(url) -> FetchResult`), so the loop,
the filters and the address guard are shared by both engines and are tested with a fake fetcher.

## The public-address guard

`check_public_url(url)` raises `CrawlRefused` unless the URL is `http` or `https` with a host that resolves **only to global
addresses**:

- A literal IP is checked directly; a name is resolved with `getaddrinfo` and **every** returned address must be global
  (`ipaddress.ip_address(...).is_global`). Private ranges, loopback, link-local (including cloud metadata addresses), carrier-grade NAT
  (VPN ranges), multicast and reserved addresses are all refused.
- A URL containing a guarded fragment (`FORBIDDEN_URL_PARTS`) is refused.
- It runs on the start URL, on **every redirect hop** of the static fetcher (redirects are followed by hand), on every queued URL when it is
  fetched, and on **every sub-request and the final address** in the browser engine.

Why: the service runs inside a private network, so without this a crawl request could be aimed at internal services (secret stores,
model servers, router pages). **Known limit:** the name is resolved separately from the connection, so DNS rebinding is not defended
against. WebSocket connections opened by a rendered page are not routed through the check.

A refused URL is logged as `refused <url>: <reason>` and skipped; it does not stop the crawl.

## HTML to Markdown

`parse_html(html, url)` runs a single pass with the standard-library `HTMLParser` (no extra dependencies) and returns a `Page(url, title,
markdown, links)`.

| HTML | Becomes |
|---|---|
| `<title>` | the page title (not part of the text) |
| `h1` to `h6` | `#` to `######` headings |
| `p`, `div`, `section`, `article`, `main`, `ul`, `ol`, `table`, `blockquote`, `tr`, `dl`, `header` | paragraph breaks |
| `ul`/`ol`/`li` | `-` and numbered items, indented by nesting |
| `pre` | a fenced block; `code` inline becomes backticks |
| `strong`/`b`, `em`/`i` | `**bold**`, `*italic*` |
| `a href` | `[text](absolute url)`; a link with no text is dropped |
| `td`/`th`/`tr` | a simple row of cells separated by bars (`\|`), one line per table row |
| `br`, `hr` | a line break, `---` |
| `script`, `style`, `noscript`, `nav`, `footer`, `aside`, `form`, `svg`, `template`, `button` | **dropped with their content** |

Whitespace is collapsed (outside `pre`), runs of blank lines shrink to one, and the result is trimmed. All `<a href>` values are
collected, including those inside dropped elements, as the page's `links`. A page whose Markdown is empty is skipped.

## The crawl loop

`crawl_pages(start_url, opts, fetcher, log, ...)` returns the pages in visit order. `CrawlOptions`:

| Option | Default | Meaning |
|---|---|---|
| `depth` | 1 | 1 = the start page only; 2 = plus pages it links to; each level adds one hop |
| `match_pattern` | none | regex **searched** in a link's address; non-matching links are not followed |
| `max_pages` | 200 (`HIPPO_CRAWL_MAX_PAGES`) | stop after this many pages |
| `delay` | 0.5 s (`HIPPO_CRAWL_DELAY`) | pause between requests (not before the first) |
| `respect_robots` | true | obey `robots.txt` |

Behaviour:

- **Breadth first** from the normalised start URL (lowercase scheme and host, empty path becomes `/`, fragment removed).
- **Same host only:** a link must have the same host (including port) as the start URL.
- A link is skipped if it is not `http(s)`, was already seen, has a path ending in a skipped extension (images, archives, PDFs, media,
  fonts, `.css`, `.js`, `.json`, `.xml`), contains a guarded fragment, or fails `match_pattern`.
- **robots.txt** is fetched once from the start host (a failure or a missing file means allowed) and checked with the crawler's user agent for every URL;
  a disallowed URL is logged `skip (robots.txt) <url>`.
- A response is used only if the status is `200` and the body is non-empty HTML; otherwise `skip (<status> <content-type>) <url>`.
- One failing page never ends the crawl: exceptions are logged `error <url>: <type>: <message>` and the loop continues.
- Each page logs `fetched <url> (<n> chars, level <k>)`.

## Fetchers

### Static (`HttpxFetcher`)

`httpx` with `follow_redirects=False`: each redirect is followed by hand so every hop passes `check_public_url`, up to 5 hops
(`too many redirects` after that). It accepts only `text/html` and `application/xhtml+xml`, streams the body and stops reading at
2,000,000 bytes (`MAX_BYTES`), decoding with the response's encoding (UTF-8 fallback, errors replaced). Timeout 15 s. The user agent
identifies the crawler. It also provides `fetch_robots`.

### Browser (`BrowserFetcher`)

Headless Chromium through Playwright, for pages that build their content with JavaScript. Created **per job** (Playwright's synchronous API
is bound to its thread) and closed at the end of the job.

- Launch arguments `--no-sandbox --disable-dev-shm-usage --disable-gpu` (the container has no user namespaces for the sandbox).
- The context blocks service workers and downloads, and routes **every** request through a handler that aborts images, media, fonts and
  stylesheets and runs `check_public_url` on everything else (navigations, scripts, XHR, iframes).
- A page load waits for `load`, then up to five settle periods for network idle (busy pages never idle; it renders what it has), then
  one settle period (1 s); the final address is checked again, and the page content (at most 2 MB) is returned.
- It needs the `browser` extra and the Chromium binary. Without them the job fails with `engine 'browser' needs the 'browser' extra
  (playwright) and Chromium`. The container image installs Chromium only when built with `HIPPO_INSTALL_BROWSER=1` (about 400 MB more).

## Integration (`make_crawler`)

`make_crawler(store_factory, embedder, fetcher=None, max_pages=200, delay=0.5, ...)` returns the `crawler(job, request)` callable the API
runs in a job. It validates the engine (`static` or `browser`), checks the start URL, crawls, builds `{normalise(url): Document(title,
markdown)}`, logs `embedding N pages`, runs `sync_collection(prune=False)` and returns `{"pages", "added", "changed", "unchanged"}`.
Because it never prunes, pages that vanished from the site remain in the collection.

## Limits and non-goals

- No authentication to the target site, no cookies, no form submission, no sitemap reading, no PDF handling.
- One host per crawl; no politeness beyond the fixed delay and robots.txt (set `HIPPO_CRAWL_DELAY` higher for fragile sites).
- Not a general scraper: it extracts readable text and links, and drops navigation and page furniture.

## Tests

`tests/unit/test_crawler.py` covers the HTML extractor, URL normalisation, the crawl loop with a fake fetcher (depth, host, pattern, robots,
cap, errors), the address guard (private, loopback, link-local, VPN and metadata addresses, redirects), the browser fetcher with a fake
browser, and the API integration. The test that needs a real Chromium is skipped when Playwright's browser is not installed.
