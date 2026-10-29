"""Web crawler (HIPPO-E03-F1-S01).

Engine-independent crawl logic (depth, domain and pattern filters, robots.txt, page cap, politeness
delay, SSRF guard) behind a small ``Fetcher`` protocol. The default ``static`` engine fetches with httpx
and converts HTML to Markdown with the stdlib parser, so it needs no browser. The ``browser`` engine
renders pages in headless Chromium (Playwright, the layer Crawl4AI and Scrapling are built on) for
JavaScript-only sites; it is another ``Fetcher``, so the crawl loop, filters and SSRF guard are shared.

Fetched content is untrusted data. Nothing here interprets it as instructions.
"""
from __future__ import annotations

import ipaddress
import math
import re
import socket
import time
import urllib.robotparser
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urldefrag, urljoin, urlparse

from ..core.diff import Document, sync_collection
from .sync_core import exclude_path_parts

USER_AGENT = "Hippocampus-crawler/0.1 (+internal)"
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".tgz",
    ".mp4", ".mp3", ".woff", ".woff2", ".ttf", ".css", ".js", ".json", ".xml",
)


# --------------------------------------------------------------------------- HTML -> Markdown

_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "aside", "form", "svg", "template", "button"}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "ul", "ol", "table", "blockquote", "tr", "dl", "header"}
_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


