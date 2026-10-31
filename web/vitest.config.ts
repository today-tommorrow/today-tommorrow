import { defineConfig } from 'vitest/config'
import { loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, '..', ''), ...process.env }
  return {
    envDir: '..',
    test: {
      environment: 'jsdom',
      environmentOptions: {
        jsdom: { url: `http://127.0.0.1:${Number(env.WEB_PORT || 5173)}/` },
      },
      testTimeout: 15000,
    },
  }
})
