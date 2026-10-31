import { Component, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, apiUrl, request, setCsrfToken } from './api'
import type { AuthSession, Profile } from './types'
import { Intake } from './Intake'
import { Results, SavedCourses } from './Courses'

function HealthStatus() {
  const [message, setMessage] = useState('연결을 확인해주세요.')
  const [busy, setBusy] = useState(false)
  return <details className="health"><summary>연결 확인</summary><p role="status">{message}</p><button disabled={busy} onClick={async () => {
    setBusy(true)
    try {
      const data = await request<{ status: string }>('/health')
      setMessage(data.status === 'ok' ? 'API와 데이터베이스 연결 성공' : '연결 상태를 확인할 수 없습니다.')
    } catch { setMessage('서버에 연결하지 못했습니다.') } finally { setBusy(false) }
  }}>연결 확인하기</button></details>
}

class ScreenBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <section className="panel"><h2>화면을 표시하지 못했습니다</h2><p>서버 응답을 확인할 수 없습니다. 저장된 입력은 서버에 유지됩니다.</p><button onClick={() => window.location.reload()}>다시 불러오기</button></section> : this.props.children
  }
}

function Workspace({ session }: { session: AuthSession }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [route, setRoute] = useState(() => new URLSearchParams(window.location.search))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const key = `today-tommorrow:profile:${session.user.id}`
  const active = useRef(true)
  const creation = useRef<AbortController | null>(null)
  useEffect(() => {
    active.current = true
    const onPop = () => { creation.current?.abort(); creation.current = null; setBusy(false); setProfile(null); setError(''); setRoute(new URLSearchParams(window.location.search)) }
    window.addEventListener('popstate', onPop)
    return () => { active.current = false; creation.current?.abort(); window.removeEventListener('popstate', onPop) }
  }, [])
  const navigate = (query: string) => {
    creation.current?.abort(); creation.current = null
    setProfile(null); setBusy(false)
    window.history.pushState(null, '', `/onboarding${query}`)
    setRoute(new URLSearchParams(query))
    setError('')
  }
  const storeProfile = (value: Profile) => {
    if (!value || typeof value.id !== 'string' || !value.intake_state || !Array.isArray(value.documents) || !Number.isInteger(value.revision)) throw new ApiError('입력 상태 응답이 올바르지 않습니다.', 502)
    if (!active.current) return
    setProfile(value)
    try { sessionStorage.setItem(key, value.id) } catch { /* Resume remains available through the current URL. */ }
  }
  useEffect(() => {
    const id = route.get('profile')
    if (!id || route.has('run') || route.has('saved')) return
    const controller = new AbortController()
    setBusy(true); setError(''); setProfile(null)
    request<Profile>(`/profiles/${encodeURIComponent(id)}`, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return
      if (value.id !== id) throw new ApiError('요청한 입력과 서버 응답이 다릅니다.', 502)
      storeProfile(value)
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '입력을 불러오지 못했습니다.')
    }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
    // A session change remounts this entire workspace.
  }, [route])
  const create = async () => {
    if (creation.current) return
    const controller = new AbortController()
    creation.current = controller
    setBusy(true); setError('')
    try {
      const result = await request<Profile>('/profiles', { method: 'POST', signal: controller.signal })
      if (active.current && !controller.signal.aborted) { storeProfile(result); navigate(`?profile=${encodeURIComponent(result.id)}`) }
    } catch (e) { if (active.current && !controller.signal.aborted) setError(e instanceof Error ? e.message : '초안을 만들지 못했습니다.') }
    finally { if (creation.current === controller) { creation.current = null; if (active.current) setBusy(false) } }
  }
  let remembered: string | null = null
  try { remembered = sessionStorage.getItem(key) } catch { /* Storage may be unavailable. */ }
  const runId = route.get('run')
  return <div className="stack">
    <nav className="actions" aria-label="내 학습"><button onClick={() => navigate('')}>시작 화면</button><button onClick={() => navigate('?saved=1')}>관심 교육</button></nav>
    {error && <p role="alert" className="error">{error} <button onClick={() => setRoute(new URLSearchParams(route))}>다시 불러오기</button></p>}
    {busy && <p role="status">입력 상태를 확인하고 있습니다…</p>}
    {runId ? <Results key={runId} runId={runId} /> : route.has('saved') ? <SavedCourses /> : profile && profile.id === route.get('profile') ? <Intake key={profile.id} profile={profile} onProfile={storeProfile} onRun={(id) => navigate(`?run=${encodeURIComponent(id)}`)} /> : !route.has('profile') && <section className="panel stack">
      <p className="eyebrow">나에게 맞는 다음 배움</p><h1>어떤 배움이 필요한지,<br />함께 찾아볼까요?</h1><p>질문에 답하며 방향을 정하고, 확인한 정보로 교육을 찾아보세요. 아직 정하지 못한 답도 괜찮아요.</p>
      <div className="actions"><button className="primary" disabled={busy} onClick={() => void create()}>새로 시작하기</button>{remembered && <button disabled={busy} onClick={() => navigate(`?profile=${encodeURIComponent(remembered)}`)}>이 브라우저에서 작성하던 입력 이어가기</button>}</div>
      <p className="muted">입력은 계정에 저장됩니다. 이 브라우저에 남은 초안 링크로 이어갈 수 있습니다.</p>
    </section>}
  </div>
}

