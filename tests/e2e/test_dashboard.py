"""The dashboard in a real Chromium, against the real stack (page served by the API at /ui/).

Needs the ``browser`` extra (playwright) and a Chromium build; without them every test here is skipped.
Each test gets a fresh browser context, so no token or storage leaks between tests.
"""
import uuid

import httpx
import pytest
from e2e_support import Api

pytest.importorskip("playwright")
from playwright.sync_api import expect, sync_playwright

PG_DOC = ("# Database backups\n\nRun pg_dump every night and copy the dump to another machine. "
          "Restore a PostgreSQL database with pg_restore after stopping the application.")
CAT_DOC = "# Cats\n\nCats are small domesticated felines. They purr, sleep for most of the day and chase mice."


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as exc:  # no Chromium build installed
            pytest.skip(f"Chromium is not available to Playwright: {str(exc).splitlines()[0]}")
        yield b
        b.close()


class Dashboard:
    """A page plus the errors the browser reported while it was open."""

    def __init__(self, browser, stack, token: str | None = None):
        self.ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        if token:  # what pasting it in the Access panel would store
            self.ctx.add_init_script(f"sessionStorage.setItem('hippo-token', {token!r})")
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(15_000)
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(f"console: {m.text}"))
        self.page.goto(f"{stack.url}/ui/")
        expect(self.page.locator("#pills")).to_contain_text("DB ok")

    def row(self, table: str, text: str):
        return self.page.locator(f"#{table} tr", has_text=text)

    def accept_dialogs(self, *answers: str):
        """Accept every confirm(); answer prompt()s in order."""
        replies = list(answers)
        self.page.on("dialog", lambda d: d.accept(replies.pop(0)) if d.type == "prompt" and replies else d.accept())

    def close(self):
        self.ctx.close()


@pytest.fixture
def dash(browser, stack):
    made = []

    def open_dashboard(token: str | None = None) -> Dashboard:
        d = Dashboard(browser, stack, token)
        made.append(d)
        return d

    yield open_dashboard
    for d in made:
        d.close()


@pytest.fixture(scope="module")
def library(admin):
    """A shared collection with two documents."""
    slug = _slug("ui-docs")
    assert admin.post("/v1/collections", json={"slug": slug, "name": "UI docs"}).status_code == 201
    for uri, title, text in (("pg.md", "Backups", PG_DOC), ("cats.md", "Cats", CAT_DOC)):
        r = admin.post("/v1/ingest/file", json={"collection": slug, "uri": uri, "title": title, "text": text})
        assert r.status_code == 200
    return slug


# --- the page loads and shows the live system

def test_anonymous_dashboard_loads_clean(dash, library):
    d = dash()
    expect(d.page).to_have_title("Hippocampus")
    pills = d.page.locator("#pills")
    expect(pills).to_contain_text("BAAI/bge-small-en-v1.5 (384d)")
    expect(pills).to_contain_text("collections")
    expect(d.page.locator("#who")).to_have_text("anonymous")
    expect(d.row("collections", library)).to_be_visible()
    expect(d.page.locator("#clientsBox")).to_be_hidden()
    assert d.errors == []  # no script errors and no blocked resources under the strict policy


def test_root_redirects_to_the_dashboard(browser, stack):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(stack.url + "/")
    expect(page).to_have_url(f"{stack.url}/ui/")
    ctx.close()


def test_collection_row_shows_counts_and_lists_sources(dash, library):
    d = dash()
    row = d.row("collections", library)
    expect(row.locator(".badge.shared")).to_have_text("shared")
    expect(row.locator("td").nth(3)).to_have_text("Other")  # pg.md and cats.md are not web, local or repo
    expect(row.locator("td").nth(4)).to_have_text("2")  # sources
    row.get_by_role("button", name="Sources").click()
    expect(d.page.locator("#sourcesTitle")).to_have_text(library)
    expect(d.row("sources", "pg.md")).to_contain_text("Backups")
    expect(d.row("sources", "cats.md")).to_be_visible()
    d.page.locator("#closeSources").click()
    expect(d.page.locator("#sourcesSection")).to_be_hidden()


