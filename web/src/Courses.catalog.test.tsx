import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { CourseDetails } from './Courses'
import { courseDetailFixture } from './Courses.fixtures'
import type { CourseDetail, Offering } from './types'

// Synthetic HTTP examples, not rows imported from the Work24 package.
const course: CourseDetail = {
  ...courseDetailFixture,
  id: '00000000-0000-4000-8000-000000000001',
  name: '합성 고용24 과정',
  source_system: 'work24',
  course_type: 'vocational_training',
  detail_url: 'https://example.com/work24/synthetic-course',
  evidence: courseDetailFixture.evidence.map((item) => ({
    ...item,
    source_system: 'work24',
  })),
  offerings: [
    {
      ...courseDetailFixture.offerings[0],
      id: '00000000-0000-4000-8000-000000000002',
      delivery_mode: 'offline',
      region: '서울',
      start_date: '2026-12-01',
      end_date: '2026-12-31',
      enrollment_start: '2026-11-01',
      enrollment_end: '2026-11-20',
      training_hours: 120,
      recruitment_status: 'open',
    },
  ],
}

function respond(value: CourseDetail) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(Response.json(value))),
  )
}
async function show(offering: Partial<Offering> = {}) {
  respond({ ...course, offerings: [{ ...course.offerings[0], ...offering }] })
  render(<CourseDetails courseId={course.id} />)
  await screen.findByRole('heading', { name: course.name })
}
function fact(label: string) {
  return screen.getByText(label).parentElement!
}
beforeEach(() => vi.stubEnv('VITE_API_BASE_URL', '/api'))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('고용24 교육 정보의 단위와 결측 표시', () => {
  it('원천과 목록 기준의 확인 범위를 표시하고 원문 출처를 유지한다', async () => {
    await show()
    expect(screen.getByText('고용24')).toBeTruthy()
    expect(screen.getByText('목록 정보 기준 · 상세 확인 필요')).toBeTruthy()
    expect(screen.queryByText('상세 정보 확인 자료')).toBeNull()
    expect(
      screen.getByRole('link', { name: '교육 원문 보기' }).getAttribute('href'),
    ).toBe(course.detail_url)
    expect(screen.getByText('미확인 항목: 교육비, 수업 일정')).toBeTruthy()
  })

  it('상세 확인 범위는 계약의 evidence_level이 명시한 경우에만 표시한다', async () => {
    respond({ ...course, evidence_level: 'detail_verified' })
    render(<CourseDetails courseId={course.id} />)
    await screen.findByText('상세 정보 확인 자료')
    expect(screen.queryByText('목록 정보 기준 · 상세 확인 필요')).toBeNull()
  })

  it('교육 기간과 신청 기간을 구분해 원래 날짜를 표시한다', async () => {
    await show()
    expect(
      within(fact('교육 기간')).getByText('2026-12-01 ~ 2026-12-31'),
    ).toBeTruthy()
    expect(
      within(fact('신청 기간')).getByText('2026-11-01 ~ 2026-11-20'),
    ).toBeTruthy()
    expect(within(fact('총 교육 시간')).getByText('120시간')).toBeTruthy()
  })

  it.each([0, 38.5, 1200])(
    '교육 시간 %s에 단위를 표시하며 0을 결측으로 바꾸지 않는다',
    async (hours) => {
      await show({ training_hours: hours })
      expect(
        within(fact('총 교육 시간')).getByText(
          `${hours.toLocaleString('ko-KR')}시간`,
        ),
      ).toBeTruthy()
    },
  )

  it('null 시간과 신청 기간을 0시간이나 교육 기간으로 채우지 않는다', async () => {
    await show({
      training_hours: null,
      enrollment_start: null,
      enrollment_end: null,
    })
    expect(within(fact('총 교육 시간')).getByText('미확인')).toBeTruthy()
    expect(within(fact('총 교육 시간')).queryByText('0시간')).toBeNull()
    expect(within(fact('신청 기간')).getByText('미확인 ~ 미확인')).toBeTruthy()
    expect(
      within(fact('교육 기간')).getByText('2026-12-01 ~ 2026-12-31'),
    ).toBeTruthy()
  })

  it('신청 마감일이 없으면 시작일이나 교육 종료일로 추정하지 않는다', async () => {
    await show({ enrollment_end: null })
    expect(
      within(fact('신청 기간')).getByText('2026-11-01 ~ 미확인'),
    ).toBeTruthy()
  })

  it('공시 자부담과 모집 상태를 개인 최종 가격·실시간 정보로 표시하지 않는다', async () => {
    await show({ tuition_krw: 100000, listed_self_pay_krw: 50000 })
    expect(
      within(fact('교육비 / 공시 자부담')).getByText('100,000원 / 50,000원'),
    ).toBeTruthy()
    expect(
      screen.getByText('공시 자부담은 개인별 최종 부담금과 다를 수 있습니다.'),
    ).toBeTruthy()
    expect(
      within(fact('모집 상태 (확인 시점 기준)')).getByText('모집 중'),
    ).toBeTruthy()
    expect(
      within(fact('확인 시각')).getByText('2026-11-01T00:00:00Z'),
    ).toBeTruthy()
    expect(screen.queryByText('현재 모집 중')).toBeNull()
  })

  it('알 수 없는 확인 범위를 상세 확인 자료로 승격하지 않는다', async () => {
    const invalid = { ...course, evidence_level: 'unrecognized' }
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(Response.json(invalid))),
    )
    render(<CourseDetails courseId={course.id} />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('heading', { name: course.name })).toBeNull()
    expect(screen.queryByText('상세 정보 확인 자료')).toBeNull()
  })
})
