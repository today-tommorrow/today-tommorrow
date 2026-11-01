"""HTTP-only BGE-M3 boundary; no model, database or fallback implementation."""

import http.client
import json
import math
from typing import cast
from urllib.parse import urlsplit

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]

DIMENSION = 1024
MAX_LENGTH = 512
MODEL = "bge-m3"
SERVICE_PROFILE = "bge-m3-flagembedding-cuda-v1"


class EmbeddingError(ValueError):
    """Safe failure code, without request text, endpoint or server response."""


def _unique_object(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_json_number")
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("nonfinite_json_constant")


def _json_object(body: bytes) -> dict[str, JSONValue]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_invalid_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("json_object_required")
        return cast(dict[str, JSONValue], value)
    except (ValueError, UnicodeError, RecursionError):
        raise EmbeddingError("invalid_embedding_response") from None


class BgeM3Embeddings:
    """Check the pinned runtime, then encode exactly one unmodified sentence.

    The socket timeout and response limit apply to each of the two HTTP calls.
    No retries or redirects are followed. This is not a total latency guarantee.
    Corpus MPS and query CUDA are compatible profiles, not bit-identical devices.
    """

    def __init__(
        self, base_url: str, *, timeout: float, max_response_bytes: int = 65536,
    ) -> None:
        try:
            if not isinstance(base_url, str) or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in base_url
            ):
                raise ValueError
            address = urlsplit(base_url)
            if (
                address.scheme not in {"http", "https"}
                or not address.hostname
                or address.username is not None
                or address.password is not None
                or address.path not in {"", "/"}
                or "?" in base_url
                or "#" in base_url
                or (address.port is not None and address.port < 1)
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise EmbeddingError("invalid_embedding_base_url") from None
        try:
            valid_timeout = type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0
        except OverflowError:
            valid_timeout = False
        if not valid_timeout or type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise EmbeddingError("invalid_embedding_budget")
        self.address = address
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def _request(
        self, method: str, path: str, body: bytes | None = None,
    ) -> dict[str, JSONValue]:
        connection_type = (
            http.client.HTTPSConnection if self.address.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            cast(str, self.address.hostname), self.address.port, timeout=self.timeout,
        )
        try:
            connection.request(
                method, path, body=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise EmbeddingError("embedding_http_error")
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise EmbeddingError("embedding_response_too_large")
        except TimeoutError:
            raise EmbeddingError("embedding_timeout") from None
        except (OSError, http.client.HTTPException, UnicodeError, OverflowError):
            raise EmbeddingError("embedding_unavailable") from None
        finally:
            connection.close()
        return _json_object(data)

    def _verify_metadata(self, revision: str) -> None:
        metadata = self._request("GET", "/metadata")
        expected: dict[str, str | int] = {
            "repository": "BAAI/bge-m3",
            "revision": revision,
            "dimension": DIMENSION,
            "profile": SERVICE_PROFILE,
            "model": MODEL,
            "device": "cuda:0",
            "dtype": "torch.float16",
            "attention": "sdpa",
        }
        if type(metadata.get("dimension")) is not int or any(
            metadata.get(key) != value for key, value in expected.items()
        ):
            raise EmbeddingError("embedding_profile_mismatch")

    def embed(self, text: str, *, revision: str) -> tuple[float, ...]:
        """The caller supplies the corpus model revision, not its MPS device."""
        if not isinstance(text, str) or not text or not isinstance(revision, str) or not revision:
            raise EmbeddingError("invalid_embedding_input")
        try:
            body = json.dumps(
                {"model": MODEL, "input": text, "max_length": MAX_LENGTH, "encoding_format": "float"},
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
            revision.encode("utf-8")
        except UnicodeError:
            raise EmbeddingError("invalid_embedding_input") from None
        self._verify_metadata(revision)
        result = self._request("POST", "/v1/embeddings", body)
        if result.get("object") != "list" or result.get("model") != MODEL:
            raise EmbeddingError("invalid_embedding_response")
        data = result.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise EmbeddingError("invalid_embedding_response")
        row = data[0]
        if row.get("object") != "embedding" or type(row.get("index")) is not int or row["index"] != 0:
            raise EmbeddingError("invalid_embedding_response")
        values = row.get("embedding")
        if not isinstance(values, list) or len(values) != DIMENSION:
            raise EmbeddingError("invalid_embedding_vector")
        try:
            if any(type(value) not in {int, float} for value in values):
                raise ValueError
            vector = tuple(float(cast(int | float, value)) for value in values)
            if not all(math.isfinite(value) for value in vector):
                raise ValueError
            if not math.isclose(math.hypot(*vector), 1, rel_tol=0, abs_tol=1e-5):
                raise ValueError
        except (ValueError, OverflowError):
            raise EmbeddingError("invalid_embedding_vector") from None
        return vector