# --- search sandbox

def test_search_sandbox_finds_by_meaning(dash, library):
    d = dash()
    d.page.get_by_role("tab", name="Search").click()
    expect(d.page.locator("#panel-search")).to_be_visible()
    d.page.select_option("#searchScope", library)
    d.page.fill("#query", "how do we keep copies of our data safe?")
    d.page.get_by_role("button", name="Run search").click()
    first = d.page.locator("#results .hit").first
    expect(first).to_contain_text("pg.md")
    expect(first.locator(".score")).to_contain_text("score 0.")
    expect(d.page.locator("#searchStats")).to_contain_text("result")
    d.page.fill("#query", "a purring animal")
    d.page.fill("#topK", "1")
    d.page.get_by_role("button", name="Run search").click()
    expect(d.page.locator("#results .hit")).to_have_count(1)
    expect(d.page.locator("#results .hit").first).to_contain_text("cats.md")


def test_collection_search_button_opens_the_search_tab_scoped_to_it(dash, library):
    d = dash()
    d.row("collections", library).get_by_role("button", name="Search").click()
    expect(d.page.locator("#panel-search")).to_be_visible()
    expect(d.page.get_by_role("tab", name="Search")).to_have_attribute("aria-selected", "true")
    expect(d.page.locator("#searchScope")).to_have_value(library)
    expect(d.page.locator("#query")).to_be_focused()
    d.page.get_by_role("tab", name="Collections").click()
    expect(d.page.locator("#panel-search")).to_be_hidden()


def test_hostile_content_is_shown_as_text_not_run(dash, admin):
    slug = _slug("ui-xss")
    admin.post("/v1/collections", json={"slug": slug, "name": "Hostile"})
    admin.post("/v1/ingest/file", json={
        "collection": slug, "uri": "x.md", "title": '<img src=x onerror="window.__pwned=1">',
        "text": "# Garden\n\n<script>window.__pwned=1</script> notes about growing tomatoes in a garden"})
    d = dash()
    d.row("collections", slug).get_by_role("button", name="Sources").click()
    expect(d.row("sources", "x.md")).to_contain_text('<img src=x onerror="window.__pwned=1">')
    d.page.get_by_role("tab", name="Search").click()
    d.page.select_option("#searchScope", slug)
    d.page.fill("#query", "growing tomatoes")
    d.page.get_by_role("button", name="Run search").click()
    expect(d.page.locator("#results pre").first).to_contain_text("<script>window.__pwned=1</script>")
    assert d.page.evaluate("window.__pwned") is None
    assert d.page.locator("#results img, #sources img").count() == 0


# --- tabs

def test_tabs_switch_panels_keep_the_hash_and_move_with_the_arrow_keys(dash, library):
    d = dash()
    page = d.page
    panels = {"Collections": "#panel-collections", "Search": "#panel-search", "Local": "#panel-local", "Web": "#panel-web", "Access": "#panel-access",
              "API": "#panel-api"}

    def only(shown):
        for name, sel in panels.items():
            (expect(page.locator(sel)).to_be_visible if name == shown else expect(page.locator(sel)).to_be_hidden)()
            expect(page.get_by_role("tab", name=name)).to_have_attribute("aria-selected", "true" if name == shown else "false")

    only("Collections")  # the first tab is the default, and it holds the collections table
    expect(d.row("collections", library)).to_be_visible()
    page.get_by_role("tab", name="Search").click()
    only("Search")
    expect(page.locator("#searchForm")).to_be_visible()
    page.get_by_role("tab", name="Local").click()
    only("Local")
    expect(page.locator("#dropZone")).to_be_visible()
    assert page.evaluate("location.hash") == "#local"
    page.get_by_role("tab", name="Web").click()
    only("Web")
    expect(page.locator("#crawlForm")).to_be_visible()
    page.reload()  # the hash brings the same tab back
    only("Web")
    page.get_by_role("tab", name="Web").focus()
    page.keyboard.press("ArrowRight")
    only("Access")
    page.keyboard.press("ArrowRight")
    only("API")  # the last tab
    page.keyboard.press("ArrowRight")  # wraps around
    only("Collections")
    page.keyboard.press("End")
    only("API")
    page.keyboard.press("Home")
    only("Collections")
    page.locator("#openAccess").click()  # the name in the header is a shortcut to the Access tab
    only("Access")
    assert d.errors == []


