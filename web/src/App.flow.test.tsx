import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { App } from './App'
import { setCsrfToken } from './api'
import {
  choices,
  createFlowFixture,
  flowCourses,
  flowId,
  flowIds,
} from './App.flow.fixtures'
import type { Answers } from './types'

// Complete App/component wiring against scripted HTTP responses in jsdom.
// This is not browser E2E, Google OAuth, a real backend or model evidence.
function button(name: string) {
  return screen.getByRole('button', { name }) as HTMLButtonElement
}
async function clickReady(name: string) {
  await screen.findByRole('button', { name })
  await waitFor(() => expect(button(name).matches(':disabled')).toBe(false))
  fireEvent.click(button(name))
}
async function formReady() {
  await waitFor(() =>
    expect(
      (screen.getByRole('group', { name: '자료 추가' }) as HTMLFieldSetElement)
        .disabled,
    ).toBe(false),
  )
}
function requests(
  fixture: ReturnType<typeof createFlowFixture>,
  suffix: string,
  method?: string,
) {
  return fixture.fetch.mock.calls.filter(
    ([url, init]) =>
      new URL(url).pathname.endsWith(suffix) &&
      (!method || init.method === method),
  )
}
async function start(fixture: ReturnType<typeof createFlowFixture>) {
  vi.stubGlobal('fetch', fixture.fetch)
  const view = render(<App />)
  await clickReady('새로 시작하기')
  await screen.findByRole('region', { name: '학습 입력' })
  expect(fixture.profile().answers).toEqual({})
  expect(requests(fixture, '/submit')).toHaveLength(0)
  expect(requests(fixture, `/${flowIds.run}`)).toHaveLength(0)
  await clickReady('다음 질문 받기')
  return view
}
async function answerRange(
  fixture: ReturnType<typeof createFlowFixture>,
  from: number,
  until: number,
) {
  for (let index = from; index < until; index++) {
    await clickReady(choices[index].label)
    await waitFor(() =>
      expect(fixture.profile().selection_summaries).toHaveLength(index + 1),
    )
  }
}
async function readyForSubmission(
  fixture: ReturnType<typeof createFlowFixture>,
) {
  await answerRange(fixture, 3, choices.length)
  await screen.findByRole('heading', { name: '입력한 내용을 확인해 주세요' })
  await waitFor(() =>
    expect(button('확인하고 교육 추천 받기').disabled).toBe(false),
  )
  expect(requests(fixture, '/submit')).toHaveLength(0)
  expect(requests(fixture, `/${flowIds.run}`)).toHaveLength(0)
  const selected = Object.fromEntries(
    choices.map((choice) => [choice.slot, choice.value]),
  )
  expect(fixture.profile().answers).toEqual(expect.objectContaining(selected))
}
async function withoutDocuments(fixture: ReturnType<typeof createFlowFixture>) {
  const view = await start(fixture)
  await answerRange(fixture, 0, 3)
  await clickReady('자료 없이 진행')
  await waitFor(() =>
    expect(fixture.profile().answers.document_decision).toBe('none'),
  )
  await clickReady('다음 질문 받기')
  await readyForSubmission(fixture)
  return view
}
async function submit(fixture: ReturnType<typeof createFlowFixture>) {
  await clickReady('확인하고 교육 추천 받기')
  await screen.findByRole('heading', { name: '일부 조건 확인이 필요한 교육' })
  expect(window.location.search).toBe(`?run=${flowIds.run}`)
  expect(screen.queryByRole('region', { name: '학습 입력' })).toBeNull()
  expect(screen.queryByRole('button', { name: '다음 질문 받기' })).toBeNull()
  expect(fixture.run()?.profile_snapshot).toEqual(fixture.profile().answers)
}
async function detailsCompareAndSaved(
  fixture: ReturnType<typeof createFlowFixture>,
) {
  const first = flowCourses[0]
  const result = screen.getByRole('region', { name: '추천 결과' })
  const firstCard = within(result)
    .getByRole('heading', { name: first.name })
    .closest('article')!
  fireEvent.click(within(firstCard).getByRole('button', { name: '교육 상세' }))
  const detail = await screen.findByRole('region', { name: '교육 상세' })
  await within(detail).findByText(first.description!)
  expect(
    (
      within(detail).getByRole('link', {
        name: '교육 원문 보기',
      }) as HTMLAnchorElement
    ).href,
  ).toBe(first.detail_url)
  await clickReady('상세 닫기')
  for (const course of flowCourses)
    fireEvent.click(
      screen.getByRole('checkbox', { name: `${course.name} 비교에 추가` }),
    )
  await clickReady('선택한 교육 비교')
  const compared = await screen.findByRole('region', { name: '교육 비교 결과' })
  for (const course of flowCourses)
    await within(compared).findByRole('heading', { name: course.name })
  const [compareUrl] = requests(fixture, '/courses/compare')[0]
  expect(new URL(compareUrl).searchParams.get('ids')).toBe(
    flowCourses.map((course) => course.id).join(','),
  )
  await clickReady('비교 닫기')
  await clickReady(`${first.name} 관심 교육 저장`)
  await screen.findByText(`${first.name}의 관심 저장 요청을 완료했습니다.`)
  await clickReady('관심 교육')
  await screen.findByRole('heading', { name: first.name })
  expect(window.location.search).toBe('?saved=1')
  await clickReady('교육 상세')
  await within(screen.getByRole('region', { name: '교육 상세' })).findByText(
    first.description!,
  )
  await clickReady('상세 닫기')
  await clickReady(`${first.name} 관심 교육 해제`)
  await screen.findByText('저장한 교육이 없습니다.')
  expect(requests(fixture, `/saved-courses/${first.id}`, 'PUT')).toHaveLength(1)
  expect(
    requests(fixture, `/saved-courses/${first.id}`, 'DELETE'),
  ).toHaveLength(1)
  expect(requests(fixture, '/questions', 'POST')).toHaveLength(choices.length)
  await clickReady('로그아웃')
  await screen.findByRole('link', { name: 'Google로 시작하기' })
  expect(screen.queryByRole('button', { name: '새로 시작하기' })).toBeNull()
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  sessionStorage.clear()
  window.history.replaceState(null, '', '/onboarding')
  setCsrfToken(null)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  sessionStorage.clear()
  setCsrfToken(null)
  window.history.replaceState(null, '', '/')
})

