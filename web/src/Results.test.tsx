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
import { courseDetailFixture, savedCoursesFixture } from './Courses.fixtures'
import type { RecommendationItem, RecommendationRun } from './types'

// Synthetic L response contracts, never a production result fallback.
const runId = '00000000-0000-4000-8000-000000000001'
const item: RecommendationItem = {
  course: {
    ...savedCoursesFixture.items[0],
    id: '00000000-0000-4000-8000-000000000002',
    name: '합성 추천 교육',
    source_system: 'work24',
  },
  offering: courseDetailFixture.offerings[0],
  rank: 1,
  match_state: 'unknown_conditions',
  reasons: [
    {
      text: '서버가 제공한 합성 추천 이유',
      answer_keys: ['learning_goal'],
      evidence_ids: ['synthetic-evidence'],
    },
  ],
  evidence: [
    {
      ...courseDetailFixture.evidence[0],
      id: 'synthetic-evidence',
      source_system: 'work24',
      quote: '합성 교육 원문 근거',
    },
  ],
  missing_fields: ['listed_self_pay_krw'],
  rank_breakdown: {
    goal_basis: 'content_supported',
    score_lower: 0.65,
    score_upper: 0.9,
    coverage: 0.75,
    axes: [],
    ranking_config_version: 'synthetic-ranking',
    constraint_checks: [
      {
        field: 'mode',
        status: 'pass',
        reason_code: 'MODE_MATCH',
        evidence_ids: ['synthetic-evidence'],
      },
      {
        field: 'budget',
        status: 'unknown',
        reason_code: 'PERSONAL_PRICE_UNKNOWN',
        evidence_ids: [],
      },
      {
        field: 'region',
        status: 'not_applicable',
        reason_code: 'NO_COMMUTE',
        evidence_ids: [],
      },
    ],
  },
}
const run: RecommendationRun = {
  id: runId,
  profile_revision: 4,
  catalog_version_id: '00000000-0000-4000-8000-000000000003',
  result_state: 'unknown_conditions',
  explanation_mode: 'llm',
  items: [item],
  notices: ['합성 카탈로그 안내'],
  excluded_counts: {},
  created_at: '2026-11-01T00:00:00Z',
  profile_id: '00000000-0000-4000-8000-000000000004',
  profile_snapshot: { learning_goal: 'foundation' },
  model_id: 'synthetic-model',
  runtime_mode: 'real_model',
  model_outcome: 'succeeded',
  pipeline_version: 'synthetic-pipeline',
  ranking_config_version: 'synthetic-ranking',
  retrieval_config_version: 'synthetic-retrieval',
  as_of: '2026-10-31T00:00:00Z',
  needs: [],
  stages: [],
}
function emptyRun(
  state: 'empty' | 'unsupported_field' = 'empty',
): RecommendationRun {
  return {
    ...run,
    result_state: state,
    items: [],
    model_outcome: 'not_needed',
    explanation_mode: 'none',
  }
}
function respond(value: unknown, status = 200) {
  const fetch = vi.fn(() => Promise.resolve(Response.json(value, { status })))
  vi.stubGlobal('fetch', fetch)
  return fetch
}
beforeEach(() => vi.stubEnv('VITE_API_BASE_URL', '/api'))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('추천 결과와 참여 조건 표시', () => {
  it('근거 확인 시점이 없으면 추천 기준일로 대신 표시하지 않는다', async () => {
    respond({
      ...run,
      items: [
        { ...item, evidence: [{ ...item.evidence[0], checked_at: null }] },
      ],
    })
    render(<Results runId={runId} />)
    await screen.findByRole('heading', { name: item.course.name })
    fireEvent.click(screen.getByText('추천 근거 확인'))
    const evidence = screen.getByText('합성 교육 원문 근거').closest('li')!
    expect(
      within(evidence).getByText(/work24 · observed · 미확인/),
    ).toBeTruthy()
    expect(within(evidence).queryByText(run.as_of)).toBeNull()
    expect(screen.getByText(`추천 기준일: ${run.as_of}`)).toBeTruthy()
  })

  it('서버의 충족·미확인·해당 없음을 구분하고 근거·기준 시점을 보존한다', async () => {
    respond(run)
    render(<Results runId={runId} />)
    await screen.findByRole('heading', { name: '일부 조건 확인이 필요한 교육' })
    const conditions = screen.getByRole('region', { name: '참여 조건 확인' })
    for (const [label, state] of [
      ['교육 방식', '충족'],
      ['예산', '미확인'],
      ['통학 지역', '해당 없음'],
    ]) {
      const row = within(conditions).getByText(label).parentElement!
      expect(within(row).getByText(state)).toBeTruthy()
    }
    expect(
      screen.getByText('미확인 조건은 교육 원문에서 확인해 주세요.'),
    ).toBeTruthy()
    expect(screen.getByText('서버가 제공한 합성 추천 이유')).toBeTruthy()
    expect(screen.getByText('추천 기준일: 2026-10-31T00:00:00Z')).toBeTruthy()
    expect(screen.getByText('미확인 항목: listed_self_pay_krw')).toBeTruthy()
    fireEvent.click(screen.getByText('추천 근거 확인'))
    expect(screen.getByText('합성 교육 원문 근거')).toBeTruthy()
    // Ranking bounds/coverage are not a probability or learner ability score.
    expect(screen.queryByText(/65%|90%|75%|0\.65/)).toBeNull()
    expect(screen.queryByRole('button', { name: '다음 질문 받기' })).toBeNull()
  })

  it('확인된 조건의 추천 결과를 표시한다', async () => {
    respond({
      ...run,
      result_state: 'matches',
      items: [
        {
          ...item,
          match_state: 'verified_conditions',
          rank_breakdown: {
            ...item.rank_breakdown,
            constraint_checks: item.rank_breakdown.constraint_checks.filter(
              (check) => check.status !== 'unknown',
            ),
          },
        },
      ],
    })
    render(<Results runId={runId} />)
    await screen.findByRole('heading', { name: '확인된 조건에 맞는 교육' })
    expect(screen.getByText('확인된 조건 일치')).toBeTruthy()
  })

  it.each([
    ['empty', '조건에 맞는 교육을 찾지 못했습니다.'],
    ['unsupported_field', '현재 제공되는 교육 분야에 포함되지 않습니다.'],
  ] as const)(
    '%s는 오류와 구분하고 임의 교육을 채우지 않는다',
    async (state, label) => {
      respond(emptyRun(state))
      render(<Results runId={runId} />)
      await screen.findByRole('heading', { name: label })
      expect(
        screen.getByText('모델 평가가 필요하지 않은 검색 결과입니다.'),
      ).toBeTruthy()
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.queryByRole('button', { name: '교육 상세' })).toBeNull()
    },
  )

  it('모델 실행 후 최종 후보가 없는 응답도 미실행으로 바꾸지 않는다', async () => {
    respond({ ...emptyRun(), model_outcome: 'succeeded' })
    render(<Results runId={runId} />)
    await screen.findByRole('heading', {
      name: '조건에 맞는 교육을 찾지 못했습니다.',
    })
    expect(
      screen.queryByText('모델 평가가 필요하지 않은 검색 결과입니다.'),
    ).toBeNull()
  })

  it('제공된 조건 판정이 없으면 충족으로 추정하지 않는다', async () => {
    respond({
      ...run,
      items: [
        {
          ...item,
          rank_breakdown: { ...item.rank_breakdown, constraint_checks: [] },
        },
      ],
    })
    render(<Results runId={runId} />)
    await screen.findByText('제공된 조건 확인 정보가 없습니다.')
    expect(
      within(
        screen.getByRole('region', { name: '참여 조건 확인' }),
      ).queryByText('충족'),
    ).toBeNull()
  })
})

