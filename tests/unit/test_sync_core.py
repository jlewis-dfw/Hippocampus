import pytest

from hippocampus.sync_core import (
    FileFilter,
    PathError,
    case_collisions,
    exclude_path_parts,
    file_uri,
    normalize_path,
    normalize_text,
    scan_files,
)


@pytest.mark.parametrize("raw,expected", [
    ("a/b.md", "a/b.md"),
    ("a\\b\\c.md", "a/b/c.md"),  # Windows separators
    ("./a//b.md", "a/b.md"),
    ("Cafe\u0301/n.md", "Caf\u00e9/n.md"),  # macOS NFD becomes NFC
    ("Caf\u00e9/n.md", "Caf\u00e9/n.md"),
])
def test_normalize_path_ok(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw,rule", [
    ("", "empty-path"), (".", "empty-path"), ("/etc/passwd", "absolute-path"), ("\\\\server\\share\\a.md", "absolute-path"),
    ("C:\\Users\\a.md", "drive-letter"), ("c:/a.md", "drive-letter"),
    ("../a.md", "path-traversal"), ("a/../../b.md", "path-traversal"), ("a\\..\\b.md", "path-traversal"),
    ("a/CON", "windows-reserved-name"), ("nul.txt", "windows-reserved-name"), ("x/com1.md", "windows-reserved-name"),
    ("a/b. ", "windows-reserved-name"), ("a/b.", "windows-reserved-name"),
    ("a\x00b.md", "control-character"), ("a/" + "x" * 1100, "path-too-long"),
])
def test_normalize_path_refused(raw, rule):
    with pytest.raises(PathError, match=rule):
        normalize_path(raw)


def test_normalize_text_line_endings_and_bom():
    assert normalize_text("a\r\nb\rc\n") == "a\nb\nc\n"
    assert normalize_text("\ufeff# T") == "# T"


def test_crlf_and_lf_give_the_same_document():
    flt = FileFilter.build()
    a = scan_files("s", [("n.md", "# T\r\nbody\r\n", None)], flt).documents
    b = scan_files("s", [("n.md", "# T\nbody\n", None)], flt).documents
    assert a == b and list(a) == [file_uri("s", "n.md")]


def decide(path, flt=None, size=None):
    return (flt or FileFilter.build()).check_path(path, size)


@pytest.mark.parametrize("path", [
    ".git/config.md", "Work/.obsidian/app.md", ".trash/old.md", "node_modules/x/README.md", "a/.DS_Store.md".replace(".md", ""),
    "Thumbs.db", "desktop.ini", "~$report.md", ".Trash-1000/a.md", "$RECYCLE.BIN/a.md", "p/__pycache__/a.md",
])
def test_default_excludes(path):
    d = decide(path)
    assert (d.outcome, d.reason) == ("skipped", "default-exclude")


def test_default_excludes_can_be_turned_off():
    assert decide(".obsidian/notes.md", FileFilter.build(default_excludes=False)).outcome == "indexed"


def test_extensions_default_and_custom():
    assert decide("a.md").outcome == "indexed"
    assert decide("a.MD").outcome == "indexed"
    assert decide("pic.png").reason == "extension"
    assert decide("a.py", FileFilter.build(extensions=["py", ".PY"])).outcome == "indexed"
    assert decide("a.bin", FileFilter.build(extensions=[])).outcome == "indexed"


def test_include_and_exclude_globs():
    flt = FileFilter.build(include=["Work/", "Projects/**/*.md"], exclude=["Work/Clients/", "*.draft.md"])
    assert decide("Work/a.md", flt).outcome == "indexed"
    assert decide("Projects/x/y/z.md", flt).outcome == "indexed"
    assert decide("Journal/a.md", flt).reason == "not-included"
    assert decide("Work/Clients/acme.md", flt).reason == "excluded-by-rule"
    assert decide("Work/deep/a.draft.md", flt).reason == "excluded-by-rule"


def test_anchored_and_unanchored_globs():
    flt = FileFilter.build(exclude=["/top.md", "docs/old"])
    assert decide("top.md", flt).reason == "excluded-by-rule"
    assert decide("sub/top.md", flt).outcome == "indexed"  # anchored to the root only
    assert decide("docs/old/a.md", flt).reason == "excluded-by-rule"
    assert decide("x/docs/old/a.md", flt).outcome == "indexed"  # has a "/", so anchored


