import type {
  CourseDetail,
  CourseSummary,
  Error as ApiErrorBody,
} from './types'

// Synthetic v0.8 HTTP samples for UI tests, never production fallback data.
export const courseDetailFixture: CourseDetail = {
  id: 'synthetic-course-1',
  name: '합성 교육 과정',
  provider_name: null,
  field_code: 'software_cloud',
  source_system: 'kmooc',
  course_type: 'online_course',
  evidence_level: 'listing_only',
  detail_url: 'https://example.com/courses/synthetic-course-1',
  is_demo: false,
  description: null,
  prerequisites: null,
  learning_outcomes: [],
  offerings: [
    {
      id: 'synthetic-offering-1',
      external_offering_id: null,
      cohort: '합성 회차',
      start_date: null,
      end_date: null,
      enrollment_start: null,
      enrollment_end: null,
      delivery_mode: 'online',
      region: null,
      schedule: null,
      training_hours: null,
      tuition_krw: null,
      listed_self_pay_krw: null,
      eligibility_text: null,
      recruitment_status: 'unknown',
      checked_at: '2026-11-01T00:00:00Z',
    },
  ],
  evidence: [
    {
      id: 'synthetic-evidence-1',
      field_path: 'name',
      quote: '합성 자료의 교육명',
      source_url: 'https://example.com/evidence/synthetic-evidence-1',
      source_system: 'kmooc',
      checked_at: '2026-11-01T00:00:00Z',
      verification_state: 'observed',
    },
  ],
  missing_fields: ['tuition_krw', 'schedule'],
}

export const savedCoursesFixture: { items: CourseSummary[] } = {
  items: [
    {
      id: courseDetailFixture.id,
      name: courseDetailFixture.name,
      provider_name: courseDetailFixture.provider_name,
      field_code: courseDetailFixture.field_code,
      source_system: courseDetailFixture.source_system,
      course_type: courseDetailFixture.course_type,
      evidence_level: courseDetailFixture.evidence_level,
      detail_url: courseDetailFixture.detail_url,
      is_demo: false,
    },
  ],
}

export const emptySavedCoursesFixture: { items: CourseSummary[] } = {
  items: [],
}

export const courseNotFoundFixture: ApiErrorBody = {
  code: 'not_found',
  message: 'Synthetic course not found',
  request_id: 'synthetic-request-1',
}
