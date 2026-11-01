import { useEffect, useRef, useState } from 'react'
import { request } from './api'
import type {
  ConstraintCheck,
  CourseDetail,
  CourseSummary,
  Evidence,
  Offering,
  RecommendationRun,
} from './types'

const conditionNames: Record<ConstraintCheck['field'], string> = {
  recruitment: '모집',
  schedule: '수업 시간',
  mode: '교육 방식',
  region: '통학 지역',
  budget: '예산',
  eligibility: '참여 자격',
  start_timing: '시작 시점',
}
const conditionStates: Record<ConstraintCheck['status'], string> = {
  pass: '충족',
  fail: '불충족',
  unknown: '미확인',
  not_applicable: '해당 없음',
}

const missingFieldLabels: Record<string, string> = {
  field_code: '교육 분야',
  course_type: '교육 유형',
  checked_at: '확인 시각',
  description: '교육 설명',
  prerequisites: '선수 지식',
  learning_outcomes: '학습 결과',
  provider_name: '교육기관',
  external_offering_id: '회차 식별 정보',
  cohort: '회차명',
  start_date: '시작일',
  end_date: '종료일',
  enrollment_start: '모집 시작일',
  enrollment_end: '모집 종료일',
  delivery_mode: '교육 방식',
  region: '교육 지역',
  schedule: '수업 일정',
  training_hours: '교육 시간',
  tuition_krw: '교육비',
  listed_self_pay_krw: '공시 자부담금',
  eligibility_text: '참여 자격',
  recruitment_status: '모집 상태',
}

function missingFieldLabelsForDisplay(fields: string[]) {
  return [
    ...new Set(
      fields.map((field) => {
        const match = /^offerings\.[^.]+\.(.+)$/.exec(field)
        const key = match?.[1] ?? field
        const label = Object.hasOwn(missingFieldLabels, key)
          ? missingFieldLabels[key]
          : undefined
        return match ? `회차 ${label ?? '정보'}` : (label ?? '기타 정보')
      }),
    ),
  ]
}

function MissingFields({ fields }: { fields: string[] }) {
  const labels = missingFieldLabelsForDisplay(fields)
  return labels.length > 0 ? <p>미확인 항목: {labels.join(', ')}</p> : null
}

function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : '요청을 완료하지 못했습니다. 다시 시도해주세요.'
}

function safeUrl(value: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}

function SourceLink({ url, label }: { url: string | null; label: string }) {
  const href = safeUrl(url)
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {label}
    </a>
  ) : (
    <span>출처 링크 미확인</span>
  )
}

function realCourse(course: CourseSummary) {
  if (
    !course ||
    course.is_demo !== false ||
    typeof course.id !== 'string' ||
    typeof course.name !== 'string' ||
    !['kmooc', 'work24'].includes(course.source_system) ||
    !['listing_only', 'detail_verified'].includes(course.evidence_level)
  ) {
    throw new Error('실제 교육 데이터로 확인되지 않은 응답입니다.')
  }
  return course
}

function courseDetail(course: CourseDetail) {
  realCourse(course)
  if (
    !Array.isArray(course.offerings) ||
    !Array.isArray(course.evidence) ||
    !Array.isArray(course.learning_outcomes) ||
    !Array.isArray(course.missing_fields)
  ) {
    throw new Error('교육 상세 응답 형식을 확인할 수 없습니다.')
  }
  return course
}

function realRun(run: RecommendationRun) {
  if (
    !run ||
    run.runtime_mode !== 'real_model' ||
    !Array.isArray(run.items) ||
    !Array.isArray(run.notices) ||
    !Array.isArray(run.needs) ||
    !['matches', 'unknown_conditions', 'empty', 'unsupported_field'].includes(
      run.result_state,
    ) ||
    !['succeeded', 'not_needed'].includes(run.model_outcome)
  ) {
    throw new Error('실제 모델의 추천 결과로 확인되지 않은 응답입니다.')
  }
  const expectsItems = ['matches', 'unknown_conditions'].includes(
    run.result_state,
  )
  const hasItems = run.items.length > 0
  if (
    expectsItems !== hasItems ||
    (run.model_outcome === 'not_needed' &&
      (hasItems || run.explanation_mode !== 'none'))
  ) {
    throw new Error(
      '추천 상태와 교육 목록이 일치하지 않습니다. 결과를 다시 확인해주세요.',
    )
  }
  run.items.forEach((item) => {
    if (
      !item ||
      !Array.isArray(item.reasons) ||
      !Array.isArray(item.evidence) ||
      !Array.isArray(item.missing_fields) ||
      !['verified_conditions', 'unknown_conditions'].includes(
        item.match_state,
      ) ||
      !item.rank_breakdown ||
      !Array.isArray(item.rank_breakdown.constraint_checks)
    ) {
      throw new Error('추천 근거 응답 형식을 확인할 수 없습니다.')
    }
    realCourse(item.course)
    if (
      item.rank_breakdown.constraint_checks.some(
        (check) =>
          !check ||
          !Object.hasOwn(conditionNames, check.field) ||
          !Object.hasOwn(conditionStates, check.status) ||
          check.status === 'fail' ||
          (item.match_state === 'verified_conditions' &&
            check.status === 'unknown') ||
          !Array.isArray(check.evidence_ids) ||
          typeof check.reason_code !== 'string',
      )
    ) {
      throw new Error(
        '추천의 참여 조건을 확인할 수 없습니다. 결과를 다시 확인해주세요.',
      )
    }
  })
  return run
}

