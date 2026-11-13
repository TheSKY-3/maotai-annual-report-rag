import { splitCitations } from '../api'

/** 答案正文：[来源: …] 渲染成出处胶囊，其余按原文本展示。 */
export default function AnswerText({ text }: { text: string }) {
  return (
    <>
      {splitCitations(text).map((s, i) => s.type === 'cite'
        ? <span key={i} className="cite-pill">📄 {s.value}</span>
        : <span key={i}>{s.value}</span>)}
    </>
  )
}