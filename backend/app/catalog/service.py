"""Call the embedding provider after releasing the catalog preparation transaction."""

from datetime import date
from typing import Protocol

from .contracts import (
    CatalogError,
    CatalogSnapshot,
    SearchFilters,
    SearchResult,
    validate_options,
)
from .repository import CatalogRepository


class EmbeddingProvider(Protocol):
    def embed(self, text: str, *, revision: str) -> tuple[float, ...]: ...


class SearchService:
    def __init__(self, repository: CatalogRepository, provider: EmbeddingProvider) -> None:
        self.repository, self.provider = repository, provider

    def search(
        self, query: str, *, as_of: date, filters: SearchFilters = SearchFilters(),
        limit: int = 30, evidence_limit: int = 3,
        snapshot: CatalogSnapshot | None = None,
    ) -> SearchResult:
        validate_options(as_of, filters, limit, evidence_limit)
        try:
            valid = isinstance(query, str) and bool(query.strip()) and len(query.encode("utf-8")) <= 65536
        except UnicodeError:
            valid = False
        if not valid:
            raise CatalogError("invalid_search_query")
        snapshot = self.repository.prepare(snapshot) if snapshot is not None else self.repository.prepare()
        vector = self.provider.embed(query, revision=snapshot.revision)
        return self.repository.search(vector, snapshot, as_of=as_of, filters=filters, limit=limit, evidence_limit=evidence_limit)
