import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createConversation, deleteConversation, getConversation,
  listConversations, renameConversation,
} from './api'

afterEach(() => vi.unstubAllGlobals())

function stubJson(data: unknown) {
  const mock = vi.fn().mockResolvedValue({ ok: true, json: async () => data })
  vi.stubGlobal('fetch', mock)
  return mock
}

describe('会话客户端', () => {
  it('listConversations 取列表', async () => {
    const items = [{ id: 'c1', title: 't', created_at: 1, updated_at: 2 }]
    const mock = stubJson({ conversations: items })
    expect(await listConversations()).toEqual(items)
    expect(mock.mock.calls[0][0]).toBe('/conversations')
  })

  it('createConversation 返回 id 并 POST 标题', async () => {
    const mock = stubJson({ id: 'c1' })
    expect(await createConversation('标题')).toBe('c1')
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/conversations')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ title: '标题' })
  })

  it('getConversation 取消息', async () => {
    const msgs = [{ role: 'user', content: 'q', meta: {}, created_at: 1 }]
    const mock = stubJson({ messages: msgs })
    expect(await getConversation('c1')).toEqual(msgs)
    expect(mock.mock.calls[0][0]).toBe('/conversations/c1')
  })

  it('deleteConversation 发 DELETE', async () => {
    const mock = stubJson({ ok: true })
    await deleteConversation('c1')
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/conversations/c1')
    expect(init.method).toBe('DELETE')
  })

  it('renameConversation 发 PATCH', async () => {
    const mock = stubJson({ ok: true })
    await renameConversation('c1', '新')
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/conversations/c1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ title: '新' })
  })

  it('非 ok 抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))
    await expect(listConversations()).rejects.toThrow()
  })
})