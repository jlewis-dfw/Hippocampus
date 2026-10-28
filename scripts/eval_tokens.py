#!/usr/bin/env python3
"""Compare the token cost of answering documentation questions two ways, using `claude -p` (headless Claude Code):

  manual  the model may only use Read, Grep and Glob on this repository's docs/ folder
  mcp     the model may only use the Hippocampus MCP search tool (collection "hippocampus-docs")

  scripts/eval_tokens.py --set synthesis        the harder set: no unique keyword, several need more than one page
  scripts/eval_tokens.py --plan                 print the questions and the two claude commands; runs nothing
  scripts/eval_tokens.py                        every question in both arms, once
  scripts/eval_tokens.py --repeat 3 --arm mcp   three runs of the mcp arm only
  scripts/eval_tokens.py --model sonnet --only 2,4

Every run is a separate `claude -p` process with no saved session, so no arm benefits from another's prompt cache. The
answer is checked against the regular expressions in QUESTIONS, because a cheap wrong answer is not a saving. Results are
written to evals/token-usage/<YYYYMMDDHHMMSS>/ (results.json and summary.md; the folder is git-ignored). Each run is
capped by --max-budget-usd, and the whole session by --total-budget-usd (default $10): once the summed cost reaches it, no
further run starts and the results so far are still written.

Needs: the claude CLI, a running Hippocampus with docs/ indexed (scripts/index_docs.py), and HIPPO_TOKEN in the environment
(read access to the collection). HIPPO_URL defaults to http://127.0.0.1:8090. The MCP server is started from a temporary
config that holds no secret; it inherits the two variables from this process.
Exit status: 0 finished, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLECTION = "hippocampus-docs"

# (question, patterns that must all match the answer, case-insensitive)
KEYWORD: list[tuple[str, list[str]]] = [
    ("Which embedding model does Hippocampus use, and how many dimensions does it produce?", [r"bge-small", r"384"]),
    ("What are the chunker's default target_tokens and overlap_ratio?", [r"\b400\b", r"0\.12"]),
    ("Which address does the published API port bind to by default?", [r"127\.0\.0\.1"]),
    ("Which HTTP header carries the API token?", [r"X-Hippo-Token"]),
    ("What is the largest file, in bytes, that the hippocampus_index_doc MCP tool accepts?", [r"1[, ]?000[, ]?000"]),
    ("Which environment variable tells the MCP server where the Hippocampus service is?", [r"HIPPO_URL"]),
]

# Questions with no unique keyword, several of which need facts from more than one page
SYNTHESIS: list[tuple[str, list[str]]] = [
    ("A search names a restricted collection the caller has no grant for. What status code comes back, and why can't "
     "hidden chunks use up the top_k slots?", [r"404", r"before|same query|part of the (same )?query|filter"]),
    ("If a crawled page later disappears from the site, is it removed from the collection? Say why.",
     [r"stay|remain|kept|not (be )?(removed|deleted)|isn'?t (removed|deleted)"]),
    ("Which index type is built on the embedding column, and which distance measure does it use?", [r"HNSW", r"cosine"]),
    ("Who may create a restricted collection, and what happens when someone tries to create a collection whose name "
     "is already taken by a restricted one they cannot see?", [r"admin", r"403"]),
    ("If embedding fails partway through syncing a changed document, what happens to that source's existing chunks, "
     "and why is the code ordered that way?", [r"untouched|unchanged|remain|preserved|kept|intact", r"before|first"]),
    ("Describe the steps a pushed Markdown file goes through to become searchable chunks, including how unchanged "
     "files are detected.", [r"sha-?256|hash|digest", r"chunk", r"embed", r"replace_source|transaction|store"]),
]
SETS = {"keyword": KEYWORD, "synthesis": SYNTHESIS}

PROMPTS = {
    "manual": "Answer using only the Markdown files under docs/ in the current directory (use Read, Grep, Glob). "
              "Answer in one or two sentences.\n\nQuestion: {q}",
    "mcp": "Answer using only the hippocampus_search tool on the collection '" + COLLECTION + "'. "
           "Answer in one or two sentences.\n\nQuestion: {q}",
}


def mcp_config(path: Path, servers: bool) -> None:
    """Write an MCP config: the Hippocampus server (using whichever module path this checkout has), or none at all."""
    module = "hippocampus.cli.mcp_server" if (ROOT / "src/hippocampus/cli/mcp_server.py").exists() else "hippocampus.mcp_server"
    cfg = {"mcpServers": {"hippocampus": {"command": str(ROOT / ".venv/bin/python"), "args": ["-m", module]}} if servers else {}}
    path.write_text(json.dumps(cfg))


def command(arm: str, model: str, config: Path, budget: float) -> list[str]:
    cmd = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
           "--disable-slash-commands", "--model", model, "--max-budget-usd", str(budget), "--mcp-config", str(config)]
    if arm == "manual":
        cmd += ["--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob"]
    else:
        cmd += ["--tools", "", "--allowedTools", "mcp__hippocampus__hippocampus_search"]
    return cmd


def run_one(arm: str, question: str, checks: list[str], model: str, config: Path, budget: float) -> dict:
    proc = subprocess.run(command(arm, model, config, budget), input=PROMPTS[arm].format(q=question), cwd=ROOT,
                          capture_output=True, text=True, timeout=600)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"arm": arm, "question": question, "error": (proc.stderr or proc.stdout)[:500]}
    u = out.get("usage", {})
    answer = out.get("result") or ""
    return {
        "arm": arm, "question": question, "answer": answer,
        "correct": all(re.search(p, answer, re.I) for p in checks),
        "input_tokens": u.get("input_tokens", 0),
        "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cost_usd": out.get("total_cost_usd", 0.0), "turns": out.get("num_turns", 0),
        "duration_s": round(out.get("duration_ms", 0) / 1000, 1), "is_error": out.get("is_error", False),
    }


def summarise(rows: list[dict], arms: list[str]) -> str:
    lines = ["| arm | runs | correct | turns | input+cache tokens | output tokens | cost USD | seconds |",
             "|---|---|---|---|---|---|---|---|"]
    for arm in arms:
        ok = [r for r in rows if r["arm"] == arm and "error" not in r]
        if not ok:
            lines.append(f"| {arm} | 0 | - | - | - | - | - | - |")
            continue
        mean = lambda k, ok=ok: statistics.mean(r[k] for r in ok)  # noqa: E731
        ctx = statistics.mean(r["input_tokens"] + r["cache_creation_tokens"] + r["cache_read_tokens"] for r in ok)
        lines.append(f"| {arm} | {len(ok)} | {sum(r['correct'] for r in ok)}/{len(ok)} | {mean('turns'):.1f} | {ctx:,.0f} "
                     f"| {mean('output_tokens'):,.0f} | {mean('cost_usd'):.4f} | {mean('duration_s'):.1f} |")
    lines.append("\nMeans per run. 'input+cache tokens' is input, cache-creation and cache-read tokens summed over all turns.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--set", dest="qset", choices=list(SETS), default="keyword",
                    help="keyword: one distinctive term each; synthesis: no unique term, some span several pages")
    ap.add_argument("--arm", choices=["manual", "mcp", "both"], default="both")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only", help="comma-separated question numbers, from 1")
    ap.add_argument("--max-budget-usd", type=float, default=1.0)
    ap.add_argument("--total-budget-usd", type=float, default=10.0, help="stop starting new runs once spend reaches this")
    ap.add_argument("--plan", action="store_true", help="print the questions and commands; run nothing")
    args = ap.parse_args()

    arms = ["manual", "mcp"] if args.arm == "both" else [args.arm]
    qlist = SETS[args.qset]
    picked = [int(n) for n in args.only.split(",")] if args.only else list(range(1, len(qlist) + 1))
    questions = [qlist[n - 1] for n in picked]

    with tempfile.TemporaryDirectory() as tmp:
        configs = {"manual": Path(tmp) / "none.json", "mcp": Path(tmp) / "mcp.json"}
        mcp_config(configs["manual"], servers=False)
        mcp_config(configs["mcp"], servers=True)
        if args.plan:
            for i, (q, checks) in zip(picked, questions, strict=True):
                print(f"{i}. {q}  [must match: {', '.join(checks)}]")
            for arm in arms:
                print(f"\n{arm}: {' '.join(command(arm, args.model, configs[arm], args.max_budget_usd))}  < prompt on stdin")
            return 0
        if "mcp" in arms and not os.environ.get("HIPPO_TOKEN"):
            print("HIPPO_TOKEN is not set; the mcp arm needs it.", file=sys.stderr)
            return 2
        os.environ.setdefault("HIPPO_URL", "http://127.0.0.1:8090")

        rows: list[dict] = []
        spent = 0.0
        jobs = [(n, q, checks, rep, arm) for n, (q, checks) in zip(picked, questions, strict=True)
                for rep in range(args.repeat) for arm in arms]
        for n, q, checks, rep, arm in jobs:
            if spent >= args.total_budget_usd:
                print(f"Total budget ${args.total_budget_usd:.2f} reached (spent ${spent:.4f}); stopping.")
                break
            row = run_one(arm, q, checks, args.model, configs[arm], args.max_budget_usd)
            row["question_no"], row["repeat"] = n, rep + 1
            rows.append(row)
            spent += row.get("cost_usd", 0.0)
            note = row.get("error") or f"{'ok' if row['correct'] else 'WRONG'} ${row['cost_usd']:.4f} {row['turns']} turns"
            print(f"Q{n} #{rep + 1} {arm:6} {note}", flush=True)

    out_dir = ROOT / "evals/token-usage" / (datetime.now().strftime("%Y%m%d%H%M%S") + "-" + args.qset)
    out_dir.mkdir(parents=True)
    (out_dir / "results.json").write_text(json.dumps({"model": args.model, "set": args.qset, "rows": rows}, indent=2))
    summary = summarise(rows, arms)
    (out_dir / "summary.md").write_text(summary + "\n")
    print("\n" + summary + f"\n\nWritten to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
