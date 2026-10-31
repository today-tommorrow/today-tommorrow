// @vitest-environment node
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const configPath = fileURLToPath(new URL('./vitest.config.ts', import.meta.url))
const viteUrl = new URL(
  './node_modules/vite/dist/node/index.js',
  import.meta.url,
)

function configuredRequestSettings(envFile = '', webPort?: string) {
  const root = mkdtempSync(join(tmpdir(), 'today-tommorrow-vitest-'))
  const web = join(root, 'web')
  mkdirSync(web)
  writeFileSync(join(root, '.env'), envFile)
  const env = { ...process.env }
  delete env.WEB_PORT
  delete env.VITE_API_BASE_URL
  if (webPort !== undefined) env.WEB_PORT = webPort
  try {
    const output = execFileSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        `import { loadConfigFromFile, resolveConfig } from ${JSON.stringify(viteUrl.href)};
         const { config } = await loadConfigFromFile(
           { command: 'serve', mode: 'test' }, ${JSON.stringify(configPath)}
         );
         const resolved = await resolveConfig({ ...config, configFile: false }, 'serve', 'test');
         console.log(JSON.stringify({
           origin: config.test.environmentOptions.jsdom.url,
           apiBase: resolved.env.VITE_API_BASE_URL,
         }));`,
      ],
      { cwd: web, env, encoding: 'utf8' },
    ).trim()
    return JSON.parse(output) as { origin: string; apiBase?: string }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

describe('테스트 API 요청의 웹 origin', () => {
  it('포트 설정이 없으면 기본 개발 서버를 사용한다', () => {
    expect(configuredRequestSettings().origin).toBe('http://127.0.0.1:5173/')
  })

  it('루트 .env의 WEB_PORT를 사용한다', () => {
    expect(configuredRequestSettings('WEB_PORT=5273\n').origin).toBe(
      'http://127.0.0.1:5273/',
    )
  })

  it('프로세스 WEB_PORT가 루트 .env보다 우선한다', () => {
    expect(configuredRequestSettings('WEB_PORT=5273\n', '6373').origin).toBe(
      'http://127.0.0.1:6373/',
    )
  })

  it('공개 API 기본 경로도 루트 .env에서 읽는다', () => {
    expect(
      configuredRequestSettings('VITE_API_BASE_URL=/custom/api\n').apiBase,
    ).toBe('/custom/api')
  })
})
