import { fireEvent, render, screen } from '@testing-library/react'
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
    expect(screen.getByText(/2024年报 P26/)).toBeInTheDocument()
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