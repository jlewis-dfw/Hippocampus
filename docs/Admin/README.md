# The admin tool

`python -m hippocampus.cli.admin` manages accounts and collection visibility **directly in the database**. It needs only database access, so it works
before any admin account exists and is the way to recover from a lost admin token. Source: `src/hippocampus/cli/admin.py`.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## Running it

| Setup | Command |
|---|---|
| Local stack | `./hippocampus admin <command> ...` (runs the tool in the API container, which already knows the database) |
| Any environment | `HIPPO_DSN=postgresql://user:pass@host:5432/hippocampus python -m hippocampus.cli.admin <command> ...` (needs the package installed, `pip install -e ".[db]"`) |

Without `HIPPO_DSN` the tool prints `HIPPO_DSN is not set` and exits with status 2.

## Commands

### `create-client`

```bash
./hippocampus admin create-client --name notes-reader --read team-notes
./hippocampus admin create-client --name notes-writer --read 'team-*' --write team-notes --note "nightly importer"
./hippocampus admin create-client --name admin2 --admin
```

| Option | Meaning |
|---|---|
| `--name NAME` | Required. Lowercase letters, digits, `-`, `_`, starting with a letter or digit, up to 64 characters |
| `--read GRANT` | A slug or `*` pattern allowing read. Repeat the option for several |
| `--write GRANT` | A slug or `*` pattern allowing write (which implies read). Repeatable |
| `--admin` | Make it an admin account |
| `--note TEXT` | Free text |

It prints the account and then the token **once**: `token (shown once, store it in the vault): hip_...`. Copy it immediately. A bare `*` grant or an
invalid name exits with status 2; a name that is already taken (even by a revoked account) exits with status 1.

### `list-clients`

```text
admin                    active   admin=True  read=[] write=[] last_used=2026-10-22T19:38:01
notes-reader             active   admin=False read=['team-notes'] write=[] last_used=-
old-tool                 REVOKED  admin=False read=[] write=[] last_used=2026-09-01T08:00:00
```

Shows every account, revoked ones included. It never shows tokens (only hashes exist).

### `rotate-client NAME`

Issues a new token for an active account and prints it once. The old token stops working immediately. The name, grants and admin flag are
unchanged. Exit status 1 if there is no active account with that name.

### `revoke-client NAME`

Revokes an account for good; its token stops working at once and its name stays reserved. Exit status 1 if there is no active account with that name.

### `set-visibility SLUG shared|restricted`

Changes a collection's visibility. Making a collection with a reserved private prefix `shared` is refused (exit 2). Exit status 1 if the collection does not
exist.

## What it does not do

- **No guard for the last admin.** The API refuses to revoke or demote the last active admin; this tool does not, because it exists so you can always recover. If you
  revoke every admin by mistake, create a new one with `create-client --admin`.
- **No grant editing.** To change an account's grants use the dashboard or `PATCH /v1/clients/{name}`, or revoke and create under a new name.
- **No listing or deleting of collections.** Use the dashboard, the API or SQL.

## When to use it

- The very first admin account on a bare database (the local stack's launcher does this for you).
- A lost admin token: `./hippocampus admin create-client --name admin-2 --admin`, then revoke the old one.
- Scripting account creation in a deployment.
- Emergency visibility changes when no admin token is at hand.

## Security

The tool prints a live credential. Run it in a terminal you control, do not paste its output into a chat or a ticket, and store the token in a password manager or a
secret store. Anyone who can run it with the database connection string can create an admin, which is why the database connection string is as sensitive as an admin token.

## Tests

`tests/integration/test_admin_cli.py` runs the tool against a real database: it creates an account (and checks that only the hash is stored), refuses a duplicate and a bare
wildcard, rotates, changes visibility, refuses to share a reserved name, lists and revokes. It needs `HIPPO_TEST_DSN` and is skipped without it.
