import { describe, expect, it, vi } from 'vitest'
import {
  answerNumbers, askOnce, cropUrl, fetchReady, parseSSE, splitCitations,
  stepLabel, streamAsk,
} from './api'

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

  it('排除已标「计算得出」的数——纸上没有，红框框不出来', () => {
    const answer = '直销收入 8,454,303.19 万元（即 845.43 亿元（计算得出））；' +
      ' [来源: 2025年报 P17]'
    expect(answerNumbers(answer)).toEqual(['8,454,303.19'])
  })

  it('排除逗号写法的计算得出标记', () => {
    const answer = '净利润 4,259,628.69 万元（约425.96亿元，计算得出）' +
      ' [来源: 2023年报 P19]'
    expect(answerNumbers(answer)).toEqual(['4,259,628.69'])
  })

  it('不把邻近别的数的标记算过来', () => {
    // 94.50% 自己带标记该排除；10.51% 不该被隔壁的标记牵连
    const answer = '同比减少10.51%，毛利率94.50%（计算得出）'
    expect(answerNumbers(answer)).toEqual(['10.51%'])
  })

  it('全是计算值时返回空——不出红框图', () => {
    expect(answerNumbers('增幅 67.64%（计算得出）')).toEqual([])
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

describe('splitCitations', () => {
  it('无出处时整段为文本', () => {
    expect(splitCitations('营收1,708.99亿元。'))
      .toEqual([{ type: 'text', value: '营收1,708.99亿元。' }])
  })

  it('切出出处并保留前后文本', () => {
    expect(splitCitations('合计680.93。 [来源: 2024年报 P26] 以上。')).toEqual([
      { type: 'text', value: '合计680.93。 ' },
      { type: 'cite', value: '2024年报 P26' },
      { type: 'text', value: ' 以上。' },
    ])
  })

  it('全角冒号同样识别', () => {
    expect(splitCitations('[来源：2024年报 P5]'))
      .toEqual([{ type: 'cite', value: '2024年报 P5' }])
  })

  it('空文本返回空数组', () => {
    expect(splitCitations('')).toEqual([])
  })
})

describe('askOnce', () => {
  it('POST /ask 合并 opts 并返回 meta', async () => {
    const meta = { question: 'q', answer: 'a' }
    const mock = vi.fn().mockResolvedValue({ ok: true, json: async () => meta })
    vi.stubGlobal('fetch', mock)
    const got = await askOnce('q', { year: 2024, backend: 'ollama' })
    expect(mock.mock.calls[0][0]).toBe('/ask')
    expect(JSON.parse(mock.mock.calls[0][1].body))
      .toEqual({ question: 'q', year: 2024, backend: 'ollama' })
    expect(got).toEqual(meta)
    vi.unstubAllGlobals()
  })

  it('HTTP 非 200 抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 502 }))
    await expect(askOnce('q')).rejects.toThrow('HTTP 502')
    vi.unstubAllGlobals()
  })
})

describe('fetchReady', () => {
  it('返回就绪信息', async () => {
    const ready = { backend: 'cloud', api_key_configured: true }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ready }))
    expect(await fetchReady()).toEqual(ready)
    vi.unstubAllGlobals()
  })

  it('网络失败返回 null 不抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    expect(await fetchReady()).toBeNull()
    vi.unstubAllGlobals()
  })
})

describe('streamAsk backend 选项', () => {
  it('backend 随请求体发送', async () => {
    const stream = new ReadableStream({ start(c) { c.close() } })
    const mock = vi.fn().mockResolvedValue({ ok: true, body: stream })
    vi.stubGlobal('fetch', mock)
    await streamAsk('q', () => {}, undefined, { backend: 'ollama' })
    expect(JSON.parse(mock.mock.calls[0][1].body))
      .toEqual({ question: 'q', backend: 'ollama' })
    vi.unstubAllGlobals()
  })
})