function savedList(value: { items: CourseSummary[] }) {
  if (!value || !Array.isArray(value.items)) {
    throw new Error('관심 교육 목록 응답 형식을 확인할 수 없습니다.')
  }
  value.items.forEach(realCourse)
  return value
}

function comparison(value: { items: CourseDetail[] }) {
  if (
    !value ||
    !Array.isArray(value.items) ||
    value.items.length < 2 ||
    value.items.length > 3
  ) {
    throw new Error('비교 응답 형식을 확인할 수 없습니다.')
  }
  value.items.forEach(courseDetail)
  return value
}

function validComparisonIds(ids: string[]) {
  return (
    ids.length >= 2 &&
    ids.length <= 3 &&
    new Set(ids.map((id) => id.toLowerCase())).size === ids.length &&
    ids.every((id) => /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id))
  )
}

type LoadState<T> = {
  path: string
  data: T | null
  error: string | null
  loading: boolean
}

function useRemote<T>(path: string, validate: (value: T) => T) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<LoadState<T>>({
    path,
    data: null,
    error: null,
    loading: true,
  })
  useEffect(() => {
    const controller = new AbortController()
    setState({ path, data: null, error: null, loading: true })
    void request<T>(path, { signal: controller.signal })
      .then(validate)
      .then((data) => {
        if (!controller.signal.aborted) {
          setState({ path, data, error: null, loading: false })
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ path, data: null, error: message(error), loading: false })
        }
      })
    return () => controller.abort()
  }, [path, attempt, validate])
  return {
    ...(state.path === path
      ? state
      : { path, data: null, error: null, loading: true }),
    retry: () => setAttempt((value) => value + 1),
  }
}

function LoadMessage({
  loading,
  error,
  retry,
}: {
  loading: boolean
  error: string | null
  retry: () => void
}) {
  if (loading) return <p role="status">불러오고 있습니다…</p>
  if (!error) return null
  return (
    <div className="error" role="alert">
      <p>{error}</p>
      <button type="button" onClick={retry}>
        다시 불러오기
      </button>
    </div>
  )
}

const deliveryNames = {
  online: '온라인',
  offline: '오프라인',
  hybrid: '온·오프라인 혼합',
  unknown: '미확인',
}

function money(value: number | null) {
  return value === null ? '미확인' : `${value.toLocaleString('ko-KR')}원`
}

function OfferingInfo({ offering }: { offering: Offering }) {
  return (
    <dl className="facts">
      <div>
        <dt>교육 방식</dt>
        <dd>{deliveryNames[offering.delivery_mode]}</dd>
      </div>
      <div>
        <dt>교육 기간</dt>
        <dd>
          {offering.start_date ?? '미확인'} ~ {offering.end_date ?? '미확인'}
        </dd>
      </div>
      <div>
        <dt>신청 기간</dt>
        <dd>
          {offering.enrollment_start ?? '미확인'} ~{' '}
          {offering.enrollment_end ?? '미확인'}
        </dd>
      </div>
      <div>
        <dt>총 교육 시간</dt>
        <dd>
          {offering.training_hours === null
            ? '미확인'
            : `${offering.training_hours.toLocaleString('ko-KR')}시간`}
        </dd>
      </div>
      <div>
        <dt>지역</dt>
        <dd>{offering.region ?? '미확인'}</dd>
      </div>
      <div>
        <dt>교육비 / 공시 자부담</dt>
        <dd>
          <span>
            {money(offering.tuition_krw)} /{' '}
            {money(offering.listed_self_pay_krw)}
          </span>
          <p className="muted">
            공시 자부담은 개인별 최종 부담금과 다를 수 있습니다.
          </p>
        </dd>
      </div>
      <div>
        <dt>참여 자격</dt>
        <dd>{offering.eligibility_text ?? '미확인'}</dd>
      </div>
      <div>
        <dt>시간표</dt>
        <dd>
          {offering.schedule
            ? `${offering.schedule.days.join(', ') || '요일 미확인'} · ${offering.schedule.start_time ?? '시작 시각 미확인'} ~ ${offering.schedule.end_time ?? '종료 시각 미확인'} (${offering.schedule.timezone})`
            : '미확인'}
        </dd>
      </div>
      <div>
        <dt>자율 수강</dt>
        <dd>
          {offering.schedule?.is_asynchronous === true
            ? '가능'
            : offering.schedule?.is_asynchronous === false
              ? '아니요'
              : '미확인'}
        </dd>
      </div>
      <div>
        <dt>모집 상태 (확인 시점 기준)</dt>
        <dd>
          {offering.recruitment_status === 'open'
            ? '모집 중'
            : offering.recruitment_status === 'closed'
              ? '마감'
              : '미확인'}
        </dd>
      </div>
      <div>
        <dt>확인 시각</dt>
        <dd>{offering.checked_at ?? '미확인'}</dd>
      </div>
    </dl>
  )
}

