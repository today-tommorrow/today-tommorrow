import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
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
  Interpretation,
  Job,
  OnboardingOptions,
  Profile,
} from './types'

// Synthetic contract data isolates UI regressions; this is not model/API E2E.
const options: OnboardingOptions = {
  schema_version: '0.8',
  fields: [],
  entry_intents: [],
  allowed_file_extensions: ['pdf'],
  max_upload_bytes: 1024,
  max_profile_documents: 5,
  upload_limit_note: '테스트 자료 제한',
  branch_rules_version: '1',
  intake_policy_version: '1',
  allowed_catalog_sources: ['kmooc'],
  interpretation_enabled: true,
  question_generation_enabled: true,
  question_schema_version: '1',
}

function profile(): Profile {
  return {
    id: 'profile-1',
    revision: 1,
    status: 'draft',
    answers: {},
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
}

const document: Document = {
  id: 'document-1',
  profile_id: 'profile-1',
  kind: 'resume',
  source_type: 'file',
  display_name: '등록한 테스트 자료',
  source_url: null,
  mime_type: 'application/pdf',
  size_bytes: 10,
  status: 'ready',
  extracted_preview: null,
  job_id: null,
  error_code: null,
  content_sha256: null,
  review_confirmed: true,
  created_at: '2026-11-01T00:00:00Z',
}

const interpretation: Interpretation = {
  id: 'interpretation-1',
  profile_id: 'profile-1',
  base_revision: 1,
  input_hash: 'test-hash',
  status: 'ready',
  assistant_text: '테스트 해석 확인',
  proposals: [
    {
      id: 'proposal-1',
      slot: 'goal_text',
      value: { kind: 'text', text: '기초 배우기' },
      label: '테스트 학습 목표',
      source_type: 'user_text',
      document_id: null,
      chunk_id: null,
      quote: '처음 작성한 문장',
      status: 'proposed',
      conflicts_with: null,
    },
  ],
  model_id: 'test-only',
  prompt_version: '1',
  created_at: '2026-11-01T00:00:00Z',
  suggested_slots: [],
}

function job(kind: Job['kind'], status: Job['status']): Job {
  return {
    id: 'job-1',
    kind,
    status,
    phase: status === 'failed' ? 'failed' : 'completed',
    profile_id: 'profile-1',
    result: { interpretation_id: 'interpretation-1' },
    error:
      status === 'failed'
        ? {
            code: 'extraction_failed',
            message: '자료 추출 실패',
            request_id: 'test',
          }
        : null,
    created_at: '2026-11-01T00:00:00Z',
    finished_at: '2026-11-01T00:00:01Z',
    completed_stages: [],
  }
}

function Harness({ initial = profile() }: { initial?: Profile }) {
  const [current, setCurrent] = useState(initial)
  return <Intake profile={current} onProfile={setCurrent} onRun={() => {}} />
}

function json(data: unknown) {
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json' },
  })
}

function serve(
  handler: (path: string, init: RequestInit) => Response | Promise<Response>,
) {
  vi.stubGlobal('fetch', async (input: string, init: RequestInit) => {
    const path = new URL(input).pathname
    if (path === '/api/onboarding/options') return json(options)
    return handler(path, init)
  })
}

async function ready() {
  await waitFor(() =>
    expect(
      (screen.getByRole('group', { name: '자료 추가' }) as HTMLFieldSetElement)
        .disabled,
    ).toBe(false),
  )
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '/api')
  sessionStorage.clear()
  setCsrfToken('test-csrf')
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
})

