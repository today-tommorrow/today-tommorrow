import unittest


class IntakePolicyTests(unittest.TestCase):
    def state(self, answers, conflicts=()):
        from app.intake.policy import evaluate

        return evaluate(answers, list(conflicts))

    def test_empty_draft_needs_first_question(self):
        state = self.state({})
        self.assertEqual(state["section"], "direction")
        self.assertEqual(state["next_action"], "generate_question")
        self.assertEqual(state["eligible_slots"], ["entry_intent"])
        self.assertEqual(state["completed_sections"], [])

    def test_explicit_unknown_is_answered_and_inapplicable_children_are_skipped(self):
        answers = {
            "entry_intent": "explore",
            "field_code": "undecided",
            "experience": "unknown",
            "learning_goal": "unknown",
            "availability": "unknown",
            "delivery_mode": "unknown",
            "budget_status": "unknown",
            "start_timing": "unknown",
            "document_decision": "none",
        }
        state = self.state(answers)
        self.assertEqual(state["missing_slots"], [])
        self.assertEqual(state["next_action"], "review")
        self.assertEqual(
            state["completed_sections"],
            ["direction", "experience", "goal", "conditions"],
        )

    def test_conditional_fields_and_zero_budget(self):
        state = self.state(
            {
                "field_code": "other",
                "experience": "basic",
                "delivery_mode": "offline",
                "budget_status": "amount",
                "entry_intent": "goal_known",
            }
        )
        for slot in [
            "custom_field",
            "previous_gap",
            "commute_region",
            "budget_krw",
            "goal_text",
        ]:
            self.assertIn(slot, state["missing_slots"])
        state = self.state({"budget_status": "amount", "budget_krw": 0})
        self.assertNotIn("budget_krw", state["missing_slots"])

    def test_document_choice_does_not_claim_documents_were_processed(self):
        answers = {
            "entry_intent": "explore",
            "field_code": "ai_data",
            "experience": "beginner",
        }
        self.assertEqual(self.state(answers)["next_action"], "collect_documents")
        self.assertEqual(
            self.state({**answers, "document_decision": "provided"})["next_action"],
            "collect_documents",
        )

    def test_conflicts_require_reconfirmation_before_completion(self):
        state = self.state({"experience": "basic"}, ["experience"])
        self.assertEqual(state["next_action"], "resolve_conflict")
        self.assertIn("experience", state["missing_slots"])
