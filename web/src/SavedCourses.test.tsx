import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { App } from './App'
import { Results, SavedCourses } from './Courses'
import { courseDetailFixture, savedCoursesFixture } from './Courses.fixtures'
import { setCsrfToken } from './api'
import type {
  AuthSession,
  CoursePage,
  CourseSummary,
  RecommendationRun,
} from './types'

// Synthetic L state/HTTP examples. No real account, DB or OAuth login is used.
const course: CourseSummary = {
  ...savedCoursesFixture.items[0],
  id: 'a0000000-0000-4000-8000-000000000001',
  name: '합성 과정 A',
  source_system: 'work24',
}
const other: CourseSummary = {
  ...course,
  id: 'b0000000-0000-4000-8000-000000000002',
  name: '합성 과정 B',
}
const runId = '00000000-0000-4000-8000-000000000003'
const savePath = `/api/me/saved-courses/${course.id}`
const savedPath = '/api/me/saved-courses'
const savedNotice = `${course.name}의 관심 저장 요청을 완료했습니다.`
const deletedNotice = `${course.name}의 관심 해제 요청을 완료했습니다.`
function page(items: CourseSummary[] = [course]): CoursePage {
  return {
    items,
    next_cursor: null,
    catalog_version_id: '00000000-0000-4000-8000-000000000004',
  }
}
function result(value = course, id = runId): RecommendationRun {
  return {
    id,
    profile_revision: 1,
    catalog_version_id: page().catalog_version_id,
    result_state: 'matches',
    explanation_mode: 'llm',
    items: [
      {
        course: value,
        offering: null,
        rank: 1,
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
      },
    ],
    notices: [],
    excluded_counts: {},
    created_at: '2026-11-01T00:00:00Z',
    profile_id: '00000000-0000-4000-8000-000000000005',
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
function session(account: 'A' | 'B'): AuthSession {
  return {
    user: {
      id:
        account === 'A'
          ? '00000000-0000-4000-8000-00000000000a'
          : '00000000-0000-4000-8000-00000000000b',
      display_name: `합성 계정 ${account}`,
      email: `synthetic-${account}@example.com`,
      avatar_url: null,
    },
    csrf_token: `synthetic-csrf-${account}`,
    expires_at: '2099-01-01T00:00:00Z',
  }
}
function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  const fetch = vi.fn((url: string, init: RequestInit) =>
    Promise.resolve(handler(new URL(url).pathname, init)),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function saveButton(value = course) {
  return screen.getByRole('button', { name: `${value.name} 관심 교육 저장` })
}
function deleteButton(value = course) {
  return screen.getByRole('button', { name: `${value.name} 관심 교육 해제` })
}
async function readyResults() {
  const view = render(<Results runId={runId} />)
  await screen.findByRole('heading', { name: course.name })
  return view
}
async function readySaved() {
  const view = render(<SavedCourses />)
  await screen.findByRole('heading', { name: course.name, level: 2 })
  return view
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  setCsrfToken('synthetic-csrf')
  sessionStorage.clear()
  window.history.replaceState(null, '', '/')
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
  sessionStorage.clear()
  window.history.replaceState(null, '', '/')
})

describe('관심 저장·해제 요청과 상태', () => {
  it('PUT 중복 클릭을 막고 cookie·CSRF로 성공을 확인한 뒤 해제 버튼으로 바꾼다', async () => {
    let complete!: (response: Response) => void
    const fetch = serve((path, init) => {
      if (path.startsWith('/api/recommendations/'))
        return Response.json(result())
      expect(path).toBe(savePath)
      expect(init.method).toBe('PUT')
      expect(init.credentials).toBe('include')
      expect((init.headers as Headers).get('X-CSRF-Token')).toBe(
        'synthetic-csrf',
      )
      expect(init.body).toBeUndefined()
      return new Promise<Response>((resolve) => {
        complete = resolve
      })
    })
    await readyResults()
    const button = saveButton()
    fireEvent.click(button)
    fireEvent.click(button)
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(screen.queryByText(savedNotice)).toBeNull()
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'PUT'),
    ).toHaveLength(1)
    complete(new Response(null, { status: 204 }))
    await screen.findByText(savedNotice)
    expect(deleteButton()).toBeTruthy()
  })

  it.each([403, 503])(
    'PUT %i 실패는 저장 상태로 바꾸지 않고 같은 PUT으로 재시도한다',
    async (status) => {
      let writes = 0
      const fetch = serve((path, init) => {
        if (path.startsWith('/api/recommendations/'))
          return Response.json(result())
        expect(init.method).toBe('PUT')
        return ++writes === 1
          ? Response.json({}, { status })
          : new Response(null, { status: 204 })
      })
      await readyResults()
      fireEvent.click(saveButton())
      await screen.findByRole('alert')
      expect(saveButton()).toBeTruthy()
      expect(screen.queryByText(savedNotice)).toBeNull()
      fireEvent.click(saveButton())
      await screen.findByText(savedNotice)
      expect(
        fetch.mock.calls.filter(([, init]) => init.method === 'PUT'),
      ).toHaveLength(2)
    },
  )

  it('CSRF가 없으면 저장 요청을 전송하거나 성공으로 표시하지 않는다', async () => {
    setCsrfToken(null)
    const fetch = serve(() => Response.json(result()))
    await readyResults()
    fireEvent.click(saveButton())
    expect((await screen.findByRole('alert')).textContent).toContain(
      '다시 로그인해주세요',
    )
    expect(fetch.mock.calls.every(([, init]) => init.method === 'GET')).toBe(
      true,
    )
    expect(screen.queryByText(savedNotice)).toBeNull()
  })

  it('저장한 교육을 재진입 GET으로 확인하고 DELETE 후 목록을 재조회한다', async () => {
    let saved = false
    const fetch = serve((path, init) => {
      if (path.startsWith('/api/recommendations/'))
        return Response.json(result())
      if (path === savedPath) return Response.json(page(saved ? [course] : []))
      expect(path).toBe(savePath)
      saved = init.method === 'PUT'
      return new Response(null, { status: 204 })
    })
    const view = await readyResults()
    fireEvent.click(saveButton())
    await screen.findByText(savedNotice)
    view.unmount()
    await readySaved()
    fireEvent.click(deleteButton())
    await screen.findByText('저장한 교육이 없습니다.')
    expect(screen.getByText(deletedNotice)).toBeTruthy()
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'PUT'),
    ).toHaveLength(1)
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'DELETE'),
    ).toHaveLength(1)
    expect(
      fetch.mock.calls.filter(([url]) => new URL(url).pathname === savedPath),
    ).toHaveLength(2)
  })

  it('DELETE를 기다리는 동안 중복 요청과 선제 제거를 막는다', async () => {
    let complete!: (response: Response) => void
    let saved = true
    const fetch = serve((path, init) => {
      if (path === savedPath) return Response.json(page(saved ? [course] : []))
      expect(init.method).toBe('DELETE')
      expect(init.credentials).toBe('include')
      expect((init.headers as Headers).get('X-CSRF-Token')).toBe(
        'synthetic-csrf',
      )
      return new Promise<Response>((resolve) => {
        complete = resolve
      })
    })
    await readySaved()
    const button = deleteButton()
    fireEvent.click(button)
    fireEvent.click(button)
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(
      screen.getByRole('heading', { name: course.name, level: 2 }),
    ).toBeTruthy()
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'DELETE'),
    ).toHaveLength(1)
    saved = false
    complete(new Response(null, { status: 204 }))
    await screen.findByText('저장한 교육이 없습니다.')
  })
})