describe('합성 HTTP 경계의 App 연결 회귀', () => {
  it('A: 자료 없음·미정·온라인에서 제출→결과→상세/비교/관심→로그아웃까지 연결한다', async () => {
    const fixture = createFlowFixture()
    await withoutDocuments(fixture)
    const expected: Answers = {
      ...Object.fromEntries(
        choices.map((choice) => [choice.slot, choice.value]),
      ),
      document_decision: 'none',
    }
    expect(fixture.profile().answers).toEqual(expected)
    expect(fixture.profile().documents).toEqual([])
    expect(
      sessionStorage.getItem(`today-tommorrow:profile:${flowIds.user}`),
    ).toBe(flowIds.profile)
    await submit(fixture)
    await detailsCompareAndSaved(fixture)
  })

  it('B: 파일·공개 JD 인용을 확인하고 주말 제한을 수락한 뒤 같은 결과 흐름을 완료한다', async () => {
    const fixture = createFlowFixture()
    await start(fixture)
    await answerRange(fixture, 0, 3)
    await screen.findByRole('button', { name: '자료 없이 진행' })
    await formReady()
    const file = new File(['검증용 합성 이력서 본문'], 'synthetic-resume.txt', {
      type: 'text/plain',
    })
    fireEvent.change(screen.getByLabelText('파일'), {
      target: { files: [file] },
    })
    await clickReady('파일 등록')
    await screen.findByText('합성 이력서 원문 인용', {
      selector: '.document-card blockquote',
    })
    await formReady()
    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'job_description' },
    })
    fireEvent.change(screen.getByLabelText('공개 자료 주소'), {
      target: { value: 'https://example.org/synthetic-jd' },
    })
    await waitFor(() =>
      expect(
        (
          screen.getByRole('group', {
            name: '자료 추가',
          }) as HTMLFieldSetElement
        ).disabled,
      ).toBe(false),
    )
    await clickReady('링크 등록')
    await screen.findByText('합성 JD 원문 인용', {
      selector: '.document-card blockquote',
    })
    await formReady()
    expect(fixture.profile().answers.constraints_text).toBeUndefined()
    fireEvent.click(screen.getByText('직접 알려줄게요'))
    fireEvent.change(screen.getByLabelText('내 경험이나 원하는 배움'), {
      target: { value: '주말에만 수강할 수 있어요.' },
    })
    await waitFor(() =>
      expect(button('입력과 준비된 자료 해석하기').disabled).toBe(false),
    )
    await clickReady('입력과 준비된 자료 해석하기')
    await screen.findByRole('region', { name: '입력 해석 확인' })
    expect(fixture.profile().answers.constraints_text).toBeUndefined()
    fireEvent.click(screen.getByRole('radio', { name: '맞아요' }))
    await clickReady('확인한 제안만 반영')
    await screen.findByText('제안 확인 결과를 저장했어요.')
    await formReady()
    expect(fixture.profile().answers.constraints_text).toBe(
      '주말에만 수강할 수 있어요.',
    )
    for (const [label, state] of [
      ['파일에서 추출한 합성 경험', 'have'],
      ['JD에서 추출한 합성 학습', 'want_to_learn'],
    ]) {
      const group = screen.getByRole('group', { name: label })
      fireEvent.change(within(group).getByLabelText('나의 상태'), {
        target: { value: state },
      })
    }
    await clickReady('확인 내용 저장')
    await screen.findByText('확인 내용을 저장했어요.')
    expect(fixture.profile().reviewed_items.map((item) => item.state)).toEqual([
      'have',
      'want_to_learn',
    ])
    await clickReady('다음 질문 받기')
    await readyForSubmission(fixture)
    expect(fixture.profile().answers).toEqual({
      ...Object.fromEntries(
        choices.map((choice) => [choice.slot, choice.value]),
      ),
      document_decision: 'provided',
      constraints_text: '주말에만 수강할 수 있어요.',
    })
    await submit(fixture)
    expect(fixture.run()?.profile_snapshot.constraints_text).toBe(
      '주말에만 수강할 수 있어요.',
    )
    await detailsCompareAndSaved(fixture)
  })

  it('첫 선택 409 후 같은 초안을 유지하고 새 질문에서 명시적으로 다시 선택한다', async () => {
    const fixture = createFlowFixture({ answerConflict: true })
    await start(fixture)
    fireEvent.click(
      await screen.findByRole('button', { name: choices[0].label }),
    )
    await screen.findByText(
      '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
    )
    expect(fixture.profile().answers).toEqual({})
    expect(window.location.search).toBe(`?profile=${flowIds.profile}`)
    await clickReady('다음 질문 받기')
    await answerRange(fixture, 0, 3)
    fireEvent.click(
      await screen.findByRole('button', { name: '자료 없이 진행' }),
    )
    await screen.findByRole('button', { name: '다음 질문 받기' })
    await clickReady('다음 질문 받기')
    await readyForSubmission(fixture)
    await submit(fixture)
    expect(requests(fixture, '/answer', 'POST')).toHaveLength(
      choices.length + 1,
    )
  })

  it('추천 job 실패는 성공 결과로 전환하지 않고 입력을 보존한 채 새 요청으로 복구한다', async () => {
    const fixture = createFlowFixture({ submitFailure: true })
    await withoutDocuments(fixture)
    const snapshot = fixture.profile().answers
    await clickReady('확인하고 교육 추천 받기')
    await screen.findByText(
      '작업이 완료되지 않았습니다. 현재 입력을 확인해주세요.',
    )
    expect(screen.queryByRole('region', { name: '추천 결과' })).toBeNull()
    expect(fixture.profile().answers).toEqual(snapshot)
    expect(window.location.search).toBe(`?profile=${flowIds.profile}`)
    await waitFor(() =>
      expect(button('확인하고 교육 추천 받기').disabled).toBe(false),
    )
    await submit(fixture)
    const submits = requests(fixture, '/submit', 'POST')
    expect(submits).toHaveLength(2)
    expect(submits[0][1].body).toBe(submits[1][1].body)
    expect(new Headers(submits[0][1].headers).get('Idempotency-Key')).not.toBe(
      new Headers(submits[1][1].headers).get('Idempotency-Key'),
    )
  })

  it('결과 GET 503과 App 재마운트는 같은 run만 조회하며 제출·질문을 다시 만들지 않는다', async () => {
    const fixture = createFlowFixture({ resultFailure: true })
    const view = await withoutDocuments(fixture)
    await clickReady('확인하고 교육 추천 받기')
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    expect(window.location.search).toBe(`?run=${flowIds.run}`)
    expect(
      screen.queryByRole('heading', { name: flowCourses[0].name }),
    ).toBeNull()
    await clickReady('다시 불러오기')
    await screen.findByRole('heading', { name: flowCourses[0].name })
    const snapshot = fixture.run()
    view.unmount()
    render(<App />)
    await screen.findByRole('heading', { name: flowCourses[0].name })
    expect(fixture.run()).toEqual(snapshot)
    expect(requests(fixture, '/submit', 'POST')).toHaveLength(1)
    expect(requests(fixture, '/questions', 'POST')).toHaveLength(choices.length)
    expect(requests(fixture, `/${flowIds.run}`)).toHaveLength(3)
  })

  it('결과에서 관심 저장 401을 받으면 App 전체가 로그인 화면으로 복귀한다', async () => {
    const fixture = createFlowFixture({ saveUnauthorized: true })
    await withoutDocuments(fixture)
    await submit(fixture)
    await clickReady(`${flowCourses[0].name} 관심 교육 저장`)
    await screen.findByRole('link', { name: 'Google로 시작하기' })
    expect(screen.queryByRole('region', { name: '추천 결과' })).toBeNull()
    expect(screen.queryByRole('button', { name: '로그아웃' })).toBeNull()
    expect(
      sessionStorage.getItem(`today-tommorrow:profile:${flowIds.user}`),
    ).toBe(flowIds.profile)
    expect(
      requests(fixture, `/saved-courses/${flowId(5)}`, 'PUT'),
    ).toHaveLength(1)
  })
})
