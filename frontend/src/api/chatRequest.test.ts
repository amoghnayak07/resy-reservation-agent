import { describe, expect, it } from 'vitest'
import { buildChatRequest } from './chatRequest'

describe('buildChatRequest', () => {
  it('sends the message with the selected region and no location or timezone', () => {
    const body = buildChatRequest('hi', 'los-angeles-ca', 'conv-1')
    expect(body).toEqual({ conversation_id: 'conv-1', message: 'hi', region: 'los-angeles-ca' })
  })
})
