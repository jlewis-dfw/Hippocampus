# ruff: noqa: F811  (the dash and browser fixtures are imported from test_dashboard, then used as arguments)
"""Index a folder from the dashboard, in a real Chromium against the real stack.

The folder is built from tests/vectors/vault_paths.json (the same fixture the unit and JavaScript checks use) and handed to
the page through the "Choose folder" input, which takes the same code path as a drop after the files are listed.
Dropping itself (webkitGetAsEntry) cannot be driven by Playwright: it is on the manual checklist in docs/Testing.
"""
import json
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_dashboard import Dashboard, browser, dash  # noqa: F401

VAULT = json.loads((Path(__file__).parents[1] / "vectors" / "vault_paths.json").read_text(encoding="utf-8"))


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for rel, text in VAULT["files"].items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return root


def uris(admin, slug):
    return sorted(s["uri"] for s in admin.get(f"/v1/collections/{slug}/sources?limit=500").json()["sources"])


def choose(d: Dashboard, vault, name: str):
    d.page.set_input_files("#folderInput", str(vault))
    expect(d.page.locator("#folderPanel")).to_be_visible()
    d.page.fill("#fName", name)


def run(d: Dashboard, contains: str):
    d.page.locator("#fStart").click()
    expect(d.page.locator("#fSummary")).to_be_visible()
    expect(d.page.locator("#fSummary")).to_contain_text(contains)


def test_choose_a_folder_preview_and_index_it(dash, admin, admin_token, vault):
    slug = f"vault-{uuid.uuid4().hex[:8]}"
    d = dash(admin_token)
    choose(d, vault, slug)
    tree = d.page.locator("#fTree")
    expect(tree.locator("tr", has_text="Journal")).to_contain_text("looks private")
    expect(tree.locator('input[data-root="Journal"]')).not_to_be_checked()
    expect(tree.locator('input[data-root="Work"]')).to_be_checked()
    expect(d.page.locator("#fPreview")).to_contain_text("6 files to index")  # Journal is unticked; the secret in leak.md is found when it is read
    d.page.select_option("#fCollection", "__new__")
    d.page.fill("#fNewSlug", slug)
    d.page.get_by_role("button", name="Check with server").click()
    d.page.locator("#fStart").click()
    expect(d.page.locator("#fSummary")).to_contain_text("5 added")
    expect(d.page.locator("#fSummary")).to_contain_text("Work/leak.md: credential-assignment")
    assert uris(admin, slug) == [f"files://{slug}/{p}" for p in (
        "Café/menu.md", "Projects/p.md", "README.md", "Work/Clients/acme.md", "Work/plan.md")]
    assert "abcdefgh12345678" not in d.page.content()
    assert d.errors == []


def test_second_run_and_unticking_a_folder_removes_its_files(dash, admin, admin_token, vault):
    slug = f"vault-{uuid.uuid4().hex[:8]}"
    d = dash(admin_token)
    choose(d, vault, slug)
    d.page.select_option("#fCollection", "__new__")
    d.page.fill("#fNewSlug", slug)
    d.page.check("#fModeSave")
    run(d, "5 added")
    expect(d.row("savedSources", slug)).to_contain_text("ok")

    (vault / "Work" / "plan.md").write_text("# Plan\nChanged.", encoding="utf-8")
    (vault / "README.md").unlink()
    d.row("savedSources", slug).get_by_role("button", name="Drop again").click()
    d.page.set_input_files("#folderInput", str(vault))
    expect(d.page.locator("#fTree")).to_contain_text("Work")
    d.page.locator('#fTree input[data-root="Projects"]').uncheck()
    run(d, "1 changed")
    expect(d.page.locator("#fSummary")).to_contain_text("2 removed")  # README.md is gone, Projects is unticked
    assert uris(admin, slug) == [f"files://{slug}/{p}" for p in ("Café/menu.md", "Work/Clients/acme.md", "Work/plan.md")]
    assert d.errors == []


def test_one_collection_per_folder(dash, admin, admin_token, vault):
    prefix = f"v{uuid.uuid4().hex[:6]}"
    d = dash(admin_token)
    choose(d, vault, prefix)
    d.page.select_option("#fCollection", "__new__")
    d.page.fill("#fNewSlug", prefix)
    d.page.check("#fPerFolder")
    expect(d.page.locator('#fTree input[data-slug="Work"]')).to_have_value(f"{prefix}-work")
    d.page.check("#fModeSave")
    run(d, "5 added")
    assert uris(admin, f"{prefix}-work") == [f"files://{prefix}/Work/Clients/acme.md", f"files://{prefix}/Work/plan.md"]
    assert uris(admin, f"{prefix}-projects") == [f"files://{prefix}/Projects/p.md"]
    assert uris(admin, prefix) == [f"files://{prefix}/README.md"]  # the files at the top stay in the base collection

    d.row("savedSources", prefix).get_by_role("button", name="Drop again").click()
    d.page.set_input_files("#folderInput", str(vault))
    expect(d.page.locator("#fTree")).to_contain_text("Work")
    d.page.locator('#fTree input[data-root="Work"]').uncheck()
    run(d, "2 removed")
    assert uris(admin, f"{prefix}-work") == []
    assert d.errors == []
