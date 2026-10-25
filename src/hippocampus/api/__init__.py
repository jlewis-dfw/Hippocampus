"""The REST API package. ``uvicorn --factory hippocampus.api:app_from_env`` is the production entry point."""
from .app import (
    DEFAULT_ALLOWED_CIDRS,
    SLUG_RE,
    UI_CSP,
    UI_DIR,
    _docs_sync_from_env,  # noqa: F401  (tests call it)
    app_from_env,
    create_app,
    parse_networks,
)

__all__ = ["DEFAULT_ALLOWED_CIDRS", "SLUG_RE", "UI_CSP", "UI_DIR", "app_from_env", "create_app", "parse_networks"]