# --- access panel

def test_token_panel_signs_in_and_out(dash, admin_token, library):
    d = dash()
    d.page.locator("#openAccess").click()
    expect(d.page.locator("#accessSection")).to_be_visible()
    d.page.fill("#token", admin_token)
    d.page.get_by_role("button", name="Use token").click()
    expect(d.page.locator("#who")).to_have_text("e2e-root (admin)")
    expect(d.page.locator("#whoDetail")).to_contain_text("admin, sees everything")
    expect(d.page.locator("#clientsBox")).to_be_visible()
    expect(d.row("clients", "e2e-root")).to_be_visible()
    assert d.page.input_value("#token") == ""  # the field is cleared once used
    assert d.page.evaluate("sessionStorage.getItem('hippo-token')") == admin_token
    d.page.reload()  # a reload in the same tab stays signed in
    expect(d.page.locator("#who")).to_have_text("e2e-root (admin)")
    d.page.locator("#openAccess").click()
    d.page.get_by_role("button", name="Forget token").click()
    expect(d.page.locator("#who")).to_have_text("anonymous")
    expect(d.page.locator("#clientsBox")).to_be_hidden()
    assert d.page.evaluate("sessionStorage.getItem('hippo-token')") is None


def test_bad_token_shows_an_error_banner(dash):
    d = dash()
    d.page.locator("#openAccess").click()
    d.page.fill("#token", "hip_not_a_real_token")
    d.page.get_by_role("button", name="Use token").click()
    expect(d.page.locator("#banner")).to_contain_text("401")


