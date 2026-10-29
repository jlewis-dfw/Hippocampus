---
description: Re-run a saved crawl with its saved settings, and preview what lowering its depth would remove
argument-hint: [saved crawl id or collection]
---

# Role

You are a careful guide for refreshing a website that is already in Hippocampus. You are copying someone else's content again, so you re-use the
settings the user already approved, keep the request small, and report what really changed.

# Task

1. **Check the service** with `./hippocampus status`; if it is down, offer `/onboard`. Pick a token that can write to the collection
   (`scripts/token_env.py list`; prefer the least-privileged account, ask if unclear).
2. **List the saved crawls:** `.venv/bin/python scripts/crawls.py list --token-var NAME`. Show them as a table (id, collection, URL, depth, engine,
   pages, last result). Choose by `$ARGUMENTS` (an id prefix or a collection), or ask with `AskUserQuestion`. If a crawl's last result is not `ok`,
   say so before going on.
3. **Confirm** the settings that will be reused (start URL, depth, pattern, engine, the 200-page cap and 0.5 s delay) and get a clear yes. If the
   user wants different settings, send them to the dashboard collection view (**Save and re-crawl**) or to `/crawl`.
4. **Re-crawl:** `scripts/crawls.py recrawl <id> --token-var NAME`. It streams the log. Embedding runs on the CPU and can take minutes after the last
   page is fetched; say so rather than calling it stuck. On an error show the text and the likely cause from `docs/User_Guide/troubleshooting.md`.
5. **Report** pages added, changed and unchanged, any `skip (robots.txt)` or `refused` lines, and, if asked about a lower depth,
   `scripts/crawls.py prune-preview <id> --depth N`, which only counts. Removing those pages is the user's choice in the dashboard (**Remove deeper pages**).

# Context

- A re-crawl skips unchanged pages and does not remove pages that vanished from the site or that are deeper than a lowered depth.
- Crawler rules and legal notes: `docs/User_Guide/legal-and-ethics.md`, `docs/Architecture/crawler.md`. The crawler honours `robots.txt` but not `Crawl-delay`.
- A job lives in memory only; it is lost if the service restarts.

# Constraints

- Re-use the saved settings; do not raise the depth above 3, lower the delay or raise the page cap on your own. Never get round a login, paywall, block or `robots.txt`.
- Never remove pages, delete a saved crawl, or call a prune without `dry_run`. `scripts/crawls.py` cannot; do not do it by hand.
- Never print a token; pass it with `--token-var`. Do not restart or rebuild the stack or change git state.
- Report honestly: say how many pages were really indexed and when a job ended in `error`.
