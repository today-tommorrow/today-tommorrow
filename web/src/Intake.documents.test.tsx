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
import type {
  Document,
  DocumentAccepted,
  Job,
  OnboardingOptions,
  Profile,
} from './types'

// Synthetic HTTP fixtures only: these do not parse files, run OCR or store data.
const profileId = '00000000-0000-4000-8000-000000000001'
const documentId = '00000000-0000-4000-8000-000000000002'
const jobId = '00000000-0000-4000-8000-000000000003'
const profile: Profile = {
  id: profileId,
  revision: 1,
  status: 'draft',
  answers: { experience: 'basic' },
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
const document: Document = {
  id: documentId,
  profile_id: profileId,
  kind: 'resume',
  source_type: 'text',
  display_name: '합성 학습 자료',
  source_url: null,
  mime_type: 'text/plain',
  size_bytes: 8,
  status: 'ready',
  extracted_preview: '주말에만 참여할 수 있어요.',
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
  allowed_file_extensions: ['.TXT', 'pdf'],
  max_upload_bytes: 8,
  max_profile_documents: 2,
  upload_limit_note: '합성 옵션: TXT/PDF, 최대 8바이트, 자료 2개',
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
const confirmedMessage =
  '자료 등록을 확인했어요. 처리 상태와 추출 미리보기를 확인해 주세요.'
const changed = vi.fn()
function registered(doc: Document = document, revision = 2): Profile {
  return { ...profile, revision, documents: [doc] }
}
function accepted(doc: Document = document): DocumentAccepted {
  return { document: doc, profile_revision: 2 }
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
async function ready() {
  await waitFor(() =>
    expect(
      (screen.getByRole('group', { name: '자료 추가' }) as HTMLFieldSetElement)
        .disabled,
    ).toBe(false),
  )
}
function textInput() {
  return screen.getByLabelText('자료 내용') as HTMLTextAreaElement
}
function typeText(value = '등록할 합성 원문') {
  fireEvent.change(textInput(), { target: { value } })
}
function button(name = '텍스트 등록') {
  return screen.getByRole('button', { name }) as HTMLButtonElement
}
function selectFile(name = '합성.TXT', content = '12345678') {
  const file = new File([content], name, { type: 'text/plain' })
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

describe('파일·텍스트 자료 등록', () => {
  it('긴 텍스트를 자르지 않고 전송하고 작업 완료 후 서버의 미리보기만 표시한다', async () => {
    const processing: Document = {
      ...document,
      status: 'processing',
      extracted_preview: null,
      job_id: jobId,
    }
    let reads = 0
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/texts`)
        return Response.json(accepted(processing), { status: 202 })
      if (path === profilePath)
        return Response.json(registered(++reads === 1 ? processing : document))
      if (path === `/api/jobs/${jobId}`) return Response.json(job)
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    const content = '학습 경험을 적은 합성 원문입니다.\n'.repeat(400)
    typeText(content)
    selectFile()
    fireEvent.change(screen.getByLabelText('공개 자료 주소'), {
      target: { value: 'https://example.org/synthetic' },
    })
    fireEvent.click(button())
    await screen.findByText(document.extracted_preview!)
    await ready()
    expect(posts(fetch)).toHaveLength(1)
    const [url, init] = posts(fetch)[0]
    expect(new URL(url).pathname).toBe(`${profilePath}/documents/texts`)
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: 1,
      kind: 'resume',
      text: content,
    })
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(textInput().value).toBe('')
    expect(button('파일 등록').disabled).toBe(false)
    expect(
      (screen.getByLabelText('공개 자료 주소') as HTMLInputElement).value,
    ).toBe('https://example.org/synthetic')
    expect(
      screen.getByText(
        '분석에 사용한 원문 미리보기이며 전체가 아닐 수 있어요.',
      ),
    ).toBeTruthy()
    expect(
      changed.mock.calls.every(
        ([value]) =>
          JSON.stringify(value.answers) === JSON.stringify(profile.answers),
      ),
    ).toBe(true)
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('옵션의 대문자 확장자·용량 경계와 multipart 필드를 따르고 파일만 초기화한다', async () => {
    const doc: Document = {
      ...document,
      source_type: 'file',
      kind: 'portfolio',
    }
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/files`)
        return Response.json(accepted(doc), { status: 202 })
      if (path === profilePath) return Response.json(registered(doc))
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    expect(screen.getByText(options.upload_limit_note)).toBeTruthy()
    const input = screen.getByLabelText('파일') as HTMLInputElement
    expect(input.accept).toBe('.TXT,.pdf')
    const file = selectFile()
    typeText('파일과 별도로 작성 중인 문장')
    fireEvent.change(screen.getByLabelText('자료 종류'), {
      target: { value: 'portfolio' },
    })
    fireEvent.click(button('파일 등록'))
    await screen.findByText(confirmedMessage)
    const [url, init] = posts(fetch)[0]
    expect(new URL(url).pathname).toBe(`${profilePath}/documents/files`)
    const form = init.body as FormData
    expect([...form.keys()]).toEqual(['expected_revision', 'kind', 'file'])
    expect(form.get('expected_revision')).toBe('1')
    expect(form.get('kind')).toBe('portfolio')
    expect((form.get('file') as File).name).toBe(file.name)
    expect((form.get('file') as File).size).toBe(options.max_upload_bytes)
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    expect(init.credentials).toBe('include')
    expect(button('파일 등록').disabled).toBe(true)
    expect(screen.getByLabelText('파일')).not.toBe(input)
    expect(textInput().value).toBe('파일과 별도로 작성 중인 문장')
  })

  it.each([
    ['합성.exe', '123', '지원하지 않는 파일 형식이에요.'],
    ['합성.txt', '123456789', '파일이 업로드 크기 제한을 초과했어요.'],
  ])(
    '옵션 위반 파일 %s은 전송하지 않고 선택을 보존한다',
    async (name, content, message) => {
      const fetch = serve(() => {
        throw new Error('Must not send')
      })
      render(<Harness />)
      await ready()
      selectFile(name, content)
      fireEvent.click(button('파일 등록'))
      await screen.findByText(message)
      expect(posts(fetch)).toHaveLength(0)
      expect(button('파일 등록').disabled).toBe(false)
    },
  )

  it('옵션의 자료 개수 한도를 넘기면 전송하지 않는다', async () => {
    const fetch = serve(() => {
      throw new Error('Must not send')
    })
    render(
      <Harness
        initial={{
          ...profile,
          documents: [document, { ...document, id: jobId }],
        }}
      />,
    )
    await ready()
    typeText()
    fireEvent.click(button())
    await screen.findByText('등록 가능한 자료 수를 초과했어요.')
    expect(posts(fetch)).toHaveLength(0)
    expect(textInput().value).toBe('등록할 합성 원문')
  })

  it('빈 텍스트와 공백만 있는 텍스트는 등록을 활성화하지 않는다', async () => {
    const fetch = serve(() => {
      throw new Error('Must not send')
    })
    render(<Harness />)
    await ready()
    expect(button().disabled).toBe(true)
    typeText(' \n\t')
    expect(button().disabled).toBe(true)
    expect(posts(fetch)).toHaveLength(0)
  })

  it.each([
    [413, '파일이 서버에서 허용한 크기를 초과합니다.'],
    [
      415,
      '서버에서 지원하지 않는 파일 형식입니다. 다른 형식으로 등록해주세요.',
    ],
    [422, '입력 내용을 확인해주세요.'],
    [503, '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.'],
  ])(
    '서버 %s 응답에서 파일과 미등록 텍스트를 보존한다',
    async (status, message) => {
      const fetch = serve(() => new Response(null, { status }))
      render(<Harness />)
      await ready()
      const file = selectFile()
      typeText()
      fireEvent.click(button('파일 등록'))
      await screen.findByText(message)
      expect(posts(fetch)).toHaveLength(1)
      expect(button('파일 등록').disabled).toBe(false)
      expect(
        (screen.getByLabelText('파일') as HTMLInputElement).files?.[0],
      ).toBe(file)
      expect(textInput().value).toBe('등록할 합성 원문')
      expect(changed).not.toHaveBeenCalled()
      expect(
        screen.queryByRole('button', { name: '등록한 자료 다시 확인' }),
      ).toBeNull()
    },
  )

  it('등록 409는 파일을 보존하고 최신 revision으로 재시도한다', async () => {
    const doc: Document = { ...document, source_type: 'file' }
    let attempts = 0
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/files`)
        return ++attempts === 1
          ? new Response(null, { status: 409 })
          : Response.json(
              { document: doc, profile_revision: 4 },
              { status: 202 },
            )
      if (path === profilePath)
        return Response.json(
          attempts === 1 ? { ...profile, revision: 3 } : registered(doc, 4),
        )
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    const file = selectFile()
    typeText()
    fireEvent.click(button('파일 등록'))
    await screen.findByText(
      '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
    )
    expect((screen.getByLabelText('파일') as HTMLInputElement).files?.[0]).toBe(
      file,
    )
    expect(textInput().value).toBe('등록할 합성 원문')
    fireEvent.click(button('파일 등록'))
    await screen.findByText(confirmedMessage)
    expect(
      posts(fetch).map(([, init]) =>
        (init.body as FormData).get('expected_revision'),
      ),
    ).toEqual(['1', '3'])
  })

  it.each([false, true])(
    '접수 뒤 조회 실패는 새 POST 없이 복구한다 (job=%s)',
    async (hasJob) => {
      const doc: Document = { ...document, job_id: hasJob ? jobId : null }
      let reads = 0
      const fetch = serve((path) => {
        if (path === `${profilePath}/documents/texts`)
          return Response.json(accepted(doc), { status: 202 })
        if (path === profilePath)
          return ++reads === 1
            ? new Response(null, { status: 503 })
            : Response.json(registered(doc))
        if (path === `/api/jobs/${jobId}`) return Response.json(job)
        throw new Error(`Unexpected ${path}`)
      })
      render(<Harness />)
      await ready()
      typeText()
      const submit = button()
      fireEvent.click(submit)
      fireEvent.click(submit)
      await screen.findByText(
        '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
      )
      await ready()
      expect(
        screen.getByText(
          '자료 등록은 접수됐어요. 목록을 다시 확인하면 처리 상태를 볼 수 있어요.',
        ),
      ).toBeTruthy()
      expect(sessionStorage.getItem(storageKey)).toBe(hasJob ? jobId : null)
      expect(button('자료 없이 진행').disabled).toBe(true)
      typeText('접수 후 새로 작성한 문장')
      expect(button().disabled).toBe(true)
      fireEvent.click(button())
      expect(posts(fetch)).toHaveLength(1)
      fireEvent.click(button('등록한 자료 다시 확인'))
      await screen.findByText(document.extracted_preview!)
      await ready()
      expect(posts(fetch)).toHaveLength(1)
      expect(textInput().value).toBe('접수 후 새로 작성한 문장')
      expect(
        screen.queryByRole('button', { name: '등록한 자료 다시 확인' }),
      ).toBeNull()
      expect(sessionStorage.getItem(storageKey)).toBeNull()
    },
  )

  it.each([
    ['다른 프로필', accepted({ ...document, profile_id: jobId })],
    ['빈 자료 ID', accepted({ ...document, id: '' })],
    ['다른 원천', accepted({ ...document, source_type: 'file' })],
    ['잘못된 작업 ID', accepted({ ...document, job_id: '' })],
    ['갱신되지 않은 revision', { ...accepted(), profile_revision: 1 }],
    ['누락된 자료', { profile_revision: 2 }],
  ])(
    '%s 접수 응답은 입력을 지우거나 작업을 시작하지 않는다',
    async (_, response) => {
      const fetch = serve(() => Response.json(response, { status: 202 }))
      render(<Harness />)
      await ready()
      typeText()
      fireEvent.click(button())
      await screen.findByText(
        '자료 등록 응답을 확인할 수 없어요. 최신 입력을 확인한 뒤 다시 시도해 주세요.',
      )
      expect(textInput().value).toBe('등록할 합성 원문')
      expect(fetch.mock.calls).toHaveLength(2)
      expect(changed).not.toHaveBeenCalled()
      expect(sessionStorage.getItem(storageKey)).toBeNull()
    },
  )

  it('최신 입력의 다음 단계가 바뀌어도 접수 자료 복구를 숨기지 않는다', async () => {
    let reads = 0
    serve((path) => {
      if (path === `${profilePath}/documents/texts`)
        return Response.json(accepted(), { status: 202 })
      if (path === profilePath) {
        reads++
        if (reads === 1) return new Response(null, { status: 503 })
        if (reads === 2)
          return Response.json({
            ...profile,
            revision: 2,
            intake_state: {
              ...profile.intake_state,
              next_action: 'generate_question',
            },
          })
        return Response.json(registered())
      }
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeText()
    fireEvent.click(button())
    await screen.findByText(
      '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    )
    fireEvent.click(button('최신 입력 확인'))
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1))
    await ready()
    expect(button('자료 없이 진행').disabled).toBe(true)
    fireEvent.click(button('등록한 자료 다시 확인'))
    await screen.findByText(confirmedMessage)
  })

  it('등록 자료가 빠진 목록은 완료 처리하지 않고 같은 조회를 다시 확인한다', async () => {
    let reads = 0
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/texts`)
        return Response.json(accepted(), { status: 202 })
      if (path === profilePath)
        return Response.json(
          ++reads === 1 ? { ...profile, revision: 2 } : registered(),
        )
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeText()
    fireEvent.click(button())
    await screen.findByText(
      '등록한 자료가 목록에서 확인되지 않아요. 등록한 자료를 다시 확인해 주세요.',
    )
    expect(screen.queryByText(confirmedMessage)).toBeNull()
    expect(changed).not.toHaveBeenCalled()
    fireEvent.click(button('등록한 자료 다시 확인'))
    await screen.findByText(confirmedMessage)
    expect(posts(fetch)).toHaveLength(1)
  })

  it('접수 revision보다 오래된 목록은 성공으로 표시하지 않는다', async () => {
    let stale = true
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/texts`)
        return Response.json(accepted(), { status: 202 })
      if (path === profilePath)
        return Response.json(registered(document, stale ? 1 : 2))
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeText()
    fireEvent.click(button())
    await screen.findByText(
      '등록한 자료의 최신 버전을 아직 확인하지 못했어요. 등록한 자료를 다시 확인해 주세요.',
    )
    expect(screen.queryByText(confirmedMessage)).toBeNull()
    expect(changed).not.toHaveBeenCalled()
    expect(button('등록한 자료 다시 확인')).toBeTruthy()
    stale = false
    fireEvent.click(button('등록한 자료 다시 확인'))
    await screen.findByText(confirmedMessage)
    expect(posts(fetch)).toHaveLength(1)
  })

  it('접수 전 대기 중지 후 늦은 응답이 입력과 선택 파일을 지우지 않는다', async () => {
    const pending = deferred()
    const fetch = serve(() => pending.promise)
    render(<Harness />)
    await ready()
    typeText()
    const file = selectFile()
    fireEvent.click(button('파일 등록'))
    fireEvent.click(button('대기 중지'))
    await act(async () =>
      pending.resolve(
        Response.json(accepted({ ...document, source_type: 'file' }), {
          status: 202,
        }),
      ),
    )
    expect(textInput().value).toBe('등록할 합성 원문')
    expect((screen.getByLabelText('파일') as HTMLInputElement).files?.[0]).toBe(
      file,
    )
    expect(changed).not.toHaveBeenCalled()
    expect(fetch.mock.calls).toHaveLength(2)
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })

  it('접수 확인 중인 자료 삭제가 성공하면 등록 대기 상태도 해제한다', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let reads = 0
    let deleted = false
    const fetch = serve((path, init) => {
      if (path === `${profilePath}/documents/texts`)
        return Response.json(accepted({ ...document, job_id: jobId }), {
          status: 202,
        })
      if (init.method === 'DELETE') {
        deleted = true
        return new Response(null, { status: 204 })
      }
      if (path === profilePath)
        return ++reads === 1
          ? new Response(null, { status: 503 })
          : Response.json(deleted ? { ...profile, revision: 3 } : registered())
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeText()
    fireEvent.click(button())
    await screen.findByRole('alert')
    fireEvent.click(button('최신 입력 확인'))
    await screen.findByText(document.display_name)
    fireEvent.click(button('자료 삭제'))
    await waitFor(() =>
      expect(screen.queryByText(document.display_name)).toBeNull(),
    )
    typeText('다음 자료')
    expect(button().disabled).toBe(false)
    expect(
      screen.queryByRole('button', { name: '등록한 자료 다시 확인' }),
    ).toBeNull()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(posts(fetch)).toHaveLength(1)
  })

  it('접수 후 목록 대기를 중지해도 같은 자료와 job을 조회로 복구한다', async () => {
    const pending = deferred()
    const doc = { ...document, job_id: jobId }
    let reads = 0
    const fetch = serve((path) => {
      if (path === `${profilePath}/documents/texts`)
        return Response.json(accepted(doc), { status: 202 })
      if (path === profilePath)
        return ++reads === 1 ? pending.promise : Response.json(registered())
      if (path === `/api/jobs/${jobId}`) return Response.json(job)
      throw new Error(`Unexpected ${path}`)
    })
    render(<Harness />)
    await ready()
    typeText()
    fireEvent.click(button())
    await screen.findByRole('button', { name: '등록한 자료 다시 확인' })
    fireEvent.click(button('대기 중지'))
    typeText('중지 후 작성한 새 문장')
    await act(async () => pending.resolve(Response.json(registered())))
    expect(changed).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(storageKey)).toBe(jobId)
    fireEvent.click(button('등록한 자료 다시 확인'))
    await screen.findByText(document.extracted_preview!)
    await ready()
    expect(posts(fetch)).toHaveLength(1)
    expect(textInput().value).toBe('중지 후 작성한 새 문장')
  })

  it.each([
    ['queued', '대기 중'],
    ['processing', '처리 중'],
    ['failed', '처리 실패'],
    ['ready', '확인 가능'],
  ] as const)(
    '%s 자료를 ready로 바꾸거나 미완료 미리보기를 확정 표시하지 않는다',
    async (status, label) => {
      serve(() => {
        throw new Error('No document request expected')
      })
      render(<Harness initial={registered({ ...document, status })} />)
      await ready()
      expect(screen.getByText(label)).toBeTruthy()
      expect(!!screen.queryByText(document.extracted_preview!)).toBe(
        status === 'ready',
      )
      expect(changed).not.toHaveBeenCalled()
    },
  )

  it('ready 자료의 미리보기 누락을 빈칸 대신 안내한다', async () => {
    serve(() => {
      throw new Error('No document request expected')
    })
    render(
      <Harness
        initial={registered({ ...document, extracted_preview: null })}
      />,
    )
    await screen.findByText('추출 미리보기가 제공되지 않았어요.')
  })
})
