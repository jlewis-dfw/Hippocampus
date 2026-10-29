#!/usr/bin/env python3
"""Index a Git repository (or any folder of code and docs) into a collection through a Repomix pack.

  scripts/index_repo.py ~/code/my-project --collection code --plan
  scripts/index_repo.py ~/code/my-project --collection code --token-var HIPPO_TOKEN_ADMIN --yes --query "where is auth handled?"
  scripts/index_repo.py --pack pack.xml --repo my-project --collection code --yes        use a pack you already made

Without --pack it runs ``npx repomix --style xml`` in the folder (Node.js needed; Repomix honours .gitignore) into a temporary
file. The pack goes to POST /v1/ingest/repo and the job log is streamed. The server rejects any file that looks like it holds a
secret, and files of this repo label that are no longer in the pack are removed from the collection. --plan makes the pack and
reports its size and file count, and sends nothing. The label (--repo, default: the folder name) names the sources repo://<repo>/<path>.

Limits: pack 20,000,000 characters, 5,000 files, 200,000 bytes per file. Options: --url, --token-var, --collection, --name,
--restricted, --repo, --pack, --plan, --yes, --query.
Exit status: 0 done, 1 the job failed or rejected files, 2 could not run or declined.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hippo_client import add_connection_args, api, detail, ensure_collection, fail, read_token, run_job, verify_search

MAX_PACK = 20_000_000


def make_pack(folder: Path, out: Path) -> None:
    npx = shutil.which("npx")
    if npx is None:
        fail("npx (Node.js) is needed to make the pack, or pass a ready pack with --pack")
    run = subprocess.run([str(npx), "--yes", "repomix", "--style", "xml", "-o", str(out)], cwd=folder, capture_output=True, text=True)
    if run.returncode != 0 or not out.is_file():
        fail(f"repomix failed: {(run.stderr or run.stdout).strip()[-500:]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, nargs="?")
    add_connection_args(ap)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--restricted", action="store_true")
    ap.add_argument("--repo", default=None, help="label for the sources (default: the folder name)")
    ap.add_argument("--pack", type=Path, default=None, help="a Repomix xml or markdown pack made earlier")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--query", action="append", default=[])
    a = ap.parse_args()

    if a.pack is None and (a.folder is None or not a.folder.expanduser().is_dir()):
        fail("give a folder, or a ready pack with --pack and --repo")
    repo = a.repo or (a.folder.expanduser().resolve().name if a.folder else None)
    if not repo or not re.match(r"^[A-Za-z0-9._-]{1,100}$", repo):
        fail(f"invalid repo label {repo!r} (letters, digits, . _ -, up to 100); set --repo")

    with tempfile.TemporaryDirectory() as tmp:
        pack_path = a.pack or Path(tmp) / "pack.xml"
        if a.pack is None:
            make_pack(a.folder.expanduser().resolve(), pack_path)
        pack = pack_path.read_text(encoding="utf-8", errors="replace")
    files = len(re.findall(r'<file path="', pack)) or len(re.findall(r"^## File: ", pack, re.M))
    print(f"pack: {len(pack):,} characters, about {files} file(s), repo label {repo!r} -> collection {a.collection}")
    if len(pack) > MAX_PACK:
        fail(f"the pack is over {MAX_PACK:,} characters: index a sub-folder instead")
    if a.plan:
        print("(plan only: nothing was sent)")
        return 0
    if not a.yes and input("Send it, and remove indexed files of this repo label that are not in the pack? [y/N] ").lower() != "y":
        print("not indexing.")
        return 2

    token = read_token(a.token_var)
    ensure_collection(a.url, token, a.collection, a.name, a.restricted)
    st, job = api(a.url, token, "POST", "/v1/ingest/repo", {"collection": a.collection, "repo": repo, "pack": pack}, timeout=600)
    if st != 202:
        fail(f"cannot start the job: HTTP {st} {detail(job)}")
    print(f"job {job['job_id']} started")
    status, view = run_job(a.url, token, job["job_id"])
    if status != "done":
        fail(f"job {status}: {view.get('error')}", 1)
    res = view.get("result") or {}
    rejected = res.get("rejected") or []
    print(f"done: {({k: v for k, v in res.items() if k != 'rejected'})}")
    for r in rejected:
        print(f"  rejected {r.get('path')}: {', '.join(r.get('reasons', []))}")
    verify_search(a.url, token, a.collection, a.query)
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
