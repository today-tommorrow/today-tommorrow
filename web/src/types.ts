// v0.8: choice-first, local Gemma question contracts.
export type Error = {
  code: string
  message: string
  request_id: string
  details?: { [key: string]: unknown }
}

export type EntryIntent = 'goal_known' | 'explore' | 'advance'

export type FieldCode =
  | 'ai_data'
  | 'software_cloud'
  | 'security_network'
  | 'robotics_manufacturing'
  | 'semiconductor'
  | 'digital_content'
  | 'other'
  | 'undecided'

export type Evidence = {
  id: string
  field_path: string
  quote: string | null
  source_url: string | null
  source_system: 'kmooc' | 'work24' | 'ncs'
  checked_at: string | null
  verification_state: 'observed' | 'verified' | 'unknown' | 'conflicting'
}

export type Schedule = {
  days: Array<'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'>
  start_time: string | null
  end_time: string | null
  timezone: 'Asia/Seoul'
  is_asynchronous: boolean | null
}

export type Offering = {
  id: string
  external_offering_id: string | null
  cohort: string | null
  start_date: string | null
  end_date: string | null
  enrollment_start: string | null
  enrollment_end: string | null
  delivery_mode: 'online' | 'offline' | 'hybrid' | 'unknown'
  region: string | null
  schedule: Schedule | null
  training_hours: number | null
  tuition_krw: number | null
  listed_self_pay_krw: number | null
  eligibility_text: string | null
  recruitment_status: 'open' | 'closed' | 'unknown'
  checked_at: string | null
}

export type CourseSummary = {
  id: string
  name: string
  provider_name: string | null
  field_code: FieldCode | null
  source_system: 'kmooc' | 'work24'
  course_type: 'online_course' | 'vocational_training' | 'kdt' | 'unknown'
  evidence_level: 'listing_only' | 'detail_verified'
  detail_url: string | null
  is_demo: boolean
}

export type CourseDetail = {
  id: string
  name: string
  provider_name: string | null
  field_code: FieldCode | null
  source_system: 'kmooc' | 'work24'
  course_type: 'online_course' | 'vocational_training' | 'kdt' | 'unknown'
  evidence_level: 'listing_only' | 'detail_verified'
  detail_url: string | null
  is_demo: boolean
  description: string | null
  prerequisites: string | null
  learning_outcomes: Array<string>
  offerings: Array<Offering>
  evidence: Array<Evidence>
  missing_fields: Array<string>
}

export type CoursePage = {
  items: Array<CourseSummary>
  next_cursor: string | null
  catalog_version_id: string
}

export type SavedCourse = CourseSummary & {
  saved_catalog_version_id: string
  catalog_status: 'current' | 'changed' | 'unavailable'
}

export type SavedCoursePage = {
  items: SavedCourse[]
  next_cursor: string | null
  catalog_version_id: string
}

export type Reason = {
  text: string
  answer_keys: Array<string>
  evidence_ids: Array<string>
}

export type RecommendationItem = {
  course: CourseSummary
  offering: Offering | null
  rank: number
  match_state: 'verified_conditions' | 'unknown_conditions'
  reasons: Array<Reason>
  evidence: Array<Evidence>
  missing_fields: Array<string>
  rank_breakdown: RankBreakdown
}

export type RecommendationRun = {
  id: string
  profile_revision: number
  catalog_version_id: string
  result_state: 'matches' | 'unknown_conditions' | 'empty' | 'unsupported_field'
  explanation_mode: 'llm' | 'none'
  items: Array<RecommendationItem>
  notices: Array<string>
  excluded_counts: { [key: string]: number }
  created_at: string
  profile_id: string
  profile_snapshot: Answers
  model_id: string
  runtime_mode: 'real_model' | 'sample'
  model_outcome: 'succeeded' | 'not_needed'
  pipeline_version: string
  ranking_config_version: string
  retrieval_config_version: string
  as_of: string
  needs: Array<Need>
  stages: Array<PipelineStage>
}

export type CatalogMeta = {
  catalog_version_id: string
  as_of: string
  is_snapshot: boolean
  course_rows: number
  source_counts: { [key: string]: number }
  coverage_notes: Array<string>
  sources?: Array<'kmooc' | 'work24' | 'ncs'>
}

export type Option = { value: string; label: string; description: string }

export type Health = { status: 'ok' }

export type User = {
  id: string
  display_name: string | null
  email: string
  avatar_url: string | null
}

export type AuthSession = { user: User; csrf_token: string; expires_at: string }

