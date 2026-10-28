from contextlib import contextmanager

from fastapi.testclient import TestClient
from test_access import add_client
from test_api import KeywordEmbedder, MemStore, make_client
from test_crawler import FakeFetcher

from hippocampus.ingest.crawl_probe import make_prober, probe_site
from hippocampus.ingest.crawler import FetchResult
from hippocampus.service.api import create_app
from hippocampus.service.jobs import JobRegistry

ROBOTS = "User-agent: *\nDisallow: /private/\nCrawl-delay: 5\nContent-Signal: ai-train=yes\nSitemap: https://docs.example/sitemap.xml\n"
TEXT = "<p>" + "word " * 150 + "</p>"
HOME = f"""<html><head><title>Docs</title></head><body><main><h1>Docs</h1>{TEXT}
<a href="/docs/a">A</a> <a href="/docs/b/">B</a> <a href="/docs/b">B2</a> <a href="/blog/x">X</a> <a href="/logo.png">i</a>
<a href="https://other.example/">o</a></main></body></html>"""
SITEMAP = ("<urlset>" + "".join(f"<url><loc>https://docs.example/docs/{p}</loc></url>" for p in ("a", "b", "c/d")) +
           "<url><loc>https://docs.example/blog/x</loc></url></urlset>")


class ProbeFetcher(FakeFetcher):
    def __init__(self, pages, robots=None, sitemap=None):
        super().__init__(pages, robots)
        self.sitemap = sitemap

    def fetch_sitemap(self, url):
        return self.sitemap


def probe(home=HOME, robots=ROBOTS, sitemap=SITEMAP, url="https://docs.example/docs/", **kw):
    return probe_site(url, fetcher=ProbeFetcher({url: home}, robots, sitemap), has_browser=kw.pop("has_browser", True), **kw)


def test_probe_reports_robots_and_suggests_settings():
    out = probe()
    assert out["robots"]["allowed"] and out["robots"]["content_signal"] == "ai-train=yes"
    assert out["robots"]["crawl_delay"] == 5
    assert any("Crawl-delay" in w for w in out["warnings"])
    s = out["suggest"]
    assert s["engine"] == "static"
    assert s["match_pattern"] == "/docs/"  # derived from the start path, and it matches links that exist
    assert s["depth"] == 3  # /docs/c/d is two segments below /docs/
    assert out["sitemap"]["matching"] == 3 and s["pages_estimate"] == 3
    assert any("both with and without a trailing slash" in w for w in out["warnings"])
    assert out["start_page"]["same_host_links"] == 4  # image and other host dropped


def test_probe_disallowed_start_page_is_not_fetched():
    fetcher = ProbeFetcher({}, "User-agent: *\nDisallow: /docs/\n", None)
    out = probe_site("https://docs.example/docs/", fetcher=fetcher, has_browser=True)
    assert not out["robots"]["allowed"] and not out["start_page"]["fetched"]
    assert fetcher.calls == [] and any("disallows" in w for w in out["warnings"])


def test_probe_script_built_page_suggests_browser_only_when_installed():
    shell = '<html><body><div id="root"></div><noscript>enable js</noscript></body></html>'
    assert probe(home=shell, has_browser=True)["suggest"]["engine"] == "browser"
    out = probe(home=shell, has_browser=False)
    assert out["suggest"]["engine"] == "static" and any("not installed" in w for w in out["warnings"])


def test_probe_without_sitemap_defaults_depth_and_flags_site_root():
    out = probe(sitemap=None, url="https://docs.example/")
    assert out["suggest"]["depth"] == 2 and out["suggest"]["pages_estimate"] is None
    assert any("whole host" in w for w in out["warnings"])


