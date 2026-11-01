# Text document registration

`POST /api/profiles/{id}/documents/texts` accepts `expected_revision`, `kind` (`resume`, `job_description`, `portfolio`, or `other`), and a nonempty `text` string. It requires the service session and CSRF protection. The default limit is 100,000 UTF-8 bytes per text and five documents per profile. Operators can set `DOCUMENT_MAX_TEXT_BYTES` (1–1,000,000) and `DOCUMENT_MAX_PER_PROFILE` (1–100). JSON transport has a separate bound so escaped Unicode at the text limit is accepted. Blank-only input, NUL and invalid Unicode are rejected. Nonblank originals retain their exact whitespace.

A successful 202 response contains `document` and the new `profile_revision`. `GET /api/documents/{id}` and profile GET return public metadata only. Original text and chunks remain private in `today_app`; the SHA-256 digest and byte count cover the exact UTF-8 input. A private chunk records the complete text and its character offsets. All responses use `Cache-Control: no-store`; other owners receive 404. Invalid input, stale revisions, and capacity errors do not reveal text.

An optional `Idempotency-Key` (1–128 printable ASCII characters) replays the accepted response for identical requests; reuse with different input returns 409. Without a key, profile, revision, kind and content digest form a deterministic identity. Registration, revision change, question invalidation and replay record commit together. Registration sets `document_decision` to `provided`; PATCH cannot set it to `none` while documents exist. Profiles with documents do not receive a review token.

Text is stored for later processing. The public state is `queued` with `job_id: null`; no worker is running and semantic analysis is not yet available. File upload, URL extraction, deletion, retry, and model inference are outside this slice. The shared course catalog is unchanged.

## Reproduce and verify

Apply the normal local setup and latest migration (`cd backend && .venv/bin/python -m alembic upgrade head`) with the configured migration account. After authenticating, submit the following JSON to `/api/profiles/<owned-profile-id>/documents/texts` with the current profile revision and session CSRF header:

```json
{"expected_revision":0,"kind":"other","text":"합성 검증 자료: 리눅스 서버 운영 기초"}
```

Expect 202 with a `document` whose `source_type` is `text`, `status` is `queued`, and `job_id`/`extracted_preview` are null, plus `profile_revision: 1`. GET the returned document ID and the owning profile: both must preserve the same metadata. Replaying the identical request returns the original 202 payload without adding another document or revision. Registration with stale revision or conflicting idempotency key returns 409; malformed/blank input returns 422; over-limit text returns 413.

From `backend`, with the configured local DB environment, run `L0_DATABASE_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_text_documents.py'`. The focused suite contains four document-specific tests and thirteen inherited profile tests. It checks exact Unicode/CRLF preservation, hashes, limits, ownership, replay, concurrent revision conflict, and injected failure after invalidation: original/chunks/profile/request/job changes all roll back.

### Recorded verification — 2026-11-01

Python 3.14.6, Node 26.5.0 and isolated PostgreSQL 16.14/pgvector 0.6.0 were used. Migration 0009 applied successfully. Full `./check` passed: 275 web tests, 257 backend tests (one optional reimport test skipped), 22 launcher tests, 18 catalog unit tests and 10 catalog integration tests, plus type/lint/format/build checks.

A real HTTP server on `127.0.0.1:62731` and a synthetic authenticated user verified registration/replay 202, document/profile GET 200, the no-material bypass guard 422, and anonymous GET 401. The persisted state remained `queued` with no job and revision 1. Cookies and original private values were not included in the report. Evidence remains local in `../text18-verification/{full-check.log,http-result.json}` relative to the verification checkout.

A new browser end-to-end run was not performed because port 5173 was occupied by another workstream; its service was left untouched. Existing web tests passed. No GPU/model calls were made. GitHub Actions remained disabled per the team's explicit instruction; local checks are the verification evidence for this change. This completes only the text intake slice of issue #18, not file/OCR support or semantic analysis.
