-- 다음 / EduFit · PostgreSQL 17.11 schema design v0.8 · 2026-11-01 KST
-- UUID defaults are database-generated; timestamps are supplied by the application.
-- Apply through Alembic or psql with ON_ERROR_STOP=1. UTF-8 database, UTC session timezone.

CREATE TABLE catalog_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status TEXT NOT NULL CHECK(status IN ('draft','active','archived')),
  as_of TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  summary_json JSONB NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX one_active_catalog ON catalog_versions(status) WHERE status='active';

CREATE TABLE import_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_version_id UUID NOT NULL REFERENCES catalog_versions(id),
  source_system TEXT NOT NULL CHECK(source_system IN ('kmooc','work24','ncs')),
  file_name TEXT NOT NULL,
  file_sha256 TEXT NOT NULL,
  source_schema_version TEXT NOT NULL,
  adapter_version TEXT NOT NULL,
  batch_mode TEXT NOT NULL CHECK(batch_mode IN ('full','partial')),
  coverage TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('staged','validated','quarantined','imported')),
  received_at TIMESTAMPTZ NOT NULL,
  collected_at TIMESTAMPTZ,
  record_count INTEGER NOT NULL CHECK(record_count>=0),
  validation_report_json JSONB NOT NULL DEFAULT '{}',
  UNIQUE(catalog_version_id,source_system,file_sha256)
);

CREATE TABLE source_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_version_id UUID NOT NULL REFERENCES catalog_versions(id),
  source_system TEXT NOT NULL CHECK(source_system IN ('kmooc','work24','ncs')),
  collection_method TEXT NOT NULL CHECK(collection_method IN ('rest_api','mcp','official_download','public_web','manual_enrichment','synthetic_demo')),
  entity_kind TEXT NOT NULL CHECK(entity_kind IN ('course','course_detail','skill_reference')),
  external_id TEXT,
  source_url TEXT,
  file_name TEXT,
  import_batch_id UUID NOT NULL REFERENCES import_batches(id),
  record_locator TEXT NOT NULL,
  collected_at TIMESTAMPTZ,
  raw_json JSONB NOT NULL,
  content_sha256 TEXT NOT NULL,
  reuse_status TEXT NOT NULL DEFAULT 'unverified' CHECK(reuse_status IN ('unverified','reviewed_for_demo','reviewed_for_public')),
  UNIQUE(import_batch_id,record_locator)
);

CREATE TABLE courses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_version_id UUID NOT NULL REFERENCES catalog_versions(id),
  source_record_id UUID NOT NULL REFERENCES source_records(id),
  source_system TEXT NOT NULL CHECK(source_system IN ('kmooc','work24')),
  external_id TEXT,
  name TEXT NOT NULL,
  provider_name TEXT,
  field_code TEXT,
  topic_tags_json JSONB NOT NULL DEFAULT '[]',
  description TEXT,
  prerequisites TEXT,
  learning_outcomes_json JSONB NOT NULL DEFAULT '[]',
  course_type TEXT NOT NULL DEFAULT 'unknown' CHECK(course_type IN ('online_course','vocational_training','kdt','unknown')),
  evidence_level TEXT NOT NULL DEFAULT 'listing_only' CHECK(evidence_level IN ('listing_only','detail_verified')),
  detail_url TEXT,
  is_demo BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(catalog_version_id,source_system,external_id)
);

CREATE TABLE offerings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  source_record_id UUID NOT NULL REFERENCES source_records(id),
  external_offering_id TEXT,
  cohort TEXT,
  start_date DATE,
  end_date DATE,
  enrollment_start TIMESTAMPTZ,
  enrollment_end TIMESTAMPTZ,
  delivery_mode TEXT NOT NULL DEFAULT 'unknown' CHECK(delivery_mode IN ('online','offline','hybrid','unknown')),
  region TEXT,
  schedule_json JSONB,
  training_hours NUMERIC(8,2) CHECK(training_hours IS NULL OR training_hours>=0),
  tuition_krw BIGINT CHECK(tuition_krw IS NULL OR tuition_krw>=0),
  listed_self_pay_krw BIGINT CHECK(listed_self_pay_krw IS NULL OR listed_self_pay_krw>=0),
  eligibility_text TEXT,
  recruitment_status TEXT NOT NULL DEFAULT 'unknown' CHECK(recruitment_status IN ('open','closed','unknown')),
  checked_at TIMESTAMPTZ,
  CHECK(start_date IS NULL OR end_date IS NULL OR start_date<=end_date),
  UNIQUE(id,course_id)
);

