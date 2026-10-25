"""Access-control accounts (admin only): create, update, rotate, revoke."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import CLIENT_NAME_RE, Principal, hash_token, new_token, validate_grants
from .deps import Deps
from .schemas import ClientCreate, ClientUpdate
from .tags import ACCESS


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[ACCESS])
    store_factory = d.store_factory
    current_principal = d.current_principal
    need_admin = d.need_admin

    @router.get("/v1/clients")
    def list_clients(p: Principal = Depends(current_principal)):
        """All accounts and their grants (never their tokens)."""
        need_admin(p)
        with store_factory() as store:
            return {"clients": store.list_clients()}

    @router.post("/v1/clients", status_code=201)
    def create_client(req: ClientCreate, p: Principal = Depends(current_principal)):
        """Create an account. The token in the reply is shown once."""
        need_admin(p)
        if not CLIENT_NAME_RE.match(req.name):
            raise HTTPException(422, f"invalid client name {req.name!r}")
        try:
            read, write = validate_grants(req.read), validate_grants(req.write)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        token = new_token()
        with store_factory() as store:
            if not store.create_client(req.name, hash_token(token), req.admin, read, write, req.note):
                raise HTTPException(409, f"client {req.name!r} already exists")
        # The only time the token is ever shown: only its hash is stored.
        return {"name": req.name, "token": token, "admin": req.admin, "read": read, "write": write}

    def _last_admin(store, name: str) -> bool:
        target = next((c for c in store.list_clients() if c["name"] == name and not c["revoked"]), None)
        return bool(target and target["admin"] and store.count_admins() <= 1)

    @router.patch("/v1/clients/{name}")
    def update_client(name: str, req: ClientUpdate, p: Principal = Depends(current_principal)):
        """Change an account's grants, admin flag or note. The last admin cannot be demoted."""
        need_admin(p)
        try:
            read = validate_grants(req.read) if req.read is not None else None
            write = validate_grants(req.write) if req.write is not None else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with store_factory() as store:
            if req.admin is False and _last_admin(store, name):
                raise HTTPException(409, "cannot remove the last admin")
            if not store.update_client(name, req.admin, read, write, req.note):
                raise HTTPException(404, f"client {name!r} not found")
            return next(c for c in store.list_clients() if c["name"] == name)

    @router.post("/v1/clients/{name}/rotate")
    def rotate_client(name: str, p: Principal = Depends(current_principal)):
        """New token for the same client (name and grants unchanged). The old token stops working immediately."""
        need_admin(p)
        token = new_token()
        with store_factory() as store:
            if not store.rotate_client(name, hash_token(token)):
                raise HTTPException(404, f"client {name!r} not found")
        return {"name": name, "token": token}  # shown once; only the hash is stored

    @router.delete("/v1/clients/{name}", status_code=204)
    def revoke_client(name: str, p: Principal = Depends(current_principal)):
        """Revoke an account; its token stops working at once. The last admin cannot be revoked."""
        need_admin(p)
        with store_factory() as store:
            if _last_admin(store, name):
                raise HTTPException(409, "cannot revoke the last admin")
            if not store.revoke_client(name):
                raise HTTPException(404, f"client {name!r} not found")

    return router
