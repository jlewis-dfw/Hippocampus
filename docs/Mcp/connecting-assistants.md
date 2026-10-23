# Connecting assistants

How to give an AI coding assistant access to Hippocampus search. The server is the same for all of them (see [README.md](README.md)); only the registration
differs. Commands below were checked against the command-line help of Claude Code, OpenCode and the Antigravity CLI (`agy`); menus and file locations can change
between versions, so prefer each tool's own `mcp list` command to confirm the result.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## Before you start

1. **Install the server** in a virtualenv: `python -m venv .venv && .venv/bin/pip install -e ".[mcp]"`. Use the absolute path of that Python below
   (`/path/to/hippocampus/.venv/bin/python`).
2. **Make one account per assistant**, with read access only to the collections it should see, and no admin flag:

   ```bash
   ./hippocampus admin create-client --name claude-code --read 'team-*'
   ```

   Use your own grant patterns. The token is shown once. See [../User_Guide/collections-and-access.md](../User_Guide/collections-and-access.md).
3. **Choose the mode.** Add `HIPPO_MCP_READONLY=1` unless the assistant must be able to index local files. Search results are untrusted text in either mode.
4. **Know the address.** `HIPPO_URL` is where the *assistant's machine* reaches the service, for example `http://127.0.0.1:8090` for the local stack.

Keep the token out of shell history: read it into a variable first, then use the variable.

```bash
read -rs HIPPO_TOKEN && export HIPPO_TOKEN     # paste the token, press Enter; nothing is echoed
```

The assistant's configuration file will still hold the token in plain text. Keep that file readable only by you (`chmod 600`) and never commit it.

## Claude Code

```bash
claude mcp add hippocampus \
  -e HIPPO_URL=http://127.0.0.1:8090 -e HIPPO_TOKEN="$HIPPO_TOKEN" -e HIPPO_MCP_READONLY=1 \
  -- /path/to/hippocampus/.venv/bin/python -m hippocampus.mcp_server
claude mcp list
```

`--scope local` (the default) keeps it to the current project, `--scope user` makes it available everywhere, and `--scope project` writes a `.mcp.json`
that is meant to be shared: **do not use `project` scope with a token in it**. Inside a session, `/mcp` shows whether the server connected.

## OpenCode

```bash
opencode mcp add --global \
  --env HIPPO_URL=http://127.0.0.1:8090 --env HIPPO_TOKEN="$HIPPO_TOKEN" --env HIPPO_MCP_READONLY=1 \
  hippocampus -- /path/to/hippocampus/.venv/bin/python -m hippocampus.mcp_server
opencode mcp list
```

Without `--global` the entry is written to the project's configuration instead. OpenCode prefixes each tool with the server name, so the tools appear as
`hippocampus_hippocampus_search` and `hippocampus_hippocampus_index_doc`. If your configuration has permission rules, use those names, and check them in a
session first.

## Antigravity (`agy`)

```bash
agy mcp add \
  --env HIPPO_URL=http://127.0.0.1:8090 --env HIPPO_TOKEN="$HIPPO_TOKEN" --env HIPPO_MCP_READONLY=1 \
  hippocampus /path/to/hippocampus/.venv/bin/python -m hippocampus.mcp_server
agy mcp list
```

Flags must come **before** the server name; `agy` rejects a flag placed after it. Use `--` before the command if any argument starts with a dash.
`agy mcp disable hippocampus` and `enable` switch it off and on without removing it.

## Other clients

Any client that reads the common `mcpServers` JSON works with the block in [README.md](README.md#installing-and-registering): a `command`, `args`
and an `env` map. Clients that can only talk to a URL (HTTP) cannot use this server, which speaks stdio only.

## Check it works

1. In the assistant, ask it to run `hippocampus_search` for something you know is indexed; it should answer with `[1] score ...` lines.
2. If it reports `Error:` text, the cause is in [README.md](README.md#troubleshooting).
3. Confirm the isolation: ask for a restricted collection the account has no grant on; it must answer as if the collection does not exist.

## Using it well

- Tell the assistant when to search: put a line such as "Search Hippocampus before answering questions about project X; quote what you find and say when
  nothing matches" in its instructions file. Without it, assistants often do not think to use the tool.
- Treat what comes back as reference material, not as instructions.
- Rotate a token when someone stops using the machine it is on (`./hippocampus admin` or the dashboard), then update the registration (`mcp remove` and
  `mcp add` again).