CREATE TABLE skill_references (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_record_id UUID NOT NULL REFERENCES source_records(id),
  reference_kind TEXT NOT NULL CHECK(reference_kind IN ('module','training_standard')),
  name TEXT NOT NULL,
  classification TEXT,
  keywords_json JSONB NOT NULL DEFAULT '[]',
  standard_level TEXT,
  standard_hours TEXT,
  raw_details_json JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE evidence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_record_id UUID NOT NULL REFERENCES source_records(id),
  course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
  offering_id UUID REFERENCES offerings(id) ON DELETE CASCADE,
  skill_reference_id UUID REFERENCES skill_references(id) ON DELETE CASCADE,
  field_path TEXT NOT NULL,
  quote TEXT,
  fact_json JSONB,
  verification_state TEXT NOT NULL CHECK(verification_state IN ('observed','verified','unknown','conflicting')),
  checked_at TIMESTAMPTZ NOT NULL,
  CHECK(num_nonnulls(course_id,offering_id,skill_reference_id)=1)
);

-- Account ownership replaces anonymous onboarding sessions.
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  google_sub TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  display_name TEXT,
  avatar_url TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE auth_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
);
CREATE TABLE oauth_login_states (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  state_hash TEXT NOT NULL UNIQUE,
  browser_binding_hash TEXT NOT NULL,
  nonce_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ
);
CREATE TABLE intake_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
  answers_json JSONB NOT NULL DEFAULT '{}',
  reviewed_items_json JSONB NOT NULL DEFAULT '[]',
  completed_sections_json JSONB NOT NULL DEFAULT '[]',
  slot_origins_json JSONB NOT NULL DEFAULT '{}',
  unresolved_conflicts_json JSONB NOT NULL DEFAULT '[]',
  submitted_revision INTEGER,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(id,user_id),
  CHECK(submitted_revision IS NULL OR submitted_revision<=revision)
);
CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL,
  user_id UUID NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('resume','job_description','portfolio','other')),
  source_type TEXT NOT NULL CHECK(source_type IN ('file','url','text')),
  display_name TEXT NOT NULL,
  source_url TEXT,
  storage_key TEXT,
  mime_type TEXT,
  size_bytes BIGINT CHECK(size_bytes IS NULL OR size_bytes>=0),
  content_sha256 TEXT,
  status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed')),
  extracted_text TEXT,
  extraction_json JSONB,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY(profile_id,user_id) REFERENCES intake_profiles(id,user_id) ON DELETE CASCADE,
  UNIQUE(id,profile_id,user_id),
  CHECK((source_type='file' AND storage_key IS NOT NULL) OR (source_type='url' AND source_url IS NOT NULL) OR source_type='text')
);
CREATE TABLE document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  page_number INTEGER,
  source_locator TEXT,
  text_content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  UNIQUE(document_id,ordinal)
);
CREATE TABLE jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL,
  user_id UUID NOT NULL,
  document_id UUID,
  kind TEXT NOT NULL CHECK(kind IN ('document_processing','intake_interpretation','intake_question','recommendation')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','superseded','cancelled')),
  phase TEXT NOT NULL DEFAULT 'queued',
  profile_revision INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  input_json JSONB NOT NULL,
  result_json JSONB,
  error_code TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  lease_expires_at TIMESTAMPTZ,
  FOREIGN KEY(profile_id,user_id) REFERENCES intake_profiles(id,user_id) ON DELETE CASCADE,
  FOREIGN KEY(document_id,profile_id,user_id) REFERENCES documents(id,profile_id,user_id) ON DELETE CASCADE,
  UNIQUE(id,profile_id,user_id),
  UNIQUE(user_id,kind,idempotency_key),
  CHECK((kind='document_processing' AND document_id IS NOT NULL) OR (kind IN ('recommendation','intake_interpretation','intake_question') AND document_id IS NULL))
);
CREATE TABLE recommendation_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL,
  user_id UUID NOT NULL,
  job_id UUID NOT NULL UNIQUE,
  catalog_version_id UUID NOT NULL REFERENCES catalog_versions(id),
  profile_revision INTEGER NOT NULL,
  profile_snapshot_json JSONB NOT NULL,
  source_snapshot_json JSONB NOT NULL,
  result_state TEXT NOT NULL CHECK(result_state IN ('matches','unknown_conditions','empty','unsupported_field')),
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  retrieval_config_version TEXT NOT NULL,
  ranking_config_version TEXT NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  needs_json JSONB NOT NULL DEFAULT '[]',
  model_outcome TEXT NOT NULL CHECK(model_outcome IN ('succeeded','not_needed')),
  runtime_mode TEXT NOT NULL CHECK(runtime_mode IN ('real_model','sample')),
  is_stale BOOLEAN NOT NULL DEFAULT FALSE,
  excluded_counts_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY(profile_id,user_id) REFERENCES intake_profiles(id,user_id) ON DELETE CASCADE,
  FOREIGN KEY(job_id,profile_id,user_id) REFERENCES jobs(id,profile_id,user_id) ON DELETE CASCADE
);
CREATE TABLE recommendation_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES recommendation_runs(id) ON DELETE CASCADE,
  course_id UUID NOT NULL REFERENCES courses(id),
  offering_id UUID,
  rank INTEGER NOT NULL CHECK(rank>0),
  match_state TEXT NOT NULL CHECK(match_state IN ('verified_conditions','unknown_conditions')),
  reason_json JSONB NOT NULL,
  rank_breakdown_json JSONB NOT NULL,
  evidence_ids_json JSONB NOT NULL DEFAULT '[]',
  missing_fields_json JSONB NOT NULL DEFAULT '[]',
  FOREIGN KEY(offering_id,course_id) REFERENCES offerings(id,course_id),
  UNIQUE(run_id,course_id),
  UNIQUE(run_id,rank)
);
CREATE TABLE saved_courses (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  course_id UUID NOT NULL REFERENCES courses(id),
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(user_id,course_id)
);