def test_gitignore_rules_last_match_wins():
    flt = FileFilter.build(gitignore="# c\n*.md\n!keep.md\nbuild-notes/\n")
    assert decide("a.md", flt).reason == "gitignored"
    assert decide("keep.md", flt).outcome == "indexed"
    assert decide("build-notes/a.txt", flt).reason == "gitignored"


def test_secret_file_names_and_excluded_parts_are_rejected(monkeypatch):
    assert (decide(".env").outcome, decide(".env").reason) == ("rejected", "secret-file-name")
    assert decide("keys/id_rsa").reason == "secret-file-name"
    assert decide("deploy/server.pem.md".replace(".md", "")).reason == "secret-file-name"
    assert decide("private-infra/a.md").outcome == "indexed"  # nothing is excluded unless configured
    monkeypatch.setenv("HIPPO_EXCLUDE_PATH_PARTS", "Private-Infra, vault")
    flt = FileFilter.build()
    assert decide("x/PRIVATE-INFRA/a.md", flt).reason == "excluded-source"
    assert decide("my-vault/a.md", flt).reason == "excluded-source"


def test_exclude_path_parts_default_and_override(monkeypatch):
    monkeypatch.delenv("HIPPO_EXCLUDE_PATH_PARTS", raising=False)
    assert exclude_path_parts() == ()
    assert exclude_path_parts(("Private-Infra",)) == ("private-infra",)
    monkeypatch.setenv("HIPPO_EXCLUDE_PATH_PARTS", "")
    assert exclude_path_parts(("private-infra",)) == ()  # an explicit empty value turns the guard off


def test_traversal_and_windows_paths_are_rejected_with_the_rule():
    d = decide("..\\..\\secret.md")
    assert (d.outcome, d.reason) == ("rejected", "invalid-path:path-traversal")
    assert decide("C:\\x.md").reason == "invalid-path:drive-letter"


def test_size_limit():
    assert decide("big.md", size=200_001).reason == "too-large"
    assert decide("ok.md", size=200_000).outcome == "indexed"


def test_content_checks_name_the_rule_not_the_content():
    flt = FileFilter.build()
    token = "ghp_" + "a" * 36
    d = flt.check_content("a.md", f"my token {token}")
    assert d.outcome == "rejected" and token not in str(d.view())
    assert flt.check_content("a.md", "x\x00y").reason == "binary"
    assert flt.check_content("a.md", "  \n").reason == "empty"
    assert flt.check_content("a.md", "x" * 200_001).reason == "too-large"


def test_case_collisions():
    assert case_collisions(["A.md", "a.md", "b.md", "B.MD"]) == [["A.md", "a.md"], ["B.MD", "b.md"]]
    assert case_collisions(["a.md", "b.md"]) == []


def test_scan_files_collects_documents_and_reasons():
    files = [
        ("Work/a.md", "# A\nhello", None),
        ("Work\\b.md", "# B\nhi", None),  # Windows separator, same set
        (".obsidian/x.md", "x", None),
        ("c.md", "token = \"abcdefgh12345678\"", None),
        ("Cafe\u0301.md", "# nfd", None),
        ("Caf\u00e9.md", "# nfc", None),  # same name once normalised
        ("Work/A.md", "# case", None),
        ("../evil.md", "x", None),
    ]
    scan = scan_files("vault", files, FileFilter.build())
    assert sorted(scan.documents) == ["files://vault/Caf\u00e9.md", "files://vault/Work/A.md",
                                      "files://vault/Work/a.md", "files://vault/Work/b.md"]
    by_path = {d.path: d for d in scan.decisions}
    assert by_path[".obsidian/x.md"].reason == "default-exclude"
    assert by_path["c.md"].outcome == "rejected"
    assert by_path["Work/A.md"].warning == "case-collision with Work/a.md"
    assert scan.counts() == {"indexed": 4, "skipped": 2, "rejected": 2}
    assert {"path": "c.md", "reasons": ["credential-assignment"]} in scan.rejected()


def test_scan_files_non_markdown_becomes_a_fenced_document():
    scan = scan_files("s", [("src/a.py", "print(1)", None)], FileFilter.build(extensions=[".py"]))
    assert scan.documents["files://s/src/a.py"].text.startswith("# src/a.py\n\n```python\n")


def test_scan_files_path_only_pass_for_preview():
    scan = scan_files("s", [("a.md", None, 10), ("b.md", None, 500_000)], FileFilter.build())
    assert [d.outcome for d in scan.decisions] == ["indexed", "rejected"] and scan.documents == {}


def test_scan_files_rejects_bad_source_names():
    with pytest.raises(ValueError):
        scan_files("../x", [], FileFilter.build())
