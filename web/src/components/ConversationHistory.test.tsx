import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ConversationHistory from './ConversationHistory'

const items = [
  { id: 'c1', title: '净利润', created_at: 1, updated_at: 2 },
  { id: 'c2', title: '', created_at: 1, updated_at: 1 },
]

const noop = () => {}

describe('ConversationHistory', () => {
  it('渲染对话标题，空标题显示新对话', () => {
    render(<ConversationHistory conversations={items} activeId="c1" busy={false}
      onNew={noop} onSwitch={noop} onDelete={noop} />)
    expect(screen.getByText('净利润')).toBeInTheDocument()
    expect(screen.getByText('新对话')).toBeInTheDocument()
  })

  it('点标题切换、点删除、点新建各调对应回调', () => {
    const onSwitch = vi.fn()
    const onDelete = vi.fn()
    const onNew = vi.fn()
    render(<ConversationHistory conversations={items} activeId="c1" busy={false}
      onNew={onNew} onSwitch={onSwitch} onDelete={onDelete} />)
    fireEvent.click(screen.getByText('净利润'))
    expect(onSwitch).toHaveBeenCalledWith('c1')
    fireEvent.click(screen.getByLabelText('删除对话 净利润'))
    expect(onDelete).toHaveBeenCalledWith('c1')
    fireEvent.click(screen.getByLabelText('新建对话'))
    expect(onNew).toHaveBeenCalled()
  })

  it('busy 时新建与删除禁用', () => {
    render(<ConversationHistory conversations={items} activeId="c1" busy
      onNew={noop} onSwitch={noop} onDelete={noop} />)
    expect(screen.getByLabelText('新建对话')).toBeDisabled()
    expect(screen.getByLabelText('删除对话 净利润')).toBeDisabled()
  })
})