describe('관심 목록 오류와 해제 완료 안내', () => {
  it('해제 성공 뒤 목록 GET이 실패해도 해제 완료와 조회 오류를 구분한다', async () => {
    let reads = 0
    const fetch = serve((path, init) => {
      if (init.method === 'DELETE') return new Response(null, { status: 204 })
      expect(path).toBe(savedPath)
      reads++
      return reads === 1
        ? Response.json(page())
        : reads === 2
          ? Response.json({}, { status: 503 })
          : Response.json(page([]))
    })
    await readySaved()
    fireEvent.click(deleteButton())
    await screen.findByRole('alert')
    expect(screen.getByText(deletedNotice)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await screen.findByText('저장한 교육이 없습니다.')
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'DELETE'),
    ).toHaveLength(1)
    expect(reads).toBe(3)
  })

  it('DELETE 실패는 선택 상세와 목록을 유지하고 성공 후에만 닫고 재조회한다', async () => {
    let writes = 0
    let reads = 0
    const fetch = serve((path, init) => {
      if (path === savedPath) {
        reads++
        return Response.json(page(writes >= 2 ? [] : [course]))
      }
      if (path === `/api/courses/${course.id}`)
        return Response.json({ ...courseDetailFixture, ...course })
      expect(path).toBe(savePath)
      expect(init.method).toBe('DELETE')
      return ++writes === 1
        ? Response.json({}, { status: 403 })
        : new Response(null, { status: 204 })
    })
    await readySaved()
    fireEvent.click(screen.getByRole('button', { name: '교육 상세' }))
    await within(screen.getByRole('region', { name: '교육 상세' })).findByRole(
      'heading',
      { name: course.name, level: 3 },
    )
    fireEvent.click(deleteButton())
    await screen.findByRole('alert')
    expect(reads).toBe(1)
    expect(screen.getByRole('region', { name: '교육 상세' })).toBeTruthy()
    fireEvent.click(deleteButton())
    await screen.findByText('저장한 교육이 없습니다.')
    expect(screen.queryByRole('region', { name: '교육 상세' })).toBeNull()
    expect(
      fetch.mock.calls.filter(([, init]) => init.method === 'DELETE'),
    ).toHaveLength(2)
  })

  it.each([401, 403])(
    '목록 %i를 빈 성공이나 임의 목록으로 바꾸지 않는다',
    async (status) => {
      const dispatch = vi.spyOn(window, 'dispatchEvent')
      serve(() => Response.json({}, { status }))
      render(<SavedCourses />)
      await screen.findByRole('alert')
      expect(screen.queryByText('저장한 교육이 없습니다.')).toBeNull()
      expect(screen.queryByRole('heading', { name: course.name })).toBeNull()
      expect(
        dispatch.mock.calls.some(([event]) => event.type === 'session-expired'),
      ).toBe(status === 401)
    },
  )
})