function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) return <p>확인된 출처 근거가 없습니다.</p>
  return (
    <ul className="stack">
      {items.map((item) => (
        <li key={item.id}>
          {item.field_path.startsWith('inferred_units.') && (
            <p className="muted">추정 보완 정보 · 실제 교육내용 확인 필요</p>
          )}
          {item.quote && <blockquote>{item.quote}</blockquote>}
          <p>
            {item.source_system} · {item.verification_state} ·{' '}
            {item.checked_at ?? '미확인'}
          </p>
          <SourceLink url={item.source_url} label="근거 원문 보기" />
        </li>
      ))}
    </ul>
  )
}

function ConditionChecks({ checks }: { checks: ConstraintCheck[] }) {
  return (
    <section aria-label="참여 조건 확인">
      <h3>참여 조건 확인</h3>
      {checks.length ? (
        <dl className="facts">
          {checks.map((check, index) => (
            <div key={`${check.field}-${index}`}>
              <dt>{conditionNames[check.field]}</dt>
              <dd>{conditionStates[check.status]}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p>제공된 조건 확인 정보가 없습니다.</p>
      )}
      {checks.some((check) => check.status === 'unknown') && (
        <p className="muted">미확인 조건은 교육 원문에서 확인해 주세요.</p>
      )}
    </section>
  )
}

function SaveCourse({
  courseId,
  courseName,
  initiallySaved = false,
  onChange,
}: {
  courseId: string
  courseName: string
  initiallySaved?: boolean
  onChange?: (notice: string) => void
}) {
  const [saved, setSaved] = useState(initiallySaved)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const operation = useRef<AbortController | null>(null)
  useEffect(() => () => operation.current?.abort(), [])
  async function toggle() {
    if (operation.current) return
    const controller = new AbortController()
    operation.current = controller
    setBusy(true)
    setError(null)
    setNotice('')
    try {
      await request<void>(`/me/saved-courses/${encodeURIComponent(courseId)}`, {
        method: saved ? 'DELETE' : 'PUT',
        signal: controller.signal,
      })
      if (!controller.signal.aborted) {
        setSaved(!saved)
        const notice = saved
          ? `${courseName}의 관심 해제 요청을 완료했습니다.`
          : `${courseName}의 관심 저장 요청을 완료했습니다.`
        if (onChange) onChange(notice)
        else setNotice(notice)
      }
    } catch (error: unknown) {
      if (!controller.signal.aborted) setError(message(error))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
      operation.current = null
    }
  }
  const action = saved ? '관심 교육 해제' : '관심 교육 저장'
  return (
    <div>
      <button
        type="button"
        disabled={busy}
        aria-label={`${courseName} ${action}${busy ? ' 중' : ''}`}
        onClick={() => void toggle()}
      >
        {busy ? '처리 중…' : action}
      </button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </div>
  )
}

function DetailBody({ course }: { course: CourseDetail }) {
  return (
    <div className="stack">
      <p className="eyebrow">
        {course.source_system === 'work24' ? '고용24' : 'K-MOOC'}
      </p>
      <h3>{course.name}</h3>
      <p className="muted">
        {course.evidence_level === 'listing_only'
          ? '목록 정보 기준 · 상세 확인 필요'
          : '상세 정보 확인 자료'}
      </p>
      <p>{course.provider_name ?? '교육기관 미확인'}</p>
      <p>{course.description ?? '교육 설명 미확인'}</p>
      <h4>선수 지식</h4>
      <p>{course.prerequisites ?? '미확인'}</p>
      <h4>학습 결과</h4>
      {course.learning_outcomes.length ? (
        <ul>
          {course.learning_outcomes.map((outcome, index) => (
            <li key={index}>{outcome}</li>
          ))}
        </ul>
      ) : (
        <p>미확인</p>
      )}
      <SourceLink url={course.detail_url} label="교육 원문 보기" />
      <h4>교육 회차</h4>
      {course.offerings.length ? (
        course.offerings.map((offering) => (
          <section className="panel" key={offering.id}>
            <h5>{offering.cohort ?? '회차명 미확인'}</h5>
            <OfferingInfo offering={offering} />
          </section>
        ))
      ) : (
        <p>등록된 회차 정보가 없습니다.</p>
      )}
      <MissingFields fields={course.missing_fields} />
      <h4>출처와 근거</h4>
      <EvidenceList items={course.evidence} />
    </div>
  )
}

export function CourseDetails({ courseId }: { courseId: string }) {
  const remote = useRemote(
    `/courses/${encodeURIComponent(courseId)}`,
    courseDetail,
  )
  return (
    <section className="panel stack" aria-label="교육 상세">
      <h2>교육 상세</h2>
      <LoadMessage {...remote} />
      {remote.data && remote.data.id !== courseId && (
        <p role="alert">요청한 교육과 상세 결과가 다릅니다.</p>
      )}
      {remote.data?.id === courseId && <DetailBody course={remote.data} />}
    </section>
  )
}

function CompareCourses({
  ids,
  onClose,
}: {
  ids: string[]
  onClose: () => void
}) {
  const remote = useRemote(
    `/courses/compare?${new URLSearchParams({ ids: ids.join(',') })}`,
    comparison,
  )
  const data = remote.data
  const matches =
    data &&
    data.items.length === ids.length &&
    new Set(data.items.map((course) => course.id.toLowerCase())).size ===
      ids.length &&
    data.items.every((course) =>
      ids.some((id) => id.toLowerCase() === course.id.toLowerCase()),
    )
  const ordered =
    matches && data
      ? ids.flatMap((id) =>
          data.items.filter(
            (course) => course.id.toLowerCase() === id.toLowerCase(),
          ),
        )
      : []
  return (
    <section className="panel stack" aria-label="교육 비교 결과">
      <div className="section-heading">
        <h2>선택한 교육 비교</h2>
        <button type="button" onClick={onClose}>
          비교 닫기
        </button>
      </div>
      <LoadMessage
        {...remote}
        error={
          remote.error ||
          (data && !matches ? '선택한 교육과 비교 결과가 다릅니다.' : null)
        }
      />
      {matches && data && (
        <div className="comparison-grid">
          {ordered.map((course) => (
            <article className="panel" key={course.id}>
              <DetailBody course={course} />
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

export function Results({ runId }: { runId: string }) {
  return <ResultContent key={runId} runId={runId} />
}

function ResultContent({ runId }: { runId: string }) {
  const remote = useRemote(
    `/recommendations/${encodeURIComponent(runId)}`,
    realRun,
  )
  const [detailId, setDetailId] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [comparisonError, setComparisonError] = useState('')
  const run = remote.data
  const resultNames = {
    matches: '확인된 조건에 맞는 교육',
    unknown_conditions: '일부 조건 확인이 필요한 교육',
    empty: '조건에 맞는 교육을 찾지 못했습니다.',
    unsupported_field: '현재 제공되는 교육 분야에 포함되지 않습니다.',
  }
  function select(id: string, checked: boolean) {
    setCompareIds([])
    setComparisonError('')
    setSelected((current) =>
      checked
        ? [...new Set([...current, id])].slice(0, 3)
        : current.filter((item) => item !== id),
    )
  }
  return (
    <section className="stack" aria-label="추천 결과">
      <h1>나에게 맞는 다음 배움</h1>
      <LoadMessage
        {...remote}
        error={
          remote.error ||
          (run && run.id !== runId ? '요청한 추천 결과와 다릅니다.' : null)
        }
      />
      {run && run.id === runId && (
        <>
          <section className="panel">
            <h2>{resultNames[run.result_state]}</h2>
            <p>교육 정보 기준: {run.as_of}</p>
            <p>추천 생성: {run.created_at}</p>
            {run.notices.map((notice, index) => (
              <p key={index}>{notice}</p>
            ))}
            {run.model_outcome === 'not_needed' && (
              <p>모델 평가가 필요하지 않은 검색 결과입니다.</p>
            )}
            {run.needs.length > 0 && (
              <ul>
                {run.needs.map((need) => (
                  <li key={need.id}>{need.label}</li>
                ))}
              </ul>
            )}
          </section>
          {!run.items.length && <p>표시할 추천 교육이 없습니다.</p>}
          <div className="stack">
            {run.items.map((item) => (
              <article
                className="panel stack"
                key={`${item.course.id}-${item.rank}`}
              >
                <p>
                  {item.course.source_system === 'kmooc' ? 'K-MOOC' : '고용24'}
                </p>
                <h2>{item.course.name}</h2>
                <p>{item.course.provider_name ?? '교육기관 미확인'}</p>
                <p>
                  {item.match_state === 'verified_conditions'
                    ? '확인된 조건 일치'
                    : '확인이 필요한 조건 포함'}
                </p>
                {item.reasons.map((reason, index) => (
                  <p key={index}>{reason.text}</p>
                ))}
                {item.offering ? (
                  <OfferingInfo offering={item.offering} />
                ) : (
                  <p>회차 정보 미확인</p>
                )}
                <MissingFields fields={item.missing_fields} />
                <ConditionChecks
                  checks={item.rank_breakdown.constraint_checks}
                />
                <details>
                  <summary>추천 근거 확인</summary>
                  <EvidenceList items={item.evidence} />
                </details>
                <div className="actions">
                  <button
                    type="button"
                    onClick={() => setDetailId(item.course.id)}
                  >
                    교육 상세
                  </button>
                  <SaveCourse
                    courseId={item.course.id}
                    courseName={item.course.name}
                  />
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`${item.course.name} 비교에 추가`}
                      checked={selected.includes(item.course.id)}
                      disabled={
                        selected.length >= 3 &&
                        !selected.includes(item.course.id)
                      }
                      onChange={(event) =>
                        select(item.course.id, event.target.checked)
                      }
                    />
                    비교에 추가
                  </label>
                </div>
              </article>
            ))}
          </div>
          {run.items.length > 1 && (
            <div className="actions">
              <p>교육 2~3개를 선택하세요. 현재 {selected.length}개 선택</p>
              <button
                type="button"
                disabled={selected.length < 2}
                onClick={() => {
                  if (!validComparisonIds(selected)) {
                    setComparisonError(
                      '선택한 교육 정보를 확인할 수 없어 비교하지 못했어요. 다른 교육을 선택해 주세요.',
                    )
                    return
                  }
                  setComparisonError('')
                  setCompareIds([...selected])
                }}
              >
                선택한 교육 비교
              </button>
              <button
                type="button"
                disabled={selected.length === 0}
                onClick={() => {
                  setSelected([])
                  setCompareIds([])
                  setComparisonError('')
                }}
              >
                선택 초기화
              </button>
            </div>
          )}
          {comparisonError && <p role="alert">{comparisonError}</p>}
          {compareIds.length >= 2 && (
            <CompareCourses
              ids={compareIds}
              onClose={() => setCompareIds([])}
            />
          )}
          {detailId && (
            <div className="stack">
              <button type="button" onClick={() => setDetailId(null)}>
                상세 닫기
              </button>
              <CourseDetails key={detailId} courseId={detailId} />
            </div>
          )}
        </>
      )}
    </section>
  )
}

export function SavedCourses() {
  const remote = useRemote('/me/saved-courses', savedList)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  return (
    <section className="stack" aria-label="관심 교육">
      <h1>관심 교육</h1>
      {notice && <p role="status">{notice}</p>}
      <LoadMessage {...remote} />
      {remote.data?.items.length === 0 && <p>저장한 교육이 없습니다.</p>}
      {remote.data?.items.map((course) => (
        <article className="panel stack" key={course.id}>
          <h2>{course.name}</h2>
          <p>{course.provider_name ?? '교육기관 미확인'}</p>
          <SourceLink url={course.detail_url} label="교육 원문 보기" />
          <div className="actions">
            <button type="button" onClick={() => setDetailId(course.id)}>
              교육 상세
            </button>
            <SaveCourse
              courseId={course.id}
              courseName={course.name}
              initiallySaved
              onChange={(notice) => {
                setNotice(notice)
                setDetailId(null)
                remote.retry()
              }}
            />
          </div>
        </article>
      ))}
      {detailId && (
        <div className="stack">
          <button type="button" onClick={() => setDetailId(null)}>
            상세 닫기
          </button>
          <CourseDetails key={detailId} courseId={detailId} />
        </div>
      )}
    </section>
  )
}
