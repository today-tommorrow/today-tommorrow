import { useEffect, useRef, useState } from 'react'
import { request } from './api'
import type {
  CourseDetail,
  CourseSummary,
  Evidence,
  Offering,
  RecommendationRun,
} from './types'

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
  run.items.forEach((item) => {
    realCourse(item.course)
    if (
      !Array.isArray(item.reasons) ||
      !Array.isArray(item.evidence) ||
      !Array.isArray(item.missing_fields)
    ) {
      throw new Error('추천 근거 응답 형식을 확인할 수 없습니다.')
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
          {item.quote && <blockquote>{item.quote}</blockquote>}
          <p>
            {item.source_system} · {item.verification_state} · {item.checked_at}
          </p>
          <SourceLink url={item.source_url} label="근거 원문 보기" />
        </li>
      ))}
    </ul>
  )
}

function SaveCourse({
  courseId,
  initiallySaved = false,
  onChange,
}: {
  courseId: string
  initiallySaved?: boolean
  onChange?: () => void
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
        setNotice(
          saved ? '관심 교육에서 해제했습니다.' : '관심 교육에 저장했습니다.',
        )
        onChange?.()
      }
    } catch (error: unknown) {
      if (!controller.signal.aborted) setError(message(error))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
      operation.current = null
    }
  }
  return (
    <div>
      <button type="button" disabled={busy} onClick={() => void toggle()}>
        {busy ? '처리 중…' : saved ? '관심 교육 해제' : '관심 교육 저장'}
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
      {course.missing_fields.length > 0 && (
        <p>미확인 항목: {course.missing_fields.join(', ')}</p>
      )}
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

function CompareCourses({ ids }: { ids: string[] }) {
  const remote = useRemote(
    `/courses/compare?${new URLSearchParams({ ids: ids.join(',') })}`,
    comparison,
  )
  const data = remote.data
  const matches =
    data &&
    data.items.length === ids.length &&
    new Set(data.items.map((course) => course.id)).size === ids.length &&
    data.items.every((course) => ids.includes(course.id))
  return (
    <section className="panel stack" aria-label="교육 비교 결과">
      <h2>선택한 교육 비교</h2>
      <LoadMessage {...remote} />
      {data && !matches && (
        <p role="alert">선택한 교육과 비교 결과가 다릅니다.</p>
      )}
      {matches && data && (
        <div className="comparison-grid">
          {data.items.map((course) => (
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
  const run = remote.data
  const resultNames = {
    matches: '확인된 조건에 맞는 교육',
    unknown_conditions: '일부 조건 확인이 필요한 교육',
    empty: '조건에 맞는 교육을 찾지 못했습니다.',
    unsupported_field: '현재 제공되는 교육 분야에 포함되지 않습니다.',
  }
  function select(id: string, checked: boolean) {
    setCompareIds([])
    setSelected((current) =>
      checked
        ? [...new Set([...current, id])].slice(0, 3)
        : current.filter((item) => item !== id),
    )
  }
  return (
    <section className="stack" aria-label="추천 결과">
      <h1>나에게 맞는 다음 배움</h1>
      <LoadMessage {...remote} />
      {run && run.id !== runId && (
        <p role="alert">요청한 추천 결과와 다릅니다.</p>
      )}
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
                {item.missing_fields.length > 0 && (
                  <p>미확인 항목: {item.missing_fields.join(', ')}</p>
                )}
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
                  <SaveCourse courseId={item.course.id} />
                  <label>
                    <input
                      type="checkbox"
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
                onClick={() => setCompareIds([...selected])}
              >
                선택한 교육 비교
              </button>
            </div>
          )}
          {compareIds.length >= 2 && <CompareCourses ids={compareIds} />}
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
  return (
    <section className="stack" aria-label="관심 교육">
      <h1>관심 교육</h1>
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
              initiallySaved
              onChange={() => {
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