class _Extractor(HTMLParser):
    """Collects the page title, Markdown text and outgoing links in one pass."""

    def __init__(self, base_url: str, only_main: bool = False):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._only_main = only_main  # keep only what is inside <main> or role="main"; links are collected everywhere
        self._stack: list[tuple[str, bool]] = []  # open (tag, is_main) so the end of a role="main" div is found
        self._main_depth = 0
        self.saw_main = False
        self.title = ""
        self.links: list[str] = []
        self._out: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_pre = False
        self._list_stack: list[list] = []  # [kind, counter]
        self._href: str | None = None
        self._link_start = 0

    # -- helpers
    def _emitting(self) -> bool:
        return not self._only_main or self._main_depth > 0

    def _emit(self, text: str) -> None:
        if self._emitting():
            self._out.append(text)

    def _newlines(self, n: int = 2) -> None:
        joined = "".join(self._out)
        trailing = len(joined) - len(joined.rstrip("\n"))
        if joined and trailing < n:
            self._emit("\n" * (n - trailing))

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag not in _VOID_TAGS:
            is_main = tag == "main" or (a.get("role") or "").lower() == "main"
            self._stack.append((tag, is_main))
            self._main_depth += is_main
            self.saw_main = self.saw_main or is_main
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = 1
            return
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._newlines()
            self._emit("#" * int(tag[1]) + " ")
        elif tag == "pre":
            self._newlines()
            self._emit("```\n")
            self._in_pre = True
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag in ("ul", "ol"):
            self._newlines(1)
            self._list_stack.append([tag, 0])
        elif tag == "li":
            self._newlines(1)
            indent = "  " * max(len(self._list_stack) - 1, 0)
            if self._list_stack and self._list_stack[-1][0] == "ol":
                self._list_stack[-1][1] += 1
                self._emit(f"{indent}{self._list_stack[-1][1]}. ")
            else:
                self._emit(f"{indent}- ")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._newlines()
            self._emit("---")
            self._newlines()
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "a" and a.get("href") and self._emitting():
            self._href = urljoin(self.base_url, a["href"])
            self._link_start = len(self._out)
            self._emit("[")
        elif tag in ("td", "th"):
            self._emit("| ")
        elif tag in _BLOCK_TAGS:
            self._newlines()

    def handle_endtag(self, tag):
        if tag not in _VOID_TAGS:  # close back to the matching open tag, as a browser does for unclosed ones
            for i in range(len(self._stack) - 1, -1, -1):
                if self._stack[i][0] == tag:
                    self._main_depth -= sum(1 for _, is_main in self._stack[i:] if is_main)
                    del self._stack[i:]
                    break
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._newlines()
        elif tag == "pre":
            self._newlines(1)
            self._emit("```")
            self._newlines()
            self._in_pre = False
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._newlines()
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "a" and self._href is not None:
            text = "".join(self._out[self._link_start + 1:]).strip()
            if text:
                self._emit(f"]({self._href})")
            else:  # empty link: drop the opening bracket
                del self._out[self._link_start:]
            self._href = None
        elif tag in ("td", "th"):
            self._emit(" ")
        elif tag == "tr":
            self._emit("|")
            self._newlines(1)
        elif tag in _BLOCK_TAGS or tag == "li":
            self._newlines(1 if tag == "li" else 2)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        if self._in_pre:
            self._emit(data)
        else:
            self._emit(re.sub(r"\s+", " ", data))

    def markdown(self) -> str:
        text = "".join(self._out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    markdown: str
    links: list[str]
    depth: int = 1  # the level it was found at in a crawl (the start page is 1)


MAIN_MIN_CHARS = 200  # a <main> with less text than this is not trusted to be the whole article


def parse_html(html: str, url: str) -> Page:
    """Title, Markdown and links of a page. A page that marks its main content (<main> or role="main") is read from
    there only, which leaves out menus, sidebars and footers built from plain divs; a tiny one is ignored."""
    ex = _Extractor(url)
    ex.feed(html)
    ex.close()
    title = re.sub(r"\s+", " ", ex.title).strip()
    markdown = ex.markdown()
    if ex.saw_main:
        inner = _Extractor(url, only_main=True)
        inner.feed(html)
        inner.close()
        if len(inner.markdown()) >= MAIN_MIN_CHARS:
            markdown = inner.markdown()
    return Page(url=url, title=title, markdown=markdown, links=ex.links)


# --------------------------------------------------------------------------- site-wide repeated lines

BOILERPLATE_MIN_PAGES = 10  # fewer pages than this and "most of them" proves nothing


def _body_lines(markdown: str):
    """Yield (line, kind) with kind one of "code" (fence lines and what they hold), "blank", "heading" or "text"."""
    in_code = False
    for raw in markdown.split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            yield raw, "code"
        elif in_code:
            yield raw, "code"
        elif not stripped:
            yield raw, "blank"
        else:
            yield raw, "heading" if stripped.startswith("#") else "text"


def _drop_empty_headings(lines: list[tuple[str, str]]) -> list[str]:
    """The lines whose heading is still followed by something: a heading with nothing under it, or only another
    heading, is left over from a removed menu or footer."""
    kept: list[str] = []
    for i, (raw, kind) in enumerate(lines):
        if kind == "heading":
            following = next((k for _, k in lines[i + 1:] if k != "blank"), None)
            if following in (None, "heading"):
                continue
        kept.append(raw)
    return kept


def strip_repeated_lines(pages: list[Page], threshold: float = 0.6,
                         min_pages: int = BOILERPLATE_MIN_PAGES) -> tuple[list[Page], list[str]]:
    """Remove the lines that appear on at least ``threshold`` of the pages (menus, footers, promos built from plain divs).

    Code blocks are never touched, and a heading is only removed when it is left with nothing under it, since reference
    pages share headings such as "Syntax". Returns the pages and the removed lines, most widespread first. Does nothing
    under ``min_pages`` pages or at a threshold of 0.
    """
    if threshold <= 0 or len(pages) < min_pages:
        return pages, []
    need = max(2, math.ceil(threshold * len(pages)))
    seen: Counter[str] = Counter()
    for page in pages:
        seen.update({raw.strip() for raw, kind in _body_lines(page.markdown) if kind == "text"})
    common = {line for line, n in seen.items() if n >= need}
    if not common:
        return pages, []
    out = []
    for page in pages:
        lines = [(raw, kind) for raw, kind in _body_lines(page.markdown) if not (kind == "text" and raw.strip() in common)]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(_drop_empty_headings(lines))).strip()
        out.append(replace(page, markdown=text))
    return out, sorted(common, key=lambda line: -seen[line])


# --------------------------------------------------------------------------- fetching

class CrawlRefused(Exception):
    """A URL was refused by policy (not an error in the target)."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    text: str
    x_robots_tag: str = ""  # the X-Robots-Tag response header, if any (reported by the probe; the crawler does not act on it)


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


def check_public_url(url: str) -> None:
    """Refuse non-http(s) URLs and any host that resolves to a non-public address.

    The crawler runs inside a private network, so without this a crawl request could be
    pointed at internal services (secret stores, model servers, router UIs, VPN addresses). Known limit: the check
    resolves the name separately from the connection (DNS rebinding is not defended against).
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CrawlRefused(f"unsupported URL {url!r}")
    if any(p in url.lower() for p in exclude_path_parts()):
        raise CrawlRefused("this source is excluded from ingestion")
    host = parts.hostname
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise CrawlRefused(f"cannot resolve {host!r}: {exc}") from exc
        addrs = [ipaddress.ip_address(i[4][0]) for i in infos]
    for ip in addrs:
        if not ip.is_global:  # private, loopback, link-local, CGNAT/VPN, multicast, reserved
            raise CrawlRefused(f"{host!r} resolves to a non-public address")


