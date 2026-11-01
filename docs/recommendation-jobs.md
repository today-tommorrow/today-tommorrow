# Recommendation submission and persistence

The supported flow is a complete, confirmed profile with `document_decision: none`. No document upload, interpretation, or source review is implied. Existing authenticated session and CSRF checks apply. All responses use `Cache-Control: no-store`; other owners and missing IDs return 404.

- `GET /api/profiles/{id}` returns a `review_token` only for a complete supported input. The domain-separated HMAC binds owner, profile, revision, answers, reviewed items and selected wording. GET never calls a model.
- `POST /api/profiles/{id}/submit` accepts `expected_revision`, `review_token`, and one `Idempotency-Key` header. It freezes input and configured catalog/pipeline context, marks the current revision submitted, and returns 202 with a job ID. Current incomplete input is 422; stale revision/token or unsupported material state is 409; unavailable configuration is 503.
- Repeating the same key and body replays the original response even after later edits. Reusing a key for a different request is 409. Different keys reuse the same active or successful recommendation for the current profile revision. After failure or cancellation, an explicit new-key submission can retry.
- `GET /api/jobs/{id}` exposes only public status, safe error, stages and `recommendation_run_id`. Existing POST cancellation remains available.
- `GET /api/recommendations/{id}` returns the immutable saved public result. Later profile or catalog changes do not rewrite it. Internal candidates/model provenance and original frozen input are stored separately and never included by this endpoint.

## Worker and storage

From `backend/`, run `uv run --env-file ../.env --locked python -m app.recommendations.worker` or add `--once`. The Python module reads process environment variables; `uv --env-file` explicitly loads the repository-root `.env`. The configured runner performs retrieval and model processing outside ORM transactions. Phase updates and heartbeat are short fenced transactions. Result insertion and successful job completion share one transaction and require the original lease token, attempt, current profile revision and submitted revision. Input edits supersede active jobs. Cancelled, superseded or expired attempts cannot save results.

Both question and recommendation workers use a nonblocking PostgreSQL session advisory lock `(741201, 2)` throughout execution, on a dedicated autocommit connection. The separate `(741201, 1)` transaction lock continues to serialize claims and heartbeat. Thus cancellation can report promptly while a blocked provider returns, without starting another physical worker call. No database transaction is held during a model request. If the guard connection itself dies, PostgreSQL releases its lock; this is not proof that an already-issued remote inference has stopped. HTTP timeouts and the model server's own concurrency limit remain necessary.

Migration `0007_recommendation_runs` changes only `today_app`, adds owner-bound result snapshots, and allows recommendation jobs. Source `work24_bge` remains read-only. Downgrade refuses while recommendation jobs exist, requiring an explicit retention decision instead of silently deleting results.

## Verification

`L0_DATABASE_TESTS=1 python -m unittest discover -s tests -p 'test_recommendation_flow.py' -v` uses the guarded disposable local PostgreSQL configuration. Tests create synthetic authenticated service sessions and explicitly inject a runner marked `runtime_mode: sample`. They cover replay/deduplication, ownership and CSRF, current input/token validation, failure and cancellation retry, concurrent submissions, immutable saved input, atomic persistence, stale/expired worker fencing, and a blocked provider that outlives its lease while heartbeat renews it. These synthetic tests are not live model verification.