def test_admin_manages_a_client_end_to_end(dash, stack, admin_token, library):
    d = dash(admin_token)
    d.accept_dialogs()
    d.page.locator("#openAccess").click()
    name = _slug("ui-bot")
    d.page.fill("#clientName", name)
    d.page.fill("#clientRead", library)
    d.page.fill("#clientNote", "made by the browser test")
    d.page.get_by_role("button", name="Create client").click()

    box = d.page.locator("#tokenOnce")
    expect(box).to_be_visible()
    expect(d.page.locator("#tokenOnceName")).to_have_text(name)
    token = d.page.locator("#tokenOnceValue").inner_text().strip()
    assert token.startswith("hip_")
    assert Api(stack.url, token).get("/v1/whoami").json()["read"] == [library]
    row = d.row("clients", name)
    expect(row).to_contain_text(library)
    expect(row).to_contain_text("never")
    d.page.get_by_role("button", name="I have stored it").click()
    expect(box).to_be_hidden()
    assert d.page.locator("#tokenOnceValue").inner_text() == ""

    # rotate: a new token is shown once and the old one stops working
    row.get_by_role("button", name="Rotate token").click()
    expect(box).to_be_visible()
    rotated = d.page.locator("#tokenOnceValue").inner_text().strip()
    assert rotated != token and rotated.startswith("hip_")
    assert httpx.get(f"{stack.url}/v1/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert Api(stack.url, rotated).get("/v1/whoami").status_code == 200

    # revoke: the row is marked and the token dies
    row.get_by_role("button", name="Revoke").click()
    expect(row.locator(".badge")).to_have_text("revoked")
    expect(row.get_by_role("button", name="Rotate token")).to_have_count(0)
    assert Api(stack.url, rotated).get("/v1/whoami").status_code == 401


def test_admin_edits_grants_through_prompts(dash, stack, admin, admin_token, library):
    name = _slug("ui-edit")
    created = admin.post("/v1/clients", json={"name": name, "read": [library]}).json()
    d = dash(admin_token)
    d.accept_dialogs(f"{library}, extra-*", "extra-*")
    d.page.locator("#openAccess").click()
    d.row("clients", name).get_by_role("button", name="Edit grants").click()
    expect(d.row("clients", name)).to_contain_text("extra-*")
    who = Api(stack.url, created["token"]).get("/v1/whoami").json()
    assert who["read"] == [library, "extra-*"] and who["write"] == ["extra-*"]


def test_form_rejects_a_bare_wildcard_with_a_banner(dash, admin_token):
    d = dash(admin_token)
    d.page.locator("#openAccess").click()
    d.page.fill("#clientName", _slug("ui-wild"))
    d.page.fill("#clientRead", "*")
    d.page.get_by_role("button", name="Create client").click()
    expect(d.page.locator("#banner")).to_contain_text("422")
    expect(d.page.locator("#tokenOnce")).to_be_hidden()


# --- collections: crawl form, visibility, delete

def test_anonymous_crawl_is_refused_in_a_banner(dash):
    d = dash()
    d.page.get_by_role("tab", name="Web").click()
    d.page.select_option("#collection", "__new__")
    expect(d.page.locator("#newCollection")).to_be_visible()
    d.page.fill("#newSlug", _slug("ui-anon"))
    d.page.fill("#url", "https://93.184.216.34/")
    d.page.locator("#crawlBtn").click()
    expect(d.page.locator("#banner")).to_contain_text("403")
    expect(d.page.locator("#jobSection")).to_be_hidden()


def test_crawl_form_creates_a_collection_and_reports_the_failed_job(dash, admin_token):
    d = dash(admin_token)
    d.page.get_by_role("tab", name="Web").click()
    slug = _slug("ui-crawl")
    d.page.select_option("#collection", "__new__")
    d.page.fill("#newSlug", slug)
    d.page.fill("#newName", "Crawl test")
    d.page.fill("#url", "http://127.0.0.1:9/")  # a private address: the guard must refuse it
    d.page.locator("#crawlBtn").click()
    expect(d.page.locator("#jobSection")).to_be_visible()
    expect(d.page.locator("#jobStatus")).to_have_text("error")
    expect(d.page.locator("#log")).to_contain_text("non-public")
    expect(d.page.locator("#jobMeta")).to_contain_text(f"crawl http://127.0.0.1:9/ -> {slug}")
    d.page.get_by_role("tab", name="Collections").click()
    expect(d.row("collections", slug)).to_contain_text("Crawl test")  # created before the crawl started
    d.page.get_by_role("tab", name="Web").click()
    expect(d.page.locator("#collection")).to_have_value(slug)  # the form will not try to create it again


def test_admin_restricts_shares_and_deletes_a_collection(dash, stack, admin, admin_token):
    slug = _slug("ui-vis")
    admin.post("/v1/collections", json={"slug": slug, "name": "Visibility"})
    admin.post("/v1/ingest/file", json={"collection": slug, "uri": "a.md", "text": "# A\n\nzebra facts"})
    anon = Api(stack.url)
    d = dash(admin_token)
    d.accept_dialogs()
    row = d.row("collections", slug)

    row.get_by_role("button", name="Restrict").click()
    expect(row.locator(".badge.shared, .badge.restricted")).to_have_text("restricted")
    assert slug not in [c["slug"] for c in anon.get("/v1/collections").json()["collections"]]

    row.get_by_role("button", name="Share").click()
    expect(row.locator(".badge.shared, .badge.restricted")).to_have_text("shared")
    assert slug in [c["slug"] for c in anon.get("/v1/collections").json()["collections"]]

    row.get_by_role("button", name="Delete").click()
    expect(row).to_have_count(0)
    assert slug not in [c["slug"] for c in admin.get("/v1/collections").json()["collections"]]


def test_declining_a_confirmation_changes_nothing(dash, admin, admin_token):
    slug = _slug("ui-keep")
    admin.post("/v1/collections", json={"slug": slug, "name": "Keep"})
    d = dash(admin_token)
    d.page.on("dialog", lambda dialog: dialog.dismiss())
    d.row("collections", slug).get_by_role("button", name="Delete").click()
    d.page.locator("#refresh").click()
    expect(d.row("collections", slug)).to_have_count(1)


def test_admin_only_controls_are_hidden_from_other_clients(dash, admin, library):
    created = admin.post("/v1/clients", json={"name": _slug("ui-reader"), "read": [library]}).json()
    d = dash(created["token"])
    expect(d.page.locator("#who")).to_have_text(created["name"])
    expect(d.page.locator("#clientsBox")).to_be_hidden()
    expect(d.row("collections", library).get_by_role("button", name="Restrict")).to_have_count(0)


# --- the collection view: every source, filter, remove

@pytest.fixture
def mixed(admin):
    """A collection holding web pages, a local file and a pushed note."""
    slug = _slug("ui-mixed")
    assert admin.post("/v1/collections", json={"slug": slug, "name": "Mixed"}).status_code == 201
    for uri, title in (("https://docs.example/a", "Web A"), ("https://docs.example/b", "Web B"),
                       ("files://notes/todo.md", "Local todo"), ("loose-note", "Loose")):
        r = admin.post("/v1/ingest/file", json={"collection": slug, "uri": uri, "title": title, "text": f"# {title}\n\nbody of {title}"})
        assert r.status_code == 200
    return slug


def test_collections_show_and_filter_by_source_type(dash, admin_token, library, mixed):
    d = dash(admin_token)
    row = d.row("collections", mixed)
    for label in ("Web", "Local", "Other"):
        expect(row.locator("td").nth(3).get_by_text(label, exact=True)).to_be_visible()
    d.page.select_option("#sourceFilter", "web")
    expect(d.row("collections", mixed)).to_be_visible()
    expect(d.row("collections", library)).to_have_count(0)  # holds no web pages
    d.page.select_option("#sourceFilter", "")
    expect(d.row("collections", library)).to_be_visible()


def test_clicking_a_collection_lists_every_source_and_filters_them(dash, admin_token, mixed):
    d = dash(admin_token)
    d.row("collections", mixed).locator("td").first.click()  # the row itself, not a button
    expect(d.page.locator("#sourcesTitle")).to_have_text(mixed)
    expect(d.page.locator("#sources tr")).to_have_count(4)
    d.page.select_option("#srcType", "web")
    expect(d.page.locator("#sources tr")).to_have_count(2)
    d.page.select_option("#srcType", "")
    d.page.fill("#srcFilter", "todo")
    expect(d.page.locator("#sources tr")).to_have_count(1)
    expect(d.row("sources", "files://notes/todo.md")).to_be_visible()
    expect(d.page.locator("#removeNote")).to_contain_text("Local tab")


def test_remove_one_and_remove_selected_sources(dash, admin_token, admin, mixed):
    d = dash(admin_token)
    d.accept_dialogs()
    d.row("collections", mixed).get_by_role("button", name="Sources").click()
    d.row("sources", "loose-note").get_by_role("button", name="Remove").click()
    expect(d.page.locator("#sources tr")).to_have_count(3)
    d.page.select_option("#srcType", "web")
    d.page.check("#srcAll")
    expect(d.page.locator("#removeSelected")).to_have_text("Remove selected (2)")
    d.page.locator("#removeSelected").click()
    d.page.select_option("#srcType", "")
    expect(d.page.locator("#sources tr")).to_have_count(1)
    expect(d.row("sources", "files://notes/todo.md")).to_be_visible()
    uris = [s["uri"] for s in admin.get(f"/v1/collections/{mixed}/sources").json()["sources"]]
    assert uris == ["files://notes/todo.md"]
    expect(d.row("collections", mixed).locator("td").nth(4)).to_have_text("1")  # the totals follow
    assert d.errors == []


def test_a_web_collection_without_saved_settings_offers_the_add_crawl_form(dash, admin_token, mixed):
    d = dash(admin_token)
    d.row("collections", mixed).get_by_role("button", name="Sources").click()
    expect(d.page.locator("#addCrawl")).to_have_attribute("open", "")  # web pages but no saved crawl: shown open
    expect(d.page.locator("#acUrl")).to_have_value("https://docs.example/a")  # a guess from its own pages
    expect(d.page.locator("#acPattern")).to_have_value("")  # the pages sit at the site root: no pattern to suggest


PROBE = {
    "url": "https://docs.example/docs/",
    "robots": {"found": True, "allowed": True, "crawl_delay": 5, "content_signal": None, "sitemaps": []},
    "start_page": {"fetched": True, "status": 200, "words": 420, "same_host_links": 31},
    "sitemap": {"found": True, "index": False, "urls": 80, "matching": 60},
    "suggest": {"engine": "static", "depth": 3, "depth_basis": "sitemap URL depth", "match_pattern": "/docs/",
                "pages_estimate": 60, "fetch_seconds": 30, "max_pages": 200},
    "warnings": ["robots.txt asks for a 5 s Crawl-delay"],
    "already_indexed": [],
}


def _mock_probe(d, **changes):
    """Answer the probe from the page's own fetch: the test stack cannot reach a real site."""
    import json

    d.page.route("**/v1/crawl/probe", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({**PROBE, **changes})))


