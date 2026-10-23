# Collections and access

How Hippocampus decides who can see and change what. For the exact enforcement code and edge cases, see
[../Api/authorization.md](../Api/authorization.md).

## Shared and restricted collections

Every collection is one of two kinds:

| Kind | Who can read it | Who can write to it |
|---|---|---|
| **shared** (the default) | Everyone who can reach the service, with or without a token. | Everyone, unless `HIPPO_REQUIRE_AUTH=true` (the default for the local stack): then only accounts with a matching write grant, and admins. |
| **restricted** | Only accounts with a matching read or write grant, and admins. | Only accounts with a matching write grant, and admins. |

A restricted collection is **invisible** to everyone else, not just locked:

- it does not appear in the collections list or the totals in the status pills;
- an unscoped search (no collection named) never returns its text, even when it is the best match;
- asking for it by name answers `404 collection '<slug>' not found`, exactly as for a collection that does not exist.

So a caller without a grant cannot even learn that it exists. (Creating a collection whose name is taken by a
restricted one you cannot see answers `403 this collection name cannot be used`, which is the one place the name leaks.)

## Accounts, tokens and grants

Access is held by named **accounts** (the API calls them *clients*). Each account has:

- a **name**: lowercase letters, digits, `-` and `_`, starting with a letter or digit, up to 64 characters;
- a secret **token**, `hip_` followed by 43 random characters. Only a SHA-256 hash is stored;
- a list of **read grants** and a list of **write grants**;
- an **admin** flag, a free-text note, and timestamps for creation and last use.

A grant is a collection slug or a pattern with `*` wildcards, for example `team-handbook` or `notes-*`. The pattern may
contain lowercase letters, digits, `-` and `*`, up to 64 characters. A bare `*` is refused (it would include every
restricted collection; use the admin flag if you mean that). Matching is whole-slug: `notes-*` matches `notes-2026` but not
`notes` and not `my-notes-2026`.

- A **write grant implies read** on the same collections.
- An **admin** account can read and write everything, create restricted collections, change visibility, delete restricted
  collections, and manage accounts.
- Without a token you are **anonymous**: you see shared collections and, unless `HIPPO_REQUIRE_AUTH` is on, can write to them.

### How to send a token

Either header works; if both are sent, `X-Hippo-Token` wins:

```
Authorization: Bearer hip_...
X-Hippo-Token: hip_...
```

The second exists for deployments where a reverse proxy puts its own `Authorization` header on every request. A header
value that does not start with `hip_` (for example a proxy's own credential) is ignored and the request is treated as
anonymous. A `hip_` token that is unknown or revoked is rejected with `401 unknown or revoked token`; it is never silently
downgraded to anonymous.

## What each operation needs

| Operation | Needs |
|---|---|
| Search, list collections, list sources, read status | Read on each collection touched (the readable set is used when none is named) |
| Create a **shared** collection | A write grant on that slug, or anyone when `HIPPO_REQUIRE_AUTH` is off |
| Create a **restricted** collection | Admin |
| Change a collection's name, description or visibility | Admin |
| Delete a **shared** collection | Write on it |
| Delete a **restricted** collection | Admin |
| Index a file, crawl, index a repository, resync the docs repository | Write on the target collection |
| Read a job's status or log | The account that started it, or an admin (jobs started by the webhook are visible to everyone) |
| List, create, edit, rotate or revoke accounts | Admin |

## Creating a restricted collection

In the dashboard: sign in as an admin, start a crawl, choose **New collection...** and set **Visibility** to `restricted`. Or
through the API:

```bash
curl -s -X POST http://127.0.0.1:8090/v1/collections \
  -H "X-Hippo-Token: $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"slug": "team-notes", "name": "Team notes", "visibility": "restricted"}'
```

The empty collection is created and nothing else changes. Then create an account for whoever should use it.

## Creating and managing accounts

Dashboard: **Access**, **New client**, **Create client**. Or the admin tool, which works even before any admin exists:

```bash
./hippocampus admin create-client --name notes-reader --read team-notes
./hippocampus admin create-client --name notes-writer --read team-notes --write team-notes
./hippocampus admin list-clients
```

The token is printed once. Give a tool **only the access it needs**: a search-only integration should have a read grant and
no write grant. For the full set of commands see [../Admin/README.md](../Admin/README.md).

| Action | Dashboard | Admin tool | API |
|---|---|---|---|
| Create | New client | `create-client` | `POST /v1/clients` |
| Change grants or note | Edit grants | (use the API or revoke and create) | `PATCH /v1/clients/{name}` |
| New token, same name and grants | Rotate token | `rotate-client NAME` | `POST /v1/clients/{name}/rotate` |
| Disable | Revoke | `revoke-client NAME` | `DELETE /v1/clients/{name}` |

Rules worth knowing:

- **Names cannot be reused.** A revoked account keeps its name, so create a new account under a different name.
- **The last active admin cannot be revoked or demoted** through the API. (The admin tool talks to the database directly and
  can revoke any account, so you can always recover.)
- **Revocation and rotation take effect immediately** on the next request.
- **"Last used"** is refreshed at most once a minute.

## Lost or leaked tokens

A token cannot be recovered, because only its hash exists.

- **Lost token for an ordinary account:** rotate it (dashboard, admin tool or API) and give the new one to the tool.
- **Lost admin token:** run `./hippocampus admin create-client --name admin-2 --admin` (the admin tool needs only database
  access) and then revoke the old one.
- **Leaked token:** revoke or rotate it at once. Anything the token could read is exposed, so think about what that was.
- If you pasted a token into a chat, ticket or commit, treat it as leaked.

## The `HIPPO_REQUIRE_AUTH` switch

| Value | Effect |
|---|---|
| `true` (the default in the local stack) | Every write needs a token with a write grant on the collection. Anonymous callers can still read shared collections. |
| unset or `false` (the service's built-in default) | Anyone who can reach the service can create, crawl into and index shared collections. Restricted collections are unaffected: they always need a grant. |

Set it in `hippocampus.env`. See [configuration.md](configuration.md).

## Reserved private names

Collection slugs that start with a reserved prefix are **always restricted** and cannot be shared, and sources whose URI
uses the matching private scheme can only be indexed into a restricted collection. This protects material that must never
land in a collection everyone can read. The exact prefix is in
[../Api/authorization.md](../Api/authorization.md#reserved-private-names); an ordinary installation never needs it.

## A worked example

You want an assistant to search your team notes but never change them, and a colleague to keep the notes up to date:

```bash
./hippocampus admin create-client --name notes-admin --admin                # you: manage everything (token shown once)
# in the dashboard with that token, or with curl: create the restricted collection "team-notes"
./hippocampus admin create-client --name assistant --read team-notes        # search only
./hippocampus admin create-client --name colleague --read team-notes --write team-notes
```

The assistant's token searches `team-notes` and gets `403` on any attempt to index; the colleague's token can index; an
anonymous caller finds nothing and `team-notes` does not even show in their list.
