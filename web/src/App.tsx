import { useEffect, useRef, useState } from 'react'
import {
  answerNumbers, createConversation, deleteConversation, fetchReady,
  getConversation, listConversations, stepLabel, streamAsk,
  type AskMeta, type ConversationSummary, type ReadyInfo, type StoredMessage,
  type StreamEvent,
} from './api'
import AnswerText from './components/AnswerText'
import CompareView from './components/CompareView'
import Sidebar, { type Backend, type Mode } from './components/Sidebar'
import SourceCard from './components/SourceCard'

interface Msg {
  role: 'user' | 'assistant'
  text: string
  meta?: AskMeta
  error?: string
  trace: string[]
}

const PRESETS = [
  '2024年茅台营业总收入是多少？',
  '茅台2024年度分红方案是什么？',
  '2024年末国内经销商有多少家？',
  'i茅台渠道2024年收入是多少？',
  '2025年营业总收入比2022年增长了多少？',
  '五粮液2024年的营业收入是多少？',
]
const CIRCLED = Array.from({ length: 6 }, (_, i) => String.fromCharCode(0x2460 + i))

/** 把后端存储的消息还原成界面 Msg（助手 meta 重建出处/步骤）。 */
function toMsg(m: StoredMessage): Msg {
  const meta = m.role === 'assistant' ? (m.meta as unknown as AskMeta) : undefined
  return { role: m.role, text: m.content, meta, trace: meta?.steps?.map(stepLabel) ?? [] }
}

