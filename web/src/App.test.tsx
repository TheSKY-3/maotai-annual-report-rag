import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

function stubStream(events: string[]) {
  const stream = new ReadableStream({
    start(c) {
      for (const e of events) c.enqueue(new TextEncoder().encode(e))
      c.close()
    },
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body: stream }))
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
    expect(screen.getByText(/出处 · 2024年报 P26/)).toBeInTheDocument()
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
    const mock = vi.fn().mockImplementation(async () => ({
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
    const body = JSON.parse(mock.mock.calls[0][1].body)
    expect(body.question).toBe('2024年茅台营业总收入是多少？')
    vi.unstubAllGlobals()
  })

  it('Key/年份/强制Agent 随请求发送', async () => {
    const mock = stubFresh(answerEvents(BASE_META))
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /设置/ }))
    fireEvent.change(screen.getByLabelText('DeepSeek API Key'), { target: { value: 'sk-mine' } })
    fireEvent.change(screen.getByLabelText('检索范围'), { target: { value: '2024' } })
    fireEvent.click(screen.getByLabelText('强制 Agent'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '营收多少？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() => expect(mock).toHaveBeenCalled())
    const body = JSON.parse(mock.mock.calls[0][1].body)
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

  it('Agent 路径渲染工具步骤', async () => {
    stubFresh(answerEvents({
      ...BASE_META, route: 'agent',
      steps: [{ tool: 'search_by_year', args: { year: 2024 }, n_hits: 5 }],
    }))
    render(<App />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '跨年对比？' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText(/search_by_year/)).toBeInTheDocument()
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
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2))
    const body2 = JSON.parse(mock.mock.calls[1][1].body)
    expect(body2.history).toEqual([
      { role: 'user', content: '第一问' },
      { role: 'assistant', content: '营收1,708.99亿元。' },
    ])
    vi.unstubAllGlobals()
  })
})