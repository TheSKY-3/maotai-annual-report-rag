import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const READY = {
  status: 'ok', years: [2022, 2023, 2024, 2025], model: 'deepseek-flash',
  backend: 'cloud', api_key_configured: true,
}

/** fetch 桩按 URL 分流：/ready 返回就绪信息，其余返回流式响应。 */
function routed(make: () => unknown) {
  return vi.fn().mockImplementation(async (url: unknown) => {
    if (String(url) === '/ready') return { ok: true, json: async () => READY }
    return make()
  })
}

/** 只取 /ask* 调用（/ready 探活不算问答）。 */
const askCalls = (mock: ReturnType<typeof vi.fn>) =>
  mock.mock.calls.filter(c => String(c[0]).startsWith('/ask'))

/** 每次问答返回一组新事件（/ready 单独分流）。 */
function stubAskStream(events: string[]) {
  const mock = routed(() => ({
    ok: true,
    body: new ReadableStream({
      start(c) {
        for (const e of events) c.enqueue(new TextEncoder().encode(e))
        c.close()
      },
    }),
  }))
  vi.stubGlobal('fetch', mock)
  return mock
}

function stubStream(events: string[]) {
  const stream = new ReadableStream({
    start(c) {
      for (const e of events) c.enqueue(new TextEncoder().encode(e))
      c.close()
    },
  })
  vi.stubGlobal('fetch', routed(() => ({ ok: true, body: stream })))
}

/** 手动控流：测试可在流中途断言界面状态，再推后续事件。 */
function stubControlled() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({ start(c) { ctrl = c } })
  vi.stubGlobal('fetch', routed(() => ({ ok: true, body: stream })))
  return {
    push: (e: string) => ctrl.enqueue(new TextEncoder().encode(e)),
    end: () => ctrl.close(),
  }
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

