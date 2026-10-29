"""The HTTP routes, one module per area. Each has ``build(deps) -> APIRouter``; ``api.create_app`` includes them."""
from . import access, collections, docs, health, ingest, jobs, search, sources, uploads, web_sources

ROUTERS = (health, search, collections, access, ingest, uploads, sources, web_sources, docs, jobs)