def test_check_site_on_the_web_tab_reports_and_fills_the_form(dash, library):
    d = dash()
    _mock_probe(d, already_indexed=[{"collection": library, "sources": 2}])
    d.page.get_by_role("tab", name="Web").click()
    d.page.fill("#url", "https://docs.example/docs/")
    d.page.locator("#checkSite").click()
    box = d.page.locator("#siteCheck")
    expect(box).to_contain_text("allows this path")
    expect(box).to_contain_text("about 60 pages")
    expect(box).to_contain_text("5 s Crawl-delay")
    expect(box).to_contain_text(f"Already indexed in: {library}")
    box.get_by_role("button", name="Use suggested settings").click()
    expect(d.page.locator("#depth")).to_have_value("3")
    expect(d.page.locator("#pattern")).to_have_value("/docs/")


def test_check_site_in_the_collection_view_fills_its_own_form(dash, admin_token, mixed):
    d = dash(admin_token)
    _mock_probe(d)
    d.row("collections", mixed).get_by_role("button", name="Sources").click()
    d.page.fill("#acUrl", "https://docs.example/docs/")
    d.page.locator("#acCheckSite").click()
    box = d.page.locator("#acSiteCheck")
    expect(box).to_contain_text("about 60 pages")
    expect(d.page.locator("#siteCheck")).to_be_hidden()  # the Web tab's result is separate
    box.get_by_role("button", name="Use suggested settings").click()
    expect(d.page.locator("#acDepth")).to_have_value("3")
    expect(d.page.locator("#acPattern")).to_have_value("/docs/")
    assert d.errors == []


