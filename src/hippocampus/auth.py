"""Interim access control (HIPPO-E06-F0): static bearer tokens with per-collection grants.

Every /v1 request resolves to a Principal. Shared collections behave as before; a restricted collection is visible
only to a principal holding a matching grant (or an admin). Replaced by an external JWT identity provider in HIPPO-E06: only
``authenticate`` changes, the grant model stays.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
import secrets
from dataclasses import dataclass, field

TOKEN_PREFIX = "hip_"
GRANT_RE = re.compile(r"^[a-z0-9*-]{1,64}$")
CLIENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Collections with these slug prefixes hold private story data: they must stay restricted, and anything with a
# matching URI scheme may only be written into a restricted collection (a guard like FORBIDDEN_URI_PARTS).
PRIVATE_SLUG_PREFIXES = ("jstories",)
PRIVATE_URI_PREFIXES = ("jstories://",)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_grants(grants: list[str]) -> list[str]:
    """Slugs or globs; a bare '*' is refused (it would include every restricted collection: use the admin flag)."""
    out = []
    for g in grants:
        if not GRANT_RE.match(g) or g.strip("*") == "":
            raise ValueError(f"invalid grant {g!r}")
        if g not in out:
            out.append(g)
    return out


def matches(grants: list[str], slug: str) -> bool:
    return any(fnmatch.fnmatchcase(slug, g) for g in grants)


def is_private_slug(slug: str) -> bool:
    return slug.startswith(PRIVATE_SLUG_PREFIXES)


@dataclass(frozen=True)
class Principal:
    name: str = "anonymous"
    admin: bool = False
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)

    @property
    def anonymous(self) -> bool:
        return self.name == "anonymous"

    def can_read(self, slug: str, visibility: str) -> bool:
        return self.admin or visibility == "shared" or matches(self.read, slug) or matches(self.write, slug)

    def can_write(self, slug: str, visibility: str, *, require_auth: bool) -> bool:
        if self.admin or matches(self.write, slug):
            return True
        return visibility == "shared" and not require_auth

    def view(self) -> dict:
        return {"name": self.name, "admin": self.admin, "read": self.read, "write": self.write}


ANONYMOUS = Principal()


class AuthError(Exception):
    """Bad, unknown or revoked token."""


def authenticate(authorization: str | None, store, hippo_token: str | None = None) -> Principal:
    """Resolve the caller. ``X-Hippo-Token`` wins over ``Authorization: Bearer`` (a reverse proxy in front of the
    dashboard may put its own Authorization header on every request).

    No credential, or a credential that is not one of ours (anything not starting ``hip_``, such as a proxy's JWT),
    is anonymous: it carries no privilege, so ignoring it is safe. A ``hip_`` token that does not resolve is an
    error, never a silent downgrade.
    """
    token = (hippo_token or "").strip()
    if not token and authorization:
        scheme, _, value = authorization.partition(" ")
        token = value.strip() if scheme.lower() == "bearer" else ""
    if not token.startswith(TOKEN_PREFIX):
        return ANONYMOUS
    client = store.find_client(hash_token(token))
    if client is None:
        raise AuthError("unknown or revoked token")
    return Principal(client["name"], client["is_admin"], list(client["read_grants"]), list(client["write_grants"]))
