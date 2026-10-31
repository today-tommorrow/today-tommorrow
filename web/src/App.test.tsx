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

  it('현재 서버에 없는 인증 API를 로그인 성공으로 바꾸지 않는다', async () => {
    try {
      await request('/auth/me')
      throw new Error('The baseline server must not return a session')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(404)
    }
  })

  it('인증 API의 실제 404를 안내하고 로그인 링크를 노출하지 않는다', async () => {
    render(<App />)
    await waitFor(
      () => {
        expect(screen.getByRole('alert').textContent).toContain(
          '요청한 기능이나 자료를 서버에서 찾을 수 없습니다.',
        )
      },
      { timeout: 10000 },
    )
    expect(screen.queryByRole('link', { name: 'Google로 시작하기' })).toBeNull()
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