# --- saved crawls in the collection view (seeded in the database: the stack will not crawl a private or fake site)

@pytest.fixture
def crawled(admin, stack):
    """What a depth-3 crawl of https://docs.example/ leaves: three pages with depths, and the saved crawl."""
    slug = _slug("ui-crawled")
    assert admin.post("/v1/collections", json={"slug": slug, "name": "Crawled"}).status_code == 201
    pages = {"https://docs.example/": 1, "https://docs.example/a": 2, "https://docs.example/a/b": 3}
    for uri in pages:
        r = admin.post("/v1/ingest/file", json={"collection": slug, "uri": uri, "title": uri, "text": f"# {uri}\n\ntext of {uri}"})
        assert r.status_code == 200
    web_id = stack.psql(
        "INSERT INTO hippocampus.web_sources (collection_id, url, depth, engine, created_by, last_run_at, last_status, last_summary) "
        f"SELECT id, 'https://docs.example/', 3, 'static', 'e2e', NOW(), 'ok', '{{\"pages\": 3}}' FROM hippocampus.collections "
        f"WHERE slug = '{slug}' RETURNING id").splitlines()[0]
    for uri, depth in pages.items():
        stack.psql(f"UPDATE hippocampus.sources SET web_source_id = '{web_id}', crawl_depth = {depth} "
                   f"WHERE uri = '{uri}' AND collection_id = (SELECT id FROM hippocampus.collections WHERE slug = '{slug}')")
    return slug, web_id


