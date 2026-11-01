import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { setCsrfToken } from './api'
import { Intake } from './Intake'
import type { IntakeQuestion, OnboardingOptions, Profile } from './types'

// Synthetic HTTP/UI fixtures for L; server storage/ownership is not exercised.
const profileId = '00000000-0000-4000-8000-000000000001'
const questionId = '00000000-0000-4000-8000-000000000002'
const profile: Profile = {
  id: profileId,
  revision: 4,
  status: 'draft',
  answers: {
    field_code: 'ai_data',
    experience: 'basic',
    learning_goal: 'foundation',
  },
  reviewed_items: [],
  documents: [
    {
      id: '00000000-0000-4000-8000-000000000003',
      profile_id: profileId,
      kind: 'resume',
      source_type: 'text',
      display_name: '보존할 합성 자료',
      source_url: null,
      mime_type: 'text/plain',
      size_bytes: 12,
      status: 'ready',
      extracted_preview: '합성 내용',
      job_id: null,
      error_code: null,
      content_sha256: null,
      review_confirmed: true,
      created_at: '2026-11-01T00:00:00Z',
    },
  ],
  completed_sections: [],
  review_token: null,
  updated_at: '2026-11-01T00:00:00Z',
  slot_origins: {},
  unresolved_conflicts: [],
  latest_interpretation_id: null,
  selection_summaries: [
    {
      question_id: '00000000-0000-4000-8000-000000000004',
      selected_option_id: '00000000-0000-4000-8000-000000000005',
      target_slot: 'field_code',
      label: '기존 분야 선택',
      revision: 3,
    },
  ],
  intake_state: {
    section: 'experience',
    next_action: 'generate_question',
    eligible_slots: ['experience'],
    missing_slots: ['experience'],
    completed_sections: [],
    active_question_id: questionId,
  },
}
const question: IntakeQuestion = {
  id: questionId,
  profile_id: profileId,
  base_revision: 4,
  context_hash: 'synthetic-context',
  status: 'ready',
  origin: 'llm',
  schema_version: '1.0',
  target_slot: 'experience',
  acknowledgement: '',
  question: '수정 전 질문',
  helper_text: '합성 선택지',
  selection_mode: 'single',
  options: [
    {
      id: '00000000-0000-4000-8000-000000000006',
      label: '프로젝트 경험',
      description: '',
      kind: 'generated',
    },
    {
      id: '00000000-0000-4000-8000-000000000007',
      label: '아직 모르겠어요',
      description: '',
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
function cleared(base = profile): Profile {
  const answers = { ...base.answers }
  delete answers.field_code
  return {
    ...base,
    revision: base.revision + 1,
    answers,
    selection_summaries: [],
    intake_state: {
      ...base.intake_state,
      next_action: 'generate_question',
      active_question_id: null,
    },
  }
}
const newer: Profile = {
  ...profile,
  revision: 6,
  answers: {
    ...profile.answers,
    field_code: 'software_cloud',
    experience: 'work',
  },
  selection_summaries: [
    {
      ...profile.selection_summaries[0],
      label: '다른 탭의 최신 선택',
      revision: 6,
    },
  ],
  intake_state: { ...profile.intake_state, active_question_id: null },
}
function Harness({ initial = profile }: { initial?: Profile }) {
  const [current, setCurrent] = useState(initial)
  return (
    <>
      <button onClick={() => setCurrent(newer)}>합성 외부 수정 반영</button>
      <output aria-label="검사용 현재 버전">{current.revision}</output>
      <Intake profile={current} onProfile={setCurrent} onRun={() => {}} />
    </>
  )
}
function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/api/onboarding/options')
      return Promise.resolve(Response.json(options))
    if (path === `/api/questions/${questionId}`)
      return Promise.resolve(Response.json(question))
    return Promise.resolve(handler(path, init))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
async function ready() {
  await screen.findByRole('heading', { name: question.question })
  fireEvent.click(screen.getByText('지금까지 선택한 내용'))
  return screen.getByRole('button', { name: '다시 선택' })
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  setCsrfToken('synthetic-csrf')
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
  sessionStorage.clear()
})

describe('이전 답변 수정·입력 보존', () => {
  it('선택한 답변만 비우고 다른 답변·자료·미등록 문장을 유지한다', async () => {
    const fetch = serve((path, init) => {
      expect(path).toBe(`/api/profiles/${profileId}`)
      expect(init.method).toBe('PATCH')
      expect(JSON.parse(init.body as string)).toEqual({
        expected_revision: 4,
        answers: { experience: 'basic', learning_goal: 'foundation' },
      })
      return Response.json(cleared())
    })
    render(<Harness />)
    const edit = await ready()
    fireEvent.change(screen.getByLabelText('내 경험이나 원하는 배움'), {
      target: { value: '작성 중인 문장' },
    })
    fireEvent.click(edit)
    await screen.findByText(/해당 답변을 다시 선택할 수 있도록 저장했어요/)
    expect(screen.queryByText('기존 분야 선택')).toBeNull()
    expect(
      screen.queryByRole('heading', { name: question.question }),
    ).toBeNull()
    expect(screen.getByText('보존할 합성 자료')).toBeTruthy()
    expect(
      (screen.getByLabelText('내 경험이나 원하는 배움') as HTMLTextAreaElement)
        .value,
    ).toBe('작성 중인 문장')
    expect(screen.getByRole('button', { name: '다음 질문 받기' })).toBeTruthy()
    expect(
      fetch.mock.calls.filter(([, init]) => init.method !== 'GET'),
    ).toHaveLength(1)
    expect(fetch.mock.calls.some(([, init]) => init.method === 'DELETE')).toBe(
      false,
    )
  })

  it('서버가 자료 단계를 반환하면 새 질문을 요청하라고 안내하지 않는다', async () => {
    serve(() =>
      Response.json({
        ...cleared(),
        intake_state: {
          ...cleared().intake_state,
          next_action: 'collect_documents',
        },
      }),
    )
    render(<Harness />)
    fireEvent.click(await ready())
    await screen.findByText(
      '수정한 입력을 저장했어요. 화면의 다음 단계를 확인해 주세요.',
    )
    expect(screen.queryByText(/다음 질문을 요청해 주세요/)).toBeNull()
    expect(screen.queryByRole('button', { name: '다음 질문 받기' })).toBeNull()
  })

  it('수정 실패 전에는 이전 선택과 질문을 지우지 않고 같은 revision으로 재시도한다', async () => {
    let attempts = 0
    serve((_path, init) => {
      expect(JSON.parse(init.body as string).expected_revision).toBe(4)
      return ++attempts === 1
        ? Response.json({}, { status: 503 })
        : Response.json(cleared())
    })
    render(<Harness />)
    fireEvent.click(await ready())
    await screen.findByRole('alert')
    expect(screen.getByText('기존 분야 선택')).toBeTruthy()
    expect(
      screen.getByRole('heading', { name: question.question }),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '다시 선택' }))
    await screen.findByText(/해당 답변을 다시 선택할 수 있도록 저장했어요/)
    expect(attempts).toBe(2)
  })

  it('409 후 최신 서버 답변·revision으로만 재시도한다', async () => {
    const patches: Array<{ expected_revision: number; answers: unknown }> = []
    serve((_path, init) => {
      if (init.method === 'PATCH') {
        patches.push(JSON.parse(init.body as string))
        return patches.length === 1
          ? Response.json({}, { status: 409 })
          : Response.json(cleared(newer))
      }
      return Response.json(newer)
    })
    render(<Harness />)
    fireEvent.click(await ready())
    await screen.findByRole('alert')
    expect(screen.getByText('다른 탭의 최신 선택')).toBeTruthy()
    expect(screen.queryByText('기존 분야 선택')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '다시 선택' }))
    await screen.findByText(/해당 답변을 다시 선택할 수 있도록 저장했어요/)
    expect(patches[1]).toEqual({
      expected_revision: 6,
      answers: { experience: 'work', learning_goal: 'foundation' },
    })
    expect(screen.getByText('보존할 합성 자료')).toBeTruthy()
  })

  it('외부 수정 뒤 늦은 PATCH 응답을 성공으로 안내하거나 최신 상태를 덮어쓰지 않는다', async () => {
    let complete!: (response: Response) => void
    serve((_path, init) =>
      init.method === 'PATCH'
        ? new Promise<Response>((resolve) => {
            complete = resolve
          })
        : Response.json(newer),
    )
    render(<Harness />)
    fireEvent.click(await ready())
    fireEvent.click(screen.getByRole('button', { name: '합성 외부 수정 반영' }))
    expect(screen.getByLabelText('검사용 현재 버전').textContent).toBe('6')
    complete(Response.json(cleared()))
    await screen.findByRole('alert')
    expect(screen.getByLabelText('검사용 현재 버전').textContent).toBe('6')
    expect(screen.getByText('다른 탭의 최신 선택')).toBeTruthy()
    expect(
      screen.queryByText(/해당 답변을 다시 선택할 수 있도록 저장했어요/),
    ).toBeNull()
    expect(
      screen.queryByRole('heading', { name: question.question }),
    ).toBeNull()
  })

  it('오래된 GET 응답 후 복구에도 최신 revision을 유지한다', async () => {
    let reads = 0
    serve(() => Response.json(++reads === 1 ? profile : newer))
    render(<Harness initial={newer} />)
    fireEvent.click(screen.getByRole('button', { name: '최신 입력 확인' }))
    await screen.findByRole('alert')
    expect(reads).toBe(2)
    expect(screen.getByLabelText('검사용 현재 버전').textContent).toBe('6')
    expect(screen.getByText('다른 탭의 최신 선택')).toBeTruthy()
  })

  it('늦은 질문 job 완료 뒤 오래된 프로필 조회가 최신 입력을 덮어쓰지 않는다', async () => {
    const jobId = '00000000-0000-4000-8000-000000000008'
    const storageKey = `today-tommorrow:job:${profileId}`
    sessionStorage.setItem(storageKey, jobId)
    let reads = 0
    serve((path) => {
      if (path === `/api/jobs/${jobId}`)
        return Response.json({
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
        })
      expect(path).toBe(`/api/profiles/${profileId}`)
      return Response.json(++reads === 1 ? profile : newer)
    })
    render(<Harness initial={newer} />)
    fireEvent.click(
      screen.getByRole('button', { name: '진행 중이던 작업 확인' }),
    )
    await screen.findByRole('alert')
    expect(reads).toBe(2)
    expect(screen.getByLabelText('검사용 현재 버전').textContent).toBe('6')
    expect(screen.getByText('다른 탭의 최신 선택')).toBeTruthy()
    expect(
      screen.queryByRole('heading', { name: question.question }),
    ).toBeNull()
    expect(sessionStorage.getItem(storageKey)).toBe(jobId)
  })

  it('다른 프로필의 수정 응답을 현재 입력에 적용하지 않는다', async () => {
    serve(() => Response.json({ ...cleared(), id: 'another-profile' }))
    render(<Harness />)
    fireEvent.click(await ready())
    expect((await screen.findByRole('alert')).textContent).toContain(
      '현재 입력의 응답을 확인할 수 없어요',
    )
    expect(screen.getByLabelText('검사용 현재 버전').textContent).toBe('4')
    expect(screen.getByText('기존 분야 선택')).toBeTruthy()
  })

  it('수정 전과 같은 revision의 응답은 저장 성공으로 안내하지 않는다', async () => {
    serve((_path, init) =>
      Response.json(
        init.method === 'PATCH' ? { ...cleared(), revision: 4 } : profile,
      ),
    )
    render(<Harness />)
    fireEvent.click(await ready())
    await screen.findByRole('alert')
    expect(screen.getByLabelText('검사용 현재 버전').textContent).toBe('4')
    expect(screen.getByText('기존 분야 선택')).toBeTruthy()
    expect(
      screen.queryByText(/해당 답변을 다시 선택할 수 있도록 저장했어요/),
    ).toBeNull()
  })
})