describe('자료 등록 시 미등록 초안 보존', () => {
  it.each(['files', 'links', 'texts'] as const)(
    '%s만 등록하면 다른 자료 입력은 유지한다',
    async (source) => {
      const registeredDocument: Document = {
        ...document,
        source_type:
          source === 'files' ? 'file' : source === 'links' ? 'url' : 'text',
        source_url: source === 'links' ? 'https://example.org/source' : null,
      }
      serve((path, init) => {
        if (
          path === `/api/profiles/profile-1/documents/${source}` &&
          init.method === 'POST'
        )
          return json({ document: registeredDocument, profile_revision: 2 })
        if (path === '/api/profiles/profile-1')
          return json({
            ...profile(),
            revision: 2,
            documents: [registeredDocument],
          })
        throw new Error(`예상하지 않은 요청: ${path}`)
      })
      render(<Harness />)
      await ready()
      const file = new File(['test'], 'test.pdf', { type: 'application/pdf' })
      fireEvent.change(screen.getByLabelText('파일'), {
        target: { files: [file] },
      })
      fireEvent.change(screen.getByLabelText('공개 자료 주소'), {
        target: { value: 'https://example.org/source' },
      })
      fireEvent.change(screen.getByLabelText('자료 내용'), {
        target: { value: '아직 등록하지 않은 자료' },
      })
      const buttons = {
        files: '파일 등록',
        links: '링크 등록',
        texts: '텍스트 등록',
      }
      fireEvent.click(screen.getByRole('button', { name: buttons[source] }))
      await screen.findByRole('heading', { name: '등록한 테스트 자료' })
      expect(
        (screen.getByLabelText('공개 자료 주소') as HTMLInputElement).value,
      ).toBe(source === 'links' ? '' : 'https://example.org/source')
      expect(
        (screen.getByLabelText('자료 내용') as HTMLTextAreaElement).value,
      ).toBe(source === 'texts' ? '' : '아직 등록하지 않은 자료')
      const chosen = (screen.getByLabelText('파일') as HTMLInputElement).files
      expect(chosen?.length).toBe(source === 'files' ? 0 : 1)
      if (source !== 'files') expect(chosen?.[0]).toBe(file)
    },
  )
})

describe('해석 적용 시 자유 입력 보존', () => {
  it.each([false, true])(
    '원문 변경 여부가 %s이면 제출한 원문만 초기화한다',
    async (changed) => {
      let respond!: (response: Response) => void
      const applied = new Promise<Response>((resolve) => {
        respond = resolve
      })
      serve((path, init) => {
        if (path === '/api/profiles/profile-1/interpretations')
          return json({
            job_id: 'job-1',
            status: 'queued',
            poll_after_ms: 1000,
          })
        if (path === '/api/jobs/job-1')
          return json(job('intake_interpretation', 'succeeded'))
        if (path === '/api/interpretations/interpretation-1')
          return json(interpretation)
        if (path.endsWith('/apply') && init.method === 'POST') return applied
        if (path === '/api/profiles/profile-1') return json(profile())
        throw new Error(`예상하지 않은 요청: ${path}`)
      })
      render(<Harness />)
      await ready()
      fireEvent.click(screen.getByText('직접 알려줄게요'))
      const input = screen.getByLabelText(
        '내 경험이나 원하는 배움',
      ) as HTMLTextAreaElement
      fireEvent.change(input, { target: { value: '처음 작성한 문장' } })
      fireEvent.click(
        screen.getByRole('button', { name: '입력과 준비된 자료 해석하기' }),
      )
      await screen.findByRole('region', { name: '입력 해석 확인' })
      fireEvent.click(screen.getByRole('radio', { name: '맞아요' }))
      fireEvent.click(
        screen.getByRole('button', { name: '확인한 제안만 반영' }),
      )
      if (changed)
        fireEvent.change(input, {
          target: { value: '해석 적용 중 새로 작성한 문장' },
        })
      respond(json({ ...profile(), revision: 2 }))
      await waitFor(() =>
        expect(
          screen.queryByRole('region', { name: '입력 해석 확인' }),
        ).toBeNull(),
      )
      expect(input.value).toBe(changed ? '해석 적용 중 새로 작성한 문장' : '')
    },
  )

  it('복원한 해석은 현재 작성한 새 문장을 지우지 않는다', async () => {
    const initial = {
      ...profile(),
      latest_interpretation_id: interpretation.id,
    }
    serve((path) => {
      if (path === '/api/interpretations/interpretation-1')
        return json(interpretation)
      if (path.endsWith('/apply')) return json({ ...initial, revision: 2 })
      throw new Error(`예상하지 않은 요청: ${path}`)
    })
    render(<Harness initial={initial} />)
    await screen.findByRole('region', { name: '입력 해석 확인' })
    fireEvent.click(screen.getByText('직접 알려줄게요'))
    const input = screen.getByLabelText(
      '내 경험이나 원하는 배움',
    ) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: '복원 후 새로 작성한 문장' } })
    fireEvent.click(screen.getByRole('radio', { name: '맞아요' }))
    fireEvent.click(screen.getByRole('button', { name: '확인한 제안만 반영' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('region', { name: '입력 해석 확인' }),
      ).toBeNull(),
    )
    expect(input.value).toBe('복원 후 새로 작성한 문장')
  })
})