class HttpxFetcher:
    """Static fetcher: GET with manual redirect handling so every hop passes the SSRF check."""

    def __init__(self, timeout: float = 15.0, client=None):
        import httpx

        self._client = client or httpx.Client(
            timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )

    def fetch(self, url: str) -> FetchResult:
        return self._get(url, ("text/html", "application/xhtml+xml"))

    def fetch_robots(self, url: str) -> str | None:
        res = self._get(url, ("text/plain",))
        return res.text if res.status == 200 else None

    def fetch_sitemap(self, url: str) -> str | None:
        res = self._get(url, ("application/xml", "text/xml", "text/plain"))
        return res.text if res.status == 200 else None

    def _get(self, url: str, accepted: tuple[str, ...]) -> FetchResult:
        for _ in range(MAX_REDIRECTS + 1):
            check_public_url(url)
            with self._client.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                tag = resp.headers.get("x-robots-tag", "")
                if ctype not in accepted:
                    return FetchResult(url, resp.status_code, ctype, "", tag)
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                text = bytes(body[:MAX_BYTES]).decode(resp.encoding or "utf-8", errors="replace")
                return FetchResult(url, resp.status_code, ctype, text, tag)
        raise CrawlRefused("too many redirects")


BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}
BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]  # rootless container, no user namespaces for the sandbox


class BrowserFetcher:
    """JavaScript-rendering fetcher: headless Chromium via Playwright.

    Every request the page makes (navigation hops, scripts, XHR, iframes) is routed through
    ``check_public_url``, so a rendered page cannot reach private or VPN addresses either.
    Playwright's sync API is bound to the thread that started it, so create one instance per job and
    call ``close()`` when done (``make_crawler`` does). Known limit: WebSockets are not routed.
    """

    def __init__(self, timeout: float = 30.0, settle: float = 1.0, launch=None):
        self._timeout_ms = int(timeout * 1000)
        self._settle_ms = int(settle * 1000)
        self._launch = launch  # test seam: () -> (playwright_or_None, browser)
        self._pw: Any = None  # playwright objects; typed Any because the extra is optional
        self._browser: Any = None
        self._context: Any = None
        self._robots = HttpxFetcher(timeout=15.0)

    def _ensure(self) -> None:
        if self._context is not None:
            return
        if self._launch:
            self._pw, self._browser = self._launch()
        else:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError("engine 'browser' needs the 'browser' extra (playwright) and Chromium") from exc
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True, args=BROWSER_ARGS)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT, service_workers="block", accept_downloads=False, java_script_enabled=True
        )
        self._context.route("**/*", self._route)

    @staticmethod
    def _route(route) -> None:
        req = route.request
        if req.resource_type in BLOCKED_RESOURCES:
            route.abort()
            return
        try:
            check_public_url(req.url)
        except CrawlRefused:
            route.abort()
            return
        route.continue_()

    def fetch(self, url: str) -> FetchResult:
        check_public_url(url)
        self._ensure()
        page = self._context.new_page()
        try:
            page.set_default_timeout(self._timeout_ms)
            resp = page.goto(url, wait_until="load")
            try:
                page.wait_for_load_state("networkidle", timeout=self._settle_ms * 5)
            except Exception:  # busy pages never go idle; render what we have
                pass
            if self._settle_ms:
                page.wait_for_timeout(self._settle_ms)
            final = page.url
            check_public_url(final)
            status = resp.status if resp else 0
            ctype = (resp.headers.get("content-type", "") if resp else "").split(";")[0].strip().lower()
            if ctype not in ("text/html", "application/xhtml+xml"):
                return FetchResult(final, status, ctype, "")
            return FetchResult(final, status, ctype, page.content()[:MAX_BYTES])
        finally:
            page.close()

    def fetch_robots(self, url: str) -> str | None:
        return self._robots.fetch_robots(url)

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._context = self._browser = self._pw = None


# --------------------------------------------------------------------------- crawl loop

def normalize(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    return p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower(), path=path, fragment="").geturl()


@dataclass
class CrawlOptions:
    depth: int = 1  # 1 = the start page only; 2 = plus pages it links to; and so on
    match_pattern: str | None = None  # regex searched in the URL; non-matching links are not followed
    max_pages: int = 200
    delay: float = 0.5
    respect_robots: bool = True


