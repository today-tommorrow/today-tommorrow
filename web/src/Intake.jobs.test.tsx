import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { ApiError, request, setCsrfToken, waitForJob } from './api'
import { Intake } from './Intake'
import type { Job, OnboardingOptions, Profile } from './types'

// L fixtures: only the browser HTTP boundary is replaced, never a live model.
const profile: Profile = {
  id: 'synthetic-profile',
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
    next_action: 'collect_documents',
    eligible_slots: ['entry_intent'],
    missing_slots: ['entry_intent'],
    completed_sections: [],
    active_question_id: null,
  },
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
function job(status: Job['status'], phase: Job['phase'] = 'extracting'): Job {
  return {
    id: 'synthetic-job',
    kind: 'document_processing',
    status,
    phase,
    profile_id: profile.id,
    result: null,
    error: null,
    created_at: '2026-11-01T00:00:00Z',
    finished_at: null,
    completed_stages: [],
  }
}
const storageKey = `today-tommorrow:job:${profile.id}`
function Harness() {
  const [current, setCurrent] = useState(profile)
  return <Intake profile={current} onProfile={setCurrent} onRun={() => {}} />
}
function serve(handler: () => Response | Promise<Response>) {
  const fetch = vi.fn((url: string) => {
    const path = new URL(url).pathname
    if (path === '/api/onboarding/options')
      return Promise.resolve(Response.json(options))
    if (path === `/api/profiles/${profile.id}`)
      return Promise.resolve(Response.json(profile))
    if (path === '/api/jobs/synthetic-job') return Promise.resolve(handler())
    throw new Error(`Unexpected path: ${path}`)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  sessionStorage.clear()
  setCsrfToken('synthetic-csrf')
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
  sessionStorage.clear()
})

describe('작업 상태와 복구 UI', () => {
  it.each([
    ['failed', '처리에 실패했어요'],
    ['cancelled', '서버 작업이 취소됐어요'],
    ['superseded', '입력이 바뀌어 이전 작업이 만료됐어요'],
  ] as const)(
    '%s를 이전 phase의 진행 상태로 표시하지 않는다',
    async (status, label) => {
      sessionStorage.setItem(storageKey, 'synthetic-job')
      serve(() => Response.json(job(status)))
      render(<Harness />)
      fireEvent.click(
        screen.getByRole('button', { name: '진행 중이던 작업 확인' }),
      )
      expect(await screen.findByText(label)).toBeTruthy()
      await screen.findByRole('alert')
      await waitFor(() => expect(sessionStorage.getItem(storageKey)).toBeNull())
      expect(screen.queryByText('자료를 읽고 있어요')).toBeNull()
    },
  )

  it('화면 대기만 중지하고 같은 작업을 GET으로 복구하며 입력을 유지한다', async () => {
    sessionStorage.setItem(storageKey, 'synthetic-job')
    let completed = false
    const fetch = serve(() =>
      Response.json(job(completed ? 'succeeded' : 'running')),
    )
    render(<Harness />)
    fireEvent.change(await screen.findByLabelText('내 경험이나 원하는 배움'), {
      target: { value: '보존할 문장' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: '진행 중이던 작업 확인' }),
    )
    await screen.findByText('자료를 읽고 있어요')
    fireEvent.click(screen.getByRole('button', { name: '대기 중지' }))
    await screen.findByText(/서버 작업은 계속될 수 있으니/)
    expect(sessionStorage.getItem(storageKey)).toBe('synthetic-job')
    completed = true
    fireEvent.click(
      screen.getByRole('button', { name: '진행 중이던 작업 확인' }),
    )
    await screen.findByText('처리를 마쳤어요')
    await waitFor(() => expect(sessionStorage.getItem(storageKey)).toBeNull())
    expect(
      (screen.getByLabelText('내 경험이나 원하는 배움') as HTMLTextAreaElement)
        .value,
    ).toBe('보존할 문장')
    expect(
      fetch.mock.calls.filter(([url]) =>
        new URL(url).pathname.includes('/jobs/'),
      ),
    ).toHaveLength(2)
  })

  it('408 응답에서 작업 ID를 남기고 성공으로 바꾸지 않는다', async () => {
    sessionStorage.setItem(storageKey, 'synthetic-job')
    serve(() => Response.json({}, { status: 408 }))
    render(<Harness />)
    fireEvent.click(
      screen.getByRole('button', { name: '진행 중이던 작업 확인' }),
    )
    expect((await screen.findByRole('alert')).textContent).toContain(
      '응답 대기 시간이 초과',
    )
    expect(sessionStorage.getItem(storageKey)).toBe('synthetic-job')
    expect(screen.queryByText('처리를 마쳤어요')).toBeNull()
    await screen.findByRole('button', { name: '진행 중이던 작업 확인' })
  })

  it('phase=completed만으로 아직 running인 작업을 성공으로 표시하지 않는다', async () => {
    sessionStorage.setItem(storageKey, 'synthetic-job')
    serve(() => Response.json(job('running', 'completed')))
    render(<Harness />)
    fireEvent.click(
      screen.getByRole('button', { name: '진행 중이던 작업 확인' }),
    )
    await screen.findByText('작업의 최종 완료를 확인하고 있어요')
    expect(screen.queryByText('처리를 마쳤어요')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '대기 중지' }))
  })
})

