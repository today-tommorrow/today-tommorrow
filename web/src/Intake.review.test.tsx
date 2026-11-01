import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { setCsrfToken } from './api'
import { Intake } from './Intake'
import type {
  Document,
  Interpretation,
  Job,
  OnboardingOptions,
  Profile,
  ReviewedItem,
} from './types'

// Synthetic consumer contracts; no actual storage, derived-data deletion or model.
const profileId = '00000000-0000-4000-8000-000000000001'
const documentId = '00000000-0000-4000-8000-000000000002'
const otherId = '00000000-0000-4000-8000-000000000003'
const jobId = '00000000-0000-4000-8000-000000000004'
const itemId = '00000000-0000-4000-8000-000000000005'
const source: Document = {
  id: documentId,
  profile_id: profileId,
  kind: 'resume',
  source_type: 'text',
  display_name: '합성 자료 A',
  source_url: null,
  mime_type: 'text/plain',
  size_bytes: 32,
  status: 'failed',
  extracted_preview: '자료 A 미리보기',
  job_id: null,
  error_code: 'synthetic_extraction_failure',
  content_sha256: null,
  created_at: '2026-11-01T00:00:00Z',
}
const other: Document = {
  ...source,
  id: otherId,
  display_name: '보존할 자료 B',
  status: 'ready',
  extracted_preview: '자료 B 미리보기',
  error_code: null,
}
const items: ReviewedItem[] = [
  {
    id: itemId,
    label: '합성 기초 내용',
    state: 'unknown',
    document_id: documentId,
    chunk_id: '00000000-0000-4000-8000-000000000006',
    evidence_quote: '자료 A 원문 인용',
    origin: 'document',
  },
  {
    id: '00000000-0000-4000-8000-000000000007',
    label: '보존할 학습 내용',
    state: 'want_to_learn',
    document_id: otherId,
    chunk_id: null,
    evidence_quote: '자료 B 원문 인용',
    origin: 'document',
  },
]
const profile: Profile = {
  id: profileId,
  revision: 2,
  status: 'draft',
  answers: { experience: 'basic', goal_text: '보존할 학습 목표' },
  reviewed_items: items,
  documents: [source, other],
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
  allowed_file_extensions: ['txt'],
  max_upload_bytes: 1024,
  max_profile_documents: 5,
  upload_limit_note: '합성 자료 옵션',
  branch_rules_version: '1',
  intake_policy_version: '1',
  allowed_catalog_sources: ['work24'],
  interpretation_enabled: true,
  question_generation_enabled: true,
  question_schema_version: '1',
}
const job: Job = {
  id: jobId,
  kind: 'document_processing',
  status: 'succeeded',
  phase: 'completed',
  profile_id: profileId,
  result: { document_id: documentId },
  error: null,
  created_at: '2026-11-01T00:00:00Z',
  finished_at: '2026-11-01T00:00:01Z',
  completed_stages: [],
}
const path = `/api/profiles/${profileId}`
const retryPath = `/api/documents/${documentId}/retry`
const deletePath = `/api/documents/${documentId}`
const storageKey = `today-tommorrow:job:${profileId}`
const changed = vi.fn()
function readyProfile(revision = 2): Profile {
  return {
    ...profile,
    revision,
    documents: [{ ...source, status: 'ready', error_code: null }, other],
  }
}
function deletedProfile(revision = 3): Profile {
  // One possible provider response; D11 physical deletion/redaction is not tested.
  return {
    ...profile,
    revision,
    documents: [other],
    reviewed_items: [items[1]],
  }
}
function accepted(doc: Document, revision = 3) {
  return Response.json(
    { document: doc, profile_revision: revision },
    { status: 202 },
  )
}
function Harness({ initial = profile }: { initial?: Profile }) {
  const [current, setCurrent] = useState(initial)
  return (
    <Intake
      profile={current}
      onProfile={(next) => {
        changed(next)
        setCurrent(next)
      }}
      onRun={() => {}}
    />
  )
}
function serve(
  handler: (
    path: string,
    init: RequestInit,
    url: URL,
  ) => Response | Promise<Response>,
) {
  const fetch = vi.fn(async (input: string, init: RequestInit) => {
    const url = new URL(input)
    if (url.pathname === '/api/onboarding/options')
      return Response.json(options)
    return handler(url.pathname, init, url)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function writes(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls.filter(([, init]) => init.method !== 'GET')
}
function button(name: string) {
  return screen.getByRole('button', { name }) as HTMLButtonElement
}
function targetCard() {
  return screen
    .getByRole('heading', { name: source.display_name })
    .closest('article')!
}
function targetButton(name: string) {
  return within(targetCard()).getByRole('button', { name }) as HTMLButtonElement
}
function reviewGroup(label = items[0].label) {
  return screen.getByRole('group', { name: label })
}
function editLabel(value = '사용자가 확인한 기초 내용') {
  fireEvent.change(within(reviewGroup()).getByLabelText('표시 내용'), {
    target: { value },
  })
}
function textInput() {
  return screen.getByLabelText('자료 내용') as HTMLTextAreaElement
}
function drafts() {
  fireEvent.change(textInput(), { target: { value: '보존할 미등록 문장' } })
  fireEvent.change(screen.getByLabelText('공개 자료 주소'), {
    target: { value: 'https://example.org/synthetic' },
  })
  const file = new File(['synthetic'], 'synthetic.txt', { type: 'text/plain' })
  fireEvent.change(screen.getByLabelText('파일'), { target: { files: [file] } })
  return file
}
function expectDrafts(file: File) {
  expect(textInput().value).toBe('보존할 미등록 문장')
  expect(
    (screen.getByLabelText('공개 자료 주소') as HTMLInputElement).value,
  ).toBe('https://example.org/synthetic')
  expect((screen.getByLabelText('파일') as HTMLInputElement).files?.[0]).toBe(
    file,
  )
}
async function ready() {
  await waitFor(() =>
    expect(
      (screen.getByRole('group', { name: '자료 추가' }) as HTMLFieldSetElement)
        .disabled,
    ).toBe(false),
  )
}
function deferred() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
beforeEach(() => {
  changed.mockClear()
  sessionStorage.clear()
  vi.stubEnv('VITE_API_BASE_URL', '/api')
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

describe('추출 내용 원문 대조와 저장', () => {
  it('서버 ID·label·state만 PATCH하고 원문·출처·다른 답변을 보존한다', async () => {
    const next: Profile = {
      ...readyProfile(3),
      reviewed_items: [
        { ...items[0], label: '사용자가 확인한 기초 내용', state: 'have' },
        items[1],
      ],
    }
    const fetch = serve((endpoint) => {
      expect(endpoint).toBe(`${path}/review-items`)
      return Response.json(next)
    })
    render(<Harness initial={readyProfile()} />)
    await ready()
    const file = drafts()
    expect(within(reviewGroup()).getByText('출처: 합성 자료 A')).toBeTruthy()
    expect(
      within(reviewGroup()).getByText(items[0].evidence_quote!),
    ).toBeTruthy()
    expect(writes(fetch)).toHaveLength(0)
    editLabel()
    fireEvent.change(within(reviewGroup()).getByLabelText('나의 상태'), {
      target: { value: 'have' },
    })
    expect(button('확인하고 교육 추천 받기').disabled).toBe(true)
    fireEvent.click(button('확인 내용 저장'))
    await screen.findByText('확인 내용을 저장했어요.')
    const [, init] = writes(fetch)[0]
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: 2,
      items: [
        { id: itemId, label: '사용자가 확인한 기초 내용', state: 'have' },
        { id: items[1].id, label: items[1].label, state: items[1].state },
      ],
    })
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(init.credentials).toBe('include')
    expect(changed).toHaveBeenCalledWith(next)
    expectDrafts(file)
    expect(button('확인하고 교육 추천 받기').disabled).toBe(false)
  })

  it('저장 503·중복 클릭 후 편집을 유지하고 같은 내용으로 다시 저장한다', async () => {
    const pending = deferred()
    let attempts = 0
    const fetch = serve(() =>
      ++attempts === 1
        ? pending.promise
        : Response.json({
            ...readyProfile(3),
            reviewed_items: [
              { ...items[0], label: '사용자가 확인한 기초 내용' },
              items[1],
            ],
          }),
    )
    render(<Harness initial={readyProfile()} />)
    await ready()
    editLabel()
    const save = button('확인 내용 저장')
    fireEvent.click(save)
    fireEvent.click(save)
    expect(writes(fetch)).toHaveLength(1)
    await act(async () => pending.resolve(new Response(null, { status: 503 })))
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    expect(
      (within(reviewGroup()).getByLabelText('표시 내용') as HTMLInputElement)
        .value,
    ).toBe('사용자가 확인한 기초 내용')
    fireEvent.click(button('확인 내용 저장'))
    await screen.findByText('확인 내용을 저장했어요.')
    expect(writes(fetch)[1][1].body).toBe(writes(fetch)[0][1].body)
  })

  it.each([409, 200])(
    '%s 응답이 편집 revision을 확정하지 못하면 초안을 지우지 않는다',
    async (status) => {
      const latest = readyProfile(status === 409 ? 3 : 2)
      const fetch = serve((endpoint) =>
        endpoint === `${path}/review-items`
          ? status === 409
            ? new Response(null, { status })
            : Response.json(readyProfile())
          : Response.json(latest),
      )
      render(<Harness initial={readyProfile()} />)
      await ready()
      editLabel()
      fireEvent.click(button('확인 내용 저장'))
      await screen.findByText(
        '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
      )
      expect(screen.queryByText('확인 내용을 저장했어요.')).toBeNull()
      expect(
        (within(reviewGroup()).getByLabelText('표시 내용') as HTMLInputElement)
          .value,
      ).toBe('사용자가 확인한 기초 내용')
      expect(writes(fetch)).toHaveLength(1)
      if (status === 409) expect(button('확인 내용 저장').disabled).toBe(true)
      fireEvent.click(button('편집 취소'))
      expect(
        (within(reviewGroup()).getByLabelText('표시 내용') as HTMLInputElement)
          .value,
      ).toBe(items[0].label)
    },
  )

  it('서버에서 편집 대상이 사라져도 편집 취소를 남겨 제출 차단을 해제할 수 있다', async () => {
    const latest: Profile = {
      ...readyProfile(3),
      reviewed_items: [],
      documents: [],
      intake_state: {
        ...profile.intake_state,
        next_action: 'generate_question',
      },
    }
    serve(() => Response.json(latest))
    render(<Harness initial={readyProfile()} />)
    await ready()
    editLabel()
    fireEvent.click(button('최신 입력 확인'))
    await waitFor(() => expect(changed).toHaveBeenCalledWith(latest))
    expect(button('편집 취소').disabled).toBe(false)
    fireEvent.click(button('편집 취소'))
    expect(screen.queryByText('추출한 내용을 확인해 주세요')).toBeNull()
  })

  it('저장 대기 중지 뒤 늦은 응답이 새 편집을 지우지 않는다', async () => {
    const pending = deferred()
    serve(() => pending.promise)
    render(<Harness initial={readyProfile()} />)
    await ready()
    editLabel()
    fireEvent.click(button('확인 내용 저장'))
    fireEvent.click(button('대기 중지'))
    editLabel('중지 후 새 편집')
    await act(async () => pending.resolve(Response.json(readyProfile(3))))
    expect(
      (within(reviewGroup()).getByLabelText('표시 내용') as HTMLInputElement)
        .value,
    ).toBe('중지 후 새 편집')
    expect(changed).not.toHaveBeenCalled()
  })
})

describe('실패 자료 재시도', () => {
  it('재시도 접수 뒤 조회 실패는 job을 보존하고 POST 없이 복구한다', async () => {
    const processing: Document = {
      ...source,
      status: 'processing',
      job_id: jobId,
      error_code: null,
    }
    let reads = 0
    let complete = false
    const fetch = serve((endpoint) => {
      if (endpoint === retryPath) return accepted(processing)
      if (endpoint === path) {
        if (++reads === 1) return new Response(null, { status: 503 })
        return Response.json(
          complete
            ? readyProfile(3)
            : { ...profile, revision: 3, documents: [processing, other] },
        )
      }
      if (endpoint === `/api/jobs/${jobId}`) {
        complete = true
        return Response.json(job)
      }
      throw new Error(`Unexpected ${endpoint}`)
    })
    render(<Harness />)
    await ready()
    const file = drafts()
    const retry = targetButton('다시 처리')
    fireEvent.click(retry)
    fireEvent.click(retry)
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    expect(sessionStorage.getItem(storageKey)).toBe(jobId)
    expect(targetButton('다시 처리').disabled).toBe(true)
    expect(button('확인하고 교육 추천 받기').disabled).toBe(true)
    expect(writes(fetch)).toHaveLength(1)
    const [url, init] = writes(fetch)[0]
    expect(new URL(url).pathname).toBe(retryPath)
    expect(JSON.parse(init.body as string)).toEqual({ expected_revision: 2 })
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(init.credentials).toBe('include')
    fireEvent.click(button('재시도한 자료 다시 확인'))
    await screen.findByText('자료 A 미리보기')
    await ready()
    expect(writes(fetch)).toHaveLength(1)
    expectDrafts(file)
    expect(changed).toHaveBeenLastCalledWith(readyProfile(3))
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(button('확인하고 교육 추천 받기').disabled).toBe(false)
  })

  it.each([
    ['다른 자료', { ...source, id: otherId }, 3],
    ['다른 프로필', { ...source, profile_id: otherId }, 3],
    ['잘못된 job ID', { ...source, job_id: '' }, 3],
    ['오래된 revision', source, 2],
  ])('%s 재시도 응답으로 작업을 시작하지 않는다', async (_, doc, revision) => {
    const fetch = serve(() => accepted(doc, revision))
    render(<Harness />)
    await ready()
    drafts()
    fireEvent.click(targetButton('다시 처리'))
    await screen.findByText(
      '자료 재처리 응답을 확인할 수 없어요. 최신 입력을 확인해 주세요.',
    )
    expect(fetch.mock.calls).toHaveLength(2)
    expect(changed).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(textInput().value).toBe('보존할 미등록 문장')
  })

  it('409 뒤 최신 revision으로 같은 실패 자료를 재시도한다', async () => {
    let attempts = 0
    const readyDoc: Document = { ...source, status: 'ready', error_code: null }
    const fetch = serve((endpoint) => {
      if (endpoint === retryPath)
        return ++attempts === 1
          ? new Response(null, { status: 409 })
          : accepted(readyDoc, 4)
      if (endpoint === path)
        return Response.json(
          attempts === 1 ? { ...profile, revision: 3 } : readyProfile(4),
        )
      throw new Error(`Unexpected ${endpoint}`)
    })
    render(<Harness />)
    await ready()
    const file = drafts()
    fireEvent.click(targetButton('다시 처리'))
    await screen.findByText(
      '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
    )
    expectDrafts(file)
    fireEvent.click(targetButton('다시 처리'))
    await screen.findByText(
      '자료 재처리를 확인했어요. 처리 상태와 추출 미리보기를 확인해 주세요.',
    )
    expect(
      writes(fetch).map(
        ([, init]) => JSON.parse(init.body as string).expected_revision,
      ),
    ).toEqual([2, 3])
  })

  it('접수 후 조회 대기를 중지해도 같은 재시도 접수를 이어 확인한다', async () => {
    const pending = deferred()
    const readyDoc: Document = { ...source, status: 'ready', error_code: null }
    let reads = 0
    const fetch = serve((endpoint) => {
      if (endpoint === retryPath) return accepted(readyDoc)
      if (endpoint === path)
        return ++reads === 1 ? pending.promise : Response.json(readyProfile(3))
      throw new Error(`Unexpected ${endpoint}`)
    })
    render(<Harness />)
    await ready()
    fireEvent.click(targetButton('다시 처리'))
    await screen.findByRole('button', { name: '재시도한 자료 다시 확인' })
    fireEvent.click(button('대기 중지'))
    await act(async () => pending.resolve(Response.json(readyProfile(3))))
    expect(changed).not.toHaveBeenCalled()
    fireEvent.click(button('재시도한 자료 다시 확인'))
    await screen.findByText('자료 A 미리보기')
    expect(writes(fetch)).toHaveLength(1)
  })
})

describe('자료 삭제와 목록 복구', () => {
  it('삭제 목록 확인 중에는 해석 재조회와 새 해석을 요청하지 않는다', async () => {
    const interpretationId = '00000000-0000-4000-8000-000000000009'
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetch = serve((endpoint) =>
      endpoint === deletePath
        ? new Response(null, { status: 204 })
        : new Response(null, { status: 503 }),
    )
    render(
      <Harness
        initial={{
          ...readyProfile(),
          latest_interpretation_id: interpretationId,
        }}
      />,
    )
    await screen.findByRole('button', { name: '저장된 해석 다시 불러오기' })
    await ready()
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByText(
      '자료 삭제 요청은 처리됐어요. 최신 목록을 확인해 주세요.',
    )
    await ready()
    expect(button('저장된 해석 다시 불러오기').disabled).toBe(true)
    fireEvent.click(screen.getByText('직접 알려줄게요'))
    expect(button('입력과 준비된 자료 해석하기').disabled).toBe(true)
    fireEvent.click(button('저장된 해석 다시 불러오기'))
    fireEvent.click(button('입력과 준비된 자료 해석하기'))
    expect(writes(fetch)).toHaveLength(1)
    expect(
      fetch.mock.calls.filter(([url]) =>
        new URL(url).pathname.startsWith('/api/interpretations/'),
      ),
    ).toHaveLength(1)
  })

  it.each([
    { affected: true, delayed: false },
    { affected: false, delayed: false },
    { affected: true, delayed: true },
  ])(
    '삭제 자료 해석의 연결·응답 시점에 따라 숨김·적용 차단을 유지한다 (%j)',
    async ({ affected, delayed }) => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const pendingInterpretation = deferred()
      const interpretationId = '00000000-0000-4000-8000-000000000009'
      const interpretation: Interpretation = {
        id: interpretationId,
        profile_id: profileId,
        base_revision: 2,
        input_hash: 'synthetic-input-hash',
        status: 'ready',
        assistant_text: '합성 자료 해석 확인',
        proposals: [
          {
            id: '00000000-0000-4000-8000-000000000010',
            slot: 'goal_text',
            value: { kind: 'text', text: '제안한 학습 목표' },
            label: '합성 해석 제안',
            source_type: 'document',
            document_id: affected ? documentId : otherId,
            chunk_id: null,
            quote: '합성 해석 원문 인용',
            status: 'proposed',
            conflicts_with: null,
          },
        ],
        model_id: 'synthetic-model',
        prompt_version: '1',
        created_at: '2026-11-01T00:00:00Z',
        suggested_slots: [],
      }
      const fetch = serve((endpoint) => {
        if (endpoint === `/api/interpretations/${interpretationId}`)
          return delayed
            ? pendingInterpretation.promise
            : Response.json(interpretation)
        if (endpoint === deletePath) return new Response(null, { status: 204 })
        if (endpoint === path) return new Response(null, { status: 503 })
        throw new Error(`Unexpected ${endpoint}`)
      })
      render(
        <Harness
          initial={{
            ...readyProfile(),
            latest_interpretation_id: interpretationId,
          }}
        />,
      )
      if (!delayed)
        await screen.findByRole('region', { name: '입력 해석 확인' })
      await ready()
      if (!delayed) {
        fireEvent.click(screen.getByRole('radio', { name: '맞아요' }))
        expect(button('확인한 제안만 반영').disabled).toBe(false)
      }
      fireEvent.click(targetButton('자료 삭제'))
      await screen.findByText(
        '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
      )
      if (delayed) {
        await act(async () =>
          pendingInterpretation.resolve(Response.json(interpretation)),
        )
        expect(screen.queryByText('저장된 해석을 불러오고 있어요…')).toBeNull()
      }
      if (affected)
        expect(
          screen.queryByRole('region', { name: '입력 해석 확인' }),
        ).toBeNull()
      else {
        expect(
          screen.getByRole('region', { name: '입력 해석 확인' }),
        ).toBeTruthy()
        expect(button('확인한 제안만 반영').disabled).toBe(true)
      }
      expect(writes(fetch)).toHaveLength(1)
      expect(writes(fetch)[0][1].method).toBe('DELETE')
    },
  )

  it('삭제 확인을 취소하면 요청하지 않는다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const fetch = serve(() => {
      throw new Error('No request expected')
    })
    render(<Harness />)
    await ready()
    fireEvent.click(targetButton('자료 삭제'))
    expect(writes(fetch)).toHaveLength(0)
    expect(changed).not.toHaveBeenCalled()
  })

  it('204 뒤 GET 실패는 삭제 접수를 보존하고 같은 DELETE를 반복하지 않는다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const pending = deferred()
    let reads = 0
    const fetch = serve((endpoint, init, url) => {
      if (endpoint === deletePath) {
        expect(url.searchParams.get('expected_revision')).toBe('2')
        return pending.promise
      }
      if (endpoint === path)
        return ++reads === 1
          ? new Response(null, { status: 503 })
          : Response.json(deletedProfile())
      throw new Error(`Unexpected ${endpoint} ${init.method}`)
    })
    render(<Harness initial={readyProfile()} />)
    await ready()
    const file = drafts()
    const remove = targetButton('자료 삭제')
    fireEvent.click(remove)
    fireEvent.click(remove)
    expect(writes(fetch)).toHaveLength(1)
    await act(async () => pending.resolve(new Response(null, { status: 204 })))
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    expect(
      screen.getByText(
        '자료 삭제 요청은 처리됐어요. 최신 목록을 확인해 주세요.',
      ),
    ).toBeTruthy()
    expect(targetButton('자료 삭제').disabled).toBe(true)
    expect(screen.queryByText('자료 A 미리보기')).toBeNull()
    expect(screen.queryByText(items[0].evidence_quote!)).toBeNull()
    expect(button('확인하고 교육 추천 받기').disabled).toBe(true)
    expect(button('파일 등록').disabled).toBe(true)
    fireEvent.click(button('삭제 후 목록 다시 확인'))
    await screen.findByText('자료 삭제 결과를 확인했어요.')
    expect(
      screen.queryByRole('heading', { name: source.display_name }),
    ).toBeNull()
    expect(
      screen.getByRole('heading', { name: other.display_name }),
    ).toBeTruthy()
    expectDrafts(file)
    expect(changed).toHaveBeenCalledWith(deletedProfile())
    const [, init] = writes(fetch)[0]
    expect(init.method).toBe('DELETE')
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(writes(fetch)).toHaveLength(1)
  })

  it.each([
    ['자료가 남은 목록', { ...profile, revision: 3 }],
    ['오래된 revision', deletedProfile(2)],
    ['다른 프로필', { ...deletedProfile(), id: jobId }],
  ])('%s은 삭제 결과로 확정하지 않는다', async (_, stale) => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let reads = 0
    const fetch = serve((endpoint) => {
      if (endpoint === deletePath) return new Response(null, { status: 204 })
      if (endpoint === path)
        return Response.json(++reads === 1 ? stale : deletedProfile())
      throw new Error(`Unexpected ${endpoint}`)
    })
    render(<Harness />)
    await ready()
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByRole('button', { name: '삭제 후 목록 다시 확인' })
    await ready()
    expect(changed).not.toHaveBeenCalled()
    expect(screen.queryByText('자료 삭제 결과를 확인했어요.')).toBeNull()
    fireEvent.click(button('삭제 후 목록 다시 확인'))
    await screen.findByText('자료 삭제 결과를 확인했어요.')
    expect(writes(fetch)).toHaveLength(1)
  })

  it('삭제 503은 자료와 초안을 보존하고 재시도할 수 있다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let attempts = 0
    const fetch = serve((endpoint) =>
      endpoint === deletePath
        ? ++attempts === 1
          ? new Response(null, { status: 503 })
          : new Response(null, { status: 204 })
        : Response.json(deletedProfile()),
    )
    render(<Harness initial={readyProfile()} />)
    await ready()
    const file = drafts()
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    expectDrafts(file)
    expect(screen.getByText('자료 A 미리보기')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: '삭제 후 목록 다시 확인' }),
    ).toBeNull()
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByText('자료 삭제 결과를 확인했어요.')
    expect(writes(fetch)).toHaveLength(2)
  })

  it('삭제 409 뒤 최신 revision을 사용하며 다른 답변을 바꾸지 않는다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let attempts = 0
    const fetch = serve((endpoint) => {
      if (endpoint === deletePath)
        return ++attempts === 1
          ? new Response(null, { status: 409 })
          : new Response(null, { status: 204 })
      if (endpoint === path)
        return Response.json(
          attempts === 1 ? { ...profile, revision: 3 } : deletedProfile(4),
        )
      throw new Error(`Unexpected ${endpoint}`)
    })
    render(<Harness />)
    await ready()
    const file = drafts()
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByText(
      '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
    )
    expectDrafts(file)
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByText('자료 삭제 결과를 확인했어요.')
    expect(
      writes(fetch).map(([url]) =>
        new URL(url).searchParams.get('expected_revision'),
      ),
    ).toEqual(['2', '3'])
    expect(changed).toHaveBeenLastCalledWith(deletedProfile(4))
  })

  it.each([true, false])(
    '처리 중 자료 삭제에서 연결 job 여부 %s에 따라 해당 복구만 해제한다',
    async (matches) => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const storedJob = matches ? jobId : '00000000-0000-4000-8000-000000000008'
      sessionStorage.setItem(storageKey, storedJob)
      serve((endpoint) =>
        endpoint === deletePath
          ? new Response(null, { status: 204 })
          : Response.json(deletedProfile()),
      )
      render(
        <Harness
          initial={{
            ...profile,
            documents: [
              { ...source, status: 'processing', job_id: jobId },
              other,
            ],
          }}
        />,
      )
      await ready()
      fireEvent.click(targetButton('자료 삭제'))
      await screen.findByText('자료 삭제 결과를 확인했어요.')
      await ready()
      expect(sessionStorage.getItem(storageKey)).toBe(
        matches ? null : storedJob,
      )
      expect(
        !!screen.queryByRole('button', { name: '진행 중이던 작업 확인' }),
      ).toBe(!matches)
    },
  )

  it('삭제 접수 전 중지한 요청의 늦은 204가 자료와 새 입력을 지우지 않는다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const pending = deferred()
    serve(() => pending.promise)
    render(<Harness initial={readyProfile()} />)
    await ready()
    fireEvent.click(targetButton('자료 삭제'))
    fireEvent.click(button('대기 중지'))
    drafts()
    await act(async () => pending.resolve(new Response(null, { status: 204 })))
    expect(screen.getByText('자료 A 미리보기')).toBeTruthy()
    expect(textInput().value).toBe('보존할 미등록 문장')
    expect(changed).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: '삭제 후 목록 다시 확인' }),
    ).toBeNull()
  })

  it('204 후 조회 대기를 중지해도 DELETE 없이 목록 복구를 이어간다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const pending = deferred()
    let reads = 0
    const fetch = serve((endpoint) => {
      if (endpoint === deletePath) return new Response(null, { status: 204 })
      if (endpoint === path)
        return ++reads === 1 ? pending.promise : Response.json(deletedProfile())
      throw new Error(`Unexpected ${endpoint}`)
    })
    render(<Harness />)
    await ready()
    fireEvent.click(targetButton('자료 삭제'))
    await screen.findByRole('button', { name: '삭제 후 목록 다시 확인' })
    fireEvent.click(button('대기 중지'))
    await act(async () => pending.resolve(Response.json(deletedProfile())))
    expect(changed).not.toHaveBeenCalled()
    fireEvent.click(button('삭제 후 목록 다시 확인'))
    await screen.findByText('자료 삭제 결과를 확인했어요.')
    expect(writes(fetch)).toHaveLength(1)
  })

  it.each(['queued', 'processing', 'failed', 'ready'] as const)(
    '%s 자료가 있으면 제출 가능 상태를 정확히 유지한다',
    async (status) => {
      const fetch = serve(() => {
        throw new Error('No mutation expected')
      })
      render(
        <Harness
          initial={{
            ...readyProfile(),
            documents: [{ ...source, status, error_code: null }, other],
          }}
        />,
      )
      await ready()
      expect(button('확인하고 교육 추천 받기').disabled).toBe(
        status !== 'ready',
      )
      expect(writes(fetch)).toHaveLength(0)
    },
  )
})
