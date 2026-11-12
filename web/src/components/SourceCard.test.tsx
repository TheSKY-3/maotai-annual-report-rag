import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SourceCard from './SourceCard'

describe('SourceCard', () => {
  it('显示出处标签与坐标高亮图地址', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={['680.93']} />)
    expect(screen.getByText(/出处 · 2024年报 P26/)).toBeInTheDocument()
    const img = screen.getByAltText(/坐标高亮/) as HTMLImageElement
    expect(img.src).toContain('/cite_crop?year=2024&page=26&numbers=680.93')
  })

  it('图加载失败时给出灰字说明并保留出处', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={['680.93']} />)
    fireEvent.error(screen.getByAltText(/坐标高亮/))
    expect(screen.queryByAltText(/坐标高亮/)).toBeNull()
    expect(screen.getByText(/坐标高亮加载失败/)).toBeInTheDocument()
    expect(screen.getByText(/2024年报 P26/)).toBeInTheDocument()
  })

  it('无数字时不请求坐标图', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={[]} />)
    expect(screen.queryByAltText(/坐标高亮/)).toBeNull()
  })

  it('defaultOpen 时不点击即见坐标核验说明与横带图', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={['680.93']} defaultOpen />)
    expect(screen.getByText('坐标核验 · 红框 = 答案引用的数字')).toBeVisible()
    expect(screen.getByAltText(/坐标高亮/)).toBeVisible()
    expect(screen.getByText('2024年报 P26 原文横带')).toBeVisible()
  })

  it('默认折叠时说明与图不可见', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={['680.93']} />)
    expect(screen.getByText('坐标核验 · 红框 = 答案引用的数字')).not.toBeVisible()
    expect(screen.getByAltText(/坐标高亮/)).not.toBeVisible()
  })
})