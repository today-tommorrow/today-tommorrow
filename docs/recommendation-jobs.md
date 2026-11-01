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

## Restart regression under execution contention (#31)

`--once` performs one claim attempt. Exit status zero can mean the execution guard was unavailable and no job was processed; it is not evidence of job completion. The long-running worker polls and retries.

The restart regression now terminates a worker during model HTTP, expires only the synthetic job lease, and deliberately holds the shared execution guard. It verifies that `--once` leaves the job at `running`, attempt 1. A replacement daemon has a unique PostgreSQL application name; the test observes its guard attempt in `pg_stat_activity` before releasing the guard, then requires success on attempt 2 and exactly one saved question. This uses bounded observable-state polling rather than assuming an immediate successful claim after process termination. No production locking or worker behavior was weakened.

The original intermittent run's transient trigger remains unconfirmed. The invalid one-shot completion assumption and the held-guard failure mechanism are now directly tested. On 2026-11-01, all 206 backend tests passed before the final test-only observation refinement; the refined restart test passed four targeted runs. This resolves this regression-test defect, not all browser, material-input, or failure-recovery criteria in issue #31.


### Recover a submitted profile in another tab

`GET /api/profiles/{id}/recommendation-job` requires the authenticated owner and returns `{"profile_id": "…", "revision": 1, "job": null}` when there is no recommendation job for the current profile revision. Otherwise `job` is the same public job shape as `GET /api/jobs/{id}`, including `profile_revision`, terminal errors or a completed recommendation run ID. The additive `profile_revision` field is also returned by `GET /api/jobs/{id}`; clients must verify it before opening a remembered recommendation result. The newest job is selected by creation time and then ID, both descending. Another owner's or missing profile returns 404; anonymous access returns 401. Responses are `no-store`. The client must compare profile ID and revision, and treat a failed lookup as unknown rather than as no job. This endpoint neither starts nor retries work.

## Failure and explicit retry regression (#31)

The browser regression uses different first-attempt and retry job IDs. It requires the reviewed goal, budget, selected wording, revision and review token to remain unchanged, verifies that failure does not trigger an automatic submission or result navigation, and follows only the result from an explicit new-key retry. This is a synthetic UI test, not GPU evidence.

The database regression sends a real HTTP request to a loopback server returning 503 through `GemmaRecommendations`. It verifies the safe terminal error, unchanged profile inputs and zero saved results. An explicit retry uses a new job and key; the retry and its sole saved result must match the frozen input captured immediately after the first submission. The successful retry runner is synthetic; actual GPU evidence is recorded separately.

### Actual browser failure and GPU recovery, 2026-11-01

Verified on main `7828d59` plus the regression tests in this change, using a synthetic authenticated session and an HTTP-seeded complete profile with no documents. This run starts at the review/submit screen; it does not re-verify Google OAuth or question acquisition. Its seeded profile has an empty selection-summary list. Nonempty selected wording is covered by the synthetic UI regression, not by this live recovery run.

Reproduction procedure:

1. Configure the API and recommendation worker with the same owned loopback model proxy URL before creating/submitting the profile. The model URL and budgets are frozen in each submission context. Keep them fixed so both attempts have equivalent inputs; changing worker configuration after submission can trigger context mismatch instead of provider failure. Keep the actual GPU services running.
2. Make that proxy return HTTP 503 for model generation. Submit through the browser. Require a terminal public `recommendation_failed`, preserved answers, selection summaries and profile revision, no result navigation, and zero saved recommendation runs.
3. Switch only the proxy behavior to forward to the actual model, keeping its URL and configuration unchanged. Explicitly press the browser recommendation button again. Require a new idempotency key and job ID, identical frozen input, and exactly one saved run associated with the successful retry.
4. Compare two authenticated result reads with the saved database result. Reload the result page and require the same run and course cards.

Observed: the first job failed with zero runs; the retry succeeded with two course items in one run. Both jobs and the saved run retained the same frozen input; answers and selection summaries remained unchanged. Two API reads matched the database, and the browser navigated to the saved result and reloaded it successfully. Pipeline duration was 176120 ms; worker job duration was 176.776 seconds. These timings describe one recovery run, not a latency distribution or reliability estimate.

