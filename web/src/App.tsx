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

export default function App() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const patchLast = (fn: (m: Msg) => Msg) =>
    setMsgs(prev => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)))

  async function send() {
    const q = input.trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setMsgs(prev => [...prev,
      { role: 'user', text: q },
      { role: 'assistant', text: '', status: '检索中…' }])
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
      }, ac.signal)
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
      <main className="thread">
        {msgs.length === 0 && (
          <p className="empty">问一个年报里的问题，例如「2024年营业总收入是多少？」</p>
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
                {m.meta?.sources.map(s => (
                  <SourceCard
                    key={`${s.year}-${s.page}`}
                    source={s}
                    numbers={answerNumbers(m.meta!.answer)}
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