import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { App } from './App'
import { setCsrfToken } from './api'
import type { AuthSession } from './types'

// Synthetic HTTP responses verify session UI, not a Google OAuth exchange.
function session(): AuthSession {
  return {
    user: {
      id: 'synthetic-user-1',
      display_name: '합성 계정',
      email: 'synthetic@example.com',
      avatar_url: null,
    },
    csrf_token: 'synthetic-csrf-token',
    expires_at: new Date(Date.now() + 3600000).toISOString(),
  }
}

function json(status: number, body: unknown = {}) {
  return Response.json(body, { status })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  sessionStorage.clear()
  setCsrfToken(null)
  window.history.replaceState(null, '', '/')
})

describe('로그인·취소·세션 종료 화면', () => {
  it('비로그인 401에서는 같은 API의 Google 로그인 링크를 제공한다', async () => {
    const fetch = vi.fn().mockResolvedValue(json(401))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    const login = await screen.findByRole('link', { name: 'Google로 시작하기' })
    expect(new URL(login.getAttribute('href')!).pathname).toBe(
      '/api/auth/google/start',
    )
    expect(new URL(fetch.mock.calls[0][0]).pathname).toBe('/api/auth/me')
    expect(fetch.mock.calls[0][1].credentials).toBe('include')
    expect(screen.queryByRole('button', { name: '로그아웃' })).toBeNull()
  })

  it('취소 콜백에서는 취소 안내와 다시 로그인할 방법을 제공한다', async () => {
    window.history.replaceState(null, '', '/login?error=oauth_cancelled')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(401)))
    render(<App />)
    await screen.findByRole('link', { name: 'Google로 시작하기' })
    expect(screen.getByText(/Google 로그인을 취소했어요/)).toBeTruthy()
  })

  it('세션 조회 장애를 비로그인으로 바꾸지 않고 다시 확인한다', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(503))
    fetch.mockImplementation(() => Promise.resolve(json(200, session())))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect((await screen.findByRole('alert')).textContent).toContain(
      '현재 서비스를 사용할 수 없습니다.',
    )
    expect(screen.queryByRole('link', { name: 'Google로 시작하기' })).toBeNull()
    fireEvent.click(
      screen.getByRole('button', { name: '로그인 상태 다시 확인' }),
    )
    expect(await screen.findByText('합성 계정')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('이미 만료된 세션 응답으로 개인 작업 화면을 열지 않는다', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          json(200, { ...session(), expires_at: '2020-01-01T00:00:00Z' }),
        ),
    )
    render(<App />)
    await screen.findByRole('link', { name: 'Google로 시작하기' })
    expect(screen.queryByRole('button', { name: '새로 시작하기' })).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain(
      '로그인이 필요합니다.',
    )
  })

  it('로그아웃은 쿠키·CSRF와 함께 POST하고 성공하면 개인 화면을 닫는다', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(200, session()))
    fetch.mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '로그아웃' }))
    await screen.findByRole('link', { name: 'Google로 시작하기' })

    const [url, options] = fetch.mock.calls[1]
    expect(new URL(url).pathname).toBe('/api/auth/logout')
    expect(options.method).toBe('POST')
    expect(options.credentials).toBe('include')
    expect(options.headers.get('X-CSRF-Token')).toBe('synthetic-csrf-token')
    expect(window.location.pathname).toBe('/login')
    expect(screen.queryByText('합성 계정')).toBeNull()
    expect(screen.queryByRole('button', { name: '새로 시작하기' })).toBeNull()
  })

  it('로그아웃 실패는 재시도할 수 있고 성공 후 이전 오류를 지운다', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(200, session()))
      .mockResolvedValueOnce(json(503))
      .mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '로그아웃' }))
    expect((await screen.findByRole('alert')).textContent).toContain(
      '현재 서비스를 사용할 수 없습니다.',
    )
    expect(screen.getByText('합성 계정')).toBeTruthy()
    await waitFor(() => {
      expect(
        screen
          .getByRole('button', { name: '로그아웃' })
          .hasAttribute('disabled'),
      ).toBe(false)
    })
    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }))
    await screen.findByRole('link', { name: 'Google로 시작하기' })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})
