import { useEffect, useState } from 'react'

type HealthState = 'loading' | 'success' | 'failure'

export function App() {
  const [state, setState] = useState<HealthState>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 8000)
    setState('loading')
    async function checkHealth() {
      try {
        const response = await fetch('/api/health', {
          signal: controller.signal,
        })
        const data: unknown = await response.json()
        if (
          !response.ok ||
          typeof data !== 'object' ||
          data === null ||
          !('status' in data) ||
          data.status !== 'ok'
        ) {
          throw new Error('Health check failed')
        }
        if (!cancelled) setState('success')
      } catch {
        if (!cancelled) setState('failure')
      } finally {
        window.clearTimeout(timeout)
      }
    }
    let cancelled = false
    void checkHealth()
    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timeout)
    }
  }, [attempt])

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-20 text-slate-900">
      <section className="mx-auto max-w-lg rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <p className="text-sm font-semibold text-indigo-600">
          TODAY · TOMMORROW
        </p>
        <h1 className="mt-3 text-3xl font-bold">개발 환경 연결 확인</h1>
        <p className="mt-4 text-slate-600">
          웹에서 API와 PostgreSQL의 실제 연결을 확인합니다.
        </p>
        <p
          role="status"
          aria-live="polite"
          className="mt-8 rounded-lg bg-slate-100 p-4"
        >
          {state === 'loading' && '연결을 확인하고 있습니다…'}
          {state === 'success' &&
            '연결 성공 · API와 데이터베이스가 준비됐습니다.'}
          {state === 'failure' &&
            '연결 실패 · API와 데이터베이스 실행 상태를 확인해주세요.'}
        </p>
        <button
          className="mt-6 rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          disabled={state === 'loading'}
          onClick={() => setAttempt((value) => value + 1)}
        >
          다시 확인
        </button>
      </section>
    </main>
  )
}
