"""Explicit model/catalog configuration; there is no production sample provider."""

import os
import re
from dataclasses import dataclass

from sqlalchemy import Engine

from app.ai.embeddings import BgeM3Embeddings
from app.catalog.repository import CatalogRepository
from app.catalog.service import SearchService

from .contracts import RecommendationError
from .model import GemmaRecommendations
from .pipeline import RecommendationPipeline


@dataclass(frozen=True)
class RecommendationSettings:
    model_base_url: str
    model_id: str
    embedding_base_url: str
    profile_id: str
    dataset_id: str
    model_revision: str
    timeout: float = 60
    embedding_timeout: float = 30
    max_tokens: int = 2048
    max_response_bytes: int = 131072

    @classmethod
    def load(cls) -> "RecommendationSettings":
        try:
            settings = cls(
                model_base_url=os.environ.get("RECOMMENDATION_MODEL_BASE_URL", "").strip(),
                model_id=os.environ.get("RECOMMENDATION_MODEL_ID", "").strip(),
                embedding_base_url=os.environ.get("RECOMMENDATION_EMBEDDING_BASE_URL", "").strip(),
                profile_id=os.environ.get("CATALOG_PROFILE_ID", "").strip(),
                dataset_id=os.environ.get("CATALOG_DATASET_ID", "").strip(),
                model_revision=os.environ.get("RECOMMENDATION_MODEL_REVISION", "").strip(),
                timeout=float(os.environ.get("RECOMMENDATION_MODEL_TIMEOUT_SECONDS", "60")),
                embedding_timeout=float(os.environ.get("RECOMMENDATION_EMBEDDING_TIMEOUT_SECONDS", "30")),
                max_tokens=int(os.environ.get("RECOMMENDATION_MODEL_MAX_TOKENS", "2048")),
                max_response_bytes=int(os.environ.get("RECOMMENDATION_MODEL_MAX_RESPONSE_BYTES", "131072")),
            )
            if any(re.fullmatch(r"[a-f0-9]{64}", value) is None for value in (settings.profile_id, settings.dataset_id)):
                raise ValueError
            settings.model()
            settings.embedding()
            return settings
        except (ValueError, TypeError, OverflowError):
            raise RecommendationError("recommendation_provider_unavailable") from None

    def model(self) -> GemmaRecommendations:
        return GemmaRecommendations(self.model_base_url, self.model_id, timeout=self.timeout,
                                    max_tokens=self.max_tokens, max_response_bytes=self.max_response_bytes,
                                    model_revision=self.model_revision)

    def embedding(self) -> BgeM3Embeddings:
        return BgeM3Embeddings(self.embedding_base_url, timeout=self.embedding_timeout)


def build_runner(engine: Engine) -> RecommendationPipeline:
    settings = RecommendationSettings.load()
    repository = CatalogRepository(engine, profile_id=settings.profile_id, dataset_id=settings.dataset_id)
    return RecommendationPipeline(SearchService(repository, settings.embedding()), settings.model())
