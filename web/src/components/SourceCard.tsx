import { useState } from 'react'
import { cropUrl, type Source } from '../api'

export default function SourceCard({ source, numbers, defaultOpen = false, index = 0 }: {
  source: Source
  numbers: string[]
  defaultOpen?: boolean
  index?: number
}) {
  const [broken, setBroken] = useState(false)
  const badge = source.direct ? '页码直达' : index === 0 ? '最佳匹配' : ''
  return (
    <details className="source-card" open={defaultOpen}>
      <summary>
        <span>证据 {index + 1} · {source.year}年报 P{source.page}</span>
        {badge && (
          <span className={source.direct ? 'badge-direct' : 'badge-best'}>{badge}</span>
        )}
      </summary>
      {numbers.length > 0 && !broken && (
        <>
          <p className="crop-tag">坐标核验 · 红框 = 答案引用的数字</p>
          <img
            className="source-crop"
            src={cropUrl(source.year, source.page, numbers)}
            alt={`坐标高亮：${source.year}年报 P{source.page}`}
            loading={defaultOpen ? 'eager' : 'lazy'}
            onError={() => setBroken(true)}
          />
          <p className="crop-caption">{source.year}年报 P{source.page} 原文横带</p>
        </>
      )}
      {broken && <p className="crop-missing">坐标高亮加载失败：该页未定位到被引数字</p>}
      {source.snippet && <p className="source-snippet">{source.snippet}</p>}
    </details>
  )
}