The real-model record reports `gemma4-checklist`, revision `29d097773436b69ff9feafd636ab4cf873786537`, prompt `work24-recommendation-grounded-4`; the environment used the existing remote Gemma/BGE services. The local PostgreSQL test databases were upgraded additively from `0008` to `0009_text_documents`; the earlier browser profile and result were preserved. The injected failure was confined to an owned loopback proxy; no shared GPU service was stopped.

Private local evidence: `.outline/issue31-failure-recovery/failure-recovery-evidence.json`, `failure-preserved-input.jpg`, and `retry-result-reloaded.jpg`. Environment/session files and database dumps stay outside version control.

Still unverified by this slice: material registration/interpretation/deletion, real Google OAuth, an independent teammate's complete A/B walkthrough, DB outage, physical GPU cancellation, and the full cross-system failure matrix. Issue #31 remains partially complete.

## Result-write rejection and recovery (#31)

A result-table write rejection is distinct from a model HTTP error or a complete database outage. The recommendation worker inserts the result and marks its job successful in one transaction. An insertion error must roll back that transaction; if the database remains reachable and job writes still work, the separate failure transaction can expose the generic `recommendation_failed` status. A disconnected database may prevent that failure update too, so this scenario does not establish full-outage recovery.

The bounded fault is a temporary denial of `INSERT` on `today_app.recommendation_runs` for the application role in an exclusive test database. It must leave profile, session and job access unchanged. Capture the original permission state and register restoration before injection; verify the application role is neither superuser nor table owner and that its effective insert permission becomes false. Restore the original permission state before explicit retry and on cleanup. Never apply this experiment to a shared or production database.

Acceptance: the first attempt reaches persistence and fails safely with zero result rows; the reviewed profile and frozen submission remain intact. After permission restoration, explicit retry creates a distinct job and key, saves exactly one result associated with that retry, and preserves the original frozen input. Result reads must match the saved record. This does not verify blocked writes, lease expiry, physical GPU cancellation, or material-input recovery.

The ACL regression is opt-in because it changes table privileges temporarily. After configuring the normal guarded local database URLs, set `EXCLUSIVE_TEST_PORT` to that database's port and ensure no other API/worker uses it. Run from `backend/`:

```sh
L0_DATABASE_TESTS=1 ISSUE31_EXCLUSIVE_ACL_TEST=1 ISSUE31_EXCLUSIVE_ACL_PORT="$EXCLUSIVE_TEST_PORT" \
  python -m unittest discover -s tests -p test_recommendation_flow.py -k test_denied_result_insert_rolls_back_and_retry_preserves_snapshot -v
```

The test checks loopback host, matching application/migration database targets, the explicitly designated port, application role privileges, and absence of pre-existing queued/running jobs before starting the scenario. It registers cleanup before mutation and restores the original ACL in a `finally` block before retry. The successful model runner in this regression is synthetic.

### Actual browser result-write recovery, 2026-11-01

On main `62c8083` plus this regression, a separate loopback PostgreSQL clone was used exclusively for the fault. The application role was neither superuser nor table owner; its effective result-table INSERT privilege was verified false. All other application table access remained available. A synthetic authenticated session and HTTP-seeded complete no-document profile started at the browser review/submit screen. Its selection-summary list was empty; this run does not verify generated-question wording or real OAuth.

The first actual GPU attempt failed after 171.064 seconds at `persisting`, with safe diagnostic category `database_error` and public `recommendation_failed`. Zero recommendation runs existed. Answers, revision and frozen input remained intact. After restoring the exact original table ACL, explicit browser retry created a different job and idempotency key. It succeeded after 169.701 seconds (pipeline: 168779 ms), storing one run containing one course item. The original and retry snapshots and saved input were equal; the stored run belonged only to the retry job. Two authenticated result reads matched the database, and browser navigation plus reload displayed the same run. These are individual timings, not reliability or latency-distribution measurements.

The successful run reports real-model execution with `gemma4-checklist`, revision `29d097773436b69ff9feafd636ab4cf873786537`, prompt `work24-recommendation-grounded-4`. The experiment used the existing remote Gemma/BGE services without changing or stopping them. The original ACL was verified restored with effective INSERT enabled. The ordinary regression database was separate from the live browser fault database.

Local evidence is retained in `.outline/issue31-db-write-recovery/db-write-evidence.json`, `db-failure-preserved-input.jpg`, and `db-retry-result-reloaded.jpg`; credentials and database dumps are excluded from version control. This completes the bounded immediate result-write rejection/recovery scenario, not issue #31's full outage, material-input, OAuth or teammate E2E requirements.
