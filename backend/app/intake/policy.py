"""Completion from explicit answers; no model calls, inferred values or review token."""

from typing import Any

SECTIONS = ("direction", "experience", "goal", "conditions")


def has_answer(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _section_slots(answers: dict[str, Any]) -> dict[str, list[str]]:
    groups = {
        "direction": ["entry_intent", "field_code"],
        "experience": ["experience"],
        "goal": ["learning_goal"],
        "conditions": [
            "availability",
            "delivery_mode",
            "budget_status",
            "start_timing",
        ],
    }
    if answers.get("field_code") == "other":
        groups["direction"].append("custom_field")
    if answers.get("experience") in ("basic", "project", "work"):
        groups["experience"].append("previous_gap")
    if answers.get("entry_intent") == "goal_known":
        groups["goal"].append("goal_text")
    if answers.get("delivery_mode") in ("offline", "hybrid"):
        groups["conditions"].append("commute_region")
    if answers.get("budget_status") == "amount":
        groups["conditions"].append("budget_krw")

    return groups


def active_conflicts(answers: dict[str, Any], conflicts: list[str]) -> list[str]:
    slots = {key for keys in _section_slots(answers).values() for key in keys}
    return [key for key in conflicts if key in slots]


def evaluate(answers: dict[str, Any], conflicts: list[str]) -> dict[str, Any]:
    groups = _section_slots(answers)
    conflicts = active_conflicts(answers, conflicts)

    missing_by_section = {
        section: [
            key for key in keys if not has_answer(answers.get(key)) or key in conflicts
        ]
        for section, keys in groups.items()
    }
    missing = [key for keys in missing_by_section.values() for key in keys]
    if not has_answer(answers.get("document_decision")):
        missing.append("document_decision")
    completed = [section for section in SECTIONS if not missing_by_section[section]]
    section = next((s for s in SECTIONS if missing_by_section[s]), "review")
    action = "review" if section == "review" else "generate_question"
    eligible = missing_by_section.get(section, [])[:1]
    if conflicts:
        action = "resolve_conflict"
        section = next(
            (s for s, keys in groups.items() if any(k in conflicts for k in keys)),
            "direction",
        )
        eligible = []
    elif (
        "direction" in completed
        and "experience" in completed
        and answers.get("document_decision") != "none"
    ):
        section, action, eligible = "experience", "collect_documents", []
    return {
        "section": section,
        "next_action": action,
        "eligible_slots": eligible,
        "missing_slots": missing,
        "completed_sections": completed,
        "active_question_id": None,
    }
