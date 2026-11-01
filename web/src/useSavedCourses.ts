import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, request } from './api'
import type { SavedCoursePage } from './types'

function validate(page: SavedCoursePage) {
  if (
    !page ||
    !Array.isArray(page.items) ||
    typeof page.catalog_version_id !== 'string' ||
    !(
      page.next_cursor === null ||
      (typeof page.next_cursor === 'string' && page.next_cursor.length > 0)
    ) ||
    page.items.some(
      (course) =>
        !course ||
        course.is_demo !== false ||
        typeof course.id !== 'string' ||
        typeof course.name !== 'string' ||
        !['work24', 'kmooc'].includes(course.source_system) ||
        !['listing_only', 'detail_verified'].includes(course.evidence_level) ||
        typeof course.saved_catalog_version_id !== 'string' ||
        !['current', 'changed', 'unavailable'].includes(course.catalog_status),
    )
  )
    throw new Error('관심 교육 목록 응답 형식을 확인할 수 없습니다.')
  return page
}

export function useSavedCourses() {
  const [data, setData] = useState<SavedCoursePage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const operation = useRef<AbortController | null>(null)
  const retryPage = useRef<{ cursor: string | null; version: string | null }>({
    cursor: null,
    version: null,
  })

  const load = useCallback(
    async (cursor: string | null, version: string | null) => {
      if (operation.current) return
      const controller = new AbortController()
      operation.current = controller
      retryPage.current = { cursor, version }
      setLoading(true)
      setError(null)
      if (cursor === null) setData(null)
      try {
        const page = validate(
          await request<SavedCoursePage>(
            `/me/saved-courses${cursor === null ? '' : `?cursor=${encodeURIComponent(cursor)}`}`,
            { signal: controller.signal },
          ),
        )
        if (controller.signal.aborted) return
        if (version !== null && page.catalog_version_id !== version) {
          throw new ApiError(
            '교육 목록이 갱신되었습니다. 처음부터 다시 불러와 주세요.',
            409,
          )
        }
        if (cursor !== null && page.next_cursor === cursor) {
          throw new Error('다음 관심 교육 목록을 확인할 수 없습니다.')
        }
        setData((previous) => {
          const items = cursor === null ? [] : (previous?.items ?? [])
          return {
            ...page,
            items: [
              ...new Map(
                [...items, ...page.items].map((item) => [item.id, item]),
              ).values(),
            ],
          }
        })
      } catch (cause: unknown) {
        if (controller.signal.aborted) return
        if (cause instanceof ApiError && cause.status === 409) {
          setData(null)
          retryPage.current = { cursor: null, version: null }
          setError('교육 목록이 갱신되었습니다. 처음부터 다시 불러와 주세요.')
        } else {
          setError(
            cause instanceof Error
              ? cause.message
              : '관심 교육을 불러오지 못했습니다.',
          )
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          operation.current = null
        }
      }
    },
    [],
  )

  useEffect(() => {
    void load(null, null)
    return () => {
      operation.current?.abort()
      operation.current = null
    }
  }, [load])

  return {
    data,
    loading,
    error,
    refresh: () => {
      operation.current?.abort()
      operation.current = null
      void load(null, null)
    },
    more: () => {
      if (data?.next_cursor)
        void load(data.next_cursor, data.catalog_version_id)
    },
    retry: () => {
      const target = error ? retryPage.current : { cursor: null, version: null }
      operation.current?.abort()
      operation.current = null
      void load(target.cursor, target.version)
    },
  }
}
