# Connecting assistants

How to give an AI coding assistant access to Hippocampus search. The server is the same for all of them (see [README.md](README.md)); only the registration
differs. The Claude Code, OpenCode and Antigravity (`agy`) commands were checked against each tool's command-line help; the Devin section comes from Devin's
published documentation (read October 2026) and has not been run. Menus and file locations change between versions, so prefer each tool's own `mcp list` command to confirm the result.

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
  -- /path/to/hippocampus/.venv/bin/python -m hippocampus.cli.mcp_server
claude mcp list
```

`--scope local` (the default) keeps it to the current project, `--scope user` makes it available everywhere, and `--scope project` writes a `.mcp.json`
that is meant to be shared: **do not use `project` scope with a token in it**. Inside a session, `/mcp` shows whether the server connected.

## OpenCode

```bash
opencode mcp add --global \
  --env HIPPO_URL=http://127.0.0.1:8090 --env HIPPO_TOKEN="$HIPPO_TOKEN" --env HIPPO_MCP_READONLY=1 \
  hippocampus -- /path/to/hippocampus/.venv/bin/python -m hippocampus.cli.mcp_server
opencode mcp list
```

Without `--global` the entry is written to the project's configuration instead. OpenCode prefixes each tool with the server name, so the tools appear as
`hippocampus_hippocampus_search` and `hippocampus_hippocampus_index_doc`. If your configuration has permission rules, use those names, and check them in a
session first.

## Antigravity (`agy`)

```bash
agy mcp add \
  --env HIPPO_URL=http://127.0.0.1:8090 --env HIPPO_TOKEN="$HIPPO_TOKEN" --env HIPPO_MCP_READONLY=1 \
  hippocampus /path/to/hippocampus/.venv/bin/python -m hippocampus.cli.mcp_server
agy mcp list
```

Flags must come **before** the server name; `agy` rejects a flag placed after it. Use `--` before the command if any argument starts with a dash.
`agy mcp disable hippocampus` and `enable` switch it off and on without removing it.

## Devin

Devin comes in two forms, and they behave very differently for Hippocampus. The Hippocampus server is **stdio only**, so what matters is *where Devin
runs the command* and whether that place can reach `HIPPO_URL`.

### Devin CLI (Devin for Terminal): runs on your machine

This works like the other command-line assistants. Register the server for yourself only. The `devin mcp add` command has no option for environment variables,
so write the entry in a config file (this is the schema in Devin's documentation):

```json
{
  "mcpServers": {
    "hippocampus": {
      "command": "/path/to/hippocampus/.venv/bin/python",
      "args": ["-m", "hippocampus.cli.mcp_server"],
      "env": {
        "HIPPO_URL": "http://127.0.0.1:8090",
        "HIPPO_TOKEN": "hip_...",
        "HIPPO_MCP_READONLY": "1"
      }
    }
  }
}
```

| Scope | File | Use for Hippocampus |
|---|---|---|
| user | `~/.config/devin/mcp_config.json` (Windows: `%APPDATA%\devin\mcp_config.json`) | **This one.** Private to you; `chmod 600` it |
| local | `.devin/mcp_config.local.json` in the project | Acceptable: it is git-ignored, but check that your `.gitignore` really covers it |
| project | `.devin/mcp_config.json` in the project | **Never with a token**: it is committed and shared |

Older Devin CLI versions (before v3000.3) kept `mcpServers` in `config.json`; they are migrated on start. Check with `devin mcp list` and `devin mcp get hippocampus`,
and start the command by hand if the tools do not appear. Devin's documentation does not say whether `${env:VAR}` expansion works in the `env` block (it is
documented for the OAuth fields only), so assume the token must be written literally.

Devin asks for approval before using an MCP tool. Tool names have the form `mcp__<server>__<tool>`, so to allow searching without prompts and keep a prompt
for indexing:

```json
{ "permissions": { "allow": ["mcp__hippocampus__hippocampus_search"], "ask": ["mcp__hippocampus__hippocampus_index_doc"] } }
```

(`permissions` goes in Devin's settings file, not the MCP file. Skip the second line if you registered the server read-only.)

### Devin in the cloud (the web app): usually not a good fit

Cloud Devin lets an organisation admin (the "Manage MCP Servers" permission) add a custom server under **Customize, MCPs, Add MCP, Add custom MCP**, with
STDIO, SSE or Streamable HTTP transports. Three things make it a poor match for a self-hosted Hippocampus:

1. A stdio server runs inside **Devin's own environment**, so the Python package must be installable there, and
2. that environment must be able to reach `HIPPO_URL`; a service bound to `127.0.0.1` or a home network is not reachable from a cloud service, and
3. you would be handing a cloud service a token that can read your collections.

Hippocampus does not offer an HTTP (SSE or Streamable HTTP) MCP endpoint, which is the transport cloud Devin handles best. If you do use it, publish the
service only behind TLS and authentication, give Devin its own **read-only account limited to non-sensitive shared collections**, set `HIPPO_MCP_READONLY=1`,
enter the token through Devin's secrets rather than in arguments, and rotate it when you stop. Never give it a token that can read restricted collections.
Stdio servers also must not print anything to standard output except protocol messages (the Hippocampus server does not).

Sources: [Devin MCP overview](https://docs.devin.ai/work-with-devin/mcp) and [Devin CLI MCP configuration](https://docs.devin.ai/cli/extensibility/mcp/configuration).

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
