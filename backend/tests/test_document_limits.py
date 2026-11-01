"""Source slicing and preflight limits."""

import unittest
from uuid import uuid4

from app.ai.document_analysis import (
    DocumentAnalysisError,
    DocumentChunkInput,
    GemmaDocumentAnalyzer,
)
from app.documents.service import split_chunks
from app.recommendations.pipeline import confirmed_sources


class DocumentLimitsTests(unittest.TestCase):
    def test_maximum_ascii_input_fits_chunk_budget_without_truncation(self):
        text = "a" * 100000
        chunks = split_chunks([(text, "text:chars:0:100000")])
        self.assertLessEqual(len(chunks), 256)
        self.assertEqual("".join(chunk for chunk, _ in chunks), text)
        self.assertTrue(all(len(chunk) <= 600 for chunk, _ in chunks))

    def test_excess_chunks_fail_before_model_request(self):
        provider = GemmaDocumentAnalyzer(
            "http://127.0.0.1:1",
            "synthetic",
            timeout=1,
            max_tokens=64,
            max_response_bytes=1024,
        )
        chunks = tuple(DocumentChunkInput(str(uuid4()), "a") for _ in range(257))
        with self.assertRaisesRegex(DocumentAnalysisError, "document_chunk_limit"):
            provider.analyze(str(uuid4()), "resume", chunks)

    def test_confirmed_document_sources_preserve_roles_and_provenance(self):
        documents = [{"id": "document-1", "status": "ready", "review_confirmed": True}]

        def item(id_, state, label):
            return {
                "id": id_,
                "state": state,
                "label": label,
                "document_id": "document-1",
                "chunk_id": "chunk-1",
                "evidence_quote": "original quote",
                "source_locator": "text:chars:0:14",
                "content_sha256": "a" * 64,
            }

        snapshot = {
            "profile_id": "profile-1",
            "profile_revision": 1,
            "answers": {
                "document_decision": "provided",
                "field_code": "software_cloud",
            },
            "documents": documents,
            "selection_summaries": [],
            "reviewed_items": [
                item("have-id", "have", "Python"),
                item("want-id", "want_to_learn", "SQL"),
                item("unknown-id", "unknown", "Private unknown"),
            ],
        }
        sources, query = confirmed_sources(snapshot)
        by_id = {source["id"]: source for source in sources}
        self.assertEqual(by_id["have-id"]["learner_state"], "have")
        self.assertEqual(by_id["want-id"]["learner_state"], "want_to_learn")
        self.assertEqual(by_id["want-id"]["evidence_quote"], "original quote")
        self.assertNotIn("unknown-id", by_id)
        self.assertNotIn("Python", query)
        self.assertIn("SQL", query)