describe('App 问答主流程', () => {
  it('流式渲染答案并给出出处坐标图', async () => {
    stubStream([
      'event: delta\ndata: {"text":"合计为68"}\n\n',
      'event: delta\ndata: {"text":"0.93。"}\n\n',
      `event: done\ndata: {"meta":${JSON.stringify(DONE_META)}}\n\n`,
    ])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '合计多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText(/合计为680.93/)).toBeInTheDocument()
    expect(screen.getByText(/证据 1 · 2024年报 P26/)).toBeInTheDocument()
    expect(screen.getByAltText(/坐标高亮/)).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('拒答时明确标出拒答', async () => {
    stubStream([
      `event: done\ndata: {"meta":${JSON.stringify({
        ...DONE_META, answer: '年报未披露该数据。', refused: true, sources: [],
      })}}\n\n`,
    ])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '五粮液营收？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText('拒答')).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('error 事件显示错误信息', async () => {
    stubStream(['event: error\ndata: {"message":"生成失败：连接错误"}\n\n'])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText(/生成失败：连接错误/)).toBeInTheDocument()
    vi.unstubAllGlobals()
  })
})

describe('App 预设与设置', () => {
  const BASE_META = {
    question: '营收多少？',
    answer: '营收1,708.99亿元。',
    refused: false,
    sources: [{ year: 2024, page: 26 }],
    citations: ['2024年报 P26'],
    route: 'direct',
    model: 'deepseek-flash',
    latency_ms: 6500,
    cost_cny: 0.001,
  }

  function stubFresh(events: string[]) {
    const mock = routed(() => ({
      ok: true,
      body: new ReadableStream({
        start(c) {
          for (const e of events) c.enqueue(new TextEncoder().encode(e))
          c.close()
        },
      }),
    }))
    vi.stubGlobal('fetch', mock)
    return mock
  }

  const answerEvents = (meta: { answer: string; [key: string]: unknown }) => [
    `event: replace\ndata: ${JSON.stringify({ text: meta.answer })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ meta })}\n\n`,
  ]

  it('点预设问题直接发问', async () => {
    const mock = stubFresh(answerEvents(BASE_META))
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /2024年茅台营业总收入/ }))
    expect(await screen.findByText('2024年茅台营业总收入是多少？')).toBeInTheDocument()
    const body = JSON.parse(askCalls(mock)[0][1].body)
    expect(body.question).toBe('2024年茅台营业总收入是多少？')
    vi.unstubAllGlobals()
  })

  it('Key/年份/强制Agent 随请求发送', async () => {
    const mock = stubFresh(answerEvents(BASE_META))
    render(<App />)
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), { target: { value: 'sk-mine' } })
    fireEvent.change(screen.getByLabelText('检索范围'), { target: { value: '2024' } })
    fireEvent.click(screen.getByLabelText('强制 Agent'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '营收多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() => expect(mock).toHaveBeenCalled())
    const body = JSON.parse(askCalls(mock)[0][1].body)
    expect(body.api_key).toBe('sk-mine')
    expect(body.year).toBe(2024)
    expect(body.agent).toBe(true)
    vi.unstubAllGlobals()
  })

  it('清空对话后回到空界面', async () => {
    stubFresh(answerEvents(BASE_META))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '营收多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText(/营收1,708.99亿元/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /清空/ }))
    expect(screen.queryByText(/营收1,708.99亿元/)).not.toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('Agent 路径渲染工具调用时间线', async () => {
    stubFresh([
      'event: status\ndata: {"label":"调用 search_by_year（year: 2024），取回 5 个片段"}\n\n',
      ...answerEvents({ ...BASE_META, route: 'agent' }),
    ])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '跨年对比？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText(/调用 search_by_year/)).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('流式期间时间线展开且逐条累积，答完收起成摘要', async () => {
    const s = stubControlled()
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '跨年对比？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    s.push('event: status\ndata: {"label":"调用 search_by_year（year: 2022），取回 5 个片段"}\n\n')
    s.push('event: status\ndata: {"label":"调用 compare_metric（metric: 净利润），取回 3 个片段"}\n\n')
    expect(await screen.findByText(/调用 search_by_year/)).toBeVisible()
    expect(screen.getByText(/调用 compare_metric/)).toBeVisible()
    s.push(`event: done\ndata: {"meta":${JSON.stringify(BASE_META)}}\n\n`)
    s.end()
    const summary = await screen.findByText('过程 · 2 步')
    const box = summary.closest('details')!
    await waitFor(() => expect(box.open).toBe(false))
    expect(screen.getByText(/调用 search_by_year/)).not.toBeVisible()
    fireEvent.click(summary)
    expect(screen.getByText(/调用 compare_metric/)).toBeVisible()
    vi.unstubAllGlobals()
  })

  it('delta 与 replace 不清掉已累积的时间线', async () => {
    const s = stubControlled()
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '跨年对比？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    s.push('event: status\ndata: {"label":"调用 compare_years（years: 2022、2025），取回 6 个片段"}\n\n')
    await screen.findByText(/调用 compare_years/)
    s.push('event: delta\ndata: {"text":"增长"}\n\n')
    s.push('event: replace\ndata: {"text":"增长 34.89%。"}\n\n')
    s.push(`event: done\ndata: {"meta":${JSON.stringify(BASE_META)}}\n\n`)
    s.end()
    const summary = await screen.findByText('过程 · 1 步')
    fireEvent.click(summary)
    expect(screen.getByText(/调用 compare_years/)).toBeVisible()
    vi.unstubAllGlobals()
  })

  it('答完后时间线换成结构化工具清单', async () => {
    stubFresh([
      'event: status\ndata: {"label":"检索并核对证据中…"}\n\n',
      ...answerEvents({
        ...BASE_META, route: 'agent',
        steps: [
          { tool: 'search_by_year', args: { year: 2022 }, n_hits: 5 },
          { tool: 'compare_metric', args: { metric: '净利润', years: [2022, 2025] }, n_hits: 3 },
        ],
      }),
    ])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '跨年对比？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    const summary = await screen.findByText('过程 · 2 步')
    fireEvent.click(summary)
    expect(screen.getByText(
      '调用 compare_metric（metric: 净利润、years: [2022, 2025]），取回 3 个片段',
    )).toBeVisible()
    expect(screen.queryByText('检索并核对证据中…')).toBeNull()
    vi.unstubAllGlobals()
  })

  it('直通路径答完保留阶段状态行', async () => {
    stubFresh([
      'event: status\ndata: {"label":"检索年报…"}\n\n',
      ...answerEvents({ ...BASE_META, steps: [] }),
    ])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '营收多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    const summary = await screen.findByText('过程 · 1 步')
    fireEvent.click(summary)
    expect(screen.getByText('检索年报…')).toBeVisible()
    vi.unstubAllGlobals()
  })

  it('两条出处时首张默认展开、第二张折叠', async () => {
    stubFresh(answerEvents({
      ...BASE_META,
      sources: [{ year: 2024, page: 26 }, { year: 2024, page: 34 }],
    }))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '营收多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await screen.findByText(/营收1,708.99亿元/)
    const tags = screen.getAllByText('坐标核验 · 红框 = 答案引用的数字')
    expect(tags).toHaveLength(2)
    expect(tags[0]).toBeVisible()
    expect(tags[1]).not.toBeVisible()
    vi.unstubAllGlobals()
  })

  it('第二轮带上上一轮 history', async () => {
    const mock = stubFresh(answerEvents(BASE_META))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '第一问' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await screen.findByText(/营收1,708.99亿元/)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '第二问' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() => expect(askCalls(mock)).toHaveLength(2))
    const body2 = JSON.parse(askCalls(mock)[1][1].body)
    expect(body2.history).toEqual([
      { role: 'user', content: '第一问' },
      { role: 'assistant', content: '营收1,708.99亿元。' },
    ])
    vi.unstubAllGlobals()
  })
})

