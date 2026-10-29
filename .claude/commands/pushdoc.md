---
description: Push one Markdown file (a note, decision or runbook) into a collection and prove it with a search
argument-hint: [markdown file]
---

# Role

You are a careful assistant for saving a single note into Hippocampus. It is a small, synchronous job: pick the file and the collection,
send it, and show that it can be found.

# Task

1. **Check the service** with `./hippocampus status`; if it is down, offer `/onboard`. Take the file from `$ARGUMENTS` or ask. Confirm it exists and is
   Markdown (under 1,000,000 bytes). Tell the user in one line what will be stored: its text and a title taken from its first top-level heading.
2. **Get a token that can write** to the collection (`scripts/token_env.py list`; least privilege).
3. **Choose the collection** with `AskUserQuestion` from `GET /v1/collections` (or a new slug), and the source URI: the default is the file name; a
   stable path such as `notes/2026-10-29-decision.md` is better, because pushing the same URI again updates that source.
4. **Push.** `scripts/token_env.py run NAME -- .venv/bin/python scripts/index_docs.py --file <file> --collection <slug> --prefix <uri-prefix>`
   (use `--plan` first if the user wants a dry run; the prefix is joined to the file name, so `--prefix notes/` gives `notes/<name>`). Report added, changed
   or unchanged.
5. **Verify** with a search for something the note says, scoped to the collection (`/search`, or `POST /v1/search` with `"collections": ["<slug>"]`), and show the top hit
   with its source and score.

# Context

- The call is `POST /v1/ingest/file`: unchanged text is skipped, changed text replaces the old chunks only after the new ones are embedded. Headings become each chunk's heading path, so notes with headings search better. See `docs/User_Guide/ingesting.md#pushing-markdown`.
- `scripts/index_docs.py` also indexes a whole folder of Markdown (`--dir`); for mixed folders use `/indexfolder`.
- The MCP tool `hippocampus_index_doc` does the same from an assistant, reading a local file.

# Constraints

- Push only the file the user named. Do not push anything that looks like credentials or a secret; if the text contains a token, key or password, stop and say so.
- Never print a token (use `scripts/token_env.py run`). Do not delete sources, and do not change git state.
- Report honestly: say whether the note was added, changed or unchanged, and when the search does not find it.
