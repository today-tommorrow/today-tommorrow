import type { Job } from './types'

let csrfToken: string | null = null

export function setCsrfToken(value: string | null) {
  csrfToken = value
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function apiUrl(path: string) {
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  const url = new URL(base.replace(/\/$/, '') + path, window.location.origin)
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password
  ) {
    throw new Error('API 주소 설정을 확인해주세요.')
  }
  return url.href
}

type RequestOptions = {
  method?: string
  body?: unknown
  signal?: AbortSignal
  idempotencyKey?: string
}

const timeoutMessage =
  '응답 대기 시간이 초과됐습니다. 잠시 후 다시 확인해주세요.'

function assertRealData(value: unknown): void {
  if (!value || typeof value !== 'object') return
  if ('is_demo' in value && value.is_demo === true) {
    throw new ApiError('실제 교육 데이터가 아닌 응답은 표시하지 않습니다.', 502)
  }
  if ('runtime_mode' in value && value.runtime_mode === 'sample') {
    throw new ApiError('실제 모델 결과가 아닌 응답은 표시하지 않습니다.', 502)
  }
  for (const child of Object.values(value)) assertRealData(child)
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method || 'GET'
  const headers = new Headers({ Accept: 'application/json' })
  if (method !== 'GET' && method !== 'HEAD') {
    if (!csrfToken) throw new ApiError('다시 로그인해주세요.', 401)
    headers.set('X-CSRF-Token', csrfToken)
  }
  if (options.idempotencyKey)
    headers.set('Idempotency-Key', options.idempotencyKey)
  const form = options.body instanceof FormData
  if (options.body !== undefined && !form)
    headers.set('Content-Type', 'application/json')
  const timeout = AbortSignal.timeout(30000)
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeout])
    : timeout
  const checkAborted = () => {
    options.signal?.throwIfAborted()
    if (timeout.aborted) throw new ApiError(timeoutMessage, 408)
  }
  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      credentials: 'include',
      cache: 'no-store',
      signal,
      body:
        options.body === undefined
          ? undefined
          : form
            ? (options.body as FormData)
            : JSON.stringify(options.body),
    })
  } catch {
    checkAborted()
    throw new ApiError(
      '서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.',
      0,
    )
  }
  checkAborted()
  if (response.status === 401) {
    csrfToken = null
    window.dispatchEvent(new Event('session-expired'))
  }
  if (!response.ok) {
    let requestId: string | undefined
    try {
      const error: unknown = await response.json()
      if (
        error &&
        typeof error === 'object' &&
        'request_id' in error &&
        typeof error.request_id === 'string'
      )
        requestId = error.request_id
    } catch {
      /* An error response need not have a JSON body. */
    }
    const messages: Record<number, string> = {
      401: '로그인이 필요합니다.',
      403: '요청 권한을 확인할 수 없습니다. 로그인 상태를 다시 확인해주세요.',
      404: '요청한 기능이나 자료를 서버에서 찾을 수 없습니다.',
      408: timeoutMessage,
      409: '입력이 변경되었습니다. 최신 상태를 확인한 뒤 다시 시도해주세요.',
      413: '파일이 서버에서 허용한 크기를 초과합니다.',
      422: '입력 내용을 확인해주세요.',
      429: '요청이 많습니다. 잠시 후 다시 시도해주세요.',
      503: '현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.',
    }
    throw new ApiError(
      messages[response.status] || '요청을 처리하지 못했습니다.',
      response.status,
      requestId,
    )
  }
  if (response.status === 204) return undefined as T
  let data: unknown
  try {
    data = await response.json()
  } catch {
    checkAborted()
    throw new ApiError('서버 응답을 읽을 수 없습니다.', 502)
  }
  checkAborted()
  assertRealData(data)
  return data as T
}

export async function waitForJob(
  id: string,
  signal: AbortSignal,
  onUpdate: (job: Job) => void,
  pollAfterMs = 1500,
): Promise<Job> {
  const delay = Math.max(1000, Math.min(pollAfterMs || 1500, 15000))
  while (!signal.aborted) {
    const job = await request<Job>(`/jobs/${encodeURIComponent(id)}`, {
      signal,
    })
    if (
      !job ||
      job.id !== id ||
      ![
        'queued',
        'running',
        'succeeded',
        'failed',
        'cancelled',
        'superseded',
      ].includes(job.status)
    )
      throw new ApiError('작업 상태 응답이 올바르지 않습니다.', 502)
    onUpdate(job)
    if (job.status === 'succeeded') return job
    if (['failed', 'cancelled', 'superseded'].includes(job.status)) {
      throw new ApiError(
        job.status === 'superseded'
          ? '입력 변경으로 작업이 만료되었습니다.'
          : job.status === 'cancelled'
            ? '서버 작업이 취소됐습니다. 현재 입력을 확인해주세요.'
            : '작업이 완료되지 않았습니다. 현재 입력을 확인해주세요.',
        job.status === 'superseded' ? 409 : 503,
      )
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      const timer = window.setTimeout(() => {
        signal.removeEventListener('abort', abort)
        resolve()
      }, delay)
      signal.addEventListener('abort', abort, { once: true })
      if (signal.aborted) abort()
    })
  }
  throw new DOMException('Aborted', 'AbortError')
}
