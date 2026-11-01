"""Stable identities shared with the existing Work24 search contract."""

import json
from uuid import UUID, uuid5

NAMESPACE = UUID("a4b29d71-4f3e-50ab-9e95-dfba02578443")


def identity(*parts: str) -> UUID:
    return uuid5(NAMESPACE, json.dumps(parts, ensure_ascii=False, separators=(",", ":")))


def course_id(source_id: str) -> UUID:
    return identity("work24", source_id)


def offering_id(source_id: str, tme: str) -> UUID:
    return identity("work24", source_id, tme)
