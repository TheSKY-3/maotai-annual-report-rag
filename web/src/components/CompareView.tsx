import { useState } from 'react'
import { answerNumbers, askOnce, YEARS, type AskMeta, type AskOpts } from '../api'
import AnswerText from './AnswerText'
import SourceCard from './SourceCard'

interface Result {
  status: 'loading' | 'done' | 'error'
  meta?: AskMeta
  error?: string
}

/** 双年对比：同一问题在两份年报内并排回答（与 Streamlit 对比模式同摆法）。 */
export default function CompareView({ backend, apiKey, forceAgent }: {
  backend: 'cloud' | 'ollama'
  apiKey: string
  forceAgent: boolean
}) {
  const [yearA, setYearA] = useState(YEARS[YEARS.length - 2])
  const [yearB, setYearB] = useState(YEARS[YEARS.length - 1])
  const [q, setQ] = useState('营业总收入是多少？主要经营情况如何？')
  const [results, setResults] = useState<[Result, Result] | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    const question = q.trim()
    if (!question || busy) return
    setBusy(true)
    setResults([{ status: 'loading' }, { status: 'loading' }])
    const opts: AskOpts = {
      backend,
      api_key: backend === 'cloud' && apiKey.trim() ? apiKey.trim() : undefined,
      agent: forceAgent || undefined,
    }
    const settled = await Promise.allSettled([
      askOnce(question, { ...opts, year: yearA }),
      askOnce(question, { ...opts, year: yearB }),
    ])
    setResults(settled.map(s => s.status === 'fulfilled'
      ? { status: 'done', meta: s.value }
      : { status: 'error', error: String(s.reason) }) as [Result, Result])
    setBusy(false)
  }

  const years = [yearA, yearB]
  return (
    <section className="compare">
      <div className="compare-controls">
        <label>年报 A
          <select value={yearA} onChange={e => setYearA(Number(e.target.value))}>
            {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        <label>年报 B
          <select value={yearB} onChange={e => setYearB(Number(e.target.value))}>
            {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        <label className="compare-q">问题
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="同一问题，两份年报并排回答"
          />
        </label>
        <button className="compare-run" onClick={() => void run()} disabled={busy || !q.trim()}>
          {busy ? '对比中…' : '开始对比'}
        </button>
      </div>

      {!results && (
        <p className="compare-hint">同一问题在两份年报内并排回答，各自附出处与坐标核验；年份可任选。</p>
      )}

      {results && (
        <div className="compare-grid">
          {results.map((r, i) => (
            <article className="compare-col" key={i}>
              <header className="compare-year">
                <span className="year-seal">{years[i]}</span>年报
              </header>
              {r.status === 'loading' && <p className="compare-loading">检索并生成中</p>}
              {r.status === 'error' && <p className="error">{r.error}</p>}
              {r.status === 'done' && r.meta && (
                <>
                  {r.meta.refused && <span className="badge-refused">拒答</span>}
                  <p className="answer"><AnswerText text={r.meta.answer} /></p>
                  <p className="meta">
                    <span className={`route-tag${r.meta.route === 'agent' ? ' agent' : ''}`}>
                      {r.meta.route === 'agent' ? 'Agent 工具循环' : '直通检索'}
                    </span>
                    {r.meta.latency_ms ?? '—'}ms · ¥{r.meta.cost_cny ?? '—'}
                  </p>
                  {r.meta.sources.map((s, k) => (
                    <SourceCard
                      key={`${s.year}-${s.page}`}
                      source={s}
                      numbers={answerNumbers(r.meta!.answer)}
                      defaultOpen={k === 0}
                      index={k}
                    />
                  ))}
                </>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}