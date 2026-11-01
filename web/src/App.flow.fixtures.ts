import { expect, vi } from 'vitest'
import { courseDetailFixture } from './Courses.fixtures'
import type {
  AuthSession,
  CourseDetail,
  CourseSummary,
  Document,
  IntakeQuestion,
  IntakeSlot,
  Interpretation,
  Job,
  OnboardingOptions,
  Profile,
  RecommendationRun,
  SavedCoursePage,
} from './types'

// Test-only scripted HTTP producer. This is not an API implementation, an intake
// policy oracle, real authentication, extraction, catalog storage or inference.
export const flowId = (n: number) =>
  `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`
export const flowIds = {
  user: flowId(1),
  profile: flowId(2),
  run: flowId(3),
  catalog: flowId(4),
  interpretation: flowId(30),
  proposal: flowId(31),
}
export const choices: {
  slot: IntakeSlot
  value: string
  label: string
  section: Profile['intake_state']['section']
}[] = [
  {
    slot: 'entry_intent',
    value: 'explore',
    label: '방향을 찾아보고 싶어요',
    section: 'direction',
  },
  {
    slot: 'field_code',
    value: 'software_cloud',
    label: '소프트웨어를 배우고 싶어요',
    section: 'direction',
  },
  {
    slot: 'experience',
    value: 'beginner',
    label: '처음 시작해요',
    section: 'experience',
  },
  {
    slot: 'learning_goal',
    value: 'foundation',
    label: '기초부터 배우고 싶어요',
    section: 'goal',
  },
  {
    slot: 'availability',
    value: 'unknown',
    label: '참여 시간은 아직 미정이에요',
    section: 'conditions',
  },
  {
    slot: 'delivery_mode',
    value: 'online',
    label: '온라인으로 배우고 싶어요',
    section: 'conditions',
  },
  {
    slot: 'budget_status',
    value: 'unknown',
    label: '예산은 아직 미정이에요',
    section: 'conditions',
  },
  {
    slot: 'start_timing',
    value: 'browse',
    label: '먼저 알아보고 싶어요',
    section: 'conditions',
  },
]
export const flowCourses: CourseDetail[] = [5, 6].map((n, index) => ({
  ...courseDetailFixture,
  id: flowId(n),
  name: `흐름 검증용 합성 교육 ${index + 1}`,
  source_system: 'work24',
  course_type: 'vocational_training',
  field_code: 'software_cloud',
  description: `합성 교육 ${index + 1}의 상세 설명`,
  detail_url: `https://example.org/synthetic-course/${n}`,
  evidence: courseDetailFixture.evidence.map((item) => ({
    ...item,
    id: flowId(70 + n),
    source_system: 'work24',
    quote: `합성 교육 ${index + 1}의 원문 근거`,
  })),
}))
const options: OnboardingOptions = {
  schema_version: '0.8',
  fields: [],
  entry_intents: [],
  allowed_file_extensions: ['txt'],
  max_upload_bytes: 4096,
  max_profile_documents: 5,
  upload_limit_note: '검증 전용 합성 자료 옵션',
  branch_rules_version: '1',
  intake_policy_version: '1',
  allowed_catalog_sources: ['work24'],
  interpretation_enabled: true,
  question_generation_enabled: true,
  question_schema_version: '1',
}
const session: AuthSession = {
  user: {
    id: flowIds.user,
    display_name: '흐름 검증용 합성 계정',
    email: 'synthetic@example.org',
    avatar_url: null,
  },
  csrf_token: 'synthetic-flow-csrf',
  expires_at: '2099-01-01T00:00:00Z',
}
const timestamp = '2026-11-01T00:00:00Z'
function summary(course: CourseDetail): CourseSummary {
  return {
    id: course.id,
    name: course.name,
    provider_name: course.provider_name,
    field_code: course.field_code,
    source_system: course.source_system,
    course_type: course.course_type,
    evidence_level: course.evidence_level,
    detail_url: course.detail_url,
    is_demo: course.is_demo,
  }
}
type Faults = {
  answerConflict?: boolean
  submitFailure?: boolean
  resultFailure?: boolean
  saveUnauthorized?: boolean
}
export function createFlowFixture(faults: Faults = {}) {
  let profile: Profile = {
    id: flowIds.profile,
    revision: 0,
    status: 'draft',
    answers: {},
    reviewed_items: [],
    documents: [],
    completed_sections: [],
    review_token: null,
    updated_at: timestamp,
    slot_origins: {},
    unresolved_conflicts: [],
    latest_interpretation_id: null,
    selection_summaries: [],
    intake_state: {
      section: 'direction',
      next_action: 'generate_question',
      eligible_slots: ['entry_intent'],
      missing_slots: choices.map((x) => x.slot),
      completed_sections: [],
      active_question_id: null,
    },
  }
  let step = 0
  let generation = 0
  let submissions = 0
  let run: RecommendationRun | null = null
  let authenticated = true
  let interpretation: Interpretation | null = null
  const questions = new Map<string, IntakeQuestion>()
  const jobs = new Map<string, Job>()
  const saved = new Set<string>()
  const remainingFaults = { ...faults }
  const base = `/api/profiles/${profile.id}`
  function action(next: Profile['intake_state']['next_action']) {
    const completed = (
      ['direction', 'experience', 'goal', 'conditions'] as const
    ).filter((_, i) => step >= [2, 3, 4, 8][i])
    profile = {
      ...profile,
      completed_sections: completed,
      intake_state: {
        section:
          next === 'collect_documents' || next === 'review_sources'
            ? 'experience'
            : (choices[step]?.section ?? 'review'),
        next_action: next,
        eligible_slots:
          next === 'generate_question' ? [choices[step].slot] : [],
        missing_slots: choices.slice(step).map((x) => x.slot),
        completed_sections: completed,
        active_question_id: null,
      },
    }
    if (next === 'review') profile.review_token = 'synthetic-flow-review-token'
  }
  function job(id: string, kind: Job['kind'], result: Job['result']): Job {
    return {
      id,
      kind,
      status: 'succeeded',
      phase: 'completed',
      profile_id: profile.id,
      profile_revision: profile.revision,
      result,
      error: null,
      created_at: timestamp,
      finished_at: timestamp,
      completed_stages: [],
    }
  }
  function generatedRun(): RecommendationRun {
    return {
      id: flowIds.run,
      profile_id: profile.id,
      profile_revision: profile.revision,
      catalog_version_id: flowIds.catalog,
      result_state: 'unknown_conditions',
      explanation_mode: 'llm',
      // Contract discriminators exercise the existing consumer guards. They are
      // synthetic values and provide no evidence of a real model/catalog run.
      runtime_mode: 'real_model',
      model_id: 'synthetic-flow-model',
      model_outcome: 'succeeded',
      profile_snapshot: structuredClone(profile.answers),
      items: flowCourses.map((course, i) => ({
        course: summary(course),
        offering: course.offerings[0],
        rank: i + 1,
        match_state: 'unknown_conditions',
        reasons: [
          {
            text: '합성 계약에 포함된 추천 이유',
            answer_keys: ['learning_goal'],
            evidence_ids: [course.evidence[0].id],
          },
        ],
        evidence: course.evidence,
        missing_fields: ['schedule'],
        rank_breakdown: {
          goal_basis: 'content_supported',
          score_lower: 0.5,
          score_upper: 0.8,
          coverage: 0.5,
          axes: [],
          ranking_config_version: 'synthetic-ranking',
          constraint_checks: [
            {
              field: 'mode',
              status: 'pass',
              reason_code: 'SYNTHETIC_MODE',
              evidence_ids: [],
            },
            {
              field: 'schedule',
              status: 'unknown',
              reason_code: 'SYNTHETIC_UNKNOWN',
              evidence_ids: [],
            },
          ],
        },
      })),
      notices: ['합성 응답 기반 App 연결 검증'],
      excluded_counts: {},
      created_at: timestamp,
      pipeline_version: 'synthetic-pipeline',
      ranking_config_version: 'synthetic-ranking',
      retrieval_config_version: 'synthetic-retrieval',
      as_of: timestamp,
      needs: [],
      stages: [],
    }
  }
  const fetch = vi.fn(async (input: string, init: RequestInit) => {
    const url = new URL(input)
    const path = url.pathname
    const method = init.method || 'GET'
    const body =
      init.body && !(init.body instanceof FormData)
        ? (JSON.parse(init.body as string) as Record<string, unknown>)
        : {}
    if (method !== 'GET') {
      expect(init.credentials).toBe('include')
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe(
        session.csrf_token,
      )
    }
    if (path === '/api/auth/me')
      return authenticated
        ? Response.json(session)
        : new Response(null, { status: 401 })
    if (path === '/api/auth/logout') {
      authenticated = false
      return new Response(null, { status: 204 })
    }
    if (path === '/api/onboarding/options') return Response.json(options)
    if (path === '/api/profiles' && method === 'POST')
      return Response.json(profile, { status: 201 })
    if (path === base && method === 'GET') return Response.json(profile)
    if (path === `${base}/recommendation-job` && method === 'GET') {
      const current = [...jobs.values()]
        .filter(
          (item) =>
            item.kind === 'recommendation' &&
            item.profile_revision === profile.revision,
        )
        .sort(
          (a, b) =>
            b.created_at.localeCompare(a.created_at) ||
            b.id.localeCompare(a.id),
        )[0]
      return Response.json({
        profile_id: profile.id,
        revision: profile.revision,
        job: current ?? null,
      })
    }
    if (path === `${base}/questions` && method === 'POST') {
      expect(profile.intake_state.next_action).toBe('generate_question')
      expect(body.expected_revision).toBe(profile.revision)
      const choice = choices[step]
      const questionId = flowId(100 + ++generation)
      const jobId = flowId(200 + generation)
      const question: IntakeQuestion = {
        id: questionId,
        profile_id: profile.id,
        base_revision: profile.revision,
        context_hash: `synthetic-context-${generation}`,
        status: 'ready',
        origin: 'llm',
        schema_version: '1',
        target_slot: choice.slot,
        acknowledgement: '합성 질문 응답',
        question: `흐름 질문 ${step + 1}`,
        helper_text: '직접 선택한 값만 반영하는 계약 검증입니다.',
        selection_mode: 'single',
        options: [
          {
            id: flowId(300 + generation),
            label: choice.label,
            description: '',
            kind: choice.value === 'unknown' ? 'unknown' : 'generated',
          },
          {
            id: flowId(400 + generation),
            label: '추가 합성 선택',
            description: '',
            kind: 'generated',
          },
        ],
        allow_custom_text: true,
        model_id: 'synthetic-flow-model',
        prompt_version: '1',
        policy_version: '1',
        created_at: timestamp,
      }
      questions.set(questionId, question)
      profile.intake_state.active_question_id = questionId
      jobs.set(
        jobId,
        job(jobId, 'intake_question', { question_id: questionId }),
      )
      return Response.json(
        { job_id: jobId, status: 'queued', poll_after_ms: 1000 },
        { status: 202 },
      )
    }
    if (path.startsWith('/api/questions/'))
      return Response.json(questions.get(path.split('/').at(-1)!))
    if (path.startsWith(`${base}/questions/`) && path.endsWith('/answer')) {
      const question = questions.get(path.split('/').at(-2)!)!
      expect(body.expected_revision).toBe(profile.revision)
      expect(body.selected_option_id).toBe(question.options[0].id)
      if (remainingFaults.answerConflict) {
        remainingFaults.answerConflict = false
        profile.revision++
        action('generate_question')
        return new Response(null, { status: 409 })
      }
      const choice = choices[step++]
      profile.answers = { ...profile.answers, [choice.slot]: choice.value }
      profile.revision++
      profile.selection_summaries.push({
        question_id: question.id,
        selected_option_id: question.options[0].id,
        target_slot: choice.slot,
        label: choice.label,
        revision: profile.revision,
      })
      action(
        step === 3
          ? 'collect_documents'
          : step === choices.length
            ? 'review'
            : 'generate_question',
      )
      return Response.json(profile)
    }
    if (path === base && method === 'PATCH') {
      expect(body.expected_revision).toBe(profile.revision)
      expect(body.answers).toEqual({
        ...profile.answers,
        document_decision: 'none',
      })
      profile.answers.document_decision = 'none'
      profile.revision++
      action('generate_question')
      return Response.json(profile)
    }
    if (
      path === `${base}/documents/files` ||
      path === `${base}/documents/links`
    ) {
      const file = init.body instanceof FormData
      expect(
        file
          ? (init.body as FormData).get('expected_revision')
          : body.expected_revision,
      ).toBe(file ? String(profile.revision) : profile.revision)
      const doc: Document = {
        id: flowId(file ? 20 : 21),
        profile_id: profile.id,
        kind: file ? 'resume' : 'job_description',
        source_type: file ? 'file' : 'url',
        display_name: file ? '합성 이력서' : '합성 공개 JD',
        source_url: file ? null : String(body.url),
        mime_type: file ? 'text/plain' : 'text/html',
        size_bytes: null,
        status: 'queued',
        extracted_preview: null,
        job_id: flowId(file ? 40 : 41),
        error_code: null,
        content_sha256: null,
        created_at: timestamp,
      }
      profile.documents.push(doc)
      profile.answers.document_decision = 'provided'
      profile.revision++
      action('review_sources')
      jobs.set(
        doc.job_id!,
        job(doc.job_id!, 'document_processing', { document_id: doc.id }),
      )
      return Response.json(
        { document: doc, profile_revision: profile.revision },
        { status: 202 },
      )
    }
    if (path === `${base}/interpretations`) {
      expect(body.expected_revision).toBe(profile.revision)
      expect(body.text).toBe('주말에만 수강할 수 있어요.')
      expect(body.ready_document_ids).toEqual([flowId(20), flowId(21)])
      interpretation = {
        id: flowIds.interpretation,
        profile_id: profile.id,
        base_revision: profile.revision,
        input_hash: 'synthetic-flow-hash',
        status: 'ready',
        assistant_text: '주말 제한을 확인해 주세요.',
        proposals: [
          {
            id: flowIds.proposal,
            slot: 'constraints_text',
            value: { kind: 'text', text: String(body.text) },
            label: '주말만 참여',
            source_type: 'user_text',
            document_id: null,
            chunk_id: null,
            quote: String(body.text),
            status: 'proposed',
            conflicts_with: null,
          },
        ],
        model_id: 'synthetic-flow-model',
        prompt_version: '1',
        created_at: timestamp,
        suggested_slots: [],
      }
      profile.latest_interpretation_id = interpretation.id
      jobs.set(
        flowId(42),
        job(flowId(42), 'intake_interpretation', {
          interpretation_id: interpretation.id,
        }),
      )
      return Response.json(
        { job_id: flowId(42), status: 'queued', poll_after_ms: 1000 },
        { status: 202 },
      )
    }
    if (path === `/api/interpretations/${flowIds.interpretation}`)
      return Response.json(interpretation)
    if (path === `${base}/interpretations/${flowIds.interpretation}/apply`) {
      expect(body).toEqual({
        expected_revision: profile.revision,
        accepted_proposal_ids: [flowIds.proposal],
        rejected_proposal_ids: [],
      })
      profile.answers.constraints_text = '주말에만 수강할 수 있어요.'
      profile.revision++
      interpretation!.status = 'applied'
      return Response.json(profile)
    }
    if (path === `${base}/review-items`) {
      expect(body.expected_revision).toBe(profile.revision)
      const edits = body.items as {
        id: string
        label: string
        state: 'have' | 'want_to_learn'
      }[]
      expect(edits.map((item) => item.id)).toEqual([flowId(50), flowId(51)])
      edits.forEach((item) =>
        expect(Object.keys(item).sort()).toEqual(['id', 'label', 'state']),
      )
      profile.reviewed_items = profile.reviewed_items.map((item) => ({
        ...item,
        ...edits.find((edit) => edit.id === item.id)!,
      }))
      profile.revision++
      action('generate_question')
      return Response.json(profile)
    }
    if (path === `${base}/submit`) {
      expect(profile.intake_state.next_action).toBe('review')
      expect(profile.documents.every((doc) => doc.status === 'ready')).toBe(
        true,
      )
      expect(body).toEqual({
        expected_revision: profile.revision,
        review_token: profile.review_token,
      })
      const id = flowId(60 + submissions++)
      const result = job(id, 'recommendation', {
        recommendation_run_id: flowIds.run,
      })
      if (remainingFaults.submitFailure) {
        remainingFaults.submitFailure = false
        result.status = 'failed'
        result.result = null
        result.phase = 'failed'
        result.error = {
          code: 'synthetic_model_failure',
          message: '합성 추천 작업 실패',
          request_id: 'synthetic-flow-request',
        }
      }
      jobs.set(id, result)
      return Response.json(
        { job_id: id, status: 'queued', poll_after_ms: 1000 },
        { status: 202 },
      )
    }
    if (path.startsWith('/api/jobs/')) {
      const result = jobs.get(path.split('/').at(-1)!)!
      if (result.kind === 'document_processing') {
        const doc = profile.documents.find(
          (item) => item.id === result.result!.document_id,
        )!
        doc.status = 'ready'
        doc.extracted_preview =
          doc.source_type === 'file'
            ? '합성 이력서 원문 인용'
            : '합성 JD 원문 인용'
        if (!profile.reviewed_items.some((item) => item.document_id === doc.id))
          profile.reviewed_items.push({
            id: flowId(doc.source_type === 'file' ? 50 : 51),
            label:
              doc.source_type === 'file'
                ? '파일에서 추출한 합성 경험'
                : 'JD에서 추출한 합성 학습',
            state: 'unknown',
            document_id: doc.id,
            chunk_id: null,
            evidence_quote: doc.extracted_preview,
            origin: 'document',
          })
      }
      if (result.kind === 'recommendation' && result.status === 'succeeded') {
        run = generatedRun()
        profile.status = 'submitted'
        profile.review_token = null
      }
      return Response.json(result)
    }
    if (path === `/api/recommendations/${flowIds.run}`) {
      expect(run).not.toBeNull()
      if (remainingFaults.resultFailure) {
        remainingFaults.resultFailure = false
        return new Response(null, { status: 503 })
      }
      return Response.json(run)
    }
    if (path === '/api/courses/compare') {
      const ids = url.searchParams.get('ids')!.split(',')
      return Response.json({
        items: ids.map((id) => flowCourses.find((course) => course.id === id)!),
      })
    }
    if (path.startsWith('/api/courses/'))
      return Response.json(
        flowCourses.find((course) => course.id === path.split('/').at(-1)),
      )
    if (path === '/api/me/saved-courses')
      return Response.json({
        next_cursor: null,
        catalog_version_id: flowIds.catalog,
        items: flowCourses
          .filter((course) => saved.has(course.id))
          .map((course) => ({
            ...summary(course),
            saved_catalog_version_id: flowIds.catalog,
            catalog_status: 'current',
          })),
      } satisfies SavedCoursePage)
    if (path.startsWith('/api/me/saved-courses/')) {
      if (remainingFaults.saveUnauthorized) {
        authenticated = false
        return new Response(null, { status: 401 })
      }
      const id = path.split('/').at(-1)!
      if (method === 'PUT') saved.add(id)
      else {
        expect(method).toBe('DELETE')
        saved.delete(id)
      }
      return new Response(null, { status: 204 })
    }
    throw new Error(`Unexpected scripted request: ${method} ${path}`)
  })
  return {
    fetch,
    profile: () => structuredClone(profile),
    run: () => structuredClone(run),
  }
}
