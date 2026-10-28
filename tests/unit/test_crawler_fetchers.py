"""Crawler pieces the main crawler tests don't reach: HTML edge cases, DNS checks, both fetchers, crawl-loop errors."""
import socket
from types import SimpleNamespace

import httpx
import pytest
from test_api import KeywordEmbedder
from test_crawler import FakeFetcher

from hippocampus.ingest import crawler as c
from hippocampus.ingest.crawler import (
    BrowserFetcher,
    CrawlOptions,
    CrawlRefused,
    FetchResult,
    HttpxFetcher,
    check_public_url,
    crawl_pages,
    make_crawler,
    parse_html,
)
from hippocampus.service.jobs import Job

PUBLIC = "https://93.184.216.34/"


# --- HTML to Markdown edge cases

def test_parse_html_lists_tables_and_inline_markup():
    html = """<html><head><title>T</title></head><body><main>
    <h2>Head</h2><p>a<br>b</p><hr>
    <ol><li>first<ul><li>nested</li></ul></li><li>second</li></ol>
    <p><em>em</em> <i>it</i> <b>bo</b> <a href="/x"></a><a href="/y">link</a></p>
    <pre><code>raw  code</code></pre>
    <table><tr><th>H</th><td>D</td></tr></table>
    <svg><g><path/></g></svg><p>after</p><noscript>no</noscript>
    </main></body></html>"""
    page = parse_html(html, "https://docs.example/")
    md = page.markdown
    assert "## Head" in md and "a\nb" in md and "---" in md
    assert "1. first" in md and "  - nested" in md and "2. second" in md
    assert "*em*" in md and "**bo**" in md and "[link](https://docs.example/y)" in md
    assert "[](" not in md  # an empty link drops its bracket
    assert "raw  code" in md and "| H " in md and "D |" in md
    assert "after" in md and md.rstrip().endswith("after")  # noscript content is dropped


# --- check_public_url resolution paths

def test_check_public_url_resolves_names(monkeypatch):
    monkeypatch.setattr(c.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 443))])
    check_public_url("https://docs.example/")
    monkeypatch.setattr(c.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.0.0.5", 443))])
    with pytest.raises(CrawlRefused, match="non-public"):
        check_public_url("https://docs.example/")


def test_check_public_url_unresolvable(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(c.socket, "getaddrinfo", boom)
    with pytest.raises(CrawlRefused, match="cannot resolve"):
        check_public_url("https://nowhere.example/")


# --- HttpxFetcher

@pytest.fixture
def allow_public(monkeypatch):
    monkeypatch.setattr(c, "check_public_url", lambda url: None)


def _fetcher(handler):
    return HttpxFetcher(client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))


def test_httpx_fetcher_default_client_builds():
    assert HttpxFetcher(timeout=1.0)._client is not None


def test_httpx_fetcher_html_and_robots(allow_public):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="User-agent: *\nDisallow: /x")
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text="<p>hi</p>")

    f = _fetcher(handler)
    res = f.fetch(PUBLIC)
    assert (res.status, res.content_type, res.text) == (200, "text/html", "<p>hi</p>")
    assert "Disallow" in f.fetch_robots(PUBLIC + "robots.txt")


def test_httpx_fetcher_robots_missing_and_wrong_type(allow_public):
    f = _fetcher(lambda req: httpx.Response(404, headers={"content-type": "text/plain"}, text="no"))
    assert f.fetch_robots(PUBLIC + "robots.txt") is None
    f = _fetcher(lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    res = f.fetch(PUBLIC)
    assert res.text == "" and res.content_type == "application/pdf"


def test_httpx_fetcher_follows_redirects_and_caps_them(allow_public):
    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "/end"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="ok")

    res = _fetcher(handler).fetch(PUBLIC + "start")
    assert res.url == PUBLIC + "end" and res.text == "ok"
    loop = _fetcher(lambda req: httpx.Response(302, headers={"location": "/again"}))
    with pytest.raises(CrawlRefused, match="too many redirects"):
        loop.fetch(PUBLIC)


def test_httpx_fetcher_redirect_hops_are_checked(monkeypatch):
    def check(url):
        if "internal" in url:
            raise CrawlRefused("private")

    monkeypatch.setattr(c, "check_public_url", check)
    f = _fetcher(lambda req: httpx.Response(302, headers={"location": "https://internal.example/"}))
    with pytest.raises(CrawlRefused):
        f.fetch(PUBLIC)


def test_httpx_fetcher_truncates_large_bodies(allow_public, monkeypatch):
    monkeypatch.setattr(c, "MAX_BYTES", 10)
    f = _fetcher(lambda req: httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 100))
    assert f.fetch(PUBLIC).text == "x" * 10


# --- BrowserFetcher with a scripted fake browser

class FakePage:
    def __init__(self, resp, url=PUBLIC, idle_fails=False):
        self.resp, self.url, self.idle_fails, self.closed = resp, url, idle_fails, False

    def set_default_timeout(self, ms):
        self.timeout = ms

    def goto(self, url, wait_until):
        return self.resp

    def wait_for_load_state(self, state, timeout):
        if self.idle_fails:
            raise TimeoutError("never idle")

    def wait_for_timeout(self, ms):
        self.waited = ms

    def content(self):
        return "<main><h1>Rendered</h1></main>"

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page):
        self.page, self.routes, self.closed = page, [], False

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.ctx, self.closed = FakeContext(page), False

    def new_context(self, **kw):
        self.kw = kw
        return self.ctx

    def close(self):
        self.closed = True


