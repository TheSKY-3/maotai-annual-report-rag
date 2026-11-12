import { useRef, useState } from 'react'
import { answerNumbers, streamAsk, type AskMeta, type StreamEvent } from './api'
import SourceCard from './components/SourceCard'

interface Msg {
  role: 'user' | 'assistant'
  text: string
  meta?: AskMeta
  error?: string
  status?: string
}

const PRESETS = [
  '2024年茅台营业总收入是多少？',
  '茅台2024年度分红方案是什么？',
  '2024年末国内经销商有多少家？',
  'i茅台渠道2024年收入是多少？',
  '2025年营业总收入比2022年增长了多少？',
  '五粮液2024年的营业收入是多少？',
]
const YEARS = [2022, 2023, 2024, 2025]

export default function App() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [year, setYear] = useState<number | null>(null)
  const [forceAgent, setForceAgent] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const patchLast = (fn: (m: Msg) => Msg) =>
    setMsgs(prev => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)))

  async function send(override?: string) {
    const q = (override ?? input).trim()
    if (!q || busy) return
    const history = msgs.map(m => ({ role: m.role, content: m.text })).slice(-6)
    setInput('')
    setBusy(true)
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setMsgs(prev => [...prev,
      { role: 'user', text: q },
      { role: 'assistant', text: '', status: '检索中…' }])
    const opts = {
      year: year ?? undefined,
      api_key: apiKey.trim() || undefined,
      agent: forceAgent || undefined,
      history: history.length ? history : undefined,
    }
    try {
      await streamAsk(q, (e: StreamEvent) => {
        if (e.event === 'status') patchLast(m => ({ ...m, status: e.data.label }))
        else if (e.event === 'delta')
          patchLast(m => ({ ...m, text: m.text + e.data.text, status: undefined }))
        else if (e.event === 'replace')
          patchLast(m => ({ ...m, text: e.data.text, status: undefined }))
        else if (e.event === 'done')
          patchLast(m => ({ ...m, meta: e.data.meta, status: undefined }))
        else if (e.event === 'error')
          patchLast(m => ({ ...m, error: e.data.message, status: undefined }))
      }, ac.signal, opts)
    } catch (err) {
      if (!ac.signal.aborted)
        patchLast(m => ({ ...m, error: String(err), status: undefined }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="shell">
      <header>
        <h1>茅台年报问答</h1>
        <p className="tagline">贵州茅台 2022–2025 年报 · 答案附原文页码与坐标高亮 · 查不到就明说</p>
      </header>
      <div className="toolbar">
        <button onClick={() => setShowSettings(s => !s)}>
          {showSettings ? '收起设置' : '设置'}
        </button>
        <button onClick={() => setMsgs([])} disabled={busy || msgs.length === 0}>
          清空
        </button>
      </div>
      {showSettings && (
        <div className="settings">
          <label className="field">
            DeepSeek API Key
            <input
              type="password"
              value={apiKey}
              placeholder="sk-…（留空则用服务端环境变量）"
              onChange={e => setApiKey(e.target.value)}
            />
          </label>
          <span className={`pill${apiKey.trim() ? '' : ' off'}`}>
            {apiKey.trim() ? '已填 Key · 走云端 DeepSeek' : '未填 Key · 用服务端默认'}
          </span>
          <label className="field">
            检索范围
            <select
              value={year ?? ''}
              onChange={e => setYear(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">全部年份</option>
              {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={forceAgent}
              onChange={e => setForceAgent(e.target.checked)}
            />
            强制 Agent
          </label>
        </div>
      )}
      <main className="thread">
        {msgs.length === 0 && (
          <div className="presets">
            <p className="empty">问一个年报里的问题，或点下面的推荐问题：</p>
            {PRESETS.map(p => (
              <button key={p} className="preset" onClick={() => void send(p)} disabled={busy}>
                {p}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <article key={i} className={`msg ${m.role}`}>
            {m.role === 'user' ? (
              <p className="bubble">{m.text}</p>
            ) : (
              <>
                {m.status && <p className="status">{m.status}</p>}
                {m.meta?.refused && <span className="badge-refused">拒答</span>}
                {m.text && <p className="answer">{m.text}</p>}
                {m.error && <p className="error">{m.error}</p>}
                {m.meta && (
                  <p className="meta">
                    {m.meta.route} · {m.meta.latency_ms ?? '—'}ms · ¥{m.meta.cost_cny ?? '—'}
                  </p>
                )}
                {m.meta?.steps && m.meta.steps.length > 0 && (
                  <ul className="steps">
                    {m.meta.steps.map((s, j) => (
                      <li key={j}>{s.tool} · {s.n_hits} 处</li>
                    ))}
                  </ul>
                )}
                {m.meta?.sources.map((s, i) => (
                  <SourceCard
                    key={`${s.year}-${s.page}`}
                    source={s}
                    numbers={answerNumbers(m.meta!.answer)}
                    defaultOpen={i === 0}
                  />
                ))}
              </>
            )}
          </article>
        ))}
      </main>
      <footer className="composer">
        <textarea
          value={input}
          placeholder="例如：2024年茅台营业总收入是多少？"
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void send()
            }
          }}
        />
        <button onClick={() => void send()} disabled={busy || !input.trim()}>
          发送
        </button>
      </footer>
    </div>
  )
}