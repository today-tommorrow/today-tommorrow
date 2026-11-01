# Course detail and comparison browser acceptance

`tools/acceptance/course-browser.mjs` exercises the shipped `Results`, `CourseDetails`, and comparison components in Chromium against the running authenticated course API and real local PostgreSQL catalog. It requires the normal Vite development server and API proxy.

The runner offers a persisted recommendation mode and a separate synthetic selection scaffold built from three actual course responses. In scaffold mode, the recommendation response is explicitly synthetic. It supplies selectable cards because this API change does not implement recommendation generation. It is not persisted and is not evidence of model execution, recommendation quality, Google login, or completion of issue #26. Course detail and comparison requests are never intercepted. Scaffold mode does not execute a GPU model; persisted-run mode consumes a separately executed real-model run.

## Run

Use the repository's pinned Node version and an existing compatible Playwright installation/browser. No Playwright dependency is added to the application. Set `PLAYWRIGHT_MODULE` to its absolute `index.mjs` path if it is not available through normal Node module resolution.

Prepare a private, untracked JSON configuration with permissions `0600`:

```json
{
  "origin": "http://127.0.0.1:5175",
  "web_root": "/absolute/path/to/checkout/web",
  "cookies": [
    {
      "name": "today_tommorrow_session",
      "value": "<synthetic test-session cookie>",
      "domain": "127.0.0.1",
      "path": "/",
      "httpOnly": true,
      "sameSite": "Lax"
    }
  ],
  "course_ids": ["<actual UUID 1>", "<actual UUID 2>", "<actual UUID 3>"]
}
```

For persisted-run mode, replace `course_ids` with `"recommendation_run_id": "<persisted run UUID>"`. The authenticated user must own that run. The runner fetches it from the real API, requires a nonempty successful `real_model` result, and derives course IDs from its items. It does not intercept recommendation requests. The configuration must contain exactly one of `course_ids` and `recommendation_run_id`.

Use a synthetic user session issued by the real authentication store in the disposable test database. Do not use a person's Google session or commit the configuration. The course IDs must identify actual records in the pinned package; include nullable cost/source metadata among the selected courses.

```sh
node tools/acceptance/course-browser.mjs /private/test-config.json /private/evidence-directory
```

The runner creates a temporary `.course-browser-harness.tsx` in the configured web root and removes it in `finally`. It writes `course-browser.json` and `detail.png` to the evidence directory, plus `compare.png` when at least three courses are available. Cookies are never included in the report.

## Checks and limits

- Actual course UUIDs, real-data flags, and unknown source verification dates survive HTTP retrieval.
- In persisted-run mode, every selected offering and recommendation evidence ID, field path, source, verification state and excerpt is checked against its detail response, and every result is opened in the shipped UI. Cross-offering conflicts may make detail evidence conflicting where the single-offering recommendation reports observed.
- The existing detail component displays source metadata and unknown dates.
- Selecting two or three courses, closing comparison, deselecting, and selecting different courses produces the matching real API results.
- Direct browser navigation to detail/comparison API URLs preserves successful retrieval, all-or-nothing missing-ID errors, and invalid/duplicate-input errors.
- Existing component tests separately cover null amounts versus explicit zero and online mode versus explicit asynchronous schedules.

Direct URL checks here mean the documented HTTP API URLs. The application does not gain a new standalone course or comparison page route from this change. Catalog pinning and database permissions require backend integration checks; this browser harness does not substitute for those checks. A persisted run must be executed in this mode to produce recommendation/detail correspondence evidence.

## Recorded verification — 2026-11-01

The browser acceptance passed using Node 26.5.0, Chromium 145.0.7632.6, the actual local course API, and the pinned PostgreSQL catalog. The authenticated session was issued for a synthetic test user; Google login was not exercised. API reads used the Vite proxy on port 5175. The API's configured local authentication origin remained port 5173; this read-only browser test does not claim a cross-origin login or mutation/CSRF flow.

Three real Work24 providers were exercised: 중소기업중앙회, 주식회사항공보안아카데미, and 멀티캠퍼스 원격평생교육원. All three selected courses contained an unknown cost field. The successful scenarios were detail rendering, two- and three-course comparison, closing comparison, deselection/reselection, and direct API URL access. Missing-course comparisons returned 404 without partial results; duplicate and malformed comparison inputs returned 422. Source verification dates remained unknown.

The artifact set is `course-browser.json`, `detail.png`, and `compare.png` in the chosen evidence directory. Complete sanitized request/response examples are committed in [examples/course-api.json](examples/course-api.json). They contain public course data only; cookies, sessions, users and synthetic recommendation responses are excluded.

This verifies the currently approved Work24-only scope. It does not claim K-MOOC ingestion or cross-system data equivalence. It also does not prove the same catalog version was used by a successful actual recommendation run; the persisted-run verification below supplies that separate issue #27 evidence.

## Persisted real-model verification — 2026-11-01

A synthetic authenticated user submitted a complete explore profile through the real HTTP API: software/cloud, beginner, foundation goal, flexible availability, online, unknown budget, browse start timing, no document, and goal “리눅스 서버 운영 기초”. The production worker used the existing remote BGE-M3 and Gemma services against a disposable PostgreSQL database containing the pinned 406-course / 6,099-unit package. The job succeeded in 195.19 seconds and persisted three non-demo recommendation items (`runtime_mode=real_model`, `model_outcome=succeeded`). Identical submit replay and persisted-result reload were also checked. This is correspondence evidence, not an assessment of recommendation relevance or latency acceptance.

The stored run's `provenance_json.context.catalog` profile, dataset and manifest matched all three configuration pins in [course-api.md](course-api.md). Recomputing the catalog UUID from those pins matched `eee32537-570b-5103-be12-c65cdaaa435e`. The run used `gemma4-checklist`, revision `29d097773436b69ff9feafd636ab4cf873786537`; BGE-M3 revision was `5617a9f61b028005a4858fdac845db406aefb181`.

Using the persisted-run configuration and runner command above, Chromium 145.0.7632.6 / Node 26.5.0 passed all detail, offering, evidence and comparison assertions through the Vite proxy on port 5177. Providers were 에듀퓨어, 에듀윌원격국비교육원, and 그린온라인캠프원격평생교육원. Two different courses shared a title: comparison identity was checked by ordered course IDs and provider, not unique title assumptions. API direct navigation returned expected 200, 404 and 422 responses. No recommendation response was intercepted.

Reproduce by submitting the complete profile above to the normal profile/submit APIs, processing it with the production recommendation worker, and supplying its successful run ID and the owning synthetic user's session to the documented private browser configuration. Run the database regression with `L0_DATABASE_TESTS=1 python -m unittest discover -s tests -p 'test_course_recommendation_integration.py'` from `backend`; this separately uses explicit sample providers to test pin drift, exact text offering IDs and inferred source preservation. Full `./check` passed: 261 web tests, 220 backend tests (one optional reimport check skipped), 22 launcher tests, 11 catalog unit tests and four catalog integration tests, plus lint/type/build checks. The latest main worker-restart test also passed separately after rebasing.

Local evidence: `../course-27-verification/live-result.json` and `../course-27-verification/browser-real/{course-browser.json,detail.png,compare.png}` relative to the verification checkout. Private cookies/configuration are excluded from version control. Google OAuth, personal documents, production deployment and historical catalog retrieval were not exercised.
