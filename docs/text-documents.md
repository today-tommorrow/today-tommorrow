# Text document registration

`POST /api/profiles/{id}/documents/texts` accepts `expected_revision`, `kind` (`resume`, `job_description`, `portfolio`, or `other`), and a nonempty `text` string. It requires the service session and CSRF protection. The default limit is 100,000 UTF-8 bytes per text and five documents per profile. Operators can set `DOCUMENT_MAX_TEXT_BYTES` (1–1,000,000) and `DOCUMENT_MAX_PER_PROFILE` (1–100). JSON transport has a separate bound so escaped Unicode at the text limit is accepted. Blank-only input, NUL and invalid Unicode are rejected. Nonblank originals retain their exact whitespace.

A successful 202 response contains `document` and the new `profile_revision`. Original text and chunks remain private in `today_app`; the SHA-256 digest and byte count cover the exact UTF-8 input. Bounded private slices preserve the full text and its character offsets. `GET /api/documents/{id}` and profile GET return owner-visible metadata and, once ready, an extracted preview of up to 500 characters. Server review items expose their exact source quotation, locator, and hash to that owner. All responses use `Cache-Control: no-store`; other owners receive 404. Invalid input, stale revisions, and capacity errors do not reveal text.

An optional `Idempotency-Key` (1–128 printable ASCII characters) replays the accepted response for identical requests; reuse with different input returns 409. Without a key, profile, revision, kind and content digest form a deterministic identity. Registration, analysis job, revision change, question invalidation and replay record commit together. Registration sets `document_decision` to `provided`; PATCH cannot set it to `none` while documents exist. Pending, failed, or unconfirmed documents block the review token. Once all documents are ready and explicitly confirmed, the ordinary answer-completion rules determine whether the profile can be submitted.

Text registration returns `queued` with a non-null analysis `job_id` and `review_confirmed: false`. The separately started document worker analyzes the persisted slices and publishes server-owned items or a failure state. The user confirms or edits the suggested content, including explicitly confirming an empty analysis result. Failed text analysis can be retried from retained text; deletion removes the source and linked citations. Worker setup and limits are described in [document-analysis.md](document-analysis.md), lifecycle behavior in [document-lifecycle.md](document-lifecycle.md), and file upload in [file-documents.md](file-documents.md). URL extraction remains outside this flow. The shared course catalog is unchanged.

## Reproduce and verify

Apply the normal local setup and latest migration (`cd backend && .venv/bin/python -m alembic upgrade head`) with the configured migration account. After authenticating, submit the following JSON to `/api/profiles/<owned-profile-id>/documents/texts` with the current profile revision and session CSRF header:

```json
{"expected_revision":0,"kind":"other","text":"합성 검증 자료: 리눅스 서버 운영 기초"}
```

Expect 202 with a `document` whose `source_type` is `text`, `status` is `queued`, `job_id` is non-null, `extracted_preview` is null, and `review_confirmed` is false, plus `profile_revision: 1`. Start the document worker, poll the job, and reload the owner profile for the current state and generated review items. A ready document still needs explicit confirmation before submission. Replaying the identical registration request returns the original 202 payload without adding another document or revision; it is not a current-state poll. Registration with stale revision or conflicting idempotency key returns 409; malformed/blank input returns 422; over-limit text returns 413.

From `backend`, with the configured local DB environment, run `L0_DATABASE_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_text_documents.py'` and the lifecycle/worker tests linked above. These use synthetic inputs/providers and check exact Unicode/CRLF preservation, hashes, limits, ownership, replay, concurrent revision conflict, and rollback of original/chunks/profile/request/job changes.

### Prior text-registration verification — 2026-11-01, before analysis integration

Python 3.14.6, Node 26.5.0 and isolated PostgreSQL 16.14/pgvector 0.6.0 were used. Migration 0009 applied successfully. Full `./check` passed: 276 web tests, 257 backend tests (one optional reimport test skipped), 22 launcher tests, 18 catalog unit tests and 10 catalog integration tests, plus type/lint/format/build checks.

A real HTTP server on `127.0.0.1:62731` and a synthetic authenticated user verified registration/replay 202, document/profile GET 200, the no-material bypass guard 422, and anonymous GET 401. The persisted state remained `queued` with no job and revision 1. Cookies and original private values were not included in the report. Evidence remains local in `../text18-verification/{full-check-final.log,http-result.json}` relative to the verification checkout.

That earlier text-registration verification did not run a new browser E2E because port 5173 was occupied by another workstream; its service was left untouched. Existing web tests passed, and that run made no GPU/model calls. GitHub Actions remained disabled per the team's explicit instruction. Those results establish only the earlier text-registration slice. Current model/browser lifecycle evidence is recorded in [document-lifecycle.md](document-lifecycle.md); OCR remains unimplemented.

The final run used `VITE_API_BASE_URL=http://127.0.0.1:5179/api API_TARGET=http://127.0.0.1:62731 ./check` with a dedicated Vite proxy on 5179 and the task database on 62730. This avoids depending on another workstream’s web service.