def _browser(page, settle=1.0):
    br = FakeBrowser(page)
    pw = SimpleNamespace(stopped=False, stop=lambda: setattr(pw, "stopped", True))
    return BrowserFetcher(timeout=1, settle=settle, launch=lambda: (pw, br)), br, pw


def _resp(status=200, ctype="text/html; charset=utf-8"):
    return SimpleNamespace(status=status, headers={"content-type": ctype})


def test_browser_fetcher_renders_and_closes(allow_public):
    page = FakePage(_resp(), idle_fails=True)
    f, br, pw = _browser(page)
    res = f.fetch(PUBLIC)
    assert res.status == 200 and "Rendered" in res.text and page.closed and page.waited == 1000
    f.fetch(PUBLIC)  # the context is created once
    assert len(br.ctx.routes) == 1 and br.kw["service_workers"] == "block"
    f.close()
    assert br.closed and br.ctx.closed and pw.stopped
    f.close()  # closing twice is harmless


def test_browser_fetcher_non_html_and_no_response(allow_public):
    f, _, _ = _browser(FakePage(_resp(ctype="application/pdf")), settle=0)
    assert f.fetch(PUBLIC).text == ""
    f, _, _ = _browser(FakePage(None), settle=0)
    res = f.fetch(PUBLIC)
    assert res.status == 0 and res.text == ""


def test_browser_fetcher_refuses_private_final_url(monkeypatch):
    calls = []

    def check(url):
        calls.append(url)
        if len(calls) > 1:
            raise CrawlRefused("redirected somewhere private")

    monkeypatch.setattr(c, "check_public_url", check)
    f, _, _ = _browser(FakePage(_resp(), url="http://10.0.0.1/"))
    with pytest.raises(CrawlRefused):
        f.fetch(PUBLIC)


def test_browser_fetcher_close_survives_errors():
    class Bad:
        def close(self):
            raise RuntimeError("x")

        def stop(self):
            raise RuntimeError("x")

    f = BrowserFetcher(launch=lambda: (None, None))
    f._context, f._browser, f._pw = Bad(), Bad(), Bad()
    f.close()
    assert f._context is None and f._pw is None


def test_browser_fetcher_needs_playwright(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_playwright(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_playwright)
    with pytest.raises(RuntimeError, match="browser"):
        BrowserFetcher()._ensure()


@pytest.mark.parametrize(
    ("rtype", "url", "refused", "expected"),
    [("image", PUBLIC, False, "abort"), ("script", "http://10.0.0.1/", True, "abort"),
     ("script", PUBLIC, False, "continue")],
)
def test_browser_route(monkeypatch, rtype, url, refused, expected):
    seen = []

    def check(u):
        if refused:
            raise CrawlRefused("private")

    monkeypatch.setattr(c, "check_public_url", check)
    route = SimpleNamespace(
        request=SimpleNamespace(resource_type=rtype, url=url),
        abort=lambda: seen.append("abort"),
        continue_=lambda: seen.append("continue"),
    )
    BrowserFetcher._route(route)
    assert seen == [expected]


# --- crawl loop error handling

class ScriptedFetcher:
    def __init__(self, results):
        self.results = results

    def fetch(self, url):
        r = self.results[url]
        if isinstance(r, Exception):
            raise r
        return r


def test_crawl_survives_refusals_errors_and_empty_pages():
    start = "https://docs.example/"
    body = "<main><h1>Home</h1><a href='/r'>r</a><a href='/e'>e</a><a href='/n'>n</a><a href='/m'>m</a></main>"
    fetcher = ScriptedFetcher({
        start: FetchResult(start, 200, "text/html", body),
        start + "r": CrawlRefused("private"),
        start + "e": RuntimeError("boom"),
        start + "n": FetchResult(start + "n", 404, "text/html", ""),
        start + "m": FetchResult(start + "m", 200, "text/html", "<script>x</script>"),
    })
    log = []
    pages = crawl_pages(start, CrawlOptions(depth=2, delay=0), fetcher, log.append, sleep=lambda s: None)
    assert [p.url for p in pages] == [start]
    text = "\n".join(log)
    assert "refused" in text and "error" in text and "skip (404" in text and "skip (empty)" in text


def test_crawl_robots_fetch_failure_means_allowed():
    class NoRobots(FakeFetcher):
        def fetch_robots(self, url):
            raise OSError("down")

    f = NoRobots({"https://docs.example/": "<main><h1>Hi</h1></main>"})
    assert len(crawl_pages("https://docs.example/", CrawlOptions(delay=0), f, sleep=lambda s: None)) == 1


def test_crawl_without_robots_support_or_with_custom_getter():
    class Plain:
        def fetch(self, url):
            return FetchResult(url, 200, "text/html", "<main><h1>Hi</h1></main>")

    assert len(crawl_pages("https://docs.example/", CrawlOptions(delay=0), Plain())) == 1
    pages = crawl_pages("https://docs.example/", CrawlOptions(delay=0), Plain(),
                        robots_fetcher=lambda url: "User-agent: *\nDisallow: /")
    assert pages == []


def test_make_crawler_rejects_unknown_engine():
    class Req:
        url, collection, depth, match_pattern, engine = PUBLIC, "web", 1, None, "carrier-pigeon"

    crawler = make_crawler(lambda: None, KeywordEmbedder(), FakeFetcher({}))
    with pytest.raises(ValueError, match="not available"):
        crawler(Job(id="j", kind="crawl"), Req())
