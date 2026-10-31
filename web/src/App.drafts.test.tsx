import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { App } from './App'
import { setCsrfToken } from './api'
import type { AuthSession, OnboardingOptions, Profile } from './types'

// HTTP-boundary fixtures for L; no real session, DB or model is created.
const profile: Profile = {
  id: 'synthetic-profile-1',
  revision: 4,
  status: 'draft',
  answers: { field_code: 'ai_data', experience: 'basic' },
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
    section: 'experience',
    next_action: 'collect_documents',
    eligible_slots: [],
    missing_slots: [],
    completed_sections: [],
    active_question_id: null,
  },
}
const session: AuthSession = {
  user: {
    id: 'synthetic-user-1',
    display_name: '합성 계정',
    email: 'synthetic@example.com',
    avatar_url: null,
  },
  csrf_token: 'synthetic-csrf',
  expires_at: '2099-01-01T00:00:00Z',
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
  interpretation_enabled: false,
  question_generation_enabled: false,
  question_schema_version: '1',
}
const storageKey = `today-tommorrow:profile:${session.user.id}`

function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/api/auth/me') return Promise.resolve(Response.json(session))
    if (path === '/api/onboarding/options')
      return Promise.resolve(Response.json(options))
    return Promise.resolve(handler(path, init))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  sessionStorage.clear()
  window.history.replaceState(null, '', '/onboarding')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
  sessionStorage.clear()
  window.history.replaceState(null, '', '/')
})

describe('초안 생성·다시 불러오기', () => {
  it('생성 실패 후 재시도 버튼이 실제 생성 요청을 다시 보내고 초안을 연다', async () => {
    let creates = 0
    const fetch = serve((path, init) => {
      if (path === '/api/profiles' && init.method === 'POST') {
        creates++
        return creates === 1
          ? Response.json({}, { status: 503 })
          : Response.json(profile, { status: 201 })
      }
      if (path === `/api/profiles/${profile.id}`) return Response.json(profile)
      throw new Error(`Unexpected path: ${path}`)
    })
    render(<App />)
    fireEvent.click(
      await screen.findByRole('button', { name: '새로 시작하기' }),
    )
    await screen.findByRole('heading', { name: '새 초안을 만들지 못했어요' })
    fireEvent.click(
      screen.getByRole('button', { name: '초안 만들기 다시 시도' }),
    )
    await screen.findByRole('region', { name: '학습 입력' })
    expect(creates).toBe(2)
    expect(window.location.search).toBe(`?profile=${profile.id}`)
    expect(sessionStorage.getItem(storageKey)).toBe(profile.id)
    expect(screen.queryByRole('alert')).toBeNull()
    const creation = fetch.mock.calls.filter(
      ([url]) => new URL(url).pathname === '/api/profiles',
    )
    expect(creation[1][1].headers).toBeInstanceOf(Headers)
    expect((creation[1][1].headers as Headers).get('X-CSRF-Token')).toBe(
      'synthetic-csrf',
    )
  })

  it('불러오기 실패 후 같은 초안을 다시 조회하고 URL과 저장 ID를 유지한다', async () => {
    window.history.replaceState(null, '', `/onboarding?profile=${profile.id}`)
    sessionStorage.setItem(storageKey, profile.id)
    let reads = 0
    const fetch = serve(() => {
      reads++
      return reads === 1
        ? Response.json({}, { status: 503 })
        : Response.json(profile)
    })
    render(<App />)
    await screen.findByRole('heading', {
      name: '저장한 초안을 불러오지 못했어요',
    })
    expect(window.location.search).toBe(`?profile=${profile.id}`)
    expect(sessionStorage.getItem(storageKey)).toBe(profile.id)
    fireEvent.click(screen.getByRole('button', { name: '초안 다시 불러오기' }))
    await screen.findByRole('region', { name: '학습 입력' })
    expect(reads).toBe(2)
    expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(
      true,
    )
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([403, 404])(
    '%i 응답에서 다른 초안을 자동 생성하거나 기존 ID를 지우지 않는다',
    async (status) => {
      window.history.replaceState(null, '', `/onboarding?profile=${profile.id}`)
      sessionStorage.setItem(storageKey, profile.id)
      const fetch = serve(() => Response.json({}, { status }))
      render(<App />)
      await screen.findByRole('heading', {
        name: '저장한 초안을 불러오지 못했어요',
      })
      expect(sessionStorage.getItem(storageKey)).toBe(profile.id)
      expect(screen.queryByRole('region', { name: '학습 입력' })).toBeNull()
      expect(fetch.mock.calls.some(([, init]) => init.method === 'POST')).toBe(
        false,
      )
    },
  )

  it('계정에 보관된 초안 링크로 이어가며 새로고침 후에도 같은 초안을 조회한다', async () => {
    sessionStorage.setItem(storageKey, profile.id)
    let reads = 0
    serve((path) => {
      expect(path).toBe(`/api/profiles/${profile.id}`)
      reads++
      return Response.json(profile)
    })
    const view = render(<App />)
    fireEvent.click(
      await screen.findByRole('button', {
        name: '이 브라우저에서 작성하던 입력 이어가기',
      }),
    )
    await screen.findByRole('region', { name: '학습 입력' })
    view.unmount()
    render(<App />)
    await screen.findByRole('region', { name: '학습 입력' })
    expect(reads).toBe(2)
    expect(sessionStorage.getItem(storageKey)).toBe(profile.id)
  })

  it('다른 계정의 초안 링크를 이어가기 버튼으로 표시하지 않는다', async () => {
    sessionStorage.setItem(
      'today-tommorrow:profile:another-user',
      'another-profile',
    )
    serve(() => {
      throw new Error('Profile request must not run')
    })
    render(<App />)
    await screen.findByRole('button', { name: '새로 시작하기' })
    expect(
      screen.queryByRole('button', {
        name: '이 브라우저에서 작성하던 입력 이어가기',
      }),
    ).toBeNull()
  })

  it('생성을 기다리는 동안 중복 요청을 막고 이동 후 늦은 응답으로 화면을 바꾸지 않는다', async () => {
    let complete!: (response: Response) => void
    let creationSignal: AbortSignal | undefined
    const fetch = serve((path, init) => {
      expect(path).toBe('/api/profiles')
      creationSignal = init.signal as AbortSignal
      return new Promise<Response>((resolve) => {
        complete = resolve
      })
    })
    render(<App />)
    const start = await screen.findByRole('button', { name: '새로 시작하기' })
    fireEvent.click(start)
    fireEvent.click(start)
    expect(start.hasAttribute('disabled')).toBe(true)
    expect(screen.getByText('새 초안을 만들고 있어요…')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '시작 화면' }))
    expect(creationSignal?.aborted).toBe(true)
    complete(Response.json(profile, { status: 201 }))
    await waitFor(() =>
      expect(
        screen
          .getByRole('button', { name: '새로 시작하기' })
          .hasAttribute('disabled'),
      ).toBe(false),
    )
    expect(window.location.search).toBe('')
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(
      fetch.mock.calls.filter(
        ([url]) => new URL(url).pathname === '/api/profiles',
      ),
    ).toHaveLength(1)
  })
})