def _mine(admin, slug):
    """The saved crawls of one collection (the stack is shared with the other tests)."""
    return [w for w in admin.get("/v1/web-sources").json()["web_sources"] if w["collection"] == slug]


def test_saved_crawl_shows_depths_and_its_settings(dash, admin_token, crawled):
    slug, _ = crawled
    d = dash(admin_token)
    d.row("collections", slug).get_by_role("button", name="Sources").click()
    card = d.page.locator("#webList .webcard")
    expect(card).to_contain_text("https://docs.example/")
    expect(card).to_contain_text("3 pages with a recorded depth, deepest at level 3")
    expect(card).to_contain_text("last crawl")
    expect(card.locator("select").first).to_have_value("3")
    for uri, depth in (("https://docs.example/a/b", "3"), ("https://docs.example/a", "2")):
        expect(d.page.locator(f'#sources tr:has(td.uri:text-is("{uri}"))').locator("td").nth(4)).to_have_text(depth)
    expect(d.page.locator("#addCrawl")).not_to_have_attribute("open", "")  # a saved crawl exists: the form stays closed


def test_lowering_the_depth_keeps_pages_until_remove_deeper_pages(dash, admin_token, admin, crawled):
    slug, web_id = crawled
    d = dash(admin_token)
    d.accept_dialogs()
    d.row("collections", slug).get_by_role("button", name="Sources").click()
    card = d.page.locator("#webList .webcard")
    card.locator("select").first.select_option("2")
    with d.page.expect_response(lambda r: r.request.method == "PATCH" and "/v1/web-sources/" in r.url):
        card.get_by_role("button", name="Save settings").click()
    expect(d.page.locator("#webList .webcard select").first).to_have_value("2")
    assert _mine(admin, slug)[0]["depth"] == 2
    expect(d.page.locator("#sources tr")).to_have_count(3)  # saving a lower depth removes nothing
    d.page.locator("#webList .webcard").get_by_role("button", name="Remove deeper pages").click()
    expect(d.page.locator("#sources tr")).to_have_count(2)
    assert sorted(s["uri"] for s in admin.get(f"/v1/collections/{slug}/sources").json()["sources"]) == [
        "https://docs.example/", "https://docs.example/a"]
    d.page.locator("#webList .webcard").get_by_role("button", name="Forget settings").click()
    expect(d.page.locator("#webList .webcard")).to_have_count(0)
    expect(d.page.locator("#sources tr")).to_have_count(2)  # the pages stay
    assert _mine(admin, slug) == []


def test_save_and_recrawl_starts_a_job_and_records_the_failure(dash, admin_token, admin, crawled):
    slug, _ = crawled
    d = dash(admin_token)
    d.row("collections", slug).get_by_role("button", name="Sources").click()
    d.page.locator("#webList .webcard").get_by_role("button", name="Save and re-crawl").click()
    expect(d.page.locator("#jobStatus")).to_have_text("error")  # docs.example does not resolve to a public address here
    expect(d.page.locator("#jobMeta")).to_contain_text(f"re-crawl https://docs.example/ -> {slug}")
    expect(d.page.locator("#webList .webcard")).to_contain_text("error")  # the view refreshed with the outcome
    assert _mine(admin, slug)[0]["last_status"] == "error"


