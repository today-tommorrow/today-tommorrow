"""Opt-in, synthetic two-page PDF to live document-model boundary check."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from app.ai.document_analysis import DocumentAnalysis, DocumentChunkInput

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DOCUMENT_ID = "d8448fbd-4697-4ccb-bf4b-083a32c10515"
CHUNK_IDS = (
    "10e7e667-304e-4863-86d6-264f99a26001",
    "10e7e667-304e-4863-86d6-264f99a26002",
)
PAGE_TEXT = (
    "I have experience using Python and Linux.",
    "I want to learn SIEM and incident response.",
)
SAFE_ERRORS = frozenset(
    {
        "document_provider_unavailable",
        "invalid_document_input",
        "document_context_too_large",
        "generation_cancelled",
        "invalid_model_response",
        "invalid_model_grounding",
        "duplicate_model_item",
        "document_output_too_large",
        "model_http_error",
        "model_timeout",
        "model_unavailable",
        "model_response_too_large",
    }
)


def synthetic_pdf() -> bytes:
    writer = PdfWriter()
    for value in PAGE_TEXT:
        page = writer.add_blank_page(width=400, height=200)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({value}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    }
                ),
            }
        )
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def evaluate(
    result: DocumentAnalysis,
    chunks: tuple[DocumentChunkInput, ...],
    locators: tuple[str, ...],
) -> dict:
    from app.ai.document_analysis import PROMPT_VERSION

    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    expected_ids = tuple(chunk.chunk_id for chunk in chunks)
    lineage = all(
        p.document_id == DOCUMENT_ID
        and p.document_kind == "resume"
        and p.chunk_id in by_id
        and p.evidence_quote in by_id[p.chunk_id].text
        and p.label in p.evidence_quote
        for p in result.proposals
    )

    def contains(term: str, state: str, chunk_id: str) -> bool:
        return any(
            term.casefold() in p.label.casefold()
            and term.casefold() in p.evidence_quote.casefold()
            and p.suggested_state == state
            and p.chunk_id == chunk_id
            for p in result.proposals
        )

    checks = {
        "two_pdf_page_locators": len(locators) == 2
        and all(locators[i].startswith(f"pdf:page:{i + 1}:") for i in range(2)),
        "all_chunks_processed": result.processed_chunk_ids == expected_ids,
        "all_proposals_have_exact_source_lineage": lineage,
        "python_and_linux_suggested_experience": all(
            contains(x, "have", CHUNK_IDS[0]) for x in ("Python", "Linux")
        ),
        "siem_and_incident_response_suggested_learning": all(
            contains(x, "want_to_learn", CHUNK_IDS[1])
            for x in ("SIEM", "incident response")
        ),
        "no_unsupported_budget_or_schedule": all(
            not any(
                term in (p.label + " " + p.evidence_quote).casefold()
                for term in ("budget", "schedule", "salary")
            )
            for p in result.proposals
        ),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "page_count": len(chunks),
        "proposal_count": len(result.proposals),
        "prompt_version": PROMPT_VERSION,
    }


def run() -> dict:
    from app.ai.document_analysis import DocumentChunkInput, GemmaDocumentAnalyzer
    from app.documents.routes import DocumentError, extract_file

    start = time.monotonic()
    previous_path = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(BACKEND) + os.pathsep + (previous_path or "")
    try:
        extracted = extract_file(synthetic_pdf(), "pdf")
    except DocumentError:
        return {"status": "failed", "failure_code": "synthetic_pdf_extraction_failed"}
    finally:
        if previous_path is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = previous_path
    if extracted.get("status") != "queued" or len(extracted.get("chunks", [])) != 2:
        return {"status": "failed", "failure_code": "synthetic_pdf_extraction_failed"}
    pairs = extracted["chunks"]
    if any(
        not isinstance(pair, (list, tuple))
        or len(pair) != 2
        or not isinstance(pair[0], str)
        or not isinstance(pair[1], str)
        for pair in pairs
    ):
        return {"status": "failed", "failure_code": "synthetic_pdf_extraction_failed"}
    chunks = tuple(
        DocumentChunkInput(CHUNK_IDS[i], pair[0]) for i, pair in enumerate(pairs)
    )
    locators = tuple(pair[1] for pair in pairs)
    if tuple(chunk.text for chunk in chunks) != PAGE_TEXT:
        return {"status": "failed", "failure_code": "synthetic_pdf_unexpected_text"}
    analyzer = GemmaDocumentAnalyzer.from_question_settings()
    result = analyzer.analyze(DOCUMENT_ID, "resume", chunks)
    report = evaluate(result, chunks, locators)
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly authorize requests to the configured model",
    )
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {"status": "failed", "failure_code": "explicit_live_opt_in_required"}
            )
        )
        return 2
    try:
        sys.path.insert(0, str(BACKEND))
        from app.ai.document_analysis import DocumentAnalysisError
    except (
        ImportError,
        ValueError,
        TypeError,
        RuntimeError,
        OSError,
        AttributeError,
        SQLAlchemyError,
    ):
        print(json.dumps({"status": "failed", "failure_code": "verification_failed"}))
        return 1
    try:
        report = run()
    except DocumentAnalysisError as exc:
        code = (
            exc.args[0]
            if len(exc.args) == 1
            and type(exc.args[0]) is str
            and exc.args[0] in SAFE_ERRORS
            else "verification_failed"
        )
        report = {
            "status": "failed",
            "failure_code": code,
            "processed_chunk_count": len(exc.processed_chunk_ids),
        }
    except (
        ImportError,
        ValueError,
        TypeError,
        RuntimeError,
        OSError,
        AttributeError,
        LookupError,
        SQLAlchemyError,
    ):
        report = {"status": "failed", "failure_code": "verification_failed"}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
