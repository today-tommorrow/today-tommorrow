import { readFileSync } from 'node:fs'
import { afterAll, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SaveCourse, SavedCourses } from '../src/Courses'
import { setCsrfToken } from '../src/api'

const session = JSON.parse(readFileSync(process.env.TEST_SESSION_FILE!, 'utf8'))
const course = session.courses[0]
const realFetch = globalThis.fetch
const calls: Array<{ method: string; path: string; status: number }> = []
vi.stubEnv('VITE_API_BASE_URL', '/api')
vi.stubGlobal('fetch', async (url: string, init: RequestInit = {}) => {
  const source = new URL(url)
  const target = new URL(source.pathname + source.search, session.api_base_url)
  const headers = new Headers(init.headers)
  headers.set('Cookie', `${session.cookie_name}=${session.owner.token}`)
  headers.set('Origin', session.origin)
  const response = await realFetch(target, { ...init, headers })
  calls.push({
    method: init.method || 'GET',
    path: source.pathname,
    status: response.status,
  })
  return response
})
setCsrfToken(session.owner.csrf_token)
afterAll(() => {
  cleanup()
  setCsrfToken(null)
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

it('saves the catalog course, reloads the real list, and removes it', async () => {
  const save = render(
    <SaveCourse courseId={course.id} courseName={course.name} />,
  )
  fireEvent.click(
    screen.getByRole('button', { name: `${course.name} 관심 교육 저장` }),
  )
  await screen.findByText(`${course.name}의 관심 저장 요청을 완료했습니다.`)
  save.unmount()
  const list = render(<SavedCourses />)
  await screen.findByRole('heading', { name: course.name, level: 2 })
  list.unmount()
  render(<SavedCourses />)
  await screen.findByRole('heading', { name: course.name, level: 2 })
  fireEvent.click(
    screen.getByRole('button', { name: `${course.name} 관심 교육 해제` }),
  )
  await screen.findByText(`${course.name}의 관심 해제 요청을 완료했습니다.`)
  await screen.findByText('저장한 교육이 없습니다.')
  expect(calls.filter((c) => c.method === 'PUT').map((c) => c.status)).toEqual([
    204,
  ])
  expect(
    calls.filter((c) => c.method === 'DELETE').map((c) => c.status),
  ).toEqual([204])
  expect(
    calls.filter(
      (c) => c.method === 'GET' && c.path === '/api/me/saved-courses',
    ).length,
  ).toBeGreaterThanOrEqual(3)
})