def crawl_pages(
    start_url: str,
    opts: CrawlOptions,
    fetcher: Fetcher,
    log: Callable[[str], None] = lambda m: None,
    robots_fetcher: Callable[[str], str | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Page]:
    """Breadth-first crawl restricted to the start URL's host. Returns pages in visit order."""
    start = normalize(start_url)
    host = urlparse(start).netloc
    pattern = re.compile(opts.match_pattern) if opts.match_pattern else None

    robots = None
    if opts.respect_robots:
        robots = urllib.robotparser.RobotFileParser()
        robots_url = f"{urlparse(start).scheme}://{host}/robots.txt"
        body = None
        try:
            getter = robots_fetcher or getattr(fetcher, "fetch_robots", None)
            body = getter(robots_url) if getter else None
        except Exception:  # missing robots.txt means allowed
            body = None
        robots.parse((body or "").splitlines())

    queue: deque[tuple[str, int]] = deque([(start, 1)])
    seen = {start}
    pages: list[Page] = []
    first = True
    while queue and len(pages) < opts.max_pages:
        url, level = queue.popleft()
        if robots is not None and not robots.can_fetch(USER_AGENT, url):
            log(f"skip (robots.txt) {url}")
            continue
        if not first:
            sleep(opts.delay)
        first = False
        try:
            res = fetcher.fetch(url)
        except CrawlRefused as exc:
            log(f"refused {url}: {exc}")
            continue
        except Exception as exc:  # one bad page must not end the crawl
            log(f"error {url}: {type(exc).__name__}: {exc}")
            continue
        if res.status != 200 or not res.text:
            log(f"skip ({res.status} {res.content_type or 'no content'}) {url}")
            continue
        page = parse_html(res.text, res.url)
        if not page.markdown:
            log(f"skip (empty) {url}")
            continue
        page = replace(page, depth=level)
        pages.append(page)
        log(f"fetched {url} ({len(page.markdown)} chars, level {level})")
        if level >= opts.depth:
            continue
        for href in page.links:
            nxt = normalize(urljoin(res.url, href))
            p = urlparse(nxt)
            if (p.scheme not in ("http", "https") or p.netloc != host or nxt in seen
                    or p.path.lower().endswith(SKIP_EXTENSIONS)
                    or any(x in nxt.lower() for x in exclude_path_parts())
                    or (pattern and not pattern.search(nxt))):
                continue
            seen.add(nxt)
            queue.append((nxt, level + 1))
    return pages


# --------------------------------------------------------------------------- API integration

ENGINES = ("static", "browser")


def make_crawler(store_factory, embedder, fetcher: Fetcher | None = None, *, max_pages: int = 200,
                 delay: float = 0.5, sleep: Callable[[float], None] = time.sleep,
                 browser_factory: Callable[[], Fetcher] = BrowserFetcher, boilerplate_threshold: float = 0.6):
    """Return the ``crawler(job, request)`` callable that ``create_app`` expects.

    ``fetcher`` overrides the static engine; ``browser_factory`` builds a fresh browser fetcher per job.
    ``boilerplate_threshold``: lines on at least this share of the crawled pages are removed before indexing (0 = off).
    """

    def crawler(job, req) -> dict:
        if req.engine not in ENGINES:
            raise ValueError(f"engine {req.engine!r} is not available (use one of {', '.join(ENGINES)})")
        check_public_url(req.url)
        opts = CrawlOptions(depth=req.depth, match_pattern=req.match_pattern, max_pages=max_pages, delay=delay)
        active = (fetcher or HttpxFetcher()) if req.engine == "static" else browser_factory()
        try:
            pages = crawl_pages(req.url, opts, active, job.log, sleep=sleep)
        finally:
            if hasattr(active, "close"):
                active.close()
        pages, common = strip_repeated_lines(pages, boilerplate_threshold)
        if common:
            shown = "; ".join(line[:50] for line in common[:3])
            job.log(f"removed {len(common)} site-wide repeated lines (on {boilerplate_threshold:.0%}+ of the pages), "
                    f"for example: {shown}")
        emptied = [p for p in pages if not p.markdown]
        if emptied:
            job.log(f"skipped {len(emptied)} pages that had nothing but repeated lines")
            pages = [p for p in pages if p.markdown]
        docs = {normalize(p.url): Document(p.title or None, p.markdown) for p in pages}
        job.log(f"embedding {len(docs)} pages")
        def progress(done: int, total: int) -> None:
            if done and (done % 10 == 0 or done == total):
                job.log(f"embedded {done}/{total} pages")

        with store_factory() as store:
            result = sync_collection(req.collection, docs, store, embedder, prune=False, progress=progress)
        job.log(f"done: {len(result.added)} added, {len(result.changed)} changed, {len(result.unchanged)} unchanged")
        return {"pages": len(docs), "added": len(result.added), "changed": len(result.changed),
                "unchanged": len(result.unchanged), "depths": {normalize(p.url): p.depth for p in pages}}

    return crawler
