import os


def bounded(name: str, default: int, maximum: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} out of range")
    return value


def max_text_bytes() -> int:
    return bounded("DOCUMENT_MAX_TEXT_BYTES", 100000, 1000000)


def max_documents() -> int:
    return bounded("DOCUMENT_MAX_PER_PROFILE", 5, 100)
