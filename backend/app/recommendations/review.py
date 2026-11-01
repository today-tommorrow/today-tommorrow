"""Bind review consent to the exact currently confirmed, document-free input."""

import hashlib
import hmac
import json

from app.auth.config import AuthSettings
from app.intake.models import IntakeProfile
from app.intake.policy import evaluate


def review_snapshot(
    profile: IntakeProfile, selections: list[dict], documents: list[dict] | None = None
) -> dict:
    return {
        "profile_id": str(profile.id),
        "user_id": str(profile.user_id),
        "profile_revision": profile.revision,
        "answers": profile.answers_json,
        "reviewed_items": profile.reviewed_items_json,
        "selection_summaries": selections,
        "documents": documents or [],
    }


def ready(profile: IntakeProfile, documents: list[dict] | None = None) -> bool:
    documents = documents or []
    documents_ready = (
        profile.answers_json.get("document_decision") == "provided"
        and bool(documents)
        and all(d["status"] == "ready" and d["review_confirmed"] for d in documents)
    )
    state = evaluate(
        profile.answers_json, profile.unresolved_conflicts_json, documents_ready
    )
    return (
        state["next_action"] == "review"
        and not state["missing_slots"]
        and (
            documents_ready
            or (
                profile.answers_json.get("document_decision") == "none"
                and not documents
                and not profile.reviewed_items_json
            )
        )
    )


def review_token(
    profile: IntakeProfile, selections: list[dict], documents: list[dict] | None = None
) -> str | None:
    if not ready(profile, documents):
        return None
    secret = AuthSettings.load().signing_key
    body = json.dumps(
        review_snapshot(profile, selections, documents),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    signature = hmac.new(
        secret, b"today-recommendation-review-v1\0" + body, hashlib.sha256
    )
    return "v1." + signature.hexdigest()