export function App() {
  const [session, setSession] = useState<AuthSession | null>(null)
  const [state, setState] = useState<'loading' | 'anonymous' | 'ready' | 'error'>('loading')
  const [message, setMessage] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [loggingOut, setLoggingOut] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setState('loading'); setSession(null); setCsrfToken(null)
    const expire = () => { setSession(null); setCsrfToken(null); setState('anonymous'); setMessage('로그인이 필요합니다. 저장한 입력은 로그인 후 다시 확인할 수 있어요.') }
    window.addEventListener('session-expired', expire)
    void request<AuthSession>('/auth/me', { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return
      if (!value?.user?.id || !value.csrf_token || !Number.isFinite(Date.parse(value.expires_at))) throw new ApiError('로그인 응답을 확인할 수 없습니다.', 502)
      if (Date.parse(value.expires_at) <= Date.now()) { expire(); return }
      setCsrfToken(value.csrf_token); setSession(value); setState('ready'); setMessage('')
    }).catch((e: unknown) => {
      if (controller.signal.aborted) return
      if (e instanceof ApiError && e.status === 401) { setState('anonymous'); return }
      setState('error'); setMessage(e instanceof Error ? e.message : '로그인 상태를 확인하지 못했습니다.')
    })
    return () => { controller.abort(); window.removeEventListener('session-expired', expire) }
  }, [attempt])
  useEffect(() => {
    if (!session) return
    const delay = Math.min(2147483647, Math.max(0, Date.parse(session.expires_at) - Date.now()))
    const timer = window.setTimeout(() => setAttempt(n => n + 1), delay)
    return () => window.clearTimeout(timer)
  }, [session])
  const cancelled = new URLSearchParams(window.location.search).get('error') === 'oauth_cancelled'
  return <><header className="site-header"><a className="brand" href="/">다음<span>오늘에서 내일로</span></a>{session && <div className="actions"><span>{session.user.display_name || '내 계정'}</span><button disabled={loggingOut} onClick={async () => {
    setLoggingOut(true)
    try { await request<void>('/auth/logout', { method: 'POST' }); setCsrfToken(null); setSession(null); setState('anonymous'); window.history.replaceState(null, '', '/login') }
    catch (e) { setMessage(e instanceof Error ? e.message : '로그아웃하지 못했습니다.') }
    finally { setLoggingOut(false) }
  }}>로그아웃</button></div>}</header>
    <main className="page"><ScreenBoundary key={session?.user.id || 'anonymous'}>
      {state === 'loading' && <p role="status">로그인 상태를 확인하고 있습니다…</p>}
      {state !== 'ready' && state !== 'loading' && <section className="panel login stack"><p className="eyebrow">당신의 다음 배움</p><h1>오늘의 관심을<br />내일의 가능성으로.</h1><p>나의 방향과 경험에 맞는 교육을 찾아보세요.</p>{cancelled && <p role="status">Google 로그인을 취소했어요. 준비되면 다시 시작해주세요.</p>}{message && <p role="alert" className="error">{message}</p>}{state === 'anonymous' && <a className="button primary" href={apiUrl('/auth/google/start')}>Google로 시작하기</a>}<button onClick={() => setAttempt(n => n + 1)}>로그인 상태 다시 확인</button></section>}
      {state === 'ready' && session && <>{message && <p role="alert">{message}</p>}<Workspace key={session.user.id} session={session} /></>}
    </ScreenBoundary><HealthStatus /></main><footer>다음 · 확인한 정보로 찾는 배움</footer></>
}
