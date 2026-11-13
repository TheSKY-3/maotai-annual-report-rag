import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const READY = {
  status: 'ok', years: [2022, 2023, 2024, 2025], model: 'deepseek-flash',
  backend: 'cloud', api_key_configured: true,
}

const DONE_META = {
  question: '合计多少？',
  answer: '合计为680.93。 [来源: 2024年报 P26]',
  refused: false,
  sources: [{ year: 2024, page: 26 }],
  citations: ['2024年报 P26'],
  route: 'direct',
  model: 'deepseek-flash',
  latency_ms: 6500,
  cost_cny: 0.001,
}

const doneEvent = `event: done\ndata: {"meta":${JSON.stringify(DONE_META)}}\n\n`

interface Call { url: string; method: string; body?: Record<string, unknown> }

/** 按 URL+方法分流：/ready、/conversations CRUD、/ask* 流式。 */
function makeRouter(opts: {
  conversations?: { id: string; title: string; created_at: number; updated_at: number }[]
  messages?: Record<string, { role: string; content: string; meta: unknown; created_at: number }[]>
  streamEvents?: string[]
}) {
  const calls: Call[] = []
  const mock = vi.fn().mockImplementation(async (url: unknown, init?: { method?: string; body?: string }) => {
    const u = String(url)
    const method = init?.method ?? 'GET'
    calls.push({ url: u, method, body: init?.body ? JSON.parse(init.body) : undefined })
    if (u === '/ready') return { ok: true, json: async () => READY }
    if (u === '/conversations' && method === 'GET')
      return { ok: true, json: async () => ({ conversations: opts.conversations ?? [] }) }
    if (u === '/conversations' && method === 'POST')
      return { ok: true, json: async () => ({ id: 'new-cid' }) }
    if (u.startsWith('/conversations/') && method === 'GET') {
      const id = u.split('/')[2]
      return { ok: true, json: async () => ({ messages: opts.messages?.[id] ?? [] }) }
    }
    if (u.startsWith('/conversations/') && method === 'DELETE')
      return { ok: true, json: async () => ({ ok: true }) }
    if (u.startsWith('/ask')) {
      return {
        ok: true,
        body: new ReadableStream({
          start(c) {
            for (const e of opts.streamEvents ?? []) c.enqueue(new TextEncoder().encode(e))
            c.close()
          },
        }),
      }
    }
    return { ok: true, json: async () => ({}) }
  })
  vi.stubGlobal('fetch', mock)
  return { mock, calls }
}

afterEach(() => vi.unstubAllGlobals())

describe('App 会话持久化', () => {
  it('挂载时加载历史并恢复最近对话的消息', async () => {
    makeRouter({
      conversations: [{ id: 'c1', title: '合计', created_at: 1, updated_at: 2 }],
      messages: {
        c1: [
          { role: 'user', content: '合计多少？', meta: {}, created_at: 1 },
          { role: 'assistant', content: DONE_META.answer, meta: DONE_META, created_at: 2 },
        ],
      },
    })
    render(<App />)
    expect(await screen.findByText('合计多少？')).toBeInTheDocument()
    expect(screen.getByText('合计')).toBeInTheDocument()
  })

  it('发送时创建会话并把 conversation_id 带给 /ask', async () => {
    const { calls } = makeRouter({ conversations: [], streamEvents: [doneEvent] })
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '营收？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() =>
      expect(calls.some(c => c.url === '/conversations' && c.method === 'POST')).toBe(true))
    const ask = calls.find(c => c.url.startsWith('/ask'))
    expect(ask?.body?.conversation_id).toBe('new-cid')
  })

  it('新建对话清空当前消息', async () => {
    makeRouter({
      conversations: [{ id: 'c1', title: '合计', created_at: 1, updated_at: 2 }],
      messages: { c1: [{ role: 'user', content: '合计多少？', meta: {}, created_at: 1 }] },
    })
    render(<App />)
    await screen.findByText('合计多少？')
    fireEvent.click(screen.getByLabelText('新建对话'))
    expect(screen.queryByText('合计多少？')).not.toBeInTheDocument()
  })

  it('切换对话加载其消息', async () => {
    makeRouter({
      conversations: [
        { id: 'c1', title: '甲', created_at: 1, updated_at: 2 },
        { id: 'c2', title: '乙', created_at: 1, updated_at: 1 },
      ],
      messages: {
        c1: [{ role: 'user', content: '甲的问题', meta: {}, created_at: 1 }],
        c2: [{ role: 'user', content: '乙的问题', meta: {}, created_at: 1 }],
      },
    })
    render(<App />)
    await screen.findByText('甲的问题')
    fireEvent.click(screen.getByText('乙'))
    expect(await screen.findByText('乙的问题')).toBeInTheDocument()
  })
})