"""Bind review consent to the exact currently confirmed, document-free input."""

import hashlib
import hmac
import json

from app.auth.config import AuthSettings
from app.intake.models import IntakeProfile
from app.intake.policy import evaluate


def review_snapshot(profile: IntakeProfile, selections: list[dict]) -> dict:
    return {
        "profile_id": str(profile.id),
        "user_id": str(profile.user_id),
        "profile_revision": profile.revision,
        "answers": profile.answers_json,
        "reviewed_items": profile.reviewed_items_json,
        "selection_summaries": selections,
    }


def ready(profile: IntakeProfile) -> bool:
    state = evaluate(profile.answers_json, profile.unresolved_conflicts_json)
    return (
        state["next_action"] == "review"
        and not state["missing_slots"]
        and profile.answers_json.get("document_decision") == "none"
        and not profile.reviewed_items_json
    )


def review_token(profile: IntakeProfile, selections: list[dict]) -> str | None:
    if not ready(profile):
        return None
    secret = AuthSettings.load().signing_key
    body = json.dumps(
        review_snapshot(profile, selections),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    signature = hmac.new(
        secret, b"today-recommendation-review-v1\0" + body, hashlib.sha256
    )
    return "v1." + signature.hexdigest()
