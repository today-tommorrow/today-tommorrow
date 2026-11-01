import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { setCsrfToken } from './api'
import { Intake } from './Intake'
import type { Document, Job, OnboardingOptions, Profile } from './types'

// Synthetic consumer fixtures; no HTTP collector, DNS, redirects or live parsing.
const profileId = '00000000-0000-4000-8000-000000000001'
const documentId = '00000000-0000-4000-8000-000000000002'
const jobId = '00000000-0000-4000-8000-000000000003'
const replacementId = '00000000-0000-4000-8000-000000000004'
const sourceUrl = 'https://example.org/public-synthetic-source'
const profile: Profile = {
  id: profileId,
  revision: 1,
  status: 'draft',
  answers: { experience: 'basic', availability: 'evening_weekend' },
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
const link: Document = {
  id: documentId,
  profile_id: profileId,
  kind: 'job_description',
  source_type: 'url',
  display_name: '합성 공개 자료',
  source_url: sourceUrl,
  mime_type: 'text/html',
  size_bytes: null,
  status: 'ready',
  extracted_preview: '서버가 반환한 합성 본문',
  job_id: null,
  error_code: null,
  content_sha256: null,
  review_confirmed: true,
  created_at: '2026-11-01T00:00:00Z',
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
const profilePath = `/api/profiles/${profileId}`
const storageKey = `today-tommorrow:job:${profileId}`
const changed = vi.fn()
function registered(docs: Document[] = [link], revision = 2): Profile {
  return { ...profile, revision, documents: docs }
}
function accepted(doc = link, revision = 2) {
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
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/api/onboarding/options') return Response.json(options)
    return handler(path, init)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function posts(fetch: ReturnType<typeof serve>) {
  return fetch.mock.calls.filter(([, init]) => init.method === 'POST')
}
function button(name = '링크 등록') {
  return screen.getByRole('button', { name }) as HTMLButtonElement
}
function urlInput() {
  return screen.getByLabelText('공개 자료 주소') as HTMLInputElement
}
function textInput() {
  return screen.getByLabelText('자료 내용') as HTMLTextAreaElement
}
function typeUrl(url = sourceUrl) {
  fireEvent.change(urlInput(), { target: { value: url } })
}
async function ready() {
  await waitFor(() =>
    expect(
      (screen.getByRole('group', { name: '자료 추가' }) as HTMLFieldSetElement)
        .disabled,
    ).toBe(false),
  )
}
function draft() {
  fireEvent.change(textInput(), {
    target: { value: '별도로 작성 중인 합성 문장' },
  })
  const file = new File(['synthetic text'], 'synthetic.txt', {
    type: 'text/plain',
  })
  fireEvent.change(screen.getByLabelText('파일'), { target: { files: [file] } })
  return file
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

describe('공개 URL 등록과 대체 입력', () => {
  it.each([
    [
      ' HTTPS://EXAMPLE.ORG/public?sample=1#intro ',
      'https://example.org/public?sample=1#intro',
    ],
    ['http://example.org/public', 'http://example.org/public'],
  ])(
    '유효 주소 %s은 본문 대신 API에 URL을 접수하고 서버 본문·출처를 표시한다',
    async (input, normalized) => {
      const doc = { ...link, source_url: normalized }
      const fetch = serve((path) => {
        if (path === `${profilePath}/documents/links`) return accepted(doc)
        if (path === profilePath) return Response.json(registered([doc]))
        throw new Error(`Unexpected ${path}`)
      })
      render(<Harness />)
      await ready()
      const file = draft()
      fireEvent.change(screen.getByLabelText('자료 종류'), {
        target: { value: 'job_description' },
      })
      typeUrl(input)
      fireEvent.click(button())
      await screen.findByText(link.extracted_preview!)
      await ready()
      expect(posts(fetch)).toHaveLength(1)
      const [url, init] = posts(fetch)[0]
      expect(new URL(url).pathname).toBe(`${profilePath}/documents/links`)
      expect(JSON.parse(init.body as string)).toEqual({
        expected_revision: 1,
        kind: 'job_description',
        url: normalized,
      })
      expect(init.credentials).toBe('include')
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe(
        'synthetic-csrf',
      )
      expect(
        fetch.mock.calls.every(([url]) =>
          new URL(url).pathname.startsWith('/api/'),
        ),
      ).toBe(true)
      const source = screen.getByRole('link', {
        name: '자료 원문 보기',
      }) as HTMLAnchorElement
      expect(source.href).toBe(normalized)
      expect(source.target).toBe('_blank')
      expect(source.rel).toBe('noopener noreferrer')
      expect(urlInput().value).toBe('')
      expect(textInput().value).toBe('별도로 작성 중인 합성 문장')
      expect(
        (screen.getByLabelText('파일') as HTMLInputElement).files?.[0],
      ).toBe(file)
      expect(changed).toHaveBeenCalledWith(registered([doc]))
    },
  )

  it.each([
    'http://',
    'https://exa mple.org/path',
    'javascript:alert(1)',
    'file:///synthetic.txt',
    '//example.org/public',
    'example.org/public',
    'https://reader:synthetic-password@example.org/public',
    'https://reader@example.org/public',
  ])(
    '잘못된 주소 또는 사용자정보 포함 주소 %s는 전송하지 않는다',
    async (input) => {
      const fetch = serve(() => {
        throw new Error('No request expected')
      })
      render(<Harness />)
      await ready()
      typeUrl(input)
      const previous = urlInput().value
      fireEvent.click(button())
      await screen.findByText(
        '아이디·비밀번호가 없는 올바른 http 또는 https 주소를 입력해 주세요.',
      )
      expect(posts(fetch)).toHaveLength(0)
      expect(urlInput().value).toBe(previous)
      expect(changed).not.toHaveBeenCalled()
    },
  )

  it.each([
    null,
    'javascript:alert(1)',
    'https://reader:synthetic-password@example.org/public',
  ])(
    '출처 주소 %s를 안전한 링크로 오인해 표시하지 않는다',
    async (source_url) => {
      serve(() => {
        throw new Error('No document request expected')
      })
      render(<Harness initial={registered([{ ...link, source_url }])} />)
      await screen.findByText('자료 원문 주소를 확인할 수 없어요.')
      expect(screen.queryByRole('link', { name: '자료 원문 보기' })).toBeNull()
      expect(screen.getByText(link.extracted_preview!)).toBeTruthy()
    },
  )

  it.each(['texts', 'files'] as const)(
    '접수 전 실패 후 %s 대체 입력으로 이동해도 초안·URL·파일을 보존한다',
    async (source) => {
      const replacement: Document = {
        ...link,
        id: replacementId,
        source_type: source === 'texts' ? 'text' : 'file',
        source_url: null,
        kind: 'resume',
      }
      const fetch = serve((path) => {
        if (path === `${profilePath}/documents/links`)
          return new Response(null, { status: 503 })
        if (path === `${profilePath}/documents/${source}`)
          return accepted(replacement)
        if (path === profilePath)
          return Response.json(registered([replacement]))
        throw new Error(`Unexpected ${path}`)
      })
      render(<Harness />)
      await ready()
      const file = draft()
      typeUrl()
      fireEvent.click(button())
      await screen.findByText(
        '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
      )
      expect(
        screen.getByRole('region', { name: '링크 대체 입력' }),
      ).toBeTruthy()
      fireEvent.click(button('텍스트 입력으로 이동'))
      expect(document.activeElement).toBe(textInput())
      fireEvent.click(button('파일 선택으로 이동'))
      expect(document.activeElement).toBe(screen.getByLabelText('파일'))
      expect(textInput().value).toBe('별도로 작성 중인 합성 문장')
      expect(
        (screen.getByLabelText('파일') as HTMLInputElement).files?.[0],
      ).toBe(file)
      expect(urlInput().value).toBe(sourceUrl)
      expect(posts(fetch)).toHaveLength(1)
      fireEvent.click(button(source === 'texts' ? '텍스트 등록' : '파일 등록'))
      await screen.findByText(link.extracted_preview!)
      await ready()
      expect(posts(fetch)).toHaveLength(2)
      expect(urlInput().value).toBe(sourceUrl)
      expect(textInput().value).toBe(
        source === 'texts' ? '' : '별도로 작성 중인 합성 문장',
      )
      expect(
        screen.queryByRole('region', { name: '링크 대체 입력' }),
      ).toBeNull()
      expect(changed).toHaveBeenCalledWith(registered([replacement]))
    },
  )

  it('구체 오류 코드가 없어도 실패 링크에 대체 입력을 제공한다', async () => {
    const fetch = serve(() => {
      throw new Error('No document request expected')
    })
    render(
      <Harness
        initial={registered([{ ...link, status: 'failed', error_code: null }])}
      />,
    )
    await ready()
    expect(screen.getByRole('alert').textContent).toContain(
      '링크 본문을 읽지 못했어요',
    )
    draft()
    fireEvent.click(button('텍스트 입력으로 이동'))
    expect(document.activeElement).toBe(textInput())
    expect(textInput().value).toBe('별도로 작성 중인 합성 문장')
    expect(posts(fetch)).toHaveLength(0)
  })

  it('링크 작업 실패 후 서버 코드·출처를 보존하고 다른 답변을 바꾸지 않고 대체 텍스트를 등록한다', async () => {
    const processing: Document = {
      ...link,
      status: 'processing',
      extracted_preview: null,
      job_id: jobId,
    }
    const failed: Document = {
      ...processing,
      status: 'failed',
      error_code: 'synthetic_link_failure',
    }
    const replacement: Document = {
      ...link,
      id: replacementId,
      source_type: 'text',
      source_url: null,
    }
    let failure = false
    let replaced = false
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/links`) return accepted(processing)
      if (path === `/api/jobs/${jobId}`) {
        failure = true
        return Response.json({
          ...job,
          status: 'failed',
          phase: 'failed',
          error: {
            code: 'synthetic_link_failure',
            message: '합성 링크 수집 실패',
            request_id: 'synthetic-request',
          },
        })
      }
      if (path === `${profilePath}/documents/texts`) {
        replaced = true
        return accepted(replacement, 3)
      }
      if (path === profilePath)
        return Response.json(
          registered(
            replaced ? [failed, replacement] : [failure ? failed : processing],
            replaced ? 3 : 2,
          ),
        )
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    const file = draft()
    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'job_description' },
    })
    typeUrl()
    fireEvent.click(button())
    await screen.findByText(
      /링크 본문을 읽지 못했어요 \(synthetic_link_failure\)/,
    )
    await ready()
    expect(screen.queryByText(link.extracted_preview!)).toBeNull()
    expect(
      (
        screen.getByRole('link', {
          name: '자료 원문 보기',
        }) as HTMLAnchorElement
      ).href,
    ).toBe(sourceUrl)
    fireEvent.click(button('텍스트 입력으로 이동'))
    fireEvent.click(button('텍스트 등록'))
    await screen.findByText(link.extracted_preview!)
    await ready()
    expect(JSON.parse(posts(fetch)[1][1].body as string)).toEqual({
      expected_revision: 2,
      kind: 'job_description',
      text: '별도로 작성 중인 합성 문장',
    })
    expect(changed).toHaveBeenLastCalledWith(
      registered([failed, replacement], 3),
    )
    expect(
      changed.mock.calls.every(
        ([value]) =>
          JSON.stringify(value.answers) === JSON.stringify(profile.answers),
      ),
    ).toBe(true)
    expect(
      fetch.mock.calls.some(([, init]) =>
        ['PATCH', 'DELETE'].includes(init.method || ''),
      ),
    ).toBe(false)
    expect((screen.getByLabelText('파일') as HTMLInputElement).files?.[0]).toBe(
      file,
    )
  })

  it('queued 링크의 주소를 추출 본문으로 표시하지 않고 대기 중지 후 같은 job을 복구한다', async () => {
    const pending = deferred()
    const queued: Document = {
      ...link,
      status: 'queued',
      extracted_preview: null,
      job_id: jobId,
    }
    let complete = false
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/links`) return accepted(queued)
      if (path === profilePath)
        return Response.json(registered([complete ? link : queued]))
      if (path === `/api/jobs/${jobId}`)
        return complete ? Response.json(job) : pending.promise
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeUrl()
    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'job_description' },
    })
    fireEvent.click(button())
    await screen.findByText('대기 중')
    expect(screen.queryByText(link.extracted_preview!)).toBeNull()
    expect(screen.queryByText('확인 가능')).toBeNull()
    fireEvent.click(button('대기 중지'))
    await act(async () => pending.resolve(Response.json(job)))
    complete = true
    expect(sessionStorage.getItem(storageKey)).toBe(jobId)
    fireEvent.click(button('진행 중이던 작업 확인'))
    await screen.findByText(link.extracted_preview!)
    await ready()
    expect(posts(fetch)).toHaveLength(1)
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('주소 등록 409 후 입력을 보존하고 최신 revision으로만 재시도한다', async () => {
    let attempts = 0
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/links`)
        return ++attempts === 1
          ? new Response(null, { status: 409 })
          : accepted(link, 4)
      if (path === profilePath)
        return Response.json(
          attempts === 1 ? { ...profile, revision: 3 } : registered([link], 4),
        )
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeUrl()
    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'job_description' },
    })
    const file = draft()
    fireEvent.click(button())
    await screen.findByText(
      '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
    )
    expect(urlInput().value).toBe(sourceUrl)
    expect(textInput().value).toBe('별도로 작성 중인 합성 문장')
    expect((screen.getByLabelText('파일') as HTMLInputElement).files?.[0]).toBe(
      file,
    )
    fireEvent.click(button())
    await screen.findByText(link.extracted_preview!)
    expect(
      posts(fetch).map(
        ([, init]) => JSON.parse(init.body as string).expected_revision,
      ),
    ).toEqual([1, 3])
  })

  it('접수 전 중지 뒤 늦은 응답은 새 주소를 지우거나 실패 대체 UI를 만들지 않는다', async () => {
    const pending = deferred()
    const fetch = serve(() => pending.promise)
    render(<Harness />)
    await ready()
    typeUrl()
    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'job_description' },
    })
    fireEvent.click(button())
    fireEvent.click(button('대기 중지'))
    typeUrl('https://example.org/new-synthetic-source')
    await act(async () => pending.resolve(accepted()))
    expect(urlInput().value).toBe('https://example.org/new-synthetic-source')
    expect(changed).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: '링크 대체 입력' })).toBeNull()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(posts(fetch)).toHaveLength(1)
  })
})