export default function App() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [year, setYear] = useState<number | null>(null)
  const [forceAgent, setForceAgent] = useState(false)
  const [backend, setBackend] = useState<Backend>('cloud')
  const [mode, setMode] = useState<Mode>('chat')
  const [ready, setReady] = useState<ReadyInfo | null>(null)
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('maotai.sideCollapsed') === '1' } catch { return false }
  })
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const toggleSide = () => setCollapsed(v => {
    const next = !v
    try { localStorage.setItem('maotai.sideCollapsed', next ? '1' : '0') } catch { /* 隐私模式忽略 */ }
    return next
  })

  useEffect(() => {
    let live = true
    void fetchReady().then(r => {
      if (!live) return
      setReady(r)
      if (r?.backend === 'ollama') setBackend('ollama')
    })
    return () => { live = false }
  }, [])

  // P1：挂载时恢复最近一条对话；会话 API 不可用则回退无持久化。
  useEffect(() => {
    let live = true
    void (async () => {
      try {
        const list = await listConversations()
        if (!live) return
        setConversations(list)
        if (list.length > 0) {
          const stored = await getConversation(list[0].id)
          if (!live) return
          setActiveId(list[0].id)
          setMsgs(stored.map(toMsg))
        }
      } catch { /* 无持久化兜底 */ }
    })()
    return () => { live = false }
  }, [])

  async function loadConversation(id: string) {
    try {
      const stored = await getConversation(id)
      setActiveId(id)
      setMsgs(stored.map(toMsg))
    } catch { /* ignore */ }
  }

  function newChat() {
    abortRef.current?.abort()
    setActiveId(null)
    setMsgs([])
  }

  function switchChat(id: string) {
    if (busy) return
    abortRef.current?.abort()
    void loadConversation(id)
  }

  async function deleteChat(id: string) {
    try {
      await deleteConversation(id)
      const list = await listConversations()
      setConversations(list)
      if (id === activeId) {
        if (list.length > 0) void loadConversation(list[0].id)
        else { setActiveId(null); setMsgs([]) }
      }
    } catch { /* ignore */ }
  }

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
    let cid = activeId
    if (!cid) {
      try { cid = await createConversation(q.slice(0, 30)); setActiveId(cid) }
      catch { cid = null }
    }
    setMsgs(prev => [...prev,
      { role: 'user', text: q, trace: [] },
      { role: 'assistant', text: '', trace: [] }])
    const opts = {
      year: year ?? undefined,
      api_key: backend === 'cloud' && apiKey.trim() ? apiKey.trim() : undefined,
      agent: forceAgent || undefined,
      history: history.length ? history : undefined,
      backend,
      conversation_id: cid ?? undefined,
    }
    try {
      await streamAsk(q, (e: StreamEvent) => {
        if (e.event === 'status')
          patchLast(m => ({ ...m, trace: [...m.trace, e.data.label] }))
        else if (e.event === 'delta')
          patchLast(m => ({ ...m, text: m.text + e.data.text }))
        else if (e.event === 'replace')
          patchLast(m => ({ ...m, text: e.data.text }))
        else if (e.event === 'done')
          patchLast(m => ({
            ...m,
            meta: e.data.meta,
            trace: e.data.meta.steps?.length
              ? e.data.meta.steps.map(stepLabel)
              : m.trace,
          }))
        else if (e.event === 'error')
          patchLast(m => ({ ...m, error: e.data.message }))
      }, ac.signal, opts)
    } catch (err) {
      if (!ac.signal.aborted)
        patchLast(m => ({ ...m, error: String(err) }))
    } finally {
      setBusy(false)
      void listConversations().then(setConversations).catch(() => {})
    }
  }

  return (
    <div className={`app${collapsed ? ' side-collapsed' : ''}`}>
      <Sidebar
        mode={mode} onMode={setMode}
        year={year} onYear={setYear}
        backend={backend} onBackend={setBackend}
        apiKey={apiKey} onApiKey={setApiKey}
        forceAgent={forceAgent} onForceAgent={setForceAgent}
        ready={ready} busy={busy}
        canClear={msgs.length > 0} onClear={newChat}
        onCollapse={toggleSide}
        conversations={conversations} activeId={activeId}
        onNewChat={newChat} onSwitchChat={switchChat} onDeleteChat={deleteChat}
      />
      {collapsed && (
        <button className="side-expand" onClick={toggleSide} aria-label="展开侧栏" title="展开侧栏">»</button>
      )}
      <div className="main">
        <div className="main-scroll">
          <header className="page-head">
            <p className="eyebrow">{mode === 'chat' ? '对话问答' : '双年对比'}</p>
            <h1>年报问答 · 证据可溯</h1>
            <p className="tagline">贵州茅台 2022–2025 年报语料 · 答案附原文页码与坐标高亮 · 查不到就明说</p>
          </header>

          {mode === 'chat' ? (
            <main className={`thread${msgs.length === 0 ? ' empty' : ''}`}>
              {msgs.length === 0 && (
                <div className="empty-state">
                  <section className="cover">
                    <div className="cover-band" />
                    <div className="cover-body">
                      <p className="cover-k">语料说明</p>
                      <p className="cover-v">语料：贵州茅台 2022–2025 四份年报 · <b>1,778</b> 个证据块</p>
                      <p className="cover-v">核验：答案数字定位到原文页框并高亮 · 留出集拒答 <b>15/15</b> 零编造</p>
                    </div>
                  </section>
                  <p className="eyebrow preset-eyebrow">试试这些问题</p>
                  <div className="preset-grid">
                    {PRESETS.map((p, i) => (
                      <button
                        key={p}
                        className="preset"
                        style={{ animationDelay: `${i * 70}ms` }}
                        onClick={() => void send(p)}
                        disabled={busy}
                      >
                        <span className="preset-num">{CIRCLED[i]}</span>{' '}
                        <span>{p}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {msgs.map((m, i) => (
                <article key={i} className={`msg ${m.role}`}>
                  {m.role === 'user' ? (
                    <p className="bubble">{m.text}</p>
                  ) : (
                    <>
                      {m.trace.length > 0 && (
                        <details className="trace" open={busy && i === msgs.length - 1}>
                          <summary>过程 · {m.trace.length} 步</summary>
                          <ol>
                            {m.trace.map((t, j) => (
                              <li
                                key={j}
                                className={busy && i === msgs.length - 1
                                  && j === m.trace.length - 1 ? 'active' : undefined}
                              >
                                {t}
                              </li>
                            ))}
                          </ol>
                        </details>
                      )}
                      {m.meta?.refused && <span className="badge-refused">拒答</span>}
                      {m.text && (
                        <p className={`answer${busy && i === msgs.length - 1 && !m.meta ? ' streaming' : ''}`}>
                          <AnswerText text={m.text} />
                        </p>
                      )}
                      {m.error && <p className="error">{m.error}</p>}
                      {m.meta && (
                        <p className="meta">
                          <span className={`route-tag${m.meta.route === 'agent' ? ' agent' : ''}`}>
                            {m.meta.route === 'agent' ? 'Agent 工具循环' : '直通检索'}
                          </span>
                          {m.meta.latency_ms ?? '—'}ms · ¥{m.meta.cost_cny ?? '—'}
                        </p>
                      )}
                      {m.meta?.sources.map((s, k) => (
                        <SourceCard
                          key={`${s.year}-${s.page}`}
                          source={s}
                          numbers={answerNumbers(m.meta!.answer)}
                          defaultOpen={k === 0}
                          index={k}
                        />
                      ))}
                      {m.meta?.followups?.length && i === msgs.length - 1 ? (
                        <div className="followups">
                          <p className="followups-label">接着问</p>
                          {m.meta.followups.map(f => (
                            <button key={f} className="chip" onClick={() => void send(f)}>
                              {f}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </>
                  )}
                </article>
              ))}
            </main>
          ) : (
            <CompareView backend={backend} apiKey={apiKey} forceAgent={forceAgent} />
          )}
        </div>

        {mode === 'chat' && (
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
        )}
      </div>
    </div>
  )
}