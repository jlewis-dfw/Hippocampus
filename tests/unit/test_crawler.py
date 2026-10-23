import pytest
from test_api import KeywordEmbedder, MemStore

from hippocampus.crawler import (
    CrawlOptions,
    CrawlRefused,
    FetchResult,
    check_public_url,
    crawl_pages,
    make_crawler,
    normalize,
    parse_html,
)
from hippocampus.jobs import Job

HOME = """<html><head><title>Home  Docs</title><style>x{}</style></head><body>
<nav><a href="/nav-only">Nav</a></nav>
<main><h1>Guide</h1><p>Intro with <strong>bold</strong> and <code>code</code>.</p>
<ul><li>one</li><li>two</li></ul>
<pre>line1
  line2</pre>
<a href="/a">Alpha</a> <a href="/b.png">img</a> <a href="https://other.example/x">ext</a>
<a href="/a#frag">Alpha again</a> <a href="/skip/me">skip</a> <a href="/private-infra/x">infra</a>
</main><footer>foot</footer><script>alert(1)</script></body></html>"""


class FakeFetcher:
    def __init__(self, pages, robots=None):
        self.pages, self.robots, self.calls = pages, robots, []

    def fetch(self, url):
        self.calls.append(url)
        if url in self.pages:
            return FetchResult(url, 200, "text/html", self.pages[url])
        return FetchResult(url, 404, "text/html", "")

    def fetch_robots(self, url):
        return self.robots


SITE = {
    "https://docs.example/": HOME,
    "https://docs.example/a": "<main><h1>Alpha</h1><p>cat</p><a href='/c'>C</a></main>",
    "https://docs.example/skip/me": "<main><h1>Skip</h1><p>dog</p></main>",
    "https://docs.example/c": "<main><h1>C</h1><p>deep</p></main>",
}


def test_parse_html_markdown_and_links():
    page = parse_html(HOME, "https://docs.example/")
    assert page.title == "Home Docs"
    md = page.markdown
    assert "# Guide" in md and "**bold**" in md and "`code`" in md
    assert "- one" in md and "- two" in md
    assert "```\nline1\n  line2\n```" in md
    assert "Nav" not in md  # nav text dropped from the content (its links are still followed)
    assert "alert" not in md and "foot" not in md
    assert "[Alpha](https://docs.example/a)" in md


def test_depth_one_fetches_only_start():
    f = FakeFetcher(SITE)
    pages = crawl_pages("https://docs.example/", CrawlOptions(depth=1, delay=0), f, sleep=lambda s: None)
    assert [p.url for p in pages] == ["https://docs.example/"]


def test_depth_two_filters_domain_extensions_fragments_and_infra():
    f = FakeFetcher(SITE)
    pages = crawl_pages("https://docs.example/", CrawlOptions(depth=2, delay=0), f, sleep=lambda s: None)
    urls = [p.url for p in pages]
    assert urls == ["https://docs.example/", "https://docs.example/a", "https://docs.example/skip/me"]
    assert not any("other.example" in c or ".png" in c or "infrastructure" in c for c in f.calls)
    assert f.calls.count("https://docs.example/a") == 1  # fragment variant deduplicated


def test_match_pattern_and_max_pages():
    f = FakeFetcher(SITE)
    pages = crawl_pages("https://docs.example/", CrawlOptions(depth=3, match_pattern=r"/(a|c)$", delay=0), f,
                        sleep=lambda s: None)
    assert [p.url for p in pages] == ["https://docs.example/", "https://docs.example/a", "https://docs.example/c"]
    pages = crawl_pages("https://docs.example/", CrawlOptions(depth=3, max_pages=2, delay=0), FakeFetcher(SITE),
                        sleep=lambda s: None)
    assert len(pages) == 2


def test_robots_disallow_is_respected():
    f = FakeFetcher(SITE, robots="User-agent: *\nDisallow: /skip/\n")
    pages = crawl_pages("https://docs.example/", CrawlOptions(depth=2, delay=0), f, sleep=lambda s: None)
    assert "https://docs.example/skip/me" not in [p.url for p in pages]
    ignore = crawl_pages("https://docs.example/", CrawlOptions(depth=2, delay=0, respect_robots=False),
                         FakeFetcher(SITE, robots="User-agent: *\nDisallow: /\n"), sleep=lambda s: None)
    assert len(ignore) == 3