describe('App 追问建议', () => {
  const FU = ['2024年净利润是多少？', '2024年分红方案是什么？']

  /** 每轮返回各自的答案与追问建议。 */
  function stubRounds(rounds: { answer: string; followups: string[] }[]) {
    let n = 0
    const mock = routed(() => {
      const r = rounds[Math.min(n++, rounds.length - 1)]
      const meta = {
        question: '营收多少？', answer: r.answer, refused: false,
        sources: [], citations: [], route: 'direct', model: 'deepseek-flash',
        latency_ms: 6500, cost_cny: 0.001, followups: r.followups,
      }
      return {
        ok: true,
        body: new ReadableStream({
          start(c) {
            const enc = new TextEncoder()
            c.enqueue(enc.encode(
              `event: replace\ndata: ${JSON.stringify({ text: r.answer })}\n\n`))
            c.enqueue(enc.encode(
              `event: done\ndata: ${JSON.stringify({ meta })}\n\n`))
            c.close()
          },
        }),
      }
    })
    vi.stubGlobal('fetch', mock)
    return mock
  }

  async function ask(text: string) {
    fireEvent.change(screen.getByRole('textbox'), { target: { value: text } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
  }

  it('答完给出「接着问」建议按钮', async () => {
    stubRounds([{ answer: '营收1,708.99亿元。', followups: FU }])
    render(<App />)
    await ask('营收多少？')
    expect(await screen.findByText('接着问')).toBeInTheDocument()
    for (const f of FU)
      expect(screen.getByRole('button', { name: f })).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('点建议按钮直接发问并带上上一轮 history', async () => {
    const mock = stubRounds([
      { answer: '营收1,708.99亿元。', followups: FU },
      { answer: '净利润862.28亿元。', followups: [] },
    ])
    render(<App />)
    await ask('营收多少？')
    fireEvent.click(await screen.findByRole('button', { name: FU[0] }))
    await waitFor(() => expect(askCalls(mock)).toHaveLength(2))
    const body = JSON.parse(askCalls(mock)[1][1].body)
    expect(body.question).toBe(FU[0])
    expect(body.history).toEqual([
      { role: 'user', content: '营收多少？' },
      { role: 'assistant', content: '营收1,708.99亿元。' },
    ])
    vi.unstubAllGlobals()
  })

  it('只有最新一条答案下保留建议', async () => {
    stubRounds([
      { answer: '营收1,708.99亿元。', followups: FU },
      { answer: '净利润862.28亿元。', followups: ['2024年毛利率是多少？'] },
    ])
    render(<App />)
    await ask('营收多少？')
    await screen.findByRole('button', { name: FU[0] })
    await ask('净利润呢？')
    expect(await screen.findByRole('button', { name: '2024年毛利率是多少？' }))
      .toBeInTheDocument()
    expect(screen.queryByRole('button', { name: FU[0] })).toBeNull()
    vi.unstubAllGlobals()
  })

  it('新一轮没有建议时收掉接着问', async () => {
    stubRounds([
      { answer: '营收1,708.99亿元。', followups: FU },
      { answer: '年报未披露该数据。', followups: [] },
    ])
    render(<App />)
    await ask('营收多少？')
    await screen.findByRole('button', { name: FU[0] })
    await ask('五粮液营收？')
    await screen.findByText(/年报未披露该数据/)
    expect(screen.queryByText('接着问')).toBeNull()
    vi.unstubAllGlobals()
  })
})

describe('App 语料封面与出处胶囊', () => {
  it('空态显示语料说明与语料统计', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => READY }))
    render(<App />)
    await act(async () => {})
    expect(screen.getByText('语料说明')).toBeInTheDocument()
    expect(screen.getByText('1,778')).toBeInTheDocument()
    expect(screen.getByText('15/15')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /① 2024年茅台营业总收入/ }))
      .toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('答案里的出处渲染成胶囊', async () => {
    stubStream([
      `event: replace\ndata: ${JSON.stringify({ text: DONE_META.answer })}\n\n`,
      `event: done\ndata: {"meta":${JSON.stringify(DONE_META)}}\n\n`,
    ])
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '合计多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    const pill = await screen.findByText('📄 2024年报 P26')
    expect(pill).toHaveClass('cite-pill')
    vi.unstubAllGlobals()
  })
})