describe('모순·실패 응답과 재조회', () => {
  it('미확인 조건이 있는 항목을 확인된 조건 일치로 표시하지 않는다', async () => {
    respond({
      ...run,
      result_state: 'matches',
      items: [{ ...item, match_state: 'verified_conditions' }],
    })
    render(<Results runId={runId} />)
    await screen.findByRole('alert')
    expect(screen.queryByText('확인된 조건 일치')).toBeNull()
    expect(screen.queryByRole('heading', { name: item.course.name })).toBeNull()
    expect(screen.getByRole('button', { name: '다시 불러오기' })).toBeTruthy()
  })

  it.each([
    { ...run, result_state: 'empty' },
    { ...run, result_state: 'unsupported_field' },
    { ...run, result_state: 'matches', items: [] },
    { ...run, items: [] },
    { ...run, model_outcome: 'not_needed' },
    { ...emptyRun(), explanation_mode: 'llm' },
    { ...run, model_outcome: 'failed' },
  ])(
    '모순된 상태를 교육 카드나 빈 성공으로 표시하지 않는다 (%#)',
    async (value) => {
      respond(value)
      render(<Results runId={runId} />)
      await screen.findByRole('alert')
      expect(
        screen.queryByRole('heading', { name: item.course.name }),
      ).toBeNull()
      expect(screen.queryByText('표시할 추천 교육이 없습니다.')).toBeNull()
      expect(screen.getByRole('button', { name: '다시 불러오기' })).toBeTruthy()
    },
  )

  it.each(['fail', 'invented'])(
    '참여 조건 %s를 충족·미확인으로 바꾸거나 후보만 조용히 제거하지 않는다',
    async (status) => {
      respond({
        ...run,
        items: [
          {
            ...item,
            rank_breakdown: {
              ...item.rank_breakdown,
              constraint_checks: [
                {
                  field: 'budget',
                  status,
                  reason_code: 'SYNTHETIC_CONFLICT',
                  evidence_ids: [],
                },
              ],
            },
          },
        ],
      })
      render(<Results runId={runId} />)
      await screen.findByRole('alert')
      expect(
        screen.queryByRole('heading', { name: item.course.name }),
      ).toBeNull()
      expect(
        screen.queryByRole('heading', {
          name: '조건에 맞는 교육을 찾지 못했습니다.',
        }),
      ).toBeNull()
    },
  )

  it.each([
    { ...run, runtime_mode: 'sample' },
    { ...run, items: [{ ...item, course: { ...item.course, is_demo: true } }] },
  ])('샘플/가상 데이터 거부를 유지한다 (%#)', async (value) => {
    respond(value)
    render(<Results runId={runId} />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('heading', { name: item.course.name })).toBeNull()
  })

  it('503 후 같은 저장 결과의 GET만 재시도하고 submit을 다시 보내지 않는다', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}, { status: 503 }))
      .mockImplementation(() => Promise.resolve(Response.json(run)))
    vi.stubGlobal('fetch', fetch)
    render(<Results runId={runId} />)
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await screen.findByRole('heading', { name: item.course.name })
    expect(fetch).toHaveBeenCalledTimes(2)
    for (const [url, init] of fetch.mock.calls) {
      expect(new URL(url).pathname).toBe(`/api/recommendations/${runId}`)
      expect(init.method).toBe('GET')
      expect(init.credentials).toBe('include')
    }
  })

  it('요청한 run ID와 다르면 표시하지 않고 동일 ID로 재조회할 수 있다', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ ...run, id: 'different-run' }))
      .mockImplementation(() => Promise.resolve(Response.json(run)))
    vi.stubGlobal('fetch', fetch)
    render(<Results runId={runId} />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      '요청한 추천 결과와 다릅니다.',
    )
    expect(screen.queryByRole('heading', { name: item.course.name })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await screen.findByRole('heading', { name: item.course.name })
  })

  it('다른 결과로 이동한 뒤 늦은 이전 결과 응답을 표시하지 않는다', async () => {
    const otherId = '00000000-0000-4000-8000-000000000005'
    let complete!: (response: Response) => void
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (new URL(url).pathname.endsWith(runId))
          return new Promise<Response>((resolve) => {
            complete = resolve
          })
        return Promise.resolve(
          Response.json({
            ...run,
            id: otherId,
            items: [
              {
                ...item,
                course: { ...item.course, name: '다른 합성 추천 교육' },
              },
            ],
          }),
        )
      }),
    )
    const view = render(<Results runId={runId} />)
    view.rerender(<Results runId={otherId} />)
    await screen.findByRole('heading', { name: '다른 합성 추천 교육' })
    await act(async () => complete(Response.json(run)))
    expect(screen.queryByRole('heading', { name: item.course.name })).toBeNull()
    expect(
      screen.getByRole('heading', { name: '다른 합성 추천 교육' }),
    ).toBeTruthy()
  })
})
