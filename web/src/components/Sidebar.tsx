import { YEARS, type ConversationSummary, type ReadyInfo } from '../api'
import ConversationHistory from './ConversationHistory'

export type Mode = 'chat' | 'compare'
export type Backend = 'cloud' | 'ollama'

/** 常驻侧栏：模式/年份/后端/Key/Agent/清空，全部即时生效。 */
export default function Sidebar({ mode, onMode, year, onYear, backend, onBackend,
  apiKey, onApiKey, forceAgent, onForceAgent, ready, busy, canClear, onClear, onCollapse,
  conversations, activeId, onNewChat, onSwitchChat, onDeleteChat }: {
  mode: Mode
  onMode: (m: Mode) => void
  year: number | null
  onYear: (y: number | null) => void
  backend: Backend
  onBackend: (b: Backend) => void
  apiKey: string
  onApiKey: (k: string) => void
  forceAgent: boolean
  onForceAgent: (v: boolean) => void
  ready: ReadyInfo | null
  busy: boolean
  canClear: boolean
  onClear: () => void
  onCollapse: () => void
  conversations?: ConversationSummary[]
  activeId?: string | null
  onNewChat?: () => void
  onSwitchChat?: (id: string) => void
  onDeleteChat?: (id: string) => void
}) {
  const hasKey = !!apiKey.trim() || !!ready?.api_key_configured
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="seal">溯</div>
        <div>
          <div className="brand-name">年报问答</div>
          <div className="brand-sub">证据可溯 · EVIDENCE</div>
        </div>
        <button className="side-collapse" onClick={onCollapse} aria-label="收起侧栏" title="收起侧栏">«</button>
      </div>

      {conversations && (
        <ConversationHistory
          conversations={conversations} activeId={activeId ?? null} busy={busy}
          onNew={onNewChat ?? (() => {})} onSwitch={onSwitchChat ?? (() => {})}
          onDelete={onDeleteChat ?? (() => {})}
        />
      )}

      <p className="side-label">模式</p>
      <div className="seg">
        <button aria-pressed={mode === 'chat'} onClick={() => onMode('chat')}>对话问答</button>
        <button aria-pressed={mode === 'compare'} onClick={() => onMode('compare')}>双年对比</button>
      </div>

      {mode === 'chat' && (
        <label className="side-field">检索范围
          <select
            value={year ?? ''}
            onChange={e => onYear(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">全部年份</option>
            {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
      )}

      <p className="side-label">生成模型</p>
      <div className="seg">
        <button aria-pressed={backend === 'cloud'} onClick={() => onBackend('cloud')}>云端 DeepSeek</button>
        <button aria-pressed={backend === 'ollama'} onClick={() => onBackend('ollama')}>本地 Ollama</button>
      </div>

      {backend === 'cloud' ? (
        <>
          <span className={`conn-pill${hasKey ? '' : ' off'}`}>
            <span className="dot" />
            {hasKey ? 'DeepSeek 已连接' : '待填 Key · 当前不可用'}
          </span>
          <label className="side-field">DeepSeek API Key
            <input
              type="password"
              value={apiKey}
              placeholder="sk-…（留空则用环境变量）"
              onChange={e => onApiKey(e.target.value)}
            />
          </label>
          <p className="side-caption">没有 Key？到 platform.deepseek.com 申请，一次问答约 ¥0.005。</p>
        </>
      ) : (
        <>
          <span className="conn-pill"><span className="dot" />本地 Ollama · 离线零成本</span>
          <p className="side-caption">需要本机运行 Ollama（qwen3:4b）。完全离线、零成本。</p>
        </>
      )}

      <label className="side-check">
        <input
          type="checkbox"
          checked={forceAgent}
          onChange={e => onForceAgent(e.target.checked)}
        />
        强制 Agent
      </label>

      <button className="clear-btn" onClick={onClear} disabled={busy || !canClear}>
        清空对话
      </button>

      <div className="side-foot">贵州茅台 2022–2025 年报<br />仅学习研究，非投资建议</div>
    </aside>
  )
}