export type Answers = {
  entry_intent?: EntryIntent
  field_code?: FieldCode
  custom_field?: string | null
  interest_activity?: string | null
  field_confirmed?: boolean
  experience?: 'beginner' | 'basic' | 'project' | 'work' | 'unknown'
  previous_gap?: 'application' | 'depth' | 'collaboration' | 'unknown' | null
  learning_goal?: 'foundation' | 'portfolio' | 'collaboration' | 'unknown'
  availability?: 'weekday_day' | 'evening_weekend' | 'flexible' | 'unknown'
  delivery_mode?: 'online' | 'offline' | 'hybrid' | 'any' | 'unknown'
  commute_region?: string | null
  budget_status?: 'amount' | 'no_limit' | 'unknown'
  budget_krw?: number | null
  start_timing?: 'soon' | 'one_to_three_months' | 'browse' | 'unknown'
  document_decision?: 'provided' | 'none'
  self_description?: string | null
  eligibility_context?: {
    age?: number | null
    employment_status?:
      'employed' | 'unemployed' | 'student' | 'self_employed' | 'unknown' | null
    education?: string | null
    residence_region?: string | null
  }
  goal_text?: string | null
  constraints_text?: string | null
}

export type ReviewedItem = {
  id: string
  label: string
  state: 'have' | 'want_to_learn' | 'unknown' | 'excluded'
  document_id: string | null
  chunk_id: string | null
  evidence_quote: string | null
  origin: 'document' | 'user'
}

export type ReviewedItemEdit = {
  id: string
  label: string
  state: 'have' | 'want_to_learn' | 'unknown' | 'excluded'
}

export type Profile = {
  id: string
  revision: number
  status: 'draft' | 'submitted'
  answers: Answers
  reviewed_items: Array<ReviewedItem>
  documents: Array<Document>
  completed_sections: Array<'direction' | 'experience' | 'goal' | 'conditions'>
  review_token: string | null
  updated_at: string
  slot_origins: {
    [key: string]:
      | 'direct'
      | 'accepted_interpretation'
      | 'accepted_document'
      | 'explicit_unknown'
      | 'selected_option'
  }
  unresolved_conflicts: Array<string>
  latest_interpretation_id: string | null
  intake_state: IntakeState
  selection_summaries: Array<{
    question_id: string
    selected_option_id: string
    target_slot: IntakeSlot
    label: string
    revision: number
  }>
}

export type ProfilePatch = { expected_revision: number; answers: Answers }

export type ReviewPatch = {
  expected_revision: number
  items: Array<ReviewedItemEdit>
}

export type DocumentKind = 'resume' | 'job_description' | 'portfolio' | 'other'

export type Document = {
  id: string
  profile_id: string
  kind: DocumentKind
  source_type: 'file' | 'url' | 'text'
  display_name: string
  source_url: string | null
  mime_type: string | null
  size_bytes: number | null
  status: 'queued' | 'processing' | 'ready' | 'failed'
  extracted_preview: string | null
  job_id: string | null
  error_code: string | null
  content_sha256: string | null
  created_at: string
}

export type DocumentAccepted = { document: Document; profile_revision: number }

export type DocumentFileInput = {
  expected_revision: number
  kind: DocumentKind
  file: Blob
}

export type DocumentLinkInput = {
  expected_revision: number
  kind: DocumentKind
  url: string
}

export type DocumentTextInput = {
  expected_revision: number
  kind: DocumentKind
  text: string
}

export type RevisionRequest = { expected_revision: number }

export type SubmitRequest = { expected_revision: number; review_token: string }

export type JobAccepted = {
  job_id: string
  status:
    'queued' | 'running' | 'succeeded' | 'failed' | 'superseded' | 'cancelled'
  poll_after_ms: number
}

export type Job = {
  id: string
  kind:
    | 'document_processing'
    | 'intake_interpretation'
    | 'recommendation'
    | 'intake_question'
  status:
    'queued' | 'running' | 'succeeded' | 'failed' | 'superseded' | 'cancelled'
  phase:
    | 'queued'
    | 'fetching'
    | 'extracting'
    | 'interpreting'
    | 'freezing'
    | 'profiling'
    | 'mapping_needs'
    | 'retrieving'
    | 'checking_constraints'
    | 'assessing'
    | 'ranking'
    | 'explaining'
    | 'validating'
    | 'persisting'
    | 'completed'
    | 'failed'
    | 'generating_question'
    | 'validating_question'
  profile_id: string
  result: {
    document_id?: string | null
    interpretation_id?: string | null
    recommendation_run_id?: string | null
    question_id?: string | null
  } | null
  error: Error | null
  created_at: string
  finished_at: string | null
  completed_stages: Array<string>
}

export type OnboardingOptions = {
  schema_version: string
  fields: Array<Option>
  entry_intents: Array<Option>
  allowed_file_extensions: Array<string>
  max_upload_bytes: number
  max_profile_documents: number
  upload_limit_note: string
  branch_rules_version: string
  intake_policy_version: string
  allowed_catalog_sources: Array<'kmooc' | 'work24' | 'ncs'>
  interpretation_enabled: boolean
  question_generation_enabled: boolean
  question_schema_version: string
}

export type IntakeSlot =
  | 'entry_intent'
  | 'field_code'
  | 'custom_field'
  | 'experience'
  | 'previous_gap'
  | 'learning_goal'
  | 'availability'
  | 'delivery_mode'
  | 'commute_region'
  | 'budget_status'
  | 'budget_krw'
  | 'start_timing'
  | 'document_decision'
  | 'goal_text'
  | 'constraints_text'
  | 'interest_activity'