describe('App 生成后端切换', () => {
  const META = {
    question: '营收多少？', answer: '营收1,708.99亿元。', refused: false,
    sources: [], citations: [], route: 'direct', model: 'deepseek-flash',
    latency_ms: 6500, cost_cny: 0.001,
  }
  const events = [
    `event: replace\ndata: ${JSON.stringify({ text: META.answer })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ meta: META })}\n\n`,
  ]
  async function ask(text: string) {
    fireEvent.change(screen.getByRole('textbox'), { target: { value: text } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
  }

  it('服务端已配 Key 时显示已连接', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => READY }))
    render(<App />)
    expect(await screen.findByText('DeepSeek 已连接')).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('选本地 Ollama：请求带 backend 且不带 Key，Key 输入收起', async () => {
    const mock = stubAskStream(events)
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '本地 Ollama' }))
    expect(screen.queryByLabelText('DeepSeek API Key')).toBeNull()
    expect(screen.getByText(/离线零成本/)).toBeInTheDocument()
    await ask('营收多少？')
    await waitFor(() => expect(askCalls(mock)).toHaveLength(1))
    const body = JSON.parse(askCalls(mock)[0][1].body)
    expect(body.backend).toBe('ollama')
    expect(body.api_key).toBeUndefined()
    vi.unstubAllGlobals()
  })

  it('切回云端：请求带 backend cloud 与自填 Key', async () => {
    const mock = stubAskStream(events)
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '本地 Ollama' }))
    fireEvent.click(screen.getByRole('button', { name: '云端 DeepSeek' }))
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), { target: { value: 'sk-mine' } })
    await ask('营收多少？')
    await waitFor(() => expect(askCalls(mock)).toHaveLength(1))
    const body = JSON.parse(askCalls(mock)[0][1].body)
    expect(body.backend).toBe('cloud')
    expect(body.api_key).toBe('sk-mine')
    vi.unstubAllGlobals()
  })
})

