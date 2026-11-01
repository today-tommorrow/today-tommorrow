import { useEffect, useId, useRef, useState } from 'react'
import { ApiError, request, waitForJob } from './api'
import type {
  Answers,
  Document,
  DocumentAccepted,
  DocumentKind,
  IntakeQuestion,
  IntakeState,
  Interpretation,
  Job,
  JobAccepted,
  OnboardingOptions,
  Profile,
  ReviewedItem,
} from './types'

type Props = {
  profile: Profile
  onProfile: (profile: Profile) => void
  onRun: (id: string) => void
}

type ReviewEdit = { label: string; state: ReviewedItem['state'] }
type PendingDocument = DocumentAccepted & { operation: 'register' | 'retry' }
type PendingDeletion = { id: string; minimumRevision: number }

function httpUrl(value: string | null) {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (!/^https?:\/\//i.test(trimmed)) return null
  try {
    const url = new URL(trimmed)
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null
  } catch {
    return null
  }
}

function DocumentSource({ url }: { url: string | null }) {
  const href = httpUrl(url)
  return href ? (
    <p>
      <a href={href} target="_blank" rel="noopener noreferrer">
        자료 원문 보기
      </a>
    </p>
  ) : (
    <p>자료 원문 주소를 확인할 수 없어요.</p>
  )
}

function interpretationMatches(
  value: Interpretation,
  id: string,
  profile: Profile,
) {
  return (
    !!value &&
    value.id === id &&
    value.profile_id === profile.id &&
    Number.isInteger(value.base_revision) &&
    value.base_revision >= 0 &&
    ['ready', 'stale', 'failed', 'applied'].includes(value.status) &&
    typeof value.assistant_text === 'string' &&
    Array.isArray(value.proposals) &&
    value.proposals.every(
      (proposal) =>
        proposal &&
        typeof proposal.id === 'string' &&
        proposal.id.trim().length > 0 &&
        typeof proposal.label === 'string' &&
        proposal.label.trim().length > 0 &&
        typeof proposal.quote === 'string' &&
        ['proposed', 'conflict'].includes(proposal.status) &&
        proposal.value &&
        ((proposal.value.kind === 'text' &&
          typeof proposal.value.text === 'string') ||
          (proposal.value.kind === 'integer' &&
            Number.isInteger(proposal.value.integer))),
    ) &&
    new Set(value.proposals.map((proposal) => proposal.id)).size ===
      value.proposals.length
  )
}

function questionMatches(value: IntakeQuestion, id: string, profile: Profile) {
  return (
    !!value &&
    value.id === id &&
    value.profile_id === profile.id &&
    profile.intake_state.active_question_id === id &&
    value.base_revision === profile.revision &&
    value.status === 'ready' &&
    value.selection_mode === 'single' &&
    typeof value.question === 'string' &&
    value.question.trim().length > 0 &&
    Array.isArray(value.options) &&
    value.options.length > 0 &&
    value.options.every(
      (option) =>
        option &&
        typeof option.id === 'string' &&
        option.id.length > 0 &&
        typeof option.label === 'string' &&
        option.label.trim().length > 0,
    ) &&
    new Set(value.options.map((option) => option.id)).size ===
      value.options.length
  )
}

const answerLabels: Record<keyof Answers, string> = {
  entry_intent: '시작한 이유',
  field_code: '관심 분야',
  custom_field: '직접 입력한 분야',
  interest_activity: '관심 활동',
  field_confirmed: '분야 확인',
  experience: '현재 경험',
  previous_gap: '보완하고 싶은 점',
  learning_goal: '학습 목표',
  availability: '참여 시간',
  delivery_mode: '참여 방식',
  commute_region: '참여 가능한 지역',
  budget_status: '예산 기준',
  budget_krw: '예산 (원)',
  start_timing: '시작 시기',
  document_decision: '참고 자료',
  self_description: '나의 경험',
  eligibility_context: '참여 자격 관련 정보',
  goal_text: '원하는 배움',
  constraints_text: '참여 조건',
}

const valueLabels: Record<string, string> = {
  beginner: '처음 시작해요',
  basic: '기초를 배웠어요',
  project: '프로젝트 경험이 있어요',
  work: '실무 경험이 있어요',
  unknown: '아직 모르겠어요',
  application: '활용 경험',
  depth: '더 깊은 이해',
  collaboration: '협업 경험',
  foundation: '기초 다지기',
  portfolio: '포트폴리오 만들기',
  weekday_day: '평일 낮',
  evening_weekend: '저녁이나 주말',
  flexible: '시간 조정 가능',
  online: '온라인',
  offline: '오프라인',
  hybrid: '온·오프라인 병행',
  any: '참여 방식 무관',
  amount: '정해진 예산',
  no_limit: '예산 제한 없음',
  soon: '곧 시작',
  one_to_three_months: '1~3개월 이내',
  browse: '먼저 알아보기',
  provided: '자료 등록',
  none: '자료 없이 진행',
  employed: '재직 중',
  unemployed: '미취업',
  student: '학생',
  self_employed: '자영업',
}

const sections = [
  ['direction', '방향'],
  ['experience', '경험'],
  ['goal', '학습 목표'],
  ['conditions', '참여 조건'],
] as const

const phases: Record<Job['phase'], string> = {
  queued: '작업을 기다리고 있어요',
  fetching: '자료를 가져오고 있어요',
  extracting: '자료를 읽고 있어요',
  interpreting: '입력 내용을 해석하고 있어요',
  freezing: '확인한 입력을 저장하고 있어요',
  profiling: '학습 목표를 확인하고 있어요',
  mapping_needs: '필요한 학습 내용을 정리하고 있어요',
  retrieving: '교육 후보를 찾고 있어요',
  checking_constraints: '참여 조건을 확인하고 있어요',
  assessing: '교육 근거를 살펴보고 있어요',
  ranking: '추천 순서를 정하고 있어요',
  explaining: '추천 이유를 정리하고 있어요',
  validating: '결과를 확인하고 있어요',
  persisting: '결과를 저장하고 있어요',
  completed: '처리를 마쳤어요',
  failed: '처리에 실패했어요',
  generating_question: '다음 질문을 만들고 있어요',
  validating_question: '질문과 선택지를 확인하고 있어요',
}

function jobMessage(job: Job) {
  switch (job.status) {
    case 'queued':
      return '작업을 기다리고 있어요'
    case 'succeeded':
      return '처리를 마쳤어요'
    case 'failed':
      return '처리에 실패했어요'
    case 'cancelled':
      return '서버 작업이 취소됐어요'
    case 'superseded':
      return '입력이 바뀌어 이전 작업이 만료됐어요'
    case 'running':
      return job.phase === 'completed'
        ? '작업의 최종 완료를 확인하고 있어요'
        : phases[job.phase]
  }
}

export function Intake({ profile, onProfile, onRun }: Props) {
  const [options, setOptions] = useState<OnboardingOptions | null>(null)
  const [question, setQuestion] = useState<IntakeQuestion | null>(null)
  const [questionLoading, setQuestionLoading] = useState(false)
  const [questionError, setQuestionError] = useState('')
  const [questionAttempt, setQuestionAttempt] = useState(0)
  const [interpretation, setInterpretation] = useState<Interpretation | null>(
    null,
  )
  const [interpretationLoading, setInterpretationLoading] = useState(false)
  const [interpretationError, setInterpretationError] = useState('')
  const [interpretationInterrupted, setInterpretationInterrupted] =
    useState(false)
  const [interpretationAttempt, setInterpretationAttempt] = useState(0)
  const interpretationLoad = useRef<AbortController | null>(null)
  const [decisions, setDecisions] = useState<
    Record<string, 'accept' | 'reject'>
  >({})
  const [text, setText] = useState('')
  const [documentText, setDocumentText] = useState('')
  const [documentUrl, setDocumentUrl] = useState('')
  const [linkFallback, setLinkFallback] = useState(false)
  const linkHintId = useId()
  const fileInput = useRef<HTMLInputElement>(null)
  const documentTextInput = useRef<HTMLTextAreaElement>(null)
  const [kind, setKind] = useState<DocumentKind>('resume')
  const [file, setFile] = useState<File | null>(null)
  const [fileInputKey, setFileInputKey] = useState(0)
  const [pendingDocument, setPendingDocument] =
    useState<PendingDocument | null>(null)
  const [pendingDeletion, setPendingDeletion] =
    useState<PendingDeletion | null>(null)
  const [reviewEdits, setReviewEdits] = useState<Record<string, ReviewEdit>>({})
  const [reviewRevision, setReviewRevision] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const jobStorageKey = `today-tommorrow:job:${profile.id}`
  const [pendingJob, setPendingJob] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(jobStorageKey)
    } catch {
      return null
    }
  })
  const active = useRef<AbortController | null>(null)
  const keys = useRef(new Map<string, string>())
  const interpretationInput = useRef<{
    jobId: string
    interpretationId: string | null
    text: string
  } | null>(null)
  const latest = useRef(profile)
  latest.current = profile
  const callbacks = useRef({ onProfile, onRun })
  callbacks.current = { onProfile, onRun }
  const path = `/profiles/${encodeURIComponent(profile.id)}`

  function applyProfile(
    next: Profile,
    minimumRevision = latest.current.revision,
  ) {
    if (
      !next ||
      next.id !== latest.current.id ||
      !Number.isInteger(next.revision) ||
      next.revision < 0
    )
      throw new ApiError('현재 입력의 응답을 확인할 수 없어요.', 502)
    if (
      next.revision < latest.current.revision ||
      next.revision < minimumRevision
    )
      throw new ApiError(
        '입력 응답의 버전이 최신 상태와 맞지 않아요. 다시 확인해 주세요.',
        409,
      )
    latest.current = next
    callbacks.current.onProfile(next)
    setQuestion((current) =>
      current?.base_revision === next.revision &&
      current.id === next.intake_state.active_question_id
        ? current
        : null,
    )
  }

  async function refresh(signal?: AbortSignal) {
    const next = await request<Profile>(path, { signal })
    if (!signal?.aborted) applyProfile(next)
    return next
  }

  function rememberJob(id: string | null) {
    setPendingJob(id)
    try {
      if (id) sessionStorage.setItem(jobStorageKey, id)
      else sessionStorage.removeItem(jobStorageKey)
    } catch {
      // The active job remains recoverable until this page is left.
    }
  }

  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action(controller.signal)
    } catch (cause) {
      if (controller.signal.aborted) return
      if (cause instanceof ApiError && cause.status === 409) {
        try {
          await refresh(controller.signal)
          if (!controller.signal.aborted)
            setError(
              '다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.',
            )
        } catch {
          if (!controller.signal.aborted)
            setError(
              '입력이 변경됐지만 최신 내용을 불러오지 못했어요. 새로 확인해 주세요.',
            )
        }
      } else {
        setError(
          cause instanceof Error ? cause.message : '요청을 처리하지 못했어요.',
        )
      }
    } finally {
      if (active.current === controller) {
        active.current = null
        if (!controller.signal.aborted) setBusy(false)
      }
    }
  }

  async function mutate<T>(
    endpoint: string,
    body: unknown,
    signal: AbortSignal,
  ) {
    const signature = endpoint + JSON.stringify(body)
    let key = keys.current.get(signature)
    if (!key) {
      key = crypto.randomUUID()
      keys.current.set(signature, key)
    }
    const result = await request<T>(endpoint, {
      method: 'POST',
      body,
      signal,
      idempotencyKey: key,
    })
    return result
  }

  async function finishJob(
    id: string,
    signal: AbortSignal,
    pollAfterMs?: number,
  ) {
    if (signal.aborted) return
    rememberJob(id)
    let terminalFailure = false
    const result = await waitForJob(
      id,
      signal,
      (update) => {
        if (signal.aborted) return
        if (update.profile_id !== latest.current.id)
          throw new Error('현재 입력의 작업이 아니에요.')
        setJob(update)
        if (['failed', 'cancelled', 'superseded'].includes(update.status)) {
          terminalFailure = true
          keys.current.clear()
        }
      },
      pollAfterMs,
    ).catch(async (cause: unknown) => {
      if (terminalFailure && !signal.aborted) {
        await refresh(signal)
        if (!signal.aborted) rememberJob(null)
      }
      throw cause
    })
    if (signal.aborted) return
    if (result.profile_id !== latest.current.id)
      throw new Error('현재 입력의 작업이 아니에요.')
    if (result.status !== 'succeeded')
      throw new Error(result.error?.message || '작업을 완료하지 못했어요.')
    await refresh(signal)
    if (signal.aborted) return
    if (result.kind === 'recommendation') {
      if (!result.result?.recommendation_run_id)
        throw new Error(
          '완료한 추천 결과를 확인하지 못했어요. 작업 상태를 다시 확인해 주세요.',
        )
      rememberJob(null)
      callbacks.current.onRun(result.result.recommendation_run_id)
      return
    }
    if (result.kind === 'intake_question') {
      if (!result.result?.question_id)
        throw new Error(
          '완료한 질문을 확인하지 못했어요. 작업 상태를 다시 확인해 주세요.',
        )
      const found = await request<IntakeQuestion>(
        `/questions/${encodeURIComponent(result.result.question_id)}`,
        { signal },
      )
      if (signal.aborted) return
      if (questionMatches(found, result.result.question_id, latest.current))
        setQuestion(found)
      else
        setNotice(
          '입력이 바뀌어 이전 질문을 표시하지 않았어요. 다음 질문을 다시 요청해 주세요.',
        )
    }
    if (result.kind === 'intake_interpretation') {
      if (!result.result?.interpretation_id)
        throw new Error(
          '완료한 해석을 확인하지 못했어요. 작업 상태를 다시 확인해 주세요.',
        )
      const found = await request<Interpretation>(
        `/interpretations/${encodeURIComponent(result.result.interpretation_id)}`,
        { signal },
      )
      if (signal.aborted) return
      if (
        !interpretationMatches(
          found,
          result.result.interpretation_id,
          latest.current,
        )
      )
        throw new Error('해석 응답이 요청한 입력과 맞지 않아요.')
      interpretationLoad.current?.abort()
      setInterpretationLoading(false)
      setInterpretationError('')
      setInterpretationInterrupted(false)
      if (
        found.base_revision === latest.current.revision &&
        found.status !== 'applied'
      ) {
        if (interpretationInput.current?.jobId === id)
          interpretationInput.current.interpretationId = found.id
        setInterpretation(found)
        setDecisions({})
      } else
        setNotice(
          '입력이 바뀌어 이전 해석을 표시하지 않았어요. 필요한 경우 다시 해석해 주세요.',
        )
    }
    rememberJob(null)
  }

  useEffect(() => {
    const controller = new AbortController()
    void request<OnboardingOptions>('/onboarding/options', {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setOptions(value)
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : '입력 옵션을 불러오지 못했어요.',
          )
      })
    return () => {
      controller.abort()
      active.current?.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const id = profile.intake_state.active_question_id
    setQuestion(null)
    setQuestionLoading(!!id)
    setQuestionError('')
    if (id) {
      void request<IntakeQuestion>(`/questions/${encodeURIComponent(id)}`, {
        signal: controller.signal,
      })
        .then((value) => {
          if (controller.signal.aborted) return
          if (!questionMatches(value, id, latest.current))
            throw new Error(
              '질문 응답이 현재 입력과 맞지 않아요. 최신 입력을 확인한 뒤 다시 불러와 주세요.',
            )
          setQuestion(value)
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted)
            setQuestionError(
              cause instanceof Error
                ? cause.message
                : '질문을 불러오지 못했어요.',
            )
        })
        .finally(() => {
          if (!controller.signal.aborted) setQuestionLoading(false)
        })
    }
    return () => controller.abort()
  }, [
    profile.id,
    profile.revision,
    profile.intake_state.active_question_id,
    questionAttempt,
  ])

  useEffect(() => {
    const controller = new AbortController()
    interpretationLoad.current = controller
    setInterpretationInterrupted(false)
    const id = profile.latest_interpretation_id
    setInterpretation(null)
    setDecisions({})
    setInterpretationLoading(!!id)
    setInterpretationError('')
    if (id) {
      void request<Interpretation>(
        `/interpretations/${encodeURIComponent(id)}`,
        { signal: controller.signal },
      )
        .then((value) => {
          if (controller.signal.aborted) return
          if (!interpretationMatches(value, id, latest.current))
            throw new Error('해석 응답이 요청한 입력과 맞지 않아요.')
          if (
            value.base_revision === latest.current.revision &&
            value.status !== 'applied'
          ) {
            setInterpretation(value)
            setDecisions({})
          } else if (value.status !== 'applied')
            setInterpretationError(
              '입력이 바뀌어 이전 해석을 표시하지 않았어요. 필요한 경우 다시 해석해 주세요.',
            )
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted)
            setInterpretationError(
              cause instanceof Error
                ? cause.message
                : '입력 해석을 불러오지 못했어요.',
            )
        })
        .finally(() => {
          if (!controller.signal.aborted) setInterpretationLoading(false)
        })
    }
    return () => controller.abort()
  }, [profile.id, profile.latest_interpretation_id, interpretationAttempt])

  async function generate(signal: AbortSignal) {
    const response = await mutate<JobAccepted | IntakeState>(
      `${path}/questions`,
      { expected_revision: latest.current.revision },
      signal,
    )
    if ('job_id' in response)
      await finishJob(response.job_id, signal, response.poll_after_ms)
    else await refresh(signal)
  }

  async function answer(optionId: string, signal: AbortSignal) {
    if (!question || question.base_revision !== latest.current.revision)
      throw new Error('최신 질문을 다시 확인해 주세요.')
    const next = await mutate<Profile>(
      `${path}/questions/${encodeURIComponent(question.id)}/answer`,
      {
        expected_revision: question.base_revision,
        selected_option_id: optionId,
      },
      signal,
    )
    if (signal.aborted) return
    applyProfile(next)
    setInterpretation(null)
    if (next.intake_state.next_action === 'generate_question')
      await generate(signal)
  }

  async function interpret(signal: AbortSignal) {
    if (pendingDeletion) throw new Error('자료 삭제 결과를 먼저 확인해 주세요.')
    interpretationLoad.current?.abort()
    setInterpretationInterrupted(
      !!latest.current.latest_interpretation_id && !interpretation,
    )
    setInterpretationLoading(false)
    setInterpretationError('')
    const accepted = await mutate<JobAccepted>(
      `${path}/interpretations`,
      {
        expected_revision: latest.current.revision,
        text,
        ready_document_ids: latest.current.documents
          .filter((item) => item.status === 'ready')
          .map((item) => item.id),
        ...(question ? { question_id: question.id } : {}),
      },
      signal,
    )
    if (signal.aborted) return
    interpretationInput.current = {
      jobId: accepted.job_id,
      interpretationId: null,
      text,
    }
    await finishJob(accepted.job_id, signal, accepted.poll_after_ms)
  }

  async function acceptInterpretation(signal: AbortSignal) {
    if (
      !interpretation ||
      pendingDocument ||
      pendingDeletion ||
      interpretation.profile_id !== latest.current.id ||
      interpretation.status !== 'ready' ||
      interpretation.base_revision !== latest.current.revision
    )
      throw new Error('입력이 변경됐어요. 해석을 다시 요청해 주세요.')
    if (
      interpretation.proposals.length === 0 ||
      interpretation.proposals.some((item) => !decisions[item.id])
    )
      throw new Error('각 제안을 확인한 뒤 수락 또는 거절을 선택해 주세요.')
    const input = interpretationInput.current
    const next = await mutate<Profile>(
      `${path}/interpretations/${encodeURIComponent(interpretation.id)}/apply`,
      {
        expected_revision: interpretation.base_revision,
        accepted_proposal_ids: interpretation.proposals
          .filter((item) => decisions[item.id] === 'accept')
          .map((item) => item.id),
        rejected_proposal_ids: interpretation.proposals
          .filter((item) => decisions[item.id] === 'reject')
          .map((item) => item.id),
      },
      signal,
    )
    if (!signal.aborted) {
      applyProfile(next, interpretation.base_revision + 1)
      interpretationLoad.current?.abort()
      setInterpretationLoading(false)
      setInterpretation(null)
      setNotice('제안 확인 결과를 저장했어요.')
      if (input?.interpretationId === interpretation.id)
        setText((current) => (current === input.text ? '' : current))
      interpretationInput.current = null
    }
  }

  async function addDocument(
    source: 'files' | 'links' | 'texts',
    signal: AbortSignal,
  ) {
    if (!options) throw new Error('자료 등록 조건을 먼저 불러와야 해요.')
    if (pendingDocument) throw new Error('접수된 자료를 먼저 확인해 주세요.')
    if (pendingDeletion) throw new Error('자료 삭제 결과를 먼저 확인해 주세요.')
    if (latest.current.documents.length >= options.max_profile_documents)
      throw new Error('등록 가능한 자료 수를 초과했어요.')
    if (source === 'texts' && !documentText.trim())
      throw new Error('자료 내용을 입력해 주세요.')
    const expectedRevision = latest.current.revision
    const submittedText = documentText
    const submittedUrl = documentUrl
    const url = source === 'links' ? httpUrl(documentUrl) : null
    if (source === 'links' && !url)
      throw new Error(
        '아이디·비밀번호가 없는 올바른 http 또는 https 주소를 입력해 주세요.',
      )
    let body: unknown = {
      expected_revision: expectedRevision,
      kind,
      ...(source === 'links' ? { url } : { text: documentText }),
    }
    if (source === 'files') {
      if (!file) throw new Error('파일을 선택해 주세요.')
      const extension = file.name.split('.').at(-1)?.toLowerCase()
      if (
        !extension ||
        !options.allowed_file_extensions.some(
          (value) => value.replace(/^\./, '').toLowerCase() === extension,
        )
      )
        throw new Error('지원하지 않는 파일 형식이에요.')
      if (file.size > options.max_upload_bytes)
        throw new Error('파일이 업로드 크기 제한을 초과했어요.')
      const form = new FormData()
      form.set('expected_revision', String(expectedRevision))
      form.set('kind', kind)
      form.set('file', file)
      body = form
    }
    const accepted = await request<DocumentAccepted>(
      `${path}/documents/${source}`,
      { method: 'POST', body, signal },
    ).catch((cause: unknown) => {
      if (source === 'links' && !signal.aborted) setLinkFallback(true)
      throw cause
    })
    if (signal.aborted) return
    const document = accepted?.document
    if (
      !document ||
      document.profile_id !== latest.current.id ||
      typeof document.id !== 'string' ||
      !document.id.trim() ||
      document.kind !== kind ||
      document.source_type !==
        { files: 'file', links: 'url', texts: 'text' }[source] ||
      !['queued', 'processing', 'ready', 'failed'].includes(document.status) ||
      (document.job_id !== null &&
        (typeof document.job_id !== 'string' || !document.job_id.trim())) ||
      !Number.isInteger(accepted.profile_revision) ||
      accepted.profile_revision <= expectedRevision
    )
      throw new Error(
        '자료 등록 응답을 확인할 수 없어요. 최신 입력을 확인한 뒤 다시 시도해 주세요.',
      )
    const pending: PendingDocument = { ...accepted, operation: 'register' }
    setPendingDocument(pending)
    setLinkFallback(false)
    if (document.job_id) rememberJob(document.job_id)
    if (source === 'texts')
      setDocumentText((current) => (current === submittedText ? '' : current))
    if (source === 'links')
      setDocumentUrl((current) => (current === submittedUrl ? '' : current))
    if (source === 'files') {
      setFile(null)
      setFileInputKey((value) => value + 1)
    }
    await confirmDocument(pending, signal)
  }

  async function confirmDocument(
    accepted: PendingDocument,
    signal: AbortSignal,
  ) {
    const next = await request<Profile>(path, { signal })
    if (signal.aborted) return
    const action = accepted.operation === 'retry' ? '재시도한' : '등록한'
    if (
      !Array.isArray(next?.documents) ||
      !next.documents.some(
        (document) =>
          document.id === accepted.document.id &&
          document.profile_id === latest.current.id,
      )
    )
      throw new Error(
        `${action} 자료가 목록에서 확인되지 않아요. ${action} 자료를 다시 확인해 주세요.`,
      )
    if (next.revision < accepted.profile_revision)
      throw new Error(
        `${action} 자료의 최신 버전을 아직 확인하지 못했어요. ${action} 자료를 다시 확인해 주세요.`,
      )
    applyProfile(next, accepted.profile_revision)
    setPendingDocument(null)
    setNotice(
      accepted.operation === 'retry'
        ? '자료 재처리를 확인했어요. 처리 상태와 추출 미리보기를 확인해 주세요.'
        : '자료 등록을 확인했어요. 처리 상태와 추출 미리보기를 확인해 주세요.',
    )
    if (accepted.document.job_id)
      await finishJob(accepted.document.job_id, signal)
  }

  async function retryDocument(document: Document, signal: AbortSignal) {
    if (pendingDocument || pendingDeletion)
      throw new Error('접수된 자료 처리 결과를 먼저 확인해 주세요.')
    const current = latest.current.documents.find(
      (item) => item.id === document.id,
    )
    if (
      !current ||
      current.profile_id !== latest.current.id ||
      current.status !== 'failed'
    )
      throw new Error('현재 입력의 실패 자료를 다시 확인해 주세요.')
    const expectedRevision = latest.current.revision
    const accepted = await request<DocumentAccepted>(
      `/documents/${encodeURIComponent(current.id)}/retry`,
      { method: 'POST', body: { expected_revision: expectedRevision }, signal },
    )
    if (signal.aborted) return
    const retried = accepted?.document
    if (
      !retried ||
      retried.id !== current.id ||
      retried.profile_id !== latest.current.id ||
      !['queued', 'processing', 'ready', 'failed'].includes(retried.status) ||
      (retried.job_id !== null &&
        (typeof retried.job_id !== 'string' || !retried.job_id.trim())) ||
      !Number.isInteger(accepted.profile_revision) ||
      accepted.profile_revision <= expectedRevision
    )
      throw new Error(
        '자료 재처리 응답을 확인할 수 없어요. 최신 입력을 확인해 주세요.',
      )
    const pending: PendingDocument = { ...accepted, operation: 'retry' }
    setPendingDocument(pending)
    if (retried.job_id) rememberJob(retried.job_id)
    await confirmDocument(pending, signal)
  }

  async function confirmDeletion(
    pending: PendingDeletion,
    signal: AbortSignal,
  ) {
    const next = await request<Profile>(path, { signal })
    if (signal.aborted) return
    if (
      !Array.isArray(next?.documents) ||
      next.documents.some((item) => item.id === pending.id)
    )
      throw new Error(
        '삭제한 자료가 아직 목록에 있어요. 삭제 후 목록을 다시 확인해 주세요.',
      )
    if (next.revision < pending.minimumRevision)
      throw new Error(
        '삭제 후 최신 버전을 아직 확인하지 못했어요. 목록을 다시 확인해 주세요.',
      )
    applyProfile(next, pending.minimumRevision)
    setPendingDeletion(null)
    setNotice('자료 삭제 결과를 확인했어요.')
  }

  async function deleteDocument(id: string, signal: AbortSignal) {
    if (pendingDeletion) throw new Error('자료 삭제 결과를 먼저 확인해 주세요.')
    const current = latest.current.documents.find((item) => item.id === id)
    if (!current || current.profile_id !== latest.current.id)
      throw new Error('현재 입력의 자료를 다시 확인해 주세요.')
    const expectedRevision = latest.current.revision
    await request<void>(
      `/documents/${encodeURIComponent(id)}?expected_revision=${expectedRevision}`,
      {
        method: 'DELETE',
        signal,
      },
    )
    if (signal.aborted) return
    const pending = { id, minimumRevision: expectedRevision + 1 }
    setPendingDeletion(pending)
    interpretationLoad.current?.abort()
    setInterpretationLoading(false)
    setInterpretation((current) =>
      current?.proposals.some((proposal) => proposal.document_id === id)
        ? null
        : current,
    )
    if (pendingDocument?.document.id === id) {
      setPendingDocument(null)
      if (pendingJob === pendingDocument.document.job_id) rememberJob(null)
    }
    if (current.job_id && pendingJob === current.job_id) rememberJob(null)
    await confirmDeletion(pending, signal)
  }

  async function editAnswer(slot: string, signal: AbortSignal) {
    const expectedRevision = latest.current.revision
    const answers: Answers = { ...latest.current.answers }
    delete answers[slot as keyof Answers]
    const next = await request<Profile>(path, {
      method: 'PATCH',
      body: { expected_revision: expectedRevision, answers },
      signal,
    })
    if (!signal.aborted) {
      applyProfile(next, expectedRevision + 1)
      setInterpretation(null)
      setNotice(
        next.intake_state.next_action === 'generate_question'
          ? '해당 답변을 다시 선택할 수 있도록 저장했어요. 다음 질문을 요청해 주세요.'
          : '수정한 입력을 저장했어요. 화면의 다음 단계를 확인해 주세요.',
      )
    }
  }

  function editReview(id: string, edit: ReviewEdit) {
    if (reviewRevision === null) setReviewRevision(profile.revision)
    setReviewEdits((value) => ({ ...value, [id]: edit }))
  }

  async function saveReview(signal: AbortSignal) {
    if (pendingDocument || pendingDeletion)
      throw new Error('자료 처리 결과를 먼저 확인해 주세요.')
    if (reviewRevision !== null && reviewRevision !== latest.current.revision)
      throw new Error(
        '자료가 변경됐어요. 편집 취소 후 최신 내용을 확인하고 다시 수정해 주세요.',
      )
    const expectedRevision = reviewRevision ?? latest.current.revision
    const items = latest.current.reviewed_items.map((item) => ({
      id: item.id,
      label: reviewEdits[item.id]?.label ?? item.label,
      state: reviewEdits[item.id]?.state ?? item.state,
    }))
    const next = await request<Profile>(`${path}/review-items`, {
      method: 'PATCH',
      body: {
        expected_revision: expectedRevision,
        items,
      },
      signal,
    })
    if (!signal.aborted) {
      applyProfile(next, expectedRevision + 1)
      setReviewEdits({})
      setReviewRevision(null)
      setNotice('확인 내용을 저장했어요.')
    }
  }

  const canSubmit =
    profile.intake_state.next_action === 'review' &&
    !!profile.review_token &&
    profile.unresolved_conflicts.length === 0 &&
    profile.documents.every((item) => item.status === 'ready') &&
    reviewRevision === null &&
    pendingDocument === null &&
    pendingDeletion === null
  const showDocuments =
    [
      'collect_documents',
      'review_sources',
      'resolve_conflict',
      'review',
    ].includes(profile.intake_state.next_action) ||
    profile.documents.length > 0 ||
    pendingDocument !== null ||
    pendingDeletion !== null ||
    reviewRevision !== null ||
    linkFallback
  const showLinkFallback =
    linkFallback ||
    profile.documents.some(
      (document) =>
        document.source_type === 'url' && document.status === 'failed',
    )
  const reviewIsStale =
    reviewRevision !== null && reviewRevision !== profile.revision
  const showReview =
    !pendingDeletion &&
    (profile.reviewed_items.length > 0 || reviewRevision !== null)
  const interpretationReady =
    pendingDocument === null &&
    pendingDeletion === null &&
    interpretation?.profile_id === profile.id &&
    interpretation.status === 'ready' &&
    interpretation.base_revision === profile.revision

  function answerValue(
    key: keyof Answers,
    value: Answers[keyof Answers],
  ): string {
    if (value === null || value === undefined) return '미입력'
    if (typeof value === 'boolean')
      return value ? '확인했어요' : '확인하지 않았어요'
    if (typeof value === 'number') return value.toLocaleString('ko-KR')
    if (typeof value === 'object') {
      const labels: Record<string, string> = {
        age: '나이',
        employment_status: '현재 상태',
        education: '학력',
        residence_region: '거주 지역',
      }
      return Object.entries(value)
        .map(
          ([field, entry]) =>
            `${labels[field] ?? field}: ${entry === null ? '미입력' : field === 'employment_status' ? (valueLabels[String(entry)] ?? String(entry)) : String(entry)}`,
        )
        .join(' · ')
    }
    const source =
      key === 'field_code'
        ? options?.fields
        : key === 'entry_intent'
          ? options?.entry_intents
          : undefined
    if (source)
      return source.find((item) => item.value === value)?.label ?? value
    return [
      'experience',
      'previous_gap',
      'learning_goal',
      'availability',
      'delivery_mode',
      'budget_status',
      'start_timing',
      'document_decision',
    ].includes(key)
      ? (valueLabels[value] ?? value)
      : value
  }

  return (
    <div className="stack">
      <section className="panel stack" aria-label="학습 입력">
        <div className="section-heading">
          <div>
            <p className="eyebrow">나에게 맞는 배움</p>
            <h1>한 가지씩, 나의 다음을 찾아요</h1>
          </div>
          <button
            disabled={busy}
            onClick={() =>
              void perform(async (signal) => {
                await refresh(signal)
              })
            }
          >
            최신 입력 확인
          </button>
        </div>
        <ol className="progress-list" aria-label="입력 진행 상황">
          {sections.map(([id, name]) => (
            <li
              key={id}
              aria-current={
                profile.intake_state.section === id ? 'step' : undefined
              }
            >
              {name}
              {profile.completed_sections.includes(id) ? ' · 완료' : ''}
            </li>
          ))}
        </ol>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {!options && (
          <button
            disabled={busy}
            onClick={() =>
              void perform(async (signal) => {
                const value = await request<OnboardingOptions>(
                  '/onboarding/options',
                  { signal },
                )
                if (!signal.aborted) setOptions(value)
              })
            }
          >
            입력 옵션 다시 불러오기
          </button>
        )}
        {notice && <p role="status">{notice}</p>}
        {job && (
          <p role="status">
            {jobMessage(job)}
            {job.error ? ` · ${job.error.message}` : ''}
          </p>
        )}
        {busy && (
          <button
            onClick={() => {
              active.current?.abort()
              active.current = null
              setBusy(false)
              setNotice(
                '화면의 대기를 중지했어요. 서버 작업은 계속될 수 있으니 새 요청 전에 상태를 확인해 주세요.',
              )
            }}
          >
            대기 중지
          </button>
        )}
        {pendingJob && !busy && !pendingDocument && (
          <button
            onClick={() =>
              void perform((signal) => finishJob(pendingJob, signal))
            }
          >
            진행 중이던 작업 확인
          </button>
        )}
        {profile.intake_state.active_question_id && !question && (
          <div className="stack">
            {questionLoading && (
              <p role="status">저장된 질문을 불러오고 있어요…</p>
            )}
            {questionError && (
              <p role="alert" className="error">
                {questionError}
              </p>
            )}
            {!questionLoading && (
              <button
                disabled={busy}
                onClick={() => setQuestionAttempt((value) => value + 1)}
              >
                저장된 질문 다시 불러오기
              </button>
            )}
          </div>
        )}
        {question &&
        question.base_revision === profile.revision &&
        question.id === profile.intake_state.active_question_id ? (
          <div className="stack">
            {question.origin === 'fallback' && (
              <p className="muted">서버가 제공한 기본 질문입니다.</p>
            )}
            <p>{question.acknowledgement}</p>
            <h2>{question.question}</h2>
            <p className="muted">{question.helper_text}</p>
            <div className="option-grid">
              {question.options.map((option) => (
                <button
                  className="option-card"
                  key={option.id}
                  disabled={busy}
                  onClick={() =>
                    void perform((signal) => answer(option.id, signal))
                  }
                >
                  <strong>{option.label}</strong>
                  <span>{option.description}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          !profile.intake_state.active_question_id &&
          profile.intake_state.next_action === 'generate_question' && (
            <button
              className="primary"
              disabled={busy || !options?.question_generation_enabled}
              onClick={() => void perform(generate)}
            >
              다음 질문 받기
            </button>
          )
        )}
        {profile.unresolved_conflicts.length > 0 && (
          <div role="status">
            <h2>다시 확인할 입력이 있어요</h2>
            <ul>
              {profile.unresolved_conflicts.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        {profile.selection_summaries.length > 0 && (
          <details>
            <summary>지금까지 선택한 내용</summary>
            <ul className="stack">
              {profile.selection_summaries.map((item) => (
                <li key={item.question_id}>
                  {item.label}{' '}
                  <button
                    disabled={busy}
                    onClick={() =>
                      void perform((signal) =>
                        editAnswer(item.target_slot, signal),
                      )
                    }
                  >
                    다시 선택
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
        {options?.interpretation_enabled &&
          (question?.allow_custom_text || showDocuments) && (
            <details>
              <summary>직접 알려줄게요</summary>
              <div className="stack">
                <label>
                  내 경험이나 원하는 배움
                  <textarea
                    value={text}
                    maxLength={6000}
                    onChange={(event) => setText(event.target.value)}
                  />
                </label>
                <button
                  disabled={
                    busy ||
                    !!pendingDeletion ||
                    (!text.trim() &&
                      !profile.documents.some(
                        (item) => item.status === 'ready',
                      ))
                  }
                  onClick={() => void perform(interpret)}
                >
                  입력과 준비된 자료 해석하기
                </button>
              </div>
            </details>
          )}
        {interpretationLoading && (
          <p role="status">저장된 해석을 불러오고 있어요…</p>
        )}
        {(interpretationError || interpretationInterrupted) && (
          <div className="stack">
            {interpretationError && (
              <p role="alert" className="error">
                {interpretationError}
              </p>
            )}
            <button
              disabled={busy || interpretationLoading || !!pendingDeletion}
              onClick={() => setInterpretationAttempt((value) => value + 1)}
            >
              저장된 해석 다시 불러오기
            </button>
          </div>
        )}
        {interpretation && (
          <section className="stack" aria-label="입력 해석 확인">
            <h2>내 뜻과 맞는지 확인해 주세요</h2>
            <p>{interpretation.assistant_text}</p>
            {!interpretationReady && (
              <p role="alert">
                이 해석은 현재 입력에 적용할 수 없어요. 다시 해석해 주세요.
              </p>
            )}
            {interpretation.proposals.length === 0 && (
              <p>
                반영할 해석 제안이 없어요. 내용을 보완해 다시 해석해 주세요.
              </p>
            )}
            {interpretation.proposals.map((proposal) => (
              <fieldset
                key={proposal.id}
                disabled={busy || !interpretationReady}
              >
                <legend>
                  {proposal.label}
                  {proposal.status === 'conflict' ? ' · 기존 답변과 상충' : ''}
                </legend>
                <blockquote>{proposal.quote}</blockquote>
                <p>
                  {proposal.value.kind === 'text'
                    ? proposal.value.text
                    : proposal.value.integer}
                </p>
                <label>
                  <input
                    type="radio"
                    name={`proposal-${proposal.id}`}
                    checked={decisions[proposal.id] === 'accept'}
                    onChange={() =>
                      setDecisions((value) => ({
                        ...value,
                        [proposal.id]: 'accept',
                      }))
                    }
                  />
                  맞아요
                </label>
                <label>
                  <input
                    type="radio"
                    name={`proposal-${proposal.id}`}
                    checked={decisions[proposal.id] === 'reject'}
                    onChange={() =>
                      setDecisions((value) => ({
                        ...value,
                        [proposal.id]: 'reject',
                      }))
                    }
                  />
                  반영하지 않을게요
                </label>
              </fieldset>
            ))}
            <button
              disabled={
                busy ||
                !interpretationReady ||
                interpretation.proposals.length === 0 ||
                interpretation.proposals.some((item) => !decisions[item.id])
              }
              onClick={() => void perform(acceptInterpretation)}
            >
              확인한 제안만 반영
            </button>
          </section>
        )}
      </section>
      {showDocuments && (
        <section className="panel stack" aria-label="참고 자료">
          <h2>내 경험을 알려주는 자료</h2>
          <p>자료에서 읽은 내용은 직접 확인한 뒤에만 입력에 반영돼요.</p>
          <fieldset disabled={busy || !options}>
            <legend>자료 추가</legend>
            <label>
              자료 종류
              <select
                value={kind}
                onChange={(event) =>
                  setKind(event.target.value as DocumentKind)
                }
              >
                <option value="resume">이력서</option>
                <option value="job_description">채용 공고</option>
                <option value="portfolio">포트폴리오</option>
                <option value="other">기타</option>
              </select>
            </label>
            <p className="muted">{options?.upload_limit_note}</p>
            <label>
              파일
              <input
                key={fileInputKey}
                ref={fileInput}
                type="file"
                accept={options?.allowed_file_extensions
                  .map((value) => (value.startsWith('.') ? value : `.${value}`))
                  .join(',')}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <button
              disabled={!file || !!pendingDocument || !!pendingDeletion}
              onClick={() =>
                void perform((signal) => addDocument('files', signal))
              }
            >
              파일 등록
            </button>
            <label>
              공개 자료 주소
              <input
                type="url"
                aria-describedby={linkHintId}
                value={documentUrl}
                onChange={(event) => setDocumentUrl(event.target.value)}
              />
            </label>
            <p id={linkHintId} className="muted">
              로그인 없이 볼 수 있는 http 또는 https 주소를 입력해 주세요.
              링크를 읽지 못하면 파일이나 텍스트로 대신 등록할 수 있어요.
            </p>
            <button
              disabled={
                !documentUrl.trim() || !!pendingDocument || !!pendingDeletion
              }
              onClick={() =>
                void perform((signal) => addDocument('links', signal))
              }
            >
              링크 등록
            </button>
            <label>
              자료 내용
              <textarea
                ref={documentTextInput}
                value={documentText}
                onChange={(event) => setDocumentText(event.target.value)}
              />
            </label>
            <button
              disabled={
                !documentText.trim() || !!pendingDocument || !!pendingDeletion
              }
              onClick={() =>
                void perform((signal) => addDocument('texts', signal))
              }
            >
              텍스트 등록
            </button>
          </fieldset>
          {showLinkFallback && (
            <section className="stack" aria-label="링크 대체 입력">
              <h3>링크 대신 자료 등록</h3>
              <p>
                작성한 내용을 유지한 채 파일이나 텍스트로 대신 등록할 수 있어요.
              </p>
              <div className="buttons">
                <button
                  disabled={busy || !options}
                  onClick={() => documentTextInput.current?.focus()}
                >
                  텍스트 입력으로 이동
                </button>
                <button
                  disabled={busy || !options}
                  onClick={() => fileInput.current?.focus()}
                >
                  파일 선택으로 이동
                </button>
              </div>
            </section>
          )}
          {pendingDocument && (
            <div className="stack">
              <p role="status">
                {pendingDocument.operation === 'retry'
                  ? '자료 재처리는 접수됐어요. 목록을 다시 확인하면 처리 상태를 볼 수 있어요.'
                  : '자료 등록은 접수됐어요. 목록을 다시 확인하면 처리 상태를 볼 수 있어요.'}
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void perform((signal) =>
                    confirmDocument(pendingDocument, signal),
                  )
                }
              >
                {pendingDocument.operation === 'retry'
                  ? '재시도한 자료 다시 확인'
                  : '등록한 자료 다시 확인'}
              </button>
            </div>
          )}
          {pendingDeletion && (
            <div className="stack">
              <p role="status">
                자료 삭제 요청은 처리됐어요. 최신 목록을 확인해 주세요.
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void perform((signal) =>
                    confirmDeletion(pendingDeletion, signal),
                  )
                }
              >
                삭제 후 목록 다시 확인
              </button>
            </div>
          )}
          {profile.documents.map((document) => (
            <article className="document-card stack" key={document.id}>
              <h3>{document.display_name}</h3>
              {document.source_type === 'url' && (
                <DocumentSource url={document.source_url} />
              )}
              <p>
                {
                  {
                    queued: '대기 중',
                    processing: '처리 중',
                    ready: '확인 가능',
                    failed: '처리 실패',
                  }[document.status]
                }
              </p>
              {(document.error_code || document.status === 'failed') && (
                <p role="alert">
                  {document.source_type === 'url'
                    ? '링크 본문을 읽지 못했어요'
                    : '자료를 읽지 못했어요'}
                  {document.error_code ? ` (${document.error_code})` : ''}.
                  파일이나 텍스트로 다시 등록할 수 있어요.
                </p>
              )}
              {document.status === 'ready' &&
                document.id !== pendingDeletion?.id && (
                  <div>
                    <p className="muted">
                      추출 미리보기이며 원문 전체가 아닐 수 있어요.
                    </p>
                    {document.extracted_preview ? (
                      <blockquote>{document.extracted_preview}</blockquote>
                    ) : (
                      <p>추출 미리보기가 제공되지 않았어요.</p>
                    )}
                  </div>
                )}
              <div className="buttons">
                {document.job_id &&
                  ['queued', 'processing'].includes(document.status) && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void perform((signal) =>
                          finishJob(document.job_id!, signal),
                        )
                      }
                    >
                      처리 상태 확인
                    </button>
                  )}
                {document.status === 'failed' && (
                  <button
                    disabled={busy || !!pendingDocument || !!pendingDeletion}
                    onClick={() =>
                      void perform((signal) => retryDocument(document, signal))
                    }
                  >
                    다시 처리
                  </button>
                )}
                <button
                  disabled={busy || !!pendingDeletion}
                  onClick={() => {
                    if (window.confirm('이 자료와 추출 내용을 삭제할까요?'))
                      void perform((signal) =>
                        deleteDocument(document.id, signal),
                      )
                  }}
                >
                  자료 삭제
                </button>
              </div>
            </article>
          ))}
          {profile.documents.length === 0 && (
            <button
              disabled={busy || !!pendingDocument || !!pendingDeletion}
              onClick={() =>
                void perform(async (signal) => {
                  const next = await request<Profile>(path, {
                    method: 'PATCH',
                    body: {
                      expected_revision: latest.current.revision,
                      answers: {
                        ...latest.current.answers,
                        document_decision: 'none',
                      },
                    },
                    signal,
                  })
                  if (!signal.aborted) applyProfile(next)
                })
              }
            >
              자료 없이 진행
            </button>
          )}
          {showReview && (
            <div className="stack">
              <h3>추출한 내용을 확인해 주세요</h3>
              {reviewIsStale && (
                <p role="alert">
                  자료가 변경됐어요. 작성한 내용을 확인한 뒤 편집 취소로 최신
                  내용을 불러와 주세요.
                </p>
              )}
              {profile.reviewed_items.map((item) => {
                const edit = reviewEdits[item.id] ?? {
                  label: item.label,
                  state: item.state,
                }
                return (
                  <fieldset key={item.id} disabled={busy}>
                    <legend>{item.label}</legend>
                    <p>
                      출처:{' '}
                      {item.origin === 'user'
                        ? '직접 입력'
                        : (profile.documents.find(
                            (document) => document.id === item.document_id,
                          )?.display_name ?? '출처 자료 확인 필요')}
                    </p>
                    {item.evidence_quote && (
                      <blockquote>{item.evidence_quote}</blockquote>
                    )}
                    <label>
                      표시 내용
                      <input
                        value={edit.label}
                        onChange={(event) =>
                          editReview(item.id, {
                            ...edit,
                            label: event.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      나의 상태
                      <select
                        value={edit.state}
                        onChange={(event) =>
                          editReview(item.id, {
                            ...edit,
                            state: event.target.value as ReviewedItem['state'],
                          })
                        }
                      >
                        <option value="have">경험이 있어요</option>
                        <option value="want_to_learn">배우고 싶어요</option>
                        <option value="unknown">아직 모르겠어요</option>
                        <option value="excluded">반영하지 않을게요</option>
                      </select>
                    </label>
                  </fieldset>
                )
              })}
              <div className="buttons">
                <button
                  disabled={busy || !!pendingDocument || reviewIsStale}
                  onClick={() => void perform(saveReview)}
                >
                  확인 내용 저장
                </button>
                <button
                  disabled={busy || reviewRevision === null}
                  onClick={() => {
                    setReviewEdits({})
                    setReviewRevision(null)
                  }}
                >
                  편집 취소
                </button>
              </div>
            </div>
          )}
        </section>
      )}
      {profile.intake_state.next_action === 'review' && (
        <section className="panel stack">
          <h2>입력한 내용을 확인해 주세요</h2>
          <ul>
            {profile.selection_summaries.map((item) => (
              <li key={item.question_id}>{item.label}</li>
            ))}
          </ul>
          <dl>
            {Object.entries(profile.answers).map(([key, value]) => (
              <div key={key}>
                <dt>{answerLabels[key as keyof Answers]}</dt>
                <dd>{answerValue(key as keyof Answers, value)}</dd>
              </div>
            ))}
          </dl>
          <p>확인한 입력과 자료로 교육 추천을 시작합니다.</p>
          <button
            className="primary"
            disabled={busy || !canSubmit}
            onClick={() =>
              void perform(async (signal) => {
                const accepted = await mutate<JobAccepted>(
                  `${path}/submit`,
                  {
                    expected_revision: latest.current.revision,
                    review_token: latest.current.review_token,
                  },
                  signal,
                )
                await finishJob(accepted.job_id, signal, accepted.poll_after_ms)
              })
            }
          >
            확인하고 교육 추천 받기
          </button>
          {!canSubmit && (
            <p>확인이 필요한 입력이나 자료를 먼저 처리해 주세요.</p>
          )}
        </section>
      )}
    </div>
  )
}
