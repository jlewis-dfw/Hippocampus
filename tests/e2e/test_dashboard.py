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
    expect(row.locator(".badge")).to_have_text("shared")
    expect(row.locator("td").nth(3)).to_have_text("2")  # sources
    row.get_by_role("button", name="Sources").click()
    expect(d.page.locator("#sourcesTitle")).to_have_text(library)
    expect(d.row("sources", "pg.md")).to_contain_text("Backups")
    expect(d.row("sources", "cats.md")).to_be_visible()
    d.page.locator("#closeSources").click()
    expect(d.page.locator("#sourcesSection")).to_be_hidden()


# --- search sandbox

def test_search_sandbox_finds_by_meaning(dash, library):
    d = dash()
    d.page.locator("#openSearch").click()
    expect(d.page.locator("#drawer")).to_be_visible()
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


def test_collection_search_button_scopes_the_drawer(dash, library):
    d = dash()
    d.row("collections", library).get_by_role("button", name="Search").click()
    expect(d.page.locator("#drawer")).to_be_visible()
    expect(d.page.locator("#searchScope")).to_have_value(library)
    d.page.locator("#closeSearch").click()
    expect(d.page.locator("#drawer")).to_be_hidden()


def test_hostile_content_is_shown_as_text_not_run(dash, admin):
    slug = _slug("ui-xss")
    admin.post("/v1/collections", json={"slug": slug, "name": "Hostile"})
    admin.post("/v1/ingest/file", json={
        "collection": slug, "uri": "x.md", "title": '<img src=x onerror="window.__pwned=1">',
        "text": "# Garden\n\n<script>window.__pwned=1</script> notes about growing tomatoes in a garden"})
    d = dash()
    d.row("collections", slug).get_by_role("button", name="Sources").click()
    expect(d.row("sources", "x.md")).to_contain_text('<img src=x onerror="window.__pwned=1">')
    d.page.locator("#openSearch").click()
    d.page.select_option("#searchScope", slug)
    d.page.fill("#query", "growing tomatoes")
    d.page.get_by_role("button", name="Run search").click()
    expect(d.page.locator("#results pre").first).to_contain_text("<script>window.__pwned=1</script>")
    assert d.page.evaluate("window.__pwned") is None
    assert d.page.locator("#results img, #sources img").count() == 0


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
    d.page.select_option("#collection", "__new__")
    expect(d.page.locator("#newCollection")).to_be_visible()
    d.page.fill("#newSlug", _slug("ui-anon"))
    d.page.fill("#url", "https://93.184.216.34/")
    d.page.locator("#crawlBtn").click()
    expect(d.page.locator("#banner")).to_contain_text("403")
    expect(d.page.locator("#jobSection")).to_be_hidden()


def test_crawl_form_creates_a_collection_and_reports_the_failed_job(dash, admin_token):
    d = dash(admin_token)
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
    expect(d.row("collections", slug)).to_contain_text("Crawl test")  # created before the crawl started
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
    expect(row.locator(".badge")).to_have_text("restricted")
    assert slug not in [c["slug"] for c in anon.get("/v1/collections").json()["collections"]]

    row.get_by_role("button", name="Share").click()
    expect(row.locator(".badge")).to_have_text("shared")
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
