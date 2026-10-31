import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { setCsrfToken } from './api'
import { Intake } from './Intake'
import type { Interpretation, Job, OnboardingOptions, Profile } from './types'

// Synthetic consumer fixtures: no real API storage or model inference.
const profileId = '00000000-0000-4000-8000-000000000001'
const interpretationId = '00000000-0000-4000-8000-000000000002'
const jobId = '00000000-0000-4000-8000-000000000003'
const profile: Profile = {
  id: profileId,
  revision: 1,
  status: 'draft',
  answers: { availability: 'evening_weekend' },
  reviewed_items: [],
  documents: [],
  completed_sections: [],
  review_token: null,
  updated_at: '2026-11-01T00:00:00Z',
  slot_origins: {},
  unresolved_conflicts: [],
  latest_interpretation_id: interpretationId,
  selection_summaries: [],
  intake_state: {
    section: 'experience',
    next_action: 'collect_documents',
    eligible_slots: [],
    missing_slots: [],
    completed_sections: [],
    active_question_id: null,
  },
}
const interpretation: Interpretation = {
  id: interpretationId,
  profile_id: profileId,
  base_revision: 1,
  input_hash: 'synthetic-input-hash',
  status: 'ready',
  assistant_text: '원문과 제안이 맞는지 각각 확인해 주세요.',
  proposals: [
    {
      id: '00000000-0000-4000-8000-000000000004',
      slot: 'constraints_text',
      value: { kind: 'text', text: '주말에만 참여 가능' },
      label: '참여 시간 확인',
      source_type: 'user_text',
      document_id: null,
      chunk_id: null,
      quote: '주말에만 배울 수 있어요.',
      status: 'conflict',
      conflicts_with: 'availability',
    },
    {
      id: '00000000-0000-4000-8000-000000000005',
      slot: 'budget_krw',
      value: { kind: 'integer', integer: 10000 },
      label: '예산 확인',
      source_type: 'user_text',
      document_id: null,
      chunk_id: null,
      quote: '예산은 만 원이에요.',
      status: 'proposed',
      conflicts_with: null,
    },
  ],
  model_id: 'synthetic-model',
  prompt_version: '1',
  created_at: '2026-11-01T00:00:00Z',
  suggested_slots: [],
}
const options: OnboardingOptions = {
  schema_version: '0.8',
  fields: [],
  entry_intents: [],
  allowed_file_extensions: [],
  max_upload_bytes: 1024,
  max_profile_documents: 5,
  upload_limit_note: '합성 제한',
  branch_rules_version: '1',
  intake_policy_version: '1',
  allowed_catalog_sources: ['work24'],
  interpretation_enabled: true,
  question_generation_enabled: true,
  question_schema_version: '1',
}
const job: Job = {
  id: jobId,
  kind: 'intake_interpretation',
  status: 'succeeded',
  phase: 'completed',
  profile_id: profileId,
  result: { interpretation_id: interpretationId },
  error: null,
  created_at: '2026-11-01T00:00:00Z',
  finished_at: '2026-11-01T00:00:01Z',
  completed_stages: [],
}
const lookupPath = `/api/interpretations/${interpretationId}`
const profilePath = `/api/profiles/${profileId}`
const applyPath = `${profilePath}/interpretations/${interpretationId}/apply`
const changed = vi.fn()
function Harness({ initial = profile }: { initial?: Profile }) {
  const [current, setCurrent] = useState(initial)
  return (
    <Intake
      profile={current}
      onProfile={(next) => {
        changed(next)
        setCurrent(next)
      }}
      onRun={() => {}}
    />
  )
}
function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/api/onboarding/options') return Response.json(options)
    return handler(path, init)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function mutations(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls.filter(([, init]) => init.method === 'POST')
}
function submit() {
  return screen.getByRole('button', {
    name: '확인한 제안만 반영',
  }) as HTMLButtonElement
}
function choose(first: '맞아요' | '반영하지 않을게요' = '맞아요') {
  fireEvent.click(
    within(screen.getByRole('group', { name: /참여 시간 확인/ })).getByRole(
      'radio',
      { name: first },
    ),
  )
  fireEvent.click(
    within(screen.getByRole('group', { name: '예산 확인' })).getByRole(
      'radio',
      { name: '반영하지 않을게요' },
    ),
  )
}
function draft() {
  fireEvent.click(screen.getByText('직접 알려줄게요'))
  const input = screen.getByLabelText(
    '내 경험이나 원하는 배움',
  ) as HTMLTextAreaElement
  fireEvent.change(input, { target: { value: '보존할 새 문장' } })
  return input
}
function deferred() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
beforeEach(() => {
  changed.mockClear()
  sessionStorage.clear()
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  setCsrfToken('synthetic-csrf')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
  sessionStorage.clear()
})

