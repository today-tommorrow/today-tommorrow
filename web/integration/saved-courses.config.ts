import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [
    {
      name: 'expose-save-for-live-test',
      enforce: 'pre',
      transform(code, id) {
        if (id.endsWith('/src/Courses.tsx'))
          return code.replace(
            'function SaveCourse({',
            'export function SaveCourse({',
          )
      },
    },
    react(),
  ],
  test: {
    include: ['integration/saved-courses.live.tsx'],
    environment: 'jsdom',
    environmentOptions: { jsdom: { url: 'http://127.0.0.1:5173/' } },
    testTimeout: 30000,
  },
})
