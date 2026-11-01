# Document lifecycle decisions

D11: Deleting a document removes its retained raw bytes, extracted text, and chunks. Source-linked review items and citations in retained snapshots are removed in the same transaction. Dependent generated explanations are replaced with a fixed notice; catalog facts remain historical. Requests whose saved responses contain deleted source material retain only a non-sensitive tombstone, and replay returns 409. This is application-level deletion, not a claim about PostgreSQL backups or storage media.

D07: A recommendation tied to an earlier profile revision remains historical. Its result includes a notice requesting regeneration when the profile revision has changed. Deletion also removes affected generated explanations and adds a deletion notice.

Failed text and file documents can be retried. A file extraction failure re-runs the bounded extractor over retained bytes; an analysis failure reuses retained extracted text and source slices. Successful extraction queues a new analysis job; it does not mark the document ready. Retry replaces old derived review items and citations, invalidates dependent results, and clears document confirmation. An already queued document with an active job, a processing document, or a ready document cannot be retried. Legacy queued documents without jobs become retryable failures during migration.

The document worker advances queued → processing → ready/failed. Publication requires the current document job, a valid lease, and unchanged source hash and slices. Deletion, retry, cancellation, or source changes prevent an older attempt from publishing. Unrelated profile edits may proceed while analysis runs; successful publication increments the current profile revision. See [document-analysis.md](document-analysis.md) for worker settings and processing limits.

Ready documents expose an owner-only preview and server-generated review items with immutable source quotation, location, and hash. Model suggestions are separate from the initially unknown user state. The user saves label/state edits and explicitly confirms each document, including a document with no proposed items. Pending, failed, or unconfirmed documents block submission. A saved change without explicit reconfirmation clears the affected document's confirmation. The review token binds the confirmed snapshot. Confirmed experience and learning wishes retain source references in recommendation input; unknown and excluded items do not become confirmed facts.

Validation: use a disposable PostgreSQL database and the local test environment described in the repository. From `backend`, run `L0_DATABASE_TESTS=1 PYTHONPATH=.:tests python -m unittest test_document_lifecycle test_document_worker test_document_limits test_document_analysis_migration`. These tests use synthetic data/providers and do not replace actual model or browser evidence.

## Prior validation before worker integration (2026-11-01)

Environment: dedicated PostgreSQL 16.14 / pgvector 0.6.0, Python 3.14.6, Node 26.5.0. Full `./check` exited 0: web 276 passed; backend 340 run, two conditional skips; launcher 22 passed; catalog unit 32 passed; catalog integration 13 run, three conditional skips; TypeScript/Vite build passed. Existing checks were not disabled or weakened. An initial API-restart connection failure was rerun after health/unauthenticated responses were verified. The stale-result regression now asserts the intended notice while all historical facts and persisted public JSON remain unchanged.

Real HTTP on the dedicated API with a synthetic AuthStore user and invalid UTF-8 TXT confirmed registration/retry 202 (honest failed state), deletion 204, deleted-document GET 404, preservation of an unrelated direct answer, and review-token recovery after explicitly selecting no documents. The profile PATCH sends the complete answer object as required by its existing contract. Local logs and reproducible HTTP script are under `.outline/issue20-verification/` (`full-check.log`, `http_smoke.py`, `http-result.json`). No model or GPU call is involved; submission is covered separately with the existing synthetic provider in the DB suite.

That earlier validation did not include browser interaction: automatic approval review rejected starting the port reserved for stream 3. Its web regression tests and HTTP/database evidence are not browser E2E. Worker and pipeline integration described above was added later. Cleanup covers persisted document provenance; it does not claim support for arbitrary future schemas.

## Prior PR #78 cleanup review follow-up

Cleanup determines dependency from document/chunk/review-item references and persisted job/question/recommendation relationships before modifying a snapshot. The dependency closure includes downstream question references; quotation collection is local to the related records. All nonempty quotation lengths are handled inside those records, so a short quoted place name embedded in generated prose is removed without applying a global substring match to unrelated records.

Explicitly unrelated source subtrees remain unchanged, including `document_id`, `source_item_ids`, and historical `{id, text}` entries inside `sources`. Independent direct answers are preserved. An arbitrary root `id` does not exempt an otherwise linked snapshot from cleanup. Regressions cover the two reviewer examples (short embedded Korean quotation and an unrelated `sample` input when the removed quote is `a`), mixed sources, downstream question/job references, and local quotation isolation. The focused lifecycle PostgreSQL suite passed 25 tests, and an independent targeted review found no remaining blocker.

Prior cleanup follow-up validation: `./check` exited 0; web 276 passed, backend 340 run (two conditional skips), launcher 22 passed, catalog unit 32 passed, catalog integration 13 run (three conditional skips), TypeScript/Vite build passed. Logs are retained locally under `.outline/pr78-review-verification/`. This predates the worker and pipeline integration.

### Identifier-value preservation

The follow-up to inline comment 4434260623 shares the identifier field set between reference detection and cleanup. Identifier values are removed only on exact identifier matches; quote substring matching continues for ordinary text. Regression checks cover a mixed `source_item_ids` list, all supported scalar identifier fields, exact target-ID removal, and unchanged text/record-level redaction behavior. This prevents a removed quote such as `a` from deleting the unrelated identifier `another-item`.

Identifier follow-up validation: full `./check` exited 0 (web 276 passed; backend 342 run with two conditional skips; launcher 22 passed; catalog unit 32 passed; catalog integration 13 run with three conditional skips; production build passed). Focused PostgreSQL and pure-function regressions plus independent review passed. Logs: `.outline/pr78-followup-verification/`.
