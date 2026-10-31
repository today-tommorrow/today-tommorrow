import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { setCsrfToken } from './api'
import { Intake } from './Intake'
import type { IntakeQuestion, Job, OnboardingOptions, Profile } from './types'

// Synthetic L fixtures; no live question generation or database persistence.
const profileId = '00000000-0000-4000-8000-000000000001'
const questionId = '00000000-0000-4000-8000-000000000002'
const optionId = '00000000-0000-4000-8000-000000000003'
const jobId = '00000000-0000-4000-8000-000000000004'
const profile: Profile = {
  id: profileId,
  revision: 1,
  status: 'draft',
  answers: {},
  reviewed_items: [],
  documents: [],
  completed_sections: [],
  review_token: null,
  updated_at: '2026-11-01T00:00:00Z',
  slot_origins: {},
  unresolved_conflicts: [],
  latest_interpretation_id: null,
  selection_summaries: [],
  intake_state: {
    section: 'direction',
    next_action: 'generate_question',
    eligible_slots: ['entry_intent'],
    missing_slots: ['entry_intent'],
    completed_sections: [],
    active_question_id: questionId,
  },
}
const question: IntakeQuestion = {
  id: questionId,
  profile_id: profileId,
  base_revision: 1,
  context_hash: 'synthetic-context',
  status: 'ready',
  origin: 'llm',
  schema_version: '1.0',
  target_slot: 'entry_intent',
  acknowledgement: '합성 질문입니다.',
  question: '어떤 마음으로 배움을 찾나요?',
  helper_text: '가까운 선택지를 골라주세요.',
  selection_mode: 'single',
  options: [
    {
      id: optionId,
      label: '방향을 찾아보고 싶어요',
      description: '새로운 분야 탐색',
      kind: 'generated',
    },
    {
      id: '00000000-0000-4000-8000-000000000005',
      label: '아직 모르겠어요',
      description: '미정',
      kind: 'unknown',
    },
  ],
  allow_custom_text: true,
  model_id: 'synthetic-model',
  prompt_version: '1',
  policy_version: '1',
  created_at: '2026-11-01T00:00:00Z',
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
function Harness({ initial = profile }: { initial?: Profile }) {
  const [current, setCurrent] = useState(initial)
  return <Intake profile={current} onProfile={setCurrent} onRun={() => {}} />
}
function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/api/onboarding/options')
      return Promise.resolve(Response.json(options))
    return Promise.resolve(handler(path, init))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function answered(): Profile {
  return {
    ...profile,
    revision: 2,
    answers: { entry_intent: 'explore' },
    selection_summaries: [
      {
        question_id: questionId,
        selected_option_id: optionId,
        target_slot: 'entry_intent',
        label: question.options[0].label,
        revision: 2,
      },
    ],
    intake_state: {
      ...profile.intake_state,
      active_question_id: null,
      next_action: 'collect_documents',
    },
  }
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  sessionStorage.clear()
  setCsrfToken('synthetic-csrf')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  sessionStorage.clear()
  setCsrfToken(null)
})

describe('저장된 질문의 조회와 복구', () => {
  it('조회 실패는 기존 질문을 GET으로 재시도하고 생성 POST를 보내지 않는다', async () => {
    let reads = 0
    const fetch = serve((path) => {
      expect(path).toBe(`/api/questions/${questionId}`)
      return ++reads === 1
        ? Response.json({}, { status: 503 })
        : Response.json(question)
    })
    render(<Harness />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: '다음 질문 받기' })).toBeNull()
    fireEvent.click(
      screen.getByRole('button', { name: '저장된 질문 다시 불러오기' }),
    )
    await screen.findByRole('heading', { name: question.question })
    expect(reads).toBe(2)
    expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(
      true,
    )
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([
    { id: 'wrong-question' },
    { profile_id: 'another-profile' },
    { base_revision: 0 },
    { status: 'stale' },
    { options: [question.options[0], question.options[0]] },
  ])(
    '현재 질문에 맞지 않는 응답을 선택지로 표시하지 않는다: %j',
    async (invalid) => {
      serve(() => Response.json({ ...question, ...invalid }))
      render(<Harness />)
      expect((await screen.findByRole('alert')).textContent).toContain(
        '현재 입력과 맞지 않아요',
      )
      expect(
        screen.queryByRole('button', { name: /방향을 찾아보고 싶어요/ }),
      ).toBeNull()
      expect(
        screen.queryByRole('button', { name: '다음 질문 받기' }),
      ).toBeNull()
    },
  )

  it('저장된 fallback 질문을 표준 질문으로 표시하고 재렌더링으로 생성하지 않는다', async () => {
    const fetch = serve(() =>
      Response.json({ ...question, origin: 'fallback', model_id: null }),
    )
    const view = render(<Harness />)
    await screen.findByRole('heading', { name: question.question })
    expect(screen.getByText('서버가 제공한 기본 질문입니다.')).toBeTruthy()
    view.rerender(<Harness />)
    expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(
      true,
    )
  })
})

