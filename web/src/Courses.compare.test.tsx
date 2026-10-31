import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { Results } from './Courses'
import { courseDetailFixture } from './Courses.fixtures'
import type { CourseDetail, CourseSummary, RecommendationRun } from './types'

// Synthetic L contracts; these are not rows from the real catalog database.
const ids = ['a', 'b', 'c', 'd'].map(
  (prefix, i) => `${prefix}0000000-0000-4000-8000-00000000000${i + 1}`,
)
const details: CourseDetail[] = ids.map((id, i) => ({
  ...courseDetailFixture,
  id,
  name: `합성 교육 ${i + 1}`,
  source_system: 'work24',
  course_type: 'vocational_training',
  evidence: courseDetailFixture.evidence.map((item) => ({
    ...item,
    source_system: 'work24' as const,
  })),
  detail_url: `https://example.com/course/${i + 1}`,
  offerings: [
    {
      ...courseDetailFixture.offerings[0],
      id: `synthetic-offering-${i + 1}`,
      tuition_krw: i === 0 ? 0 : null,
      listed_self_pay_krw: null,
    },
  ],
}))
function summary(detail: CourseDetail): CourseSummary {
  return {
    id: detail.id,
    name: detail.name,
    provider_name: detail.provider_name,
    field_code: detail.field_code,
    source_system: detail.source_system,
    course_type: detail.course_type,
    evidence_level: detail.evidence_level,
    detail_url: detail.detail_url,
    is_demo: detail.is_demo,
  }
}
function run(courses = details): RecommendationRun {
  return {
    id: '00000000-0000-4000-8000-000000000009',
    profile_revision: 4,
    catalog_version_id: '00000000-0000-4000-8000-000000000008',
    result_state: 'matches',
    explanation_mode: 'llm',
    items: courses.map((course, i) => ({
      course: summary(course),
      offering: null,
      rank: i + 1,
      match_state: 'verified_conditions',
      reasons: [],
      evidence: [],
      missing_fields: [],
      rank_breakdown: {
        goal_basis: 'content_supported',
        score_lower: 0.5,
        score_upper: 1,
        coverage: 0.5,
        axes: [],
        constraint_checks: [],
        ranking_config_version: 'synthetic-ranking',
      },
    })),
    notices: [],
    excluded_counts: {},
    created_at: '2026-11-01T00:00:00Z',
    profile_id: '00000000-0000-4000-8000-000000000007',
    profile_snapshot: {},
    model_id: 'synthetic-model',
    runtime_mode: 'real_model',
    model_outcome: 'succeeded',
    pipeline_version: 'synthetic-pipeline',
    ranking_config_version: 'synthetic-ranking',
    retrieval_config_version: 'synthetic-retrieval',
    as_of: '2026-11-01T00:00:00Z',
    needs: [],
    stages: [],
  }
}
function serve(
  handler: (ids: string[], init: RequestInit) => Response | Promise<Response>,
  courses = details,
) {
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const parsed = new URL(url)
    if (parsed.pathname.startsWith('/api/recommendations/'))
      return Promise.resolve(Response.json(run(courses)))
    expect(parsed.pathname).toBe('/api/courses/compare')
    return Promise.resolve(
      handler(parsed.searchParams.get('ids')!.split(','), init),
    )
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
async function choose(...indices: number[]) {
  for (const i of indices)
    fireEvent.click(
      await screen.findByRole('checkbox', {
        name: `${details[i].name} 비교에 추가`,
      }),
    )
}
function compare() {
  return screen.getByRole('button', { name: '선택한 교육 비교' })
}
function panel() {
  return screen.getByRole('region', { name: '교육 비교 결과' })
}
function compareCalls(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls.filter(
    ([url]) => new URL(url).pathname === '/api/courses/compare',
  )
}
beforeEach(() => vi.stubEnv('VITE_API_BASE_URL', '/api'))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('비교 선택과 입력 계약', () => {
  it('0·1개는 비교하지 않고 최대 3개만 선택해 CSV GET으로 요청한다', async () => {
    const fetch = serve((selected, init) => {
      expect(selected).toEqual([ids[0], ids[1], ids[2]])
      expect(init.method).toBe('GET')
      return Response.json({ items: details.slice(0, 3) })
    })
    render(<Results runId={run().id} />)
    await screen.findByRole('checkbox', { name: '합성 교육 1 비교에 추가' })
    expect(compare().hasAttribute('disabled')).toBe(true)
    await choose(0)
    expect(compare().hasAttribute('disabled')).toBe(true)
    await choose(1, 2)
    expect(
      screen
        .getByRole('checkbox', { name: '합성 교육 4 비교에 추가' })
        .hasAttribute('disabled'),
    ).toBe(true)
    expect(compareCalls(fetch)).toHaveLength(0)
    fireEvent.click(compare())
    await within(panel()).findByRole('heading', { name: details[2].name })
    expect(compareCalls(fetch)).toHaveLength(1)
  })

  it.each(['', 'not-a-uuid', `${ids[0]},${ids[1]}`])(
    '잘못된 UUID %j를 비교 API로 보내지 않는다',
    async (id) => {
      const courses = [{ ...details[0], id }, details[1]]
      const fetch = serve(() => {
        throw new Error('Must not request invalid IDs')
      }, courses)
      render(<Results runId={run().id} />)
      await choose(0, 1)
      fireEvent.click(compare())
      expect((await screen.findByRole('alert')).textContent).toContain(
        '선택한 교육 정보를 확인할 수 없어',
      )
      expect(compareCalls(fetch)).toHaveLength(0)
    },
  )

  it('대소문자만 다른 UUID를 서로 다른 교육으로 비교하지 않는다', async () => {
    const fetch = serve(() => {
      throw new Error('Must not request duplicate UUIDs')
    }, [details[0], { ...details[1], id: ids[0].toUpperCase() }])
    render(<Results runId={run().id} />)
    await choose(0, 1)
    fireEvent.click(compare())
    await screen.findByRole('alert')
    expect(compareCalls(fetch)).toHaveLength(0)
  })
})

describe('비교 표시·해제·복구', () => {
  it('응답 순서가 달라도 선택 순서로 같은 항목과 결측을 비교한다', async () => {
    serve(() => Response.json({ items: [details[1], details[0]] }))
    render(<Results runId={run().id} />)
    await choose(0, 1)
    fireEvent.click(compare())
    await within(panel()).findByRole('heading', { name: details[0].name })
    const names = within(panel())
      .getAllByRole('heading', { level: 3 })
      .map((node) => node.textContent)
    expect(names).toEqual([details[0].name, details[1].name])
    const columns = within(panel()).getAllByRole('article')
    expect(within(columns[0]).getByText('0원 / 미확인')).toBeTruthy()
    expect(within(columns[1]).getByText('미확인 / 미확인')).toBeTruthy()
    expect(
      within(columns[0]).getByText('목록 정보 기준 · 상세 확인 필요'),
    ).toBeTruthy()
  })

  it('UUID 표기 대소문자가 달라도 같은 선택으로 확인한다', async () => {
    serve(() =>
      Response.json({
        items: details
          .slice(0, 2)
          .map((course) => ({ ...course, id: course.id.toUpperCase() })),
      }),
    )
    render(<Results runId={run().id} />)
    await choose(0, 1)
    fireEvent.click(compare())
    await within(panel()).findByRole('heading', { name: details[0].name })
    expect(within(panel()).queryByRole('alert')).toBeNull()
  })

  it('비교 닫기는 선택을 유지하고 선택 초기화는 체크와 결과를 지운다', async () => {
    const fetch = serve(() => Response.json({ items: details.slice(0, 2) }))
    render(<Results runId={run().id} />)
    await choose(0, 1)
    fireEvent.click(compare())
    await within(panel()).findByRole('heading', { name: details[0].name })
    fireEvent.click(within(panel()).getByRole('button', { name: '비교 닫기' }))
    expect(screen.queryByRole('region', { name: '교육 비교 결과' })).toBeNull()
    expect(
      (
        screen.getByRole('checkbox', {
          name: '합성 교육 1 비교에 추가',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true)
    fireEvent.click(compare())
    await within(panel()).findByRole('heading', { name: details[0].name })
    fireEvent.click(screen.getByRole('button', { name: '선택 초기화' }))
    expect(screen.queryByRole('region', { name: '교육 비교 결과' })).toBeNull()
    expect(
      screen
        .getAllByRole('checkbox')
        .every((node) => !(node as HTMLInputElement).checked),
    ).toBe(true)
    expect(compare().hasAttribute('disabled')).toBe(true)
    expect(compareCalls(fetch)).toHaveLength(2)
  })

  it.each([404, 422, 503])(
    '%i 후 같은 선택으로 GET을 재시도한다',
    async (status) => {
      let attempts = 0
      const fetch = serve((selected) => {
        expect(selected).toEqual(ids.slice(0, 2))
        return ++attempts === 1
          ? Response.json({}, { status })
          : Response.json({ items: details.slice(0, 2) })
      })
      render(<Results runId={run().id} />)
      await choose(0, 1)
      fireEvent.click(compare())
      await within(panel()).findByRole('alert')
      fireEvent.click(
        within(panel()).getByRole('button', { name: '다시 불러오기' }),
      )
      await within(panel()).findByRole('heading', { name: details[0].name })
      expect(compareCalls(fetch)).toHaveLength(2)
      expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(
        true,
      )
    },
  )

  it.each(
    [
      [details[0], details[2]],
      [details[0], details[0]],
      [details[0], { ...details[0], id: ids[0].toUpperCase() }],
      [],
      [details[0]],
    ].map((items) => ({ items })),
  )(
    '다른/중복/누락 결과를 표시하지 않고 재조회할 수 있다 (%#)',
    async ({ items }) => {
      let attempts = 0
      serve(() =>
        Response.json({
          items: ++attempts === 1 ? items : details.slice(0, 2),
        }),
      )
      render(<Results runId={run().id} />)
      await choose(0, 1)
      fireEvent.click(compare())
      await within(panel()).findByRole('alert')
      expect(
        within(panel()).queryByRole('heading', { name: details[0].name }),
      ).toBeNull()
      fireEvent.click(
        within(panel()).getByRole('button', { name: '다시 불러오기' }),
      )
      await within(panel()).findByRole('heading', { name: details[0].name })
      expect(attempts).toBe(2)
    },
  )

  it('선택 변경 후 이전 비교 응답이 늦게 도착해도 새 선택을 덮어쓰지 않는다', async () => {
    let complete!: (response: Response) => void
    let oldSignal: AbortSignal | null | undefined
    const fetch = serve((selected, init) => {
      if (selected.includes(ids[0])) {
        oldSignal = init.signal
        return new Promise<Response>((resolve) => {
          complete = resolve
        })
      }
      expect(selected).toEqual([ids[1], ids[2]])
      return Response.json({ items: [details[1], details[2]] })
    })
    render(<Results runId={run().id} />)
    await choose(0, 1)
    fireEvent.click(compare())
    await within(panel()).findByRole('status')
    await choose(0, 2)
    expect(oldSignal?.aborted).toBe(true)
    expect(screen.queryByRole('region', { name: '교육 비교 결과' })).toBeNull()
    fireEvent.click(compare())
    await within(panel()).findByRole('heading', { name: details[2].name })
    await act(async () =>
      complete(Response.json({ items: details.slice(0, 2) })),
    )
    expect(
      within(panel()).queryByRole('heading', { name: details[0].name }),
    ).toBeNull()
    expect(compareCalls(fetch)).toHaveLength(2)
  })
})
