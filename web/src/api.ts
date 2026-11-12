export interface Source {
  year: number
  page: number
  score?: number
  snippet?: string
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

const CITE_RE = /\[来源[^\]]*\]/g
const LIST_MARK_RE = /^\s*\d{1,2}[.、)]\s*/gm
const UNIT = '(?:万亿元|亿元|百万元|千万元|千元|万元|元|亿|万|千|%|％)?'
const NUM_RE = new RegExp(
  `\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?${UNIT}|\\d+(?:\\.\\d+)?${UNIT}`, 'g')

/** 答案里待坐标核验的指标数字：剥出处与列举序号、跳过年份（与后端同约定）。 */
export function answerNumbers(answer: string): string[] {
  const body = answer.replace(CITE_RE, '').replace(LIST_MARK_RE, '')
  const seen: string[] = []
  for (const m of body.match(NUM_RE) ?? []) {
    if (/^20\d{2}$/.test(m)) continue
    if (!seen.includes(m)) seen.push(m)
  }
  return seen
}