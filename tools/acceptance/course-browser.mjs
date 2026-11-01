// Real course API/DB acceptance, with either a persisted recommendation or a synthetic scaffold.
// Config is private: {origin, web_root, cookies, recommendation_run_id} or {origin, web_root, cookies, course_ids}.
import assert from 'node:assert/strict'
import { readFile, writeFile, unlink, mkdir } from 'node:fs/promises'
import path from 'node:path'

const config = JSON.parse(await readFile(process.argv[2], 'utf8'))
const output = path.resolve(process.argv[3])
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const realRunMode = Boolean(config.recommendation_run_id)
assert(realRunMode !== Boolean(config.course_ids), 'configure exactly one of recommendation_run_id or course_ids')
if (!realRunMode) assert.equal(config.course_ids.length, 3)
const runId = config.recommendation_run_id || '00000000-0000-4000-8000-000000000099'
assert.match(runId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await context.addCookies(config.cookies)
const page = await context.newPage()
const report = {
  mode: realRunMode
    ? 'Actual Chromium, persisted successful real-model recommendation and authenticated course API'
    : 'Actual Chromium, real authenticated course API and PostgreSQL; synthetic recommendation selection scaffold only',
  browser: browser.version(),
  checks: [],
  course_responses: [],
}
page.on('response', (response) => {
  const url = new URL(response.url())
  if (url.pathname.startsWith('/api/courses/')) {
    report.course_responses.push({ path: url.pathname + url.search, status: response.status() })
  }
})
const harness = path.join(config.web_root, '.course-browser-harness.tsx')
let created = false
try {
  let run = null
  if (realRunMode) {
    const response = await context.request.get(`${config.origin}/api/recommendations/${encodeURIComponent(runId)}`)
    assert.equal(response.status(), 200)
    run = await response.json()
    assert.equal(run.id, runId)
    assert.equal(run.runtime_mode, 'real_model')
    assert.equal(run.model_outcome, 'succeeded')
    assert(typeof run.model_id === 'string' && run.model_id.length > 0)
    assert(typeof run.catalog_version_id === 'string' && run.catalog_version_id.length > 0)
    assert(Array.isArray(run.items) && run.items.length > 0, 'real run must have recommendation items')
    report.recommendation = { id: run.id, catalog_version_id: run.catalog_version_id,
      model_id: run.model_id, runtime_mode: run.runtime_mode, model_outcome: run.model_outcome,
      course_ids: run.items.map((item) => item.course.id) }
  }
  const courseIds = realRunMode ? run.items.map((item) => item.course.id) : config.course_ids
  assert.equal(new Set(courseIds).size, courseIds.length)
  const details = []
  for (const id of courseIds) {
    const response = await context.request.get(`${config.origin}/api/courses/${id}`)
    assert.equal(response.status(), 200)
    const course = await response.json()
    assert.equal(course.id, id)
    assert.equal(course.is_demo, false)
    assert(course.evidence.every((item) => item.checked_at === null))
    details.push(course)
  }
  report.checks.push('real course details preserve IDs, real-data flag and unknown evidence time')
  if (realRunMode) {
    for (const [index, item] of run.items.entries()) {
      const detail = details[index]
      assert.equal(detail.id, item.course.id)
      const offering = detail.offerings.find((entry) => entry.id === item.offering.id)
      assert(offering, `selected offering missing from detail: ${item.offering.id}`)
      for (const [key, value] of Object.entries(item.offering)) {
        assert.deepEqual(offering[key], value, `offering ${item.offering.id}: ${key}`)
      }
      const evidence = new Map(detail.evidence.map((entry) => [entry.id, entry]))
      for (const entry of item.evidence) {
        const match = evidence.get(entry.id)
        assert(match, `evidence missing from detail: ${entry.id}`)
        for (const key of ['field_path', 'source_url', 'source_system', 'checked_at']) {
          assert.deepEqual(match[key], entry[key], `evidence ${entry.id}: ${key}`)
        }
        assert(entry.quote === null ? match.quote === null : typeof match.quote === 'string' && match.quote.startsWith(entry.quote),
          `evidence ${entry.id}: excerpt does not match full quote`)
        assert(match.verification_state === entry.verification_state ||
          (entry.verification_state === 'observed' && match.verification_state === 'conflicting'),
        `evidence ${entry.id}: verification state`)
      }
    }
    report.checks.push('persisted recommendation items match selected offerings and evidence in real details')
  }
  report.courses = details.map((course) => ({
    id: course.id, source_system: course.source_system, provider_name: course.provider_name,
    offering_ids: course.offerings.map((offering) => offering.id),
    unknown_cost: course.offerings.some((offering) => offering.tuition_krw === null || offering.listed_self_pay_krw === null),
  }))
  const syntheticRun = {
    id: runId, profile_id: '00000000-0000-4000-8000-000000000098', profile_revision: 1,
    profile_snapshot: {}, catalog_version_id: '00000000-0000-4000-8000-000000000097',
    result_state: 'unknown_conditions', explanation_mode: 'llm', notices: [], excluded_counts: {},
    created_at: '2026-11-01T00:00:00Z', as_of: '2026-11-01T00:00:00Z', needs: [], stages: [],
    model_id: 'synthetic-selection-scaffold', runtime_mode: 'real_model', model_outcome: 'succeeded',
    pipeline_version: 'acceptance-only', ranking_config_version: 'acceptance-only', retrieval_config_version: 'acceptance-only',
    items: details.map((course, index) => ({
      course, offering: course.offerings[0] ?? null, rank: index + 1,
      match_state: 'unknown_conditions', reasons: [], evidence: course.evidence,
      missing_fields: course.missing_fields,
      rank_breakdown: { goal_basis: 'topic_only', score_lower: 0, score_upper: 1, coverage: 0,
        axes: [], constraint_checks: [], ranking_config_version: 'acceptance-only' },
    })),
  }
  // This fixture enables existing selection UI; it is neither persisted nor a model result.
  if (!realRunMode) await page.route(`**/api/recommendations/${runId}`, (route) => route.fulfill({ json: syntheticRun }))
  await writeFile(harness, `import React from 'react'; import {createRoot} from 'react-dom/client';\nimport {Results} from './src/Courses';\nimport './src/style.css';\ncreateRoot(document.getElementById('root')!).render(<Results runId='${runId}'/>);\n`, { flag: 'wx' })
  created = true
  await page.route('**/__course_browser_acceptance__', (route) => route.fulfill({
    contentType: 'text/html',
    body: `<html lang="ko"><body><div id="root"></div><script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;</script><script type="module" src="/.course-browser-harness.tsx"></script></body></html>`,
  }))
  await page.goto(`${config.origin}/__course_browser_acceptance__`)
  const checkboxes = page.getByRole('checkbox')
  await checkboxes.nth(courseIds.length - 1).waitFor()
  const detailPanel = page.getByRole('region', { name: '교육 상세', exact: true })
  for (const [index, detail] of details.entries()) {
    await page.getByRole('button', { name: '교육 상세', exact: true }).nth(index).click()
    await detailPanel.getByRole('heading', { name: detail.name, exact: true }).waitFor()
    assert((await detailPanel.innerText()).includes('미확인'))
    if (detail.evidence.length) assert((await detailPanel.innerText()).includes('· 미확인'))
    if (index === 0) await page.screenshot({ path: path.join(output, 'detail.png'), fullPage: true })
    await page.getByRole('button', { name: '상세 닫기', exact: true }).click()
  }
  report.checks.push('shipped CourseDetails renders every actual result and unknown evidence timestamp')
  if (courseIds.length >= 3) {
    const panel = page.getByRole('region', { name: '교육 비교 결과' })
    const compare = async (indices) => {
      const ids = indices.map((index) => courseIds[index])
      const responsePromise = page.waitForResponse((response) => {
        const url = new URL(response.url())
        return url.pathname === '/api/courses/compare' && url.searchParams.get('ids') === ids.join(',')
      })
      await page.getByRole('button', { name: '선택한 교육 비교', exact: true }).click()
      const response = await responsePromise
      assert.equal(response.status(), 200)
      const body = await response.json()
      assert.deepEqual(body.items.map((item) => item.id), ids)
      const articles = panel.locator('article')
      await articles.nth(indices.length - 1).waitFor()
      assert.equal(await articles.count(), indices.length)
      for (const [position, index] of indices.entries()) {
        const article = articles.nth(position)
        await article.getByRole('heading', { name: details[index].name, exact: true }).waitFor()
        assert((await article.innerText()).includes(details[index].provider_name ?? '교육기관 미확인'))
      }
    }
    await checkboxes.nth(0).check()
    await checkboxes.nth(1).check()
    await compare([0, 1])
    await page.getByRole('button', { name: '비교 닫기', exact: true }).click()
    await checkboxes.nth(0).uncheck()
    await checkboxes.nth(2).check()
    await compare([1, 2])
    await page.getByRole('button', { name: '비교 닫기', exact: true }).click()
    await checkboxes.nth(0).check()
    await compare([1, 2, 0])
    await page.screenshot({ path: path.join(output, 'compare.png'), fullPage: true })
    report.checks.push('two and three real courses compare; close, deselect and reselect use current IDs')
  }
  for (const [suffix, expected] of [
    [`/${courseIds[0]}`, 200],
    ...(courseIds.length >= 2 ? [[`/compare?ids=${courseIds.slice(0, 3).join(',')}`, 200]] : []),
    [`/compare?ids=${courseIds[0]},00000000-0000-4000-8000-000000000000`, 404],
    [`/compare?ids=${courseIds[0]},${courseIds[0]}`, 422],
    ['/compare?ids=invalid', 422],
  ]) {
    const response = await page.goto(`${config.origin}/api/courses${suffix}`)
    assert.equal(response.status(), expected, suffix)
  }
  report.checks.push('direct browser detail/compare URLs, missing IDs and invalid/duplicate inputs')
  report.status = 'passed'
} catch (error) {
  report.status = 'failed'
  report.failure = String(error)
  throw error
} finally {
  if (created) await unlink(harness)
  await writeFile(path.join(output, 'course-browser.json'), JSON.stringify(report, null, 2))
  await context.close()
  await browser.close()
}
console.log(JSON.stringify({ status: report.status, checks: report.checks }))
