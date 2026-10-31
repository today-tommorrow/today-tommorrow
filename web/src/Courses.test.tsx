import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { CourseDetails, SavedCourses } from './Courses'
import {
  courseDetailFixture,
  courseNotFoundFixture,
  emptySavedCoursesFixture,
  savedCoursesFixture,
} from './Courses.fixtures'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function respond(body: unknown, status = 200) {
  const fetch = vi.fn().mockResolvedValue(Response.json(body, { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

// These HTTP boundary samples test UI contracts, not live API/DB/model E2E.
describe('교육 조회의 공통 계약 샘플', () => {
  it('상세 성공 응답의 출처와 미확인 조건을 보존한다', async () => {
    const fetch = respond(courseDetailFixture)
    render(<CourseDetails courseId={courseDetailFixture.id} />)

    await screen.findByRole('heading', { name: courseDetailFixture.name })
    expect(new URL(fetch.mock.calls[0][0]).pathname).toBe(
      `/api/courses/${courseDetailFixture.id}`,
    )
    expect(fetch.mock.calls[0][1].credentials).toBe('include')
    expect(screen.getByText('교육기관 미확인')).toBeTruthy()
    expect(screen.getByText('교육 설명 미확인')).toBeTruthy()
    expect(screen.getByText('미확인 항목: tuition_krw, schedule')).toBeTruthy()
    expect(screen.getByText('합성 자료의 교육명')).toBeTruthy()
    expect(screen.getByText(/kmooc · observed · 2026-11-01/)).toBeTruthy()
    expect(
      screen.getByRole('link', { name: '교육 원문 보기' }).getAttribute('href'),
    ).toBe(courseDetailFixture.detail_url)
    expect(
      screen.getByRole('link', { name: '근거 원문 보기' }).getAttribute('href'),
    ).toBe(courseDetailFixture.evidence[0].source_url)
  })

  it('null 교육비와 온라인 방식을 무료·자율 수강으로 추정하지 않는다', async () => {
    respond(courseDetailFixture)
    render(<CourseDetails courseId={courseDetailFixture.id} />)
    await screen.findByRole('heading', { name: courseDetailFixture.name })

    const facts = screen.getByText('교육비 / 공시 자부담').closest('dl')!
    expect(within(facts).getByText('미확인 / 미확인')).toBeTruthy()
    expect(within(facts).getByText('온라인')).toBeTruthy()
    const asynchronous = within(facts).getByText('자율 수강').parentElement!
    expect(within(asynchronous).getByText('미확인')).toBeTruthy()
    expect(within(facts).queryByText(/무료|0원|가능/)).toBeNull()
  })

  it('실제 0원과 명시된 자율 수강 가능 상태는 표시한다', async () => {
    respond({
      ...courseDetailFixture,
      offerings: [
        {
          ...courseDetailFixture.offerings[0],
          tuition_krw: 0,
          listed_self_pay_krw: 0,
          schedule: {
            days: [],
            start_time: null,
            end_time: null,
            timezone: 'Asia/Seoul',
            is_asynchronous: true,
          },
        },
      ],
    })
    render(<CourseDetails courseId={courseDetailFixture.id} />)
    expect(await screen.findByText('0원 / 0원')).toBeTruthy()
    expect(screen.getByText('가능')).toBeTruthy()
  })

  it('관심 교육 성공 목록을 표시한다', async () => {
    const fetch = respond(savedCoursesFixture)
    render(<SavedCourses />)
    expect(
      await screen.findByRole('heading', { name: courseDetailFixture.name }),
    ).toBeTruthy()
    expect(new URL(fetch.mock.calls[0][0]).pathname).toBe(
      '/api/me/saved-courses',
    )
    expect(screen.queryByText('저장한 교육이 없습니다.')).toBeNull()
  })

  it('빈 목록을 오류와 구분하고 가상 교육을 채우지 않는다', async () => {
    respond(emptySavedCoursesFixture)
    render(<SavedCourses />)
    expect(await screen.findByText('저장한 교육이 없습니다.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: '교육 상세' })).toBeNull()
  })

  it('404는 성공이나 빈 목록으로 바꾸지 않고 재시도할 수 있다', async () => {
    const fetch = respond(courseNotFoundFixture, 404)
    render(<CourseDetails courseId={courseDetailFixture.id} />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      '요청한 기능이나 자료를 서버에서 찾을 수 없습니다.',
    )
    expect(screen.queryByText(courseDetailFixture.name)).toBeNull()

    fetch.mockResolvedValue(Response.json(courseDetailFixture))
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    expect(
      await screen.findByRole('heading', { name: courseDetailFixture.name }),
    ).toBeTruthy()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('요청한 ID와 다른 교육 상세를 표시하지 않는다', async () => {
    respond({ ...courseDetailFixture, id: 'different-course' })
    render(<CourseDetails courseId={courseDetailFixture.id} />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      '요청한 교육과 상세 결과가 다릅니다.',
    )
    expect(screen.queryByText(courseDetailFixture.name)).toBeNull()
  })

  it('가상 데이터 응답을 실제 교육으로 표시하지 않는다', async () => {
    respond({ ...courseDetailFixture, is_demo: true })
    render(<CourseDetails courseId={courseDetailFixture.id} />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      '실제 교육 데이터가 아닌 응답은 표시하지 않습니다.',
    )
    expect(screen.queryByText(courseDetailFixture.name)).toBeNull()
  })
})