describe('해석 제안 확인과 수락·거절', () => {
  it('원문·상충·값을 표시하고 모든 제안을 확인한 뒤 ID 분할만 전송한다', async () => {
    const fetch = serve((path) => {
      if (path === lookupPath) return Response.json(interpretation)
      if (path === applyPath) return Response.json({ ...profile, revision: 2 })
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await screen.findByText('주말에만 배울 수 있어요.')
    expect(screen.getByText('주말에만 참여 가능')).toBeTruthy()
    expect(screen.getByText('10000')).toBeTruthy()
    expect(
      screen.getByRole('group', { name: '참여 시간 확인 · 기존 답변과 상충' }),
    ).toBeTruthy()
    expect(changed).not.toHaveBeenCalled()
    expect(mutations(fetch)).toHaveLength(0)
    expect(submit().disabled).toBe(true)
    fireEvent.click(
      within(screen.getByRole('group', { name: /참여 시간 확인/ })).getByRole(
        'radio',
        { name: '맞아요' },
      ),
    )
    expect(submit().disabled).toBe(true)
    choose()
    fireEvent.click(submit())
    await screen.findByText('제안 확인 결과를 저장했어요.')
    const [[url, init]] = mutations(fetch)
    expect(new URL(url).pathname).toBe(applyPath)
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: 1,
      accepted_proposal_ids: [interpretation.proposals[0].id],
      rejected_proposal_ids: [interpretation.proposals[1].id],
    })
    expect(init.credentials).toBe('include')
    const headers = new Headers(init.headers)
    expect(headers.get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(headers.get('Idempotency-Key')).toBeTruthy()
    expect(changed).toHaveBeenCalledWith({ ...profile, revision: 2 })
    expect(screen.queryByRole('region', { name: '입력 해석 확인' })).toBeNull()
  })

  it('모두 거절해도 빈 수락 목록과 전체 거절 목록을 보낸다', async () => {
    const fetch = serve((path) =>
      Response.json(
        path === lookupPath ? interpretation : { ...profile, revision: 2 },
      ),
    )
    render(<Harness />)
    await screen.findByText(interpretation.assistant_text)
    choose('반영하지 않을게요')
    fireEvent.click(submit())
    await screen.findByText('제안 확인 결과를 저장했어요.')
    expect(JSON.parse(mutations(fetch)[0][1].body as string)).toEqual({
      expected_revision: 1,
      accepted_proposal_ids: [],
      rejected_proposal_ids: interpretation.proposals.map((item) => item.id),
    })
  })

  it.each(['stale', 'failed'] as const)(
    '%s 해석은 표시하되 반영을 막는다',
    async (status) => {
      const fetch = serve(() => Response.json({ ...interpretation, status }))
      render(<Harness />)
      await screen.findByText(
        '이 해석은 현재 입력에 적용할 수 없어요. 다시 해석해 주세요.',
      )
      expect(submit().disabled).toBe(true)
      expect(
        (
          screen.getByRole('group', {
            name: /참여 시간 확인/,
          }) as HTMLFieldSetElement
        ).disabled,
      ).toBe(true)
      expect(mutations(fetch)).toHaveLength(0)
    },
  )

  it('이미 적용한 해석은 다시 확인하거나 전송하지 않는다', async () => {
    const fetch = serve(() =>
      Response.json({ ...interpretation, status: 'applied' }),
    )
    render(<Harness />)
    await waitFor(() =>
      expect(screen.queryByText('저장된 해석을 불러오고 있어요…')).toBeNull(),
    )
    expect(screen.queryByRole('region', { name: '입력 해석 확인' })).toBeNull()
    expect(mutations(fetch)).toHaveLength(0)
  })

  it('빈 제안은 빈 결과 안내를 제공하고 반영하지 않는다', async () => {
    const fetch = serve(() =>
      Response.json({ ...interpretation, proposals: [] }),
    )
    render(<Harness />)
    await screen.findByText(
      '반영할 해석 제안이 없어요. 내용을 보완해 다시 해석해 주세요.',
    )
    expect(submit().disabled).toBe(true)
    expect(mutations(fetch)).toHaveLength(0)
  })

  it('조회 실패 후 같은 ID의 GET만 다시 실행한다', async () => {
    let attempts = 0
    const fetch = serve((path) => {
      expect(path).toBe(lookupPath)
      return ++attempts === 1
        ? new Response(null, { status: 503 })
        : Response.json(interpretation)
    })
    render(<Harness />)
    fireEvent.click(
      await screen.findByRole('button', { name: '저장된 해석 다시 불러오기' }),
    )
    await screen.findByText(interpretation.assistant_text)
    expect(attempts).toBe(2)
    expect(mutations(fetch)).toHaveLength(0)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([
    ['다른 해석 ID', { ...interpretation, id: jobId }],
    ['다른 프로필', { ...interpretation, profile_id: jobId }],
    [
      '중복 제안 ID',
      {
        ...interpretation,
        proposals: [interpretation.proposals[0], interpretation.proposals[0]],
      },
    ],
    [
      '빈 제안 ID',
      {
        ...interpretation,
        proposals: [{ ...interpretation.proposals[0], id: '' }],
      },
    ],
    [
      '잘못된 값',
      {
        ...interpretation,
        proposals: [{ ...interpretation.proposals[0], value: null }],
      },
    ],
    ['제안 배열 누락', { ...interpretation, proposals: null }],
    ['잘못된 revision', { ...interpretation, base_revision: -1 }],
  ])('%s 응답은 수락 화면에 사용하지 않는다', async (_, response) => {
    const fetch = serve(() => Response.json(response))
    render(<Harness />)
    await screen.findByText('해석 응답이 요청한 입력과 맞지 않아요.')
    expect(screen.queryByRole('region', { name: '입력 해석 확인' })).toBeNull()
    expect(mutations(fetch)).toHaveLength(0)
  })

  it('이전 revision 해석은 현재 입력에 표시하지 않는다', async () => {
    serve(() => Response.json(interpretation))
    render(<Harness initial={{ ...profile, revision: 2 }} />)
    await screen.findByText(
      '입력이 바뀌어 이전 해석을 표시하지 않았어요. 필요한 경우 다시 해석해 주세요.',
    )
    expect(screen.queryByRole('region', { name: '입력 해석 확인' })).toBeNull()
  })

  it('503과 중복 클릭 후에도 선택·문장을 유지하고 같은 멱등 키로 재시도한다', async () => {
    const pending = deferred()
    let attempts = 0
    const fetch = serve((path) => {
      if (path === lookupPath) return Response.json(interpretation)
      if (path === applyPath)
        return ++attempts === 1
          ? pending.promise
          : Response.json({ ...profile, revision: 2 })
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await screen.findByText(interpretation.assistant_text)
    const input = draft()
    choose()
    const button = submit()
    fireEvent.click(button)
    fireEvent.click(button)
    expect(mutations(fetch)).toHaveLength(1)
    await act(async () => pending.resolve(new Response(null, { status: 503 })))
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    expect(submit().disabled).toBe(false)
    expect(input.value).toBe('보존할 새 문장')
    expect(changed).not.toHaveBeenCalled()
    fireEvent.click(submit())
    await screen.findByText('제안 확인 결과를 저장했어요.')
    const [first, second] = mutations(fetch).map(([, init]) => init)
    expect(second.body).toBe(first.body)
    expect(new Headers(second.headers).get('Idempotency-Key')).toBe(
      new Headers(first.headers).get('Idempotency-Key'),
    )
    expect(input.value).toBe('보존할 새 문장')
  })

  it.each([409, 200])(
    '%s 적용 응답이 revision을 갱신하지 못하면 성공 처리하지 않는다',
    async (status) => {
      const fetch = serve((path) => {
        if (path === lookupPath) return Response.json(interpretation)
        if (path === applyPath)
          return status === 409
            ? new Response(null, { status })
            : Response.json(profile)
        if (path === profilePath)
          return Response.json({ ...profile, revision: 2 })
        throw new Error(`Unexpected ${path}`)
      })
      render(<Harness />)
      await screen.findByText(interpretation.assistant_text)
      const input = draft()
      choose()
      fireEvent.click(submit())
      await screen.findByText(
        '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
      )
      expect(submit().disabled).toBe(true)
      expect(input.value).toBe('보존할 새 문장')
      expect(screen.queryByText('제안 확인 결과를 저장했어요.')).toBeNull()
      expect(mutations(fetch)).toHaveLength(1)
      expect(changed).toHaveBeenCalledTimes(1)
      expect(changed).toHaveBeenCalledWith({ ...profile, revision: 2 })
    },
  )

  it.each([false, true])(
    '완료 작업의 해석 ID 일치 여부 %s를 확인한다',
    async (matches) => {
      const initial = { ...profile, latest_interpretation_id: null }
      const fetch = serve((path) => {
        if (path === `${profilePath}/interpretations`)
          return Response.json({
            job_id: jobId,
            status: 'queued',
            poll_after_ms: 1000,
          })
        if (path === `/api/jobs/${jobId}`) return Response.json(job)
        if (path === profilePath) return Response.json(initial)
        if (path === lookupPath)
          return Response.json({
            ...interpretation,
            id: matches ? interpretationId : jobId,
          })
        throw new Error(`Unexpected ${path}`)
      })
      render(<Harness initial={initial} />)
      await screen.findByText('직접 알려줄게요')
      draft()
      fireEvent.click(
        screen.getByRole('button', { name: '입력과 준비된 자료 해석하기' }),
      )
      if (matches) await screen.findByText(interpretation.assistant_text)
      else {
        await screen.findByText('해석 응답이 요청한 입력과 맞지 않아요.')
        expect(
          screen.queryByRole('region', { name: '입력 해석 확인' }),
        ).toBeNull()
      }
      expect(mutations(fetch)).toHaveLength(1)
      expect(JSON.parse(mutations(fetch)[0][1].body as string)).toEqual({
        expected_revision: 1,
        text: '보존할 새 문장',
        ready_document_ids: [],
      })
      expect(changed).toHaveBeenCalledWith(initial)
    },
  )

  it('새 해석을 요청한 뒤 이전 해석 조회가 늦게 와도 새 제안을 유지한다', async () => {
    const pending = deferred()
    const nextId = '00000000-0000-4000-8000-000000000006'
    serve((path) => {
      if (path === lookupPath) return pending.promise
      if (path === `${profilePath}/interpretations`)
        return Response.json({
          job_id: jobId,
          status: 'queued',
          poll_after_ms: 1000,
        })
      if (path === `/api/jobs/${jobId}`)
        return Response.json({ ...job, result: { interpretation_id: nextId } })
      if (path === profilePath) return Response.json(profile)
      if (path === `/api/interpretations/${nextId}`)
        return Response.json({
          ...interpretation,
          id: nextId,
          assistant_text: '새 해석 확인',
        })
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await screen.findByText('직접 알려줄게요')
    draft()
    fireEvent.click(
      screen.getByRole('button', { name: '입력과 준비된 자료 해석하기' }),
    )
    await screen.findByText('새 해석 확인')
    await act(async () => pending.resolve(Response.json(interpretation)))
    expect(screen.getByText('새 해석 확인')).toBeTruthy()
    expect(screen.queryByText(interpretation.assistant_text)).toBeNull()
    expect(screen.queryByText('저장된 해석을 불러오고 있어요…')).toBeNull()
  })

  it('화면 이동 뒤 늦은 조회가 다음 프로필의 제안을 덮어쓰지 않는다', async () => {
    const pending = deferred()
    serve((path) =>
      path === lookupPath ? pending.promise : Response.json(interpretation),
    )
    const old = render(<Harness />)
    expect(screen.getByText('저장된 해석을 불러오고 있어요…')).toBeTruthy()
    old.unmount()
    render(
      <Harness
        initial={{ ...profile, id: jobId, latest_interpretation_id: null }}
      />,
    )
    await act(async () => pending.resolve(Response.json(interpretation)))
    expect(screen.queryByRole('region', { name: '입력 해석 확인' })).toBeNull()
    expect(changed).not.toHaveBeenCalled()
  })
})