def test_probe_sitemap_over_cap_and_index():
    big = "<urlset>" + "".join(f"<loc>https://docs.example/docs/p{i}</loc>" for i in range(30)) + "</urlset>"
    out = probe(sitemap=big, max_pages=10)
    assert out["suggest"]["pages_estimate"] == 10 and any("cap is 10" in w for w in out["warnings"])
    idx = probe(sitemap="<sitemapindex><sitemap><loc>https://docs.example/s1.xml</loc></sitemap></sitemapindex>")
    assert idx["sitemap"]["index"] is True


def test_probe_reports_meta_robots_and_redirect_host():
    noindex = HOME.replace("<head>", '<head><meta name="robots" content="noindex, nofollow">')
    assert any("noindex" in w for w in probe(home=noindex)["warnings"])

    class Redirect(ProbeFetcher):
        def fetch(self, url):
            return FetchResult("https://new.example/docs/", 200, "text/html", HOME, "")

    out = probe_site("https://docs.example/docs/", fetcher=Redirect({}, None, None), has_browser=False)
    assert any("another host" in w for w in out["warnings"])


def probe_client(require_auth):
    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    add_client(mem, "writer", "hip_writer", write=["docs"])
    prober = make_prober(fetcher=ProbeFetcher({"https://docs.example/docs/": HOME}, ROBOTS, SITEMAP))
    return TestClient(create_app(factory, KeywordEmbedder(), JobRegistry(), None, prober=prober, require_auth=require_auth))


BODY = {"url": "https://docs.example/docs/"}
WRITER = {"X-Hippo-Token": "hip_writer"}


def test_probe_endpoint_501_without_prober():
    c, _ = make_client()
    assert c.post("/v1/crawl/probe", json=BODY).status_code == 501


def test_probe_endpoint_needs_a_token_when_auth_is_required(monkeypatch):
    monkeypatch.setattr("hippocampus.ingest.crawl_probe.check_public_url", lambda url: None)  # no DNS in unit tests
    client = probe_client(require_auth=True)
    assert client.post("/v1/crawl/probe", json=BODY).status_code == 403  # anonymous
    ok = client.post("/v1/crawl/probe", json=BODY, headers=WRITER)
    assert ok.status_code == 200 and ok.json()["suggest"]["match_pattern"] == "/docs/"
    assert probe_client(require_auth=False).post("/v1/crawl/probe", json=BODY).status_code == 200


def test_probe_endpoint_rejects_a_bad_pattern_and_a_private_address():
    client = probe_client(require_auth=True)
    assert client.post("/v1/crawl/probe", json={**BODY, "match_pattern": "("}, headers=WRITER).status_code == 422
    refused = client.post("/v1/crawl/probe", json={"url": "http://127.0.0.1/"}, headers=WRITER)
    assert refused.status_code == 422 and "non-public" in refused.json()["detail"]


def test_probe_reads_the_listed_sitemap_that_covers_the_start_path():
    robots = "User-agent: *\nSitemap: https://docs.example/sitemap.xml\nSitemap: https://docs.example/v2/sitemap.xml\n"
    maps = {"https://docs.example/sitemap.xml": "<urlset><loc>https://docs.example/blog/a</loc></urlset>",
            "https://docs.example/v2/sitemap.xml": "<urlset><loc>https://docs.example/v2/docs/a</loc>"
                                                   "<loc>https://docs.example/v2/docs/b</loc></urlset>"}

    class Maps(ProbeFetcher):
        def fetch_sitemap(self, url):
            return maps.get(url)

    url = "https://docs.example/v2/docs/"
    out = probe_site(url, fetcher=Maps({url: HOME.replace("/docs/", "/v2/docs/")}, robots), has_browser=True)
    assert out["sitemap"]["url"].endswith("/v2/sitemap.xml") and out["sitemap"]["matching"] == 2


def test_probe_warns_when_the_sitemap_has_nothing_under_the_pattern():
    other = "<urlset><loc>https://docs.example/blog/a</loc></urlset>"
    out = probe(sitemap=other)
    assert any("no URL that matches" in w for w in out["warnings"])