def test_delay_between_requests():
    slept, f = [], FakeFetcher(SITE)
    crawl_pages("https://docs.example/", CrawlOptions(depth=2, delay=0.5), f, sleep=slept.append)
    assert slept == [0.5] * (len(f.calls) - 1)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://10.0.0.11:8090/", "http://100.64.0.5/", "http://169.254.169.254/",
    "http://[::1]/", "http://localhost/", "ftp://example.com/", "file:///etc/passwd",
    "https://docs.example/private-infra/docs",
])
def test_check_public_url_refuses(url):
    with pytest.raises(CrawlRefused):
        check_public_url(url)


def test_check_public_url_allows_public_literal():
    check_public_url("https://93.184.216.34/")


def test_normalize():
    assert normalize("HTTPS://Docs.Example#x") == "https://docs.example/"


def test_make_crawler_indexes_pages_and_is_incremental():
    from contextlib import contextmanager

    store = MemStore()
    store.create_collection("web", "web", None)

    @contextmanager
    def factory():
        yield store

    crawler = make_crawler(factory, KeywordEmbedder(), FakeFetcher(SITE), delay=0, sleep=lambda s: None)

    class Req:
        url, collection, depth, match_pattern, engine = "https://93.184.216.34/", "web", 1, None, "static"

    class SiteFetcher(FakeFetcher):
        pass

    # the start URL is a public literal; serve it from the fake
    crawler = make_crawler(factory, KeywordEmbedder(),
                           FakeFetcher({"https://93.184.216.34/": HOME}), delay=0, sleep=lambda s: None)
    job = Job(id="j", kind="crawl")
    first = crawler(job, Req())
    assert first["pages"] == 1 and first["added"] == 1
    second = crawler(Job(id="k", kind="crawl"), Req())
    assert second["unchanged"] == 1 and second["added"] == 0
    assert any("fetched" in e for e in job.events)

    Req.engine = "crawl4ai"
    with pytest.raises(ValueError):
        crawler(job, Req())


def test_make_crawler_refuses_private_start():
    class Req:
        url, collection, depth, match_pattern, engine = "http://10.0.0.28:11434/", "web", 1, None, "static"

    crawler = make_crawler(lambda: None, KeywordEmbedder(), FakeFetcher({}))
    with pytest.raises(CrawlRefused):
        crawler(Job(id="j", kind="crawl"), Req())


def test_make_crawler_browser_engine_uses_factory_and_closes():
    from contextlib import contextmanager

    class Closing(FakeFetcher):
        closed = False

        def close(self):
            self.closed = True

    made = []

    def factory_browser():
        made.append(Closing({"https://93.184.216.34/": HOME}))
        return made[0]

    class Req:
        url, collection, depth, match_pattern, engine = "https://93.184.216.34/", "web", 1, None, "browser"

    store = MemStore()
    store.create_collection("web", "web", None)

    @contextmanager
    def factory():
        yield store

    crawler = make_crawler(factory, KeywordEmbedder(), FakeFetcher({}), delay=0, sleep=lambda s: None,
                           browser_factory=factory_browser)
    out = crawler(Job(id="j", kind="crawl"), Req())
    assert out["pages"] == 1 and made[0].closed


# --- real Chromium (skipped where Playwright/Chromium are not installed)

def _browser_available():
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch(args=["--no-sandbox"]).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _browser_available(), reason="playwright/chromium not installed")
def test_browser_fetcher_renders_js_and_blocks_private_subrequests(monkeypatch):
    import http.server
    import threading

    from hippocampus import crawler as c

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/secret":
                body, ctype = b"LEAKED", "text/plain"
            else:
                body, ctype = (
                    b"<html><head><title>JS</title></head><body><div id=o>loading</div><script>"
                    b"document.getElementById('o').textContent='rendered by js';"
                    b"fetch('http://127.0.0.2:%d/secret').then(r=>r.text()).then(t=>{document.title=t})"
                    b".catch(()=>{document.body.append('blocked')});</script></body></html>" % self.server.server_port,
                    "text/html",
                )
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = c.check_public_url

    def allow_only_loopback_page(url):
        if url.startswith("http://127.0.0.1:"):
            return
        real(url)  # 127.0.0.2 and everything else is non-public -> refused

    monkeypatch.setattr(c, "check_public_url", allow_only_loopback_page)
    f = c.BrowserFetcher(timeout=15, settle=0.5)
    try:
        res = f.fetch(f"http://127.0.0.1:{srv.server_port}/")
    finally:
        f.close()
        srv.shutdown()
    assert res.status == 200
    page = c.parse_html(res.text, res.url)
    assert "rendered by js" in page.markdown
    assert "LEAKED" not in res.text and page.title != "LEAKED"
