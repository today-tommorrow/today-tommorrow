# Document analysis model boundary

`GemmaDocumentAnalyzer` in `backend/app/ai/document_analysis.py` is a synchronous, database-free adapter. The caller supplies an owner-checked document UUID, its trusted kind (`resume`, `job_description`, `portfolio`, or `other`), and an ordered tuple of `DocumentChunkInput` values with canonical chunk UUIDs and exact extracted text. The model is never asked to invent document IDs, page locations, or hashes. The caller maps returned chunk IDs back to its own persisted document, locator, and hash and verifies ownership.

Each chunk is sent as untrusted user data in a separate bounded, structured-output request. Returned labels must be exact substrings of their exact source quotes, and quotes must be exact substrings of that supplied chunk. One nonblank character is valid. Every proposal retains its supplied document ID, kind and chunk ID. `suggested_state` (`have`, `want_to_learn`, or `unknown`) is an unconfirmed model suggestion, not an accepted fact; the adapter does not emit `excluded` or create public ReviewedItem IDs. Exact quotations establish provenance, not semantic correctness. The caller must present and explicitly confirm or correct suggestions before treating them as facts.

A valid `items: []` means the chunk produced no proposals, not a parsing failure. Success reports all processed chunk IDs, including chunks with no proposals. The adapter accepts at most 100 chunks, 12 proposals per chunk and 100 proposals total; labels are limited to 160 characters and quotes to 2,000. Exact duplicate items within a chunk fail; distinct citations across chunks or distinct states are preserved. Consumers must not deduplicate by label: matching labels can carry different source quotes, pages, or suggested states. The adapter preflights every chunk before sending a request. Empty/invalid input, non-UTF-8 text, excessive chunk/context size, malformed/truncated output, unsupported output fields, foreign IDs, fabricated text, duplicates, cancellation, HTTP errors and timeouts fail with a fixed `DocumentAnalysisError` code. An error exposes only the failed chunk ID and previously processed chunk IDs, and returns no partial result. The caller must not mark an incompletely processed document ready.

Construction can reuse the server-only question-model settings with `GemmaDocumentAnalyzer.from_question_settings()`; the explicit constructor accepts the same base URL, model, timeout, output-token and response-byte budgets. The default context-window budget is 8192 tokens and can be overridden explicitly to match the configured service. Input uses a conservative UTF-8 byte budget with space reserved for the prompt, output tokens and template overhead; over-limit input fails instead of being silently truncated. The model and transport do not log source text, model responses, prompts, credentials, or exception text. Model calls are sequential; this adapter does not own the shared execution lock.

Cancellation is checked before calls, after blocking I/O, before each chunk is recorded, and immediately before return. This cannot cancel an already-running remote GPU request or prevent a cancellation, revision change, deletion, or lease loss after return. The calling worker must fence ownership, cancellation and current revision again in its persistence transaction. The adapter does not change document state, database records, retries, or user confirmation.

Focused synthetic tests from `backend`:

```sh
PYTHONPATH=.:tests python -m unittest test_document_analysis test_questions -q
ruff check app/ai/document_analysis.py tests/test_document_analysis.py
```

## Reproduce the synthetic live-model check

The demonstration creates its own two-page ASCII PDF in memory and uses the existing `extract_file` boundary. It contains only synthetic statements: Python/Linux experience on page 1 and a wish to learn SIEM/incident response on page 2. It does not load user files or write a PDF or model response to disk. Install the locked backend dependencies and start the explicitly authorized Gemma endpoint first. From the repository root, for example:

```sh
QUESTION_MODEL_BASE_URL=http://127.0.0.1:28080 \
QUESTION_MODEL_ID=gemma4-checklist \
QUESTION_MODEL_TIMEOUT_SECONDS=120 \
backend/.venv/bin/python scripts/verify_document_analysis.py --live
```

The endpoint and model settings are supplied by the operator, and the explicit `--live` flag is required. The example uses the existing server-only question-model settings; never put credentials in the command or output. It emits only aggregate check booleans, counts, prompt version and elapsed time. It exits zero only when both pages were extracted and processed, every returned quote maps to its page, the expected experience/learning terms appear in their proposed labels and exact quotes with the expected suggested states, and no unsupported budget or schedule appeared. A model can validly produce different suggestions; a failed expected check is reported with a nonzero exit and is not silently treated as success. The no-flag invocation exits without contacting a model.

Focused tests from `backend` include the reproducibility example:

```sh
PYTHONPATH=.:tests python -m unittest test_document_analysis_example test_document_analysis test_questions -q
```

On 2026-11-01, this repository script was run against the configured L4 Gemma service through the loopback tunnel. An initial run returned `status: passed`, two pages, four proposals and all six checks true in 33.667 seconds. After strengthening the oracle to require expected terms in proposed labels as well as quotes, a second run passed the same six checks with two pages and four proposals in 15.692 seconds. After restructuring the example imports and scoped extractor environment, a third run independently passed all six checks with two pages and four proposals in 14.142 seconds. Its output was synthetic aggregate evidence; model alias `gemma4-checklist` and revision `29d097773436b69ff9feafd636ab4cf873786537` were operator-supplied and served weights were not independently checksummed. The earlier equivalent synthetic run took 31.252 seconds. Private local evidence is retained outside Git under `.outline/issue18-model-verification/`. These observations do not verify the stream 1 worker, API, database persistence, deletion, user review, OCR, Google OAuth, or general model quality; they do not complete issue #18.