describe('선택 제출과 다음 질문', () => {
  it('답변 제출 실패 후 같은 선택을 재시도하면 같은 멱등 키를 유지한다', async () => {
    let submits = 0
    const fetch = serve((path) => {
      if (path === `/api/questions/${questionId}`)
        return Response.json(question)
      expect(path).toBe(
        `/api/profiles/${profileId}/questions/${questionId}/answer`,
      )
      return ++submits === 1
        ? Response.json({}, { status: 503 })
        : Response.json(answered())
    })
    render(<Harness />)
    const choice = await screen.findByRole('button', {
      name: /방향을 찾아보고 싶어요/,
    })
    fireEvent.click(choice)
    await screen.findByRole('alert')
    await waitFor(() => expect(choice.hasAttribute('disabled')).toBe(false))
    fireEvent.click(choice)
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', { name: question.question }),
      ).toBeNull(),
    )
    const submissions = fetch.mock.calls.filter(([url]) =>
      url.endsWith('/answer'),
    )
    expect(submissions).toHaveLength(2)
    expect(submissions[0][1].body).toBe(submissions[1][1].body)
    expect((submissions[0][1].headers as Headers).get('Idempotency-Key')).toBe(
      (submissions[1][1].headers as Headers).get('Idempotency-Key'),
    )
  })

  it('질문 대신 200 IntakeState를 받으면 프로필을 재조회해 서버의 다음 단계를 따른다', async () => {
    const initial = {
      ...profile,
      intake_state: { ...profile.intake_state, active_question_id: null },
    }
    const next = {
      ...initial,
      intake_state: {
        ...initial.intake_state,
        next_action: 'collect_documents' as const,
      },
    }
    const fetch = serve((path) => {
      if (path === `/api/profiles/${profileId}/questions`)
        return Response.json(next.intake_state)
      expect(path).toBe(`/api/profiles/${profileId}`)
      return Response.json(next)
    })
    render(<Harness initial={initial} />)
    const generate = await screen.findByRole('button', {
      name: '다음 질문 받기',
    })
    await waitFor(() => expect(generate.hasAttribute('disabled')).toBe(false))
    fireEvent.click(generate)
    await screen.findByRole('button', { name: '자료 없이 진행' })
    expect(screen.queryByRole('button', { name: '다음 질문 받기' })).toBeNull()
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'POST'),
    ).toHaveLength(1)
    expect(
      fetch.mock.calls.some(
        ([url]) => new URL(url).pathname === `/api/profiles/${profileId}`,
      ),
    ).toBe(true)
  })

  it('선택지 ID·revision만 한 번 제출하고 CSRF·멱등 키를 사용한다', async () => {
    let complete!: (response: Response) => void
    const fetch = serve((path, init) => {
      if (path === `/api/questions/${questionId}`)
        return Response.json(question)
      expect(path).toBe(
        `/api/profiles/${profileId}/questions/${questionId}/answer`,
      )
      expect(init.method).toBe('POST')
      return new Promise<Response>((resolve) => {
        complete = resolve
      })
    })
    render(<Harness />)
    const choice = await screen.findByRole('button', {
      name: /방향을 찾아보고 싶어요/,
    })
    fireEvent.click(choice)
    fireEvent.click(choice)
    expect(choice.hasAttribute('disabled')).toBe(true)
    const submissions = fetch.mock.calls.filter(([url]) =>
      url.endsWith('/answer'),
    )
    expect(submissions).toHaveLength(1)
    const init = submissions[0][1]
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: 1,
      selected_option_id: optionId,
    })
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(
      (init.headers as Headers).get('Idempotency-Key')!.length,
    ).toBeGreaterThanOrEqual(8)
    complete(Response.json(answered()))
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', { name: question.question }),
      ).toBeNull(),
    )
    expect(screen.getByText('방향을 찾아보고 싶어요')).toBeTruthy()
  })

  it('답변 저장 후 다음 질문 생성 실패는 저장된 선택을 유지하고 최신 revision으로 재시도한다', async () => {
    let generationCount = 0
    let current = profile
    const fetch = serve((path, init) => {
      if (path === `/api/questions/${questionId}`)
        return Response.json(question)
      if (path.endsWith('/answer')) {
        current = {
          ...answered(),
          intake_state: {
            ...answered().intake_state,
            next_action: 'generate_question',
          },
        }
        return Response.json(current)
      }
      if (path === `/api/profiles/${profileId}/questions`) {
        generationCount++
        expect(JSON.parse(init.body as string)).toEqual({
          expected_revision: 2,
        })
        return Response.json({}, { status: 503 })
      }
      throw new Error(`Unexpected path: ${path}`)
    })
    render(<Harness />)
    fireEvent.click(
      await screen.findByRole('button', { name: /방향을 찾아보고 싶어요/ }),
    )
    await screen.findByRole('alert')
    expect(screen.getByText('방향을 찾아보고 싶어요')).toBeTruthy()
    expect(
      screen.queryByRole('heading', { name: question.question }),
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '다음 질문 받기' }))
    await waitFor(() => expect(generationCount).toBe(2))
    expect(
      fetch.mock.calls.filter(([url]) => url.endsWith('/answer')),
    ).toHaveLength(1)
  })

  it('생성 job 완료 후 저장된 질문을 조회하고 재렌더링으로 새 job을 만들지 않는다', async () => {
    const initial = {
      ...profile,
      intake_state: { ...profile.intake_state, active_question_id: null },
    }
    const completed: Job = {
      id: jobId,
      kind: 'intake_question',
      status: 'succeeded',
      phase: 'completed',
      profile_id: profileId,
      result: { question_id: questionId },
      error: null,
      created_at: '2026-11-01T00:00:00Z',
      finished_at: '2026-11-01T00:00:01Z',
      completed_stages: [],
    }
    const fetch = serve((path) => {
      if (path === `/api/profiles/${profileId}/questions`)
        return Response.json(
          { job_id: jobId, status: 'queued', poll_after_ms: 500 },
          { status: 202 },
        )
      if (path === `/api/jobs/${jobId}`) return Response.json(completed)
      if (path === `/api/profiles/${profileId}`) return Response.json(profile)
      if (path === `/api/questions/${questionId}`)
        return Response.json(question)
      throw new Error(`Unexpected path: ${path}`)
    })
    const view = render(<Harness initial={initial} />)
    const generate = await screen.findByRole('button', {
      name: '다음 질문 받기',
    })
    await waitFor(() => expect(generate.hasAttribute('disabled')).toBe(false))
    fireEvent.click(generate)
    await screen.findByRole('heading', { name: question.question })
    view.rerender(<Harness initial={initial} />)
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'POST'),
    ).toHaveLength(1)
    await waitFor(() =>
      expect(
        sessionStorage.getItem(`today-tommorrow:job:${profileId}`),
      ).toBeNull(),
    )
  })

  it('잘못된 생성 JSON을 성공 질문으로 바꾸지 않는다', async () => {
    serve(() => new Response('invalid JSON'))
    const initial = {
      ...profile,
      intake_state: { ...profile.intake_state, active_question_id: null },
    }
    render(<Harness initial={initial} />)
    const generate = await screen.findByRole('button', {
      name: '다음 질문 받기',
    })
    await waitFor(() => expect(generate.hasAttribute('disabled')).toBe(false))
    fireEvent.click(generate)
    expect((await screen.findByRole('alert')).textContent).toContain(
      '서버 응답을 읽을 수 없습니다.',
    )
    expect(
      screen.queryByRole('heading', { name: question.question }),
    ).toBeNull()
  })
})
