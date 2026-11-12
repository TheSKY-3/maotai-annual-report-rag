import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SourceCard from './SourceCard'

describe('SourceCard', () => {
  it('显示出处标签与坐标高亮图地址', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={['680.93']} />)
    expect(screen.getByText(/2024年报 P26/)).toBeInTheDocument()
    const img = screen.getByAltText(/坐标高亮/) as HTMLImageElement
    expect(img.src).toContain('/cite_crop?year=2024&page=26&numbers=680.93')
  })

  it('图加载失败时隐藏图但保留出处', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={['680.93']} />)
    fireEvent.error(screen.getByAltText(/坐标高亮/))
    expect(screen.queryByAltText(/坐标高亮/)).toBeNull()
    expect(screen.getByText(/2024年报 P26/)).toBeInTheDocument()
  })

  it('无数字时不请求坐标图', () => {
    render(<SourceCard source={{ year: 2024, page: 26 }} numbers={[]} />)
    expect(screen.queryByAltText(/坐标高亮/)).toBeNull()
  })
})