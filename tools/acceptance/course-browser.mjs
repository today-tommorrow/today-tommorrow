// Real course API/DB acceptance; only the recommendation selection scaffold is synthetic.
// Config is a private JSON file: {origin, web_root, cookies, course_ids}.
import assert from 'node:assert/strict'
import { readFile, writeFile, unlink, mkdir } from 'node:fs/promises'
import path from 'node:path'

const config = JSON.parse(await readFile(process.argv[2], 'utf8'))
const output = path.resolve(process.argv[3])
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const runId = '00000000-0000-4000-8000-000000000099'
assert.equal(config.course_ids.length, 3)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await context.addCookies(config.cookies)
const page = await context.newPage()
const report = {
  mode: 'Actual Chromium, real authenticated course API and PostgreSQL; synthetic recommendation selection scaffold only',
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
  const details = []
  for (const id of config.course_ids) {
    const response = await context.request.get(`${config.origin}/api/courses/${id}`)
    assert.equal(response.status(), 200)
    const course = await response.json()
    assert.equal(course.id, id)
    assert.equal(course.is_demo, false)
    assert(course.evidence.every((item) => item.checked_at === null))
    details.push(course)
  }
  report.checks.push('three real course details preserve IDs, real-data flag and unknown evidence time')
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
  await page.route(`**/api/recommendations/${runId}`, (route) => route.fulfill({ json: syntheticRun }))
  await writeFile(harness, `import React from 'react'; import {createRoot} from 'react-dom/client';\nimport {Results} from './src/Courses';\nimport './src/style.css';\ncreateRoot(document.getElementById('root')!).render(<Results runId='${runId}'/>);\n`, { flag: 'wx' })
  created = true
  await page.route('**/__course_browser_acceptance__', (route) => route.fulfill({
    contentType: 'text/html',
    body: `<html lang="ko"><body><div id="root"></div><script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;</script><script type="module" src="/.course-browser-harness.tsx"></script></body></html>`,
  }))
  await page.goto(`${config.origin}/__course_browser_acceptance__`)
  const checkboxes = page.getByRole('checkbox')
  await checkboxes.nth(2).waitFor()
  await page.getByRole('button', { name: '교육 상세', exact: true }).first().click()
  const detailPanel = page.getByRole('region', { name: '교육 상세', exact: true })
  await detailPanel.getByRole('heading', { name: details[0].name, exact: true }).waitFor()
  assert((await detailPanel.innerText()).includes('미확인'))
  if (details[0].evidence.length) assert((await detailPanel.innerText()).includes('· 미확인'))
  await page.screenshot({ path: path.join(output, 'detail.png'), fullPage: true })
  await page.getByRole('button', { name: '상세 닫기', exact: true }).click()
  report.checks.push('shipped CourseDetails renders actual detail and unknown evidence timestamp')
  await checkboxes.nth(0).check()
  await checkboxes.nth(1).check()
  await page.getByRole('button', { name: '선택한 교육 비교', exact: true }).click()
  const panel = page.getByRole('region', { name: '교육 비교 결과' })
  await panel.getByRole('heading', { name: details[1].name, exact: true }).waitFor()
  assert.equal(await panel.locator('article').count(), 2)
  await page.getByRole('button', { name: '비교 닫기', exact: true }).click()
  await checkboxes.nth(0).uncheck()
  await checkboxes.nth(2).check()
  await page.getByRole('button', { name: '선택한 교육 비교', exact: true }).click()
  await panel.getByRole('heading', { name: details[2].name, exact: true }).waitFor()
  assert.equal(await panel.getByRole('heading', { name: details[0].name, exact: true }).count(), 0)
  await page.getByRole('button', { name: '비교 닫기', exact: true }).click()
  await checkboxes.nth(0).check()
  await page.getByRole('button', { name: '선택한 교육 비교', exact: true }).click()
  await panel.getByRole('heading', { name: details[0].name, exact: true }).waitFor()
  assert.equal(await panel.locator('article').count(), 3)
  await page.screenshot({ path: path.join(output, 'compare.png'), fullPage: true })
  report.checks.push('two and three real courses compare; close, deselect and reselect use current IDs')
  for (const [suffix, expected] of [
    [`/${config.course_ids[0]}`, 200],
    [`/compare?ids=${config.course_ids.join(',')}`, 200],
    [`/compare?ids=${config.course_ids[0]},00000000-0000-4000-8000-000000000000`, 404],
    [`/compare?ids=${config.course_ids[0]},${config.course_ids[0]}`, 422],
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
