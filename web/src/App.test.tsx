import { afterEach, describe, expect, it } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { ApiError, request } from './api'
import { App } from './App'

afterEach(cleanup)

// These tests require the real Vite proxy, FastAPI process and PostgreSQL
// started by GitHub CI. No network interception or generated records.
describe('실제 API 연결', () => {
  it('Vite 프록시를 통해 실제 PostgreSQL health 결과를 받는다', async () => {
    const response = await request<{ status: string }>('/health')
    expect(response.status).toBe('ok')
  })

  it('실제 인증 API가 세션 없는 요청에 401을 반환한다', async () => {
    try {
      await request('/auth/me')
      throw new Error('An unauthenticated request must not return a session')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(401)
    }
  })

  it('인증 API의 실제 401에서는 Google 로그인 링크를 제공한다', async () => {
    render(<App />)
    await waitFor(
      () => {
        expect(
          screen.getByRole('link', { name: 'Google로 시작하기' }),
        ).toBeTruthy()
      },
      { timeout: 10000 },
    )
    expect(
      new URL(
        screen
          .getByRole('link', { name: 'Google로 시작하기' })
          .getAttribute('href')!,
      ).pathname,
    ).toBe('/api/auth/google/start')
    expect(screen.getByRole('alert').textContent).toBe(
      '로그인이 필요합니다. 저장한 입력은 로그인 후 다시 확인할 수 있어요.',
    )
    expect(screen.queryByRole('button', { name: '로그아웃' })).toBeNull()
  })

  it('화면의 연결 확인 버튼으로 실제 API와 DB를 확인한다', async () => {
    render(<App />)
    fireEvent.click(screen.getByText('연결 확인'))
    fireEvent.click(screen.getByRole('button', { name: '연결 확인하기' }))
    await waitFor(
      () => {
        expect(screen.getByText('API와 데이터베이스 연결 성공')).toBeTruthy()
      },
      { timeout: 10000 },
    )
  })
})
