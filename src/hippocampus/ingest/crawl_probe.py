"""Pre-crawl probe: what a crawl of a site would meet, found with at most four polite requests.

It fetches ``robots.txt``, the start page and up to two sitemaps (listed ones under the start path first, else
``/sitemap.xml``), through the same fetcher,
user agent and public-address check as a crawl, and returns plain findings plus suggested settings (engine, depth, pattern).
Nothing is stored. The numbers are estimates: only the start page and the sitemap are seen, not the site.

Fetched content is untrusted data. Nothing here interprets it as instructions.
"""
from __future__ import annotations

import importlib.util
import re
import urllib.robotparser
from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import urljoin, urlparse

from .crawler import SKIP_EXTENSIONS, USER_AGENT, HttpxFetcher, check_public_url, normalize, parse_html

MIN_WORDS = 100  # a start page with less text than this, and signs of an app shell, needs the browser engine
_APP_ROOT = re.compile(r"""id=["'](?:root|__next|app|__nuxt)["']|<noscript""", re.I)
_META_ROBOTS = re.compile(r"""<meta[^>]+name=["']robots["'][^>]*content=["']([^"']+)""", re.I)
_CONTENT_SIGNAL = re.compile(r"^content-signal:\s*(.+)$", re.I | re.M)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def browser_available() -> bool:
    """True when Playwright is installed in this image (the ``browser`` engine also needs Chromium)."""
    return importlib.util.find_spec("playwright") is not None


def _safe(call: Callable[[], str | None]) -> str | None:
    try:
        return call()
    except Exception:  # a missing or unreadable file is a finding, not an error
        return None


def _prefix_of(path: str) -> str:
    """The directory part of a path: '/docs/en/intro' -> '/docs/en/'; '/docs/en/' stays; '/' stays."""
    return path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"


def _segments_below(path: str, base: str) -> int:
    """Path segments of ``path`` under ``base`` ('/docs/en/a/b' under '/docs/en/' is 2); a path outside it counts as 1."""
    if not path.startswith(base):
        return 1
    return len([seg for seg in path[len(base):].split("/") if seg])


