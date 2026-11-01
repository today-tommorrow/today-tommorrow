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
import type { Job, OnboardingOptions, Profile } from './types'

// Synthetic HTTP fixtures verify UI recovery, not a model or deployed API.
const profile: Profile = {
  id: 'synthetic-ready-profile',
  revision: 12,
  status: 'draft',
  answers: {
    entry_intent: 'goal_known',
    field_code: 'software_cloud',
    field_confirmed: true,
    experience: 'beginner',
    learning_goal: 'foundation',
    availability: 'flexible',
    delivery_mode: 'online',
    budget_status: 'unknown',
    start_timing: 'browse',
    document_decision: 'none',
  },
  reviewed_items: [],
  documents: [],
  completed_sections: ['direction', 'experience', 'goal', 'conditions'],
  review_token: 'synthetic-review-token',
  updated_at: '2026-11-01T00:00:00Z',
  slot_origins: {},
  unresolved_conflicts: [],
  latest_interpretation_id: null,
  selection_summaries: [],
  intake_state: {
    section: 'conditions',
    next_action: 'review',
    eligible_slots: [],
    missing_slots: [],
    completed_sections: ['direction', 'experience', 'goal', 'conditions'],
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
const jobId = 'synthetic-recommendation-job'
const runId = 'synthetic-saved-run'
const storageKey = `today-tommorrow:job:${profile.id}`
const submitLabel = '확인하고 교육 추천 받기'
const resumeLabel = '진행 중이던 작업 확인'
const cancelLabel = '서버 추천 작업 취소'

function job(status: Job['status']): Job {
  return {
    id: jobId,
    kind: 'recommendation',
    status,
    phase: status === 'succeeded' ? 'completed' : 'assessing',
    profile_id: profile.id,
    profile_revision: profile.revision,
    result: status === 'succeeded' ? { recommendation_run_id: runId } : null,
    error: null,
    created_at: '2026-11-01T00:00:00Z',
    finished_at: null,
    completed_stages: [],
  }
}

function Harness({
  onRun,
  initial = profile,
}: {
  onRun: (id: string) => void
  initial?: Profile
}) {
  const [current, setCurrent] = useState(initial)
  return <Intake profile={current} onProfile={setCurrent} onRun={onRun} />
}

function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
  currentProfile: Profile = profile,
) {
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/api/onboarding/options') return Response.json(options)
    if (path === `/api/profiles/${profile.id}`)
      return Response.json(currentProfile)
    return handler(path, init)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function submitButton() {
  return screen.getByRole('button', { name: submitLabel }) as HTMLButtonElement
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
  sessionStorage.clear()
  setCsrfToken(null)
})

describe('서버 추천 작업 복구', () => {
  it('새 탭에서 확인 중 제출을 막고 완료 작업을 복구한다', async () => {
    let resolveRecovery!: (value: Response) => void
    const recovery = new Promise<Response>((resolve) => {
      resolveRecovery = resolve
    })
    const onRun = vi.fn()
    const fetch = serve((path) => {
      if (path.endsWith('/recommendation-job')) return recovery
      if (path === `/api/jobs/${jobId}`) return Response.json(job('succeeded'))
      throw new Error(`Unexpected path: ${path}`)
    })
    render(
      <Harness onRun={onRun} initial={{ ...profile, status: 'submitted' }} />,
    )
    expect(submitButton().disabled).toBe(true)
    fireEvent.click(submitButton())
    resolveRecovery(
      Response.json({
        profile_id: profile.id,
        revision: profile.revision,
        job: job('succeeded'),
      }),
    )
    await waitFor(() => expect(onRun).toHaveBeenCalledWith(runId))
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/submit'))).toBe(
      false,
    )
  })

  it('늦은 이전 버전 응답은 새 입력의 결과를 열지 않는다', async () => {
    let resolveOld!: (value: Response) => void
    const old = new Promise<Response>((resolve) => {
      resolveOld = resolve
    })
    let count = 0
    const onRun = vi.fn()
    serve((path) => {
      if (path.endsWith('/recommendation-job')) {
        count += 1
        return count === 1
          ? old
          : Response.json({
              profile_id: profile.id,
              revision: profile.revision + 1,
              job: null,
            })
      }
      throw new Error(`Unexpected path: ${path}`)
    })
    const submitted = { ...profile, status: 'submitted' as const }
    const view = render(
      <Intake profile={submitted} onProfile={vi.fn()} onRun={onRun} />,
    )
    view.rerender(
      <Intake
        profile={{ ...submitted, revision: profile.revision + 1 }}
        onProfile={vi.fn()}
        onRun={onRun}
      />,
    )
    resolveOld(
      Response.json({
        profile_id: profile.id,
        revision: profile.revision,
        job: job('succeeded'),
      }),
    )
    await waitFor(() => expect(submitButton().disabled).toBe(false))
    expect(onRun).not.toHaveBeenCalled()
  })

  it('기억된 이전 버전 추천 작업은 결과로 이동하지 않는다', async () => {
    sessionStorage.setItem(storageKey, jobId)
    const onRun = vi.fn()
    serve((path) => {
      if (path.endsWith('/recommendation-job'))
        return Response.json({
          profile_id: profile.id,
          revision: profile.revision,
          job: null,
        })
      if (path === `/api/jobs/${jobId}`)
        return Response.json({
          ...job('succeeded'),
          profile_revision: profile.revision - 1,
        })
      throw new Error(`Unexpected path: ${path}`)
    })
    render(
      <Harness onRun={onRun} initial={{ ...profile, status: 'submitted' }} />,
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: resumeLabel })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
    await screen.findByRole('alert')
    expect(onRun).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('작업 조회 후 입력이 변경되면 이전 결과를 열지 않는다', async () => {
    sessionStorage.setItem(storageKey, jobId)
    const onRun = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = new URL(url).pathname
        if (path === '/api/onboarding/options') return Response.json(options)
        if (path === `/api/profiles/${profile.id}`)
          return Response.json({ ...profile, revision: profile.revision + 1 })
        if (path === `/api/jobs/${jobId}`)
          return Response.json(job('succeeded'))
        throw new Error(`Unexpected path: ${path}`)
      }),
    )
    render(<Harness onRun={onRun} />)
    fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
    await screen.findByText('입력이 바뀌어 이전 추천을 표시하지 않았어요.')
    expect(onRun).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('기존 탭 작업을 복구 작업으로 덮지 않는다', async () => {
    sessionStorage.setItem(storageKey, 'saved-question-job')
    serve((path) => {
      if (path.endsWith('/recommendation-job'))
        return Response.json({
          profile_id: profile.id,
          revision: profile.revision,
          job: job('running'),
        })
      throw new Error(`Unexpected path: ${path}`)
    })
    render(
      <Harness onRun={vi.fn()} initial={{ ...profile, status: 'submitted' }} />,
    )
    await screen.findByRole('button', { name: '추천 작업 다시 확인' })
    expect(sessionStorage.getItem(storageKey)).toBe('saved-question-job')
    expect(submitButton().disabled).toBe(true)
  })

  it('복구 오류는 제출을 막고 재확인을 제공한다', async () => {
    let count = 0
    serve((path) => {
      if (path.endsWith('/recommendation-job')) {
        count += 1
        return count === 1
          ? Response.json({}, { status: 503 })
          : Response.json({
              profile_id: profile.id,
              revision: profile.revision,
              job: null,
            })
      }
      throw new Error(`Unexpected path: ${path}`)
    })
    render(
      <Harness onRun={vi.fn()} initial={{ ...profile, status: 'submitted' }} />,
    )
    fireEvent.click(
      await screen.findByRole('button', { name: '추천 작업 다시 확인' }),
    )
    await waitFor(() => expect(submitButton().disabled).toBe(false))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('복구 성공은 조회 오류만 지우고 다른 작업의 오류는 보존한다', async () => {
    let reads = 0
    const fetch = vi.fn(async (url: string) => {
      const path = new URL(url).pathname
      if (path === '/api/onboarding/options') return Response.json(options)
      if (path === `/api/profiles/${profile.id}`)
        return Response.json({}, { status: 403 })
      if (path.endsWith('/recommendation-job'))
        return ++reads === 1
          ? Response.json({}, { status: 503 })
          : Response.json({
              profile_id: profile.id,
              revision: profile.revision,
              job: null,
            })
      throw new Error(`Unexpected path: ${path}`)
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <Harness onRun={vi.fn()} initial={{ ...profile, status: 'submitted' }} />,
    )
    const retry = await screen.findByRole('button', {
      name: '추천 작업 다시 확인',
    })
    expect(submitButton().disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '최신 입력 확인' }))
    const otherError =
      '요청 권한을 확인할 수 없습니다. 로그인 상태를 다시 확인해주세요.'
    await screen.findByText(otherError)
    fireEvent.click(retry)
    await waitFor(() => expect(submitButton().disabled).toBe(false))
    expect(
      screen.queryByText(
        '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
      ),
    ).toBeNull()
    expect(screen.getByRole('alert').textContent).toBe(otherError)
    expect(reads).toBe(2)
  })
})

describe('추천 제출과 저장된 결과 복구', () => {
  it('중복 클릭은 한 번만 제출하고 완료된 run ID로 이동한다', async () => {
    const onRun = vi.fn()
    const fetch = serve((path) => {
      if (path.endsWith('/submit'))
        return Response.json({
          job_id: jobId,
          status: 'queued',
          poll_after_ms: 1000,
        })
      if (path === `/api/jobs/${jobId}`) return Response.json(job('succeeded'))
      throw new Error(`Unexpected path: ${path}`)
    })
    render(<Harness onRun={onRun} />)
    fireEvent.click(submitButton())
    fireEvent.click(submitButton())
    await waitFor(() => expect(onRun).toHaveBeenCalledWith(runId))
    const submissions = fetch.mock.calls.filter(([url]) =>
      url.endsWith('/submit'),
    )
    expect(submissions).toHaveLength(1)
    const init = submissions[0][1]
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: profile.revision,
      review_token: profile.review_token,
    })
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(new Headers(init.headers).get('Idempotency-Key')).toBeTruthy()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('새로고침 후 저장된 작업을 조회하고 다시 제출하지 않는다', async () => {
    sessionStorage.setItem(storageKey, jobId)
    const onRun = vi.fn()
    const fetch = serve((path) => {
      if (path === `/api/jobs/${jobId}`) return Response.json(job('succeeded'))
      throw new Error(`Unexpected path: ${path}`)
    })
    render(<Harness onRun={onRun} />)
    expect(submitButton().disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
    await waitFor(() => expect(onRun).toHaveBeenCalledWith(runId))
    expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(
      true,
    )
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it.each(['failed', 'cancelled'] as const)(
    '%s 뒤 확인한 입력을 보존하고 새 키와 작업으로 명시적으로 재시도한다',
    async (status) => {
      const onRun = vi.fn()
      const retryJobId = 'synthetic-retry-job'
      const retryRunId = 'synthetic-retry-run'
      const selectedLabel = '합성 사용자 선택: 예산 상한 100000원'
      const reviewed: Profile = {
        ...profile,
        answers: {
          ...profile.answers,
          goal_text: '합성 소프트웨어 학습 목표',
          budget_status: 'amount',
          budget_krw: 100000,
        },
        selection_summaries: [
          {
            question_id: 'synthetic-budget-question',
            selected_option_id: 'synthetic-budget-option',
            target_slot: 'budget_status',
            label: selectedLabel,
            revision: profile.revision,
          },
        ],
      }
      let attempts = 0
      const fetch = serve((path) => {
        if (path.endsWith('/submit')) {
          attempts += 1
          return Response.json({
            job_id: attempts === 1 ? jobId : retryJobId,
            status: 'queued',
            poll_after_ms: 1000,
          })
        }
        if (path === `/api/jobs/${jobId}`)
          return Response.json({
            ...job(status),
            error:
              status === 'failed'
                ? {
                    code: 'recommendation_failed',
                    message: '합성 추천 처리 실패',
                    request_id: jobId,
                  }
                : null,
          })
        if (path === `/api/jobs/${retryJobId}`)
          return Response.json({
            ...job('succeeded'),
            id: retryJobId,
            result: { recommendation_run_id: retryRunId },
          })
        throw new Error(`Unexpected path: ${path}`)
      }, reviewed)
      render(<Harness onRun={onRun} initial={reviewed} />)
      fireEvent.click(submitButton())
      await screen.findByRole('alert')
      await waitFor(() => expect(submitButton().disabled).toBe(false))
      expect(onRun).not.toHaveBeenCalled()
      expect(attempts).toBe(1)
      expect(sessionStorage.getItem(storageKey)).toBeNull()
      expect(screen.getAllByText(selectedLabel).length).toBeGreaterThan(0)
      expect(screen.getByText('합성 소프트웨어 학습 목표')).toBeTruthy()
      expect(screen.getByText('100,000')).toBeTruthy()
      fireEvent.click(submitButton())
      await waitFor(() => expect(onRun).toHaveBeenCalledWith(retryRunId))
      expect(onRun).toHaveBeenCalledTimes(1)
      const submissions = fetch.mock.calls.filter(([url]) =>
        url.endsWith('/submit'),
      )
      const keys = submissions.map(([, init]) =>
        new Headers(init.headers).get('Idempotency-Key'),
      )
      expect(keys).toHaveLength(2)
      expect(keys[0]).toBeTruthy()
      expect(keys[1]).toBeTruthy()
      expect(keys[0]).not.toBe(keys[1])
      for (const [, init] of submissions)
        expect(init.body).toBe(
          JSON.stringify({
            expected_revision: reviewed.revision,
            review_token: reviewed.review_token,
          }),
        )
      expect(
        fetch.mock.calls
          .map(([url]) => new URL(url).pathname)
          .filter((path) => path.startsWith('/api/jobs/')),
      ).toEqual([`/api/jobs/${jobId}`, `/api/jobs/${retryJobId}`])
      expect(
        fetch.mock.calls.filter(([, init]) => init.method === 'PATCH'),
      ).toHaveLength(0)
    },
  )

  it('저장된 작업이 404이면 최신 입력 확인 후 다시 제출할 수 있다', async () => {
    sessionStorage.setItem(storageKey, jobId)
    const onRun = vi.fn()
    serve((path) => {
      if (path === `/api/jobs/${jobId}`)
        return Response.json({}, { status: 404 })
      throw new Error(`Unexpected path: ${path}`)
    })
    render(<Harness onRun={onRun} />)
    fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
    await screen.findByRole('alert')
    await waitFor(() => expect(submitButton().disabled).toBe(false))
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(onRun).not.toHaveBeenCalled()
  })

  it.each([
    { ...job('succeeded'), profile_id: 'another-profile' },
    { ...job('succeeded'), result: null },
  ])(
    '다른 입력 또는 결과 ID 없는 완료 응답은 이동하지 않는다 (%#)',
    async (value) => {
      sessionStorage.setItem(storageKey, jobId)
      const onRun = vi.fn()
      serve((path) => {
        if (path === `/api/jobs/${jobId}`) return Response.json(value)
        throw new Error(`Unexpected path: ${path}`)
      })
      render(<Harness onRun={onRun} />)
      fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
      await screen.findByRole('alert')
      expect(onRun).not.toHaveBeenCalled()
      expect(sessionStorage.getItem(storageKey)).toBe(jobId)
      expect(submitButton().disabled).toBe(true)
    },
  )
})

describe('추천 대기와 서버 작업 취소', () => {
  it.each(['cancelled', 'succeeded'] as const)(
    '서버 취소 후 실제 상태 %s를 재조회하고 완료 경쟁을 보존한다',
    async (terminal) => {
      sessionStorage.setItem(storageKey, jobId)
      const onRun = vi.fn()
      let cancelled = false
      const fetch = serve((path) => {
        if (path === `/api/jobs/${jobId}/cancel`) {
          cancelled = true
          return Response.json(job(terminal))
        }
        if (path === `/api/jobs/${jobId}`)
          return Response.json(job(cancelled ? terminal : 'running'))
        throw new Error(`Unexpected path: ${path}`)
      })
      render(<Harness onRun={onRun} />)
      fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
      await screen.findByText('교육 근거를 살펴보고 있어요')
      fireEvent.click(screen.getByRole('button', { name: '대기 중지' }))
      expect(cancelled).toBe(false)
      expect(submitButton().disabled).toBe(true)
      fireEvent.click(screen.getByRole('button', { name: cancelLabel }))
      if (terminal === 'cancelled') {
        await screen.findByText('서버 작업이 취소됐어요')
        await waitFor(() => expect(submitButton().disabled).toBe(false))
        expect(onRun).not.toHaveBeenCalled()
      } else {
        await waitFor(() => expect(onRun).toHaveBeenCalledWith(runId))
        expect(screen.queryByText('서버 작업이 취소됐어요')).toBeNull()
      }
      const cancelCalls = fetch.mock.calls.filter(([url]) =>
        url.endsWith('/cancel'),
      )
      expect(cancelCalls).toHaveLength(1)
      expect(cancelCalls[0][1].method).toBe('POST')
      expect(new Headers(cancelCalls[0][1].headers).get('X-CSRF-Token')).toBe(
        'synthetic-csrf',
      )
      expect(sessionStorage.getItem(storageKey)).toBeNull()
    },
  )

  it('취소 요청 실패는 취소 성공으로 표시하지 않고 같은 작업을 복구한다', async () => {
    sessionStorage.setItem(storageKey, jobId)
    const onRun = vi.fn()
    let completed = false
    serve((path) => {
      if (path === `/api/jobs/${jobId}/cancel`)
        return Response.json({}, { status: 503 })
      if (path === `/api/jobs/${jobId}`)
        return Response.json(job(completed ? 'succeeded' : 'running'))
      throw new Error(`Unexpected path: ${path}`)
    })
    render(<Harness onRun={onRun} />)
    fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
    await screen.findByText('교육 근거를 살펴보고 있어요')
    fireEvent.click(screen.getByRole('button', { name: '대기 중지' }))
    fireEvent.click(screen.getByRole('button', { name: cancelLabel }))
    await screen.findByRole('alert')
    expect(screen.queryByText('서버 작업이 취소됐어요')).toBeNull()
    expect(sessionStorage.getItem(storageKey)).toBe(jobId)
    expect(submitButton().disabled).toBe(true)
    completed = true
    fireEvent.click(screen.getByRole('button', { name: resumeLabel }))
    await waitFor(() => expect(onRun).toHaveBeenCalledWith(runId))
  })
})
