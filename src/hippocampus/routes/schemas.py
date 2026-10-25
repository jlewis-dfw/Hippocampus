"""Request bodies. Field limits are part of the API contract."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..files_ingest import MAX_BATCH_FILES
from ..sync_core import SOURCE_NAME_RE, FileFilter


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    collections: list[str] = []
    top_k: int = Field(5, ge=1, le=50)
    min_score: float = Field(0.0, ge=-1.0, le=1.0)


class CollectionCreate(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["shared", "restricted"] = "shared"


class CollectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["shared", "restricted"] | None = None


class ClientCreate(BaseModel):
    name: str
    admin: bool = False
    read: list[str] = []
    write: list[str] = []
    note: str | None = Field(None, max_length=500)


class ClientUpdate(BaseModel):
    admin: bool | None = None
    read: list[str] | None = None
    write: list[str] | None = None
    note: str | None = Field(None, max_length=500)


class IngestFileRequest(BaseModel):
    collection: str
    uri: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(None, max_length=512)
    text: str


class IngestRepoRequest(BaseModel):
    collection: str
    repo: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    pack: str = Field(min_length=1, max_length=20_000_000)  # `repomix --style xml|markdown` output


class FileItem(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str | None = None  # None only in a dry run, to preview paths and sizes without sending the text
    size: int | None = Field(None, ge=0)


class FilterSpec(BaseModel):
    include: list[str] = Field([], max_length=200)
    exclude: list[str] = Field([], max_length=200)
    extensions: list[str] | None = Field(None, max_length=100)  # None: the default text extensions
    default_excludes: bool = True
    gitignore: str | None = Field(None, max_length=100_000)
    folders: list[str] = Field([], max_length=500)  # top-level folders to index ("." = files at the top); empty = all

    def build(self, roots: list[str] | None = None) -> FileFilter:
        if any(len(g) > 300 for g in (*self.include, *self.exclude)):
            raise HTTPException(422, "a rule is longer than 300 characters")
        if any(not f or len(f) > 255 or "/" in f or "\\" in f or f == ".." for f in self.folders):
            raise HTTPException(422, "folders are top-level folder names")
        return FileFilter.build(self.include, self.exclude, self.extensions, self.default_excludes, self.gitignore,
                                self.folders if roots is None else roots)


class IngestFilesRequest(BaseModel):
    collection: str
    source: str = Field(pattern=SOURCE_NAME_RE.pattern)  # the folder's label; sources are files://<source>/<path>
    files: list[FileItem] = Field(max_length=MAX_BATCH_FILES)
    filter: FilterSpec = FilterSpec()
    session: str | None = None  # from the first batch's reply; later batches of the same run send it back
    final: bool = False  # the last batch: files not sent in this run are removed
    dry_run: bool = False  # decide only, write nothing


class SourceCreate(BaseModel):
    name: str = Field(pattern=SOURCE_NAME_RE.pattern)
    kind: Literal["upload"] = "upload"  # mount, s3, drive and git arrive with their readers
    collection: str
    settings: FilterSpec = FilterSpec()
    folder_collections: dict[str, str] = Field({}, max_length=500)  # top-level folder -> its own collection


class SourceUpdate(BaseModel):
    collection: str | None = None
    settings: FilterSpec | None = None
    folder_collections: dict[str, str] | None = Field(None, max_length=500)


class CrawlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    collection: str
    depth: int = Field(1, ge=1, le=5)
    match_pattern: str | None = None
    engine: Literal["static", "browser"] = "static"
