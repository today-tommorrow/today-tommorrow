import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.ai.gemma import GemmaQuestions


@dataclass(frozen=True)
class QuestionSettings:
    base_url: str
    model: str
    timeout: float
    max_tokens: int
    max_response_bytes: int

    @classmethod
    def load(cls):
        settings = cls(
            os.environ.get("QUESTION_MODEL_BASE_URL", "").strip(),
            os.environ.get("QUESTION_MODEL_ID", "").strip(),
            float(os.environ.get("QUESTION_MODEL_TIMEOUT_SECONDS", "60")),
            int(os.environ.get("QUESTION_MODEL_MAX_TOKENS", "512")),
            int(os.environ.get("QUESTION_MODEL_MAX_RESPONSE_BYTES", "65536")),
        )
        if (
            not settings.base_url
            or not settings.model
            or not (
                0 < settings.timeout <= 300
                and 16 <= settings.max_tokens <= 8192
                and 1024 <= settings.max_response_bytes <= 1048576
            )
        ):
            raise ValueError("question_provider_unavailable")
        _ = urlsplit(settings.base_url).port
        settings.provider()
        return settings

    def provider(self):
        return GemmaQuestions(
            self.base_url,
            self.model,
            timeout=self.timeout,
            max_tokens=self.max_tokens,
            max_response_bytes=self.max_response_bytes,
        )


def questions_enabled() -> bool:
    try:
        QuestionSettings.load()
        return True
    except ValueError:
        return False
