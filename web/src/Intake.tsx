import { useEffect, useRef, useState } from 'react'
import { ApiError, request, waitForJob } from './api'
import type {
  Answers,
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

const answerLabels: Record<keyof Answers, string> = {
  entry_intent: '시작한 이유', field_code: '관심 분야', custom_field: '직접 입력한 분야',
  interest_activity: '관심 활동', field_confirmed: '분야 확인', experience: '현재 경험',
  previous_gap: '보완하고 싶은 점', learning_goal: '학습 목표', availability: '참여 시간',
  delivery_mode: '참여 방식', commute_region: '참여 가능한 지역', budget_status: '예산 기준',
  budget_krw: '예산 (원)', start_timing: '시작 시기', document_decision: '참고 자료',
  self_description: '나의 경험', eligibility_context: '참여 자격 관련 정보',
  goal_text: '원하는 배움', constraints_text: '참여 조건',
}

const valueLabels: Record<string, string> = {
  beginner: '처음 시작해요', basic: '기초를 배웠어요', project: '프로젝트 경험이 있어요',
  work: '실무 경험이 있어요', unknown: '아직 모르겠어요', application: '활용 경험',
  depth: '더 깊은 이해', collaboration: '협업 경험', foundation: '기초 다지기',
  portfolio: '포트폴리오 만들기', weekday_day: '평일 낮', evening_weekend: '저녁이나 주말',
  flexible: '시간 조정 가능', online: '온라인', offline: '오프라인', hybrid: '온·오프라인 병행',
  any: '참여 방식 무관', amount: '정해진 예산', no_limit: '예산 제한 없음', soon: '곧 시작',
  one_to_three_months: '1~3개월 이내', browse: '먼저 알아보기', provided: '자료 등록',
  none: '자료 없이 진행', employed: '재직 중', unemployed: '미취업', student: '학생',
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

export function Intake({ profile, onProfile, onRun }: Props) {
  const [options, setOptions] = useState<OnboardingOptions | null>(null)
  const [question, setQuestion] = useState<IntakeQuestion | null>(null)
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null)
  const [decisions, setDecisions] = useState<Record<string, 'accept' | 'reject'>>({})
  const [text, setText] = useState('')
  const [documentText, setDocumentText] = useState('')
  const [documentUrl, setDocumentUrl] = useState('')
  const [kind, setKind] = useState<DocumentKind>('resume')
  const [file, setFile] = useState<File | null>(null)
  const [fileInputKey, setFileInputKey] = useState(0)
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
  const latest = useRef(profile)
  latest.current = profile
  const callbacks = useRef({ onProfile, onRun })
  callbacks.current = { onProfile, onRun }
  const path = `/profiles/${encodeURIComponent(profile.id)}`

  function applyProfile(next: Profile) {
    if (next.id !== latest.current.id || next.revision < latest.current.revision) return
    latest.current = next
    callbacks.current.onProfile(next)
    setQuestion((current) => current?.base_revision === next.revision && current.id === next.intake_state.active_question_id ? current : null)
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
          if (!controller.signal.aborted) setError('다른 변경 사항을 불러왔어요. 작성 중인 내용은 유지했으니 최신 입력을 확인하고 다시 시도해 주세요.')
        } catch {
          if (!controller.signal.aborted) setError('입력이 변경됐지만 최신 내용을 불러오지 못했어요. 새로 확인해 주세요.')
        }
      } else {
        setError(cause instanceof Error ? cause.message : '요청을 처리하지 못했어요.')
      }
    } finally {
      if (active.current === controller) {
        active.current = null
        if (!controller.signal.aborted) setBusy(false)
      }
    }
  }

  async function mutate<T>(endpoint: string, body: unknown, signal: AbortSignal) {
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

  async function finishJob(id: string, signal: AbortSignal, pollAfterMs?: number) {
    if (signal.aborted) return
    rememberJob(id)
    const result = await waitForJob(id, signal, (update) => {
      if (signal.aborted) return
      if (update.profile_id !== latest.current.id) throw new Error('현재 입력의 작업이 아니에요.')
      setJob(update)
      if (['failed', 'cancelled', 'superseded'].includes(update.status)) {
        rememberJob(null)
        keys.current.clear()
      }
    }, pollAfterMs)
    if (signal.aborted) return
    if (result.profile_id !== latest.current.id) throw new Error('현재 입력의 작업이 아니에요.')
    if (result.status !== 'succeeded') throw new Error(result.error?.message || '작업을 완료하지 못했어요.')
    const next = await refresh(signal)
    if (signal.aborted) return
    if (result.kind === 'recommendation') {
      if (!result.result?.recommendation_run_id) throw new Error('완료한 추천 결과를 확인하지 못했어요. 작업 상태를 다시 확인해 주세요.')
      rememberJob(null)
      callbacks.current.onRun(result.result.recommendation_run_id)
      return
    }
    if (result.kind === 'intake_question') {
      if (!result.result?.question_id) throw new Error('완료한 질문을 확인하지 못했어요. 작업 상태를 다시 확인해 주세요.')
      const found = await request<IntakeQuestion>(`/questions/${encodeURIComponent(result.result.question_id)}`, { signal })
      if (signal.aborted) return
      if (found.profile_id === next.id && found.base_revision === latest.current.revision && found.status === 'ready') setQuestion(found)
      else setNotice('입력이 바뀌어 이전 질문을 표시하지 않았어요. 다음 질문을 다시 요청해 주세요.')
    }
    if (result.kind === 'intake_interpretation') {
      if (!result.result?.interpretation_id) throw new Error('완료한 해석을 확인하지 못했어요. 작업 상태를 다시 확인해 주세요.')
      const found = await request<Interpretation>(`/interpretations/${encodeURIComponent(result.result.interpretation_id)}`, { signal })
      if (signal.aborted) return
      if (found.profile_id !== latest.current.id) throw new Error('현재 입력의 해석이 아니에요.')
      if (found.base_revision === latest.current.revision && found.status !== 'applied') {
        setInterpretation(found)
        setDecisions({})
      } else setNotice('입력이 바뀌어 이전 해석을 표시하지 않았어요. 필요한 경우 다시 해석해 주세요.')
    }
    rememberJob(null)
  }

  useEffect(() => {
    const controller = new AbortController()
    void request<OnboardingOptions>('/onboarding/options', { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setOptions(value) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '입력 옵션을 불러오지 못했어요.') })
    return () => {
      controller.abort()
      active.current?.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const id = profile.intake_state.active_question_id
    setQuestion(null)
    if (id) {
      void request<IntakeQuestion>(`/questions/${encodeURIComponent(id)}`, { signal: controller.signal })
        .then((value) => {
          if (!controller.signal.aborted && value.profile_id === latest.current.id && value.base_revision === latest.current.revision && value.status === 'ready') setQuestion(value)
        })
        .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '질문을 불러오지 못했어요.') })
    }
    return () => controller.abort()
  }, [profile.id, profile.revision, profile.intake_state.active_question_id])

  useEffect(() => {
    const controller = new AbortController()
    const id = profile.latest_interpretation_id
    setInterpretation(null)
    setDecisions({})
    if (id) {
      void request<Interpretation>(`/interpretations/${encodeURIComponent(id)}`, { signal: controller.signal })
        .then((value) => {
          if (!controller.signal.aborted && value.profile_id === latest.current.id && value.base_revision === latest.current.revision && value.status !== 'applied') {
            setInterpretation(value)
            setDecisions({})
          }
        })
        .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '입력 해석을 불러오지 못했어요.') })
    }
    return () => controller.abort()
  }, [profile.id, profile.latest_interpretation_id])

  async function generate(signal: AbortSignal) {
    const response = await mutate<JobAccepted | IntakeState>(`${path}/questions`, { expected_revision: latest.current.revision }, signal)
    if ('job_id' in response) await finishJob(response.job_id, signal, response.poll_after_ms)
    else await refresh(signal)
  }

  async function answer(optionId: string, signal: AbortSignal) {
    if (!question || question.base_revision !== latest.current.revision) throw new Error('최신 질문을 다시 확인해 주세요.')
    const next = await mutate<Profile>(`${path}/questions/${encodeURIComponent(question.id)}/answer`, { expected_revision: question.base_revision, selected_option_id: optionId }, signal)
    if (signal.aborted) return
    applyProfile(next)
    setInterpretation(null)
    if (next.intake_state.next_action === 'generate_question') await generate(signal)
  }

  async function interpret(signal: AbortSignal) {
    const accepted = await mutate<JobAccepted>(`${path}/interpretations`, {
      expected_revision: latest.current.revision,
      text,
      ready_document_ids: latest.current.documents.filter((item) => item.status === 'ready').map((item) => item.id),
      ...(question ? { question_id: question.id } : {}),
    }, signal)
    await finishJob(accepted.job_id, signal, accepted.poll_after_ms)
  }

  async function acceptInterpretation(signal: AbortSignal) {
    if (!interpretation || interpretation.base_revision !== latest.current.revision) throw new Error('입력이 변경됐어요. 해석을 다시 요청해 주세요.')
    const next = await mutate<Profile>(`${path}/interpretations/${encodeURIComponent(interpretation.id)}/apply`, {
      expected_revision: interpretation.base_revision,
      accepted_proposal_ids: interpretation.proposals.filter((item) => decisions[item.id] === 'accept').map((item) => item.id),
      rejected_proposal_ids: interpretation.proposals.filter((item) => decisions[item.id] === 'reject').map((item) => item.id),
    }, signal)
    if (!signal.aborted) {
      applyProfile(next)
      setInterpretation(null)
      setText('')
    }
  }

  async function addDocument(source: 'files' | 'links' | 'texts', signal: AbortSignal) {
    if (!options) throw new Error('자료 등록 조건을 먼저 불러와야 해요.')
    if (latest.current.documents.length >= options.max_profile_documents) throw new Error('등록 가능한 자료 수를 초과했어요.')
    let body: unknown = { expected_revision: latest.current.revision, kind, ...(source === 'links' ? { url: documentUrl } : { text: documentText }) }
    if (source === 'files') {
      if (!file) throw new Error('파일을 선택해 주세요.')
      const extension = file.name.split('.').at(-1)?.toLowerCase()
      if (!extension || !options.allowed_file_extensions.some((value) => value.replace(/^\./, '').toLowerCase() === extension)) throw new Error('지원하지 않는 파일 형식이에요.')
      if (file.size > options.max_upload_bytes) throw new Error('파일이 업로드 크기 제한을 초과했어요.')
      const form = new FormData()
      form.set('expected_revision', String(latest.current.revision))
      form.set('kind', kind)
      form.set('file', file)
      body = form
    }
    if (source === 'links' && !/^https?:\/\//i.test(documentUrl)) throw new Error('http 또는 https 주소를 입력해 주세요.')
    const accepted = await request<DocumentAccepted>(`${path}/documents/${source}`, { method: 'POST', body, signal })
    if (signal.aborted) return
    setDocumentText('')
    setDocumentUrl('')
    setFile(null)
    setFileInputKey((value) => value + 1)
    await refresh(signal)
    if (accepted.document.job_id) await finishJob(accepted.document.job_id, signal)
  }

  async function editAnswer(slot: string, signal: AbortSignal) {
    const answers: Answers = { ...latest.current.answers }
    delete answers[slot as keyof Answers]
    const next = await request<Profile>(path, { method: 'PATCH', body: { expected_revision: latest.current.revision, answers }, signal })
    if (!signal.aborted) {
      applyProfile(next)
      setInterpretation(null)
      setNotice('해당 답변을 다시 선택할 수 있도록 저장했어요. 다음 질문을 요청해 주세요.')
    }
  }

  function editReview(id: string, edit: ReviewEdit) {
    if (reviewRevision === null) setReviewRevision(profile.revision)
    setReviewEdits((value) => ({ ...value, [id]: edit }))
  }

  async function saveReview(signal: AbortSignal) {
    if (reviewRevision !== null && reviewRevision !== latest.current.revision) throw new Error('자료가 변경됐어요. 편집 취소 후 최신 내용을 확인하고 다시 수정해 주세요.')
    const items = latest.current.reviewed_items.map((item) => ({ id: item.id, label: reviewEdits[item.id]?.label ?? item.label, state: reviewEdits[item.id]?.state ?? item.state }))
    const next = await request<Profile>(`${path}/review-items`, { method: 'PATCH', body: { expected_revision: reviewRevision ?? latest.current.revision, items }, signal })
    if (!signal.aborted) {
      applyProfile(next)
      setReviewEdits({})
      setReviewRevision(null)
    }
  }

  const canSubmit = profile.intake_state.next_action === 'review' && !!profile.review_token && profile.unresolved_conflicts.length === 0 && profile.documents.every((item) => item.status === 'ready') && reviewRevision === null
  const showDocuments = ['collect_documents', 'review_sources', 'resolve_conflict', 'review'].includes(profile.intake_state.next_action) || profile.documents.length > 0
  const interpretationReady = interpretation?.profile_id === profile.id && interpretation.status === 'ready' && interpretation.base_revision === profile.revision

  function answerValue(key: keyof Answers, value: Answers[keyof Answers]): string {
    if (value === null || value === undefined) return '미입력'
    if (typeof value === 'boolean') return value ? '확인했어요' : '확인하지 않았어요'
    if (typeof value === 'number') return value.toLocaleString('ko-KR')
    if (typeof value === 'object') {
      const labels: Record<string, string> = { age: '나이', employment_status: '현재 상태', education: '학력', residence_region: '거주 지역' }
      return Object.entries(value).map(([field, entry]) => `${labels[field] ?? field}: ${entry === null ? '미입력' : field === 'employment_status' ? valueLabels[String(entry)] ?? String(entry) : String(entry)}`).join(' · ')
    }
    const source = key === 'field_code' ? options?.fields : key === 'entry_intent' ? options?.entry_intents : undefined
    if (source) return source.find((item) => item.value === value)?.label ?? value
    return ['experience', 'previous_gap', 'learning_goal', 'availability', 'delivery_mode', 'budget_status', 'start_timing', 'document_decision'].includes(key) ? valueLabels[value] ?? value : value
  }

  return (
    <div className="stack">
      <section className="panel stack" aria-label="학습 입력">
        <div className="section-heading"><div><p className="eyebrow">나에게 맞는 배움</p><h1>한 가지씩, 나의 다음을 찾아요</h1></div><button disabled={busy} onClick={() => void perform(async (signal) => { await refresh(signal) })}>최신 입력 확인</button></div>
        <ol className="progress-list" aria-label="입력 진행 상황">
          {sections.map(([id, name]) => <li key={id} aria-current={profile.intake_state.section === id ? 'step' : undefined}>{name}{profile.completed_sections.includes(id) ? ' · 완료' : ''}</li>)}
        </ol>
        {error && <p role="alert" className="error">{error}</p>}
        {!options && <button disabled={busy} onClick={() => void perform(async (signal) => { const value = await request<OnboardingOptions>('/onboarding/options', { signal }); if (!signal.aborted) setOptions(value) })}>입력 옵션 다시 불러오기</button>}
        {notice && <p role="status">{notice}</p>}
        {job && <p role="status">{phases[job.phase]}{job.error ? ` · ${job.error.message}` : ''}</p>}
        {busy && <button onClick={() => { active.current?.abort(); active.current = null; setBusy(false); setNotice('화면의 대기를 중지했어요. 서버 작업은 계속될 수 있으니 새 요청 전에 상태를 확인해 주세요.') }}>대기 중지</button>}
        {pendingJob && !busy && <button onClick={() => void perform((signal) => finishJob(pendingJob, signal))}>진행 중이던 작업 확인</button>}
        {question && question.base_revision === profile.revision ? (
          <div className="stack">
            {question.origin === 'fallback' && <p className="muted">서버가 제공한 기본 질문입니다.</p>}
            <p>{question.acknowledgement}</p><h2>{question.question}</h2><p className="muted">{question.helper_text}</p>
            <div className="option-grid">{question.options.map((option) => <button className="option-card" key={option.id} disabled={busy} onClick={() => void perform((signal) => answer(option.id, signal))}><strong>{option.label}</strong><span>{option.description}</span></button>)}</div>
          </div>
        ) : profile.intake_state.next_action === 'generate_question' && <button className="primary" disabled={busy || !options?.question_generation_enabled} onClick={() => void perform(generate)}>다음 질문 받기</button>}
        {profile.unresolved_conflicts.length > 0 && <div role="status"><h2>다시 확인할 입력이 있어요</h2><ul>{profile.unresolved_conflicts.map((item) => <li key={item}>{item}</li>)}</ul></div>}
        {profile.selection_summaries.length > 0 && <details><summary>지금까지 선택한 내용</summary><ul className="stack">{profile.selection_summaries.map((item) => <li key={item.question_id}>{item.label} <button disabled={busy} onClick={() => void perform((signal) => editAnswer(item.target_slot, signal))}>다시 선택</button></li>)}</ul></details>}
        {options?.interpretation_enabled && (question?.allow_custom_text || showDocuments) && <details><summary>직접 알려줄게요</summary><div className="stack"><label>내 경험이나 원하는 배움<textarea value={text} maxLength={6000} onChange={(event) => setText(event.target.value)} /></label><button disabled={busy || (!text.trim() && !profile.documents.some((item) => item.status === 'ready'))} onClick={() => void perform(interpret)}>입력과 준비된 자료 해석하기</button></div></details>}
        {interpretation && <section className="stack" aria-label="입력 해석 확인"><h2>내 뜻과 맞는지 확인해 주세요</h2><p>{interpretation.assistant_text}</p>{!interpretationReady && <p role="alert">이 해석은 현재 입력에 적용할 수 없어요. 다시 해석해 주세요.</p>}{interpretation.proposals.map((proposal) => <fieldset key={proposal.id} disabled={busy || !interpretationReady}><legend>{proposal.label}{proposal.status === 'conflict' ? ' · 기존 답변과 상충' : ''}</legend><blockquote>{proposal.quote}</blockquote><p>{proposal.value.kind === 'text' ? proposal.value.text : proposal.value.integer}</p><label><input type="radio" name={`proposal-${proposal.id}`} checked={decisions[proposal.id] === 'accept'} onChange={() => setDecisions((value) => ({ ...value, [proposal.id]: 'accept' }))} />맞아요</label><label><input type="radio" name={`proposal-${proposal.id}`} checked={decisions[proposal.id] === 'reject'} onChange={() => setDecisions((value) => ({ ...value, [proposal.id]: 'reject' }))} />반영하지 않을게요</label></fieldset>)}<button disabled={busy || !interpretationReady || interpretation.proposals.some((item) => !decisions[item.id])} onClick={() => void perform(acceptInterpretation)}>확인한 제안만 반영</button></section>}
      </section>
      {showDocuments && <section className="panel stack" aria-label="참고 자료"><h2>내 경험을 알려주는 자료</h2><p>자료에서 읽은 내용은 직접 확인한 뒤에만 입력에 반영돼요.</p><fieldset disabled={busy || !options}><legend>자료 추가</legend><label>자료 종류<select value={kind} onChange={(event) => setKind(event.target.value as DocumentKind)}><option value="resume">이력서</option><option value="job_description">채용 공고</option><option value="portfolio">포트폴리오</option><option value="other">기타</option></select></label><p className="muted">{options?.upload_limit_note}</p><label>파일<input key={fileInputKey} type="file" accept={options?.allowed_file_extensions.map((value) => value.startsWith('.') ? value : `.${value}`).join(',')} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><button disabled={!file} onClick={() => void perform((signal) => addDocument('files', signal))}>파일 등록</button><label>공개 자료 주소<input type="url" value={documentUrl} onChange={(event) => setDocumentUrl(event.target.value)} /></label><button disabled={!documentUrl.trim()} onClick={() => void perform((signal) => addDocument('links', signal))}>링크 등록</button><label>자료 내용<textarea value={documentText} onChange={(event) => setDocumentText(event.target.value)} /></label><button disabled={!documentText.trim()} onClick={() => void perform((signal) => addDocument('texts', signal))}>텍스트 등록</button></fieldset>
        {profile.documents.map((document) => <article className="document-card stack" key={document.id}><h3>{document.display_name}</h3><p>{({ queued: '대기 중', processing: '처리 중', ready: '확인 가능', failed: '처리 실패' })[document.status]}</p>{document.error_code && <p role="alert">자료를 읽지 못했어요 ({document.error_code}). 파일이나 텍스트로 다시 등록할 수 있어요.</p>}{document.extracted_preview && <blockquote>{document.extracted_preview}</blockquote>}<div className="buttons">{document.job_id && ['queued', 'processing'].includes(document.status) && <button disabled={busy} onClick={() => void perform((signal) => finishJob(document.job_id!, signal))}>처리 상태 확인</button>}{document.status === 'failed' && <button disabled={busy} onClick={() => void perform(async (signal) => { const accepted = await request<DocumentAccepted>(`/documents/${encodeURIComponent(document.id)}/retry`, { method: 'POST', body: { expected_revision: latest.current.revision }, signal }); await refresh(signal); if (accepted.document.job_id) await finishJob(accepted.document.job_id, signal) })}>다시 처리</button>}<button disabled={busy} onClick={() => { if (window.confirm('이 자료와 추출 내용을 삭제할까요?')) void perform(async (signal) => { await request<void>(`/documents/${encodeURIComponent(document.id)}?expected_revision=${latest.current.revision}`, { method: 'DELETE', signal }); await refresh(signal) }) }}>자료 삭제</button></div></article>)}
        {profile.documents.length === 0 && <button disabled={busy} onClick={() => void perform(async (signal) => { const next = await request<Profile>(path, { method: 'PATCH', body: { expected_revision: latest.current.revision, answers: { ...latest.current.answers, document_decision: 'none' } }, signal }); if (!signal.aborted) applyProfile(next) })}>자료 없이 진행</button>}
        {profile.reviewed_items.length > 0 && <div className="stack"><h3>추출한 내용을 확인해 주세요</h3>{profile.reviewed_items.map((item) => { const edit = reviewEdits[item.id] ?? { label: item.label, state: item.state }; return <fieldset key={item.id} disabled={busy}><legend>{item.label}</legend>{item.evidence_quote && <blockquote>{item.evidence_quote}</blockquote>}<label>표시 내용<input value={edit.label} onChange={(event) => editReview(item.id, { ...edit, label: event.target.value })} /></label><label>나의 상태<select value={edit.state} onChange={(event) => editReview(item.id, { ...edit, state: event.target.value as ReviewedItem['state'] })}><option value="have">경험이 있어요</option><option value="want_to_learn">배우고 싶어요</option><option value="unknown">아직 모르겠어요</option><option value="excluded">반영하지 않을게요</option></select></label></fieldset> })}<div className="buttons"><button disabled={busy} onClick={() => void perform(saveReview)}>확인 내용 저장</button><button disabled={busy || reviewRevision === null} onClick={() => { setReviewEdits({}); setReviewRevision(null) }}>편집 취소</button></div></div>}
      </section>}
      {profile.intake_state.next_action === 'review' && <section className="panel stack"><h2>입력한 내용을 확인해 주세요</h2><ul>{profile.selection_summaries.map((item) => <li key={item.question_id}>{item.label}</li>)}</ul><dl>{Object.entries(profile.answers).map(([key, value]) => <div key={key}><dt>{answerLabels[key as keyof Answers]}</dt><dd>{answerValue(key as keyof Answers, value)}</dd></div>)}</dl><p>확인한 입력과 자료로 교육 추천을 시작합니다.</p><button className="primary" disabled={busy || !canSubmit} onClick={() => void perform(async (signal) => { const accepted = await mutate<JobAccepted>(`${path}/submit`, { expected_revision: latest.current.revision, review_token: latest.current.review_token }, signal); await finishJob(accepted.job_id, signal, accepted.poll_after_ms) })}>확인하고 교육 추천 받기</button>{!canSubmit && <p>확인이 필요한 입력이나 자료를 먼저 처리해 주세요.</p>}</section>}
    </div>
  )
}