CREATE TABLE catalog_search_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_version_id UUID NOT NULL REFERENCES catalog_versions(id),
  course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
  skill_reference_id UUID REFERENCES skill_references(id) ON DELETE CASCADE,
  title_terms TEXT NOT NULL,
  body_terms TEXT NOT NULL,
  normalized_terms_json JSONB NOT NULL DEFAULT '[]',
  dictionary_version TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  CHECK(num_nonnulls(course_id,skill_reference_id)=1),
  UNIQUE(course_id),
  UNIQUE(skill_reference_id)
);
CREATE INDEX search_title_fts ON catalog_search_documents USING GIN (to_tsvector('simple'::regconfig,title_terms));
CREATE INDEX search_body_fts ON catalog_search_documents USING GIN (to_tsvector('simple'::regconfig,body_terms));
CREATE INDEX search_catalog ON catalog_search_documents(catalog_version_id);

CREATE TABLE course_skill_links (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  skill_reference_id UUID NOT NULL REFERENCES skill_references(id),
  course_evidence_id UUID REFERENCES evidence(id),
  relation_state TEXT NOT NULL CHECK(relation_state IN ('content_supported','topic_only','suggested')),
  method TEXT NOT NULL CHECK(method IN ('explicit_source','rule','model')),
  mapping_version TEXT NOT NULL,
  rationale_json JSONB NOT NULL DEFAULT '{}',
  UNIQUE(course_id,skill_reference_id,mapping_version),
  CHECK(relation_state<>'content_supported' OR course_evidence_id IS NOT NULL)
);

CREATE TABLE profile_interpretations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL,
  user_id UUID NOT NULL,
  job_id UUID NOT NULL UNIQUE,
  base_revision INTEGER NOT NULL CHECK(base_revision>=0),
  input_hash TEXT NOT NULL,
  source_hashes_json JSONB NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','stale','failed','applied')),
  proposal_json JSONB NOT NULL,
  decision_json JSONB,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  applied_at TIMESTAMPTZ,
  FOREIGN KEY(profile_id,user_id) REFERENCES intake_profiles(id,user_id) ON DELETE CASCADE,
  FOREIGN KEY(job_id,profile_id,user_id) REFERENCES jobs(id,profile_id,user_id) ON DELETE CASCADE
);

CREATE TABLE recommendation_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK(stage IN ('freezing','profiling','mapping_needs','retrieving','checking_constraints','assessing','ranking','explaining','validating','persisting')),
  attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt>0),
  status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','skipped')),
  input_hash TEXT NOT NULL,
  output_json JSONB NOT NULL DEFAULT '{}',
  version_json JSONB NOT NULL,
  input_count INTEGER CHECK(input_count IS NULL OR input_count>=0),
  output_count INTEGER CHECK(output_count IS NULL OR output_count>=0),
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  error_code TEXT,
  UNIQUE(job_id,stage,attempt)
);
CREATE INDEX interpretation_owner ON profile_interpretations(profile_id,user_id,base_revision);
CREATE INDEX steps_job ON recommendation_steps(job_id,stage);

