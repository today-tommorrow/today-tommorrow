# Course detail and comparison browser acceptance

`tools/acceptance/course-browser.mjs` exercises the shipped `Results`, `CourseDetails`, and comparison components in Chromium against the running authenticated course API and real local PostgreSQL catalog. It requires the normal Vite development server and API proxy.

The recommendation response is an explicitly synthetic selection scaffold built from three actual course responses. It supplies selectable cards because this API change does not implement recommendation generation. It is not persisted and is not evidence of model execution, recommendation quality, Google login, or completion of issue #26. Course detail and comparison requests are never intercepted. GPU execution is not applicable to this acceptance test.

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

Use a synthetic user session issued by the real authentication store in the disposable test database. Do not use a person's Google session or commit the configuration. The course IDs must identify actual records in the pinned package; include nullable cost/source metadata among the selected courses.

```sh
node tools/acceptance/course-browser.mjs /private/test-config.json /private/evidence-directory
```

The runner creates a temporary `.course-browser-harness.tsx` in the configured web root and removes it in `finally`. It writes `course-browser.json`, `detail.png`, and `compare.png` to the evidence directory. Cookies are never included in the report.

## Checks and limits

- Actual course UUIDs, real-data flags, and unknown source verification dates survive HTTP retrieval.
- The existing detail component displays source metadata and unknown dates.
- Selecting two or three courses, closing comparison, deselecting, and selecting different courses produces the matching real API results.
- Direct browser navigation to detail/comparison API URLs preserves successful retrieval, all-or-nothing missing-ID errors, and invalid/duplicate-input errors.
- Existing component tests separately cover null amounts versus explicit zero and online mode versus explicit asynchronous schedules.

Direct URL checks here mean the documented HTTP API URLs. The application does not gain a new standalone course or comparison page route from this change. Catalog pinning and database permissions require backend integration checks; this browser harness does not substitute for those checks or establish historical recommendation-version correspondence.

## Recorded verification — 2026-11-01

The browser acceptance passed using Node 26.5.0, Chromium 145.0.7632.6, the actual local course API, and the pinned PostgreSQL catalog. The authenticated session was issued for a synthetic test user; Google login was not exercised. API reads used the Vite proxy on port 5175. The API's configured local authentication origin remained port 5173; this read-only browser test does not claim a cross-origin login or mutation/CSRF flow.

Three real Work24 providers were exercised: 중소기업중앙회, 주식회사항공보안아카데미, and 멀티캠퍼스 원격평생교육원. All three selected courses contained an unknown cost field. The successful scenarios were detail rendering, two- and three-course comparison, closing comparison, deselection/reselection, and direct API URL access. Missing-course comparisons returned 404 without partial results; duplicate and malformed comparison inputs returned 422. Source verification dates remained unknown.

The artifact set is `course-browser.json`, `detail.png`, and `compare.png` in the chosen evidence directory. Complete sanitized request/response examples are committed in [examples/course-api.json](examples/course-api.json). They contain public course data only; cookies, sessions, users and synthetic recommendation responses are excluded.

This verifies the currently approved Work24-only scope. It does not claim K-MOOC ingestion or cross-system data equivalence. It also does not prove the same catalog version was used by a successful actual recommendation run; that remaining issue #27 acceptance condition must be checked with recommendation integration evidence.