describe('화면·계정 변경 뒤 늦은 응답 격리', () => {
  it('다른 결과로 이동한 뒤 이전 PUT 완료가 새 버튼 상태를 바꾸지 않는다', async () => {
    const nextId = '00000000-0000-4000-8000-000000000006'
    let complete!: (response: Response) => void
    let signal: AbortSignal | null | undefined
    serve((path, init) => {
      if (path.startsWith('/api/recommendations/'))
        return Response.json(
          path.endsWith(nextId) ? result(other, nextId) : result(),
        )
      signal = init.signal
      return new Promise<Response>((resolve) => {
        complete = resolve
      })
    })
    const view = await readyResults()
    fireEvent.click(saveButton())
    view.rerender(<Results runId={nextId} />)
    await screen.findByRole('heading', { name: other.name })
    expect(signal?.aborted).toBe(true)
    await act(async () => complete(new Response(null, { status: 204 })))
    expect(saveButton(other)).toBeTruthy()
    expect(screen.queryByText(savedNotice)).toBeNull()
  })

  it('로그아웃·다른 합성 계정 진입 뒤 이전 DELETE 완료가 새 목록에 영향을 주지 않는다', async () => {
    let account: 'A' | 'B' = 'A'
    let complete!: (response: Response) => void
    let signal: AbortSignal | null | undefined
    const fetch = serve((path, init) => {
      if (path === '/api/auth/me') return Response.json(session(account))
      if (path === '/api/auth/logout') {
        account = 'B'
        return new Response(null, { status: 204 })
      }
      if (path === savedPath)
        return Response.json(page(account === 'A' ? [course] : [other]))
      expect(path).toBe(savePath)
      expect(init.method).toBe('DELETE')
      signal = init.signal
      return new Promise<Response>((resolve) => {
        complete = resolve
      })
    })
    render(<App />)
    await screen.findByText('합성 계정 A')
    fireEvent.click(screen.getByRole('button', { name: '관심 교육' }))
    await screen.findByRole('heading', { name: course.name, level: 2 })
    fireEvent.click(deleteButton())
    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))
    await screen.findByRole('link', { name: 'Google로 시작하기' })
    expect(signal?.aborted).toBe(true)
    fireEvent.click(
      screen.getByRole('button', { name: '로그인 상태 다시 확인' }),
    )
    await screen.findByText('합성 계정 B')
    fireEvent.click(screen.getByRole('button', { name: '관심 교육' }))
    await screen.findByRole('heading', { name: other.name, level: 2 })
    await act(async () => complete(new Response(null, { status: 204 })))
    expect(screen.queryByRole('heading', { name: course.name })).toBeNull()
    expect(screen.queryByText(deletedNotice)).toBeNull()
    expect(
      fetch.mock.calls.filter(([url]) => new URL(url).pathname === savedPath),
    ).toHaveLength(2)
  })
})
