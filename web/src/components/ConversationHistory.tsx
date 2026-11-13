import type { ConversationSummary } from '../api'

/** P1 历史对话列表：新建/切换/删除；空标题显示「新对话」。 */
export default function ConversationHistory({ conversations, activeId, busy,
  onNew, onSwitch, onDelete }: {
  conversations: ConversationSummary[]
  activeId: string | null
  busy: boolean
  onNew: () => void
  onSwitch: (id: string) => void
  onDelete: (id: string) => void
}) {
  return (
    <div className="history">
      <div className="history-head">
        <p className="side-label">历史对话</p>
        <button className="history-new" onClick={onNew} disabled={busy} aria-label="新建对话">
          ＋ 新建
        </button>
      </div>
      <ul className="history-list">
        {conversations.map(c => (
          <li key={c.id} className={`history-item${c.id === activeId ? ' active' : ''}`}>
            <button
              className="history-title"
              onClick={() => onSwitch(c.id)}
              title={c.title || '新对话'}
            >
              {c.title || '新对话'}
            </button>
            <button
              className="history-del"
              onClick={() => onDelete(c.id)}
              disabled={busy}
              aria-label={`删除对话 ${c.title || '新对话'}`}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}