export interface Source {
  year: number
  page: number
  score?: number
  snippet?: string
  direct?: boolean
}

export interface Step {
  tool: string
  args: Record<string, unknown>
  n_hits: number
}

export interface AskMeta {
  question: string
  answer: string
  refused: boolean
  sources: Source[]
  citations: string[]
  route: string
  model: string
  latency_ms?: number
  cost_cny?: number
  steps?: Step[]
  followups?: string[]
}

export type StreamEvent =
  | { event: 'status'; data: { label: string } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'replace'; data: { text: string } }
  | { event: 'done'; data: { meta: AskMeta } }
  | { event: 'error'; data: { message: string } }

/** 把 SSE 字节流缓冲切成完整事件；不完整尾部留在 rest 里等下一块。 */
export function parseSSE(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = []
  let rest = buffer
  let idx: number
  while ((idx = rest.indexOf('\n\n')) >= 0) {
    const block = rest.slice(0, idx)
    rest = rest.slice(idx + 2)
    let ev = 'message'
    let data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) ev = line.slice(6).trim()
      else if (line.startsWith('data:')) data += line.slice(5).trim()
    }
    if (!data) continue
    events.push({ event: ev, data: JSON.parse(data) } as StreamEvent)
  }
  return { events, rest }
}

export interface AskOpts {
  year?: number
  api_key?: string
  agent?: boolean
  history?: { role: string; content: string }[]
  backend?: 'cloud' | 'ollama'
}

export async function streamAsk(
  question: string,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
  opts?: AskOpts,
): Promise<void> {
  const resp = await fetch('/ask/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, ...opts }),
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf = (buf + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')
    const { events, rest } = parseSSE(buf)
    buf = rest
    for (const e of events) onEvent(e)
  }
}

export function cropUrl(year: number, page: number, numbers: string[]): string {
  return `/cite_crop?year=${year}&page=${page}&numbers=${encodeURIComponent(numbers.join(','))}`
}

const fmtArg = (v: unknown) => (Array.isArray(v) ? `[${v.join(', ')}]` : String(v))

/** 与后端 agent status 同格式：调用 tool（k: v、…），取回 N 个片段。 */
export function stepLabel(s: Step): string {
  const args = Object.entries(s.args).map(([k, v]) => `${k}: ${fmtArg(v)}`).join('、')
  return `调用 ${s.tool}（${args}），取回 ${s.n_hits} 个片段`
}

const CITE_RE = /\[来源[^\]]*\]/g
const LIST_MARK_RE = /^\s*\d{1,2}[.、)]\s*/gm
const UNIT = '(?:万亿元|亿元|百万元|千万元|千元|万元|元|亿|万|千|%|％)?'
const NUM_RE = new RegExp(
  `\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?${UNIT}|\\d+(?:\\.\\d+)?${UNIT}`, 'g')

/** 答案里待坐标核验的指标数字：剥出处与列举序号、跳过年份（与后端同约定）。
 *  已标「计算得出」的数排除在外——那是模型算的，年报纸上没有，送进红框
 *  只会框不出来，而界面写着「红框 = 答案引用的数字」，等于承诺做不到的事。 */
export function answerNumbers(answer: string): string[] {
  const body = answer.replace(CITE_RE, '').replace(LIST_MARK_RE, '')
  const seen: string[] = []
  for (const m of body.matchAll(NUM_RE)) {
    const raw = m[0]
    if (/^20\d{2}$/.test(raw)) continue
    if (seen.includes(raw)) continue
    const at = (m.index ?? 0) + raw.length
    // 截到下一个数字或出处标注前，避免把邻近别的数的标记算过来
    const tail = body.slice(at, at + 40).split(/\[来源|\d/)[0]
    if (tail.includes('计算得出')) continue
    seen.push(raw)
  }
  return seen
}

export const YEARS = [2022, 2023, 2024, 2025]

export interface ReadyInfo {
  status: string
  years: number[]
  model: string
  backend: string
  api_key_configured: boolean
}

/** 就绪探活：失败返回 null，不阻塞界面。 */
export async function fetchReady(): Promise<ReadyInfo | null> {
  try {
    const resp = await fetch('/ready')
    return resp.ok ? await resp.json() : null
  } catch {
    return null
  }
}

/** 非流式问答（对比模式两列各调一次）。 */
export async function askOnce(
  question: string,
  opts?: AskOpts,
  signal?: AbortSignal,
): Promise<AskMeta> {
  const resp = await fetch('/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, ...opts }),
    signal,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export type AnswerSegment = { type: 'text' | 'cite'; value: string }

const CITE_TAG_RE = /\[来源[:：]\s*([^\]]+)\]/g

/** 把答案切成文本段与出处段（与 Streamlit fmt_answer 同一正则）。 */
export function splitCitations(text: string): AnswerSegment[] {
  const out: AnswerSegment[] = []
  let last = 0
  for (const m of text.matchAll(CITE_TAG_RE)) {
    const i = m.index ?? 0
    if (i > last) out.push({ type: 'text', value: text.slice(last, i) })
    out.push({ type: 'cite', value: m[1] })
    last = i + m[0].length
  }
  if (last < text.length) out.push({ type: 'text', value: text.slice(last) })
  return out
}