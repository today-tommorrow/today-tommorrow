import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { App } from './App'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('실제 health 요청 결과 표시', () => {
  it('로딩 후 성공을 표시한다', async () => {
    const fetchHealth = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchHealth)
    render(<App />)
    expect(screen.getByRole('status').textContent).toContain(
      '확인하고 있습니다',
    )
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('연결 성공'),
    )
    expect(fetchHealth).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('503을 실패로 표시하고 재시도 성공을 반영한다', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: false,
          json: async () => ({ detail: 'database_unavailable' }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ status: 'ok' }),
        }),
    )
    render(<App />)
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('연결 실패'),
    )
    fireEvent.click(screen.getByRole('button', { name: '다시 확인' }))
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('연결 성공'),
    )
  })
})
