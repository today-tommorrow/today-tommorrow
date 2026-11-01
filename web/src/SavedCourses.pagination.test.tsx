import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SavedCourses } from './Courses'
import { setCsrfToken } from './api'
import { savedCoursesFixture } from './Courses.fixtures'
import type { SavedCourse, SavedCoursePage } from './types'

const version = savedCoursesFixture.catalog_version_id
function course(id: string): SavedCourse {
  return { ...savedCoursesFixture.items[0], id, name: `합성 교육 ${id}` }
}
function page(ids: string[], cursor: string | null = null): SavedCoursePage {
  return {
    items: ids.map(course),
    next_cursor: cursor,
    catalog_version_id: version,
  }
}
function serve(
  handler: (url: URL, method: string) => Response | Promise<Response>,
) {
  const fetch = vi.fn((url: string, init: RequestInit) =>
    Promise.resolve(handler(new URL(url), init.method ?? 'GET')),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  setCsrfToken('synthetic-csrf')
})
afterEach(() => {
  cleanup()
  setCsrfToken(null)
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

it('다음 페이지를 이어 붙이고 중복 과정을 한 번만 표시한다', async () => {
  const fetch = serve((url) =>
    Response.json(
      url.searchParams.has('cursor')
        ? page(['A', 'B'])
        : page(['A'], 'opaque+/='),
    ),
  )
  render(<SavedCourses />)
  await screen.findByRole('heading', { name: '합성 교육 A' })
  fireEvent.click(screen.getByRole('button', { name: '관심 교육 더 보기' }))
  await screen.findByRole('heading', { name: '합성 교육 B' })
  expect(screen.getAllByRole('heading', { name: '합성 교육 A' })).toHaveLength(
    1,
  )
  expect(new URL(fetch.mock.calls[1][0]).searchParams.get('cursor')).toBe(
    'opaque+/=',
  )
  expect(screen.queryByRole('button', { name: '관심 교육 더 보기' })).toBeNull()
})

it('다음 페이지 실패는 기존 목록을 보존하며 같은 cursor로 재시도한다', async () => {
  let attempts = 0
  serve((url) => {
    if (!url.search) return Response.json(page(['A'], 'next'))
    expect(url.searchParams.get('cursor')).toBe('next')
    return ++attempts === 1
      ? Response.json({}, { status: 503 })
      : Response.json(page(['B']))
  })
  render(<SavedCourses />)
  fireEvent.click(
    await screen.findByRole('button', { name: '관심 교육 더 보기' }),
  )
  await screen.findByRole('alert')
  expect(screen.getByRole('heading', { name: '합성 교육 A' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
  await screen.findByRole('heading', { name: '합성 교육 B' })
  expect(attempts).toBe(2)
})

it.each(['409', 'changed-version'])(
  '카탈로그 변경 %s 뒤 첫 페이지부터 다시 조회한다',
  async (mode) => {
    let firstReads = 0
    serve((url) => {
      if (url.search)
        return mode === '409'
          ? Response.json({}, { status: 409 })
          : Response.json({ ...page(['B']), catalog_version_id: 'new' })
      return Response.json(
        ++firstReads === 1 ? page(['A'], 'next') : page(['C']),
      )
    })
    render(<SavedCourses />)
    fireEvent.click(
      await screen.findByRole('button', { name: '관심 교육 더 보기' }),
    )
    await screen.findByRole('alert')
    expect(screen.queryByRole('heading', { name: '합성 교육 A' })).toBeNull()
    expect(screen.queryByRole('heading', { name: '합성 교육 B' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await screen.findByRole('heading', { name: '합성 교육 C' })
  },
)

it('해제는 다음 페이지 실패 상태에서도 첫 페이지부터 새로 조회한다', async () => {
  let deleted = false
  serve((url, method) => {
    if (method === 'DELETE') {
      deleted = true
      return new Response(null, { status: 204 })
    }
    if (url.search) return Response.json({}, { status: 503 })
    return Response.json(deleted ? page(['B']) : page(['A'], 'next'))
  })
  render(<SavedCourses />)
  fireEvent.click(
    await screen.findByRole('button', { name: '관심 교육 더 보기' }),
  )
  await screen.findByRole('alert')
  fireEvent.click(
    screen.getByRole('button', { name: '합성 교육 A 관심 교육 해제' }),
  )
  await screen.findByRole('heading', { name: '합성 교육 B' })
  expect(screen.queryByRole('heading', { name: '합성 교육 A' })).toBeNull()
})

it('현재 없는 과정은 저장 당시 정보와 제공되지 않음 상태를 표시한다', async () => {
  serve(() =>
    Response.json({
      ...page([]),
      items: [{ ...course('A'), catalog_status: 'unavailable' }],
    }),
  )
  render(<SavedCourses />)
  await screen.findByText('현재 제공되지 않음 · 저장 당시 정보를 표시합니다.')
  expect(screen.getByRole('button', { name: '교육 상세' })).toHaveProperty(
    'disabled',
    true,
  )
  expect(
    screen.getByRole('button', { name: '합성 교육 A 관심 교육 해제' }),
  ).toBeTruthy()
})

it('갱신된 과정은 현재 정보임을 명시한다', async () => {
  serve(() =>
    Response.json({
      ...page([]),
      items: [{ ...course('A'), catalog_status: 'changed' }],
    }),
  )
  render(<SavedCourses />)
  await screen.findByText(
    '저장 이후 교육 목록이 갱신되어 현재 정보를 표시합니다.',
  )
})

it('불완전한 페이지 응답을 빈 목록 성공으로 숨기지 않는다', async () => {
  serve(() => Response.json({ items: [] }))
  render(<SavedCourses />)
  await screen.findByRole('alert')
  expect(screen.queryByText('저장한 교육이 없습니다.')).toBeNull()
})