export type ProposalValue =
  { kind: 'text'; text: string } | { kind: 'integer'; integer: number }

export type SlotProposal = {
  id: string
  slot: IntakeSlot
  value: ProposalValue
  label: string
  source_type: 'user_text' | 'document'
  document_id: string | null
  chunk_id: string | null
  quote: string
  status: 'proposed' | 'conflict'
  conflicts_with: string | null
}

export type InterpretationRequest = {
  expected_revision: number
  text: string
  ready_document_ids: Array<string>
  question_id?: string | null
}

export type Interpretation = {
  id: string
  profile_id: string
  base_revision: number
  input_hash: string
  status: 'ready' | 'stale' | 'failed' | 'applied'
  assistant_text: string
  proposals: Array<SlotProposal>
  model_id: string
  prompt_version: string
  created_at: string
  suggested_slots: Array<IntakeSlot>
}

export type InterpretationApply = {
  expected_revision: number
  accepted_proposal_ids: Array<string>
  rejected_proposal_ids: Array<string>
}

export type Need = {
  id: string
  label: string
  basis: 'user_requested' | 'jd_required' | 'ncs_reference'
  learner_state: 'self_reported_have' | 'want_to_learn' | 'unknown'
  source_item_ids: Array<string>
  ncs_reference_ids: Array<string>
  mapping_state: 'supported' | 'suggested' | 'unmapped'
}

export type ConstraintCheck = {
  field:
    | 'recruitment'
    | 'schedule'
    | 'mode'
    | 'region'
    | 'budget'
    | 'eligibility'
    | 'start_timing'
  status: 'pass' | 'fail' | 'unknown' | 'not_applicable'
  reason_code: string
  evidence_ids: Array<string>
}

export type AxisAssessment = {
  axis: 'goal' | 'prerequisite' | 'outcome' | 'preference'
  value: 0 | 0.5 | 1 | null
  evidence_ids: Array<string>
  reason_code: string
}

export type RankBreakdown = {
  goal_basis: 'content_supported' | 'topic_only' | 'unrelated'
  score_lower: number
  score_upper: number
  coverage: number
  axes: Array<AxisAssessment>
  constraint_checks: Array<ConstraintCheck>
  ranking_config_version: string
}

export type PipelineStage = {
  stage: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
  input_count: number | null
  output_count: number | null
  reason_counts: { [key: string]: number }
  duration_ms: number | null
}

export type IntakeState = {
  section: 'direction' | 'experience' | 'goal' | 'conditions' | 'review'
  next_action:
    | 'generate_question'
    | 'collect_documents'
    | 'review_sources'
    | 'resolve_conflict'
    | 'review'
  eligible_slots: Array<IntakeSlot>
  missing_slots: Array<IntakeSlot>
  completed_sections: Array<'direction' | 'experience' | 'goal' | 'conditions'>
  active_question_id: string | null
}

export type QuestionPatch = {
  entry_intent?: EntryIntent
  field_code?: FieldCode
  custom_field?: string | null
  experience?: 'beginner' | 'basic' | 'project' | 'work' | 'unknown'
  previous_gap?: 'application' | 'depth' | 'collaboration' | 'unknown' | null
  learning_goal?: 'foundation' | 'portfolio' | 'collaboration' | 'unknown'
  availability?: 'weekday_day' | 'evening_weekend' | 'flexible' | 'unknown'
  delivery_mode?: 'online' | 'offline' | 'hybrid' | 'any' | 'unknown'
  commute_region?: string | null
  budget_status?: 'amount' | 'no_limit' | 'unknown'
  budget_krw?: number | null
  start_timing?: 'soon' | 'one_to_three_months' | 'browse' | 'unknown'
  document_decision?: 'provided' | 'none'
  goal_text?: string | null
  constraints_text?: string | null
  interest_activity?: string | null
}

export type QuestionDraftOption = {
  key: string
  label: string
  description: string
  answer_patch: QuestionPatch
}

export type QuestionDraft = {
  schema_version: string
  target_slot: IntakeSlot
  acknowledgement: string
  question: string
  helper_text: string
  selection_mode: string
  options: Array<QuestionDraftOption>
}

export type QuestionOption = {
  id: string
  label: string
  description: string
  kind: 'generated' | 'unknown'
}

export type IntakeQuestion = {
  id: string
  profile_id: string
  base_revision: number
  context_hash: string
  status: 'ready' | 'answered' | 'stale'
  origin: 'llm' | 'fallback'
  schema_version: string
  target_slot: IntakeSlot
  acknowledgement: string
  question: string
  helper_text: string
  selection_mode: string
  options: Array<QuestionOption>
  allow_custom_text: boolean
  model_id: string | null
  prompt_version: string
  policy_version: string
  created_at: string
}

export type QuestionRequest = { expected_revision: number }

export type QuestionAnswer = {
  expected_revision: number
  selected_option_id: string
}
