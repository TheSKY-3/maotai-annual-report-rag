import { useState } from 'react'
import { cropUrl, type Source } from '../api'

export default function SourceCard({ source, numbers, defaultOpen = false }: {
  source: Source
  numbers: string[]
  defaultOpen?: boolean
}) {
  const [broken, setBroken] = useState(false)
  return (
    <details className="source-card" open={defaultOpen}>
      <summary>出处 · {source.year}年报 P{source.page}</summary>
      {numbers.length > 0 && !broken && (
        <>
          <p className="crop-tag">坐标核验 · 红框 = 答案引用的数字</p>
          <img
            className="source-crop"
            src={cropUrl(source.year, source.page, numbers)}
            alt={`坐标高亮：${source.year}年报 P{source.page}`}
            loading="lazy"
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