def probe_site(url: str, match_pattern: str | None = None, fetcher=None, *, max_pages: int = 200, delay: float = 0.5,
               has_browser: bool | None = None) -> dict:
    """Probe ``url`` and return findings, warnings and a ``suggest`` block. Raises CrawlRefused for a refused address."""
    fetcher = fetcher or HttpxFetcher()
    has_browser = browser_available() if has_browser is None else has_browser
    pattern = re.compile(match_pattern) if match_pattern else None
    warnings: list[str] = []
    start = normalize(url)
    parts = urlparse(start)
    origin = f"{parts.scheme}://{parts.netloc}"

    # robots.txt
    body = _safe(lambda: fetcher.fetch_robots(origin + "/robots.txt"))
    rp = urllib.robotparser.RobotFileParser()
    rp.parse((body or "").splitlines())
    allowed = rp.can_fetch(USER_AGENT, start) if body is not None else True
    raw_delay = rp.crawl_delay(USER_AGENT)
    crawl_delay = float(raw_delay) if raw_delay else None
    sitemap_urls = rp.site_maps() or []
    signal = _CONTENT_SIGNAL.search(body or "")
    robots: dict[str, Any] = {"found": body is not None, "allowed": allowed, "crawl_delay": crawl_delay,
              "content_signal": signal.group(1).strip() if signal else None, "sitemaps": sitemap_urls}
    if not allowed:
        warnings.append("robots.txt disallows the start URL for the crawler: do not crawl it")
    if crawl_delay and crawl_delay > delay:
        warnings.append(f"robots.txt asks for a {crawl_delay:g} s Crawl-delay; the crawler ignores it and waits {delay:g} s "
                        "(raise HIPPO_CRAWL_DELAY to at least that, or do not crawl)")
    if body is None:
        warnings.append("no robots.txt could be read; the crawler treats that as allowed, so check the site's rules yourself")

    # the start page (not fetched when robots.txt disallows it)
    page_info: dict = {"fetched": False}
    links: list[str] = []
    final = start
    needs_js = False
    if allowed:
        res = fetcher.fetch(start)
        final = normalize(res.url)
        page = parse_html(res.text, final) if res.text else None
        words = len(page.markdown.split()) if page else 0
        host = urlparse(final).netloc
        seen: dict[str, None] = {}
        for link in (page.links if page else []):
            n = normalize(urljoin(final, link))
            if urlparse(n).netloc == host and not urlparse(n).path.lower().endswith(SKIP_EXTENSIONS):
                seen[n] = None
        links = list(seen)
        meta = _META_ROBOTS.search(res.text or "")
        directives = " ".join(x for x in (meta.group(1) if meta else "", res.x_robots_tag) if x).lower()
        page_info = {"fetched": True, "status": res.status, "final_url": final, "content_type": res.content_type,
                     "words": words, "same_host_links": len(links), "robots_directives": directives or None}
        if res.status != 200 or page is None:
            warnings.append(f"the start page answered HTTP {res.status} ({res.content_type or 'no content type'}): "
                            "the crawler only reads HTML pages")
        if urlparse(final).netloc != urlparse(start).netloc:
            warnings.append(f"the start URL redirects to another host ({urlparse(final).netloc}); the crawler only follows "
                            "links on the start URL's own host, so use the final URL instead")
        if "noindex" in directives or "nofollow" in directives:
            warnings.append(f"the page asks for '{directives}'; the crawler does not honour it, so do not crawl if that is "
                            "an opt-out")
        needs_js = bool(page) and words < MIN_WORDS and (bool(_APP_ROOT.search(res.text)) or not links)
        if needs_js:
            warnings.append(f"only {words} words of text on the start page and it looks like a script-built app: a static "
                            "crawl will probably find little")
        dup = sum(1 for u in links if u.endswith("/") and u.rstrip("/") in seen)
        if dup:
            warnings.append(f"{dup} page(s) are linked both with and without a trailing slash and would be stored twice")

    # the pattern and the links it keeps
    suggested_pattern = match_pattern
    if pattern is None:
        prefix = _prefix_of(urlparse(final).path or "/")
        if prefix != "/" and any(prefix in urlparse(u).path for u in links):
            suggested_pattern = re.escape(prefix)
        elif prefix == "/":
            warnings.append("no match pattern and the start page is the site root: the crawl would cover the whole host")
    matching = [u for u in links if re.search(suggested_pattern, u)] if suggested_pattern else links
    if allowed and page_info.get("fetched") and not matching:
        warnings.append("no link on the start page matches the pattern: the crawl would index the start page only")

    # the sitemap: up to two of the listed ones (or /sitemap.xml), those under the start path's first segment first
    sitemap: dict = {"found": False}
    first = "/" + (urlparse(final).path.strip("/").split("/")[0] if urlparse(final).path.strip("/") else "")
    same_host = [s for s in sitemap_urls if urlparse(s).netloc == parts.netloc]
    candidates = sorted(same_host, key=lambda s: not urlparse(s).path.startswith(first + "/"))[:2] or [origin + "/sitemap.xml"]
    fetch_sitemap = getattr(fetcher, "fetch_sitemap", None)
    urls: list[str] = []
    keep = re.compile(suggested_pattern) if suggested_pattern else None
    if fetch_sitemap is not None:
        for cand in candidates:
            xml = _safe(partial(fetch_sitemap, cand))
            if not xml:
                continue
            locs = _LOC.findall(xml)
            if "<sitemapindex" in xml.lower():
                sitemap = {"found": True, "url": cand, "index": True, "sitemaps": len(locs)}
                warnings.append(f"the sitemap is an index of {len(locs)} sitemaps; the size of the site is not estimated")
                continue
            found = [u for u in locs if urlparse(u).netloc == urlparse(final).netloc and (keep is None or keep.search(u))]
            sitemap = {"found": True, "url": cand, "index": False, "urls": len(locs), "matching": len(found)}
            if found:
                urls = found
                break
    if sitemap["found"] and not sitemap.get("index") and not urls:
        warnings.append("the sitemap lists no URL that matches the pattern, so the size is not estimated")

    # suggestions
    engine = "browser" if needs_js and has_browser else "static"
    if needs_js and not has_browser:
        warnings.append("the browser engine is not installed in this image (build with HIPPO_INSTALL_BROWSER=1)")
    if urls:
        base = _prefix_of(urlparse(final).path or "/")
        depths = sorted(_segments_below(urlparse(u).path, base) for u in urls)
        p90 = depths[min(len(depths) - 1, int(len(depths) * 0.9))]
        depth, depth_basis = (2 if p90 <= 1 else 3), "sitemap URL depth"
    else:
        depth, depth_basis = 2, "default: no sitemap to measure"
    pages = min(len(urls), max_pages) if urls else None
    if urls and len(urls) > max_pages:
        warnings.append(f"the sitemap lists {len(urls)} matching pages and the cap is {max_pages}: the crawl stops at {max_pages} "
                        "in breadth-first order; narrow the pattern to cover the part you want")
    return {
        "url": start, "robots": robots, "start_page": page_info, "sitemap": sitemap,
        "suggest": {"engine": engine, "depth": depth, "depth_basis": depth_basis, "match_pattern": suggested_pattern,
                    "pages_estimate": pages, "fetch_seconds": round(pages * delay) if pages else None, "max_pages": max_pages},
        "warnings": warnings,
    }


def make_prober(*, max_pages: int = 200, delay: float = 0.5, fetcher=None) -> Callable[[object], dict]:
    """Return the ``prober(request)`` callable the API uses; ``fetcher`` is replaced in tests."""

    def prober(req) -> dict:
        check_public_url(req.url)
        return probe_site(req.url, req.match_pattern, fetcher, max_pages=max_pages, delay=delay)

    return prober