describe('App 双年对比', () => {
  function stubCompare() {
    const mock = vi.fn().mockImplementation(async (url: unknown, init?: { body?: string }) => {
      if (String(url) === '/ready') return { ok: true, json: async () => READY }
      const body = JSON.parse(init?.body ?? '{}')
      const meta = {
        question: body.question, answer: `${body.year}年营收1,708.99亿元。`,
        refused: false, sources: [{ year: body.year, page: 26 }],
        citations: [`${body.year}年报 P26`], route: 'direct', model: 'deepseek-flash',
        latency_ms: 6500, cost_cny: 0.001,
      }
      return { ok: true, json: async () => meta }
    })
    vi.stubGlobal('fetch', mock)
    return mock
  }

  it('切到对比模式：双年份默认最近两年与默认问题', async () => {
    stubCompare()
    render(<App />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: '双年对比' }))
    expect(screen.getByLabelText('年报 A')).toHaveValue('2024')
    expect(screen.getByLabelText('年报 B')).toHaveValue('2025')
    expect(screen.getByLabelText('问题')).toHaveValue('营业总收入是多少？主要经营情况如何？')
    expect(screen.getByRole('button', { name: '开始对比' })).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('开始对比：并排渲染两年答案与出处', async () => {
    const mock = stubCompare()
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: '双年对比' }))
    fireEvent.click(screen.getByRole('button', { name: '开始对比' }))
    expect(await screen.findByText('2024年营收1,708.99亿元。')).toBeInTheDocument()
    expect(screen.getByText('2025年营收1,708.99亿元。')).toBeInTheDocument()
    const calls = mock.mock.calls.filter(c => String(c[0]) === '/ask')
    expect(calls).toHaveLength(2)
    const bodies = calls.map(c => JSON.parse(c[1].body))
    expect(bodies.map(b => b.year)).toEqual([2024, 2025])
    expect(bodies[0].question).toBe('营业总收入是多少？主要经营情况如何？')
    expect(bodies[0].backend).toBe('cloud')
    expect(screen.getAllByText(/证据 1 ·/)).toHaveLength(2)
    vi.unstubAllGlobals()
  })
})

describe('App 品牌印记（描述性命名、不含商标字）', () => {
  it('印章用「溯」字、产品名「年报问答」，不出现「茅」与「审计卷宗」', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => READY }))
    render(<App />)
    await act(async () => {})
    expect(screen.getByText('溯')).toHaveClass('seal')
    expect(screen.getByText('年报问答')).toHaveClass('brand-name')
    expect(screen.getByText('年报问答 · 证据可溯')).toBeInTheDocument()
    expect(screen.queryByText('茅')).toBeNull()
    expect(screen.queryByText(/审计卷宗/)).toBeNull()
    vi.unstubAllGlobals()
  })
})

describe('App 侧栏收起', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => READY }))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('点收起按钮隐藏侧栏并出现展开按钮', async () => {
    render(<App />)
    await act(async () => {})
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '展开侧栏' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }))
    expect(document.querySelector('.app')).toHaveClass('side-collapsed')
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeVisible()
  })

  it('点展开按钮恢复侧栏', async () => {
    render(<App />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }))
    fireEvent.click(screen.getByRole('button', { name: '展开侧栏' }))
    expect(document.querySelector('.app')).not.toHaveClass('side-collapsed')
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '展开侧栏' })).toBeNull()
  })

  it('收起状态写入 localStorage 并在重挂载后恢复', async () => {
    const { unmount } = render(<App />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }))
    expect(localStorage.getItem('maotai.sideCollapsed')).toBe('1')
    unmount()
    render(<App />)
    await act(async () => {})
    expect(document.querySelector('.app')).toHaveClass('side-collapsed')
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeVisible()
  })
})