CREATE INDEX source_records_catalog ON source_records(catalog_version_id,entity_kind);
CREATE INDEX courses_catalog_field ON courses(catalog_version_id,field_code);
CREATE INDEX offerings_course ON offerings(course_id);
CREATE INDEX evidence_course ON evidence(course_id);
CREATE INDEX evidence_offering ON evidence(offering_id);
CREATE INDEX profiles_owner ON intake_profiles(user_id,updated_at);
CREATE INDEX documents_profile ON documents(profile_id,status);
CREATE INDEX jobs_queue ON jobs(status,created_at);
CREATE INDEX jobs_owner ON jobs(user_id,created_at);
CREATE INDEX runs_owner ON recommendation_runs(user_id,created_at);
CREATE INDEX auth_sessions_expiry ON auth_sessions(expires_at);
CREATE INDEX oauth_states_expiry ON oauth_login_states(expires_at);
-- Implementation invariants, enforced in service-layer transactions:
-- 1. New business migrations follow 0001_baseline; do not rewrite that revision.
-- 2. Google identity is google_sub; never merge accounts by email alone.
-- 3. HTTP session expiry does not delete user profiles, uploads or results.
-- 4. Submission validates every applicable input, source readiness and review_token.
-- 5. Store only IDs/hashes in job input_json; do not duplicate raw private files there.
-- 6. A long model/OCR/fetch call runs outside an open DB transaction.
-- 7. Mutation increments profile revision; stale work cannot replace newer inputs.
-- 8. Source provenance, catalog versions, evidence IDs and owner joins must agree.
-- 9. Source deletion removes private derived text and invalidates affected results.
-- 10. This is reference DDL. Implement SQLAlchemy models + Alembic migration, then test.

-- v0.7 implementation invariants:
-- 11. Only kmooc/work24/ncs are catalog sources; NCS cannot create course/offering rows.
-- 12. Raw user documents and their URLs are personal context, not a fourth catalog source.
-- 13. Never run this reference file over production as a drop/replace migration.
-- 14. A partial import cannot delete absent records or mark absent offerings closed.
-- 15. Skill links, evidence and search documents must reference one catalog snapshot.
-- 16. Rebuild changed normalized documents before atomically activating a catalog.
-- 17. Interpretation application checks base revision AND source hashes in one transaction.
-- 18. Store decision facts/IDs/counts, not hidden model reasoning, in recommendation_steps.
-- 19. The service checks evidence belongs to the linked course; topic links are not curriculum proof.
-- 20. PostgreSQL simple FTS is keyword search; normalized Korean terms require a versioned dictionary.


-- v0.8: immutable generated questions and selected-option audit trail.
CREATE TABLE intake_questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id UUID NOT NULL,
  user_id UUID NOT NULL,
  job_id UUID NOT NULL UNIQUE,
  base_revision INTEGER NOT NULL CHECK(base_revision>=0),
  context_hash TEXT NOT NULL,
  source_hashes_json JSONB NOT NULL,
  target_slot TEXT NOT NULL,
  question_signature TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','answered','stale')),
  origin TEXT NOT NULL CHECK(origin IN ('llm','fallback')),
  schema_version TEXT NOT NULL,
  model_id TEXT,
  prompt_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  draft_json JSONB NOT NULL,
  public_question_json JSONB NOT NULL,
  option_bindings_json JSONB NOT NULL,
  validation_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  answered_at TIMESTAMPTZ,
  FOREIGN KEY(profile_id,user_id) REFERENCES intake_profiles(id,user_id) ON DELETE CASCADE,
  FOREIGN KEY(job_id,profile_id,user_id) REFERENCES jobs(id,profile_id,user_id) ON DELETE CASCADE,
  UNIQUE(id,profile_id,user_id),
  UNIQUE(profile_id,base_revision,context_hash),
  CHECK(origin='fallback' OR model_id IS NOT NULL)
);
CREATE UNIQUE INDEX one_ready_intake_question ON intake_questions(profile_id) WHERE status='ready';
CREATE TABLE intake_answers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id UUID NOT NULL UNIQUE,
  profile_id UUID NOT NULL,
  user_id UUID NOT NULL,
  selected_option_id UUID NOT NULL,
  applied_patch_json JSONB NOT NULL,
  revision_before INTEGER NOT NULL CHECK(revision_before>=0),
  revision_after INTEGER NOT NULL CHECK(revision_after=revision_before+1),
  created_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY(question_id,profile_id,user_id) REFERENCES intake_questions(id,profile_id,user_id) ON DELETE CASCADE
);
CREATE INDEX questions_owner ON intake_questions(profile_id,user_id,base_revision);
-- 21. Validate the exact selected_option_id against persisted option_bindings_json.
-- 22. Lock profile + question, verify revision/source hashes, apply one selection atomically.
-- 23. Invalidate old ready questions before activating a new one; reuse equivalent context.
-- 24. Keep labels, allowed patches, model/prompt/policy/schema versions for reproducibility.
-- 25. Completion and review_token are computed by server policy, never by a model done flag.
-- 26. Document deletion also removes private quotations from derived question snapshots.