it('문서 job 실패 후 같은 화면에서 실패 이유와 다시 처리 버튼을 표시한다', async () => {
  const queued = { ...document, status: 'processing' as const, job_id: 'job-1' }
  let failed = false
  serve((path) => {
    if (path === '/api/jobs/job-1') {
      failed = true
      return json(job('document_processing', 'failed'))
    }
    if (path === '/api/profiles/profile-1')
      return json({
        ...profile(),
        revision: 2,
        documents: [
          { ...queued, status: 'failed', error_code: 'extraction_failed' },
        ],
      })
    throw new Error(`예상하지 않은 요청: ${path}`)
  })
  render(<Harness initial={{ ...profile(), documents: [queued] }} />)
  fireEvent.click(screen.getByRole('button', { name: '처리 상태 확인' }))
  await waitFor(() => expect(failed).toBe(true))
  const retry = await screen.findByRole('button', { name: '다시 처리' })
  await waitFor(() => expect((retry as HTMLButtonElement).disabled).toBe(false))
  expect(screen.getByText(/자료를 읽지 못했어요/).textContent).toContain(
    'extraction_failed',
  )
})

it.each(['job', 'profile'])(
  '%s 조회 통신 실패 시 작업 ID와 미등록 입력을 보존한다',
  async (failure) => {
    const queued = {
      ...document,
      status: 'processing' as const,
      job_id: 'job-1',
    }
    serve((path) => {
      if (path === '/api/jobs/job-1' && failure === 'profile')
        return json(job('document_processing', 'failed'))
      throw new TypeError('연결 실패')
    })
    render(<Harness initial={{ ...profile(), documents: [queued] }} />)
    await ready()
    fireEvent.change(screen.getByLabelText('자료 내용'), {
      target: { value: '통신 오류에도 보존할 초안' },
    })
    fireEvent.click(screen.getByRole('button', { name: '처리 상태 확인' }))
    await screen.findByRole('button', { name: '진행 중이던 작업 확인' })
    expect(sessionStorage.getItem('today-tommorrow:job:profile-1')).toBe(
      'job-1',
    )
    expect(
      (screen.getByLabelText('자료 내용') as HTMLTextAreaElement).value,
    ).toBe('통신 오류에도 보존할 초안')
  },
)

it.each([false, true])(
  '409 후 작성 중인 입력을 보존하고 최신 revision으로만 재시도한다 (조회 실패=%s)',
  async (refreshFails) => {
    const initial = {
      ...profile(),
      answers: { experience: 'beginner' as const },
    }
    const latest = {
      ...initial,
      revision: 2,
      answers: { experience: 'work' as const },
    }
    const patches: Array<{ expected_revision: number; answers: unknown }> = []
    let reads = 0
    serve((path, init) => {
      if (path !== '/api/profiles/profile-1')
        throw new Error(`예상하지 않은 요청: ${path}`)
      if (init.method === 'PATCH') {
        patches.push(JSON.parse(init.body as string))
        if (patches.length === 1) return new Response('{}', { status: 409 })
        return json({
          ...latest,
          revision: 3,
          answers: { ...latest.answers, document_decision: 'none' },
        })
      }
      reads++
      if (refreshFails && reads === 1)
        return new Response('{}', { status: 503 })
      return json(latest)
    })
    render(<Harness initial={initial} />)
    await ready()
    fireEvent.change(screen.getByLabelText('자료 내용'), {
      target: { value: '다른 탭 수정 중에도 유지할 문장' },
    })
    fireEvent.click(screen.getByRole('button', { name: '자료 없이 진행' }))
    expect((await screen.findByRole('alert')).textContent).toContain(
      refreshFails ? '최신 내용을 불러오지 못했어요' : '작성 중인 내용은 유지',
    )
    expect(patches).toHaveLength(1)
    if (refreshFails) {
      fireEvent.click(screen.getByRole('button', { name: '최신 입력 확인' }))
      await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    }
    fireEvent.click(screen.getByRole('button', { name: '자료 없이 진행' }))
    await waitFor(() => expect(patches).toHaveLength(2))
    expect(patches[1]).toEqual({
      expected_revision: 2,
      answers: { experience: 'work', document_decision: 'none' },
    })
    expect(
      (screen.getByLabelText('자료 내용') as HTMLTextAreaElement).value,
    ).toBe('다른 탭 수정 중에도 유지할 문장')
  },
)
