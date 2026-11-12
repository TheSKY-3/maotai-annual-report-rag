import { useState } from 'react'
import { cropUrl, type Source } from '../api'

export default function SourceCard({ source, numbers }: {
  source: Source
  numbers: string[]
}) {
  const [broken, setBroken] = useState(false)
  return (
    <details className="source-card">
      <summary>出处 · {source.year}年报 P{source.page}</summary>
      {numbers.length > 0 && !broken && (
        <img
          className="source-crop"
          src={cropUrl(source.year, source.page, numbers)}
          alt={`坐标高亮：${source.year}年报 P${source.page}`}
          loading="lazy"
          onError={() => setBroken(true)}
        />
      )}
      {source.snippet && <p className="source-snippet">{source.snippet}</p>}
    </details>
  )
}