describe('요청 timeout·취소 계약', () => {
  it.each(['timeout', 'caller'] as const)(
    '오류 응답 본문에서도 %s 중단 원인을 보존한다',
    async (source) => {
      const timeout = new AbortController()
      const caller = new AbortController()
      vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeout.signal)
      const response = Response.json({}, { status: 503 })
      vi.spyOn(response, 'json').mockImplementation(async () => {
        if (source === 'timeout') {
          timeout.abort(new DOMException('synthetic timeout', 'TimeoutError'))
          throw timeout.signal.reason
        }
        caller.abort()
        throw caller.signal.reason
      })
      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.resolve(response)),
      )
      await expect(
        request('/jobs/synthetic-job', { signal: caller.signal }),
      ).rejects.toMatchObject(
        source === 'timeout' ? { status: 408 } : { name: 'AbortError' },
      )
    },
  )

  it('응답 본문을 읽는 중 timeout도 408로 안내한다', async () => {
    const controller = new AbortController()
    vi.spyOn(AbortSignal, 'timeout').mockReturnValue(controller.signal)
    const response = Response.json({})
    vi.spyOn(response, 'json').mockImplementation(async () => {
      controller.abort(new DOMException('synthetic timeout', 'TimeoutError'))
      throw controller.signal.reason
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(response)),
    )
    await expect(request('/jobs/synthetic-job')).rejects.toMatchObject({
      status: 408,
    })
  })

  it('잘못된 JSON은 timeout과 구분해 응답 오류로 안내한다', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('invalid JSON'))),
    )
    await expect(request('/jobs/synthetic-job')).rejects.toMatchObject({
      status: 502,
    })
  })

  it('실제 timeout 신호를 사용자에게 안내할 408 오류로 변환한다', async () => {
    const controller = new AbortController()
    vi.spyOn(AbortSignal, 'timeout').mockReturnValue(controller.signal)
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((_, reject) => {
            controller.signal.addEventListener(
              'abort',
              () => reject(controller.signal.reason),
              { once: true },
            )
          }),
      ),
    )
    const result = request('/jobs/synthetic-job')
    const assertion = expect(result).rejects.toMatchObject({ status: 408 })
    controller.abort(new DOMException('synthetic timeout', 'TimeoutError'))
    await assertion
  })

  it('호출자가 대기를 취소하면 timeout으로 바꾸지 않는다', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, init: RequestInit) =>
          new Promise((_, reject) => {
            init.signal?.addEventListener(
              'abort',
              () => reject(controller.signal.reason),
              { once: true },
            )
          }),
      ),
    )
    const result = request('/jobs/synthetic-job', { signal: controller.signal })
    const assertion = expect(result).rejects.toMatchObject({
      name: 'AbortError',
    })
    controller.abort()
    await assertion
  })

  it('잘못된 작업 상태는 화면 onUpdate에 넘기지 않는다', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          Response.json({ ...job('running'), status: 'invented' }),
        ),
      ),
    )
    const update = vi.fn()
    await expect(
      waitForJob('synthetic-job', new AbortController().signal, update),
    ).rejects.toBeInstanceOf(ApiError)
    expect(update).not.toHaveBeenCalled()
  })
})