def test_add_a_crawl_submit_saves_it_and_the_view_refreshes(dash, admin_token, mixed):
    d = dash(admin_token)
    d.accept_dialogs()
    d.row("collections", mixed).get_by_role("button", name="Sources").click()
    expect(d.page.locator("#webList .webcard")).to_have_count(0)
    d.page.fill("#acUrl", "http://127.0.0.1:9/")  # a private address: the guard must refuse it
    d.page.locator("#addCrawlForm").get_by_role("button", name="Crawl and save settings").click()
    expect(d.page.locator("#jobStatus")).to_have_text("error")
    expect(d.page.locator("#log")).to_contain_text("non-public")
    card = d.page.locator("#webList .webcard")
    expect(card).to_contain_text("http://127.0.0.1:9/")  # saved when the crawl was started
    expect(card).to_contain_text("error")


# --- HIPPO_CRAWL_REQUIRE_ACK, and the layout

def _require_ack(d):
    """Make the page see the server setting turned on (the e2e stack runs with it off)."""
    import json

    def handler(route):
        body = route.fetch().json()
        route.fulfill(status=200, content_type="application/json", body=json.dumps({**body, "crawl_require_ack": True}))

    d.page.route("**/v1/status", handler)
    d.page.reload()
    expect(d.page.locator("#pills")).to_contain_text("DB ok")


def test_the_tick_is_only_asked_for_when_configured(dash, admin_token, mixed):
    d = dash(admin_token)
    d.page.get_by_role("tab", name="Web").click()
    expect(d.page.locator("#crawlNotice")).to_be_visible()
    expect(d.page.locator("#crawlAckRow")).to_be_hidden()  # off by default: the notice never blocks
    _require_ack(d)
    d.page.get_by_role("tab", name="Web").click()
    expect(d.page.locator("#crawlAckRow")).to_be_visible()
    d.page.select_option("#collection", mixed)
    d.page.fill("#url", "http://127.0.0.1:9/")
    d.page.locator("#crawlBtn").click()  # unticked: the browser refuses to submit
    expect(d.page.locator("#jobSection")).to_be_hidden()
    d.page.check("#crawlAck")
    d.page.locator("#crawlBtn").click()
    expect(d.page.locator("#jobStatus")).to_have_text("error")


def test_collection_view_asks_before_a_crawl_when_the_tick_is_required(dash, admin_token, mixed):
    d = dash(admin_token)
    _require_ack(d)
    asked = []
    d.page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    d.row("collections", mixed).get_by_role("button", name="Sources").click()
    d.page.fill("#acUrl", "http://127.0.0.1:9/")
    d.page.locator("#addCrawlForm").get_by_role("button", name="Crawl and save settings").click()
    assert asked and "right to copy" in asked[0]
    expect(d.page.locator("#jobSection")).to_be_hidden()  # declined: nothing started


def test_filter_menus_are_not_stretched_across_the_page(dash, admin_token, mixed):
    d = dash(admin_token)
    assert d.page.locator("#sourceFilter").bounding_box()["width"] < 300
    d.row("collections", mixed).get_by_role("button", name="Sources").click()
    expect(d.page.locator("#srcType")).to_be_visible()
    for selector in ("#srcType", "#srcFilter"):
        assert d.page.locator(selector).bounding_box()["width"] < 400, selector


def test_a_failed_site_check_is_shown_next_to_the_button(dash, library):
    d = dash()  # anonymous: the probe needs a token and answers 403
    d.page.get_by_role("tab", name="Web").click()
    d.page.fill("#url", "https://docs.example/docs/")
    d.page.locator("#checkSite").click()
    box = d.page.locator("#siteCheck")
    expect(box).to_be_visible()
    expect(box).to_contain_text("Site check failed")
    expect(box).to_contain_text("403")


def test_an_error_banner_stays_in_view_when_the_page_is_scrolled(dash, library):
    d = dash()
    d.page.set_viewport_size({"width": 1280, "height": 500})
    d.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    d.page.evaluate("document.getElementById('banner').textContent = 'boom'; document.getElementById('banner').hidden = false")
    box = d.page.locator("#banner").bounding_box()
    assert 0 <= box["y"] and box["y"] + box["height"] <= 500  # inside the window, not above it
