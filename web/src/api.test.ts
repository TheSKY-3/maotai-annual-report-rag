import { describe, expect, it, vi } from 'vitest'
import { answerNumbers, cropUrl, parseSSE, stepLabel, streamAsk } from './api'

describe('parseSSE', () => {
  it('切出完整事件并保留不完整尾部', () => {
    const buf = 'event: delta\ndata: {"text":"680"}\n\nevent: delta\ndata: {"te'
    const { events, rest } = parseSSE(buf)
    expect(events).toEqual([{ event: 'delta', data: { text: '680' } }])
    expect(rest).toBe('event: delta\ndata: {"te')
  })

  it('识别 replace 与 done 事件', () => {
    const buf =
      'event: replace\ndata: {"text":"全文"}\n\n' +
      'event: done\ndata: {"meta":{"answer":"全文","refused":false}}\n\n'
    const { events, rest } = parseSSE(buf)
    expect(events.map((e) => e.event)).toEqual(['replace', 'done'])
    expect(rest).toBe('')
  })

  it('跳过没有 data 的空块', () => {
    const { events } = parseSSE('event: status\n\n')
    expect(events).toEqual([])
  })
})

describe('cropUrl', () => {
  it('编码百分号与逗号', () => {
    const url = cropUrl(2024, 26, ['11.44%', '680.93'])
    expect(url).toBe('/cite_crop?year=2024&page=26&numbers=11.44%25%2C680.93')
  })
})

describe('answerNumbers', () => {
  it('剥出处与列举序号、跳过年份', () => {
    const answer =
      '1. 2024年董事、监事和高级管理人员从公司获得的税前报酬总额合计为680.93。' +
      ' [来源: 2024年报 P26]'
    expect(answerNumbers(answer)).toEqual(['680.93'])
  })

  it('保留带单位与百分数、去重', () => {
    expect(answerNumbers('营收1,708.99亿元，毛利率94.19%，再说94.19%。'))
      .toEqual(['1,708.99亿元', '94.19%'])
  })
})

describe('streamAsk', () => {
  it('跨 chunk 边界按序派发事件', async () => {
    const chunks = [
      'event: delta\ndata: {"text":"68"}\n\nevent: del',
      'ta\ndata: {"text":"0.93"}\n\nevent: done\ndata: {"meta":{"answer":"680.93"}}\n\n',
    ]
    const stream = new ReadableStream({
      start(c) {
        for (const ch of chunks) c.enqueue(new TextEncoder().encode(ch))
        c.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body: stream }))
    const seen: string[] = []
    await streamAsk('合计多少？', (e) => seen.push(e.event))
    expect(seen).toEqual(['delta', 'delta', 'done'])
    vi.unstubAllGlobals()
  })

  it('HTTP 非 200 抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 502 }))
    await expect(streamAsk('x', () => {})).rejects.toThrow('HTTP 502')
    vi.unstubAllGlobals()
  })

  it('把 opts 合并进请求体', async () => {
    const stream = new ReadableStream({ start(c) { c.close() } })
    const mock = vi.fn().mockResolvedValue({ ok: true, body: stream })
    vi.stubGlobal('fetch', mock)
    await streamAsk('q', () => {}, undefined, {
      year: 2024, api_key: 'sk-test', agent: true,
      history: [{ role: 'user', content: 'prev' }],
    })
    const body = JSON.parse(mock.mock.calls[0][1].body)
    expect(body).toEqual({
      question: 'q', year: 2024, api_key: 'sk-test', agent: true,
      history: [{ role: 'user', content: 'prev' }],
    })
    vi.unstubAllGlobals()
  })
})

describe('stepLabel', () => {
  it('把工具步骤格式化成与后端 status 一致的行', () => {
    expect(stepLabel({
      tool: 'compare_metric',
      args: { metric: '净利润', years: [2022, 2025] },
      n_hits: 3,
    })).toBe('调用 compare_metric（metric: 净利润、years: [2022, 2025]），取回 3 个片段')
  })
})